"""Ponte local e lazy entre a análise e o backend faster-whisper."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import dependencies
from .capabilities import choose_recommendation, default_model_root
from .contracts import CliError, EXIT_CODES


@dataclass(frozen=True)
class WhisperConfig:
    """Descreve o modelo e o dispositivo já validados antes de carregar o backend."""

    model_name: str
    model_path: Path
    device: str
    compute_type: str


def _error(code: str, message: str) -> CliError:
    """Cria erro público de dependência sem expor diagnóstico do runtime externo."""

    return CliError(code=code, message=message, exit_code=EXIT_CODES["DEPENDENCY"])


def _model_by_name(models: dict[str, Any], name: str) -> Path | None:
    """Obtém somente o caminho de um modelo inventariado como disponível localmente."""

    for item in models.get("models", []):
        if item.get("name") == name and item.get("status") == "available":
            return Path(str(item["path"]))
    return None


def _explicit_model_path(reference: str) -> Path | None:
    """Valida uma referência de caminho local sem aceitar árvore de modelo linkada."""

    candidate = Path(reference).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = Path(os.path.normpath(os.fspath(candidate)))
    if dependencies.is_valid_model_directory(candidate):
        return candidate
    return None


def _select_model(
    reference: str | None,
    models: dict[str, Any],
    device: str,
) -> tuple[str, Path]:
    """Seleciona um modelo existente, sem permitir nome remoto ou download implícito."""

    if reference:
        normalized = reference.strip().lower()
        if normalized in dependencies.ALLOWED_MODEL_NAMES:
            path = _model_by_name(models, normalized)
            if path is not None:
                return normalized, path
        else:
            path = _explicit_model_path(reference.strip())
            if path is not None:
                return path.name, path
        raise _error("MODEL_MISSING", "O modelo Whisper local não foi encontrado ou é inválido.")

    cuda_status = "available" if device == "cuda" else "missing"
    recommendation = choose_recommendation(models, cuda_status)
    model_name = recommendation.get("model")
    if not isinstance(model_name, str):
        raise _error("MODEL_MISSING", "Nenhum modelo Whisper local válido está disponível.")
    model_path = _model_by_name(models, model_name)
    if model_path is None:
        raise _error("MODEL_MISSING", "Nenhum modelo Whisper local válido está disponível.")
    return model_name, model_path


def resolve_config(model: str | None, device: str) -> WhisperConfig:
    """Resolve backend, dispositivo e modelo usando somente recursos locais já detectados."""

    if device not in {"auto", "cpu", "cuda"}:
        raise CliError(
            code="ARGUMENT_INVALID",
            message="O dispositivo deve ser auto, cpu ou cuda.",
            exit_code=EXIT_CODES["ARGUMENT"],
        )

    models = dependencies.discover_models(default_model_root())
    gpu = dependencies.detect_gpu()
    runtime = (
        dependencies.detect_cuda_runtime()
        if gpu["status"] == "available"
        else {"status": gpu["status"], "device_count": 0}
    )
    backend = dependencies.detect_whisper_backend()
    cuda_status = "available"
    if gpu["status"] != "available":
        cuda_status = gpu["status"]
    elif runtime["status"] != "available":
        cuda_status = runtime["status"]
    elif backend["status"] != "available":
        cuda_status = backend["status"]

    if device == "cuda" and cuda_status != "available":
        raise _error("DEVICE_UNAVAILABLE", "O dispositivo CUDA não está disponível localmente.")

    if device == "auto":
        recommendation = choose_recommendation(models, cuda_status)
        resolved_device = str(recommendation["device"])
    else:
        resolved_device = device

    model_name, model_path = _select_model(model, models, resolved_device)
    if backend["status"] != "available":
        raise _error(
            "DEPENDENCY_MISSING",
            "O backend local faster-whisper/CTranslate2 não está disponível.",
        )

    return WhisperConfig(
        model_name=model_name,
        model_path=model_path,
        device=resolved_device,
        compute_type="float16" if resolved_device == "cuda" else "int8",
    )


def _load_whisper_model(config: WhisperConfig) -> Any:
    """Carrega o modelo somente a partir do diretório local, bloqueando downloads do hub."""

    try:
        from faster_whisper import WhisperModel
    except (ImportError, ModuleNotFoundError) as error:
        raise _error(
            "DEPENDENCY_MISSING",
            "O backend local faster-whisper/CTranslate2 não está disponível.",
        ) from error

    try:
        return WhisperModel(
            str(config.model_path),
            device=config.device,
            compute_type=config.compute_type,
            local_files_only=True,
        )
    except Exception as error:
        if config.device == "cuda":
            raise _error(
                "DEVICE_UNAVAILABLE",
                "O dispositivo CUDA não pôde iniciar o backend local.",
            ) from error
        raise _error(
            "MODEL_MISSING",
            "O modelo Whisper local não pôde ser carregado.",
        ) from error


def transcribe(
    config: WhisperConfig,
    input_path: Path,
    language: str | None,
) -> tuple[list[Any], Any]:
    """Executa a transcrição local e materializa os segmentos antes da publicação."""

    model_instance = _load_whisper_model(config)
    try:
        segments, info = model_instance.transcribe(
            str(input_path),
            language=language,
            word_timestamps=True,
        )
        return list(segments), info
    except CliError:
        raise
    except Exception as error:
        raise CliError(
            code="ANALYSIS_FAILED",
            message="A transcrição local falhou.",
            exit_code=EXIT_CODES["OPERATION"],
        ) from error
