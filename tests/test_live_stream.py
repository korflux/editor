"""Testes do render ao vivo (T1 da phase 7): stream da montagem a partir de qualquer instante e quadro de scrub.

A prova central compara, em `full`, o vídeo cru (`framemd5`) e o PCM (`s16le`) do grafo do stream a
partir de T com os do grafo da full nos mesmos tempos, antes de qualquer encoder: quadro a quadro e
amostra a amostra. Os cenários cobrem T no início, no meio de um segmento, num corte J/L, num
`reframe`, num `motion`, numa camada de texto (com sombra e contorno), de imagem e de vídeo, e numa
trilha com `loop`. Depois vêm as dimensões por resolução, o proxy só nas frações, o stream
codificado (H.264/AAC em MP4 fragmentado), o quadro de scrub e a janela de tempo do planejador.
O desempenho (C3) é medido por `scripts/bench_render.py --scenario live`.

O stream da fonte inteira (T5, modo Fonte) tem a própria seção, `SourceStreamTests`: o canvas da fonte (proporção
mantida, par, teto de UHD), o primeiro quadro igual ao quadro n da fonte decodificada inteira (`framemd5`),
o MP4 fragmentado H.264/AAC do instante pedido ao fim da fonte, o encoder igual ao do stream da montagem, o
original em `full` e o proxy válido nas frações, a fonte só de áudio sem imagem e as recusas na borda.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import re
from dataclasses import replace
from pathlib import Path
import shutil
import struct
import subprocess
import unittest
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from editor import dependencies
from editor.contracts import CliError
from editor.plan.validation import load_validated_plan
from editor.render import live
from editor.render.ffmpeg_graph import _GraphInputs, _used_source_ids, build_ffmpeg_graph
from editor.render.preview import _number
from editor.render.proxy import build_proxies
from editor.render.windows import DecodeWindow, TimeWindow, plan_decode_windows, source_intervals
from tests.support import remove_tree
from tests.test_decode_windows import create_gop_source, graph_arguments, plan, segment, track
from tests.test_proxy_build import make_source


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRAME_RATE = 25
SAMPLE_RATE = 48000


# --- Infraestrutura ------------------------------------------------------------------------------


@contextmanager
def live_workdir() -> Iterator[Path]:
    """Trabalho descartável no workspace, com `sources/` e `.tmp`, removido ao final."""

    path = PROJECT_ROOT / f"tmp-live-{uuid4().hex}"
    (path / "sources").mkdir(parents=True)
    (path / ".tmp").mkdir()
    try:
        yield path
    finally:
        remove_tree(path)


def load(workdir: Path, document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Grava o plano e o devolve validado, como o servidor da T3 o entregará ao núcleo."""

    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")
    return load_validated_plan(workdir)


def run_ffmpeg(arguments: list[str]) -> bytes:
    """Executa o FFmpeg local e devolve o stdout; falha com o stderr para diagnóstico do teste."""

    result = subprocess.run(["ffmpeg", *arguments], capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout


def lossless_outputs(graph: Any, workdir: Path, frame_rate: float) -> tuple[list[str], Path, Path]:
    """Saídas sem perdas do grafo, no lugar do encoder de transmissão: `framemd5` do vídeo (na taxa da full) e PCM `s16le`.

    Toda saída do grafo precisa de destino, então vídeo e áudio vão no mesmo comando, cada um para um
    arquivo. Devolve os argumentos de saída e os dois caminhos (o do PCM só é usado com áudio).
    """

    stem = uuid4().hex
    frames_path, pcm_path = workdir / f"{stem}.md5", workdir / f"{stem}.pcm"
    arguments = ["-map", f"[{graph.video_label}]", "-r", _number(frame_rate), "-f", "framemd5", str(frames_path)]
    if graph.audio_label:
        arguments += ["-map", f"[{graph.audio_label}]", "-f", "s16le", str(pcm_path)]
    return arguments, frames_path, pcm_path


def read_outputs(graph: Any, frames_path: Path, pcm_path: Path) -> "Outputs":
    """Lê o que `lossless_outputs` gravou: os quadros e, com áudio, o PCM."""

    return Outputs(
        parse_framemd5(frames_path.read_text()),
        pcm_path.read_bytes() if graph.audio_label else None,
    )


def parse_framemd5(text: str, stream: int = 0) -> list[tuple[int, str, str]]:
    """Lê `(pts, tamanho, md5)` de cada quadro de um stream do `framemd5` (as linhas `#` são cabeçalho)."""

    frames = []
    for line in text.splitlines():
        if line and not line.startswith("#"):
            index, _, pts, _, size, digest = (item.strip() for item in line.split(","))
            if int(index) == stream:
                frames.append((int(pts), size, digest))
    return frames


class Outputs:
    """Vídeo cru e PCM de um grafo: lista `(pts, tamanho, md5)` por quadro e bytes `s16le` (ou `None` sem áudio)."""

    def __init__(self, frames: list[tuple[int, str, str]], pcm: bytes | None) -> None:
        self.frames = frames
        self.pcm = pcm


def full_outputs(workdir: Path, document: dict[str, Any], validation: dict[str, Any]) -> Outputs:
    """Saídas do grafo da full (canvas inteiro, janelas de decode como no render), independente do núcleo ao vivo."""

    graph = build_ffmpeg_graph(
        document,
        validation,
        canvas_width=int(validation["profile"]["full_width"]),
        canvas_height=int(validation["profile"]["full_height"]),
        windows=True,
    )
    filter_option = dependencies.ffmpeg_filter_option()
    command = graph_arguments(graph, validation)
    inputs = command[: command.index(filter_option)]
    script = workdir / f"filter-{uuid4().hex}.txt"
    # Em bytes, como o render: o modo texto do Windows trocaria a quebra de linha do texto da camada por CRLF.
    script.write_bytes(graph.filter_complex.encode("utf-8"))
    outputs, frames_path, pcm_path = lossless_outputs(graph, workdir, live.output_frame_rate(document, validation))
    run_ffmpeg([*inputs, filter_option, str(script), *outputs])
    return read_outputs(graph, frames_path, pcm_path)


def live_outputs(
    workdir: Path,
    document: dict[str, Any],
    validation: dict[str, Any],
    start_s: float,
    resolution: str = "full",
) -> tuple[live.LiveRender, Outputs]:
    """Saídas do grafo do stream a partir de T: o mesmo comando do stream com a saída trocada por `framemd5` e PCM."""

    state = live.prepare_live(workdir, document, validation, start_s, resolution)
    outputs, frames_path, pcm_path = lossless_outputs(state.graph, workdir, state.frame_rate)
    with live.filter_script(workdir, state) as script:
        run_ffmpeg(live.live_arguments(state, script, outputs))
    return state, read_outputs(state.graph, frames_path, pcm_path)


def top_level_boxes(data: bytes) -> list[tuple[str, bytes]]:
    """Caixas de nível superior de um MP4, na ordem em que aparecem, como `(tipo, conteúdo)`."""

    boxes, offset = [], 0
    while offset + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[offset : offset + 8])
        end = offset + size if size >= 8 else len(data)
        boxes.append((kind.decode("latin-1"), data[offset + 8 : end]))
        offset = end
    return boxes


def probe(path: Path) -> dict[str, Any]:
    """Streams e formato de um arquivo, pelo FFprobe, para conferir codecs e dimensões do stream."""

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height:format=format_name,duration", "-of", "json", str(path)],
        capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def create_ntsc_source(path: Path) -> None:
    """Fonte H.264/AAC de 30 s a 29,97 quadros por segundo (grade de `n / fps` com decimais infinitos)."""

    run_ffmpeg(
        ["-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30000/1001:duration=30",
         "-f", "lavfi", "-i", "aevalsrc=0.4*sin(2*PI*(220+15*t)*t)|0.4*sin(2*PI*(330+9*t)*t):s=48000:d=30",
         "-c:v", "libx264", "-g", "15", "-pix_fmt", "yuv420p", "-c:a", "aac", "-aac_pns", "0", "-b:a", "128k",
         "-movflags", "+faststart", "-y", str(path)]
    )


# --- Cenários: documento e instantes de T (todos na grade de 25 quadros) --------------------------


KEYFRAMES = [
    {"t_s": 0, "x": 0.3, "y": 0.4, "zoom": 1.0, "interpolation": "ease"},
    {"t_s": 2, "x": 0.7, "y": 0.6, "zoom": 2.0, "interpolation": "linear"},
    {"t_s": 4, "x": 0.4, "y": 0.5, "zoom": 1.5, "interpolation": "ease"},
]
"""Keyframes de `reframe` com `ease` e `linear` misturados e zoom animado: o caso mais sensível ao relógio."""

TEXT_LAYER = {
    "type": "text", "start_s": 1.0, "end_s": 9.0, "x": 0.05, "y": 0.5, "width": 0.9, "height": 0.3,
    "text": "Montagem ao vivo", "font": "Arial", "size_px": 20, "color": "#FFFFFF", "align": "left",
    "wrap": True,
    "shadow": {"offset_x_px": 2, "offset_y_px": 2, "blur_px": 3, "color": "#000000", "opacity": 0.6},
    "outline": {"width_px": 1, "color": "#000000"},
}

LATER_TEXT_LAYER = {**TEXT_LAYER, "text": "Depois de T", "start_s": 10.0, "end_s": 11.5, "y": 0.1}


def video_segment(in_s: float, out_s: float, **extras: Any) -> dict[str, Any]:
    """Segmento com o áudio da própria fonte e `fit` explícito; `extras` entram no objeto `video`."""

    item = segment(in_s, out_s)
    item["video"] = {**item["video"], "fit": "crop", **extras}
    return item


SCENARIOS: dict[str, tuple[dict[str, Any], tuple[float, ...]]] = {
    "segmentos": (
        plan([segment(2, 6), segment(10, 14), segment(20, 24)]),
        (0, 1.0, 6.04, 8.0, 11.96),
    ),
    # Trilha da segunda cena cobre a janela de áudio com J antes e L depois (1,7 a 4,3 s na montagem).
    "cortes_j_e_l": (
        plan(
            [segment(3, 5), segment(12, 14, [track(11.7, 14.3)]), segment(22, 24)],
            cuts=[
                {"after_segment": 1, "type": "j", "duration_s": 0.3},
                {"after_segment": 2, "type": "l", "duration_s": 0.3},
            ],
        ),
        (1.8, 2.0, 3.0, 4.12),
    ),
    "reframe": (
        plan([video_segment(2, 6, reframe={"keyframes": KEYFRAMES}), video_segment(10, 14)]),
        (0, 1.52, 3.0, 4.0, 5.2),
    ),
    "motion": (
        plan([
            video_segment(
                2, 6,
                motion={"from": {"x": 0.5, "y": 0.5, "zoom": 1.0}, "to": {"x": 0.7, "y": 0.4, "zoom": 1.8}},
            ),
            segment(10, 12),
        ]),
        (1.0, 3.2),
    ),
    "texto": (
        plan([segment(2, 6), segment(10, 14), segment(20, 24)], layers=[TEXT_LAYER, LATER_TEXT_LAYER]),
        (0, 0.96, 1.0, 5.0, 9.0, 10.56),
    ),
    "camada_de_video": (
        plan(
            [segment(2, 6), segment(10, 14)],
            layers=[{
                "type": "video", "source_id": "main", "in_s": 22, "out_s": 26,
                "start_s": 2.0, "end_s": 6.0, "x": 0.5, "y": 0.5, "width": 0.4, "height": 0.4,
                "fit": "contain",
            }],
        ),
        (0, 1.0, 3.0, 4.0),
    ),
    "camada_de_imagem": (
        plan(
            [segment(2, 6), segment(10, 14)],
            layers=[{
                "type": "image", "source_id": "logo", "start_s": 1.0, "end_s": 7.0,
                "x": 0.7, "y": 0.05, "width": 0.25, "height": 0.25, "fit": "contain",
            }],
            sources=[
                {"id": "main", "kind": "video", "path": "sources/main.mp4"},
                {"id": "logo", "kind": "image", "path": "sources/logo.png"},
            ],
        ),
        (0, 1.0, 4.0, 6.0),
    ),
    "trilha_com_loop": (
        plan([segment(5, 9, [track(15, 16, loop=True)], mode="replace"), segment(22, 24)]),
        (0, 1.0, 2.52, 4.0),
    ),
}


class LiveStreamTestCase(unittest.TestCase):
    """Fonte H.264/AAC sintética de 30 s (GOP 12, com B-frames) e um logo PNG, compartilhados pela classe."""

    cache: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.cache = PROJECT_ROOT / f"tmp-live-source-{uuid4().hex}"
        cls.cache.mkdir()
        create_gop_source(cls.cache / "main.mp4")
        run_ffmpeg(
            ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=1:duration=1",
             "-frames:v", "1", "-y", str(cls.cache / "logo.png")]
        )

    @classmethod
    def tearDownClass(cls) -> None:
        remove_tree(cls.cache)

    def install_sources(self, workdir: Path) -> None:
        """Copia a fonte e o logo para `sources/` do trabalho."""

        shutil.copy2(self.cache / "main.mp4", workdir / "sources" / "main.mp4")
        shutil.copy2(self.cache / "logo.png", workdir / "sources" / "logo.png")


# --- Equivalência com a full ---------------------------------------------------------------------


class FullEquivalenceTests(LiveStreamTestCase):
    """Em `full`, o vídeo cru e o PCM do grafo a partir de T são os da full nos mesmos tempos."""

    def assert_matches_full(
        self, state: live.LiveRender, produced: Outputs, reference: Outputs
    ) -> None:
        """Compara quadro a quadro (md5 e tamanho; o `pts` recomeça em 0) e amostra a amostra com o recorte da full."""

        first_frame = round(state.start_s * state.frame_rate)
        expected = reference.frames[first_frame:]
        self.assertTrue(produced.frames, "o stream não produziu quadros")
        self.assertEqual(
            [(pts, size, digest) for pts, size, digest in produced.frames],
            [(index, size, digest) for index, (_, size, digest) in enumerate(expected)],
            "quadros do stream a partir de T diferem dos da full",
        )
        if reference.pcm is None:
            self.assertIsNone(produced.pcm)
            return
        first_sample = round(state.start_s * SAMPLE_RATE) * 4
        expected_pcm = reference.pcm[first_sample:]
        if produced.pcm is None:
            # Sem grupo de áudio depois de T: a full também não tem áudio ali.
            self.assertEqual(expected_pcm, b"")
            return
        self.assertEqual(len(produced.pcm), len(expected_pcm), "amostras do stream e da full")
        if produced.pcm != expected_pcm:
            first = next(i for i, pair in enumerate(zip(produced.pcm, expected_pcm)) if pair[0] != pair[1])
            self.fail(f"o PCM do stream diverge da full a partir da amostra {first // 4} (T={state.start_s})")

    def test_stream_from_any_instant_equals_the_full_frame_by_frame_and_sample_by_sample(self) -> None:
        with live_workdir() as workdir:
            self.install_sources(workdir)
            for name, (document, instants) in SCENARIOS.items():
                document, validation = load(workdir, document)
                reference = full_outputs(workdir, document, validation)
                self.assertGreater(len(reference.frames), 1, name)
                for start in instants:
                    with self.subTest(cenario=name, t=start):
                        state, produced = live_outputs(workdir, document, validation, start)
                        self.assertEqual(state.start_s, start)
                        self.assertEqual((state.width, state.height), (160, 90))
                        self.assert_matches_full(state, produced, reference)

    def test_reordered_source_preserves_first_seek_and_full_video_and_pcm(self) -> None:
        """Reuso contínuo desde zero não anula o seek inicial; recortes longos e camadas continuam inteiros."""

        with live_workdir() as workdir:
            self.install_sources(workdir)
            document = plan([segment(22, 24), segment(0, 22), segment(22, 26), segment(26, 30)])
            document, validation = load(workdir, document)
            unchanged = build_ffmpeg_graph(document, validation, canvas_width=160, canvas_height=90, windows=True)
            self.assertEqual(unchanged.input_windows, (None,), "a política da preview/full continua sem seek")
            reference = full_outputs(workdir, document, validation)
            for start in (0.0, 1.0, 2.0, 24.0):
                with self.subTest(t=start):
                    state, produced = live_outputs(workdir, document, validation, start)
                    self.assert_matches_full(state, produced, reference)
                    if start < 2:
                        windows = [span for span in state.graph.input_windows if span is not None]
                        self.assertEqual([span.start_s for span in windows], [0.0, 22.0 + start])
                        self.assertEqual(windows[-1].end_s, 30.0, "o uso posterior mais longo cabe na janela")
                        self.assertEqual(windows[-1].seek_s, 21.75 + start)
            # Camada e trilha em loop têm recortes próprios, mas usam a mesma política de entradas.
            document = plan(
                [segment(22, 24, [track(20, 24, loop=True)]), segment(0, 22), segment(22, 30)],
                layers=[{"type": "video", "source_id": "main", "in_s": 26, "out_s": 30,
                         "start_s": 0, "end_s": 4, "x": 0, "y": 0, "width": 0.5, "height": 0.5,
                         "fit": "contain"}],
            )
            document, validation = load(workdir, document)
            reference = full_outputs(workdir, document, validation)
            for start in (0.0, 1.0):
                with self.subTest(loop_e_camada=start):
                    state, produced = live_outputs(workdir, document, validation, start)
                    self.assert_matches_full(state, produced, reference)
                    self.assertEqual(state.graph.input_windows[-1], DecodeWindow(20.0, 30.0))

    def test_video_on_a_29_97_fps_grid_equals_the_full(self) -> None:
        """T cai em `n / 29,97003`, de decimais infinitos: os quadros seguem idênticos aos da full.

        O PCM não entra: T fora da grade de amostras (1.601,6 por quadro) põe os grupos de áudio seguintes
        a até uma amostra (21 microssegundos) da posição que têm na full.
        """

        kf = {"keyframes": KEYFRAMES}
        scenarios = {
            "segmentos": (plan([segment(2, 6), segment(10, 14), segment(20, 24)]), (1.0, 6.04, 8.0)),
            "reframe": (plan([video_segment(2, 6, reframe=kf), video_segment(10, 14)]), (1.52, 3.0, 5.2)),
            "texto": (
                plan([segment(2, 6), segment(10, 14)], layers=[{**TEXT_LAYER, "end_s": 7.5}]),
                (0.96, 5.0),
            ),
        }
        with live_workdir() as workdir:
            create_ntsc_source(workdir / "sources" / "main.mp4")
            for name, (document, instants) in scenarios.items():
                document, validation = load(workdir, document)
                self.assertAlmostEqual(live.output_frame_rate(document, validation), 30000 / 1001, places=3)
                reference = full_outputs(workdir, document, validation)
                for start in instants:
                    with self.subTest(cenario=name, t=start):
                        state, produced = live_outputs(workdir, document, validation, start)
                        first_frame = round(state.start_s * state.frame_rate)
                        self.assertEqual(
                            [(index, size, digest) for index, (_, size, digest) in enumerate(produced.frames)],
                            [(index, size, digest) for index, (_, size, digest) in enumerate(reference.frames[first_frame:])],
                        )

    def test_time_zero_builds_the_exact_graph_of_the_full(self) -> None:
        with live_workdir() as workdir:
            self.install_sources(workdir)
            for name, (document, _) in SCENARIOS.items():
                with self.subTest(cenario=name):
                    document, validation = load(workdir, document)
                    state = live.prepare_live(workdir, document, validation, 0, "full")
                    full = build_ffmpeg_graph(document, validation, canvas_width=160, canvas_height=90, windows=True)
                    self.assertEqual(state.graph, full)

    def test_cached_image_matches_the_previous_looping_decoder_with_alpha_and_windows(self) -> None:
        """A otimização mantém cada pixel do grafo anterior, inclusive bordas, alfa e o trecho ao vivo."""

        with live_workdir() as workdir:
            self.install_sources(workdir)
            # Raster com regiões transparentes e semitransparentes, mais de uma camada e ambos os fits.
            run_ffmpeg(["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=red@0.4:s=64x48,format=rgba,drawbox=x=0:y=0:w=20:h=20:color=blue@0:t=fill:replace=1",
                        "-frames:v", "1", "-y", str(workdir / "sources" / "logo.png")])
            document, validation = load(workdir, SCENARIOS["camada_de_imagem"][0])
            document["layers"] = [
                {**document["layers"][0], "start_s": 1.04, "end_s": 3.08, "fit": "contain"},
                {**document["layers"][0], "start_s": 5.12, "end_s": 7.04, "fit": "crop"},
            ]
            document, validation = load(workdir, document)
            for width, height in ((160, 90), (80, 44)):
                for windows in (False, True):
                    with self.subTest(canvas=(width, height), windows=windows):
                        graph = build_ffmpeg_graph(document, validation, canvas_width=width,
                                                   canvas_height=height, windows=windows)
                        # Reconstrói exatamente o ramo anterior: image2 em loop, scale por quadro,
                        # trim pela timeline inteira. O resto do grafo e as entradas não mudam.
                        old_text = graph.filter_complex.replace(
                            "trim=end_frame=1,setpts=PTS-STARTPTS", "trim=duration=8,setpts=PTS-STARTPTS")
                        old_text = re.sub(r",loop=loop=-1:size=1:start=0,trim=duration=8,setpts=PTS-STARTPTS", "", old_text)
                        previous = replace(graph, filter_complex=old_text)
                        comparisons = []
                        for candidate, legacy in ((previous, True), (graph, False)):
                            command = graph_arguments(candidate, validation)
                            option = dependencies.ffmpeg_filter_option()
                            inputs = command[:command.index(option)]
                            if legacy:
                                position = inputs.index("-framerate")
                                inputs[position:position] = ["-loop", "1"]
                            script = workdir / "comparison-filter.txt"
                            script.write_bytes(candidate.filter_complex.encode("utf-8"))
                            output, frames, pcm = lossless_outputs(candidate, workdir, 25)
                            run_ffmpeg([*inputs, option, str(script), *output])
                            comparisons.append(read_outputs(candidate, frames, pcm))
                        self.assertEqual(comparisons[0].frames, comparisons[1].frames)
                        self.assertEqual(comparisons[0].pcm, comparisons[1].pcm)
                        resolution = "full" if width == 160 else "1/2"
                        for start in (0, 1.04, 2, 3.08, 5.12, 6, 7.04):
                            state, produced = live_outputs(workdir, document, validation, start, resolution)
                            self.assert_matches_full(state, produced, comparisons[0])

    def test_a_late_start_reads_only_what_feeds_the_rest_of_the_timeline(self) -> None:
        document = SCENARIOS["segmentos"][0]
        with live_workdir() as workdir:
            self.install_sources(workdir)
            document, validation = load(workdir, document)
            state = live.prepare_live(workdir, document, validation, 8.0, "full")
            with live.filter_script(workdir, state) as script:
                arguments = live.stream_arguments(state, script)
        seeks = [float(arguments[i + 1]) for i, item in enumerate(arguments) if item == "-ss"]
        # Só o último segmento (fonte de 20 a 24 s) sobra: nada da fonte antes dele é aberto ou lido.
        self.assertEqual(state.graph.input_source_ids, ("main",))
        self.assertEqual(seeks, [19.75])
        self.assertNotIn("trim=start=2:end=6", state.graph.filter_complex)
        self.assertNotIn("atrim=start=10", state.graph.filter_complex)

    def test_a_tail_without_audio_streams_without_audio(self) -> None:
        mute_tail = segment(10, 14, [], mode="mute")
        with live_workdir() as workdir:
            self.install_sources(workdir)
            document, validation = load(workdir, plan([segment(2, 6), mute_tail]))
            state, produced = live_outputs(workdir, document, validation, 5.0)
            self.assertIsNone(state.graph.audio_label)
            self.assertIsNone(produced.pcm)
            self.assertIn("-an", live.stream_output_arguments(state))
            # Antes do trecho mudo ainda há áudio.
            before, _ = live_outputs(workdir, document, validation, 1.0)
            self.assertIsNotNone(before.graph.audio_label)


# --- Resolução, proxy, stream codificado e scrub -------------------------------------------------


class ResolutionAndStreamTests(LiveStreamTestCase):
    """Dimensões por resolução, entradas por resolução, stream H.264/AAC fragmentado e quadro de scrub."""

    def test_resolutions_divide_the_canvas_and_round_down_to_even(self) -> None:
        with live_workdir() as workdir:
            self.install_sources(workdir)
            document, validation = load(workdir, SCENARIOS["segmentos"][0])
            # 160x90: 90 / 2 = 45 vira 44, 160 / 3 = 53 vira 52 e 90 / 3 = 30, 90 / 4 = 22.
            expected = {"full": (160, 90), "1/2": (80, 44), "1/3": (52, 30), "1/4": (40, 22)}
            self.assertEqual(tuple(expected), live.RESOLUTIONS)
            for resolution, dimensions in expected.items():
                with self.subTest(resolucao=resolution):
                    state = live.prepare_live(workdir, document, validation, 1.0, resolution)
                    self.assertEqual((state.width, state.height), dimensions)
                    with live.filter_script(workdir, state) as script:
                        stream = run_ffmpeg(live.stream_arguments(state, script))
                    path = workdir / f"{resolution.replace('/', '-')}.mp4"
                    path.write_bytes(stream)
                    streams = probe(path)["streams"]
                    video = next(item for item in streams if item["codec_type"] == "video")
                    self.assertEqual((video["width"], video["height"]), dimensions)

    def test_the_encoded_stream_is_fragmented_h264_aac_with_the_canvas_dimensions(self) -> None:
        with live_workdir() as workdir:
            self.install_sources(workdir)
            document, validation = load(workdir, SCENARIOS["segmentos"][0])
            state = live.prepare_live(workdir, document, validation, 6.04, "full")
            with live.filter_script(workdir, state) as script:
                arguments = live.stream_arguments(state, script)
                prefix = live.live_arguments(state, script, [])
                stream = run_ffmpeg(arguments)
            # O stream é o mesmo comando da prova, só com o encoder de transmissão na saída.
            self.assertEqual(arguments[: len(prefix)], prefix)
            boxes = top_level_boxes(stream)
            kinds_in_order = [kind for kind, _ in boxes]
            self.assertEqual(kinds_in_order[:2], ["ftyp", "moov"])
            self.assertGreater(kinds_in_order.count("moof"), 1, "sem fragmentos por quadro-chave")
            # `empty_moov` + `mvex`: o cabeçalho sai sem esperar amostras e o resto vem em fragmentos.
            self.assertIn(b"mvex", boxes[1][1])
            path = workdir / "stream.mp4"
            path.write_bytes(stream)
            info = probe(path)
            kinds = {item["codec_type"]: item for item in info["streams"]}
            self.assertEqual(kinds["video"]["codec_name"], "h264")
            self.assertEqual(kinds["audio"]["codec_name"], "aac")
            self.assertEqual((kinds["video"]["width"], kinds["video"]["height"]), (160, 90))
            self.assertAlmostEqual(float(info["format"]["duration"]), 12.0 - 6.04, delta=0.1)
            # O encoder de transmissão é constante própria e não toca nos presets da full.
            self.assertEqual(arguments[arguments.index("-preset") + 1], live.LIVE_VIDEO_PRESET)
            self.assertEqual(arguments[arguments.index("-crf") + 1], str(live.LIVE_VIDEO_CRF))

    def test_fractions_read_the_valid_proxy_and_full_never_does(self) -> None:
        with live_workdir() as workdir:
            make_source(workdir / "sources" / "main.mp4", "1920x1080")
            document, validation = load(workdir, plan([segment(0, 1), segment(1, 2)]))
            original_path = str(validation["sources"][0]["path"])

            def read_paths(resolution: str) -> tuple[live.LiveRender, list[str]]:
                state = live.prepare_live(workdir, document, validation, 0, resolution)
                with live.filter_script(workdir, state) as script:
                    arguments = live.stream_arguments(state, script)
                return state, [arguments[i + 1] for i, item in enumerate(arguments) if item == "-i"]

            # Sem proxy construído todas as resoluções leem o original.
            for resolution in live.RESOLUTIONS:
                state, paths = read_paths(resolution)
                self.assertEqual(state.proxy_sources, ())
                self.assertEqual(set(paths), {original_path})

            created = build_proxies(workdir)["proxies"]
            self.assertEqual([item["status"] for item in created], ["created"])
            document, validation = load_validated_plan(workdir)
            for resolution in live.RESOLUTIONS:
                with self.subTest(resolucao=resolution):
                    state, paths = read_paths(resolution)
                    if resolution == "full":
                        self.assertEqual(state.proxy_sources, ())
                        self.assertEqual(set(paths), {original_path})
                    else:
                        self.assertEqual(state.proxy_sources, ("main",))
                        self.assertEqual(len(paths), 1)
                        self.assertEqual(Path(paths[0]).parent.name, "proxy")
            # 1920x1080: 960x540, 640x360 e 480x270.
            self.assertEqual(live.live_canvas(validation, "1/2"), (960, 540))
            self.assertEqual(live.live_canvas(validation, "1/3"), (640, 360))
            self.assertEqual(live.live_canvas(validation, "1/4"), (480, 270))
            # O stream lido do proxy sai completo, nas dimensões da fração.
            state = live.prepare_live(workdir, document, validation, 1.0, "1/2")
            with live.filter_script(workdir, state) as script:
                (workdir / "proxied.mp4").write_bytes(run_ffmpeg(live.stream_arguments(state, script)))
            streams = probe(workdir / "proxied.mp4")["streams"]
            video = next(item for item in streams if item["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (960, 540))

    def test_scrub_frame_equals_the_raw_first_frame_of_the_stream_graph(self) -> None:
        with live_workdir() as workdir:
            self.install_sources(workdir)
            for name in ("segmentos", "reframe", "texto", "camada_de_imagem"):
                document, instants = SCENARIOS[name]
                document, validation = load(workdir, document)
                for start in instants[:4]:
                    with self.subTest(cenario=name, t=start):
                        digests = []
                        for audio in (True, False):  # o stream leva áudio; o scrub, não
                            state = live.prepare_live(workdir, document, validation, start, "full", audio=audio)
                            tail = ["-map", f"[{state.graph.video_label}]", "-r", str(FRAME_RATE), "-frames:v", "1"]
                            if state.graph.audio_label:  # toda saída do grafo precisa de destino
                                tail += ["-map", f"[{state.graph.audio_label}]"]
                            tail += ["-f", "framemd5", "-"]
                            with live.filter_script(workdir, state) as script:
                                digests.append(parse_framemd5(
                                    run_ffmpeg(live.live_arguments(state, script, tail)).decode()
                                ))
                        self.assertEqual(len(digests[0]), 1)
                        self.assertEqual(digests[0], digests[1])

    def test_extract_frame_returns_a_jpeg_of_the_requested_resolution(self) -> None:
        with live_workdir() as workdir:
            self.install_sources(workdir)
            document, validation = load(workdir, SCENARIOS["texto"][0])
            for resolution, dimensions in (("full", (160, 90)), ("1/2", (80, 44))):
                with self.subTest(resolucao=resolution):
                    image = live.extract_frame(workdir, document, validation, 5.0, resolution)
                    path = workdir / "scrub.jpg"
                    path.write_bytes(image)
                    info = probe(path)
                    self.assertEqual(info["streams"][0]["codec_name"], "mjpeg")
                    self.assertEqual((info["streams"][0]["width"], info["streams"][0]["height"]), dimensions)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [], "o script de filtro deve ser removido")

    def test_image_layer_repeats_one_scaled_frame_without_looping_or_seeking_the_input(self) -> None:
        """Imagem estática mantém o relógio do ao vivo sem releitura nem escala por quadro."""
        with live_workdir() as workdir:
            self.install_sources(workdir)
            document, validation = load(workdir, SCENARIOS["camada_de_imagem"][0])
            for start in (0.0, 4.0):
                with self.subTest(t=start):
                    state = live.prepare_live(workdir, document, validation, start, "full")
                    with live.filter_script(workdir, state) as script:
                        arguments = live.stream_arguments(state, script)
                    self.assertNotIn("-loop", arguments)
                    image = arguments.index("-framerate")
                    self.assertEqual(arguments[image + 1 : image + 3], ["25", "-i"])
                    self.assertIn("trim=end_frame=1,setpts=PTS-STARTPTS,format=rgba,scale=", state.graph.filter_complex)
                    self.assertIn("loop=loop=-1:size=1:start=0", state.graph.filter_complex)

    def test_scrub_failure_is_a_safe_error_and_leaves_no_temporary_file(self) -> None:
        with live_workdir() as workdir:
            self.install_sources(workdir)
            document, validation = load(workdir, SCENARIOS["segmentos"][0])
            (workdir / "sources" / "main.mp4").unlink()
            with self.assertRaises(CliError) as caught:
                live.extract_frame(workdir, document, validation, 1.0, "full")
            self.assertEqual(caught.exception.code, "FRAME_FAILED")
            self.assertNotIn(str(workdir), caught.exception.message)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_invalid_requests_are_refused_without_running_ffmpeg(self) -> None:
        with live_workdir() as workdir:
            self.install_sources(workdir)
            document, validation = load(workdir, SCENARIOS["segmentos"][0])
            cases: list[tuple[str, Any, str]] = [
                ("resolução fora da allowlist", 1.0, "1/5"),
                ("resolução não textual", 1.0, 2),
                ("T negativo", -0.04, "full"),
                ("T depois do fim", 12.5, "full"),
                ("T não finito", float("nan"), "full"),
                ("T booleano", True, "full"),
                ("T textual", "1.0", "full"),
            ]
            for name, start, resolution in cases:
                with self.subTest(name), self.assertRaises(CliError) as caught:
                    live.prepare_live(workdir, document, validation, start, resolution)
                self.assertEqual(caught.exception.code, "ARGUMENT_INVALID")
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])


# --- Taxa de quadros e colagem de T na grade -----------------------------------------------------


class FrameGridTests(unittest.TestCase):
    """`snap_start` e a taxa de saída, sem FFmpeg."""

    def test_snap_start_picks_the_displayed_frame_and_clamps_to_the_last_one(self) -> None:
        self.assertEqual(live.snap_start(1.019, 12.0, 25.0), 1.0)
        self.assertEqual(live.snap_start(1.04, 12.0, 25.0), 1.04)
        self.assertEqual(live.snap_start(0, 12.0, 25.0), 0.0)
        self.assertEqual(live.snap_start(12.0, 12.0, 25.0), 11.96)
        self.assertAlmostEqual(live.snap_start(1.0, 5.0, 30000 / 1001), 29 / (30000 / 1001), places=9)

    def test_output_frame_rate_is_the_first_segment_source_rate_or_25(self) -> None:
        document = {"segments": [{"video": {"source_id": "main"}}]}
        self.assertEqual(live.output_frame_rate(document, {"sources": [{"id": "main", "video_frame_rate": 29.97}]}), 29.97)
        self.assertEqual(
            live.output_frame_rate(document, {"sources": [{"id": "main", "video_frame_rate": None}]}),
            live.DEFAULT_FRAME_RATE,
        )


# --- Janela de tempo do planejador ---------------------------------------------------------------


def contiguous_timeline(durations: list[float]) -> list[dict[str, float]]:
    """Timeline sem cortes J/L: a janela de áudio de cada segmento é a de vídeo."""

    items, start = [], 0.0
    for duration in durations:
        items.append({"start_s": start, "end_s": start + duration, "audio_start_s": start, "audio_end_s": start + duration})
        start += duration
    return items


class TimeWindowPlannerTests(unittest.TestCase):
    """O planejador cobre só o que alimenta `[T, fim]`, com o mesmo avanço que o grafo aplica."""

    DOCUMENT = {
        "segments": [
            {
                "video": {"source_id": "main", "in_s": start, "out_s": start + 2},
                "audio": {"mode": "keep", "tracks": [{"source_id": "main", "in_s": start, "out_s": start + 2}]},
            }
            for start in (0, 10, 20, 40)
        ],
        "layers": [],
    }
    TIMELINE = contiguous_timeline([2, 2, 2, 2])
    RECORDS = {"main": {"id": "main", "kind": "video", "duration_seconds": 100.0}}

    def test_live_first_use_is_temporal_and_windows_cover_bisect_consumers_within_caps(self) -> None:
        """Áudio J e camada podem preceder vídeo; a janela ordenada cobre todo consumidor selecionado."""

        document = {
            "segments": [
                {"video": {"source_id": "other", "in_s": 0, "out_s": 2}, "audio": {"tracks": []}},
                {"video": {"source_id": "main", "in_s": 80, "out_s": 82},
                 "audio": {"tracks": [{"source_id": "main", "in_s": 79, "out_s": 82}]}},
                {"video": {"source_id": "main", "in_s": 0, "out_s": 100}, "audio": {"tracks": []}},
                {"video": {"source_id": "main", "in_s": 80, "out_s": 100}, "audio": {"tracks": []}},
            ], "layers": [],
        }
        timeline = contiguous_timeline([2, 2, 100, 20])
        timeline[1]["audio_start_s"] = 1.0  # J-cut: áudio de main começa antes de seu vídeo.
        for layers, expected in (([], 79.0), ([{"type": "video", "source_id": "main", "in_s": 70,
                                              "out_s": 74, "start_s": 0, "end_s": 4}], 70.0)):
            document["layers"] = layers
            self.assertEqual(plan_decode_windows(document, self.RECORDS, ("main",)), {})
            result = plan_decode_windows(document, self.RECORDS, ("main",), timeline, TimeWindow(0))
            self.assertEqual(result["main"], (DecodeWindow(0, 100), DecodeWindow(expected, 100)))
            inputs = _GraphInputs(("main",), result)
            for in_s, out_s in source_intervals(document, timeline, TimeWindow(0))["main"]:
                index, _ = inputs.locate("main", in_s)
                self.assertGreaterEqual(inputs.windows[index].end_s, out_s)
        # Com 24 fontes não cabe entrada adicional; acima disso conserva o fallback vigente.
        document["layers"] = []
        for count in (23, 24, 25):
            ids = tuple(f"v{i}" for i in range(count))
            records = {source_id: {**self.RECORDS["main"], "id": source_id} for source_id in ids}
            expanded = {"segments": [], "layers": []}
            for source_id in ids:
                for in_s, out_s in ((80, 82), (0, 100)):
                    expanded["segments"].append({"video": {"source_id": source_id, "in_s": in_s, "out_s": out_s},
                                                 "audio": {"tracks": []}})
            timing = contiguous_timeline([2, 100] * count)
            result = plan_decode_windows(expanded, records, ids, timing, TimeWindow(0))
            self.assertTrue(all(len(spans) <= 8 for spans in result.values()))
            if count <= 24:
                self.assertLessEqual(sum(len(result.get(source_id, (None,))) for source_id in ids), 24)
            else:
                self.assertEqual(result, {})
        # O primeiro uso cai numa janela mesclada, mas oito entradas já ocupam o teto por fonte.
        starts = [4, 0, 10, 20, 30, 40, 50, 60, 70]
        sparse = {"segments": [
            {"video": {"source_id": "main", "in_s": start, "out_s": start + 0.5},
             "audio": {"tracks": []}} for start in starts
        ], "layers": []}
        result = plan_decode_windows(sparse, self.RECORDS, ("main",),
                                     contiguous_timeline([0.5] * len(starts)), TimeWindow(0))
        self.assertEqual(len(result["main"]), 8)
        self.assertEqual(result["main"][0].start_s, 0)
        # Diferença dentro do pre-roll não justifica outro decoder com seek idêntico.
        sparse["segments"] = [
            {"video": {"source_id": "main", "in_s": start, "out_s": end}, "audio": {"tracks": []}}
            for start, end in ((0.1, 0.5), (0, 100))
        ]
        result = plan_decode_windows(sparse, self.RECORDS, ("main",), contiguous_timeline([0.4, 100]), TimeWindow(0))
        self.assertEqual(result, {})

    def test_time_window_skip_rules(self) -> None:
        window = TimeWindow(3.0)
        self.assertIsNone(window.skip(0.0, 3.0))  # acabou em T
        self.assertIsNone(window.skip(0.0, 3.0000005))  # acabou a menos de 1 microssegundo de T
        self.assertEqual(window.skip(2.0, 4.0), 1.0)  # contém T
        self.assertEqual(window.skip(3.0, 5.0), 0.0)  # começa em T
        self.assertEqual(window.skip(6.0, 8.0), 0.0)  # começa depois
        self.assertEqual(TimeWindow(0.0).skip(0.0, 2.0), 0.0)  # T = 0 é a identidade
        self.assertFalse(TimeWindow(0.0).active)

    def test_windows_start_at_the_advanced_cut_and_never_before_t(self) -> None:
        # T = 3,0 s cai 1 s dentro do segundo segmento (montagem de 2 a 4 s, fonte de 10 a 12 s): o
        # primeiro intervalo lido começa em 11 s, o primeiro segmento nem entra.
        windows = plan_decode_windows(self.DOCUMENT, self.RECORDS, ("main",), self.TIMELINE, TimeWindow(3.0))
        self.assertEqual(
            windows["main"],
            (DecodeWindow(11.0, 12.5), DecodeWindow(20.0, 22.5), DecodeWindow(40.0, 42.5)),
        )
        # Sem a janela de tempo o primeiro intervalo é o do começo da fonte, como sempre.
        self.assertEqual(source_intervals(self.DOCUMENT)["main"][0], (0.0, 2.0))

    def test_any_read_reduction_justifies_windows_when_the_timeline_starts_late(self) -> None:
        # Um único trecho longo no fim da fonte: a leitura desde 0 só cai ~17% a partir de T = 100 s,
        # abaixo dos 20% do render completo, mas o ao vivo não pode decodificar o começo à toa.
        document = {
            "segments": [{
                "video": {"source_id": "main", "in_s": 0, "out_s": 600},
                "audio": {"mode": "mute", "tracks": []},
            }],
            "layers": [],
        }
        records = {"main": {"id": "main", "kind": "video", "duration_seconds": 600.0}}
        timeline = [{"start_s": 0.0, "end_s": 600.0, "audio_start_s": 0.0, "audio_end_s": 600.0}]
        self.assertEqual(plan_decode_windows(document, records, ("main",)), {})
        windows = plan_decode_windows(document, records, ("main",), timeline, TimeWindow(100.0))
        self.assertEqual(windows["main"], (DecodeWindow(100.0, 600.0),))

    def test_a_source_used_only_before_t_is_not_opened(self) -> None:
        document = {
            "segments": [
                {"video": {"source_id": "early", "in_s": 0, "out_s": 2}, "audio": {"mode": "mute", "tracks": []}},
                {"video": {"source_id": "late", "in_s": 5, "out_s": 7}, "audio": {"mode": "mute", "tracks": []}},
            ],
            "layers": [],
        }
        timeline = contiguous_timeline([2, 2])
        records = {
            "early": {"id": "early", "kind": "video", "duration_seconds": 10.0},
            "late": {"id": "late", "kind": "video", "duration_seconds": 10.0},
        }
        self.assertEqual(_used_source_ids(document), ("early", "late"))
        self.assertEqual(_used_source_ids(document, timeline, TimeWindow(2.0)), ("late",))
        windows = plan_decode_windows(document, records, ("late",), timeline, TimeWindow(2.0))
        self.assertEqual(set(windows), {"late"})

    def test_window_without_timeline_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            source_intervals(self.DOCUMENT, None, TimeWindow(3.0))


# --- Fonte inteira (T5): o stream do modo Fonte -------------------------------------------------


class SourceStreamTests(LiveStreamTestCase):
    """Stream e quadro da fonte inteira: canvas da própria fonte, original ou proxy, T, fonte só de áudio e recusas."""

    def install_voice(self, workdir: Path) -> None:
        """Fonte só de áudio: WAV PCM estéreo de 10 s a 48 kHz em `sources/voice.wav`."""

        run_ffmpeg(
            ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=10:sample_rate=48000",
             "-ac", "2", "-c:a", "pcm_s16le", "-y", str(workdir / "sources" / "voice.wav")]
        )

    def main_plan(self, workdir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        """Plano de dois segmentos sobre `main` (a fonte de 30 s) e o logo, já validado."""

        self.install_sources(workdir)
        return load(workdir, SCENARIOS["camada_de_imagem"][0])

    def test_source_canvas_keeps_the_aspect_divides_to_even_and_caps_the_long_side(self) -> None:
        def canvas(width: int, height: int, resolution: str) -> tuple[int, int]:
            return live.source_canvas({"video_width": width, "video_height": height}, resolution)

        for resolution, expected in {"full": (160, 90), "1/2": (80, 44), "1/3": (52, 30), "1/4": (40, 22)}.items():
            with self.subTest(resolucao=resolution):
                self.assertEqual(canvas(160, 90, resolution), expected)
        self.assertEqual(canvas(161, 91, "full"), (160, 90), "lado ímpar vira par para baixo")
        self.assertEqual(canvas(3840, 2160, "full"), (3840, 2160))
        self.assertEqual(canvas(3840, 2160, "1/2"), (1920, 1080))
        # Acima do teto de UHD a fonte é reduzida sem cortar nem mudar a proporção, em qualquer orientação.
        self.assertEqual(canvas(7680, 4320, "full"), (3840, 2160))
        self.assertEqual(canvas(4320, 7680, "full"), (2160, 3840))
        self.assertEqual(canvas(8192, 4320, "1/2"), (1920, 1012))  # 4320 * 3840 // 8192 = 2025, metade 1012
        self.assertEqual(canvas(2, 2, "1/4"), (2, 2), "mínimo de 2 pixels")
        with self.assertRaises(CliError) as caught:
            canvas(160, 90, "1/5")
        self.assertEqual(caught.exception.code, "ARGUMENT_INVALID")

    def test_the_source_stream_starts_at_the_requested_frame_and_is_fragmented_h264_aac(self) -> None:
        with live_workdir() as workdir:
            _, validation = self.main_plan(workdir)
            reference = parse_framemd5(
                run_ffmpeg(["-hide_banner", "-loglevel", "error", "-i", str(workdir / "sources" / "main.mp4"),
                            "-map", "0:v:0", "-f", "framemd5", "-"]).decode()
            )
            for start, first_frame in ((0, 0), (10.0, 250), (17.04, 426), (17.05, 426), (29.9, 747)):
                with self.subTest(t=start):
                    state = live.prepare_source_live(workdir, validation, "main", start, "full")
                    self.assertEqual((state.start_s, state.proxy, state.has_video), (first_frame / 25, False, True))
                    self.assertEqual((state.width, state.height), (160, 90))
                    # O primeiro quadro do stream é o quadro n da fonte inteira (antes do encoder, em `framemd5`).
                    frame_arguments = live.source_frame_arguments(state)
                    raw = frame_arguments[: frame_arguments.index("-frames:v")] + ["-frames:v", "1", "-f", "framemd5", "-"]
                    self.assertEqual(
                        [digest for _, _, digest in parse_framemd5(run_ffmpeg(raw).decode())],
                        [reference[first_frame][2]],
                    )
                    stream = run_ffmpeg(live.source_stream_arguments(state))
                    kinds = [kind for kind, _ in top_level_boxes(stream)]
                    self.assertEqual(kinds[:2], ["ftyp", "moov"])
                    self.assertGreaterEqual(kinds.count("moof"), 1)
                    path = workdir / "stream.mp4"
                    path.write_bytes(stream)
                    info = probe(path)
                    streams = {item["codec_type"]: item for item in info["streams"]}
                    self.assertEqual((streams["video"]["codec_name"], streams["audio"]["codec_name"]), ("h264", "aac"))
                    self.assertEqual((streams["video"]["width"], streams["video"]["height"]), (160, 90))
                    # A fonte vai inteira, do instante pedido ao fim (30 s), sem o corte de `-t` da montagem.
                    self.assertAlmostEqual(float(info["format"]["duration"]), 30.0 - state.start_s, delta=0.15)

    def test_the_stream_reuses_the_encoder_of_the_assembly_stream(self) -> None:
        with live_workdir() as workdir:
            document, validation = self.main_plan(workdir)
            assembly = live.prepare_live(workdir, document, validation, 1.0, "full")
            with live.filter_script(workdir, assembly) as script:
                shared = live.stream_arguments(assembly, script)
            source = live.source_stream_arguments(live.prepare_source_live(workdir, validation, "main", 1.0, "full"))
            for option in ("-c:v", "-preset", "-tune", "-crf", "-pix_fmt", "-c:a", "-b:a", "-ar", "-movflags", "-g"):
                self.assertEqual(source[source.index(option) + 1], shared[shared.index(option) + 1], option)
            self.assertEqual(source[-3:], ["-f", "mp4", "pipe:1"])

    def test_the_source_reads_the_original_in_full_and_the_valid_proxy_in_fractions(self) -> None:
        with live_workdir() as workdir:
            make_source(workdir / "sources" / "main.mp4", "1920x1080")
            _, validation = load(workdir, plan([segment(0, 1), segment(1, 2)]))
            original = str(validation["sources"][0]["path"])

            def read_path(resolution: str) -> tuple[live.SourceLive, str]:
                state = live.prepare_source_live(workdir, validation, "main", 0.5, resolution)
                arguments = live.source_stream_arguments(state)
                return state, arguments[arguments.index("-i") + 1]

            for resolution in live.RESOLUTIONS:
                state, path = read_path(resolution)
                self.assertEqual((state.proxy, path), (False, original), "sem proxy toda resolução lê o original")
            created = build_proxies(workdir)["proxies"]
            self.assertEqual([item["status"] for item in created], ["created"])
            _, validation = load_validated_plan(workdir)
            expected = {"full": (1920, 1080), "1/2": (960, 540), "1/3": (640, 360), "1/4": (480, 270)}
            for resolution, dimensions in expected.items():
                with self.subTest(resolucao=resolution):
                    state, path = read_path(resolution)
                    self.assertEqual((state.width, state.height), dimensions)
                    if resolution == "full":
                        self.assertEqual((state.proxy, path), (False, original))
                    else:
                        self.assertTrue(state.proxy)
                        self.assertEqual(Path(path).parent.name, "proxy")
                    (workdir / "o.mp4").write_bytes(run_ffmpeg(live.source_stream_arguments(state)))
                    video = next(item for item in probe(workdir / "o.mp4")["streams"] if item["codec_type"] == "video")
                    self.assertEqual((video["width"], video["height"]), dimensions)

    def test_an_audio_only_source_streams_aac_without_image(self) -> None:
        with live_workdir() as workdir:
            self.install_sources(workdir)
            self.install_voice(workdir)
            document = plan(
                [segment(2, 6, [track(0, 4, source_id="voice")], mode="replace")],
                sources=[
                    {"id": "main", "kind": "video", "path": "sources/main.mp4"},
                    {"id": "voice", "kind": "audio", "path": "sources/voice.wav"},
                ],
            )
            _, validation = load(workdir, document)
            state = live.prepare_source_live(workdir, validation, "voice", 2.0, "1/2")
            self.assertEqual((state.has_video, state.width, state.height, state.kind), (False, None, None, "audio"))
            self.assertEqual((state.frame_rate, state.start_s, state.proxy), (live.DEFAULT_FRAME_RATE, 2.0, False))
            arguments = live.source_stream_arguments(state)
            self.assertNotIn("0:v:0", arguments)
            self.assertNotIn("-vf", arguments)
            stream = run_ffmpeg(arguments)
            (workdir / "voice.mp4").write_bytes(stream)
            info = probe(workdir / "voice.mp4")
            self.assertEqual([(item["codec_type"], item["codec_name"]) for item in info["streams"]], [("audio", "aac")])
            self.assertAlmostEqual(float(info["format"]["duration"]), 8.0, delta=0.15)
            self.assertGreater(len(top_level_boxes(stream)), 2)
            # Sem imagem não há quadro de scrub, e a resolução inválida continua recusada.
            with self.assertRaises(CliError) as caught:
                live.source_frame_arguments(state)
            self.assertEqual(caught.exception.code, "ARGUMENT_INVALID")
            with self.assertRaises(CliError) as caught:
                live.prepare_source_live(workdir, validation, "voice", 2.0, "1/5")
            self.assertEqual(caught.exception.code, "ARGUMENT_INVALID")

    def test_the_source_frame_is_a_jpeg_with_the_canvas_of_the_source(self) -> None:
        with live_workdir() as workdir:
            _, validation = self.main_plan(workdir)
            for resolution, dimensions in (("full", (160, 90)), ("1/2", (80, 44)), ("1/4", (40, 22))):
                with self.subTest(resolucao=resolution):
                    state = live.prepare_source_live(workdir, validation, "main", 12.5, resolution)
                    image = run_ffmpeg(live.source_frame_arguments(state))
                    self.assertTrue(image.startswith(b"\xff\xd8"))
                    (workdir / "frame.jpg").write_bytes(image)
                    video = probe(workdir / "frame.jpg")["streams"][0]
                    self.assertEqual((video["codec_name"], video["width"], video["height"]), ("mjpeg", *dimensions))

    def test_invalid_source_requests_are_refused_and_a_missing_file_is_source_invalid(self) -> None:
        with live_workdir() as workdir:
            _, validation = self.main_plan(workdir)
            cases: list[tuple[str, Any, Any, str]] = [
                ("fonte fora do plano", "outra", 1.0, "full"),
                ("fonte de imagem", "logo", 1.0, "full"),
                ("id não textual", 7, 1.0, "full"),
                ("resolução fora da allowlist", "main", 1.0, "1/5"),
                ("T negativo", "main", -0.04, "full"),
                ("T depois do fim", "main", 30.5, "full"),
                ("T não finito", "main", float("inf"), "full"),
                ("T booleano", "main", True, "full"),
            ]
            for name, source_id, start, resolution in cases:
                with self.subTest(name), self.assertRaises(CliError) as caught:
                    live.prepare_source_live(workdir, validation, source_id, start, resolution)
                self.assertEqual(caught.exception.code, "ARGUMENT_INVALID")
                self.assertNotIn(str(workdir), caught.exception.message)
            (workdir / "sources" / "main.mp4").unlink()
            with self.assertRaises(CliError) as caught:
                live.prepare_source_live(workdir, validation, "main", 1.0, "full")
            self.assertEqual((caught.exception.code, caught.exception.exit_code), ("SOURCE_INVALID", 3))
            self.assertNotIn(str(workdir), caught.exception.message)
            self.assertNotIn("main.mp4", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
