"""Testes do contrato local de capacidades e da recomendação determinística."""

from __future__ import annotations

import json
from contextlib import contextmanager
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Iterator
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies as editor_dependencies
from editor.capabilities import choose_recommendation, collect_capabilities
from editor.whisper_backend import resolve_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTATION_PATHS = (
    PROJECT_ROOT / "skills" / "vibe-editor" / "SKILL.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "installation.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "youtube-editing.md",
    PROJECT_ROOT / ".vibeflow" / "REGRAS.md",
)


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para validar o contrato consumido por agentes."""

    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    """Cria fixture temporária dentro do workspace, compatível com a ACL do host gerenciado."""

    temporary_path = PROJECT_ROOT / f"tmp-capabilities-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


class CapabilitiesTests(unittest.TestCase):
    """Garante consulta local estável mesmo quando componentes opcionais estão ausentes."""

    def test_process_returns_stable_json_envelope(self) -> None:
        """Confirma que a CLI real retorna capacidades sem prompt, traceback ou stderr."""

        result = run_module("capabilities", "--json")
        payload = json.loads(result.stdout)
        data = payload["data"]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "capabilities")
        self.assertEqual(data["cli_version"], "1.0.0")
        for key in (
            "system",
            "dependencies",
            "models",
            "devices",
            "formats",
            "limits",
            "operations",
            "recommendation",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["operations"]["capabilities"]["status"], "available")
        self.assertEqual(data["operations"]["init"]["status"], "available")
        self.assertEqual(data["plan"]["schema_version"], 2)
        self.assertEqual(
            data["plan"]["profile"],
            {
                "aspect_ratio": "16:9",
                "max_full_width_default": 1_920,
                "max_full_width_min": 32,
                "max_full_width_max": 3_840,
                "max_full_width_step": 32,
            },
        )
        self.assertEqual(data["render"]["filter_transport"], "file")
        self.assertEqual(data["render"]["preview_max_dimension"], 720)
        self.assertEqual(data["render"]["full_default_max_width"], 1_920)
        self.assertEqual(data["render"]["max_command_characters"], 32_767)
        self.assertEqual(data["transcription"]["word_drift_tolerance_seconds"], 0.5)
        self.assertEqual(
            data["transcription"]["counters"],
            ["discarded_word_count", "clamped_word_count", "discarded_segment_count"],
        )
        self.assertEqual(data["fonts"]["renderer"], "fontfile")
        self.assertEqual(data["limits"]["max_full_width_default"], 1_920)
        self.assertEqual(data["limits"]["max_command_characters"], 32_767)
        self.assertEqual(data["formats"]["image"], ["png", "jpg", "jpeg", "webp"])
        self.assertEqual(data["limits"]["max_layers"], 1_000)
        self.assertEqual(data["limits"]["max_cuts"], 999)
        self.assertEqual(data["limits"]["max_cut_duration_seconds"], 30.0)
        self.assertEqual(data["limits"]["motion_zoom_min"], 0.5)
        self.assertEqual(data["limits"]["motion_zoom_max"], 4.0)
        self.assertEqual(data["limits"]["max_image_size_bytes"], 100 * 1024**2)
        self.assertEqual(data["limits"]["max_image_dimension"], 8_192)
        self.assertNotIn("max_overlays", data["limits"])
        self.assertIn(data["recommendation"]["status"], {"recommended", "missing"})

    def test_documentation_describes_only_the_v2_contract(self) -> None:
        """Impede que a descoberta e o fluxo do agente voltem a documentar o contrato antigo."""

        contents = {path: path.read_text(encoding="utf-8") for path in DOCUMENTATION_PATHS}
        combined = "\n".join(contents.values())

        for marker in (
            "schema v2",
            "editor init",
            "cuts",
            "layers",
            "J/L",
            "0.5",
            "4.0",
            "PNG",
            "WebP",
            "100 MiB",
            "8.192",
        ):
            self.assertIn(marker, combined)

        for path, content in contents.items():
            self.assertNotIn('"schema_version": 1', content, str(path))
            self.assertNotIn('"overlays"', content, str(path))

        self.assertNotIn("não possui `editor init`", combined)
        self.assertNotIn("`editor init` ainda não existe", combined)
        self.assertNotIn("subcomandos de mídia ainda não existem", combined)
        self.assertNotIn("zoom/movimento, inserções em camada e cortes J/L", combined)

    def test_root_json_flag_is_preserved_before_subcommand(self) -> None:
        """Confirma as duas posições aceitas para `--json` sem alterar o envelope."""

        result = run_module("--json", "capabilities")
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["command"], "capabilities")

    def test_recommendation_prefers_cuda_small_then_cpu_base_then_tiny(self) -> None:
        """Confirma a ordem explícita de recomendação sem inventar modelo remoto."""

        self.assertEqual(
            choose_recommendation({"available": ["base", "small"]}, "available")["model"],
            "small",
        )
        self.assertEqual(
            choose_recommendation({"available": ["base"]}, "missing")["device"],
            "cpu",
        )
        self.assertEqual(
            choose_recommendation({"available": ["tiny"]}, "missing")["model"],
            "tiny",
        )
        self.assertEqual(
            choose_recommendation({"available": []}, "missing")["status"],
            "missing",
        )

    def test_model_inventory_uses_installer_contract(self) -> None:
        """Confirma que modelo completo é disponível e modelo sem vocabulário é rejeitado."""

        with workspace_tempdir() as root:
            base = root / "base"
            base.mkdir()
            for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (base / filename).write_bytes(b"fixture")

            tiny = root / "tiny"
            tiny.mkdir()
            for filename in ("config.json", "model.bin", "tokenizer.json"):
                (tiny / filename).write_bytes(b"fixture")

            data = collect_capabilities(root)
            models = {item["name"]: item for item in data["models"]["models"]}

            self.assertEqual(models["base"]["status"], "available")
            self.assertEqual(models["tiny"]["status"], "unsupported")
            self.assertEqual(data["recommendation"]["model"], "base")

    def test_render_capabilities_require_the_declared_encoders(self) -> None:
        """Confirma que render indisponível aparece antes da execução sem libx264."""

        ffmpeg = {
            "status": "available",
            "version": "fixture",
            "tools": {
                "ffmpeg": {"status": "available", "path": "ffmpeg", "version": "fixture"},
                "ffprobe": {"status": "available", "path": "ffprobe", "version": "fixture"},
            },
            "encoders": {
                "status": "unsupported",
                "available": ["aac"],
                "missing": ["libx264"],
                "reason": "libx264 ausente",
            },
        }
        with workspace_tempdir() as root:
            with patch(
                "editor.capabilities.dependencies.detect_ffmpeg",
                return_value=ffmpeg,
            ):
                data = collect_capabilities(root)

        self.assertEqual(data["operations"]["render_preview"]["status"], "unsupported")
        self.assertEqual(data["operations"]["render_full"]["status"], "unsupported")

    def test_cuda_capability_requires_a_working_ctranslate_runtime(self) -> None:
        """Confirma que driver e backend presentes não bastam para recomendar CUDA."""

        gpu = {
            "status": "available",
            "path": "nvidia-smi",
            "devices": [{"status": "available", "name": "fixture", "memory_bytes": 1}],
        }
        whisper = {"status": "available", "packages": {}}
        runtime = {
            "status": "unsupported",
            "device_count": 0,
            "reason": "runtime ausente",
        }
        models = {
            "status": "available",
            "root": "models",
            "available": ["base", "small"],
            "models": [],
        }
        with (
            patch("editor.capabilities.dependencies.detect_gpu", return_value=gpu),
            patch("editor.capabilities.dependencies.detect_whisper_backend", return_value=whisper),
            patch("editor.capabilities.dependencies.detect_cuda_runtime", return_value=runtime),
            patch("editor.capabilities.dependencies.discover_models", return_value=models),
        ):
            data = collect_capabilities(Path("models"))

        self.assertEqual(data["dependencies"]["cuda"]["status"], "unsupported")
        self.assertEqual(data["devices"]["cuda"]["status"], "unsupported")
        self.assertEqual(data["recommendation"]["device"], "cpu")
        self.assertEqual(data["devices"]["auto"]["resolves_to"], "cpu")

    def test_metal_probe_never_fails_and_reports_without_error(self) -> None:
        """Confirma que a sonda Metal best effort nunca levanta e informa status sem erro."""

        probe = editor_dependencies.detect_metal()

        self.assertIn(probe["status"], {"available", "missing"})
        self.assertTrue(probe["reason"])

    def test_metal_missing_keeps_cpu_recommendation(self) -> None:
        """Confirma que Metal ausente mantém CPU como recomendação sem falhar capabilities."""

        models = {
            "status": "available",
            "root": "models",
            "available": ["base"],
            "models": [],
        }
        with (
            patch(
                "editor.capabilities.dependencies.detect_metal",
                return_value={"status": "missing", "reason": "fixture sem Metal"},
            ),
            patch(
                "editor.capabilities.dependencies.discover_models",
                return_value=models,
            ),
        ):
            with workspace_tempdir() as root:
                data = collect_capabilities(root)

        self.assertEqual(data["devices"]["metal"]["status"], "missing")
        self.assertFalse(data["devices"]["metal"]["recommended"])
        self.assertEqual(data["recommendation"]["device"], "cpu")
        self.assertEqual(data["devices"]["auto"]["resolves_to"], "cpu")

    def test_metal_available_does_not_change_cpu_baseline(self) -> None:
        """Confirma que Metal disponível é só reporte e o baseline continua CPU nesta fatia."""

        models = {
            "status": "available",
            "root": "models",
            "available": ["base"],
            "models": [],
        }
        with (
            patch(
                "editor.capabilities.dependencies.detect_metal",
                return_value={"status": "available", "reason": "fixture com Metal"},
            ),
            patch(
                "editor.capabilities.dependencies.discover_models",
                return_value=models,
            ),
        ):
            with workspace_tempdir() as root:
                data = collect_capabilities(root)

        self.assertEqual(data["devices"]["metal"]["status"], "available")
        self.assertFalse(data["devices"]["metal"]["recommended"])
        self.assertEqual(data["recommendation"]["device"], "cpu")

    def test_auto_resolves_cpu_int8_when_metal_missing(self) -> None:
        """Confirma que auto resolve CPU int8 com modelo base mesmo sem Metal."""

        with workspace_tempdir() as workdir:
            model_root = workdir / "models"
            model_root.mkdir()
            base = model_root / "base"
            base.mkdir()
            for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (base / filename).write_bytes(b"fixture")
            models = {
                "status": "available",
                "root": str(model_root),
                "available": ["base"],
                "models": [
                    {"name": "base", "status": "available", "path": str(base)},
                ],
            }
            with (
                patch("editor.whisper_backend.default_model_root", return_value=model_root),
                patch(
                    "editor.whisper_backend.dependencies.discover_models",
                    return_value=models,
                ),
                patch(
                    "editor.whisper_backend.dependencies.detect_gpu",
                    return_value={"status": "missing"},
                ),
                patch(
                    "editor.whisper_backend.dependencies.detect_whisper_backend",
                    return_value={"status": "available"},
                ),
                patch(
                    "editor.whisper_backend.dependencies.detect_metal",
                    return_value={"status": "missing", "reason": "fixture sem Metal"},
                ),
            ):
                config = resolve_config(None, "auto")

        self.assertEqual(config.device, "cpu")
        self.assertEqual(config.compute_type, "int8")
        self.assertEqual(config.model_name, "base")


    def test_filter_flag_and_ceiling_follow_host(self) -> None:
        """Confirma que Windows usa -/filter_complex com 32767 e demais usam -filter_complex com limite do sistema."""

        with patch("editor.dependencies.is_windows", return_value=True):
            self.assertEqual(editor_dependencies.ffmpeg_filter_option(), "-/filter_complex")
            self.assertEqual(editor_dependencies.max_command_characters(), 32_767)
            with workspace_tempdir() as root:
                data = collect_capabilities(root)
            self.assertEqual(data["render"]["filter_transport"], "file")
            self.assertEqual(data["render"]["filter_option"], "-/filter_complex")
            self.assertEqual(data["render"]["max_command_characters"], 32_767)
            self.assertEqual(data["limits"]["max_command_characters"], 32_767)

        with (
            patch("editor.dependencies.is_windows", return_value=False),
            patch("editor.dependencies.os.sysconf", return_value=262_144, create=True),
        ):
            self.assertEqual(editor_dependencies.ffmpeg_filter_option(), "-filter_complex")
            self.assertEqual(editor_dependencies.max_command_characters(), 262_144)
            with workspace_tempdir() as root:
                data = collect_capabilities(root)
            self.assertEqual(data["render"]["filter_option"], "-filter_complex")
            self.assertEqual(data["render"]["max_command_characters"], 262_144)
            self.assertEqual(data["limits"]["max_command_characters"], 262_144)

    def test_filter_ceiling_falls_back_without_sysconf(self) -> None:
        """Confirma que sem SC_ARG_MAX a capabilities informa o fallback POSIX sem falhar."""

        with (
            patch("editor.dependencies.is_windows", return_value=False),
            patch("editor.dependencies.os.sysconf", side_effect=OSError("sem limite"), create=True),
        ):
            self.assertEqual(
                editor_dependencies.max_command_characters(),
                editor_dependencies.FALLBACK_POSIX_MAX_COMMAND_CHARACTERS,
            )

    def test_font_origin_follows_host_without_silent_fallback(self) -> None:
        """Confirma que Windows informa registry HKCU/HKLM e demais informam fc-match com fallback rejeitado."""

        with patch("editor.dependencies.is_windows", return_value=True):
            self.assertEqual(editor_dependencies.font_lookup_source(), "registry")
            self.assertEqual(editor_dependencies.font_registry_hives(), ["HKCU", "HKLM"])
            with workspace_tempdir() as root:
                data = collect_capabilities(root)
            self.assertEqual(data["fonts"]["renderer"], "fontfile")
            self.assertEqual(data["fonts"]["source"], "registry")
            self.assertEqual(data["fonts"]["registry"], ["HKCU", "HKLM"])
            self.assertEqual(data["fonts"]["fallback"], "reject")

        with patch("editor.dependencies.is_windows", return_value=False):
            self.assertEqual(editor_dependencies.font_lookup_source(), "fc-match")
            self.assertEqual(editor_dependencies.font_registry_hives(), [])
            with workspace_tempdir() as root:
                data = collect_capabilities(root)
            self.assertEqual(data["fonts"]["renderer"], "fontfile")
            self.assertEqual(data["fonts"]["source"], "fc-match")
            self.assertEqual(data["fonts"]["registry"], [])
            self.assertEqual(data["fonts"]["fallback"], "reject")

    def test_resolve_without_exe_prioritizes_tools_bundle(self) -> None:
        """Confirma que fora do Windows a resolução prioriza .tools/ffmpeg sem sufixo .exe."""

        with workspace_tempdir() as root:
            bundle = root / ".tools" / "ffmpeg"
            bundle.mkdir(parents=True)
            candidate = bundle / "ffmpeg"
            candidate.write_bytes(b"fixture")
            with (
                patch("editor.dependencies.is_windows", return_value=False),
                patch("editor.dependencies._package_root", return_value=root),
                patch("editor.dependencies.Path.cwd", return_value=root),
                patch("editor.dependencies.shutil.which", return_value=None),
            ):
                resolved = editor_dependencies.resolve_executable("ffmpeg")
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.name, "ffmpeg")
            self.assertNotEqual(resolved.suffix, ".exe")


if __name__ == "__main__":
    unittest.main()
