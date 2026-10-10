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

Fonte inteira (phase 7, T5): o modo Fonte da interface confere cada fonte de vídeo ou áudio do plano
fora da montagem. `prepare_source_live`, `source_stream_arguments` e `source_frame_arguments` fazem
uma transcodificação simples (um `-i`, sem grafo) da fonte a partir de T, com o mesmo encoder de
transmissão (`LIVE_*`) e a mesma seleção de proxy (`proxy.lookup_proxy`): `full` lê o original e as
frações, o proxy válido. O canvas é o da própria fonte (`source_canvas`), nunca o do perfil, porque a
conferência mostra o quadro inteiro, sem corte nem barras do `fit` da montagem.

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
import os
from pathlib import Path
import stat
import subprocess
from types import MappingProxyType
from typing import Any

from .. import dependencies
from ..contracts import EXIT_CODES, CliError
from ..plan.schema import MAX_MAX_FULL_WIDTH
from ..workdir import PreviewLayout, _ensure_directory, validate_workdir
from .ffmpeg_graph import FfmpegGraph, build_ffmpeg_graph
from .preview import (
    _build_ffmpeg_arguments,
    _cleanup_filter_script,
    _create_filter_script,
    _number,
    _select_proxies,
)
from .proxy import lookup_proxy
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

SOURCE_VIEW_KINDS = ("video", "audio")
"""Tipos de fonte que o modo Fonte confere: imagem estática não tem tempo nem som e fica fora."""

SOURCE_SEEK_SLACK_S = 0.001
"""Recuo do `-ss` da fonte antes do quadro pedido.

O primeiro quadro do stream da fonte é o primeiro com `pts` a partir do `-ss`. Pedir exatamente a posição
`n / fps` deixaria o `pts` real, que a taxa arredondada dos metadados nunca reproduz por inteiro, cair
um microssegundo antes e descartar o quadro n; 1 ms é muito menos que um quadro e muito mais que esse erro.
"""

SOURCE_MAX_SIDE = MAX_MAX_FULL_WIDTH
"""Maior lado da imagem do stream da fonte em `full`: o teto de canvas que o produto já gera (UHD).

As fontes aceitas chegam a 8.192 pixels; transcodificar 8K ao vivo não acompanha o tempo real e
navegador nenhum decodifica H.264 desse tamanho, então `full` reduz a fonte maior que isso (sem corte).
"""

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


def source_frame_rate(record: Mapping[str, Any]) -> float:
    """Taxa de quadros de uma fonte validada: a medida pelo FFprobe, ou 25 sem vídeo (fonte só de áudio)."""

    rate = record.get("video_frame_rate")
    return float(rate) if rate is not None else DEFAULT_FRAME_RATE


def output_frame_rate(document: Mapping[str, Any], validation: Mapping[str, Any]) -> float:
    """Taxa de quadros da saída: a da fonte do primeiro segmento, o `-r` que preview e full já aplicam.

    Exposta para o timecode (`HH:MM:SS:FF`) da interface; 25 quando a fonte não informa taxa.
    """

    first_source_id = str(document["segments"][0]["video"]["source_id"])
    record = next(item for item in validation["sources"] if str(item["id"]) == first_source_id)
    return source_frame_rate(record)


def _resolution_divisor(resolution: object) -> int:
    """Divisor das dimensões para a resolução pedida; fora da allowlist é `ARGUMENT_INVALID`."""

    divisor = RESOLUTION_DIVISORS.get(resolution) if isinstance(resolution, str) else None
    if divisor is None:
        raise _error(
            "ARGUMENT_INVALID", "A resolução informada não é suportada.", EXIT_CODES["ARGUMENT"]
        )
    return divisor


def _reduced(size: int, divisor: int) -> int:
    """Lado dividido e arredondado para baixo a par (o `yuv420p` exige par), com mínimo de 2 pixels."""

    return max(2, size // divisor // 2 * 2)


def live_canvas(validation: Mapping[str, Any], resolution: str) -> tuple[int, int]:
    """Dimensões do canvas na resolução pedida: o da full dividido e arredondado para baixo a par (mínimo 2)."""

    divisor = _resolution_divisor(resolution)
    profile = validation["profile"]
    return _reduced(int(profile["full_width"]), divisor), _reduced(int(profile["full_height"]), divisor)


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


def live_arguments(live: LiveRender, filter_script: Path, output_arguments: Sequence[str]) -> list[str]:
    """Comando do FFmpeg (sem o executável): entradas do render, filtro por arquivo e a saída dada.

    As entradas (`-ss`/`-t` das janelas de decode, imagens estáticas, caminhos do original ou do proxy)
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
    inputs = full_command[: full_command.index(filter_option)]
    return [*inputs, filter_option, str(filter_script), *output_arguments]


def _transmission_video_arguments(frame_rate: float) -> list[str]:
    """Codec de vídeo do encoder de transmissão, com o quadro-chave a cada meio segundo na taxa dada.

    Fonte única para o stream da montagem e o da fonte inteira: os dois entregam o mesmo formato ao `<video>`.
    """

    keyframe_interval = max(1, round(frame_rate * LIVE_KEYFRAME_INTERVAL_S))
    return [
        "-c:v", LIVE_VIDEO_ENCODER, "-preset", LIVE_VIDEO_PRESET, "-tune", LIVE_VIDEO_TUNE,
        "-crf", str(LIVE_VIDEO_CRF), "-g", str(keyframe_interval), "-pix_fmt", "yuv420p",
    ]


def _transmission_audio_arguments() -> list[str]:
    """Codec de áudio do encoder de transmissão (AAC 48 kHz), comum ao stream da montagem e ao da fonte."""

    return ["-c:a", "aac", "-b:a", LIVE_AUDIO_BITRATE, "-ar", "48000"]


def stream_output_arguments(live: LiveRender) -> list[str]:
    """Saída do stream: MP4 fragmentado H.264/AAC no stdout, com o encoder de transmissão.

    A taxa de saída é a da full (`-r`), o áudio é o mesmo AAC 48 kHz, e `-t` fecha o fim da montagem.
    Sem grupo de áudio de T ao fim o stream sai sem áudio (`-an`).
    """

    arguments = [
        "-map", f"[{live.graph.video_label}]", "-map_metadata", "-1",
        *_transmission_video_arguments(live.frame_rate),
        "-r", _number(live.frame_rate),
    ]
    if live.graph.audio_label:
        arguments.extend(["-map", f"[{live.graph.audio_label}]", *_transmission_audio_arguments()])
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


# --- Fonte inteira (phase 7, T5) ------------------------------------------------------------------


def source_unavailable() -> CliError:
    """Erro público de fonte do plano que sumiu ou deixou de ser arquivo regular, sem caminho na mensagem.

    `exit_code` de mídia, como o `SOURCE_INVALID` do restante do produto (a interface o mostra como 409).
    """

    return _error("SOURCE_INVALID", "Não foi possível ler uma fonte do plano.", EXIT_CODES["MEDIA"])


@dataclass(frozen=True)
class SourceLive:
    """Uma fonte inteira pronta para virar comando: arquivo lido (original ou proxy), canvas e instante.

    `start_s` é o primeiro quadro do stream, colado na grade de quadros da própria fonte, e `seek_s` o
    `-ss` que o alcança (`SOURCE_SEEK_SLACK_S` antes). `width` e `height` são `None` na fonte só de
    áudio, que o stream entrega sem imagem. `proxy` indica que o arquivo lido é o proxy da fonte.
    """

    source_id: str
    kind: str
    read_path: Path
    resolution: str
    width: int | None
    height: int | None
    frame_rate: float
    start_s: float
    seek_s: float
    proxy: bool

    @property
    def has_video(self) -> bool:
        """Se o stream tem imagem: só a fonte de vídeo, e só ela tem canvas."""

        return self.width is not None


def source_record(validation: Mapping[str, Any], source_id: object) -> Mapping[str, Any]:
    """Registro validado da fonte de vídeo ou áudio com esse id; qualquer outro valor é `ARGUMENT_INVALID`.

    Imagem estática não entra no modo Fonte. A mensagem é fixa e não ecoa o id recebido.
    """

    for record in validation["sources"]:
        if str(record["id"]) == source_id and record["kind"] in SOURCE_VIEW_KINDS:
            return record
    raise _error("ARGUMENT_INVALID", "A fonte informada não está no plano.", EXIT_CODES["ARGUMENT"])


def source_canvas(record: Mapping[str, Any], resolution: str) -> tuple[int, int]:
    """Dimensões da imagem do stream da fonte: as dela, reduzidas a `SOURCE_MAX_SIDE`, divididas e pares.

    Mantém a proporção da fonte (nada é cortado nem ganha barras) e vale igual com o original ou o proxy
    (o proxy cabe em 1280x720 e é ampliado quando a fração pede mais, sem mudar o tamanho do stream).
    ponytail: a rotação gravada no metadado da fonte (celular na vertical) não é lida, o produto inteiro
    parte das dimensões codificadas, tratar a rotação em `media.py` quando uma fonte real chegar rodada.
    """

    divisor = _resolution_divisor(resolution)
    width, height = int(record["video_width"]), int(record["video_height"])
    longest = max(width, height)
    if longest > SOURCE_MAX_SIDE:
        width, height = width * SOURCE_MAX_SIDE // longest, height * SOURCE_MAX_SIDE // longest
    return _reduced(width, divisor), _reduced(height, divisor)


def prepare_source_live(
    workdir: str | Path,
    validation: Mapping[str, Any],
    source_id: str,
    start_s: float,
    resolution: str,
) -> SourceLive:
    """Monta a leitura da fonte inteira a partir de T na resolução pedida.

    `full` lê sempre o original; as frações de uma fonte de vídeo leem o proxy válido (`lookup_proxy`, só
    por metadados) e o original quando ele não existe ou está obsoleto, como `render preview --proxy auto`.
    Fonte que sumiu depois da validação é `SOURCE_INVALID`. T é colado na grade de quadros da fonte
    (`snap_start`) e T fora de `[0, duração]` é `ARGUMENT_INVALID`.
    """

    record = source_record(validation, source_id)
    has_video = record["kind"] == "video"
    # A resolução é validada mesmo na fonte só de áudio, que a ignora: o pedido é o mesmo nos dois casos.
    _resolution_divisor(resolution)
    width, height = source_canvas(record, resolution) if has_video else (None, None)
    frame_rate = source_frame_rate(record)
    start = snap_start(start_s, float(record["duration_seconds"]), frame_rate)
    root = validate_workdir(workdir)
    path = Path(str(record["path"]))
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise source_unavailable()
    except OSError as error:
        raise source_unavailable() from error
    proxy = False
    if has_video and resolution != "full":
        status = lookup_proxy(root, str(record["id"]), path)
        if status.state == "valid":
            path, proxy = status.path, True
    return SourceLive(
        source_id=str(record["id"]),
        kind=str(record["kind"]),
        read_path=path,
        resolution=resolution,
        width=width,
        height=height,
        frame_rate=frame_rate,
        start_s=start,
        seek_s=max(0.0, round(start - SOURCE_SEEK_SLACK_S, 6)) if start > 0 else 0.0,
        proxy=proxy,
    )


def _source_input_arguments(live: SourceLive) -> list[str]:
    """Entrada única da fonte: `-ss` de entrada (seek preciso) só quando T não é o início, e o arquivo lido."""

    arguments = ["-hide_banner", "-nostdin", "-loglevel", "error"]
    if live.seek_s > 0:
        arguments.extend(["-ss", _number(live.seek_s)])
    return [*arguments, "-i", str(live.read_path)]


def _source_scale(live: SourceLive) -> str:
    """Filtro de imagem do stream: o canvas da fonte e pixel quadrado, como todo ramo da montagem."""

    return f"scale={live.width}:{live.height},setsar=1"


def source_stream_arguments(live: SourceLive) -> list[str]:
    """Comando do FFmpeg (sem o executável) que transmite a fonte de `live.start_s` ao fim, em MP4 fragmentado.

    Mesmo encoder de transmissão da montagem. A fonte de vídeo leva `0:a:0?` (áudio opcional: o vídeo
    sem som segue sem faixa); a de áudio sai só com AAC, e o `<video>` a toca sem imagem. O áudio vai
    sempre a estéreo, o que o navegador aceita de qualquer layout de origem.
    """

    arguments = _source_input_arguments(live)
    if live.has_video:
        arguments.extend(
            ["-map", "0:v:0", "-vf", _source_scale(live), *_transmission_video_arguments(live.frame_rate)]
        )
        audio_map = "0:a:0?"
    else:
        audio_map = "0:a:0"
    arguments.extend(
        [
            "-map", audio_map, *_transmission_audio_arguments(), "-ac", "2",
            "-map_metadata", "-1", "-movflags", LIVE_MOVFLAGS, "-f", "mp4", "pipe:1",
        ]
    )
    return arguments


def source_frame_arguments(live: SourceLive) -> list[str]:
    """Comando do FFmpeg do quadro de scrub da fonte em `live.start_s`, como JPEG no stdout (só fonte de vídeo)."""

    if not live.has_video:
        raise _error("ARGUMENT_INVALID", "Esta fonte não tem imagem.", EXIT_CODES["ARGUMENT"])
    return [
        *_source_input_arguments(live),
        "-map", "0:v:0", "-vf", _source_scale(live), "-frames:v", "1", "-an",
        "-c:v", "mjpeg", "-q:v", str(SCRUB_JPEG_QUALITY), "-pix_fmt", "yuvj420p",
        "-f", "image2pipe", "pipe:1",
    ]
