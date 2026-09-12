"""Contrato de descoberta local usado pelo comando ``capabilities``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import dependencies
from .contracts import CLI_VERSION


MAX_SOURCE_SIZE_BYTES = 4 * 1024**3
MAX_SOURCE_DURATION_SECONDS = 3_600


def _default_model_root() -> Path:
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


def _cuda_dependency(gpu: dict[str, Any], whisper: dict[str, Any]) -> dict[str, Any]:
    """Classifica CUDA como utilizável apenas quando hardware e backend local estão prontos."""

    if gpu["status"] != "available":
        return {
            "status": gpu["status"],
            "reason": "GPU NVIDIA não foi detectada ou não respondeu à sondagem local",
        }
    if whisper["status"] == "available":
        return {
            "status": "available",
            "reason": "GPU NVIDIA e backend Whisper local estão disponíveis",
        }
    return {
        "status": whisper["status"],
        "reason": "backend Whisper local é necessário para usar CUDA na transcrição",
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
    cuda = _cuda_dependency(gpu, whisper)
    models = dependencies.discover_models(model_dir or _default_model_root())
    recommendation = choose_recommendation(models, cuda["status"])

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
            "unsupported",
            [],
            "validação de plano será implementada em uma task posterior",
        ),
        "render_preview": _operation(
            "unsupported",
            [],
            "renderização será implementada em uma task posterior",
        ),
        "approve": _operation(
            "unsupported",
            [],
            "aprovação será implementada em uma task posterior",
        ),
        "render_full": _operation(
            "unsupported",
            [],
            "renderização será implementada em uma task posterior",
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
            "jobs_in_parallel": 1,
        },
        "operations": operations,
        "recommendation": recommendation,
    }
