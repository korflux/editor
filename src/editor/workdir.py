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
PROXY_DIRECTORY = "proxy"
"""Pasta do cache de proxies dentro do trabalho; fonte única para o layout e para `capabilities`."""
INIT_DIRECTORY_PATHS = (".tmp", "analysis", "analysis/audio", "analysis/transcription", "frames")
_KNOWN_ROOT_ARTIFACTS = ("plan.json", "approval.json", "preview.mp4", "full.mp4")
_KNOWN_FRAME_FILE = re.compile(r"^frame-\d{4}\.png$")


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


@dataclass(frozen=True)
class PreviewLayout:
    """Representa o destino único da preview e o diretório temporário do trabalho."""

    root: Path
    temporary: Path
    destination: Path


@dataclass(frozen=True)
class FullLayout:
    """Representa o destino único da full e o diretório temporário do trabalho."""

    root: Path
    temporary: Path
    destination: Path


@dataclass(frozen=True)
class ProxyLayout:
    """Representa os destinos do proxy de uma fonte (`proxy/<id>.mp4` e `.json`) e o temporário."""

    root: Path
    temporary: Path
    directory: Path
    video: Path
    metadata: Path


@dataclass(frozen=True)
class ApprovalLayout:
    """Representa o destino do registro de aprovação e o temporário do trabalho."""

    root: Path
    temporary: Path
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


def _normalize_workdir_path(raw_path: str | Path) -> Path:
    """Normaliza um caminho absoluto sem resolver links, preservando a fronteira de segurança."""

    try:
        candidate = Path(raw_path).expanduser()
    except (OSError, TypeError, ValueError) as error:
        raise _error(
            "WORKDIR_INVALID",
            "O diretório de trabalho deve ser um caminho absoluto válido.",
            EXIT_CODES["STATE"],
        ) from error
    if not candidate.is_absolute():
        raise _error(
            "WORKDIR_INVALID",
            "O diretório de trabalho deve ser um caminho absoluto.",
            EXIT_CODES["STATE"],
        )
    return Path(os.path.normpath(os.fspath(candidate)))


def _init_invalid(message: str = "O diretório de trabalho não pode ser preparado com segurança.") -> CliError:
    """Cria o erro público único para qualquer falha de validação do `init`."""

    return _error("WORKDIR_INVALID", message, EXIT_CODES["STATE"])


def _validate_init_root(raw_path: str | Path) -> tuple[Path, bool]:
    """Valida o trabalho existente ou confirma que somente seu último componente pode nascer."""

    path = _normalize_workdir_path(raw_path)
    if _lexists(path):
        if (
            not is_regular_path(path, directory=True)
            or _has_reparse_ancestor(path)
            or not os.access(path, os.W_OK)
        ):
            raise _init_invalid()
        return path, False

    parent = path.parent
    if (
        not is_regular_path(parent, directory=True)
        or _has_reparse_ancestor(parent)
        or not os.access(parent, os.W_OK)
    ):
        raise _init_invalid()
    return path, True


def _validate_regular_tree(root: Path) -> None:
    """Garante que uma árvore gerenciada não contém links, reparse points ou tipos especiais."""

    if not is_regular_path(root, directory=True) or _has_reparse_ancestor(root):
        raise _init_invalid()

    def raise_walk_error(error: OSError) -> None:
        """Interrompe o preflight quando uma pasta gerenciada não pode ser lida."""

        raise error

    try:
        for current, directories, files in os.walk(
            root,
            topdown=True,
            onerror=raise_walk_error,
            followlinks=False,
        ):
            current_path = Path(current)
            if not is_regular_path(current_path, directory=True) or _has_reparse_ancestor(current_path):
                raise _init_invalid()
            directories.sort()
            files.sort()
            for name in directories:
                child = current_path / name
                if not is_regular_path(child, directory=True) or _has_reparse_ancestor(child):
                    raise _init_invalid()
            for name in files:
                child = current_path / name
                if not is_regular_path(child) or _has_reparse_ancestor(child.parent):
                    raise _init_invalid()
    except CliError:
        raise
    except OSError as error:
        raise _init_invalid() from error


def _validate_init_parent(path: Path) -> None:
    """Valida o diretório efetivamente usado para uma criação sem atravessar componentes inseguros."""

    parent = path.parent
    while not _lexists(parent):
        if parent.parent == parent:
            raise _init_invalid()
        parent = parent.parent
    if (
        not is_regular_path(parent, directory=True)
        or _has_reparse_ancestor(parent)
        or not os.access(parent, os.W_OK)
    ):
        raise _init_invalid()


def _validate_init_directory(path: Path) -> None:
    """Valida uma pasta gerenciada existente e todos os arquivos que o `init` deve preservar."""

    if not is_regular_path(path, directory=True) or _has_reparse_ancestor(path):
        raise _init_invalid()
    if not os.access(path, os.W_OK):
        raise _init_invalid()
    _validate_regular_tree(path)


def _validate_init_preflight(root: Path) -> None:
    """Conclui todas as validações de estado antes de permitir qualquer subpasta nova."""

    for relative_path in INIT_DIRECTORY_PATHS:
        managed_path = root / relative_path
        if _lexists(managed_path):
            _validate_init_directory(managed_path)
        else:
            _validate_init_parent(managed_path)

    for artifact_name in _KNOWN_ROOT_ARTIFACTS:
        artifact = root / artifact_name
        if _lexists(artifact) and not is_regular_path(artifact):
            raise _init_invalid()


def _create_init_directory(root: Path, relative_path: str) -> bool:
    """Cria uma pasta gerenciada sem `parents=True` e informa se ela já existia."""

    path = root / relative_path
    if _lexists(path):
        _validate_init_directory(path)
        return False

    _validate_init_parent(path)
    try:
        path.mkdir()
    except FileExistsError:
        _validate_init_directory(path)
        return False
    except OSError as error:
        raise _init_invalid() from error

    if not is_regular_path(path, directory=True) or _has_reparse_ancestor(path):
        raise _init_invalid()
    return True


def _cleanup_created_init_directories(
    root: Path,
    created_paths: list[str],
    root_created: bool,
) -> None:
    """Remove somente diretórios vazios criados pela operação, sem tocar em estado vigente."""

    for relative_path in reversed(created_paths):
        path = root / relative_path
        if not is_regular_path(path, directory=True):
            continue
        try:
            path.rmdir()
        except OSError:
            pass
    if root_created and is_regular_path(root, directory=True):
        try:
            root.rmdir()
        except OSError:
            pass


def _collect_preserved_artifacts(root: Path) -> list[str]:
    """Lista somente artefatos conhecidos já existentes, mantendo a ordem operacional estável."""

    preserved: list[str] = []
    for artifact_name in _KNOWN_ROOT_ARTIFACTS:
        if is_regular_path(root / artifact_name):
            preserved.append(artifact_name)

    for relative_directory in ("analysis/audio", "analysis/transcription"):
        directory = root / relative_directory
        if not is_regular_path(directory, directory=True):
            continue
        for current, directories, files in os.walk(directory, topdown=True, followlinks=False):
            directories.sort()
            files.sort()
            current_path = Path(current)
            for name in files:
                candidate = current_path / name
                if candidate.suffix.lower() == ".json" and is_regular_path(candidate):
                    preserved.append(candidate.relative_to(root).as_posix())

    frames = root / "frames"
    if is_regular_path(frames, directory=True):
        for current, directories, files in os.walk(frames, topdown=True, followlinks=False):
            directories.sort()
            files.sort()
            current_path = Path(current)
            for name in files:
                if name == "manifest.json" or _KNOWN_FRAME_FILE.fullmatch(name):
                    candidate = current_path / name
                    if is_regular_path(candidate):
                        preserved.append(candidate.relative_to(root).as_posix())
    return preserved


def init_workdir(workdir: str | Path) -> dict[str, object]:
    """Prepara a estrutura técnica do trabalho sem criar ou substituir estado editorial."""

    root, root_created = _validate_init_root(workdir)
    if not root_created:
        _validate_init_preflight(root)

    created_paths: list[str] = []
    existing_paths: list[str] = []
    try:
        if root_created:
            try:
                root.mkdir()
            except OSError as error:
                raise _init_invalid() from error
            if not is_regular_path(root, directory=True) or _has_reparse_ancestor(root):
                raise _init_invalid()
            _validate_init_preflight(root)

        for relative_path in INIT_DIRECTORY_PATHS:
            if _create_init_directory(root, relative_path):
                created_paths.append(relative_path)
            else:
                existing_paths.append(relative_path)
    except Exception:
        _cleanup_created_init_directories(root, created_paths, root_created)
        raise

    return {
        "workdir": str(root),
        "state": "ready",
        "created": created_paths,
        "existing": existing_paths,
        "preserved_artifacts": _collect_preserved_artifacts(root),
    }


def resolve_workdir(raw_path: str | Path | None) -> str | Path:
    """Devolve o trabalho informado ou o diretório atual, sem validar nem criar estado."""

    if raw_path is None:
        return Path(os.getcwd())
    return raw_path


def derive_source_id(input_path: str | Path) -> str:
    """Deriva um source-id válido do basename do input, sem tocar no disco."""

    try:
        stem = Path(input_path).stem.strip().lower()
    except (OSError, TypeError, ValueError) as error:
        raise _error(
            "ARGUMENT_INVALID",
            "Não foi possível derivar um source-id válido a partir do input.",
            EXIT_CODES["ARGUMENT"],
        ) from error
    # Normaliza para o alfabeto do contrato, sem nova dependência.
    # ponytail: derivação por basename pode colidir, exigir --source-id explícito quando houver múltiplas fontes com mesmo nome.
    normalized = "".join(
        char if ("a" <= char <= "z" or "0" <= char <= "9" or char in ("-", "_")) else "-"
        for char in stem
    ).lstrip("-_")[:64]
    if not normalized:
        raise _error(
            "ARGUMENT_INVALID",
            "Não foi possível derivar um source-id válido a partir do input.",
            EXIT_CODES["ARGUMENT"],
        )
    return validate_source_id(normalized)


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

    path = _normalize_workdir_path(raw_path)
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


def ensure_sources_do_not_alias_output(
    source_paths: list[Path],
    destination: Path,
    artifact_name: str,
) -> None:
    """Rejeita aliases físicos entre fontes e o destino antes de substituir uma entrada."""

    destination_path = Path(os.path.abspath(os.fspath(destination)))
    for raw_source_path in source_paths:
        source_path = resolve_source_path(raw_source_path)
        if os.path.normcase(os.fspath(source_path)) == os.path.normcase(
            os.fspath(destination_path)
        ):
            raise _error(
                "OUTPUT_INVALID",
                f"Uma fonte não pode coincidir com o destino da {artifact_name}.",
                EXIT_CODES["STATE"],
            )
        if not _lexists(destination_path):
            continue
        if not is_regular_path(destination_path):
            raise _error(
                "OUTPUT_INVALID",
                f"O destino da {artifact_name} não é um arquivo regular válido.",
                EXIT_CODES["STATE"],
            )
        try:
            same_file = os.path.samefile(source_path, destination_path)
        except FileNotFoundError:
            same_file = False
        except OSError as error:
            raise _error(
                "OUTPUT_INVALID",
                f"Não foi possível verificar o destino da {artifact_name}.",
                EXIT_CODES["STATE"],
            ) from error
        if same_file:
            raise _error(
                "OUTPUT_INVALID",
                f"Uma fonte não pode coincidir com o destino da {artifact_name}.",
                EXIT_CODES["STATE"],
            )


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


def _prepare_render_layout(
    workdir: str | Path,
    filename: str,
    artifact_name: str,
) -> tuple[Path, Path, Path]:
    """Valida o destino de um render MP4 antes de criar temporários ou chamar o FFmpeg."""

    root = validate_workdir(workdir)
    temporary = root / ".tmp"
    destination = root / filename
    _ensure_directory(temporary)
    if _lexists(destination) and (
        not is_regular_path(destination) or _has_reparse_ancestor(destination.parent)
    ):
        raise _error(
            "OUTPUT_INVALID",
            f"O destino da {artifact_name} deve ser um arquivo regular dentro do trabalho.",
            EXIT_CODES["STATE"],
        )
    return root, temporary, destination


def prepare_preview_layout(workdir: str | Path) -> PreviewLayout:
    """Prepara `preview.mp4` e rejeita destinos não regulares antes do FFmpeg."""

    root, temporary, destination = _prepare_render_layout(
        workdir,
        "preview.mp4",
        "preview",
    )
    return PreviewLayout(root=root, temporary=temporary, destination=destination)


def prepare_full_layout(workdir: str | Path) -> FullLayout:
    """Prepara `full.mp4` e rejeita destinos não regulares antes do FFmpeg."""

    root, temporary, destination = _prepare_render_layout(
        workdir,
        "full.mp4",
        "full",
    )
    return FullLayout(root=root, temporary=temporary, destination=destination)


def prepare_approval_layout(workdir: str | Path) -> ApprovalLayout:
    """Prepara `approval.json` e rejeita um destino linkado antes de calcular estado."""

    root = validate_workdir(workdir)
    temporary = root / ".tmp"
    destination = root / "approval.json"
    _ensure_directory(temporary)
    if _lexists(destination) and (
        not is_regular_path(destination) or _has_reparse_ancestor(destination.parent)
    ):
        raise _error(
            "OUTPUT_INVALID",
            "O destino da aprovação deve ser um arquivo regular dentro do trabalho.",
            EXIT_CODES["STATE"],
        )
    return ApprovalLayout(root=root, temporary=temporary, destination=destination)


def proxy_paths(root: Path, source_id: str) -> tuple[Path, Path, Path]:
    """Deriva pasta, vídeo e metadado do proxy só do ID validado, sem tocar no disco.

    Fonte única do layout `proxy/<id>.mp4` + `proxy/<id>.json`, usada pelo build e pela leitura
    de validade consumida na preview; `root` já deve ter passado por `validate_workdir`.
    """

    safe_source_id = validate_source_id(source_id)
    directory = root / PROXY_DIRECTORY
    return directory, directory / f"{safe_source_id}.mp4", directory / f"{safe_source_id}.json"


def _ensure_proxy_destinations(layout: ProxyLayout, code: str) -> None:
    """Rejeita destino de proxy que exista como link, reparse point ou tipo especial."""

    for destination in (layout.video, layout.metadata):
        if _lexists(destination) and (
            not is_regular_path(destination) or _has_reparse_ancestor(destination.parent)
        ):
            raise _error(
                code,
                "O destino do proxy deve ser um arquivo regular dentro do trabalho.",
                EXIT_CODES["STATE"] if code == "OUTPUT_INVALID" else EXIT_CODES["OPERATION"],
            )


def prepare_proxy_layout(workdir: str | Path, source_id: str) -> ProxyLayout:
    """Prepara `.tmp` e `proxy/` e valida os destinos do proxy antes de chamar o FFmpeg."""

    root = validate_workdir(workdir)
    directory, video, metadata = proxy_paths(root, source_id)
    temporary = root / ".tmp"
    _ensure_directory(temporary)
    _ensure_directory(directory)
    layout = ProxyLayout(
        root=root,
        temporary=temporary,
        directory=directory,
        video=video,
        metadata=metadata,
    )
    _ensure_proxy_destinations(layout, "OUTPUT_INVALID")
    return layout


def publish_proxy_atomic(layout: ProxyLayout, staging: Path, metadata_payload: bytes) -> None:
    """Publica vídeo e metadado do proxy já verificados, cada um por substituição atômica.

    Ordem deliberada: o vídeo novo entra antes do metadado. No intervalo entre as duas trocas o
    metadado antigo não casa com o vídeo novo (tamanho do proxy ou estado da fonte), então o
    leitor vê o proxy como obsoleto e nunca como válido com conteúdo trocado.
    """

    temporary_metadata: Path | None = None
    try:
        if not is_regular_path(staging) or _has_reparse_ancestor(staging.parent):
            raise _error(
                "OUTPUT_PUBLISH_FAILED",
                "O arquivo temporário do proxy não pôde ser validado.",
                EXIT_CODES["OPERATION"],
            )
        _ensure_directory(layout.temporary)
        _ensure_directory(layout.directory)
        _ensure_proxy_destinations(layout, "OUTPUT_PUBLISH_FAILED")

        descriptor, raw_temporary_path = tempfile.mkstemp(
            prefix=f".{layout.metadata.stem}-proxy-",
            suffix=".json.tmp",
            dir=layout.temporary,
        )
        temporary_metadata = Path(raw_temporary_path)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(metadata_payload)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(staging, layout.video)
        os.replace(temporary_metadata, layout.metadata)
        temporary_metadata = None
    except CliError:
        raise
    except OSError as error:
        raise _error(
            "OUTPUT_PUBLISH_FAILED",
            "Não foi possível publicar o proxy completo.",
            EXIT_CODES["OPERATION"],
        ) from error
    finally:
        for leftover in (staging, temporary_metadata):
            if leftover is not None and _lexists(leftover):
                try:
                    leftover.unlink()
                except OSError:
                    pass


def relative_artifact_path(
    layout: WorkdirLayout | FramesLayout | PreviewLayout | FullLayout | ApprovalLayout,
) -> str:
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


def _publish_render_atomic(
    layout: PreviewLayout | FullLayout,
    staging: Path,
    artifact_name: str,
) -> None:
    """Publica um render MP4 completo por substituição atômica e preserva o vigente em falha."""

    try:
        if not is_regular_path(staging) or _has_reparse_ancestor(staging.parent):
            raise _error(
                "OUTPUT_PUBLISH_FAILED",
                f"O arquivo temporário da {artifact_name} não pôde ser validado.",
                EXIT_CODES["OPERATION"],
            )
        _ensure_directory(layout.temporary)
        if _lexists(layout.destination) and (
            not is_regular_path(layout.destination)
            or _has_reparse_ancestor(layout.destination.parent)
        ):
            raise _error(
                "OUTPUT_PUBLISH_FAILED",
                f"O destino da {artifact_name} mudou para um estado não seguro.",
                EXIT_CODES["OPERATION"],
            )
        os.replace(staging, layout.destination)
    except CliError:
        raise
    except OSError as error:
        raise _error(
            "OUTPUT_PUBLISH_FAILED",
            f"Não foi possível publicar a {artifact_name} completa.",
            EXIT_CODES["OPERATION"],
        ) from error
    finally:
        if _lexists(staging):
            try:
                staging.unlink()
            except OSError:
                pass


def publish_preview_atomic(layout: PreviewLayout, staging: Path) -> None:
    """Substitui a preview vigente somente depois que o arquivo temporário foi validado."""

    _publish_render_atomic(layout, staging, "preview")


def publish_full_atomic(layout: FullLayout, staging: Path) -> None:
    """Substitui a full vigente somente depois que o arquivo temporário foi validado."""

    _publish_render_atomic(layout, staging, "full")


def publish_approval_atomic(layout: ApprovalLayout, payload: bytes) -> None:
    """Publica o registro completo sem substituir uma aprovação por estado parcial."""

    temporary_path: Path | None = None
    try:
        _ensure_directory(layout.temporary)
        if _lexists(layout.destination) and (
            not is_regular_path(layout.destination)
            or _has_reparse_ancestor(layout.destination.parent)
        ):
            raise _error(
                "OUTPUT_PUBLISH_FAILED",
                "O destino da aprovação mudou para um estado não seguro.",
                EXIT_CODES["OPERATION"],
            )

        descriptor, raw_temporary_path = tempfile.mkstemp(
            prefix=".approval-",
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
                "O arquivo temporário da aprovação não pôde ser validado.",
                EXIT_CODES["OPERATION"],
            )
        os.replace(temporary_path, layout.destination)
        temporary_path = None
    except CliError:
        raise
    except OSError as error:
        raise _error(
            "OUTPUT_PUBLISH_FAILED",
            "Não foi possível publicar a aprovação completa.",
            EXIT_CODES["OPERATION"],
        ) from error
    finally:
        if temporary_path is not None and _lexists(temporary_path):
            try:
                temporary_path.unlink()
            except OSError:
                pass
