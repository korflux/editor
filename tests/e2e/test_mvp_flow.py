"""Prova E2E da jornada local completa do MVP, do inventário à full aprovada."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from uuid import uuid4

from editor import dependencies


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "e2e"
FIXTURE_SOURCES = FIXTURE_ROOT / "sources"
MODEL_ROOT = PROJECT_ROOT / "models"
REQUIRED_OPERATIONS = (
    "capabilities",
    "analyze_audio",
    "analyze_transcribe",
    "extract_frames",
    "plan_validate",
    "render_preview",
    "approve",
    "render_full",
)


def run_cli(
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Executa a CLI em processo separado com dependências e pesos somente locais."""

    child_environment = os.environ.copy()
    child_environment.update(
        {
            "EDITOR_MODEL_DIR": str(MODEL_ROOT.resolve()),
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    if environment:
        child_environment.update(environment)
    return subprocess.run(
        [sys.executable, "-B", "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_environment,
        check=False,
    )


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    """Cria um trabalho E2E dentro do workspace e remove seu estado publicado ao final."""

    temporary_path = PROJECT_ROOT / f"tmp-mvp-e2e-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


def copy_fixture(workdir: Path) -> None:
    """Copia plano e fontes curtas para que a jornada não altere fixtures versionadas."""

    shutil.copy2(FIXTURE_ROOT / "plan.json", workdir / "plan.json")
    shutil.copytree(FIXTURE_SOURCES, workdir / "sources")


def sha256_file(path: Path) -> str:
    """Calcula o hash de um artefato em blocos para conferir integridade sem carregá-lo inteiro."""

    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    """Retorna hashes determinísticos das fontes regulares de uma fixture ou trabalho."""

    return {
        path.name: sha256_file(path)
        for path in sorted(root.iterdir())
        if path.is_file()
    }


def assert_json_success(
    testcase: unittest.TestCase,
    result: subprocess.CompletedProcess[str],
    command: str,
) -> dict[str, object]:
    """Valida stdout JSON puro, ausência de prompt e envelope de sucesso de um comando."""

    testcase.assertEqual(result.returncode, 0, result.stderr)
    testcase.assertEqual(result.stderr, "")
    testcase.assertEqual(len(result.stdout.splitlines()), 1)
    payload = json.loads(result.stdout)
    testcase.assertTrue(payload["ok"])
    testcase.assertEqual(payload["schema_version"], 1)
    testcase.assertEqual(payload["command"], command)
    return payload


def assert_json_error(
    testcase: unittest.TestCase,
    result: subprocess.CompletedProcess[str],
) -> dict[str, object]:
    """Valida que uma falha pública continue estruturada e sem diagnóstico interno."""

    testcase.assertNotEqual(result.returncode, 0)
    testcase.assertEqual(result.stderr, "")
    testcase.assertEqual(len(result.stdout.splitlines()), 1)
    payload = json.loads(result.stdout)
    testcase.assertFalse(payload["ok"])
    testcase.assertNotIn("Traceback", result.stdout)
    return payload


class MvpFlowTests(unittest.TestCase):
    """Confirma a jornada E2E local, seus artefatos e o bloqueio de estado obsoleto."""

    @classmethod
    def setUpClass(cls) -> None:
        """Garante que a prova real tenha FFmpeg, backend Whisper e modelo local disponíveis."""

        if not (FIXTURE_ROOT / "plan.json").is_file() or not FIXTURE_SOURCES.is_dir():
            raise unittest.SkipTest("fixture E2E do MVP não está disponível")
        if any(dependencies.resolve_executable(name) is None for name in ("ffmpeg", "ffprobe")):
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if dependencies.detect_whisper_backend()["status"] != "available":
            raise unittest.SkipTest("backend Whisper local não está disponível")
        if not dependencies.is_valid_model_directory(MODEL_ROOT / "base"):
            raise unittest.SkipTest("modelo Whisper base local não está disponível")

    def test_complete_local_mvp_workflow(self) -> None:
        """Executa análise, plano, preview, aprovação, full e falha por mutação de fonte."""

        fixture_hashes = source_hashes(FIXTURE_SOURCES)
        with workspace_tempdir() as workdir:
            copy_fixture(workdir)
            workdir_argument = str(workdir.resolve())
            working_sources = workdir / "sources"
            initial_working_hashes = source_hashes(working_sources)

            capabilities = assert_json_success(
                self,
                run_cli("capabilities", "--json"),
                "capabilities",
            )
            operations = capabilities["data"]["operations"]
            for operation in REQUIRED_OPERATIONS:
                with self.subTest(operation=operation):
                    self.assertEqual(operations[operation]["status"], "available")

            audio = assert_json_success(
                self,
                run_cli(
                    "analyze",
                    "audio",
                    "--workdir",
                    workdir_argument,
                    "--source-id",
                    "main",
                    "--input",
                    str(working_sources / "main.mp4"),
                    "--window-s",
                    "0.5",
                    "--json",
                ),
                "analyze audio",
            )
            transcription = assert_json_success(
                self,
                run_cli(
                    "analyze",
                    "transcribe",
                    "--workdir",
                    workdir_argument,
                    "--source-id",
                    "main",
                    "--input",
                    str(working_sources / "main.mp4"),
                    "--device",
                    "cpu",
                    "--model",
                    "base",
                    "--json",
                ),
                "analyze transcribe",
            )
            frames = assert_json_success(
                self,
                run_cli(
                    "extract",
                    "frames",
                    "--workdir",
                    workdir_argument,
                    "--source-id",
                    "main",
                    "--input",
                    str(working_sources / "main.mp4"),
                    "--timestamps",
                    "0,1",
                    "--json",
                ),
                "extract frames",
            )
            plan = assert_json_success(
                self,
                run_cli("plan", "validate", "--workdir", workdir_argument, "--json"),
                "plan validate",
            )
            preview = assert_json_success(
                self,
                run_cli("render", "preview", "--workdir", workdir_argument, "--json"),
                "render preview",
            )
            approval = assert_json_success(
                self,
                run_cli("approve", "--workdir", workdir_argument, "--json"),
                "approve",
            )
            full = assert_json_success(
                self,
                run_cli("render", "full", "--workdir", workdir_argument, "--json"),
                "render full",
            )

            artifact_paths = (
                Path(audio["data"]["artifact_path"]),
                Path(transcription["data"]["artifact_path"]),
                Path(frames["data"]["artifact_path"]),
                workdir / "preview.mp4",
                Path(approval["data"]["artifact_path"]),
                Path(full["data"]["artifact_path"]),
            )
            for artifact_path in artifact_paths:
                with self.subTest(artifact=artifact_path):
                    self.assertTrue(artifact_path.is_file())

            manifest = json.loads(
                (workdir / "frames" / "main" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["selection"]["mode"], "timestamps")
            self.assertEqual(len(manifest["frames"]), 2)
            self.assertEqual(plan["data"]["timeline"]["duration_seconds"], 2.0)
            self.assertEqual(preview["data"]["timeline_duration_seconds"], 2.0)

            approval_document = json.loads(
                (workdir / "approval.json").read_text(encoding="utf-8")
            )
            self.assertEqual(full["data"]["plan_sha256"], approval_document["plan_sha256"])
            self.assertEqual(full["data"]["timeline_duration_seconds"], preview["data"]["timeline_duration_seconds"])
            self.assertEqual(full["data"]["approval"]["preview_sha256"], approval_document["preview_sha256"])
            self.assertEqual(source_hashes(working_sources), initial_working_hashes)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

            full_before_mutation = sha256_file(workdir / "full.mp4")
            approval_before_mutation = (workdir / "approval.json").read_bytes()
            mutated_source = working_sources / "main.mp4"
            mutated_source.write_bytes(mutated_source.read_bytes() + b"mutation")
            stale = assert_json_error(
                self,
                run_cli("render", "full", "--workdir", workdir_argument, "--json"),
            )

            self.assertEqual(stale["error"]["code"], "APPROVAL_STALE")
            self.assertEqual(sha256_file(workdir / "full.mp4"), full_before_mutation)
            self.assertEqual((workdir / "approval.json").read_bytes(), approval_before_mutation)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])
            self.assertEqual(source_hashes(FIXTURE_SOURCES), fixture_hashes)


if __name__ == "__main__":
    unittest.main()
