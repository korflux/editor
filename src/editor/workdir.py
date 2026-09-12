"""Validação do diretório de trabalho e publicação atômica de artefatos."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .contracts import CliError, EXIT_CODES
from .dependencies import is_regular_path


SOURCE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class WorkdirLayout:
    """Representa os diretórios e o destino reservados para um artefato do trabalho."""

    root: Path
    temporary: Path
    analysis: Path
    destination: Path


@dataclass(frozen=True)
class FramesLayout:
    """Representa a pasta de frames e o temporário usado na publicação conjunta."""

    root: Path
    temporary: Path
    frames: Path
    destination: Path


def _error(code: str, message: str, exit_code: int) -> CliError:
    """Cria um erro público seguro para falhas de caminho e de estado local."""

    return CliError(code=code, message=message, exit_code=exit_code)


def _lexists(path: Path) -> bool:
    """Detecta inclusive links quebrados, que `Path.exists()` não considera existentes."""

    return os.path.lexists(os.fspath(path))


def _has_reparse_ancestor(path: Path) -> bool:
    """Verifica componentes existentes do caminho sem seguir junctions ou symlinks."""

    current = Path(os.path.abspath(os.fspath(path)))
    while True:
        if _lexists(current) and not is_regular_path(current, directory=True):
            return True
        if current.parent == current:
            return False
        current = current.parent


def validate_source_id(source_id: str) -> str:
    """Aceita somente IDs que podem virar nome de arquivo sem traversal."""

    if not SOURCE_ID_PATTERN.fullmatch(source_id):
        raise _error(
            "ARGUMENT_INVALID",
            "O source-id deve usar até 64 caracteres minúsculos, números, hífen ou sublinhado.",
            EXIT_CODES["ARGUMENT"],
        )
    return source_id


def validate_workdir(raw_path: str | Path) -> Path:
    """Valida um diretório absoluto, existente, gravável e sem componentes linkados."""

    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise _error(
            "WORKDIR_INVALID",
            "O diretório de trabalho deve ser um caminho absoluto.",
            EXIT_CODES["STATE"],
        )

    path = Path(os.path.normpath(os.fspath(candidate)))
    if (
        not is_regular_path(path, directory=True)
        or _has_reparse_ancestor(path)
        or not os.access(path, os.W_OK)
    ):
        raise _error(
            "WORKDIR_INVALID",
            "O diretório de trabalho deve ser uma pasta regular, existente e gravável.",
            EXIT_CODES["STATE"],
        )
    return path


def resolve_source_path(raw_path: str | Path) -> Path:
    """Resolve uma fonte local sem aceitar URL, diretório, symlink ou reparse point."""

    candidate = Path(raw_path).expanduser()
    path = Path(os.path.abspath(os.fspath(candidate)))
    if not is_regular_path(path) or _has_reparse_ancestor(path.parent):
        raise _error(
            "SOURCE_INVALID",
            "A fonte deve ser um arquivo local regular e acessível.",
            EXIT_CODES["MEDIA"],
        )
    return path


def _ensure_directory(path: Path) -> None:
    """Cria uma pasta interna somente depois de validar todos os pais existentes."""

    if _lexists(path):
        if not is_regular_path(path, directory=True) or _has_reparse_ancestor(path.parent):
            raise _error(
                "OUTPUT_INVALID",
                "O destino do trabalho contém um diretório não regular.",
                EXIT_CODES["STATE"],
            )
        return

    if _has_reparse_ancestor(path.parent):
        raise _error(
            "OUTPUT_INVALID",
            "O destino do trabalho contém um componente linkado.",
            EXIT_CODES["STATE"],
        )
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise _error(
            "OUTPUT_INVALID",
            "Não foi possível preparar o diretório de trabalho.",
            EXIT_CODES["STATE"],
        ) from error
    if not is_regular_path(path, directory=True) or _has_reparse_ancestor(path.parent):
        raise _error(
            "OUTPUT_INVALID",
            "O diretório de trabalho mudou para um estado não seguro.",
            EXIT_CODES["STATE"],
        )


def prepare_layout(
    workdir: str | Path,
    source_id: str,
    analysis_kind: str = "audio",
) -> WorkdirLayout:
    """Prepara a pasta de uma análise permitida e valida seu destino antes do processamento."""

    root = validate_workdir(workdir)
    safe_source_id = validate_source_id(source_id)
    if analysis_kind not in {"audio", "transcription"}:
        raise _error(
            "ARGUMENT_INVALID",
            "O tipo de análise não é suportado.",
            EXIT_CODES["ARGUMENT"],
        )
    temporary = root / ".tmp"
    analysis = root / "analysis" / analysis_kind
    destination = analysis / f"{safe_source_id}.json"

    _ensure_directory(temporary)
    _ensure_directory(analysis)
    if _lexists(destination) and (
        not is_regular_path(destination) or _has_reparse_ancestor(destination.parent)
    ):
        raise _error(
            "OUTPUT_INVALID",
            "O destino da análise deve ser um arquivo regular dentro do trabalho.",
            EXIT_CODES["STATE"],
        )
    return WorkdirLayout(
        root=root,
        temporary=temporary,
        analysis=analysis,
        destination=destination,
    )


def prepare_frames_layout(workdir: str | Path, source_id: str) -> FramesLayout:
    """Prepara `frames/<source-id>` sem permitir publicação fora do trabalho."""

    root = validate_workdir(workdir)
    safe_source_id = validate_source_id(source_id)
    temporary = root / ".tmp"
    frames_root = root / "frames"
    frames = frames_root / safe_source_id
    destination = frames / "manifest.json"

    _ensure_directory(temporary)
    _ensure_directory(frames_root)
    if _lexists(frames):
        if not is_regular_path(frames, directory=True) or _has_reparse_ancestor(frames.parent):
            raise _error(
                "OUTPUT_INVALID",
                "O destino dos frames deve ser uma pasta regular dentro do trabalho.",
                EXIT_CODES["STATE"],
            )
        if _lexists(destination) and (
            not is_regular_path(destination) or _has_reparse_ancestor(destination.parent)
        ):
            raise _error(
                "OUTPUT_INVALID",
                "O manifesto de frames deve ser um arquivo regular dentro do trabalho.",
                EXIT_CODES["STATE"],
            )
    return FramesLayout(
        root=root,
        temporary=temporary,
        frames=frames,
        destination=destination,
    )


def relative_artifact_path(layout: WorkdirLayout | FramesLayout) -> str:
    """Retorna o caminho do artefato relativo ao trabalho para respostas estáveis."""

    return layout.destination.relative_to(layout.root).as_posix()


def _remove_regular_tree(path: Path) -> None:
    """Remove somente uma árvore regular já validada, sem seguir links ou reparse points."""

    if not _lexists(path):
        return
    if not is_regular_path(path, directory=True):
        raise OSError("árvore de frames não regular")
    for current, directories, files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            child = current_path / name
            if not is_regular_path(child, directory=name in directories):
                raise OSError("árvore de frames contém link ou reparse point")
    shutil.rmtree(path)


def publish_frames_atomic(layout: FramesLayout, staging: Path) -> None:
    """Troca a pasta completa de frames só depois que PNGs e manifesto estão prontos."""

    if not is_regular_path(staging, directory=True) or _has_reparse_ancestor(staging.parent):
        raise _error(
            "OUTPUT_PUBLISH_FAILED",
            "A pasta temporária de frames não pôde ser validada.",
            EXIT_CODES["OPERATION"],
        )

    backup: Path | None = None
    try:
        _ensure_directory(layout.frames.parent)
        if _lexists(layout.frames):
            if not is_regular_path(layout.frames, directory=True) or _has_reparse_ancestor(
                layout.frames.parent
            ):
                raise _error(
                    "OUTPUT_INVALID",
                    "O destino dos frames deve ser uma pasta regular dentro do trabalho.",
                    EXIT_CODES["STATE"],
                )
            backup = layout.temporary / f".{layout.frames.name}-backup-{uuid4().hex}"
            os.replace(layout.frames, backup)

        os.replace(staging, layout.frames)
    except CliError:
        if backup is not None and _lexists(backup) and not _lexists(layout.frames):
            try:
                os.replace(backup, layout.frames)
            except OSError:
                pass
        raise
    except OSError as error:
        if backup is not None and _lexists(backup):
            try:
                if _lexists(layout.frames):
                    _remove_regular_tree(layout.frames)
                os.replace(backup, layout.frames)
            except OSError:
                pass
        raise _error(
            "OUTPUT_PUBLISH_FAILED",
            "Não foi possível publicar os frames completos.",
            EXIT_CODES["OPERATION"],
        ) from error
    finally:
        if _lexists(staging):
            try:
                _remove_regular_tree(staging)
            except OSError:
                pass

    if backup is not None and _lexists(backup):
        try:
            _remove_regular_tree(backup)
        except OSError:
            # A saída nova já está íntegra; o temporário antigo permanece isolado.
            pass


def publish_atomic(layout: WorkdirLayout, payload: bytes) -> None:
    """Publica bytes completos por arquivo temporário e substituição atômica segura."""

    temporary_path: Path | None = None
    try:
        _ensure_directory(layout.temporary)
        _ensure_directory(layout.analysis)
        if _lexists(layout.destination) and (
            not is_regular_path(layout.destination)
            or _has_reparse_ancestor(layout.destination.parent)
        ):
            raise _error(
                "OUTPUT_INVALID",
                "O destino da análise mudou para um estado não seguro.",
                EXIT_CODES["STATE"],
            )

        descriptor, raw_temporary_path = tempfile.mkstemp(
            prefix=f".{layout.destination.stem}-",
            suffix=".tmp",
            dir=layout.temporary,
        )
        temporary_path = Path(raw_temporary_path)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

        if not is_regular_path(temporary_path):
            raise _error(
                "OUTPUT_PUBLISH_FAILED",
                "O arquivo temporário da análise não pôde ser validado.",
                EXIT_CODES["OPERATION"],
            )
        os.replace(temporary_path, layout.destination)
        temporary_path = None
    except CliError:
        raise
    except OSError as error:
        raise _error(
            "OUTPUT_PUBLISH_FAILED",
            "Não foi possível publicar a análise de áudio.",
            EXIT_CODES["OPERATION"],
        ) from error
    finally:
        if temporary_path is not None and _lexists(temporary_path):
            try:
                temporary_path.unlink()
            except OSError:
                pass
