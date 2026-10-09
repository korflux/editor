"""Registro atômico da aprovação v2 (plano e fontes), sua forma condicionada e sua verificação.

A aprovação vincula o hash canônico do plano e o SHA-256 de cada fonte declarada. Ela não olha
`preview.mp4`: a preview é só uma exportação opcional e nunca decide se a full pode ser gerada.

Pontos de entrada:
- `approve_workdir`: caminho da CLI, aprova o estado atual do trabalho;
- `approve_workdir_if_unchanged`: caminho do servidor da interface, aprova só a versão que o
  operador viu, recusando com `APPROVAL_PLAN_CHANGED` se plano ou fontes mudaram desde o carregamento;
- `sources_fingerprint`: impressão leve das fontes, fonte única para o estado exibido e para a recusa;
- `verify_approval`: barreira da full, recalcula os hashes e libera ou recusa com `APPROVAL_STALE`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

from ..contracts import CliError, EXIT_CODES, reject_duplicate_json_keys
from ..dependencies import is_regular_path
from ..plan.canonical import canonical_sha256
from ..plan.validation import load_validated_plan
from ..workdir import (
    prepare_approval_layout,
    publish_approval_atomic,
    relative_artifact_path,
)
from .hashes import sha256_file


MAX_APPROVAL_BYTES = 64 * 1024
"""Limite suficiente para o registro local e para impedir payloads arbitrários."""
APPROVAL_SCHEMA_VERSION = 2
"""Versão do `approval.json`: a v1 (que vinculava `preview_sha256`) é tratada como obsoleta."""
_APPROVAL_FIELDS = {
    "schema_version",
    "approved_at",
    "plan_sha256",
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
    """Calcula hash de toda fonte declarada, na ordem do plano, para preservar a identidade editorial."""

    source_hashes: list[dict[str, str]] = []
    # A aprovação vincula todas as declarações do plano, inclusive fontes usadas
    # somente por camadas ou ainda não consumidas pelo grafo, para que nenhuma
    # mutação de imagem, vídeo ou áudio passe pela barreira da full.
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


def sources_fingerprint(validation: Mapping[str, Any]) -> str:
    """Impressão leve das fontes do plano validado: SHA-256 da lista canônica de tamanho e `mtime_ns`.

    É a fonte única da impressão: o estado exibido pela interface a entrega no carregamento e a
    aprovação condicionada a recalcula (por esta mesma função) para detectar fonte trocada sem
    ler a mídia inteira. A lista segue a ordem do plano, com `source_id`, `size_bytes` e `mtime_ns`
    lidos do arquivo no momento da chamada, não do que a validação sondou antes.

    ponytail: tamanho e `mtime_ns` não detectam uma troca que preserve os dois, usar hash parcial
    do conteúdo na impressão se esse caso aparecer; o SHA-256 completo gravado na aprovação e
    conferido pela full continua cobrindo a identidade do conteúdo.
    """

    entries: list[dict[str, Any]] = []
    for source in validation["sources"]:
        try:
            metadata = os.stat(Path(str(source["path"])))
        except OSError as error:
            raise _error(
                "SOURCE_INVALID",
                "Não foi possível ler uma fonte do plano para aprovação.",
                EXIT_CODES["MEDIA"],
            ) from error
        entries.append(
            {
                "source_id": str(source["id"]),
                "size_bytes": metadata.st_size,
                "mtime_ns": metadata.st_mtime_ns,
            }
        )
    return canonical_sha256(entries)


def _plan_changed() -> CliError:
    """Recusa pública da aprovação condicionada, usada quando a versão exibida deixou de ser a atual."""

    return _error(
        "APPROVAL_PLAN_CHANGED",
        "O plano mudou desde que foi carregado. Revise a versão atual antes de aprovar.",
        EXIT_CODES["STATE"],
    )


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
    """Lê e valida estruturalmente o approval.json v2 antes de comparar seus hashes."""

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

    if not isinstance(payload, dict):
        raise _approval_stale("O registro de aprovação não possui o schema esperado.")
    # A versão vem antes dos campos: um registro v1 (com `preview_sha256`) deve dizer que é obsoleto
    # e pedir nova aprovação, em vez de parecer apenas malformado.
    if (
        isinstance(payload.get("schema_version"), bool)
        or payload.get("schema_version") != APPROVAL_SCHEMA_VERSION
    ):
        raise _approval_stale("O registro de aprovação usa uma versão obsoleta. Aprove o plano novamente.")
    if set(payload) != _APPROVAL_FIELDS:
        raise _approval_stale("O registro de aprovação não possui o schema esperado.")

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
    """Recalcula plano e fontes e libera a full somente com hashes coincidentes.

    Não consulta `preview.mp4`: a aprovação v2 vincula só o plano e as fontes. Qualquer divergência,
    registro v1 ou registro corrompido vira `APPROVAL_STALE`; a ausência do arquivo é
    `APPROVAL_REQUIRED`.
    """

    layout = prepare_approval_layout(workdir)
    approval = _read_approval(layout.destination)

    try:
        source_hashes = _hash_sources(validation)
    except (CliError, OSError) as error:
        raise _approval_stale(
            "A aprovação não pôde ser comparada com os artefatos atuais."
        ) from error

    if (
        approval["plan_sha256"] != validation["plan_sha256"]
        or approval["sources"] != source_hashes
    ):
        raise _approval_stale(
            "A aprovação não corresponde ao plano ou às fontes atuais."
        )
    return approval


def _approve(workdir: str | Path, expected: tuple[str, str] | None) -> dict[str, Any]:
    """Valida o plano, calcula os hashes das fontes e publica `approval.json` v2 atomicamente.

    `expected` é `(plan_sha256, sources_fingerprint)` da versão que o chamador exibiu, ou `None` para
    aprovar o estado atual sem condição. Com `expected`, a divergência de qualquer um dos dois é
    recusada com `APPROVAL_PLAN_CHANGED` antes de gravar. A impressão é conferida duas vezes: antes
    do cálculo dos SHA-256 (recusa barata) e depois dele, porque o cálculo lê as fontes por
    inteiro e uma troca nesse intervalo gravaria o hash de uma fonte que o operador não viu.
    """

    _, validation = load_validated_plan(workdir)
    layout = prepare_approval_layout(workdir)
    if expected is not None:
        expected_plan_sha256, expected_fingerprint = expected
        if (
            validation["plan_sha256"] != expected_plan_sha256
            or sources_fingerprint(validation) != expected_fingerprint
        ):
            raise _plan_changed()

    approved_at = _approved_at()
    source_hashes = _hash_sources(validation)
    if expected is not None and sources_fingerprint(validation) != expected[1]:
        raise _plan_changed()

    document = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "approved_at": approved_at,
        "plan_sha256": str(validation["plan_sha256"]),
        "sources": source_hashes,
    }
    publish_approval_atomic(layout, _serialize_approval(document))
    return {
        "artifact_path": str(layout.destination),
        "relative_artifact_path": relative_artifact_path(layout),
        "approved_at": approved_at,
        "plan_sha256": document["plan_sha256"],
        "sources": source_hashes,
    }


def approve_workdir(workdir: str | Path) -> dict[str, Any]:
    """Caminho da CLI: valida o plano atual e publica a aprovação v2, sem exigir preview."""

    return _approve(workdir, None)


def approve_workdir_if_unchanged(
    workdir: str | Path,
    expected_plan_sha256: str,
    expected_sources_fingerprint: str,
) -> dict[str, Any]:
    """Caminho do servidor da interface: aprova só a versão exibida ao operador.

    Recusa com `APPROVAL_PLAN_CHANGED`, sem criar nem alterar `approval.json`, se o hash canônico do
    plano ou a `sources_fingerprint` atuais diferirem dos informados (checados antes e depois do
    cálculo dos SHA-256 das fontes). Plano inválido propaga `PLAN_INVALID`; fonte ilegível,
    `SOURCE_INVALID`.
    """

    return _approve(workdir, (expected_plan_sha256, expected_sources_fingerprint))
