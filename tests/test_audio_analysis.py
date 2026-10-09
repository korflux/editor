"""Testes do contrato real de análise de áudio e de seus guardrails."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from editor.analysis.audio import MAX_AUDIO_WINDOWS, analyze_audio
from editor.contracts import CliError
from editor import dependencies
from editor.media import MAX_SOURCE_SIZE_BYTES
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "media" / "main.mp4"


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa a CLI em processo separado para comprovar o contrato de stdout."""

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
    """Cria e remove um trabalho isolado dentro do workspace gerenciado."""

    temporary_path = PROJECT_ROOT / f"tmp-audio-analysis-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        remove_tree(temporary_path)


class AudioAnalysisTests(unittest.TestCase):
    """Garante análise determinística, rejeição precoce e publicação sem estado parcial."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFmpeg local para os cenários que exercitam a integração real."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not MEDIA_FIXTURE.is_file():
            raise unittest.SkipTest("fixture MP4 da análise não está disponível")

    def test_real_cli_publishes_audio_analysis(self) -> None:
        """Confirma o envelope, o artefato canônico, as janelas e os silêncios detectados."""

        with workspace_tempdir() as workdir:
            result = run_module(
                "analyze",
                "audio",
                "--workdir",
                str(workdir),
                "--source-id",
                "main",
                "--input",
                str(MEDIA_FIXTURE),
                "--window-s",
                "0.5",
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(payload["command"], "analyze audio")
            artifact_path = Path(payload["data"]["artifact_path"])
            self.assertTrue(artifact_path.is_file())
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(artifact["schema_version"], 1)
            self.assertEqual(artifact["source"]["id"], "main")
            self.assertEqual(len(artifact["source"]["sha256"]), 64)
            self.assertEqual(len(artifact["audio"]["windows"]), 4)
            self.assertGreaterEqual(len(artifact["audio"]["silence"]), 2)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_invalid_window_is_rejected_before_media_probe(self) -> None:
        """Confirma que uma janela inválida não chama FFprobe nem cria artefato."""

        with workspace_tempdir() as workdir:
            with patch("editor.process.run_tool") as run_tool:
                with self.assertRaises(CliError) as raised:
                    analyze_audio(workdir, "main", MEDIA_FIXTURE, window_seconds=0)

            self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")
            run_tool.assert_not_called()
            self.assertFalse((workdir / "analysis").exists())

    def test_unrepresentable_window_is_rejected_before_media_probe(self) -> None:
        """Confirma que valores enorme e subnormal não chegam ao processamento de áudio."""

        for value in (1e308, 1e-300):
            with self.subTest(window_seconds=value):
                with workspace_tempdir() as workdir:
                    with patch("editor.analysis.audio.media.inspect_source") as inspect:
                        with self.assertRaises(CliError) as raised:
                            analyze_audio(
                                workdir,
                                "main",
                                MEDIA_FIXTURE,
                                window_seconds=value,
                            )

                    self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")
                    inspect.assert_not_called()

    def test_window_count_limit_is_rejected_before_processing(self) -> None:
        """Confirma que uma janela pequena não cria um artefato temporal desproporcional."""

        with workspace_tempdir() as workdir:
            with patch(
                "editor.analysis.audio.media.inspect_source",
                return_value=SimpleNamespace(duration_seconds=3_600.0),
            ), patch("editor.analysis.audio.media.sha256_file") as sha256_file:
                with self.assertRaises(CliError) as raised:
                    analyze_audio(
                        workdir,
                        "main",
                        MEDIA_FIXTURE,
                        window_seconds=0.001,
                    )

            self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")
            sha256_file.assert_not_called()

    def test_source_above_size_limit_is_rejected_before_ffprobe(self) -> None:
        """Mantém o teto de 4 GiB na ingestão mesmo após separar a política de render."""

        oversized = SimpleNamespace(st_size=MAX_SOURCE_SIZE_BYTES + 1)
        with workspace_tempdir() as workdir, patch(
            "editor.media.Path.stat", return_value=oversized
        ), patch("editor.media.process.run_tool") as run_tool:
            with self.assertRaises(CliError) as raised:
                analyze_audio(workdir, "main", MEDIA_FIXTURE)

        self.assertEqual(raised.exception.code, "MEDIA_LIMIT_EXCEEDED")
        run_tool.assert_not_called()

    def test_window_count_limit_accounts_for_sample_rounding(self) -> None:
        """Confirma que o limite usa a janela quantizada em amostras na fronteira exata."""
        boundary_window = 3_600.0 / MAX_AUDIO_WINDOWS
        with workspace_tempdir() as workdir:
            with patch(
                "editor.analysis.audio.media.inspect_source",
                return_value=SimpleNamespace(duration_seconds=3_600.0),
            ), patch("editor.analysis.audio.media.sha256_file") as sha256_file, patch(
                "editor.analysis.audio.process.stream_pcm"
            ) as stream_pcm:
                with self.assertRaises(CliError) as raised:
                    analyze_audio(
                        workdir,
                        "main",
                        MEDIA_FIXTURE,
                        window_seconds=boundary_window,
                    )

            self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")
            sha256_file.assert_not_called()
            stream_pcm.assert_not_called()

    def test_unsupported_extension_is_rejected_before_ffmpeg(self) -> None:
        """Confirma que formatos fora da allowlist falham antes de qualquer subprocesso."""

        with workspace_tempdir() as workdir:
            unsupported = workdir / "source.txt"
            unsupported.write_text("not media", encoding="utf-8")
            with patch("editor.process.run_tool") as run_tool:
                with self.assertRaises(CliError) as raised:
                    analyze_audio(workdir, "main", unsupported)

            self.assertEqual(raised.exception.code, "MEDIA_UNSUPPORTED")
            run_tool.assert_not_called()
            self.assertFalse((workdir / "analysis").exists())

    def test_ffmpeg_failure_preserves_existing_artifact_and_cleans_temp(self) -> None:
        """Confirma que falha simulada não substitui análise vigente nem publica parcial."""

        probe_payload = {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "2.0"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "duration": "2.0",
                    "sample_rate": "48000",
                    "channels": 1,
                },
            ],
        }

        def fake_run_tool(
            tool: str,
            arguments: list[str],
            *,
            timeout_seconds: float,
        ) -> subprocess.CompletedProcess[str]:
            """Simula FFprobe válido e falha do filtro FFmpeg sem executar processo externo."""

            if tool == "ffprobe":
                return subprocess.CompletedProcess(
                    args=[tool, *arguments],
                    returncode=0,
                    stdout=json.dumps(probe_payload),
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=[tool, *arguments],
                returncode=1,
                stdout="",
                stderr="simulated failure",
            )

        with workspace_tempdir() as workdir:
            destination = workdir / "analysis" / "audio" / "main.json"
            destination.parent.mkdir(parents=True)
            previous = b'{"previous":true}'
            destination.write_bytes(previous)
            plan = workdir / "plan.json"
            plan_bytes = b'{"schema_version":1}'
            plan.write_bytes(plan_bytes)
            source_hash_before = hashlib.sha256(MEDIA_FIXTURE.read_bytes()).hexdigest()

            with patch("editor.process.run_tool", side_effect=fake_run_tool):
                with self.assertRaises(CliError) as raised:
                    analyze_audio(workdir, "main", MEDIA_FIXTURE)

            self.assertEqual(raised.exception.code, "ANALYSIS_FAILED")
            self.assertEqual(destination.read_bytes(), previous)
            self.assertEqual(plan.read_bytes(), plan_bytes)
            self.assertEqual(hashlib.sha256(MEDIA_FIXTURE.read_bytes()).hexdigest(), source_hash_before)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
