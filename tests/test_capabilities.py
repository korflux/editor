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

from editor.capabilities import choose_recommendation, collect_capabilities


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
        self.assertEqual(data["cli_version"], "0.1.0")
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
        self.assertIn(data["recommendation"]["status"], {"recommended", "missing"})

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


if __name__ == "__main__":
    unittest.main()
