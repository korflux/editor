"""Allowlist estrutural do schema v1 do plano linear."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..contracts import CliError, EXIT_CODES


ROOT_FIELDS = frozenset(
    {"schema_version", "profile", "sources", "segments", "overlays", "audio_processing"}
)
PROFILE_FIELDS = frozenset({"aspect_ratio"})
SOURCE_FIELDS = frozenset({"id", "kind", "path"})
SEGMENT_FIELDS = frozenset({"video", "audio"})
VIDEO_FIELDS = frozenset({"source_id", "in_s", "out_s", "fit"})
AUDIO_FIELDS = frozenset({"mode", "tracks"})
TRACK_FIELDS = frozenset({"source_id", "in_s", "out_s", "gain_db", "loop"})
OVERLAY_FIELDS = frozenset(
    {"text", "start_s", "end_s", "position", "font", "size_px", "color", "align", "wrap"}
)
AUDIO_PROCESSING_FIELDS = frozenset({"normalize_loudness", "target_lufs", "limiter", "ducking"})
LIMITER_FIELDS = frozenset({"enabled", "ceiling_dbfs"})

SOURCE_KINDS = frozenset({"video", "audio"})
FIT_MODES = frozenset({"preserve", "contain", "crop"})
AUDIO_MODES = frozenset({"keep", "mute", "replace", "mix"})
OVERLAY_POSITIONS = frozenset(
    {"top-left", "top-center", "center", "bottom-center", "bottom-right"}
)
OVERLAY_ALIGNS = frozenset({"left", "center", "right"})


def plan_error(message: str) -> CliError:
    """Cria o único erro público usado para qualquer inconsistência do plano."""

    return CliError(code="PLAN_INVALID", message=message, exit_code=EXIT_CODES["STATE"])


def require_object(value: Any, location: str) -> dict[str, Any]:
    """Garante que um trecho do JSON seja objeto antes de acessar suas chaves."""

    if not isinstance(value, dict):
        raise plan_error(f"O campo {location} deve ser um objeto JSON.")
    return value


def require_array(value: Any, location: str) -> list[Any]:
    """Garante que um trecho do JSON seja uma lista antes de iterar seus itens."""

    if not isinstance(value, list):
        raise plan_error(f"O campo {location} deve ser uma lista JSON.")
    return value


def reject_unknown_fields(
    value: Mapping[str, Any], allowed: Iterable[str], location: str
) -> None:
    """Rejeita campos não documentados para impedir parâmetros silenciosamente ignorados."""

    unknown = set(value) - set(allowed)
    if unknown:
        raise plan_error(f"O plano contém campo não permitido em {location}.")
