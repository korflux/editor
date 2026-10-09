"""Núcleo do render ao vivo da montagem (phase 7, T1): stream a partir de qualquer instante e quadro de scrub.

Responsabilidade: dado o plano já validado, montar os argumentos do FFmpeg que transmitem a montagem
do instante T até o fim, em MP4 fragmentado H.264/AAC no stdout, e o que extrai o quadro composto de
um instante. Não há HTTP aqui: o servidor da T3 só abre o processo, repassa o stdout e o encerra.

Mecanismo (UI-01): é o mesmo `build_ffmpeg_graph` da preview e da full, sem reimplementar efeito
algum. Mudam só o canvas (a full ou uma fração dela), as entradas (originais em `full`, proxy válido
nas frações), a janela de tempo (`TimeWindow`, que faz o grafo e as janelas de decode cobrirem só
`[T, fim]`, ver `ffmpeg_graph.build_ffmpeg_graph`) e o encoder, que aqui é o de transmissão. Em
`full` o vídeo cru e o PCM do grafo a partir de T são idênticos aos da full nos mesmos tempos
(`tests/test_live_stream.py`); a compressão do encoder de transmissão é a única diferença.

Pontos de extensão: `RESOLUTIONS` (frações do canvas), as constantes `LIVE_*` (encoder de transmissão,
fora dos presets da full) e `live_arguments`, que aceita qualquer saída no lugar do encoder (a prova
troca o encoder por `framemd5` para comparar antes de qualquer perda).

Limitações conhecidas (o que `tests/test_live_stream.py` prova e o que não):
ponytail: a igualdade quadro a quadro com a full foi provada com fontes na taxa da saída (a do primeiro
segmento), em 25 e em 29,97 quadros; misturando taxas (medido com fontes de 25 e de 29,97 quadros) um
T dentro de um segmento de outra taxa, ou depois de um, pode cair num quadro vizinho
da full e a cauda ter 1 ou 2 quadros de diferença, porque a fase da escolha de quadros e o início de
cada segmento na full dependem dos quadros anteriores; medir a fase com FFprobe e compensar se a
divergência incomodar;
ponytail: T fora da grade de 48 kHz (29,97 quadros: 1.601,6 amostras por quadro) põe os grupos de áudio
seguintes a até uma amostra (21 microssegundos) da posição que têm na full, aceitável até a
interface exigir PCM idêntico nessa taxa;
ponytail: `limiter` e `loudnorm` guardam estado e partem frios em T (medido: o limiter muda as primeiras
centenas de amostras; o `loudnorm`, que mede a sonoridade do que já passou, muda o ganho e o tamanho
do PCM em todo o trecho), então com `audio_processing` ligado o áudio ao vivo não é o da full e o
plano padrão (tudo desligado) é o que a prova cobre; pré-aquecer o filtro com alguns segundos antes de T
se o operador revisar planos com normalização;
ponytail: fonte só de áudio não tem janela de decode, então T tardio numa trilha longa decodifica o
começo dela para descartá-lo, estender `plan_decode_windows` às fontes de áudio se isso pesar;
ponytail: fonte de áudio que não é 48 kHz passa pelo `aresample` com o filtro frio em T (medido a 44,1 kHz:
as primeiras 12 amostras depois de T diferem da full, que o tem aquecido).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
from types import MappingProxyType
from typing import Any

from .. import dependencies
from ..contracts import EXIT_CODES, CliError
from ..workdir import PreviewLayout, _ensure_directory, validate_workdir
from .ffmpeg_graph import FfmpegGraph, build_ffmpeg_graph
from .preview import (
    _build_ffmpeg_arguments,
    _cleanup_filter_script,
    _create_filter_script,
    _number,
    _select_proxies,
)
from .windows import TimeWindow


RESOLUTION_DIVISORS: Mapping[str, int] = MappingProxyType({"full": 1, "1/2": 2, "1/3": 3, "1/4": 4})
"""Divisor das duas dimensões do canvas da full por resolução do player; a ordem é a do seletor."""

RESOLUTIONS = tuple(RESOLUTION_DIVISORS)
"""Allowlist de resolução do player: `full` é o canvas da full, as frações leem o proxy válido."""

DEFAULT_FRAME_RATE = 25.0
"""Taxa de saída quando a fonte do primeiro segmento não informa a sua (a mesma do `color` do grafo)."""

LIVE_VIDEO_ENCODER = "libx264"
LIVE_VIDEO_PRESET = "veryfast"
LIVE_VIDEO_TUNE = "zerolatency"
LIVE_VIDEO_CRF = 28
LIVE_KEYFRAME_INTERVAL_S = 0.5
"""Encoder de transmissão: constantes próprias, fora de `QUALITY_PRESETS` (a full não é tocada).

`zerolatency` tira o lookahead e os B-frames que atrasariam o primeiro quadro, e o intervalo curto de
quadros-chave dá um fragmento a cada meio segundo, que é o que o `<video>` precisa para mostrar
o primeiro quadro e para buscar dentro do que já chegou. O CRF 28 troca qualidade por velocidade:
o player é para conferir a montagem, a qualidade final é a da full.
"""

LIVE_AUDIO_BITRATE = "128k"
LIVE_MOVFLAGS = "+frag_keyframe+empty_moov+default_base_moof"
"""MP4 fragmentado: o `moov` vazio sai na hora e cada quadro-chave fecha um fragmento reproduzível."""

SCRUB_JPEG_QUALITY = 3
"""Qualidade MJPEG do quadro de scrub (`-q:v`, 2 a 31, menor é melhor): leve para o navegador."""

SCRUB_TIMEOUT_S = 30.0
"""Prazo da extração de um quadro; passado isso o FFmpeg é encerrado e o pedido falha."""

_FRAME_EPSILON = 1e-9
"""Folga contra erro de ponto flutuante ao converter segundos em índice de quadro."""


def _error(code: str, message: str, exit_code: int) -> CliError:
    """Cria erros públicos do render ao vivo sem expor comandos ou stderr do FFmpeg."""

    return CliError(code=code, message=message, exit_code=exit_code)


@dataclass(frozen=True)
class LiveRender:
    """Um render ao vivo pronto para virar comando: grafo, entradas e instante efetivo.

    `start_s` é o primeiro quadro da saída (o T pedido, na grade de quadros) e o timecode do player é
    `start_s` mais o tempo do stream; `duration_s` vai desse quadro ao fim da montagem. `proxy_sources`
    são as fontes lidas pelo proxy (indicador "Proxy" da interface); vazio em `full`.
    """

    graph: FfmpegGraph
    source_records: tuple[Mapping[str, Any], ...]
    resolution: str
    width: int
    height: int
    frame_rate: float
    start_s: float
    duration_s: float
    proxy_sources: tuple[str, ...]


def output_frame_rate(document: Mapping[str, Any], validation: Mapping[str, Any]) -> float:
    """Taxa de quadros da saída: a da fonte do primeiro segmento, o `-r` que preview e full já aplicam.

    Exposta para o timecode (`HH:MM:SS:FF`) da interface; 25 quando a fonte não informa taxa.
    """

    first_source_id = str(document["segments"][0]["video"]["source_id"])
    record = next(item for item in validation["sources"] if str(item["id"]) == first_source_id)
    rate = record.get("video_frame_rate")
    return float(rate) if rate is not None else DEFAULT_FRAME_RATE


def live_canvas(validation: Mapping[str, Any], resolution: str) -> tuple[int, int]:
    """Dimensões do canvas na resolução pedida: o da full dividido e arredondado para baixo a par (mínimo 2)."""

    divisor = RESOLUTION_DIVISORS.get(resolution) if isinstance(resolution, str) else None
    if divisor is None:
        raise _error(
            "ARGUMENT_INVALID", "A resolução informada não é suportada.", EXIT_CODES["ARGUMENT"]
        )
    profile = validation["profile"]

    def reduced(full_size: int) -> int:
        return max(2, full_size // divisor // 2 * 2)

    return reduced(int(profile["full_width"])), reduced(int(profile["full_height"]))


def snap_start(start_s: object, duration_s: float, frame_rate: float) -> float:
    """Cola T no quadro da saída que o contém (o quadro exibido em T), limitado ao último quadro.

    O primeiro quadro do stream tem de cair na grade `n / fps` da full para ser o mesmo quadro dela;
    `floor` escolhe o quadro que já está na tela em T, e o limite superior evita um stream vazio
    quando T é a duração. T inválido (não numérico, não finito, fora de `[0, duração]`) é recusado.
    """

    if (
        isinstance(start_s, bool)
        or not isinstance(start_s, (int, float))
        or not math.isfinite(start_s)
        or start_s < 0
        or start_s > duration_s + _FRAME_EPSILON
    ):
        raise _error(
            "ARGUMENT_INVALID", "O instante informado está fora da montagem.", EXIT_CODES["ARGUMENT"]
        )
    last_frame = max(0, math.ceil(duration_s * frame_rate - _FRAME_EPSILON) - 1)
    frame = min(last_frame, math.floor(start_s * frame_rate + _FRAME_EPSILON))
    return frame / frame_rate


def _without_audio(document: Mapping[str, Any]) -> dict[str, Any]:
    """Cópia do plano com todos os segmentos mudos: o grafo do scrub só precisa do vídeo.

    Sem áudio nenhum filtro de áudio é construído e as fontes só de áudio nem são abertas. O
    vídeo do grafo não depende do áudio, então o quadro é o mesmo do stream.
    """

    return {
        **document,
        "segments": [
            {**segment, "audio": {"mode": "mute", "tracks": []}} for segment in document["segments"]
        ],
    }


def prepare_live(
    workdir: str | Path,
    document: Mapping[str, Any],
    validation: Mapping[str, Any],
    start_s: float,
    resolution: str,
    *,
    audio: bool = True,
) -> LiveRender:
    """Monta o grafo da montagem de T ao fim na resolução pedida, com as entradas certas.

    `full` lê sempre os originais; as frações leem o proxy válido de cada fonte de vídeo, como
    `render preview --proxy auto` (`preview._select_proxies`), e o original nas demais. `audio=False`
    (scrub) dispensa o áudio. O T é colado na grade de quadros (`snap_start`) e vira `start_s`.
    """

    width, height = live_canvas(validation, resolution)
    duration = float(validation["timeline"]["duration_seconds"])
    frame_rate = output_frame_rate(document, validation)
    start = snap_start(start_s, duration, frame_rate)
    root = validate_workdir(workdir)
    read_paths: Mapping[str, Path] = {}
    if resolution != "full":
        read_paths, _ = _select_proxies(root, document, validation, "auto")
    graph = build_ffmpeg_graph(
        document if audio else _without_audio(document),
        validation,
        canvas_width=width,
        canvas_height=height,
        windows=True,
        time_window=TimeWindow(start),
    )
    # Só o `-i` troca: grafo, canvas e taxa seguem os metadados do original validado.
    records = tuple(
        {**record, "path": str(read_paths[record["id"]])} if record["id"] in read_paths else record
        for record in validation["sources"]
    )
    return LiveRender(
        graph=graph,
        source_records=records,
        resolution=resolution,
        width=width,
        height=height,
        frame_rate=frame_rate,
        start_s=start,
        duration_s=round(duration - start, 6),
        proxy_sources=tuple(
            source_id for source_id in dict.fromkeys(graph.input_source_ids) if source_id in read_paths
        ),
    )


def _seek_image_inputs(arguments: Sequence[str], seek_s: float) -> list[str]:
    """Dá `-ss` às entradas de imagem em loop (as únicas com `-framerate`), para não decodificá-las desde 0.

    O grafo conta com esse seek (`FfmpegGraph.image_seek_s`): o fluxo da imagem nasce em T e ele
    devolve o relógio global. Sem seek (T igual a 0) a lista volta como está.
    """

    if seek_s <= 0:
        return list(arguments)
    result: list[str] = []
    previous = ""
    for token in arguments:
        result.append(token)
        if previous == "-framerate":
            result.extend(["-ss", _number(seek_s)])
        previous = token
    return result


def live_arguments(live: LiveRender, filter_script: Path, output_arguments: Sequence[str]) -> list[str]:
    """Comando do FFmpeg (sem o executável): entradas do render, filtro por arquivo e a saída dada.

    As entradas (`-ss`/`-t` das janelas de decode, imagens em loop, caminhos do original ou do proxy)
    vêm da mesma `preview._build_ffmpeg_arguments` da preview e da full, cortadas antes do filtro.
    A saída é de quem chama: `stream_output_arguments`, `frame_output_arguments` ou, na prova, um
    `framemd5` no lugar do encoder.
    """

    full_command = _build_ffmpeg_arguments(
        live.graph,
        live.source_records,
        Path("saida.mp4"),
        filter_script=filter_script,
        duration_seconds=live.duration_s,
        frame_rate=live.frame_rate,
    )
    filter_option = dependencies.ffmpeg_filter_option()
    inputs = _seek_image_inputs(full_command[: full_command.index(filter_option)], live.graph.image_seek_s)
    return [*inputs, filter_option, str(filter_script), *output_arguments]


def stream_output_arguments(live: LiveRender) -> list[str]:
    """Saída do stream: MP4 fragmentado H.264/AAC no stdout, com o encoder de transmissão.

    A taxa de saída é a da full (`-r`), o áudio é o mesmo AAC 48 kHz, e `-t` fecha o fim da montagem.
    Sem grupo de áudio de T ao fim o stream sai sem áudio (`-an`).
    """

    keyframe_interval = max(1, round(live.frame_rate * LIVE_KEYFRAME_INTERVAL_S))
    arguments = [
        "-map", f"[{live.graph.video_label}]", "-map_metadata", "-1",
        "-c:v", LIVE_VIDEO_ENCODER, "-preset", LIVE_VIDEO_PRESET, "-tune", LIVE_VIDEO_TUNE,
        "-crf", str(LIVE_VIDEO_CRF), "-g", str(keyframe_interval), "-pix_fmt", "yuv420p",
        "-r", _number(live.frame_rate),
    ]
    if live.graph.audio_label:
        arguments.extend(
            [
                "-map", f"[{live.graph.audio_label}]", "-c:a", "aac",
                "-b:a", LIVE_AUDIO_BITRATE, "-ar", "48000",
            ]
        )
    else:
        arguments.append("-an")
    arguments.extend(["-movflags", LIVE_MOVFLAGS, "-t", _number(live.duration_s), "-f", "mp4", "pipe:1"])
    return arguments


def frame_output_arguments(live: LiveRender) -> list[str]:
    """Saída do scrub: o primeiro quadro do grafo como JPEG no stdout, sem áudio."""

    return [
        "-map", f"[{live.graph.video_label}]", "-frames:v", "1", "-an",
        "-c:v", "mjpeg", "-q:v", str(SCRUB_JPEG_QUALITY), "-pix_fmt", "yuvj420p",
        "-f", "image2pipe", "pipe:1",
    ]


def stream_arguments(live: LiveRender, filter_script: Path) -> list[str]:
    """Argumentos completos do stream da montagem de `live.start_s` ao fim (função 1 da T1)."""

    return live_arguments(live, filter_script, stream_output_arguments(live))


@contextmanager
def filter_script(workdir: str | Path, live: LiveRender) -> Iterator[Path]:
    """Grava o grafo em arquivo UTF-8 de `.tmp` (transporte do filtro) e o remove ao sair, em qualquer desfecho."""

    root = validate_workdir(workdir)
    temporary = root / ".tmp"
    _ensure_directory(temporary)
    layout = PreviewLayout(root=root, temporary=temporary, destination=root / "preview.mp4")
    script = _create_filter_script(layout, live.graph, "live")
    try:
        yield script
    finally:
        _cleanup_filter_script(script)


def extract_frame(
    workdir: str | Path,
    document: Mapping[str, Any],
    validation: Mapping[str, Any],
    start_s: float,
    resolution: str,
) -> bytes:
    """Extrai o quadro composto de `start_s` (função 2 da T1), como JPEG, pelo mesmo grafo do stream.

    Serve ao scrub: é o primeiro quadro do grafo de T ao fim, idêntico ao primeiro quadro do stream
    antes de qualquer encoder com perda. Falha com `FRAME_FAILED`, sem stderr nem caminho na mensagem.
    """

    failure = _error("FRAME_FAILED", "Não foi possível extrair este quadro.", EXIT_CODES["OPERATION"])
    executable = dependencies.resolve_executable("ffmpeg")
    if executable is None:
        raise _error(
            "DEPENDENCY_MISSING", "FFmpeg não está disponível localmente.", EXIT_CODES["DEPENDENCY"]
        )
    live = prepare_live(workdir, document, validation, start_s, resolution, audio=False)
    with filter_script(workdir, live) as script:
        arguments = live_arguments(live, script, frame_output_arguments(live))
        try:
            result = subprocess.run(
                [str(executable), *arguments],
                capture_output=True,
                check=False,
                shell=False,
                timeout=SCRUB_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise failure from error
    if result.returncode != 0 or not result.stdout.startswith(b"\xff\xd8"):
        raise failure
    return result.stdout
