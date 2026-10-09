"""Instância da interface por trabalho: `ui start`, `ui stop` e o registro em `.tmp` (lado da CLI).

Responsabilidade: subir o servidor (`python -m editor.ui.server`) em segundo plano, reaproveitar uma
instância viva, encerrá-la com os FFmpeg filhos e manter o registro dela. Este módulo não importa o
servidor, então a CLI não paga o custo de importar o render para abrir a interface.

Registro (`.tmp/ui-instance.json`): pid, porta, token e identidade da instância, escrito pelo próprio
servidor depois de escutar e removido no encerramento. É arquivo regular do trabalho (sem links nem
pontos de reanálise), publicado por substituição atômica. O token nunca passa por argumento de
processo: o servidor o gera e só o registro o leva à CLI.

Instância "viva" não é pid vivo: é a porta registrada respondendo com a identidade registrada
(`INSTANCE_HEADER`) e o pid registrado (`PID_HEADER`). Isso trata processo morto como ausente e impede que
`stop` mate um pid reaproveitado pelo sistema, ou posto num registro forjado, para outro programa: só se
encerra o processo que a própria porta provou ser o da instância.

ponytail: duas chamadas simultâneas de `ui start` no mesmo trabalho podem subir dois servidores, e só o
último registrado é encerrado pelo `stop` (o outro encerra sozinho por inatividade), usar um arquivo de
trava de criação exclusiva se isso ocorrer de verdade.
ponytail: um servidor vivo mas travado (não responde) é tratado como ausente e deixado como está, sem
kill por pid sem identidade provada; ele não encerra sozinho se o laço de inatividade também travou.
"""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import webbrowser
from typing import Any

from ..contracts import EXIT_CODES, CliError, reject_duplicate_json_keys
from ..dependencies import is_regular_path, is_windows
from ..workdir import _ensure_directory, validate_workdir
from .limits import (
    HOST,
    INSTANCE_HEADER,
    LIVE_FILTER_PREFIX,
    PID_HEADER,
    PROBE_TIMEOUT_S,
    REGISTRY_NAME,
    START_TIMEOUT_S,
    STOP_TIMEOUT_S,
    TOKEN_PATTERN,
)


MAX_REGISTRY_BYTES = 4096
"""O registro tem poucas dezenas de bytes; um arquivo maior que isto não é nosso."""

_INSTANCE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_REGISTRY_FIELDS = frozenset({"pid", "port", "token", "instance_id"})
_POLL_S = 0.05
_SERVER_MODULE = "editor.ui.server"


@dataclass(frozen=True)
class Instance:
    """Instância registrada: processo, porta, token da URL e identidade provada pelo `INSTANCE_HEADER`."""

    pid: int
    port: int
    token: str
    instance_id: str


def _error(code: str, message: str, exit_code: int) -> CliError:
    """Cria um erro público seguro para as falhas de abrir e encerrar a interface."""

    return CliError(code=code, message=message, exit_code=exit_code)


def build_url(instance: Instance) -> str:
    """URL de entrada com o token, a única forma de abrir a sessão (o servidor tira o token do endereço)."""

    return f"http://{HOST}:{instance.port}/?token={instance.token}"


def registry_path(root: Path) -> Path:
    """Caminho do registro da instância dentro de `.tmp` do trabalho já validado."""

    return root / ".tmp" / REGISTRY_NAME


def _prepare_root(workdir: str | Path, *, create_tmp: bool) -> Path:
    """Valida o trabalho e, quando preciso, a pasta `.tmp`, sempre com o erro público `WORKDIR_INVALID`."""

    root = validate_workdir(workdir)
    if create_tmp:
        try:
            _ensure_directory(root / ".tmp")
        except CliError as error:
            raise _error(
                "WORKDIR_INVALID",
                "O diretório de trabalho não pode abrigar a interface com segurança.",
                EXIT_CODES["STATE"],
            ) from error
    return root


def _safe_registry(root: Path) -> Path | None:
    """Caminho do registro só se `.tmp` for pasta regular e o registro, arquivo regular; senão `None`.

    Pasta `.tmp` trocada por link ou junção nunca é lida nem alterada: o registro decide qual processo o
    `stop` encerra, então não pode vir de fora do trabalho.
    """

    path = registry_path(root)
    if is_regular_path(path.parent, directory=True) and is_regular_path(path):
        return path
    return None


def read_instance(root: Path) -> Instance | None:
    """Lê o registro; arquivo ausente, não regular, grande, malformado ou fora do formato vale como ausente."""

    path = _safe_registry(root)
    if path is None:
        return None
    try:
        if path.stat().st_size > MAX_REGISTRY_BYTES:
            return None
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_json_keys
        )
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict) or set(payload) != _REGISTRY_FIELDS:
        return None
    pid, port, token, instance_id = (payload[key] for key in ("pid", "port", "token", "instance_id"))
    if (
        isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
        or isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536
        or not isinstance(token, str) or TOKEN_PATTERN.fullmatch(token) is None
        or not isinstance(instance_id, str) or _INSTANCE_ID_PATTERN.fullmatch(instance_id) is None
    ):
        return None
    return Instance(pid=pid, port=port, token=token, instance_id=instance_id)


def write_instance(root: Path, instance: Instance) -> None:
    """Publica o registro por arquivo temporário em `.tmp` e substituição atômica (chamado pelo servidor)."""

    path = registry_path(root)
    if not is_regular_path(path.parent, directory=True) or (
        os.path.lexists(path) and not is_regular_path(path)
    ):
        raise _error(
            "WORKDIR_INVALID",
            "O registro da interface não é um arquivo regular do trabalho.",
            EXIT_CODES["STATE"],
        )
    payload = json.dumps(
        {"pid": instance.pid, "port": instance.port, "token": instance.token, "instance_id": instance.instance_id},
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor, raw_temporary = tempfile.mkstemp(prefix=".ui-instance-", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def remove_instance(root: Path, instance_id: str | None = None) -> None:
    """Remove o registro; com `instance_id`, só se ele ainda for o desta instância (outra pode ter assumido)."""

    path = _safe_registry(root)
    if path is None:
        return
    if instance_id is not None:
        current = read_instance(root)
        if current is None or current.instance_id != instance_id:
            return
    try:
        path.unlink()
    except OSError:
        pass


def probe(instance: Instance) -> bool:
    """Prova que a porta registrada responde e é a instância registrada, sem autenticar nem criar sessão.

    O pedido sem cookie recebe 401 com a identidade no `INSTANCE_HEADER` e o pid no `PID_HEADER`; processo
    morto, porta de outro programa, identidade diferente ou pid diferente do registrado resultam em `False`.
    """

    connection = http.client.HTTPConnection(HOST, instance.port, timeout=PROBE_TIMEOUT_S)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()
        identity = response.getheader(INSTANCE_HEADER, "")
        return (
            response.status == 401
            and hmac.compare_digest(identity.encode("utf-8", "replace"), instance.instance_id.encode("ascii"))
            and response.getheader(PID_HEADER) == str(instance.pid)
        )
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def terminate_tree(pid: int) -> None:
    """Encerra o processo do servidor e os FFmpeg filhos dele, à força.

    Windows: `taskkill /T /F` pelo caminho do sistema (sem depender do PATH). Fora dele, o servidor foi
    criado em sessão própria, então o grupo inteiro recebe `SIGKILL`.
    """

    if is_windows():
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            subprocess.run(
                [str(taskkill), "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                shell=False,
                timeout=STOP_TIMEOUT_S,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _remove_leftover_filter_scripts(root: Path) -> None:
    """Apaga de `.tmp` os scripts de filtro do render ao vivo que um servidor encerrado à força não removeu.

    Só arquivos regulares com o prefixo do render ao vivo: as prévias, a full e o resto de `.tmp` não são tocados.
    """

    try:
        entries = list((root / ".tmp").iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name.startswith(LIVE_FILTER_PREFIX) and entry.suffix == ".txt" and is_regular_path(entry):
            try:
                entry.unlink()
            except OSError:
                pass


def _project_directory() -> Path:
    """Diretório estável para o servidor rodar: a raiz do pacote, nunca o trabalho nem a pasta do chamador.

    Um servidor com o trabalho como diretório atual impediria apagar a pasta (o Windows trava o
    diretório atual de um processo vivo). `dependencies` também procura `.tools` nesta raiz.
    """

    return Path(__file__).resolve().parents[3]


def _spawn_server(root: Path) -> subprocess.Popen[bytes]:
    """Inicia o servidor em segundo plano, sem herdar stdin, stdout nem stderr da CLI.

    O envelope de `ui start` precisa ser a única saída do stdout, e um filho que herdasse o pipe de um
    chamador que captura a saída o manteria aberto até o servidor morrer. No Windows o processo ganha
    grupo e console ocultos próprios e tenta sair do Job do chamador (um agente que encerra o Job ao
    fim do comando mataria o servidor); se o Job não permitir, repete sem sair dele.
    """

    command = [sys.executable, "-m", _SERVER_MODULE, "--workdir", str(root)]
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": _project_directory(),
        "shell": False,
        "close_fds": True,
    }
    if not is_windows():
        return subprocess.Popen(command, start_new_session=True, **options)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options)
    except OSError:
        return subprocess.Popen(command, creationflags=flags, **options)


def _wait_until_ready(root: Path, process: subprocess.Popen[bytes]) -> Instance:
    """Espera o servidor recém-criado registrar-se e responder; falha com `UI_START_FAILED` se morrer ou demorar.

    O registro velho foi removido antes de criar o processo, então o primeiro registro que responde com a
    própria identidade é o do servidor criado agora. Não se compara o pid com o do `Popen`: no Windows o
    `python.exe` de um venv é um lançador que cria o interpretador real como filho, e o servidor registra o
    pid dele (o que o `stop` precisa encerrar).
    """

    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        instance = read_instance(root)
        if instance is not None and probe(instance):
            return instance
        time.sleep(_POLL_S)
    terminate_tree(process.pid)
    try:
        process.wait(timeout=STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        pass
    remove_instance(root)
    raise _error(
        "UI_START_FAILED",
        "A interface não ficou pronta no prazo.",
        EXIT_CODES["OPERATION"],
    )


def start_ui(workdir: str | Path, *, open_browser: bool = True) -> dict[str, Any]:
    """`ui start`: reaproveita a instância viva ou sobe uma nova, e abre o navegador na URL com o token.

    Devolve `state` (`started` ou `running`), `url`, `pid` e `port`. Registro de instância morta é
    descartado antes de subir a nova.
    """

    root = _prepare_root(workdir, create_tmp=True)
    instance = read_instance(root)
    state = "running"
    if instance is None or not probe(instance):
        remove_instance(root)
        try:
            process = _spawn_server(root)
        except OSError as error:
            raise _error(
                "UI_START_FAILED",
                "Não foi possível iniciar a interface.",
                EXIT_CODES["OPERATION"],
            ) from error
        instance = _wait_until_ready(root, process)
        state = "started"
    url = build_url(instance)
    if open_browser:
        webbrowser.open(url)
    return {"state": state, "url": url, "pid": instance.pid, "port": instance.port}


def stop_ui(workdir: str | Path) -> dict[str, Any]:
    """`ui stop`: encerra o servidor e os FFmpeg filhos da instância viva do trabalho.

    Sem instância, ou com registro de processo morto, devolve `not_running` (e limpa o registro velho).
    Só encerra um processo depois de provar, pela porta, que ele é a instância registrada.
    """

    root = _prepare_root(workdir, create_tmp=False)
    instance = read_instance(root)
    if instance is None or not probe(instance):
        remove_instance(root)
        _remove_leftover_filter_scripts(root)
        return {"state": "not_running"}
    terminate_tree(instance.pid)
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while probe(instance):
        if time.monotonic() >= deadline:
            raise _error(
                "UI_STOP_FAILED",
                "Não foi possível encerrar a interface.",
                EXIT_CODES["OPERATION"],
            )
        time.sleep(_POLL_S)
    remove_instance(root)
    _remove_leftover_filter_scripts(root)
    return {"state": "stopped"}
