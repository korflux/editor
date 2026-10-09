"""Fala original em frases curtas a partir da transcrição publicada (`editor captions suggest`).

Responsabilidade: entregar ao agente, em frases curtas e com tempos da fonte, o texto original que
ele vai traduzir para as legendas. O módulo é somente leitura: lê `analysis/transcription/<id>.json`
(o artefato de `analysis/transcription.py`), valida o formato, quebra as palavras pela regra da
decisão 12 da spec da phase 6 e responde só no envelope; não escreve arquivo, não traduz, não usa
rede nem processo externo.

Fluxo: validar argumentos (antes de tocar no trabalho) -> validar o trabalho -> localizar a
transcrição sem seguir links -> ler com teto de tamanho -> validar o formato -> filtrar a janela ->
quebrar em frases -> responder.

Pontos de extensão: as constantes `SUGGEST_*` abaixo são a fonte única de padrões, faixas, pausa e
pontuação de fecho; `capabilities` (T4) as lê daqui em vez de repeti-las.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import EXIT_CODES, CliError, reject_duplicate_json_keys
from ..dependencies import is_regular_path
from ..workdir import validate_source_id, validate_workdir


# --- Constantes do contrato (fonte única) -----------------------------------------------------------

# `max_chars`: tamanho máximo da frase em caracteres (84 = duas linhas de 42).
SUGGEST_MAX_CHARS_DEFAULT = 84
SUGGEST_MAX_CHARS_MIN = 20
SUGGEST_MAX_CHARS_MAX = 200
# `max_duration_s`: duração máxima da frase, do início da primeira palavra ao fim da última.
SUGGEST_MAX_DURATION_DEFAULT_S = 6.0
SUGGEST_MAX_DURATION_MIN_S = 1.0
SUGGEST_MAX_DURATION_MAX_S = 10.0
# Pausa, em segundos, entre o fim de uma palavra e o início da próxima que fecha a frase (>=).
SUGGEST_PAUSE_BREAK_S = 0.6
# Fim literal de palavra que fecha a frase.
SUGGEST_SENTENCE_END_CHARS = (".", "?", "!", "…")
# Teto de leitura da transcrição publicada: uma hora de fala tem cerca de 1 MiB, então 16 MiB deixa
# folga larga e ainda barra um arquivo adulterado antes de ele virar objeto em memória.
MAX_TRANSCRIPTION_BYTES = 16 * 1024 * 1024

# Casas decimais do artefato publicado (`analysis/transcription.py` arredonda em 6): comparar
# diferenças de tempo nesta precisão evita que `3.3 - 2.7 = 0.5999999999999996` esconda uma pausa
# de exatamente 0,6 s.
_TIME_DECIMALS = 6
# Argumentos numéricos da CLI chegam como texto: só decimais sem sinal nem notação científica, o que
# recusa `nan`, `inf`, `1e3`, `1_0` e negativos antes de qualquer conversão.
_DECIMAL_PATTERN = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,9})?")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_TRANSCRIPTION_SCHEMA_VERSION = 1
_MAX_LANGUAGE_CHARS = 32


# --- Erros públicos (mensagens fixas, sem eco de argumento nem de conteúdo) --------------------------


def _argument_error(message: str) -> CliError:
    """Erro de argumento fora da faixa; a mensagem nomeia a regra e nunca o valor recebido."""

    return CliError(code="ARGUMENT_INVALID", message=message, exit_code=EXIT_CODES["ARGUMENT"])


def _required_error() -> CliError:
    """Transcrição ainda não publicada para a fonte: o agente precisa rodar `analyze transcribe`."""

    return CliError(
        code="ANALYSIS_REQUIRED",
        message="A transcrição da fonte ainda não foi publicada.",
        exit_code=EXIT_CODES["STATE"],
    )


def _invalid_error() -> CliError:
    """Transcrição presente mas fora do formato publicado; serve a qualquer falha de leitura ou de schema."""

    return CliError(
        code="ANALYSIS_INVALID",
        message="A transcrição publicada da fonte não está no formato esperado.",
        exit_code=EXIT_CODES["STATE"],
    )


# --- Validação dos argumentos --------------------------------------------------------------------------


def _parse_decimal(raw: str) -> float | None:
    """Converte texto decimal sem sinal em float, ou devolve None quando não segue o formato."""

    if not _DECIMAL_PATTERN.fullmatch(raw):
        return None
    return float(raw)


def _parse_window(start_s: str | None, end_s: str | None) -> tuple[float, float | None]:
    """Valida `--start-s` e `--end-s`: finitos, >= 0 e início menor que o fim (padrão: transcrição inteira)."""

    message = "A janela deve usar --start-s maior ou igual a 0 e --end-s maior que --start-s, em segundos."
    start = 0.0 if start_s is None else _parse_decimal(start_s)
    end = None if end_s is None else _parse_decimal(end_s)
    if start is None or (end_s is not None and end is None) or (end is not None and end <= start):
        raise _argument_error(message)
    return start, end


def _parse_max_chars(raw: str | None) -> int:
    """Valida `--max-chars`: inteiro de `SUGGEST_MAX_CHARS_MIN` a `SUGGEST_MAX_CHARS_MAX`."""

    if raw is None:
        return SUGGEST_MAX_CHARS_DEFAULT
    if raw.isascii() and raw.isdecimal() and len(raw) <= 9:
        value = int(raw)
        if SUGGEST_MAX_CHARS_MIN <= value <= SUGGEST_MAX_CHARS_MAX:
            return value
    raise _argument_error(
        f"O argumento --max-chars deve ser um inteiro de {SUGGEST_MAX_CHARS_MIN} a {SUGGEST_MAX_CHARS_MAX}."
    )


def _parse_max_duration(raw: str | None) -> float:
    """Valida `--max-duration-s`: número de `SUGGEST_MAX_DURATION_MIN_S` a `SUGGEST_MAX_DURATION_MAX_S`."""

    if raw is None:
        return SUGGEST_MAX_DURATION_DEFAULT_S
    value = _parse_decimal(raw)
    if value is not None and SUGGEST_MAX_DURATION_MIN_S <= value <= SUGGEST_MAX_DURATION_MAX_S:
        return value
    raise _argument_error(
        "O argumento --max-duration-s deve ser um número de "
        f"{SUGGEST_MAX_DURATION_MIN_S:g} a {SUGGEST_MAX_DURATION_MAX_S:g} segundos."
    )


# --- Leitura e validação da transcrição publicada -------------------------------------------------------


@dataclass(frozen=True)
class _Word:
    """Palavra da transcrição, com o índice do segmento de origem (a regra (a) quebra na troca de segmento)."""

    segment: int
    text: str
    start_s: float
    end_s: float
    probability: float | None


@dataclass(frozen=True)
class _Transcription:
    """Parte da transcrição publicada que o `suggest` consome: identidade da fonte e palavras em ordem."""

    language: str
    source_sha256: str
    words: tuple[_Word, ...]


def _locate_transcription(root: Path, source_id: str) -> Path:
    """Deriva `analysis/transcription/<id>.json` só do ID já validado, sem seguir links nem reparse points.

    Componente ausente é `ANALYSIS_REQUIRED` (nada publicado ainda); componente que existe mas é link,
    junction ou tipo especial é `ANALYSIS_INVALID`, porque o artefato publicado é sempre arquivo regular
    dentro de pastas regulares e este módulo nunca segue um redirecionamento para fora do trabalho.
    """

    current = root
    for name, is_directory in (("analysis", True), ("transcription", True), (f"{source_id}.json", False)):
        current = current / name
        if not os.path.lexists(current):
            raise _required_error()
        if not is_regular_path(current, directory=is_directory):
            raise _invalid_error()
    return current


def _read_document(path: Path) -> Any:
    """Lê o JSON com teto de tamanho, UTF-8 estrito e chaves duplicadas recusadas; qualquer falha é formato inválido."""

    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_TRANSCRIPTION_BYTES + 1)
        if len(raw) <= MAX_TRANSCRIPTION_BYTES:
            return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_json_keys)
    except (OSError, ValueError, RecursionError):
        # ValueError cobre JSON inválido, chave duplicada e UTF-8 inválido; RecursionError cobre
        # aninhamento adversarial, que de outro modo viraria falha interna.
        pass
    raise _invalid_error()


def _object(value: Any) -> dict[str, Any]:
    """Exige objeto JSON; o resto é formato inválido."""

    if not isinstance(value, dict):
        raise _invalid_error()
    return value


def _list(value: Any) -> list[Any]:
    """Exige lista JSON não vazia; o artefato publicado nunca tem segmento nem palavras vazios."""

    if not isinstance(value, list) or not value:
        raise _invalid_error()
    return value


def _finite_number(value: Any) -> float:
    """Aceita só número JSON finito (nunca booleano), inclusive o que estoura float, que é recusado."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid_error()
    try:
        number = float(value)
    except OverflowError:
        raise _invalid_error() from None
    if not math.isfinite(number):
        raise _invalid_error()
    return number


def _time_span(item: dict[str, Any]) -> tuple[float, float]:
    """Lê `start_s` e `end_s` de um segmento ou palavra: finitos, não negativos e com início menor que o fim."""

    start = _finite_number(item.get("start_s"))
    end = _finite_number(item.get("end_s"))
    if start < 0 or end <= start:
        raise _invalid_error()
    return start, end


def _parse_word(segment_index: int, raw: Any) -> _Word:
    """Valida palavra, tempos e probabilidade opcional, com a mesma regra finita do publicador.

    `transcription._probability` não impõe faixa [0, 1]; este consumidor preserva o valor publicado.
    """

    entry = _object(raw)
    text = entry.get("word")
    if not isinstance(text, str):
        raise _invalid_error()
    start, end = _time_span(entry)
    probability = _finite_number(entry["probability"]) if "probability" in entry else None
    return _Word(segment=segment_index, text=text, start_s=start, end_s=end, probability=probability)


def _parse_transcription(document: Any, source_id: str) -> _Transcription:
    """Valida o artefato publicado pelo `analyze transcribe` e achata as palavras em ordem de fala.

    Confere versão do schema, identidade da fonte (o `source.id` do artefato deve ser o pedido, senão
    o `source_sha256` devolvido seria de outra fonte), idioma e, palavra a palavra, tempos finitos e
    início não decrescente (palavra fora de ordem recusa o arquivo inteiro em vez de gerar frases
    com tempo regressivo).
    """

    root = _object(document)
    source = _object(root.get("source"))
    transcription = _object(root.get("transcription"))
    sha256 = source.get("sha256")
    language = transcription.get("language")
    if (
        type(root.get("schema_version")) is not int
        or root.get("schema_version") != _TRANSCRIPTION_SCHEMA_VERSION
        or source.get("id") != source_id
        or not isinstance(sha256, str)
        or not _SHA256_PATTERN.fullmatch(sha256)
        or not isinstance(language, str)
        or not 0 < len(language) <= _MAX_LANGUAGE_CHARS
    ):
        raise _invalid_error()

    duration = _finite_number(source.get("duration_seconds"))
    if duration <= 0:
        raise _invalid_error()
    words: list[_Word] = []
    previous_start = previous_end = 0.0
    for segment_index, raw_segment in enumerate(_list(transcription.get("segments"))):
        segment = _object(raw_segment)
        segment_start, segment_end = _time_span(segment)
        if segment_end > duration or not isinstance(segment.get("text"), str):
            raise _invalid_error()
        for raw_word in _list(segment.get("words")):
            word = _parse_word(segment_index, raw_word)
            # O publicador limita palavras ao segmento; sobreposição é permitida, regressão não.
            if (
                word.start_s < previous_start
                or word.end_s < previous_end
                or word.start_s < segment_start
                or word.end_s > segment_end
            ):
                raise _invalid_error()
            previous_start, previous_end = word.start_s, word.end_s
            words.append(word)
    return _Transcription(language=language, source_sha256=sha256, words=tuple(words))


# --- Quebra em frases (decisão 12) ----------------------------------------------------------------------


def _breaks_before(
    phrase: list[_Word],
    phrase_text: str,
    word: _Word,
    max_chars: int,
    max_duration_s: float,
) -> bool:
    """Diz se a frase em curso deve fechar ANTES de receber `word`, pelas regras (a) a (d) da decisão 12.

    (e), a pontuação de fecho, é decidida depois que a palavra entra, em `_chunk_words`. Uma única
    palavra nunca é partida: se ela sozinha excede `max_chars` ou `max_duration_s`, forma uma frase
    própria (a frase em curso já fechou pela regra que a palavra disparou).
    """

    previous = phrase[-1]
    return (
        word.segment != previous.segment  # (a) troca de segmento da transcrição
        or len((phrase_text + word.text).strip()) > max_chars  # (b) a palavra estouraria os caracteres
        or round(word.end_s - phrase[0].start_s, _TIME_DECIMALS) > max_duration_s  # (c) estouraria a duração
        or round(word.start_s - previous.end_s, _TIME_DECIMALS) >= SUGGEST_PAUSE_BREAK_S  # (d) pausa
    )


def _serialize_chunk(phrase: list[_Word]) -> dict[str, Any]:
    """Monta a frase da resposta: tempos da primeira e da última palavra, texto, palavras e menor probabilidade.

    `words[].word` mantém o texto como publicado (com o espaço inicial do Whisper), de modo que
    `"".join(...)` reproduz `text` em qualquer idioma, inclusive nos sem espaço entre palavras.
    `min_probability` é a menor `probability` entre as palavras que a têm, ou `None` se nenhuma tiver.
    """

    probabilities = [word.probability for word in phrase if word.probability is not None]
    return {
        "in_s": phrase[0].start_s,
        "out_s": phrase[-1].end_s,
        "text": "".join(word.text for word in phrase).strip(),
        "words": [
            {"word": word.text, "start_s": word.start_s, "end_s": word.end_s}
            | ({} if word.probability is None else {"probability": word.probability})
            for word in phrase
        ],
        "min_probability": min(probabilities) if probabilities else None,
    }


def _chunk_words(words: list[_Word], max_chars: int, max_duration_s: float) -> list[dict[str, Any]]:
    """Percorre as palavras em ordem e fecha a frase pelas cinco regras da decisão 12."""

    chunks: list[dict[str, Any]] = []
    phrase: list[_Word] = []
    phrase_text = ""
    for word in words:
        if phrase and _breaks_before(phrase, phrase_text, word, max_chars, max_duration_s):
            chunks.append(_serialize_chunk(phrase))
            phrase, phrase_text = [], ""
        phrase.append(word)
        phrase_text += word.text
        if word.text.rstrip().endswith(SUGGEST_SENTENCE_END_CHARS):  # (e) pontuação de fecho
            chunks.append(_serialize_chunk(phrase))
            phrase, phrase_text = [], ""
    if phrase:
        chunks.append(_serialize_chunk(phrase))
    return chunks


# --- Ponto de entrada ------------------------------------------------------------------------------------


def suggest_captions(
    workdir: str | Path,
    source_id: str,
    *,
    start_s: str | None = None,
    end_s: str | None = None,
    max_chars: str | None = None,
    max_duration_s: str | None = None,
) -> dict[str, Any]:
    """Quebra a fala original da fonte em frases curtas com tempos, sem escrever nada no trabalho.

    Pertencimento à janela: uma palavra entra quando seu INÍCIO está em `[start_s, end_s)`, intervalo
    aberto à direita. É a regra mais previsível porque janelas vizinhas (`[0, 10)` e `[10, 20)`) particionam
    a fala: nenhuma palavra some nem aparece duas vezes, o que não vale para "palavra inteira dentro",
    que perderia a palavra que cruza a fronteira. Consequência documentada: o `out_s` da última frase
    pode passar de `end_s` pela cauda da última palavra. Palavra só de espaços não conta e é ignorada.

    `ponytail: palavra indivisível pode exceder max_chars ou max_duration_s sozinha, definir subdivisão editorial se houver demanda por limites absolutos`;
    `ponytail: uma transcrição por fonte (a do último analyze transcribe), guardar uma por idioma quando o fluxo de cruzar idiomas pesar`;
    `ponytail: o limite de caracteres conta caracteres, não largura de glifo, medir glifos se a quebra da legenda desbordar a caixa`.
    """

    safe_source_id = validate_source_id(source_id)
    window_start, window_end = _parse_window(start_s, end_s)
    char_limit = _parse_max_chars(max_chars)
    duration_limit = _parse_max_duration(max_duration_s)

    root = validate_workdir(workdir)
    transcription = _parse_transcription(_read_document(_locate_transcription(root, safe_source_id)), safe_source_id)
    window_words = [
        word
        for word in transcription.words
        if word.text.strip()
        and word.start_s >= window_start
        and (window_end is None or word.start_s < window_end)
    ]
    return {
        "source_id": safe_source_id,
        "language": transcription.language,
        "source_sha256": transcription.source_sha256,
        "chunks": _chunk_words(window_words, char_limit, duration_limit),
    }
