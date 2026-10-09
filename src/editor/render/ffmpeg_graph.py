"""Construção determinística do grafo de filtros usado pelo render de preview."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
import math
import textwrap
from typing import Any

from .windows import DecodeWindow, plan_decode_windows


MIN_LIMITER_DBFS = 20 * math.log10(0.0625)
"""Menor teto representável diretamente pelo filtro `alimiter` do FFmpeg."""


@dataclass(frozen=True)
class FfmpegGraph:
    """Descreve o grafo e os labels que o comando FFmpeg deve mapear."""

    filter_complex: str
    video_label: str
    audio_label: str | None
    input_source_ids: tuple[str, ...]
    """Fonte de cada entrada do FFmpeg, na ordem dos `-i`; repete o id quando a fonte tem janelas."""
    has_audio: bool
    input_windows: tuple[DecodeWindow | None, ...] = ()
    """Janela de cada entrada (paralela a `input_source_ids`); vazio ou `None` é leitura completa sem `-ss`."""


class _GraphInputs:
    """Resolve qual entrada do FFmpeg lê um recorte de fonte e quanto o grafo deve deslocá-lo.

    Sem janelas cada fonte tem uma entrada e deslocamento zero, o que reproduz o grafo
    anterior byte a byte. Com janelas, o recorte usa a janela que contém seu `in_s`;
    o planejador garante que todo recorte cabe inteiro numa única janela.
    """

    def __init__(
        self,
        source_order: tuple[str, ...],
        windows: Mapping[str, tuple[DecodeWindow, ...]],
    ) -> None:
        self.source_ids: list[str] = []
        self.windows: list[DecodeWindow | None] = []
        self._first_index: dict[str, int] = {}
        self._starts: dict[str, list[float]] = {}
        for source_id in source_order:
            self._first_index[source_id] = len(self.source_ids)
            source_windows = windows.get(source_id, ())
            self._starts[source_id] = [window.start_s for window in source_windows]
            for window in source_windows or (None,):
                self.source_ids.append(source_id)
                self.windows.append(window)

    def locate(self, source_id: str, in_s: object) -> tuple[int, float]:
        """Devolve o índice da entrada e o início da janela a subtrair dos tempos do recorte."""

        first = self._first_index[source_id]
        starts = self._starts[source_id]
        if not starts:
            return first, 0.0
        position = bisect_right(starts, float(in_s) + 0.000001) - 1
        if position < 0:
            raise ValueError("recorte fora das janelas de decode da fonte")
        return first + position, starts[position]


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
    """Transforma o quadro normalizado e retorna o grafo com canvas preto do tamanho da saída."""

    start = motion["from"]
    end = motion["to"]
    focal_x = _linear_motion_expression(start["x"], end["x"], segment_duration)
    focal_y = _linear_motion_expression(start["y"], end["y"], segment_duration)
    zoom = _linear_motion_expression(start["zoom"], end["zoom"], segment_duration)
    rate = _number(frame_rate if frame_rate and frame_rate > 0 else 25.0)
    scaled_width = f"trunc(iw*{zoom}/2)*2"
    scaled_height = f"trunc(ih*{zoom}/2)*2"
    scaled_label = f"m{segment_index}s"
    background_label = f"m{segment_index}b"
    output_label = f"m{segment_index}p"
    # A imagem escalada é posicionada em (W-w)*fx dentro do próprio canvas de saída; o overlay
    # recorta o que excede (zoom > 1) e preenche com preto o que falta (zoom < 1). Antes o quadro
    # era composto num canvas preto 4x maior e recortado depois, o que custava ~16x mais por frame
    # para chegar à mesma posição.
    # ponytail: `scale eval=frame` recria o scaler a cada frame (~8 ms/frame em 1080p, o maior custo
    # do render com movimento), trocar por um caminho sem reinicialização se o zoom animado dominar o tempo.
    graph = ";".join(
        [
            f"[{input_label}]scale=w={scaled_width}:h={scaled_height}:eval=frame[{scaled_label}]",
            (
                f"color=c=black:s={canvas_width}x{canvas_height}:r={rate}:"
                f"d={_number(segment_duration)}[{background_label}]"
            ),
            (
                f"[{background_label}][{scaled_label}]overlay="
                f"x=(W-w)*{focal_x}:y=(H-h)*{focal_y}:eval=frame:shortest=1"
                f"[{output_label}]"
            ),
        ]
    )
    return graph, output_label


def _video_segment_filter(
    segment: Mapping[str, Any],
    inputs: _GraphInputs,
    segment_index: int,
    canvas_width: int,
    canvas_height: int,
    frame_rate: float | None,
) -> str:
    """Corta um vídeo e normaliza sua imagem para o canvas do perfil (qualquer proporção).

    `crop` amplia até cobrir o canvas e recorta o centro (sem barras); `preserve` e `contain`
    cabem inteiros e preenchem o resto com preto. O canvas já chega na proporção do perfil,
    então este filtro não depende de ela ser 16:9.
    O `trim` usa os tempos do plano menos o início da janela de decode da entrada.
    """

    video = segment["video"]
    input_index, offset = inputs.locate(str(video["source_id"]), video["in_s"])
    start = _number(float(video["in_s"]) - offset)
    end = _number(float(video["out_s"]) - offset)
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
    inputs: _GraphInputs,
    segment_index: int,
    track_index: int,
    required_duration: float,
) -> str:
    """Corta, repete quando solicitado, aplica ganho e cobre a janela global do grupo.

    O `atrim` usa os tempos do plano menos o início da janela de decode da entrada.
    """

    input_index, offset = inputs.locate(str(track["source_id"]), track["in_s"])
    start = _number(float(track["in_s"]) - offset)
    end = _number(float(track["out_s"]) - offset)
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
    inputs: _GraphInputs,
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
                inputs,
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
    font_file: str,
) -> tuple[str, str]:
    """Desenha texto com o arquivo de fonte validado e o sobrepõe no intervalo declarado."""

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
    escaped_font_file = _escape_filter_value(font_file)
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
        f"drawtext=fontfile='{escaped_font_file}':fontsize={int(layer['size_px'])}:"
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
    inputs: _GraphInputs,
    canvas_width: int,
    canvas_height: int,
    timeline_duration: float,
) -> tuple[str, str]:
    """Dimensiona imagem ou trecho de vídeo e o sobrepõe sem mapear áudio da camada.

    O recorte da camada de vídeo é deslocado pelo início da janela de decode; imagem
    nunca tem janela.
    """

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
        input_index, offset = inputs.locate(str(layer["source_id"]), layer["in_s"])
        # O PTS global posiciona o primeiro frame do recorte na abertura da camada.
        source = (
            f"trim=start={_number(float(layer['in_s']) - offset)}:"
            f"end={_number(float(layer['out_s']) - offset)},"
            f"setpts=PTS-STARTPTS+{_number(layer['start_s'])}/TB"
        )
    else:
        input_index, _ = inputs.locate(str(layer["source_id"]), 0.0)
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
    windows: bool = False,
) -> FfmpegGraph:
    """Cria o grafo completo de vídeo, áudio, concatenação e processamento final.

    Com `windows=True`, cada fonte de vídeo que ganha com a decodificação seletiva passa
    a ter uma entrada por janela (`render/windows.py`); sem ganho o grafo é o mesmo de
    `windows=False`. Os tempos do plano nunca mudam, só os recortes do grafo.
    """

    source_order = _used_source_ids(document)
    source_records = {str(record["id"]): record for record in validation["sources"]}
    inputs = _GraphInputs(
        source_order,
        plan_decode_windows(document, source_records, source_order) if windows else {},
    )
    segments = list(document["segments"])
    timeline_segments = list(validation["timeline"]["segments"])
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
                inputs,
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
                    inputs,
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
    resolved_font_files = validation.get("_resolved_font_files", {})
    for layer_index, layer in enumerate(document["layers"]):
        if layer["type"] == "text":
            font_file = resolved_font_files.get(str(layer["font"]))
            if not isinstance(font_file, str):
                raise ValueError("arquivo de fonte não resolvido")
            filter_part, video_label = _text_layer_filter(
                video_label,
                layer,
                layer_index,
                canvas_width,
                canvas_height,
                font_file,
            )
            parts.append(filter_part)
        elif layer["type"] in {"image", "video"}:
            filter_part, video_label = _media_layer_filter(
                video_label, layer, layer_index,
                inputs,
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
        input_source_ids=tuple(inputs.source_ids),
        input_windows=tuple(inputs.windows),
        has_audio=has_audio,
    )
