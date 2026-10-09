from __future__ import annotations

import hashlib
from contextlib import contextmanager
import os
import re
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from tests.support import remove_tree


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install.ps1"
SHELL_SCRIPT = ROOT / "scripts" / "install.sh"
LOCKFILE = ROOT / "requirements.lock"
MACOS_LOCKFILE = ROOT / "requirements.macos.lock"


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
        # Decodificação tolerante: o host Windows pode emitir bytes fora do UTF-8
        # (página de código do console), o que quebrava a thread de leitura com
        # UnicodeDecodeError; errors="replace" preserva as asserções ASCII.
        return subprocess.run(
            [self.pwsh, "-NoProfile", "-File", str(SCRIPT), *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def find_usable_bash(self) -> str | None:
        """Localiza o bash utilizável, priorizando o Git Bash no Windows antes do PATH."""
        candidates = [
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return shutil.which("bash")

    def run_shell_installer(
        self, *arguments: str, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Executa o espelho Unix sem shell intermediário para testar seu contrato de processo."""
        bash = self.find_usable_bash()
        if bash is None:
            raise unittest.SkipTest("bash não está disponível no host")
        env = dict(os.environ)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [bash, str(SHELL_SCRIPT), *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )

    def get_user_path(self) -> str:
        """Lê o PATH do usuário via .NET sem shell intermediário, para conferir o shim."""
        result = subprocess.run(
            [self.pwsh, "-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('Path','User')"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return (result.stdout or "").strip()

    def set_user_path(self, value: str) -> None:
        """Restaura o PATH do usuário via .NET, com valor passado por ambiente para evitar injeção."""
        env = dict(os.environ)
        env["EDITOR_TEST_USER_PATH"] = value
        result = subprocess.run(
            [self.pwsh, "-NoProfile", "-Command", "[Environment]::SetEnvironmentVariable('Path', $env:EDITOR_TEST_USER_PATH, 'User')"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def count_shim_in_user_path(self, shim_dir: Path) -> int:
        """Conta ocorrências do shim no PATH do usuário com comparação sem diferenciar maiúsculas."""
        current = self.get_user_path()
        if not current:
            return 0
        target = str(shim_dir).rstrip("\\/").lower()
        count = 0
        for entry in current.split(";"):
            if entry.strip().rstrip("\\/").lower() == target:
                count += 1
        return count

    @contextmanager
    def workspace_tempdir(self) -> Iterator[Path]:
        """Cria um diretório temporário pelo PowerShell para respeitar a ACL do host gerenciado."""
        temporary_path = ROOT / f"tmp-installation-{uuid4().hex}"
        quoted_path = "'" + str(temporary_path).replace("'", "''") + "'"
        # Mesma tolerância do run_installer: comandos auxiliares também herdam a
        # página de código do host e não podem falhar a decodificação.
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
            errors="replace",
            check=False,
        )
        self.assertEqual(create.returncode, 0, create.stdout + create.stderr)
        try:
            yield temporary_path
        finally:
            # A criação fica no PowerShell por causa da ACL; a remoção usa a limpeza única dos testes, que
            # repete a remoção e avisa se a pasta sobrar (o `Remove-Item` antigo tinha o resultado ignorado).
            remove_tree(temporary_path)

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

    def test_install_models_rejects_reparse_ancestor_before_writing(self) -> None:
        """Confirma que uma junction ancestral não consegue redirecionar a instalação de modelos."""
        with self.workspace_tempdir() as temporary_path:
            source = temporary_path / "source"
            model = source / "base"
            model.mkdir(parents=True)
            for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (model / filename).write_bytes(b"fixture")

            target = temporary_path / "physical-target"
            (target / "deep").mkdir(parents=True)
            link = temporary_path / "link"
            quoted_link = "'" + str(link).replace("'", "''") + "'"
            quoted_target = "'" + str(target).replace("'", "''") + "'"
            # Decodificação tolerante pelos mesmos motivos do run_installer.
            create = subprocess.run(
                [
                    self.pwsh,
                    "-NoProfile",
                    "-Command",
                    f"New-Item -ItemType Junction -Path {quoted_link} -Target {quoted_target} | Out-Null",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stdout + create.stderr)
            try:
                result = self.run_installer(
                    "-InstallModels",
                    "-ModelSource",
                    str(source),
                    "-ModelDir",
                    str(link / "deep" / "models"),
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("componente linkado", result.stdout + result.stderr)
                self.assertFalse((target / "deep" / "models").exists())
            finally:
                # Decodificação tolerante pelos mesmos motivos do run_installer.
                subprocess.run(
                    [
                        self.pwsh,
                        "-NoProfile",
                        "-Command",
                        f"if (Test-Path -LiteralPath {quoted_link}) {{ Remove-Item -LiteralPath {quoted_link} -Force }}",
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )

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

    def test_verify_cannot_combine_with_add_to_path_and_creates_no_shim(self) -> None:
        """Confirma que Verify continua somente leitura e rejeita AddToPath sem criar shim."""
        with self.workspace_tempdir() as temporary_path:
            shim_dir = temporary_path / "shim"
            result = self.run_installer(
                "-Verify",
                "-AddToPath",
                "-VenvPath",
                str(temporary_path / "venv"),
                "-ModelDir",
                str(temporary_path / "models"),
                "-ShimDir",
                str(shim_dir),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("-Verify não pode ser combinado", result.stdout + result.stderr)
            self.assertFalse(shim_dir.exists())

    def test_add_to_path_fails_closed_when_venv_python_is_missing(self) -> None:
        """Confirma que o shim falha fechado sem venv e sem tocar no PATH do usuário."""
        with self.workspace_tempdir() as temporary_path:
            venv_path = temporary_path / "venv"
            venv_path.mkdir(parents=True)
            shim_dir = temporary_path / "shim"
            result = self.run_installer(
                "-AddToPath",
                "-VenvPath",
                str(venv_path),
                "-ShimDir",
                str(shim_dir),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ambiente virtual", result.stdout + result.stderr)
            self.assertFalse((shim_dir / "editor.cmd").exists())
            self.assertEqual(self.count_shim_in_user_path(shim_dir), 0)

    def test_add_to_path_registers_reversible_user_shim_without_shell(self) -> None:
        """Confirma que o shim por usuário aponta para o venv e é idempotente sem shell intermediário."""
        if os.name != "nt":
            raise unittest.SkipTest("shim Windows com PATH por usuário exige Windows")
        with self.workspace_tempdir() as temporary_path:
            venv_python = temporary_path / "venv" / "Scripts" / "python.exe"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_bytes(b"fixture")
            shim_dir = temporary_path / "shim"
            original_path = self.get_user_path()
            try:
                result = self.run_installer(
                    "-AddToPath",
                    "-VenvPath",
                    str(temporary_path / "venv"),
                    "-ShimDir",
                    str(shim_dir),
                )

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                shim_file = shim_dir / "editor.cmd"
                self.assertTrue(shim_file.is_file())
                content = shim_file.read_text(encoding="utf-8")
                self.assertTrue(content.startswith("@echo off"))
                self.assertIn("-m editor %*", content)
                match = re.search(r'"(.+python\.exe)"', content, re.IGNORECASE)
                self.assertIsNotNone(match, content)
                assert match is not None
                self.assertTrue(Path(match.group(1)).is_file())
                for forbidden in ("cmd /c", "powershell", "Invoke-Expression", "Start-Process"):
                    self.assertNotIn(forbidden.lower(), content.lower())
                self.assertEqual(self.count_shim_in_user_path(shim_dir), 1)

                rerun = self.run_installer(
                    "-AddToPath",
                    "-VenvPath",
                    str(temporary_path / "venv"),
                    "-ShimDir",
                    str(shim_dir),
                )
                self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
                self.assertEqual(self.count_shim_in_user_path(shim_dir), 1)
            finally:
                self.set_user_path(original_path)

    def test_macos_lockfile_is_hash_pinned_and_contains_runtime_contract(self) -> None:
        """Garante que o lock do Mac pinou o mesmo contrato de runtime com hashes obrigatórios."""
        text = MACOS_LOCKFILE.read_text(encoding="utf-8")
        for package in ("faster-whisper", "ctranslate2", "pip-audit", "setuptools", "wheel"):
            self.assertRegex(text, rf"(?m)^{package}==[^\s\\]+")
        self.assertGreaterEqual(text.count("--hash=sha256:"), 40)

    def test_platform_lock_selection_uses_windows_and_macos_locks(self) -> None:
        """Confirma que a seleção por plataforma usa requirements.lock no Windows e macos.lock fora dele."""
        script_text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Get-PlatformLockName", script_text)
        self.assertIn("requirements.macos.lock", script_text)
        self.assertIn("--require-hashes", script_text)
        # Extrai só a função para não executar o bloco principal do instalador no dot-source.
        command = (
            "$text = Get-Content -Raw -LiteralPath '" + str(SCRIPT).replace("'", "''") + "'; "
            "$match = [regex]::Match($text, '(?s)function Get-PlatformLockName.*?\\n\\}'); "
            "if (-not $match.Success) { exit 1 }; "
            "Invoke-Expression $match.Value; "
            "Get-PlatformLockName -IsWindowsPlatform $true; "
            "Get-PlatformLockName -IsWindowsPlatform $false"
        )
        probe = subprocess.run(
            [self.pwsh, "-NoProfile", "-Command", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        lines = [line.strip() for line in (probe.stdout or "").splitlines() if line.strip()]
        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
        self.assertIn("requirements.lock", lines)
        self.assertIn("requirements.macos.lock", lines)

    def test_install_fails_closed_when_platform_lock_is_missing_before_venv(self) -> None:
        """Confirma que o lock ausente falha antes de criar o venv, sem rede implícita."""
        venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
        if not venv_python.is_file():
            raise unittest.SkipTest("venv 3.11 local não está disponível para provar falha fechada do lock")
        backup = LOCKFILE.read_bytes()
        with self.workspace_tempdir() as temporary_path:
            venv_path = temporary_path / "venv"
            LOCKFILE.unlink()
            try:
                result = self.run_installer(
                    "-Install",
                    "-PythonPath",
                    str(venv_python),
                    "-VenvPath",
                    str(venv_path),
                )
            finally:
                LOCKFILE.write_bytes(backup)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requirements.lock", result.stdout + result.stderr)
            self.assertFalse(venv_path.exists())

    def test_install_sh_exists_and_declares_unix_contract(self) -> None:
        """Garante que o espelho Unix existe com flags, lock do Mac e hashes obrigatórios."""
        self.assertTrue(SHELL_SCRIPT.is_file(), "scripts/install.sh não foi criado")
        text = SHELL_SCRIPT.read_text(encoding="utf-8")
        for flag in ("--verify", "--install", "--add-to-path", "--venv-path", "--model-dir"):
            self.assertIn(flag, text)
        self.assertIn("requirements.macos.lock", text)
        self.assertIn("--require-hashes", text)
        self.assertIn("get_platform_lock_name", text.lower())
        # Sem download implícito de binários ou pesos nesta fatia.
        for forbidden in ("curl ", "wget ", "apt-get ", "brew install"):
            self.assertNotIn(forbidden, text)

    def test_install_sh_verify_is_read_only_without_creating_state(self) -> None:
        """Confirma que --verify do espelho falha fechado sem criar venv nem modelos."""
        with self.workspace_tempdir() as temporary_path:
            venv_path = temporary_path / "venv"
            model_path = temporary_path / "models"
            result = self.run_shell_installer(
                "--verify",
                "--venv-path",
                venv_path.as_posix(),
                "--model-dir",
                model_path.as_posix(),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(venv_path.exists())
            self.assertFalse(model_path.exists())

    def test_install_sh_rejects_verify_combined_with_install_actions(self) -> None:
        """Confirma que o espelho mantém --verify somente leitura e rejeita --add-to-path."""
        with self.workspace_tempdir() as temporary_path:
            shim_path = temporary_path / "shim"
            result = self.run_shell_installer(
                "--verify",
                "--add-to-path",
                "--venv-path",
                (temporary_path / "venv").as_posix(),
                "--model-dir",
                (temporary_path / "models").as_posix(),
                "--shim-dir",
                shim_path.as_posix(),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--verify", result.stdout + result.stderr)
            self.assertFalse((shim_path / "editor").exists())

    def test_install_sh_add_to_path_creates_reversible_unix_shim(self) -> None:
        """Confirma que o shim Unix aponta para o venv e registra o PATH por usuário sem shell."""
        bash = self.find_usable_bash()
        if bash is None:
            raise unittest.SkipTest("bash não está disponível no host")
        with self.workspace_tempdir() as temporary_path:
            venv_bin = temporary_path / "venv" / "bin"
            venv_bin.mkdir(parents=True)
            venv_python = venv_bin / "python"
            venv_python.write_bytes(b"fixture")
            shim_dir = temporary_path / "shim"
            home_dir = temporary_path / "home"
            home_dir.mkdir(parents=True)
            profile = home_dir / "profile"
            extra = {
                "HOME": home_dir.as_posix(),
                "EDITOR_TEST_PROFILE": profile.as_posix(),
                # Força o caminho Unix mesmo no Git Bash Windows para provar o shim isolado.
                "EDITOR_FAKE_UNAME": "Darwin",
            }
            result = self.run_shell_installer(
                "--add-to-path",
                "--venv-path",
                (temporary_path / "venv").as_posix(),
                "--shim-dir",
                shim_dir.as_posix(),
                extra_env=extra,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            shim_file = shim_dir / "editor"
            self.assertTrue(shim_file.is_file())
            content = shim_file.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("#!/bin/sh"))
            self.assertIn('-m editor "$@"', content)
            match = re.search(r'exec "(.+)" -m editor', content)
            self.assertIsNotNone(match, content)
            assert match is not None
            self.assertTrue(Path(match.group(1)).is_file())
            for forbidden in ("curl", "wget", "sudo", "Invoke-Expression"):
                self.assertNotIn(forbidden.lower(), content.lower())
            profile_text = profile.read_text(encoding="utf-8")
            self.assertIn(shim_dir.as_posix(), profile_text)
            rerun = self.run_shell_installer(
                "--add-to-path",
                "--venv-path",
                (temporary_path / "venv").as_posix(),
                "--shim-dir",
                shim_dir.as_posix(),
                extra_env=extra,
            )
            self.assertEqual(rerun.returncode, 0, rerun.stdout + rerun.stderr)
            recount = profile.read_text(encoding="utf-8").count(shim_dir.as_posix())
            self.assertEqual(recount, 1)


if __name__ == "__main__":
    unittest.main()
