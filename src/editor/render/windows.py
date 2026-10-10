"""Planejador puro das janelas de decode: quais trechos de cada fonte de vídeo o FFmpeg lê.

Cada janela vira uma entrada própria do FFmpeg com `-ss`/`-t`, e o grafo de
`ffmpeg_graph.py` desloca seus recortes pelo início da janela. O planejador só
decide intervalos; não toca em tempos do plano, hash canônico ou linha de comando.
Regras (spec da phase 4, decisões 3 a 6): união dos intervalos de segmentos,
camadas de vídeo e trilhas de áudio; lacuna menor que 2 s é mesclada; fim com
folga de 0,5 s; teto de 8 janelas por fonte e 24 entradas de vídeo no total;
só janela fonte `video` e só quando a leitura estimada cai pelo menos 20%.

O seek de entrada do FFmpeg (`-ss` antes do `-i`) decodifica a partir do keyframe
anterior e descarta o excedente, então quadros e amostras saem iguais aos da leitura
completa; só muda o tamanho dos blocos de áudio entregues ao grafo, que o encoder
reagrupa.

Pre-roll de áudio (T6 da phase 5): o seek não entrega o áudio de graça. O demuxer MP4 começa
o áudio no pacote que contém o instante do keyframe de vídeo e o decoder AAC descarta o
primeiro quadro (1.024 amostras) decodificado depois do seek (medido com `ffprobe -show_frames`
e `-read_intervals`, sem o recorte do CLI), então o áudio útil só começa até 1.024 amostras
depois do keyframe. Com B-frames o keyframe tem `dts` anterior ao `pts` e a sobra cobre o corte;
sem B-frames o keyframe cai no próprio corte e o início do áudio some, e o `asetpts=PTS-STARTPTS`
do grafo esconde a lacuna adiantando a trilha. Por isso o `-ss` de cada janela recua
`AUDIO_PREROLL_S` antes do primeiro uso (`DecodeWindow.seek_s`), e o grafo e o comando derivam o
seek dessa mesma propriedade.
ponytail: AAC codificado com PNS (substituição perceptual de ruído) sai diferente após
qualquer seek, pois o ruído depende do estado acumulado do decoder; aceitar ou ler o
áudio dessas fontes sem janela se uma fonte real exigir igualdade bit a bit.

Janela de tempo (T1 da phase 7): o render ao vivo começa a montagem num instante T e não pode
processar o trecho anterior. `TimeWindow` diz, para cada item da timeline (segmento, janela de
áudio, camada de vídeo), quanto do começo dele já passou de T (`skip`); o planejador cobre só o
que alimenta `[T, fim]` e o grafo (`ffmpeg_graph.py`) avança o `in_s` do item pelo mesmo `skip`,
então os dois sempre concordam sobre o que cada janela precisa conter. Sem janela de tempo (ou
com T igual a 0) os recortes não mudam. Só o ao vivo preserva também o seek do primeiro uso
temporal de uma fonte reutilizada fora de ordem; preview/full mantêm a política de união.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


MERGE_GAP_S = 2.0
"""Lacunas menores que isto são lidas de uma vez: reabrir a fonte custaria mais que decodificar a lacuna."""

END_SLACK_S = 0.5
"""Folga no fim da janela para que `-t` nunca corte o último quadro ou amostra usados."""

MAX_WINDOWS_PER_SOURCE = 8
"""Teto de entradas por fonte, que limita decoders abertos e o tamanho da linha de comando."""

MAX_VIDEO_INPUTS = 24
"""Teto global de entradas de vídeo do comando, sempre com ao menos uma entrada por fonte."""

MIN_READ_REDUCTION = 0.2
"""Redução mínima da leitura estimada para justificar janelar uma fonte."""

AUDIO_PREROLL_S = 0.25
"""Recuo do seek antes do primeiro uso da janela, que absorve o descarte do decoder AAC após o seek.

O descarte e a posição de entrada do demuxer somam no máximo 1.024 amostras por quadro AAC
(21 ms a 48 kHz); 0,25 s cobre também taxas baixas e HE-AAC, que tem quadros de 2.048 amostras.
ponytail: estimativa de ganho e teto de leitura ignoram esse recuo (no máximo 0,25 s por janela);
somar o recuo à estimativa se o limiar de 20% passar a decidir casos de fronteira reais.
"""


@dataclass(frozen=True)
class DecodeWindow:
    """Intervalo contínuo de uma fonte, lido por uma entrada `-ss seek_s -t read_duration_s`.

    `start_s` e `end_s` são o primeiro e o último instante realmente usados pela timeline (mais a
    folga final); o seek começa `AUDIO_PREROLL_S` antes de `start_s`.
    """

    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        """Duração do intervalo usado, base da estimativa de ganho do planejador."""

        return self.end_s - self.start_s

    @property
    def seek_s(self) -> float:
        """Instante de `-ss`, ou 0 quando a janela começa no início da fonte e a entrada não faz seek.

        Fonte única do deslocamento: o comando de entrada e os recortes do grafo derivam daqui.
        O arredondamento a 6 casas é o mesmo da linha de comando, então `-ss` e deslocamento nunca
        diferem; sem seek a leitura é a mesma da fonte inteira, inclusive o início do áudio, que
        um `-ss 0` perderia em fontes sem B-frames.
        """

        return max(0.0, round(self.start_s - AUDIO_PREROLL_S, 6))

    @property
    def read_duration_s(self) -> float:
        """Duração passada a `-t`, contada a partir de `seek_s`, para nunca divergir do intervalo."""

        return self.end_s - self.seek_s


SKIP_TOLERANCE_S = 0.000001
"""Tolerância dos tempos da timeline (arredondados a 6 casas) ao decidir se um item já acabou ou já começou."""


@dataclass(frozen=True)
class TimeWindow:
    """Janela de tempo `[start_s, fim da timeline]` de um render ao vivo que começa em `start_s`.

    Fonte única do que sobra de cada item da timeline depois de T: o planejador de janelas de decode
    e o construtor do grafo perguntam aqui, nunca recalculam. `TimeWindow(0)` é a identidade: nenhum
    item é omitido ou avançado.
    """

    start_s: float

    @property
    def active(self) -> bool:
        """Verdadeiro quando há trecho anterior a pular; com T igual a 0 o render é o da full."""

        return self.start_s > SKIP_TOLERANCE_S

    def skip(self, item_start_s: float, item_end_s: float) -> float | None:
        """Quanto do começo do item `[item_start_s, item_end_s)` já passou de T, ou `None` se ele já acabou.

        Item que começa em T ou depois devolve 0; item que contém T devolve `T - início` (o avanço
        do `in_s`). Um fim a menos de 1 µs de T conta como acabado, e um início a menos de 1 µs de T
        como começando em T, para o ruído de arredondamento não criar avanço fantasma.
        """

        if not self.active:
            return 0.0
        if item_end_s <= self.start_s + SKIP_TOLERANCE_S:
            return None
        delta = self.start_s - item_start_s
        return delta if delta > SKIP_TOLERANCE_S else 0.0


def track_in_s(track: Mapping[str, Any], skip_s: float) -> float | None:
    """`in_s` de uma trilha de áudio depois de avançar `skip_s` do início da janela de áudio do grupo.

    Trilha com `loop` não avança: a repetição nasce do recorte inteiro e o grafo corta o começo
    do resultado (`ffmpeg_graph._audio_track_filter`). `None` quando a trilha já acabou antes
    de T (o `in_s` avançado alcança o `out_s`), caso em que ela não contribui com nada.
    """

    in_s = float(track["in_s"])
    if bool(track.get("loop", False)):
        return in_s
    in_s += skip_s
    return in_s if in_s < float(track["out_s"]) - SKIP_TOLERANCE_S else None


def remaining_reads(
    document: Mapping[str, Any],
    timeline_segments: Sequence[Mapping[str, Any]] | None = None,
    window: TimeWindow | None = None,
) -> Iterator[tuple[str, float | None, float | None]]:
    """Lê cada leitura de fonte que a timeline faz, na ordem de primeira referência.

    Cada item é `(source_id, in_s, out_s)`: segmento de vídeo, trilha de áudio e camada de vídeo
    trazem o recorte; camada de imagem traz `None` nos dois (a imagem inteira, sem recorte). Sem
    `window` ativa são todas as leituras do plano. Com ela (render ao vivo), só as que alimentam
    `[window.start_s, fim]`: itens que já acabaram saem e os que contêm T entram a partir do `in_s`
    avançado (`TimeWindow.skip`), exatamente como o grafo os recorta. A janela exige
    `timeline_segments` (janelas de vídeo e de áudio de cada segmento, de `validate_plan`).
    """

    if window is None or not window.active:
        for segment in document["segments"]:
            for item in (segment["video"], *segment["audio"]["tracks"]):
                yield str(item["source_id"]), float(item["in_s"]), float(item["out_s"])
        for layer in document["layers"]:
            if layer["type"] == "video":
                yield str(layer["source_id"]), float(layer["in_s"]), float(layer["out_s"])
            elif layer["type"] == "image":
                yield str(layer["source_id"]), None, None
        return

    if timeline_segments is None:
        raise ValueError("a janela de tempo exige a timeline validada")
    for segment, timeline in zip(document["segments"], timeline_segments, strict=True):
        video = segment["video"]
        video_skip = window.skip(float(timeline["start_s"]), float(timeline["end_s"]))
        if video_skip is not None:
            yield str(video["source_id"]), float(video["in_s"]) + video_skip, float(video["out_s"])
        audio_skip = window.skip(float(timeline["audio_start_s"]), float(timeline["audio_end_s"]))
        if audio_skip is not None:
            for track in segment["audio"]["tracks"]:
                in_s = track_in_s(track, audio_skip)
                if in_s is not None:
                    yield str(track["source_id"]), in_s, float(track["out_s"])
    for layer in document["layers"]:
        if layer["type"] not in {"video", "image"}:
            continue
        layer_skip = window.skip(float(layer["start_s"]), float(layer["end_s"]))
        if layer_skip is None:
            continue
        if layer["type"] == "image":
            yield str(layer["source_id"]), None, None
        else:
            yield str(layer["source_id"]), float(layer["in_s"]) + layer_skip, float(layer["out_s"])


def source_intervals(
    document: Mapping[str, Any],
    timeline_segments: Sequence[Mapping[str, Any]] | None = None,
    window: TimeWindow | None = None,
) -> dict[str, list[tuple[float, float]]]:
    """Coleta os intervalos `in_s`/`out_s` de cada fonte em segmentos, trilhas e camadas de vídeo.

    Trilha com `loop` entra pelo seu recorte; a repetição acontece no filtro, não na leitura.
    Com `window` ativa só entra o que alimenta `[window.start_s, fim]` (ver `remaining_reads`).
    """

    intervals: dict[str, list[tuple[float, float]]] = {}
    for source_id, in_s, out_s in remaining_reads(document, timeline_segments, window):
        if in_s is not None and out_s is not None:
            intervals.setdefault(source_id, []).append((in_s, out_s))
    return intervals


def _merge_close(intervals: list[tuple[float, float]]) -> list[list[float]]:
    """Ordena e une intervalos sobrepostos, adjacentes ou separados por lacuna menor que 2 s."""

    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if merged and start - merged[-1][1] < MERGE_GAP_S:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _merge_smallest_gap(groups: Mapping[str, list[list[float]]], order: list[str]) -> str:
    """Une, no lugar, o par vizinho de menor lacuna entre as fontes dadas e devolve a fonte alterada.

    O desempate pela ordem de referência da fonte e pela posição mantém o resultado
    determinístico para o mesmo plano.
    """

    best: tuple[float, int, int] | None = None
    for source_position, source_id in enumerate(order):
        spans = groups[source_id]
        for index in range(len(spans) - 1):
            candidate = (spans[index + 1][0] - spans[index][1], source_position, index)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        raise ValueError("nenhuma lacuna disponível para mesclar")
    _, source_position, index = best
    spans = groups[order[source_position]]
    spans[index][1] = max(spans[index][1], spans[index + 1][1])
    del spans[index + 1]
    return order[source_position]


def _windows_with_gain(
    spans: list[list[float]], duration_s: float, min_reduction: float = MIN_READ_REDUCTION
) -> tuple[DecodeWindow, ...] | None:
    """Aplica a folga final e devolve as janelas só se a leitura cair pelo menos `min_reduction`.

    A leitura atual é estimada do início da fonte ao último `out_s`, que é o que o
    FFmpeg decodifica hoje com uma única entrada sem `-ss`.
    """

    windows = tuple(
        DecodeWindow(start_s=start, end_s=min(duration_s, end + END_SLACK_S))
        for start, end in spans
    )
    current_read = max(end for _, end in spans)
    windowed_read = sum(window.duration_s for window in windows)
    if current_read <= 0 or windowed_read > current_read * (1 - min_reduction):
        return None
    return windows


def plan_decode_windows(
    document: Mapping[str, Any],
    source_records: Mapping[str, Mapping[str, Any]],
    source_order: tuple[str, ...],
    timeline_segments: Sequence[Mapping[str, Any]] | None = None,
    window: TimeWindow | None = None,
) -> dict[str, tuple[DecodeWindow, ...]]:
    """Calcula as janelas por fonte de vídeo; fontes ausentes do retorno mantêm a entrada atual.

    `source_records` são os registros validados por id (com `kind` e `duration_seconds`)
    e `source_order` é a ordem estável das entradas do grafo. Retorna vazio quando nenhuma
    fonte ganha ou quando as fontes de vídeo, sozinhas, já excedem o teto global.

    Com `window` ativa (render ao vivo a partir de T) os intervalos são os de `[T, fim]` e qualquer
    redução de leitura justifica janelar (`min_reduction` 0): num render que começa no meio, o ganho
    mínimo de 20% deixaria uma fonte longa ser decodificada desde o início só para descartar o
    começo, e o ao vivo não pode crescer com a posição.
    A presença explícita de `window`, inclusive T=0, habilita a preservação do primeiro seek
    temporal quando a união contém usos anteriores na fonte; sem ela a política fica inalterada.
    """

    video_ids = [
        source_id
        for source_id in source_order
        if source_records[source_id].get("kind") == "video"
        and source_records[source_id].get("duration_seconds") is not None
    ]
    if len(video_ids) > MAX_VIDEO_INPUTS:
        # Nem uma entrada por fonte cabe no teto: mantém o comando atual sem janelas.
        return {}

    min_reduction = 0.0 if window is not None and window.active else MIN_READ_REDUCTION
    intervals = source_intervals(document, timeline_segments, window)
    groups: dict[str, list[list[float]]] = {}
    windows: dict[str, tuple[DecodeWindow, ...]] = {}

    def refresh(source_id: str) -> None:
        """Recalcula as janelas de uma fonte e a retira do conjunto quando o ganho some."""

        planned = _windows_with_gain(
            groups[source_id], float(source_records[source_id]["duration_seconds"]), min_reduction
        )
        if planned is None:
            windows.pop(source_id, None)
            groups.pop(source_id, None)
        else:
            windows[source_id] = planned

    for source_id in video_ids:
        # Sem janela de tempo toda fonte em `source_order` é referenciada por algum recorte; com ela,
        # uma fonte usada só antes de T não tem intervalo e segue com a entrada atual, sem janelas.
        if source_id not in intervals:
            continue
        groups[source_id] = _merge_close(intervals[source_id])
        while len(groups[source_id]) > MAX_WINDOWS_PER_SOURCE:
            _merge_smallest_gap(groups, [source_id])
        refresh(source_id)

    # Fontes sem janela ocupam exatamente uma entrada; o excedente é absorvido mesclando
    # a menor lacuna entre as fontes janeladas, e o ganho é reavaliado a cada mescla.
    def total_inputs() -> int:
        return sum(len(windows.get(source_id, (None,))) for source_id in video_ids)

    while total_inputs() > MAX_VIDEO_INPUTS:
        candidates = [source_id for source_id in video_ids if len(windows.get(source_id, ())) > 1]
        refresh(_merge_smallest_gap(groups, candidates))
    if window is not None:
        _preserve_live_first_reads(document, timeline_segments, window, source_records, video_ids, intervals, windows)
    return windows


def _preserve_live_first_reads(
    document: Mapping[str, Any],
    timeline_segments: Sequence[Mapping[str, Any]] | None,
    window: TimeWindow,
    source_records: Mapping[str, Mapping[str, Any]],
    video_ids: list[str],
    intervals: Mapping[str, list[tuple[float, float]]],
    windows: dict[str, tuple[DecodeWindow, ...]],
) -> None:
    """Preserva o seek do primeiro uso temporal sem mudar a união usada pela preview/full.

    Uma montagem pode abrir no fim da fonte e depois voltar ao começo. A união sozinha força
    o primeiro ramo a consumir esse prefixo. Uma entrada sobreposta, ordenada por `start_s`,
    permite ao `bisect_right` do grafo escolher o seek tardio. Ela cobre todos os recortes que
    podem passar a escolhê-la, inclusive os posteriores mais longos e o áudio com pre-roll.
    ponytail: sem folga nos tetos 8/24 mantém a união, reservar entradas ao primeiro uso se um
    trabalho real atingir esses tetos e ainda apresentar demora no início da reprodução.
    """

    if timeline_segments is None:
        raise ValueError("a política ao vivo exige a timeline validada")
    first: dict[str, tuple[float, float]] = {}

    def consider(source_id: str, start_s: float, end_s: float, in_s: float, track: Mapping[str, Any] | None = None) -> None:
        """Ordena usos pelo início efetivo na timeline; usos simultâneos precisam do menor seek."""

        skip = window.skip(start_s, end_s)
        if skip is None:
            return
        advanced = track_in_s(track, skip) if track is not None else in_s + skip
        if advanced is None:
            return
        candidate = (max(window.start_s, start_s), advanced)
        first[source_id] = min(first.get(source_id, candidate), candidate)

    for segment, timeline in zip(document["segments"], timeline_segments, strict=True):
        video = segment["video"]
        consider(str(video["source_id"]), float(timeline["start_s"]), float(timeline["end_s"]), float(video["in_s"]))
        for track in segment["audio"]["tracks"]:
            consider(str(track["source_id"]), float(timeline["audio_start_s"]), float(timeline["audio_end_s"]), float(track["in_s"]), track)
    for layer in document["layers"]:
        if layer["type"] == "video":
            consider(str(layer["source_id"]), float(layer["start_s"]), float(layer["end_s"]), float(layer["in_s"]))

    total = sum(len(windows.get(source_id, (None,))) for source_id in video_ids)
    for source_id in video_ids:
        if source_id not in first or source_id not in intervals:
            continue
        anchor = first[source_id][1]
        existing = windows.get(source_id, ())
        containing = [span for span in existing if span.start_s <= anchor + SKIP_TOLERANCE_S]
        start = containing[-1].start_s if containing else 0.0
        if anchor <= start + SKIP_TOLERANCE_S or len(existing) >= MAX_WINDOWS_PER_SOURCE or total >= MAX_VIDEO_INPUTS:
            continue
        duration = float(source_records[source_id]["duration_seconds"])
        # Sem ganho de união, a entrada inicial continua cobrindo todos os usos anteriores ao anchor.
        base = existing or (DecodeWindow(0.0, duration),)
        end = max(out_s for in_s, out_s in intervals[source_id] if in_s + SKIP_TOLERANCE_S >= anchor)
        extra = DecodeWindow(anchor, min(duration, end + END_SLACK_S))
        if extra.seek_s <= (containing[-1].seek_s if containing else 0.0) + SKIP_TOLERANCE_S:
            # Dentro do pre-roll, uma entrada adicional faria a mesma leitura desde zero.
            continue
        windows[source_id] = tuple(sorted((*base, extra), key=lambda span: span.start_s))
        total += 1
