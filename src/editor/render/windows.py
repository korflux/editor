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
"""

from __future__ import annotations

from collections.abc import Mapping
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


def source_intervals(document: Mapping[str, Any]) -> dict[str, list[tuple[float, float]]]:
    """Coleta os intervalos `in_s`/`out_s` de cada fonte em segmentos, trilhas e camadas de vídeo.

    Trilha com `loop` entra pelo seu recorte; a repetição acontece no filtro, não na leitura.
    """

    intervals: dict[str, list[tuple[float, float]]] = {}

    def add(item: Mapping[str, Any]) -> None:
        intervals.setdefault(str(item["source_id"]), []).append(
            (float(item["in_s"]), float(item["out_s"]))
        )

    for segment in document["segments"]:
        add(segment["video"])
        for track in segment["audio"]["tracks"]:
            add(track)
    for layer in document["layers"]:
        if layer["type"] == "video":
            add(layer)
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
    spans: list[list[float]], duration_s: float
) -> tuple[DecodeWindow, ...] | None:
    """Aplica a folga final e devolve as janelas só se a leitura cair pelo menos 20%.

    A leitura atual é estimada do início da fonte ao último `out_s`, que é o que o
    FFmpeg decodifica hoje com uma única entrada sem `-ss`.
    """

    windows = tuple(
        DecodeWindow(start_s=start, end_s=min(duration_s, end + END_SLACK_S))
        for start, end in spans
    )
    current_read = max(end for _, end in spans)
    windowed_read = sum(window.duration_s for window in windows)
    if current_read <= 0 or windowed_read > current_read * (1 - MIN_READ_REDUCTION):
        return None
    return windows


def plan_decode_windows(
    document: Mapping[str, Any],
    source_records: Mapping[str, Mapping[str, Any]],
    source_order: tuple[str, ...],
) -> dict[str, tuple[DecodeWindow, ...]]:
    """Calcula as janelas por fonte de vídeo; fontes ausentes do retorno mantêm a entrada atual.

    `source_records` são os registros validados por id (com `kind` e `duration_seconds`)
    e `source_order` é a ordem estável das entradas do grafo. Retorna vazio quando nenhuma
    fonte ganha ou quando as fontes de vídeo, sozinhas, já excedem o teto global.
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

    intervals = source_intervals(document)
    groups: dict[str, list[list[float]]] = {}
    windows: dict[str, tuple[DecodeWindow, ...]] = {}

    def refresh(source_id: str) -> None:
        """Recalcula as janelas de uma fonte e a retira do conjunto quando o ganho some."""

        planned = _windows_with_gain(
            groups[source_id], float(source_records[source_id]["duration_seconds"])
        )
        if planned is None:
            windows.pop(source_id, None)
            groups.pop(source_id, None)
        else:
            windows[source_id] = planned

    for source_id in video_ids:
        # Toda fonte em `source_order` é referenciada por algum recorte, logo há intervalos.
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
    return windows
