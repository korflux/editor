"""Prova E2E da Phase 1 com composição v2 e barreira de aprovação."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

from editor import dependencies
from editor.media import inspect_source
from tests.e2e.test_mvp_flow import (
    FIXTURE_ROOT,
    FIXTURE_SOURCES,
    PHASE_IMAGE_FIXTURE,
    assert_json_error,
    assert_json_success,
    copy_fixture,
    run_cli,
    source_hashes,
    workspace_tempdir,
)
from tests.test_preview_render import (
    extract_mono_samples,
    extract_rgb_frame,
    mean_audio_level,
    mean_frame_difference,
    mean_region_difference,
)


def configure_motion_fixture(workdir: Path) -> None:
    """Cria fontes visuais determinísticas e liga o áudio do plano a fontes explícitas."""

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

    layer_output_path = workdir / "sources" / "layer.mp4"
    layer_result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=160x160:rate=25:duration=0.5",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:size=160x160:rate=25:duration=0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:duration=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            "-y",
            str(layer_output_path),
        ],
        capture_output=True,
        check=False,
    )
    if layer_result.returncode != 0:
        raise AssertionError(layer_result.stderr.decode(errors="replace"))

    plan_path = workdir / "plan.json"
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    document["sources"].append({
        "id": "motion",
        "kind": "video",
        "path": "sources/motion.mp4",
    })
    document["sources"].append({
        "id": "layer",
        "kind": "video",
        "path": "sources/layer.mp4",
    })
    document["segments"][0]["video"]["source_id"] = "motion"
    document["segments"][0]["audio"] = {
        "mode": "replace",
        "tracks": [{
            "source_id": "main",
            "in_s": 0,
            "out_s": 0.75,
            "gain_db": 0,
        }],
    }
    for layer in document["layers"]:
        if layer["type"] == "video":
            layer["source_id"] = "layer"
            layer["in_s"] = 0
            layer["out_s"] = 0.75
    plan_path.write_text(json.dumps(document), encoding="utf-8")


class PhaseOneFlowTests(unittest.TestCase):
    """Confirma a jornada completa da Phase 1 e a invalidação por qualquer mutação."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige as fixtures e ferramentas locais sem mascarar ausência com SkipTest."""

        if (
            not (FIXTURE_ROOT / "plan.json").is_file()
            or not FIXTURE_SOURCES.is_dir()
            or not PHASE_IMAGE_FIXTURE.is_file()
        ):
            raise AssertionError("fixtures locais da Phase 1 não estão disponíveis")
        if any(dependencies.resolve_executable(name) is None for name in ("ffmpeg", "ffprobe")):
            raise AssertionError("ffmpeg e ffprobe não estão disponíveis localmente")

    def test_complete_phase_1_workflow_and_integrity_guards(self) -> None:
        """Executa init, composição v2, comparação por mídia e mutações isoladas."""

        with workspace_tempdir("phase-1-e2e") as workdir:
            fixture_hashes = source_hashes(FIXTURE_SOURCES)
            workdir_argument = str(workdir.resolve())
            initialized = assert_json_success(
                self,
                run_cli("init", "--workdir", workdir_argument, "--json"),
                "init",
            )
            self.assertEqual(initialized["data"]["state"], "ready")

            copy_fixture(workdir)
            configure_motion_fixture(workdir)
            plan_path = workdir / "plan.json"
            source_dir = workdir / "sources"
            source_bytes_before_init = {
                path: path.read_bytes() for path in source_dir.iterdir() if path.is_file()
            }
            plan_bytes_before_init = plan_path.read_bytes()
            repeated_init = assert_json_success(
                self,
                run_cli("init", "--workdir", workdir_argument, "--json"),
                "init",
            )
            self.assertEqual(repeated_init["data"]["created"], [])
            self.assertEqual(plan_path.read_bytes(), plan_bytes_before_init)
            self.assertEqual(
                {path: path.read_bytes() for path in source_dir.iterdir() if path.is_file()},
                source_bytes_before_init,
            )
            self.assertFalse((workdir / "approval.json").exists())
            self.assertFalse((workdir / "preview.mp4").exists())
            self.assertFalse((workdir / "full.mp4").exists())

            capabilities = assert_json_success(
                self,
                run_cli("capabilities", "--json"),
                "capabilities",
            )
            self.assertEqual(capabilities["data"]["plan"]["schema_version"], 2)
            self.assertEqual(capabilities["data"]["operations"]["init"]["status"], "available")

            plan = assert_json_success(
                self,
                run_cli("plan", "validate", "--workdir", workdir_argument, "--json"),
                "plan validate",
            )
            self.assertEqual(plan["data"]["schema_version"], 2)
            self.assertEqual(plan["data"]["timeline"]["duration_seconds"], 2.0)
            self.assertEqual(plan["data"]["cut_count"], 2)
            self.assertEqual(plan["data"]["layer_count"], 3)
            self.assertNotIn("overlay_count", plan["data"])

            preview = assert_json_success(
                self,
                run_cli("render", "preview", "--workdir", workdir_argument, "--json"),
                "render preview",
            )
            self.assertEqual(preview["data"]["timeline_duration_seconds"], 2.0)
            self.assertEqual(preview["data"]["cut_count"], 2)
            self.assertEqual(preview["data"]["layer_count"], 3)
            self.assertEqual(preview["data"]["audio"], {"codec": "aac", "present": True})

            approval = assert_json_success(
                self,
                run_cli("approve", "--workdir", workdir_argument, "--json"),
                "approve",
            )
            full = assert_json_success(
                self,
                run_cli("render", "full", "--workdir", workdir_argument, "--json"),
                "render full",
            )
            self.assertEqual(full["data"]["timeline_duration_seconds"], 2.0)
            self.assertEqual(full["data"]["cut_count"], 2)
            self.assertEqual(full["data"]["layer_count"], 3)
            self.assertEqual(full["data"]["video"]["width"], 320)
            self.assertEqual(full["data"]["video"]["height"], 180)
            self.assertEqual(full["data"]["audio"], {"codec": "aac", "present": True})

            preview_path = workdir / "preview.mp4"
            approval_path = workdir / "approval.json"
            full_path = workdir / "full.mp4"
            for artifact_path in (
                preview_path,
                Path(approval["data"]["artifact_path"]),
                full_path,
            ):
                with self.subTest(artifact=artifact_path):
                    self.assertTrue(artifact_path.is_file())
            self.assertEqual(
                inspect_source(full_path, require_audio=True, require_video=True).duration_seconds,
                2.0,
            )
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

            # Repetir init depois da aprovação prova que os artefatos vigentes continuam byte a byte.
            approved_bytes = {
                path: path.read_bytes() for path in (plan_path, preview_path, approval_path, full_path)
            }
            approved_source_bytes = {
                path: path.read_bytes() for path in source_dir.iterdir() if path.is_file()
            }
            reinitialized = assert_json_success(
                self,
                run_cli("init", "--workdir", workdir_argument, "--json"),
                "init",
            )
            self.assertEqual(reinitialized["data"]["created"], [])
            self.assertEqual(
                {path: path.read_bytes() for path in approved_bytes},
                approved_bytes,
            )
            self.assertEqual(
                {path: path.read_bytes() for path in source_dir.iterdir() if path.is_file()},
                approved_source_bytes,
            )

            # Referências controladas isolam movimento, camadas e J/L sem duplicar código de produção.
            reference_paths: dict[str, Path] = {}
            for name in ("no-layers", "static", "no-cuts"):
                reference_workdir = workdir / name
                reference_workdir.mkdir()
                copy_fixture(reference_workdir)
                configure_motion_fixture(reference_workdir)
                reference_plan_path = reference_workdir / "plan.json"
                reference_document = json.loads(reference_plan_path.read_text(encoding="utf-8"))
                if name in {"no-layers", "static"}:
                    reference_document["layers"] = []
                if name == "static":
                    reference_document["cuts"] = []
                    for segment in reference_document["segments"]:
                        segment["video"].pop("motion", None)
                if name == "no-cuts":
                    reference_document["cuts"] = []
                reference_plan_path.write_text(
                    json.dumps(reference_document),
                    encoding="utf-8",
                )
                reference_result = assert_json_success(
                    self,
                    run_cli(
                        "render",
                        "preview",
                        "--workdir",
                        str(reference_workdir.resolve()),
                        "--json",
                    ),
                    "render preview",
                )
                reference_paths[name] = Path(reference_result["data"]["artifact_path"])

            active_frame = extract_rgb_frame(preview_path, 0.8)
            no_layers_frame = extract_rgb_frame(reference_paths["no-layers"], 0.8)
            self.assertLess(
                mean_frame_difference(
                    extract_rgb_frame(preview_path, 0.4),
                    extract_rgb_frame(reference_paths["no-layers"], 0.4),
                ),
                2,
            )
            self.assertGreater(
                mean_frame_difference(
                    extract_rgb_frame(preview_path, 0.4),
                    extract_rgb_frame(reference_paths["static"], 0.4),
                ),
                5,
            )
            self.assertGreater(mean_region_difference(active_frame, no_layers_frame, 40, 58, 50, 65), 5)
            self.assertGreater(mean_region_difference(active_frame, no_layers_frame, 205, 75, 215, 85), 5)
            self.assertGreater(mean_region_difference(active_frame, no_layers_frame, 140, 140, 210, 165), 2)
            self.assertLess(mean_region_difference(active_frame, no_layers_frame, 300, 10, 318, 30), 2)

            active_samples = extract_mono_samples(preview_path)
            no_cuts_samples = extract_mono_samples(reference_paths["no-cuts"])
            self.assertGreater(
                mean_audio_level(active_samples, 0.60, 0.70),
                mean_audio_level(no_cuts_samples, 0.60, 0.70) * 1.4,
            )
            self.assertGreater(
                mean_audio_level(active_samples, 1.30, 1.40),
                max(mean_audio_level(no_cuts_samples, 1.30, 1.40) * 4, 100),
            )

            # Cada mutação é isolada para provar que a aprovação vigente nunca é reutilizada indevidamente.
            original_plan = plan_path.read_bytes()
            original_preview = preview_path.read_bytes()
            original_approval = approval_path.read_bytes()
            original_full = full_path.read_bytes()
            original_sources = {
                path: path.read_bytes() for path in source_dir.iterdir() if path.is_file()
            }
            mutation_targets = (
                ("plan", plan_path),
                ("preview", preview_path),
                ("video", source_dir / "layer.mp4"),
                ("audio", source_dir / "music.mp3"),
                ("image", source_dir / "alpha.png"),
            )
            for kind, target in mutation_targets:
                with self.subTest(mutation=kind):
                    if kind == "plan":
                        mutated_document = json.loads(original_plan.decode("utf-8"))
                        mutated_document["layers"][0]["x"] = 0.11
                        target.write_text(json.dumps(mutated_document), encoding="utf-8")
                    elif kind == "preview":
                        target.write_bytes(original_preview + b"mutation")
                    else:
                        target.write_bytes(original_sources[target] + b"mutation")

                    stale = assert_json_error(
                        self,
                        run_cli("render", "full", "--workdir", workdir_argument, "--json"),
                    )
                    self.assertEqual(stale["error"]["code"], "APPROVAL_STALE")
                    self.assertEqual(full_path.read_bytes(), original_full)
                    self.assertEqual(approval_path.read_bytes(), original_approval)
                    self.assertEqual(list((workdir / ".tmp").iterdir()), [])

                    target.write_bytes(
                        original_plan
                        if kind == "plan"
                        else original_preview
                        if kind == "preview"
                        else original_sources[target]
                    )

            self.assertEqual(
                {path: path.read_bytes() for path in source_dir.iterdir() if path.is_file()},
                original_sources,
            )
            self.assertEqual(source_hashes(FIXTURE_SOURCES), fixture_hashes)


if __name__ == "__main__":
    unittest.main()
