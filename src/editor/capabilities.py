"""Contrato de descoberta local usado pelo comando ``capabilities``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import dependencies
from .analysis.audio import MAX_AUDIO_WINDOW_SECONDS, MAX_AUDIO_WINDOWS
from .contracts import CLI_VERSION
from .media import MAX_SOURCE_DURATION_SECONDS, MAX_SOURCE_SIZE_BYTES
from .plan.validation import (
    MAX_OVERLAYS,
    MAX_SEGMENTS,
    MAX_TIMELINE_DURATION_SECONDS,
)


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
    models = dependencies.discover_models(model_dir or default_model_root())
    recommendation = choose_recommendation(models, cuda["status"])

    # A capacidade reportada precisa refletir os comandos existentes, mas sem
    # declarar operações que ainda não têm a dependência externa necessária.
    ffprobe_status = ffmpeg["tools"]["ffprobe"]["status"]
    render_status = _combine_statuses(ffmpeg["status"], ffmpeg["encoders"]["status"])
    render_requires = ["ffmpeg", "ffprobe", *dependencies.RENDER_ENCODERS]

    transcribe_status = _combine_statuses(whisper["status"], models["status"])
    operations = {
        "capabilities": _operation("available", [], "consulta local sem efeitos colaterais"),
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
        },
        "formats": {
            "status": "available",
            "video": ["mp4"],
            "audio": ["mp3", "wav"],
            "codecs": {
                "video": ["h264"],
                "audio": ["aac", "mp3", "pcm_s16le"],
            },
        },
        "limits": {
            "max_source_size_bytes": MAX_SOURCE_SIZE_BYTES,
            "max_source_duration_seconds": MAX_SOURCE_DURATION_SECONDS,
            "max_audio_window_seconds": MAX_AUDIO_WINDOW_SECONDS,
            "max_audio_windows": MAX_AUDIO_WINDOWS,
            "max_segments": MAX_SEGMENTS,
            "max_overlays": MAX_OVERLAYS,
            "max_timeline_duration_seconds": MAX_TIMELINE_DURATION_SECONDS,
            "jobs_in_parallel": 1,
        },
        "operations": operations,
        "recommendation": recommendation,
    }
