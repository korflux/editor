"""Testes do modo `--accel gpu`: argumentos por modo, teto de decoders, fallback e render real."""

from __future__ import annotations

from contextlib import contextmanager
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
from editor.render.ffmpeg_graph import FfmpegGraph
from editor.render.preview import (
    GPU_MAX_VIDEO_INPUTS,
    _build_ffmpeg_arguments,
    render_preview,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / "tests" / "fixtures" / "workflow"


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    """Cria um trabalho isolado no workspace com o plano e as fontes do cenário de vlog."""

    path = PROJECT_ROOT / f"tmp-gpu-accel-{uuid4().hex}"
    path.mkdir()
    try:
        shutil.copy2(WORKFLOW_DIR / "plan.json", path / "plan.json")
        shutil.copytree(WORKFLOW_DIR / "sources", path / "sources")
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def synthetic_arguments(kinds: list[str], accel: str) -> list[str]:
    """Monta os argumentos para fontes sintéticas, sem executar FFmpeg."""

    ids = tuple(f"s{index}" for index in range(len(kinds)))
    records = [
        {"id": source_id, "kind": kind, "path": f"{source_id}.bin"}
        for source_id, kind in zip(ids, kinds)
    ]
    graph = FfmpegGraph("x", "v", None, ids, False)
    return _build_ffmpeg_arguments(
        graph,
        records,
        Path("out.mp4"),
        filter_script=Path("filter.txt"),
        duration_seconds=1.0,
        frame_rate=30.0,
        accel=accel,
    )


class GpuArgumentsTests(unittest.TestCase):
    """Garante o que cada modo coloca na linha de comando do FFmpeg."""

    def test_cpu_mode_keeps_libx264_without_hwaccel(self) -> None:
        arguments = synthetic_arguments(["video"], "cpu")
        self.assertNotIn("-hwaccel", arguments)
        self.assertEqual(arguments[arguments.index("-c:v") + 1], "libx264")

    def test_gpu_mode_decodes_only_video_inputs_and_encodes_with_nvenc(self) -> None:
        arguments = synthetic_arguments(["video", "image", "audio", "video"], "gpu")
        self.assertEqual(arguments.count("-hwaccel"), 2)
        # Cada -hwaccel precede o -i de uma fonte de vídeo; imagem e áudio ficam em software.
        inputs = [arguments[index + 1] for index, item in enumerate(arguments) if item == "-i"]
        for index, item in enumerate(arguments):
            if item == "-hwaccel":
                self.assertEqual(arguments[index + 1], "cuda")
                self.assertIn(arguments[index + 2], ("-i",))
                self.assertIn(arguments[index + 3], ("s0.bin", "s3.bin"))
        self.assertEqual(inputs, ["s0.bin", "s1.bin", "s2.bin", "s3.bin"])
        self.assertEqual(arguments[arguments.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(arguments[arguments.index("-cq") + 1], "31")
        self.assertIn("yuv420p", arguments)

    def test_gpu_decode_is_skipped_above_the_decoder_limit_but_encoder_stays(self) -> None:
        arguments = synthetic_arguments(["video"] * (GPU_MAX_VIDEO_INPUTS + 1), "gpu")
        self.assertNotIn("-hwaccel", arguments)
        self.assertEqual(arguments[arguments.index("-c:v") + 1], "h264_nvenc")


class GpuFallbackTests(unittest.TestCase):
    """Prova a política de fallback com um FFmpeg que falha quando recebe NVENC."""

    def run_with_failing_gpu(self, failure: Exception | None) -> tuple[dict[str, object] | None, list[list[str]]]:
        """Renderiza com a GPU simulada como indisponível e devolve resultado e chamadas de render."""

        calls: list[list[str]] = []
        original = process.run_tool

        def fake_run_tool(tool: str, arguments: list[str], *, timeout_seconds: float):
            if tool == "ffmpeg" and ("-/filter_complex" in arguments or "-filter_complex" in arguments):
                calls.append(list(arguments))
                if "h264_nvenc" in arguments:
                    if failure is not None:
                        raise failure
                    return subprocess.CompletedProcess(arguments, 1, "", "nvenc indisponivel")
            return original(tool, arguments, timeout_seconds=timeout_seconds)

        with workspace_tempdir() as workdir, patch(
            "editor.render.preview.dependencies.gpu_acceleration_available", return_value=True
        ), patch("editor.render.preview.process.run_tool", side_effect=fake_run_tool):
            try:
                result = render_preview(workdir, accel="gpu")
            except CliError:
                self.assertEqual(list((workdir / ".tmp").iterdir()), [])
                return None, calls
            self.assertTrue((workdir / "preview.mp4").is_file())
            return result, calls

    def test_failure_opening_nvenc_falls_back_to_cpu_and_reports_it(self) -> None:
        result, calls = self.run_with_failing_gpu(None)
        assert result is not None
        self.assertEqual(result["video"]["accel"], "cpu")
        self.assertEqual(len(calls), 2)
        self.assertIn("h264_nvenc", calls[0])
        self.assertIn("libx264", calls[1])

    def test_timeout_does_not_trigger_a_second_full_attempt(self) -> None:
        result, calls = self.run_with_failing_gpu(process.ProcessExecutionError("ffmpeg", timed_out=True))
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)

    def test_unknown_accel_is_rejected_before_rendering(self) -> None:
        with workspace_tempdir() as workdir:
            with self.assertRaises(CliError) as context:
                render_preview(workdir, accel="tpu")
            self.assertEqual(context.exception.code, "ARGUMENT_INVALID")
            self.assertFalse((workdir / "preview.mp4").exists())


@unittest.skipUnless(
    shutil.which("nvidia-smi") and dependencies.gpu_acceleration_available(),
    "requer GPU NVIDIA operante e FFmpeg com NVENC e CUDA",
)
class GpuRealRenderTests(unittest.TestCase):
    """Prova de ponta a ponta na GPU real da máquina, pelo ponto de entrada da CLI."""

    def test_cli_renders_preview_on_gpu_with_valid_output(self) -> None:
        with workspace_tempdir() as workdir:
            result = subprocess.run(
                [sys.executable, "-m", "editor", "render", "preview", "--workdir", str(workdir), "--accel", "gpu", "--json"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            envelope = json.loads(result.stdout)
            self.assertTrue(envelope["ok"], result.stdout)
            self.assertEqual(envelope["data"]["video"]["accel"], "gpu")
            self.assertEqual(envelope["data"]["video"]["codec"], "h264")


if __name__ == "__main__":
    unittest.main()
