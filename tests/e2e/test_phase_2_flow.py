"""Prova E2E da Phase 2 com plano detalhado, perfil calculado e grafo por arquivo."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from editor import dependencies
from editor.media import inspect_rendered_artifact
from editor.plan.validation import load_validated_plan
from editor.render.ffmpeg_graph import build_ffmpeg_graph
from tests.e2e.test_mvp_flow import (
    PROJECT_ROOT,
    assert_json_error,
    assert_json_success,
    run_cli,
    source_hashes,
    workspace_tempdir,
)


WORKFLOW_SOURCES = PROJECT_ROOT / "tests" / "fixtures" / "workflow" / "sources"
IMAGE_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "phase-1" / "images" / "alpha.png"
VIDEO_COUNT = 60
AUDIO_COUNT = 6
IMAGE_COUNT = 3
SEGMENT_COUNT = 231
SEGMENT_DURATION_S = 0.2
TEXT_LAYERS = 15
IMAGE_LAYERS = 14
VIDEO_LAYERS = 14
TIMELINE_DURATION_S = round(SEGMENT_COUNT * SEGMENT_DURATION_S, 6)


def copy_scale_fixture(workdir: Path) -> None:
    """Repete mídias pequenas locais para atingir 69 fontes sem versionar arquivos grandes."""

    source_dir = workdir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    for index in range(VIDEO_COUNT):
        shutil.copy2(WORKFLOW_SOURCES / "main.mp4", source_dir / f"v{index:02d}.mp4")
    for index in range(AUDIO_COUNT):
        shutil.copy2(WORKFLOW_SOURCES / "music.mp3", source_dir / f"a{index:02d}.mp3")
    for index in range(IMAGE_COUNT):
        shutil.copy2(IMAGE_FIXTURE, source_dir / f"img{index:02d}.png")


def write_scale_plan(workdir: Path) -> None:
    """Escreve o plano detalhado com 231 segmentos e 43 camadas sobre as fontes repetidas."""

    sources = [
        {"id": f"v{index:02d}", "kind": "video", "path": f"sources/v{index:02d}.mp4"}
        for index in range(VIDEO_COUNT)
    ]
    sources += [
        {"id": f"a{index:02d}", "kind": "audio", "path": f"sources/a{index:02d}.mp3"}
        for index in range(AUDIO_COUNT)
    ]
    sources += [
        {"id": f"img{index:02d}", "kind": "image", "path": f"sources/img{index:02d}.png"}
        for index in range(IMAGE_COUNT)
    ]
    segments = []
    for position in range(SEGMENT_COUNT):
        video_id = f"v{position % VIDEO_COUNT:02d}"
        segments.append({
            "video": {
                "source_id": video_id,
                "in_s": 0,
                "out_s": SEGMENT_DURATION_S,
                "fit": "preserve",
            },
            "audio": {
                "mode": "keep",
                "tracks": [{
                    "source_id": video_id,
                    "in_s": 0,
                    "out_s": SEGMENT_DURATION_S,
                    "gain_db": 0,
                }],
            },
        })
    layers: list[dict[str, object]] = []
    for position in range(TEXT_LAYERS):
        start = round(position * 3.0, 3)
        layers.append({
            "type": "text",
            "start_s": start,
            "end_s": round(start + 0.5, 3),
            "x": 0.1,
            "y": 0.1,
            "width": 0.3,
            "height": 0.15,
            "text": f"Titulo {position} com texto longo para o filtro",
            "font": "Arial",
            "size_px": 24,
            "color": "#FFFFFF",
            "align": "center",
            "wrap": False,
        })
    for position in range(IMAGE_LAYERS):
        start = round(position * 3.0 + 1.0, 3)
        layers.append({
            "type": "image",
            "start_s": start,
            "end_s": round(start + 0.5, 3),
            "x": 0.1,
            "y": 0.2,
            "width": 0.25,
            "height": 0.4,
            "source_id": f"img{position % IMAGE_COUNT:02d}",
            "fit": "contain",
        })
    for position in range(VIDEO_LAYERS):
        start = round(position * 3.0 + 2.0, 3)
        layers.append({
            "type": "video",
            "start_s": start,
            "end_s": round(start + 0.5, 3),
            "x": 0.55,
            "y": 0.2,
            "width": 0.4,
            "height": 0.5,
            "source_id": f"v{(position + 1) % VIDEO_COUNT:02d}",
            "in_s": 0,
            "out_s": 0.5,
            "fit": "contain",
        })
    layers.sort(key=lambda layer: float(layer["start_s"]))
    plan = {
        "schema_version": 2,
        "profile": {"aspect_ratio": "16:9"},
        "sources": sources,
        "segments": segments,
        "cuts": [],
        "layers": layers,
        "audio_processing": {
            "normalize_loudness": False,
            "target_lufs": -16,
            "limiter": {"enabled": False, "ceiling_dbfs": -1},
            "ducking": False,
        },
    }
    (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


class PhaseTwoFlowTests(unittest.TestCase):
    """Confirma a jornada detalhada da Phase 2 sem rough cuts, monkey patch ou script lateral."""

    @classmethod
    def setUpClass(cls) -> None:
        """Falha o gate quando a prova real não possui fixtures ou FFmpeg locais."""

        if not WORKFLOW_SOURCES.is_dir() or not IMAGE_FIXTURE.is_file():
            raise AssertionError("fixtures locais da Phase 2 não estão disponíveis")
        if any(dependencies.resolve_executable(name) is None for name in ("ffmpeg", "ffprobe")):
            raise AssertionError("ffmpeg e ffprobe não estão disponíveis localmente")

    def test_detailed_plan_renders_preview_and_full_with_file_graph(self) -> None:
        """Executa validate, preview, approve e full sobre o plano de 231 segmentos."""

        fixture_hashes = source_hashes(WORKFLOW_SOURCES)
        with workspace_tempdir("phase-2-e2e") as workdir:
            workdir_argument = str(workdir.resolve())
            initialized = assert_json_success(
                self,
                run_cli("init", "--workdir", workdir_argument, "--json"),
                "init",
            )
            self.assertEqual(initialized["data"]["state"], "ready")

            copy_scale_fixture(workdir)
            write_scale_plan(workdir)

            capabilities = assert_json_success(
                self,
                run_cli("capabilities", "--json"),
                "capabilities",
            )
            self.assertEqual(capabilities["data"]["plan"]["schema_version"], 2)
            self.assertEqual(
                capabilities["data"]["plan"]["profile"]["max_full_width_default"],
                1_920,
            )
            self.assertEqual(capabilities["data"]["render"]["filter_transport"], "file")

            plan = assert_json_success(
                self,
                run_cli("plan", "validate", "--workdir", workdir_argument, "--json"),
                "plan validate",
            )
            self.assertEqual(plan["data"]["source_count"], 69)
            self.assertEqual(plan["data"]["segment_count"], SEGMENT_COUNT)
            self.assertEqual(plan["data"]["layer_count"], TEXT_LAYERS + IMAGE_LAYERS + VIDEO_LAYERS)
            self.assertEqual(plan["data"]["cut_count"], 0)
            self.assertEqual(plan["data"]["timeline"]["duration_seconds"], TIMELINE_DURATION_S)
            self.assertEqual(plan["data"]["profile"]["max_full_width"], 1_920)
            self.assertEqual(plan["data"]["profile"]["effective_full_width"], 320)

            # A inspeção em memória prova escala e fonte concreta sem interceptar o render.
            document, validation = load_validated_plan(workdir_argument)
            graph = build_ffmpeg_graph(document, validation, canvas_width=320, canvas_height=180)
            self.assertGreater(len(graph.filter_complex), 100_000)
            self.assertIn("drawtext=fontfile=", graph.filter_complex)
            self.assertIn("Arial", validation["_resolved_font_files"])
            # Um grafo acima de 100 mil caracteres jamais caberia inline no teto do Windows.
            self.assertGreater(len(graph.filter_complex), 32_767)

            preview = assert_json_success(
                self,
                run_cli("render", "preview", "--workdir", workdir_argument, "--json"),
                "render preview",
            )
            self.assertEqual(preview["data"]["timeline_duration_seconds"], TIMELINE_DURATION_S)
            self.assertEqual(preview["data"]["source_count"], 69)
            self.assertEqual(preview["data"]["profile"]["effective_full_width"], 320)
            self.assertEqual(preview["data"]["video"]["width"], 320)
            self.assertEqual(preview["data"]["video"]["height"], 180)
            self.assertTrue(preview["data"]["audio"]["present"])
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

            approval = assert_json_success(
                self,
                run_cli("approve", "--workdir", workdir_argument, "--json"),
                "approve",
            )
            approval_document = json.loads((workdir / "approval.json").read_text(encoding="utf-8"))
            self.assertEqual(approval_document["plan_sha256"], plan["data"]["plan_sha256"])
            self.assertEqual(len(approval_document["sources"]), 69)
            self.assertEqual(approval_document["schema_version"], 2)
            self.assertNotIn("preview_sha256", approval_document)

            full = assert_json_success(
                self,
                run_cli("render", "full", "--workdir", workdir_argument, "--json"),
                "render full",
            )
            self.assertEqual(full["data"]["timeline_duration_seconds"], TIMELINE_DURATION_S)
            self.assertEqual(full["data"]["plan_sha256"], approval_document["plan_sha256"])
            self.assertEqual(
                full["data"]["approval"],
                {"approved_at": approval_document["approved_at"], "plan_sha256": approval_document["plan_sha256"]},
            )
            self.assertEqual(full["data"]["video"]["width"], 320)
            self.assertEqual(full["data"]["video"]["height"], 180)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

            metadata = inspect_rendered_artifact(workdir / "full.mp4", require_audio=True)
            self.assertAlmostEqual(metadata.duration_seconds, TIMELINE_DURATION_S, delta=0.3)

            # A jornada usa somente preview.mp4 e full.mp4, sem derivados intermediários.
            rendered_outputs = sorted(
                path.name for path in workdir.iterdir()
                if path.is_file() and path.suffix == ".mp4"
            )
            self.assertEqual(rendered_outputs, ["full.mp4", "preview.mp4"])

            # Mutação explícita do perfil invalida a aprovação sem tocar nos artefatos.
            plan_path = workdir / "plan.json"
            original_plan = plan_path.read_bytes()
            original_full = (workdir / "full.mp4").read_bytes()
            original_approval = (workdir / "approval.json").read_bytes()
            mutated = json.loads(original_plan.decode("utf-8"))
            mutated["profile"]["max_full_width"] = 160
            plan_path.write_text(json.dumps(mutated), encoding="utf-8")
            stale = assert_json_error(
                self,
                run_cli("render", "full", "--workdir", workdir_argument, "--json"),
            )
            self.assertEqual(stale["error"]["code"], "APPROVAL_STALE")
            self.assertEqual((workdir / "full.mp4").read_bytes(), original_full)
            self.assertEqual((workdir / "approval.json").read_bytes(), original_approval)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])
            plan_path.write_bytes(original_plan)

            self.assertEqual(source_hashes(WORKFLOW_SOURCES), fixture_hashes)


if __name__ == "__main__":
    unittest.main()
