"""Testes das janelas de decode: regras do planejador, comando inalterado sem ganho e equivalência real.

A equivalência compara o `framemd5` do vídeo e do áudio produzidos pelo mesmo grafo com e
sem janelas, numa fonte H.264/AAC de GOP curto gerada pelo próprio teste.
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
from uuid import uuid4

from editor import dependencies
from editor.plan.validation import load_validated_plan
from editor.render.ffmpeg_graph import FfmpegGraph, build_ffmpeg_graph
from editor.render.preview import _build_ffmpeg_arguments
from editor.render.windows import (
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


def create_gop_source(path: Path) -> None:
    """Gera fonte H.264 de GOP 12 com B-frames e AAC estéreo cujo sinal nunca se repete.

    O PNS do encoder AAC nativo fica desligado: ele sorteia ruído com estado do decoder que
    depende de todos os quadros anteriores, e nenhum seek preserva esse estado.
    """

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=25:duration={SOURCE_DURATION_S:g}",
            "-f", "lavfi", "-i",
            f"aevalsrc=0.4*sin(2*PI*(220+15*t)*t)|0.4*sin(2*PI*(330+9*t)*t):s=48000:d={SOURCE_DURATION_S:g}",
            "-c:v", "libx264", "-g", "12", "-pix_fmt", "yuv420p",
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


def graph_arguments(graph: FfmpegGraph, validation: dict[str, Any]) -> list[str]:
    """Argumentos completos do render para o grafo, sem executar o FFmpeg."""

    return _build_ffmpeg_arguments(
        graph,
        validation["sources"],
        Path("out.mp4"),
        filter_script=Path("filter.txt"),
        duration_seconds=float(validation["timeline"]["duration_seconds"]),
        frame_rate=25.0,
    )


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


if __name__ == "__main__":
    unittest.main()
