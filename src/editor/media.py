"""Inspeção e validação comum das fontes locais de mídia."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import process
from .contracts import CliError, EXIT_CODES
from .integrity.hashes import sha256_file as _sha256_file
from .workdir import resolve_source_path


MAX_SOURCE_SIZE_BYTES = 4 * 1024**3
MAX_SOURCE_DURATION_SECONDS = 3_600
MAX_IMAGE_SIZE_BYTES = 100 * 1024**2
MAX_IMAGE_DIMENSION = 8_192
# Extensões audiovisuais (vídeo com ou sem áudio). `.mp4` e `.mov` compartilham o demuxer `mov` do
# FFmpeg: o `format_name` de ambos é `mov,mp4,m4a,3gp,3g2,mj2`, e `_validate_probe_payload` exige que
# o nome da extensão conste nele, então `.mov` e `.mp4` aceitam o mesmo conteúdo.
VIDEO_SOURCE_EXTENSIONS = (".mp4", ".mov")
SUPPORTED_SOURCE_EXTENSIONS = {*VIDEO_SOURCE_EXTENSIONS, ".mp3", ".wav"}
# O plano trata áudio como fonte somente de áudio, embora a análise genérica aceite MP4 audiovisual.
PLAN_AUDIO_SOURCE_EXTENSIONS = frozenset({".mp3", ".wav"})
# Codec de áudio aceito por extensão; fonte única da validação e de `capabilities.formats`.
SOURCE_AUDIO_CODECS = {
    ".mp4": frozenset({"aac"}),
    ".mov": frozenset({"aac"}),
    ".mp3": frozenset({"mp3"}),
    ".wav": frozenset({"pcm_s16le"}),
}
# Codecs de vídeo das fontes audiovisuais. `h264` não tem restrição de perfil nem de formato de pixel
# (contrato anterior à T8 da phase 5); `hevc` só aceita o que `HEVC_PROFILES` e `HEVC_PIXEL_FORMATS`
# listam, o que deixa 4:2:2, 4:4:4, 12 bits e as extensões de alcance (rext) fora.
# ponytail: fonte HDR (HLG ou PQ, primárias BT.2020) é convertida para 8 bits SDR sem tone mapping e
# aparece lavada; adicionar tone mapping (`zscale`/`tonemap`) quando uma fonte HDR real precisar sair
# fiel, e até lá a skill manda avisar o usuário.
SOURCE_VIDEO_CODECS = ("h264", "hevc")
HEVC_PROFILES = ("Main", "Main 10")
HEVC_PIXEL_FORMATS = ("yuv420p", "yuv420p10le")
# Toda saída renderizada, proxy e PNG de frame é de 8 bits `yuv420p`/`rgb24`, qualquer que seja a fonte.
OUTPUT_PIXEL_FORMAT = "yuv420p"
# Streams que a validação ignora quando não são o de vídeo ou o de áudio selecionado: trilhas de dados
# (`codec_type: data`, como o timecode `tmcd` e a telemetria das câmeras) e miniatura anexada
# (`disposition.attached_pic`). Legenda, `attachment` e qualquer outro tipo continuam recusados.
IGNORED_STREAM_CODEC_TYPES = ("data",)
ATTACHED_PICTURE_DISPOSITION = "attached_pic"
IGNORED_STREAM_DISPOSITIONS = (ATTACHED_PICTURE_DISPOSITION,)
IMAGE_FORMATS = {
    ".png": ("png_pipe", "png"),
    ".jpg": ("jpeg_pipe", "mjpeg"),
    ".jpeg": ("jpeg_pipe", "mjpeg"),
    ".webp": ("webp_pipe", "webp"),
}


@dataclass(frozen=True)
class MediaMetadata:
    """Metadados validados necessários para operações locais de mídia."""

    path: Path
    container: str
    audio_codec: str | None
    video_codec: str | None
    duration_seconds: float | None
    size_bytes: int
    sample_rate: int | None
    channels: int | None
    video_width: int | None = None
    video_height: int | None = None
    video_frame_rate: float | None = None


def _error(code: str, message: str, exit_code: int = EXIT_CODES["MEDIA"]) -> CliError:
    """Cria mensagens públicas estáveis sem repassar saída bruta do FFmpeg."""

    return CliError(code=code, message=message, exit_code=exit_code)


def _parse_duration(value: Any) -> float | None:
    """Converte duração do ffprobe e rejeita valores ausentes, infinitos ou negativos."""

    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) and duration > 0 else None


def _stream_duration(stream: dict[str, Any]) -> float | None:
    """Obtém a duração do stream como fallback quando o contêiner não a informa."""

    return _parse_duration(stream.get("duration"))


def _parse_positive_int(value: Any) -> int | None:
    """Converte uma dimensão FFprobe positiva sem transformar metadado inválido em zero."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_frame_rate(value: Any) -> float | None:
    """Converte uma razão de frames do FFprobe para um número finito positivo."""

    try:
        numerator, denominator = str(value).split("/", 1)
        rate = float(numerator) / float(denominator)
    except (AttributeError, ValueError, ZeroDivisionError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def _is_attached_picture(stream: dict[str, Any]) -> bool:
    """Indica se o stream é miniatura anexada (`disposition.attached_pic == 1`), que o FFmpeg conta como vídeo."""

    disposition = stream.get("disposition")
    return isinstance(disposition, dict) and disposition.get(ATTACHED_PICTURE_DISPOSITION) == 1


def _hevc_is_supported(stream: dict[str, Any]) -> bool:
    """Confere perfil (Main, Main 10) e formato de pixel (`yuv420p`, `yuv420p10le`) do HEVC.

    Allowlist fechada: perfil ausente ou desconhecido, 4:2:2, 4:4:4, 12 bits e rext não passam.
    Os valores vêm só do FFprobe e a mensagem pública do chamador nunca os repete.
    """

    profile = str(stream.get("profile", "")).strip().lower()
    pixel_format = str(stream.get("pix_fmt", "")).strip().lower()
    return (
        profile in {item.lower() for item in HEVC_PROFILES}
        and pixel_format in HEVC_PIXEL_FORMATS
    )


def _map_process_error(error: process.ProcessExecutionError) -> CliError:
    """Converte falha interna de processo em erro seguro da borda de mídia."""

    if error.missing:
        return _error(
            "DEPENDENCY_MISSING",
            "FFprobe não está disponível localmente.",
            EXIT_CODES["DEPENDENCY"],
        )
    return _error(
        "MEDIA_PROBE_FAILED",
        "Não foi possível inspecionar a fonte de mídia.",
    )


def _validate_probe_payload(
    path: Path,
    size_bytes: int,
    payload: dict[str, Any],
    *,
    require_audio: bool,
    require_video: bool,
    enforce_source_limits: bool,
) -> MediaMetadata:
    """Valida metadados comuns e aplica limites somente quando o arquivo é uma fonte."""

    format_data = payload.get("format")
    streams = payload.get("streams")
    if not isinstance(format_data, dict) or not isinstance(streams, list):
        raise _error("MEDIA_UNSUPPORTED", "A fonte não possui metadados de mídia suportados.")

    extension = path.suffix.lower()
    format_names = {
        item.strip().lower()
        for item in str(format_data.get("format_name", "")).split(",")
        if item.strip()
    }
    expected_format = extension.removeprefix(".")
    if extension not in SUPPORTED_SOURCE_EXTENSIONS or expected_format not in format_names:
        raise _error("MEDIA_UNSUPPORTED", "O formato da fonte não é aceito.")

    typed_streams = [stream for stream in streams if isinstance(stream, dict)]
    if len(typed_streams) != len(streams):
        raise _error("MEDIA_UNSUPPORTED", "A fonte contém streams inválidos.")
    unsupported_streams = {
        str(stream.get("codec_type", "")).lower()
        for stream in typed_streams
        if stream.get("codec_type") not in {"audio", "video", *IGNORED_STREAM_CODEC_TYPES}
    }
    if unsupported_streams:
        raise _error("MEDIA_UNSUPPORTED", "A fonte contém streams não suportados.")

    audio_streams = [stream for stream in typed_streams if stream.get("codec_type") == "audio"]
    video_typed_streams = [stream for stream in typed_streams if stream.get("codec_type") == "video"]
    # Invariante de seleção de streams (T8 da phase 5): todo consumidor (`analysis/audio.py`,
    # `analysis/frames.py`, `render/ffmpeg_graph.py`, `render/proxy.py`, `process.stream_pcm`) escolhe
    # o vídeo por `v:0` e o áudio por `a:0`, a posição entre streams do MESMO tipo. Isso só é correto
    # porque (1) trilhas de dados não contam como vídeo nem como áudio, (2) há no máximo um stream de
    # áudio e um de vídeo real e (3) a miniatura anexada, que o FFmpeg conta como vídeo, nunca é o
    # primeiro stream de vídeo. A regra 3 é a recusa abaixo: sem ela `v:0` mapearia a miniatura.
    if video_typed_streams and _is_attached_picture(video_typed_streams[0]):
        raise _error(
            "MEDIA_UNSUPPORTED",
            "O primeiro stream de vídeo da fonte é uma miniatura anexada.",
        )
    video_streams = [stream for stream in video_typed_streams if not _is_attached_picture(stream)]
    if not audio_streams and not video_streams:
        raise _error("MEDIA_UNSUPPORTED", "A fonte não contém streams de mídia suportados.")
    if require_audio and len(audio_streams) != 1:
        raise _error("MEDIA_UNSUPPORTED", "A fonte deve conter exatamente um stream de áudio.")
    if len(audio_streams) > 1:
        raise _error("MEDIA_UNSUPPORTED", "A fonte deve conter no máximo um stream de áudio.")
    if require_video and len(video_streams) != 1:
        raise _error("MEDIA_UNSUPPORTED", "A fonte deve conter exatamente um stream de vídeo.")

    audio_codec: str | None = None
    if audio_streams:
        audio_codec = str(audio_streams[0].get("codec_name", "")).lower()
        if audio_codec not in SOURCE_AUDIO_CODECS[extension]:
            raise _error("MEDIA_UNSUPPORTED", "O codec de áudio da fonte não é aceito.")

    video_codec: str | None = None
    if video_streams:
        if extension not in VIDEO_SOURCE_EXTENSIONS or len(video_streams) != 1:
            raise _error("MEDIA_UNSUPPORTED", "A fonte contém vídeo incompatível com o contrato.")
        video_codec = str(video_streams[0].get("codec_name", "")).lower()
        if video_codec not in SOURCE_VIDEO_CODECS:
            raise _error("MEDIA_UNSUPPORTED", "O codec de vídeo da fonte não é aceito.")
        if video_codec == "hevc" and not _hevc_is_supported(video_streams[0]):
            raise _error(
                "MEDIA_UNSUPPORTED",
                "O perfil ou o formato de pixel do vídeo HEVC não é aceito.",
            )

    duration = _parse_duration(format_data.get("duration"))
    if duration is None:
        for stream in (*audio_streams, *video_streams):
            duration = _stream_duration(stream)
            if duration is not None:
                break
    if duration is None:
        raise _error("MEDIA_UNSUPPORTED", "A duração da fonte não pôde ser determinada.")
    if enforce_source_limits and (
        size_bytes > MAX_SOURCE_SIZE_BYTES or duration > MAX_SOURCE_DURATION_SECONDS
    ):
        raise _error(
            "MEDIA_LIMIT_EXCEEDED",
            "A fonte excede o limite de tamanho ou duração permitido.",
        )

    sample_rate = None
    channels = None
    if audio_streams:
        try:
            sample_rate = int(audio_streams[0].get("sample_rate"))
        except (TypeError, ValueError):
            sample_rate = None
        try:
            channels = int(audio_streams[0].get("channels"))
        except (TypeError, ValueError):
            channels = None
    video_width = None
    video_height = None
    video_frame_rate = None
    if video_streams:
        video_width = _parse_positive_int(video_streams[0].get("width"))
        video_height = _parse_positive_int(video_streams[0].get("height"))
        video_frame_rate = _parse_frame_rate(
            video_streams[0].get("avg_frame_rate")
            or video_streams[0].get("r_frame_rate")
        )
    return MediaMetadata(
        path=path,
        container=expected_format,
        audio_codec=audio_codec,
        video_codec=video_codec,
        duration_seconds=duration,
        size_bytes=size_bytes,
        sample_rate=sample_rate,
        channels=channels,
        video_width=video_width,
        video_height=video_height,
        video_frame_rate=video_frame_rate,
    )


def _image_is_animated(path: Path) -> bool:
    """Detecta APNG e WebP animado sem depender de metadados temporais do FFprobe."""

    try:
        with path.open("rb") as source:
            if path.suffix.lower() == ".png":
                if source.read(8) != b"\x89PNG\r\n\x1a\n":
                    return True
                while True:
                    header = source.read(8)
                    if len(header) != 8:
                        return True
                    length = int.from_bytes(header[:4], "big")
                    chunk = header[4:]
                    if chunk == b"acTL":
                        return True
                    if chunk == b"IDAT":
                        return False
                    source.seek(length + 4, os.SEEK_CUR)
            if path.suffix.lower() == ".webp":
                header = source.read(21)
                return (
                    len(header) < 16
                    or header[:4] != b"RIFF"
                    or header[8:12] != b"WEBP"
                    or (header[12:16] == b"VP8X" and (len(header) < 21 or bool(header[20] & 2)))
                )
    except OSError as error:
        raise _error("SOURCE_INVALID", "Não foi possível ler a imagem.") from error
    return False


def _validate_image_probe_payload(
    path: Path, size_bytes: int, payload: dict[str, Any]
) -> MediaMetadata:
    """Aceita somente um stream raster estático cujo codec e formato correspondem à extensão."""

    expected_format, expected_codec = IMAGE_FORMATS[path.suffix.lower()]
    format_data = payload.get("format")
    streams = payload.get("streams")
    if not isinstance(format_data, dict) or not isinstance(streams, list) or len(streams) != 1:
        raise _error("MEDIA_UNSUPPORTED", "A imagem não possui metadados compatíveis.")
    stream = streams[0]
    if (
        not isinstance(stream, dict)
        or format_data.get("format_name") != expected_format
        or stream.get("codec_type") != "video"
        or stream.get("codec_name") != expected_codec
        or _image_is_animated(path)
    ):
        raise _error("MEDIA_UNSUPPORTED", "O formato da imagem não é aceito.")
    width = _parse_positive_int(stream.get("width"))
    height = _parse_positive_int(stream.get("height"))
    if width is None or height is None:
        raise _error("MEDIA_UNSUPPORTED", "As dimensões da imagem são inválidas.")
    if size_bytes > MAX_IMAGE_SIZE_BYTES or max(width, height) > MAX_IMAGE_DIMENSION:
        raise _error("MEDIA_LIMIT_EXCEEDED", "A imagem excede os limites permitidos.")
    return MediaMetadata(
        path=path, container=path.suffix.lower().removeprefix("."),
        audio_codec=None, video_codec=expected_codec, duration_seconds=None,
        size_bytes=size_bytes, sample_rate=None, channels=None,
        video_width=width, video_height=height,
    )


def _inspect_media_file(
    input_path: str | Path,
    *,
    require_audio: bool,
    require_video: bool,
    allow_image: bool,
    enforce_source_limits: bool,
) -> MediaMetadata:
    """Sonda um arquivo regular e aplica a política indicada sem duplicar o parser FFprobe."""

    path = resolve_source_path(input_path)
    is_image = allow_image and path.suffix.lower() in IMAGE_FORMATS
    if path.suffix.lower() not in SUPPORTED_SOURCE_EXTENSIONS and not is_image:
        raise _error("MEDIA_UNSUPPORTED", "A extensão da fonte não é aceita.")
    try:
        size_bytes = path.stat().st_size
    except OSError as error:
        raise _error("SOURCE_INVALID", "Não foi possível ler o tamanho da fonte.") from error
    if enforce_source_limits and size_bytes > (
        MAX_IMAGE_SIZE_BYTES if is_image else MAX_SOURCE_SIZE_BYTES
    ):
        raise _error("MEDIA_LIMIT_EXCEEDED", "A fonte excede o limite de tamanho permitido.")

    arguments = [
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = process.run_tool("ffprobe", arguments, timeout_seconds=15)
    except process.ProcessExecutionError as error:
        raise _map_process_error(error) from error
    if result.returncode != 0:
        raise _error("MEDIA_PROBE_FAILED", "Não foi possível inspecionar a fonte de mídia.")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise _error("MEDIA_PROBE_FAILED", "Os metadados da fonte são inválidos.") from error
    if not isinstance(payload, dict):
        raise _error("MEDIA_PROBE_FAILED", "Os metadados da fonte são inválidos.")
    if is_image:
        return _validate_image_probe_payload(path, size_bytes, payload)
    return _validate_probe_payload(
        path,
        size_bytes,
        payload,
        require_audio=require_audio,
        require_video=require_video,
        enforce_source_limits=enforce_source_limits,
    )


def inspect_source(
    input_path: str | Path,
    *,
    require_audio: bool = True,
    require_video: bool = False,
    allow_image: bool = False,
) -> MediaMetadata:
    """Inspeciona uma fonte local e mantém os limites de ingestão antes do processamento."""

    return _inspect_media_file(
        input_path,
        require_audio=require_audio,
        require_video=require_video,
        allow_image=allow_image,
        enforce_source_limits=True,
    )


def inspect_rendered_artifact(
    input_path: str | Path,
    *,
    require_audio: bool,
) -> MediaMetadata:
    """Valida um MP4 renderizado sem aplicar os limites arbitrários das fontes de entrada."""

    return _inspect_media_file(
        input_path,
        require_audio=require_audio,
        require_video=True,
        allow_image=False,
        enforce_source_limits=False,
    )


def sha256_file(path: Path) -> str:
    """Calcula o hash da fonte em blocos para registrar a origem analisada."""

    try:
        return _sha256_file(path)
    except OSError as error:
        raise _error("SOURCE_INVALID", "Não foi possível ler a fonte de mídia.") from error
