"""Renderização local e publicação atômica da única preview do trabalho."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import os
from pathlib import Path
import re
from uuid import uuid4
from typing import Any

from .. import media, process
from ..contracts import CliError, EXIT_CODES
from ..dependencies import is_regular_path
from ..plan.validation import load_validated_plan
from ..workdir import (
    FullLayout,
    PreviewLayout,
    prepare_preview_layout,
    publish_preview_atomic,
    relative_artifact_path,
)
from .ffmpeg_graph import FfmpegGraph, build_ffmpeg_graph


MAX_PREVIEW_DIMENSION = 720
"""Maior dimensão permitida para reduzir o custo da aprovação visual."""
_ENCODER_LINE = re.compile(r"^\s*[A-Z.]{6}\s+(\S+)")


def _error(code: str, message: str, exit_code: int) -> CliError:
    """Cria erros públicos de renderização sem expor comandos ou stderr do FFmpeg."""

    return CliError(code=code, message=message, exit_code=exit_code)


def _number(value: object) -> str:
    """Formata duração e taxa de quadros sem permitir valores não finitos no comando."""

    number = float(value)
    if not math.isfinite(number):
        raise _error("RENDER_FAILED", "O perfil da preview contém um número inválido.", EXIT_CODES["OPERATION"])
    if abs(number) < 0.0000005:
        number = 0.0
    return f"{number:.6f}".rstrip("0").rstrip(".") or "0"


def _preview_dimensions(
    document: Mapping[str, Any],
    validation: Mapping[str, Any],
    *,
    max_dimension: int | None = MAX_PREVIEW_DIMENSION,
    artifact_name: str = "preview",
) -> tuple[int, int]:
    """Escolhe o canvas 16:9 do primeiro vídeo, com limite opcional para a preview."""

    first_video_source_id = str(document["segments"][0]["video"]["source_id"])
    source = next(
        (
            item
            for item in validation["sources"]
            if str(item["id"]) == first_video_source_id
        ),
        None,
    )
    if not isinstance(source, Mapping):
        raise _error(
            "RENDER_FAILED",
            f"A fonte visual inicial da {artifact_name} não pôde ser determinada.",
            EXIT_CODES["OPERATION"],
        )
    try:
        source_width = int(source["video_width"])
        source_height = int(source["video_height"])
    except (KeyError, TypeError, ValueError) as error:
        raise _error(
            "RENDER_FAILED",
            f"As dimensões da fonte visual inicial da {artifact_name} são inválidas.",
            EXIT_CODES["OPERATION"],
        ) from error
    if source_width <= 0 or source_height <= 0:
        raise _error(
            "RENDER_FAILED",
            f"As dimensões da fonte visual inicial da {artifact_name} são inválidas.",
            EXIT_CODES["OPERATION"],
        )

    # Larguras múltiplas de 32 mantêm largura e altura pares no perfil 16:9
    # yuv420p, evitando arredondamento que alteraria a proporção publicada.
    width = min(source_width, max_dimension) if max_dimension is not None else source_width
    if max_dimension is not None:
        width = max(32, (width // 32) * 32)
    else:
        # A full mantém a maior resolução possível em um canvas 16:9 exato.
        width -= width % 16
    if width < 2:
        raise _error(
            "RENDER_FAILED",
            f"As dimensões da fonte visual inicial da {artifact_name} são inválidas.",
            EXIT_CODES["OPERATION"],
        )
    height = width * 9 // 16
    height -= height % 2
    if height < 2:
        raise _error(
            "RENDER_FAILED",
            f"As dimensões da fonte visual inicial da {artifact_name} são inválidas.",
            EXIT_CODES["OPERATION"],
        )
    return width, height


def _encoder_names(output: str) -> set[str]:
    """Extrai apenas nomes de encoders da sondagem local do FFmpeg."""

    return {
        match.group(1)
        for line in output.splitlines()
        if (match := _ENCODER_LINE.match(line)) is not None
    }


def _require_encoders(has_audio: bool) -> None:
    """Confirma encoders locais antes de criar a saída, produzindo dependência explícita."""

    try:
        result = process.run_tool(
            "ffmpeg",
            ["-hide_banner", "-encoders"],
            timeout_seconds=15,
        )
    except process.ProcessExecutionError as error:
        if error.missing:
            raise _error(
                "DEPENDENCY_MISSING",
                "FFmpeg não está disponível localmente.",
                EXIT_CODES["DEPENDENCY"],
            ) from error
        raise _error(
            "RENDER_FAILED",
            "Não foi possível verificar os encoders locais.",
            EXIT_CODES["OPERATION"],
        ) from error
    if result.returncode != 0:
        raise _error(
            "RENDER_FAILED",
            "Não foi possível verificar os encoders locais.",
            EXIT_CODES["OPERATION"],
        )

    names = _encoder_names(f"{result.stdout or ''}\n{result.stderr or ''}")
    if "libx264" not in names:
        raise _error(
            "DEPENDENCY_MISSING",
            "O encoder H.264 libx264 não está disponível localmente.",
            EXIT_CODES["DEPENDENCY"],
        )
    if has_audio and "aac" not in names:
        raise _error(
            "DEPENDENCY_MISSING",
            "O encoder AAC não está disponível localmente.",
            EXIT_CODES["DEPENDENCY"],
        )


def _create_staging_file(
    layout: PreviewLayout | FullLayout,
    artifact_name: str = "preview",
) -> Path:
    """Cria um temporário exclusivo dentro do trabalho para um render MP4."""

    for _ in range(8):
        candidate = layout.temporary / f".{artifact_name}-{uuid4().hex}.mp4"
        try:
            with candidate.open("xb"):
                pass
        except FileExistsError:
            continue
        except OSError as error:
            raise _error(
                "OUTPUT_PUBLISH_FAILED",
                f"Não foi possível preparar a saída temporária da {artifact_name}.",
                EXIT_CODES["OPERATION"],
            ) from error
        if is_regular_path(candidate):
            return candidate
    raise _error(
        "OUTPUT_PUBLISH_FAILED",
        f"Não foi possível reservar a saída temporária da {artifact_name}.",
        EXIT_CODES["OPERATION"],
    )


def _build_ffmpeg_arguments(
    graph: FfmpegGraph,
    source_records: Sequence[Mapping[str, Any]],
    staging: Path,
    *,
    duration_seconds: float,
    frame_rate: float | None,
) -> list[str]:
    """Monta a lista estruturada de argumentos sem interpolar caminhos em shell."""

    records = {str(record["id"]): record for record in source_records}
    arguments = ["-hide_banner", "-nostdin", "-loglevel", "error"]
    for source_id in graph.input_source_ids:
        record = records.get(source_id)
        if record is None:
            raise _error(
                "RENDER_FAILED",
                "Uma fonte usada pela timeline não pôde ser localizada.",
                EXIT_CODES["OPERATION"],
            )
        arguments.extend(["-i", str(record["path"])])

    arguments.extend(
        [
            "-filter_complex",
            graph.filter_complex,
            "-map",
            f"[{graph.video_label}]",
            "-map_metadata",
            "-1",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if frame_rate is not None:
        arguments.extend(["-r", _number(frame_rate)])
    if graph.audio_label:
        arguments.extend(
            [
                "-map",
                f"[{graph.audio_label}]",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
            ]
        )
    else:
        arguments.append("-an")
    arguments.extend(
        [
            "-movflags",
            "+faststart",
            "-t",
            _number(duration_seconds),
            "-y",
            str(staging),
        ]
    )
    return arguments


def _run_ffmpeg(
    arguments: Sequence[str],
    duration_seconds: float,
    artifact_name: str = "preview",
) -> None:
    """Executa um render com timeout proporcional e converte falhas em erro seguro."""

    timeout_seconds = max(60.0, min(14_400.0, duration_seconds * 8 + 30))
    try:
        result = process.run_tool(
            "ffmpeg",
            arguments,
            timeout_seconds=timeout_seconds,
        )
    except process.ProcessExecutionError as error:
        if error.missing:
            raise _error(
                "DEPENDENCY_MISSING",
                "FFmpeg não está disponível localmente.",
                EXIT_CODES["DEPENDENCY"],
            ) from error
        if error.timed_out:
            raise _error(
                "RENDER_FAILED",
                f"A renderização da {artifact_name} excedeu o tempo permitido.",
                EXIT_CODES["OPERATION"],
            ) from error
        raise _error(
            "RENDER_FAILED",
            f"O FFmpeg não conseguiu renderizar a {artifact_name}.",
            EXIT_CODES["OPERATION"],
        ) from error
    if result.returncode != 0:
        raise _error(
            "RENDER_FAILED",
            f"O FFmpeg não conseguiu renderizar a {artifact_name}.",
            EXIT_CODES["OPERATION"],
        )


def _validate_rendered_preview(
    staging: Path,
    *,
    expected_duration: float,
    expected_audio: bool,
    canvas_width: int,
    canvas_height: int,
    max_dimension: int | None = MAX_PREVIEW_DIMENSION,
    artifact_name: str = "preview",
) -> media.MediaMetadata:
    """Valida o MP4 produzido antes de permitir sua publicação como artefato vigente."""

    try:
        metadata = media.inspect_source(
            staging,
            require_audio=expected_audio,
            require_video=True,
        )
    except CliError as error:
        if error.code == "DEPENDENCY_MISSING":
            raise
        raise _error(
            "RENDER_FAILED",
            f"A {artifact_name} renderizada não passou na validação de mídia.",
            EXIT_CODES["OPERATION"],
        ) from error

    exceeds_max_dimension = (
        max_dimension is not None
        and max(metadata.video_width or 0, metadata.video_height or 0) > max_dimension
    )
    if (
        metadata.video_width != canvas_width
        or metadata.video_height != canvas_height
        or metadata.video_width is None
        or metadata.video_height is None
        or exceeds_max_dimension
        or abs(metadata.video_width / metadata.video_height - 16 / 9) > 0.001
    ):
        raise _error(
            "RENDER_FAILED",
            (
                "A preview renderizada não respeita o perfil 16:9 de até 720 pixels."
                if max_dimension is not None
                else "A full renderizada não respeita o perfil final 16:9."
            ),
            EXIT_CODES["OPERATION"],
        )
    if (metadata.audio_codec is not None) != expected_audio:
        raise _error(
            "RENDER_FAILED",
            f"A {artifact_name} renderizada não respeita as faixas de áudio do plano.",
            EXIT_CODES["OPERATION"],
        )
    frame_rate = metadata.video_frame_rate or 25.0
    duration_tolerance = max(0.1, 2.0 / frame_rate)
    if abs(metadata.duration_seconds - expected_duration) > duration_tolerance:
        raise _error(
            "RENDER_FAILED",
            f"A duração da {artifact_name} renderizada não corresponde à timeline.",
            EXIT_CODES["OPERATION"],
        )
    return metadata


def _cleanup_staging(staging: Path | None) -> None:
    """Remove somente o temporário da operação, preservando o artefato vigente em qualquer falha."""

    if staging is None or not os.path.lexists(staging):
        return
    try:
        staging.unlink()
    except OSError:
        pass


def render_preview(workdir: str | Path) -> dict[str, Any]:
    """Valida o plano, renderiza a preview em `.tmp` e publica um único arquivo completo."""

    document, validation = load_validated_plan(workdir)
    layout = prepare_preview_layout(workdir)
    canvas_width, canvas_height = _preview_dimensions(document, validation)
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
    staging = _create_staging_file(layout)
    try:
        arguments = _build_ffmpeg_arguments(
            graph,
            source_records,
            staging,
            duration_seconds=expected_duration,
            frame_rate=float(frame_rate) if frame_rate is not None else None,
        )
        _run_ffmpeg(arguments, expected_duration)
        metadata = _validate_rendered_preview(
            staging,
            expected_duration=expected_duration,
            expected_audio=graph.has_audio,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
        publish_preview_atomic(layout, staging)
        staging = None
    finally:
        _cleanup_staging(staging)

    return {
        "artifact_path": str(layout.destination),
        "relative_artifact_path": relative_artifact_path(layout),
        "plan_sha256": validation["plan_sha256"],
        "duration_seconds": round(metadata.duration_seconds, 6),
        "timeline_duration_seconds": expected_duration,
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
