"""Testes das fontes HEVC e MOV nativas (T8 da phase 5, aceite A13, decisão 19).

O editor aceita fontes audiovisuais `.mp4` e `.mov` com vídeo H.264 ou HEVC (Main e Main 10, 8 ou 10
bits, 4:2:0) e áudio AAC, ignora trilhas de dados (`tmcd`, telemetria) e miniatura anexada, e entrega
sempre H.264/AAC `yuv420p` de 8 bits. Este módulo prova isso em quatro frentes:

- contrato de `media.py` com payloads simulados do FFprobe (allowlist, recusas e mensagens fixas);
- recusas com arquivos reais gerados aqui (MKV, dois áudios, dois vídeos, VP9, AV1, MPEG-4,
  legenda e HEVC 4:2:2, 4:4:4 e 12 bits);
- a jornada completa de CLI por fonte HEVC sintética (frames, proxy, preview com e sem proxy,
  aprovação por hash e full), com a cor conferida em pixels;
- janelas de decode (equivalência de `framemd5` com e sem janelas) e decode CUDA.

Nenhuma fonte vive no repositório: todas saem de `ffmpeg -f lavfi` em pasta descartável.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator, Sequence
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies, media
from editor.contracts import EXIT_CODES, CliError
from editor.integrity.approval import approve_workdir
from editor.render.ffmpeg_graph import build_ffmpeg_graph
from editor.render.full import render_full
from editor.render.preview import render_preview
from editor.plan.validation import load_validated_plan
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMAT_NAME_MOV_MP4 = "mov,mp4,m4a,3gp,3g2,mj2"
# Cores conhecidas das quatro caixas de teste (RGB) e as tolerâncias de cada comparação. Um erro de
# faixa (limitada lida como cheia) desloca o branco e o vermelho em mais de 15 níveis, então 8 e 10 o
# separam do ruído de duas gerações de compressão com folga. O erro medido fica abaixo de 5.
BOX_COLORS = ((200, 50, 20), (30, 160, 60), (40, 80, 220), (240, 240, 240))
FULL_TOLERANCE = 8
PREVIEW_TOLERANCE = 10
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# --- Fábricas de fonte sintética -------------------------------------------------------------


def _run_ffmpeg(arguments: Sequence[str]) -> None:
    """Executa o FFmpeg local sem shell e falha o teste com o stderr quando a geração não funciona."""

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *arguments],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))


def _box_filters(width: int, height: int) -> str:
    """Quatro caixas de cor conhecida na faixa superior, sobre o `testsrc2` em movimento.

    O fundo animado faz cada quadro ser distinto (sensível a erro de seek e de janela) e as caixas
    estáticas dão o ponto de leitura de cor, no centro de cada quarto da largura e a 1/8 da altura.
    """

    box_width, box_height = width // 4, height // 4
    return ",".join(
        f"drawbox=x={index * box_width}:y=0:w={box_width}:h={box_height}"
        f":color=0x{red:02X}{green:02X}{blue:02X}:t=fill"
        for index, (red, green, blue) in enumerate(BOX_COLORS)
    )


def make_hevc_source(
    path: Path,
    width: int,
    height: int,
    duration_s: float,
    *,
    pixel_format: str = "yuv420p10le",
    profile: str = "main10",
    thumbnail: bool = True,
) -> None:
    """Gera uma fonte HEVC com áudio AAC, trilhas de dados (timecode) e miniatura MJPEG anexada.

    O `-timecode` do muxer cria as trilhas `data` (`tmcd`). A miniatura vem DEPOIS do vídeo real,
    como nas câmeras, e é um quadro vermelho de 320x180 que denuncia qualquer leitura do stream
    errado (tamanho e cor mudariam). O muxer MOV não grava imagem anexada (só o MP4 grava), então
    nas fontes `.mov` a miniatura é descartada pelo próprio FFmpeg e só as trilhas de dados ficam.
    `keyint=25` dá quadros-chave frequentes para as janelas de decode cortarem fora do início.
    """

    command = [
        "-f", "lavfi", "-i",
        f"testsrc2=size={width}x{height}:rate=30000/1001:duration={duration_s:g},{_box_filters(width, height)}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration_s:g}",
    ]
    mapping = ["-map", "0:v", "-map", "1:a"]
    if thumbnail:
        command += ["-f", "lavfi", "-i", "color=c=red:s=320x180:d=1"]
        mapping += ["-map", "2:v"]
    command += [
        *mapping,
        "-c:v:0", "libx265", "-preset", "ultrafast", "-pix_fmt:0", pixel_format,
        "-profile:v:0", profile, "-x265-params", "log-level=error:keyint=25:min-keyint=25",
        "-c:a", "aac",
    ]
    if thumbnail:
        command += ["-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"]
    command += ["-timecode", "01:02:03:04", str(path)]
    _run_ffmpeg(command)


def make_h264_source(path: Path, width: int = 320, height: int = 180, duration_s: float = 1.0) -> None:
    """Gera uma fonte H.264/AAC simples, base de comparação das fontes HEVC."""

    _run_ffmpeg([
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30:duration={duration_s:g}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration_s:g}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ])


# --- Leitura e conferência de mídia ---------------------------------------------------------


def probe_streams(path: Path) -> list[dict[str, Any]]:
    """Lista todos os streams do arquivo, com `disposition`, como o FFprobe os reporta."""

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["streams"]


def probe_stream(path: Path, selector: str) -> dict[str, Any]:
    """Codec, formato de pixel e dimensões de um stream (`v:0`, `a:0`)."""

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", selector, "-show_entries",
         "stream=codec_name,pix_fmt,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["streams"][0]


def probe_duration(path: Path) -> float:
    """Duração do contêiner em segundos."""

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def box_colors(path: Path, time_s: float, width: int, height: int) -> list[tuple[int, int, int]]:
    """Lê o RGB de 8 bits no centro de cada caixa de cor no quadro de `time_s` do primeiro vídeo.

    Imagens PNG entram pelo mesmo caminho (`time_s` 0): o FFmpeg as devolve como `rgb24`, então a
    leitura vale para o quadro de vídeo e para o PNG extraído.
    """

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{time_s:g}",
         "-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    )
    raw = result.stdout
    if len(raw) != width * height * 3:
        raise AssertionError(f"quadro de {len(raw)} bytes, esperado {width * height * 3}")
    row = height // 8
    return [
        tuple(raw[(row * width + int((index + 0.5) * width / 4)) * 3:][:3])  # type: ignore[misc]
        for index in range(4)
    ]


def assert_colors_close(
    case: unittest.TestCase,
    actual: Sequence[tuple[int, ...]],
    expected: Sequence[tuple[int, ...]],
    tolerance: int,
    label: str = "",
) -> None:
    """Cada canal de cada caixa deve ficar a até `tolerance` níveis do esperado."""

    for index, (found, wanted) in enumerate(zip(actual, expected, strict=True)):
        for channel, (value, target) in enumerate(zip(found, wanted, strict=True)):
            case.assertLessEqual(
                abs(value - target), tolerance,
                f"{label} caixa {index} canal {channel}: {found} esperado {wanted}",
            )


def output_framemd5(path: Path) -> str:
    """`framemd5` do vídeo e do áudio decodificados de um MP4 renderizado."""

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-map", "0:v", "-map", "0:a", "-f", "framemd5", "-"],
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout.decode()


def png_header(path: Path) -> tuple[int, int, int, int]:
    """Largura, altura, profundidade de bits e tipo de cor lidos do bloco IHDR do PNG."""

    data = path.read_bytes()[:26]
    if data[:8] != PNG_SIGNATURE:
        raise AssertionError("assinatura PNG inválida")
    return (
        int.from_bytes(data[16:20], "big"),
        int.from_bytes(data[20:24], "big"),
        data[24],
        data[25],
    )


# --- Execução da CLI e do trabalho ---------------------------------------------------------


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real, como o agente usaria."""

    return subprocess.run(
        [sys.executable, "-B", "-m", "editor", *arguments, "--json"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )


def cli(case: unittest.TestCase, *arguments: str) -> dict[str, Any]:
    """Roda um comando que deve terminar com sucesso e devolve o envelope."""

    result = run_module(*arguments)
    case.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    envelope = json.loads(result.stdout)
    case.assertTrue(envelope["ok"], result.stdout)
    return envelope


@contextmanager
def workspace_tempdir(prefix: str = "tmp-hevc-sources") -> Iterator[Path]:
    """Pasta descartável dentro do workspace, removida ao final."""

    path = PROJECT_ROOT / f"{prefix}-{uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        remove_tree(path)


def segment(source_id: str, in_s: float, out_s: float) -> dict[str, Any]:
    """Trecho de vídeo da fonte com o próprio áudio no mesmo recorte, sem ajuste."""

    return {
        "video": {"source_id": source_id, "in_s": in_s, "out_s": out_s, "fit": "preserve"},
        "audio": {"mode": "keep", "tracks": [{"source_id": source_id, "in_s": in_s, "out_s": out_s, "gain_db": 0}]},
    }


def write_plan(workdir: Path, file_name: str, spans: Sequence[tuple[float, float]]) -> None:
    """Plano schema v2 mínimo (16:9, sem processamento de áudio) sobre `sources/<file_name>`."""

    document = {
        "schema_version": 2,
        "profile": {"aspect_ratio": "16:9"},
        "sources": [{"id": "a", "kind": "video", "path": f"sources/{file_name}"}],
        "segments": [segment("a", in_s, out_s) for in_s, out_s in spans],
        "cuts": [],
        "layers": [],
        "audio_processing": {"normalize_loudness": False},
    }
    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")


def stage_source(fixtures: Path, workdir: Path, file_name: str) -> Path:
    """Copia a fonte compartilhada para `sources/` do trabalho, como numa edição real."""

    (workdir / "sources").mkdir(exist_ok=True)
    return Path(shutil.copy2(fixtures / file_name, workdir / "sources" / file_name))


# Fontes da matriz: (nome, largura, altura, duração, formato de pixel, perfil). As duas de 1080p são
# longas (12 s) para o teste de janelas de decode; a jornada usa só o trecho de 0,5 s a 1,5 s.
SOURCE_SPECS = {
    "hevc10-2688.mov": (2688, 1512, 2.5, "yuv420p10le", "main10"),
    "hevc10-2688.mp4": (2688, 1512, 2.5, "yuv420p10le", "main10"),
    "hevc10-1080.mov": (1920, 1080, 12.0, "yuv420p10le", "main10"),
    "hevc10-1080.mp4": (1920, 1080, 12.0, "yuv420p10le", "main10"),
    "hevc8-1080.mp4": (1920, 1080, 2.5, "yuv420p", "main"),
}


_fixture_dir: Path | None = None


def shared_fixtures() -> Path:
    """Gera uma única vez por módulo as cinco fontes da matriz e confere que têm os streams prometidos.

    As pré-condições garantem que o cenário é o que o teste diz: o vídeo real é o primeiro stream de
    vídeo, há trilhas de dados e, no MP4, a miniatura anexada vem depois dele. A pasta é removida em
    `tearDownModule`.
    """

    global _fixture_dir
    if _fixture_dir is not None:
        return _fixture_dir
    for tool in ("ffmpeg", "ffprobe"):
        if dependencies.resolve_executable(tool) is None:
            raise unittest.SkipTest(f"{tool} local é necessário")
    directory = PROJECT_ROOT / f"tmp-hevc-fixtures-{uuid4().hex}"
    directory.mkdir()
    _fixture_dir = directory
    for name, (width, height, duration, pixel_format, profile) in SOURCE_SPECS.items():
        make_hevc_source(directory / name, width, height, duration, pixel_format=pixel_format, profile=profile)
        streams = probe_streams(directory / name)
        video = [stream for stream in streams if stream["codec_type"] == "video"]
        assert video[0]["codec_name"] == "hevc" and not video[0]["disposition"]["attached_pic"], name
        assert any(stream["codec_type"] == "data" for stream in streams), name
        assert sum(stream["codec_type"] == "audio" for stream in streams) == 1, name
        if name.endswith(".mp4"):
            assert video[-1]["disposition"]["attached_pic"] == 1, name
    return directory


def tearDownModule() -> None:
    """Remove as fontes sintéticas compartilhadas ao fim do módulo."""

    if _fixture_dir is not None:
        remove_tree(_fixture_dir)


class HevcFixtureCase(unittest.TestCase):
    """Base das classes de integração: expõe a pasta das fontes HEVC sintéticas compartilhadas."""

    fixtures: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = shared_fixtures()


# --- Contrato de media.py com payloads simulados -----------------------------------------------


def video_stream(
    codec: str = "hevc", profile: str = "Main 10", pixel_format: str = "yuv420p10le", *, attached: bool = False
) -> dict[str, Any]:
    """Stream de vídeo no formato do FFprobe (`disposition.attached_pic` marca a miniatura)."""

    return {
        "codec_type": "video", "codec_name": codec, "profile": profile, "pix_fmt": pixel_format,
        "width": 1920, "height": 1080, "avg_frame_rate": "30/1", "disposition": {"attached_pic": 1 if attached else 0},
    }


def audio_stream(codec: str = "aac") -> dict[str, Any]:
    """Stream de áudio estéreo de 48 kHz no formato do FFprobe."""

    return {"codec_type": "audio", "codec_name": codec, "sample_rate": "48000", "channels": 2}


def data_stream() -> dict[str, Any]:
    """Trilha de dados (timecode ou telemetria), `codec_type: data`."""

    return {"codec_type": "data", "codec_name": "none", "disposition": {"attached_pic": 0}}


def probe_payload(*streams: dict[str, Any], duration: str = "2.5", format_name: str = FORMAT_NAME_MOV_MP4) -> dict[str, Any]:
    """Resposta do FFprobe com `format` e `streams`, o que `media.py` valida."""

    return {"format": {"format_name": format_name, "duration": duration}, "streams": list(streams)}


class SourceContractTests(unittest.TestCase):
    """Allowlist de contêiner, codec, perfil, formato de pixel e streams, sem depender do FFmpeg."""

    def inspect(self, extension: str, payload: dict[str, Any], **options: Any) -> media.MediaMetadata:
        """Passa o payload simulado pela fronteira pública `inspect_source` com um arquivo real vazio."""

        with workspace_tempdir("tmp-hevc-contract") as directory:
            path = directory / f"fonte{extension}"
            path.write_bytes(b"x")
            completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(payload), stderr="")
            with patch("editor.media.process.run_tool", return_value=completed):
                return media.inspect_source(path, **options)

    def assert_refused(self, extension: str, payload: dict[str, Any], *echo: str, **options: Any) -> CliError:
        """Exige `MEDIA_UNSUPPORTED` da família MEDIA e que a mensagem não repita valores da fonte."""

        with self.assertRaises(CliError) as context:
            self.inspect(extension, payload, **options)
        error = context.exception
        self.assertEqual(error.code, "MEDIA_UNSUPPORTED")
        self.assertEqual(error.exit_code, EXIT_CODES["MEDIA"])
        for token in echo:
            self.assertNotIn(token.lower(), error.message.lower())
        return error

    def test_hevc_main_and_main10_are_accepted_in_mp4_and_mov(self) -> None:
        for extension in (".mp4", ".mov"):
            for profile, pixel_format in (("Main", "yuv420p"), ("Main 10", "yuv420p10le")):
                with self.subTest(extension=extension, profile=profile):
                    metadata = self.inspect(
                        extension,
                        probe_payload(video_stream("hevc", profile, pixel_format), audio_stream()),
                    )
                    self.assertEqual(metadata.video_codec, "hevc")
                    self.assertEqual(metadata.container, extension.removeprefix("."))
                    self.assertEqual(metadata.audio_codec, "aac")

    def test_data_tracks_and_trailing_attached_picture_are_ignored(self) -> None:
        payload = probe_payload(
            video_stream(), audio_stream(), data_stream(), data_stream(),
            video_stream("mjpeg", "Baseline", "yuvj420p", attached=True),
        )
        metadata = self.inspect(".mp4", payload)
        self.assertEqual(metadata.video_codec, "hevc")
        self.assertEqual((metadata.video_width, metadata.video_height), (1920, 1080))

    def test_h264_keeps_the_previous_contract_without_profile_or_pixel_format_limits(self) -> None:
        """A allowlist de perfil e de formato de pixel é só do HEVC: H.264 passa como antes da T8."""

        for profile, pixel_format in (("High", "yuv420p"), ("High 4:4:4 Predictive", "yuv444p")):
            with self.subTest(profile=profile):
                metadata = self.inspect(
                    ".mp4", probe_payload(video_stream("h264", profile, pixel_format), audio_stream())
                )
                self.assertEqual(metadata.video_codec, "h264")

    def test_video_without_audio_is_accepted_when_audio_is_not_required(self) -> None:
        metadata = self.inspect(".mov", probe_payload(video_stream(), data_stream()), require_audio=False)
        self.assertIsNone(metadata.audio_codec)

    def test_hevc_outside_main_and_main10_420_is_refused_with_a_fixed_message(self) -> None:
        cases = (
            ("Main 4:2:2 10", "yuv422p10le"),
            ("Main 4:4:4", "yuv444p"),
            ("Main 4:4:4 10", "yuv444p10le"),
            ("Main 12", "yuv420p12le"),
            ("Rext", "yuv420p10le"),
            ("Main Still Picture", "yuv420p"),
            ("Main 10", "yuv422p10le"),
            ("Main", "yuvj420p"),
            ("", "yuv420p"),
        )
        messages = set()
        for profile, pixel_format in cases:
            with self.subTest(profile=profile, pixel_format=pixel_format):
                error = self.assert_refused(
                    ".mp4",
                    probe_payload(video_stream("hevc", profile, pixel_format), audio_stream()),
                    pixel_format, "4:2:2", "4:4:4", "12",
                )
                messages.add(error.message)
        self.assertEqual(len(messages), 1)

    def test_hevc_without_profile_or_pixel_format_fields_is_refused(self) -> None:
        stream = video_stream()
        del stream["profile"], stream["pix_fmt"]
        self.assert_refused(".mp4", probe_payload(stream, audio_stream()))

    def test_codecs_outside_the_allowlist_are_refused(self) -> None:
        for codec in ("vp9", "av1", "mpeg4", "prores"):
            with self.subTest(codec=codec):
                self.assert_refused(
                    ".mp4", probe_payload(video_stream(codec, "Profile 0", "yuv420p"), audio_stream()), codec
                )

    def test_attached_picture_as_first_video_stream_is_refused(self) -> None:
        payload = probe_payload(
            video_stream("mjpeg", "Baseline", "yuvj420p", attached=True), video_stream(), audio_stream()
        )
        self.assert_refused(".mp4", payload)

    def test_two_real_video_streams_are_refused(self) -> None:
        self.assert_refused(".mov", probe_payload(video_stream(), video_stream(), audio_stream()))

    def test_two_audio_streams_are_refused(self) -> None:
        self.assert_refused(".mp4", probe_payload(video_stream(), audio_stream(), audio_stream()))

    def test_stream_types_outside_video_audio_and_data_are_refused(self) -> None:
        for codec_type in ("subtitle", "attachment", "unknown", ""):
            with self.subTest(codec_type=codec_type):
                payload = probe_payload(
                    video_stream(), audio_stream(), {"codec_type": codec_type, "codec_name": "x"}
                )
                self.assert_refused(".mp4", payload)

    def test_audio_codec_outside_aac_is_refused_in_mov_too(self) -> None:
        self.assert_refused(".mov", probe_payload(video_stream(), audio_stream("pcm_s16le")), "pcm_s16le")

    def test_containers_outside_mp4_and_mov_are_refused_by_extension(self) -> None:
        for extension in (".mkv", ".m4v", ".avi", ".webm", ".ts"):
            with self.subTest(extension=extension):
                self.assert_refused(extension, probe_payload(video_stream(), audio_stream(), format_name="matroska,webm"))

    def test_extension_must_match_the_probed_container(self) -> None:
        """Um `.mov` que o FFprobe identifica como outro contêiner não passa só pela extensão."""

        self.assert_refused(".mov", probe_payload(video_stream(), audio_stream(), format_name="matroska,webm"))

    def test_duration_and_size_ceilings_still_apply_to_hevc_sources(self) -> None:
        payload = probe_payload(video_stream(), audio_stream(), duration=str(media.MAX_SOURCE_DURATION_SECONDS + 1))
        with self.assertRaises(CliError) as duration_error:
            self.inspect(".mov", payload)
        self.assertEqual(duration_error.exception.code, "MEDIA_LIMIT_EXCEEDED")
        self.assertEqual(duration_error.exception.exit_code, EXIT_CODES["MEDIA"])
        with patch.object(media, "MAX_SOURCE_SIZE_BYTES", 0), self.assertRaises(CliError) as size_error:
            self.inspect(".mp4", probe_payload(video_stream(), audio_stream()))
        self.assertEqual(size_error.exception.code, "MEDIA_LIMIT_EXCEEDED")
        self.assertEqual(size_error.exception.exit_code, EXIT_CODES["MEDIA"])


    def test_video_dimension_ceiling_applies_per_side_to_h264_and_hevc(self) -> None:
        """O teto de `MAX_VIDEO_DIMENSION` vale por lado, nas duas orientações e nos dois codecs.

        8192x4320 (8K DCI) e o mesmo quadro em pé passam; um pixel a mais em qualquer lado e o
        cabeçalho forjado 16384x16384 são recusados com `MEDIA_LIMIT_EXCEEDED`, sem repetir as
        dimensões recebidas na mensagem.
        """

        ceiling = media.MAX_VIDEO_DIMENSION
        self.assertEqual(ceiling, 8_192)
        codecs = (("hevc", "Main 10", "yuv420p10le"), ("h264", "High", "yuv420p"))

        def sized(codec: tuple[str, str, str], width: int, height: int) -> dict[str, Any]:
            stream = video_stream(*codec)
            stream.update(width=width, height=height)
            return stream

        for codec in codecs:
            for width, height in ((ceiling, 4_320), (4_320, ceiling)):
                with self.subTest(codec=codec[0], accepted=(width, height)):
                    metadata = self.inspect(".mov", probe_payload(sized(codec, width, height), audio_stream()))
                    self.assertEqual((metadata.video_width, metadata.video_height), (width, height))
            for width, height in ((ceiling + 1, 4_320), (4_320, ceiling + 1), (16_384, 16_384)):
                with self.subTest(codec=codec[0], refused=(width, height)):
                    with self.assertRaises(CliError) as context:
                        self.inspect(".mp4", probe_payload(sized(codec, width, height), audio_stream()))
                    self.assertEqual(context.exception.code, "MEDIA_LIMIT_EXCEEDED")
                    self.assertEqual(context.exception.exit_code, EXIT_CODES["MEDIA"])
                    for token in (str(width), str(height)):
                        self.assertNotIn(token, context.exception.message)

# --- Recusas com arquivos reais ---------------------------------------------------------------


class RefusalRealFilesTests(unittest.TestCase):
    """Fontes reais geradas aqui que o editor deve recusar com `MEDIA_UNSUPPORTED`.

    A miniatura anexada como PRIMEIRO stream de vídeo não aparece aqui: o demuxer MOV/MP4 sempre
    a entrega depois dos streams de mídia (vem do átomo `covr`) e o muxer do FFmpeg não consegue
    escrever outra ordem, então essa recusa é provada só com o payload simulado de `SourceContractTests`.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """Gera uma fonte pequena por motivo de recusa e confere a pré-condição de cada uma."""

        for tool in ("ffmpeg", "ffprobe"):
            if dependencies.resolve_executable(tool) is None:
                raise unittest.SkipTest(f"{tool} local é necessário")
        cls.directory = PROJECT_ROOT / f"tmp-hevc-refusals-{uuid4().hex}"
        cls.directory.mkdir()
        cls.addClassCleanup(remove_tree, cls.directory)
        video = ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1"]
        other_video = ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1:alpha=0"]
        audio = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1"]
        h264 = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
        x265 = ["-c:v", "libx265", "-preset", "ultrafast", "-x265-params", "log-level=error"]
        subtitles = cls.directory / "legenda.srt"
        subtitles.write_text("1\n00:00:00,000 --> 00:00:00,900\nolá\n", encoding="utf-8")
        recipes: dict[str, list[str]] = {
            "matroska.mkv": [*video, *audio, *h264, "-c:a", "aac"],
            "dois-audios.mp4": [*video, *audio, *audio, "-map", "0:v", "-map", "1:a", "-map", "2:a", *h264, "-c:a", "aac"],
            "dois-videos.mp4": [*video, *other_video, *audio, "-map", "0:v", "-map", "1:v", "-map", "2:a", *h264, "-c:a", "aac"],
            "vp9.mp4": [*video, *audio, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "50", "-deadline", "realtime", "-c:a", "aac"],
            "av1.mp4": [*video, *audio, "-c:v", "libaom-av1", "-cpu-used", "8", "-b:v", "0", "-crf", "50", "-c:a", "aac"],
            "mpeg4.mp4": [*video, *audio, "-c:v", "mpeg4", "-c:a", "aac"],
            "legenda.mp4": [
                *video, *audio, "-i", str(subtitles), "-map", "0:v", "-map", "1:a", "-map", "2:s",
                *h264, "-c:a", "aac", "-c:s", "mov_text",
            ],
            "hevc-422.mp4": [*video, *audio, *x265, "-pix_fmt", "yuv422p10le", "-profile:v", "main422-10", "-c:a", "aac"],
            "hevc-444.mp4": [*video, *audio, *x265, "-pix_fmt", "yuv444p", "-profile:v", "main444-8", "-c:a", "aac"],
            "hevc-12bits.mp4": [*video, *audio, *x265, "-pix_fmt", "yuv420p12le", "-profile:v", "main12", "-c:a", "aac"],
        }
        for name, recipe in recipes.items():
            _run_ffmpeg([*recipe, str(cls.directory / name)])
        # Pré-condição de cada caso: o arquivo gerado tem mesmo o defeito que o nome promete, para a
        # recusa não vir de outro motivo (por exemplo, um encoder que caiu em outro formato).
        expected = {
            "dois-audios.mp4": lambda streams: sum(item["codec_type"] == "audio" for item in streams) == 2,
            "dois-videos.mp4": lambda streams: sum(
                item["codec_type"] == "video" and not item["disposition"]["attached_pic"] for item in streams
            ) == 2,
            "vp9.mp4": lambda streams: streams[0]["codec_name"] == "vp9",
            "av1.mp4": lambda streams: streams[0]["codec_name"] == "av1",
            "mpeg4.mp4": lambda streams: streams[0]["codec_name"] == "mpeg4",
            "legenda.mp4": lambda streams: any(item["codec_type"] == "subtitle" for item in streams),
            "hevc-422.mp4": lambda streams: (streams[0]["codec_name"], streams[0]["pix_fmt"]) == ("hevc", "yuv422p10le"),
            "hevc-444.mp4": lambda streams: (streams[0]["codec_name"], streams[0]["pix_fmt"]) == ("hevc", "yuv444p"),
            "hevc-12bits.mp4": lambda streams: (streams[0]["codec_name"], streams[0]["pix_fmt"]) == ("hevc", "yuv420p12le"),
        }
        for name, holds in expected.items():
            assert holds(probe_streams(cls.directory / name)), name

    def test_each_real_file_is_refused_with_a_stable_code_and_no_echo(self) -> None:
        # nome do arquivo -> trechos que a mensagem pública não pode conter (valores lidos da fonte)
        cases = {
            "matroska.mkv": ("matroska",),
            "dois-audios.mp4": ("aac",),
            "dois-videos.mp4": ("h264",),
            "vp9.mp4": ("vp9", "vp09"),
            "av1.mp4": ("av1", "av01"),
            "mpeg4.mp4": ("mpeg4",),
            "legenda.mp4": ("mov_text", "subtitle"),
            "hevc-422.mp4": ("yuv422p10le", "422", "main422"),
            "hevc-444.mp4": ("yuv444p", "444", "main444"),
            "hevc-12bits.mp4": ("yuv420p12le", "main12"),
        }
        for name, echoes in cases.items():
            with self.subTest(source=name):
                with self.assertRaises(CliError) as context:
                    media.inspect_source(self.directory / name)
                error = context.exception
                self.assertEqual(error.code, "MEDIA_UNSUPPORTED")
                self.assertEqual(error.exit_code, EXIT_CODES["MEDIA"])
                lowered = error.message.lower()
                for token in (*echoes, Path(name).stem):
                    self.assertNotIn(token, lowered)

    def test_cli_reports_the_refusals_in_the_public_envelope(self) -> None:
        for name in ("matroska.mkv", "hevc-422.mp4"):
            with self.subTest(source=name), workspace_tempdir("tmp-hevc-cli") as workdir:
                result = run_module(
                    "analyze", "audio", "--workdir", str(workdir), "--source-id", "a",
                    "--input", str(self.directory / name),
                )
                envelope = json.loads(result.stdout)
                self.assertEqual(result.returncode, EXIT_CODES["MEDIA"], result.stdout)
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["error"]["code"], "MEDIA_UNSUPPORTED")
                self.assertEqual(envelope["error"]["exit_code"], EXIT_CODES["MEDIA"])
                self.assertNotIn("Traceback", result.stdout + result.stderr)
                self.assertFalse((workdir / "analysis" / "audio" / "a.json").exists())

    def test_plan_validate_keeps_its_own_stable_code_for_a_refused_source(self) -> None:
        """No plano a recusa da mídia segue virando `PLAN_INVALID` (mapeamento anterior à T8), sem ecoar a fonte."""

        with workspace_tempdir("tmp-hevc-cli") as workdir:
            (workdir / "sources").mkdir()
            shutil.copy2(self.directory / "hevc-422.mp4", workdir / "sources" / "hevc-422.mp4")
            write_plan(workdir, "hevc-422.mp4", [(0.0, 0.5)])
            result = run_module("plan", "validate", "--workdir", str(workdir))
            envelope = json.loads(result.stdout)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(envelope["error"]["code"], "PLAN_INVALID")
            for token in ("422", "yuv422p10le", "main422"):
                self.assertNotIn(token, envelope["error"]["message"].lower())


# --- Jornada de CLI por fonte HEVC -----------------------------------------------------------


class HevcSourceJourneyTests(HevcFixtureCase):
    """Cada fonte HEVC percorre análise, frames, validação, proxy, preview, aprovação e full."""

    def run_journey(self, file_name: str) -> None:
        """Executa os comandos pela CLI e confere formato, cor e aprovação por hash da fonte original."""

        width, height, _duration, _pixel_format, _profile = SOURCE_SPECS[file_name]
        with workspace_tempdir() as workdir:
            source = stage_source(self.fixtures, workdir, file_name)
            write_plan(workdir, file_name, [(0.5, 1.5)])
            args = ("--workdir", str(workdir))
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            cli(self, "init", *args)

            # Análise de áudio: o áudio é o do stream AAC, apesar das trilhas de dados e da miniatura.
            analysis = cli(self, "analyze", "audio", *args, "--source-id", "a", "--input", str(source))
            self.assertEqual(analysis["data"]["source_sha256"], source_sha)
            artifact = json.loads((workdir / "analysis" / "audio" / "a.json").read_text(encoding="utf-8"))
            self.assertEqual(artifact["source"]["container"], source.suffix.removeprefix("."))
            self.assertEqual(artifact["source"]["video_codec"], "hevc")
            self.assertEqual(artifact["source"]["audio_codec"], "aac")

            # Frames: PNG de 8 bits RGB (nunca `rgb48`), do vídeo real e com a cor conhecida.
            cli(self, "extract", "frames", *args, "--source-id", "a", "--input", str(source), "--timestamps", "0.5")
            png = workdir / "frames" / "a" / "frame-0001.png"
            self.assertEqual(png_header(png), (width, height, 8, 2))
            assert_colors_close(self, box_colors(png, 0, width, height), BOX_COLORS, FULL_TOLERANCE, "PNG extraído")

            # Validação do plano: metadados da fonte HEVC e perfil 16:9 sem ampliação.
            validation = cli(self, "plan", "validate", *args)["data"]
            self.assertEqual(validation["sources"][0]["video_codec"], "hevc")
            self.assertEqual(validation["sources"][0]["video_width"], width)
            self.assertEqual(validation["profile"]["upscale"], [])

            # Proxy: H.264 yuv420p de 8 bits em 1280x720, mesmo vindo de uma fonte de 10 bits.
            proxies = cli(self, "proxy", "build", *args)["data"]["proxies"]
            self.assertEqual([item["status"] for item in proxies], ["created"])
            proxy = probe_stream(workdir / "proxy" / "a.mp4", "v:0")
            self.assertEqual(
                (proxy["codec_name"], proxy["pix_fmt"], proxy["width"], proxy["height"]),
                ("h264", "yuv420p", 1280, 720),
            )
            # R8: o proxy só leva vídeo e áudio. A fonte tem trilhas `tmcd` (o `-timecode` do gerador) que o
            # muxer recriaria no proxy, como terceiro stream, sem o `-write_tmcd 0` do `proxy build`.
            self.assertTrue(any(item["codec_type"] == "data" for item in probe_streams(source)))
            self.assertEqual(
                [item["codec_type"] for item in probe_streams(workdir / "proxy" / "a.mp4")],
                ["video", "audio"],
            )

            # Preview com proxy e sem proxy: H.264/AAC yuv420p, na proporção do perfil e na cor certa.
            with_proxy = cli(self, "render", "preview", *args)["data"]
            self.assertEqual(with_proxy["proxy"]["sources"], ["a"])
            self.assert_rendered(workdir / "preview.mp4", PREVIEW_TOLERANCE, 0.5)
            without_proxy = cli(self, "render", "preview", *args, "--proxy", "off")["data"]
            self.assertEqual(without_proxy["proxy"]["sources"], [])
            self.assert_rendered(workdir / "preview.mp4", FULL_TOLERANCE, 0.5)

            # Aprovação vincula o ORIGINAL por hash (nunca o proxy) e a full sai em 8 bits.
            cli(self, "approve", *args)
            approval = json.loads((workdir / "approval.json").read_text(encoding="utf-8"))
            self.assertEqual(approval["sources"], [{"source_id": "a", "sha256": source_sha}])
            self.assertNotIn("proxy", json.dumps(approval))
            cli(self, "render", "full", *args)
            full = self.assert_rendered(workdir / "full.mp4", FULL_TOLERANCE, 0.5)
            self.assertEqual((full["width"], full["height"]), (1920, 1080))
            self.assertAlmostEqual(probe_duration(workdir / "full.mp4"), 1.0, delta=0.1)
            # A cor da full confere com a da própria fonte decodificada (mesmas posições relativas).
            assert_colors_close(
                self,
                box_colors(workdir / "full.mp4", 0.5, 1920, 1080),
                box_colors(source, 1.0, width, height),
                FULL_TOLERANCE,
                "full contra a fonte",
            )

    def assert_rendered(self, path: Path, tolerance: int, time_s: float) -> dict[str, Any]:
        """MP4 publicado em H.264/AAC `yuv420p` e com as cores das caixas dentro da tolerância."""

        video = probe_stream(path, "v:0")
        self.assertEqual((video["codec_name"], video["pix_fmt"]), ("h264", "yuv420p"))
        self.assertEqual(probe_stream(path, "a:0")["codec_name"], "aac")
        assert_colors_close(
            self, box_colors(path, time_s, video["width"], video["height"]), BOX_COLORS, tolerance, path.name
        )
        return video

    def test_hevc_10_bit_mov_2688(self) -> None:
        self.run_journey("hevc10-2688.mov")

    def test_hevc_10_bit_mp4_2688(self) -> None:
        self.run_journey("hevc10-2688.mp4")

    def test_hevc_10_bit_mov_1080(self) -> None:
        self.run_journey("hevc10-1080.mov")

    def test_hevc_10_bit_mp4_1080(self) -> None:
        self.run_journey("hevc10-1080.mp4")

    def test_hevc_8_bit_mp4_1080(self) -> None:
        self.run_journey("hevc8-1080.mp4")


class HevcStreamSelectionTests(HevcFixtureCase):
    """O vídeo e o áudio usados são os reais, nunca a miniatura nem uma trilha de dados."""

    def test_thumbnail_and_data_tracks_never_replace_the_real_streams(self) -> None:
        file_name = "hevc10-2688.mp4"
        with workspace_tempdir() as workdir:
            source = stage_source(self.fixtures, workdir, file_name)
            write_plan(workdir, file_name, [(0.5, 1.5)])
            args = ("--workdir", str(workdir))
            cli(self, "extract", "frames", *args, "--source-id", "a", "--input", str(source), "--interval-s", "1")
            for png in sorted((workdir / "frames" / "a").glob("frame-*.png")):
                # A miniatura é 320x180: qualquer PNG com essa medida seria o stream errado.
                self.assertEqual(png_header(png)[:2], (2688, 1512))
            cli(self, "render", "preview", *args, "--proxy", "off")
            preview = probe_stream(workdir / "preview.mp4", "v:0")
            self.assertEqual((preview["width"], preview["height"]), (704, 396))
            # O vermelho puro da miniatura (255, 0, 0) não aparece: a primeira caixa é a do quadro real.
            assert_colors_close(
                self, box_colors(workdir / "preview.mp4", 0.5, 704, 396), BOX_COLORS, FULL_TOLERANCE, "preview"
            )

    def test_graph_is_the_same_for_hevc_and_h264_sources(self) -> None:
        """O `filter_complex` não depende do codec: a conversão para 8 bits é o `format=yuv420p` que já existia."""

        with workspace_tempdir() as workdir:
            (workdir / "sources").mkdir()
            make_h264_source(workdir / "sources" / "h264.mp4", 1920, 1080, 3.0)
            shutil.copy2(self.fixtures / "hevc8-1080.mp4", workdir / "sources" / "hevc.mp4")
            graphs = {}
            for name in ("h264", "hevc"):
                write_plan(workdir, f"{name}.mp4", [(0.5, 1.5)])
                document, validation = load_validated_plan(workdir)
                profile = validation["profile"]
                graphs[name] = build_ffmpeg_graph(
                    document, validation, canvas_width=profile["full_width"], canvas_height=profile["full_height"]
                ).filter_complex
        self.assertEqual(graphs["h264"], graphs["hevc"])
        self.assertIn("format=yuv420p", graphs["hevc"])


class FramePixelFormatTests(unittest.TestCase):
    """O PNG de frame é RGB de 8 bits e, em fonte H.264 de 8 bits, idêntico ao que o FFmpeg escolheria sozinho."""

    def test_h264_frame_is_byte_identical_to_the_automatic_pixel_format(self) -> None:
        for tool in ("ffmpeg", "ffprobe"):
            if dependencies.resolve_executable(tool) is None:
                self.skipTest(f"{tool} local é necessário")
        with workspace_tempdir() as workdir:
            (workdir / "sources").mkdir()
            source = workdir / "sources" / "h264.mp4"
            make_h264_source(source, 640, 360, 2.0)
            cli(self, "extract", "frames", "--workdir", str(workdir), "--source-id", "a",
                "--input", str(source), "--timestamps", "1")
            edited = workdir / "frames" / "a" / "frame-0001.png"
            effective = json.loads((workdir / "frames" / "a" / "manifest.json").read_text(encoding="utf-8"))[
                "frames"][0]["timestamp_effective_s"]
            automatic = workdir / "automatico.png"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{effective:g}", "-copyts",
                 "-i", str(source), "-map", "0:v:0", "-frames:v", "1", "-f", "image2", "-update", "1", str(automatic)],
                check=True, capture_output=True,
            )
            self.assertEqual(png_header(edited)[2:], (8, 2))
            self.assertEqual(edited.read_bytes(), automatic.read_bytes())


# --- Janelas de decode e decode CUDA --------------------------------------------------------


@contextmanager
def windows_disabled() -> Iterator[None]:
    """Força o render a montar o grafo sem janelas de decode, que serve de referência."""

    def plain_graph(document: Any, validation: Any, *, canvas_width: int, canvas_height: int, windows: bool = False):
        return build_ffmpeg_graph(document, validation, canvas_width=canvas_width, canvas_height=canvas_height)

    with patch("editor.render.preview.build_ffmpeg_graph", side_effect=plain_graph):
        yield


class HevcDecodeWindowTests(HevcFixtureCase):
    """Janelas de decode numa fonte HEVC longa: leitura parcial, mesma imagem e mesmo áudio."""

    def test_windowed_full_matches_the_unwindowed_full_frame_by_frame(self) -> None:
        for file_name in ("hevc10-1080.mp4", "hevc10-1080.mov"):
            with self.subTest(source=file_name), workspace_tempdir() as workdir:
                stage_source(self.fixtures, workdir, file_name)
                # Dois trechos curtos longe do início de uma fonte de 12 s: ganho de leitura acima de 20%.
                write_plan(workdir, file_name, [(1.0, 2.5), (8.0, 9.5)])
                preview = render_preview(workdir)
                self.assertEqual(preview["decode"], {"windowed": True, "inputs": 2, "sources": ["a"]})
                approve_workdir(workdir)
                windowed = render_full(workdir)
                self.assertEqual(windowed["decode"], {"windowed": True, "inputs": 2, "sources": ["a"]})
                windowed_md5 = output_framemd5(workdir / "full.mp4")
                self.assertGreater(len(windowed_md5.splitlines()), 90)
                with windows_disabled():
                    plain = render_full(workdir)
                self.assertEqual(plain["decode"], {"windowed": False, "inputs": 1, "sources": []})
                self.assertEqual(windowed_md5, output_framemd5(workdir / "full.mp4"))
                video = probe_stream(workdir / "full.mp4", "v:0")
                self.assertEqual((video["codec_name"], video["pix_fmt"]), ("h264", "yuv420p"))


@unittest.skipUnless(
    shutil.which("nvidia-smi") and dependencies.gpu_acceleration_available(),
    "requer GPU NVIDIA operante e FFmpeg com NVENC e CUDA",
)
class HevcGpuTests(HevcFixtureCase):
    """`--accel gpu` com HEVC 8 e 10 bits: o decode CUDA entrega nv12 ou p010 e o grafo converte para 8 bits."""

    def test_gpu_preview_and_full_render_or_fall_back_without_failing(self) -> None:
        for file_name, spans in (("hevc10-1080.mp4", [(1.0, 2.5), (8.0, 9.5)]), ("hevc8-1080.mp4", [(0.5, 1.5)])):
            with self.subTest(source=file_name), workspace_tempdir() as workdir:
                stage_source(self.fixtures, workdir, file_name)
                write_plan(workdir, file_name, spans)
                preview = render_preview(workdir, accel="gpu")
                self.assertIn(preview["video"]["accel"], ("gpu", "cpu"))
                self.check_output(workdir / "preview.mp4", PREVIEW_TOLERANCE)
                approve_workdir(workdir)
                full = render_full(workdir, accel="gpu")
                self.assertIn(full["video"]["accel"], ("gpu", "cpu"))
                self.check_output(workdir / "full.mp4", PREVIEW_TOLERANCE)

    def check_output(self, path: Path, tolerance: int) -> None:
        """H.264/AAC `yuv420p` com as cores das caixas no tempo 0,5 s (primeiro trecho de cada plano)."""

        video = probe_stream(path, "v:0")
        self.assertEqual((video["codec_name"], video["pix_fmt"]), ("h264", "yuv420p"))
        self.assertEqual(probe_stream(path, "a:0")["codec_name"], "aac")
        assert_colors_close(
            self, box_colors(path, 0.5, video["width"], video["height"]), BOX_COLORS, tolerance, path.name
        )


if __name__ == "__main__":
    unittest.main()
