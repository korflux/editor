"""Testes do schema estrito, da timeline linear e do hash canônico do plano."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies
from editor.contracts import CliError
from editor.media import MediaMetadata
from editor.plan.canonical import canonical_sha256
from editor.plan.validation import validate_plan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / "tests" / "fixtures" / "workflow"
WORKFLOW_PLAN = WORKFLOW_DIR / "plan.json"
WORKFLOW_SOURCES = WORKFLOW_DIR / "sources"


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para comprovar o envelope público da T7."""

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
    """Cria um trabalho isolado dentro do workspace gerenciado para os testes de erro."""

    temporary_path = PROJECT_ROOT / f"tmp-plan-validation-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


def load_workflow_plan() -> dict[str, object]:
    """Lê o fixture canônico para que os testes de mutação partam de um plano válido."""

    return json.loads(WORKFLOW_PLAN.read_text(encoding="utf-8"))


def write_plan(workdir: Path, document: dict[str, object]) -> None:
    """Escreve somente o plan.json do trabalho de teste, preservando fontes existentes."""

    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")


def fake_inspect_source(
    path: Path,
    *,
    require_audio: bool,
    require_video: bool,
) -> MediaMetadata:
    """Fornece metadados determinísticos para testes que devem parar antes do FFprobe."""

    is_audio_only = path.suffix.lower() == ".mp3"
    return MediaMetadata(
        path=path,
        container="mp3" if is_audio_only else "mp4",
        audio_codec="mp3" if is_audio_only else "aac",
        video_codec=None if is_audio_only else "h264",
        duration_seconds=2.0,
        size_bytes=path.stat().st_size,
        sample_rate=48_000,
        channels=1,
        video_width=None if is_audio_only else 320,
        video_height=None if is_audio_only else 180,
        video_frame_rate=None if is_audio_only else 25.0,
    )


class PlanValidationTests(unittest.TestCase):
    """Garante que somente planos explícitos e temporalmente consistentes sejam aceitos."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFprobe para o cenário de aceitação que valida fontes reais do fixture."""

        if dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffprobe não está disponível localmente")
        if not WORKFLOW_PLAN.is_file() or not WORKFLOW_SOURCES.is_dir():
            raise unittest.SkipTest("fixture do plano não está disponível")

    def test_real_cli_validates_linear_travel_workflow(self) -> None:
        """Confirma fontes reais, timeline concatenada, b-roll, mixagem e lettering."""

        result = run_module(
            "plan",
            "validate",
            "--workdir",
            str(WORKFLOW_DIR.resolve()),
            "--json",
        )
        payload = json.loads(result.stdout)
        data = payload["data"]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["command"], "plan validate")
        self.assertEqual(data["source_count"], 3)
        self.assertEqual(data["segment_count"], 3)
        self.assertEqual(data["overlay_count"], 1)
        self.assertEqual(data["timeline"]["duration_seconds"], 2.0)
        self.assertEqual(
            [segment["video_source_id"] for segment in data["timeline"]["segments"]],
            ["main", "broll-01", "main"],
        )
        self.assertEqual(data["timeline"]["segments"][1]["audio_source_ids"], ["main"])
        self.assertEqual(
            data["timeline"]["segments"][2]["audio_source_ids"],
            ["main", "music-01"],
        )
        self.assertEqual(len(data["plan_sha256"]), 64)
        self.assertEqual(json.loads(data["canonical_json"])["schema_version"], 1)

    def test_canonical_hash_ignores_json_formatting(self) -> None:
        """Confirma que espaços e ordem textual não alteram o hash do plano semântico."""

        document = load_workflow_plan()
        first = canonical_sha256(document)
        formatted = json.dumps(document, ensure_ascii=False, indent=4, sort_keys=True)
        reformatted = json.loads(formatted)

        self.assertEqual(canonical_sha256(reformatted), first)
        self.assertNotIn(" ", json.dumps(reformatted, sort_keys=True, separators=(",", ":")))

    def test_unknown_root_field_is_rejected_before_media_probe(self) -> None:
        """Confirma rejeição precoce de campo desconhecido sem consultar dependências externas."""

        document = load_workflow_plan()
        document["unexpected"] = True
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch("editor.plan.validation.media.inspect_source") as inspect:
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            inspect.assert_not_called()

    def test_relative_source_cannot_escape_workdir(self) -> None:
        """Confirma que um caminho relativo com traversal falha antes da inspeção de mídia."""

        document = load_workflow_plan()
        document["sources"][0]["path"] = "../outside.mp4"
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch("editor.plan.validation.media.inspect_source") as inspect:
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            inspect.assert_not_called()

    def test_broll_cannot_keep_audio_from_a_different_source(self) -> None:
        """Confirma a regra que exige áudio explícito de A quando B ocupa a imagem."""

        document = load_workflow_plan()
        segment = document["segments"][1]
        segment["audio"]["mode"] = "keep"
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch(
                "editor.plan.validation.media.inspect_source",
                side_effect=fake_inspect_source,
            ):
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_invalid_overlay_interval_is_rejected(self) -> None:
        """Confirma que um lettering não pode ultrapassar a duração calculada da timeline."""

        document = load_workflow_plan()
        document["overlays"][0]["end_s"] = 2.01
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch(
                "editor.plan.validation.media.inspect_source",
                side_effect=fake_inspect_source,
            ):
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_nul_in_overlay_returns_plan_invalid_envelope(self) -> None:
        """Confirma que controle NUL falha na validação antes de alcançar o filtro FFmpeg."""
        document = load_workflow_plan()
        document["overlays"][0]["text"] = "texto\x00inválido"
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            result = run_module(
                "plan",
                "validate",
                "--workdir",
                str(workdir),
                "--json",
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["error"]["code"], "PLAN_INVALID")

    def test_cli_returns_plan_invalid_envelope_for_malformed_json(self) -> None:
        """Confirma que JSON malformado vira erro estruturado sem traceback no stdout."""

        with workspace_tempdir() as workdir:
            (workdir / "plan.json").write_text("{", encoding="utf-8")
            result = run_module(
                "plan",
                "validate",
                "--workdir",
                str(workdir),
                "--json",
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["error"]["code"], "PLAN_INVALID")


if __name__ == "__main__":
    unittest.main()
