"""Inspeção e validação comum das fontes locais de mídia."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import process
from .contracts import CliError, EXIT_CODES
from .workdir import resolve_source_path


MAX_SOURCE_SIZE_BYTES = 4 * 1024**3
MAX_SOURCE_DURATION_SECONDS = 3_600
SUPPORTED_SOURCE_EXTENSIONS = {".mp4", ".mp3", ".wav"}


@dataclass(frozen=True)
class MediaMetadata:
    """Metadados validados necessários para analisar o áudio sem ambiguidade."""

    path: Path
    container: str
    audio_codec: str
    video_codec: str | None
    duration_seconds: float
    size_bytes: int
    sample_rate: int | None
    channels: int | None


def _error(code: str, message: str, exit_code: int = EXIT_CODES["MEDIA"]) -> CliError:
    """Cria mensagens públicas estáveis sem repassar saída bruta do FFmpeg."""

    return CliError(code=code, message=message, exit_code=exit_code)


def _parse_duration(value: Any) -> float | None:
    """Converte duração do ffprobe e rejeita valores ausentes, infinitos ou negativos."""

    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) and duration > 0 else None


def _stream_duration(stream: dict[str, Any]) -> float | None:
    """Obtém a duração do stream como fallback quando o contêiner não a informa."""

    return _parse_duration(stream.get("duration"))


def _map_process_error(error: process.ProcessExecutionError) -> CliError:
    """Converte falha interna de processo em erro seguro da borda de mídia."""

    if error.missing:
        return _error(
            "DEPENDENCY_MISSING",
            "FFprobe não está disponível localmente.",
            EXIT_CODES["DEPENDENCY"],
        )
    return _error(
        "MEDIA_PROBE_FAILED",
        "Não foi possível inspecionar a fonte de mídia.",
    )


def _validate_probe_payload(
    path: Path,
    size_bytes: int,
    payload: dict[str, Any],
) -> MediaMetadata:
    """Valida contêiner, streams, codecs, duração e limites antes de qualquer análise."""

    format_data = payload.get("format")
    streams = payload.get("streams")
    if not isinstance(format_data, dict) or not isinstance(streams, list):
        raise _error("MEDIA_UNSUPPORTED", "A fonte não possui metadados de mídia suportados.")

    extension = path.suffix.lower()
    format_names = {
        item.strip().lower()
        for item in str(format_data.get("format_name", "")).split(",")
        if item.strip()
    }
    expected_format = extension.removeprefix(".")
    if extension not in SUPPORTED_SOURCE_EXTENSIONS or expected_format not in format_names:
        raise _error("MEDIA_UNSUPPORTED", "O formato da fonte não é aceito.")

    typed_streams = [stream for stream in streams if isinstance(stream, dict)]
    if len(typed_streams) != len(streams):
        raise _error("MEDIA_UNSUPPORTED", "A fonte contém streams inválidos.")
    unsupported_streams = {
        str(stream.get("codec_type", "")).lower()
        for stream in typed_streams
        if stream.get("codec_type") not in {"audio", "video"}
    }
    if unsupported_streams:
        raise _error("MEDIA_UNSUPPORTED", "A fonte contém streams não suportados.")

    audio_streams = [stream for stream in typed_streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in typed_streams if stream.get("codec_type") == "video"]
    if len(audio_streams) != 1:
        raise _error("MEDIA_UNSUPPORTED", "A fonte deve conter exatamente um stream de áudio.")

    audio_codec = str(audio_streams[0].get("codec_name", "")).lower()
    allowed_audio_codecs = {
        ".mp4": {"aac"},
        ".mp3": {"mp3"},
        ".wav": {"pcm_s16le"},
    }
    if audio_codec not in allowed_audio_codecs[extension]:
        raise _error("MEDIA_UNSUPPORTED", "O codec de áudio da fonte não é aceito.")

    video_codec: str | None = None
    if video_streams:
        if extension != ".mp4" or len(video_streams) != 1:
            raise _error("MEDIA_UNSUPPORTED", "A fonte contém vídeo incompatível com o contrato.")
        video_codec = str(video_streams[0].get("codec_name", "")).lower()
        if video_codec != "h264":
            raise _error("MEDIA_UNSUPPORTED", "O codec de vídeo da fonte não é aceito.")

    duration = _parse_duration(format_data.get("duration")) or _stream_duration(audio_streams[0])
    if duration is None:
        raise _error("MEDIA_UNSUPPORTED", "A duração da fonte não pôde ser determinada.")
    if size_bytes > MAX_SOURCE_SIZE_BYTES or duration > MAX_SOURCE_DURATION_SECONDS:
        raise _error(
            "MEDIA_LIMIT_EXCEEDED",
            "A fonte excede o limite de tamanho ou duração permitido.",
        )

    try:
        sample_rate = int(audio_streams[0].get("sample_rate"))
    except (TypeError, ValueError):
        sample_rate = None
    try:
        channels = int(audio_streams[0].get("channels"))
    except (TypeError, ValueError):
        channels = None
    return MediaMetadata(
        path=path,
        container=expected_format,
        audio_codec=audio_codec,
        video_codec=video_codec,
        duration_seconds=duration,
        size_bytes=size_bytes,
        sample_rate=sample_rate,
        channels=channels,
    )


def inspect_source(input_path: str | Path) -> MediaMetadata:
    """Inspeciona uma fonte local com ffprobe antes de iniciar o processamento de áudio."""

    path = resolve_source_path(input_path)
    if path.suffix.lower() not in SUPPORTED_SOURCE_EXTENSIONS:
        raise _error("MEDIA_UNSUPPORTED", "A extensão da fonte não é aceita.")
    try:
        size_bytes = path.stat().st_size
    except OSError as error:
        raise _error("SOURCE_INVALID", "Não foi possível ler o tamanho da fonte.") from error
    if size_bytes > MAX_SOURCE_SIZE_BYTES:
        raise _error("MEDIA_LIMIT_EXCEEDED", "A fonte excede o limite de tamanho permitido.")

    arguments = [
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = process.run_tool("ffprobe", arguments, timeout_seconds=15)
    except process.ProcessExecutionError as error:
        raise _map_process_error(error) from error
    if result.returncode != 0:
        raise _error("MEDIA_PROBE_FAILED", "Não foi possível inspecionar a fonte de mídia.")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise _error("MEDIA_PROBE_FAILED", "Os metadados da fonte são inválidos.") from error
    if not isinstance(payload, dict):
        raise _error("MEDIA_PROBE_FAILED", "Os metadados da fonte são inválidos.")
    return _validate_probe_payload(path, size_bytes, payload)


def sha256_file(path: Path) -> str:
    """Calcula o hash da fonte em blocos para registrar a origem analisada."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise _error("SOURCE_INVALID", "Não foi possível ler a fonte de mídia.") from error
    return digest.hexdigest()
