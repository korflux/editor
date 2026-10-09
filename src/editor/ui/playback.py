"""Processos FFmpeg da interface: abertura, relé do stream, quadro de scrub e encerramento.

Responsabilidade: tudo o que o servidor faz com um FFmpeg depois que o núcleo ao vivo
(`editor.render.live`, T1) montou os argumentos. Aqui não há HTTP nem regra de montagem: o
chamador entrega os argumentos, uma função para escrever no cliente e o espaço (`ProcessSlots`) da
sessão em que o processo vive.

Garantias (spec, Contratos e Boundaries):
- sem shell e com argumentos estruturados (`spawn_ffmpeg`), sem janela de console no Windows;
- no máximo um processo por tipo e por sessão: um pedido novo do mesmo tipo encerra o anterior;
- a pressão do pipe limita o FFmpeg ao ritmo do cliente, e um cliente parado por mais de
  `STREAM_STALL_TIMEOUT_S` (a escrita no socket estoura o prazo) ou desconectado encerra o processo;
- falha antes do primeiro byte vira `PLAYBACK_FAILED` e `FRAME_FAILED`; depois do primeiro byte o
  chamador só encerra a conexão (o cabeçalho de sucesso já saiu).

Pontos de extensão: a T5 reaproveita `relay_stream` com o tipo `source` para a fonte inteira.

ponytail: o novo processo é criado antes de reivindicar o espaço, então por instantes uma sessão tem
dois do mesmo tipo (o anterior é morto logo na reivindicação), reservar o espaço antes de criar se o
teto de três por sessão precisar ser estrito a todo instante.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import subprocess
import threading

from .. import dependencies
from ..contracts import EXIT_CODES, CliError
from ..render import live
from .limits import (
    PROCESS_KINDS,
    STREAM_CHUNK_BYTES,
    STREAM_FIRST_BYTE_TIMEOUT_S,
)


def playback_failed() -> CliError:
    """Erro público de reprodução (F3), sem comando, stderr nem caminho na mensagem."""

    return CliError(
        code="PLAYBACK_FAILED",
        message="Não foi possível reproduzir este trecho.",
        exit_code=EXIT_CODES["OPERATION"],
    )


def frame_failed() -> CliError:
    """Erro público do quadro de scrub (F4), com a mesma mensagem do núcleo da T1."""

    return CliError(
        code="FRAME_FAILED",
        message="Não foi possível extrair este quadro.",
        exit_code=EXIT_CODES["OPERATION"],
    )


def kill_process(process: subprocess.Popen[bytes]) -> None:
    """Mata o processo sem esperar nem fechar o pipe, seguro de qualquer thread.

    O dono do processo (a thread que o leu) fecha e espera em `finalize_process`; fechar o pipe de
    outra thread enquanto ela lê não é seguro.
    """

    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def finalize_process(process: subprocess.Popen[bytes]) -> None:
    """Encerra o processo e libera o pipe: usado só pela thread dona, no fim do pedido."""

    kill_process(process)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    if process.stdout is not None:
        try:
            process.stdout.close()
        except OSError:
            pass


class ProcessSlots:
    """Processos FFmpeg em andamento de uma sessão: um por tipo de `PROCESS_KINDS`.

    Reivindicar um tipo ocupado encerra o processo anterior (suposição 13), o que cancela o scrub
    antigo quando um novo quadro é pedido e o stream antigo quando o player reinicia em outro T.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_kind: dict[str, subprocess.Popen[bytes]] = {}

    def claim(self, kind: str, process: subprocess.Popen[bytes]) -> None:
        """Registra o processo no tipo e mata o que estava lá."""

        if kind not in PROCESS_KINDS:
            raise ValueError(kind)
        with self._lock:
            previous = self._by_kind.get(kind)
            self._by_kind[kind] = process
        if previous is not None and previous is not process:
            kill_process(previous)

    def release(self, kind: str, process: subprocess.Popen[bytes]) -> None:
        """Libera o tipo, mas só se o processo ainda for o registrado (um mais novo pode tê-lo substituído)."""

        with self._lock:
            if self._by_kind.get(kind) is process:
                del self._by_kind[kind]

    def terminate_all(self) -> None:
        """Mata todos os processos da sessão (sessão descartada ou servidor encerrando)."""

        with self._lock:
            processes = list(self._by_kind.values())
            self._by_kind.clear()
        for process in processes:
            kill_process(process)


def spawn_ffmpeg(arguments: Sequence[str]) -> subprocess.Popen[bytes]:
    """Abre o FFmpeg local com stdout em pipe e sem shell, stdin nem stderr.

    `CREATE_NO_WINDOW` evita que cada FFmpeg abra um console quando o servidor roda sem console.
    O stderr é descartado: o `-loglevel error` do comando pode citar caminhos e nada dele sai pela API.
    """

    executable = dependencies.resolve_executable("ffmpeg")
    if executable is None:
        raise CliError(
            code="DEPENDENCY_MISSING",
            message="FFmpeg não está disponível localmente.",
            exit_code=EXIT_CODES["DEPENDENCY"],
        )
    return subprocess.Popen(
        [str(executable), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        creationflags=subprocess.CREATE_NO_WINDOW if dependencies.is_windows() else 0,
    )


def _first_chunk(process: subprocess.Popen[bytes], timeout_s: float) -> bytes:
    """Lê o primeiro trecho do stream sob um relógio; sem byte no prazo o FFmpeg é morto.

    Vazio significa que o processo terminou (falha ou superado por outro pedido) ou estourou o prazo.
    """

    assert process.stdout is not None
    timer = threading.Timer(timeout_s, kill_process, args=(process,))
    timer.daemon = True
    timer.start()
    try:
        return process.stdout.read1(STREAM_CHUNK_BYTES)
    except (OSError, ValueError):
        return b""
    finally:
        timer.cancel()


def relay_stream(
    slots: ProcessSlots,
    kind: str,
    process: subprocess.Popen[bytes],
    *,
    send_headers: Callable[[], None],
    write: Callable[[bytes], None],
    touch: Callable[[], None],
    first_byte_timeout_s: float = STREAM_FIRST_BYTE_TIMEOUT_S,
) -> None:
    """Repassa o stdout do FFmpeg ao cliente até o fim do stream, sem nunca deixar o processo para trás.

    `send_headers` só é chamada depois do primeiro byte: antes disso uma falha ainda pode virar a
    resposta `PLAYBACK_FAILED`. `write` levanta `OSError` quando o cliente fecha a conexão ou deixa de
    consumir por mais que o prazo do socket; o erro sobe depois da limpeza. `touch` marca atividade
    para o prazo de inatividade do servidor.
    """

    slots.claim(kind, process)
    try:
        first = _first_chunk(process, first_byte_timeout_s)
        if not first:
            raise playback_failed()
        send_headers()
        write(first)
        touch()
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read1(STREAM_CHUNK_BYTES)
            if not chunk:
                return
            write(chunk)
            touch()
    finally:
        slots.release(kind, process)
        finalize_process(process)


def capture_frame(slots: ProcessSlots, arguments: Sequence[str]) -> bytes:
    """Roda o FFmpeg do quadro de scrub e devolve o JPEG, com o espaço `frame` da sessão.

    Mesmos argumentos e mesma conferência do `live.extract_frame` da T1, mas com o processo sob
    controle da sessão: um scrub novo mata o anterior (durante o arraste só o último é atendido).
    ponytail: duplica as poucas linhas de `live.extract_frame` porque ele não expõe o processo, extrair
    um `run_frame(process_hook)` em `live.py` quando esse módulo puder ser tocado.
    """

    try:
        process = spawn_ffmpeg(arguments)
    except OSError as error:
        raise frame_failed() from error
    slots.claim("frame", process)
    try:
        try:
            output, _ = process.communicate(timeout=live.SCRUB_TIMEOUT_S)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise frame_failed() from error
        if process.returncode != 0 or not output.startswith(b"\xff\xd8"):
            raise frame_failed()
        return output
    finally:
        slots.release("frame", process)
        finalize_process(process)
