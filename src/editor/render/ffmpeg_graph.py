"""Construção determinística do grafo de filtros usado pelo render de preview."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import textwrap
from typing import Any

from .windows import DecodeWindow, plan_decode_windows


MIN_LIMITER_DBFS = 20 * math.log10(0.0625)
"""Menor teto representável diretamente pelo filtro `alimiter` do FFmpeg."""

SHADOW_SIGMA_PER_BLUR_PX = 0.5
"""Sigma do `gblur` por pixel de `blur_px`: `blur_px` é o raio de desfoque no sentido do CSS
(`box-shadow`), cujo desvio padrão gaussiano é metade dele."""


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
        self._seeks: dict[str, list[float]] = {}
        for source_id in source_order:
            self._first_index[source_id] = len(self.source_ids)
            source_windows = windows.get(source_id, ())
            self._starts[source_id] = [window.start_s for window in source_windows]
            self._seeks[source_id] = [window.seek_s for window in source_windows]
            for window in source_windows or (None,):
                self.source_ids.append(source_id)
                self.windows.append(window)

    def locate(self, source_id: str, in_s: object) -> tuple[int, float]:
        """Devolve o índice da entrada e o instante de seek da janela a subtrair dos tempos do recorte.

        A janela é escolhida pelo primeiro uso (`start_s`), mas o deslocamento é o `seek_s` do
        `-ss` da entrada, que recua o pre-roll de áudio antes dele (ver `render/windows.py`).
        """

        first = self._first_index[source_id]
        starts = self._starts[source_id]
        if not starts:
            return first, 0.0
        position = bisect_right(starts, float(in_s) + 0.000001) - 1
        if position < 0:
            raise ValueError("recorte fora das janelas de decode da fonte")
        return first + position, self._seeks[source_id][position]


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
            # Há dois parsers: o grafo deve entregar \\' ao parser de opções do filtro.
            # Uma única barra faria a aspa engolir `expansion=none` como parte do texto.
            escaped.append("'\\\\\\''")
        elif character in {"\\", ":", ",", ";", "[", "]"}:
            escaped.append(f"\\{character}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _layer_text(value: str, *, wrap: bool, size_px: int, box_width: int) -> str:
    """Quebra linhas pela largura da caixa, preservando quebras explícitas.

    `size_px` e `box_width` são sempre os da full (referência do plano, decisão 11 da phase 5):
    a preview escala só o corpo da fonte (`_scaled_font_size`) e quebra nos mesmos pontos da full.
    """

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


def _scaled_overlay_graph(
    input_label: str,
    *,
    scale_options: str,
    overlay_x: str,
    overlay_y: str,
    segment_index: int,
    segment_duration: float,
    frame_rate: float | None,
    canvas_width: int,
    canvas_height: int,
) -> tuple[str, str]:
    """Escala o quadro e o posiciona num canvas preto do tamanho da saída (base de `motion` e `reframe`).

    O overlay recorta o que excede o canvas e preenche com preto o que falta. Ele avalia `x`/`y`
    a cada quadro com o tamanho do quadro escalado (`w`/`h`) e o tempo `t` do canvas, que nasce em
    zero no início do segmento, então a posição acompanha um `scale` de tamanho variável. Os rótulos
    `m{segmento}s|b|p` são exclusivos do segmento, que nunca tem `motion` e `reframe` juntos.
    """

    rate = _number(frame_rate if frame_rate and frame_rate > 0 else 25.0)
    scaled_label = f"m{segment_index}s"
    background_label = f"m{segment_index}b"
    output_label = f"m{segment_index}p"
    graph = ";".join(
        [
            f"[{input_label}]scale={scale_options}[{scaled_label}]",
            (
                f"color=c=black:s={canvas_width}x{canvas_height}:r={rate}:"
                f"d={_number(segment_duration)}[{background_label}]"
            ),
            (
                f"[{background_label}][{scaled_label}]overlay="
                f"x={overlay_x}:y={overlay_y}:eval=frame:shortest=1"
                f"[{output_label}]"
            ),
        ]
    )
    return graph, output_label


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
    scaled_width = f"trunc(iw*{zoom}/2)*2"
    scaled_height = f"trunc(ih*{zoom}/2)*2"
    # A imagem escalada é posicionada em (W-w)*fx dentro do próprio canvas de saída; o overlay
    # recorta o que excede (zoom > 1) e preenche com preto o que falta (zoom < 1). Antes o quadro
    # era composto num canvas preto 4x maior e recortado depois, o que custava ~16x mais por frame
    # para chegar à mesma posição.
    # ponytail: `scale eval=frame` recria o scaler a cada frame (~8 ms/frame em 1080p, o maior custo
    # do render com movimento), trocar por um caminho sem reinicialização se o zoom animado dominar o tempo.
    return _scaled_overlay_graph(
        input_label,
        scale_options=f"w={scaled_width}:h={scaled_height}:eval=frame",
        overlay_x=f"(W-w)*{focal_x}",
        overlay_y=f"(H-h)*{focal_y}",
        segment_index=segment_index,
        segment_duration=segment_duration,
        frame_rate=frame_rate,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )


def _reframe_position_statements(keyframes: Sequence[Mapping[str, Any]]) -> list[str]:
    """Instruções FFmpeg que deixam na memória 0 a posição `s(t)` no espaço dos keyframes.

    `s` vale `i` no keyframe `i` e `i + e(p)` no trecho entre `i` e `i+1`, onde `p` é o progresso
    de 0 a 1 do trecho e `e` é a curva do `interpolation` do keyframe inicial (`ease` é
    `3p² - 2p³`, `linear` é `p`). Cada eixo vira uma soma plana de `delta * clip(s - j, 0, 1)`
    (`_reframe_axis`), então `s` é calculado uma vez por expressão e a profundidade do parser
    nunca cresce com o número de keyframes. Antes do primeiro e depois do último keyframe `s`
    satura em `0` e `m - 1`, o que mantém o estado do primeiro e do último.

    `st(0,u)` soma o progresso linear de cada trecho (`u = i + p`); com algum `ease`, a parte
    inteira `i` e a fração `p` vêm de `floor(u)`, e a fração é remodelada na memória 2. Com todos
    `ease` o remodelado é `p²(3-2p)`; com mistura, `p + w*p(1-p)(2p-1)`, em que `w` é 1 nos trechos
    `ease` (escolhendo o menor dos dois conjuntos de índices para o texto ficar curto). A forma
    contínua em `u` inteiro (a curva vale 0 e 1 nas pontas) torna o resultado exato mesmo quando o
    arredondamento deixa `u` um ulp abaixo do inteiro.
    """

    spans = [
        f"clip((t-{_number(first['t_s'])})/{_number(second['t_s'] - first['t_s'])},0,1)"
        for first, second in zip(keyframes, keyframes[1:])
    ]
    statements = [f"st(0,{'+'.join(spans)})"]
    eased = [index for index, item in enumerate(keyframes[:-1]) if item["interpolation"] == "ease"]
    if not eased:
        return statements
    statements.extend(["st(1,floor(ld(0)))", "st(2,ld(0)-ld(1))"])
    if len(eased) == len(spans):
        shaped = "ld(2)*ld(2)*(3-2*ld(2))"
    else:
        linear = [index for index in range(len(spans)) if index not in eased]
        if len(eased) <= len(linear):
            weight = "+".join(f"eq(ld(1),{index})" for index in eased)
        else:
            weight = "1-(" + "+".join(f"eq(ld(1),{index})" for index in linear) + ")"
        shaped = f"ld(2)+({weight})*ld(2)*(1-ld(2))*(2*ld(2)-1)"
    statements.append(f"st(0,ld(1)+{shaped})")
    return statements


def _reframe_axis(keyframes: Sequence[Mapping[str, Any]], axis: str) -> tuple[str, bool]:
    """Expressão de um eixo (`x`, `y` ou `zoom`) em função de `s` e se ele varia ao longo do segmento.

    O valor é o do primeiro keyframe mais, para cada trecho com variação, `delta * clip(s - j, 0, 1)`;
    trechos sem variação não geram termo, e um eixo que não varia é só uma constante e dispensa `s`.
    Os números passam por `_number`, o único formatador de valores do plano no grafo.
    """

    values = [item[axis] for item in keyframes]
    expression = _number(values[0])
    animated = False
    for index, (first, second) in enumerate(zip(values, values[1:])):
        delta = second - first
        if delta == 0:
            continue
        animated = True
        position = "ld(0)" if index == 0 else f"ld(0)-{index}"
        sign = "+" if delta > 0 else "-"
        expression += f"{sign}{_number(abs(delta))}*clip({position},0,1)"
    return expression, animated


def _reframe_filter_graph(
    input_label: str,
    keyframes: Sequence[Mapping[str, Any]],
    *,
    segment_index: int,
    segment_duration: float,
    frame_rate: float | None,
    canvas_width: int,
    canvas_height: int,
) -> tuple[str, str]:
    """Recorta o quadro original na proporção do canvas conforme os keyframes de `reframe`.

    O quadro inteiro é escalado para que o recorte do `zoom` do instante preencha exatamente a
    largura do canvas, e o canvas é posicionado sobre ele com o ponto `(x, y)` no centro, encostado
    na borda quando o centro levaria o recorte para fora (nunca aparece preto, pois o quadro
    escalado nunca é menor que o canvas). Escalar primeiro e posicionar depois dá precisão de pixel
    da saída, e não da fonte, e as expressões só usam `iw`/`ih`/`w`/`h` e coordenadas normalizadas,
    então valem igualmente para o original e para o proxy reduzido da preview.

    - Maior recorte da proporção do canvas que cabe na fonte (zoom 1.0): `min(iw, ih*cw/ch)` de
      largura. Fator de escala `cw*zoom/essa largura`; a altura vem de `h=-2` (par, mais próxima).
    - A largura escalada usa `ceil(x/2 - 0.001)*2`: nunca fica abaixo do canvas por arredondamento
      (o que abriria uma faixa preta) e ignora o ruído de ponto flutuante de um valor já par.
    - `t` é o tempo desde o início do segmento (após `trim`/`setpts`), igual com e sem janela de decode.
    - Se o zoom é o mesmo em todos os keyframes o `scale` é estático; só o zoom animado paga o
      `eval=frame`. Posição em `x`/`y` sempre avalia por quadro no overlay.
    - Cada expressão vai entre aspas simples (o texto só tem números do `_number` e funções do
      FFmpeg), o que dispensa escapar cada vírgula e `;` e mantém o grafo curto.
    """

    # ponytail: o quadro inteiro é escalado, então zoom alto em fonte grande cria quadros enormes (zoom 4
    # em 4K para 9:16 passa de 13 mil pixels de largura) e `eval=frame` recria o scaler a cada quadro;
    # pré-recortar a fonte na região usada quando o zoom animado ou alto dominar o tempo de render.
    # ponytail: o overlay em yuv420p alinha a posição a pixel par da saída, erro de até 1 pixel; usar
    # overlay yuv444 se um acompanhamento sub-pixel for necessário.
    x_axis, x_animated = _reframe_axis(keyframes, "x")
    y_axis, y_animated = _reframe_axis(keyframes, "y")
    zoom_axis, zoom_animated = _reframe_axis(keyframes, "zoom")
    # Sem eixo animado (foco estático ou keyframes iguais) não há `s(t)` a calcular.
    positioned = (
        ";".join(_reframe_position_statements(keyframes)) + ";"
        if x_animated or y_animated or zoom_animated
        else ""
    )

    def expression(body: str, animated: bool) -> str:
        """Prefixa as instruções de `s(t)` só nas expressões cujo eixo varia."""

        return f"'{positioned if animated else ''}{body}'"

    width = _number(canvas_width)
    height = _number(canvas_height)
    scaled_width = expression(
        f"2*ceil(iw*{width}*({zoom_axis})/min(iw,ih*{width}/{height})/2-0.001)", zoom_animated
    )
    scale_options = f"w={scaled_width}:h=-2" + (":eval=frame" if zoom_animated else "")
    return _scaled_overlay_graph(
        input_label,
        scale_options=scale_options,
        overlay_x=expression(f"max(min(W/2-({x_axis})*w,0),W-w)", x_animated),
        overlay_y=expression(f"max(min(H/2-({y_axis})*h,0),H-h)", y_animated),
        segment_index=segment_index,
        segment_duration=segment_duration,
        frame_rate=frame_rate,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )


def _video_segment_filter(
    segment: Mapping[str, Any],
    inputs: _GraphInputs,
    segment_index: int,
    canvas_width: int,
    canvas_height: int,
    frame_rate: float | None,
    reframe: Mapping[str, Any] | None = None,
) -> str:
    """Corta um vídeo e normaliza sua imagem para o canvas do perfil (qualquer proporção).

    `crop` amplia até cobrir o canvas e recorta o centro (sem barras); `preserve` e `contain`
    cabem inteiros e preenchem o resto com preto. O canvas já chega na proporção do perfil,
    então este filtro não depende de ela ser 16:9.
    Com `reframe` (já normalizado pela validação, só existe com `crop`) o recorte central é
    trocado pelo recorte animado por keyframes sobre a fonte original (`_reframe_filter_graph`).
    O `trim` usa os tempos do plano menos o início da janela de decode da entrada.
    Todo ramo entrega pixel quadrado (SAR 1:1) ao `concat`: `reframe` e `motion` por nascerem do
    `overlay` sobre `color`, e `crop`, `contain` e `preserve` pelo `setsar=1` que fecha o ramo.

    `[i:v:0]` é o vídeo real da entrada (invariante em `media._validate_probe_payload`: a miniatura
    anexada nunca é o primeiro stream de vídeo e as trilhas de dados não contam como vídeo). O
    `scale`, o `pad` e o recorte rodam na profundidade da fonte, e o `format=yuv420p` que fecha todo
    ramo converte uma fonte HEVC de 10 bits para os 8 bits da saída (numa fonte de 8 bits ele não
    muda nada), então o grafo é o mesmo para qualquer fonte e não depende do codec.
    ponytail: fonte HDR (HLG, PQ, BT.2020) sai sem tone mapping e aparece lavada, adicionar tone
    mapping quando uma fonte HDR real precisar sair fiel.
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
    if reframe is not None:
        base_label = f"v{segment_index}base"
        reframe_graph, reframe_label = _reframe_filter_graph(
            base_label,
            reframe["keyframes"],
            segment_index=segment_index,
            segment_duration=float(video["out_s"]) - float(video["in_s"]),
            frame_rate=frame_rate,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
        return (
            f"[{input_index}:v:0]{','.join(filters)}[{base_label}];"
            f"{reframe_graph};[{reframe_label}]format=yuv420p[v{segment_index}]"
        )
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
    # Pixel quadrado no FIM do ramo: o `scale` com razão inexata (1080p para 1080x1920, 1000x562 para
    # 992x558...) regrava o SAR para preservar a proporção, e o `setsar=1` do início do filtro já ficou
    # para trás. O `concat` recusa segmentos de SAR diferentes (o ramo `reframe` e o `motion` saem do
    # `overlay` sobre `color`, SAR 1:1) e o MP4 sairia declarando SAR como 10240:10239. Só altera o
    # metadado: o conteúdo dos quadros é o mesmo e, onde o SAR já era 1:1, o filtro não muda nada.
    filters.append("setsar=1")
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

    O `atrim` usa os tempos do plano menos o seek da janela de decode da entrada. O
    `asetpts=PTS-STARTPTS` zera o primeiro quadro entregue, então só preserva o alinhamento
    porque a janela recua o pre-roll de áudio (`render/windows.py`) e o áudio do recorte
    sempre chega inteiro; se ele faltasse, a trilha sairia adiantada em vez de acusar a lacuna.
    `[i:a:0]` é o único áudio da fonte (invariante de `media._validate_probe_payload`: no máximo um
    stream de áudio, e trilhas de dados e miniatura anexada não contam como áudio).
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


def _layer_span(start: object, size: object, extent: int) -> tuple[int, int]:
    """Converte posição e tamanho normalizados (0 a 1) da camada em pixels `(início, fim)` de um eixo.

    Mesma regra de arredondamento do canvas inteiro: início truncado e dentro do quadro, fim
    arredondado para cima e limitado ao quadro. Serve a qualquer largura de referência, o que
    permite calcular a caixa da full mesmo quando o grafo é o da preview.
    """

    first = min(extent - 1, int(float(start) * extent))
    last = min(extent, math.ceil((float(start) + float(size)) * extent))
    return first, last


def _scaled_font_size(size_px: int, canvas_width: int, reference_width: int) -> int:
    """Corpo da fonte no canvas renderizado: `size_px` vale na largura da full (decisão 11 da phase 5).

    Na full (`canvas_width == reference_width`) devolve `size_px` sem alteração; na preview devolve
    `size_px × canvas_width / reference_width` arredondado (meio para cima, em inteiros, sem erro
    de ponto flutuante) e com mínimo de 1.
    """

    # ponytail: corpo arredondado a pixel inteiro (mínimo 1) e dica de glifos em fonte pequena variam
    # a largura da preview em poucos por cento da full, usar `fontsize` fracionário se isso incomodar.
    return max(1, (2 * size_px * canvas_width + reference_width) // (2 * reference_width))


def _scaled_style_pixels(value: int, canvas_width: int, reference_width: int) -> int:
    """Escala um valor de estilo em pixels (`shadow`, `outline`) da largura da full para o canvas.

    Mesma regra do corpo da fonte (`_scaled_font_size`, decisão 18 da phase 5): na full devolve o
    valor declarado; na preview, o valor × razão das larguras arredondado, com magnitude mínima de 1
    para qualquer valor diferente de zero. O sinal é preservado (deslocamentos podem ser negativos)
    e o zero continua zero (deslocamento nulo num eixo).
    """

    if value == 0:
        return 0
    scaled = _scaled_font_size(abs(value), canvas_width, reference_width)
    return scaled if value > 0 else -scaled


def _signed_offset(base: str, delta: int) -> str:
    """Soma um deslocamento inteiro a uma expressão de posição do `drawtext`, omitindo o zero."""

    return base if delta == 0 else f"{base}{delta:+d}"


def _drawtext_filter(
    font_file: str,
    font_size: int,
    color: str,
    text: str,
    align: str,
    x: str,
    y: str,
    outline: tuple[int, str] | None,
) -> str:
    """Monta um `drawtext` do texto da camada, com contorno (`borderw`/`bordercolor`) opcional.

    Serve ao texto nítido e à cópia da sombra, que diferem só na cor, na posição e no contorno.
    Sem contorno o resultado é o `drawtext` de antes do estilo, byte a byte. As cores só chegam aqui
    depois de casar `HEX_COLOR_PATTERN` e `outline` traz a largura já escalada, inteira e validada.
    """

    border = f"borderw={outline[0]}:bordercolor={outline[1]}:" if outline else ""
    return (
        f"drawtext=fontfile='{_escape_filter_value(font_file)}':fontsize={font_size}:"
        f"fontcolor={color}:{border}text='{_escape_filter_value(text)}':expansion=none:"
        f"text_align={align}:x={x}:y={y}"
    )


def _text_layer_filter(
    input_label: str,
    layer: Mapping[str, Any],
    layer_index: int,
    canvas_width: int,
    canvas_height: int,
    font_file: str,
    reference_width: int,
    style: Mapping[str, Any] | None = None,
    *,
    anchor_bottom: bool = False,
) -> tuple[str, str]:
    """Desenha texto com o arquivo de fonte validado e o sobrepõe no intervalo declarado.

    A caixa e o `drawtext` usam o canvas renderizado, mas `size_px` é definido na largura da full
    (`reference_width`): o corpo da fonte é escalado por `_scaled_font_size` e a quebra de linha
    (`wrap`) é calculada com o `size_px` e a largura de caixa da full. Assim preview e full quebram
    nos mesmos pontos e o texto ocupa a mesma fração do quadro; com `reference_width` igual ao
    canvas (full) o filtro é idêntico ao anterior a esta escala.

    `style` é o `shadow`/`outline` já validado e normalizado da camada (`validation["_text_styles"]`).
    O contorno é o `borderw` do `drawtext`. A sombra é uma segunda superfície RGBA da caixa com a
    cópia do texto (e do contorno) na cor da sombra, deslocada, desfocada por `gblur` e com o alfa
    multiplicado pela opacidade, composta por `overlay` atrás do texto nítido. O rótulo de saída da
    superfície (`textlayerN`) é o mesmo com ou sem estilo, então o resto do grafo não muda; sem
    `style` o filtro é idêntico ao anterior ao estilo. Os valores em pixels do estilo são os da
    largura da full e a preview os escala como o `size_px` (`_scaled_style_pixels`).

    `anchor_bottom` serve às legendas: o bloco inteiro termina na base da caixa, mesmo
    quando a quantidade de linhas muda. Camadas mantêm a posição original no topo.

    ponytail: sombra e contorno são recortados pela caixa da camada (a superfície tem o tamanho da
    caixa e o texto parte do canto superior esquerdo), então o contorno da primeira linha encosta
    no topo da caixa; dar folga à caixa, ou compor numa superfície maior que a caixa se isso
    incomodar. A sombra é desfocada a cada quadro do intervalo da camada (cerca de 30 ms de CPU por
    quadro numa caixa de 2473x212), congelar um quadro se muitas camadas sombreadas e longas pesarem.
    """

    left, right = _layer_span(layer["x"], layer["width"], canvas_width)
    top, bottom = _layer_span(layer["y"], layer["height"], canvas_height)
    box_width = max(1, right - left)
    box_height = max(1, bottom - top)
    reference_left, reference_right = _layer_span(layer["x"], layer["width"], reference_width)
    text = _layer_text(
        str(layer["text"]),
        wrap=bool(layer["wrap"]),
        size_px=int(layer["size_px"]),
        box_width=max(1, reference_right - reference_left),
    )
    font_size = _scaled_font_size(int(layer["size_px"]), canvas_width, reference_width)
    start = _number(layer["start_s"])
    end = _number(layer["end_s"])
    align = str(layer["align"])
    align_x = {"left": "0", "center": "(w-text_w)/2", "right": "w-text_w"}[align]
    align_y = "h-text_h" if anchor_bottom else "0"
    layer_label = f"textlayer{layer_index}"
    output_label = f"vtext{layer_index}"
    style = style or {}

    def scaled(value: int) -> int:
        """Valor de estilo em pixels da full convertido para o canvas renderizado."""

        return _scaled_style_pixels(value, canvas_width, reference_width)

    outline = None
    if "outline" in style:
        outline = (scaled(int(style["outline"]["width_px"])), str(style["outline"]["color"]))
    # Superfície do texto nítido: `color` transparente do tamanho da caixa e o `drawtext` do topo.
    face = (
        f"color=c=black@0:s={box_width}x{box_height}:r=25:d={end},format=rgba,"
        + _drawtext_filter(font_file, font_size, str(layer["color"]), text, align, align_x, align_y, outline)
    )
    if "shadow" in style:
        shadow = style["shadow"]
        shadow_color = str(shadow["color"])
        shadow_outline = (outline[0], shadow_color) if outline else None
        # A superfície nasce na cor da sombra (alfa 0) para o desfoque não escurecer a cor nas
        # bordas, e o texto é desenhado opaco: a opacidade entra uma única vez, no alfa, depois do
        # desfoque (com alfa no `drawtext` o miolo da letra e o contorno se somariam).
        shadow_surface = (
            f"color=c={shadow_color}@0:s={box_width}x{box_height}:r=25:d={end},format=rgba,"
            + _drawtext_filter(
                font_file, font_size, shadow_color, text, align,
                _signed_offset(align_x, scaled(int(shadow["offset_x_px"]))),
                _signed_offset(align_y, scaled(int(shadow["offset_y_px"]))),
                shadow_outline,
            )
        )
        # A superfície nasce em t = 0 e o desfoque é o passo caro (~30 ms de CPU por quadro numa caixa
        # de 2473x212): `trim` antes do `gblur` o limita ao intervalo da camada. O `trim` mantém o
        # quadro que contém `start_s`, então um início fora da grade de 25 fps não perde o primeiro
        # quadro visível (provado em `tests/test_text_style.py`).
        shadow_surface += f",trim=start={start}"
        blur = scaled(int(shadow["blur_px"]))
        if blur > 0:
            shadow_surface += f",gblur=sigma={_number(blur * SHADOW_SIGMA_PER_BLUR_PX)}"
        shadow_surface += f",colorchannelmixer=aa={_number(shadow['opacity'])}"
        shadow_label = f"textshadow{layer_index}"
        face_label = f"textface{layer_index}"
        surface = (
            f"{shadow_surface}[{shadow_label}];{face}[{face_label}];"
            f"[{shadow_label}][{face_label}]overlay=format=auto[{layer_label}]"
        )
    else:
        surface = f"{face}[{layer_label}]"
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
        # `[i:v:0]` abaixo é o vídeo real da fonte, pela mesma invariante de `_video_segment_filter`
        # (a fonte HEVC de 10 bits vira RGBA de 8 bits em `format=rgba`).
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

    O `size_px` do texto é definido na largura da full (decisão 11 da phase 5), lida de
    `validation["profile"]["full_width"]`, a mesma fonte do canvas que `render/full.py` usa. A
    full tem `canvas_width` igual a ela e renderiza o valor declarado; a preview, de canvas
    menor, escala o corpo da fonte pela razão das larguras. O mesmo vale para os pixels de
    `shadow` e `outline` das camadas de texto, lidos já normalizados de `validation["_text_styles"]`.
    """

    text_reference_width = int(validation["profile"]["full_width"])
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
                timeline_segment.get("reframe"),
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
    text_styles = validation.get("_text_styles", {})
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
                text_reference_width,
                text_styles.get(layer_index),
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

    # Legendas não leem mídia: reaproveitam a superfície de texto, depois de todas as camadas.
    # Índices posteriores aos das camadas evitam colisão dos labels; a ordem dos itens é editorial.
    captions = validation.get("_captions")
    if captions is not None:
        style = captions["style"]
        font_file = resolved_font_files.get(str(style["font"]))
        if not isinstance(font_file, str):
            raise ValueError("arquivo de fonte não resolvido")
        ordered = sorted(captions["occurrences"], key=lambda occurrence: occurrence["item"])
        for caption_index, occurrence in enumerate(ordered, start=len(document["layers"])):
            layer = {**style, **occurrence, "text": captions["items"][occurrence["item"] - 1]["text"]}
            filter_part, video_label = _text_layer_filter(
                video_label, layer, caption_index, canvas_width, canvas_height,
                font_file, text_reference_width, style, anchor_bottom=True,
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
