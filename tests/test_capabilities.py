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


if __name__ == "__main__":
    unittest.main()
