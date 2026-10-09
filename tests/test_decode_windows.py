"""Testes das janelas de decode: planejador, comando inalterado sem ganho, equivalência e render.

A equivalência compara o `framemd5` do vídeo e do áudio produzidos pelo mesmo grafo com e
sem janelas, numa fonte H.264/AAC de GOP curto gerada pelo próprio teste. Os testes de
render (T2) provam preview e full reais com janelas por padrão, o envelope `data.decode`,
o fallback sem janelas e a regra do decode por GPU por entradas de vídeo. Os testes de PCM
(T6 da phase 5) provam amostra a amostra que o áudio renderizado com janelas coincide com o
sem janelas em fontes com e sem B-frames, com cortes dentro e fora dos keyframes.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies, process
from editor.contracts import CliError
from editor.integrity.approval import approve_workdir
from editor.plan.validation import load_validated_plan
from editor.render.ffmpeg_graph import FfmpegGraph, build_ffmpeg_graph
from editor.render.full import render_full
from editor.render.preview import GPU_MAX_VIDEO_INPUTS, _build_ffmpeg_arguments, render_preview
from editor.render.windows import (
    AUDIO_PREROLL_S,
    MAX_VIDEO_INPUTS,
    MAX_WINDOWS_PER_SOURCE,
    DecodeWindow,
    plan_decode_windows,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / "tests" / "fixtures" / "workflow"
SOURCE_DURATION_S = 30.0
AUDIO_PROCESSING = {
    "normalize_loudness": False,
    "target_lufs": -16,
    "limiter": {"enabled": False, "ceiling_dbfs": -1},
    "ducking": False,
}


# --- Fábricas de plano usadas pelos cenários ---------------------------------------------


def segment(in_s: float, out_s: float, tracks: list[dict[str, Any]] | None = None, mode: str = "keep") -> dict[str, Any]:
    """Segmento da fonte `main` que, por padrão, mantém o próprio áudio no mesmo recorte."""

    return {
        "video": {"source_id": "main", "in_s": in_s, "out_s": out_s, "fit": "preserve"},
        "audio": {
            "mode": mode,
            "tracks": tracks if tracks is not None else [track(in_s, out_s)],
        },
    }


def track(in_s: float, out_s: float, *, loop: bool = False, source_id: str = "main") -> dict[str, Any]:
    """Trilha de áudio sem ganho, com `loop` explícito apenas quando pedido."""

    item: dict[str, Any] = {"source_id": source_id, "in_s": in_s, "out_s": out_s, "gain_db": 0}
    if loop:
        item["loop"] = True
    return item


def plan(segments: list[dict[str, Any]], *, cuts: list[dict[str, Any]] | None = None, layers: list[dict[str, Any]] | None = None, sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Documento schema v2 mínimo sobre a fonte sintética, sem processamento de áudio."""

    return {
        "schema_version": 2,
        "profile": {"aspect_ratio": "16:9"},
        "sources": sources or [{"id": "main", "kind": "video", "path": "sources/main.mp4"}],
        "segments": segments,
        "cuts": cuts or [],
        "layers": layers or [],
        "audio_processing": AUDIO_PROCESSING,
    }


def synthetic_records(**durations: tuple[str, float | None]) -> dict[str, dict[str, Any]]:
    """Registros validados mínimos (`kind` e `duration_seconds`) para testar o planejador puro."""

    return {
        source_id: {"id": source_id, "kind": kind, "duration_seconds": duration}
        for source_id, (kind, duration) in durations.items()
    }


def video_only_document(intervals: dict[str, list[tuple[float, float]]]) -> dict[str, Any]:
    """Documento sintético com um segmento mudo por intervalo, para exercitar só o planejador."""

    return {
        "segments": [
            {
                "video": {"source_id": source_id, "in_s": start, "out_s": end},
                "audio": {"mode": "mute", "tracks": []},
            }
            for source_id, spans in intervals.items()
            for start, end in spans
        ],
        "layers": [],
    }


# --- Infraestrutura de execução real -------------------------------------------------------


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    """Cria um trabalho isolado no workspace e o remove ao final."""

    path = PROJECT_ROOT / f"tmp-decode-windows-{uuid4().hex}"
    (path / "sources").mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def create_gop_source(
    path: Path, *, gop: int = 12, b_frames: bool = True, sample_rate: int = 48000
) -> None:
    """Gera fonte H.264 e AAC estéreo cujo sinal nunca se repete, com GOP, B-frames e taxa de áudio escolhidos.

    O padrão (GOP 12, com B-frames, 48 kHz) é a fonte da phase 4. `b_frames=False` gera a fonte
    sem reordenação, em que o `dts` do keyframe coincide com o `pts` (o caso do defeito de áudio
    da T6 da phase 5); com B-frames o `dts` do keyframe fica 2 quadros antes e o áudio já nasce
    recuado, o que esconde o defeito.

    O PNS do encoder AAC nativo fica desligado: ele sorteia ruído com estado do decoder que
    depende de todos os quadros anteriores, e nenhum seek preserva esse estado.
    """

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=25:duration={SOURCE_DURATION_S:g}",
            "-f", "lavfi", "-i",
            f"aevalsrc=0.4*sin(2*PI*(220+15*t)*t)|0.4*sin(2*PI*(330+9*t)*t):s={sample_rate}:d={SOURCE_DURATION_S:g}",
            "-c:v", "libx264", "-g", str(gop), *([] if b_frames else ["-bf", "0"]),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-aac_pns", "0", "-b:a", "128k",
            "-movflags", "+faststart", "-y", str(path),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))


def write_plan(workdir: Path, document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Grava o plano e devolve documento e validação reais, como o render os recebe."""

    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")
    return load_validated_plan(workdir)


def graph_arguments(graph: FfmpegGraph, validation: dict[str, Any], accel: str = "cpu") -> list[str]:
    """Argumentos completos do render para o grafo, sem executar o FFmpeg."""

    return _build_ffmpeg_arguments(
        graph,
        validation["sources"],
        Path("out.mp4"),
        filter_script=Path("filter.txt"),
        duration_seconds=float(validation["timeline"]["duration_seconds"]),
        frame_rate=25.0,
        accel=accel,
    )


@contextmanager
def windows_disabled() -> Iterator[None]:
    """Força o render a montar só o grafo atual, sem janelas, para servir de referência."""

    def plain_graph(
        document: Any, validation: Any, *, canvas_width: int, canvas_height: int, windows: bool = False
    ) -> FfmpegGraph:
        return build_ffmpeg_graph(
            document, validation, canvas_width=canvas_width, canvas_height=canvas_height
        )

    with patch("editor.render.preview.build_ffmpeg_graph", side_effect=plain_graph):
        yield


def output_framemd5(path: Path) -> str:
    """`framemd5` do vídeo e do áudio decodificados de um MP4 publicado."""

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-map", "0:v", "-map", "0:a", "-f", "framemd5", "-"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout.decode()


def output_pcm(path: Path) -> bytes:
    """PCM `s16le` decodificado do áudio de um MP4 publicado (4 bytes por amostra estéreo)."""

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-map", "0:a", "-f", "s16le", "-"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout


def decoded_framemd5(workdir: Path, graph: FfmpegGraph, validation: dict[str, Any]) -> str:
    """Executa o grafo com as entradas reais do render e devolve o `framemd5` de vídeo e áudio.

    Reaproveita de `_build_ffmpeg_arguments` só o trecho das entradas (com `-ss`/`-t` quando
    houver), trocando o encoder por quadros decodificados para comparar sem perdas. O áudio
    é reagrupado em blocos fixos de 1024 amostras porque o seek muda só o tamanho dos
    blocos que o decoder entrega, e o encoder AAC do render reagrupa as amostras do mesmo
    jeito; o hash passa a refletir amostras e timestamps, não a fronteira dos blocos.
    """

    filter_option = dependencies.ffmpeg_filter_option()
    arguments = graph_arguments(graph, validation)
    input_arguments = arguments[: arguments.index(filter_option)]
    filter_complex = graph.filter_complex
    if graph.audio_label:
        filter_complex += f";[{graph.audio_label}]asetnsamples=n=1024:p=0[acompare]"
    script = workdir / f"filter-{uuid4().hex}.txt"
    script.write_text(filter_complex, encoding="utf-8")
    command = [
        "ffmpeg", *input_arguments, filter_option, str(script),
        "-map", f"[{graph.video_label}]",
    ]
    if graph.audio_label:
        command.extend(["-map", "[acompare]"])
    command.extend(["-f", "framemd5", "-"])
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout.decode()


# --- Planejador puro ------------------------------------------------------------------------


class DecodeWindowPlannerTests(unittest.TestCase):
    """Regras das decisões 3 a 6 da spec, sem FFmpeg."""

    def plan_for(self, intervals: dict[str, list[tuple[float, float]]], duration: float = 100.0) -> dict[str, tuple[DecodeWindow, ...]]:
        """Planeja janelas para fontes de vídeo sintéticas com a mesma duração."""

        records = synthetic_records(**{source_id: ("video", duration) for source_id in intervals})
        return plan_decode_windows(video_only_document(intervals), records, tuple(intervals))

    def test_merges_short_gaps_adds_end_slack_and_clamps_to_duration(self) -> None:
        windows = self.plan_for({"main": [(10, 11), (0, 1), (2.9, 3), (28, 30)]}, duration=30.0)
        self.assertEqual(
            windows["main"],
            (DecodeWindow(0, 3.5), DecodeWindow(10, 11.5), DecodeWindow(28, 30.0)),
        )

    def test_gap_of_exactly_two_seconds_is_not_merged(self) -> None:
        windows = self.plan_for({"main": [(0, 1), (3, 4), (20, 21)]})
        self.assertEqual(len(windows["main"]), 3)

    def test_requires_at_least_twenty_percent_less_read(self) -> None:
        # Leitura atual 10 s (até o último out_s); janelas [0, 3.5] + [6, 10.5] somam 8 s, 20% a menos.
        self.assertIn("main", self.plan_for({"main": [(0, 3), (6, 10)]}))
        self.assertEqual(self.plan_for({"main": [(0, 3.1), (6, 10)]}), {})
        # Um único trecho do início não reduz a leitura e mantém a entrada atual.
        self.assertEqual(self.plan_for({"main": [(0, 10)]}), {})

    def test_only_video_sources_are_windowed(self) -> None:
        document = video_only_document({"main": [(20, 21)]})
        document["segments"][0]["audio"] = {"mode": "mix", "tracks": [track(40, 41, source_id="music"), track(20, 21)]}
        document["layers"] = [{"type": "image", "source_id": "logo"}]
        records = synthetic_records(main=("video", 60.0), music=("audio", 60.0), logo=("image", None))
        windows = plan_decode_windows(document, records, ("main", "music", "logo"))
        self.assertEqual(windows, {"main": (DecodeWindow(20, 21.5),)})

    def test_per_source_cap_merges_the_smallest_gaps(self) -> None:
        starts = [0, 3, 7, 12, 18, 25, 33, 42, 52, 63]
        windows = self.plan_for({"main": [(start, start + 0.5) for start in starts]})
        self.assertEqual(len(windows["main"]), MAX_WINDOWS_PER_SOURCE)
        # As duas menores lacunas (2,5 s e 3,5 s) são as primeiras absorvidas.
        self.assertEqual(windows["main"][0], DecodeWindow(0, 8.0))
        self.assertEqual(windows["main"][1], DecodeWindow(12, 13.0))

    def test_global_cap_keeps_one_input_per_source_and_is_deterministic(self) -> None:
        spans = [(start, start + 0.5) for start in (0, 5, 10, 15, 20, 25, 30, 35)]
        intervals = {f"s{index}": spans for index in range(4)}
        intervals["plain"] = [(0, 10)]
        first = self.plan_for(intervals)
        self.assertEqual(first, self.plan_for(intervals))
        self.assertNotIn("plain", first)
        self.assertTrue(first)
        # Fonte sem janelas ocupa exatamente uma entrada; o total fecha no teto global.
        inputs = sum(len(first.get(source_id, (None,))) for source_id in intervals)
        self.assertEqual(inputs, MAX_VIDEO_INPUTS)

    def test_seek_leads_the_first_use_by_the_audio_preroll_and_never_seeks_at_the_source_start(self) -> None:
        window = DecodeWindow(10, 11.5)
        self.assertEqual(window.seek_s, 10 - AUDIO_PREROLL_S)
        self.assertEqual(window.read_duration_s, 1.5 + AUDIO_PREROLL_S)
        # `duration_s` continua sendo o intervalo usado, base da estimativa de ganho.
        self.assertEqual(window.duration_s, 1.5)
        # Janela que começa dentro do pre-roll lê a fonte desde o início, sem `-ss`.
        for start in (0, 0.1, AUDIO_PREROLL_S, AUDIO_PREROLL_S + 0.0000004):
            self.assertEqual(DecodeWindow(start, 3).seek_s, 0.0, start)
        self.assertEqual(DecodeWindow(AUDIO_PREROLL_S + 0.00001, 3).seek_s, 0.00001)

    def test_more_video_sources_than_the_cap_disables_windows(self) -> None:
        intervals = {f"s{index}": [(50, 51)] for index in range(MAX_VIDEO_INPUTS + 1)}
        self.assertEqual(self.plan_for(intervals), {})


# --- Grafo e comando reais ------------------------------------------------------------------


class DecodeWindowGraphTests(unittest.TestCase):
    """Comando inalterado sem ganho e equivalência de quadros e áudio com janelas."""

    source: Path
    _cache: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._cache = PROJECT_ROOT / f"tmp-decode-windows-source-{uuid4().hex}"
        cls._cache.mkdir()
        cls.source = cls._cache / "main.mp4"
        create_gop_source(cls.source)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._cache, ignore_errors=True)

    def assert_unchanged(self, workdir: Path, document: dict[str, Any]) -> None:
        """Confirma grafo e comando idênticos aos de `windows=False`, sem nenhum `-ss`."""

        document, validation = write_plan(workdir, document)
        options = {"canvas_width": 160, "canvas_height": 90}
        plain = build_ffmpeg_graph(document, validation, **options)
        windowed = build_ffmpeg_graph(document, validation, windows=True, **options)
        self.assertEqual(windowed, plain)
        arguments = graph_arguments(windowed, validation)
        self.assertEqual(arguments, graph_arguments(plain, validation))
        self.assertNotIn("-ss", arguments)

    def test_without_gain_graph_and_command_stay_as_today(self) -> None:
        with workspace_tempdir() as workdir:
            shutil.copy2(WORKFLOW_DIR / "plan.json", workdir / "plan.json")
            shutil.copytree(WORKFLOW_DIR / "sources", workdir / "sources", dirs_exist_ok=True)
            document = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
            self.assert_unchanged(workdir, document)

            shutil.copy2(self.source, workdir / "sources" / "main.mp4")
            self.assert_unchanged(workdir, plan([segment(0, 6), segment(7, 9)]))

    def test_global_cap_overflow_keeps_graph_and_command_as_today(self) -> None:
        with workspace_tempdir() as workdir:
            sources = []
            segments = []
            for index in range(MAX_VIDEO_INPUTS + 1):
                shutil.copy2(self.source, workdir / "sources" / f"s{index}.mp4")
                sources.append({"id": f"s{index}", "kind": "video", "path": f"sources/s{index}.mp4"})
                item = segment(20, 20.5)
                item["video"]["source_id"] = f"s{index}"
                item["audio"] = {"mode": "mute", "tracks": []}
                segments.append(item)
            self.assert_unchanged(workdir, plan(segments, sources=sources))

    def test_command_and_graph_share_the_preroll_seek(self) -> None:
        with workspace_tempdir() as workdir:
            shutil.copy2(self.source, workdir / "sources" / "main.mp4")
            document, validation = write_plan(
                workdir, plan([segment(0, 2), segment(10, 12), segment(20, 21)])
            )
            graph = build_ffmpeg_graph(
                document, validation, windows=True, canvas_width=160, canvas_height=90
            )
            arguments = graph_arguments(graph, validation)
        # A janela que começa em 0 não faz seek; as outras recuam o pre-roll de áudio.
        self.assertEqual([arguments[i + 1] for i, item in enumerate(arguments) if item == "-ss"], ["9.75", "19.75"])
        window_reads = [arguments[i + 1] for i, item in enumerate(arguments) if item == "-t"][:3]
        self.assertEqual(window_reads, ["2.5", "2.75", "1.75"])
        # O deslocamento do grafo é o mesmo seek: o corte de 10 s começa 0,25 s dentro da janela.
        for expected in ("trim=start=0:end=2,", "atrim=start=0:end=2,",
                         "trim=start=0.25:end=2.25,", "atrim=start=0.25:end=2.25,",
                         "trim=start=0.25:end=1.25,", "atrim=start=0.25:end=1.25,"):
            self.assertIn(expected, graph.filter_complex)

    def test_windowed_graph_decodes_the_same_frames_and_audio(self) -> None:
        video_layer = {
            "type": "video", "source_id": "main", "in_s": 25, "out_s": 27,
            "start_s": 0.5, "end_s": 2.5, "x": 0.5, "y": 0.5, "width": 0.4, "height": 0.4,
            "fit": "contain",
        }
        scenarios: dict[str, dict[str, Any]] = {
            "inicio_e_fim_da_fonte": plan([segment(0, 2), segment(27, 30)]),
            "trechos_adjacentes": plan([segment(10, 12), segment(12, 14), segment(24, 25)]),
            "fronteiras_de_quadro_e_keyframe": plan(
                [segment(10.08, 11.2), segment(20.04, 21.36), segment(25, 26)]
            ),
            "camada_de_video_e_trilha_da_mesma_fonte": plan(
                [segment(1, 4, [track(20, 23)], mode="replace"), segment(8, 9)],
                layers=[video_layer],
            ),
            "trilha_com_loop": plan(
                [segment(5, 9, [track(15, 16, loop=True)], mode="replace"), segment(22, 23)]
            ),
            "cortes_j_e_l": plan(
                [segment(3, 5), segment(12, 14, [track(11.7, 14.3)]), segment(22, 24)],
                cuts=[
                    {"after_segment": 1, "type": "j", "duration_s": 0.3},
                    {"after_segment": 2, "type": "l", "duration_s": 0.3},
                ],
            ),
        }
        with workspace_tempdir() as workdir:
            shutil.copy2(self.source, workdir / "sources" / "main.mp4")
            for name, raw_document in scenarios.items():
                with self.subTest(name):
                    document, validation = write_plan(workdir, raw_document)
                    options = {"canvas_width": 160, "canvas_height": 90}
                    plain = build_ffmpeg_graph(document, validation, **options)
                    windowed = build_ffmpeg_graph(document, validation, windows=True, **options)
                    # O cenário só prova algo se a fonte realmente ganhou janelas com `-ss`.
                    self.assertTrue(any(window is not None for window in windowed.input_windows))
                    self.assertIn("-ss", graph_arguments(windowed, validation))
                    expected = decoded_framemd5(workdir, plain, validation)
                    self.assertIn("\n1,", expected)
                    self.assertEqual(decoded_framemd5(workdir, windowed, validation), expected)


# --- Áudio idêntico com e sem janelas, inclusive em keyframe de vídeo (T6 da phase 5) -------


AUDIO_SOURCES: dict[str, dict[str, Any]] = {
    # Keyframes a cada 1,0 s e sem B-frames: o `dts` do keyframe é o próprio instante, o caso do defeito.
    "gop25_sem_b_48khz": {"gop": 25, "b_frames": False, "sample_rate": 48000},
    # 44,1 kHz também passa pelo `aresample` do grafo depois do recorte.
    "gop25_sem_b_44khz": {"gop": 25, "b_frames": False, "sample_rate": 44100},
    # A fonte da phase 4 (GOP 12 com B-frames): continua idêntica.
    "gop12_com_b": {"gop": 12},
}

AUDIO_VIDEO_LAYER = {
    "type": "video", "source_id": "main", "in_s": 25, "out_s": 27,
    "start_s": 0.5, "end_s": 2.5, "x": 0.5, "y": 0.5, "width": 0.4, "height": 0.4,
    "fit": "contain",
}

AUDIO_SCENARIOS: dict[str, dict[str, Any]] = {
    # O caso reproduzido pelo revisor: cortes de 3 s exatamente nos keyframes de 1,0 s e 8,0 s.
    "cortes_em_1_0_e_8_0": plan([segment(1, 4), segment(8, 11)]),
    "cortes_nao_alinhados": plan([segment(1.04, 2.2), segment(9.5, 10.3), segment(17.7, 19.1)]),
    # A 0,01 s e 0,02 s do keyframe: dentro das primeiras 1.024 amostras que o decoder AAC descarta após o seek.
    "logo_apos_o_keyframe": plan([segment(3.01, 4.0), segment(11.02, 12.0), segment(20, 21)]),
    "cinco_janelas_na_mesma_fonte": plan(
        [segment(1, 2), segment(5, 6), segment(9.01, 10), segment(14, 15.5), segment(21.02, 22)]
    ),
    "janela_no_inicio_da_fonte": plan([segment(0, 2), segment(10, 11)]),
    "janela_perto_do_inicio_da_fonte": plan([segment(0.1, 1.5), segment(12, 13)]),
    # GOP 12 tem keyframes a cada 0,48 s; os cortes abaixo caem neles nessa fonte.
    "cortes_em_keyframes_do_gop_12": plan([segment(0.96, 2.4), segment(4.8, 6.24)]),
    "trilha_com_in_e_out_proprios": plan([segment(1, 4, [track(20, 23)], mode="replace"), segment(8, 9)]),
    "trilha_com_loop": plan([segment(5, 9, [track(15, 16, loop=True)], mode="replace"), segment(22, 23)]),
    "cortes_j_e_l": plan(
        [segment(3, 5), segment(12, 14, [track(11.7, 14.3)]), segment(22, 24)],
        cuts=[
            {"after_segment": 1, "type": "j", "duration_s": 0.3},
            {"after_segment": 2, "type": "l", "duration_s": 0.3},
        ],
    ),
    "camada_de_video_e_trilha_da_mesma_fonte": plan(
        [segment(1, 4, [track(20, 23)], mode="replace"), segment(8, 9)],
        layers=[AUDIO_VIDEO_LAYER],
    ),
}


class WindowedAudioPcmTests(unittest.TestCase):
    """O PCM renderizado com janelas coincide amostra a amostra com o renderizado sem janelas.

    Causa do defeito: após o seek de entrada o decoder AAC descarta o primeiro quadro (1.024
    amostras, medido com `ffprobe -show_frames`, sem o recorte do CLI) e o demuxer começa o áudio
    no quadro do keyframe de vídeo; sem B-frames o keyframe cai no próprio corte e o início do
    áudio some, e o `asetpts=PTS-STARTPTS` do grafo esconde a lacuna ao adiantar a trilha. A
    janela precisa recuar o `-ss` antes do primeiro uso.
    """

    sources: dict[str, Path]
    _cache: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._cache = PROJECT_ROOT / f"tmp-decode-windows-pcm-{uuid4().hex}"
        cls._cache.mkdir()
        cls.sources = {}
        for name, options in AUDIO_SOURCES.items():
            cls.sources[name] = cls._cache / f"{name}.mp4"
            create_gop_source(cls.sources[name], **options)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._cache, ignore_errors=True)

    def render_preview_outputs(self, workdir: Path, *, windowed: bool) -> dict[str, Any]:
        """Renderiza a preview real, com ou sem janelas, e devolve envelope `decode`, PCM e `framemd5`."""

        if windowed:
            result = render_preview(workdir)
        else:
            with windows_disabled():
                result = render_preview(workdir)
        output = workdir / "preview.mp4"
        return {
            "decode": result["decode"],
            "pcm": output_pcm(output),
            "framemd5": output_framemd5(output),
        }

    def assert_same_pcm(self, windowed: bytes, plain: bytes) -> None:
        """Compara os PCM e, na divergência, informa a contagem de amostras e a primeira que difere."""

        self.assertEqual(len(windowed) // 4, len(plain) // 4, "amostras por canal com e sem janelas")
        if windowed != plain:
            first = next(index for index, pair in enumerate(zip(windowed, plain)) if pair[0] != pair[1])
            self.fail(f"o PCM com janelas diverge do sem janelas a partir da amostra {first // 4}")

    def test_pcm_and_frames_are_identical_with_and_without_windows(self) -> None:
        with workspace_tempdir() as workdir:
            for source_name, source in self.sources.items():
                shutil.copy2(source, workdir / "sources" / "main.mp4")
                for scenario, raw_document in AUDIO_SCENARIOS.items():
                    with self.subTest(source=source_name, scenario=scenario):
                        document, validation = write_plan(workdir, raw_document)
                        options = {"canvas_width": 160, "canvas_height": 90}
                        plain_graph = build_ffmpeg_graph(document, validation, **options)
                        windowed_graph = build_ffmpeg_graph(document, validation, windows=True, **options)
                        # O cenário só prova algo se a fonte ganhou janelas com leitura própria.
                        self.assertTrue(any(window is not None for window in windowed_graph.input_windows))

                        # Render real: o que a preview publica, comparado amostra a amostra.
                        plain = self.render_preview_outputs(workdir, windowed=False)
                        windowed = self.render_preview_outputs(workdir, windowed=True)
                        self.assertFalse(plain["decode"]["windowed"])
                        self.assertTrue(windowed["decode"]["windowed"])
                        self.assertGreater(len(plain["pcm"]), 0)
                        self.assert_same_pcm(windowed["pcm"], plain["pcm"])
                        self.assertEqual(windowed["framemd5"], plain["framemd5"])

                        # Grafo: vídeo e áudio decodificados antes do encoder, sem perdas.
                        expected = decoded_framemd5(workdir, plain_graph, validation)
                        self.assertIn("\n1,", expected)
                        self.assertEqual(decoded_framemd5(workdir, windowed_graph, validation), expected)

    def test_full_with_windows_keeps_the_pcm_of_a_cut_on_a_keyframe(self) -> None:
        with workspace_tempdir() as workdir:
            shutil.copy2(self.sources["gop25_sem_b_48khz"], workdir / "sources" / "main.mp4")
            write_plan(workdir, AUDIO_SCENARIOS["cortes_em_1_0_e_8_0"])
            render_preview(workdir)
            approve_workdir(workdir)
            with windows_disabled():
                self.assertFalse(render_full(workdir)["decode"]["windowed"])
            plain = output_pcm(workdir / "full.mp4")
            self.assertTrue(render_full(workdir)["decode"]["windowed"])
            windowed = output_pcm(workdir / "full.mp4")
        # Seis segundos de áudio a 48 kHz: o defeito entregava 286.976 amostras em vez de 288.000.
        self.assertEqual(len(plain) // 4, 6 * 48000)
        self.assert_same_pcm(windowed, plain)


# --- Render real com janelas por padrão (T2) ------------------------------------------------


WINDOWED_PLAN = plan([segment(2, 4), segment(12, 14), segment(24, 26)])
"""Plano que usa menos da metade da fonte, com ganho de leitura para janelar `main`."""


class WindowedRenderTests(unittest.TestCase):
    """Preview e full reais com janelas, envelope `data.decode`, fallback e regra da GPU."""

    source: Path
    _cache: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls._cache = PROJECT_ROOT / f"tmp-decode-windows-render-{uuid4().hex}"
        cls._cache.mkdir()
        cls.source = cls._cache / "main.mp4"
        create_gop_source(cls.source)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._cache, ignore_errors=True)

    @contextmanager
    def job(self, document: dict[str, Any] = WINDOWED_PLAN) -> Iterator[Path]:
        """Trabalho com a fonte sintética e o plano dado já gravado."""

        with workspace_tempdir() as workdir:
            shutil.copy2(self.source, workdir / "sources" / "main.mp4")
            (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")
            yield workdir

    def test_preview_and_full_with_windows_publish_the_same_frames_and_audio(self) -> None:
        unwindowed = {"windowed": False, "inputs": 1, "sources": []}
        with self.job() as workdir:
            outputs: dict[str, dict[str, str]] = {"preview": {}, "full": {}}
            with windows_disabled():
                self.assertEqual(render_preview(workdir)["decode"], unwindowed)
            outputs["preview"]["plain"] = output_framemd5(workdir / "preview.mp4")
            self.assertEqual(
                render_preview(workdir)["decode"],
                {"windowed": True, "inputs": 3, "sources": ["main"]},
            )
            outputs["preview"]["windowed"] = output_framemd5(workdir / "preview.mp4")

            # A aprovação vincula a preview vigente; as duas fulls partem da mesma aprovação.
            approve_workdir(workdir)
            with windows_disabled():
                self.assertEqual(render_full(workdir)["decode"], unwindowed)
            outputs["full"]["plain"] = output_framemd5(workdir / "full.mp4")
            self.assertTrue(render_full(workdir)["decode"]["windowed"])
            outputs["full"]["windowed"] = output_framemd5(workdir / "full.mp4")

            for artifact, hashes in outputs.items():
                with self.subTest(artifact):
                    self.assertIn("\n1,", hashes["plain"])
                    self.assertEqual(hashes["windowed"], hashes["plain"])
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_without_gain_envelope_reports_unwindowed_single_input(self) -> None:
        with self.job(plan([segment(0, 6), segment(7, 9)])) as workdir:
            result = render_preview(workdir)
        self.assertEqual(result["decode"], {"windowed": False, "inputs": 1, "sources": []})

    def run_failing(
        self,
        workdir: Path,
        should_fail: Any,
        failure: Exception | None,
        accel: str = "none",
    ) -> tuple[dict[str, Any] | None, list[list[str]]]:
        """Renderiza a preview com um FFmpeg que falha quando `should_fail(argumentos)` é verdadeiro.

        Devolve o resultado (ou `None` se o render falhou) e os argumentos de cada render tentado,
        sempre conferindo que `.tmp` ficou vazio e que o erro público não traz o stderr.
        """

        calls: list[list[str]] = []
        original = process.run_tool
        filter_option = dependencies.ffmpeg_filter_option()

        def fake_run_tool(tool: str, arguments: list[str], *, timeout_seconds: float):
            if tool == "ffmpeg" and filter_option in arguments:
                calls.append(list(arguments))
                # O script de filtro de cada tentativa existe enquanto ela roda.
                self.assertTrue(Path(arguments[arguments.index(filter_option) + 1]).is_file())
                if should_fail(arguments):
                    if failure is not None:
                        raise failure
                    return subprocess.CompletedProcess(arguments, 1, "", "falha simulada")
            return original(tool, arguments, timeout_seconds=timeout_seconds)

        with patch(
            "editor.render.preview.dependencies.gpu_acceleration_available", return_value=True
        ), patch("editor.render.preview.process.run_tool", side_effect=fake_run_tool):
            try:
                result = render_preview(workdir, accel=accel)
            except CliError as error:
                self.assertEqual(error.code, "RENDER_FAILED")
                self.assertNotIn("falha simulada", error.message)
                result = None
        self.assertEqual(list((workdir / ".tmp").iterdir()), [])
        return result, calls

    def test_failure_with_windows_retries_without_windows_and_timeout_does_not(self) -> None:
        with self.subTest("falha com janelas repete sem janelas"), self.job() as workdir:
            result, calls = self.run_failing(workdir, lambda arguments: "-ss" in arguments, None)
            assert result is not None
            self.assertEqual(result["decode"], {"windowed": False, "inputs": 1, "sources": []})
            self.assertEqual(["-ss" in call for call in calls], [True, False])
            self.assertTrue((workdir / "preview.mp4").is_file())

        with self.subTest("gpu: pedido com janelas, cpu com janelas, cpu sem janelas"), self.job() as workdir:
            result, calls = self.run_failing(
                workdir, lambda arguments: "-ss" in arguments, None, accel="gpu"
            )
            assert result is not None
            self.assertEqual(result["video"]["accel"], "cpu")
            self.assertFalse(result["decode"]["windowed"])
            self.assertEqual(
                [("-ss" in call, dependencies.GPU_VIDEO_ENCODER in call) for call in calls],
                [(True, True), (True, False), (False, False)],
            )

        with self.subTest("timeout não repete"), self.job() as workdir:
            previous = b"preview-vigente"
            (workdir / "preview.mp4").write_bytes(previous)
            result, calls = self.run_failing(
                workdir,
                lambda arguments: True,
                process.ProcessExecutionError("ffmpeg", timed_out=True),
            )
            self.assertIsNone(result)
            self.assertEqual(len(calls), 1)
            self.assertEqual((workdir / "preview.mp4").read_bytes(), previous)

        with self.subTest("sem janelas não há tentativa duplicada"), self.job(plan([segment(0, 6)])) as workdir:
            result, calls = self.run_failing(workdir, lambda arguments: True, None)
            self.assertIsNone(result)
            self.assertEqual(len(calls), 1)

    def test_gpu_decode_counts_video_inputs_not_sources(self) -> None:
        with self.job() as workdir:
            for count in (GPU_MAX_VIDEO_INPUTS, GPU_MAX_VIDEO_INPUTS + 1):
                with self.subTest(inputs=count):
                    document, validation = write_plan(
                        workdir, plan([segment(index * 6, index * 6 + 1) for index in range(count)])
                    )
                    graph = build_ffmpeg_graph(
                        document, validation, canvas_width=160, canvas_height=90, windows=True
                    )
                    # Uma única fonte, mas uma entrada de vídeo por janela.
                    self.assertEqual(graph.input_source_ids, ("main",) * count)
                    arguments = graph_arguments(graph, validation, accel="gpu")
                    expected_hwaccel = count if count <= GPU_MAX_VIDEO_INPUTS else 0
                    self.assertEqual(arguments.count("-hwaccel"), expected_hwaccel)
                    self.assertEqual(
                        arguments[arguments.index("-c:v") + 1], dependencies.GPU_VIDEO_ENCODER
                    )


if __name__ == "__main__":
    unittest.main()
