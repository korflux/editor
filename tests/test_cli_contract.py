"""Smoke tests do ponto de entrada real e dos envelopes da CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa `python -m editor` no diretório do projeto para testar o entry point real."""

    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class CliContractTests(unittest.TestCase):
    """Verifica o contrato mínimo antes de qualquer integração de mídia."""

    def test_module_help_is_non_interactive(self) -> None:
        """Confirma que o módulo sobe sem prompt nem diagnóstico em stderr."""

        result = run_module("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: editor", result.stdout)
        self.assertIn("init", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_installed_entry_point_help_is_non_interactive(self) -> None:
        """Confirma que o script instalado aponta para a mesma CLI executável."""

        entry_point = shutil.which("editor")
        self.assertIsNotNone(entry_point, "o entry point deve existir após a instalação editável")

        result = subprocess.run(
            [entry_point, "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("usage: editor", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_json_success_envelope(self) -> None:
        """Confirma os campos obrigatórios de sucesso usando o processo real."""

        result = run_module("--version", "--json")
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "version")
        self.assertEqual(payload["data"]["version"], "1.0.0")

    def test_json_error_envelope_has_no_traceback(self) -> None:
        """Confirma que chamada inválida retorna somente erro JSON seguro no stdout."""

        result = run_module("unknown", "--json")
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(
            set(payload["error"]),
            {"code", "message", "exit_code"},
        )
        self.assertEqual(payload["error"]["code"], "ARGUMENT_INVALID")
        self.assertEqual(payload["error"]["exit_code"], 2)
        self.assertNotIn("Traceback", result.stdout)


if __name__ == "__main__":
    unittest.main()
