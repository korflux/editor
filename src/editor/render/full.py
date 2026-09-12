"""Renderização final autorizada e publicação atômica da full do trabalho."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..integrity.approval import verify_approval
from ..plan.validation import load_validated_plan
from ..workdir import (
    prepare_full_layout,
    publish_full_atomic,
    relative_artifact_path,
)
from .ffmpeg_graph import build_ffmpeg_graph
from .preview import (
    _build_ffmpeg_arguments,
    _cleanup_staging,
    _create_staging_file,
    _preview_dimensions,
    _require_encoders,
    _run_ffmpeg,
    _validate_rendered_preview,
)


def render_full(workdir: str | Path) -> dict[str, Any]:
    """Revalida a aprovação e renderiza exatamente a timeline aprovada em `full.mp4`."""

    document, validation = load_validated_plan(workdir)
    layout = prepare_full_layout(workdir)
    approval = verify_approval(workdir, validation)

    # O primeiro vídeo define a resolução final, enquanto o limite de 720 pixels
    # continua exclusivo da preview para preservar a qualidade da full aprovada.
    canvas_width, canvas_height = _preview_dimensions(
        document,
        validation,
        max_dimension=None,
        artifact_name="full",
    )
    graph = build_ffmpeg_graph(
        document,
        validation,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    _require_encoders(graph.has_audio)

    expected_duration = float(validation["timeline"]["duration_seconds"])
    first_source_id = str(document["segments"][0]["video"]["source_id"])
    source_records = list(validation["sources"])
    first_source = next(record for record in source_records if record["id"] == first_source_id)
    frame_rate = first_source.get("video_frame_rate")
    staging = _create_staging_file(layout, "full")
    try:
        arguments = _build_ffmpeg_arguments(
            graph,
            source_records,
            staging,
            duration_seconds=expected_duration,
            frame_rate=float(frame_rate) if frame_rate is not None else None,
        )
        _run_ffmpeg(arguments, expected_duration, "full")
        metadata = _validate_rendered_preview(
            staging,
            expected_duration=expected_duration,
            expected_audio=graph.has_audio,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            max_dimension=None,
            artifact_name="full",
        )
        publish_full_atomic(layout, staging)
        staging = None
    finally:
        _cleanup_staging(staging)

    return {
        "artifact_path": str(layout.destination),
        "relative_artifact_path": relative_artifact_path(layout),
        "plan_sha256": validation["plan_sha256"],
        "timeline_duration_seconds": expected_duration,
        "duration_seconds": round(metadata.duration_seconds, 6),
        "approval": {
            "approved_at": approval["approved_at"],
            "plan_sha256": approval["plan_sha256"],
            "preview_sha256": approval["preview_sha256"],
        },
        "source_count": validation["source_count"],
        "segment_count": validation["segment_count"],
        "overlay_count": validation["overlay_count"],
        "video": {
            "codec": metadata.video_codec,
            "width": metadata.video_width,
            "height": metadata.video_height,
            "frame_rate": metadata.video_frame_rate,
            "max_dimension": max(metadata.video_width or 0, metadata.video_height or 0),
        },
        "audio": {
            "present": metadata.audio_codec is not None,
            "codec": metadata.audio_codec,
        },
    }
