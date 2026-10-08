"""Construção, verificação e leitura de validade dos proxies de preview (`editor proxy build`).

Responsabilidades:
- `build_proxies`: gera `proxy/<id>.mp4` + `proxy/<id>.json` para fontes de vídeo elegíveis do
  plano validado, reutilizando proxy válido e publicando por substituição atômica.
- `lookup_proxy`: API pública de leitura, sem decodificar, que a preview (T4) consulta para saber
  se a fonte tem proxy válido, obsoleto ou ausente.

Decisões não óbvias:
- Timestamps: `-copyts` mantém o `start_time` original (sem ele o FFmpeg rebaseia para zero quando
  a fonte começa deslocada), `-fps_mode passthrough` impede duplicar ou descartar quadros e
  `-enc_time_base demux` usa a base de tempo do demuxer no encoder. Medido com fontes H.264 com
  B-frames, 29,97/23,976/25 fps e deslocamento inicial: PTS dos pacotes de vídeo idênticos.
- O proxy só é publicado depois da verificação por FFprobe (contagem de pacotes, `start_time` e
  duração); falha por fonte vira status `failed` e não interrompe as demais.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .. import dependencies, process
from ..contracts import EXIT_CODES, CliError, reject_duplicate_json_keys
from ..plan.validation import load_validated_plan
from ..workdir import (
    ProxyLayout,
    prepare_proxy_layout,
    proxy_paths,
    publish_proxy_atomic,
    validate_source_id,
)


# Versão dos parâmetros de codificação abaixo; mudar resolução, GOP, crf, preset ou argumentos
# de timestamp exige incrementar para que proxies antigos passem a contar como obsoletos.
PROXY_PARAMETERS_VERSION = 1
PROXY_MAX_WIDTH = 1280
PROXY_MAX_HEIGHT = 720
PROXY_GOP = 15
PROXY_CRF = 20
PROXY_VIDEO_ENCODER = "libx264"
PROXY_PIXEL_FORMAT = "yuv420p"
MAX_METADATA_BYTES = 4 * 1024
METADATA_FIELDS = frozenset(
    {
        "version",
        "source_id",
        "source_size_bytes",
        "source_mtime_ns",
        "proxy_size_bytes",
        "width",
        "height",
    }
)
# Tolerância de `start_time` exigida pela spec (decisão 9).
START_TIME_TOLERANCE_SECONDS = 0.001


# ---------------------------------------------------------------------------
# Leitura de validade (API pública consumida pela preview)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProxyStatus:
    """Estado do proxy de uma fonte, obtido só por metadados e `stat`, sem decodificar.

    `state`: `valid` (pode substituir o original na preview), `stale` (vídeo ou metadado presente
    porém obsoleto, inválido ou inseguro) ou `absent` (nenhum dos dois arquivos existe).
    `width`, `height` e `size_bytes` só são preenchidos quando `state == "valid"`.
    """

    state: str
    path: Path
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None


def _strict_int(value: Any, minimum: int) -> int | None:
    """Aceita apenas inteiro JSON (não booleano) a partir do mínimo; o resto é formato inválido."""

    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def _read_metadata(path: Path, source_id: str) -> dict[str, int] | None:
    """Lê o metadado do proxy com limite de 4 KiB e schema exato; formato inválido devolve None."""

    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_METADATA_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_METADATA_BYTES:
        return None
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_json_keys)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(document, dict) or set(document) != METADATA_FIELDS:
        return None
    if document["source_id"] != source_id or document["version"] != PROXY_PARAMETERS_VERSION:
        return None
    parsed: dict[str, int] = {}
    for field, minimum in (
        ("version", 1),
        ("source_size_bytes", 0),
        ("source_mtime_ns", 0),
        ("proxy_size_bytes", 1),
        ("width", 2),
        ("height", 2),
    ):
        value = _strict_int(document[field], minimum)
        if value is None:
            return None
        parsed[field] = value
    return parsed


def lookup_proxy(root: Path, source_id: str, source_path: Path) -> ProxyStatus:
    """Informa se a fonte tem proxy válido, obsoleto ou ausente, sem decodificar e sem erro.

    `root` deve ser o trabalho já validado por `validate_workdir`; `source_path` é o original
    resolvido pelo plano. Qualquer divergência (metadado fora do formato, fonte com tamanho ou
    `mtime_ns` diferentes, versão de parâmetros antiga, tamanho real do proxy diferente, link ou
    tipo especial) resulta em `stale`, nunca em exceção.
    """

    directory, video, metadata_path = proxy_paths(root, source_id)
    if not os.path.lexists(video) and not os.path.lexists(metadata_path):
        return ProxyStatus(state="absent", path=video)
    stale = ProxyStatus(state="stale", path=video)
    if not (
        dependencies.is_regular_path(directory, directory=True)
        and dependencies.is_regular_path(video)
        and dependencies.is_regular_path(metadata_path)
    ):
        return stale
    metadata = _read_metadata(metadata_path, source_id)
    if metadata is None:
        return stale
    try:
        source_stat = os.stat(source_path)
        proxy_size = os.lstat(video).st_size
    except OSError:
        return stale
    if (
        metadata["source_size_bytes"] != source_stat.st_size
        or metadata["source_mtime_ns"] != source_stat.st_mtime_ns
        or metadata["proxy_size_bytes"] != proxy_size
    ):
        return stale
    return ProxyStatus(
        state="valid",
        path=video,
        width=metadata["width"],
        height=metadata["height"],
        size_bytes=proxy_size,
    )


# ---------------------------------------------------------------------------
# Codificação e verificação de um proxy
# ---------------------------------------------------------------------------


class _ProxyFailure(Exception):
    """Falha recuperável de uma fonte; vira `failed` com o motivo público estável `reason`."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class _VideoStream:
    """Medidas do primeiro stream de vídeo usadas para comparar proxy e original."""

    packets: int
    start_time: float
    duration: float
    frame_rate: float | None
    codec: str
    pix_fmt: str
    width: int
    height: int


def _ffmpeg_arguments(source: Path, staging: Path) -> list[str]:
    """Monta os argumentos estruturados do proxy (decisão 8) preservando os timestamps do original.

    O filtro reduz para caber em 1280x720 mantendo a proporção e dimensões pares; não há upscale
    porque só fontes maiores que 1280x720 em alguma dimensão chegam aqui.
    """

    return [
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-copyts",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        (
            f"scale={PROXY_MAX_WIDTH}:{PROXY_MAX_HEIGHT}"
            ":force_original_aspect_ratio=decrease:force_divisible_by=2"
        ),
        "-c:v",
        PROXY_VIDEO_ENCODER,
        "-preset",
        "veryfast",
        "-crf",
        str(PROXY_CRF),
        "-g",
        str(PROXY_GOP),
        "-pix_fmt",
        PROXY_PIXEL_FORMAT,
        "-fps_mode",
        "passthrough",
        "-enc_time_base",
        "demux",
        "-c:a",
        "copy",
        "-f",
        "mp4",
        str(staging),
    ]


def _timeout_seconds(duration_seconds: float) -> float:
    """Timeout proporcional à duração da fonte, com piso e teto como nos renders."""

    return max(60.0, min(14_400.0, duration_seconds * 8 + 30))


def _parse_rate(value: Any) -> float | None:
    """Converte razão `num/den` do FFprobe em número positivo, ou None se indisponível."""

    try:
        numerator, denominator = str(value).split("/", 1)
        rate = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def _probe_video_stream(path: Path, duration_seconds: float) -> _VideoStream:
    """Conta pacotes e lê tempos do primeiro stream de vídeo; qualquer falha é `STREAM_MISMATCH`."""

    arguments = [
        "-v",
        "error",
        "-count_packets",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=nb_read_packets,start_time,duration,avg_frame_rate,r_frame_rate,"
        "codec_name,pix_fmt,width,height",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = process.run_tool(
            "ffprobe",
            arguments,
            timeout_seconds=max(30.0, duration_seconds + 30),
        )
    except process.ProcessExecutionError as error:
        if error.missing:
            raise CliError(
                code="DEPENDENCY_MISSING",
                message="FFprobe não está disponível localmente.",
                exit_code=EXIT_CODES["DEPENDENCY"],
            ) from error
        raise _ProxyFailure("STREAM_MISMATCH") from error
    if result.returncode != 0:
        raise _ProxyFailure("STREAM_MISMATCH")
    try:
        stream = json.loads(result.stdout)["streams"][0]
        measured = _VideoStream(
            packets=int(stream["nb_read_packets"]),
            start_time=float(stream["start_time"]),
            duration=float(stream["duration"]),
            frame_rate=_parse_rate(stream.get("avg_frame_rate")) or _parse_rate(stream.get("r_frame_rate")),
            codec=str(stream.get("codec_name", "")),
            pix_fmt=str(stream.get("pix_fmt", "")),
            width=int(stream["width"]),
            height=int(stream["height"]),
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise _ProxyFailure("STREAM_MISMATCH") from error
    if not all(math.isfinite(value) for value in (measured.start_time, measured.duration)):
        raise _ProxyFailure("STREAM_MISMATCH")
    return measured


def _verify_proxy(original: _VideoStream, proxy: _VideoStream) -> None:
    """Exige mesma contagem de pacotes, `start_time` até 1 ms e duração até um quadro (decisão 9)."""

    if original.frame_rate is None:
        raise _ProxyFailure("STREAM_MISMATCH")
    frame_seconds = 1 / original.frame_rate
    if (
        proxy.packets != original.packets
        or abs(proxy.start_time - original.start_time) > START_TIME_TOLERANCE_SECONDS
        or abs(proxy.duration - original.duration) > frame_seconds + 1e-9
        or proxy.codec != "h264"
        or proxy.pix_fmt != PROXY_PIXEL_FORMAT
        or proxy.width > PROXY_MAX_WIDTH
        or proxy.height > PROXY_MAX_HEIGHT
        or proxy.width % 2
        or proxy.height % 2
    ):
        raise _ProxyFailure("STREAM_MISMATCH")


def _encode_and_publish(layout: ProxyLayout, source_id: str, source: Path, duration: float) -> dict[str, Any]:
    """Codifica o proxy em `.tmp`, verifica contra o original e publica; limpa o temporário sempre."""

    try:
        # O estado da fonte é lido antes do decode: se ela mudar durante o build, o metadado
        # gravado não casa mais e o proxy nasce obsoleto em vez de válido.
        source_stat = os.stat(source)
    except OSError as error:
        raise _ProxyFailure("FFMPEG_FAILED") from error

    descriptor, raw_staging = tempfile.mkstemp(
        prefix=f".proxy-{source_id}-",
        suffix=".mp4",
        dir=layout.temporary,
    )
    os.close(descriptor)
    staging = Path(raw_staging)
    try:
        try:
            result = process.run_tool(
                "ffmpeg",
                _ffmpeg_arguments(source, staging),
                timeout_seconds=_timeout_seconds(duration),
            )
        except process.ProcessExecutionError as error:
            if error.missing:
                raise CliError(
                    code="DEPENDENCY_MISSING",
                    message="FFmpeg não está disponível localmente.",
                    exit_code=EXIT_CODES["DEPENDENCY"],
                ) from error
            raise _ProxyFailure("TIMEOUT" if error.timed_out else "FFMPEG_FAILED") from error
        if result.returncode != 0:
            raise _ProxyFailure("FFMPEG_FAILED")

        original = _probe_video_stream(source, duration)
        proxy = _probe_video_stream(staging, duration)
        _verify_proxy(original, proxy)
        proxy_size = staging.stat().st_size
        metadata = {
            "version": PROXY_PARAMETERS_VERSION,
            "source_id": source_id,
            "source_size_bytes": source_stat.st_size,
            "source_mtime_ns": source_stat.st_mtime_ns,
            "proxy_size_bytes": proxy_size,
            "width": proxy.width,
            "height": proxy.height,
        }
        payload = json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
        publish_proxy_atomic(layout, staging, payload)
        return _entry(source_id, "created", width=proxy.width, height=proxy.height, size_bytes=proxy_size)
    finally:
        if os.path.lexists(staging):
            try:
                staging.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Comando `proxy build`
# ---------------------------------------------------------------------------


def _entry(
    source_id: str,
    status: str,
    *,
    reason: str | None = None,
    width: int | None = None,
    height: int | None = None,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    """Monta um item estável de `data.proxies[]`; `reason` só aparece em `skipped` e `failed`."""

    entry: dict[str, Any] = {
        "source_id": source_id,
        "status": status,
        "width": width,
        "height": height,
        "size_bytes": size_bytes,
    }
    if reason is not None:
        entry["reason"] = reason
    return entry


def _is_eligible(record: dict[str, Any]) -> bool:
    """Decisão 8: só fonte de vídeo maior que 1280 de largura ou 720 de altura recebe proxy."""

    width = record.get("video_width")
    height = record.get("video_height")
    return (
        record.get("kind") == "video"
        and isinstance(width, int)
        and isinstance(height, int)
        and (width > PROXY_MAX_WIDTH or height > PROXY_MAX_HEIGHT)
    )


def _require_libx264() -> None:
    """Confirma FFmpeg com libx264 antes do primeiro encode, como os renders fazem."""

    if dependencies.resolve_executable("ffmpeg") is None:
        raise CliError(
            code="DEPENDENCY_MISSING",
            message="FFmpeg não está disponível localmente.",
            exit_code=EXIT_CODES["DEPENDENCY"],
        )
    if PROXY_VIDEO_ENCODER not in set(dependencies.probe_ffmpeg_encoders()["available"]):
        raise CliError(
            code="DEPENDENCY_MISSING",
            message="O encoder H.264 libx264 não está disponível localmente.",
            exit_code=EXIT_CODES["DEPENDENCY"],
        )


def _select_records(
    records: list[dict[str, Any]],
    source_ids: Sequence[str] | None,
) -> list[dict[str, Any]]:
    """Aplica `--source-id` mantendo a ordem do plano; id ausente do plano é `ARGUMENT_INVALID`."""

    if not source_ids:
        return records
    requested = set(source_ids)
    known = {record["id"] for record in records}
    if not requested <= known:
        raise CliError(
            code="ARGUMENT_INVALID",
            message="Um --source-id informado não existe no plano.",
            exit_code=EXIT_CODES["ARGUMENT"],
        )
    return [record for record in records if record["id"] in requested]


def build_proxies(workdir: str | Path, source_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """Executa `proxy build`: valida o plano, reutiliza proxies válidos e gera os demais.

    Erros de comando (`WORKDIR_INVALID`, `PLAN_INVALID`, `ARGUMENT_INVALID`, `DEPENDENCY_MISSING`,
    layout inseguro) sobem como `CliError`; falhas por fonte viram itens `failed` sem interromper
    as demais e sem remover o proxy válido anterior.
    """

    for source_id in source_ids or ():
        validate_source_id(source_id)
    _, validation = load_validated_plan(workdir)
    records = _select_records(validation["sources"], source_ids)

    proxies: list[dict[str, Any]] = []
    encoder_checked = False
    for record in records:
        source_id = record["id"]
        if not _is_eligible(record):
            proxies.append(_entry(source_id, "skipped", reason="NOT_ELIGIBLE"))
            continue
        layout = prepare_proxy_layout(workdir, source_id)
        source = Path(record["path"])
        current = lookup_proxy(layout.root, source_id, source)
        if current.state == "valid":
            proxies.append(
                _entry(
                    source_id,
                    "reused",
                    width=current.width,
                    height=current.height,
                    size_bytes=current.size_bytes,
                )
            )
            continue
        if not encoder_checked:
            _require_libx264()
            encoder_checked = True
        try:
            proxies.append(
                _encode_and_publish(layout, source_id, source, float(record["duration_seconds"]))
            )
        except _ProxyFailure as failure:
            proxies.append(_entry(source_id, "failed", reason=failure.reason))
    return {"proxies": proxies}
