"""Registro atômico da aprovação de um plano e de sua preview vigente."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from ..contracts import CliError, EXIT_CODES
from ..dependencies import is_regular_path
from ..plan.validation import load_validated_plan
from ..workdir import (
    prepare_approval_layout,
    publish_approval_atomic,
    relative_artifact_path,
)
from .hashes import sha256_file


def _error(code: str, message: str, exit_code: int) -> CliError:
    """Cria erros públicos estáveis para estados que impedem a aprovação."""

    return CliError(code=code, message=message, exit_code=exit_code)


def _approved_at() -> str:
    """Gera o instante da aprovação em UTC para manter o registro independente do host."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _serialize_approval(document: dict[str, Any]) -> bytes:
    """Serializa a aprovação em JSON compacto e estável antes da publicação atômica."""

    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash_sources(validation: dict[str, Any]) -> list[dict[str, str]]:
    """Calcula hashes na ordem do plano para preservar a identidade editorial das fontes."""

    source_hashes: list[dict[str, str]] = []
    for source in validation["sources"]:
        source_path = Path(str(source["path"]))
        if not is_regular_path(source_path):
            raise _error(
                "SOURCE_INVALID",
                "Uma fonte do plano não está disponível para aprovação.",
                EXIT_CODES["MEDIA"],
            )
        try:
            source_hash = sha256_file(source_path)
        except OSError as error:
            raise _error(
                "SOURCE_INVALID",
                "Não foi possível ler uma fonte do plano para aprovação.",
                EXIT_CODES["MEDIA"],
            ) from error
        source_hashes.append({"source_id": str(source["id"]), "sha256": source_hash})
    return source_hashes


def approve_workdir(workdir: str | Path) -> dict[str, Any]:
    """Valida o plano, exige uma preview regular e publica a aprovação vinculada aos hashes."""

    _, validation = load_validated_plan(workdir)
    layout = prepare_approval_layout(workdir)
    preview_path = layout.root / "preview.mp4"
    if not is_regular_path(preview_path):
        raise _error(
            "APPROVAL_REQUIRED",
            "Uma preview publicada é necessária antes da aprovação.",
            EXIT_CODES["STATE"],
        )

    try:
        preview_sha256 = sha256_file(preview_path)
    except OSError as error:
        raise _error(
            "APPROVAL_REQUIRED",
            "A preview publicada não pôde ser lida para aprovação.",
            EXIT_CODES["STATE"],
        ) from error

    approved_at = _approved_at()
    source_hashes = _hash_sources(validation)
    document = {
        "schema_version": 1,
        "approved_at": approved_at,
        "plan_sha256": str(validation["plan_sha256"]),
        "preview_sha256": preview_sha256,
        "sources": source_hashes,
    }
    publish_approval_atomic(layout, _serialize_approval(document))
    return {
        "artifact_path": str(layout.destination),
        "relative_artifact_path": relative_artifact_path(layout),
        "approved_at": approved_at,
        "plan_sha256": document["plan_sha256"],
        "preview_sha256": preview_sha256,
        "sources": source_hashes,
    }
