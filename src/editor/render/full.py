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
from .preview import _render_artifact, _require_renderable_canvas


def render_full(workdir: str | Path, accel: str = "none") -> dict[str, Any]:
    """Revalida a aprovação e renderiza exatamente a timeline aprovada em `full.mp4`.

    O render em si (janelas de decode, fallback, validação e publicação) é o mesmo corpo
    da preview, `_render_artifact`; aqui ficam só a aprovação e o perfil final.
    """

    document, validation = load_validated_plan(workdir)
    layout = prepare_full_layout(workdir)
    ensure_sources_do_not_alias_output(
        [Path(str(record["path"])) for record in validation["sources"]],
        layout.destination,
        "full",
    )
    approval = verify_approval(workdir, validation)

    # O canvas da full é o que `plan validate` informou em `data.profile` (mesma função,
    # `schema.canvas_dimensions`): tamanho fixo de plataforma em 9:16, 1:1 e 4:5 e, no 16:9,
    # a largura da primeira fonte limitada por `max_full_width`, sem upscale e alinhada a 16.
    canvas_width, canvas_height = _require_renderable_canvas(
        int(validation["profile"]["full_width"]),
        int(validation["profile"]["full_height"]),
        "full",
    )
    metadata, used_accel, decode = _render_artifact(
        document,
        validation,
        layout,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        accel=accel,
        publish=publish_full_atomic,
        max_dimension=None,
        artifact_name="full",
    )

    return {
        "artifact_path": str(layout.destination),
        "relative_artifact_path": relative_artifact_path(layout),
        "plan_sha256": validation["plan_sha256"],
        "timeline_duration_seconds": float(validation["timeline"]["duration_seconds"]),
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
        "decode": decode,
    }
