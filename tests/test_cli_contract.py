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
from editor.media import (
    PLAN_AUDIO_SOURCE_EXTENSIONS,
    SOURCE_VIDEO_CODECS,
    SUPPORTED_SOURCE_EXTENSIONS,
    VIDEO_SOURCE_EXTENSIONS,
)
from editor.workdir import derive_source_id, resolve_workdir
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "media" / "main.mp4"


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa `python -m editor` no diretório do projeto para testar o entry point real."""

    # A CLI fixa stdout e stderr em UTF-8 (`configure_output_encoding`); decodificar pela página de
    # código local corromperia a ajuda em PT-BR (acentos) no Windows.
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
    """Cria um diretório descartável dentro do workspace para prova isolada sem rede."""

    temporary_path = PROJECT_ROOT / f"tmp-cli-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        remove_tree(temporary_path)


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


    def test_shortcuts_in_help_and_longs_remain(self) -> None:
        """Confirma que os atalhos aparecem no help sem remover os longos canônicos."""

        result = run_module("--help")

        self.assertEqual(result.returncode, 0)
        for shortcut in ("audio", "transcribe", "frames", "validate", "preview", "full"):
            self.assertIn(shortcut, result.stdout)
        for long in ("analyze", "extract", "plan", "render", "init", "approve", "capabilities"):
            self.assertIn(long, result.stdout)
        self.assertEqual(result.stderr, "")

    def test_audio_shortcut_matches_long_canonical_envelope(self) -> None:
        """Confirma que `audio` gera o mesmo artefato de `analyze audio` com envelope canônico."""

        from editor import dependencies

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            self.skipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not MEDIA_FIXTURE.is_file():
            self.skipTest("fixture MP4 da análise não está disponível")

        with workspace_tempdir() as parent:
            long_dir = parent / "long"
            short_dir = parent / "short"
            long_dir.mkdir()
            short_dir.mkdir()
            self.assertEqual(run_module("init", "--workdir", str(long_dir), "--json").returncode, 0)
            self.assertEqual(run_module("init", "--workdir", str(short_dir), "--json").returncode, 0)

            long = run_module(
                "analyze", "audio",
                "--workdir", str(long_dir),
                "--source-id", "main",
                "--input", str(MEDIA_FIXTURE),
                "--json",
            )
            short = run_module(
                "audio",
                "--workdir", str(short_dir),
                "--source-id", "main",
                "--input", str(MEDIA_FIXTURE),
                "--json",
            )
            long_payload = json.loads(long.stdout)
            short_payload = json.loads(short.stdout)

            self.assertEqual(long.returncode, 0, long.stderr)
            self.assertEqual(short.returncode, 0, short.stderr)
            self.assertEqual(long_payload["command"], "analyze audio")
            self.assertEqual(short_payload["command"], "analyze audio")
            long_artifact = Path(long_payload["data"]["artifact_path"])
            short_artifact = Path(short_payload["data"]["artifact_path"])
            self.assertTrue(long_artifact.is_file())
            self.assertTrue(short_artifact.is_file())
            self.assertEqual(long_artifact.read_bytes(), short_artifact.read_bytes())

    def test_audio_shortcut_without_workdir_uses_current_directory(self) -> None:
        """Confirma que o atalho `audio` resolve o cwd como os longos de T5."""

        from editor import dependencies

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            self.skipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not MEDIA_FIXTURE.is_file():
            self.skipTest("fixture MP4 da análise não está disponível")

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            workdir.mkdir()
            self.assertEqual(run_module_in_workdir(workdir, "init", "--json").returncode, 0)

            fixture_copy = parent / "My Video.mp4"
            shutil.copyfile(MEDIA_FIXTURE, fixture_copy)
            result = run_module_in_workdir(workdir, "audio", "--input", str(fixture_copy), "--json")
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(payload["command"], "analyze audio")
            self.assertEqual(payload["data"]["source_id"], "my-video")
            self.assertTrue(Path(payload["data"]["artifact_path"]).is_file())

    def test_validate_shortcut_shares_validation(self) -> None:
        """Confirma que `validate` usa o mesmo envelope e validação de `plan validate`."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            workdir.mkdir()
            self.assertEqual(run_module("init", "--workdir", str(workdir), "--json").returncode, 0)

            long = run_module("plan", "validate", "--workdir", str(workdir), "--json")
            short = run_module("validate", "--workdir", str(workdir), "--json")
            long_payload = json.loads(long.stdout)
            short_payload = json.loads(short.stdout)

            self.assertEqual(long.returncode, short.returncode)
            self.assertFalse(long_payload["ok"])
            self.assertFalse(short_payload["ok"])
            self.assertEqual(long_payload["error"]["code"], "PLAN_INVALID")
            self.assertEqual(short_payload["error"]["code"], "PLAN_INVALID")
            self.assertNotIn("Traceback", short.stdout)

    def test_transcribe_shortcut_impossible_derivation(self) -> None:
        """Confirma que o atalho `transcribe` falha na borda como o longo sem sondar mídia."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            workdir.mkdir()
            self.assertEqual(run_module("init", "--workdir", str(workdir), "--json").returncode, 0)

            result = run_module(
                "transcribe",
                "--workdir", str(workdir),
                "--input", str(parent / "!!!.mp4"),
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "ARGUMENT_INVALID")
            self.assertNotIn("Traceback", result.stdout)

    def test_frames_shortcut_matches_long_manifest(self) -> None:
        """Confirma que `frames` publica o mesmo manifesto de `extract frames`."""

        from editor import dependencies

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            self.skipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not MEDIA_FIXTURE.is_file():
            self.skipTest("fixture MP4 da análise não está disponível")

        with workspace_tempdir() as parent:
            long_dir = parent / "long"
            short_dir = parent / "short"
            long_dir.mkdir()
            short_dir.mkdir()
            self.assertEqual(run_module("init", "--workdir", str(long_dir), "--json").returncode, 0)
            self.assertEqual(run_module("init", "--workdir", str(short_dir), "--json").returncode, 0)

            long = run_module(
                "extract", "frames",
                "--workdir", str(long_dir),
                "--source-id", "main",
                "--input", str(MEDIA_FIXTURE),
                "--timestamps", "0",
                "--json",
            )
            short = run_module(
                "frames",
                "--workdir", str(short_dir),
                "--source-id", "main",
                "--input", str(MEDIA_FIXTURE),
                "--timestamps", "0",
                "--json",
            )
            long_payload = json.loads(long.stdout)
            short_payload = json.loads(short.stdout)

            self.assertEqual(long.returncode, 0, long.stderr)
            self.assertEqual(short.returncode, 0, short.stderr)
            self.assertEqual(long_payload["command"], "extract frames")
            self.assertEqual(short_payload["command"], "extract frames")
            long_manifest = Path(long_payload["data"]["artifact_path"])
            short_manifest = Path(short_payload["data"]["artifact_path"])
            self.assertTrue(long_manifest.is_file())
            self.assertTrue(short_manifest.is_file())

    def test_preview_and_full_shortcuts_share_validation(self) -> None:
        """Confirma que `preview` e `full` retornam o mesmo erro dos longos sem plano válido."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            workdir.mkdir()
            self.assertEqual(run_module("init", "--workdir", str(workdir), "--json").returncode, 0)

            long_preview = run_module("render", "preview", "--workdir", str(workdir), "--json")
            short_preview = run_module("preview", "--workdir", str(workdir), "--json")
            long_full = run_module("render", "full", "--workdir", str(workdir), "--json")
            short_full = run_module("full", "--workdir", str(workdir), "--json")

            self.assertEqual(long_preview.returncode, short_preview.returncode)
            self.assertEqual(long_full.returncode, short_full.returncode)
            self.assertEqual(
                json.loads(long_preview.stdout)["error"]["code"],
                json.loads(short_preview.stdout)["error"]["code"],
            )
            self.assertEqual(
                json.loads(long_full.stdout)["error"]["code"],
                json.loads(short_full.stdout)["error"]["code"],
            )
            self.assertNotIn("Traceback", short_preview.stdout)
            self.assertNotIn("Traceback", short_full.stdout)


# Nome com que a ajuda da CLI cita cada codec de `SOURCE_VIDEO_CODECS`. Um codec novo no contrato de
# mídia sem entrada aqui falha o teste com `KeyError`, o que obriga a revisar o texto da ajuda.
CODEC_HELP_NAMES = {"h264": "H.264", "hevc": "HEVC"}


def _joined_with_ou(items: list[str]) -> str:
    """Junta rótulos como a ajuda em PT-BR escreve: `A ou B` para dois e `A, B ou C` para três ou mais."""

    return f"{', '.join(items[:-1])} ou {items[-1]}"


def _container_labels(suffixes: tuple[str, ...]) -> list[str]:
    """Converte extensões do contrato de mídia (`.mp4`) nos rótulos da ajuda (`MP4`), na mesma ordem."""

    return [suffix.removeprefix(".").upper() for suffix in suffixes]


class InputHelpContractTests(unittest.TestCase):
    """A ajuda de `--input` descreve o contrato real de `media.py`, nos atalhos e nos subcomandos longos."""

    # `audio`/`transcribe` aceitam toda a allowlist de fontes; `frames` só as fontes com vídeo.
    AUDIO_INPUT_COMMANDS = (("audio",), ("analyze", "audio"), ("transcribe",), ("analyze", "transcribe"))
    FRAMES_INPUT_COMMANDS = (("frames",), ("extract", "frames"))

    def help_text(self, command: tuple[str, ...]) -> str:
        """Executa `<comando> --help` no processo real e devolve a saída sem as quebras de linha do argparse."""

        result = run_module(*command, "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return " ".join(result.stdout.split())

    def test_audio_and_transcribe_help_list_every_accepted_container(self) -> None:
        """MP4, MOV, MP3 e WAV nos quatro comandos, lidos das constantes da allowlist de fontes."""

        # A análise de áudio usa a allowlist inteira; a ordem da ajuda é vídeo primeiro, depois áudio.
        self.assertEqual({*VIDEO_SOURCE_EXTENSIONS, *PLAN_AUDIO_SOURCE_EXTENSIONS}, SUPPORTED_SOURCE_EXTENSIONS)
        suffixes = (*VIDEO_SOURCE_EXTENSIONS, *sorted(PLAN_AUDIO_SOURCE_EXTENSIONS))
        expected = f"arquivo local {_joined_with_ou(_container_labels(suffixes))}"

        for command in self.AUDIO_INPUT_COMMANDS:
            with self.subTest(command=" ".join(command)):
                self.assertIn(expected, self.help_text(command))

    def test_frames_help_lists_containers_and_video_codecs(self) -> None:
        """MP4 ou MOV com vídeo H.264 ou HEVC nos dois comandos de frames, lidos das constantes de vídeo."""

        containers = _joined_with_ou(_container_labels(VIDEO_SOURCE_EXTENSIONS))
        codecs = _joined_with_ou([CODEC_HELP_NAMES[codec] for codec in SOURCE_VIDEO_CODECS])
        expected = f"arquivo local {containers} com vídeo {codecs}"

        for command in self.FRAMES_INPUT_COMMANDS:
            with self.subTest(command=" ".join(command)):
                self.assertIn(expected, self.help_text(command))


if __name__ == "__main__":
    unittest.main()
