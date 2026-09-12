"""Serialização canônica do plano para hash e comparação semântica."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(document: Any) -> bytes:
    """Serializa JSON válido em UTF-8, com chaves ordenadas e sem espaços."""

    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(document: Any) -> str:
    """Retorna a forma textual canônica usada pelos consumidores do plano."""

    return canonical_bytes(document).decode("utf-8")


def canonical_sha256(document: Any) -> str:
    """Calcula SHA-256 dos bytes canônicos sem depender da formatação de entrada."""

    return hashlib.sha256(canonical_bytes(document)).hexdigest()
