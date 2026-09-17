"""Análise local de transcrição com timestamps obrigatórios por palavra."""

from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
from pathlib import Path
from typing import Any

from .. import media, whisper_backend
from ..contracts import CliError, EXIT_CODES
from ..workdir import (
    WorkdirLayout,
    prepare_layout,
    publish_atomic,
    relative_artifact_path,
    validate_source_id,
    validate_workdir,
)


_LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2})?$")
_MAX_WORD_BOUNDARY_DRIFT_SECONDS = 0.5


def _argument_error(message: str) -> CliError:
    """Cria erro de argumento seguro para opções da transcrição."""

    return CliError(code="ARGUMENT_INVALID", message=message, exit_code=EXIT_CODES["ARGUMENT"])


def _analysis_error(message: str) -> CliError:
    """Cria erro de análise seguro para respostas incompletas do backend."""

    return CliError(code="ANALYSIS_FAILED", message=message, exit_code=EXIT_CODES["OPERATION"])


def _validate_language(language: str) -> str:
    """Normaliza `auto` ou um código de idioma curto antes de carregar mídia ou modelo."""

    normalized = language.strip().lower()
    if normalized == "auto" or _LANGUAGE_PATTERN.fullmatch(normalized):
        return normalized
    raise _argument_error("O idioma deve ser auto ou um código de idioma válido.")


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Lê atributos de objetos faster-whisper e chaves de dublês de teste com a mesma regra."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _timestamp(value: Any, label: str, duration_seconds: float) -> float:
    """Valida e arredonda um timestamp finito dentro da duração da fonte."""

    try:
        timestamp = float(value)
    except (TypeError, ValueError) as error:
        raise _analysis_error(f"A transcrição retornou {label} inválido.") from error
    if not math.isfinite(timestamp) or timestamp < 0 or timestamp > duration_seconds + 0.001:
        raise _analysis_error(f"A transcrição retornou {label} inválido.")
    return round(min(timestamp, duration_seconds), 6)


def _probability(value: Any) -> float | None:
    """Converte probabilidades opcionais sem permitir valores não serializáveis."""

    if value is None:
        return None
    try:
        probability = float(value)
    except (TypeError, ValueError) as error:
        raise _analysis_error("A transcrição retornou probabilidade inválida.") from error
    if not math.isfinite(probability):
        raise _analysis_error("A transcrição retornou probabilidade inválida.")
    return round(probability, 6)


def _clamp_word_timestamp(timestamp: float, segment_start: float, segment_end: float) -> tuple[float, bool]:
    """Limita pequena deriva do Whisper ao segmento e recusa deslocamento editorial relevante."""

    if timestamp < segment_start:
        if segment_start - timestamp > _MAX_WORD_BOUNDARY_DRIFT_SECONDS:
            raise _analysis_error("A transcrição retornou palavra muito distante do segmento.")
        return segment_start, True
    if timestamp > segment_end:
        if timestamp - segment_end > _MAX_WORD_BOUNDARY_DRIFT_SECONDS:
            raise _analysis_error("A transcrição retornou palavra muito distante do segmento.")
        return segment_end, True
    return timestamp, False


def _serialize_word(
    word: Any,
    segment_start: float,
    segment_end: float,
    duration: float,
    counters: dict[str, int],
) -> dict[str, Any] | None:
    """Normaliza uma palavra, contabilizando clamps e descartes sem esconder deriva excessiva."""

    text = _field(word, "word")
    if not isinstance(text, str):
        raise _analysis_error("A transcrição não retornou o texto de uma palavra.")
    raw_start = _timestamp(_field(word, "start"), "início da palavra", duration)
    raw_end = _timestamp(_field(word, "end"), "fim da palavra", duration)
    start, start_clamped = _clamp_word_timestamp(raw_start, segment_start, segment_end)
    end, end_clamped = _clamp_word_timestamp(raw_end, segment_start, segment_end)
    if start_clamped or end_clamped:
        counters["clamped_word_count"] += 1
    if end <= start:
        counters["discarded_word_count"] += 1
        return None
    payload: dict[str, Any] = {"word": text, "start_s": start, "end_s": end}
    probability = _probability(_field(word, "probability"))
    if probability is not None:
        payload["probability"] = probability
    return payload


def _serialize_segment(
    index: int,
    segment: Any,
    duration: float,
    counters: dict[str, int],
) -> dict[str, Any] | None:
    """Normaliza um segmento e remove somente aquele que perdeu todas as palavras válidas."""

    text = _field(segment, "text")
    raw_words = _field(segment, "words")
    if not isinstance(text, str) or raw_words is None:
        raise _analysis_error("O backend não forneceu timestamps por palavra.")
    try:
        words = list(raw_words)
    except TypeError as error:
        raise _analysis_error("O backend não forneceu timestamps por palavra.") from error
    if not words:
        counters["discarded_segment_count"] += 1
        return None

    start = _timestamp(_field(segment, "start"), "início do segmento", duration)
    end = _timestamp(_field(segment, "end"), "fim do segmento", duration)
    if end <= start:
        raise _analysis_error("A transcrição retornou intervalo de segmento inválido.")
    serialized_words = [
        serialized
        for word in words
        if (serialized := _serialize_word(word, start, end, duration, counters)) is not None
    ]
    if not serialized_words:
        counters["discarded_segment_count"] += 1
        return None
    segment_id = _field(segment, "id", index)
    try:
        segment_id = int(segment_id)
    except (TypeError, ValueError):
        segment_id = index
    return {
        "id": segment_id,
        "start_s": start,
        "end_s": end,
        "text": "".join(word["word"] for word in serialized_words),
        "words": serialized_words,
    }


def _build_transcription(
    segments: list[Any],
    info: Any,
    language: str,
    duration: float,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Monta transcrição coerente e devolve métricas da normalização tolerante."""

    detected_language = _field(info, "language") or (None if language == "auto" else language)
    if not isinstance(detected_language, str) or not detected_language:
        raise _analysis_error("O backend não retornou o idioma detectado.")
    counters = {
        "discarded_word_count": 0,
        "clamped_word_count": 0,
        "discarded_segment_count": 0,
    }
    serialized_segments = [
        serialized
        for index, segment in enumerate(segments)
        if (serialized := _serialize_segment(index, segment, duration, counters)) is not None
    ]
    if not serialized_segments:
        raise _analysis_error("A transcrição não retornou nenhuma palavra válida.")
    return (
        {
            "language": detected_language.lower(),
            "language_probability": _probability(_field(info, "language_probability")),
            "text": "".join(segment["text"] for segment in serialized_segments),
            "segments": serialized_segments,
        },
        counters,
    )


def _serialize_artifact(artifact: dict[str, Any]) -> bytes:
    """Serializa o artefato em UTF-8 canônico para consumo e hashing posteriores."""

    return json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def analyze_transcription(
    workdir: str | Path,
    source_id: str,
    input_path: str | Path,
    *,
    language: str = "auto",
    model: str | None = None,
    device: str = "auto",
) -> dict[str, Any]:
    """Valida a fonte, transcreve localmente e publica somente um artefato completo."""

    validate_workdir(workdir)
    validate_source_id(source_id)
    normalized_language = _validate_language(language)
    source = media.inspect_source(input_path)
    source_hash = media.sha256_file(source.path)
    config = whisper_backend.resolve_config(model, device)
    layout: WorkdirLayout = prepare_layout(workdir, source_id, "transcription")
    raw_segments, info = whisper_backend.transcribe(
        config,
        source.path,
        None if normalized_language == "auto" else normalized_language,
    )
    transcription, normalization = _build_transcription(
        raw_segments,
        info,
        normalized_language,
        source.duration_seconds,
    )
    artifact = {
        "schema_version": 1,
        "source": {
            "id": source_id,
            "path": str(source.path),
            "sha256": source_hash,
            "size_bytes": source.size_bytes,
            "duration_seconds": round(source.duration_seconds, 6),
            "container": source.container,
            "audio_codec": source.audio_codec,
            "video_codec": source.video_codec,
        },
        "transcription": transcription,
    }
    publish_atomic(layout, _serialize_artifact(artifact))
    return {
        "source_id": source_id,
        "artifact_path": str(layout.destination),
        "relative_artifact_path": relative_artifact_path(layout),
        "source_sha256": source_hash,
        "duration_seconds": round(source.duration_seconds, 6),
        "language": transcription["language"],
        "segment_count": len(transcription["segments"]),
        **normalization,
    }
