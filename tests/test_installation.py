from __future__ import annotations

import hashlib
from contextlib import contextmanager
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install.ps1"
LOCKFILE = ROOT / "requirements.lock"


class InstallationContractTests(unittest.TestCase):
    """Verifica o contrato do instalador sem instalar dependências no host do teste."""

    @classmethod
    def setUpClass(cls) -> None:
        """Localiza o PowerShell 7 exigido pelo instalador antes de executar os cenários."""
        cls.pwsh = shutil.which("pwsh")
        if cls.pwsh is None:
            raise unittest.SkipTest("pwsh 7 não está disponível no host")

    def run_installer(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        """Executa o script sem shell intermediário para testar seu contrato de processo."""
        return subprocess.run(
            [self.pwsh, "-NoProfile", "-File", str(SCRIPT), *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    @contextmanager
    def workspace_tempdir(self) -> Iterator[Path]:
        """Cria um diretório temporário pelo PowerShell para respeitar a ACL do host gerenciado."""
        temporary_path = ROOT / f"tmp-installation-{uuid4().hex}"
        quoted_path = "'" + str(temporary_path).replace("'", "''") + "'"
        create = subprocess.run(
            [
                self.pwsh,
                "-NoProfile",
                "-Command",
                f"New-Item -ItemType Directory -Path {quoted_path} -Force | Out-Null",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(create.returncode, 0, create.stdout + create.stderr)
        try:
            yield temporary_path
        finally:
            subprocess.run(
                [
                    self.pwsh,
                    "-NoProfile",
                    "-Command",
                    f"if (Test-Path -LiteralPath {quoted_path}) {{ Remove-Item -LiteralPath {quoted_path} -Recurse -Force }}",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

    def test_lockfile_is_hash_pinned_and_contains_runtime_contract(self) -> None:
        """Garante que o lockfile pinou o backend, o auditor e as ferramentas de build."""
        text = LOCKFILE.read_text(encoding="utf-8")
        for package in ("faster-whisper", "ctranslate2", "pip-audit", "setuptools", "wheel"):
            self.assertRegex(text, rf"(?m)^{package}==[^\s\\]+")
        self.assertGreaterEqual(text.count("--hash=sha256:"), 40)

    def test_verify_is_read_only_and_reports_missing_target_environment(self) -> None:
        """Confirma que Verify falha no host incompatível sem criar venv ou modelo."""
        lock_digest_before = hashlib.sha256(LOCKFILE.read_bytes()).hexdigest()
        with self.workspace_tempdir() as temporary_path:
            result = self.run_installer(
                "-Verify",
                "-PythonPath",
                sys.executable,
                "-VenvPath",
                str(temporary_path / "venv"),
                "-ModelDir",
                str(temporary_path / "models"),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Python 3.11 x64", result.stdout + result.stderr)
            self.assertFalse((temporary_path / "venv").exists())
            self.assertFalse((temporary_path / "models").exists())
        self.assertEqual(lock_digest_before, hashlib.sha256(LOCKFILE.read_bytes()).hexdigest())

    def test_install_rejects_incompatible_python_before_creating_venv(self) -> None:
        """Confirma que Install não cria um ambiente quando o Python não é 3.11 x64."""
        # Usa o launcher do Windows, cujo interpretador padrão é 3.13 neste host, para
        # manter o teste incompatível mesmo quando a suíte roda no Python 3.11 válido.
        incompatible_python = shutil.which("py")
        self.assertIsNotNone(incompatible_python, "o launcher py é necessário neste teste Windows")
        with self.workspace_tempdir() as temporary_path:
            venv_path = temporary_path / "venv"
            result = self.run_installer(
                "-Install",
                "-PythonPath",
                incompatible_python,
                "-VenvPath",
                str(venv_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Python 3.11 x64", result.stdout + result.stderr)
            self.assertFalse(venv_path.exists())

    def test_install_models_copies_only_a_valid_local_model(self) -> None:
        """Confirma que pesos locais são aceitos sem download e ficam dentro do destino permitido."""
        with self.workspace_tempdir() as temporary_path:
            source = temporary_path / "source"
            model = source / "base"
            model.mkdir(parents=True)
            for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (model / filename).write_bytes(b"fixture")
            destination = temporary_path / "models"

            result = self.run_installer(
                "-InstallModels",
                "-ModelSource",
                str(source),
                "-ModelDir",
                str(destination),
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                self.assertTrue((destination / "base" / filename).is_file())

    def test_no_action_is_not_an_implicit_install(self) -> None:
        """Confirma que omitir uma opção explícita não altera o ambiente nem dispara instalação."""
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nenhuma ação selecionada", result.stdout + result.stderr)

    def test_security_gates_block_when_the_sca_tool_is_unavailable(self) -> None:
        """Confirma que a ausência de pip-audit bloqueia os gates sem declarar aprovação."""
        with self.workspace_tempdir() as temporary_path:
            result = self.run_installer(
                "-Verify",
                "-SecurityGates",
                "-PythonPath",
                sys.executable,
                "-VenvPath",
                str(temporary_path / "venv"),
                "-ModelDir",
                str(temporary_path / "models"),
            )

            self.assertNotEqual(result.returncode, 0)
            combined_output = result.stdout + result.stderr
            self.assertIn("Gate SCA indisponível", combined_output)
            self.assertNotIn("Gates gitleaks e pip-audit concluídos", combined_output)


if __name__ == "__main__":
    unittest.main()
