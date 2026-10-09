"""Validação estrita do plano v2 e cálculo da timeline resultante."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import winreg
except ImportError:  # pragma: no cover - somente hosts sem a API do Windows
    winreg = None

from .. import media
from ..contracts import CliError, EXIT_CODES, reject_duplicate_json_keys
from ..dependencies import is_regular_path, is_windows, resolve_executable
from ..workdir import resolve_source_path, validate_workdir
from .canonical import canonical_json, canonical_sha256
from .schema import (
    ASPECT_RATIOS,
    AUDIO_FIELDS,
    AUDIO_MODES,
    AUDIO_PROCESSING_FIELDS,
    CUT_FIELDS,
    CUT_TYPES,
    DEFAULT_MAX_FULL_WIDTH,
    DEFAULT_OUTLINE_COLOR,
    DEFAULT_REFRAME_INTERPOLATION,
    DEFAULT_REFRAME_ZOOM,
    DEFAULT_SHADOW_COLOR,
    DEFAULT_SHADOW_OPACITY,
    FIT_MODES,
    IMAGE_LAYER_FIELDS,
    KEYFRAME_FIELDS,
    LANDSCAPE_ASPECT_RATIO,
    LAYER_FIT_MODES,
    LAYER_TYPES,
    LIMITER_FIELDS,
    MAX_CUT_DURATION_SECONDS,
    MAX_FULL_WIDTH_STEP,
    MAX_MAX_FULL_WIDTH,
    MAX_REFRAME_KEYFRAMES,
    MIN_MAX_FULL_WIDTH,
    MOTION_FIELDS,
    MOTION_STATE_FIELDS,
    MOTION_ZOOM_MAX,
    MOTION_ZOOM_MIN,
    OUTLINE_FIELDS,
    OUTLINE_REQUIRED_FIELDS,
    OUTLINE_WIDTH_MAX,
    OUTLINE_WIDTH_MIN,
    PLAN_SCHEMA_VERSION,
    PROFILE_FIELDS,
    REFRAME_FIELDS,
    REFRAME_INTERPOLATIONS,
    REFRAME_ZOOM_MAX,
    REFRAME_ZOOM_MIN,
    ROOT_FIELDS,
    SEGMENT_FIELDS,
    SHADOW_BLUR_MAX,
    SHADOW_BLUR_MIN,
    SHADOW_FIELDS,
    SHADOW_OFFSET_MAX,
    SHADOW_OFFSET_MIN,
    SHADOW_OPACITY_MAX,
    SHADOW_OPACITY_MIN,
    SHADOW_REQUIRED_FIELDS,
    SOURCE_FIELDS,
    SOURCE_KINDS,
    TEXT_ALIGNS,
    TEXT_LAYER_FIELDS,
    TRACK_FIELDS,
    VIDEO_LAYER_FIELDS,
    VIDEO_FIELDS,
    aspect_ratio_value,
    canvas_dimensions,
    plan_error,
    reject_unknown_fields,
    require_array,
    require_object,
)


MAX_PLAN_BYTES = 2 * 1024 * 1024
MAX_SEGMENTS = 1_000
MAX_LAYERS = 1_000
MAX_TIMELINE_DURATION_SECONDS = float(media.MAX_SOURCE_DURATION_SECONDS)
ASPECT_TOLERANCE = 1e-6
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_AUDIO_PROCESSING = {
    "normalize_loudness": False,
    "target_lufs": -16.0,
    "limiter": {"enabled": False, "ceiling_dbfs": -1.0},
    "ducking": False,
}
FONT_FILE_EXTENSIONS = frozenset({".otc", ".otf", ".ttc", ".ttf"})
WINDOWS_FONT_REGISTRY_PATH = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
FONTCONFIG_TIMEOUT_SECONDS = 5


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


def _normalized_font_name(value: str) -> str:
    """Normaliza o nome humano da fonte para comparar registro, fontconfig e plano."""

    without_type = re.sub(r"\s*\([^)]*\)$", "", value)
    return " ".join(without_type.split()).casefold()


def _trusted_windows_font_directories() -> tuple[Path, ...]:
    """Lista diretórios de fontes do sistema e do usuário sem aceitar raízes arbitrárias."""

    candidates = (
        (
            os.environ.get("LOCALAPPDATA"),
            ("Microsoft", "Windows", "Fonts"),
        ),
        (
            os.environ.get("WINDIR") or os.environ.get("SystemRoot"),
            ("Fonts",),
        ),
    )
    directories: list[Path] = []
    for raw_root, suffix in candidates:
        if not isinstance(raw_root, str) or not raw_root.strip():
            continue
        directory = Path(raw_root, *suffix)
        if is_regular_path(directory, directory=True) and directory not in directories:
            directories.append(directory)
    return tuple(directories)


def _path_is_within(candidate: Path, directory: Path) -> bool:
    """Confirma contenção lexical antes da validação física, sem seguir links no cálculo."""

    try:
        Path(os.path.abspath(os.fspath(candidate))).relative_to(
            Path(os.path.abspath(os.fspath(directory)))
        )
    except ValueError:
        return False
    return True


def _validated_font_file(
    candidate: Path,
    *,
    trusted_directories: tuple[Path, ...] = (),
) -> Path | None:
    """Aceita somente fonte regular, suportada e sem reparse point no caminho físico."""

    if candidate.suffix.casefold() not in FONT_FILE_EXTENSIONS:
        return None
    if trusted_directories and not any(
        _path_is_within(candidate, directory) for directory in trusted_directories
    ):
        return None
    try:
        return resolve_source_path(candidate)
    except (CliError, OSError, ValueError):
        return None


def _resolve_windows_font_file(name: str) -> Path | None:
    """Resolve o nome nas colmeias do Windows e materializa somente o arquivo registrado."""

    if winreg is None:
        return None
    trusted_directories = _trusted_windows_font_directories()
    if not trusted_directories:
        return None
    wanted = _normalized_font_name(name)
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, WINDOWS_FONT_REGISTRY_PATH) as key:
                value_count = winreg.QueryInfoKey(key)[1]
                for index in range(value_count):
                    registered, raw_path, _ = winreg.EnumValue(key, index)
                    if not isinstance(registered, str) or _normalized_font_name(registered) != wanted:
                        continue
                    if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 4_096:
                        continue
                    registry_path = Path(raw_path)
                    candidates = (
                        (registry_path,)
                        if registry_path.is_absolute()
                        else tuple(directory / registry_path for directory in trusted_directories)
                    )
                    for candidate in candidates:
                        resolved = _validated_font_file(
                            candidate,
                            trusted_directories=trusted_directories,
                        )
                        if resolved is not None:
                            return resolved
        except OSError:
            continue
    return None


def _fontconfig_name_matches(name: str, family: str, style: str) -> bool:
    """Evita aceitar o fallback silencioso do fontconfig para uma família inexistente."""

    wanted = _normalized_font_name(name)
    families = [_normalized_font_name(item) for item in family.split(",") if item.strip()]
    styles = [_normalized_font_name(item) for item in style.split(",") if item.strip()]
    return any(
        wanted == family_name
        or any(wanted == f"{family_name} {style_name}" for style_name in styles)
        for family_name in families
    )


def _resolve_fontconfig_font_file(name: str) -> Path | None:
    """Consulta o fontconfig por arquivo concreto, sem criar configuração lateral no trabalho."""

    executable = resolve_executable("fc-match")
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [
                str(executable),
                "-f",
                "%{family}\t%{style}\t%{file}\n",
                name,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=FONTCONFIG_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    stdout = result.stdout if isinstance(result.stdout, str) else ""
    if result.returncode != 0 or len(stdout) > 8_192:
        return None
    line = next((item for item in stdout.splitlines() if item.strip()), "")
    fields = line.split("\t", 2)
    if len(fields) != 3 or not _fontconfig_name_matches(name, fields[0], fields[1]):
        return None
    candidate = Path(fields[2].strip())
    if not candidate.is_absolute():
        return None
    return _validated_font_file(candidate)


def _resolve_font_file(name: str) -> Path | None:
    """Escolhe a descoberta nativa do host e devolve o arquivo seguro para o renderer."""

    # Windows mantém HKCU e HKLM; demais usam fc-match com rejeição de fallback silencioso.
    # Checa o helper central e o sys local para preservar os mocks vigentes de plataforma.
    if is_windows() or sys.platform == "win32":
        return _resolve_windows_font_file(name)
    return _resolve_fontconfig_font_file(name)


def _resolve_layer_fonts(layers: list[dict[str, Any]]) -> dict[str, str]:
    """Resolve uma vez cada fonte textual e mantém seus caminhos fora do contrato público."""

    resolved: dict[str, str] = {}
    for layer in layers:
        if layer["type"] != "text":
            continue
        name = str(layer["font"])
        if name in resolved:
            continue
        font_file = _resolve_font_file(name)
        if font_file is None:
            raise plan_error("A fonte da camada não possui um arquivo local utilizável.")
        resolved[name] = str(font_file)
    return resolved


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


def _validate_profile(value: Any) -> tuple[str, int | None]:
    """Valida o perfil e devolve a proporção e o teto final (explícito ou padrão, só no 16:9).

    Roda antes de qualquer inspeção de mídia. Nas proporções não 16:9 o tamanho da full é fixo
    (`schema.FIXED_CANVAS_DIMENSIONS`), então `max_full_width` seria um segundo teto com outro
    significado e é recusado em vez de ignorado.
    """

    profile = require_object(value, "profile")
    reject_unknown_fields(profile, PROFILE_FIELDS, "profile")
    aspect_ratio = profile.get("aspect_ratio")
    if not isinstance(aspect_ratio, str) or aspect_ratio not in ASPECT_RATIOS:
        raise plan_error("O perfil de saída deve usar aspect_ratio 16:9, 9:16, 1:1 ou 4:5.")
    if aspect_ratio != LANDSCAPE_ASPECT_RATIO:
        if "max_full_width" in profile:
            raise plan_error("O campo profile.max_full_width só é aceito com aspect_ratio 16:9.")
        return aspect_ratio, None
    max_full_width = profile.get("max_full_width", DEFAULT_MAX_FULL_WIDTH)
    if (
        isinstance(max_full_width, bool)
        or not isinstance(max_full_width, int)
        or not MIN_MAX_FULL_WIDTH <= max_full_width <= MAX_MAX_FULL_WIDTH
        or max_full_width % MAX_FULL_WIDTH_STEP != 0
    ):
        raise plan_error(
            "O campo profile.max_full_width deve ser um inteiro múltiplo de 32 entre 32 e 3840."
        )
    return aspect_ratio, max_full_width


def _upscale_factor(
    segment: dict[str, Any],
    source: dict[str, Any],
    canvas_width: int,
    canvas_height: int,
) -> float:
    """Fator linear com que a fonte original é ampliada no canvas da full (acima de 1.0 amplia).

    Parte do enquadramento atual. Com `crop`, o recorte usado é o maior da proporção do canvas que
    cabe na fonte dividido pelo maior `zoom` dos keyframes (1.0 sem `reframe`, o recorte central),
    e o fator é a largura do canvas sobre a largura desse recorte (decisão 9 da spec). Com
    `contain` e `preserve` a imagem inteira cabe no canvas e o fator é o encolhimento ou a
    ampliação dessa escala. Em seguida multiplica pelo maior `zoom` entre `motion.from` e
    `motion.to` (decisão 15, R1): o `motion` é aplicado depois do enquadramento em qualquer `fit`
    e nunca convive com `reframe`, então só um dos dois fatores sai de 1.0. Sem `motion` o fator
    não muda; um `motion` só com zoom abaixo de 1.0 reduz o fator. Usa as dimensões da fonte
    original, que é o que a full lê; a preview e o proxy só reduzem.
    """

    source_width = float(source["video_width"])
    source_height = float(source["video_height"])
    if segment["fit"] != "crop":
        factor = min(canvas_width / source_width, canvas_height / source_height)
    else:
        reframe = segment.get("reframe")
        zoom = (
            max(item["zoom"] for item in reframe["keyframes"]) if reframe else DEFAULT_REFRAME_ZOOM
        )
        base_width = min(source_width, source_height * canvas_width / canvas_height)
        factor = canvas_width * zoom / base_width
    motion = segment.get("motion")
    if motion:
        factor *= max(motion["from"]["zoom"], motion["to"]["zoom"])
    return factor


def _calculated_profile(
    aspect_ratio: str,
    max_full_width: int | None,
    source_records: list[dict[str, Any]],
    timeline_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    """Monta `data.profile`: proporção, canvas da full e lista de ampliações.

    No 16:9 informa também o teto e a largura efetiva, sem ampliar a primeira fonte visual da
    timeline. `full_width`/`full_height` vêm de `schema.canvas_dimensions`, a mesma função que a
    full lê ao renderizar, para o envelope e o arquivo nunca divergirem. `upscale` lista, com o
    índice 1-based da timeline (o `index` de `timeline.segments[]`) e o fator arredondado a 2
    casas, os segmentos ampliados acima de 1.0; ampliar não bloqueia, só avisa o agente.
    """

    sources_by_id = {record["id"]: record for record in source_records}
    first_source = sources_by_id[timeline_segments[0]["video_source_id"]]
    source_width = first_source.get("video_width")
    if isinstance(source_width, bool) or not isinstance(source_width, int) or source_width <= 0:
        raise plan_error("A largura da primeira fonte visual não pôde ser determinada.")
    full_width, full_height = canvas_dimensions(
        aspect_ratio, source_width, max_full_width, final=True
    )
    profile: dict[str, Any] = {"aspect_ratio": aspect_ratio}
    if aspect_ratio == LANDSCAPE_ASPECT_RATIO:
        profile["max_full_width"] = max_full_width
        profile["effective_full_width"] = min(source_width, max_full_width)
    profile["full_width"] = full_width
    profile["full_height"] = full_height
    upscale: list[dict[str, Any]] = []
    for segment in timeline_segments:
        factor = round(
            _upscale_factor(
                segment, sources_by_id[segment["video_source_id"]], full_width, full_height
            ),
            2,
        )
        if factor > 1.0:
            upscale.append({"segment": segment["index"], "factor": factor})
    profile["upscale"] = upscale
    return profile


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


def _validate_reframe(video: dict[str, Any], location: str) -> dict[str, list[dict[str, Any]]]:
    """Valida e normaliza `segments[].video.reframe` (foco estático ou keyframes) sem tocar em mídia.

    Roda na fronteira estrutural, antes de qualquer FFprobe, e de novo em `_validate_video` para
    obter a forma normalizada (mesmo padrão do `motion`). Só usa campos do próprio segmento: o
    `fit`, a ausência de `motion` e a duração `out_s - in_s` contra a qual `t_s` é conferido.
    Normaliza `zoom` e `interpolation` para seus padrões e arredonda os números como o `motion`;
    a ordem estritamente crescente é checada sobre os `t_s` já arredondados, para dois instantes
    que só diferem além da 6ª casa não gerarem uma divisão por zero no grafo.
    """

    reframe = require_object(video["reframe"], f"{location}.reframe")
    reject_unknown_fields(reframe, REFRAME_FIELDS, f"{location}.reframe")
    keyframes = require_array(reframe.get("keyframes"), f"{location}.reframe.keyframes")
    if not 1 <= len(keyframes) <= MAX_REFRAME_KEYFRAMES:
        raise plan_error(
            f"O reenquadramento deve declarar de 1 a {MAX_REFRAME_KEYFRAMES} keyframes."
        )
    if video.get("fit") != "crop":
        raise plan_error("O reenquadramento exige fit crop.")
    if "motion" in video:
        raise plan_error("Um segmento não pode declarar motion e reframe ao mesmo tempo.")
    duration = _rounded(
        _finite_number(video.get("out_s"), f"{location}.out_s")
        - _finite_number(video.get("in_s"), f"{location}.in_s")
    )

    normalized: list[dict[str, Any]] = []
    for keyframe_value in keyframes:
        keyframe_location = f"{location}.reframe.keyframes[]"
        keyframe = require_object(keyframe_value, keyframe_location)
        reject_unknown_fields(keyframe, KEYFRAME_FIELDS, keyframe_location)
        t_s = _rounded(_finite_number(keyframe.get("t_s"), f"{keyframe_location}.t_s"))
        x = _finite_number(keyframe.get("x"), f"{keyframe_location}.x")
        y = _finite_number(keyframe.get("y"), f"{keyframe_location}.y")
        zoom = _finite_number(
            keyframe.get("zoom", DEFAULT_REFRAME_ZOOM), f"{keyframe_location}.zoom"
        )
        interpolation = keyframe.get("interpolation", DEFAULT_REFRAME_INTERPOLATION)
        if not isinstance(interpolation, str) or interpolation not in REFRAME_INTERPOLATIONS:
            raise plan_error("A interpolação do keyframe deve ser ease ou linear.")
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise plan_error("Os pontos x e y do keyframe devem estar entre 0 e 1.")
        if not REFRAME_ZOOM_MIN <= zoom <= REFRAME_ZOOM_MAX:
            raise plan_error("O zoom do keyframe deve estar entre 1.0 e 4.0.")
        if (
            t_s < 0
            or t_s > duration + ASPECT_TOLERANCE
            or (normalized and t_s <= normalized[-1]["t_s"])
        ):
            raise plan_error("Os keyframes devem ter t_s crescente dentro da duração do segmento.")
        normalized.append(
            {
                "t_s": t_s,
                "x": _rounded(x),
                "y": _rounded(y),
                "zoom": _rounded(zoom),
                "interpolation": interpolation,
            }
        )
    return {"keyframes": normalized}


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
        if "reframe" in video:
            _validate_reframe(video, "segments[].video")

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


def _validate_style_object(
    value: Any, location: str, allowed: frozenset[str], required: frozenset[str]
) -> dict[str, Any]:
    """Abre um objeto de estilo (`shadow` ou `outline`): tipo, allowlist e campos obrigatórios.

    `null` e qualquer valor fora de objeto são recusados (não existe "ausente" por `null`); a
    ausência do campo inteiro é tratada por quem chama. A ordem dos obrigatórios é alfabética para
    a mensagem ser determinística.
    """

    style = require_object(value, location)
    reject_unknown_fields(style, allowed, location)
    for name in sorted(required):
        if name not in style:
            raise plan_error(f"O campo {location}.{name} é obrigatório.")
    return style


def _style_integer(style: dict[str, Any], name: str, location: str, minimum: int, maximum: int) -> int:
    """Lê um inteiro de estilo em pixels; booleano, decimal e texto não valem como inteiro."""

    value = style[name]
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise plan_error(
            f"O campo {location}.{name} deve ser um inteiro entre {minimum} e {maximum}."
        )
    return value


def _style_color(style: dict[str, Any], location: str, default: str) -> str:
    """Lê a cor `#RRGGBB` de um estilo (ou o padrão); só cores que casam o padrão chegam ao grafo."""

    color = style.get("color", default)
    if not isinstance(color, str) or not HEX_COLOR_PATTERN.fullmatch(color):
        raise plan_error(f"O campo {location}.color deve estar no formato hexadecimal #RRGGBB.")
    return color


def _validate_text_style(layer: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Valida e normaliza `shadow` e `outline` de uma camada de texto, sem tocar em mídia.

    Devolve só os estilos declarados, já com os padrões aplicados (`color` preto e `opacity` 0.5 na
    sombra, `color` preto no contorno) e a opacidade arredondada como os demais números do plano.
    Os valores em pixels são os da largura da full; o grafo os escala na preview. O plano canônico
    e o hash continuam sendo os do documento escrito pelo operador, como no `reframe`.
    """

    normalized: dict[str, dict[str, Any]] = {}
    if "shadow" in layer:
        location = "layers[].shadow"
        shadow = _validate_style_object(layer["shadow"], location, SHADOW_FIELDS, SHADOW_REQUIRED_FIELDS)
        offset_x = _style_integer(shadow, "offset_x_px", location, SHADOW_OFFSET_MIN, SHADOW_OFFSET_MAX)
        offset_y = _style_integer(shadow, "offset_y_px", location, SHADOW_OFFSET_MIN, SHADOW_OFFSET_MAX)
        blur = _style_integer(shadow, "blur_px", location, SHADOW_BLUR_MIN, SHADOW_BLUR_MAX)
        color = _style_color(shadow, location, DEFAULT_SHADOW_COLOR)
        opacity = _finite_number(shadow.get("opacity", DEFAULT_SHADOW_OPACITY), f"{location}.opacity")
        if not SHADOW_OPACITY_MIN <= opacity <= SHADOW_OPACITY_MAX:
            raise plan_error(
                f"O campo {location}.opacity deve estar entre "
                f"{SHADOW_OPACITY_MIN:g} e {SHADOW_OPACITY_MAX:g}."
            )
        normalized["shadow"] = {
            "offset_x_px": offset_x,
            "offset_y_px": offset_y,
            "blur_px": blur,
            "color": color,
            "opacity": _rounded(opacity),
        }
    if "outline" in layer:
        location = "layers[].outline"
        outline = _validate_style_object(layer["outline"], location, OUTLINE_FIELDS, OUTLINE_REQUIRED_FIELDS)
        normalized["outline"] = {
            "width_px": _style_integer(outline, "width_px", location, OUTLINE_WIDTH_MIN, OUTLINE_WIDTH_MAX),
            "color": _style_color(outline, location, DEFAULT_OUTLINE_COLOR),
        }
    return normalized


def _validate_layers_structure(value: Any) -> tuple[list[dict[str, Any]], dict[int, dict[str, dict[str, Any]]]]:
    """Valida camadas v2, inclusive texto em coordenadas livres, sem consultar mídia.

    Devolve as camadas como escritas e, por índice de `layers[]`, o estilo normalizado
    (`shadow`/`outline`) das camadas de texto que o declaram; o grafo lê esse mapa em vez de
    reaplicar os padrões.
    """

    layers = require_array(value, "layers")
    if len(layers) > MAX_LAYERS:
        raise plan_error("O plano excede o limite de camadas permitido.")
    fields_by_type = {
        "text": TEXT_LAYER_FIELDS,
        "image": IMAGE_LAYER_FIELDS,
        "video": VIDEO_LAYER_FIELDS,
    }
    text_styles: dict[int, dict[str, dict[str, Any]]] = {}
    for layer_index, layer_value in enumerate(layers):
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
            text_style = _validate_text_style(layer)
            if text_style:
                text_styles[layer_index] = text_style
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
    return layers, text_styles


def _validate_plan_structure(
    document: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[int, dict[str, dict[str, Any]]],
    dict[str, Any],
]:
    """Fecha a fronteira estrutural v2 antes de qualquer chamada ao FFprobe."""

    _validate_source_structure(document.get("sources"))
    _validate_segment_structure(document.get("segments"))
    cuts = _validate_cuts_structure(document.get("cuts"))
    layers, text_styles = _validate_layers_structure(document.get("layers"))
    audio_processing = _validate_audio_processing(document.get("audio_processing"))
    return cuts, layers, text_styles, audio_processing


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
    aspect_ratio: str,
) -> tuple[dict[str, Any], media.MediaMetadata]:
    """Valida fonte visual, intervalo e ajuste de um segmento contra a proporção do perfil."""

    video = require_object(value, location)
    reject_unknown_fields(video, VIDEO_FIELDS, location)
    if "motion" in video:
        video = {**video, "motion": _validate_motion(video["motion"], f"{location}.motion")}
    if "reframe" in video:
        video = {**video, "reframe": _validate_reframe(video, location)}
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
    if fit == "preserve" and abs(source_ratio - aspect_ratio_value(aspect_ratio)) > ASPECT_TOLERANCE:
        raise plan_error(
            f"Uma fonte fora de {aspect_ratio} precisa declarar fit contain ou crop."
        )
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
    aspect_ratio: str,
) -> tuple[list[dict[str, Any]], float]:
    """Valida a timeline visual e depois aplica as janelas globais de áudio dos cortes.

    `aspect_ratio` é a proporção do perfil, contra a qual `fit: "preserve"` compara cada fonte.
    """

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
        video, _ = _validate_video(
            segment.get("video"), source_map, "segments[].video", aspect_ratio
        )
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
        if "reframe" in video:
            timeline_segment["reframe"] = video["reframe"]
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
    aspect_ratio, max_full_width = _validate_profile(document.get("profile"))
    cut_definitions, layer_definitions, text_styles, audio_processing = _validate_plan_structure(document)
    resolved_font_files = _resolve_layer_fonts(layer_definitions)
    source_map, source_records = _inspect_sources(root, document.get("sources"))
    timeline_segments, timeline_duration = _validate_segments(
        document.get("segments"),
        source_map,
        cut_definitions,
        aspect_ratio,
    )
    _validate_layer_intervals(layer_definitions, timeline_duration, source_map)
    profile = _calculated_profile(aspect_ratio, max_full_width, source_records, timeline_segments)
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
        "profile": profile,
        "sources": source_records,
        "audio_processing": audio_processing,
        "_resolved_font_files": resolved_font_files,
        # Estilo normalizado (`shadow`/`outline`) por índice de `layers[]`, lido pelo grafo; privado
        # como o caminho de fonte: o envelope público lista só `layer_count`.
        "_text_styles": text_styles,
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
    return {key: value for key, value in result.items() if not key.startswith("_")}
