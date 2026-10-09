"""Testes do preparo seguro e idempotente de trabalhos com `editor init`."""

from __future__ import annotations

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

from editor.contracts import CliError
from editor.workdir import init_workdir
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DIRECTORIES = [
    ".tmp",
    "analysis",
    "analysis/audio",
    "analysis/transcription",
    "frames",
]


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa `python -m editor` em processo separado para provar o contrato público."""

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
    """Cria um pai descartável dentro do workspace para manter a prova isolada."""

    temporary_path = PROJECT_ROOT / f"tmp-init-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        remove_tree(temporary_path)


def assert_json_error(
    testcase: unittest.TestCase,
    result: subprocess.CompletedProcess[str],
    code: str,
    exit_code: int,
) -> None:
    """Confirma que uma falha de `init` permanece um envelope seguro sem traceback."""

    payload = json.loads(result.stdout)
    testcase.assertEqual(result.returncode, exit_code, result.stderr)
    testcase.assertEqual(result.stderr, "")
    testcase.assertFalse(payload["ok"])
    testcase.assertEqual(payload["error"]["code"], code)
    testcase.assertEqual(payload["error"]["exit_code"], exit_code)
    testcase.assertNotIn("Traceback", result.stdout)


class InitTests(unittest.TestCase):
    """Garante preparação segura sem alterar estado editorial ou fontes locais."""

    def test_new_workdir_and_second_run_are_deterministic(self) -> None:
        """Confirma criação da estrutura, envelope e idempotência no processo real."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            first = run_module("init", "--workdir", str(workdir), "--json")
            first_payload = json.loads(first.stdout)
            first_data = first_payload["data"]

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stderr, "")
            self.assertTrue(first_payload["ok"])
            self.assertEqual(first_payload["command"], "init")
            self.assertEqual(first_data["workdir"], str(workdir))
            self.assertEqual(first_data["state"], "ready")
            self.assertEqual(first_data["created"], EXPECTED_DIRECTORIES)
            self.assertEqual(first_data["existing"], [])
            self.assertEqual(first_data["preserved_artifacts"], [])
            self.assertTrue(workdir.is_dir())
            self.assertFalse((workdir / "sources").exists())
            for relative_path in EXPECTED_DIRECTORIES:
                self.assertTrue((workdir / relative_path).is_dir(), relative_path)

            second = run_module("init", "--workdir", str(workdir), "--json")
            second_payload = json.loads(second.stdout)
            second_data = second_payload["data"]

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(second.stderr, "")
            self.assertEqual(second_data["created"], [])
            self.assertEqual(second_data["existing"], EXPECTED_DIRECTORIES)
            self.assertEqual(second_data["preserved_artifacts"], [])

    def test_existing_artifacts_are_preserved_and_reported(self) -> None:
        """Confirma preservação byte a byte e listagem somente de artefatos conhecidos."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            (workdir / ".tmp").mkdir(parents=True)
            (workdir / "analysis" / "audio").mkdir(parents=True)
            (workdir / "frames" / "main").mkdir(parents=True)
            artifacts = {
                workdir / "plan.json": b'{"plan":true}',
                workdir / "approval.json": b'{"approval":true}',
                workdir / "preview.mp4": b"preview",
                workdir / "full.mp4": b"full",
                workdir / "analysis" / "audio" / "main.json": b'{"audio":true}',
                workdir / "frames" / "main" / "manifest.json": b'{"frames":true}',
                workdir / "frames" / "main" / "frame-0001.png": b"png",
                workdir / "notes.txt": b"preserve unknown files too",
            }
            for path, content in artifacts.items():
                path.write_bytes(content)
            before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}

            result = run_module("init", "--workdir", str(workdir), "--json")
            payload = json.loads(result.stdout)
            data = payload["data"]

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(data["created"], ["analysis/transcription"])
            self.assertEqual(data["existing"], [".tmp", "analysis", "analysis/audio", "frames"])
            self.assertEqual(
                data["preserved_artifacts"],
                [
                    "plan.json",
                    "approval.json",
                    "preview.mp4",
                    "full.mp4",
                    "analysis/audio/main.json",
                    "frames/main/frame-0001.png",
                    "frames/main/manifest.json",
                ],
            )
            self.assertFalse((workdir / "sources").exists())
            self.assertEqual(
                {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts},
                before,
            )

            second = run_module("init", "--workdir", str(workdir), "--json")
            second_data = json.loads(second.stdout)["data"]
            self.assertEqual(second_data["created"], [])
            self.assertEqual(second_data["existing"], EXPECTED_DIRECTORIES)
            self.assertEqual(second_data["preserved_artifacts"], data["preserved_artifacts"])

    def test_invalid_targets_fail_before_creating_state(self) -> None:
        """Confirma rejeição de caminho relativo, arquivo e pai inexistente sem efeitos parciais."""

        with workspace_tempdir() as parent:
            relative = run_module("init", "--workdir", "relative-job", "--json")
            assert_json_error(self, relative, "WORKDIR_INVALID", 5)

            file_target = parent / "file"
            file_target.write_bytes(b"keep")
            before = file_target.read_bytes()
            as_file = run_module("init", "--workdir", str(file_target), "--json")
            assert_json_error(self, as_file, "WORKDIR_INVALID", 5)
            self.assertEqual(file_target.read_bytes(), before)

            missing_parent = parent / "missing" / "job"
            nested = run_module("init", "--workdir", str(missing_parent), "--json")
            assert_json_error(self, nested, "WORKDIR_INVALID", 5)
            self.assertFalse(missing_parent.parent.exists())

            incompatible = parent / "incompatible"
            incompatible.mkdir()
            (incompatible / "analysis").write_bytes(b"not a directory")
            invalid_managed_path = run_module("init", "--workdir", str(incompatible), "--json")
            assert_json_error(self, invalid_managed_path, "WORKDIR_INVALID", 5)
            self.assertFalse((incompatible / ".tmp").exists())

    def test_creation_failure_rolls_back_only_new_empty_directories(self) -> None:
        """Confirma rollback limitado quando uma pasta gerenciada falha durante a criação."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            original_mkdir = Path.mkdir

            def fail_transcription(
                path: Path,
                *arguments: object,
                **keywords: object,
            ) -> None:
                """Simula falha somente na última pasta para verificar a limpeza reversa."""

                if path == workdir / "analysis" / "transcription":
                    raise OSError("simulated mkdir failure")
                original_mkdir(path, *arguments, **keywords)

            with patch("editor.workdir.Path.mkdir", new=fail_transcription):
                with self.assertRaises(CliError) as raised:
                    init_workdir(workdir)

            self.assertEqual(raised.exception.code, "WORKDIR_INVALID")
            self.assertFalse(workdir.exists())
            self.assertTrue(parent.is_dir())

    def test_non_writable_destination_is_rejected_before_creation(self) -> None:
        """Confirma que a checagem de permissão ocorre antes de criar a estrutura técnica."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            with patch("editor.workdir.os.access", return_value=False):
                with self.assertRaises(CliError) as raised:
                    init_workdir(workdir)

            self.assertEqual(raised.exception.code, "WORKDIR_INVALID")
            self.assertFalse(workdir.exists())

    def test_managed_junction_is_rejected_before_partial_creation(self) -> None:
        """Confirma que uma junction dentro do trabalho não é seguida nem deixa estado parcial."""

        powershell = shutil.which("pwsh")
        if powershell is None:
            self.skipTest("pwsh 7 não está disponível para criar a junction de prova")

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            workdir.mkdir()
            target = parent / "physical-target"
            target.mkdir()
            link = workdir / "analysis"
            quoted_link = "'" + str(link).replace("'", "''") + "'"
            quoted_target = "'" + str(target).replace("'", "''") + "'"
            create = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-Command",
                    f"New-Item -ItemType Junction -Path {quoted_link} -Target {quoted_target} | Out-Null",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stdout + create.stderr)
            try:
                result = run_module("init", "--workdir", str(workdir), "--json")
                assert_json_error(self, result, "WORKDIR_INVALID", 5)
                self.assertFalse((workdir / ".tmp").exists())
                self.assertFalse((target / "audio").exists())
            finally:
                subprocess.run(
                    [
                        powershell,
                        "-NoProfile",
                        "-Command",
                        f"if (Test-Path -LiteralPath {quoted_link}) {{ Remove-Item -LiteralPath {quoted_link} -Force }}",
                    ],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )


if __name__ == "__main__":
    unittest.main()
