"""Validação estrita do plano v2 e cálculo da timeline resultante."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from .. import media
from ..contracts import CliError, EXIT_CODES, reject_duplicate_json_keys
from ..dependencies import is_regular_path
from ..workdir import resolve_source_path, validate_workdir
from .canonical import canonical_json, canonical_sha256
from .schema import (
    AUDIO_FIELDS,
    AUDIO_MODES,
    AUDIO_PROCESSING_FIELDS,
    CUT_FIELDS,
    MAX_CUT_DURATION_SECONDS,
    CUT_TYPES,
    FIT_MODES,
    IMAGE_LAYER_FIELDS,
    LAYER_FIT_MODES,
    LAYER_TYPES,
    LIMITER_FIELDS,
    MOTION_FIELDS,
    MOTION_STATE_FIELDS,
    MOTION_ZOOM_MAX,
    MOTION_ZOOM_MIN,
    PLAN_SCHEMA_VERSION,
    PROFILE_FIELDS,
    ROOT_FIELDS,
    SEGMENT_FIELDS,
    SOURCE_FIELDS,
    SOURCE_KINDS,
    TEXT_ALIGNS,
    TEXT_LAYER_FIELDS,
    TRACK_FIELDS,
    VIDEO_LAYER_FIELDS,
    VIDEO_FIELDS,
    plan_error,
    reject_unknown_fields,
    require_array,
    require_object,
)


MAX_PLAN_BYTES = 2 * 1024 * 1024
MAX_SEGMENTS = 1_000
MAX_LAYERS = 1_000
MAX_TIMELINE_DURATION_SECONDS = float(media.MAX_SOURCE_DURATION_SECONDS)
ASPECT_RATIO = 16 / 9
ASPECT_TOLERANCE = 1e-6
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_AUDIO_PROCESSING = {
    "normalize_loudness": False,
    "target_lufs": -16.0,
    "limiter": {"enabled": False, "ceiling_dbfs": -1.0},
    "ducking": False,
}


def _finite_number(value: Any, location: str) -> float:
    """Lê apenas números JSON finitos para impedir NaN e infinito na timeline."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise plan_error(f"O campo {location} deve ser um número finito.")
    try:
        parsed = float(value)
    except (OverflowError, ValueError) as error:
        raise plan_error(f"O campo {location} deve ser um número finito.") from error
    if not math.isfinite(parsed):
        raise plan_error(f"O campo {location} deve ser um número finito.")
    return parsed


def _positive_integer(value: Any, location: str, maximum: int) -> int:
    """Valida inteiros positivos usados em tamanho de fonte e estilo de lettering."""

    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= maximum:
        raise plan_error(f"O campo {location} deve ser um inteiro positivo válido.")
    return value


def _reject_nul(value: str, location: str) -> None:
    """Rejeita NUL em texto que será incorporado a argumentos de subprocessos."""

    if "\x00" in value:
        raise plan_error(f"O campo {location} contém caractere inválido.")


@lru_cache(maxsize=128)
def _font_is_installed(name: str) -> bool:
    """Consulta fontes locais sem aceitar um caminho fornecido pelo plano nem executar shell."""

    wanted = name.casefold()
    if sys.platform == "win32":
        import winreg

        registry_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, registry_path) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        registered = winreg.EnumValue(key, index)[0]
                        family = re.sub(r"\s*\([^)]*\)$", "", registered).casefold()
                        if wanted == family:
                            return True
            except OSError:
                continue
        return False
    try:
        result = subprocess.run(
            ["fc-list", "-f", "%{family}\n"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and any(
        wanted == family.strip().casefold()
        for line in result.stdout.splitlines()
        for family in line.split(",")
    )


def _rounded(value: float) -> float:
    """Arredonda tempos derivados para evitar ruído binário no resultado público."""

    return round(value, 6)


def _reject_non_finite_constant(value: str) -> None:
    """Impede que NaN ou infinito escapem do parser e contaminem o hash canônico."""

    raise ValueError(value)


def _resolve_plan_source(root: Path, raw_path: Any) -> Path:
    """Resolve fontes locais relativas ao trabalho sem aceitar URLs ou entradas vazias."""

    if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 4_096:
        raise plan_error("Toda fonte do plano deve apontar para um arquivo local válido.")
    if "\x00" in raw_path or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_path):
        raise plan_error("Toda fonte do plano deve apontar para um arquivo local válido.")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
        absolute_candidate = Path(os.path.abspath(os.fspath(candidate)))
        try:
            absolute_candidate.relative_to(root)
        except ValueError as error:
            raise plan_error("Uma fonte relativa não pode sair do diretório de trabalho.") from error
        candidate = absolute_candidate
    try:
        return resolve_source_path(Path(os.path.abspath(os.fspath(candidate))))
    except (CliError, OSError, ValueError) as error:
        raise plan_error("Uma fonte do plano não é um arquivo local regular válido.") from error


def _read_plan(root: Path) -> tuple[Path, dict[str, Any]]:
    """Lê exclusivamente plan.json do trabalho e rejeita arquivo ausente, linkado ou excessivo."""

    plan_path = root / "plan.json"
    if not is_regular_path(plan_path):
        raise plan_error("O trabalho deve conter um plan.json regular.")
    try:
        if plan_path.stat().st_size > MAX_PLAN_BYTES:
            raise plan_error("O plan.json excede o limite de tamanho permitido.")
        raw = plan_path.read_text(encoding="utf-8")
    except CliError:
        raise
    except (OSError, UnicodeError) as error:
        raise plan_error("Não foi possível ler o plan.json.") from error
    try:
        document = json.loads(
            raw,
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise plan_error("O plan.json não contém JSON válido.") from error
    if not isinstance(document, dict):
        raise plan_error("O plan.json deve conter um objeto JSON na raiz.")
    return plan_path, document


def _validate_profile(value: Any) -> None:
    """Valida o perfil único do MVP, que precisa ser horizontal em 16:9."""

    profile = require_object(value, "profile")
    reject_unknown_fields(profile, PROFILE_FIELDS, "profile")
    if profile.get("aspect_ratio") != "16:9":
        raise plan_error("O perfil de saída do MVP deve usar aspect_ratio 16:9.")


def _validate_source_structure(value: Any) -> None:
    """Valida a forma das fontes antes de permitir qualquer inspeção de mídia."""

    sources = require_array(value, "sources")
    if not sources:
        raise plan_error("O plano deve declarar ao menos uma fonte.")
    for source_value in sources:
        source = require_object(source_value, "sources[]")
        reject_unknown_fields(source, SOURCE_FIELDS, "sources[]")
        source_id = source.get("id")
        kind = source.get("kind")
        raw_path = source.get("path")
        if not isinstance(source_id, str) or not re.fullmatch(
            r"^[a-z0-9][a-z0-9_-]{0,63}$", source_id
        ):
            raise plan_error("Cada fonte deve ter um ID seguro e único.")
        if not isinstance(kind, str) or kind not in SOURCE_KINDS:
            raise plan_error("Cada fonte deve ter um ID único e um tipo suportado.")
        if (
            not isinstance(raw_path, str)
            or not raw_path.strip()
            or len(raw_path) > 4_096
            or "\x00" in raw_path
            or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_path)
        ):
            raise plan_error("Toda fonte do plano deve apontar para um arquivo local válido.")
    ids = [str(source["id"]) for source in sources]
    if len(ids) != len(set(ids)):
        raise plan_error("Cada fonte deve ter um ID único e um tipo suportado.")


def _validate_motion(value: Any, location: str) -> dict[str, dict[str, float]]:
    """Valida e normaliza os estados completos de movimento antes do uso no FFmpeg."""

    motion = require_object(value, location)
    reject_unknown_fields(motion, MOTION_FIELDS, location)
    normalized: dict[str, dict[str, float]] = {}
    for state_name in ("from", "to"):
        state_location = f"{location}.{state_name}"
        state = require_object(motion.get(state_name), state_location)
        reject_unknown_fields(state, MOTION_STATE_FIELDS, state_location)
        x = _finite_number(state.get("x"), f"{state_location}.x")
        y = _finite_number(state.get("y"), f"{state_location}.y")
        zoom = _finite_number(state.get("zoom"), f"{state_location}.zoom")
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise plan_error("Os pontos x e y do movimento devem estar entre 0 e 1.")
        if not MOTION_ZOOM_MIN <= zoom <= MOTION_ZOOM_MAX:
            raise plan_error("O zoom do movimento deve estar entre 0.5 e 4.0.")
        normalized[state_name] = {
            "x": _rounded(x),
            "y": _rounded(y),
            "zoom": _rounded(zoom),
        }
    return normalized


def _validate_segment_structure(value: Any) -> None:
    """Valida objetos e números dos segmentos antes de consultar suas fontes reais."""

    segments = require_array(value, "segments")
    if not segments:
        raise plan_error("O plano deve declarar ao menos um segmento.")
    if len(segments) > MAX_SEGMENTS:
        raise plan_error("O plano excede o limite de segmentos permitido.")
    for segment_value in segments:
        segment = require_object(segment_value, "segments[]")
        reject_unknown_fields(segment, SEGMENT_FIELDS, "segments[]")
        video = require_object(segment.get("video"), "segments[].video")
        reject_unknown_fields(video, VIDEO_FIELDS, "segments[].video")
        source_id = video.get("source_id")
        if not isinstance(source_id, str) or not re.fullmatch(
            r"^[a-z0-9][a-z0-9_-]{0,63}$", source_id
        ):
            raise plan_error("Cada segmento deve referenciar uma fonte de vídeo válida.")
        fit = video.get("fit")
        if not isinstance(fit, str) or fit not in FIT_MODES:
            raise plan_error("Cada vídeo deve declarar fit preserve, contain ou crop.")
        _finite_number(video.get("in_s"), "segments[].video.in_s")
        _finite_number(video.get("out_s"), "segments[].video.out_s")
        if "motion" in video:
            _validate_motion(video["motion"], "segments[].video.motion")

        audio = require_object(segment.get("audio"), "segments[].audio")
        reject_unknown_fields(audio, AUDIO_FIELDS, "segments[].audio")
        mode = audio.get("mode")
        if not isinstance(mode, str) or mode not in AUDIO_MODES:
            raise plan_error("O modo de áudio do segmento não é suportado.")
        tracks = require_array(audio.get("tracks"), "segments[].audio.tracks")
        for track_value in tracks:
            track = require_object(track_value, "segments[].audio.tracks[]")
            reject_unknown_fields(track, TRACK_FIELDS, "segments[].audio.tracks[]")
            track_source_id = track.get("source_id")
            if not isinstance(track_source_id, str) or not re.fullmatch(
                r"^[a-z0-9][a-z0-9_-]{0,63}$", track_source_id
            ):
                raise plan_error("Cada faixa deve referenciar uma fonte com áudio compatível.")
            _finite_number(track.get("in_s"), "segments[].audio.tracks[].in_s")
            _finite_number(track.get("out_s"), "segments[].audio.tracks[].out_s")
            _finite_number(track.get("gain_db"), "segments[].audio.tracks[].gain_db")
            if "loop" in track and not isinstance(track["loop"], bool):
                raise plan_error("O campo loop da faixa deve ser booleano.")
def _validate_cuts_structure(value: Any) -> list[dict[str, Any]]:
    """Valida a forma explícita dos cortes sem ainda calcular sua sobreposição temporal."""

    cuts = require_array(value, "cuts")
    normalized: list[dict[str, Any]] = []
    for cut_value in cuts:
        cut = require_object(cut_value, "cuts[]")
        reject_unknown_fields(cut, CUT_FIELDS, "cuts[]")
        after_segment = cut.get("after_segment")
        if (
            isinstance(after_segment, bool)
            or not isinstance(after_segment, int)
            or after_segment < 1
        ):
            raise plan_error("O campo cuts[].after_segment deve ser um índice inteiro positivo.")
        cut_type = cut.get("type")
        if not isinstance(cut_type, str) or cut_type not in CUT_TYPES:
            raise plan_error("O tipo do corte deve ser j ou l.")
        duration = _finite_number(cut.get("duration_s"), "cuts[].duration_s")
        if not 0 < duration <= MAX_CUT_DURATION_SECONDS:
            raise plan_error("A duração do corte deve estar entre zero e trinta segundos.")
        normalized.append(
            {
                "after_segment": after_segment,
                "type": cut_type,
                "duration_s": _rounded(duration),
            }
        )
    return normalized


def _validate_layer_geometry(layer: dict[str, Any], location: str) -> None:
    """Valida intervalo e caixa normalizada comuns a todas as camadas visuais."""

    start = _finite_number(layer.get("start_s"), f"{location}.start_s")
    end = _finite_number(layer.get("end_s"), f"{location}.end_s")
    if start < 0 or end <= start:
        raise plan_error("O intervalo da camada deve ser finito e crescente.")
    x = _finite_number(layer.get("x"), f"{location}.x")
    y = _finite_number(layer.get("y"), f"{location}.y")
    width = _finite_number(layer.get("width"), f"{location}.width")
    height = _finite_number(layer.get("height"), f"{location}.height")
    if (
        x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x > 1
        or y > 1
        or width > 1
        or height > 1
        or x + width > 1 + ASPECT_TOLERANCE
        or y + height > 1 + ASPECT_TOLERANCE
    ):
        raise plan_error("A caixa da camada deve permanecer dentro do canvas normalizado.")


def _validate_layers_structure(value: Any) -> list[dict[str, Any]]:
    """Valida camadas v2, inclusive texto em coordenadas livres, sem consultar mídia."""

    layers = require_array(value, "layers")
    if len(layers) > MAX_LAYERS:
        raise plan_error("O plano excede o limite de camadas permitido.")
    fields_by_type = {
        "text": TEXT_LAYER_FIELDS,
        "image": IMAGE_LAYER_FIELDS,
        "video": VIDEO_LAYER_FIELDS,
    }
    for layer_value in layers:
        layer = require_object(layer_value, "layers[]")
        layer_type = layer.get("type")
        if not isinstance(layer_type, str) or layer_type not in LAYER_TYPES:
            raise plan_error("O tipo da camada deve ser text, image ou video.")
        location = "layers[]"
        reject_unknown_fields(layer, fields_by_type[layer_type], location)
        _validate_layer_geometry(layer, location)
        if layer_type == "text":
            text = layer.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 4_096:
                raise plan_error("O texto da camada deve ser uma string não vazia.")
            _reject_nul(text, "layers[].text")
            font = layer.get("font")
            if (
                not isinstance(font, str)
                or not font.strip()
                or len(font) > 128
                or "/" in font
                or "\\" in font
            ):
                raise plan_error("A fonte da camada deve ser um nome instalado válido.")
            _reject_nul(font, "layers[].font")
            if not _font_is_installed(font):
                raise plan_error("A fonte da camada não está instalada localmente.")
            _positive_integer(layer.get("size_px"), "layers[].size_px", 512)
            color = layer.get("color")
            if not isinstance(color, str) or not HEX_COLOR_PATTERN.fullmatch(color):
                raise plan_error("A cor da camada deve estar no formato hexadecimal #RRGGBB.")
            if (
                not isinstance(layer.get("align"), str)
                or layer.get("align") not in TEXT_ALIGNS
            ):
                raise plan_error("O alinhamento da camada de texto é inválido.")
            if not isinstance(layer.get("wrap"), bool):
                raise plan_error("A opção wrap da camada de texto deve ser booleana.")
        elif layer_type == "image":
            source_id = layer.get("source_id")
            if not isinstance(source_id, str) or not re.fullmatch(
                r"^[a-z0-9][a-z0-9_-]{0,63}$", source_id
            ):
                raise plan_error("A camada de imagem deve referenciar uma fonte válida.")
            if layer.get("fit") not in LAYER_FIT_MODES:
                raise plan_error("A camada de imagem deve declarar fit contain ou crop.")
        else:
            source_id = layer.get("source_id")
            if not isinstance(source_id, str) or not re.fullmatch(
                r"^[a-z0-9][a-z0-9_-]{0,63}$", source_id
            ):
                raise plan_error("A camada de vídeo deve referenciar uma fonte válida.")
            _finite_number(layer.get("in_s"), "layers[].in_s")
            _finite_number(layer.get("out_s"), "layers[].out_s")
            if layer.get("fit") not in LAYER_FIT_MODES:
                raise plan_error("A camada de vídeo deve declarar fit contain ou crop.")
    return layers


def _validate_plan_structure(
    document: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Fecha a fronteira estrutural v2 antes de qualquer chamada ao FFprobe."""

    _validate_source_structure(document.get("sources"))
    _validate_segment_structure(document.get("segments"))
    cuts = _validate_cuts_structure(document.get("cuts"))
    layers = _validate_layers_structure(document.get("layers"))
    audio_processing = _validate_audio_processing(document.get("audio_processing"))
    return cuts, layers, audio_processing


def _inspect_sources(root: Path, value: Any) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Valida IDs, tipos, caminhos e metadados reais de todas as fontes declaradas."""

    sources = require_array(value, "sources")
    if not sources:
        raise plan_error("O plano deve declarar ao menos uma fonte.")
    by_id: dict[str, dict[str, Any]] = {}
    by_path: dict[Path, media.MediaMetadata] = {}
    output: list[dict[str, Any]] = []
    for item in sources:
        source = require_object(item, "sources[]")
        reject_unknown_fields(source, SOURCE_FIELDS, "sources[]")
        source_id = source.get("id")
        kind = source.get("kind")
        if not isinstance(source_id, str) or not re.fullmatch(r"^[a-z0-9][a-z0-9_-]{0,63}$", source_id):
            raise plan_error("Cada fonte deve ter um ID seguro e único.")
        if source_id in by_id or not isinstance(kind, str) or kind not in SOURCE_KINDS:
            raise plan_error("Cada fonte deve ter um ID único e um tipo suportado.")
        path = _resolve_plan_source(root, source.get("path"))
        if kind == "audio" and path.suffix.lower() not in media.PLAN_AUDIO_SOURCE_EXTENSIONS:
            raise plan_error("Uma fonte declarada como áudio deve usar MP3 ou WAV PCM S16LE.")
        metadata = by_path.get(path)
        if metadata is None:
            try:
                metadata = media.inspect_source(
                    path, require_audio=False, require_video=False,
                    **({"allow_image": True} if kind == "image" else {}),
                )
            except CliError as error:
                if error.code == "DEPENDENCY_MISSING":
                    raise
                raise plan_error("Uma fonte do plano não possui formato ou metadados compatíveis.") from error
            by_path[path] = metadata
        if kind == "video" and (metadata.video_codec is None or metadata.duration_seconds is None):
            raise plan_error("Uma fonte declarada como vídeo precisa conter vídeo compatível.")
        if kind == "audio" and metadata.audio_codec is None:
            raise plan_error("Uma fonte declarada como áudio precisa conter áudio compatível.")
        if kind == "image" and metadata.duration_seconds is not None:
            raise plan_error("Uma fonte declarada como imagem precisa conter imagem estática compatível.")
        record = {
            "id": source_id,
            "kind": kind,
            "path": str(path),
            "container": metadata.container,
            "audio_codec": metadata.audio_codec,
            "video_codec": metadata.video_codec,
            "duration_seconds": (
                _rounded(metadata.duration_seconds)
                if metadata.duration_seconds is not None else None
            ),
            "size_bytes": metadata.size_bytes,
            "video_width": metadata.video_width,
            "video_height": metadata.video_height,
            "video_frame_rate": (
                _rounded(metadata.video_frame_rate)
                if metadata.video_frame_rate is not None
                else None
            ),
        }
        by_id[source_id] = {"definition": source, "metadata": metadata, "record": record}
        output.append(record)
    return by_id, output


def _validate_video(
    value: Any,
    source_map: dict[str, dict[str, Any]],
    location: str,
) -> tuple[dict[str, Any], media.MediaMetadata]:
    """Valida fonte visual, intervalo e ajuste de proporção de um segmento."""

    video = require_object(value, location)
    reject_unknown_fields(video, VIDEO_FIELDS, location)
    if "motion" in video:
        video = {**video, "motion": _validate_motion(video["motion"], f"{location}.motion")}
    source_id = video.get("source_id")
    if (
        not isinstance(source_id, str)
        or source_id not in source_map
        or source_map[source_id]["definition"]["kind"] != "video"
        or source_map[source_id]["metadata"].video_codec is None
    ):
        raise plan_error("Cada segmento deve referenciar uma fonte de vídeo válida.")
    source = source_map[source_id]["metadata"]
    start = _finite_number(video.get("in_s"), f"{location}.in_s")
    end = _finite_number(video.get("out_s"), f"{location}.out_s")
    fit = video.get("fit")
    if start < 0 or end <= start or end > source.duration_seconds + ASPECT_TOLERANCE:
        raise plan_error("O intervalo de vídeo está fora da duração da fonte.")
    if not isinstance(fit, str) or fit not in FIT_MODES:
        raise plan_error("Cada vídeo deve declarar fit preserve, contain ou crop.")
    if not source.video_width or not source.video_height:
        raise plan_error("A proporção da fonte de vídeo não pôde ser determinada.")
    source_ratio = source.video_width / source.video_height
    if fit == "preserve" and abs(source_ratio - ASPECT_RATIO) > ASPECT_TOLERANCE:
        raise plan_error("Uma fonte fora de 16:9 precisa declarar fit contain ou crop.")
    return video, source


def _validate_track(
    value: Any,
    source_map: dict[str, dict[str, Any]],
    required_duration: float,
    location: str,
) -> dict[str, Any]:
    """Valida intervalo, ganho e cobertura de uma faixa na janela global do grupo."""

    track = require_object(value, location)
    reject_unknown_fields(track, TRACK_FIELDS, location)
    source_id = track.get("source_id")
    if (
        not isinstance(source_id, str)
        or source_id not in source_map
        or source_map[source_id]["metadata"].audio_codec is None
    ):
        raise plan_error("Cada faixa deve referenciar uma fonte com áudio compatível.")
    source = source_map[source_id]["metadata"]
    start = _finite_number(track.get("in_s"), f"{location}.in_s")
    end = _finite_number(track.get("out_s"), f"{location}.out_s")
    gain = _finite_number(track.get("gain_db"), f"{location}.gain_db")
    loop = track.get("loop", False)
    if start < 0 or end <= start or end > source.duration_seconds + ASPECT_TOLERANCE:
        raise plan_error("O intervalo de áudio está fora da duração da fonte.")
    if not -60 <= gain <= 12:
        raise plan_error("O ganho de cada faixa deve estar entre -60 e +12 dB.")
    if not isinstance(loop, bool):
        raise plan_error("O campo loop da faixa deve ser booleano.")
    if not loop and end - start + ASPECT_TOLERANCE < required_duration:
        raise plan_error("A faixa de áudio não cobre a duração necessária do grupo.")
    return track


def _validate_audio(
    value: Any,
    source_map: dict[str, dict[str, Any]],
    video_source_id: str,
    required_duration: float,
    location: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Valida o modo de áudio e a cobertura necessária da janela global do grupo."""

    audio = require_object(value, location)
    reject_unknown_fields(audio, AUDIO_FIELDS, location)
    mode = audio.get("mode")
    if not isinstance(mode, str) or mode not in AUDIO_MODES:
        raise plan_error("O modo de áudio do segmento não é suportado.")
    tracks = require_array(audio.get("tracks"), f"{location}.tracks")
    if mode == "mute" and tracks:
        raise plan_error("Um segmento mute não pode declarar faixas de áudio.")
    if mode == "keep" and len(tracks) != 1:
        raise plan_error("Um segmento keep deve declarar exatamente uma faixa.")
    if mode == "replace" and len(tracks) != 1:
        raise plan_error("Um segmento replace deve declarar exatamente uma faixa.")
    if mode == "mix" and len(tracks) < 2:
        raise plan_error("Um segmento mix deve declarar ao menos duas faixas.")
    validated_tracks = [
        _validate_track(track, source_map, required_duration, f"{location}.tracks[]")
        for track in tracks
    ]
    if mode == "keep" and validated_tracks[0].get("source_id") != video_source_id:
        raise plan_error("O modo keep deve usar o áudio da própria fonte visual.")
    return audio, validated_tracks


def _resolve_audio_windows(
    cuts: list[dict[str, Any]],
    visual_segments: list[dict[str, Any]],
) -> list[tuple[float, float]]:
    """Calcula janelas globais de áudio e rejeita cortes incompatíveis com os segmentos."""

    audio_starts = [float(item["start_s"]) for item in visual_segments]
    audio_ends = [float(item["end_s"]) for item in visual_segments]
    seen_boundaries: set[int] = set()

    for cut in cuts:
        boundary = int(cut["after_segment"])
        if boundary >= len(visual_segments):
            raise plan_error("O corte referencia uma fronteira inexistente entre segmentos.")
        if boundary in seen_boundaries:
            raise plan_error("Cada fronteira de segmentos pode declarar somente um corte.")
        seen_boundaries.add(boundary)

        duration = float(cut["duration_s"])
        previous_duration = float(visual_segments[boundary - 1]["duration_s"])
        next_duration = float(visual_segments[boundary]["duration_s"])
        if (
            duration + ASPECT_TOLERANCE >= previous_duration
            or duration + ASPECT_TOLERANCE >= next_duration
        ):
            raise plan_error(
                "A duração do corte deve ser menor que a duração dos dois segmentos adjacentes."
            )

        if cut["type"] == "j":
            target_index = boundary
            if visual_segments[target_index]["segment"]["audio"]["mode"] == "mute":
                raise plan_error("Um corte J não pode estender um segmento em mute.")
            audio_starts[target_index] = _rounded(
                float(visual_segments[target_index]["start_s"]) - duration
            )
        else:
            target_index = boundary - 1
            if visual_segments[target_index]["segment"]["audio"]["mode"] == "mute":
                raise plan_error("Um corte L não pode estender um segmento em mute.")
            audio_ends[target_index] = _rounded(
                float(visual_segments[target_index]["end_s"]) + duration
            )

    return [
        (_rounded(start), _rounded(end))
        for start, end in zip(audio_starts, audio_ends, strict=True)
    ]


def _validate_segments(
    value: Any,
    source_map: dict[str, dict[str, Any]],
    cuts: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    """Valida a timeline visual e depois aplica as janelas globais de áudio dos cortes."""

    segments = require_array(value, "segments")
    if not segments:
        raise plan_error("O plano deve declarar ao menos um segmento.")
    if len(segments) > MAX_SEGMENTS:
        raise plan_error("O plano excede o limite de segmentos permitido.")
    visual_segments: list[dict[str, Any]] = []
    timeline_end = 0.0
    for index, item in enumerate(segments, start=1):
        segment = require_object(item, "segments[]")
        reject_unknown_fields(segment, SEGMENT_FIELDS, "segments[]")
        video, _ = _validate_video(segment.get("video"), source_map, "segments[].video")
        duration = float(video["out_s"]) - float(video["in_s"])
        if timeline_end + duration > MAX_TIMELINE_DURATION_SECONDS + ASPECT_TOLERANCE:
            raise plan_error("A timeline excede a duração máxima permitida.")
        visual_segments.append(
            {
                "segment": segment,
                "video": video,
                "index": index,
                "start_s": _rounded(timeline_end),
                "end_s": _rounded(timeline_end + duration),
                "duration_s": _rounded(duration),
            }
        )
        timeline_end += duration

    audio_windows = _resolve_audio_windows(cuts, visual_segments)
    timeline_segments: list[dict[str, Any]] = []
    for visual_segment, (audio_start, audio_end) in zip(
        visual_segments,
        audio_windows,
        strict=True,
    ):
        segment = visual_segment["segment"]
        video = visual_segment["video"]
        audio, tracks = _validate_audio(
            segment.get("audio"),
            source_map,
            str(video["source_id"]),
            audio_end - audio_start,
            "segments[].audio",
        )
        timeline_segment = {
            "index": visual_segment["index"],
            "start_s": visual_segment["start_s"],
            "end_s": visual_segment["end_s"],
            "duration_s": visual_segment["duration_s"],
            "audio_start_s": audio_start,
            "audio_end_s": audio_end,
            "audio_duration_s": _rounded(audio_end - audio_start),
            "video_source_id": video["source_id"],
            "fit": video["fit"],
            "audio_mode": audio["mode"],
            "audio_source_ids": [track["source_id"] for track in tracks],
        }
        if "motion" in video:
            timeline_segment["motion"] = video["motion"]
        timeline_segments.append(timeline_segment)
    return timeline_segments, _rounded(timeline_end)


def _validate_layer_intervals(
    layers: list[dict[str, Any]], timeline_duration: float,
    source_map: dict[str, dict[str, Any]],
) -> None:
    """Confere intervalos globais e a mídia compatível com cada camada."""

    for layer in layers:
        if float(layer["end_s"]) > timeline_duration + ASPECT_TOLERANCE:
            raise plan_error("O intervalo da camada está fora da timeline.")
        if layer["type"] == "image":
            source = source_map.get(str(layer["source_id"]))
            if source is None or source["definition"]["kind"] != "image":
                raise plan_error("A camada de imagem deve usar uma fonte de imagem válida.")
        elif layer["type"] == "video":
            source = source_map.get(str(layer["source_id"]))
            if source is None or source["definition"]["kind"] != "video":
                raise plan_error("A camada de vídeo deve usar uma fonte de vídeo válida.")
            start = float(layer["in_s"])
            end = float(layer["out_s"])
            duration = source["metadata"].duration_seconds
            if start < 0 or end <= start or duration is None or end > duration + ASPECT_TOLERANCE:
                raise plan_error("O intervalo da camada de vídeo está fora da fonte.")
            if abs((end - start) - (float(layer["end_s"]) - float(layer["start_s"]))) > ASPECT_TOLERANCE:
                raise plan_error("A duração da camada de vídeo deve coincidir com seu intervalo na timeline.")


def _validate_audio_processing(value: Any) -> dict[str, Any]:
    """Valida normalização, limiter e a rejeição explícita de ducking automático."""

    if value is None:
        return DEFAULT_AUDIO_PROCESSING
    processing = require_object(value, "audio_processing")
    reject_unknown_fields(processing, AUDIO_PROCESSING_FIELDS, "audio_processing")
    normalize = processing.get("normalize_loudness", False)
    ducking = processing.get("ducking", False)
    if not isinstance(normalize, bool) or not isinstance(ducking, bool):
        raise plan_error("As opções de processamento de áudio devem ser booleanas.")
    if ducking:
        raise plan_error("Ducking automático não faz parte do contrato do MVP.")
    target = _finite_number(processing.get("target_lufs", -16), "audio_processing.target_lufs")
    if not -70 <= target <= 0:
        raise plan_error("O alvo de loudness deve estar entre -70 e 0 LUFS.")
    limiter_value = processing.get("limiter", DEFAULT_AUDIO_PROCESSING["limiter"])
    limiter = require_object(limiter_value, "audio_processing.limiter")
    reject_unknown_fields(limiter, LIMITER_FIELDS, "audio_processing.limiter")
    if not isinstance(limiter.get("enabled", False), bool):
        raise plan_error("A opção enabled do limiter deve ser booleana.")
    ceiling = _finite_number(limiter.get("ceiling_dbfs", -1), "audio_processing.limiter.ceiling_dbfs")
    if not -120 <= ceiling <= 0:
        raise plan_error("O teto do limiter deve estar entre -120 e 0 dBFS.")
    return {
        "normalize_loudness": normalize,
        "target_lufs": target,
        "limiter": {"enabled": limiter.get("enabled", False), "ceiling_dbfs": ceiling},
        "ducking": False,
    }


def _validate_loaded_plan(root: Path, plan_path: Path, document: dict[str, Any]) -> dict[str, Any]:
    """Valida um documento já lido e monta o resultado que alimenta CLI e renderização."""

    reject_unknown_fields(document, ROOT_FIELDS, "raiz")
    schema_version = document.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != PLAN_SCHEMA_VERSION
    ):
        raise plan_error(f"O plano deve usar schema_version {PLAN_SCHEMA_VERSION}.")
    _validate_profile(document.get("profile"))
    cut_definitions, layer_definitions, audio_processing = _validate_plan_structure(document)
    source_map, source_records = _inspect_sources(root, document.get("sources"))
    timeline_segments, timeline_duration = _validate_segments(
        document.get("segments"),
        source_map,
        cut_definitions,
    )
    _validate_layer_intervals(layer_definitions, timeline_duration, source_map)
    return {
        "plan_path": str(plan_path),
        "relative_plan_path": "plan.json",
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_sha256": canonical_sha256(document),
        "canonical_json": canonical_json(document),
        "source_count": len(source_records),
        "segment_count": len(timeline_segments),
        "cut_count": len(cut_definitions),
        "layer_count": len(layer_definitions),
        "sources": source_records,
        "audio_processing": audio_processing,
        "timeline": {
            "duration_seconds": timeline_duration,
            "segments": timeline_segments,
        },
    }


def load_validated_plan(workdir: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lê e valida o plano uma única vez para impedir que o render use documento divergente."""

    root = validate_workdir(workdir)
    plan_path, document = _read_plan(root)
    return document, _validate_loaded_plan(root, plan_path, document)


def validate_plan(workdir: str | Path) -> dict[str, Any]:
    """Valida plan.json, consulta fontes locais e retorna a timeline e o hash canônico."""

    _, result = load_validated_plan(workdir)
    return result
