"""Testes da aprovação v2 vinculada ao plano e às fontes, sem preview, e da forma condicionada."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies
from editor.contracts import CliError, EXIT_CODES
from editor.integrity import approval as approval_module
from editor.integrity.approval import (
    approve_workdir_if_unchanged,
    sources_fingerprint,
    verify_approval,
)
from editor.plan.canonical import canonical_sha256
from editor.plan.validation import load_validated_plan
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / "tests" / "fixtures" / "workflow"
WORKFLOW_PLAN = WORKFLOW_DIR / "plan.json"
WORKFLOW_SOURCES = WORKFLOW_DIR / "sources"
IMAGE_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "phase-1" / "images"


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para provar o contrato público de aprovação."""

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
    """Cria um trabalho isolado para impedir que testes gravem estado no fixture versionado."""

    temporary_path = PROJECT_ROOT / f"tmp-approval-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        remove_tree(temporary_path)


def copy_workflow(workdir: Path) -> None:
    """Copia plano e fontes locais para reproduzir o fluxo de preview e aprovação."""

    shutil.copy2(WORKFLOW_PLAN, workdir / "plan.json")
    shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")


def configure_layered_workflow(workdir: Path) -> None:
    """Adiciona fontes distintas de imagem e vídeo para provar o vínculo das camadas."""

    shutil.copy2(IMAGE_FIXTURES / "alpha.png", workdir / "sources" / "layer-image.png")
    shutil.copy2(WORKFLOW_SOURCES / "broll.mp4", workdir / "sources" / "layer-video.mp4")
    document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
    document["sources"].extend(
        [
            {"id": "layer-image", "kind": "image", "path": "sources/layer-image.png"},
            {"id": "layer-video", "kind": "video", "path": "sources/layer-video.mp4"},
        ]
    )
    document["layers"].extend(
        [
            {
                "type": "image",
                "start_s": 0.25,
                "end_s": 1.0,
                "x": 0.1,
                "y": 0.1,
                "width": 0.25,
                "height": 0.25,
                "source_id": "layer-image",
                "fit": "contain",
            },
            {
                "type": "video",
                "start_s": 1.0,
                "end_s": 1.75,
                "x": 0.5,
                "y": 0.25,
                "width": 0.4,
                "height": 0.5,
                "source_id": "layer-video",
                "in_s": 0.25,
                "out_s": 1.0,
                "fit": "crop",
            },
        ]
    )
    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")


def render_preview(workdir: Path) -> None:
    """Gera a preview opcional, para provar que ela existe ao lado da aprovação sem afetá-la."""

    result = run_module(
        "render",
        "preview",
        "--workdir",
        str(workdir.resolve()),
        "--json",
    )
    payload = json.loads(result.stdout)
    if result.returncode != 0 or not payload.get("ok"):
        raise AssertionError(f"preview não publicada: {result.stderr}\n{payload}")


class ApprovalTests(unittest.TestCase):
    """Garante que a aprovação v2 vincula só plano e fontes e que a preview não participa dela."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFmpeg e FFprobe para validar o plano e, onde pedido, renderizar a preview real."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not WORKFLOW_PLAN.is_file() or not WORKFLOW_SOURCES.is_dir():
            raise unittest.SkipTest("fixture do workflow não está disponível")

    def test_real_cli_publishes_ordered_hash_approval_without_preview(self) -> None:
        """Confirma o registro v2 (sem preview), hashes semânticos, fontes ordenadas e publicação atômica."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            self.assertFalse((workdir / "preview.mp4").exists())
            result = run_module(
                "approve",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(result.stdout)
            approval = json.loads((workdir / "approval.json").read_text(encoding="utf-8"))
            plan = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "approve")
            self.assertEqual(
                Path(payload["data"]["artifact_path"]),
                workdir / "approval.json",
            )
            self.assertEqual(
                set(payload["data"]),
                {"artifact_path", "relative_artifact_path", "approved_at", "plan_sha256", "sources"},
            )
            self.assertEqual(
                set(approval),
                {"schema_version", "approved_at", "plan_sha256", "sources"},
            )
            self.assertEqual(approval["schema_version"], 2)
            self.assertEqual(approval["plan_sha256"], canonical_sha256(plan))
            self.assertEqual(
                [source["source_id"] for source in approval["sources"]],
                [source["id"] for source in plan["sources"]],
            )
            self.assertEqual(
                [source["sha256"] for source in approval["sources"]],
                [
                    hashlib.sha256((workdir / source["path"]).read_bytes()).hexdigest()
                    for source in plan["sources"]
                ],
            )
            self.assertEqual(
                datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00")).utcoffset().total_seconds(),
                0,
            )
            # Aprovar não cria preview nem deixa temporário.
            self.assertFalse((workdir / "preview.mp4").exists())
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_real_cli_includes_image_and_video_layer_sources_in_approval(self) -> None:
        """Confirma que aprovação e preview usam todas as fontes declaradas por camadas."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            configure_layered_workflow(workdir)
            preview = run_module(
                "render",
                "preview",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            preview_payload = json.loads(preview.stdout)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(preview_payload["data"]["cut_count"], 0)
            self.assertEqual(preview_payload["data"]["layer_count"], 3)

            result = run_module(
                "approve",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(result.stdout)
            approval = json.loads((workdir / "approval.json").read_text(encoding="utf-8"))
            plan = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                [source["source_id"] for source in approval["sources"]],
                [source["id"] for source in plan["sources"]],
            )
            self.assertEqual(
                [source["sha256"] for source in approval["sources"]],
                [
                    hashlib.sha256((workdir / source["path"]).read_bytes()).hexdigest()
                    for source in plan["sources"]
                ],
            )

    def test_render_preview_does_not_change_or_replace_the_approval(self) -> None:
        """Confirma que gerar, trocar ou apagar a preview não altera o registro de aprovação vigente."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            approved = run_module("approve", "--workdir", str(workdir.resolve()), "--json")
            self.assertEqual(approved.returncode, 0, approved.stderr)
            approval_before = (workdir / "approval.json").read_bytes()

            render_preview(workdir)
            self.assertTrue((workdir / "preview.mp4").is_file())
            self.assertEqual((workdir / "approval.json").read_bytes(), approval_before)

            # A preview agora é opcional: apagá-la não torna a aprovação inválida.
            (workdir / "preview.mp4").unlink()
            verified = verify_approval(workdir, load_validated_plan(workdir)[1])
            self.assertEqual(verified["plan_sha256"], json.loads(approval_before)["plan_sha256"])
            self.assertEqual((workdir / "approval.json").read_bytes(), approval_before)

    def test_formatting_keeps_plan_hash_and_semantics_change_it(self) -> None:
        """Confirma que o hash ignora formatação e muda somente com alteração semântica."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)

            first = run_module("approve", "--workdir", str(workdir.resolve()), "--json")
            self.assertEqual(first.returncode, 0, first.stderr)
            first_hash = json.loads(
                (workdir / "approval.json").read_text(encoding="utf-8")
            )["plan_sha256"]

            document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
            (workdir / "plan.json").write_text(
                json.dumps(document, ensure_ascii=False, indent=4, sort_keys=True),
                encoding="utf-8",
            )
            formatted = run_module("approve", "--workdir", str(workdir.resolve()), "--json")
            self.assertEqual(formatted.returncode, 0, formatted.stderr)
            formatted_hash = json.loads(
                (workdir / "approval.json").read_text(encoding="utf-8")
            )["plan_sha256"]
            self.assertEqual(formatted_hash, first_hash)

            document["profile"]["max_full_width"] = 160
            (workdir / "plan.json").write_text(
                json.dumps(document),
                encoding="utf-8",
            )
            semantic = run_module("approve", "--workdir", str(workdir.resolve()), "--json")
            self.assertEqual(semantic.returncode, 0, semantic.stderr)
            semantic_hash = json.loads(
                (workdir / "approval.json").read_text(encoding="utf-8")
            )["plan_sha256"]
            self.assertNotEqual(semantic_hash, first_hash)

    def test_versioned_fixture_approval_is_a_current_v2_record(self) -> None:
        """Mantém o `approval.json` versionado do cenário do MVP no contrato v2, aceito pela verificação."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            shutil.copy2(WORKFLOW_DIR / "approval.json", workdir / "approval.json")
            _, validation = load_validated_plan(workdir)

            approval = verify_approval(workdir, validation)

            self.assertEqual(approval["schema_version"], 2)
            self.assertNotIn("preview_sha256", approval)


def load_state(workdir: Path) -> tuple[str, str]:
    """Hash do plano e impressão das fontes que o estado exibido pela interface entregaria no carregamento."""

    _, validation = load_validated_plan(workdir)
    return validation["plan_sha256"], sources_fingerprint(validation)


def touch_source(workdir: Path, name: str = "music.mp3") -> None:
    """Muda só o `mtime_ns` de uma fonte, mantendo o conteúdo e o tamanho."""

    path = workdir / "sources" / name
    status = path.stat()
    os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))


def append_to_source(workdir: Path, name: str = "music.mp3") -> None:
    """Troca o conteúdo de uma fonte por uma versão maior que continua válida para o plano."""

    path = workdir / "sources" / name
    path.write_bytes(path.read_bytes() + b"stale")


class ConditionedApprovalTests(unittest.TestCase):
    """Garante que a aprovação condicionada só grava a versão exibida e recusa qualquer divergência."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFprobe para validar o plano e o fixture do workflow."""

        if dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffprobe não está disponível localmente")
        if not WORKFLOW_PLAN.is_file() or not WORKFLOW_SOURCES.is_dir():
            raise unittest.SkipTest("fixture do workflow não está disponível")

    def assert_refused_without_writing(
        self, workdir: Path, state: tuple[str, str], previous: bytes | None
    ) -> None:
        """Confirma `APPROVAL_PLAN_CHANGED` (exit code de estado) sem criar nem alterar `approval.json`."""

        with self.assertRaises(CliError) as raised:
            approve_workdir_if_unchanged(workdir, *state)
        self.assertEqual(raised.exception.code, "APPROVAL_PLAN_CHANGED")
        self.assertEqual(raised.exception.exit_code, EXIT_CODES["STATE"])
        destination = workdir / "approval.json"
        if previous is None:
            self.assertFalse(destination.exists())
        else:
            self.assertEqual(destination.read_bytes(), previous)
        self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_fingerprint_follows_size_and_mtime_and_is_stable(self) -> None:
        """A impressão é estável sem mudança e muda com o tamanho e com só o `mtime_ns` de uma fonte."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            _, fingerprint = load_state(workdir)
            self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
            self.assertEqual(load_state(workdir)[1], fingerprint)

            touch_source(workdir)
            touched = load_state(workdir)[1]
            self.assertNotEqual(touched, fingerprint)

            append_to_source(workdir)
            self.assertNotIn(load_state(workdir)[1], {fingerprint, touched})

    def test_matching_state_publishes_the_same_v2_record_as_the_cli(self) -> None:
        """Com plano e impressão iguais aos do carregamento, grava o registro v2 que o `approve` gravaria."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            state = load_state(workdir)

            result = approve_workdir_if_unchanged(workdir, *state)
            approval = json.loads((workdir / "approval.json").read_text(encoding="utf-8"))
            cli = run_module("approve", "--workdir", str(workdir.resolve()), "--json")
            cli_approval = json.loads((workdir / "approval.json").read_text(encoding="utf-8"))

            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertEqual(approval["schema_version"], 2)
            self.assertEqual(approval["plan_sha256"], state[0])
            self.assertEqual(result["plan_sha256"], state[0])
            self.assertEqual(result["sources"], approval["sources"])
            self.assertEqual(
                {key: value for key, value in approval.items() if key != "approved_at"},
                {key: value for key, value in cli_approval.items() if key != "approved_at"},
            )
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_plan_hash_mismatch_is_refused_without_writing_or_hashing(self) -> None:
        """Plano alterado depois do carregamento é recusado antes de ler as fontes, sem tocar a aprovação."""

        for with_previous in (False, True):
            with self.subTest(previous_approval=with_previous):
                with workspace_tempdir() as workdir:
                    copy_workflow(workdir)
                    previous = None
                    if with_previous:
                        approve_workdir_if_unchanged(workdir, *load_state(workdir))
                        previous = (workdir / "approval.json").read_bytes()
                    state = load_state(workdir)
                    document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
                    document["segments"][0]["audio"]["tracks"][0]["gain_db"] = -1
                    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")

                    with patch.object(
                        approval_module, "_hash_sources", side_effect=AssertionError("leu as fontes")
                    ):
                        self.assert_refused_without_writing(workdir, state, previous)

    def test_fingerprint_mismatch_before_hashing_is_refused_without_writing(self) -> None:
        """Fonte trocada (tamanho ou só `mtime_ns`) depois do carregamento é recusada antes do cálculo."""

        for name, mutate in {"tamanho": append_to_source, "mtime": touch_source}.items():
            for with_previous in (False, True):
                with self.subTest(mutation=name, previous_approval=with_previous):
                    with workspace_tempdir() as workdir:
                        copy_workflow(workdir)
                        previous = None
                        if with_previous:
                            approve_workdir_if_unchanged(workdir, *load_state(workdir))
                            previous = (workdir / "approval.json").read_bytes()
                        state = load_state(workdir)
                        mutate(workdir)

                        with patch.object(
                            approval_module, "_hash_sources", side_effect=AssertionError("leu as fontes")
                        ):
                            self.assert_refused_without_writing(workdir, state, previous)

    def test_fingerprint_mismatch_after_hashing_is_refused_without_writing(self) -> None:
        """Fonte trocada durante o cálculo dos SHA-256 não grava o hash da fonte que o operador não viu."""

        real_hash_sources = approval_module._hash_sources
        for name, mutate in {"tamanho": append_to_source, "mtime": touch_source}.items():
            for with_previous in (False, True):
                with self.subTest(mutation=name, previous_approval=with_previous):
                    with workspace_tempdir() as workdir:
                        copy_workflow(workdir)
                        previous = None
                        if with_previous:
                            approve_workdir_if_unchanged(workdir, *load_state(workdir))
                            previous = (workdir / "approval.json").read_bytes()
                        state = load_state(workdir)

                        def hash_after_swap(
                            validation: dict, mutate=mutate, workdir=workdir
                        ) -> list[dict[str, str]]:
                            """Troca a fonte no meio do cálculo, como um processo externo faria."""

                            mutate(workdir)
                            return real_hash_sources(validation)

                        with patch.object(
                            approval_module, "_hash_sources", side_effect=hash_after_swap
                        ):
                            self.assert_refused_without_writing(workdir, state, previous)

    def test_invalid_plan_is_not_approved(self) -> None:
        """Plano que deixou de ser válido propaga `PLAN_INVALID` e não grava aprovação."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            state = load_state(workdir)
            (workdir / "plan.json").write_text("{}", encoding="utf-8")

            with self.assertRaises(CliError) as raised:
                approve_workdir_if_unchanged(workdir, *state)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            self.assertFalse((workdir / "approval.json").exists())
            # O plano é validado antes de preparar o layout, então `.tmp` pode nem existir.
            self.assertEqual(list((workdir / ".tmp").glob("*")), [])


if __name__ == "__main__":
    unittest.main()
