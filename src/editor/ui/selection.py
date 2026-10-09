"""Seleção de um trecho da montagem: o que há nele e o bloco de texto "Copiar para IA" (F7).

Responsabilidade: dado um intervalo da montagem `[start_s, end_s]` e o plano validado, listar os
segmentos, as camadas e as legendas que o cruzam, com os tempos da fonte, e montar o texto que o
operador cola no pedido à IA. É Python puro e testável (a interface só envia o intervalo e copia o
texto devolvido), e a fonte de verdade é a mesma da CLI: `timeline.segments` e `captions.occurrences`
de `plan validate` mais o documento do plano.

Regras do mapeamento:
- sobreposição é por interseção de comprimento positivo (`EPSILON_S` absorve o arredondamento do
  plano), então blocos que só se tocam não entram, e Alt+clique num bloco seleciona exatamente ele;
- segmento: o intervalo na fonte é o do vídeo (`in_s` mais o deslocamento dentro do segmento, sem
  mudança de velocidade no contrato); se só o áudio do segmento (cortes J e L, a janela de áudio é
  sempre maior que a do vídeo) cruza o trecho, ele entra com `audio_cut` `j` ou `l` e sem vídeo;
- camada: intervalo da camada dentro do trecho; texto com os primeiros 60 caracteres; vídeo em
  camada com o intervalo da fonte da parte selecionada;
- legenda: uma entrada por ocorrência, com os tempos da fonte do item (`in_s` e `out_s`).

Formato do texto: PT-BR, uma linha por item, textos entre aspas em JSON (uma linha, escapes
conhecidos), segundos com 3 casas e timecode `HH:MM:SS:FF` na taxa de saída; sem caminho absoluto
(só o nome da pasta do trabalho) e sem token. O bloco termina em `Pedido:` para o operador completar.

Pontos de extensão: a T5 acrescenta a seleção na fonte (usado, descartado ou parcial) ao lado desta.
"""

from __future__ import annotations

import json
import math
from typing import Any

from .state import PlanSnapshot


EPSILON_S = 1e-6
"""Folga contra o arredondamento a 6 casas dos tempos do plano ao decidir se dois intervalos se cruzam."""

TEXT_PREVIEW_CHARS = 60
"""Quantos caracteres do texto de uma camada entram na seleção (F7)."""

LAYER_TYPE_LABELS = {"text": "texto", "image": "imagem", "video": "vídeo"}
"""Tipo da camada como aparece no texto copiado."""

_TIMECODE_EPSILON = 1e-6


def format_timecode(seconds: float, frame_rate: float) -> str:
    """Timecode `HH:MM:SS:FF` do quadro que está na tela em `seconds`, na taxa de saída.

    Conta quadros (`floor(t * fps)`) e os escreve na base `round(fps)`, a convenção de timecode sem
    descarte de quadros: em 29,97 quadros a base é 30. A interface repete esta regra no JavaScript.
    """

    base = max(1, round(frame_rate))
    frames = max(0, math.floor(seconds * frame_rate + _TIMECODE_EPSILON))
    total_seconds, frame = divmod(frames, base)
    total_minutes, second = divmod(total_seconds, 60)
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}:{second:02d}:{frame:02d}"


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> tuple[float, float] | None:
    """Interseção de dois intervalos, ou `None` se ela não tem comprimento positivo."""

    low, high = max(start_a, start_b), min(end_a, end_b)
    return (low, high) if high - low > EPSILON_S else None


def _round(value: float) -> float:
    """Arredonda a 6 casas, a precisão dos tempos da timeline de `plan validate`."""

    return round(value, 6)


def _segments(snapshot: PlanSnapshot, start_s: float, end_s: float) -> list[dict[str, Any]]:
    """Segmentos cujo vídeo, ou só o áudio (J e L), cruza o trecho, com a parte selecionada na fonte."""

    entries: list[dict[str, Any]] = []
    for item in snapshot.validation["timeline"]["segments"]:
        video_part = _overlap(item["start_s"], item["end_s"], start_s, end_s)
        audio_part = _overlap(item["audio_start_s"], item["audio_end_s"], start_s, end_s)
        if video_part is None and audio_part is None:
            continue
        entry: dict[str, Any] = {
            "index": item["index"],
            "source_id": item["video_source_id"],
            "video": None,
            "audio": None,
            "audio_cut": None,
        }
        if video_part is not None:
            offset = snapshot.document["segments"][item["index"] - 1]["video"]["in_s"] - item["start_s"]
            entry["video"] = {
                "start_s": _round(video_part[0]),
                "end_s": _round(video_part[1]),
                "source_in_s": _round(video_part[0] + offset),
                "source_out_s": _round(video_part[1] + offset),
            }
        if audio_part is not None:
            entry["audio"] = {"start_s": _round(audio_part[0]), "end_s": _round(audio_part[1])}
            if video_part is None:
                entry["audio_cut"] = "j" if audio_part[1] <= item["start_s"] + EPSILON_S else "l"
        entries.append(entry)
    return entries


def _layers(snapshot: PlanSnapshot, start_s: float, end_s: float) -> list[dict[str, Any]]:
    """Camadas cujo intervalo cruza o trecho, com índice de `layers[]` (base 1) e a parte selecionada."""

    entries: list[dict[str, Any]] = []
    for index, layer in enumerate(snapshot.document.get("layers") or [], start=1):
        part = _overlap(float(layer["start_s"]), float(layer["end_s"]), start_s, end_s)
        if part is None:
            continue
        entry: dict[str, Any] = {
            "index": index,
            "type": layer["type"],
            "start_s": _round(part[0]),
            "end_s": _round(part[1]),
        }
        if layer["type"] == "text":
            entry["text"] = str(layer["text"])[:TEXT_PREVIEW_CHARS]
        else:
            entry["source_id"] = layer["source_id"]
            if layer["type"] == "video":
                offset = float(layer["in_s"]) - float(layer["start_s"])
                entry["source_in_s"] = _round(part[0] + offset)
                entry["source_out_s"] = _round(part[1] + offset)
        entries.append(entry)
    return entries


def _captions(snapshot: PlanSnapshot, start_s: float, end_s: float) -> list[dict[str, Any]]:
    """Ocorrências de legenda que cruzam o trecho, com texto e os tempos da fonte do item."""

    mapped = snapshot.validation.get("captions")
    if mapped is None:
        return []
    items = snapshot.document["captions"]["items"]
    entries: list[dict[str, Any]] = []
    for occurrence in mapped["occurrences"]:
        part = _overlap(occurrence["start_s"], occurrence["end_s"], start_s, end_s)
        if part is None:
            continue
        item = items[occurrence["item"] - 1]
        entries.append(
            {
                "item": occurrence["item"],
                "text": str(item["text"]),
                "source_id": item["source_id"],
                "source_in_s": item["in_s"],
                "source_out_s": item["out_s"],
                "start_s": _round(part[0]),
                "end_s": _round(part[1]),
            }
        )
    return entries


def _bullets(lines: list[str]) -> list[str]:
    """Itens de uma seção do texto, ou `- nenhum` para a seção vazia (a estrutura do bloco não muda)."""

    return lines or ["- nenhum"]


def _text(selection: dict[str, Any]) -> str:
    """Monta o bloco "Copiar para IA" a partir da seleção já mapeada (F7, conteúdo do bloco da montagem)."""

    frame_rate = selection["frame_rate"]
    start_s, end_s, duration_s = selection["start_s"], selection["end_s"], selection["duration_s"]
    segment_lines = []
    for segment in selection["segments"]:
        prefix = f'- Segmento {segment["index"]}: fonte "{segment["source_id"]}"'
        if segment["video"] is not None:
            video = segment["video"]
            segment_lines.append(
                f'{prefix}, {video["source_in_s"]:.3f} s a {video["source_out_s"]:.3f} s da fonte'
            )
        else:
            segment_lines.append(f'{prefix}, só o áudio do corte {segment["audio_cut"].upper()} entra no trecho')
    layer_lines = []
    for layer in selection["layers"]:
        prefix = f'- Camada {layer["index"]} ({LAYER_TYPE_LABELS[layer["type"]]})'
        if layer["type"] == "text":
            layer_lines.append(f'{prefix}: {json.dumps(layer["text"], ensure_ascii=False)}')
        elif layer["type"] == "video":
            layer_lines.append(
                f'{prefix}: fonte "{layer["source_id"]}", '
                f'{layer["source_in_s"]:.3f} s a {layer["source_out_s"]:.3f} s da fonte'
            )
        else:
            layer_lines.append(f'{prefix}: fonte "{layer["source_id"]}"')
    caption_lines = []
    seen_items: set[int] = set()
    for caption in selection["captions"]:
        if caption["item"] not in seen_items:
            seen_items.add(caption["item"])
            caption_lines.append(
                f'- Legenda {caption["item"]}: {json.dumps(caption["text"], ensure_ascii=False)}'
            )
    lines = [
        "Trecho da montagem",
        f'Trabalho: {selection["workdir_name"]}',
        f'Plano (SHA-256): {selection["plan_sha256"]}',
        (
            f'Intervalo: {format_timecode(start_s, frame_rate)} a {format_timecode(end_s, frame_rate)} '
            f"({start_s:.3f} s a {end_s:.3f} s), duração {duration_s:.3f} s "
            f"({format_timecode(duration_s, frame_rate)})"
        ),
        "Segmentos:",
        *_bullets(segment_lines),
        "Camadas:",
        *_bullets(layer_lines),
        "Legendas:",
        *_bullets(caption_lines),
        "Pedido:",
    ]
    return "\n".join(lines)


def describe_selection(
    snapshot: PlanSnapshot, workdir_name: str, start_s: float, end_s: float
) -> dict[str, Any]:
    """Mapeia `[start_s, end_s]` da montagem para segmentos, camadas e legendas e monta o texto para a IA.

    O chamador já validou `0 <= start_s < end_s <= duração`. Devolve a forma que `GET /api/selection`
    entrega: o painel desenha `segments`, `layers` e `captions` e o botão copia `text`.
    """

    selection: dict[str, Any] = {
        "workdir_name": workdir_name,
        "plan_sha256": snapshot.plan_sha256,
        "frame_rate": snapshot.frame_rate,
        "start_s": _round(start_s),
        "end_s": _round(end_s),
        "duration_s": _round(end_s - start_s),
        "start_timecode": format_timecode(start_s, snapshot.frame_rate),
        "end_timecode": format_timecode(end_s, snapshot.frame_rate),
        "duration_timecode": format_timecode(end_s - start_s, snapshot.frame_rate),
        "segments": _segments(snapshot, start_s, end_s),
        "layers": _layers(snapshot, start_s, end_s),
        "captions": _captions(snapshot, start_s, end_s),
    }
    selection["text"] = _text(selection)
    return selection
