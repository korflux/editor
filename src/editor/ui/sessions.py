"""Sessões do navegador: cookie por sessão, teto de sessões simultâneas e processos de cada uma.

Responsabilidade: dado o token da instância já conferido (isso é do servidor), abrir uma sessão e
devolver o cookie, reconhecer o cookie nos pedidos seguintes e aplicar o teto de sessões
(suposição 13). Cada sessão guarda o `ProcessSlots` dos seus FFmpeg, então descartar a sessão encerra
os processos dela.

Por que um identificador por sessão e não o próprio token no cookie: o token da URL vale enquanto a
instância viver e é um só; se fosse o cookie, não haveria como distinguir as duas abas permitidas nem
descartar a ociosa mais antiga. O cookie é um identificador aleatório de 32 bytes por sessão.

Regras do teto: conta como simultânea a sessão com pedido autenticado nos últimos `active_s`
segundos. Chegando uma sessão nova com o teto cheio, a ociosa mais antiga é descartada (o cookie dela
passa a ser recusado com `UI_TOKEN_INVALID`); sem nenhuma ociosa, a nova recebe `UI_SESSION_LIMIT`.

ponytail: cookies não são isolados por porta, então outro servidor local em outra porta de `127.0.0.1`
que o operador visitasse receberia o cookie `HttpOnly` e poderia reutilizá-lo; isso equivale ao acesso que
um processo local do mesmo usuário já tem pelo registro do token em `.tmp`, aceito enquanto a interface for
de um operador numa máquina própria, trocar por um segredo a mais por pedido (cabeçalho preenchido pela
página) se esse modelo de ameaça passar a importar.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import hmac
import secrets
import threading
import time

from ..contracts import EXIT_CODES, CliError
from .limits import COOKIE_PREFIX, SESSION_ACTIVE_S, SESSION_LIMIT, TOKEN_BYTES
from .playback import ProcessSlots


def token_invalid() -> CliError:
    """Erro público de sessão ausente, descartada ou com token errado (a mensagem é a da spec, F2)."""

    return CliError(
        code="UI_TOKEN_INVALID",
        message="Sessão inválida. Abra a interface de novo pela CLI.",
        exit_code=EXIT_CODES["STATE"],
    )


def session_limit() -> CliError:
    """Erro público da terceira sessão simultânea, sem dado das sessões existentes."""

    return CliError(
        code="UI_SESSION_LIMIT",
        message="Há sessões demais abertas. Feche outra janela da interface e tente de novo.",
        exit_code=EXIT_CODES["STATE"],
    )


def cookie_name(port: int) -> str:
    """Nome do cookie de sessão com a porta, para duas instâncias no mesmo navegador não se sobrescreverem."""

    return f"{COOKIE_PREFIX}{port}"


def read_cookie(header: str | None, name: str) -> str | None:
    """Lê o valor de um cookie pelo nome, sem aceitar nada além de `nome=valor` separados por `;`.

    Parser próprio e mínimo (a biblioteca padrão é leniente com aspas e atributos); na repetição vale o primeiro.
    """

    if not header:
        return None
    for part in header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value
    return None


@dataclass
class Session:
    """Uma aba autorizada: identificador do cookie, último pedido e os FFmpeg que ela mantém."""

    identifier: str
    last_seen: float
    slots: ProcessSlots = field(default_factory=ProcessSlots)


class SessionStore:
    """Sessões vivas de uma instância, protegidas por trava (um servidor com uma thread por pedido)."""

    def __init__(
        self,
        *,
        limit: int = SESSION_LIMIT,
        active_s: float = SESSION_ACTIVE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._active_s = active_s
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: list[Session] = []

    def open(self) -> Session:
        """Abre uma sessão nova, descartando a ociosa mais antiga se o teto estiver cheio.

        Levanta `UI_SESSION_LIMIT` quando todas as sessões existentes estão ativas.
        """

        discarded: Session | None = None
        with self._lock:
            now = self._clock()
            if len(self._sessions) >= self._limit:
                idle = [item for item in self._sessions if now - item.last_seen > self._active_s]
                if not idle:
                    raise session_limit()
                discarded = min(idle, key=lambda item: item.last_seen)
                self._sessions.remove(discarded)
            session = Session(identifier=secrets.token_urlsafe(TOKEN_BYTES), last_seen=now)
            self._sessions.append(session)
        if discarded is not None:
            discarded.slots.terminate_all()
        return session

    def authenticate(self, identifier: str | None) -> Session:
        """Reconhece o cookie e marca o pedido como atividade; recusa com `UI_TOKEN_INVALID`.

        A comparação é em tempo constante contra cada sessão (são no máximo duas), sem consulta por
        chave de dicionário, que vazaria o prefixo do identificador pelo tempo de resposta.
        """

        if identifier is not None:
            candidate = identifier.encode("utf-8", "replace")
            with self._lock:
                for session in self._sessions:
                    if hmac.compare_digest(candidate, session.identifier.encode("ascii")):
                        session.last_seen = self._clock()
                        return session
        raise token_invalid()

    def close_all(self) -> None:
        """Descarta todas as sessões e mata os FFmpeg delas (servidor encerrando)."""

        with self._lock:
            sessions, self._sessions = self._sessions, []
        for session in sessions:
            session.slots.terminate_all()
