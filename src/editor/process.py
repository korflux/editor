"""Execução controlada dos processos locais usados pela análise de mídia."""

from __future__ import annotations

import os
import subprocess
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
    started_at = time.monotonic()
    try:
        child = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        assert child.stdout is not None
        while True:
            if time.monotonic() - started_at > timeout_seconds:
                raise ProcessExecutionError("ffmpeg", timed_out=True)
            chunk = child.stdout.read(1024 * 1024)
            if not chunk:
                break
            on_chunk(chunk)

        return_code = child.wait()
        if return_code != 0:
            raise ProcessExecutionError("ffmpeg")
    except ProcessExecutionError:
        if child is not None and child.poll() is None:
            child.kill()
        if child is not None:
            child.communicate()
        raise
    except Exception:
        if child is not None and child.poll() is None:
            child.kill()
        if child is not None:
            child.communicate()
        raise
