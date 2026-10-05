"""Descoberta local de executáveis, backend Whisper, hardware e modelos."""

from __future__ import annotations

import ctypes
import importlib.metadata
import importlib.util
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


ALLOWED_MODEL_NAMES = ("tiny", "base", "small")
REPARSE_POINT_ATTRIBUTE = 0x400
RENDER_ENCODERS = ("libx264", "aac")
# Aceleração opcional por GPU NVIDIA: encoder H.264 e decode CUDA; o baseline continua libx264.
GPU_VIDEO_ENCODER = "h264_nvenc"
GPU_HWACCEL = "cuda"
WINDOWS_MAX_COMMAND_CHARACTERS = 32_767
FALLBACK_POSIX_MAX_COMMAND_CHARACTERS = 131_072
_ENCODER_LINE = re.compile(r"^\s*[A-Z.]{6}\s+(\S+)")


def is_windows() -> bool:
    """Informa se o host é Windows para centralizar a variação por sistema."""

    # sys.platform cobre win32 mesmo em Python 64 bits; evita divergir de plan/validation.
    return sys.platform == "win32"


def ffmpeg_filter_option() -> str:
    """Devolve a flag de filtro por arquivo exigida pelo FFmpeg de cada sistema."""

    # Windows lê o grafo UTF-8 por arquivo com `-/filter_complex`; demais usam `-filter_complex`.
    # O transporte continua por arquivo nos dois casos, sem devolver o grafo à linha de comando.
    return "-/filter_complex" if is_windows() else "-filter_complex"


def max_command_characters() -> int:
    """Devolve o teto de linha de comando reportado em capabilities por sistema."""

    # Windows mantém o teto 32.767; demais usam o limite do sistema para não truncar falso.
    # ponytail: fallback POSIX fixo, trocar por sonda de getconf quando o Mac real exigir prova de ARG_MAX.
    if is_windows():
        return WINDOWS_MAX_COMMAND_CHARACTERS
    try:
        limit = os.sysconf("SC_ARG_MAX")
        if isinstance(limit, int) and limit > 0:
            return int(limit)
    except (AttributeError, OSError, ValueError):
        pass
    return FALLBACK_POSIX_MAX_COMMAND_CHARACTERS


def font_lookup_source() -> str:
    """Informa a origem consultada para fontes, sem expor caminho no envelope público."""

    # Windows consulta o registro; demais consultam fc-match e rejeitam fallback silencioso.
    return "registry" if is_windows() else "fc-match"


def font_registry_hives() -> list[str]:
    """Lista as colmeias consultadas no Windows, vazia nos demais sistemas."""

    # Mantém HKCU antes de HKLM por precedência do usuário; fora do Windows não há colmeia.
    return ["HKCU", "HKLM"] if is_windows() else []


def is_regular_path(path: Path, *, directory: bool = False) -> bool:
    """Valida um caminho local sem aceitar links ou reparse points operacionais."""

    try:
        metadata = os.lstat(path)
    except OSError:
        return False

    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & REPARSE_POINT_ATTRIBUTE:
        return False
    return stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)


def _package_root() -> Path:
    """Encontra a raiz do pacote editável para priorizar ferramentas locais do repositório."""

    return Path(__file__).resolve().parents[2]


def resolve_executable(name: str) -> Path | None:
    """Resolve somente executáveis regulares locais, priorizando o bundle `.tools` do projeto."""

    # Sem `.exe` fora do Windows; `.tools/ffmpeg` tem prioridade sobre o PATH nos dois sistemas.
    executable_name = f"{name}.exe" if is_windows() else name
    candidates: list[Path] = []
    if name in {"ffmpeg", "ffprobe"}:
        for root in (_package_root(), Path.cwd()):
            candidates.append(root / ".tools" / "ffmpeg" / executable_name)

    resolved = shutil.which(name)
    if resolved:
        candidates.append(Path(resolved))

    for candidate in candidates:
        if is_regular_path(candidate):
            return candidate.resolve()
    return None


def run_local_probe(executable: Path, arguments: Sequence[str]) -> dict[str, Any] | None:
    """Executa uma sondagem local sem shell e retorna apenas dados internos do processo."""

    try:
        result = subprocess.run(
            [str(executable), *arguments],
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return {
        "returncode": result.returncode,
        "output": f"{result.stdout or ''}\n{result.stderr or ''}",
    }


def parse_tool_version(output: str) -> str | None:
    """Extrai a versão de uma saída de ferramenta sem publicar o diagnóstico bruto."""

    match = re.search(r"\bversion\s+([^\s]+)", output, flags=re.IGNORECASE)
    return match.group(1).rstrip(",") if match else None


def probe_executable(name: str) -> dict[str, Any]:
    """Classifica uma ferramenta como disponível, ausente ou encontrada mas não utilizável."""

    executable = resolve_executable(name)
    if executable is None:
        return {"status": "missing", "path": None, "version": None}

    probe = run_local_probe(executable, ("-version",))
    if probe is None or probe["returncode"] != 0:
        return {"status": "unsupported", "path": str(executable), "version": None}
    return {
        "status": "available",
        "path": str(executable),
        "version": parse_tool_version(probe["output"]),
    }


def _encoder_names(output: str) -> set[str]:
    """Extrai nomes de encoders da sondagem local sem publicar a saída bruta do FFmpeg."""

    return {
        match.group(1)
        for line in output.splitlines()
        if (match := _ENCODER_LINE.match(line)) is not None
    }


def probe_ffmpeg_encoders(executable: Path | None = None) -> dict[str, Any]:
    """Confirma os encoders exigidos pelo render sem executar uma operação de mídia."""

    candidate = executable or resolve_executable("ffmpeg")
    if candidate is None:
        return {
            "status": "missing",
            "available": [],
            "missing": list(RENDER_ENCODERS),
            "reason": "FFmpeg não está disponível localmente",
        }

    probe = run_local_probe(candidate, ("-hide_banner", "-encoders"))
    if probe is None or probe["returncode"] != 0:
        return {
            "status": "unsupported",
            "available": [],
            "missing": list(RENDER_ENCODERS),
            "reason": "não foi possível sondar os encoders do FFmpeg",
        }

    names = _encoder_names(probe["output"])
    missing = [name for name in RENDER_ENCODERS if name not in names]
    return {
        "status": "available" if not missing else "unsupported",
        "available": [name for name in RENDER_ENCODERS if name in names],
        "missing": missing,
        "reason": (
            "encoders H.264 e AAC disponíveis"
            if not missing
            else "um ou mais encoders exigidos não estão disponíveis"
        ),
    }


def gpu_acceleration_available(executable: Path | None = None) -> bool:
    """Informa se o FFmpeg expõe NVENC e decode CUDA, sem provar que a GPU funciona."""

    # ponytail: listar não garante driver ou GPU operantes; o render confirma ao abrir o encoder
    # e cai para CPU. Sondar com um encode mínimo se o fallback por falha deixar de bastar.
    candidate = executable or resolve_executable("ffmpeg")
    if candidate is None:
        return False
    encoders = run_local_probe(candidate, ("-hide_banner", "-encoders"))
    hwaccels = run_local_probe(candidate, ("-hide_banner", "-hwaccels"))
    if encoders is None or hwaccels is None or encoders["returncode"] != 0 or hwaccels["returncode"] != 0:
        return False
    return GPU_VIDEO_ENCODER in _encoder_names(encoders["output"]) and GPU_HWACCEL in {
        line.strip() for line in hwaccels["output"].splitlines()
    }


def detect_ffmpeg() -> dict[str, Any]:
    """Confirma FFmpeg e FFprobe juntos, pois as operações de mídia dependem dos dois."""

    tools = {name: probe_executable(name) for name in ("ffmpeg", "ffprobe")}
    statuses = {entry["status"] for entry in tools.values()}
    if statuses == {"available"}:
        status = "available"
    elif "unsupported" in statuses:
        status = "unsupported"
    else:
        status = "missing"
    encoders = (
        probe_ffmpeg_encoders(Path(tools["ffmpeg"]["path"]))
        if tools["ffmpeg"]["status"] == "available"
        else {
            "status": tools["ffmpeg"]["status"],
            "available": [],
            "missing": list(RENDER_ENCODERS),
            "reason": "FFmpeg não está disponível para sondar encoders",
        }
    )
    return {
        "status": status,
        "version": tools["ffmpeg"]["version"],
        "tools": tools,
        "encoders": encoders,
    }


def package_info(distribution: str, module: str) -> dict[str, Any]:
    """Verifica distribuição instalada e módulo importável sem carregar o backend pesado."""

    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        version = None
    except (ValueError, OSError):
        version = None

    try:
        module_available = importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        module_available = False

    if version and module_available:
        status = "available"
    elif version is None and not module_available:
        status = "missing"
    else:
        status = "unsupported"
    return {
        "status": status,
        "version": version,
        "module": module,
    }


def detect_whisper_backend() -> dict[str, Any]:
    """Detecta o par faster-whisper/CTranslate2 exigido pelo contrato de transcrição local."""

    packages = {
        "faster-whisper": package_info("faster-whisper", "faster_whisper"),
        "ctranslate2": package_info("ctranslate2", "ctranslate2"),
    }
    statuses = {entry["status"] for entry in packages.values()}
    if statuses == {"available"}:
        status = "available"
    elif statuses == {"missing"}:
        status = "missing"
    else:
        status = "unsupported"
    return {"status": status, "packages": packages}


def parse_memory_mib(value: str) -> int | None:
    """Converte a memória reportada pelo nvidia-smi para bytes sem depender da localidade."""

    match = re.search(r"\d+(?:\.\d+)?", value)
    if not match:
        return None
    return int(float(match.group(0)) * 1024 * 1024)


def detect_gpu() -> dict[str, Any]:
    """Consulta GPUs NVIDIA locais via nvidia-smi, sem instalar driver ou fazer chamadas de rede."""

    executable = resolve_executable("nvidia-smi")
    if executable is None:
        return {"status": "missing", "path": None, "devices": []}

    probe = run_local_probe(
        executable,
        (
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ),
    )
    if probe is None or probe["returncode"] != 0:
        return {"status": "unsupported", "path": str(executable), "devices": []}

    devices: list[dict[str, Any]] = []
    for line in probe["output"].splitlines():
        columns = [column.strip() for column in line.split(",")]
        if len(columns) < 3 or not columns[0]:
            continue
        devices.append(
            {
                "status": "available",
                "name": columns[0],
                "driver_version": columns[1] or None,
                "memory_bytes": parse_memory_mib(columns[2]),
            }
        )

    if not devices:
        return {"status": "unsupported", "path": str(executable), "devices": []}
    return {"status": "available", "path": str(executable), "devices": devices}


def detect_cuda_runtime() -> dict[str, Any]:
    """Confirma que o CTranslate2 local consegue enumerar dispositivos CUDA utilizáveis."""

    try:
        import ctranslate2
    except (ImportError, ModuleNotFoundError):
        return {
            "status": "missing",
            "device_count": 0,
            "reason": "CTranslate2 não está disponível para sondar CUDA",
        }

    try:
        device_count = int(ctranslate2.get_cuda_device_count())
    except Exception:
        return {
            "status": "unsupported",
            "device_count": 0,
            "reason": "o runtime CUDA local não pôde ser inicializado pelo CTranslate2",
        }
    if device_count <= 0:
        return {
            "status": "unsupported",
            "device_count": device_count,
            "reason": "o CTranslate2 local não encontrou um dispositivo CUDA utilizável",
        }
    return {
        "status": "available",
        "device_count": device_count,
        "reason": "o CTranslate2 local enumerou dispositivos CUDA utilizáveis",
    }


def detect_metal() -> dict[str, Any]:
    """Sonda Metal best effort sem transformar ausência em erro de jornada."""

    # Metal só existe em macOS; fora dele a ausência é `missing`, nunca erro.
    # No Mac, a prova é a presença do framework do sistema; qualquer falha vira `missing`.
    # ponytail: compute Metal só com prova real no Mac com pesos pinados; nesta fatia o compute permanece CPU int8/CUDA float16.
    if platform.system() != "Darwin":
        return {
            "status": "missing",
            "reason": "Metal está disponível somente em macOS",
        }
    try:
        framework = Path("/System/Library/Frameworks/Metal.framework")
        if framework.is_dir():
            return {
                "status": "available",
                "reason": "Metal detectado localmente no macOS",
            }
        return {
            "status": "missing",
            "reason": "framework Metal não foi encontrado localmente",
        }
    except OSError:
        return {
            "status": "missing",
            "reason": "sondagem Metal indisponível sem falhar a jornada",
        }


def read_total_memory_bytes() -> int | None:
    """Lê memória física total por APIs do sistema, mantendo o detector sem dependência externa."""

    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        try:
            memory = MemoryStatus()
            memory.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
                return int(memory.total_physical)
        except (AttributeError, OSError):
            pass

    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return int(pages * page_size)
    except (AttributeError, OSError, ValueError):
        pass
    return None


def detect_system() -> dict[str, Any]:
    """Monta o inventário estável de sistema, Python, CPU e memória física local."""

    memory_bytes = read_total_memory_bytes()
    return {
        "os": {
            "name": platform.system() or "unknown",
            "release": platform.release() or None,
            "version": platform.version() or None,
        },
        "architecture": platform.machine() or platform.architecture()[0],
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "cpu": {
            "name": platform.processor() or platform.uname().processor or "unknown",
            "logical_count": os.cpu_count() or 1,
            "status": "available",
        },
        "memory": {
            "status": "available" if memory_bytes is not None else "unsupported",
            "total_bytes": memory_bytes,
            "total_gib": round(memory_bytes / (1024**3), 2) if memory_bytes is not None else None,
        },
    }


def _tree_has_only_regular_paths(root: Path) -> bool:
    """Impede que um modelo local inclua links ou reparse points em qualquer nível."""

    try:
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            for name in directories:
                if not is_regular_path(current_path / name, directory=True):
                    return False
            for name in files:
                if not is_regular_path(current_path / name):
                    return False
    except OSError:
        return False
    return True


def _has_reparse_ancestor(path: Path) -> bool:
    """Rejeita modelos cujo caminho atravesse um link ou reparse point existente."""

    current = Path(os.path.abspath(os.fspath(path)))
    while True:
        if os.path.lexists(current) and not is_regular_path(current, directory=True):
            return True
        if current.parent == current:
            return False
        current = current.parent


def _valid_model_directory(path: Path) -> bool:
    """Aplica os mesmos arquivos mínimos CTranslate2 exigidos pelo instalador local."""

    if not is_regular_path(path, directory=True) or not _tree_has_only_regular_paths(path):
        return False
    required = ("config.json", "model.bin", "tokenizer.json")
    if any(not is_regular_path(path / filename) for filename in required):
        return False
    try:
        return any(
            child.is_file() and child.name.startswith("vocabulary.") and is_regular_path(child)
            for child in path.iterdir()
        )
    except OSError:
        return False


def is_valid_model_directory(path: Path) -> bool:
    """Valida um diretório de modelo local sem permitir links nos pais ou no conteúdo."""

    candidate = Path(os.path.abspath(os.fspath(path)))
    return not _has_reparse_ancestor(candidate.parent) and _valid_model_directory(candidate)


def discover_models(root: Path) -> dict[str, Any]:
    """Classifica tiny, base e small em um diretório local sem baixar ou alterar pesos."""

    model_root = root.expanduser()
    if not model_root.is_absolute():
        model_root = Path.cwd() / model_root
    model_root = model_root.resolve()
    root_is_directory = is_regular_path(model_root, directory=True)
    items: list[dict[str, Any]] = []

    for name in ALLOWED_MODEL_NAMES:
        model_path = model_root / name
        if not root_is_directory or not model_path.exists():
            item = {
                "name": name,
                "status": "missing",
                "path": str(model_path),
                "reason": "modelo local não encontrado",
            }
        elif not is_regular_path(model_path, directory=True):
            item = {
                "name": name,
                "status": "unsupported",
                "path": str(model_path),
                "reason": "diretório de modelo inválido",
            }
        elif is_valid_model_directory(model_path):
            item = {
                "name": name,
                "status": "available",
                "path": str(model_path),
                "reason": "modelo CTranslate2 completo",
            }
        else:
            item = {
                "name": name,
                "status": "unsupported",
                "path": str(model_path),
                "reason": "arquivos mínimos do modelo não foram validados",
            }
        items.append(item)

    available = [item["name"] for item in items if item["status"] == "available"]
    if available:
        status = "available"
    elif not root_is_directory or all(item["status"] == "missing" for item in items):
        status = "missing"
    else:
        status = "unsupported"
    return {
        "status": status,
        "root": str(model_root),
        "available": available,
        "models": items,
    }
