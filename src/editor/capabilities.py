"""Contrato de descoberta local usado pelo comando ``capabilities``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import dependencies
from .analysis.audio import MAX_AUDIO_WINDOW_SECONDS, MAX_AUDIO_WINDOWS
from .contracts import CLI_VERSION
from .media import (
    IMAGE_FORMATS,
    MAX_IMAGE_DIMENSION,
    MAX_IMAGE_SIZE_BYTES,
    MAX_SOURCE_DURATION_SECONDS,
    MAX_SOURCE_SIZE_BYTES,
)
from .plan.schema import (
    CUT_TYPES,
    DEFAULT_MAX_FULL_WIDTH,
    LAYER_FIT_MODES,
    LAYER_TYPES,
    MAX_CUT_DURATION_SECONDS,
    MAX_FULL_WIDTH_STEP,
    MAX_MAX_FULL_WIDTH,
    MIN_MAX_FULL_WIDTH,
    MOTION_ZOOM_MAX,
    MOTION_ZOOM_MIN,
    PLAN_SCHEMA_VERSION,
    SOURCE_KINDS,
)
from .plan.validation import (
    FONT_FILE_EXTENSIONS,
    MAX_LAYERS,
    MAX_PLAN_BYTES,
    MAX_SEGMENTS,
    MAX_TIMELINE_DURATION_SECONDS,
)
from .render.preview import GPU_MAX_VIDEO_INPUTS, MAX_PREVIEW_DIMENSION, PROXY_CHOICES
from .render.proxy import (
    PROXY_CRF,
    PROXY_GOP,
    PROXY_MAX_HEIGHT,
    PROXY_MAX_WIDTH,
    PROXY_PARAMETERS_VERSION,
    PROXY_PIXEL_FORMAT,
    PROXY_PRESET,
    PROXY_VIDEO_ENCODER,
)
from .render.windows import (
    END_SLACK_S,
    MAX_VIDEO_INPUTS,
    MAX_WINDOWS_PER_SOURCE,
    MERGE_GAP_S,
    MIN_READ_REDUCTION,
)
from .workdir import PROXY_DIRECTORY

def default_model_root() -> Path:
    """Seleciona o diretório de modelos configurado ou o padrão local do repositório."""

    configured = os.environ.get("EDITOR_MODEL_DIR")
    if configured:
        return Path(configured)

    working_directory = Path.cwd() / "models"
    if working_directory.exists():
        return working_directory
    return Path(__file__).resolve().parents[2] / "models"


def _combine_statuses(*statuses: str) -> str:
    """Propaga o pior estado de dependências sem transformar ausência em falso suporte."""

    if all(status == "available" for status in statuses):
        return "available"
    if any(status == "unsupported" for status in statuses):
        return "unsupported"
    return "missing"


def choose_recommendation(models: dict[str, Any], cuda_status: str) -> dict[str, Any]:
    """Escolhe dispositivo e modelo somente entre recursos locais já validados."""

    available = set(models["available"])
    if cuda_status == "available" and "small" in available:
        return {
            "status": "recommended",
            "device": "cuda",
            "model": "small",
            "reason": "CUDA e o modelo small estão disponíveis localmente",
        }
    if "base" in available:
        return {
            "status": "recommended",
            "device": "cpu",
            "model": "base",
            "reason": "CPU é o baseline local e o modelo base está disponível",
        }
    if "tiny" in available:
        return {
            "status": "recommended",
            "device": "cpu",
            "model": "tiny",
            "reason": "tiny é o fallback local determinístico disponível",
        }
    if "small" in available:
        return {
            "status": "recommended",
            "device": "cpu",
            "model": "small",
            "reason": "small é o único modelo local disponível e será executado em CPU",
        }
    return {
        "status": "missing",
        "device": "cpu",
        "model": None,
        "reason": "nenhum modelo Whisper local válido foi encontrado",
    }


def _cuda_dependency(
    gpu: dict[str, Any],
    whisper: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    """Classifica CUDA somente quando hardware, backend e runtime local estão prontos."""

    if gpu["status"] != "available":
        return {
            "status": gpu["status"],
            "reason": "GPU NVIDIA não foi detectada ou não respondeu à sondagem local",
            "runtime": runtime,
        }
    if whisper["status"] == "available":
        if runtime["status"] == "available":
            return {
                "status": "available",
                "reason": "GPU NVIDIA, backend Whisper e runtime CUDA local estão disponíveis",
                "runtime": runtime,
            }
        return {
            "status": runtime["status"],
            "reason": "o runtime CUDA local não está utilizável pelo backend de transcrição",
            "runtime": runtime,
        }
    return {
        "status": whisper["status"],
        "reason": "backend Whisper local é necessário para usar CUDA na transcrição",
        "runtime": runtime,
    }


def _operation(status: str, requires: list[str], reason: str) -> dict[str, Any]:
    """Cria o registro uniforme de uma operação e suas dependências observadas."""

    return {"status": status, "requires": requires, "reason": reason}


def collect_capabilities(model_dir: Path | None = None) -> dict[str, Any]:
    """Coleta capacidades locais sem editar arquivos, baixar componentes ou acessar a rede."""

    system = dependencies.detect_system()
    ffmpeg = dependencies.detect_ffmpeg()
    whisper = dependencies.detect_whisper_backend()
    gpu = dependencies.detect_gpu()
    cuda_runtime = (
        dependencies.detect_cuda_runtime()
        if gpu["status"] == "available"
        else {
            "status": gpu["status"],
            "device_count": 0,
            "reason": "GPU NVIDIA não está disponível para sondar o runtime CUDA",
        }
    )
    cuda = _cuda_dependency(gpu, whisper, cuda_runtime)
    # Sonda Metal best effort; ausência nunca falha a jornada e nunca muda a recomendação nesta fatia.
    metal = dependencies.detect_metal()
    models = dependencies.discover_models(model_dir or default_model_root())
    recommendation = choose_recommendation(models, cuda["status"])

    # O inventário precisa refletir o contrato v2 e os comandos reais, mas sem
    # declarar como disponíveis operações que dependem de componentes ausentes.
    ffprobe_status = ffmpeg["tools"]["ffprobe"]["status"]
    render_status = _combine_statuses(ffmpeg["status"], ffmpeg["encoders"]["status"])
    render_requires = ["ffmpeg", "ffprobe", *dependencies.RENDER_ENCODERS]
    max_cut_boundaries = max(0, MAX_SEGMENTS - 1)

    # `proxy build` só codifica vídeo (o áudio é copiado), então exige libx264 e não o AAC dos renders.
    proxy_build_status = (
        ffmpeg["status"]
        if ffmpeg["status"] != "available"
        else "available"
        if PROXY_VIDEO_ENCODER in ffmpeg["encoders"]["available"]
        else "unsupported"
    )

    transcribe_status = _combine_statuses(whisper["status"], models["status"])
    operations = {
        "capabilities": _operation("available", [], "consulta local sem efeitos colaterais"),
        "init": _operation(
            "available",
            [],
            "prepara a estrutura técnica do trabalho sem criar estado editorial",
        ),
        "analyze_transcribe": _operation(
            transcribe_status,
            ["faster-whisper", "ctranslate2", "modelo local"],
            "backend e modelo Whisper local são necessários",
        ),
        "analyze_audio": _operation(
            ffmpeg["status"],
            ["ffmpeg", "ffprobe"],
            "FFmpeg e FFprobe são necessários para analisar áudio",
        ),
        "extract_frames": _operation(
            ffmpeg["status"],
            ["ffmpeg", "ffprobe"],
            "FFmpeg e FFprobe são necessários para extrair frames",
        ),
        "plan_validate": _operation(
            ffprobe_status,
            ["ffprobe"],
            "validação local de plan.json e das fontes declaradas",
        ),
        "render_preview": _operation(
            render_status,
            render_requires,
            "renderização linear da preview com publicação atômica",
        ),
        "approve": _operation(
            ffprobe_status,
            ["ffprobe"],
            "aprovação local vinculada aos hashes do plano, preview e fontes",
        ),
        "render_full": _operation(
            render_status,
            render_requires,
            "renderização final condicionada à aprovação vigente",
        ),
        "proxy_build": _operation(
            proxy_build_status,
            ["ffmpeg", "ffprobe", PROXY_VIDEO_ENCODER],
            "cria ou reutiliza proxies leves das fontes de vídeo grandes, só por comando explícito",
        ),
    }

    return {
        "cli_version": CLI_VERSION,
        "system": {
            **system,
            "gpu": gpu,
        },
        "dependencies": {
            "ffmpeg": ffmpeg,
            "whisper": whisper,
            "cuda": cuda,
        },
        "models": models,
        "devices": {
            "auto": {
                "status": "available",
                "resolves_to": recommendation["device"],
                "reason": "seleção automática usa a recomendação local",
            },
            "cpu": {
                "status": "available",
                "recommended": recommendation["device"] == "cpu",
                "reason": "CPU é o baseline obrigatório do MVP",
            },
            "cuda": {
                "status": cuda["status"],
                "recommended": recommendation["device"] == "cuda",
                "gpu_count": len(gpu["devices"]),
                "reason": cuda["reason"],
            },
            # Metal é só reporte nesta fatia; o compute garantido permanece CPU int8 (ou CUDA quando recomendado).
            "metal": {
                "status": metal["status"],
                "recommended": False,
                "reason": metal["reason"],
            },
        },
        "formats": {
            "status": "available",
            "video": ["mp4"],
            "audio": ["mp3", "wav"],
            "image": [suffix.removeprefix(".") for suffix in IMAGE_FORMATS],
            "codecs": {
                "video": ["h264"],
                "audio": ["aac", "mp3", "pcm_s16le"],
                "image": ["png", "mjpeg", "webp"],
            },
        },
        "plan": {
            "schema_version": PLAN_SCHEMA_VERSION,
            "source_kinds": sorted(SOURCE_KINDS),
            "profile": {
                "aspect_ratio": "16:9",
                "max_full_width_default": DEFAULT_MAX_FULL_WIDTH,
                "max_full_width_min": MIN_MAX_FULL_WIDTH,
                "max_full_width_max": MAX_MAX_FULL_WIDTH,
                "max_full_width_step": MAX_FULL_WIDTH_STEP,
            },
            "motion": {
                "status": "available",
                "interpolation": "linear",
                "zoom_min": MOTION_ZOOM_MIN,
                "zoom_max": MOTION_ZOOM_MAX,
            },
            "cuts": {
                "types": sorted(CUT_TYPES),
                "max_boundaries": max_cut_boundaries,
                "max_duration_seconds": MAX_CUT_DURATION_SECONDS,
            },
            "layers": {
                "types": sorted(LAYER_TYPES),
                "fit_modes": sorted(LAYER_FIT_MODES),
                "max_count": MAX_LAYERS,
            },
        },
        "render": {
            "status": "available",
            # O grafo nunca volta à linha de comando; o FFmpeg lê o filtro UTF-8 por arquivo.
            # A flag e o teto variam por sistema e são reportados para agentes não chutarem.
            "filter_transport": "file",
            "filter_option": dependencies.ffmpeg_filter_option(),
            "max_command_characters": dependencies.max_command_characters(),
            "preview_max_dimension": MAX_PREVIEW_DIMENSION,
            "full_default_max_width": DEFAULT_MAX_FULL_WIDTH,
            # `--accel gpu` é opt-in: "available" significa que o FFmpeg expõe NVENC e decode CUDA;
            # a GPU só é confirmada ao renderizar, e a falha cai para CPU (campo video.accel do envelope).
            "gpu_acceleration": {
                "status": (
                    "available"
                    if ffmpeg["tools"]["ffmpeg"]["status"] == "available"
                    and dependencies.gpu_acceleration_available(Path(ffmpeg["tools"]["ffmpeg"]["path"]))
                    else "unavailable"
                ),
                "flag": "--accel gpu",
                "encoder": dependencies.GPU_VIDEO_ENCODER,
                "decoder": dependencies.GPU_HWACCEL,
                "fallback": "cpu",
            },
            # Decodificação seletiva (phase 4, F1): ligada por padrão e sem flag, pois a saída é
            # provada equivalente. Os tetos vêm do planejador `render/windows.py`; se o render
            # janelado falhar (exceto timeout) ele é repetido uma vez sem janelas. `data.decode`
            # do envelope informa `windowed`, `inputs` e `sources` do grafo publicado.
            "decode_windows": {
                "status": "available",
                "default": "on",
                "applies_to": ["render_preview", "render_full"],
                "source_kinds": ["video"],
                "merge_gap_seconds": MERGE_GAP_S,
                "end_slack_seconds": END_SLACK_S,
                "max_windows_per_source": MAX_WINDOWS_PER_SOURCE,
                "max_video_inputs": MAX_VIDEO_INPUTS,
                "min_read_reduction": MIN_READ_REDUCTION,
                "gpu_decode_max_video_inputs": GPU_MAX_VIDEO_INPUTS,
                "fallback": "unwindowed",
                "envelope": "decode",
            },
            # Proxies (phase 4, F2/F3): `proxy build` é sempre explícito e só a preview os lê,
            # por `--proxy` (padrão `auto`); a full e a aprovação usam sempre os originais.
            # Parâmetros reais de `render/proxy.py`; `envelope` aponta `data.proxy` da preview.
            "proxy": {
                "status": proxy_build_status,
                "flag": "--proxy",
                "modes": list(PROXY_CHOICES),
                "default": "auto",
                "applies_to": ["render_preview"],
                "build_command": "proxy build",
                "automatic_build": False,
                "directory": PROXY_DIRECTORY,
                "eligibility": {
                    "source_kinds": ["video"],
                    "width_greater_than": PROXY_MAX_WIDTH,
                    "height_greater_than": PROXY_MAX_HEIGHT,
                    "match": "any_dimension",
                },
                "parameters": {
                    "version": PROXY_PARAMETERS_VERSION,
                    "max_width": PROXY_MAX_WIDTH,
                    "max_height": PROXY_MAX_HEIGHT,
                    "upscale": False,
                    "encoder": PROXY_VIDEO_ENCODER,
                    "preset": PROXY_PRESET,
                    "pixel_format": PROXY_PIXEL_FORMAT,
                    "gop": PROXY_GOP,
                    "crf": PROXY_CRF,
                    "audio": "copy",
                },
                "envelope": "proxy",
            },
        },
        "transcription": {
            "status": "available",
            # Tolerância observada no Whisper real; deriva maior falha sem
            # substituir o artefato vigente, com textos reconstruídos das palavras.
            "word_drift_tolerance_seconds": 0.5,
            "counters": [
                "discarded_word_count",
                "clamped_word_count",
                "discarded_segment_count",
            ],
        },
        "fonts": {
            "status": "available",
            # O plano aceita somente o nome humano; o validador resolve o
            # arquivo concreto e o grafo desenha com drawtext=fontfile=...
            # A origem varia por sistema e o fallback silencioso é sempre rejeitado.
            "input": "name",
            "renderer": "fontfile",
            "extensions": sorted(FONT_FILE_EXTENSIONS),
            "source": dependencies.font_lookup_source(),
            "registry": dependencies.font_registry_hives(),
            "fallback": "reject",
        },
        "limits": {
            "max_plan_bytes": MAX_PLAN_BYTES,
            "max_source_size_bytes": MAX_SOURCE_SIZE_BYTES,
            "max_source_duration_seconds": MAX_SOURCE_DURATION_SECONDS,
            "max_audio_window_seconds": MAX_AUDIO_WINDOW_SECONDS,
            "max_audio_windows": MAX_AUDIO_WINDOWS,
            "max_segments": MAX_SEGMENTS,
            "max_layers": MAX_LAYERS,
            "max_cuts": max_cut_boundaries,
            "max_cut_duration_seconds": MAX_CUT_DURATION_SECONDS,
            "motion_zoom_min": MOTION_ZOOM_MIN,
            "motion_zoom_max": MOTION_ZOOM_MAX,
            "max_image_size_bytes": MAX_IMAGE_SIZE_BYTES,
            "max_image_dimension": MAX_IMAGE_DIMENSION,
            "max_timeline_duration_seconds": MAX_TIMELINE_DURATION_SECONDS,
            "max_full_width_default": DEFAULT_MAX_FULL_WIDTH,
            "max_full_width_min": MIN_MAX_FULL_WIDTH,
            "max_full_width_max": MAX_MAX_FULL_WIDTH,
            "max_full_width_step": MAX_FULL_WIDTH_STEP,
            "max_command_characters": dependencies.max_command_characters(),
            "whisper_word_drift_tolerance_seconds": 0.5,
            "jobs_in_parallel": 1,
        },
        "operations": operations,
        "recommendation": recommendation,
    }
