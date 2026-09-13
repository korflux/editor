"""Construção determinística do grafo de filtros usado pelo render de preview."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
import textwrap
from typing import Any

from ..plan.schema import MOTION_ZOOM_MAX


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
            escaped.append("\n")
        elif character == "'":
            escaped.append("'\\''")
        elif character in {"\\", ":", ",", ";", "[", "]"}:
            escaped.append(f"\\{character}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _layer_text(value: str, *, wrap: bool, size_px: int, box_width: int) -> str:
    """Quebra linhas pela largura da caixa, preservando quebras explícitas."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not wrap:
        return normalized

    # ponytail: largura média aproxima fontes proporcionais; medir glifos quando a quebra precisa for necessária.
    character_width = max(1.0, size_px * 0.6)
    max_characters = max(1, int(box_width / character_width))
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


def _linear_motion_expression(start: object, end: object, duration: float) -> str:
    """Gera uma expressão FFmpeg de interpolação linear pelo tempo local do segmento."""

    progress = f"min(1\\,max(0\\,t/{_number(duration)}))"
    return f"({_number(start)}+({_number(end)}-{_number(start)})*{progress})"


def _motion_filter_graph(
    input_label: str,
    motion: Mapping[str, Any],
    *,
    segment_index: int,
    segment_duration: float,
    frame_rate: float | None,
    canvas_width: int,
    canvas_height: int,
) -> tuple[str, str]:
    """Transforma o quadro normalizado e retorna o grafo com canvas preto de dimensão fixa."""

    start = motion["from"]
    end = motion["to"]
    focal_x = _linear_motion_expression(start["x"], end["x"], segment_duration)
    focal_y = _linear_motion_expression(start["y"], end["y"], segment_duration)
    zoom = _linear_motion_expression(start["zoom"], end["zoom"], segment_duration)
    padded_width = int(canvas_width * MOTION_ZOOM_MAX)
    padded_height = int(canvas_height * MOTION_ZOOM_MAX)
    rate = _number(frame_rate if frame_rate and frame_rate > 0 else 25.0)
    scaled_width = f"trunc(iw*{zoom}/2)*2"
    scaled_height = f"trunc(ih*{zoom}/2)*2"
    crop_x = f"min(max((iw-ow)*{focal_x}\\,0)\\,iw-ow)"
    crop_y = f"min(max((ih-oh)*{focal_y}\\,0)\\,ih-oh)"
    scaled_label = f"m{segment_index}s"
    background_label = f"m{segment_index}b"
    composited_label = f"m{segment_index}p"
    output_label = f"m{segment_index}c"
    graph = ";".join(
        [
            f"[{input_label}]scale=w={scaled_width}:h={scaled_height}:eval=frame[{scaled_label}]",
            (
                f"color=c=black:s={padded_width}x{padded_height}:r={rate}:"
                f"d={_number(segment_duration)}[{background_label}]"
            ),
            (
                f"[{background_label}][{scaled_label}]overlay="
                f"x=(W-w)*{focal_x}:y=(H-h)*{focal_y}:eval=frame:shortest=1"
                f"[{composited_label}]"
            ),
            (
                f"[{composited_label}]crop=w={canvas_width}:h={canvas_height}:"
                f"x={crop_x}:y={crop_y}[{output_label}]"
            ),
        ]
    )
    return graph, output_label


def _video_segment_filter(
    segment: Mapping[str, Any],
    input_indexes: Mapping[str, int],
    segment_index: int,
    canvas_width: int,
    canvas_height: int,
    frame_rate: float | None,
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
    if "motion" in video:
        base_label = f"v{segment_index}base"
        motion_graph, motion_label = _motion_filter_graph(
            base_label,
            video["motion"],
            segment_index=segment_index,
            segment_duration=float(video["out_s"]) - float(video["in_s"]),
            frame_rate=frame_rate,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
        return (
            f"[{input_index}:v:0]{','.join(filters)}[{base_label}];"
            f"{motion_graph};[{motion_label}]format=yuv420p[v{segment_index}]"
        )
    filters.append("format=yuv420p")
    return f"[{input_index}:v:0]{','.join(filters)}[v{segment_index}]"


def _audio_track_filter(
    track: Mapping[str, Any],
    input_indexes: Mapping[str, int],
    segment_index: int,
    track_index: int,
    required_duration: float,
) -> str:
    """Corta, repete quando solicitado, aplica ganho e cobre a janela global do grupo."""

    source_id = str(track["source_id"])
    input_index = input_indexes[source_id]
    start = _number(track["in_s"])
    end = _number(track["out_s"])
    duration = _number(required_duration)
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
    required_duration: float,
) -> str:
    """Monta o grupo de áudio sem incluir implicitamente o áudio do vídeo exibido."""

    audio = segment["audio"]
    tracks = list(audio["tracks"])
    output_label = f"aseg{segment_index}"
    if not tracks:
        duration = _number(required_duration)
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
                required_duration,
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


def _position_audio_group(input_label: str, start_s: float, group_index: int) -> str:
    """Desloca um grupo para a timeline global sem alterar seu ganho explícito."""

    if start_s < 0:
        raise ValueError("janela de áudio negativa")
    output_label = f"apos{group_index}"
    if start_s <= 0:
        return f"[{input_label}]anull[{output_label}]"
    delay = _number(start_s * 1000)
    return f"[{input_label}]adelay={delay}|{delay}[{output_label}]"


def _global_audio_filter(
    group_labels: list[str],
    timeline_duration: float,
) -> tuple[list[str], str]:
    """Mixa grupos posicionados e recorta o resultado exatamente à timeline visual."""

    if not group_labels:
        raise ValueError("nenhum grupo de áudio para mixar")

    parts: list[str] = []
    if len(group_labels) == 1:
        mixed_label = group_labels[0]
    else:
        mixed_label = "aglobalmix"
        inputs = "".join(f"[{label}]" for label in group_labels)
        parts.append(
            f"{inputs}amix=inputs={len(group_labels)}:duration=longest:"
            f"dropout_transition=0:normalize=0[{mixed_label}]"
        )
    output_label = "aglobal"
    parts.append(
        f"[{mixed_label}]atrim=start=0:end={_number(timeline_duration)},"
        f"asetpts=PTS-STARTPTS[{output_label}]"
    )
    return parts, output_label


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


def _text_layer_filter(
    input_label: str,
    layer: Mapping[str, Any],
    layer_index: int,
    canvas_width: int,
    canvas_height: int,
) -> tuple[str, str]:
    """Desenha texto em uma superfície transparente limitada à caixa e a sobrepõe no intervalo."""

    left = min(canvas_width - 1, int(float(layer["x"]) * canvas_width))
    top = min(canvas_height - 1, int(float(layer["y"]) * canvas_height))
    right = min(
        canvas_width, math.ceil((float(layer["x"]) + float(layer["width"])) * canvas_width)
    )
    bottom = min(
        canvas_height, math.ceil((float(layer["y"]) + float(layer["height"])) * canvas_height)
    )
    box_width = max(1, right - left)
    box_height = max(1, bottom - top)
    text = _layer_text(
        str(layer["text"]),
        wrap=bool(layer["wrap"]),
        size_px=int(layer["size_px"]),
        box_width=box_width,
    )
    font = _escape_filter_value(str(layer["font"]))
    escaped_text = _escape_filter_value(text)
    start = _number(layer["start_s"])
    end = _number(layer["end_s"])
    align_x = {"left": "0", "center": "(w-text_w)/2", "right": "w-text_w"}[
        str(layer["align"])
    ]
    layer_label = f"textlayer{layer_index}"
    output_label = f"vtext{layer_index}"
    surface = (
        f"color=c=black@0:s={box_width}x{box_height}:r=25:d={end},format=rgba,"
        f"drawtext=font='{font}':fontsize={int(layer['size_px'])}:"
        f"fontcolor={layer['color']}:text='{escaped_text}':expansion=none:"
        f"text_align={layer['align']}:x={align_x}:y=0[{layer_label}]"
    )
    composite = (
        f"[{input_label}][{layer_label}]overlay=x={left}:y={top}:eof_action=pass:"
        f"enable='gte(t\\,{start})*lt(t\\,{end})'[{output_label}]"
    )
    return surface + ";" + composite, output_label


def _used_source_ids(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Lista fontes usadas por segmentos e camadas na ordem estável de primeira referência."""

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
    for layer in document["layers"]:
        if layer["type"] in {"image", "video"}:
            source_id = str(layer["source_id"])
            if source_id not in seen:
                seen.add(source_id)
                ordered.append(source_id)
    return tuple(ordered)


def _media_layer_filter(
    input_label: str,
    layer: Mapping[str, Any],
    layer_index: int,
    input_index: int,
    canvas_width: int,
    canvas_height: int,
    timeline_duration: float,
) -> tuple[str, str]:
    """Dimensiona imagem ou trecho de vídeo e o sobrepõe sem mapear áudio da camada."""

    left = min(canvas_width - 1, int(float(layer["x"]) * canvas_width))
    top = min(canvas_height - 1, int(float(layer["y"]) * canvas_height))
    right = min(canvas_width, math.ceil((float(layer["x"]) + float(layer["width"])) * canvas_width))
    bottom = min(canvas_height, math.ceil((float(layer["y"]) + float(layer["height"])) * canvas_height))
    width = max(1, right - left)
    height = max(1, bottom - top)
    fit = str(layer["fit"])
    scaled = f"media{layer_index}"
    output = f"vmedia{layer_index}"
    scale_mode = "decrease" if fit == "contain" else "increase"
    if layer["type"] == "video":
        # O PTS global posiciona o primeiro frame do recorte na abertura da camada.
        source = (
            f"trim=start={_number(layer['in_s'])}:end={_number(layer['out_s'])},"
            f"setpts=PTS-STARTPTS+{_number(layer['start_s'])}/TB"
        )
    else:
        source = f"trim=duration={_number(timeline_duration)},setpts=PTS-STARTPTS"
    filters = [
        f"[{input_index}:v:0]{source},format=rgba,"
        f"scale=w={width}:h={height}:force_original_aspect_ratio={scale_mode}"
    ]
    if fit == "crop":
        filters.append(f",crop=w={width}:h={height}:x=(iw-ow)/2:y=(ih-oh)/2")
    filters.append(f"[{scaled}]")
    x = str(left) if fit == "crop" else f"{left}+({width}-w)/2"
    y = str(top) if fit == "crop" else f"{top}+({height}-h)/2"
    start = _number(layer["start_s"])
    end = _number(layer["end_s"])
    filters.append(
        f";[{input_label}][{scaled}]overlay=x={x}:y={y}:eof_action=pass:"
        f"enable='gte(t\\,{start})*lt(t\\,{end})'[{output}]"
    )
    return "".join(filters), output


def build_ffmpeg_graph(
    document: Mapping[str, Any],
    validation: Mapping[str, Any],
    *,
    canvas_width: int,
    canvas_height: int,
) -> FfmpegGraph:
    """Cria o grafo completo de vídeo, áudio, concatenação e processamento final."""

    input_source_ids = _used_source_ids(document)
    input_indexes = {source_id: index for index, source_id in enumerate(input_source_ids)}
    segments = list(document["segments"])
    timeline_segments = list(validation["timeline"]["segments"])
    source_records = {str(record["id"]): record for record in validation["sources"]}
    has_audio = any(segment["audio"]["mode"] != "mute" for segment in segments)
    parts: list[str] = []
    video_labels: list[str] = []
    audio_group_labels: list[str] = []

    for segment_index, (segment, timeline_segment) in enumerate(
        zip(segments, timeline_segments, strict=True)
    ):
        source_record = source_records[str(segment["video"]["source_id"])]
        source_frame_rate = source_record["video_frame_rate"]
        parts.append(
            _video_segment_filter(
                segment,
                input_indexes,
                segment_index,
                canvas_width,
                canvas_height,
                float(source_frame_rate) if source_frame_rate is not None else None,
            )
        )
        video_labels.append(f"v{segment_index}")
        if has_audio and segment["audio"]["mode"] != "mute":
            parts.append(
                _audio_segment_filter(
                    segment,
                    input_indexes,
                    segment_index,
                    float(timeline_segment["audio_duration_s"]),
                )
            )
            parts.append(
                _position_audio_group(
                    f"aseg{segment_index}",
                    float(timeline_segment["audio_start_s"]),
                    segment_index,
                )
            )
            audio_group_labels.append(f"apos{segment_index}")

    concat_inputs = "".join(f"[{video_label}]" for video_label in video_labels)
    concat_video_label = "vconcat"
    parts.append(
        f"{concat_inputs}concat=n={len(video_labels)}:v=1:a=0[{concat_video_label}]"
    )

    video_label = concat_video_label
    for layer_index, layer in enumerate(document["layers"]):
        if layer["type"] == "text":
            filter_part, video_label = _text_layer_filter(
                video_label, layer, layer_index, canvas_width, canvas_height
            )
            parts.append(filter_part)
        elif layer["type"] in {"image", "video"}:
            filter_part, video_label = _media_layer_filter(
                video_label, layer, layer_index,
                input_indexes[str(layer["source_id"])],
                canvas_width, canvas_height,
                float(validation["timeline"]["duration_seconds"]),
            )
            parts.append(filter_part)

    audio_label: str | None = None
    if has_audio:
        global_audio_filters, audio_label = _global_audio_filter(
            audio_group_labels,
            float(validation["timeline"]["duration_seconds"]),
        )
        parts.extend(global_audio_filters)
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
