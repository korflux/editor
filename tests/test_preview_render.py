"""Testes da composição FFmpeg, do contrato da preview e da publicação atômica."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies, process
from editor.contracts import CliError
from editor.media import inspect_source
from editor.plan.validation import load_validated_plan
from editor.render.ffmpeg_graph import build_ffmpeg_graph
from editor.render.preview import render_preview


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / "tests" / "fixtures" / "workflow"
WORKFLOW_PLAN = WORKFLOW_DIR / "plan.json"
WORKFLOW_SOURCES = WORKFLOW_DIR / "sources"


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para provar o envelope do render de preview."""

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
    """Cria um trabalho isolado no workspace para não publicar artefatos no fixture versionado."""

    temporary_path = PROJECT_ROOT / f"tmp-preview-render-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


def copy_workflow(workdir: Path) -> None:
    """Copia plano e fontes locais para que cada teste tenha estado de trabalho independente."""

    shutil.copy2(WORKFLOW_PLAN, workdir / "plan.json")
    shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")


def create_motion_source(workdir: Path) -> Path:
    """Cria no trabalho temporário uma fonte espacial para tornar o movimento observável por pixels."""

    output_path = workdir / "sources" / "motion.mp4"
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=25:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return output_path


def use_video_only_motion_fixture(workdir: Path) -> None:
    """Troca a fonte-base pela fixture espacial e remove áudio para isolar a prova visual."""

    create_motion_source(workdir)
    document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
    document["sources"][0]["path"] = "sources/motion.mp4"
    for segment in document["segments"]:
        segment["audio"] = {"mode": "mute", "tracks": []}
    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")


def extract_rgb_frame(path: Path, timestamp: float) -> bytes:
    """Extrai um frame RGB real para comparar o enquadramento publicado pelo render."""

    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(timestamp),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout


def mean_frame_difference(first: bytes, second: bytes) -> float:
    """Calcula a diferença média de pixels sem depender de uma biblioteca de imagens."""

    if len(first) != len(second):
        raise AssertionError("frames com dimensões diferentes")
    return sum(abs(left - right) for left, right in zip(first, second)) / len(first)


class PreviewRenderTests(unittest.TestCase):
    """Garante composição linear, áudio explícito e ausência de publicação parcial."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFmpeg e FFprobe locais para os cenários de integração da T8."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        if not WORKFLOW_PLAN.is_file() or not WORKFLOW_SOURCES.is_dir():
            raise unittest.SkipTest("fixture do workflow não está disponível")

    def test_real_cli_publishes_preview_with_profile_and_audio(self) -> None:
        """Confirma timeline, perfil 16:9, codecs aceitos, áudio e limpeza do temporário."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            source_hashes = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (workdir / "sources").iterdir()
            }
            result = run_module(
                "render",
                "preview",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(payload["command"], "render preview")
            self.assertTrue(payload["ok"])
            data = payload["data"]
            preview_path = Path(data["artifact_path"])
            self.assertEqual(preview_path, workdir / "preview.mp4")
            self.assertTrue(preview_path.is_file())
            self.assertEqual(data["timeline_duration_seconds"], 2.0)
            self.assertEqual(data["cut_count"], 0)
            self.assertEqual(data["layer_count"], 1)
            self.assertNotIn("overlay_count", data)
            self.assertEqual(data["video"]["max_dimension"], 320)
            self.assertEqual(data["audio"], {"codec": "aac", "present": True})

            metadata = inspect_source(preview_path, require_audio=True, require_video=True)
            self.assertEqual((metadata.video_width, metadata.video_height), (320, 180))
            self.assertEqual(metadata.video_codec, "h264")
            self.assertEqual(metadata.audio_codec, "aac")
            self.assertAlmostEqual(metadata.duration_seconds, 2.0, places=1)
            self.assertEqual(
                {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (workdir / "sources").iterdir()
                },
                source_hashes,
            )
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_graph_does_not_use_broll_audio_and_keeps_processing_explicit(self) -> None:
        """Confirma no grafo que B fornece somente imagem e o áudio controla a mixagem."""

        document, validation = load_validated_plan(WORKFLOW_DIR)
        graph = build_ffmpeg_graph(
            document,
            validation,
            canvas_width=320,
            canvas_height=180,
        )

        self.assertIn("[1:v:0]", graph.filter_complex)
        self.assertNotIn("[1:a:0]", graph.filter_complex)
        self.assertIn("[0:a:0]atrim=start=0.75:end=1.25", graph.filter_complex)
        self.assertIn("amix=inputs=2:duration=longest:dropout_transition=0:normalize=0", graph.filter_complex)
        self.assertIn("loudnorm=I=-16", graph.filter_complex)
        self.assertIn("alimiter=", graph.filter_complex)

    def test_real_cli_interpolates_motion_without_changing_canvas_or_duration(self) -> None:
        """Confirma por frames que o movimento altera o enquadramento sem remover o vídeo-base."""

        with workspace_tempdir() as workdir:
            motion_workdir = workdir / "motion"
            static_workdir = workdir / "static"
            motion_workdir.mkdir()
            static_workdir.mkdir()
            copy_workflow(motion_workdir)
            copy_workflow(static_workdir)
            use_video_only_motion_fixture(motion_workdir)
            use_video_only_motion_fixture(static_workdir)

            motion_plan = json.loads((motion_workdir / "plan.json").read_text(encoding="utf-8"))
            motion_plan["segments"][0]["video"]["motion"] = {
                "from": {"x": 0.25, "y": 0.5, "zoom": 1.0},
                "to": {"x": 0.75, "y": 0.5, "zoom": 2.0},
            }
            (motion_workdir / "plan.json").write_text(
                json.dumps(motion_plan),
                encoding="utf-8",
            )

            motion_result = run_module(
                "render",
                "preview",
                "--workdir",
                str(motion_workdir.resolve()),
                "--json",
            )
            static_result = run_module(
                "render",
                "preview",
                "--workdir",
                str(static_workdir.resolve()),
                "--json",
            )
            motion_payload = json.loads(motion_result.stdout)
            static_payload = json.loads(static_result.stdout)

            self.assertEqual(motion_result.returncode, 0, motion_result.stderr)
            self.assertEqual(static_result.returncode, 0, static_result.stderr)
            self.assertEqual(motion_payload["data"]["timeline_duration_seconds"], 2.0)
            self.assertEqual(motion_payload["data"]["video"]["width"], 320)
            self.assertEqual(motion_payload["data"]["video"]["height"], 180)
            self.assertGreater(
                mean_frame_difference(
                    extract_rgb_frame(motion_workdir / "preview.mp4", 0.4),
                    extract_rgb_frame(static_workdir / "preview.mp4", 0.4),
                ),
                5.0,
            )

    def test_real_cli_fills_zoom_out_with_black(self) -> None:
        """Confirma que o afastamento deixa áreas descobertas pretas no canvas fixo."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            use_video_only_motion_fixture(workdir)
            document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
            document["segments"][0]["video"]["motion"] = {
                "from": {"x": 0.5, "y": 0.5, "zoom": 0.5},
                "to": {"x": 0.5, "y": 0.5, "zoom": 0.5},
            }
            (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")
            result = run_module(
                "render",
                "preview",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(result.stdout)
            frame = extract_rgb_frame(workdir / "preview.mp4", 0.3)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertGreater(
                sum(channel < 8 for channel in frame) / len(frame),
                0.5,
            )

    def test_source_cannot_alias_preview_destination(self) -> None:
        """Confirma que a preview vigente nunca pode ser usada como entrada do próprio render."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            render_preview(workdir)
            preview_path = workdir / "preview.mp4"
            previous_hash = hashlib.sha256(preview_path.read_bytes()).hexdigest()

            document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
            document["sources"][0]["path"] = "preview.mp4"
            (workdir / "plan.json").write_text(
                json.dumps(document),
                encoding="utf-8",
            )

            with self.assertRaises(CliError) as raised:
                render_preview(workdir)

            self.assertEqual(raised.exception.code, "OUTPUT_INVALID")
            self.assertEqual(
                hashlib.sha256(preview_path.read_bytes()).hexdigest(),
                previous_hash,
            )
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_real_cli_publishes_video_only_preview_when_plan_mutes_audio(self) -> None:
        """Confirma que `mute` não cria uma faixa silenciosa publicada como áudio real."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
            for segment in document["segments"]:
                segment["audio"] = {"mode": "mute", "tracks": []}
            (workdir / "plan.json").write_text(
                json.dumps(document),
                encoding="utf-8",
            )

            result = run_module(
                "render",
                "preview",
                "--workdir",
                str(workdir.resolve()),
                "--json",
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertFalse(payload["data"]["audio"]["present"])
            metadata = inspect_source(
                workdir / "preview.mp4",
                require_audio=False,
                require_video=True,
            )
            self.assertIsNone(metadata.audio_codec)

    def test_render_failure_preserves_previous_preview_and_cleans_temp(self) -> None:
        """Confirma que uma falha do FFmpeg não substitui a preview vigente nem deixa parcial."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            previous_path = workdir / "preview.mp4"
            previous = b"previous-preview"
            previous_path.write_bytes(previous)
            original_run_tool = process.run_tool

            def fake_run_tool(
                tool: str,
                arguments: list[str],
                *,
                timeout_seconds: float,
            ) -> subprocess.CompletedProcess[str]:
                """Mantém FFprobe real e simula falha somente na renderização."""

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
                    render_preview(workdir)

            self.assertEqual(raised.exception.code, "RENDER_FAILED")
            self.assertEqual(previous_path.read_bytes(), previous)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])
            self.assertFalse((workdir / "preview.mp4.tmp").exists())


if __name__ == "__main__":
    unittest.main()
