"""Servidor HTTP local da interface de revisão: sessão por token, allowlists e rotas da API (phase 7, T3).

Executado em segundo plano por `ui start` (`python -m editor.ui.server --workdir <abs>`). Só biblioteca
padrão: `ThreadingHTTPServer` preso a `127.0.0.1` numa porta livre, uma thread por pedido.

Ordem das checagens de todo pedido (nada é aberto antes de a anterior passar):
1. `Host` igual a `127.0.0.1:<porta>` (`UI_HOST_INVALID`, contra DNS rebinding);
2. troca do token por cookie em `GET /?token=` (`UI_TOKEN_INVALID`, `UI_SESSION_LIMIT`), ou cookie de sessão
   reconhecido em tempo constante (`UI_TOKEN_INVALID`; HTML da tela "Sessão inválida" fora de `/api/`);
3. `Origin`, quando presente, igual à origem própria, e obrigatória no POST (`UI_ORIGIN_INVALID`);
4. rota e método da allowlist (404 e 405, ambos `ARGUMENT_INVALID`);
5. query e corpo da allowlist da rota, com tamanhos e formato (`ARGUMENT_INVALID`);
6. o tratamento da rota.

Rotas (a forma é escolha do plano; a interface não é contrato externo):
- `GET /` e `GET /static/<nome>`: estáticos empacotados (só o que existe em `ui/static/`);
- `GET /api/state`: estado do plano (visão, erro seguro, impressão das fontes);
- `GET /api/stream?t=&res=`: MP4 fragmentado H.264/AAC da montagem a partir de `t` (núcleo da T1);
- `GET /api/frame?t=&res=`: JPEG do quadro composto de `t` (scrub);
- `GET /api/selection?start=&end=`: segmentos, camadas e legendas do trecho e o texto "Copiar para IA";
- `GET /api/source/stream?source_id=&t=&res=`: MP4 fragmentado da fonte inteira a partir de `t` (modo Fonte, T5);
- `GET /api/source/frame?source_id=&t=&res=`: JPEG do quadro da fonte em `t` (scrub do modo Fonte);
- `GET /api/source/selection?source_id=&start=&end=`: situação (usado, descartado, parcial), partes usadas e a
  posição de cada uma na montagem, mais o texto "Copiar para IA" da fonte.

Respostas JSON usam o envelope padrão de `contracts`; todas levam CSP, `nosniff` e `Referrer-Policy`.
Erros nunca carregam token, caminho nem detalhe interno, e o log do servidor registra só método, rota
casada e status (a query, onde viaja o token, nunca é registrada).

A fonte do modo Fonte (T5) é sempre um `source_id` de vídeo ou áudio do plano, validado pelo padrão do
trabalho e contra o plano antes de abrir arquivo ou processo; o arquivo vem do plano, nunca do navegador.

Pontos de extensão: a T6 acrescenta `POST /api/approve` (`Route.body_fields`, já validado aqui: JSON só com
esses campos, cada um com 64 hexadecimais minúsculos, até 1 KiB) sobre `approve_workdir_if_unchanged`.

ponytail: HTTP/1.0 sem keep-alive (cada pedido abre uma conexão local), troque por HTTP/1.1 com
`Content-Length` se o custo de conexão aparecer no scrub.
ponytail: o stream não aceita `Range` (sempre recomeça em `t`, que é o contrato do player), acrescentar
se o navegador precisar de busca dentro do trecho já transmitido.
ponytail: sem log persistente (o stderr do servidor em segundo plano é descartado), gravar em `.tmp`
com rotação se diagnosticar falhas do servidor passar a exigir.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.resources
import json
import os
from pathlib import Path
import re
import secrets
import socketserver
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import parse_qsl

from ..contracts import (
    EXIT_CODES,
    CliError,
    error_envelope,
    reject_duplicate_json_keys,
    serialize_envelope,
    success_envelope,
)
from ..render import live
from ..workdir import validate_source_id, validate_workdir
from . import playback
from .instance import Instance, remove_instance, write_instance
from .limits import (
    BODY_DRAIN_MAX_BYTES,
    BODY_MAX_BYTES,
    BODY_VALUE_PATTERN,
    CONTENT_SECURITY_POLICY,
    DECIMAL_PATTERN,
    HOST,
    IDLE_TIMEOUT_S,
    INSTANCE_HEADER,
    PID_HEADER,
    QUERY_MAX_BYTES,
    QUERY_VALUE_MAX_CHARS,
    REFERRER_POLICY,
    SESSION_ACTIVE_S,
    SOURCE_ID_MAX_CHARS,
    STATE_POLL_INTERVAL_S,
    STATIC_NAME_PATTERN,
    STREAM_STALL_TIMEOUT_S,
    TOKEN_BYTES,
    TOKEN_MAX_CHARS,
    TOKEN_PATTERN,
)
from .selection import describe_selection, describe_source_selection
from .sessions import Session, SessionStore, cookie_name, read_cookie, token_invalid
from .state import PlanState, proxy_source_ids, unavailable_source_ids


_STATIC_ROOT = importlib.resources.files("editor.ui").joinpath("static")
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
_STYLE_BLOCK = re.compile(rb"<style>(.*?)</style>", re.DOTALL)
_SERVE_POLL_S = 0.25
_DURATION_EPSILON_S = 1e-9

_STATUS_BY_CODE = {
    "ARGUMENT_INVALID": 400,
    "UI_TOKEN_INVALID": 401,
    "UI_HOST_INVALID": 403,
    "UI_ORIGIN_INVALID": 403,
    "UI_SESSION_LIMIT": 429,
    "APPROVAL_PLAN_CHANGED": 409,
    "PLAN_INVALID": 409,
    "PLAN_REQUIRED": 409,
    "PLAYBACK_FAILED": 500,
    "FRAME_FAILED": 500,
}
"""Status HTTP por código público (spec F2, F3, F4 e F8); o que não está aqui segue o `exit_code` do erro."""

_STATUS_BY_EXIT_CODE = {
    EXIT_CODES["ARGUMENT"]: 400,
    EXIT_CODES["STATE"]: 409,
    EXIT_CODES["MEDIA"]: 409,
    EXIT_CODES["DEPENDENCY"]: 503,
    EXIT_CODES["OPERATION"]: 500,
    EXIT_CODES["INTERNAL"]: 500,
}


# --- Erros e estáticos ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    """Uma rota da allowlist: caminho exato, métodos, parâmetros de query exigidos e corpo permitido.

    `query` lista os nomes que o pedido precisa trazer (todos, sem repetição e sem outros); `body_fields`
    é `None` quando a rota não aceita corpo, ou os campos exatos do JSON aceito no POST.
    """

    path: str
    methods: tuple[str, ...]
    handler: str
    query: tuple[str, ...] = ()
    body_fields: tuple[str, ...] | None = None


ROUTES = (
    Route("/", ("GET",), "_serve_index"),
    Route("/api/state", ("GET",), "_serve_state"),
    Route("/api/stream", ("GET",), "_serve_stream", query=("t", "res")),
    Route("/api/frame", ("GET",), "_serve_frame", query=("t", "res")),
    Route("/api/selection", ("GET",), "_serve_selection", query=("start", "end")),
    Route("/api/source/stream", ("GET",), "_serve_source_stream", query=("source_id", "t", "res")),
    Route("/api/source/frame", ("GET",), "_serve_source_frame", query=("source_id", "t", "res")),
    Route("/api/source/selection", ("GET",), "_serve_source_selection", query=("source_id", "start", "end")),
)
"""Allowlist de rotas dinâmicas; os estáticos entram por `/static/<nome>` com a própria allowlist de nomes."""

STATIC_PREFIX = "/static/"
API_PREFIX = "/api/"


class HttpRefusal(Exception):
    """Recusa com status próprio: 404 e 405 carregam `ARGUMENT_INVALID` com status que o código não diz."""

    def __init__(self, error: CliError, status: int, headers: Mapping[str, str] | None = None) -> None:
        self.error = error
        self.status = status
        self.headers = dict(headers or {})
        super().__init__(error.code)


def _argument_error(message: str = "Os parâmetros do pedido são inválidos.") -> CliError:
    """Erro público de entrada fora da allowlist; a mensagem é fixa e nunca repete o que foi recebido."""

    return CliError(code="ARGUMENT_INVALID", message=message, exit_code=EXIT_CODES["ARGUMENT"])


def _host_invalid() -> CliError:
    """Erro público de `Host` fora de `127.0.0.1:<porta>`."""

    return CliError(
        code="UI_HOST_INVALID",
        message="O endereço de acesso não é permitido.",
        exit_code=EXIT_CODES["STATE"],
    )


def _origin_invalid() -> CliError:
    """Erro público de `Origin` estranha ou ausente no POST."""

    return CliError(
        code="UI_ORIGIN_INVALID",
        message="A origem do pedido não é permitida.",
        exit_code=EXIT_CODES["STATE"],
    )


def status_for(error: CliError) -> int:
    """Status HTTP do erro: o da tabela por código, ou o derivado do `exit_code` (500 se desconhecido)."""

    return _STATUS_BY_CODE.get(error.code) or _STATUS_BY_EXIT_CODE.get(error.exit_code, 500)


def read_static(name: str) -> tuple[bytes, str] | None:
    """Conteúdo e tipo de um estático empacotado, ou `None` se o nome não é permitido ou não existe.

    O nome passa pelo padrão sem separador de caminho e só serve arquivo regular que exista dentro de
    `ui/static/`, então nada fora dessa pasta é alcançável por entrada do navegador.
    """

    if STATIC_NAME_PATTERN.fullmatch(name) is None:
        return None
    content_type = _STATIC_TYPES.get(Path(name).suffix)
    entry = _STATIC_ROOT.joinpath(name)
    if content_type is None or not entry.is_file():
        return None
    return entry.read_bytes(), content_type


def _invalid_page() -> tuple[bytes, str]:
    """Tela "Sessão inválida" e a CSP dela: a base da spec mais o hash do `<style>` embutido.

    A tela não pode depender de estático (todo estático exige sessão) nem de estilo em linha sem
    permissão; o hash do bloco de estilo, calculado sobre o texto como o navegador o lê (quebras de
    linha normalizadas), libera só ele e mantém a CSP restritiva.
    """

    found = read_static("invalid.html")
    if found is None:
        raise CliError(code="INTERNAL_ERROR", message="Falha interna.", exit_code=EXIT_CODES["INTERNAL"])
    page = found[0]
    policy = CONTENT_SECURITY_POLICY
    match = _STYLE_BLOCK.search(page)
    if match is not None:
        text = match.group(1).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        policy += f"; style-src 'sha256-{base64.b64encode(hashlib.sha256(text).digest()).decode('ascii')}'"
    return page, policy


# --- Entrada: query e corpo -----------------------------------------------------------------------


def parse_query(raw_query: str, names: tuple[str, ...], *, max_chars: Mapping[str, int] | None = None) -> dict[str, str]:
    """Query da allowlist: exatamente os `names`, sem repetição, não vazios e com o tamanho máximo de cada um.

    Recusa tudo o que não casar com `ARGUMENT_INVALID` sem repetir o conteúdo: query acima de 2 KiB,
    campo fora da lista, repetido, ausente, vazio ou longo demais, e escape inválido.
    """

    if len(raw_query.encode("utf-8", "replace")) > QUERY_MAX_BYTES:
        raise _argument_error()
    try:
        pairs = (
            parse_qsl(raw_query, keep_blank_values=True, strict_parsing=True, max_num_fields=len(names) + 1)
            if raw_query
            else []
        )
    except ValueError as error:
        raise _argument_error() from error
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys) or set(keys) != set(names):
        raise _argument_error()
    limits = {name: QUERY_VALUE_MAX_CHARS for name in names} | dict(max_chars or {})
    for key, value in pairs:
        if not value or len(value) > limits[key]:
            raise _argument_error()
    return dict(pairs)


def _decimal(value: str) -> float:
    """Decimal finito sem sinal nem expoente (`t`, `start`, `end`); o intervalo é conferido contra a duração depois."""

    if DECIMAL_PATTERN.fullmatch(value) is None:
        raise _argument_error()
    return float(value)


def _resolution(value: str) -> str:
    """Resolução da allowlist do player (`full`, `1/2`, `1/3`, `1/4`)."""

    if value not in live.RESOLUTIONS:
        raise _argument_error()
    return value


# --- Servidor ---------------------------------------------------------------------------------------


class UiServer(ThreadingHTTPServer):
    """Servidor da interface de um trabalho: token, sessões, estado do plano e prazo de inatividade.

    `run()` bloqueia até o encerramento (prazo de inatividade ou `close()` de outra thread) e libera tudo
    ao sair: sessões, FFmpeg filhos e vigia do plano. Os parâmetros de prazo e relógio existem para o
    teste reduzi-los; o padrão é o da spec.
    """

    daemon_threads = True
    # Windows: SO_REUSEADDR deixaria outro processo escutar a mesma porta ao mesmo tempo.
    allow_reuse_address = False

    def __init__(
        self,
        root: Path,
        *,
        token: str | None = None,
        instance_id: str | None = None,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
        session_active_s: float = SESSION_ACTIVE_S,
        poll_interval_s: float = STATE_POLL_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__((HOST, 0), UiRequestHandler)
        self.root = root
        self.port: int = self.server_address[1]
        self.token = token or secrets.token_urlsafe(TOKEN_BYTES)
        self.instance_id = instance_id or secrets.token_hex(16)
        self.cookie = cookie_name(self.port)
        self.idle_timeout_s = idle_timeout_s
        self.sessions = SessionStore(active_s=session_active_s, clock=clock)
        self.plan_state = PlanState(root, poll_interval_s=poll_interval_s)
        self._clock = clock
        self._last_activity = clock()
        self._log = log or (lambda line: print(line, file=sys.stderr, flush=True))
        self._stopped = threading.Event()
        self._serving = False
        self._closed = False
        self._close_lock = threading.Lock()

    def server_bind(self) -> None:
        """Liga o socket sem a resolução reversa de nome do `HTTPServer` (lenta no Windows e inútil em loopback)."""

        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port

    def log(self, line: str) -> None:
        """Registra uma linha segura de log (o chamador nunca passa query, cookie nem token)."""

        self._log(line)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """Falha não tratada ao atender um pedido: o padrão imprime o traceback, aqui só entra texto fixo."""

        self.log("ui: erro ao atender pedido")

    def touch(self) -> None:
        """Marca atividade autenticada, que adia o encerramento por inatividade."""

        self._last_activity = self._clock()

    def run(self) -> None:
        """Atende pedidos até o encerramento e libera os recursos; bloqueia a thread chamadora."""

        self.plan_state.start()
        threading.Thread(target=self._watch_idle, name="editor-ui-idle", daemon=True).start()
        self.log(f"ui: escutando em {HOST}:{self.port}")
        try:
            self._serving = True
            self.serve_forever(poll_interval=_SERVE_POLL_S)
        finally:
            self._serving = False
            self.close()

    def _watch_idle(self) -> None:
        """Encerra o servidor quando passa o prazo sem pedido autenticado (suposição 9)."""

        interval = min(1.0, max(0.05, self.idle_timeout_s / 4))
        while not self._stopped.wait(interval):
            if self._clock() - self._last_activity >= self.idle_timeout_s:
                self.log("ui: encerrando por inatividade")
                self.close()
                return

    def close(self) -> None:
        """Para de atender e encerra sessões, FFmpeg filhos e a vigia do plano; seguro de qualquer thread e repetível."""

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._stopped.set()
        self.plan_state.stop()
        if self._serving:
            self.shutdown()
        self.server_close()
        self.sessions.close_all()


class UiRequestHandler(BaseHTTPRequestHandler):
    """Um pedido: aplica a ordem de checagens do módulo e despacha para a rota, com respostas seguras."""

    server: UiServer
    # HTTP/1.0: sem keep-alive e com o stream delimitado pelo fechamento da conexão.
    protocol_version = "HTTP/1.0"
    # Também é o prazo de escrita: cliente que para de consumir o stream por 30 s encerra o FFmpeg.
    timeout = STREAM_STALL_TIMEOUT_S

    _log_path = "(desconhecida)"
    _body_consumed = False
    _session: Session | None = None

    # --- Log e erros de protocolo ---

    def version_string(self) -> str:
        """Identifica só o produto: nada da versão do Python no cabeçalho `Server`."""

        return "editor-ui"

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        """Registra método, rota casada e status; a linha do pedido (com a query e o token) nunca entra."""

        self.server.log(f"ui: {self.command or '-'} {self._log_path} {code}")

    def log_error(self, format: str, *args: Any) -> None:
        """Erro de protocolo do `http.server`: os argumentos citam a linha do pedido, então só entra texto fixo."""

        self.server.log("ui: erro de protocolo")

    def log_message(self, format: str, *args: Any) -> None:
        """Mensagens genéricas do `http.server` não carregam dado do pedido nesta classe e não são registradas."""

    def send_error(self, code: Any, message: str | None = None, explain: str | None = None) -> None:
        """Erro de protocolo no envelope padrão (o `http.server` responderia HTML com a mensagem recebida).

        Método sem `do_*` (PUT, DELETE, OPTIONS, HEAD...) chega aqui como 501 e vira 405 com `Allow`.
        """

        status = 405 if code == HTTPStatus.NOT_IMPLEMENTED else int(code)
        headers = {"Allow": "GET, POST"} if status == 405 else None
        text = "Método não permitido." if status == 405 else "Pedido inválido."
        try:
            self._send_json(status, error_envelope(_argument_error(text)), headers)
        except OSError:
            pass
        self.close_connection = True

    # --- Escrita de respostas ---

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        headers: Mapping[str, str] | None = None,
        policy: str = CONTENT_SECURITY_POLICY,
    ) -> None:
        """Resposta completa com os cabeçalhos de segurança de toda resposta."""

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers(policy)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_security_headers(self, policy: str = CONTENT_SECURITY_POLICY) -> None:
        """CSP, `nosniff`, `Referrer-Policy`, sem cache e a identidade e o pid da instância, em toda resposta."""

        self.send_header("Content-Security-Policy", policy)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", REFERRER_POLICY)
        self.send_header("Cache-Control", "no-store")
        self.send_header(INSTANCE_HEADER, self.server.instance_id)
        self.send_header(PID_HEADER, str(os.getpid()))

    def _send_json(self, status: int, envelope: Mapping[str, Any], headers: Mapping[str, str] | None = None) -> None:
        """Resposta JSON no envelope padrão, UTF-8 e com tamanho conhecido."""

        self._send(
            status,
            serialize_envelope(envelope).encode("utf-8"),
            "application/json; charset=utf-8",
            headers=headers,
        )

    def _drain_body(self) -> None:
        """Lê (até um teto) o corpo que será recusado, para fechar a conexão sem RST sobre a resposta.

        Corpo de tamanho desconhecido (`Transfer-Encoding`) ou acima do teto não é lido.
        """

        if self._body_consumed:
            return
        self._body_consumed = True
        raw = self.headers.get("Content-Length")
        if raw is None or not raw.isascii() or not raw.isdigit() or len(raw) > 12:
            return
        length = min(int(raw), BODY_DRAIN_MAX_BYTES)
        if length:
            try:
                self.rfile.read(length)
            except OSError:
                pass

    def _send_error(self, path: str, error: CliError, status: int | None = None, headers: Mapping[str, str] | None = None) -> None:
        """Responde um erro de domínio: a tela "Sessão inválida" para navegação sem sessão, JSON no resto."""

        self._drain_body()
        status = status if status is not None else status_for(error)
        if error.code == "UI_TOKEN_INVALID" and not path.startswith(API_PREFIX):
            page, policy = _invalid_page()
            # A tela não traz o código no corpo; o cabeçalho o deixa legível a quem chama por script.
            self._send(
                status,
                page,
                "text/html; charset=utf-8",
                headers={**(headers or {}), "X-Editor-Error": error.code},
                policy=policy,
            )
        else:
            self._send_json(status, error_envelope(error), headers)

    # --- Despacho ---

    def do_GET(self) -> None:
        """Método GET da allowlist: segue a ordem de checagens de `_dispatch`."""

        self._dispatch("GET")

    def do_POST(self) -> None:
        """Método POST da allowlist (a aprovação da T6): exige `Origin` própria e segue `_dispatch`."""

        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        """Aplica as checagens na ordem do módulo e chama a rota; qualquer falha vira resposta segura."""

        path = "/"
        try:
            if not self.path.startswith("/"):
                raise _argument_error()
            path, _, raw_query = self.path.partition("?")
            self._check_host()
            if method == "GET" and path == "/" and raw_query:
                self._log_path = "/"
                self._login(raw_query)
                return
            self._session = self.server.sessions.authenticate(
                read_cookie(self.headers.get("Cookie"), self.server.cookie)
            )
            self.server.touch()
            self._check_origin(method)
            route = self._find_route(path, method)
            if route is None:
                self._log_path = STATIC_PREFIX
                self._serve_static(path[len(STATIC_PREFIX):], raw_query)
            else:
                self._log_path = route.path
                self._check_no_unexpected_body(route)
                getattr(self, route.handler)(route, raw_query)
        except HttpRefusal as refusal:
            self._send_error(path, refusal.error, refusal.status, refusal.headers)
        except CliError as error:
            self._send_error(path, error)
        except OSError:
            # Cliente que fechou a conexão ou estourou o prazo de leitura ou escrita: não há a quem responder.
            self.close_connection = True
        except Exception as error:
            self.server.log(f"ui: erro interno ({type(error).__name__})")
            self._send_error(
                path,
                CliError(code="INTERNAL_ERROR", message="Falha interna.", exit_code=EXIT_CODES["INTERNAL"]),
            )

    def _check_host(self) -> None:
        """`Host` exatamente igual a `127.0.0.1:<porta>`, uma única vez (defesa contra DNS rebinding)."""

        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() != f"{HOST}:{self.server.port}":
            raise _host_invalid()

    def _check_origin(self, method: str) -> None:
        """`Origin`, quando presente, é a origem própria; o POST exige a presença (nenhum CORS)."""

        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] != f"http://{HOST}:{self.server.port}"):
            raise _origin_invalid()
        if method == "POST" and not origins:
            raise _origin_invalid()

    def _login(self, raw_query: str) -> None:
        """`GET /?token=`: confere o token, abre (ou reaproveita) a sessão, entrega o cookie e tira o token do endereço."""

        token = parse_query(raw_query, ("token",), max_chars={"token": TOKEN_MAX_CHARS})["token"]
        if TOKEN_PATTERN.fullmatch(token) is None or not hmac.compare_digest(
            token.encode("utf-8"), self.server.token.encode("ascii")
        ):
            raise token_invalid()
        self._check_origin("GET")
        headers = {"Location": "/"}
        try:
            # O mesmo navegador reabrindo a URL mantém a sessão (e não gasta uma das duas vagas).
            self.server.sessions.authenticate(read_cookie(self.headers.get("Cookie"), self.server.cookie))
        except CliError:
            session = self.server.sessions.open()
            headers["Set-Cookie"] = (
                f"{self.server.cookie}={session.identifier}; HttpOnly; SameSite=Strict; Path=/"
            )
        self.server.touch()
        self.send_response(302)
        self.send_header("Content-Length", "0")
        for name, value in headers.items():
            self.send_header(name, value)
        self._send_security_headers()
        self.end_headers()

    def _find_route(self, path: str, method: str) -> Route | None:
        """Rota da allowlist pelo caminho exato; `None` para o prefixo de estáticos, 404 e 405 para o resto."""

        for route in ROUTES:
            if route.path == path:
                if method not in route.methods:
                    raise HttpRefusal(
                        _argument_error("Método não permitido."), 405, {"Allow": ", ".join(route.methods)}
                    )
                return route
        if path.startswith(STATIC_PREFIX):
            if method != "GET":
                raise HttpRefusal(_argument_error("Método não permitido."), 405, {"Allow": "GET"})
            return None
        raise HttpRefusal(_argument_error("Rota não encontrada."), 404)

    def _check_no_unexpected_body(self, route: Route) -> None:
        """Recusa corpo em rota que não o aceita (e `Transfer-Encoding`, que não é suportado)."""

        if self.headers.get("Transfer-Encoding") is not None:
            raise _argument_error()
        raw = self.headers.get("Content-Length")
        if route.body_fields is None and raw not in (None, "0"):
            raise _argument_error()

    def read_json_body(self, route: Route) -> dict[str, str]:
        """Corpo JSON da rota: objeto só com os campos de `route.body_fields`, cada um com 64 hexadecimais, até 1 KiB.

        Ainda sem rota que o use (a aprovação chega na T6); fica aqui porque a allowlist de corpo é parte
        da superfície que esta tarefa fecha.
        """

        raw = self.headers.get("Content-Length")
        if (
            route.body_fields is None
            or raw is None
            or not raw.isascii()
            or not raw.isdigit()
            or not 0 < int(raw) <= BODY_MAX_BYTES
        ):
            raise _argument_error()
        content = self.rfile.read(int(raw))
        self._body_consumed = True
        try:
            body = json.loads(content.decode("utf-8"), object_pairs_hook=reject_duplicate_json_keys)
        except (UnicodeError, ValueError) as error:
            raise _argument_error() from error
        if (
            not isinstance(body, dict)
            or set(body) != set(route.body_fields)
            or any(not isinstance(value, str) or BODY_VALUE_PATTERN.fullmatch(value) is None for value in body.values())
        ):
            raise _argument_error()
        return body

    # --- Rotas ---

    def _serve_static(self, name: str, raw_query: str) -> None:
        """`GET /static/<nome>`: estático empacotado; nome fora da allowlist ou inexistente é 404."""

        parse_query(raw_query, ())
        found = read_static(name)
        if found is None:
            raise HttpRefusal(_argument_error("Rota não encontrada."), 404)
        self._send(200, found[0], found[1])

    def _serve_index(self, route: Route, raw_query: str) -> None:
        """`GET /`: a página da interface, só com sessão (sem sessão a tela "Sessão inválida" já foi servida)."""

        parse_query(raw_query, route.query)
        found = read_static("index.html")
        if found is None:
            raise CliError(code="INTERNAL_ERROR", message="Falha interna.", exit_code=EXIT_CODES["INTERNAL"])
        self._send(200, found[0], found[1])

    def _serve_state(self, route: Route, raw_query: str) -> None:
        """`GET /api/state`: estado do plano, com proxy e disponibilidade de cada fonte atualizados a cada consulta."""

        parse_query(raw_query, route.query)
        reading = self.server.plan_state.read()
        plan = None
        if reading.snapshot is not None:
            proxies = proxy_source_ids(self.server.root, reading.snapshot.validation)
            missing = unavailable_source_ids(reading.snapshot.validation)
            failure = live.source_unavailable()
            unavailable = {"code": failure.code, "message": failure.message}
            plan = dict(reading.snapshot.view)
            plan["sources"] = [
                {
                    **source,
                    "proxy": source["id"] in proxies,
                    # Fonte que sumiu do disco: a linha do modo Fonte vira "Indisponível" com o erro público.
                    "available": source["id"] not in missing,
                    "error": unavailable if source["id"] in missing else None,
                }
                for source in plan["sources"]
            ]
        data = {
            "workdir_name": self.server.root.name,
            "state": reading.status,
            "error": reading.error,
            "resolutions": list(live.RESOLUTIONS),
            "plan": plan,
        }
        self._send_json(200, success_envelope("ui state", data))

    def _serve_stream(self, route: Route, raw_query: str) -> None:
        """`GET /api/stream`: MP4 fragmentado da montagem de `t` ao fim, pelo mesmo núcleo da T1.

        Tudo é validado antes de abrir o FFmpeg. Falha antes do primeiro byte responde `PLAYBACK_FAILED`
        (500); depois dele a conexão é só encerrada. O cabeçalho `X-Editor-Start-S` traz o instante do
        primeiro quadro (o `t` colado na grade de quadros), base do timecode do player.
        """

        params = parse_query(raw_query, route.query)
        start_s, resolution = _decimal(params["t"]), _resolution(params["res"])
        snapshot = self.server.plan_state.require_snapshot()
        render = live.prepare_live(self.server.root, snapshot.document, snapshot.validation, start_s, resolution)
        with live.filter_script(self.server.root, render) as script:
            try:
                process = playback.spawn_ffmpeg(live.stream_arguments(render, script))
            except OSError as error:
                raise playback.playback_failed() from error
            # O script do filtro precisa existir enquanto o FFmpeg lê o grafo, então o relé fica dentro do `with`.
            self._relay_stream("stream", process, render.start_s)

    def _relay_stream(self, kind: str, process: subprocess.Popen[bytes], start_s: float) -> None:
        """Repassa o stdout do FFmpeg ao cliente como `video/mp4`, no espaço `kind` da sessão.

        O cabeçalho de sucesso, com `X-Editor-Start-S` (instante do primeiro quadro, base do timecode do
        player), só sai depois do primeiro byte do FFmpeg: antes disso uma falha ainda vira `PLAYBACK_FAILED`.
        Comum ao stream da montagem e ao da fonte inteira.
        """

        assert self._session is not None

        def send_headers() -> None:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("X-Editor-Start-S", f"{start_s:.6f}")
            self._send_security_headers()
            self.end_headers()

        try:
            playback.relay_stream(
                self._session.slots,
                kind,
                process,
                send_headers=send_headers,
                write=self.wfile.write,
                touch=self.server.touch,
            )
        except OSError:
            # O cliente fechou a conexão (ou parou de consumir): o relé já encerrou o FFmpeg.
            self.close_connection = True

    def _serve_frame(self, route: Route, raw_query: str) -> None:
        """`GET /api/frame`: JPEG do quadro composto de `t`, o primeiro quadro do mesmo grafo do stream."""

        params = parse_query(raw_query, route.query)
        start_s, resolution = _decimal(params["t"]), _resolution(params["res"])
        snapshot = self.server.plan_state.require_snapshot()
        render = live.prepare_live(
            self.server.root, snapshot.document, snapshot.validation, start_s, resolution, audio=False
        )
        assert self._session is not None
        with live.filter_script(self.server.root, render) as script:
            jpeg = playback.capture_frame(
                self._session.slots, live.live_arguments(render, script, live.frame_output_arguments(render))
            )
        self._send(200, jpeg, "image/jpeg", headers={"X-Editor-Start-S": f"{render.start_s:.6f}"})

    def _serve_selection(self, route: Route, raw_query: str) -> None:
        """`GET /api/selection`: o que há no trecho `[start, end]` da montagem e o texto "Copiar para IA"."""

        params = parse_query(raw_query, route.query)
        start_s, end_s = _decimal(params["start"]), _decimal(params["end"])
        snapshot = self.server.plan_state.require_snapshot()
        if not 0 <= start_s < end_s <= snapshot.duration_s + _DURATION_EPSILON_S:
            raise _argument_error()
        data = describe_selection(snapshot, self.server.root.name, start_s, end_s)
        self._send_json(200, success_envelope("ui selection", data))

    # --- Rotas do modo Fonte (T5) ---

    def _source_params(self, route: Route, raw_query: str) -> tuple[dict[str, str], str]:
        """Query da rota do modo Fonte e o `source_id` dela, validado pelo padrão do trabalho (a existência no plano vem depois)."""

        params = parse_query(raw_query, route.query, max_chars={"source_id": SOURCE_ID_MAX_CHARS})
        return params, validate_source_id(params["source_id"])

    def _serve_source_stream(self, route: Route, raw_query: str) -> None:
        """`GET /api/source/stream`: MP4 fragmentado da fonte inteira de `t` ao fim (original em `full`, proxy nas frações).

        Mesmas garantias do stream da montagem (tudo validado antes do FFmpeg, falha antes do primeiro byte
        é `PLAYBACK_FAILED`, o cliente que fecha encerra o processo), no espaço `source` da sessão.
        """

        params, source_id = self._source_params(route, raw_query)
        start_s, resolution = _decimal(params["t"]), _resolution(params["res"])
        snapshot = self.server.plan_state.require_snapshot()
        render = live.prepare_source_live(self.server.root, snapshot.validation, source_id, start_s, resolution)
        try:
            process = playback.spawn_ffmpeg(live.source_stream_arguments(render))
        except OSError as error:
            raise playback.playback_failed() from error
        self._relay_stream("source", process, render.start_s)

    def _serve_source_frame(self, route: Route, raw_query: str) -> None:
        """`GET /api/source/frame`: JPEG do quadro da fonte de vídeo em `t` (fonte só de áudio não tem imagem: 400)."""

        params, source_id = self._source_params(route, raw_query)
        start_s, resolution = _decimal(params["t"]), _resolution(params["res"])
        snapshot = self.server.plan_state.require_snapshot()
        render = live.prepare_source_live(self.server.root, snapshot.validation, source_id, start_s, resolution)
        assert self._session is not None
        jpeg = playback.capture_frame(self._session.slots, live.source_frame_arguments(render))
        self._send(200, jpeg, "image/jpeg", headers={"X-Editor-Start-S": f"{render.start_s:.6f}"})

    def _serve_source_selection(self, route: Route, raw_query: str) -> None:
        """`GET /api/source/selection`: o trecho `[start, end]` da fonte: situação, partes usadas e texto "Copiar para IA".

        Só lê o plano (não abre a fonte), então responde também com a fonte indisponível no disco.
        """

        params, source_id = self._source_params(route, raw_query)
        start_s, end_s = _decimal(params["start"]), _decimal(params["end"])
        snapshot = self.server.plan_state.require_snapshot()
        record = live.source_record(snapshot.validation, source_id)
        if not 0 <= start_s < end_s <= float(record["duration_seconds"]) + _DURATION_EPSILON_S:
            raise _argument_error()
        data = describe_source_selection(snapshot, self.server.root.name, source_id, start_s, end_s)
        self._send_json(200, success_envelope("ui source selection", data))


# --- Processo do servidor ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entrada de `python -m editor.ui.server`: escuta, registra a instância e atende até o encerramento.

    O registro (com o token) só é escrito depois de a porta estar escutando, e é removido ao sair. O
    prazo de inatividade pode ser reduzido (para provar o encerramento sem esperar 2 h), nunca ampliado.
    """

    parser = argparse.ArgumentParser(prog="python -m editor.ui.server", allow_abbrev=False)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--idle-timeout-s", type=float, default=IDLE_TIMEOUT_S)
    args = parser.parse_args(argv)
    if not 0 < args.idle_timeout_s <= IDLE_TIMEOUT_S:
        parser.error("prazo de inatividade fora do intervalo permitido")
    try:
        root = validate_workdir(args.workdir)
    except CliError:
        return EXIT_CODES["STATE"]
    server = UiServer(root, idle_timeout_s=args.idle_timeout_s)
    instance = Instance(
        pid=os.getpid(), port=server.port, token=server.token, instance_id=server.instance_id
    )
    try:
        write_instance(root, instance)
    except BaseException:
        server.close()
        raise
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        remove_instance(root, instance.instance_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
