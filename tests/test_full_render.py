"""Testes da full autorizada, da revalidação de hashes e da publicação atômica."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from collections.abc import Callable, Iterator
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies, process
from editor.contracts import CliError
from editor.media import inspect_source
from editor.render.full import render_full


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / "tests" / "fixtures" / "workflow"
WORKFLOW_PLAN = WORKFLOW_DIR / "plan.json"
WORKFLOW_SOURCES = WORKFLOW_DIR / "sources"


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para provar o contrato público da full."""

    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    """Cria um trabalho isolado para que a prova não altere fixtures versionados."""

    temporary_path = PROJECT_ROOT / f"tmp-full-render-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


def copy_workflow(workdir: Path) -> None:
    """Copia plano e fontes locais para reproduzir o cenário aprovado do MVP."""

    shutil.copy2(WORKFLOW_PLAN, workdir / "plan.json")
    shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")


def approve_workflow(workdir: Path) -> None:
    """Gera preview e aprovação antes de iniciar uma prova de renderização final."""

    preview = run_module(
        "render",
        "preview",
        "--workdir",
        str(workdir.resolve()),
        "--json",
    )
    if preview.returncode != 0:
        raise AssertionError(f"preview não publicada: {preview.stderr}\n{preview.stdout}")
    approval = run_module(
        "approve",
        "--workdir",
        str(workdir.resolve()),
        "--json",
    )
    if approval.returncode != 0:
        raise AssertionError(f"aprovação não publicada: {approval.stderr}\n{approval.stdout}")


class FullRenderTests(unittest.TestCase):
    """Garante que somente o estado aprovado pode produzir uma full completa."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFmpeg e FFprobe locais para os cenários reais de renderização final."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not WORKFLOW_PLAN.is_file() or not WORKFLOW_SOURCES.is_dir():
            raise unittest.SkipTest("fixture do workflow não está disponível")

    def test_real_cli_publishes_full_from_current_approval(self) -> None:
        """Confirma timeline, perfil final, hashes vigentes e preservação dos insumos."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            approve_workflow(workdir)
            plan_before = (workdir / "plan.json").read_bytes()
            approval_before = (workdir / "approval.json").read_bytes()
            sources_before = {
                path.name: path.read_bytes() for path in (workdir / "sources").iterdir()
            }

            result = run_module(
                "render",
                "full",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "render full")
            self.assertEqual(payload["data"]["timeline_duration_seconds"], 2.0)
            self.assertEqual(payload["data"]["video"]["max_dimension"], 320)
            self.assertEqual(payload["data"]["audio"], {"codec": "aac", "present": True})

            full_path = Path(payload["data"]["artifact_path"])
            self.assertEqual(full_path, workdir / "full.mp4")
            self.assertTrue(full_path.is_file())
            metadata = inspect_source(full_path, require_audio=True, require_video=True)
            self.assertEqual((metadata.video_width, metadata.video_height), (320, 180))
            self.assertEqual(metadata.video_codec, "h264")
            self.assertEqual(metadata.audio_codec, "aac")
            self.assertAlmostEqual(metadata.duration_seconds, 2.0, places=1)
            self.assertEqual((workdir / "plan.json").read_bytes(), plan_before)
            self.assertEqual((workdir / "approval.json").read_bytes(), approval_before)
            self.assertEqual(
                {path.name: path.read_bytes() for path in (workdir / "sources").iterdir()},
                sources_before,
            )
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_missing_approval_blocks_full_without_output(self) -> None:
        """Confirma que a existência do plano e da preview não substitui aprovação explícita."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            preview = run_module(
                "render",
                "preview",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            self.assertEqual(preview.returncode, 0, preview.stderr)

            result = run_module(
                "render",
                "full",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 5)
            self.assertEqual(result.stderr, "")
            self.assertEqual(payload["error"]["code"], "APPROVAL_REQUIRED")
            self.assertFalse((workdir / "full.mp4").exists())

    def test_source_cannot_alias_full_destination(self) -> None:
        """Confirma que a full vigente nunca pode ser usada como entrada do próprio render."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            approve_workflow(workdir)
            published = run_module(
                "render",
                "full",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            self.assertEqual(published.returncode, 0, published.stderr)
            full_path = workdir / "full.mp4"
            previous_hash = hashlib.sha256(full_path.read_bytes()).hexdigest()

            document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
            document["sources"][0]["path"] = "full.mp4"
            (workdir / "plan.json").write_text(
                json.dumps(document),
                encoding="utf-8",
            )

            with self.assertRaises(CliError) as raised:
                render_full(workdir)

            self.assertEqual(raised.exception.code, "OUTPUT_INVALID")
            self.assertEqual(
                hashlib.sha256(full_path.read_bytes()).hexdigest(),
                previous_hash,
            )
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_separate_plan_preview_and_source_mutations_make_approval_stale(self) -> None:
        """Confirma que qualquer mutação isolada bloqueia a full e preserva a saída vigente."""

        mutations: dict[str, Callable[[Path], None]] = {
            "plan": self._mutate_plan,
            "preview": self._mutate_preview,
            "source": self._mutate_source,
        }
        for name, mutation in mutations.items():
            with self.subTest(artifact=name):
                with workspace_tempdir() as workdir:
                    copy_workflow(workdir)
                    approve_workflow(workdir)
                    approval_before = (workdir / "approval.json").read_bytes()
                    previous_full = b"previous-full"
                    (workdir / "full.mp4").write_bytes(previous_full)
                    mutation(workdir)

                    result = run_module(
                        "render",
                        "full",
                        "--workdir",
                        str(workdir.resolve()),
                        "--json",
                    )
                    payload = json.loads(result.stdout)

                    self.assertEqual(result.returncode, 5)
                    self.assertEqual(result.stderr, "")
                    self.assertEqual(payload["error"]["code"], "APPROVAL_STALE")
                    self.assertEqual((workdir / "full.mp4").read_bytes(), previous_full)
                    self.assertEqual((workdir / "approval.json").read_bytes(), approval_before)
                    self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_invalid_approval_is_stale_without_rendering(self) -> None:
        """Confirma que approval.json corrompido não libera uma renderização final."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            approve_workflow(workdir)
            (workdir / "approval.json").write_text(
                '{"schema_version":1,"schema_version":1}',
                encoding="utf-8",
            )

            result = run_module(
                "render",
                "full",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 5)
            self.assertEqual(payload["error"]["code"], "APPROVAL_STALE")
            self.assertFalse((workdir / "full.mp4").exists())

    def test_render_failure_preserves_previous_full_and_cleans_temp(self) -> None:
        """Confirma que uma falha do FFmpeg não substitui a full vigente nem deixa parcial."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            approve_workflow(workdir)
            previous_full = b"previous-full"
            full_path = workdir / "full.mp4"
            full_path.write_bytes(previous_full)
            original_run_tool = process.run_tool

            def fake_run_tool(
                tool: str,
                arguments: list[str],
                *,
                timeout_seconds: float,
            ) -> subprocess.CompletedProcess[str]:
                """Mantém sondagens reais e simula falha somente no render final."""

                if tool == "ffmpeg" and "-encoders" not in arguments:
                    return subprocess.CompletedProcess(
                        args=[tool, *arguments],
                        returncode=1,
                        stdout="",
                        stderr="simulated render failure",
                    )
                return original_run_tool(
                    tool,
                    arguments,
                    timeout_seconds=timeout_seconds,
                )

            with patch("editor.render.preview.process.run_tool", side_effect=fake_run_tool):
                with self.assertRaises(CliError) as raised:
                    render_full(workdir)

            self.assertEqual(raised.exception.code, "RENDER_FAILED")
            self.assertEqual(full_path.read_bytes(), previous_full)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    @staticmethod
    def _mutate_plan(workdir: Path) -> None:
        """Altera semanticamente o plano sem torná-lo inválido para testar apenas o hash."""

        document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
        document["segments"][0]["audio"]["tracks"][0]["gain_db"] = -1
        (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")

    @staticmethod
    def _mutate_preview(workdir: Path) -> None:
        """Muda os bytes da preview vigente sem alterar o registro de aprovação."""

        preview_path = workdir / "preview.mp4"
        preview_path.write_bytes(preview_path.read_bytes() + b"stale")

    @staticmethod
    def _mutate_source(workdir: Path) -> None:
        """Muda os bytes de uma fonte local preservando o arquivo para a inspeção do plano."""

        source_path = workdir / "sources" / "music.mp3"
        source_path.write_bytes(source_path.read_bytes() + b"stale")


if __name__ == "__main__":
    unittest.main()
