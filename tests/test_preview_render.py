"""Testes da composição FFmpeg, do contrato da preview e da publicação atômica."""

from __future__ import annotations

from array import array
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
IMAGE_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "phase-1" / "images"


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


def mean_region_difference(
    first: bytes, second: bytes, left: int, top: int, right: int, bottom: int
) -> float:
    """Mede a alteração real em uma região do canvas 320x180 para provar o recorte."""

    differences = (
        abs(first[(row * 320 + column) * 3 + channel] - second[(row * 320 + column) * 3 + channel])
        for row in range(top, bottom)
        for column in range(left, right)
        for channel in range(3)
    )
    return sum(differences) / ((right - left) * (bottom - top) * 3)


def extract_mono_samples(path: Path) -> list[int]:
    """Extrai PCM mono para medir a presença de áudio em janelas temporais reais."""

    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        raise AssertionError(result.stderr.decode(errors="replace"))
    samples = array("h")
    samples.frombytes(result.stdout)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tolist()


def mean_audio_level(samples: list[int], start_s: float, end_s: float) -> float:
    """Calcula o nível médio absoluto de uma janela sem depender de bibliotecas de áudio."""

    start = int(start_s * 48_000)
    end = min(len(samples), int(end_s * 48_000))
    window = samples[start:end]
    if not window:
        return 0.0
    return sum(abs(sample) for sample in window) / len(window)


def configure_j_l_audio_plan(workdir: Path, *, with_cuts: bool) -> None:
    """Prepara planos comparáveis, mantendo áudio no primeiro grupo e isolando a fronteira L."""

    document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
    document["segments"][2]["audio"] = {"mode": "mute", "tracks": []}
    document["audio_processing"] = {
        "normalize_loudness": False,
        "target_lufs": -16,
        "limiter": {"enabled": False, "ceiling_dbfs": -1},
        "ducking": False,
    }
    if with_cuts:
        document["segments"][1]["audio"]["tracks"][0]["in_s"] = 0.5
        document["segments"][1]["audio"]["tracks"][0]["out_s"] = 1.4
        document["cuts"] = [
            {"after_segment": 1, "type": "j", "duration_s": 0.2},
            {"after_segment": 2, "type": "l", "duration_s": 0.2},
        ]
    else:
        document["cuts"] = []
    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")


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

    def test_text_layer_respects_box_interval_and_underlying_video(self) -> None:
        """Prova por frames que texto longo fica na caixa e só aparece no intervalo declarado."""

        with workspace_tempdir() as workdir:
            layered = workdir / "layered"
            baseline = workdir / "baseline"
            layered.mkdir()
            baseline.mkdir()
            for target in (layered, baseline):
                copy_workflow(target)
                use_video_only_motion_fixture(target)
            document = json.loads((layered / "plan.json").read_text(encoding="utf-8"))
            document["layers"] = [{
                "type": "text", "start_s": 0.5, "end_s": 1.25,
                "x": 0.1, "y": 0.2, "width": 0.25, "height": 0.2,
                "text": "MMMMMMMMMMMMMMMM\nSECOND-LINE", "font": "Arial",
                "size_px": 42, "color": "#FFFFFF", "align": "left", "wrap": False,
            }]
            (layered / "plan.json").write_text(json.dumps(document), encoding="utf-8")
            baseline_document = json.loads((baseline / "plan.json").read_text(encoding="utf-8"))
            baseline_document["layers"] = []
            (baseline / "plan.json").write_text(json.dumps(baseline_document), encoding="utf-8")

            layered_result = run_module("render", "preview", "--workdir", str(layered.resolve()), "--json")
            baseline_result = run_module("render", "preview", "--workdir", str(baseline.resolve()), "--json")
            self.assertEqual(layered_result.returncode, 0, layered_result.stderr)
            self.assertEqual(baseline_result.returncode, 0, baseline_result.stderr)
            self.assertEqual(json.loads(layered_result.stdout)["data"]["layer_count"], 1)

            active = extract_rgb_frame(layered / "preview.mp4", 0.75)
            plain = extract_rgb_frame(baseline / "preview.mp4", 0.75)
            self.assertGreater(mean_region_difference(active, plain, 36, 40, 105, 66), 5)
            self.assertLess(mean_region_difference(active, plain, 125, 40, 200, 66), 2)
            self.assertLess(mean_region_difference(active, plain, 36, 85, 105, 110), 2)
            self.assertLess(mean_region_difference(active, plain, 240, 120, 300, 160), 2)
            for timestamp in (0.25, 1.5):
                self.assertLess(
                    mean_frame_difference(
                        extract_rgb_frame(layered / "preview.mp4", timestamp),
                        extract_rgb_frame(baseline / "preview.mp4", timestamp),
                    ),
                    2,
                )

            document["layers"][0].update({
                "text": "ONE TWO THREE FOUR FIVE SIX", "size_px": 24,
                "height": 0.4, "wrap": True, "align": "right",
            })
            (layered / "plan.json").write_text(json.dumps(document), encoding="utf-8")
            wrapped_document, wrapped_validation = load_validated_plan(layered)
            wrapped_graph = build_ffmpeg_graph(
                wrapped_document, wrapped_validation, canvas_width=320, canvas_height=180
            )
            self.assertIn("x=w-text_w", wrapped_graph.filter_complex)
            wrapped_result = run_module("render", "preview", "--workdir", str(layered.resolve()), "--json")
            self.assertEqual(wrapped_result.returncode, 0, wrapped_result.stderr)
            self.assertGreater(
                mean_region_difference(
                    extract_rgb_frame(layered / "preview.mp4", 0.75), plain,
                    36, 75, 105, 105,
                ),
                3,
            )
            document["layers"][0].update({
                "text": "A:B,C;[x]'\\z", "wrap": False, "align": "center",
            })
            (layered / "plan.json").write_text(json.dumps(document), encoding="utf-8")
            escaped_result = run_module("render", "preview", "--workdir", str(layered.resolve()), "--json")
            self.assertEqual(escaped_result.returncode, 0, escaped_result.stderr)

    def test_image_layers_preserve_alpha_fit_interval_order_and_audio_contract(self) -> None:
        """Compara frames reais de contain, crop e z-order sem deixar imagem criar áudio."""

        with workspace_tempdir() as workdir:
            layered = workdir / "layered"
            baseline = workdir / "baseline"
            layered.mkdir()
            baseline.mkdir()
            for target in (layered, baseline):
                copy_workflow(target)
                use_video_only_motion_fixture(target)
            document = json.loads((layered / "plan.json").read_text(encoding="utf-8"))
            for source_id, name in (("alpha", "alpha.png"), ("blue", "blue.png")):
                shutil.copy2(IMAGE_FIXTURES / name, layered / "sources" / name)
                document["sources"].append({
                    "id": source_id, "kind": "image", "path": f"sources/{name}"
                })
            image_layer = {
                "type": "image", "start_s": 0.5, "end_s": 1.25,
                "x": 0.1, "y": 0.2, "width": 0.25, "height": 0.4,
                "source_id": "alpha", "fit": "contain",
            }
            document["layers"] = [image_layer]
            (layered / "plan.json").write_text(json.dumps(document), encoding="utf-8")
            baseline_document = json.loads((baseline / "plan.json").read_text(encoding="utf-8"))
            baseline_document["layers"] = []
            (baseline / "plan.json").write_text(json.dumps(baseline_document), encoding="utf-8")

            layered_result = run_module("render", "preview", "--workdir", str(layered.resolve()), "--json")
            baseline_result = run_module("render", "preview", "--workdir", str(baseline.resolve()), "--json")
            self.assertEqual(layered_result.returncode, 0, layered_result.stderr)
            self.assertEqual(baseline_result.returncode, 0, baseline_result.stderr)
            self.assertEqual(json.loads(layered_result.stdout)["data"]["audio"]["present"], False)
            active = extract_rgb_frame(layered / "preview.mp4", 0.75)
            plain = extract_rgb_frame(baseline / "preview.mp4", 0.75)
            self.assertGreater(mean_region_difference(active, plain, 40, 58, 50, 65), 5)
            self.assertLess(mean_region_difference(active, plain, 75, 58, 85, 65), 2)
            for timestamp in (0.25, 1.5):
                self.assertLess(mean_frame_difference(
                    extract_rgb_frame(layered / "preview.mp4", timestamp),
                    extract_rgb_frame(baseline / "preview.mp4", timestamp),
                ), 2)

            image_layer["fit"] = "crop"
            (layered / "plan.json").write_text(json.dumps(document), encoding="utf-8")
            crop_result = run_module("render", "preview", "--workdir", str(layered.resolve()), "--json")
            self.assertEqual(crop_result.returncode, 0, crop_result.stderr)
            crop = extract_rgb_frame(layered / "preview.mp4", 0.75)
            self.assertGreater(mean_region_difference(crop, active, 40, 58, 50, 65), 3)

            document["layers"].append({**image_layer, "source_id": "blue", "fit": "crop"})
            (layered / "plan.json").write_text(json.dumps(document), encoding="utf-8")
            stacked_result = run_module("render", "preview", "--workdir", str(layered.resolve()), "--json")
            self.assertEqual(stacked_result.returncode, 0, stacked_result.stderr)
            stacked = extract_rgb_frame(layered / "preview.mp4", 0.75)
            self.assertGreater(mean_region_difference(stacked, crop, 40, 58, 50, 65), 5)

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

    def test_real_cli_positions_j_and_l_audio_on_global_timeline(self) -> None:
        """Confirma por janelas reais a antecipação J, a extensão L e a mixagem sem ducking."""

        with workspace_tempdir() as workdir:
            cut_workdir = workdir / "cuts"
            static_workdir = workdir / "static"
            cut_workdir.mkdir()
            static_workdir.mkdir()
            copy_workflow(cut_workdir)
            copy_workflow(static_workdir)
            configure_j_l_audio_plan(cut_workdir, with_cuts=True)
            configure_j_l_audio_plan(static_workdir, with_cuts=False)

            cut_result = run_module(
                "render",
                "preview",
                "--workdir",
                str(cut_workdir.resolve()),
                "--json",
            )
            static_result = run_module(
                "render",
                "preview",
                "--workdir",
                str(static_workdir.resolve()),
                "--json",
            )
            cut_payload = json.loads(cut_result.stdout)
            static_payload = json.loads(static_result.stdout)

            self.assertEqual(cut_result.returncode, 0, cut_result.stderr)
            self.assertEqual(static_result.returncode, 0, static_result.stderr)
            self.assertEqual(cut_payload["data"]["cut_count"], 2)
            self.assertEqual(cut_payload["data"]["timeline_duration_seconds"], 2.0)
            self.assertTrue(cut_payload["data"]["audio"]["present"])
            self.assertAlmostEqual(
                inspect_source(
                    cut_workdir / "preview.mp4",
                    require_audio=True,
                    require_video=True,
                ).duration_seconds,
                2.0,
                places=1,
            )

            _, validation = load_validated_plan(cut_workdir)
            graph = build_ffmpeg_graph(
                json.loads((cut_workdir / "plan.json").read_text(encoding="utf-8")),
                validation,
                canvas_width=320,
                canvas_height=180,
            )
            self.assertIn("adelay=550|550", graph.filter_complex)
            self.assertIn(
                "amix=inputs=2:duration=longest:dropout_transition=0:normalize=0",
                graph.filter_complex,
            )
            self.assertIn("atrim=start=0:end=2", graph.filter_complex)
            self.assertNotIn("acrossfade", graph.filter_complex)
            self.assertNotIn("sidechaincompress", graph.filter_complex)

            cut_samples = extract_mono_samples(cut_workdir / "preview.mp4")
            static_samples = extract_mono_samples(static_workdir / "preview.mp4")
            cut_early = mean_audio_level(cut_samples, 0.60, 0.70)
            static_early = mean_audio_level(static_samples, 0.60, 0.70)
            cut_late = mean_audio_level(cut_samples, 1.30, 1.40)
            static_late = mean_audio_level(static_samples, 1.30, 1.40)

            self.assertGreater(cut_early, static_early * 1.4)
            self.assertGreater(cut_late, max(static_late * 4, 100))

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
