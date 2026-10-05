"""Renderização final autorizada e publicação atômica da full do trabalho."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..integrity.approval import verify_approval
from ..plan.validation import load_validated_plan
from ..workdir import (
    ensure_sources_do_not_alias_output,
    prepare_full_layout,
    publish_full_atomic,
    relative_artifact_path,
)
from .ffmpeg_graph import build_ffmpeg_graph
from .preview import (
    _accel_modes,
    _cleanup_filter_script,
    _cleanup_staging,
    _create_filter_script,
    _create_staging_file,
    _preview_dimensions,
    _render_with_fallback,
    _require_encoders,
    _validate_rendered_preview,
)


def render_full(workdir: str | Path, accel: str = "none") -> dict[str, Any]:
    """Revalida a aprovação e renderiza exatamente a timeline aprovada em `full.mp4`."""

    document, validation = load_validated_plan(workdir)
    layout = prepare_full_layout(workdir)
    ensure_sources_do_not_alias_output(
        [Path(str(record["path"])) for record in validation["sources"]],
        layout.destination,
        "full",
    )
    approval = verify_approval(workdir, validation)

    # O perfil validado limita somente a full; alinhamento 16 preserva a resolução
    # legada de fontes menores sem ultrapassar o teto explícito ou padrão.
    canvas_width, canvas_height = _preview_dimensions(
        document,
        validation,
        max_dimension=int(validation["profile"]["effective_full_width"]),
        artifact_name="full",
        width_alignment=16,
    )
    graph = build_ffmpeg_graph(
        document,
        validation,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    _require_encoders(graph.has_audio)
    modes = _accel_modes(accel)

    expected_duration = float(validation["timeline"]["duration_seconds"])
    first_source_id = str(document["segments"][0]["video"]["source_id"])
    source_records = list(validation["sources"])
    first_source = next(record for record in source_records if record["id"] == first_source_id)
    frame_rate = first_source.get("video_frame_rate")
    staging: Path | None = None
    filter_script: Path | None = None
    try:
        staging = _create_staging_file(layout, "full")
        filter_script = _create_filter_script(layout, graph, "full")
        used_accel = _render_with_fallback(
            modes,
            graph,
            source_records,
            staging,
            filter_script=filter_script,
            duration_seconds=expected_duration,
            frame_rate=float(frame_rate) if frame_rate is not None else None,
            artifact_name="full",
        )
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
        _cleanup_filter_script(filter_script)
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
        "cut_count": validation["cut_count"],
        "layer_count": validation["layer_count"],
        "profile": validation["profile"],
        "video": {
            "codec": metadata.video_codec,
            "accel": used_accel,
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
