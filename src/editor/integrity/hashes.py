"""Cálculo de hashes usados para vincular estado e artefatos locais."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    """Calcula o SHA-256 de um arquivo em blocos sem carregar a mídia inteira."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
