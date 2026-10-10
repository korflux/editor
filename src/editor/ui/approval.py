"""Situação da aprovação que a interface exibe no selo (phase 7, T6): nenhuma, em verificação, em dia ou desatualizada.

Responsabilidade: responder, a cada `GET /api/state`, se o `approval.json` do trabalho vale para a versão que a
tela exibe, sem pagar o SHA-256 das fontes a cada consulta (a tela consulta a cada segundo e as fontes têm até
4 GiB). Quem libera a full continua sendo `verify_approval`; o selo é só informativo e nunca afirma "em dia" por
palpite: a dúvida resolve para "desatualizada" ou "em verificação".

Estados (`data.approval.state` do estado, com `approved_at` só quando está em dia):
- `none`: não há `approval.json`;
- `checking`: há registro para o plano exibido e as fontes estão sendo conferidas em segundo plano (ou o plano
  ainda está sendo carregado);
- `current`: o registro vale para o plano e para o conteúdo das fontes exibidos; `approved_at` é o instante UTC;
- `stale`: há registro, mas ele não vale: outro plano, fonte com outro conteúdo, registro v1 ou corrompido, ou
  o plano em disco está inválido (nenhuma aprovação vale para um plano que a full recusaria).

Custo, do mais barato ao mais caro, sempre na ordem:
1. `lstat` do `approval.json` (some: `none`);
2. o resultado conclusivo guardado para a chave `(lstat do registro, plan_sha256, sources_fingerprint)`;
3. leitura estrutural do registro (até 64 KiB) para comparar `plan_sha256`: outro plano é `stale` sem tocar nas
   fontes, que é o caso de toda edição do plano depois de aprovado;
4. só com o plano igual, `verify_approval` (SHA-256 de todas as fontes) em uma thread por vez, uma única vez por
   chave. A aprovação feita pela própria interface não passa por aqui: `remember_current` registra o resultado,
   porque a aprovação condicionada já conferiu plano e impressão antes e depois de calcular os hashes.

ponytail: a conferência completa lê as fontes inteiras (uma thread por vez) quando o registro vem de fora da
interface (`editor approve` pela CLI) ou depois de reiniciar o servidor, e o selo fica "Verificando" por esse
tempo; guardar a impressão das fontes no `approval.json` (mudança de contrato da aprovação) se esse tempo incomodar.
ponytail: o resultado é guardado só para a última chave (uma entrada), então alternar entre dois estados do plano
refaz a conferência das fontes a cada volta; guardar mais entradas se esse vaivém for comum.
"""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from ..contracts import CliError
from ..integrity.approval import read_approval, verify_approval
from .state import PlanSnapshot, StateReading, stat_key


NONE = "none"
CHECKING = "checking"
CURRENT = "current"
STALE = "stale"

_Key = tuple[Any, str, str]


def _result(state: str, approved_at: str | None = None) -> dict[str, str | None]:
    """Forma pública da situação: o estado e, só em dia, o instante UTC da aprovação."""

    return {"state": state, "approved_at": approved_at}


def _conclusive(error: Exception) -> dict[str, str | None]:
    """Resultado de um registro que não pôde ser lido ou conferido: ausente é `none`, qualquer outro defeito é `stale`.

    O selo é informativo: nenhuma falha ao lê-lo (inclusive um JSON aninhado a ponto de estourar a recursão)
    pode derrubar o estado da tela, e a dúvida resolve para `stale`, nunca para "em dia".
    """

    absent = isinstance(error, CliError) and error.code == "APPROVAL_REQUIRED"
    return _result(NONE if absent else STALE)


class ApprovalTracker:
    """Situação da aprovação do trabalho, calculada sob demanda e guardada pela chave da versão exibida."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._path = root / "approval.json"
        self._lock = threading.Lock()
        self._known: tuple[_Key, dict[str, str | None]] | None = None
        self._pending: _Key | None = None

    def status(self, reading: StateReading) -> dict[str, str | None]:
        """Situação da aprovação para a leitura atual do plano; nunca bloqueia na leitura das fontes."""

        file_key = stat_key(self._path)
        if file_key is None:
            return _result(NONE)
        snapshot = reading.snapshot
        if snapshot is None:
            # Sem versão válida não há contra o que comparar; enquanto a primeira carga não termina, ainda não se sabe.
            return _result(STALE if reading.loaded else CHECKING)
        if reading.error is not None:
            return _result(STALE)
        key: _Key = (file_key, snapshot.plan_sha256, snapshot.sources_fingerprint)
        with self._lock:
            if self._known is not None and self._known[0] == key:
                return dict(self._known[1])
            if self._pending == key:
                return _result(CHECKING)
        try:
            approval = read_approval(self._root)
        except Exception as error:
            return self._remember(key, _conclusive(error))
        if approval["plan_sha256"] != snapshot.plan_sha256:
            return self._remember(key, _result(STALE))
        self._start_check(key, snapshot)
        return _result(CHECKING)

    def remember_current(self, plan_sha256: str, sources_fingerprint: str, approved_at: str) -> None:
        """Registra a aprovação que a própria interface acabou de publicar, sem reler as fontes.

        A chave usa o `lstat` do registro de agora: se outro processo o trocar entre a publicação e esta
        chamada, o selo pode afirmar "em dia" para o registro dele até a próxima mudança do arquivo, o que só
        afeta o indicador (a full verifica por conta própria).
        """

        file_key = stat_key(self._path)
        if file_key is None:
            return
        with self._lock:
            self._known = ((file_key, plan_sha256, sources_fingerprint), _result(CURRENT, approved_at))
            # Uma conferência em andamento para outra chave fica sem efeito (ver `_verify`).
            self._pending = None

    def _remember(self, key: _Key, result: dict[str, str | None]) -> dict[str, str | None]:
        """Guarda um resultado conclusivo para a chave e o devolve."""

        with self._lock:
            self._known = (key, result)
        return dict(result)

    def _start_check(self, key: _Key, snapshot: PlanSnapshot) -> None:
        """Dispara a conferência das fontes da chave, uma de cada vez e uma só por chave."""

        with self._lock:
            if self._pending == key:
                return
            self._pending = key
        threading.Thread(
            target=self._verify, args=(key, snapshot), name="editor-ui-approval-check", daemon=True
        ).start()

    def _verify(self, key: _Key, snapshot: PlanSnapshot) -> None:
        """Conferência completa pela mesma `verify_approval` da full; o resultado só vale se a chave ainda é a pendente.

        Falha de qualquer tipo resolve como em `_conclusive` (nunca "em dia" sem a prova) e não derruba a thread.
        """

        try:
            result = _result(CURRENT, verify_approval(self._root, snapshot.validation)["approved_at"])
        except Exception as error:
            result = _conclusive(error)
        with self._lock:
            if self._pending != key:
                return
            self._pending = None
            # Registro trocado durante a conferência: o resultado é do arquivo antigo e o próximo estado refaz.
            if stat_key(self._path) == key[0]:
                self._known = (key, result)
