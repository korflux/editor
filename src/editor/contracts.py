"""Contratos estáveis de saída e erros públicos da CLI.

Este módulo não executa comandos nem acessa o ambiente. Ele concentra a forma
dos envelopes para que todos os subcomandos futuros compartilhem o mesmo
contrato consumível por agentes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


CLI_VERSION = "1.0.0"
SCHEMA_VERSION = 1

EXIT_CODES = {
    "INTERNAL": 1,
    "ARGUMENT": 2,
    "MEDIA": 3,
    "DEPENDENCY": 4,
    "STATE": 5,
    "OPERATION": 6,
}


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Rejeita objetos JSON ambíguos antes que a última chave esconda a primeira."""

    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"chave JSON duplicada: {key}")
        document[key] = value
    return document


@dataclass
class CliError(Exception):
    """Erro de domínio que o handler da borda converte em envelope seguro."""

    code: str
    message: str
    exit_code: int

    # Mantém a exceção compatível com o comportamento padrão sem expor detalhes
    # internos, pois o texto público será escolhido pelo código que a criou.
    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


def success_envelope(command: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Monta o envelope de sucesso versionado usado no stdout da CLI."""

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "data": dict(data),
    }


def error_envelope(error: CliError) -> dict[str, Any]:
    """Monta o envelope de erro sem stack trace ou diagnóstico interno."""

    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "error": {
            "code": error.code,
            "message": error.message,
            "exit_code": error.exit_code,
        },
    }


def serialize_envelope(payload: Mapping[str, Any]) -> str:
    """Serializa envelopes em uma linha canônica para consumo determinístico."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
