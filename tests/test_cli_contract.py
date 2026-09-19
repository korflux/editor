"""Smoke tests do ponto de entrada real e dos envelopes da CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from editor.contracts import CliError
from editor.workdir import derive_source_id, resolve_workdir


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "media" / "main.mp4"


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa `python -m editor` no diretório do projeto para testar o entry point real."""

    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    """Cria um diretório descartável dentro do workspace para prova isolada sem rede."""

    temporary_path = PROJECT_ROOT / f"tmp-cli-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


def run_module_in_workdir(workdir: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa `python -m editor` com cwd no trabalho para provar o default de `--workdir`."""

    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )


class CliContractTests(unittest.TestCase):
    """Verifica o contrato mínimo antes de qualquer integração de mídia."""

    def test_module_help_is_non_interactive(self) -> None:
        """Confirma que o módulo sobe sem prompt nem diagnóstico em stderr."""

        result = run_module("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: editor", result.stdout)
        self.assertIn("init", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_installed_entry_point_help_is_non_interactive(self) -> None:
        """Confirma que o script instalado aponta para a mesma CLI executável."""

        entry_point = shutil.which("editor")
        self.assertIsNotNone(entry_point, "o entry point deve existir após a instalação editável")

        result = subprocess.run(
            [entry_point, "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: editor", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_json_success_envelope(self) -> None:
        """Confirma os campos obrigatórios de sucesso usando o processo real."""

        result = run_module("--version", "--json")
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "version")
        self.assertEqual(payload["data"]["version"], "1.0.0")

    def test_json_error_envelope_has_no_traceback(self) -> None:
        """Confirma que chamada inválida retorna somente erro JSON seguro no stdout."""

        result = run_module("unknown", "--json")
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(
            set(payload["error"]),
            {"code", "message", "exit_code"},
        )
        self.assertEqual(payload["error"]["code"], "ARGUMENT_INVALID")
        self.assertEqual(payload["error"]["exit_code"], 2)
        self.assertNotIn("Traceback", result.stdout)

    def test_init_without_workdir_uses_current_directory(self) -> None:
        """Confirma que `init` sem `--workdir` prepara o cwd com a mesma validação vigente."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            workdir.mkdir()
            result = run_module_in_workdir(workdir, "init", "--json")
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "init")
            self.assertEqual(payload["data"]["workdir"], str(workdir))
            self.assertEqual(payload["data"]["state"], "ready")
            for relative_path in (".tmp", "analysis", "analysis/audio", "analysis/transcription", "frames"):
                self.assertTrue((workdir / relative_path).is_dir(), relative_path)

            second = run_module_in_workdir(workdir, "init", "--json")
            second_payload = json.loads(second.stdout)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second_payload["data"]["created"], [])
            self.assertEqual(
                second_payload["data"]["existing"],
                [".tmp", "analysis", "analysis/audio", "analysis/transcription", "frames"],
            )

    def test_long_commands_without_workdir_share_validation(self) -> None:
        """Confirma que omitir `--workdir` aplica a mesma validação sem criar estado parcial."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            workdir.mkdir()
            init_result = run_module_in_workdir(workdir, "init", "--json")
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            default = run_module_in_workdir(workdir, "plan", "validate", "--json")
            explicit = run_module(
                "plan", "validate", "--workdir", str(workdir), "--json",
            )
            default_payload = json.loads(default.stdout)
            explicit_payload = json.loads(explicit.stdout)

            self.assertEqual(default.returncode, explicit.returncode)
            self.assertEqual(default_payload["ok"], False)
            self.assertEqual(default_payload["error"]["code"], explicit_payload["error"]["code"])
            self.assertEqual(default_payload["error"]["code"], "PLAN_INVALID")
            self.assertNotIn("Traceback", default.stdout)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_derive_source_id_normalizes_basename(self) -> None:
        """Confirma a derivação determinística do basename para o alfabeto do contrato."""

        self.assertEqual(derive_source_id("My Video.MP4"), "my-video")
        self.assertEqual(derive_source_id("MAIN.mp3"), "main")
        self.assertEqual(str(resolve_workdir(None)), str(Path.cwd()))
        self.assertEqual(resolve_workdir("/tmp/explicito"), "/tmp/explicito")
        with self.assertRaises(CliError) as raised:
            derive_source_id("!!!.mp4")
        self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")

    def test_impossible_derivation_returns_argument_invalid_without_media(self) -> None:
        """Confirma que derivação impossível falha na borda sem sondar mídia nem criar estado."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            workdir.mkdir()
            init_result = run_module("init", "--workdir", str(workdir), "--json")
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            result = run_module(
                "analyze", "audio",
                "--workdir", str(workdir),
                "--input", str(parent / "!!!.mp4"),
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "ARGUMENT_INVALID")
            self.assertNotIn("Traceback", result.stdout)
            self.assertFalse((workdir / "analysis" / "audio").exists() and any((workdir / "analysis" / "audio").iterdir()))

    def test_analyze_audio_default_source_id_matches_explicit(self) -> None:
        """Confirma que omitir `--source-id` e `--workdir` gera o mesmo artefato do longo explícito."""

        from editor import dependencies

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            self.skipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not MEDIA_FIXTURE.is_file():
            self.skipTest("fixture MP4 da análise não está disponível")

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            workdir.mkdir()
            init_result = run_module_in_workdir(workdir, "init", "--json")
            self.assertEqual(init_result.returncode, 0, init_result.stderr)

            fixture_copy = parent / "My Video.mp4"
            shutil.copyfile(MEDIA_FIXTURE, fixture_copy)
            default = run_module_in_workdir(
                workdir, "analyze", "audio", "--input", str(fixture_copy), "--json",
            )
            default_payload = json.loads(default.stdout)

            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertEqual(default_payload["command"], "analyze audio")
            self.assertEqual(default_payload["data"]["source_id"], "my-video")
            artifact = Path(default_payload["data"]["artifact_path"])
            self.assertTrue(artifact.is_file())
            self.assertEqual(artifact.parent.name, "audio")
            self.assertEqual(artifact.name, "my-video.json")
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
