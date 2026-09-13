"""Construção determinística do grafo de filtros usado pelo render de preview."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import textwrap
from typing import Any


MIN_LIMITER_DBFS = 20 * math.log10(0.0625)
"""Menor teto representável diretamente pelo filtro `alimiter` do FFmpeg."""


@dataclass(frozen=True)
class FfmpegGraph:
    """Descreve o grafo e os labels que o comando FFmpeg deve mapear."""

    filter_complex: str
    video_label: str
    audio_label: str | None
    input_source_ids: tuple[str, ...]
    has_audio: bool


def _number(value: object) -> str:
    """Formata números do plano sem notação instável ou valores não finitos."""

    number = float(value)
    if not math.isfinite(number):
        raise ValueError("número de filtro não finito")
    if abs(number) < 0.0000005:
        number = 0.0
    return f"{number:.6f}".rstrip("0").rstrip(".") or "0"


def _escape_filter_value(value: str) -> str:
    """Escapa texto e nomes de fonte para que não alterem a sintaxe do filtro."""

    escaped: list[str] = []
    for character in value.replace("\r\n", "\n").replace("\r", "\n"):
        if character == "\n":
            escaped.append("\\n")
        elif character in {"\\", "'", ":", ",", ";", "[", "]"}:
            escaped.append(f"\\{character}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _overlay_text(value: str, *, wrap: bool, size_px: int, canvas_width: int) -> str:
    """Aplica quebra de linha simples e determinística dentro da margem visual segura."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not wrap:
        return normalized

    character_width = max(1.0, size_px * 0.6)
    max_characters = max(1, int(canvas_width * 0.9 / character_width))
    lines: list[str] = []
    for line in normalized.split("\n"):
        lines.extend(
            textwrap.wrap(
                line,
                width=max_characters,
                break_long_words=True,
                break_on_hyphens=False,
            )
            or [""]
        )
    return "\n".join(lines)


def _position_expressions(position: str) -> tuple[str, str]:
    """Converte posições nomeadas do plano em expressões com margem de cinco por cento."""

    positions = {
        "top-left": ("w*0.05", "h*0.05"),
        "top-center": ("(w-text_w)/2", "h*0.05"),
        "center": ("(w-text_w)/2", "(h-text_h)/2"),
        "bottom-center": ("(w-text_w)/2", "h-text_h-h*0.05"),
        "bottom-right": ("w-text_w-w*0.05", "h-text_h-h*0.05"),
    }
    return positions[position]


def _video_segment_filter(
    segment: Mapping[str, Any],
    input_indexes: Mapping[str, int],
    segment_index: int,
    canvas_width: int,
    canvas_height: int,
) -> str:
    """Corta um vídeo e normaliza sua imagem para o canvas 16:9 da preview."""

    video = segment["video"]
    source_id = str(video["source_id"])
    input_index = input_indexes[source_id]
    start = _number(video["in_s"])
    end = _number(video["out_s"])
    fit = str(video["fit"])
    filters = [
        f"trim=start={start}:end={end}",
        "setpts=PTS-STARTPTS",
        "setsar=1",
    ]
    if fit == "crop":
        filters.extend(
            [
                f"scale={canvas_width}:{canvas_height}:force_original_aspect_ratio=increase",
                f"crop={canvas_width}:{canvas_height}:(iw-ow)/2:(ih-oh)/2",
            ]
        )
    else:
        filters.extend(
            [
                f"scale={canvas_width}:{canvas_height}:force_original_aspect_ratio=decrease",
                f"pad={canvas_width}:{canvas_height}:(ow-iw)/2:(oh-ih)/2:color=black",
            ]
        )
    filters.append("format=yuv420p")
    return f"[{input_index}:v:0]{','.join(filters)}[v{segment_index}]"


def _audio_track_filter(
    track: Mapping[str, Any],
    input_indexes: Mapping[str, int],
    segment_index: int,
    track_index: int,
    segment_duration: float,
) -> str:
    """Corta, repete quando solicitado, aplica ganho e padroniza cada faixa de áudio."""

    source_id = str(track["source_id"])
    input_index = input_indexes[source_id]
    start = _number(track["in_s"])
    end = _number(track["out_s"])
    duration = _number(segment_duration)
    gain = _number(track["gain_db"])
    filters = [
        f"atrim=start={start}:end={end}",
        "asetpts=PTS-STARTPTS",
    ]
    if bool(track.get("loop", False)):
        filters.extend(
            [
                "aloop=loop=-1:size=2147483647",
                f"atrim=duration={duration}",
                "asetpts=PTS-STARTPTS",
            ]
        )
    else:
        filters.append(f"atrim=duration={duration}")
    filters.extend(
        [
            f"volume={gain}dB",
            "aresample=48000:async=0",
            "aformat=sample_rates=48000:channel_layouts=stereo",
        ]
    )
    label = f"a{segment_index}t{track_index}"
    return f"[{input_index}:a:0]{','.join(filters)}[{label}]"


def _audio_segment_filter(
    segment: Mapping[str, Any],
    input_indexes: Mapping[str, int],
    segment_index: int,
    segment_duration: float,
) -> str:
    """Monta o áudio de um segmento sem incluir implicitamente o áudio do vídeo exibido."""

    audio = segment["audio"]
    tracks = list(audio["tracks"])
    output_label = f"aseg{segment_index}"
    if not tracks:
        duration = _number(segment_duration)
        return (
            f"anullsrc=r=48000:cl=stereo,atrim=duration={duration},"
            f"asetpts=PTS-STARTPTS[{output_label}]"
        )

    track_labels: list[str] = []
    parts: list[str] = []
    for track_index, track in enumerate(tracks):
        parts.append(
            _audio_track_filter(
                track,
                input_indexes,
                segment_index,
                track_index,
                segment_duration,
            )
        )
        track_labels.append(f"a{segment_index}t{track_index}")

    if len(track_labels) == 1:
        parts.append(f"[{track_labels[0]}]anull[{output_label}]")
    else:
        inputs = "".join(f"[{label}]" for label in track_labels)
        parts.append(
            f"{inputs}amix=inputs={len(track_labels)}:duration=longest:"
            f"dropout_transition=0:normalize=0[{output_label}]"
        )
    return ";".join(parts)


def _audio_processing_filters(
    audio_label: str,
    processing: Mapping[str, Any],
) -> tuple[list[str], str]:
    """Aplica normalização e limiter na saída mixada, preservando o ganho explícito das faixas."""

    filters: list[str] = []
    current = audio_label
    if bool(processing.get("normalize_loudness", False)):
        target = float(processing.get("target_lufs", -16))
        limiter = processing.get("limiter", {})
        limiter_map = limiter if isinstance(limiter, Mapping) else {}
        ceiling = float(limiter_map.get("ceiling_dbfs", -1))
        true_peak = _number(
            max(
                -9.0,
                min(0.0, ceiling if bool(limiter_map.get("enabled", False)) else -1.0),
            )
        )
        next_label = "anormalized"
        filters.append(
            f"[{current}]loudnorm=I={_number(max(-70.0, min(-5.0, target)))}:LRA=7:TP={true_peak}:"
            f"linear=false:print_format=none[{next_label}]"
        )
        current = next_label
        if target > -5.0:
            post_gain = target + 5.0
            next_label = "anormalized_target"
            filters.append(f"[{current}]volume={_number(post_gain)}dB[{next_label}]")
            current = next_label

    limiter_value = processing.get("limiter", {})
    if isinstance(limiter_value, Mapping) and bool(limiter_value.get("enabled", False)):
        ceiling = float(limiter_value.get("ceiling_dbfs", -1))
        limiter_dbfs = max(ceiling, MIN_LIMITER_DBFS)
        pre_gain = ceiling - limiter_dbfs
        next_label = "alimited"
        prefix = (
            f"[{current}]volume={_number(pre_gain)}dB,"
            if pre_gain < -0.000001
            else f"[{current}]"
        )
        limit = _number(10 ** (limiter_dbfs / 20))
        filters.append(
            f"{prefix}alimiter=limit={limit}:level=0:latency=1[{next_label}]"
        )
        current = next_label
    return filters, current


def _overlay_filter(
    input_label: str,
    overlay: Mapping[str, Any],
    overlay_index: int,
    canvas_width: int,
    canvas_height: int,
) -> str:
    """Desenha um lettering estático somente no intervalo declarado pelo plano."""

    x, y = _position_expressions(str(overlay["position"]))
    text = _overlay_text(
        str(overlay["text"]),
        wrap=bool(overlay["wrap"]),
        size_px=int(overlay["size_px"]),
        canvas_width=canvas_width,
    )
    font = _escape_filter_value(str(overlay["font"]))
    escaped_text = _escape_filter_value(text)
    start = _number(overlay["start_s"])
    end = _number(overlay["end_s"])
    output_label = f"vtext{overlay_index}"
    return (
        f"[{input_label}]drawtext=font='{font}':fontsize={int(overlay['size_px'])}:"
        f"fontcolor={overlay['color']}:text='{escaped_text}':expansion=none:"
        f"text_align={overlay['align']}:x={x}:y={y}:fix_bounds=1:"
        f"enable='gte(t\\,{start})*lt(t\\,{end})'[{output_label}]"
    )


def _used_source_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Lista fontes usadas pela timeline na ordem declarada, sem adicionar entradas especulativas."""

    seen: set[str] = set()
    ordered: list[str] = []
    for segment in document["segments"]:
        candidates = [segment["video"]["source_id"]]
        candidates.extend(track["source_id"] for track in segment["audio"]["tracks"])
        for source_id in candidates:
            normalized = str(source_id)
            if normalized not in seen:
                seen.add(normalized)
                ordered.append(normalized)
    return tuple(ordered)


def build_ffmpeg_graph(
    document: Mapping[str, Any],
    validation: Mapping[str, Any],
    *,
    canvas_width: int,
    canvas_height: int,
) -> FfmpegGraph:
    """Cria o grafo completo de vídeo, áudio, concatenação, overlays e processamento final."""

    input_source_ids = _used_source_ids(document)
    input_indexes = {source_id: index for index, source_id in enumerate(input_source_ids)}
    segments = list(document["segments"])
    timeline_segments = list(validation["timeline"]["segments"])
    has_audio = any(segment["audio"]["mode"] != "mute" for segment in segments)
    parts: list[str] = []
    video_labels: list[str] = []
    audio_labels: list[str] = []

    for segment_index, (segment, timeline_segment) in enumerate(
        zip(segments, timeline_segments, strict=True)
    ):
        parts.append(
            _video_segment_filter(
                segment,
                input_indexes,
                segment_index,
                canvas_width,
                canvas_height,
            )
        )
        video_labels.append(f"v{segment_index}")
        if has_audio:
            parts.append(
                _audio_segment_filter(
                    segment,
                    input_indexes,
                    segment_index,
                    float(timeline_segment["duration_s"]),
                )
            )
            audio_labels.append(f"aseg{segment_index}")

    concat_inputs = "".join(
        f"[{video_label}]" + (f"[{audio_label}]" if has_audio else "")
        for video_label, audio_label in zip(video_labels, audio_labels or [None] * len(video_labels))
    )
    concat_video_label = "vconcat"
    concat_audio_label = "aconcat" if has_audio else None
    concat_outputs = f"[{concat_video_label}]"
    if concat_audio_label:
        concat_outputs += f"[{concat_audio_label}]"
    parts.append(
        f"{concat_inputs}concat=n={len(video_labels)}:v=1:a={1 if has_audio else 0}"
        f"{concat_outputs}"
    )

    video_label = concat_video_label
    for overlay_index, overlay in enumerate(document.get("overlays", [])):
        parts.append(
            _overlay_filter(
                video_label,
                overlay,
                overlay_index,
                canvas_width,
                canvas_height,
            )
        )
        video_label = f"vtext{overlay_index}"

    audio_label: str | None = concat_audio_label
    if audio_label:
        processing_filters, audio_label = _audio_processing_filters(
            audio_label,
            validation["audio_processing"],
        )
        parts.extend(processing_filters)

    return FfmpegGraph(
        filter_complex=";".join(parts),
        video_label=video_label,
        audio_label=audio_label,
        input_source_ids=input_source_ids,
        has_audio=has_audio,
    )
