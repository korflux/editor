"""Testes do watchdog de processos e da leitura PCM em streaming."""

from __future__ import annotations

from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch

from editor.process import ProcessExecutionError, stream_pcm


class _BlockingStdout:
    """Simula um pipe que só libera a leitura depois que o processo é encerrado."""

    def __init__(self) -> None:
        self.released = threading.Event()
        self.closed = False

    def read(self, _size: int) -> bytes:
        """Bloqueia a leitura para comprovar que o watchdog não depende do pipe."""

        self.released.wait(2)
        return b""

    def close(self) -> None:
        """Libera o leitor simulado durante a limpeza do processo filho."""

        self.closed = True
        self.released.set()


class _BlockingChild:
    """Representa um processo externo que não termina sem kill explícito."""

    def __init__(self, stdout: _BlockingStdout) -> None:
        self.stdout = stdout
        self.killed = False

    def poll(self) -> int | None:
        """Informa se o processo simulado ainda está executando."""

        return -9 if self.killed else None

    def kill(self) -> None:
        """Encerra o processo simulado e desbloqueia o pipe de saída."""

        self.killed = True
        self.stdout.released.set()

    def wait(self, timeout: float | None = None) -> int:
        """Aguarda o encerramento e reproduz o timeout de um processo travado."""

        self.stdout.released.wait(timeout)
        if not self.killed:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return -9


class ProcessTests(unittest.TestCase):
    """Garante que um processo externo travado não ultrapasse o timeout operacional."""

    def test_stream_pcm_watchdog_interrupts_blocking_stdout(self) -> None:
        """Confirma que uma leitura bloqueante termina com erro de timeout e kill."""

        def discard_chunk(_chunk: bytes) -> None:
            """Descarta PCM simulado porque o teste verifica somente o watchdog."""

        stdout = _BlockingStdout()
        child = _BlockingChild(stdout)
        with (
            patch("editor.process._resolve_tool", return_value=Path("ffmpeg.exe")),
            patch("editor.process.subprocess.Popen", return_value=child),
        ):
            with self.assertRaises(ProcessExecutionError) as raised:
                stream_pcm(
                    Path("input.wav"),
                    48_000,
                    discard_chunk,
                    timeout_seconds=0.05,
                )

        self.assertTrue(raised.exception.timed_out)
        self.assertTrue(child.killed)
        self.assertTrue(stdout.closed)


if __name__ == "__main__":
    unittest.main()
