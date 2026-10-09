"""Testes da T1 da phase 6: `editor captions suggest`, a fala original em frases curtas (A1 da spec).

Prova pelo processo real da CLI (`python -m editor`), sobre transcrições sintéticas escritas aqui no
formato publicado por `analysis/transcription.py` (sem Whisper, sem FFmpeg, sem mídia). Cobre, nesta ordem:

1. Fluxo: uma fala de quatro segmentos desenhada para disparar cada regra de fecho da decisão 12 isolada
   (troca de segmento, `max_chars`, `max_duration_s`, pausa de 0,6 s com a fronteira exata e pontuação),
   com tempos, `min_probability` (inclusive palavra sem `probability`), janelas `--start-s`/`--end-s`
   (início da palavra em `[start, end)`), janela vazia e parâmetros `--max-chars`/`--max-duration-s`;
   o inventário do trabalho é idêntico antes e depois de todas as chamadas.
2. Argumentos fora da faixa: `ARGUMENT_INVALID` com mensagem fixa, sem eco, antes de ler o trabalho.
3. Estado: transcrição ausente (`ANALYSIS_REQUIRED`), fora do formato (`ANALYSIS_INVALID`, sem eco do
   conteúdo) e trabalho inválido (`WORKDIR_INVALID`).
4. Segurança: uma junction na pasta das transcrições não é seguida.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import unittest
from pathlib import Path
from typing import Any

from tests.test_cli_contract import run_module, workspace_tempdir


SOURCE_SHA256 = hashlib.sha256(b"fonte-sintetica").hexdigest()
# Marca que o teste planta no texto da transcrição inválida para provar que nenhuma resposta a ecoa.
SECRET_MARKER = "MARCADOR-SECRETO"
ARGUMENT_EXIT_CODE = 2
STATE_EXIT_CODE = 5


# --- Transcrição sintética no formato publicado -----------------------------------------------------------


def word(text: str, start_s: float, end_s: float, probability: float | None = None) -> dict[str, Any]:
    """Palavra publicada: texto com o espaço inicial do Whisper e `probability` só quando existe."""

    entry: dict[str, Any] = {"word": text, "start_s": start_s, "end_s": end_s}
    if probability is not None:
        entry["probability"] = probability
    return entry


def segment(index: int, words: list[dict[str, Any]]) -> dict[str, Any]:
    """Segmento publicado: limites e texto derivados das palavras, como o publicador faz."""

    return {
        "id": index,
        "start_s": words[0]["start_s"],
        "end_s": words[-1]["end_s"],
        "text": "".join(entry["word"] for entry in words),
        "words": words,
    }


def artifact(source_id: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Artefato completo de `analysis/transcription/<id>.json` (schema 1) com a identidade da fonte."""

    return {
        "schema_version": 1,
        "source": {
            "id": source_id,
            "path": "C:/fontes/sintetica.mp4",
            "sha256": SOURCE_SHA256,
            "size_bytes": 1024,
            "duration_seconds": 20.0,
            "container": "mov,mp4,m4a,3gp,3g2,mj2",
            "audio_codec": "aac",
            "video_codec": "h264",
        },
        "transcription": {
            "language": "en",
            "language_probability": 0.99,
            "text": "".join(item["text"] for item in segments),
            "segments": segments,
        },
    }


def publish(workdir: Path, source_id: str, content: dict[str, Any] | str) -> None:
    """Grava a transcrição como o publicador (JSON canônico UTF-8) ou um texto cru para os casos malformados."""

    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    (workdir / "analysis" / "transcription" / f"{source_id}.json").write_text(text, encoding="utf-8")


# Segmento 1 (índice 1): doze palavras de 0,5 s com 0,1 s de intervalo, sem pontuação e curtas em caracteres,
# então só a duração (> 6 s na 11ª palavra) fecha a frase. `w03` tem a menor probabilidade e `w05` não tem.
W_WORDS = [
    word(
        f" w{i:02d}",
        round(4.0 + 0.6 * i, 6),
        round(4.0 + 0.6 * i + 0.5, 6),
        {3: 0.55, 5: None}.get(i, 0.9),
    )
    for i in range(12)
]
# Segmento 3: onze palavras de 8 letras e 0,3 s, coladas (sem pausa) e sem pontuação, então só os
# caracteres fecham a frase no padrão de 84 (nove palavras = 80; a décima daria 89). `g` é a menos provável.
LETTER_WORDS = [
    word(
        f" {letter * 8}",
        round(15.0 + 0.3 * i, 6),
        round(15.0 + 0.3 * i + 0.3, 6),
        0.4 if letter == "g" else 0.99,
    )
    for i, letter in enumerate("abcdefghijk")
]


def speech_segments() -> list[dict[str, Any]]:
    """Fala sintética de quatro segmentos; cada fecho de frase esperado em `DEFAULT_CHUNKS` vem de uma regra só."""

    return [
        segment(
            0,
            [
                word(" Hello", 0.0, 0.4, 0.9),
                word(" there", 0.4, 0.8, 0.8),
                word(" my", 0.8, 1.0),  # sem probability
                word(" friend.", 1.0, 1.6, 0.7),  # (e) ponto
                word(" How", 1.7, 2.0, 0.95),
                word(" are", 2.0, 2.2, 0.96),
                word(" you", 2.8, 3.1, 0.9),  # (d) pausa de exatos 0,6 s (3.3 - 2.7 em float daria 0.5999...)
                word(" today?", 3.1, 3.6, 0.85),  # (e) interrogação
                word(" I", 3.7, 3.8, 0.9),
                word(" am", 3.8, 4.0, 0.92),  # termina o segmento 0 sem pontuação nem pausa
            ],
        ),
        segment(1, W_WORDS),
        segment(
            2,
            [
                word(" Uno", 12.0, 12.4, 0.8),
                word(" dos", 12.99, 13.3, 0.75),  # intervalo de 0,59 s: ainda não é pausa
                word(" tres…", 13.3, 13.8, 0.7),  # (e) reticências
                word(" cuatro!", 14.0, 14.5, 0.6),  # (e) exclamação
            ],
        ),
        segment(3, LETTER_WORDS),
    ]


def phrase_text(words: list[dict[str, Any]]) -> str:
    """Texto esperado de uma frase: palavras unidas como na transcrição, sem o espaço inicial."""

    return "".join(entry["word"] for entry in words).strip()


# (in_s, out_s, text, min_probability) esperados com os padrões (84 caracteres, 6 s, pausa de 0,6 s).
DEFAULT_CHUNKS = [
    (0.0, 1.6, "Hello there my friend.", 0.7),  # fecha por (e); `my` sem probability não zera o mínimo
    (1.7, 2.2, "How are", 0.95),  # fecha por (d), a pausa exata de 0,6 s até `you`
    (2.8, 3.6, "you today?", 0.85),  # fecha por (e)
    (3.7, 4.0, "I am", 0.9),  # fecha por (a), troca de segmento sem pausa
    (W_WORDS[0]["start_s"], W_WORDS[9]["end_s"], phrase_text(W_WORDS[:10]), 0.55),  # fecha por (c)
    (W_WORDS[10]["start_s"], W_WORDS[11]["end_s"], phrase_text(W_WORDS[10:]), 0.9),  # fecha pelo fim do segmento
    (12.0, 13.8, "Uno dos tres…", 0.7),  # 0,59 s não quebra; fecha por (e)
    (14.0, 14.5, "cuatro!", 0.6),  # fecha por (e)
    (LETTER_WORDS[0]["start_s"], LETTER_WORDS[8]["end_s"], phrase_text(LETTER_WORDS[:9]), 0.4),  # fecha por (b)
    (LETTER_WORDS[9]["start_s"], LETTER_WORDS[10]["end_s"], phrase_text(LETTER_WORDS[9:]), 0.99),  # resto
]


# --- Execução e leitura -----------------------------------------------------------------------------------


def suggest(workdir: Path | str, *arguments: str, source_id: str = "main") -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    """Roda `captions suggest --json` no processo real e devolve o processo com o envelope já decodificado."""

    result = run_module("captions", "suggest", "--workdir", str(workdir), "--source-id", source_id, *arguments, "--json")
    return result, json.loads(result.stdout)


def chunk_summary(payload: dict[str, Any]) -> list[tuple[Any, Any, Any, Any]]:
    """Reduz as frases da resposta a (in_s, out_s, text, min_probability) para comparar com a regra."""

    return [(chunk["in_s"], chunk["out_s"], chunk["text"], chunk["min_probability"]) for chunk in payload["data"]["chunks"]]


def inventory(workdir: Path) -> dict[str, tuple[bool, int, int]]:
    """Instantâneo do trabalho (tipo, tamanho e mtime de cada entrada, raiz incluída) para provar que nada foi escrito."""

    snapshot: dict[str, tuple[bool, int, int]] = {}
    for path in (workdir, *sorted(workdir.rglob("*"))):
        status = path.stat()
        snapshot[path.relative_to(workdir).as_posix()] = (path.is_dir(), status.st_size, status.st_mtime_ns)
    return snapshot


def init_workdir_with_main(parent: Path) -> Path:
    """Cria o trabalho pelo `init` real e publica a fala sintética como a transcrição da fonte `main`."""

    workdir = parent / "job"
    workdir.mkdir()
    initialized = run_module("init", "--workdir", str(workdir), "--json")
    assert initialized.returncode == 0, initialized.stderr
    publish(workdir, "main", artifact("main", speech_segments()))
    return workdir


class CaptionsSuggestTests(unittest.TestCase):
    """A1: frases pelas cinco regras, janelas, recusas por código e nenhum arquivo novo no trabalho."""

    def assert_error(
        self,
        result: subprocess.CompletedProcess[str],
        payload: dict[str, Any],
        code: str,
        exit_code: int,
    ) -> None:
        """Envelope de erro seguro: código estável, exit code da família, stderr vazio e sem traceback."""

        self.assertEqual(result.returncode, exit_code, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], code)
        self.assertEqual(payload["error"]["exit_code"], exit_code)
        self.assertNotIn("Traceback", result.stdout)

    def test_suggest_breaks_the_original_speech_by_the_five_rules(self) -> None:
        """Frases, tempos, `min_probability`, janelas e parâmetros conferidos contra a regra, sem escrever no trabalho."""

        with workspace_tempdir() as parent:
            workdir = init_workdir_with_main(parent)
            before = inventory(workdir)

            result, payload = suggest(workdir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["command"], "captions suggest")
            self.assertEqual(set(payload["data"]), {"source_id", "language", "source_sha256", "chunks"})
            self.assertEqual(payload["data"]["source_id"], "main")
            self.assertEqual(payload["data"]["language"], "en")
            self.assertEqual(payload["data"]["source_sha256"], SOURCE_SHA256)
            self.assertEqual(chunk_summary(payload), DEFAULT_CHUNKS)

            # `words[]` carrega o texto como publicado e `probability` só nas palavras que a têm.
            first = payload["data"]["chunks"][0]
            self.assertEqual(
                first["words"],
                [
                    {"word": " Hello", "start_s": 0.0, "end_s": 0.4, "probability": 0.9},
                    {"word": " there", "start_s": 0.4, "end_s": 0.8, "probability": 0.8},
                    {"word": " my", "start_s": 0.8, "end_s": 1.0},
                    {"word": " friend.", "start_s": 1.0, "end_s": 1.6, "probability": 0.7},
                ],
            )
            self.assertEqual("".join(entry["word"] for entry in first["words"]).strip(), first["text"])

            # Janela: a palavra entra se COMEÇA em [start, end); `w00` começa em 4.0 e fica fora de [2.8, 4.0).
            _, payload = suggest(workdir, "--start-s", "2.8", "--end-s", "4.0")
            self.assertEqual(chunk_summary(payload), [DEFAULT_CHUNKS[2], DEFAULT_CHUNKS[3]])
            # Só `my` começa em [0.5, 0.9); o `out_s` (1.0) passa de `--end-s` pela cauda da palavra.
            _, payload = suggest(workdir, "--start-s", "0.5", "--end-s", "0.9")
            self.assertEqual(chunk_summary(payload), [(0.8, 1.0, "my", None)])
            # A fronteira direita é aberta: `a` começa em 15.0 e `cuatro!` em 14.0.
            _, payload = suggest(workdir, "--start-s", "14.0", "--end-s", "15.0")
            self.assertEqual(chunk_summary(payload), [DEFAULT_CHUNKS[7]])
            # Janela depois da última palavra: sucesso com lista vazia, não erro.
            result, payload = suggest(workdir, "--start-s", "19", "--end-s", "30")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["chunks"], [])
            self.assertEqual(payload["data"]["language"], "en")

            # `--max-chars 20` (o mínimo) sobre as letras: duas palavras (17) cabem, a terceira (26) não.
            _, payload = suggest(workdir, "--start-s", "15.0", "--end-s", "18.4", "--max-chars", "20")
            pairs = [LETTER_WORDS[index : index + 2] for index in range(0, 10, 2)] + [LETTER_WORDS[10:]]
            self.assertEqual(
                chunk_summary(payload),
                [
                    (
                        pair[0]["start_s"],
                        pair[-1]["end_s"],
                        phrase_text(pair),
                        min(entry["probability"] for entry in pair),
                    )
                    for pair in pairs
                ],
            )
            # `--max-duration-s 1` (o mínimo): a segunda palavra já estende a frase a 1,1 s, então cada `w` é uma frase.
            _, payload = suggest(workdir, "--start-s", "4.0", "--end-s", "6.0", "--max-duration-s", "1")
            self.assertEqual(
                chunk_summary(payload),
                [(entry["start_s"], entry["end_s"], phrase_text([entry]), entry.get("probability")) for entry in W_WORDS[:4]],
            )

            self.assertEqual(inventory(workdir), before)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_indivisible_words_and_published_probability_contract_are_preserved(self) -> None:
        """Uma palavra não é cortada; probabilidade finita e sobreposição ordenada seguem o publicador."""

        with workspace_tempdir() as parent:
            workdir = init_workdir_with_main(parent)
            words = [word(" " + "x" * 100, 0.0, 7.0, 1.2), word(" next", 6.9, 7.2, -0.1)]
            publish(workdir, "main", artifact("main", [segment(0, words)]))
            before = inventory(workdir)
            result, payload = suggest(workdir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(chunk_summary(payload), [(0.0, 7.0, "x" * 100, 1.2), (6.9, 7.2, "next", -0.1)])
            self.assertEqual(inventory(workdir), before)

    def test_arguments_out_of_range_are_refused_without_echo_before_reading_the_workdir(self) -> None:
        """Faixas fechadas: mensagem fixa por argumento, sem o valor recebido, mesmo com o trabalho inexistente."""

        chars_message = "O argumento --max-chars deve ser um inteiro de 20 a 200."
        duration_message = "O argumento --max-duration-s deve ser um número de 1 a 10 segundos."
        window_message = "A janela deve usar --start-s maior ou igual a 0 e --end-s maior que --start-s, em segundos."
        cases = [
            (("--max-chars", "19"), chars_message),
            (("--max-chars", "201"), chars_message),
            (("--max-chars", "20.5"), chars_message),
            (("--max-chars", "-30"), chars_message),
            (("--max-chars", f"30-{SECRET_MARKER}"), chars_message),
            (("--max-duration-s", "0.9"), duration_message),
            (("--max-duration-s", "10.1"), duration_message),
            (("--max-duration-s", "nan"), duration_message),
            (("--max-duration-s", "1e1"), duration_message),
            (("--max-duration-s", f"5-{SECRET_MARKER}"), duration_message),
            (("--start-s", "-1"), window_message),
            (("--start-s", "inf"), window_message),
            (("--start-s", f"1-{SECRET_MARKER}"), window_message),
            (("--end-s", "0"), window_message),
            (("--end-s", f"9-{SECRET_MARKER}"), window_message),
            (("--start-s", "5", "--end-s", "5"), window_message),
            (("--start-s", "6", "--end-s", "5"), window_message),
        ]

        with workspace_tempdir() as parent:
            # O trabalho nem existe: se algum argumento fosse conferido depois do trabalho, o erro seria WORKDIR_INVALID.
            missing_workdir = parent / "nao-existe"
            for arguments, message in cases:
                with self.subTest(arguments=arguments):
                    result, payload = suggest(missing_workdir, *arguments)
                    self.assert_error(result, payload, "ARGUMENT_INVALID", ARGUMENT_EXIT_CODE)
                    self.assertEqual(payload["error"]["message"], message)
                    self.assertNotIn(SECRET_MARKER, result.stdout)

            result, payload = suggest(missing_workdir, source_id="Fonte Invalida")
            self.assert_error(result, payload, "ARGUMENT_INVALID", ARGUMENT_EXIT_CODE)
            self.assertNotIn("Fonte Invalida", result.stdout)

            # `--source-id` é obrigatório: sem ele o parser recusa com o mesmo código.
            bare = run_module("captions", "suggest", "--workdir", str(missing_workdir), "--json")
            self.assert_error(bare, json.loads(bare.stdout), "ARGUMENT_INVALID", ARGUMENT_EXIT_CODE)

    def test_missing_malformed_transcription_and_invalid_workdir_are_distinct_state_errors(self) -> None:
        """Ausente é `ANALYSIS_REQUIRED`, fora do formato é `ANALYSIS_INVALID` sem eco, trabalho ruim é `WORKDIR_INVALID`."""

        with workspace_tempdir() as parent:
            # Trabalho sem `analysis/` (nunca passou por `init`): ainda é "não publicada", não erro de trabalho.
            bare_workdir = parent / "bare"
            bare_workdir.mkdir()
            result, payload = suggest(bare_workdir)
            self.assert_error(result, payload, "ANALYSIS_REQUIRED", STATE_EXIT_CODE)
            self.assertEqual(payload["error"]["message"], "A transcrição da fonte ainda não foi publicada.")
            self.assertEqual(list(bare_workdir.iterdir()), [])

            workdir = init_workdir_with_main(parent)
            base = artifact("main", speech_segments())
            base["transcription"]["segments"][0]["words"][0]["word"] = f" {SECRET_MARKER}"

            def mutated(change: Any) -> dict[str, Any]:
                """Cópia do artefato com o marcador secreto e uma única quebra do formato aplicada."""

                document = copy.deepcopy(base)
                change(document)
                return document

            def first_word(document: dict[str, Any]) -> dict[str, Any]:
                """Primeira palavra do primeiro segmento, onde as quebras de formato são aplicadas."""

                return document["transcription"]["segments"][0]["words"][0]

            malformed: dict[str, dict[str, Any] | str] = {
                "bad-json": '{"schema_version":1,"source":',
                "bad-duplicate-key": '{"schema_version":1,"schema_version":1}',
                "bad-not-object": "[]",
                "bad-field": mutated(lambda document: first_word(document).pop("end_s")),
                "bad-time": mutated(lambda document: first_word(document).update(start_s=float("nan"))),
                "bad-overflow": mutated(lambda document: first_word(document).update(end_s=10**400)),
                "bad-bool": mutated(lambda document: first_word(document).update(start_s=True)),
                "bad-order": mutated(lambda document: document["transcription"]["segments"][0]["words"][3].update(start_s=0.1)),
                "bad-sha": mutated(lambda document: document["source"].pop("sha256")),
                "bad-id": mutated(lambda document: document["source"].update(id="outra")),
                "bad-version": mutated(lambda document: document.update(schema_version=2)),
                "bad-bool-version": mutated(lambda document: document.update(schema_version=True)),
                "bad-float-version": mutated(lambda document: document.update(schema_version=1.0)),
                "bad-end-order": mutated(lambda document: (
                    first_word(document).update(end_s=0.7),
                    document["transcription"]["segments"][0]["words"][1].update(end_s=0.6),
                )),
                "bad-outside-segment": mutated(lambda document: first_word(document).update(end_s=5)),
                "bad-duration": mutated(lambda document: document["source"].update(duration_seconds=1)),
                "bad-empty": mutated(lambda document: document["transcription"].update(segments=[])),
                "bad-probability": mutated(lambda document: first_word(document).update(probability="alta")),
            }
            for name, content in malformed.items():
                # Identidade válida isola a violação: source.id divergente mascararia todas as mutações.
                if isinstance(content, dict) and name != "bad-id":
                    content["source"]["id"] = name
                publish(workdir, name, content)
            before = inventory(workdir)

            result, payload = suggest(workdir, source_id="ausente")
            self.assert_error(result, payload, "ANALYSIS_REQUIRED", STATE_EXIT_CODE)
            for name in malformed:
                with self.subTest(source_id=name):
                    result, payload = suggest(workdir, source_id=name)
                    self.assert_error(result, payload, "ANALYSIS_INVALID", STATE_EXIT_CODE)
                    self.assertEqual(payload["error"]["message"], "A transcrição publicada da fonte não está no formato esperado.")
                    self.assertNotIn(SECRET_MARKER, result.stdout)
                    self.assertNotIn(name, result.stdout)

            # Trabalho inválido: caminho relativo e pasta inexistente, ambos pelo erro vigente do trabalho.
            for invalid_workdir in ("job", str(parent / "nao-existe")):
                with self.subTest(workdir=invalid_workdir):
                    result, payload = suggest(invalid_workdir)
                    self.assert_error(result, payload, "WORKDIR_INVALID", STATE_EXIT_CODE)

            self.assertEqual(inventory(workdir), before)

    def test_junction_in_transcription_folder_is_not_followed(self) -> None:
        """Uma junction em `analysis/transcription` apontando para uma pasta com transcrição válida é recusada."""

        with workspace_tempdir() as parent:
            workdir = parent / "job"
            (workdir / "analysis").mkdir(parents=True)
            physical = parent / "fisico"
            physical.mkdir()
            publish_target = physical / "main.json"
            publish_target.write_text(json.dumps(artifact("main", speech_segments())), encoding="utf-8")
            link = workdir / "analysis" / "transcription"
            created = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(physical)],
                capture_output=True,
                text=True,
                check=False,
            )
            if created.returncode != 0:
                self.skipTest("não foi possível criar a junction de prova")
            try:
                result, payload = suggest(workdir)
                self.assert_error(result, payload, "ANALYSIS_INVALID", STATE_EXIT_CODE)
                self.assertNotIn("chunks", result.stdout)
            finally:
                # `rmdir` numa junction remove só o link, nunca o alvo.
                try:
                    os.rmdir(link)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
