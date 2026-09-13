"""Registro atômico da aprovação de um plano e de sua preview vigente."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from ..contracts import CliError, EXIT_CODES, reject_duplicate_json_keys
from ..dependencies import is_regular_path
from ..plan.validation import load_validated_plan
from ..workdir import (
    prepare_approval_layout,
    publish_approval_atomic,
    relative_artifact_path,
)
from .hashes import sha256_file


MAX_APPROVAL_BYTES = 64 * 1024
"""Limite suficiente para o registro local e para impedir payloads arbitrários."""
_APPROVAL_FIELDS = {
    "schema_version",
    "approved_at",
    "plan_sha256",
    "preview_sha256",
    "sources",
}
_SOURCE_APPROVAL_FIELDS = {"source_id", "sha256"}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


def _approval_stale(message: str) -> CliError:
    """Cria o erro único usado quando a aprovação não autoriza mais o trabalho atual."""

    return _error("APPROVAL_STALE", message, EXIT_CODES["STATE"])


def _reject_non_finite_constant(value: str) -> None:
    """Impede constantes JSON não padrão de entrarem no estado de aprovação."""

    raise ValueError(value)


def _validate_sha256(value: object, field: str) -> None:
    """Valida o formato hexadecimal dos hashes persistidos no approval.json."""

    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise _approval_stale(f"O campo {field} da aprovação é inválido.")


def _read_approval(path: Path) -> dict[str, Any]:
    """Lê e valida estruturalmente o approval.json antes de comparar seus hashes."""

    if not path.exists():
        raise _error(
            "APPROVAL_REQUIRED",
            "Uma aprovação válida é necessária antes da renderização da full.",
            EXIT_CODES["STATE"],
        )
    if not is_regular_path(path):
        raise _approval_stale("O registro de aprovação não é um arquivo regular válido.")
    try:
        if path.stat().st_size > MAX_APPROVAL_BYTES:
            raise _approval_stale("O registro de aprovação excede o limite permitido.")
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_constant,
        )
    except CliError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise _approval_stale("O registro de aprovação não contém JSON válido.") from error

    if not isinstance(payload, dict) or set(payload) != _APPROVAL_FIELDS:
        raise _approval_stale("O registro de aprovação não possui o schema esperado.")
    if isinstance(payload.get("schema_version"), bool) or payload.get("schema_version") != 1:
        raise _approval_stale("O registro de aprovação usa uma versão incompatível.")

    approved_at = payload.get("approved_at")
    if not isinstance(approved_at, str) or len(approved_at) > 64:
        raise _approval_stale("O instante da aprovação é inválido.")
    try:
        parsed_at = datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise _approval_stale("O instante da aprovação é inválido.") from error
    if parsed_at.tzinfo is None or parsed_at.utcoffset() != timezone.utc.utcoffset(parsed_at):
        raise _approval_stale("O instante da aprovação deve estar em UTC.")

    _validate_sha256(payload.get("plan_sha256"), "plan_sha256")
    _validate_sha256(payload.get("preview_sha256"), "preview_sha256")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise _approval_stale("A lista de fontes da aprovação é inválida.")
    for source in sources:
        if not isinstance(source, Mapping) or set(source) != _SOURCE_APPROVAL_FIELDS:
            raise _approval_stale("Uma fonte do registro de aprovação é inválida.")
        if not isinstance(source.get("source_id"), str):
            raise _approval_stale("Uma fonte do registro de aprovação é inválida.")
        _validate_sha256(source.get("sha256"), "sources.sha256")
    return payload


def verify_approval(
    workdir: str | Path,
    validation: dict[str, Any],
) -> dict[str, Any]:
    """Recalcula plano, preview e fontes e libera a full somente com hashes coincidentes."""

    layout = prepare_approval_layout(workdir)
    approval = _read_approval(layout.destination)
    preview_path = layout.root / "preview.mp4"
    if not is_regular_path(preview_path):
        raise _approval_stale("A preview vinculada à aprovação não está disponível.")

    try:
        preview_sha256 = sha256_file(preview_path)
        source_hashes = _hash_sources(validation)
    except (CliError, OSError) as error:
        raise _approval_stale(
            "A aprovação não pôde ser comparada com os artefatos atuais."
        ) from error

    if (
        approval["plan_sha256"] != validation["plan_sha256"]
        or approval["preview_sha256"] != preview_sha256
        or approval["sources"] != source_hashes
    ):
        raise _approval_stale(
            "A aprovação não corresponde ao plano, à preview ou às fontes atuais."
        )
    return approval


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
