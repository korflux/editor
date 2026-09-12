"""Extração local de frames e manifesto temporal determinístico."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from .. import media, process
from ..contracts import CliError, EXIT_CODES
from ..dependencies import is_regular_path
from ..workdir import (
    FramesLayout,
    prepare_frames_layout,
    publish_frames_atomic,
    relative_artifact_path,
    validate_source_id,
    validate_workdir,
)


MAX_FRAME_COUNT = 10_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _argument_error(message: str) -> CliError:
    """Cria erro de argumento para seleções temporais inválidas na borda da operação."""

    return CliError(code="ARGUMENT_INVALID", message=message, exit_code=EXIT_CODES["ARGUMENT"])


def _limit_error(message: str) -> CliError:
    """Cria erro de limite para tempos fora da fonte ou volume excessivo de frames."""

    return CliError(
        code="MEDIA_LIMIT_EXCEEDED",
        message=message,
        exit_code=EXIT_CODES["MEDIA"],
    )


def _parse_decimal(raw_value: object, label: str) -> Decimal:
    """Converte um tempo externo em decimal finito sem aceitar NaN ou infinito."""

    try:
        value = Decimal(str(raw_value).strip())
    except (InvalidOperation, ValueError):
        raise _argument_error(f"{label} deve ser um número finito.") from None
    if not value.is_finite():
        raise _argument_error(f"{label} deve ser um número finito.")
    return Decimal(0) if value == 0 else value


def _format_seconds(value: Decimal) -> str:
    """Formata segundos sem notação científica para argumentos determinísticos do FFmpeg."""

    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _json_seconds(value: Decimal) -> float:
    """Converte segundos para número JSON finito com precisão estável de seis casas."""

    result = round(float(value), 6)
    if not math.isfinite(result):
        raise _argument_error("O tempo selecionado excede a precisão suportada.")
    return result


def _parse_selection(
    timestamps: str | Sequence[object] | None,
    interval_seconds: object | None,
    start_seconds: object | None,
    end_seconds: object | None,
) -> tuple[str, tuple[Decimal, ...] | None, Decimal | None, Decimal | None, Decimal | None]:
    """Valida a forma da seleção antes de consultar FFprobe ou executar FFmpeg."""

    has_timestamps = timestamps is not None
    has_interval = interval_seconds is not None
    if has_timestamps == has_interval:
        raise _argument_error("Informe exatamente timestamps ou intervalo regular.")

    if has_timestamps:
        if start_seconds is not None or end_seconds is not None:
            raise _argument_error("start-s e end-s só podem ser usados com interval-s.")
        raw_values = timestamps.split(",") if isinstance(timestamps, str) else list(timestamps or [])
        if not raw_values or any(not str(raw).strip() for raw in raw_values):
            raise _argument_error("timestamps deve conter uma lista não vazia de números.")
        values: list[Decimal] = []
        seen: set[Decimal] = set()
        for raw_value in raw_values:
            value = _parse_decimal(raw_value, "Cada timestamp")
            if value < 0:
                raise _argument_error("Os timestamps não podem ser negativos.")
            if value in seen:
                raise _argument_error("Os timestamps não podem ser duplicados.")
            seen.add(value)
            values.append(value)
            if len(values) > MAX_FRAME_COUNT:
                raise _limit_error("A seleção excede o limite de frames permitido.")
        return "timestamps", tuple(values), None, None, None

    interval = _parse_decimal(interval_seconds, "interval-s")
    if interval <= 0:
        raise _argument_error("interval-s deve ser maior que zero.")
    start = _parse_decimal(start_seconds, "start-s") if start_seconds is not None else Decimal(0)
    end = _parse_decimal(end_seconds, "end-s") if end_seconds is not None else None
    if start < 0 or (end is not None and end < 0):
        raise _argument_error("start-s e end-s não podem ser negativos.")
    if end is not None and end <= start:
        raise _argument_error("end-s deve ser maior que start-s.")
    return "interval", None, interval, start, end


def _resolve_selection(
    parsed: tuple[str, tuple[Decimal, ...] | None, Decimal | None, Decimal | None, Decimal | None],
    duration_seconds: float,
) -> tuple[str, tuple[Decimal, ...]]:
    """Aplica a duração validada da fonte e materializa os instantes do intervalo."""

    mode, explicit, interval, start, end = parsed
    duration = _parse_decimal(duration_seconds, "A duração da fonte")
    if mode == "timestamps":
        assert explicit is not None
        if any(value >= duration for value in explicit):
            raise _limit_error("Todos os timestamps devem estar antes do fim da fonte.")
        return mode, explicit

    assert interval is not None and start is not None
    resolved_end = duration if end is None else end
    if start >= duration or resolved_end > duration:
        raise _limit_error("O intervalo selecionado excede a duração da fonte.")
    if resolved_end <= start:
        raise _argument_error("end-s deve ser maior que start-s.")

    values: list[Decimal] = []
    current = start
    while current < resolved_end:
        values.append(current)
        if len(values) > MAX_FRAME_COUNT:
            raise _limit_error("A seleção excede o limite de frames permitido.")
        current += interval
    if not values:
        raise _argument_error("O intervalo não produz nenhum timestamp.")
    return mode, tuple(values)


def _map_process_error(error: process.ProcessExecutionError, tool: str) -> CliError:
    """Converte ausência, timeout ou falha do processo em erro público estável."""

    if error.missing:
        return CliError(
            code="DEPENDENCY_MISSING",
            message=f"{tool.capitalize()} não está disponível localmente.",
            exit_code=EXIT_CODES["DEPENDENCY"],
        )
    return CliError(
        code="FRAME_EXTRACTION_FAILED",
        message="A extração de frames falhou.",
        exit_code=EXIT_CODES["OPERATION"],
    )


def _probe_frame_times(source: media.MediaMetadata, timeout_seconds: float) -> tuple[Decimal, ...]:
    """Consulta os timestamps efetivos dos frames de vídeo antes da extração dos PNGs."""

    arguments = [
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp_time",
        "-of",
        "json",
        str(source.path),
    ]
    try:
        result = process.run_tool("ffprobe", arguments, timeout_seconds=timeout_seconds)
    except process.ProcessExecutionError as error:
        raise _map_process_error(error, "ffprobe") from error
    if result.returncode != 0:
        raise CliError(
            code="MEDIA_PROBE_FAILED",
            message="Não foi possível obter os timestamps dos frames.",
            exit_code=EXIT_CODES["MEDIA"],
        )
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise CliError(
            code="MEDIA_PROBE_FAILED",
            message="Os timestamps dos frames são inválidos.",
            exit_code=EXIT_CODES["MEDIA"],
        ) from error
    raw_frames = payload.get("frames") if isinstance(payload, dict) else None
    if not isinstance(raw_frames, list):
        raise CliError(
            code="MEDIA_UNSUPPORTED",
            message="A fonte não possui frames de vídeo válidos.",
            exit_code=EXIT_CODES["MEDIA"],
        )

    values: set[Decimal] = set()
    for raw_frame in raw_frames:
        if not isinstance(raw_frame, dict):
            continue
        raw_value = raw_frame.get("best_effort_timestamp_time")
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, ValueError):
            continue
        if value.is_finite() and value >= 0:
            values.add(value)
    if not values:
        raise CliError(
            code="MEDIA_UNSUPPORTED",
            message="A fonte não possui timestamps de vídeo utilizáveis.",
            exit_code=EXIT_CODES["MEDIA"],
        )
    return tuple(sorted(values))


def _effective_timestamp(requested: Decimal, frame_times: Sequence[Decimal]) -> Decimal:
    """Escolhe o primeiro frame no instante solicitado ou o último frame disponível."""

    for frame_time in frame_times:
        if frame_time >= requested:
            return frame_time
    return frame_times[-1]


def _create_staging_directory(layout: FramesLayout, source_id: str) -> Path:
    """Cria uma pasta temporária isolada para impedir publicação de frames incompletos."""

    staging = layout.temporary / f".frames-{source_id}-{uuid4().hex}"
    try:
        staging.mkdir()
    except OSError as error:
        raise CliError(
            code="OUTPUT_PUBLISH_FAILED",
            message="Não foi possível preparar a pasta temporária de frames.",
            exit_code=EXIT_CODES["OPERATION"],
        ) from error
    if not is_regular_path(staging, directory=True):
        raise CliError(
            code="OUTPUT_PUBLISH_FAILED",
            message="A pasta temporária de frames não pôde ser validada.",
            exit_code=EXIT_CODES["OPERATION"],
        )
    return staging


def _extract_frame(
    source: media.MediaMetadata,
    effective_timestamp: Decimal,
    destination: Path,
    timeout_seconds: float,
) -> None:
    """Extrai um único PNG no frame efetivo escolhido pelo FFprobe, sem shell intermediário."""

    arguments = [
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        _format_seconds(effective_timestamp),
        "-copyts",
        "-i",
        str(source.path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-f",
        "image2",
        "-update",
        "1",
        str(destination),
    ]
    try:
        result = process.run_tool("ffmpeg", arguments, timeout_seconds=timeout_seconds)
    except process.ProcessExecutionError as error:
        raise _map_process_error(error, "ffmpeg") from error
    if result.returncode != 0:
        raise CliError(
            code="FRAME_EXTRACTION_FAILED",
            message="A extração de frames falhou.",
            exit_code=EXIT_CODES["OPERATION"],
        )
    try:
        if not is_regular_path(destination) or destination.stat().st_size == 0:
            raise ValueError("PNG ausente")
        with destination.open("rb") as stream:
            if stream.read(len(PNG_SIGNATURE)) != PNG_SIGNATURE:
                raise ValueError("assinatura PNG inválida")
    except (OSError, ValueError) as error:
        raise CliError(
            code="FRAME_EXTRACTION_FAILED",
            message="O FFmpeg não produziu um PNG válido.",
            exit_code=EXIT_CODES["OPERATION"],
        ) from error


def _serialize_manifest(manifest: dict[str, Any]) -> bytes:
    """Serializa o manifesto em UTF-8 canônico para consumo determinístico por agentes."""

    return json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_manifest(staging: Path, payload: bytes) -> None:
    """Grava e sincroniza o manifesto dentro do staging antes da troca atômica da pasta."""

    destination = staging / "manifest.json"
    try:
        with destination.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise CliError(
            code="OUTPUT_PUBLISH_FAILED",
            message="Não foi possível preparar o manifesto de frames.",
            exit_code=EXIT_CODES["OPERATION"],
        ) from error


def extract_frames(
    workdir: str | Path,
    source_id: str,
    input_path: str | Path,
    *,
    timestamps: str | Sequence[object] | None = None,
    interval_seconds: object | None = None,
    start_seconds: object | None = None,
    end_seconds: object | None = None,
) -> dict[str, Any]:
    """Valida, extrai e publica uma seleção completa de PNGs com manifesto temporal."""

    validate_workdir(workdir)
    validate_source_id(source_id)
    parsed_selection = _parse_selection(
        timestamps,
        interval_seconds,
        start_seconds,
        end_seconds,
    )

    source = media.inspect_source(input_path, require_audio=False, require_video=True)
    mode, requested_timestamps = _resolve_selection(parsed_selection, source.duration_seconds)
    frame_times = _probe_frame_times(
        source,
        max(60.0, min(7_200.0, source.duration_seconds * 4 + 30)),
    )
    source_hash = media.sha256_file(source.path)
    layout = prepare_frames_layout(workdir, source_id)
    timeout_seconds = max(60.0, min(7_200.0, source.duration_seconds * 4 + 30))
    staging = _create_staging_directory(layout, source_id)

    try:
        manifest_frames: list[dict[str, Any]] = []
        assert requested_timestamps is not None
        for index, requested in enumerate(requested_timestamps, start=1):
            effective = _effective_timestamp(requested, frame_times)
            filename = f"frame-{index:04d}.png"
            destination = staging / filename
            _extract_frame(source, effective, destination, timeout_seconds)
            manifest_frames.append(
                {
                    "index": index,
                    "path": (layout.frames / filename).relative_to(layout.root).as_posix(),
                    "timestamp_effective_s": _json_seconds(effective),
                    "timestamp_requested_s": _json_seconds(requested),
                }
            )

        manifest = {
            "frames": manifest_frames,
            "schema_version": 1,
            "selection": {"mode": mode},
            "source": {
                "audio_codec": source.audio_codec,
                "container": source.container,
                "duration_seconds": round(source.duration_seconds, 6),
                "id": source_id,
                "path": str(source.path),
                "sha256": source_hash,
                "size_bytes": source.size_bytes,
                "video_codec": source.video_codec,
            },
        }
        _write_manifest(staging, _serialize_manifest(manifest))
        publish_frames_atomic(layout, staging)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        "artifact_path": str(layout.destination),
        "frame_count": len(manifest_frames),
        "relative_artifact_path": relative_artifact_path(layout),
        "source_id": source_id,
        "source_sha256": source_hash,
        "duration_seconds": round(source.duration_seconds, 6),
    }
