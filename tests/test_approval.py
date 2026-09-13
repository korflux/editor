"""Testes do registro de aprovação vinculado ao plano, à preview e às fontes."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator
from uuid import uuid4

from editor import dependencies
from editor.plan.canonical import canonical_sha256


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
        shutil.rmtree(temporary_path, ignore_errors=True)


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
    """Gera a preview publicada que a aprovação deve vincular por hash."""

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
    """Garante que somente um plano e uma preview locais completos sejam aprovados."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFmpeg e FFprobe para provar a aprovação após o render real da preview."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not WORKFLOW_PLAN.is_file() or not WORKFLOW_SOURCES.is_dir():
            raise unittest.SkipTest("fixture do workflow não está disponível")

    def test_real_cli_publishes_ordered_hash_approval(self) -> None:
        """Confirma schema, hashes semânticos, fontes ordenadas e publicação atômica."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            render_preview(workdir)
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
                set(approval),
                {"schema_version", "approved_at", "plan_sha256", "preview_sha256", "sources"},
            )
            self.assertEqual(approval["schema_version"], 1)
            self.assertEqual(approval["plan_sha256"], canonical_sha256(plan))
            self.assertEqual(
                approval["preview_sha256"],
                hashlib.sha256((workdir / "preview.mp4").read_bytes()).hexdigest(),
            )
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

    def test_missing_preview_preserves_existing_approval(self) -> None:
        """Confirma que preview ausente não cria estado novo nem substitui aprovação vigente."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            result = run_module(
                "approve",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(result.returncode, 5)
            self.assertEqual(payload["error"]["code"], "APPROVAL_REQUIRED")
            self.assertFalse((workdir / "approval.json").exists())

            render_preview(workdir)
            approved = run_module(
                "approve",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)
            previous = (workdir / "approval.json").read_bytes()
            (workdir / "preview.mp4").unlink()

            retry = run_module(
                "approve",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(retry.stdout)
            self.assertEqual(retry.returncode, 5)
            self.assertEqual(payload["error"]["code"], "APPROVAL_REQUIRED")
            self.assertEqual((workdir / "approval.json").read_bytes(), previous)

    def test_formatting_keeps_plan_hash_and_semantics_change_it(self) -> None:
        """Confirma que o hash ignora formatação e muda somente com alteração semântica."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            render_preview(workdir)

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

            document["segments"][0]["audio"]["tracks"][0]["gain_db"] = -1
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


if __name__ == "__main__":
    unittest.main()
