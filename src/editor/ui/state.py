"""Estado do trabalho que a interface exibe: plano validado, visão para a tela e vigia de arquivos.

Responsabilidade: manter a última versão válida do plano (e o erro seguro da versão atual, se ela
for inválida) e atualizá-la quando `plan.json` ou uma fonte declarada muda. A validação é a mesma
`load_validated_plan` da CLI, sem regra própria (F5): o que `plan validate` recusa a interface mostra
como erro, mantendo a versão válida anterior marcada como desatualizada.

A vigia (`PlanState.start`) faz uma sondagem barata de `lstat` a cada `STATE_POLL_INTERVAL_S` e só
revalida, o que custa FFprobe por fonte, quando a assinatura mudou e ficou igual por dois ciclos (o
`plan.json` escrito aos poucos pela IA não vira um erro passageiro). Mudança e validação somam no
máximo cerca de 1 s de atraso, fora o tempo do próprio FFprobe. A primeira carga também roda na vigia,
então `ui start` responde na hora e a tela vê o estado `loading` até o plano ser validado.

A visão (`build_view`) é o que `GET /api/state` entrega: só dados editoriais derivados da validação
(timeline, camadas, legendas, fontes com impressão e uso na montagem, ver `usage.py`). Caminho de fonte,
`plan_path`, JSON canônico e arquivo de fonte tipográfica nunca entram nela. O que muda sem o plano mudar
(proxy válido, fonte que sumiu do disco) é acrescentado a cada consulta pelo servidor.

ponytail: a resposta de estado leva a visão inteira a cada consulta (planos de mil segmentos pesam
centenas de KB), enviar só o hash e buscar a visão quando ele mudar se o uso real mostrar peso.
ponytail: a assinatura usa tamanho e `mtime_ns`, uma troca de arquivo que preserve os dois passa
despercebida; o SHA-256 da aprovação cobre o conteúdo na hora de aprovar.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import threading
from typing import Any

from ..contracts import EXIT_CODES, CliError
from ..integrity.approval import sources_fingerprint
from ..plan.validation import load_validated_plan
from ..render.live import output_frame_rate, source_frame_rate, source_unavailable
from ..render.proxy import lookup_proxy
from .limits import STATE_POLL_INTERVAL_S
from .usage import source_uses, used_count


@dataclass(frozen=True)
class PlanSnapshot:
    """Uma versão validada do plano, imutável por convenção (os pedidos a leem sem copiar).

    `document` e `validation` alimentam o grafo e a seleção no servidor e nunca saem pela API;
    `view` é a forma pública. `sources_fingerprint` é a impressão das fontes do carregamento, a que a
    aprovação (T6) recebe de volta para recusar se algo mudou.
    """

    document: dict[str, Any]
    validation: dict[str, Any]
    view: dict[str, Any]
    duration_s: float
    frame_rate: float
    plan_sha256: str
    sources_fingerprint: str


@dataclass(frozen=True)
class StateReading:
    """Leitura atômica do estado: última versão válida, erro da versão atual e se a primeira carga terminou."""

    snapshot: PlanSnapshot | None
    error: dict[str, str] | None
    loaded: bool

    @property
    def status(self) -> str:
        """Rótulo para a tela: `loading`, `ready`, `outdated` (válida antiga e atual inválida), `missing` ou `invalid`."""

        if not self.loaded:
            return "loading"
        if self.snapshot is not None:
            return "ready" if self.error is None else "outdated"
        return "missing" if self.error and self.error["code"] == "PLAN_REQUIRED" else "invalid"


def _error_dict(error: CliError) -> dict[str, str]:
    """Reduz o erro de domínio a código e mensagem, os únicos campos que a tela vê."""

    return {"code": error.code, "message": error.message}


def stat_key(path: str | Path) -> tuple[int, int] | None:
    """Tamanho e `mtime_ns` sem seguir links, ou `None` se o arquivo não existe (ou não pode ser lido)."""

    try:
        metadata = os.lstat(path)
    except OSError:
        return None
    return metadata.st_size, metadata.st_mtime_ns


def build_view(
    document: dict[str, Any], validation: dict[str, Any], fingerprint: str, frame_rate: float
) -> dict[str, Any]:
    """Monta a visão pública do plano validado: o que a timeline, o painel e o player precisam.

    Índices são de base 1, como em `plan validate` (`timeline.segments[].index`) e na cópia para a IA
    (`layers[]` e `captions.items`). Levanta `SOURCE_INVALID` se uma fonte sumiu entre a validação e agora.
    """

    uses = source_uses(document, validation)
    sources = []
    for record in validation["sources"]:
        try:
            metadata = os.stat(record["path"])
        except OSError as error:
            raise source_unavailable() from error
        source_list = uses.get(str(record["id"]), [])
        sources.append(
            {
                "id": record["id"],
                "kind": record["kind"],
                "duration_s": record["duration_seconds"],
                "size_bytes": metadata.st_size,
                "mtime_ns": metadata.st_mtime_ns,
                # Taxa da própria fonte (timecode do modo Fonte); a imagem não tem tempo nem taxa.
                "frame_rate": None if record["kind"] == "image" else source_frame_rate(record),
                "uses": source_list,
                "used_count": used_count(source_list),
            }
        )

    segments = []
    for item in validation["timeline"]["segments"]:
        declared = document["segments"][item["index"] - 1]
        video = declared["video"]
        segments.append(
            {
                "index": item["index"],
                "start_s": item["start_s"],
                "end_s": item["end_s"],
                "duration_s": item["duration_s"],
                "audio_start_s": item["audio_start_s"],
                "audio_end_s": item["audio_end_s"],
                "source_id": item["video_source_id"],
                "source_in_s": video["in_s"],
                "source_out_s": video["out_s"],
                "fit": item["fit"],
                "audio_mode": item["audio_mode"],
                "tracks": [
                    {
                        "source_id": track["source_id"],
                        "in_s": track["in_s"],
                        "out_s": track["out_s"],
                        "gain_db": track.get("gain_db", 0),
                        "loop": bool(track.get("loop", False)),
                    }
                    for track in declared["audio"]["tracks"]
                ],
            }
        )

    layers = []
    for index, layer in enumerate(document.get("layers") or [], start=1):
        entry: dict[str, Any] = {
            "index": index,
            "type": layer["type"],
            "start_s": layer["start_s"],
            "end_s": layer["end_s"],
        }
        if layer["type"] == "text":
            entry["text"] = layer["text"]
        else:
            entry["source_id"] = layer["source_id"]
            if layer["type"] == "video":
                entry["source_in_s"] = layer["in_s"]
                entry["source_out_s"] = layer["out_s"]
        layers.append(entry)

    captions = None
    mapped = validation.get("captions")
    if mapped is not None:
        items = document["captions"]["items"]
        captions = {
            "items": mapped["items"],
            "unplaced": list(mapped["unplaced"]),
            "occurrences": [
                {
                    "item": occurrence["item"],
                    "start_s": occurrence["start_s"],
                    "end_s": occurrence["end_s"],
                    "text": items[occurrence["item"] - 1]["text"],
                }
                for occurrence in mapped["occurrences"]
            ],
        }

    profile = validation["profile"]
    return {
        "plan_sha256": validation["plan_sha256"],
        "sources_fingerprint": fingerprint,
        "duration_s": validation["timeline"]["duration_seconds"],
        "frame_rate": frame_rate,
        "profile": {
            "aspect_ratio": profile["aspect_ratio"],
            "full_width": profile["full_width"],
            "full_height": profile["full_height"],
        },
        "sources": sources,
        "segments": segments,
        "layers": layers,
        "captions": captions,
    }


def proxy_source_ids(root: Path, validation: dict[str, Any]) -> set[str]:
    """IDs das fontes de vídeo com proxy válido agora (consultado a cada resposta: um `proxy build` não muda o plano).

    A tela mostra o selo "Proxy" quando a resolução não é `full` e há ao menos um proxy válido.
    """

    return {
        str(record["id"])
        for record in validation["sources"]
        if record["kind"] == "video"
        and lookup_proxy(root, str(record["id"]), Path(str(record["path"]))).state == "valid"
    }


def unavailable_source_ids(validation: dict[str, Any]) -> set[str]:
    """IDs das fontes do plano que não são mais arquivo regular legível agora (a linha "Indisponível" do modo Fonte).

    Consultado a cada resposta de estado: a fonte pode sumir sem o plano mudar (disco removido, arquivo
    movido), e a vigia só o percebe um ciclo depois, quando a revalidação falha.
    """

    missing: set[str] = set()
    for record in validation["sources"]:
        try:
            if not stat.S_ISREG(os.stat(record["path"]).st_mode):
                missing.add(str(record["id"]))
        except OSError:
            missing.add(str(record["id"]))
    return missing


class PlanState:
    """Estado vivo do plano do trabalho: leitura atômica e vigia que o mantém em dia."""

    def __init__(self, root: Path, *, poll_interval_s: float = STATE_POLL_INTERVAL_S) -> None:
        self._root = root
        self._plan_path = root / "plan.json"
        self._poll_interval_s = poll_interval_s
        self._lock = threading.Lock()
        self._reading = StateReading(snapshot=None, error=None, loaded=False)
        self._source_paths: tuple[str, ...] = ()
        self._validated: tuple[Any, Any] | None = None
        self._pending: tuple[Any, Any] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def read(self) -> StateReading:
        """Estado atual; nunca bloqueia na validação, que roda na vigia."""

        with self._lock:
            return self._reading

    def require_snapshot(self) -> PlanSnapshot:
        """Última versão válida, ou o erro 409 do estado atual (sem plano, inválido ou ainda carregando).

        Com plano desatualizado a última versão válida continua servindo o player (F5, passo 3).
        """

        reading = self.read()
        if reading.snapshot is not None:
            return reading.snapshot
        if reading.error is not None:
            raise CliError(
                code=reading.error["code"], message=reading.error["message"], exit_code=EXIT_CODES["STATE"]
            )
        raise CliError(
            code="PLAN_REQUIRED",
            message="O plano ainda está sendo carregado.",
            exit_code=EXIT_CODES["STATE"],
        )

    def start(self) -> None:
        """Inicia a vigia em segundo plano (thread daemon); a primeira carga acontece já no primeiro ciclo."""

        self._thread = threading.Thread(target=self._run, name="editor-ui-plan-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Pede o fim da vigia; a thread é daemon e termina no próximo ciclo."""

        self._stop.set()

    def _run(self) -> None:
        """Laço da vigia: um ciclo imediato (primeira carga) e depois um por intervalo até `stop`."""

        while True:
            self.refresh()
            if self._stop.wait(self._poll_interval_s):
                return

    def _signature(self) -> tuple[Any, Any]:
        """Assinatura barata do que o plano depende: `plan.json` e as fontes da última versão válida."""

        return stat_key(self._plan_path), tuple(stat_key(path) for path in self._source_paths)

    def refresh(self) -> None:
        """Um ciclo da vigia: revalida se a assinatura mudou e ficou estável por dois ciclos seguidos.

        A primeira carga não espera estabilidade. Depois dela, uma assinatura nova só vira validação
        no ciclo seguinte, se continuar a mesma (o arquivo parou de ser escrito).
        """

        signature = self._signature()
        if self._validated is not None:
            if signature == self._validated:
                self._pending = None
                return
            if signature != self._pending:
                self._pending = signature
                return
        self._reload(signature)

    def _validate(self) -> PlanSnapshot:
        """Valida o plano atual pela mesma função da CLI e monta o instantâneo com a visão pública."""

        if not os.path.lexists(self._plan_path):
            raise CliError(
                code="PLAN_REQUIRED",
                message="Nenhum plano neste trabalho. Peça à IA para montar o plano.",
                exit_code=EXIT_CODES["STATE"],
            )
        document, validation = load_validated_plan(self._root)
        fingerprint = sources_fingerprint(validation)
        frame_rate = output_frame_rate(document, validation)
        return PlanSnapshot(
            document=document,
            validation=validation,
            view=build_view(document, validation, fingerprint, frame_rate),
            duration_s=float(validation["timeline"]["duration_seconds"]),
            frame_rate=frame_rate,
            plan_sha256=str(validation["plan_sha256"]),
            sources_fingerprint=fingerprint,
        )

    def _reload(self, signature: tuple[Any, Any]) -> None:
        """Revalida e publica o resultado: versão nova, ou a anterior com o erro seguro da atual."""

        self._pending = None
        try:
            snapshot = self._validate()
        except CliError as error:
            previous = self.read().snapshot
            if (error.code == "PLAN_INVALID" and previous is not None
                    and self._validated is not None and signature[0] == self._validated[0]
                    and unavailable_source_ids(previous.validation)):
                # Só uma fonte da mesma versão válida sumiu: preserve o erro específico do modo Fonte.
                error = source_unavailable()
            failure = _error_dict(error)
        except Exception:
            # Falha inesperada nunca derruba a vigia nem vaza detalhe: a tela vê só o código genérico.
            failure = {"code": "INTERNAL_ERROR", "message": "Falha interna."}
        else:
            self._source_paths = tuple(str(record["path"]) for record in snapshot.validation["sources"])
            # O plano fica com a assinatura de antes da validação (se mudou durante ela, o próximo ciclo
            # revalida) e as fontes, da lista nova, medidas agora.
            self._validated = (signature[0], self._signature()[1])
            with self._lock:
                self._reading = StateReading(snapshot=snapshot, error=None, loaded=True)
            return
        self._validated = signature
        with self._lock:
            self._reading = StateReading(snapshot=self._reading.snapshot, error=failure, loaded=True)
