"""Testes da extração de frames, do manifesto temporal e da publicação atômica."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from collections.abc import Iterator
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies, media
from editor.analysis.frames import extract_frames
from editor.contracts import CliError
from editor.media import MediaMetadata
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "media" / "main.mp4"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para comprovar o envelope público da T6."""

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

    temporary_path = PROJECT_ROOT / f"tmp-frame-extraction-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        remove_tree(temporary_path)


class FrameExtractionTests(unittest.TestCase):
    """Garante seleção temporal, imagens válidas e ausência de publicação parcial."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFmpeg/FFprobe locais para os cenários de integração da extração."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not MEDIA_FIXTURE.is_file():
            raise unittest.SkipTest("fixture MP4 da extração não está disponível")

    def test_interval_cli_publishes_pngs_and_manifest(self) -> None:
        """Confirma intervalos regulares, caminhos relativos e PNGs completos no manifesto."""

        source_hash_before = hashlib.sha256(MEDIA_FIXTURE.read_bytes()).hexdigest()
        with workspace_tempdir() as workdir:
            result = run_module(
                "extract",
                "frames",
                "--workdir",
                str(workdir),
                "--source-id",
                "main",
                "--input",
                str(MEDIA_FIXTURE),
                "--interval-s",
                "0.5",
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(payload["command"], "extract frames")
            manifest_path = Path(payload["data"]["artifact_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["selection"]["mode"], "interval")
            self.assertEqual(
                [frame["timestamp_requested_s"] for frame in manifest["frames"]],
                [0.0, 0.5, 1.0, 1.5],
            )
            for frame in manifest["frames"]:
                image_path = workdir / Path(frame["path"])
                self.assertTrue(image_path.is_file())
                self.assertEqual(image_path.read_bytes()[: len(PNG_SIGNATURE)], PNG_SIGNATURE)
                self.assertGreaterEqual(frame["timestamp_effective_s"], frame["timestamp_requested_s"])
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])
            self.assertEqual(hashlib.sha256(MEDIA_FIXTURE.read_bytes()).hexdigest(), source_hash_before)

    def test_explicit_timestamps_use_last_frame_before_duration(self) -> None:
        """Confirma timestamps explícitos e o mapeamento do fim da fonte para o último frame."""

        with workspace_tempdir() as workdir:
            result = run_module(
                "extract",
                "frames",
                "--workdir",
                str(workdir),
                "--source-id",
                "main",
                "--input",
                str(MEDIA_FIXTURE),
                "--timestamps",
                "0,0.75,1.99",
                "--json",
            )
            payload = json.loads(result.stdout)
            manifest = json.loads(Path(payload["data"]["artifact_path"]).read_text(encoding="utf-8"))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(manifest["selection"]["mode"], "timestamps")
            self.assertEqual(len(manifest["frames"]), 3)
            self.assertEqual(manifest["frames"][0]["timestamp_effective_s"], 0.0)
            self.assertLess(manifest["frames"][-1]["timestamp_effective_s"], 1.99)

    def test_video_without_audio_is_accepted_for_visual_extraction(self) -> None:
        """Confirma que extração visual não exige áudio quando o vídeo não possui trilha sonora."""

        source = MediaMetadata(
            path=MEDIA_FIXTURE,
            container="mp4",
            audio_codec=None,
            video_codec="h264",
            duration_seconds=2.0,
            size_bytes=MEDIA_FIXTURE.stat().st_size,
            sample_rate=None,
            channels=None,
        )

        def fake_run_tool(
            tool: str,
            arguments: list[str],
            *,
            timeout_seconds: float,
        ) -> subprocess.CompletedProcess[str]:
            """Simula timestamps de vídeo e grava um PNG mínimo para o contrato visual."""

            if tool == "ffprobe":
                return subprocess.CompletedProcess(
                    args=[tool, *arguments],
                    returncode=0,
                    stdout=json.dumps({"frames": [{"best_effort_timestamp_time": "0.0"}]}),
                    stderr="",
                )
            Path(arguments[-1]).write_bytes(PNG_SIGNATURE + b"fixture")
            return subprocess.CompletedProcess(
                args=[tool, *arguments],
                returncode=0,
                stdout="",
                stderr="",
            )

        with workspace_tempdir() as workdir:
            with patch("editor.analysis.frames.media.inspect_source", return_value=source), patch(
                "editor.analysis.frames.process.run_tool", side_effect=fake_run_tool
            ):
                result = extract_frames(
                    workdir,
                    "main",
                    MEDIA_FIXTURE,
                    timestamps="0",
                )

            manifest = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))
            self.assertIsNone(manifest["source"]["audio_codec"])
            self.assertEqual(manifest["frames"][0]["timestamp_effective_s"], 0.0)

    def test_media_validator_allows_video_only_when_audio_is_optional(self) -> None:
        """Confirma no validador compartilhado a distinção entre operação visual e operação de áudio."""

        probe_payload = {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "2.0"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "duration": "2.0"}],
        }
        result = subprocess.CompletedProcess(
            args=["ffprobe"],
            returncode=0,
            stdout=json.dumps(probe_payload),
            stderr="",
        )

        with patch("editor.media.process.run_tool", return_value=result):
            source = media.inspect_source(
                MEDIA_FIXTURE,
                require_audio=False,
                require_video=True,
            )

        self.assertIsNone(source.audio_codec)
        self.assertEqual(source.video_codec, "h264")

    def test_duplicate_timestamps_are_rejected_before_ffprobe(self) -> None:
        """Confirma validação precoce de duplicação sem executar ferramenta externa."""

        with workspace_tempdir() as workdir:
            with patch("editor.process.run_tool") as run_tool:
                with self.assertRaises(CliError) as raised:
                    extract_frames(
                        workdir,
                        "main",
                        MEDIA_FIXTURE,
                        timestamps="0,0.00",
                    )

            self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")
            run_tool.assert_not_called()
            self.assertFalse((workdir / "frames").exists())

    def test_out_of_duration_timestamp_is_rejected_before_frame_extraction(self) -> None:
        """Confirma que timestamp fora da duração falha antes da segunda sondagem e do FFmpeg."""

        source = MediaMetadata(
            path=MEDIA_FIXTURE,
            container="mp4",
            audio_codec="aac",
            video_codec="h264",
            duration_seconds=2.0,
            size_bytes=MEDIA_FIXTURE.stat().st_size,
            sample_rate=48_000,
            channels=1,
        )
        with workspace_tempdir() as workdir:
            with patch("editor.analysis.frames.media.inspect_source", return_value=source), patch(
                "editor.analysis.frames.process.run_tool"
            ) as run_tool:
                with self.assertRaises(CliError) as raised:
                    extract_frames(
                        workdir,
                        "main",
                        MEDIA_FIXTURE,
                        timestamps="2.0",
                    )

            self.assertEqual(raised.exception.code, "MEDIA_LIMIT_EXCEEDED")
            run_tool.assert_not_called()
            self.assertFalse((workdir / "frames").exists())

    def test_cli_rejects_both_selection_modes(self) -> None:
        """Confirma que o contrato público não aceita timestamps e intervalo ao mesmo tempo."""

        with workspace_tempdir() as workdir:
            result = run_module(
                "extract",
                "frames",
                "--workdir",
                str(workdir),
                "--source-id",
                "main",
                "--input",
                str(MEDIA_FIXTURE),
                "--timestamps",
                "0",
                "--interval-s",
                "1",
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")
            self.assertEqual(payload["error"]["code"], "ARGUMENT_INVALID")
            self.assertFalse((workdir / "frames").exists())

    def test_cli_rejects_missing_selection_mode(self) -> None:
        """Confirma que o contrato público exige uma forma explícita de seleção temporal."""

        with workspace_tempdir() as workdir:
            result = run_module(
                "extract",
                "frames",
                "--workdir",
                str(workdir),
                "--source-id",
                "main",
                "--input",
                str(MEDIA_FIXTURE),
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr, "")
            self.assertEqual(payload["error"]["code"], "ARGUMENT_INVALID")
            self.assertFalse((workdir / "frames").exists())

    def test_ffmpeg_failure_preserves_previous_frames_and_cleans_temp(self) -> None:
        """Confirma que falha durante a extração não substitui a pasta publicada."""

        probe_payload = {
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "2.0"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "2.0"},
            ],
        }
        frame_payload = {
            "frames": [
                {"best_effort_timestamp_time": "0.0"},
                {"best_effort_timestamp_time": "1.0"},
            ]
        }

        def fake_run_tool(
            tool: str,
            arguments: list[str],
            *,
            timeout_seconds: float,
        ) -> subprocess.CompletedProcess[str]:
            """Simula probes válidos e falha de FFmpeg sem criar processo externo."""

            if tool == "ffprobe" and "-show_frames" in arguments:
                return subprocess.CompletedProcess(
                    args=[tool, *arguments],
                    returncode=0,
                    stdout=json.dumps(frame_payload),
                    stderr="",
                )
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
            destination = workdir / "frames" / "main"
            destination.mkdir(parents=True)
            previous_manifest = b'{"previous":true}'
            previous_frame = PNG_SIGNATURE + b"previous"
            (destination / "manifest.json").write_bytes(previous_manifest)
            (destination / "frame-0001.png").write_bytes(previous_frame)

            with patch("editor.process.run_tool", side_effect=fake_run_tool):
                with self.assertRaises(CliError) as raised:
                    extract_frames(
                        workdir,
                        "main",
                        MEDIA_FIXTURE,
                        timestamps="0",
                    )

            self.assertEqual(raised.exception.code, "FRAME_EXTRACTION_FAILED")
            self.assertEqual((destination / "manifest.json").read_bytes(), previous_manifest)
            self.assertEqual((destination / "frame-0001.png").read_bytes(), previous_frame)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
