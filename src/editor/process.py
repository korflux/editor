"""Execução controlada dos processos locais usados pela análise de mídia."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from . import dependencies


class ProcessExecutionError(RuntimeError):
    """Indica ausência do executável, timeout ou falha operacional do processo."""

    def __init__(self, tool: str, *, missing: bool = False, timed_out: bool = False) -> None:
        self.tool = tool
        self.missing = missing
        self.timed_out = timed_out
        super().__init__(tool)


def _resolve_tool(tool: str) -> Path:
    """Resolve somente a ferramenta local permitida, sem aceitar comando montado."""

    executable = dependencies.resolve_executable(tool)
    if executable is None:
        raise ProcessExecutionError(tool, missing=True)
    return executable


def run_tool(
    tool: str,
    arguments: Sequence[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Executa uma ferramenta com lista de argumentos, sem shell e com timeout."""

    executable = _resolve_tool(tool)
    try:
        return subprocess.run(
            [str(executable), *(str(argument) for argument in arguments)],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise ProcessExecutionError(tool, timed_out=True) from error
    except OSError as error:
        raise ProcessExecutionError(tool) from error


def stream_pcm(
    input_path: Path,
    sample_rate: int,
    on_chunk: Callable[[bytes], None],
    *,
    timeout_seconds: float,
) -> None:
    """Decodifica áudio para PCM float32 em streaming, limitando memória e shell."""

    executable = _resolve_tool("ffmpeg")
    arguments = [
        str(executable),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "-",
    ]
    child: subprocess.Popen[bytes] | None = None
    reader: threading.Thread | None = None
    stop_reader = threading.Event()
    started_at = time.monotonic()
    try:
        child = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        assert child.stdout is not None
        chunks: queue.Queue[bytes | None | Exception] = queue.Queue(maxsize=1)

        def enqueue(item: bytes | None | Exception) -> None:
            """Entrega um evento ao consumidor sem bloquear o watchdog indefinidamente."""

            while not stop_reader.is_set():
                try:
                    chunks.put(item, timeout=0.1)
                    return
                except queue.Full:
                    continue

        def read_stdout() -> None:
            """Lê stdout em segundo plano para manter o timeout independente do pipe."""

            try:
                while not stop_reader.is_set():
                    chunk = child.stdout.read(1024 * 1024)
                    if not chunk:
                        enqueue(None)
                        return
                    enqueue(chunk)
            except Exception as error:
                enqueue(error)

        reader = threading.Thread(target=read_stdout, name="editor-ffmpeg-reader", daemon=True)
        reader.start()
        deadline = started_at + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessExecutionError("ffmpeg", timed_out=True)
            try:
                chunk = chunks.get(timeout=remaining)
            except queue.Empty as error:
                raise ProcessExecutionError("ffmpeg", timed_out=True) from error
            if chunk is None:
                break
            if isinstance(chunk, Exception):
                raise chunk
            on_chunk(chunk)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProcessExecutionError("ffmpeg", timed_out=True)
        try:
            return_code = child.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise ProcessExecutionError("ffmpeg", timed_out=True) from error
        if return_code != 0:
            raise ProcessExecutionError("ffmpeg")
    finally:
        stop_reader.set()
        if child is not None and child.poll() is None:
            try:
                child.kill()
            except OSError:
                pass
            try:
                child.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if child is not None and child.stdout is not None:
            try:
                child.stdout.close()
            except OSError:
                pass
        if reader is not None:
            reader.join(timeout=1)
