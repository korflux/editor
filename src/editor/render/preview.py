"""Renderização local e publicação atômica da única preview do trabalho."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math
import os
import tempfile
from pathlib import Path
from uuid import uuid4
from typing import Any

from .. import dependencies, media, process
from ..contracts import CliError, EXIT_CODES
from ..dependencies import is_regular_path
from ..plan.schema import LANDSCAPE_ASPECT_RATIO, canvas_dimensions
from ..plan.validation import load_validated_plan
from ..workdir import (
    FullLayout,
    PreviewLayout,
    ensure_sources_do_not_alias_output,
    prepare_preview_layout,
    publish_preview_atomic,
    relative_artifact_path,
)
from .ffmpeg_graph import FfmpegGraph, _used_source_ids, build_ffmpeg_graph
from .proxy import lookup_proxy


MAX_PREVIEW_DIMENSION = 720
"""Maior dimensão permitida para reduzir o custo da aprovação visual."""

ACCEL_CHOICES = ("none", "gpu")
"""Valores de `--accel`: `none` mantém CPU; `gpu` tenta decode CUDA + NVENC e cai para CPU se falhar."""

PROXY_CHOICES = ("auto", "off")
"""Valores de `--proxy` da preview (decisão 10): `auto` lê proxies válidos, `off` só originais."""

GPU_MAX_VIDEO_INPUTS = 4
"""Teto de fontes de vídeo com decode por GPU, pois cada decoder CUDA ocupa VRAM simultaneamente."""
# ponytail: teto fixo conservador para ~8 GB de VRAM, ler a VRAM livre (nvidia-smi) se projetos reais
# com muitas fontes simultâneas precisarem de decode por GPU.


class _RenderTimeout(CliError):
    """Distingue timeout das falhas do FFmpeg que têm fallback (GPU/encoder ou janelas de decode)."""


def _error(code: str, message: str, exit_code: int) -> CliError:
    """Cria erros públicos de renderização sem expor comandos ou stderr do FFmpeg."""

    return CliError(code=code, message=message, exit_code=exit_code)


def _number(value: object) -> str:
    """Formata duração e taxa de quadros sem permitir valores não finitos no comando."""

    number = float(value)
    if not math.isfinite(number):
        raise _error("RENDER_FAILED", "O perfil da preview contém um número inválido.", EXIT_CODES["OPERATION"])
    if abs(number) < 0.0000005:
        number = 0.0
    return f"{number:.6f}".rstrip("0").rstrip(".") or "0"


def _require_renderable_canvas(
    width: int, height: int, artifact_name: str
) -> tuple[int, int]:
    """Recusa um canvas degenerado (menos de 2 pixels) antes de montar o grafo de filtros.

    Só ocorre com fonte visual inicial minúscula no 16:9, cujo canvas é derivado da largura dela;
    as proporções de tamanho fixo nunca chegam aqui com valor inválido.
    """

    if width < 2 or height < 2:
        raise _error(
            "RENDER_FAILED",
            f"As dimensões da fonte visual inicial da {artifact_name} são inválidas.",
            EXIT_CODES["OPERATION"],
        )
    return width, height


def _preview_dimensions(
    document: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> tuple[int, int]:
    """Escolhe o canvas da preview na proporção do perfil, com o lado maior em até 720 pixels.

    As proporções fixas (9:16, 1:1, 4:5) vêm de `schema.FIXED_CANVAS_DIMENSIONS`; o 16:9 mantém
    a regra vigente (largura da primeira fonte de vídeo sem upscale, alinhada a 32). A full lê o
    canvas de `validation["profile"]`, calculado pela mesma `schema.canvas_dimensions`.
    """

    first_video_source_id = str(document["segments"][0]["video"]["source_id"])
    source = next(
        (
            item
            for item in validation["sources"]
            if str(item["id"]) == first_video_source_id
        ),
        None,
    )
    if not isinstance(source, Mapping):
        raise _error(
            "RENDER_FAILED",
            "A fonte visual inicial da preview não pôde ser determinada.",
            EXIT_CODES["OPERATION"],
        )
    try:
        source_width = int(source["video_width"])
        source_height = int(source["video_height"])
    except (KeyError, TypeError, ValueError) as error:
        raise _error(
            "RENDER_FAILED",
            "As dimensões da fonte visual inicial da preview são inválidas.",
            EXIT_CODES["OPERATION"],
        ) from error
    if source_width <= 0 or source_height <= 0:
        raise _error(
            "RENDER_FAILED",
            "As dimensões da fonte visual inicial da preview são inválidas.",
            EXIT_CODES["OPERATION"],
        )

    width, height = canvas_dimensions(
        str(validation["profile"]["aspect_ratio"]),
        source_width,
        MAX_PREVIEW_DIMENSION,
        final=False,
    )
    return _require_renderable_canvas(width, height, "preview")


def _require_encoders(has_audio: bool) -> None:
    """Confirma encoders locais antes de criar a saída, produzindo dependência explícita."""

    if dependencies.resolve_executable("ffmpeg") is None:
        raise _error(
            "DEPENDENCY_MISSING",
            "FFmpeg não está disponível localmente.",
            EXIT_CODES["DEPENDENCY"],
        )

    encoders = dependencies.probe_ffmpeg_encoders()
    if encoders["status"] == "unsupported" and not encoders["available"]:
        raise _error(
            "RENDER_FAILED",
            "Não foi possível verificar os encoders locais.",
            EXIT_CODES["OPERATION"],
        )

    available = set(encoders["available"])
    if "libx264" not in available:
        raise _error(
            "DEPENDENCY_MISSING",
            "O encoder H.264 libx264 não está disponível localmente.",
            EXIT_CODES["DEPENDENCY"],
        )
    if has_audio and "aac" not in available:
        raise _error(
            "DEPENDENCY_MISSING",
            "O encoder AAC não está disponível localmente.",
            EXIT_CODES["DEPENDENCY"],
        )


def _create_staging_file(
    layout: PreviewLayout | FullLayout,
    artifact_name: str = "preview",
) -> Path:
    """Cria um temporário exclusivo dentro do trabalho para um render MP4."""

    for _ in range(8):
        candidate = layout.temporary / f".{artifact_name}-{uuid4().hex}.mp4"
        try:
            with candidate.open("xb"):
                pass
        except FileExistsError:
            continue
        except OSError as error:
            raise _error(
                "OUTPUT_PUBLISH_FAILED",
                f"Não foi possível preparar a saída temporária da {artifact_name}.",
                EXIT_CODES["OPERATION"],
            ) from error
        if is_regular_path(candidate):
            return candidate
    raise _error(
        "OUTPUT_PUBLISH_FAILED",
        f"Não foi possível reservar a saída temporária da {artifact_name}.",
        EXIT_CODES["OPERATION"],
    )


def _create_filter_script(
    layout: PreviewLayout | FullLayout,
    graph: FfmpegGraph,
    artifact_name: str = "preview",
) -> Path:
    """Grava o grafo UTF-8 em um arquivo exclusivo antes de iniciar o FFmpeg."""

    descriptor: int | None = None
    filter_script: Path | None = None
    keep_file = False
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{artifact_name}-filter-",
            suffix=".txt",
            dir=layout.temporary,
        )
        filter_script = Path(raw_path)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(graph.filter_complex.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if not is_regular_path(filter_script):
            raise _error(
                "OUTPUT_PUBLISH_FAILED",
                f"O script temporário da {artifact_name} não pôde ser validado.",
                EXIT_CODES["OPERATION"],
            )
        keep_file = True
        return filter_script
    except CliError:
        raise
    except (OSError, UnicodeError) as error:
        raise _error(
            "OUTPUT_PUBLISH_FAILED",
            f"Não foi possível preparar o script temporário da {artifact_name}.",
            EXIT_CODES["OPERATION"],
        ) from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not keep_file and filter_script is not None and os.path.lexists(filter_script):
            try:
                filter_script.unlink()
            except OSError:
                pass


def _video_encoder_arguments(accel: str) -> list[str]:
    """Parâmetros de vídeo por modo, ambos com qualidade constante e yuv420p."""

    if accel == "gpu":
        # cq 31 equivale em SSIM ao crf 23 do x264 (medido, 1080p); cq 23 gerava arquivo ~2x maior.
        # p3 foi o equilíbrio medido entre velocidade e tamanho.
        return [
            "-c:v", dependencies.GPU_VIDEO_ENCODER, "-preset", "p3",
            "-rc", "vbr", "-cq", "31", "-b:v", "0", "-pix_fmt", "yuv420p",
        ]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]


def _accel_modes(accel: str) -> list[str]:
    """Ordem de tentativa por pedido: `gpu` tenta GPU quando o FFmpeg a expõe e termina em CPU."""

    if accel not in ACCEL_CHOICES:
        raise _error(
            "ARGUMENT_INVALID",
            "O modo de aceleração informado não é suportado.",
            EXIT_CODES["ARGUMENT"],
        )
    if accel == "gpu" and dependencies.gpu_acceleration_available():
        return ["gpu", "cpu"]
    return ["cpu"]


def _video_input_count(graph: FfmpegGraph, source_records: Sequence[Mapping[str, Any]]) -> int:
    """Conta entradas `-i` de vídeo do comando; uma fonte janelada conta uma vez por janela.

    Fonte única da contagem usada pela regra do decode por GPU e pelo envelope `data.decode`.
    """

    kinds = {str(record["id"]): record.get("kind") for record in source_records}
    return sum(1 for source_id in graph.input_source_ids if kinds.get(source_id) == "video")


def _windowed_source_ids(graph: FfmpegGraph) -> list[str]:
    """Ids das fontes lidas por janelas, na ordem das entradas e sem repetição."""

    windows = graph.input_windows or (None,) * len(graph.input_source_ids)
    return list(
        dict.fromkeys(
            source_id
            for source_id, window in zip(graph.input_source_ids, windows, strict=True)
            if window is not None
        )
    )


def _decode_summary(graph: FfmpegGraph, source_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Descreve `data.decode` a partir do grafo que efetivamente publicou o artefato."""

    sources = _windowed_source_ids(graph)
    return {
        "windowed": bool(sources),
        "inputs": _video_input_count(graph, source_records),
        "sources": sources,
    }


def _render_attempts(
    windowed: FfmpegGraph,
    plain: FfmpegGraph,
    modes: Sequence[str],
) -> list[tuple[FfmpegGraph, str]]:
    """Ordena as tentativas de render: modo pedido com janelas, CPU com janelas, CPU sem janelas.

    `modes` já termina em `cpu` (`_accel_modes`). Sem janelas o grafo janelado é igual ao
    atual, então só os modos do grafo atual são tentados, sem repetir a mesma tentativa.
    """

    if not _windowed_source_ids(windowed):
        return [(plain, accel) for accel in modes]
    return [*((windowed, accel) for accel in modes), (plain, "cpu")]


def _build_ffmpeg_arguments(
    graph: FfmpegGraph,
    source_records: Sequence[Mapping[str, Any]],
    staging: Path,
    *,
    filter_script: Path,
    duration_seconds: float,
    frame_rate: float | None,
    accel: str = "cpu",
) -> list[str]:
    """Monta a lista estruturada de argumentos sem interpolar caminhos em shell.

    Entradas com janela de decode recebem `-ss`/`-t` antes do `-i` (seek preciso de
    entrada); sem janela o comando é o mesmo de antes da decodificação seletiva.
    """

    records = {str(record["id"]): record for record in source_records}
    windows = graph.input_windows or (None,) * len(graph.input_source_ids)
    arguments = ["-hide_banner", "-nostdin", "-loglevel", "error"]
    video_inputs = _video_input_count(graph, source_records)
    # Decode CUDA só nas entradas de vídeo e só até o teto de VRAM (decisão 7: conta entradas,
    # pois cada janela abre seu próprio decoder); o FFmpeg cai para decode em software sozinho
    # quando o formato não é suportado, e os quadros voltam à RAM para o grafo.
    gpu_decode = accel == "gpu" and 0 < video_inputs <= GPU_MAX_VIDEO_INPUTS
    for source_id, window in zip(graph.input_source_ids, windows, strict=True):
        record = records.get(source_id)
        if record is None:
            raise _error(
                "RENDER_FAILED",
                "Uma fonte usada pela timeline não pôde ser localizada.",
                EXIT_CODES["OPERATION"],
            )
        if record["kind"] == "image":
            arguments.extend(["-loop", "1", "-framerate", "25"])
        elif gpu_decode and record["kind"] == "video":
            arguments.extend(["-hwaccel", dependencies.GPU_HWACCEL])
        if window is not None:
            arguments.extend(["-ss", _number(window.start_s), "-t", _number(window.duration_s)])
        arguments.extend(["-i", str(record["path"])])

    arguments.extend(
        [
            # Transporte sempre por arquivo UTF-8; a flag varia por sistema via dependencies.
            dependencies.ffmpeg_filter_option(),
            str(filter_script),
            "-map",
            f"[{graph.video_label}]",
            "-map_metadata",
            "-1",
            *_video_encoder_arguments(accel),
        ]
    )
    if frame_rate is not None:
        arguments.extend(["-r", _number(frame_rate)])
    if graph.audio_label:
        arguments.extend(
            [
                "-map",
                f"[{graph.audio_label}]",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
            ]
        )
    else:
        arguments.append("-an")
    arguments.extend(
        [
            "-movflags",
            "+faststart",
            "-t",
            _number(duration_seconds),
            "-y",
            str(staging),
        ]
    )
    return arguments


def _run_ffmpeg(
    arguments: Sequence[str],
    duration_seconds: float,
    artifact_name: str = "preview",
) -> None:
    """Executa um render com timeout proporcional e converte falhas em erro seguro."""

    timeout_seconds = max(60.0, min(14_400.0, duration_seconds * 8 + 30))
    try:
        result = process.run_tool(
            "ffmpeg",
            arguments,
            timeout_seconds=timeout_seconds,
        )
    except process.ProcessExecutionError as error:
        if error.missing:
            raise _error(
                "DEPENDENCY_MISSING",
                "FFmpeg não está disponível localmente.",
                EXIT_CODES["DEPENDENCY"],
            ) from error
        if error.timed_out:
            raise _RenderTimeout(
                code="RENDER_FAILED",
                message=f"A renderização da {artifact_name} excedeu o tempo permitido.",
                exit_code=EXIT_CODES["OPERATION"],
            ) from error
        raise _error(
            "RENDER_FAILED",
            f"O FFmpeg não conseguiu renderizar a {artifact_name}.",
            EXIT_CODES["OPERATION"],
        ) from error
    if result.returncode != 0:
        raise _error(
            "RENDER_FAILED",
            f"O FFmpeg não conseguiu renderizar a {artifact_name}.",
            EXIT_CODES["OPERATION"],
        )


def _render_with_fallback(
    attempts: Sequence[tuple[FfmpegGraph, str]],
    layout: PreviewLayout | FullLayout,
    source_records: Sequence[Mapping[str, Any]],
    staging: Path,
    *,
    duration_seconds: float,
    frame_rate: float | None,
    artifact_name: str = "preview",
) -> tuple[FfmpegGraph, str]:
    """Renderiza na primeira tentativa que funcionar e devolve o grafo e o modo que produziram o arquivo.

    Cada grafo distinto (com e sem janelas) tem seu próprio script de filtro em `.tmp`,
    criado só quando sua tentativa chega e removido ao final em qualquer desfecho.
    """

    filter_scripts: dict[int, Path] = {}
    try:
        for index, (graph, accel) in enumerate(attempts):
            if id(graph) not in filter_scripts:
                filter_scripts[id(graph)] = _create_filter_script(layout, graph, artifact_name)
            arguments = _build_ffmpeg_arguments(
                graph,
                source_records,
                staging,
                filter_script=filter_scripts[id(graph)],
                duration_seconds=duration_seconds,
                frame_rate=frame_rate,
                accel=accel,
            )
            try:
                _run_ffmpeg(arguments, duration_seconds, artifact_name)
            except CliError as error:
                # Fallback só para falha do FFmpeg (GPU/encoder indisponível ou janela rejeitada);
                # timeout e dependência ausente não melhoram trocando de modo ou de grafo.
                if (
                    index == len(attempts) - 1
                    or error.code != "RENDER_FAILED"
                    or isinstance(error, _RenderTimeout)
                ):
                    raise
                continue
            return graph, accel
    finally:
        for filter_script in filter_scripts.values():
            _cleanup_filter_script(filter_script)
    raise _error(
        "RENDER_FAILED",
        f"O FFmpeg não conseguiu renderizar a {artifact_name}.",
        EXIT_CODES["OPERATION"],
    )


def _validate_rendered_preview(
    staging: Path,
    *,
    expected_duration: float,
    expected_audio: bool,
    canvas_width: int,
    canvas_height: int,
    max_dimension: int | None = MAX_PREVIEW_DIMENSION,
    artifact_name: str = "preview",
    aspect_ratio: str = LANDSCAPE_ASPECT_RATIO,
) -> media.MediaMetadata:
    """Valida o MP4 produzido antes de permitir sua publicação como artefato vigente.

    Exige as dimensões exatas do canvas do perfil (`aspect_ratio`) e, na preview, o lado maior
    dentro de `max_dimension`. A razão largura/altura só é conferida no 16:9, cujo canvas é
    derivado da fonte: nas proporções fixas as dimensões da tabela já são o contrato, e uma razão
    exata é impossível com dimensões pares (404x720 difere de 9:16 em ~0,0014).
    """

    try:
        metadata = media.inspect_rendered_artifact(
            staging,
            require_audio=expected_audio,
        )
    except CliError as error:
        if error.code == "DEPENDENCY_MISSING":
            raise
        raise _error(
            "RENDER_FAILED",
            f"A {artifact_name} renderizada não passou na validação de mídia.",
            EXIT_CODES["OPERATION"],
        ) from error

    exceeds_max_dimension = (
        max_dimension is not None
        and max(metadata.video_width or 0, metadata.video_height or 0) > max_dimension
    )
    if (
        metadata.video_width != canvas_width
        or metadata.video_height != canvas_height
        or metadata.video_width is None
        or metadata.video_height is None
        or exceeds_max_dimension
        or (
            aspect_ratio == LANDSCAPE_ASPECT_RATIO
            and abs(metadata.video_width / metadata.video_height - 16 / 9) > 0.001
        )
    ):
        raise _error(
            "RENDER_FAILED",
            (
                f"A preview renderizada não respeita o perfil {aspect_ratio} de até {max_dimension} pixels."
                if max_dimension is not None
                else f"A full renderizada não respeita o perfil final {aspect_ratio}."
            ),
            EXIT_CODES["OPERATION"],
        )
    if (metadata.audio_codec is not None) != expected_audio:
        raise _error(
            "RENDER_FAILED",
            f"A {artifact_name} renderizada não respeita as faixas de áudio do plano.",
            EXIT_CODES["OPERATION"],
        )
    frame_rate = metadata.video_frame_rate or 25.0
    duration_tolerance = max(0.1, 2.0 / frame_rate)
    if abs(metadata.duration_seconds - expected_duration) > duration_tolerance:
        raise _error(
            "RENDER_FAILED",
            f"A duração da {artifact_name} renderizada não corresponde à timeline.",
            EXIT_CODES["OPERATION"],
        )
    return metadata


def _cleanup_staging(staging: Path | None) -> None:
    """Remove somente o temporário da operação, preservando o artefato vigente em qualquer falha."""

    if staging is None or not os.path.lexists(staging):
        return
    try:
        staging.unlink()
    except OSError:
        pass


def _cleanup_filter_script(filter_script: Path | None) -> None:
    """Remove somente o script de filtro da operação, sem tocar no estado vigente do trabalho."""

    if filter_script is None or not os.path.lexists(filter_script):
        return
    try:
        filter_script.unlink()
    except OSError:
        pass


def _render_artifact(
    document: Mapping[str, Any],
    validation: Mapping[str, Any],
    layout: PreviewLayout | FullLayout,
    *,
    canvas_width: int,
    canvas_height: int,
    accel: str,
    publish: Callable[[Any, Path], Any],
    max_dimension: int | None = MAX_PREVIEW_DIMENSION,
    artifact_name: str = "preview",
    read_paths: Mapping[str, Path] | None = None,
) -> tuple[media.MediaMetadata, str, dict[str, Any]]:
    """Corpo único de render de preview e full: grafos, tentativas, validação e publicação atômica.

    Monta o grafo com janelas de decode (padrão, decisão 1) e o atual sem janelas, tenta-os
    pela ordem de `_render_attempts` e publica só o MP4 validado. Devolve os metadados, o
    modo de aceleração usado e `data.decode` da tentativa que publicou. `.tmp` fica limpo em
    sucesso, erro, exceção e timeout; o artefato vigente só é trocado pela publicação.

    `read_paths` (id -> arquivo) troca só o `-i` dessas fontes; grafo, canvas, taxa de quadros e
    validação continuam derivados dos metadados do original validado. Só a preview informa
    proxies aqui; a full nunca passa este argumento, então sempre lê os originais (decisão 10).
    """

    plain = build_ffmpeg_graph(
        document, validation, canvas_width=canvas_width, canvas_height=canvas_height
    )
    windowed = build_ffmpeg_graph(
        document, validation, canvas_width=canvas_width, canvas_height=canvas_height, windows=True
    )
    _require_encoders(plain.has_audio)
    attempts = _render_attempts(windowed, plain, _accel_modes(accel))

    expected_duration = float(validation["timeline"]["duration_seconds"])
    first_source_id = str(document["segments"][0]["video"]["source_id"])
    source_records = list(validation["sources"])
    first_source = next(record for record in source_records if record["id"] == first_source_id)
    if read_paths:
        # Cópia rasa por fonte: o `validation` original (usado por aprovação e envelope) não muda.
        source_records = [
            {**record, "path": str(read_paths[record["id"]])} if record["id"] in read_paths else record
            for record in source_records
        ]
    frame_rate = first_source.get("video_frame_rate")
    staging: Path | None = None
    try:
        staging = _create_staging_file(layout, artifact_name)
        graph, used_accel = _render_with_fallback(
            attempts,
            layout,
            source_records,
            staging,
            duration_seconds=expected_duration,
            frame_rate=float(frame_rate) if frame_rate is not None else None,
            artifact_name=artifact_name,
        )
        metadata = _validate_rendered_preview(
            staging,
            expected_duration=expected_duration,
            expected_audio=graph.has_audio,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            max_dimension=max_dimension,
            artifact_name=artifact_name,
            aspect_ratio=str(validation["profile"]["aspect_ratio"]),
        )
        publish(layout, staging)
        staging = None
    finally:
        _cleanup_staging(staging)
    return metadata, used_accel, _decode_summary(graph, source_records)


def _select_proxies(
    root: Path,
    document: Mapping[str, Any],
    validation: Mapping[str, Any],
    mode: str,
) -> tuple[dict[str, Path], dict[str, Any]]:
    """Escolhe, por fonte de vídeo usada na timeline, se a preview lê o proxy ou o original (F3).

    Devolve `read_paths` (id -> proxy válido) para `_render_artifact` e o resumo `data.proxy`.
    Com `off` nada é consultado e a preview é a mesma de antes dos proxies. Proxy obsoleto ou
    inválido entra em `stale` e a fonte segue pelo original; a checagem é só por metadados.
    """

    # ponytail: proxy íntegro nos metadados mas corrompido no conteúdo falha a preview com
    # RENDER_FAILED em vez de cair para o original; refazer com `proxy build` ou usar `--proxy off`,
    # adicionar fallback ao original se isso aparecer em uso real.
    read_paths: dict[str, Path] = {}
    stale: list[str] = []
    if mode == "auto":
        used = set(_used_source_ids(document))
        for record in validation["sources"]:
            source_id = str(record["id"])
            if record.get("kind") != "video" or source_id not in used:
                continue
            status = lookup_proxy(root, source_id, Path(str(record["path"])))
            if status.state == "valid":
                read_paths[source_id] = status.path
            elif status.state == "stale":
                stale.append(source_id)
    return read_paths, {"mode": mode, "sources": list(read_paths), "stale": stale}


def render_preview(workdir: str | Path, accel: str = "none", proxy: str = "auto") -> dict[str, Any]:
    """Valida o plano, renderiza a preview em `.tmp` e publica um único arquivo completo.

    `proxy` (`auto`|`off`) só muda o arquivo lido por fonte; plano, canvas e aprovação seguem
    os originais validados.
    """

    # Allowlist na borda do núcleo, antes de qualquer leitura; a CLI já restringe por `choices`.
    if proxy not in PROXY_CHOICES:
        raise _error("ARGUMENT_INVALID", "Argumentos inválidos.", EXIT_CODES["ARGUMENT"])
    document, validation = load_validated_plan(workdir)
    layout = prepare_preview_layout(workdir)
    read_paths, proxy_summary = _select_proxies(layout.root, document, validation, proxy)
    ensure_sources_do_not_alias_output(
        [Path(str(record["path"])) for record in validation["sources"]],
        layout.destination,
        "preview",
    )
    canvas_width, canvas_height = _preview_dimensions(document, validation)
    metadata, used_accel, decode = _render_artifact(
        document,
        validation,
        layout,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        accel=accel,
        publish=publish_preview_atomic,
        read_paths=read_paths,
    )

    return {
        "artifact_path": str(layout.destination),
        "relative_artifact_path": relative_artifact_path(layout),
        "plan_sha256": validation["plan_sha256"],
        "duration_seconds": round(metadata.duration_seconds, 6),
        "timeline_duration_seconds": float(validation["timeline"]["duration_seconds"]),
        "source_count": validation["source_count"],
        "segment_count": validation["segment_count"],
        "cut_count": validation["cut_count"],
        "layer_count": validation["layer_count"],
        "profile": validation["profile"],
        "video": {
            "codec": metadata.video_codec,
            "accel": used_accel,
            "width": metadata.video_width,
            "height": metadata.video_height,
            "frame_rate": metadata.video_frame_rate,
            "max_dimension": max(metadata.video_width or 0, metadata.video_height or 0),
        },
        "audio": {
            "present": metadata.audio_codec is not None,
            "codec": metadata.audio_codec,
        },
        "decode": decode,
        "proxy": proxy_summary,
    }
