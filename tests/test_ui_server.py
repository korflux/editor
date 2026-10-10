"""Testes do servidor local da interface (T3 da phase 7): `ui start` e `ui stop`, sessão, allowlists e API.

Prova em quatro frentes, todas com HTTP e FFmpeg reais:
- processo real (`python -m editor ui ...`): `start` duas vezes, `stop` sem processo nem FFmpeg órfão,
  `stop` sem instância, registro obsoleto, falha de início e encerramento por inatividade (prazo reduzido
  pelo argumento interno do servidor);
- segurança: token, cookie, `Host`, `Origin`, teto de sessões (com duas instâncias no mesmo cliente de
  cookies), allowlists de rota, método, query e corpo, cabeçalhos e ausência do token em log e stderr;
- estado do plano: revalidação em até 2 s, plano inválido com a última versão válida mantida, impressão das fontes;
- API de mídia e seleção: o stream é o comando do núcleo da T1 e termina com o cliente, o scrub devolve JPEG
  e o texto "Copiar para IA" confere com `plan validate` (segmentos, J e L, camadas, legendas, timecode e segundos);
- modo Fonte (T5): uso de cada fonte conferido contra o plano e `source_intervals`, seleção na fonte (usado,
  descartado, parcial e posição na montagem) conferida contra `plan validate`, stream e quadro da fonte inteira
  (comando do núcleo, original em `full` e proxy nas frações, fonte só de áudio, termina com o cliente),
  `SOURCE_INVALID` para fonte ausente e as recusas da allowlist;
- aprovação (T6): `POST /api/approve` grava o `approval.json` v2 e a `render full` real o aceita, plano ou fonte
  alterados depois do carregamento são `APPROVAL_PLAN_CHANGED` sem gravar, as recusas de `Origin`, cookie e corpo,
  e a situação da aprovação no estado (nenhuma, em verificação, em dia e desatualizada) sem hashear a cada consulta.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import http.client
import http.cookiejar
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
import unittest
import urllib.error
import urllib.request
import warnings
from collections.abc import Callable, Iterator
from contextlib import redirect_stderr
from typing import Any
from unittest.mock import call, patch
from uuid import uuid4

from editor.contracts import CliError
from editor.integrity.approval import approve_workdir, verify_approval
from editor.plan.validation import load_validated_plan, validate_plan
from editor.render import live
from editor.render.proxy import build_proxies
from editor.render.windows import source_intervals
from editor.ui import approval as ui_approval
from editor.ui import instance as ui_instance
from editor.ui import server as ui_server
from editor.ui.limits import (
    CONTENT_SECURITY_POLICY,
    INSTANCE_HEADER,
    LIVE_FILTER_PREFIX,
    MAX_PROCESSES_PER_SESSION,
    PID_HEADER,
    PROCESS_KINDS,
    SESSION_LIMIT,
)
from editor.ui.playback import ProcessSlots
from editor.ui.selection import format_timecode
from editor.ui.sessions import SessionStore
from editor.ui.state import PlanState
from tests.support import remove_tree
from tests.test_decode_windows import create_gop_source, plan as base_plan, segment, track
from tests.test_live_stream import probe, run_ffmpeg, top_level_boxes
from tests.test_proxy_build import make_source


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS = sys.platform == "win32"
DEADLINE_S = 20.0


# --- Infraestrutura -------------------------------------------------------------------------------


@contextmanager
def ui_workdir() -> Iterator[Path]:
    """Trabalho descartável no workspace, com `sources/` e `.tmp`, removido ao final.

    Antes de apagar a pasta encerra qualquer servidor que o teste tenha deixado vivo pelo `ui start`
    (sem o registro em `.tmp` ele não poderia mais ser encontrado nem encerrado).
    """

    path = PROJECT_ROOT / f"tmp-ui-{uuid4().hex}"
    (path / "sources").mkdir(parents=True)
    (path / ".tmp").mkdir()
    try:
        yield path
    finally:
        try:
            ui_instance.stop_ui(path)
        except CliError:
            pass
        remove_tree(path)


def wait_until(predicate: Callable[[], Any], timeout_s: float = DEADLINE_S, message: str = "condição não atingida") -> Any:
    """Repete `predicate` até devolver algo verdadeiro; falha com `message` se o prazo passar."""

    deadline = time.monotonic() + timeout_s
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(message)
        time.sleep(0.05)


def process_alive(pid: int) -> bool:
    """Se o processo existe, sem sinalizá-lo (no Windows `os.kill` termina o processo, então usa a API do sistema)."""

    if WINDOWS:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # acesso negado: existe
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ffmpeg_pids_using(marker: str) -> list[int]:
    """PIDs de FFmpeg cuja linha de comando cita `marker` (o nome único do trabalho), para provar que não sobrou órfão."""

    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | ForEach-Object {{ $_.ProcessId }}"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return [int(line) for line in result.stdout.split() if line.strip().isdigit()]


def run_cli(*arguments: str, cwd: Path = PROJECT_ROOT) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    """Executa `python -m editor` e devolve o processo e o envelope JSON lido do stdout (o stdout é só ele)."""

    result = subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False, timeout=120,
    )
    stdout = result.stdout.strip()
    envelope = json.loads(stdout) if stdout else {}
    return result, envelope


class Response:
    """Resposta HTTP lida por inteiro: status, cabeçalhos em minúsculas e corpo."""

    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def json(self) -> dict[str, Any]:
        """Corpo lido como JSON."""

        return json.loads(self.body.decode("utf-8"))

    @property
    def code(self) -> str:
        """Código público do envelope de erro."""

        return self.json["error"]["code"]


class Client:
    """Cliente HTTP mínimo de um servidor da interface, com o cookie de sessão que o login entregou."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.cookie: str | None = None

    def request(
        self, method: str, path: str, *, headers: dict[str, str] | None = None, body: bytes | None = None,
        cookie: bool = True,
    ) -> Response:
        """Faz o pedido com o cookie da sessão (se houver) e os cabeçalhos dados, sem seguir redirecionamento."""

        merged = {"Cookie": self.cookie} if cookie and self.cookie else {}
        merged.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            connection.request(method, path, body=body, headers=merged)
            response = connection.getresponse()
            return Response(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
        finally:
            connection.close()

    def get(self, path: str, **kwargs: Any) -> Response:
        """GET com as mesmas opções de `request`."""

        return self.request("GET", path, **kwargs)

    def login(self, token: str, *, send_cookie: bool = True) -> Response:
        """Troca o token da URL pelo cookie de sessão, como o navegador ao abrir a URL do `ui start`.

        O navegador reenvia o cookie que já tem; `send_cookie=False` simula um navegador novo.
        """

        response = self.get(f"/?token={token}", cookie=send_cookie)
        set_cookie = response.headers.get("set-cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        return response


class Harness:
    """Servidor da interface em uma thread do teste, com o log capturado e clientes prontos."""

    def __init__(self, server: ui_server.UiServer, logs: list[str]) -> None:
        self.server = server
        self.logs = logs
        self.port = server.port
        self.token = server.token

    def client(self, *, logged_in: bool = True) -> Client:
        """Cliente novo, já com a sessão aberta pelo token (como uma aba que abriu a URL)."""

        client = Client(self.port)
        if logged_in:
            response = client.login(self.token)
            assert response.status == 302, response.body
        return client

    def wait_ready(self) -> None:
        """Espera a primeira validação do plano, que roda na vigia e não no pedido."""

        wait_until(lambda: self.server.plan_state.read().loaded, message="o plano não foi carregado")


@contextmanager
def serving(workdir: Path, *, ready: bool = True, **options: Any) -> Iterator[Harness]:
    """Sobe um `UiServer` em thread no trabalho dado; encerra e libera tudo ao sair (inclusive FFmpeg filhos)."""

    logs: list[str] = []
    options.setdefault("poll_interval_s", 0.05)
    server = ui_server.UiServer(workdir, log=logs.append, **options)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    harness = Harness(server, logs)
    try:
        if ready:
            harness.wait_ready()
        yield harness
    finally:
        server.close()
        thread.join(timeout=10)


def raw_request(port: int, payload: bytes) -> bytes:
    """Envia bytes crus ao servidor (para o que `http.client` não deixa fazer, como omitir o `Host`)."""

    with socket.create_connection(("127.0.0.1", port), timeout=30) as connection:
        connection.sendall(payload)
        chunks = []
        while True:
            try:
                data = connection.recv(65536)
            except ConnectionResetError:
                break
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


# --- Planos e fontes de teste ---------------------------------------------------------------------


TEXT_LAYER = {
    "type": "text", "start_s": 1.0, "end_s": 5.0, "x": 0.05, "y": 0.5, "width": 0.9, "height": 0.3,
    "text": "Primeira camada de texto com mais de sessenta caracteres para provar o corte exato",
    "font": "Arial", "size_px": 20, "color": "#FFFFFF", "align": "left", "wrap": True,
}
IMAGE_LAYER = {
    "type": "image", "source_id": "logo", "start_s": 3.0, "end_s": 7.0,
    "x": 0.7, "y": 0.05, "width": 0.25, "height": 0.25, "fit": "contain",
}
VIDEO_LAYER = {
    "type": "video", "source_id": "main", "in_s": 24, "out_s": 26, "start_s": 5.0, "end_s": 7.0,
    "x": 0.5, "y": 0.5, "width": 0.4, "height": 0.4, "fit": "contain",
}


def rich_plan() -> dict[str, Any]:
    """Plano com J e L, as três camadas e duas legendas, cujos tempos de timeline são conhecidos à mão.

    Segmentos na montagem: S1 em [0, 4] (fonte 2 a 6), S2 em [4, 6] (fonte 12 a 14) e S3 em [6, 8] (fonte
    22 a 24). O corte J de 0,3 s depois de S1 antecipa o áudio de S2 para 3,7 s; o corte L de 0,5 s depois
    de S2 o estende até 6,5 s. As legendas caem em 0,5 a 1,5 s (item 1) e 4,2 a 4,9 s (item 2).
    """

    document = base_plan(
        [segment(2, 6), segment(12, 14, [track(11.7, 14.5)]), segment(22, 24)],
        cuts=[
            {"after_segment": 1, "type": "j", "duration_s": 0.3},
            {"after_segment": 2, "type": "l", "duration_s": 0.5},
        ],
        layers=[TEXT_LAYER, IMAGE_LAYER, VIDEO_LAYER],
        sources=[
            {"id": "main", "kind": "video", "path": "sources/main.mp4"},
            {"id": "logo", "kind": "image", "path": "sources/logo.png"},
        ],
    )
    document["captions"] = {
        "style": {"font": "Arial", "size_px": 32, "color": "#FFFFFF"},
        "items": [
            {"source_id": "main", "in_s": 2.5, "out_s": 3.5, "text": "Primeira fala"},
            {"source_id": "main", "in_s": 12.2, "out_s": 12.9, "text": "Segunda\nfala com \"aspas\""},
        ],
    }
    return document


def simple_plan() -> dict[str, Any]:
    """Plano curto de dois segmentos (4 s e 3 s), o suficiente para estado, stream e scrub."""

    return base_plan([segment(1, 5), segment(7, 10)])


SOURCE_NAMES = ("main.mp4", "logo.png", "voice.wav")
"""Fontes que `source_plan` declara, na ordem de `UiTestCase.install(names=...)`."""

EXPECTED_MAIN_USES = [
    # (tipo, índice, fonte de, fonte até, montagem de, montagem até, loop), na ordem de `usage.source_uses`.
    ("segment", 4, 2.0, 4.0, 8.0, 10.0, False),
    ("segment", 1, 2.0, 6.0, 0.0, 4.0, False),
    ("audio", 1, 2.0, 6.0, 0.0, 4.0, False),
    ("audio", 4, 5.0, 6.0, 8.0, 10.0, True),
    ("audio", 2, 11.7, 14.5, 3.7, 6.5, False),
    ("segment", 2, 12.0, 14.0, 4.0, 6.0, False),
    ("segment", 3, 22.0, 24.0, 6.0, 8.0, False),
    ("layer", 3, 24.0, 26.0, 5.0, 7.0, False),
]
"""Usos de `main` em `source_plan`, calculados à mão a partir do docstring dele."""


def source_plan() -> dict[str, Any]:
    """Plano do modo Fonte, com J e L, camadas, uma trilha declarada além do que se ouve e uma em loop.

    Montagem: S1 em [0, 4] (main 2 a 6), S2 em [4, 6] (main 12 a 14, áudio 3,7 a 6,5 pelos cortes J de 0,3 s e L
    de 0,5 s), S3 em [6, 8] (main 22 a 24, com a trilha `voice` 0 a 10 sobre uma janela de 2 s) e S4 em [8, 10]
    (main 2 a 4, com a trilha `main` 5 a 6 em loop). A camada de vídeo mostra main 24 a 26 em [5, 7].
    """

    return base_plan(
        [
            segment(2, 6),
            segment(12, 14, [track(11.7, 14.5)]),
            segment(22, 24, [track(0, 10, source_id="voice")], mode="replace"),
            segment(2, 4, [track(5, 6, loop=True)], mode="replace"),
        ],
        cuts=[
            {"after_segment": 1, "type": "j", "duration_s": 0.3},
            {"after_segment": 2, "type": "l", "duration_s": 0.5},
        ],
        layers=[TEXT_LAYER, IMAGE_LAYER, VIDEO_LAYER],
        sources=[
            {"id": "main", "kind": "video", "path": "sources/main.mp4"},
            {"id": "logo", "kind": "image", "path": "sources/logo.png"},
            {"id": "voice", "kind": "audio", "path": "sources/voice.wav"},
        ],
    )


class UiTestCase(unittest.TestCase):
    """Fontes sintéticas compartilhadas: vídeo H.264/AAC de 30 s, logo PNG e um áudio WAV de 10 s."""

    cache: Path

    @classmethod
    def setUpClass(cls) -> None:
        """Gera as fontes sintéticas uma vez para a classe."""

        cls.cache = PROJECT_ROOT / f"tmp-ui-source-{uuid4().hex}"
        cls.cache.mkdir()
        create_gop_source(cls.cache / "main.mp4")
        run_ffmpeg(
            ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=1:duration=1",
             "-frames:v", "1", "-y", str(cls.cache / "logo.png")]
        )
        run_ffmpeg(
            ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=10:sample_rate=48000",
             "-ac", "2", "-c:a", "pcm_s16le", "-y", str(cls.cache / "voice.wav")]
        )

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove a pasta das fontes sintéticas."""

        remove_tree(cls.cache)

    def install(self, workdir: Path, document: dict[str, Any] | None, *, names: tuple[str, ...] = ("main.mp4", "logo.png")) -> None:
        """Copia as fontes pedidas para `sources/` e grava o plano (se houver)."""

        for name in names:
            shutil.copy2(self.cache / name, workdir / "sources" / name)
        if document is not None:
            self.write_plan(workdir, document)

    @staticmethod
    def write_plan(workdir: Path, document: dict[str, Any]) -> None:
        """Grava `plan.json` do trabalho."""

        (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")


class BigSourceTestCase(UiTestCase):
    """Acrescenta um vídeo 1280x720 de 20 s cujo stream é grande demais para caber nos buffers do socket."""

    @classmethod
    def setUpClass(cls) -> None:
        """Acrescenta o vídeo grande às fontes comuns da classe."""

        super().setUpClass()
        run_ffmpeg(
            ["-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=20",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
             "-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-pix_fmt", "yuv420p", "-c:a", "aac",
             "-y", str(cls.cache / "big.mp4")]
        )

    def big_plan(self) -> dict[str, Any]:
        """Plano de um segmento de 20 s sobre o vídeo grande (`sources/big.mp4`)."""

        return {**simple_plan(), "sources": [{"id": "main", "kind": "video", "path": "sources/big.mp4"}],
                "segments": [segment(0, 20)]}


# --- Processo real: ui start e ui stop ------------------------------------------------------------


class InstanceProcessTests(BigSourceTestCase):
    """`python -m editor ui start|stop` com o processo, o registro e os FFmpeg filhos reais."""

    def start(self, workdir: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        """`ui start --no-open` pela CLI real."""

        return run_cli("ui", "start", "--workdir", str(workdir), "--no-open", "--json")

    def stop(self, workdir: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        """`ui stop` pela CLI real."""

        return run_cli("ui", "stop", "--workdir", str(workdir), "--json")

    def test_start_twice_reuses_the_instance_and_stop_leaves_no_process_behind(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, self.big_plan(), names=("big.mp4",))
            first, envelope = self.start(workdir)
            self.assertEqual(first.returncode, 0, first.stderr)
            # O envelope é a única saída: uma linha no stdout e nada no stderr.
            self.assertEqual(first.stdout.count("\n"), 1)
            self.assertEqual(first.stderr, "")
            data = envelope["data"]
            self.assertEqual(envelope["command"], "ui start")
            self.assertEqual(data["state"], "started")
            self.assertRegex(data["url"], rf"^http://127\.0\.0\.1:{data['port']}/\?token=[A-Za-z0-9_-]{{43}}$")
            self.assertTrue(process_alive(data["pid"]))
            registry = workdir / ".tmp" / "ui-instance.json"
            self.assertTrue(registry.is_file() and not registry.is_symlink())

            second, again = self.start(workdir)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(again["data"], {**data, "state": "running"})

            # Um stream aberto mantém um FFmpeg filho; o servidor responde com o token da URL.
            client = Client(data["port"])
            token = data["url"].split("token=", 1)[1]
            self.assertEqual(client.login(token).status, 302)
            self.assertEqual(client.get("/api/state").status, 200)
            marker = workdir.name
            connection = socket.socket()
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            connection.settimeout(30)
            connection.connect(("127.0.0.1", data["port"]))
            connection.sendall(
                f"GET /api/stream?t=0&res=full HTTP/1.0\r\nHost: 127.0.0.1:{data['port']}\r\n"
                f"Cookie: {client.cookie}\r\n\r\n".encode()
            )
            try:
                self.assertIn(b"200", connection.recv(64))
                if WINDOWS:
                    self.assertTrue(wait_until(lambda: ffmpeg_pids_using(marker), message="FFmpeg do stream não apareceu"))
                scripts = list((workdir / ".tmp").glob(f"{LIVE_FILTER_PREFIX}*.txt"))
                self.assertEqual(len(scripts), 1, "o stream em andamento mantém o script do filtro em .tmp")
                (workdir / ".tmp" / "preview-em-uso.tmp").write_text("não é do render ao vivo", encoding="utf-8")

                stopped, envelope = self.stop(workdir)
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertEqual(envelope["data"], {"state": "stopped"})
            finally:
                connection.close()
            self.assertTrue(wait_until(lambda: not process_alive(data["pid"]), message="o servidor continua vivo"))
            self.assertFalse(registry.exists())
            # O encerramento à força não deixa o script do filtro, e o resto de `.tmp` não é tocado.
            self.assertEqual(list((workdir / ".tmp").glob(f"{LIVE_FILTER_PREFIX}*")), [])
            self.assertTrue((workdir / ".tmp" / "preview-em-uso.tmp").is_file())
            if WINDOWS:
                self.assertEqual(ffmpeg_pids_using(marker), [])
            with self.assertRaises((ConnectionError, OSError)):
                client.get("/api/state")

            again_stop, envelope = self.stop(workdir)
            self.assertEqual(again_stop.returncode, 0)
            self.assertEqual(envelope["data"], {"state": "not_running"})

    def test_start_without_a_plan_uses_the_current_folder_and_does_not_lock_it(self) -> None:
        with ui_workdir() as workdir:
            # Sem `--workdir` vale o diretório atual; sem `plan.json` a interface abre e mostra o estado "missing".
            started, envelope = run_cli("ui", "start", "--no-open", "--json", cwd=workdir)
            self.assertEqual(started.returncode, 0, started.stderr)
            data = envelope["data"]
            self.assertEqual(data["state"], "started")
            client = Client(data["port"])
            self.assertEqual(client.login(data["url"].split("token=", 1)[1]).status, 302)
            state = wait_until(
                lambda: (lambda reading: reading if reading["state"] != "loading" else None)(
                    client.get("/api/state").json["data"]
                ),
                message="o estado não saiu de loading",
            )
            self.assertEqual((state["state"], state["error"]["code"], state["workdir_name"]), ("missing", "PLAN_REQUIRED", workdir.name))
            # O servidor não usa o trabalho como diretório atual: no Windows renomear a pasta falha se algum processo a usa.
            renamed = workdir.with_name(workdir.name + "-renamed")
            os.rename(workdir, renamed)
            os.rename(renamed, workdir)
            stopped, envelope = run_cli("ui", "stop", "--json", cwd=workdir)
            self.assertEqual((stopped.returncode, envelope["data"]), (0, {"state": "stopped"}))

    def test_start_opens_the_browser_on_the_url_unless_told_not_to(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            # `start_ui` em processo cria o servidor de verdade; o `Popen` solto no retorno avisa que o filho segue vivo.
            with patch.object(ui_instance.webbrowser, "open") as opened, warnings.catch_warnings():
                warnings.simplefilter("ignore", ResourceWarning)
                started = ui_instance.start_ui(workdir)
                reused = ui_instance.start_ui(workdir)
                quiet = ui_instance.start_ui(workdir, open_browser=False)
            self.assertEqual((started["state"], reused["state"], quiet["state"]), ("started", "running", "running"))
            self.assertEqual({started["pid"], reused["pid"], quiet["pid"]}, {started["pid"]})
            self.assertEqual({started["url"], reused["url"], quiet["url"]}, {started["url"]})
            # Abre o navegador na URL com o token a cada `start` sem `--no-open`, também ao reaproveitar a instância.
            self.assertEqual(opened.call_args_list, [call(started["url"]), call(reused["url"])])
            self.assertEqual(ui_instance.stop_ui(workdir), {"state": "stopped"})

    def test_stop_without_instance_reports_not_running(self) -> None:
        with ui_workdir() as workdir:
            result, envelope = self.stop(workdir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(envelope["data"], {"state": "not_running"})
            # Um `stop` sem instância não cria registro.
            self.assertFalse((workdir / ".tmp" / "ui-instance.json").exists())

    def test_a_stale_registry_is_treated_as_absent_and_replaced(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            # Registro de processo que já morreu: porta sem ninguém escutando.
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            dead_port = listener.getsockname()[1]
            listener.close()
            stale = {"pid": 999999, "port": dead_port, "token": "x" * 43, "instance_id": "0" * 32}
            (workdir / ".tmp" / "ui-instance.json").write_text(json.dumps(stale), encoding="utf-8")
            stopped, envelope = self.stop(workdir)
            self.assertEqual(envelope["data"], {"state": "not_running"})
            self.assertFalse((workdir / ".tmp" / "ui-instance.json").exists())
            (workdir / ".tmp" / "ui-instance.json").write_text(json.dumps(stale), encoding="utf-8")
            started, envelope = self.start(workdir)
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(envelope["data"]["state"], "started")
            self.assertNotEqual(envelope["data"]["pid"], stale["pid"])
            self.assertEqual(ui_instance.read_instance(workdir).pid, envelope["data"]["pid"])

    def test_a_forged_registry_never_makes_stop_kill_another_process(self) -> None:
        with ui_workdir() as real, ui_workdir() as forged:
            self.install(real, simple_plan())
            started, envelope = self.start(real)
            self.assertEqual(started.returncode, 0, started.stderr)
            instance = ui_instance.read_instance(real)
            victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdout=subprocess.DEVNULL)
            self.addCleanup(self.reap_process, victim)
            # Registro de outro trabalho com a porta e a identidade de um servidor real, mas o pid de outro programa.
            tampered = {"pid": victim.pid, "port": instance.port, "token": instance.token, "instance_id": instance.instance_id}
            (forged / ".tmp" / "ui-instance.json").write_text(json.dumps(tampered), encoding="utf-8")
            result, answer = self.stop(forged)
            self.assertEqual((result.returncode, answer["data"]), (0, {"state": "not_running"}))
            self.assertIsNone(victim.poll(), "o stop encerrou um processo que o registro forjado apontava")
            self.assertTrue(ui_instance.probe(instance), "o stop encerrou o servidor de outro trabalho")
            self.assertFalse((forged / ".tmp" / "ui-instance.json").exists())
            # O registro verdadeiro continua funcionando.
            _, answer = self.stop(real)
            self.assertEqual(answer["data"], {"state": "stopped"})
            self.assertTrue(wait_until(lambda: not process_alive(envelope["data"]["pid"])))

    @unittest.skipUnless(WINDOWS, "junção é um conceito do Windows")
    def test_a_tmp_folder_replaced_by_a_junction_is_never_trusted_or_touched(self) -> None:
        with ui_workdir() as workdir, ui_workdir() as elsewhere:
            self.install(workdir, simple_plan())
            valid = {"pid": 1, "port": 1, "token": "a" * 43, "instance_id": "0" * 32}
            outside = elsewhere / ".tmp" / "ui-instance.json"
            outside.write_text(json.dumps(valid), encoding="utf-8")
            shutil.rmtree(workdir / ".tmp")
            junction = workdir / ".tmp"
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(junction), str(elsewhere / ".tmp")],
                check=True, capture_output=True,
            )
            try:
                self.assertIsNone(ui_instance.read_instance(workdir))
                started, envelope = self.start(workdir)
                self.assertEqual((started.returncode, envelope["error"]["code"]), (5, "WORKDIR_INVALID"))
                stopped, envelope = self.stop(workdir)
                self.assertEqual(envelope["data"], {"state": "not_running"})
                with self.assertRaises(CliError):
                    ui_instance.write_instance(workdir, ui_instance.Instance(2, 2, "b" * 43, "1" * 32))
                # Nada foi lido, apagado nem escrito pela junção.
                self.assertEqual(json.loads(outside.read_text(encoding="utf-8")), valid)
            finally:
                os.rmdir(junction)

    @staticmethod
    def reap_process(process: subprocess.Popen[bytes]) -> None:
        """Mata o processo de apoio do teste e recolhe o status."""

        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)

    def test_malformed_or_hostile_registries_are_ignored(self) -> None:
        with ui_workdir() as workdir:
            registry = workdir / ".tmp" / "ui-instance.json"
            valid = {"pid": 1, "port": 1, "token": "a" * 43, "instance_id": "0" * 32}
            for payload in (
                "isto não é json", "[]", json.dumps({**valid, "extra": 1}), json.dumps({**valid, "pid": True}),
                json.dumps({**valid, "port": 70000}), json.dumps({**valid, "token": "../etc"}),
                json.dumps({**valid, "instance_id": "XYZ"}), '{"pid": 1, "pid": 2}', "x" * 5000,
            ):
                with self.subTest(payload=payload[:30]):
                    registry.write_text(payload, encoding="utf-8")
                    self.assertIsNone(ui_instance.read_instance(workdir))
            registry.write_text(json.dumps(valid), encoding="utf-8")
            self.assertEqual(ui_instance.read_instance(workdir), ui_instance.Instance(1, 1, "a" * 43, "0" * 32))

    def test_start_failures_are_public_errors(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            exits_at_once = [sys.executable, "-c", "raise SystemExit(3)"]
            never_ready = [sys.executable, "-c", "import time; time.sleep(60)"]
            for name, command, timeout in (("morre", exits_at_once, 30.0), ("não responde", never_ready, 1.0)):
                with self.subTest(name):
                    def spawn(root: Path, command: list[str] = command) -> subprocess.Popen[bytes]:
                        """Substitui o servidor por um processo que morre ou nunca responde."""

                        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    with patch.object(ui_instance, "_spawn_server", spawn), patch.object(ui_instance, "START_TIMEOUT_S", timeout):
                        with self.assertRaises(CliError) as caught:
                            ui_instance.start_ui(workdir, open_browser=False)
                    self.assertEqual(caught.exception.code, "UI_START_FAILED")
                    self.assertEqual(caught.exception.exit_code, 6)
                    self.assertFalse((workdir / ".tmp" / "ui-instance.json").exists())

    def test_cli_argument_and_workdir_errors(self) -> None:
        with ui_workdir() as workdir:
            for arguments, code in (
                (("ui", "start", "--workdir", "relativo", "--json"), "WORKDIR_INVALID"),
                (("ui", "start", "--workdir", str(workdir / "nao-existe"), "--json"), "WORKDIR_INVALID"),
                (("ui", "stop", "--workdir", "relativo", "--json"), "WORKDIR_INVALID"),
                (("ui", "--json"), "ARGUMENT_INVALID"),
                (("ui", "start", "--workdir", str(workdir), "--abrir", "--json"), "ARGUMENT_INVALID"),
                (("ui", "restart", "--json"), "ARGUMENT_INVALID"),
            ):
                with self.subTest(arguments=arguments):
                    result, envelope = run_cli(*arguments)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(envelope["error"]["code"], code)
            self.assertFalse((workdir / ".tmp" / "ui-instance.json").exists())

    def test_the_server_stops_by_itself_after_the_idle_timeout_and_cleans_its_state(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            process = subprocess.Popen(
                [sys.executable, "-m", "editor.ui.server", "--workdir", str(workdir), "--idle-timeout-s", "1.5"],
                cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.addCleanup(lambda: process.kill() if process.poll() is None else None)
            registry = workdir / ".tmp" / "ui-instance.json"
            instance = wait_until(lambda: ui_instance.read_instance(workdir), message="o servidor não se registrou")
            self.assertTrue(ui_instance.probe(instance))
            client = Client(instance.port)
            self.assertEqual(client.login(instance.token).status, 302)
            # Sondagem sem sessão não é atividade: o prazo corre desde o último pedido autenticado.
            self.assertEqual(process.wait(timeout=30), 0)
            self.assertFalse(registry.exists())
            _, stderr = process.communicate()
            self.assertIn(b"inatividade", stderr)

    def test_the_idle_timeout_argument_can_only_be_shortened(self) -> None:
        with ui_workdir() as workdir:
            for value in ("0", "-1", "7201", "nan"):
                with self.subTest(value=value), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        ui_server.main(["--workdir", str(workdir), "--idle-timeout-s", value])


# --- Sessão e segurança ---------------------------------------------------------------------------


class SessionSecurityTests(UiTestCase):
    """Token, cookie, `Host`, `Origin`, teto de sessões, cabeçalhos e ausência do token no log."""

    def setUp(self) -> None:
        self.workdir_context = ui_workdir()
        self.workdir = self.workdir_context.__enter__()
        self.addCleanup(self.workdir_context.__exit__, None, None, None)
        self.install(self.workdir, simple_plan())

    def test_the_token_becomes_a_session_cookie_and_leaves_the_url(self) -> None:
        with serving(self.workdir) as harness:
            client = Client(harness.port)
            response = client.login(harness.token)
            self.assertEqual(response.status, 302)
            self.assertEqual(response.headers["location"], "/")
            cookie = response.headers["set-cookie"]
            self.assertIn(f"editor_ui_session_{harness.port}=", cookie)
            for attribute in ("HttpOnly", "SameSite=Strict", "Path=/"):
                self.assertIn(attribute, cookie)
            # O cookie é um identificador de sessão, não o token da URL.
            self.assertNotIn(harness.token, cookie)
            page = client.get("/")
            self.assertEqual(page.status, 200)
            self.assertIn("text/html", page.headers["content-type"])
            self.assertNotIn(harness.token.encode(), page.body)
            # O mesmo navegador reabrindo a URL não gasta uma segunda vaga de sessão.
            again = client.login(harness.token)
            self.assertEqual(again.status, 302)
            self.assertNotIn("set-cookie", again.headers)

    def test_every_route_without_a_valid_session_answers_token_invalid_without_work_data(self) -> None:
        with serving(self.workdir) as harness:
            anonymous = Client(harness.port)
            stranger = Client(harness.port)
            stranger.cookie = f"editor_ui_session_{harness.port}={'a' * 43}"
            for client in (anonymous, stranger):
                for method, path in (
                    ("GET", "/"), ("GET", "/static/app.js"), ("GET", "/api/state"), ("GET", "/api/stream?t=0&res=full"),
                    ("GET", "/api/frame?t=0&res=full"), ("GET", "/api/selection?start=0&end=1"),
                    ("GET", "/rota-que-nao-existe"), ("POST", "/api/state"), ("GET", "/api/state?x=1"),
                ):
                    with self.subTest(method=method, path=path):
                        extra = {"Origin": f"http://127.0.0.1:{harness.port}"} if method == "POST" else None
                        response = client.request(method, path, headers=extra)
                        self.assertEqual(response.status, 401)
                        if path.startswith("/api/"):
                            self.assertEqual(response.code, "UI_TOKEN_INVALID")
                            self.assertEqual(
                                response.json["error"]["message"], "Sessão inválida. Abra a interface de novo pela CLI."
                            )
                        else:
                            self.assertIn("text/html", response.headers["content-type"])
                            self.assertIn("Sessão inválida", response.body.decode("utf-8"))
                            self.assertEqual(response.headers["x-editor-error"], "UI_TOKEN_INVALID")
                        # Nada do trabalho (nome da pasta, plano, fonte) nem o token vaza na resposta.
                        for secret in (self.workdir.name, harness.token, "plan_sha256", "source_id", "duration_s"):
                            self.assertNotIn(secret.encode(), response.body)
            for bad in ("", "token", "abc", "a" * 43, "../x", "%00"):
                with self.subTest(token=bad):
                    response = Client(harness.port).get(f"/?token={bad}", cookie=False)
                    self.assertIn(response.status, (400, 401))
                    self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(Client(harness.port).get(f"/?token={'a' * 129}").status, 400)

    def test_the_invalid_session_page_is_self_contained_under_a_restrictive_csp(self) -> None:
        with serving(self.workdir) as harness:
            response = Client(harness.port).get("/")
            page = response.body.decode("utf-8")
            style = re.search(r"<style>(.*?)</style>", page, re.DOTALL)
            self.assertIsNotNone(style)
            normalized = style.group(1).replace("\r\n", "\n").encode("utf-8")
            digest = base64.b64encode(hashlib.sha256(normalized).digest()).decode()
            policy = response.headers["content-security-policy"]
            self.assertTrue(policy.startswith(CONTENT_SECURITY_POLICY))
            self.assertIn(f"style-src 'sha256-{digest}'", policy)
            self.assertNotIn("unsafe-inline", policy)
            self.assertNotIn("<script", page)
            self.assertNotIn("href=", page)
            self.assertIn("Abra a interface de novo pela CLI: editor ui start.", page)

    def test_host_must_be_exactly_the_loopback_address_and_port(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            for host in ("evil.test", f"localhost:{harness.port}", f"127.0.0.1:{harness.port + 1}", "127.0.0.1", ""):
                with self.subTest(host=host):
                    for path in ("/api/state", "/", f"/?token={harness.token}"):
                        response = client.get(path, headers={"Host": host}, cookie=path == "/api/state")
                        self.assertEqual(response.status, 403)
                        self.assertEqual(response.code, "UI_HOST_INVALID")
                        self.assertNotIn("set-cookie", response.headers)
            # Sem o cabeçalho `Host` (HTTP/1.0 permite) e com `Host` repetido.
            absent = raw_request(harness.port, b"GET /api/state HTTP/1.0\r\n\r\n")
            self.assertIn(b"403", absent.split(b"\r\n", 1)[0])
            self.assertIn(b"UI_HOST_INVALID", absent)
            twice = raw_request(
                harness.port,
                f"GET /api/state HTTP/1.0\r\nHost: 127.0.0.1:{harness.port}\r\nHost: evil.test\r\n\r\n".encode(),
            )
            self.assertIn(b"UI_HOST_INVALID", twice)
            self.assertEqual(client.get("/api/state").status, 200)

    def test_origin_is_checked_and_mandatory_on_post(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            own = f"http://127.0.0.1:{harness.port}"
            for origin in ("http://evil.test", "null", f"http://127.0.0.1:{harness.port + 1}", f"https://127.0.0.1:{harness.port}", own + "/"):
                with self.subTest(origin=origin):
                    for method in ("GET", "POST"):
                        response = client.request(method, "/api/state", headers={"Origin": origin})
                        self.assertEqual((response.status, response.code), (403, "UI_ORIGIN_INVALID"))
            response = client.request("POST", "/api/state")
            self.assertEqual((response.status, response.code), (403, "UI_ORIGIN_INVALID"))
            # Origem própria passa a checagem; o POST então esbarra na allowlist de método (a rota só aceita GET).
            posted = client.request("POST", "/api/state", headers={"Origin": own})
            self.assertEqual((posted.status, posted.code), (405, "ARGUMENT_INVALID"))
            self.assertEqual(posted.headers["allow"], "GET")
            self.assertEqual(client.get("/api/state", headers={"Origin": own}).status, 200)
            # O login por token também recusa uma origem estranha, sem entregar cookie.
            foreign = Client(harness.port).get(f"/?token={harness.token}", headers={"Origin": "http://evil.test"}, cookie=False)
            self.assertEqual((foreign.status, foreign.code), (403, "UI_ORIGIN_INVALID"))
            self.assertNotIn("set-cookie", foreign.headers)

    def test_session_limit_discards_the_oldest_idle_session_and_refuses_when_all_are_active(self) -> None:
        now = [1000.0]
        with serving(self.workdir, clock=lambda: now[0], session_active_s=120.0) as harness:
            first, second = harness.client(), harness.client()
            self.assertEqual(SESSION_LIMIT, 2)
            # As duas vagas estão ativas: a terceira recebe UI_SESSION_LIMIT, sem cookie.
            third = Client(harness.port).login(harness.token)
            self.assertEqual((third.status, third.json["error"]["code"]), (429, "UI_SESSION_LIMIT"))
            self.assertNotIn("set-cookie", third.headers)
            self.assertEqual(first.get("/api/state").status, 200)
            self.assertEqual(second.get("/api/state").status, 200)
            # A primeira fica ociosa (mais de 120 s); a segunda continua ativa.
            now[0] += 100
            self.assertEqual(second.get("/api/state").status, 200)
            now[0] += 100
            self.assertEqual(second.get("/api/state").status, 200)
            newcomer = harness.client()
            self.assertEqual(newcomer.get("/api/state").status, 200)
            # A ociosa mais antiga (a primeira) perdeu a sessão; as outras duas seguem válidas.
            dropped = first.get("/api/state")
            self.assertEqual((dropped.status, dropped.code), (401, "UI_TOKEN_INVALID"))
            self.assertEqual(second.get("/api/state").status, 200)
            self.assertEqual(newcomer.get("/api/state").status, 200)
            # Com as duas ativas de novo, outra aba nova é recusada.
            again = Client(harness.port).login(harness.token)
            self.assertEqual(again.status, 429)

    def test_two_instances_in_one_cookie_jar_keep_both_sessions_and_each_applies_its_own_limit(self) -> None:
        now = [500.0]
        with ui_workdir() as other:
            self.install(other, simple_plan())
            with serving(self.workdir, clock=lambda: now[0]) as one, serving(other, clock=lambda: now[0]) as two:
                jar = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

                def fetch(harness: Harness, path: str) -> int:
                    """Pedido pelo cliente com cookies; devolve o status (o `urllib` levanta nos 4xx)."""

                    try:
                        with opener.open(f"http://127.0.0.1:{harness.port}{path}", timeout=60) as response:
                            return response.status
                    except urllib.error.HTTPError as error:
                        return error.code

                # O login segue o redirect para `/` sem o token; cada instância grava o cookie com a porta no nome.
                for harness in (one, two):
                    self.assertEqual(fetch(harness, f"/?token={harness.token}"), 200)
                self.assertEqual(
                    {cookie.name for cookie in jar},
                    {f"editor_ui_session_{one.port}", f"editor_ui_session_{two.port}"},
                )
                # O cliente envia os dois cookies a cada servidor, e cada um lê só o seu: as duas sessões valem.
                for harness in (one, two, one, two):
                    self.assertEqual(fetch(harness, "/api/state"), 200)
                # Uma terceira sessão na instância `one` (a do cliente com cookies é a primeira): duas ativas, vaga cheia.
                second_tab = one.client()
                self.assertEqual(second_tab.get("/api/state").status, 200)
                self.assertEqual(Client(one.port).login(one.token).status, 429)
                # A sessão do cliente com cookies fica ociosa em `one`; a de `two` segue ativa em `two`.
                now[0] += 200
                self.assertEqual(second_tab.get("/api/state").status, 200)
                self.assertEqual(fetch(two, "/api/state"), 200)
                third_tab = one.client()
                self.assertEqual(third_tab.get("/api/state").status, 200)
                # A ociosa mais antiga de `one` (a do cliente com cookies) perdeu a sessão; a de `two` não foi afetada.
                self.assertEqual(fetch(one, "/api/state"), 401)
                self.assertEqual(fetch(two, "/api/state"), 200)
                self.assertEqual(second_tab.get("/api/state").status, 200)
                self.assertEqual(third_tab.get("/api/state").status, 200)
                # E só com as duas ativas uma nova recebe UI_SESSION_LIMIT.
                self.assertEqual(Client(one.port).login(one.token).status, 429)

    def test_session_store_rules_without_http(self) -> None:
        now = [0.0]
        store = SessionStore(limit=2, active_s=10.0, clock=lambda: now[0])
        first, second = store.open(), store.open()
        self.assertNotEqual(first.identifier, second.identifier)
        self.assertGreaterEqual(len(first.identifier), 43)
        with self.assertRaises(CliError) as caught:
            store.open()
        self.assertEqual(caught.exception.code, "UI_SESSION_LIMIT")
        self.assertIs(store.authenticate(first.identifier), first)
        now[0] = 11.0  # `second` ficou ocioso; `first` acabou de pedir
        store.authenticate(first.identifier)
        third = store.open()
        for invalid in (None, "", second.identifier, "\u00e9" * 43):
            with self.assertRaises(CliError) as rejected:
                store.authenticate(invalid)
            self.assertEqual(rejected.exception.code, "UI_TOKEN_INVALID")
        self.assertIs(store.authenticate(third.identifier), third)

    @staticmethod
    def reap(processes: list[subprocess.Popen[bytes]]) -> None:
        """Mata o que sobrou e recolhe o status de todos, para o teste não deixar processo nem aviso para trás."""

        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)

    def test_process_slots_keep_one_process_per_kind_and_discarding_a_session_kills_its_processes(self) -> None:
        def sleeper() -> subprocess.Popen[bytes]:
            """Processo que só dorme, para o teste observar quem o servidor encerra."""

            process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL)
            spawned.append(process)
            return process

        spawned: list[subprocess.Popen[bytes]] = []
        self.addCleanup(self.reap, spawned)
        self.assertEqual(MAX_PROCESSES_PER_SESSION, 3)
        self.assertEqual(PROCESS_KINDS, ("stream", "source", "frame"))
        slots = ProcessSlots()
        older, newer, other_kind = sleeper(), sleeper(), sleeper()
        slots.claim("stream", older)
        slots.claim("frame", other_kind)
        slots.claim("stream", newer)
        wait_until(lambda: older.poll() is not None, message="o stream anterior não foi encerrado")
        self.assertIsNone(newer.poll())
        self.assertIsNone(other_kind.poll())
        slots.release("stream", older)  # o antigo já foi substituído: não derruba o registro do novo
        slots.terminate_all()
        wait_until(lambda: newer.poll() is not None and other_kind.poll() is not None)
        with self.assertRaises(ValueError):
            slots.claim("desconhecido", sleeper())

        # Sessão descartada pelo teto, e sessões encerradas com o servidor: os FFmpeg delas morrem junto.
        now = [0.0]
        store = SessionStore(limit=1, active_s=10.0, clock=lambda: now[0])
        discarded = store.open()
        in_discarded = sleeper()
        discarded.slots.claim("stream", in_discarded)
        now[0] = 11.0
        survivor = store.open()
        wait_until(lambda: in_discarded.poll() is not None, message="o processo da sessão descartada sobrou")
        in_survivor = sleeper()
        survivor.slots.claim("stream", in_survivor)
        store.close_all()
        wait_until(lambda: in_survivor.poll() is not None, message="o processo da sessão ativa sobrou")

    def test_security_headers_are_present_on_every_kind_of_response(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            anonymous = Client(harness.port)
            responses = {
                "index": client.get("/"),
                "static": client.get("/static/app.css"),
                "estado": client.get("/api/state"),
                "selecao": client.get("/api/selection?start=0&end=1"),
                "quadro": client.get("/api/frame?t=0&res=1/4"),
                "stream": client.get("/api/stream?t=6.5&res=1/4"),
                "login": Client(harness.port).login(harness.token),
                "erro 400": client.get("/api/state?x=1"),
                "erro 404": client.get("/api/nada"),
                "erro 405": client.request("PUT", "/api/state"),
                "erro 401": anonymous.get("/api/state"),
                "pagina 401": anonymous.get("/"),
                "erro 403": client.get("/api/state", headers={"Host": "x"}),
            }
            self.assertEqual(responses["erro 405"].status, 405)
            for name, response in responses.items():
                with self.subTest(name):
                    policy = response.headers["content-security-policy"]
                    self.assertTrue(policy.startswith(CONTENT_SECURITY_POLICY), policy)
                    self.assertIn("frame-ancestors 'none'", policy)
                    self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                    self.assertEqual(response.headers["referrer-policy"], "no-referrer")
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertEqual(response.headers[INSTANCE_HEADER.lower()], harness.server.instance_id)
                    self.assertEqual(response.headers[PID_HEADER.lower()], str(os.getpid()))
                    # Nada de versão do Python nem de servidor genérico.
                    self.assertEqual(response.headers["server"], "editor-ui")
            for name in ("index", "static", "estado", "selecao", "quadro", "stream", "login", "erro 400", "erro 404", "erro 405", "erro 401", "erro 403"):
                self.assertEqual(responses[name].headers["content-security-policy"], CONTENT_SECURITY_POLICY, name)
            self.assertEqual(
                CONTENT_SECURITY_POLICY,
                "default-src 'self'; media-src 'self' blob:; img-src 'self' blob: data:; frame-ancestors 'none'",
            )

    def test_routes_methods_query_and_body_outside_the_allowlist_are_refused(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            self.assertEqual(client.get("/").status, 200)
            refused = {
                "rota inexistente": ("GET", "/api/nada", None, 404),
                "barra final": ("GET", "/api/state/", None, 404),
                "caminho com ..": ("GET", "/static/../../plan.json", None, 404),
                "estático fora da pasta": ("GET", "/static/%2e%2e%2fplan.json", None, 404),
                "estático inexistente": ("GET", "/static/segredo.js", None, 404),
                "estático com extensão fora": ("GET", "/static/app.py", None, 404),
                "arquivo do trabalho": ("GET", "/plan.json", None, 404),
                "forma absoluta": ("GET", "http://127.0.0.1/api/state", None, 400),
                "PUT": ("PUT", "/api/state", None, 405),
                "DELETE": ("DELETE", "/api/state", None, 405),
                "OPTIONS": ("OPTIONS", "/api/state", None, 405),
                "HEAD": ("HEAD", "/api/state", None, 405),
                "PATCH": ("PATCH", "/", None, 405),
                "POST em rota GET": ("POST", "/api/stream?t=0&res=full", None, 405),
                "query inesperada no estado": ("GET", "/api/state?debug=1", None, 400),
                "query inesperada no index": ("GET", "/?debug=1", None, 400),
                "query no estático": ("GET", "/static/app.js?v=1", None, 400),
                "token na API": ("GET", "/api/state?token=x", None, 400),
                "falta res": ("GET", "/api/stream?t=0", None, 400),
                "falta t": ("GET", "/api/frame?res=full", None, 400),
                "campo a mais": ("GET", "/api/stream?t=0&res=full&x=1", None, 400),
                "campo repetido": ("GET", "/api/stream?t=0&t=1&res=full", None, 400),
                "valor vazio": ("GET", "/api/stream?t=&res=full", None, 400),
                "campo sem valor": ("GET", "/api/stream?t&res=full", None, 400),
                "resolução inválida": ("GET", "/api/stream?t=0&res=1/5", None, 400),
                "resolução com caminho": ("GET", "/api/stream?t=0&res=../full", None, 400),
                "t negativo": ("GET", "/api/stream?t=-1&res=full", None, 400),
                "t com expoente": ("GET", "/api/stream?t=1e2&res=full", None, 400),
                "t NaN": ("GET", "/api/frame?t=nan&res=full", None, 400),
                "t infinito": ("GET", "/api/frame?t=inf&res=full", None, 400),
                "t com 33 caracteres": ("GET", f"/api/frame?t={'1' * 33}&res=full", None, 400),
                "t depois do fim": ("GET", "/api/stream?t=7.5&res=full", None, 400),
                "t decimal demais": ("GET", "/api/frame?t=1.1234567890123&res=full", None, 400),
                "percent inválido": ("GET", "/api/stream?t=%ff&res=full", None, 400),
                "seleção invertida": ("GET", "/api/selection?start=3&end=2", None, 400),
                "seleção vazia": ("GET", "/api/selection?start=2&end=2", None, 400),
                "seleção depois do fim": ("GET", "/api/selection?start=2&end=7.5", None, 400),
                "seleção sem fim": ("GET", "/api/selection?start=2", None, 400),
                "corpo em GET": ("GET", "/api/state", b'{"a":1}', 400),
                "corpo grande em GET": ("GET", "/api/state", b"x" * 4096, 400),
            }
            for name, (method, path, body, status) in refused.items():
                with self.subTest(name):
                    extra = {"Origin": f"http://127.0.0.1:{harness.port}"} if method == "POST" else None
                    response = client.request(method, path, body=body, headers=extra)
                    self.assertEqual(response.status, status, response.body)
                    if method != "HEAD":
                        self.assertEqual(response.code, "ARGUMENT_INVALID")
            long_query = "/api/stream?t=0&res=full&x=" + "a" * 2100
            self.assertEqual(client.get(long_query).status, 400)
            chunked = raw_request(
                harness.port,
                (
                    f"POST /api/state HTTP/1.0\r\nHost: 127.0.0.1:{harness.port}\r\nCookie: {client.cookie}\r\n"
                    f"Origin: http://127.0.0.1:{harness.port}\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
                ).encode(),
            )
            self.assertIn(b" 405 ", chunked.split(b"\r\n", 1)[0] + b" ")
            # Nenhuma das recusas derrubou a sessão nem o servidor.
            self.assertEqual(client.get("/api/state").status, 200)

    def test_a_json_body_is_accepted_only_with_the_exact_fields_hex_values_and_at_most_one_kib(self) -> None:
        digest_a, digest_b = "a" * 64, "0123456789abcdef" * 4
        route = ui_server.Route("/api/echo", ("POST",), "_serve_echo", body_fields=("plan_sha256", "sources_fingerprint"))

        def serve_echo(handler: ui_server.UiRequestHandler, route: ui_server.Route, raw_query: str) -> None:
            """Rota de teste que devolve o corpo já validado por `read_json_body`."""

            ui_server.parse_query(raw_query, route.query)
            handler._send_json(200, {"ok": True, "data": handler.read_json_body(route)})

        with patch.object(ui_server, "ROUTES", ui_server.ROUTES + (route,)), \
                patch.object(ui_server.UiRequestHandler, "_serve_echo", serve_echo, create=True), \
                serving(self.workdir) as harness:
            client = harness.client()
            origin = {"Origin": f"http://127.0.0.1:{harness.port}"}

            def post(payload: Any, headers: dict[str, str] | None = None) -> Response:
                """POST na rota de teste, com a origem própria e o corpo (bytes ou JSON) dado."""

                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                return client.request("POST", "/api/echo", body=body, headers={**origin, **(headers or {})})

            good = {"plan_sha256": digest_a, "sources_fingerprint": digest_b}
            accepted = post(good)
            self.assertEqual((accepted.status, accepted.json["data"]), (200, good))
            for name, payload in {
                "campo a mais": {**good, "extra": digest_a},
                "campo a menos": {"plan_sha256": digest_a},
                "outro campo": {"plan_sha256": digest_a, "outro": digest_b},
                "hash curto": {**good, "plan_sha256": "a" * 63},
                "hash com maiúscula": {**good, "plan_sha256": "A" * 64},
                "hash não hexadecimal": {**good, "plan_sha256": "g" * 64},
                "valor numérico": {**good, "plan_sha256": 7},
                "valor nulo": {**good, "sources_fingerprint": None},
                "lista": [good],
                "texto": "x",
                "JSON inválido": b"{nao e json",
                "chave duplicada": b'{"plan_sha256":"%s","plan_sha256":"%s","sources_fingerprint":"%s"}' % ((digest_a.encode(),) * 3),
                "acima de 1 KiB": {**good, "folga": "x" * 1100},
                "vazio": b"",
            }.items():
                with self.subTest(name):
                    response = post(payload)
                    self.assertEqual((response.status, response.code), (400, "ARGUMENT_INVALID"))
            without_origin = client.request("POST", "/api/echo", body=json.dumps(good).encode())
            self.assertEqual((without_origin.status, without_origin.code), (403, "UI_ORIGIN_INVALID"))
            self.assertEqual(client.get("/api/echo").status, 405)

    def test_the_log_and_stderr_never_contain_the_token_or_the_query(self) -> None:
        captured = io.StringIO()
        with redirect_stderr(captured), serving(self.workdir) as harness:
            client = harness.client()
            client.get("/api/frame?t=1.5&res=1/4")
            Client(harness.port).get(f"/?token={harness.token}x")
            Client(harness.port).get(f"/?token={harness.token}&token={harness.token}")
            Client(harness.port).get(f"/{harness.token}")
            client.get(f"/api/state?token={harness.token}")
            client.get(f"/static/{harness.token}.js")
            raw_request(harness.port, f"GET /?token={harness.token} HTTP/9.9\r\nHost: x\r\n\r\n".encode())
            raw_request(harness.port, f"BREW /?token={harness.token} HTTP/1.0\r\n\r\n".encode())
            raw_request(harness.port, b"\x16\x03\x01\x02\x00" + harness.token.encode() + b"\r\n\r\n")
            wait_until(lambda: len(harness.logs) >= 8)
            cookie_value = client.cookie.split("=", 1)[1]
            everything = "\n".join(harness.logs) + captured.getvalue()
        self.assertGreaterEqual(len(harness.logs), 8)
        for secret in (harness.token, cookie_value, "token=", "t=1.5", str(self.workdir)):
            self.assertNotIn(secret, everything)
        self.assertIn("GET /api/frame 200", everything)

    def test_the_real_server_process_does_not_write_the_token_to_stdout_or_stderr(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-m", "editor.ui.server", "--workdir", str(self.workdir)],
            cwd=PROJECT_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        instance = wait_until(lambda: ui_instance.read_instance(self.workdir), message="o servidor não se registrou")
        client = Client(instance.port)
        self.assertEqual(client.login(instance.token).status, 302)
        wait_until(lambda: client.get("/api/state").json["data"]["state"] == "ready", message="o plano não foi carregado")
        self.assertEqual(client.get("/api/frame?t=2&res=1/4").status, 200)
        Client(instance.port).get(f"/?token={instance.token}x")
        raw_request(instance.port, f"GET /?token={instance.token} HTTP/9.9\r\n\r\n".encode())
        client.get("/api/state?x=" + instance.token)
        ui_instance.terminate_tree(instance.pid)
        process.wait(timeout=30)
        stdout, stderr = process.communicate()
        for output in (stdout, stderr):
            self.assertNotIn(instance.token.encode(), output)
        self.assertIn(b"ui: escutando em 127.0.0.1", stderr)
        self.assertIn(b"GET /api/frame 200", stderr)

    def test_static_files_are_packaged_and_served_only_from_the_static_folder(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            types = {"app.css": "text/css", "app.js": "text/javascript", "index.html": "text/html"}
            for name, kind in types.items():
                with self.subTest(name):
                    response = client.get(f"/static/{name}")
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.headers["content-type"].startswith(kind))
                    self.assertEqual(response.body, (PROJECT_ROOT / "src" / "editor" / "ui" / "static" / name).read_bytes())
            # A página da interface referencia só estáticos do próprio servidor, sem script em linha.
            page = client.get("/").body.decode("utf-8")
            self.assertIn('src="/static/app.js"', page)
            self.assertIn('href="/static/app.css"', page)
            self.assertNotRegex(page, r"<script(?![^>]*\bsrc=)")
            self.assertNotRegex(page, r"https?://(?!127\.0\.0\.1)")

    def test_the_package_declares_the_static_files_as_package_data(self) -> None:
        pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(pyproject["tool"]["setuptools"]["package-data"]["editor.ui"], ["static/*"])
        static = PROJECT_ROOT / "src" / "editor" / "ui" / "static"
        self.assertLessEqual(
            {"app.css", "app.js", "index.html", "invalid.html"}, {entry.name for entry in static.iterdir()}
        )


# --- Estado do plano ------------------------------------------------------------------------------


class PlanStateTests(UiTestCase):
    """Revalidação por arquivo alterado, plano inválido com a última versão válida e impressão das fontes."""

    def state(self, client: Client) -> dict[str, Any]:
        """`data` de `GET /api/state` da sessão dada."""

        response = client.get("/api/state")
        self.assertEqual(response.status, 200, response.body)
        return response.json["data"]

    def test_state_exposes_the_validated_plan_without_paths_or_secrets(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, rich_plan())
            validation = validate_plan(workdir)
            with serving(workdir) as harness:
                data = self.state(harness.client())
                raw = harness.client().get("/api/state").body.decode("utf-8")
            self.assertEqual(data["state"], "ready")
            self.assertIsNone(data["error"])
            self.assertEqual(data["workdir_name"], workdir.name)
            self.assertEqual(data["resolutions"], ["full", "1/2", "1/3", "1/4"])
            plan = data["plan"]
            self.assertEqual(plan["plan_sha256"], validation["plan_sha256"])
            self.assertRegex(plan["sources_fingerprint"], r"^[0-9a-f]{64}$")
            self.assertEqual(plan["duration_s"], validation["timeline"]["duration_seconds"])
            self.assertEqual(plan["frame_rate"], 25.0)
            self.assertEqual(
                plan["profile"],
                {key: validation["profile"][key] for key in ("aspect_ratio", "full_width", "full_height")},
            )
            # Segmentos: timeline de `plan validate` mais a fonte lida do plano.
            self.assertEqual(len(plan["segments"]), validation["segment_count"])
            for shown, expected in zip(plan["segments"], validation["timeline"]["segments"], strict=True):
                for key in ("index", "start_s", "end_s", "duration_s", "audio_start_s", "audio_end_s", "fit", "audio_mode"):
                    self.assertEqual(shown[key], expected[key], key)
                self.assertEqual(shown["source_id"], expected["video_source_id"])
            self.assertEqual(
                [(item["source_in_s"], item["source_out_s"]) for item in plan["segments"]],
                [(2, 6), (12, 14), (22, 24)],
            )
            self.assertEqual(plan["segments"][1]["tracks"], [{"source_id": "main", "in_s": 11.7, "out_s": 14.5, "gain_db": 0, "loop": False}])
            self.assertEqual([(item["index"], item["type"]) for item in plan["layers"]], [(1, "text"), (2, "image"), (3, "video")])
            self.assertEqual(plan["layers"][2]["source_in_s"], 24)
            self.assertEqual(
                plan["captions"],
                {
                    "items": 2, "unplaced": [],
                    "occurrences": [
                        {"item": 1, "start_s": 0.5, "end_s": 1.5, "text": "Primeira fala"},
                        {"item": 2, "start_s": 4.2, "end_s": 4.9, "text": "Segunda\nfala com \"aspas\""},
                    ],
                },
            )
            by_id = {source["id"]: source for source in plan["sources"]}
            self.assertEqual(set(by_id), {"main", "logo"})
            for source_id, name in (("main", "main.mp4"), ("logo", "logo.png")):
                metadata = os.stat(workdir / "sources" / name)
                self.assertEqual((by_id[source_id]["size_bytes"], by_id[source_id]["mtime_ns"]), (metadata.st_size, metadata.st_mtime_ns))
                self.assertFalse(by_id[source_id]["proxy"])
            self.assertEqual(by_id["main"]["kind"], "video")
            self.assertEqual(by_id["logo"]["kind"], "image")
            # Nada de caminho absoluto, do caminho do plano nem do token no que sai pela API.
            for secret in (str(workdir), str(PROJECT_ROOT), "plan_path", "canonical_json", "\\\\", "sources/main.mp4"):
                self.assertNotIn(secret, raw)

    def test_the_hash_changes_within_two_seconds_of_editing_plan_json(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            with serving(workdir) as harness:
                client = harness.client()
                before = self.state(client)["plan"]
                edited = simple_plan()
                edited["segments"][0]["video"]["out_s"] = 4
                edited["segments"][0]["audio"]["tracks"][0]["out_s"] = 4
                started = time.monotonic()
                self.write_plan(workdir, edited)
                after = wait_until(
                    lambda: (lambda plan: plan if plan["plan_sha256"] != before["plan_sha256"] else None)(self.state(client)["plan"]),
                    timeout_s=10, message="o hash não mudou",
                )
                self.assertLessEqual(time.monotonic() - started, 2.0)
                self.assertEqual(after["duration_s"], 6.0)
                self.assertEqual(after["sources_fingerprint"], before["sources_fingerprint"])
                self.assertEqual(self.state(client)["state"], "ready")

    @staticmethod
    def try_replace(origin: Path, destination: Path) -> bool:
        """Troca o arquivo; no Windows falha por instantes se o FFprobe da vigia o estiver lendo."""

        try:
            os.replace(origin, destination)
        except PermissionError:
            return False
        return True

    def test_the_fingerprint_changes_when_a_source_changes(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan(), names=("main.mp4",))
            source = workdir / "sources" / "main.mp4"
            with serving(workdir) as harness:
                client = harness.client()
                first = self.state(client)["plan"]
                # Mesmo conteúdo e outro `mtime_ns`: a impressão muda e o plano (mesmo hash) é revalidado.
                stat = source.stat()
                os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
                touched = wait_until(
                    lambda: (lambda plan: plan if plan["sources_fingerprint"] != first["sources_fingerprint"] else None)(self.state(client)["plan"]),
                    timeout_s=10, message="a impressão não mudou com o mtime",
                )
                self.assertEqual(touched["plan_sha256"], first["plan_sha256"])
                self.assertEqual(touched["sources"][0]["mtime_ns"], stat.st_mtime_ns + 5_000_000_000)
                # Outro tamanho (a fonte é trocada por outra válida).
                run_ffmpeg(
                    ["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=12",
                     "-f", "lavfi", "-i", "sine=frequency=330:duration=12", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", "-y", str(workdir / "replacement.mp4")]
                )
                wait_until(lambda: self.try_replace(workdir / "replacement.mp4", source), message="não foi possível trocar a fonte")
                replaced = wait_until(
                    lambda: (lambda plan: plan if plan["sources_fingerprint"] != touched["sources_fingerprint"] else None)(self.state(client)["plan"]),
                    timeout_s=10, message="a impressão não mudou com a troca da fonte",
                )
                self.assertEqual(replaced["sources"][0]["size_bytes"], source.stat().st_size)

    def test_an_invalid_plan_reports_a_safe_error_and_keeps_the_last_valid_version(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            with serving(workdir) as harness:
                client = harness.client()
                good = self.state(client)["plan"]
                for corruption, message in (
                    ("{ não é json", "O plan.json não contém JSON válido."),
                    (json.dumps({**simple_plan(), "segmentos": []}), "O plano contém campo não permitido em raiz."),
                ):
                    with self.subTest(corruption=corruption[:20]):
                        (workdir / "plan.json").write_text(corruption, encoding="utf-8")
                        data = wait_until(
                            lambda: (lambda data: data if data["error"] and data["error"]["message"] == message else None)(self.state(client)),
                            timeout_s=10, message="o erro não apareceu",
                        )
                        self.assertEqual(data["state"], "outdated")
                        self.assertEqual(data["error"], {"code": "PLAN_INVALID", "message": message})
                        self.assertEqual(data["plan"]["plan_sha256"], good["plan_sha256"])
                        for secret in (str(workdir), "segmentos", "Traceback"):
                            self.assertNotIn(secret, json.dumps(data["error"]))
                        # O player segue na última versão válida: o stream sai do plano antigo.
                        self.assertEqual(client.get("/api/selection?start=0&end=1").status, 200)
                self.write_plan(workdir, simple_plan())
                recovered = wait_until(
                    lambda: (lambda data: data if data["state"] == "ready" else None)(self.state(client)),
                    timeout_s=10, message="o plano não voltou a ser válido",
                )
                self.assertIsNone(recovered["error"])
                self.assertEqual(recovered["plan"]["plan_sha256"], good["plan_sha256"])

    def test_a_missing_source_is_an_error_and_a_missing_plan_is_plan_required(self) -> None:
        with ui_workdir() as workdir:
            with serving(workdir) as harness:
                client = harness.client()
                data = self.state(client)
                self.assertEqual((data["state"], data["plan"]), ("missing", None))
                self.assertEqual(data["error"]["code"], "PLAN_REQUIRED")
                for path in ("/api/stream?t=0&res=full", "/api/frame?t=0&res=full", "/api/selection?start=0&end=1"):
                    response = client.get(path)
                    self.assertEqual((response.status, response.code), (409, "PLAN_REQUIRED"), path)
                self.install(workdir, simple_plan())
                ready = wait_until(lambda: (lambda d: d if d["state"] == "ready" else None)(self.state(client)), timeout_s=10)
                (workdir / "sources" / "main.mp4").unlink()
                broken = wait_until(lambda: (lambda d: d if d["error"] else None)(self.state(client)), timeout_s=10)
                self.assertEqual(broken["state"], "outdated")
                self.assertEqual(broken["error"]["code"], "PLAN_INVALID")
                self.assertEqual(broken["plan"]["plan_sha256"], ready["plan"]["plan_sha256"])

    def test_a_plan_that_is_invalid_from_the_start_is_reported_as_invalid(self) -> None:
        with ui_workdir() as workdir:
            (workdir / "plan.json").write_text("[]", encoding="utf-8")
            with serving(workdir) as harness:
                data = self.state(harness.client())
                self.assertEqual((data["state"], data["plan"]), ("invalid", None))
                self.assertEqual(data["error"]["code"], "PLAN_INVALID")
                self.assertEqual(harness.client().get("/api/selection?start=0&end=1").code, "PLAN_INVALID")

    def test_the_state_is_loading_until_the_first_validation_finishes(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            state = PlanState(workdir, poll_interval_s=0.05)
            reading = state.read()
            self.assertEqual((reading.status, reading.snapshot, reading.error), ("loading", None, None))
            with self.assertRaises(CliError) as caught:
                state.require_snapshot()
            self.assertEqual(caught.exception.code, "PLAN_REQUIRED")
            state.start()
            self.addCleanup(state.stop)
            wait_until(lambda: state.read().status == "ready")
            self.assertEqual(state.require_snapshot().plan_sha256, validate_plan(workdir)["plan_sha256"])

    def test_the_watcher_waits_for_a_plan_being_written_to_settle(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            state = PlanState(workdir, poll_interval_s=0.05)
            state.refresh()
            self.assertEqual(state.read().status, "ready")
            (workdir / "plan.json").write_text('{"schema_version": 2,', encoding="utf-8")
            state.refresh()  # primeiro ciclo com a mudança: espera estabilizar, não revalida
            self.assertEqual(state.read().status, "ready")
            (workdir / "plan.json").write_text(json.dumps(simple_plan()), encoding="utf-8")
            state.refresh()  # mudou de novo: outra espera
            self.assertEqual(state.read().status, "ready")
            state.refresh()  # estável: revalida (plano válido) e não passa pelo erro
            self.assertEqual(state.read().status, "ready")
            (workdir / "plan.json").write_text("{}", encoding="utf-8")
            state.refresh()
            state.refresh()
            self.assertEqual(state.read().status, "outdated")


# --- Texto da seleção e mapeamento ----------------------------------------------------------------


class SelectionTests(UiTestCase):
    """O mapeamento do trecho e o texto "Copiar para IA" conferem com `plan validate`."""

    def selection(self, client: Client, start: float, end: float) -> dict[str, Any]:
        """`data` de `GET /api/selection` para o trecho dado."""

        response = client.get(f"/api/selection?start={start}&end={end}")
        self.assertEqual(response.status, 200, response.body)
        return response.json["data"]

    def test_the_selection_matches_plan_validate_field_by_field(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, rich_plan())
            validation = validate_plan(workdir)
            segments = {item["index"]: item for item in validation["timeline"]["segments"]}
            # Oráculo à mão (a timeline do plano está no docstring de `rich_plan`).
            self.assertEqual((segments[2]["audio_start_s"], segments[2]["audio_end_s"]), (3.7, 6.5))
            self.assertEqual([(segments[i]["start_s"], segments[i]["end_s"]) for i in (1, 2, 3)], [(0, 4), (4, 6), (6, 8)])
            with serving(workdir) as harness:
                client = harness.client()
                # Atravessa um corte: S1 até 4 s e S2 a partir de 4 s (com J), as três camadas e a fala 2.
                cut = self.selection(client, 3.5, 5.0)
                self.assertEqual(
                    [(item["index"], item["source_id"], item["video"], item["audio_cut"]) for item in cut["segments"]],
                    [
                        (1, "main", {"start_s": 3.5, "end_s": 4.0, "source_in_s": 5.5, "source_out_s": 6.0}, None),
                        (2, "main", {"start_s": 4.0, "end_s": 5.0, "source_in_s": 12.0, "source_out_s": 13.0}, None),
                    ],
                )
                self.assertEqual(cut["segments"][1]["audio"], {"start_s": 3.7, "end_s": 5.0})
                self.assertEqual(
                    cut["layers"],
                    [
                        {"index": 1, "type": "text", "start_s": 3.5, "end_s": 5.0, "text": TEXT_LAYER["text"][:60]},
                        {"index": 2, "type": "image", "start_s": 3.5, "end_s": 5.0, "source_id": "logo"},
                    ],
                )
                self.assertEqual(
                    cut["captions"],
                    [{"item": 2, "text": "Segunda\nfala com \"aspas\"", "source_id": "main", "source_in_s": 12.2,
                      "source_out_s": 12.9, "start_s": 4.2, "end_s": 4.9}],
                )
                self.assertEqual(cut["plan_sha256"], validation["plan_sha256"])
                self.assertEqual((cut["start_s"], cut["end_s"], cut["duration_s"]), (3.5, 5.0, 1.5))
                self.assertEqual(
                    (cut["start_timecode"], cut["end_timecode"], cut["duration_timecode"]),
                    ("00:00:03:12", "00:00:05:00", "00:00:01:12"),
                )
                self.assertEqual(
                    cut["text"],
                    "\n".join(
                        [
                            "Trecho da montagem",
                            f"Trabalho: {workdir.name}",
                            f"Plano (SHA-256): {validation['plan_sha256']}",
                            "Intervalo: 00:00:03:12 a 00:00:05:00 (3.500 s a 5.000 s), duração 1.500 s (00:00:01:12)",
                            "Segmentos:",
                            '- Segmento 1: fonte "main", 5.500 s a 6.000 s da fonte',
                            '- Segmento 2: fonte "main", 12.000 s a 13.000 s da fonte',
                            "Camadas:",
                            '- Camada 1 (texto): "' + TEXT_LAYER["text"][:60] + '"',
                            '- Camada 2 (imagem): fonte "logo"',
                            "Legendas:",
                            '- Legenda 2: "Segunda\\nfala com \\"aspas\\""',
                            "Pedido:",
                        ]
                    ),
                )
                # Só o áudio J de S2 entra (3,7 a 3,8 s); o vídeo é o de S1.
                joint = self.selection(client, 3.6, 3.8)
                self.assertEqual([(item["index"], item["audio_cut"]) for item in joint["segments"]], [(1, None), (2, "j")])
                self.assertIsNone(joint["segments"][1]["video"])
                self.assertEqual(joint["segments"][1]["audio"], {"start_s": 3.7, "end_s": 3.8})
                self.assertIn('- Segmento 2: fonte "main", só o áudio do corte J entra no trecho', joint["text"])
                # Só o áudio L de S2 entra (6,1 a 6,4 s); o vídeo é o de S3.
                tail = self.selection(client, 6.1, 6.4)
                self.assertEqual([(item["index"], item["audio_cut"]) for item in tail["segments"]], [(2, "l"), (3, None)])
                self.assertEqual(tail["segments"][1]["video"], {"start_s": 6.1, "end_s": 6.4, "source_in_s": 22.1, "source_out_s": 22.4})
                self.assertIn('- Segmento 2: fonte "main", só o áudio do corte L entra no trecho', tail["text"])
                self.assertEqual([item["index"] for item in tail["layers"]], [2, 3])
                video_layer = tail["layers"][1]
                self.assertEqual(
                    (video_layer["source_id"], video_layer["source_in_s"], video_layer["source_out_s"]),
                    ("main", 25.1, 25.4),
                )
                self.assertIn('- Camada 3 (vídeo): fonte "main", 25.100 s a 25.400 s da fonte', tail["text"])
                # Blocos que só se tocam não entram: a seleção exata de S2 (Alt+clique) traz S2 e o que a cobre.
                exact = self.selection(client, 4.0, 6.0)
                self.assertEqual([item["index"] for item in exact["segments"]], [2])
                self.assertEqual(exact["segments"][0]["video"]["source_in_s"], 12.0)
                self.assertEqual(exact["segments"][0]["video"]["source_out_s"], 14.0)
                # Sem camada nem legenda no trecho: seções presentes com `- nenhum`.
                quiet = self.selection(client, 7.0, 7.9)
                self.assertEqual((quiet["layers"], quiet["captions"]), ([], []))
                self.assertIn("Legendas:\n- nenhum\nPedido:", quiet["text"])
                self.assertTrue(quiet["text"].endswith("Pedido:"))

    def test_the_text_has_no_absolute_path_and_no_token(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, rich_plan())
            with serving(workdir) as harness:
                data = self.selection(harness.client(), 0.0, 8.0)
                raw = harness.client().get("/api/selection?start=0&end=8").body.decode("utf-8")
            for text in (data["text"], raw):
                for secret in (
                    str(workdir), json.dumps(str(workdir))[1:-1], str(PROJECT_ROOT), harness.token, "sources/"
                ):
                    self.assertNotIn(secret, text)
            # Unidade de disco seguida de barra (caminho absoluto do Windows) no texto copiado, que tem quebras reais.
            self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", data["text"]))
            self.assertEqual([item["index"] for item in data["segments"]], [1, 2, 3])
            self.assertEqual([item["index"] for item in data["layers"]], [1, 2, 3])
            self.assertEqual([item["item"] for item in data["captions"]], [1, 2])
            self.assertEqual(data["text"].splitlines()[3].split(" (")[0], "Intervalo: 00:00:00:00 a 00:00:08:00")

    def test_timecode_counts_frames_in_the_output_rate(self) -> None:
        self.assertEqual(format_timecode(0, 25), "00:00:00:00")
        self.assertEqual(format_timecode(0.04, 25), "00:00:00:01")
        self.assertEqual(format_timecode(0.96, 25), "00:00:00:24")
        self.assertEqual(format_timecode(1.0, 25), "00:00:01:00")
        self.assertEqual(format_timecode(3661.5, 25), "01:01:01:12")
        # 29,97 quadros: base 30 e contagem de quadros (sem descarte), a mesma que a tela repete.
        self.assertEqual(format_timecode(1.0, 30000 / 1001), "00:00:00:29")
        self.assertEqual(format_timecode(10.0, 30000 / 1001), "00:00:09:29")
        self.assertEqual(format_timecode(1.0334, 30000 / 1001), "00:00:01:00")
        self.assertEqual(format_timecode(2.0, 60), "00:00:02:00")


# --- Stream e quadro de scrub ---------------------------------------------------------------------


def open_slow_stream(harness: Harness, client: Client, path: str) -> socket.socket:
    """Abre um stream com buffer de recepção de 1 KiB e não o lê: a pressão do pipe prende o servidor na escrita."""

    connection = socket.socket()
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    connection.settimeout(30)
    connection.connect(("127.0.0.1", harness.port))
    connection.sendall(
        f"GET {path} HTTP/1.0\r\nHost: 127.0.0.1:{harness.port}\r\nCookie: {client.cookie}\r\n\r\n".encode()
    )
    return connection


class RecordedProcesses:
    """Registra os FFmpeg que o servidor cria (e só eles), sem alterar o que `subprocess.Popen` faz."""

    def __init__(self) -> None:
        self.processes: list[subprocess.Popen[bytes]] = []
        self.arguments: list[list[str]] = []
        self._real = subprocess.Popen

    def __call__(self, args: Any, *positional: Any, **keywords: Any) -> subprocess.Popen[bytes]:
        """Cria o processo de verdade e, se for FFmpeg, guarda o processo e os argumentos."""

        process = self._real(args, *positional, **keywords)
        if isinstance(args, list) and args and Path(str(args[0])).stem.lower() == "ffmpeg":
            self.processes.append(process)
            self.arguments.append([str(item) for item in args])
        return process

    def kill_all(self) -> None:
        """Mata o que o teste deixou vivo."""

        for process in self.processes:
            if process.poll() is None:
                process.kill()


class MediaApiTests(BigSourceTestCase):
    """O stream é o comando da T1 e termina com o cliente; o scrub devolve JPEG; falhas são seguras."""

    def setUp(self) -> None:
        self.recorder = RecordedProcesses()
        self.addCleanup(self.recorder.kill_all)

    def expected_arguments(self, workdir: Path, start_s: float, resolution: str) -> list[str]:
        """Argumentos do núcleo da T1 para o mesmo pedido, com o caminho do script de filtro neutralizado."""

        document, validation = load_validated_plan(workdir)
        state = live.prepare_live(workdir, document, validation, start_s, resolution)
        with live.filter_script(workdir, state) as script:
            arguments = live.stream_arguments(state, script)
        index = arguments.index("-/filter_complex" if WINDOWS else "-filter_complex")
        return [*arguments[: index + 1], "<script>", *arguments[index + 2:]]

    def test_the_stream_is_the_core_command_of_t1_and_a_valid_fragmented_mp4(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                for resolution, canvas in (("1/2", (80, 44)), ("full", (160, 90))):
                    with self.subTest(resolution=resolution):
                        before = len(self.recorder.arguments)
                        response = client.get(f"/api/stream?t=2.04&res={resolution}")
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers["content-type"], "video/mp4")
                        self.assertEqual(response.headers["x-editor-start-s"], "2.040000")
                        self.assertNotIn("content-length", response.headers)
                        self.assertEqual(len(self.recorder.arguments) - before, 1)
                        used = self.recorder.arguments[-1]
                        self.assertEqual(Path(used[0]).stem.lower(), "ffmpeg")
                        index = used.index("-/filter_complex" if WINDOWS else "-filter_complex")
                        self.assertEqual([*used[1: index + 1], "<script>", *used[index + 2:]],
                                         self.expected_arguments(workdir, 2.04, resolution))
                        self.assertTrue(Path(used[index + 1]).parent.name == ".tmp")
                        boxes = [kind for kind, _ in top_level_boxes(response.body)]
                        self.assertEqual(boxes[:2], ["ftyp", "moov"])
                        self.assertGreater(boxes.count("moof"), 1)
                        path = workdir / f"stream-{resolution.replace('/', '-')}.mp4"
                        path.write_bytes(response.body)
                        streams = {item["codec_type"]: item for item in probe(path)["streams"]}
                        self.assertEqual((streams["video"]["codec_name"], streams["audio"]["codec_name"]), ("h264", "aac"))
                        self.assertEqual((streams["video"]["width"], streams["video"]["height"]), canvas)
                # O script de filtro de cada pedido some ao fim (nada fica em `.tmp`).
                wait_until(lambda: not list((workdir / ".tmp").glob(f"{LIVE_FILTER_PREFIX}*")))
            # O servidor encerrou: nenhum FFmpeg sobra.
            wait_until(lambda: all(p.poll() is not None for p in self.recorder.processes))

    def test_closing_the_connection_ends_the_ffmpeg_process(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, self.big_plan(), names=("big.mp4",))
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                connection = self.open_stream(harness, client, "/api/stream?t=0&res=full")
                try:
                    self.assertIn(b"200", connection.recv(128))
                    process = wait_until(lambda: self.recorder.processes[-1:] and self.recorder.processes[-1])
                    # O cliente não consome: a pressão do pipe segura o FFmpeg (ele não roda à frente sem limite).
                    time.sleep(1.0)
                    self.assertIsNone(process.poll(), "o FFmpeg terminou sem o cliente consumir o stream")
                finally:
                    connection.close()
                wait_until(lambda: process.poll() is not None, timeout_s=15, message="o FFmpeg não terminou com a conexão")
                # A sessão segue utilizável.
                self.assertEqual(client.get("/api/state").status, 200)

    def test_a_client_that_stops_consuming_for_the_stall_timeout_ends_the_stream(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, self.big_plan(), names=("big.mp4",))
            with patch.object(subprocess, "Popen", self.recorder), \
                    patch.object(ui_server.UiRequestHandler, "timeout", 1.0), serving(workdir) as harness:
                client = harness.client()
                connection = self.open_stream(harness, client, "/api/stream?t=0&res=full")
                try:
                    self.assertIn(b"200", connection.recv(128))
                    process = wait_until(lambda: self.recorder.processes[-1:] and self.recorder.processes[-1])
                    # Pausa: o cliente fica conectado e não lê. Passado o prazo o servidor encerra o FFmpeg.
                    wait_until(lambda: process.poll() is not None, timeout_s=20, message="o FFmpeg não foi encerrado com o cliente parado")
                finally:
                    connection.close()

    def test_a_new_stream_request_of_the_session_ends_the_previous_one(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, self.big_plan(), names=("big.mp4",))
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                first = self.open_stream(harness, client, "/api/stream?t=0&res=full")
                second = None
                try:
                    self.assertIn(b"200", first.recv(128))
                    older = wait_until(lambda: self.recorder.processes[-1:] and self.recorder.processes[-1])
                    second = self.open_stream(harness, client, "/api/stream?t=5&res=full")
                    self.assertIn(b"200", second.recv(128))
                    newer = wait_until(lambda: len(self.recorder.processes) >= 2 and self.recorder.processes[-1])
                    self.assertIsNot(older, newer)
                    wait_until(lambda: older.poll() is not None, timeout_s=15, message="o stream anterior não foi encerrado")
                    self.assertIsNone(newer.poll())
                finally:
                    first.close()
                    if second is not None:
                        second.close()
                wait_until(lambda: newer.poll() is not None, timeout_s=15)

    def open_stream(self, harness: Harness, client: Client, path: str) -> socket.socket:
        """Abre um stream com um buffer de recepção minúsculo e o deixa sem ler, para o servidor ficar preso na escrita."""

        return open_slow_stream(harness, client, path)

    def test_the_scrub_frame_is_a_jpeg_of_the_requested_resolution(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            with serving(workdir) as harness:
                client = harness.client()
                for resolution, canvas in (("full", (160, 90)), ("1/2", (80, 44)), ("1/4", (40, 22))):
                    with self.subTest(resolution=resolution):
                        response = client.get(f"/api/frame?t=4.5&res={resolution}")
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers["content-type"], "image/jpeg")
                        self.assertEqual(response.headers["content-length"], str(len(response.body)))
                        self.assertEqual(response.headers["x-editor-start-s"], "4.480000")
                        self.assertTrue(response.body.startswith(b"\xff\xd8"))
                        path = workdir / f"frame-{resolution.replace('/', '-')}.jpg"
                        path.write_bytes(response.body)
                        video = probe(path)["streams"][0]
                        self.assertEqual((video["codec_name"], video["width"], video["height"]), ("mjpeg", *canvas))
            # O quadro é o do núcleo da T1 (a prova bit a bit contra o stream está em `test_live_stream`).
            document, validation = load_validated_plan(workdir)
            self.assertEqual(live.extract_frame(workdir, document, validation, 4.5, "full")[:2], b"\xff\xd8")

    def test_a_failure_before_the_first_byte_is_a_safe_500_without_paths(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan(), names=("main.mp4",))
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                # A fonte some depois de o plano ter sido carregado: o FFmpeg não abre a entrada.
                with patch.object(PlanState, "refresh", lambda self: None):
                    source = workdir / "sources" / "main.mp4"
                    source.rename(workdir / "sources" / "movida.mp4")
                    stream = client.get("/api/stream?t=0&res=full")
                    frame = client.get("/api/frame?t=0&res=full")
                for response, code in ((stream, "PLAYBACK_FAILED"), (frame, "FRAME_FAILED")):
                    self.assertEqual((response.status, response.code), (500, code))
                    self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")
                    for secret in (str(workdir), "main.mp4", "Error", "ffmpeg", "No such file"):
                        self.assertNotIn(secret, response.body.decode("utf-8"))
                self.assertEqual(stream.json["error"]["message"], "Não foi possível reproduzir este trecho.")
                self.assertEqual(frame.json["error"]["message"], "Não foi possível extrair este quadro.")
                wait_until(lambda: all(p.poll() is not None for p in self.recorder.processes))

    def test_a_missing_ffmpeg_is_a_safe_dependency_error(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, simple_plan())
            with serving(workdir) as harness:
                client = harness.client()
                with patch("editor.ui.playback.dependencies.resolve_executable", return_value=None):
                    response = client.get("/api/stream?t=0&res=full")
                self.assertEqual((response.status, response.code), (503, "DEPENDENCY_MISSING"))
                self.assertNotIn(str(workdir), response.body.decode("utf-8"))


# --- Modo Fonte (T5): uso, seleção na fonte, stream e quadro --------------------------------------


class SourceUsageTests(UiTestCase):
    """O uso de cada fonte que o estado entrega confere com o plano, com `plan validate` e com `source_intervals`."""

    def sources(self, client: Client) -> dict[str, dict[str, Any]]:
        """Fontes de `GET /api/state` por id."""

        response = client.get("/api/state")
        self.assertEqual(response.status, 200, response.body)
        return {item["id"]: item for item in response.json["data"]["plan"]["sources"]}

    @staticmethod
    def as_tuples(uses: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        """Usos na forma `(tipo, índice, fonte de, fonte até, montagem de, montagem até, loop)`."""

        return [
            (u["kind"], u["index"], u["source_in_s"], u["source_out_s"], u["start_s"], u["end_s"], u["loop"])
            for u in uses
        ]

    def test_the_usage_bar_data_matches_the_plan_and_plan_validate(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            document, validation = load_validated_plan(workdir)
            with serving(workdir) as harness:
                client = harness.client()
                sources = self.sources(client)
                raw = client.get("/api/state").body.decode("utf-8")
            self.assertEqual(self.as_tuples(sources["main"]["uses"]), EXPECTED_MAIN_USES)
            self.assertEqual(self.as_tuples(sources["voice"]["uses"]), [("audio", 3, 0.0, 2.0, 6.0, 8.0, False)])
            self.assertEqual((sources["logo"]["uses"], sources["logo"]["used_count"]), ([], 0))
            # Segmentos e camadas de cada uso saem da timeline de `plan validate`, não de conta própria.
            timeline = {item["index"]: item for item in validation["timeline"]["segments"]}
            for use in sources["main"]["uses"] + sources["voice"]["uses"]:
                if use["kind"] == "segment":
                    self.assertEqual((use["start_s"], use["end_s"]), (timeline[use["index"]]["start_s"], timeline[use["index"]]["end_s"]))
                elif use["kind"] == "audio" and not use["loop"]:
                    self.assertEqual(use["start_s"], timeline[use["index"]]["audio_start_s"])
                    self.assertLessEqual(use["end_s"], timeline[use["index"]]["audio_end_s"] + 1e-9)
                elif use["kind"] == "audio":
                    self.assertEqual((use["start_s"], use["end_s"]), (timeline[use["index"]]["audio_start_s"], timeline[use["index"]]["audio_end_s"]))
                else:
                    layer = document["layers"][use["index"] - 1]
                    self.assertEqual((use["start_s"], use["end_s"]), (layer["start_s"], layer["end_s"]))
            # Os intervalos da fonte são os de `source_intervals`, com um refinamento: a trilha `voice` declara 0 a
            # 10 s sob uma janela de 2 s e a barra mostra só o que se ouve (a música inteira não é "usada").
            planned = source_intervals(document)
            self.assertEqual(
                sorted((u["source_in_s"], u["source_out_s"]) for u in sources["main"]["uses"]), sorted(planned["main"])
            )
            self.assertEqual(planned["voice"], [(0.0, 10.0)])
            self.assertEqual([(u["source_in_s"], u["source_out_s"]) for u in sources["voice"]["uses"]], [(0.0, 2.0)])
            # Fontes: tipo, duração, taxa própria (25 sem vídeo) e quantas vezes cada uma é usada.
            self.assertEqual(
                {key: (item["kind"], item["duration_s"], item["frame_rate"], item["used_count"]) for key, item in sources.items()},
                {
                    "main": ("video", 30.0, 25.0, 5),   # S1, S2, S3, S4 e a camada 3 (vídeo e áudio do mesmo segmento contam uma vez)
                    "logo": ("image", None, None, 0),
                    "voice": ("audio", 10.0, 25.0, 1),
                },
            )
            for forbidden in (str(workdir), "sources/", "voice.wav"):
                self.assertNotIn(forbidden, raw)

    def test_a_track_longer_than_the_audible_window_uses_only_what_is_heard(self) -> None:
        with ui_workdir() as workdir:
            # Música declarada com 0 a 8 s sob um segmento de 3 s, e a mesma música em loop de 1 s.
            document = base_plan(
                [segment(2, 5, [track(0, 8, source_id="voice")], mode="replace"), segment(6, 8, [track(2, 3, loop=True, source_id="voice")], mode="replace")],
                sources=[
                    {"id": "main", "kind": "video", "path": "sources/main.mp4"},
                    {"id": "voice", "kind": "audio", "path": "sources/voice.wav"},
                ],
            )
            self.install(workdir, document, names=("main.mp4", "voice.wav"))
            with serving(workdir) as harness:
                voice = self.sources(harness.client())["voice"]
            # Primeira trilha: heard = min(8, 3) = 3 s, de 0 a 3 na fonte, na montagem de 0 a 3. A segunda repete
            # 2 a 3 em loop numa janela de 2 s: a fonte usada é 2 a 3 e a montagem aponta a janela inteira.
            self.assertEqual(
                self.as_tuples(voice["uses"]),
                [("audio", 1, 0.0, 3.0, 0.0, 3.0, False), ("audio", 2, 2.0, 3.0, 3.0, 5.0, True)],
            )
            self.assertEqual(voice["used_count"], 2)

    def test_a_source_that_vanished_is_marked_unavailable_with_the_public_error(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            with serving(workdir) as harness:
                client = harness.client()
                before = self.sources(client)
                self.assertTrue(all(item["available"] and item["error"] is None for item in before.values()))
                with patch.object(PlanState, "refresh", lambda self: None):
                    (workdir / "sources" / "voice.wav").rename(workdir / "sources" / "movida.wav")
                    after = self.sources(client)
                self.assertEqual((after["voice"]["available"], after["main"]["available"]), (False, True))
                self.assertEqual(
                    after["voice"]["error"], {"code": "SOURCE_INVALID", "message": "Não foi possível ler uma fonte do plano."}
                )
                self.assertNotIn(str(workdir), json.dumps(after["voice"]))
                # A vigia revalida e a faixa de estado traz o erro do plano; a última versão válida segue na tela.
                broken = wait_until(
                    lambda: (lambda data: data if data["error"] else None)(client.get("/api/state").json["data"]), timeout_s=10
                )
                self.assertEqual((broken["state"], broken["plan"] is not None), ("outdated", True))
                self.assertEqual(broken["error"]["code"], "PLAN_INVALID")


class SourceSelectionTests(UiTestCase):
    """O texto e a situação da seleção na fonte conferem com `plan validate` e com o plano."""

    def selection(self, client: Client, source_id: str, start: float, end: float) -> dict[str, Any]:
        """`data` de `GET /api/source/selection` para o trecho dado."""

        response = client.get(f"/api/source/selection?source_id={source_id}&start={start}&end={end}")
        self.assertEqual(response.status, 200, response.body)
        return response.json["data"]

    @staticmethod
    def parts(data: dict[str, Any]) -> list[tuple[Any, ...]]:
        """Partes na forma `(tipo, índice, fonte de, fonte até, montagem de, montagem até, loop)`."""

        return [
            (p["kind"], p["index"], p["source_in_s"], p["source_out_s"], p["start_s"], p["end_s"], p["loop"])
            for p in data["parts"]
        ]

    def test_used_discarded_and_partial_selections_with_their_places_in_the_assembly(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            validation = validate_plan(workdir)
            segments = {item["index"]: item for item in validation["timeline"]["segments"]}
            with serving(workdir) as harness:
                client = harness.client()
                # Usado: 3 a 5 s de main cabem em S1 (e, em 3 a 4 s, também em S4), vídeo e áudio de S1.
                used = self.selection(client, "main", 3, 5)
                self.assertEqual(used["status"], "used")
                self.assertEqual(
                    self.parts(used),
                    [
                        ("segment", 4, 3.0, 4.0, 9.0, 10.0, False),
                        ("segment", 1, 3.0, 5.0, 1.0, 3.0, False),
                        ("audio", 1, 3.0, 5.0, 1.0, 3.0, False),
                    ],
                )
                # A posição na montagem sai da timeline do plano: início do segmento mais o deslocamento na fonte.
                self.assertEqual(used["parts"][1]["start_s"], segments[1]["start_s"] + (3 - 2))
                self.assertEqual(used["parts"][0]["start_s"], segments[4]["start_s"] + (3 - 2))
                # Parcial: 5 a 8 s só cobre 5 a 6 s (S1 e a trilha em loop de S4); o resto não é usado.
                partial = self.selection(client, "main", 5, 8)
                self.assertEqual(partial["status"], "partial")
                self.assertEqual(
                    self.parts(partial),
                    [
                        ("segment", 1, 5.0, 6.0, 3.0, 4.0, False),
                        ("audio", 1, 5.0, 6.0, 3.0, 4.0, False),
                        ("audio", 4, 5.0, 6.0, 8.0, 10.0, True),
                    ],
                )
                self.assertEqual(
                    partial["text"],
                    "\n".join(
                        [
                            "Trecho da fonte",
                            f"Trabalho: {workdir.name}",
                            f"Plano (SHA-256): {validation['plan_sha256']}",
                            'Fonte: "main" (vídeo)',
                            "Intervalo: 00:00:05:00 a 00:00:08:00 (5.000 s a 8.000 s), duração 3.000 s (00:00:03:00)",
                            "Situação: parcialmente usado",
                            "Partes usadas:",
                            "- Segmento 1: 5.000 s a 6.000 s da fonte, na montagem de 00:00:03:00 a 00:00:04:00 (3.000 s a 4.000 s)",
                            "- Áudio do segmento 1: 5.000 s a 6.000 s da fonte, na montagem de 00:00:03:00 a 00:00:04:00 (3.000 s a 4.000 s)",
                            "- Áudio do segmento 4: 5.000 s a 6.000 s da fonte, na montagem, em loop, de 00:00:08:00 a 00:00:10:00 (8.000 s a 10.000 s)",
                            "Pedido:",
                        ]
                    ),
                )
                self.assertEqual((partial["start_timecode"], partial["end_timecode"], partial["duration_timecode"]),
                                 ("00:00:05:00", "00:00:08:00", "00:00:03:00"))
                # Descartado: 7 a 10 s de main não aparece na montagem.
                discarded = self.selection(client, "main", 7, 10)
                self.assertEqual((discarded["status"], discarded["parts"]), ("discarded", []))
                self.assertIn("Situação: descartado\nPartes usadas:\n- nenhum\nPedido:", discarded["text"])
                self.assertTrue(discarded["text"].endswith("Pedido:"))
                # O áudio J de S2 vem da fonte antes do vídeo dela e aparece na montagem antes de S2 começar.
                joint = self.selection(client, "main", 11.7, 12.0)
                self.assertEqual(joint["status"], "used")
                self.assertEqual(self.parts(joint), [("audio", 2, 11.7, 12.0, 3.7, 4.0, False)])
                self.assertEqual(
                    (joint["parts"][0]["start_timecode"], joint["parts"][0]["end_timecode"]), ("00:00:03:17", "00:00:04:00")
                )
                # A camada de vídeo e o segmento 3 usam trechos vizinhos da mesma fonte: tocar-se não é usar mais.
                tail = self.selection(client, "main", 22, 26)
                self.assertEqual((tail["status"], [p[:2] for p in self.parts(tail)]), ("used", [("segment", 3), ("audio", 3)][:1] + [("layer", 3)]))
                # Fonte só de áudio: situação e tipo no texto; a parte de `voice` usada é a janela ouvida.
                voice = self.selection(client, "voice", 0, 5)
                self.assertEqual((voice["status"], self.parts(voice)), ("partial", [("audio", 3, 0.0, 2.0, 6.0, 8.0, False)]))
                self.assertIn('Fonte: "voice" (áudio)', voice["text"])
                self.assertEqual(self.selection(client, "voice", 3, 5)["status"], "discarded")
                self.assertEqual(self.selection(client, "voice", 0, 2)["status"], "used")

    def test_the_text_has_no_absolute_path_and_no_token(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            with serving(workdir) as harness:
                client = harness.client()
                data = self.selection(client, "main", 0, 30)
            blob = json.dumps(data)
            for forbidden in (str(workdir), str(PROJECT_ROOT), "sources/", ".mp4", harness.token, "\\\\"):
                self.assertNotIn(forbidden, blob)

    def test_requests_outside_the_allowlist_are_refused(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            with serving(workdir) as harness:
                client = harness.client()
                refused = {
                    "fonte fora do plano": "source_id=outra&start=0&end=1",
                    "fonte de imagem": "source_id=logo&start=0&end=1",
                    "maiúsculas": "source_id=Main&start=0&end=1",
                    "caminho": "source_id=..%2Fmain&start=0&end=1",
                    "barra invertida": "source_id=sources%5Cmain&start=0&end=1",
                    "id com 65 caracteres": f"source_id={'a' * 65}&start=0&end=1",
                    "sem source_id": "start=0&end=1",
                    "source_id repetido": "source_id=main&source_id=voice&start=0&end=1",
                    "campo a mais": "source_id=main&start=0&end=1&res=full",
                    "invertida": "source_id=main&start=3&end=2",
                    "vazia": "source_id=main&start=2&end=2",
                    "depois do fim da fonte": "source_id=main&start=2&end=30.5",
                    "voice depois do fim": "source_id=voice&start=0&end=10.5",
                    "expoente": "source_id=main&start=1e0&end=2",
                    "negativa": "source_id=main&start=-1&end=2",
                }
                for name, query in refused.items():
                    with self.subTest(name):
                        response = client.get(f"/api/source/selection?{query}")
                        self.assertEqual((response.status, response.code), (400, "ARGUMENT_INVALID"), response.body)
                        for secret in (str(workdir), "outra", "Main"):
                            self.assertNotIn(secret, response.body.decode("utf-8"))
                # O limite exato da fonte é aceito.
                self.assertEqual(self.selection(client, "main", 0, 30)["status"], "partial")
                self.assertEqual(client.request("POST", "/api/source/selection?source_id=main&start=0&end=1",
                                                headers={"Origin": f"http://127.0.0.1:{harness.port}"}).status, 405)


class SourceMediaApiTests(BigSourceTestCase):
    """Stream e quadro da fonte inteira pela API: comando do núcleo, proxy, áudio, término e `SOURCE_INVALID`."""

    def setUp(self) -> None:
        self.recorder = RecordedProcesses()
        self.addCleanup(self.recorder.kill_all)

    def expected_arguments(self, workdir: Path, source_id: str, start_s: float, resolution: str) -> list[str]:
        """Argumentos do núcleo para o mesmo pedido de stream."""

        _, validation = load_validated_plan(workdir)
        return live.source_stream_arguments(live.prepare_source_live(workdir, validation, source_id, start_s, resolution))

    def test_the_source_stream_is_the_core_command_and_a_valid_fragmented_mp4(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                for resolution, canvas in (("1/2", (80, 44)), ("full", (160, 90))):
                    with self.subTest(resolution=resolution):
                        before = len(self.recorder.arguments)
                        response = client.get(f"/api/source/stream?source_id=main&t=10.04&res={resolution}")
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers["content-type"], "video/mp4")
                        self.assertEqual(response.headers["x-editor-start-s"], "10.040000")
                        self.assertNotIn("content-length", response.headers)
                        self.assertEqual(len(self.recorder.arguments) - before, 1)
                        self.assertEqual(self.recorder.arguments[-1][1:], self.expected_arguments(workdir, "main", 10.04, resolution))
                        boxes = [kind for kind, _ in top_level_boxes(response.body)]
                        self.assertEqual(boxes[:2], ["ftyp", "moov"])
                        self.assertGreater(boxes.count("moof"), 1)
                        path = workdir / f"source-{resolution.replace('/', '-')}.mp4"
                        path.write_bytes(response.body)
                        info = probe(path)
                        streams = {item["codec_type"]: item for item in info["streams"]}
                        self.assertEqual((streams["video"]["codec_name"], streams["audio"]["codec_name"]), ("h264", "aac"))
                        self.assertEqual((streams["video"]["width"], streams["video"]["height"]), canvas)
                        # A fonte vai inteira (30 s) a partir de T, não só o que a montagem usa.
                        self.assertAlmostEqual(float(info["format"]["duration"]), 30.0 - 10.04, delta=0.2)
                # Não há script de filtro nem outro arquivo temporário: a fonte é uma transcodificação simples.
                self.assertEqual(list((workdir / ".tmp").glob(f"{LIVE_FILTER_PREFIX}*")), [])
            wait_until(lambda: all(p.poll() is not None for p in self.recorder.processes))

    def test_an_audio_only_source_is_served_without_image_and_has_no_frame(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            with serving(workdir) as harness:
                client = harness.client()
                response = client.get("/api/source/stream?source_id=voice&t=2&res=full")
                self.assertEqual((response.status, response.headers["content-type"]), (200, "video/mp4"))
                path = workdir / "voice.mp4"
                path.write_bytes(response.body)
                info = probe(path)
                self.assertEqual([(item["codec_type"], item["codec_name"]) for item in info["streams"]], [("audio", "aac")])
                self.assertAlmostEqual(float(info["format"]["duration"]), 8.0, delta=0.2)
                frame = client.get("/api/source/frame?source_id=voice&t=2&res=full")
                self.assertEqual((frame.status, frame.code), (400, "ARGUMENT_INVALID"))

    def test_the_source_frame_is_a_jpeg_of_the_source_at_t(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            with serving(workdir) as harness:
                client = harness.client()
                for resolution, canvas in (("full", (160, 90)), ("1/2", (80, 44)), ("1/4", (40, 22))):
                    with self.subTest(resolution=resolution):
                        response = client.get(f"/api/source/frame?source_id=main&t=20.5&res={resolution}")
                        self.assertEqual((response.status, response.headers["content-type"]), (200, "image/jpeg"))
                        self.assertEqual(response.headers["x-editor-start-s"], "20.480000")
                        self.assertTrue(response.body.startswith(b"\xff\xd8"))
                        path = workdir / f"source-frame-{resolution.replace('/', '-')}.jpg"
                        path.write_bytes(response.body)
                        video = probe(path)["streams"][0]
                        self.assertEqual((video["codec_name"], video["width"], video["height"]), ("mjpeg", *canvas))

    def test_fractions_read_the_proxy_and_full_reads_the_original_through_the_api(self) -> None:
        with ui_workdir() as workdir:
            make_source(workdir / "sources" / "main.mp4", "1920x1080")
            self.write_plan(workdir, {**simple_plan(), "segments": [segment(0, 1), segment(1, 2)]})
            self.assertEqual([item["status"] for item in build_proxies(workdir)["proxies"]], ["created"])
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                state = client.get("/api/state").json["data"]["plan"]["sources"][0]
                self.assertTrue(state["proxy"])
                for resolution in ("full", "1/2"):
                    with self.subTest(resolution=resolution):
                        response = client.get(f"/api/source/stream?source_id=main&t=0.5&res={resolution}")
                        self.assertEqual(response.status, 200)
                        used = self.recorder.arguments[-1]
                        read = Path(used[used.index("-i") + 1])
                        if resolution == "full":
                            self.assertEqual(read.name, "main.mp4")
                            self.assertEqual(read.parent.name, "sources")
                        else:
                            self.assertEqual(read.parent.name, "proxy")

    def test_closing_the_connection_ends_the_ffmpeg_process(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, self.big_plan(), names=("big.mp4",))
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                connection = open_slow_stream(harness, client, "/api/source/stream?source_id=main&t=0&res=full")
                try:
                    self.assertIn(b"200", connection.recv(128))
                    process = wait_until(lambda: self.recorder.processes[-1:] and self.recorder.processes[-1])
                    time.sleep(1.0)
                    self.assertIsNone(process.poll(), "o FFmpeg terminou sem o cliente consumir o stream")
                finally:
                    connection.close()
                wait_until(lambda: process.poll() is not None, timeout_s=15, message="o FFmpeg não terminou com a conexão")
                self.assertEqual(client.get("/api/state").status, 200)

    def test_one_source_stream_per_session_and_it_lives_beside_the_assembly_stream(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, self.big_plan(), names=("big.mp4",))
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                assembly = open_slow_stream(harness, client, "/api/stream?t=0&res=full")
                first = second = None
                try:
                    self.assertIn(b"200", assembly.recv(128))
                    montage = wait_until(lambda: self.recorder.processes[-1:] and self.recorder.processes[-1])
                    first = open_slow_stream(harness, client, "/api/source/stream?source_id=main&t=0&res=full")
                    self.assertIn(b"200", first.recv(128))
                    older = wait_until(lambda: len(self.recorder.processes) >= 2 and self.recorder.processes[-1])
                    second = open_slow_stream(harness, client, "/api/source/stream?source_id=main&t=5&res=full")
                    self.assertIn(b"200", second.recv(128))
                    newer = wait_until(lambda: len(self.recorder.processes) >= 3 and self.recorder.processes[-1])
                    # O segundo pedido de fonte encerra o primeiro; o stream da montagem é de outro tipo e segue vivo.
                    wait_until(lambda: older.poll() is not None, timeout_s=15, message="o stream anterior da fonte não foi encerrado")
                    self.assertIsNone(newer.poll())
                    self.assertIsNone(montage.poll())
                finally:
                    for connection in (assembly, first, second):
                        if connection is not None:
                            connection.close()
                wait_until(lambda: newer.poll() is not None and montage.poll() is not None, timeout_s=15)

    def test_a_missing_source_file_is_source_invalid_without_starting_ffmpeg(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                with patch.object(PlanState, "refresh", lambda self: None):
                    (workdir / "sources" / "main.mp4").rename(workdir / "sources" / "movida.mp4")
                    stream = client.get("/api/source/stream?source_id=main&t=1&res=full")
                    frame = client.get("/api/source/frame?source_id=main&t=1&res=full")
                    selection = client.get("/api/source/selection?source_id=main&start=0&end=1")
                for response in (stream, frame):
                    self.assertEqual((response.status, response.code), (409, "SOURCE_INVALID"))
                    self.assertEqual(response.json["error"]["message"], "Não foi possível ler uma fonte do plano.")
                    for secret in (str(workdir), "main.mp4", "movida"):
                        self.assertNotIn(secret, response.body.decode("utf-8"))
                self.assertEqual(self.recorder.arguments, [])
                # O texto da seleção só lê o plano: segue disponível para a fonte indisponível.
                self.assertEqual(selection.status, 200)

    def test_stream_and_frame_requests_outside_the_allowlist_never_start_ffmpeg(self) -> None:
        with ui_workdir() as workdir:
            self.install(workdir, source_plan(), names=SOURCE_NAMES)
            with patch.object(subprocess, "Popen", self.recorder), serving(workdir) as harness:
                client = harness.client()
                refused = {
                    "fonte fora do plano": "source_id=outra&t=1&res=full",
                    "fonte de imagem": "source_id=logo&t=0&res=full",
                    "maiúsculas": "source_id=MAIN&t=1&res=full",
                    "caminho": "source_id=..%2F..%2Fplan.json&t=1&res=full",
                    "caminho absoluto": "source_id=C%3A%5Cx&t=1&res=full",
                    "sem source_id": "t=1&res=full",
                    "sem t": "source_id=main&res=full",
                    "sem res": "source_id=main&t=1",
                    "campo a mais": "source_id=main&t=1&res=full&x=1",
                    "resolução inválida": "source_id=main&t=1&res=1/5",
                    "t negativo": "source_id=main&t=-1&res=full",
                    "t depois do fim": "source_id=main&t=30.5&res=full",
                    "t de voice depois do fim": "source_id=voice&t=10.5&res=full",
                    "t com expoente": "source_id=main&t=1e1&res=full",
                    "t NaN": "source_id=main&t=nan&res=full",
                    "t com 33 caracteres": f"source_id=main&t={'1' * 33}&res=full",
                }
                for route in ("stream", "frame"):
                    for name, query in refused.items():
                        with self.subTest(route=route, caso=name):
                            response = client.get(f"/api/source/{route}?{query}")
                            self.assertEqual((response.status, response.code), (400, "ARGUMENT_INVALID"), response.body)
                            for secret in (str(workdir), "outra", "MAIN", "plan.json"):
                                self.assertNotIn(secret, response.body.decode("utf-8"))
                self.assertEqual(client.request("POST", "/api/source/stream?source_id=main&t=1&res=full",
                                                headers={"Origin": f"http://127.0.0.1:{harness.port}"}).status, 405)
                self.assertEqual(self.recorder.arguments, [])
                # O limite exato da fonte é aceito, no último quadro.
                response = client.get("/api/source/frame?source_id=main&t=30&res=full")
                self.assertEqual((response.status, response.headers["x-editor-start-s"]), (200, "29.960000"))


# --- Aprovação pela interface (T6) ------------------------------------------------------------------


APPROVE_FIELDS = ("plan_sha256", "sources_fingerprint")
STALE_MESSAGE = "O plano mudou desde que foi carregado. Revise a versão atual antes de aprovar."


class ApprovalApiTests(UiTestCase):
    """`POST /api/approve`, a situação da aprovação no estado e a recusa de tudo que não é a versão exibida."""

    def setUp(self) -> None:
        self.workdir_context = ui_workdir()
        self.workdir = self.workdir_context.__enter__()
        self.addCleanup(self.workdir_context.__exit__, None, None, None)
        self.install(self.workdir, simple_plan())
        self.approval_path = self.workdir / "approval.json"

    # --- Auxiliares ---

    def state(self, client: Client) -> dict[str, Any]:
        """`data` de `GET /api/state`."""

        response = client.get("/api/state")
        self.assertEqual(response.status, 200, response.body)
        return response.json["data"]

    def shown(self, client: Client) -> dict[str, str]:
        """Os dois valores que a tela devolve ao aprovar: `plan_sha256` e `sources_fingerprint` da versão exibida."""

        plan = self.state(client)["plan"]
        return {name: plan[name] for name in APPROVE_FIELDS}

    def approval(self, client: Client) -> dict[str, Any]:
        """Situação da aprovação que o estado informa."""

        return self.state(client)["approval"]

    def post(self, harness: Harness, client: Client, body: Any, *, headers: dict[str, str] | None = None) -> Response:
        """`POST /api/approve` com a origem própria (a menos que `headers` a troque) e o corpo (bytes ou JSON)."""

        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        merged = {"Origin": f"http://127.0.0.1:{harness.port}", **(headers or {})}
        return client.request("POST", "/api/approve", body=payload, headers=merged)

    def assert_nothing_written(self) -> None:
        """Não há `approval.json` nem sobra de publicação em `.tmp`."""

        self.assertFalse(self.approval_path.exists())
        self.assertEqual([entry.name for entry in (self.workdir / ".tmp").glob(".approval-*")], [])

    def edit_plan(self) -> None:
        """Muda o plano em disco (o primeiro corte fica 2 s mais curto), o que muda o hash canônico."""

        edited = simple_plan()
        edited["segments"][0]["video"]["out_s"] = 3
        edited["segments"][0]["audio"]["tracks"][0]["out_s"] = 3
        self.write_plan(self.workdir, edited)

    def await_approval(self, client: Client, state: str) -> dict[str, Any]:
        """Espera o estado informar a situação `state` da aprovação (a vigia e a conferência das fontes são assíncronas)."""

        return wait_until(
            lambda: (lambda value: value if value["state"] == state else None)(self.approval(client)),
            timeout_s=15, message=f"a aprovação não ficou {state}",
        )

    # --- A rota ---

    def test_approving_publishes_the_v2_record_and_the_real_render_full_accepts_it(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            shown = self.shown(client)
            self.assertEqual(self.approval(client), {"state": "none", "approved_at": None})
            response = self.post(harness, client, shown)
            self.assertEqual(response.status, 200, response.body)
            data = response.json["data"]
            self.assertEqual(response.json["command"], "ui approve")
            self.assertEqual(set(data), {"approved_at", "plan_sha256"})
            self.assertEqual(data["plan_sha256"], shown["plan_sha256"])
            # O registro é o v2 exato, com o SHA-256 completo de cada fonte, e não cita a preview.
            record = json.loads(self.approval_path.read_text(encoding="utf-8"))
            self.assertEqual(set(record), {"schema_version", "approved_at", "plan_sha256", "sources"})
            self.assertEqual(record["schema_version"], 2)
            self.assertEqual((record["approved_at"], record["plan_sha256"]), (data["approved_at"], shown["plan_sha256"]))
            self.assertEqual(
                record["sources"],
                [{"source_id": "main", "sha256": hashlib.sha256((self.workdir / "sources" / "main.mp4").read_bytes()).hexdigest()}],
            )
            # O caminho do arquivo e o do trabalho nunca saem pela API.
            for secret in (str(self.workdir), self.workdir.name, "approval.json", "artifact_path", "\\\\"):
                self.assertNotIn(secret, response.body.decode("utf-8"))
            # O estado fica em dia na hora, sem calcular o SHA-256 das fontes (a própria rota já o conferiu).
            with patch.object(ui_approval, "verify_approval", side_effect=AssertionError("não deve hashear as fontes")):
                self.assertEqual(self.approval(client), {"state": "current", "approved_at": data["approved_at"]})
            # A barreira da full aceita o registro, e a CLI real renderiza a full com ele (sem preview).
            _, validation = load_validated_plan(self.workdir)
            self.assertEqual(verify_approval(self.workdir, validation)["approved_at"], data["approved_at"])
        self.assertFalse((self.workdir / "preview.mp4").exists())
        result, envelope = run_cli("render", "full", "--workdir", str(self.workdir), "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(envelope["ok"])
        self.assertTrue((self.workdir / "full.mp4").is_file())

    def test_a_plan_edited_after_loading_is_refused_without_touching_the_current_approval(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            shown = self.shown(client)
            self.assertEqual(self.post(harness, client, shown).status, 200)
            before = (self.approval_path.read_bytes(), self.approval_path.stat().st_mtime_ns)
            self.edit_plan()
            # O servidor compara com o plano em disco na hora do clique, sem esperar a vigia perceber a edição.
            refused = self.post(harness, client, shown)
            self.assertEqual((refused.status, refused.code), (409, "APPROVAL_PLAN_CHANGED"), refused.body)
            self.assertEqual(refused.json["error"]["message"], STALE_MESSAGE)
            self.assertEqual((self.approval_path.read_bytes(), self.approval_path.stat().st_mtime_ns), before)
            self.assertEqual([entry.name for entry in (self.workdir / ".tmp").glob(".approval-*")], [])

    def test_a_plan_edited_after_loading_creates_no_file_when_nothing_was_approved(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            shown = self.shown(client)
            self.edit_plan()
            refused = self.post(harness, client, shown)
            self.assertEqual((refused.status, refused.code), (409, "APPROVAL_PLAN_CHANGED"), refused.body)
            self.assert_nothing_written()
            # A versão recarregada, essa sim, é aprovada.
            reloaded = wait_until(
                lambda: (lambda value: value if value != shown else None)(self.shown(client)),
                timeout_s=10, message="o hash não mudou",
            )
            self.assertEqual(self.post(harness, client, reloaded).status, 200)

    def test_a_source_changed_after_loading_is_refused_and_the_reloaded_version_is_approved(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            shown = self.shown(client)
            source = self.workdir / "sources" / "main.mp4"
            metadata = source.stat()
            # Mesmo plano, mesmo conteúdo, outro `mtime_ns`: a impressão leve diverge e a aprovação é recusada.
            os.utime(source, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 5_000_000_000))
            refused = self.post(harness, client, shown)
            self.assertEqual((refused.status, refused.code), (409, "APPROVAL_PLAN_CHANGED"), refused.body)
            self.assertEqual(refused.json["error"]["message"], STALE_MESSAGE)
            self.assert_nothing_written()
            # Depois da recarga a tela devolve a impressão nova e a aprovação passa.
            reloaded = wait_until(
                lambda: (lambda value: value if value != shown else None)(self.shown(client)),
                timeout_s=10, message="a impressão não mudou",
            )
            self.assertEqual(reloaded["plan_sha256"], shown["plan_sha256"])
            self.assertEqual(self.post(harness, client, reloaded).status, 200)

    def test_an_invalid_plan_or_a_missing_source_at_the_click_is_refused_with_a_public_code(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            shown = self.shown(client)
            (self.workdir / "plan.json").write_text("{ nada", encoding="utf-8")
            invalid = self.post(harness, client, shown)
            self.assertEqual((invalid.status, invalid.code), (409, "PLAN_INVALID"), invalid.body)
            self.write_plan(self.workdir, simple_plan())
            (self.workdir / "sources" / "main.mp4").unlink()
            missing = self.post(harness, client, shown)
            self.assertEqual(missing.status, 409, missing.body)
            self.assertIn(missing.code, {"PLAN_INVALID", "SOURCE_INVALID"})
            for response in (invalid, missing):
                for secret in (str(self.workdir), "main.mp4", "Traceback"):
                    self.assertNotIn(secret, response.body.decode("utf-8"))
            self.assert_nothing_written()

    def test_a_post_without_session_origin_or_with_a_body_outside_the_allowlist_is_refused(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            good = self.shown(client)
            digest_a = "a" * 64
            origin = {"Origin": f"http://127.0.0.1:{harness.port}"}
            body = json.dumps(good).encode()
            # Sessão e origem: o cookie é conferido antes de tudo; a origem própria é obrigatória no POST.
            no_cookie = client.request("POST", "/api/approve", body=body, headers=origin, cookie=False)
            self.assertEqual((no_cookie.status, no_cookie.code), (401, "UI_TOKEN_INVALID"))
            wrong_cookie = client.request(
                "POST", "/api/approve", body=body, headers={**origin, "Cookie": f"{harness.server.cookie}={'0' * 43}"}, cookie=False
            )
            self.assertEqual((wrong_cookie.status, wrong_cookie.code), (401, "UI_TOKEN_INVALID"))
            no_origin = client.request("POST", "/api/approve", body=body)
            self.assertEqual((no_origin.status, no_origin.code), (403, "UI_ORIGIN_INVALID"))
            for foreign in ("http://evil.test", "null", f"http://127.0.0.1:{harness.port + 1}", f"https://127.0.0.1:{harness.port}"):
                with self.subTest(origin=foreign):
                    response = self.post(harness, client, good, headers={"Origin": foreign})
                    self.assertEqual((response.status, response.code), (403, "UI_ORIGIN_INVALID"))
            wrong_host = client.request("POST", "/api/approve", body=body, headers={**origin, "Host": "evil.test"})
            self.assertEqual((wrong_host.status, wrong_host.code), (403, "UI_HOST_INVALID"))
            # Corpo fora da allowlist: campo a mais ou a menos, formato, tipo, JSON, tamanho e ausência de corpo.
            for name, payload in {
                "campo a mais": {**good, "extra": digest_a},
                "caminho no lugar": {**good, "path": "approval.json"},
                "sem a impressão": {"plan_sha256": good["plan_sha256"]},
                "sem o hash": {"sources_fingerprint": good["sources_fingerprint"]},
                "hash curto": {**good, "plan_sha256": "a" * 63},
                "hash com maiúscula": {**good, "plan_sha256": "A" * 64},
                "hash não hexadecimal": {**good, "sources_fingerprint": "z" * 64},
                "valor numérico": {**good, "plan_sha256": 7},
                "valor nulo": {**good, "sources_fingerprint": None},
                "lista": [good],
                "JSON inválido": b"{nao e json",
                "chave duplicada": b'{"plan_sha256":"%s","plan_sha256":"%s","sources_fingerprint":"%s"}' % ((digest_a.encode(),) * 3),
                "acima de 1 KiB": {**good, "folga": "x" * 1100},
                "vazio": b"",
            }.items():
                with self.subTest(name):
                    response = self.post(harness, client, payload)
                    self.assertEqual((response.status, response.code), (400, "ARGUMENT_INVALID"), response.body)
            query = client.request("POST", "/api/approve?x=1", body=body, headers=origin)
            self.assertEqual((query.status, query.code), (400, "ARGUMENT_INVALID"))
            no_length = raw_request(
                harness.port,
                (
                    f"POST /api/approve HTTP/1.0\r\nHost: 127.0.0.1:{harness.port}\r\nCookie: {client.cookie}\r\n"
                    f"Origin: http://127.0.0.1:{harness.port}\r\n\r\n"
                ).encode(),
            )
            self.assertIn(b" 400 ", no_length.split(b"\r\n", 1)[0] + b" ")
            chunked = raw_request(
                harness.port,
                (
                    f"POST /api/approve HTTP/1.0\r\nHost: 127.0.0.1:{harness.port}\r\nCookie: {client.cookie}\r\n"
                    f"Origin: http://127.0.0.1:{harness.port}\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
                ).encode(),
            )
            self.assertIn(b" 400 ", chunked.split(b"\r\n", 1)[0] + b" ")
            # Só POST: as outras formas da rota são recusadas, e nada disso gravou a aprovação.
            refused_get = client.request("GET", "/api/approve", headers=origin)
            self.assertEqual((refused_get.status, refused_get.code), (405, "ARGUMENT_INVALID"))
            self.assertEqual(refused_get.headers["allow"], "POST")
            for method in ("PUT", "DELETE"):
                response = client.request(method, "/api/approve", headers=origin)
                self.assertEqual((response.status, response.code), (405, "ARGUMENT_INVALID"), method)
            self.assert_nothing_written()
            # A sessão e o servidor seguem de pé, e o clique legítimo ainda passa.
            self.assertEqual(self.post(harness, client, good).status, 200)

    # --- Situação da aprovação no estado ---

    def test_the_state_reports_none_current_and_stale_and_hashes_only_when_the_version_changes(self) -> None:
        with patch.object(ui_approval, "verify_approval", wraps=ui_approval.verify_approval) as verify, \
                serving(self.workdir) as harness:
            client = harness.client()
            self.assertEqual(self.approval(client), {"state": "none", "approved_at": None})
            # Aprovação de fora da interface (como `editor approve` pela CLI): confere as fontes uma vez.
            published = approve_workdir(self.workdir)
            current = self.await_approval(client, "current")
            self.assertEqual(current["approved_at"], published["approved_at"])
            for _ in range(5):
                self.assertEqual(self.approval(client), current)
            self.assertEqual(verify.call_count, 1)
            # Plano editado depois de aprovado: desatualizada, sem tocar nas fontes (o hash do plano já difere).
            self.edit_plan()
            stale = self.await_approval(client, "stale")
            self.assertEqual(stale["approved_at"], None)
            self.assertEqual(verify.call_count, 1)
            # Plano de volta ao aprovado: em dia de novo (a conferência das fontes é refeita para esta versão).
            self.write_plan(self.workdir, simple_plan())
            self.assertEqual(self.await_approval(client, "current")["approved_at"], published["approved_at"])
            self.assertEqual(verify.call_count, 2)
            # Plano inválido em disco: nenhuma aprovação vale para ele, e ao corrigi-lo o registro volta a valer.
            (self.workdir / "plan.json").write_text("{ nada", encoding="utf-8")
            wait_until(lambda: self.state(client)["state"] == "outdated", timeout_s=10, message="o plano não ficou inválido")
            self.assertEqual(self.approval(client), {"state": "stale", "approved_at": None})
            self.write_plan(self.workdir, simple_plan())
            self.assertEqual(self.await_approval(client, "current")["approved_at"], published["approved_at"])

    def test_a_source_with_other_content_makes_the_approval_stale(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            published = approve_workdir(self.workdir)
            self.assertEqual(self.await_approval(client, "current")["approved_at"], published["approved_at"])
            before = self.state(client)["plan"]["sources_fingerprint"]
            create_gop_source(self.workdir / "replacement.mp4", gop=25)
            source = self.workdir / "sources" / "main.mp4"
            wait_until(lambda: PlanStateTests.try_replace(self.workdir / "replacement.mp4", source), message="não foi possível trocar a fonte")
            wait_until(lambda: self.state(client)["plan"]["sources_fingerprint"] != before, timeout_s=10, message="a impressão não mudou")
            # O plano é o mesmo e a fonte é outra: só o SHA-256 do conteúdo percebe.
            self.assertEqual(self.await_approval(client, "stale")["approved_at"], None)

    def test_an_obsolete_or_corrupted_record_is_stale(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            version_one = {
                "schema_version": 1, "approved_at": "2026-10-01T10:00:00Z", "plan_sha256": "a" * 64,
                "preview_sha256": "b" * 64, "sources": [],
            }
            nested = "[" * 30000 + "]" * 30000  # estoura a recursão do decodificador de JSON
            for name, content in (
                ("v1", json.dumps(version_one)), ("JSON inválido", "{ nada"), ("lista", "[1, 2, 3, 4, 5, 6, 7, 8]"),
                ("aninhado", nested),
            ):
                with self.subTest(name):
                    self.approval_path.write_text(content, encoding="utf-8")
                    self.assertEqual(self.approval(client), {"state": "stale", "approved_at": None})
            self.approval_path.unlink()
            self.assertEqual(self.approval(client), {"state": "none", "approved_at": None})

    def test_the_state_stays_responsive_while_the_sources_are_being_checked(self) -> None:
        entered, release = threading.Event(), threading.Event()
        real = ui_approval.verify_approval

        def slow(*arguments: Any, **options: Any) -> dict[str, Any]:
            """A conferência real, retida até o teste liberar."""

            entered.set()
            release.wait(timeout=20)
            return real(*arguments, **options)

        with patch.object(ui_approval, "verify_approval", slow), serving(self.workdir) as harness:
            client = harness.client()
            published = approve_workdir(self.workdir)
            started = time.monotonic()
            self.assertEqual(self.approval(client), {"state": "checking", "approved_at": None})
            self.assertTrue(entered.wait(timeout=10))
            # Com a conferência em andamento o estado continua respondendo e só uma conferência roda.
            for _ in range(3):
                self.assertEqual(self.approval(client)["state"], "checking")
            self.assertLess(time.monotonic() - started, 10)
            release.set()
            self.assertEqual(self.await_approval(client, "current")["approved_at"], published["approved_at"])

    def test_the_page_declares_the_accessible_approval_dialog_and_its_module(self) -> None:
        with serving(self.workdir) as harness:
            client = harness.client()
            page = client.get("/").body.decode("utf-8")
            self.assertRegex(
                page,
                r'<dialog id="approve-dialog"[^>]*role="dialog"[^>]*aria-modal="true"[^>]*aria-labelledby="approve-title"',
            )
            for marker in ('id="btn-approve"', 'id="approve-title"', 'id="approve-cancel"', 'id="approve-confirm"', 'id="approval-seal"'):
                self.assertIn(marker, page)
            module = client.get("/static/approval.js")
            self.assertEqual((module.status, module.headers["content-type"].split(";")[0]), (200, "text/javascript"))
            self.assertIn('import "./approval.js"', client.get("/static/app.js").body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
