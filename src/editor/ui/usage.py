"""Uso de cada fonte na montagem: onde ela é lida e onde essa leitura aparece (phase 7, T5, F6).

Responsabilidade: dado o plano validado, listar para cada fonte de vídeo ou de áudio os trechos dela que a
montagem usa (segmentos, trilhas de áudio e camadas de vídeo) e, para cada trecho, o intervalo da montagem
em que ele aparece. É Python puro e sem estado, a base da barra de uso do modo Fonte, da lista de fontes
e do texto da seleção na fonte (`selection.describe_source_selection`).

Relação com `windows.source_intervals`: os intervalos de fonte são os mesmos que o planejador de janelas de
decode lê (segmento, trilha, camada de vídeo), com um único refinamento. O planejador lê o `in_s`/`out_s`
declarado da trilha porque só precisa cobrir o que o grafo pode abrir, mas a montagem só ouve a janela de
áudio do segmento (`audio_duration_s`, que J e L alongam): uma trilha de música declarada com 180 s sob um
segmento de 30 s usa 30 s. A barra de uso mostra o que se ouve, senão a fonte inteira apareceria como usada e
o operador não veria o que foi descartado. Em todo plano em que a trilha tem a duração da janela o conjunto é
idêntico ao de `source_intervals` (provado em `tests/test_ui_server.py`).

Mapeamento fonte para montagem de cada uso: linear (`montagem = início do uso na montagem + (fonte - início
na fonte)`) para segmento, camada de vídeo e trilha sem repetição. A trilha com `loop` repete o recorte na
janela de áudio, então o uso aponta para a janela inteira (`loop` verdadeiro) e a posição exata de cada
repetição não é informada.
ponytail: o uso de uma trilha em `loop` aponta para a janela inteira de áudio do segmento, listar cada
repetição quando o operador precisar saber em qual volta uma parte da fonte toca.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


USE_KIND_RANK = {"segment": 0, "audio": 1, "layer": 2}
"""Ordem de exibição dos usos que começam no mesmo ponto da fonte: vídeo do segmento, áudio, camada."""

_PRECISION = 6
"""Casas decimais dos tempos, a precisão da timeline de `plan validate`."""


def _use(
    kind: str,
    index: int,
    source_in_s: float,
    source_out_s: float,
    start_s: float,
    end_s: float,
    loop: bool = False,
) -> dict[str, Any]:
    """Um uso: de onde da fonte (`source_*`) para onde da montagem (`start_s`, `end_s`), arredondado a 6 casas."""

    return {
        "kind": kind,
        "index": index,
        "source_in_s": round(source_in_s, _PRECISION),
        "source_out_s": round(source_out_s, _PRECISION),
        "start_s": round(start_s, _PRECISION),
        "end_s": round(end_s, _PRECISION),
        "loop": loop,
    }


def source_uses(document: Mapping[str, Any], validation: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Usos por `source_id`, cada lista ordenada pelo início na fonte (vídeo do segmento, áudio e camada em empate).

    `kind` é `segment` (vídeo do segmento `index`, base 1), `audio` (trilha do segmento `index`) ou `layer`
    (camada de vídeo `index` de `layers[]`, base 1). Fontes sem uso ficam fora do resultado, e imagem estática
    nunca tem uso (camada de imagem não lê um intervalo da fonte).
    """

    uses: dict[str, list[dict[str, Any]]] = {}
    timeline = validation["timeline"]["segments"]
    for index, (segment, item) in enumerate(zip(document["segments"], timeline, strict=True), start=1):
        video = segment["video"]
        uses.setdefault(str(video["source_id"]), []).append(
            _use(
                "segment", index, float(video["in_s"]), float(video["out_s"]),
                float(item["start_s"]), float(item["end_s"]),
            )
        )
        window_s = float(item["audio_duration_s"])
        for track in segment["audio"]["tracks"]:
            in_s, out_s = float(track["in_s"]), float(track["out_s"])
            heard_s = min(out_s - in_s, window_s)
            loop = bool(track.get("loop", False))
            audio_start = float(item["audio_start_s"])
            uses.setdefault(str(track["source_id"]), []).append(
                _use(
                    "audio", index, in_s, in_s + heard_s,
                    audio_start, float(item["audio_end_s"]) if loop else audio_start + heard_s, loop,
                )
            )
    for index, layer in enumerate(document.get("layers") or [], start=1):
        if layer["type"] == "video":
            uses.setdefault(str(layer["source_id"]), []).append(
                _use(
                    "layer", index, float(layer["in_s"]), float(layer["out_s"]),
                    float(layer["start_s"]), float(layer["end_s"]),
                )
            )
    for source_uses_list in uses.values():
        source_uses_list.sort(key=lambda use: (use["source_in_s"], use["source_out_s"], USE_KIND_RANK[use["kind"]], use["index"]))
    return uses


def used_count(uses: list[dict[str, Any]]) -> int:
    """Quantas vezes a fonte é usada: segmentos e camadas distintos que a leem, contados uma vez cada.

    O vídeo e o áudio do mesmo segmento (o `keep` comum) são um uso só, senão toda fonte de câmera
    apareceria como usada o dobro das vezes.
    """

    return len({("layer" if use["kind"] == "layer" else "segment", use["index"]) for use in uses})
