"""Benchmark local do render: relógio e CPU consumida por variante, com repetições intercaladas.

Uso: `.venv\\Scripts\\python.exe -B scripts\\bench_render.py --scenario windows|proxy|live --reps 3`.

Cada cenário gera suas fontes num diretório temporário do sistema (apagado ao terminar),
grava um `plan.json` e roda `render preview` uma vez por variante a cada repetição, em
processos filhos separados e com a ordem das variantes alternada entre repetições para
diluir efeitos de cache de disco e aquecimento. A primeira variante do cenário é a base
do ganho percentual.

A CPU consumida soma o processo filho e todos os seus descendentes (FFmpeg e FFprobe):
no Windows por um Job Object (contabilidade do kernel), nos demais sistemas por
`RUSAGE_CHILDREN`. Assim a medição vale mesmo com outros renders na máquina, como pede
o `REGRAS.md`. Não usa dependência fora da biblioteca padrão.

Cenários: `windows` compara a preview sem e com janelas de decode; `proxy` constrói o proxy
uma vez (medido à parte como `setup`, fora da comparação) e compara a preview sem e com
proxy, ambas com janelas; `live` (T1 da phase 7, critério C3) mede o stream ao vivo da montagem
(`render/live.py`): o tempo até o primeiro fragmento (o primeiro quadro que o `<video>` pode
mostrar) no início, no meio e no fim de uma timeline de 60 s, em 1/2 com proxy e em `full`, e a
reprodução de 30 s em 1/2 com proxy contra o tempo real. Não é comparação com uma base: cada
variante é uma métrica própria e o veredito do C3 sai no fim da tabela.

Ponto de extensão: novos cenários entram em `SCENARIOS` com sua preparação, etapas de
`setup` e variantes; cada variante é uma ação de `VARIANTS` executada no processo filho.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any


# --- Variantes executadas no processo filho ---------------------------------------------------


@contextmanager
def _windows_off() -> Iterator[None]:
    """Força o render a montar só o grafo sem janelas, reproduzindo o comando anterior à phase 4."""

    from unittest.mock import patch

    from editor.render import preview

    original = preview.build_ffmpeg_graph

    def plain_graph(document: Any, validation: Any, *, canvas_width: int, canvas_height: int, windows: bool = False) -> Any:
        return original(document, validation, canvas_width=canvas_width, canvas_height=canvas_height)

    with patch.object(preview, "build_ffmpeg_graph", side_effect=plain_graph):
        yield


def _preview_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Resumo da preview impresso pelo filho: como o grafo decodificou e quais proxies leu."""

    return {"decode": result["decode"], "accel": result["video"]["accel"], "proxy": result["proxy"]}


def _preview_without_windows(workdir: Path) -> dict[str, Any]:
    """Preview com o grafo anterior à phase 4 (sem janelas); base do cenário `windows`."""

    from editor.render.preview import render_preview

    with _windows_off():
        return _preview_summary(render_preview(workdir))


def _preview_default(workdir: Path) -> dict[str, Any]:
    """Preview como a CLI a executa hoje (janelas ligadas, `--proxy auto`)."""

    from editor.render.preview import render_preview

    return _preview_summary(render_preview(workdir))


def _preview_proxy_off(workdir: Path) -> dict[str, Any]:
    """Preview com janelas lendo só os originais (`--proxy off`); base do cenário `proxy`."""

    from editor.render.preview import render_preview

    return _preview_summary(render_preview(workdir, proxy="off"))


def _preview_proxy_on(workdir: Path) -> dict[str, Any]:
    """Preview com janelas lendo o proxy (`--proxy auto`); falha se nenhuma fonte o usou.

    A checagem impede que um proxy obsoleto faça o benchmark medir o original duas vezes.
    """

    from editor.render.preview import render_preview

    summary = _preview_summary(render_preview(workdir, proxy="auto"))
    if not summary["proxy"]["sources"]:
        raise SystemExit(f"a preview não leu nenhum proxy: {summary['proxy']}")
    return summary


def _proxy_build(workdir: Path) -> dict[str, Any]:
    """Constrói os proxies do trabalho como `editor proxy build`; exige ao menos um `created`."""

    from editor.render.proxy import build_proxies

    result = build_proxies(workdir)
    if not any(item["status"] == "created" for item in result["proxies"]):
        raise SystemExit(f"nenhum proxy foi criado: {result['proxies']}")
    return result


LIVE_POSITIONS_S = {"inicio": 0.0, "meio": 30.0, "fim": 57.5}
"""Instantes medidos da timeline de 60 s: o começo, o meio (na fronteira de um trecho) e o fim (dentro do último)."""

LIVE_FIRST_FRAME_LIMIT_S = 3.0
"""C3: o primeiro quadro aparece em até 3 s depois do pedido, em qualquer posição."""


def _read_fragmented_mp4(stream: Any, started: float) -> dict[str, float]:
    """Lê o MP4 fragmentado do stdout e mede o primeiro fragmento completo e o fim.

    O primeiro `mdat` completo é o ponto em que o `<video>` já tem um quadro para mostrar: o `moov`
    vazio sai antes e cada fragmento só fecha quando todas as amostras dele existem. Devolve os
    segundos desde `started` até esse ponto (`first_frame_s`) e até o fim do stream (`total_s`),
    mais o número de fragmentos e de bytes.
    """

    import struct

    def read_exact(size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = stream.read(size - len(data))
            if not chunk:
                break
            data += chunk
        return data

    first_frame_s = 0.0
    fragments = 0
    total_bytes = 0
    while True:
        header = read_exact(8)
        if len(header) < 8:
            break
        size, kind = struct.unpack(">I4s", header)
        payload = size - 8
        if size == 1:  # tamanho de 64 bits logo depois do tipo
            size = struct.unpack(">Q", read_exact(8))[0]
            payload = size - 16
        read_exact(payload)
        total_bytes += size
        if kind == b"mdat":
            fragments += 1
            if fragments == 1:
                first_frame_s = time.perf_counter() - started
    return {
        "first_frame_s": first_frame_s,
        "total_s": time.perf_counter() - started,
        "fragments": float(fragments),
        "bytes": float(total_bytes),
    }


def _live_stream(workdir: Path, start_s: float, resolution: str) -> dict[str, Any]:
    """Pede o stream ao vivo como o servidor da T3 o fará e lê até o fim, medindo o primeiro quadro.

    O plano já validado fica fora do relógio (o servidor o mantém em cache); contam o grafo, o
    arquivo de filtro, o processo do FFmpeg e a chegada do primeiro fragmento.
    """

    from editor import dependencies
    from editor.plan.validation import load_validated_plan
    from editor.render import live

    document, validation = load_validated_plan(workdir)
    started = time.perf_counter()
    state = live.prepare_live(workdir, document, validation, start_s, resolution)
    with live.filter_script(workdir, state) as script:
        process = subprocess.Popen(
            [str(dependencies.resolve_executable("ffmpeg")), *live.stream_arguments(state, script)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            metrics = _read_fragmented_mp4(process.stdout, started)
            error = process.stderr.read().decode(errors="replace")
        finally:
            process.kill()
            process.wait()
    if process.returncode != 0 and not metrics["fragments"]:
        raise SystemExit(f"o stream ao vivo falhou:\n{error[-2000:]}")
    return {
        **{key: round(value, 3) for key, value in metrics.items()},
        "start_s": state.start_s,
        "media_s": state.duration_s,
        "resolution": resolution,
        "proxy_sources": list(state.proxy_sources),
    }


def _live_variant(position: str, resolution: str) -> Callable[[Path], dict[str, Any]]:
    """Variante do cenário `live`: o stream de uma posição da timeline numa resolução."""

    return lambda workdir: _live_stream(workdir, LIVE_POSITIONS_S[position], resolution)


VARIANTS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "sem_janelas": _preview_without_windows,
    "com_janelas": _preview_default,
    "sem_proxy": _preview_proxy_off,
    "com_proxy": _preview_proxy_on,
    "proxy_build": _proxy_build,
    "live_inicio": _live_variant("inicio", "1/2"),
    "live_meio": _live_variant("meio", "1/2"),
    "live_fim": _live_variant("fim", "1/2"),
    "live_full_inicio": _live_variant("inicio", "full"),
    "live_full_meio": _live_variant("meio", "full"),
    "live_full_fim": _live_variant("fim", "full"),
}
"""Ações conhecidas pelo processo filho; o nome é o rótulo impresso na tabela."""


def _child(workdir: Path, variant: str) -> int:
    """Processo filho: espera o pai vinculá-lo à contabilidade de CPU e executa a ação medida."""

    sys.stdin.readline()  # sincronia: nada roda antes de o pai registrar este processo no Job Object
    print(json.dumps(VARIANTS[variant](workdir)))
    return 0


# --- Medição de CPU do processo filho e descendentes ------------------------------------------


@dataclass(frozen=True)
class Measurement:
    """Uma execução medida: relógio e CPU (usuário + sistema) do filho e descendentes, em segundos."""

    wall_s: float
    cpu_s: float
    summary: dict[str, Any]


def _run_windows_job(command: list[str]) -> tuple[subprocess.CompletedProcess[str], float]:
    """Roda o filho dentro de um Job Object e devolve o processo e a CPU total do job."""

    import ctypes
    from ctypes import wintypes

    class Accounting(ctypes.Structure):
        """JOBOBJECT_BASIC_ACCOUNTING_INFORMATION; tempos em unidades de 100 ns."""

        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW falhou")
    child = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    handle = None
    try:
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, child.pid)  # SET_QUOTA | TERMINATE
        if not handle or not kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)):
            child.kill()
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject falhou")
        stdout, stderr = child.communicate("\n")
        info = Accounting()
        if not kernel32.QueryInformationJobObject(
            wintypes.HANDLE(job), 1, ctypes.byref(info), ctypes.sizeof(info), None
        ):
            raise OSError(ctypes.get_last_error(), "QueryInformationJobObject falhou")
        cpu_s = (info.TotalUserTime + info.TotalKernelTime) / 10_000_000
    finally:
        if handle:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
        kernel32.CloseHandle(wintypes.HANDLE(job))
    return subprocess.CompletedProcess(command, child.returncode, stdout, stderr), cpu_s


def _run_posix(command: list[str]) -> tuple[subprocess.CompletedProcess[str], float]:
    """Roda o filho e mede a CPU dos descendentes aguardados por `RUSAGE_CHILDREN`."""

    import resource

    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = subprocess.run(command, input="\n", capture_output=True, text=True, encoding="utf-8", check=False)
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_s = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    return result, cpu_s


def measure(workdir: Path, variant: str) -> Measurement:
    """Executa uma variante num processo filho e mede relógio e CPU consumida."""

    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--child", "--workdir", str(workdir), "--variant", variant]
    runner = _run_windows_job if os.name == "nt" else _run_posix
    started = time.perf_counter()
    result, cpu_s = runner(command)
    wall_s = time.perf_counter() - started
    if result.returncode != 0:
        raise SystemExit(f"render da variante {variant} falhou:\n{result.stderr[-2000:]}")
    return Measurement(wall_s=wall_s, cpu_s=cpu_s, summary=json.loads(result.stdout.strip().splitlines()[-1]))


# --- Cenários ------------------------------------------------------------------------------


def _ffmpeg(arguments: list[str], timeout_s: float) -> None:
    """Executa o FFmpeg local com argumentos estruturados, sem shell."""

    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *arguments], capture_output=True, text=True, timeout=timeout_s, check=False)
    if result.returncode != 0:
        raise SystemExit(f"FFmpeg falhou ao preparar o cenário:\n{result.stderr[-2000:]}")


def _write_4k_source(workdir: Path) -> None:
    """Gera a fonte 4K H.264 de GOP 1 s com 60 s (~700 MB) em `sources/main.mp4`.

    O ruído temporal mantém o bitrate perto de 90 Mbps, como uma filmagem real, em vez
    do vídeo sintético quase sem informação que o encoder comprimiria a poucos MB.
    """

    (workdir / "sources").mkdir(parents=True)
    _ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=30:duration=60",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=60",
            "-vf", "noise=alls=12:allf=t",
            "-c:v", "libx264", "-preset", "veryfast", "-g", "30", "-pix_fmt", "yuv420p",
            "-b:v", "90M", "-maxrate", "90M", "-bufsize", "180M",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
            "-y", str(workdir / "sources" / "main.mp4"),
        ],
        timeout_s=1800,
    )


def _write_plan(workdir: Path, starts: tuple[int, ...]) -> None:
    """Grava um plano de trechos de 5 s da fonte `main`, um por `start`, com o áudio da própria fonte."""

    segments = [
        {
            "video": {"source_id": "main", "in_s": start, "out_s": start + 5, "fit": "preserve"},
            "audio": {"mode": "keep", "tracks": [{"source_id": "main", "in_s": start, "out_s": start + 5, "gain_db": 0}]},
        }
        for start in starts
    ]
    document = {
        "schema_version": 2,
        "profile": {"aspect_ratio": "16:9"},
        "sources": [{"id": "main", "kind": "video", "path": "sources/main.mp4"}],
        "segments": segments,
        "cuts": [],
        "layers": [],
        "audio_processing": {
            "normalize_loudness": False,
            "target_lufs": -16,
            "limiter": {"enabled": False, "ceiling_dbfs": -1},
            "ducking": False,
        },
    }
    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")


def _prepare_windows(root: Path) -> Path:
    """Cenário C1: fonte 4K H.264 de GOP 1 s com 60 s (~700 MB) e plano com 6 trechos de 5 s."""

    workdir = root / "job"
    _write_4k_source(workdir)
    _write_plan(workdir, (2, 12, 22, 32, 42, 52))
    return workdir


def _prepare_live(root: Path) -> Path:
    """Cenário do stream ao vivo: a mesma fonte 4K e uma timeline de 60 s, 12 trechos contíguos de 5 s."""

    workdir = root / "job"
    _write_4k_source(workdir)
    _write_plan(workdir, tuple(range(0, 60, 5)))
    return workdir


@dataclass(frozen=True)
class Scenario:
    """Cenário de benchmark: prepara o trabalho e lista as variantes, a primeira é a base do ganho.

    `setup` lista ações medidas uma vez, depois da preparação e antes das variantes, com custo
    reportado à parte (ex. o `proxy build`, que não entra na comparação das previews).
    """

    prepare: Callable[[Path], Path]
    variants: tuple[str, ...]
    description: str
    setup: tuple[str, ...] = ()
    live: bool = False


SCENARIOS: dict[str, Scenario] = {
    "windows": Scenario(
        prepare=_prepare_windows,
        variants=("sem_janelas", "com_janelas"),
        description="C1: 4K H.264 GOP 1 s, 60 s, 6 trechos de 5 s, preview CPU",
    ),
    "proxy": Scenario(
        prepare=_prepare_windows,
        variants=("sem_proxy", "com_proxy"),
        description="C1: 4K H.264 GOP 1 s, 60 s, 6 trechos de 5 s, preview CPU com janelas, sem e com proxy",
        setup=("proxy_build",),
    ),
    "live": Scenario(
        prepare=_prepare_live,
        variants=(
            "live_inicio", "live_meio", "live_fim",
            "live_full_inicio", "live_full_meio", "live_full_fim",
        ),
        description="stream ao vivo (C3): 4K H.264 GOP 1 s, timeline de 60 s, 1/2 com proxy e full com originais",
        setup=("proxy_build",),
        live=True,
    ),
}


# --- Execução e relatório -----------------------------------------------------------------


def run_scenario(name: str, reps: int) -> dict[str, Any]:
    """Prepara o cenário, mede as variantes intercaladas e devolve medianas e ganho percentual."""

    scenario = SCENARIOS[name]
    root = Path(tempfile.mkdtemp(prefix="editor-bench-"))
    try:
        workdir = scenario.prepare(root)
        setup: dict[str, Measurement] = {}
        for action in scenario.setup:
            setup[action] = measure(workdir, action)
            print(f"setup {action}: relogio {setup[action].wall_s:.2f} s, cpu {setup[action].cpu_s:.2f} s", file=sys.stderr)
        runs: dict[str, list[Measurement]] = {variant: [] for variant in scenario.variants}
        for rep in range(reps):
            # Ordem alternada: a variante que roda primeiro não leva sempre o disco frio.
            order = scenario.variants if rep % 2 == 0 else tuple(reversed(scenario.variants))
            for variant in order:
                runs[variant].append(measure(workdir, variant))
                last = runs[variant][-1]
                print(f"rep {rep + 1} {variant}: relogio {last.wall_s:.2f} s, cpu {last.cpu_s:.2f} s", file=sys.stderr)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    base = scenario.variants[0]
    report: dict[str, Any] = {
        "scenario": name,
        "description": scenario.description,
        "reps": reps,
        "setup": {
            action: {"wall_s": round(item.wall_s, 3), "cpu_s": round(item.cpu_s, 3), "summary": item.summary}
            for action, item in setup.items()
        },
        "variants": {},
    }
    for variant, measurements in runs.items():
        report["variants"][variant] = {
            "wall_s": [round(item.wall_s, 3) for item in measurements],
            "cpu_s": [round(item.cpu_s, 3) for item in measurements],
            "wall_median_s": round(statistics.median(item.wall_s for item in measurements), 3),
            "cpu_median_s": round(statistics.median(item.cpu_s for item in measurements), 3),
            "summary": measurements[-1].summary,
        }
    base_wall = report["variants"][base]["wall_median_s"]
    base_cpu = report["variants"][base]["cpu_median_s"]
    for variant, data in report["variants"].items():
        data["wall_gain_pct"] = round((1 - data["wall_median_s"] / base_wall) * 100, 1)
        data["cpu_gain_pct"] = round((1 - data["cpu_median_s"] / base_cpu) * 100, 1)
        # Razão frente à base; a meta do proxy é relógio da preview com proxy <= 0,5 da sem proxy.
        data["wall_ratio"] = round(data["wall_median_s"] / base_wall, 3)
        data["cpu_ratio"] = round(data["cpu_median_s"] / base_cpu, 3)
    return live_report(report, runs) if scenario.live else report


def live_report(report: dict[str, Any], runs: dict[str, list[Measurement]]) -> dict[str, Any]:
    """Acrescenta ao relatório as execuções e medianas do stream ao vivo e o veredito do C3.

    As métricas de cada execução vêm do `summary` que o filho imprimiu (`_live_stream`). O C3 vale
    para 1/2 com proxy: primeiro quadro em até 3 s no início, no meio e no fim, e os 30 s do meio
    (do instante 30 s ao fim) produzidos em menos de 30 s de relógio.
    """

    medians = {
        variant: {
            "first_frames": [item.summary["first_frame_s"] for item in measurements],
            "totals": [item.summary["total_s"] for item in measurements],
            "first_frame_median_s": round(statistics.median(item.summary["first_frame_s"] for item in measurements), 3),
            "total_median_s": round(statistics.median(item.summary["total_s"] for item in measurements), 3),
            "media_s": measurements[-1].summary["media_s"],
            "proxy_sources": measurements[-1].summary["proxy_sources"],
        }
        for variant, measurements in runs.items()
    }
    half = {name: medians[f"live_{name}"] for name in LIVE_POSITIONS_S}
    report["live"] = {
        "medians": medians,
        "c3_first_frame_ok": all(
            item["first_frame_median_s"] <= LIVE_FIRST_FRAME_LIMIT_S and item["proxy_sources"] for item in half.values()
        ),
        "c3_realtime_ok": half["meio"]["total_median_s"] < half["meio"]["media_s"],
        "c3_realtime_speed": round(half["meio"]["media_s"] / half["meio"]["total_median_s"], 1),
    }
    return report


def print_live_table(report: dict[str, Any]) -> None:
    """Imprime o tempo até o primeiro quadro e o tempo de produção por posição, e o veredito do C3."""

    print(f"cenario {report['scenario']}: {report['description']} ({report['reps']} execucoes)")
    for action, data in report["setup"].items():
        print(f"setup {action} (uma vez): relogio {data['wall_s']:.2f} s, cpu {data['cpu_s']:.2f} s")
    print(f"{'variante':<18}{'primeiro quadro (s)':<26}{'mediana':>9}   {'stream inteiro (s)':<26}{'mediana':>9}   midia (s)  proxy")
    for variant, stats in report["live"]["medians"].items():
        first = ", ".join(f"{value:.2f}" for value in stats["first_frames"])
        total = ", ".join(f"{value:.2f}" for value in stats["totals"])
        print(
            f"{variant:<18}{first:<26}{stats['first_frame_median_s']:>9.2f}   {total:<26}{stats['total_median_s']:>9.2f}"
            f"   {stats['media_s']:>8.1f}  {'sim' if stats['proxy_sources'] else 'nao'}"
        )
    live = report["live"]
    print(
        f"C3 (1/2 com proxy): primeiro quadro em ate {LIVE_FIRST_FRAME_LIMIT_S:g} s no inicio, meio e fim: "
        f"{'ok' if live['c3_first_frame_ok'] else 'FALHOU'}; "
        f"30 s do meio produzidos mais rapido que o tempo real: {'ok' if live['c3_realtime_ok'] else 'FALHOU'} "
        f"({live['c3_realtime_speed']}x)"
    )


def print_table(report: dict[str, Any]) -> None:
    """Imprime a tabela legível com setup, execuções, medianas, ganho e razão frente à variante base."""

    print(f"cenario {report['scenario']}: {report['description']} ({report['reps']} execucoes intercaladas)")
    for action, data in report["setup"].items():
        print(f"setup {action} (uma vez, fora da comparacao): relogio {data['wall_s']:.2f} s, cpu {data['cpu_s']:.2f} s")
    print(f"{'variante':<14}{'relogio (s)':<28}{'mediana':>9}{'ganho':>8}   {'cpu (s)':<28}{'mediana':>9}{'ganho':>8}   razao rel/cpu")
    for variant, data in report["variants"].items():
        wall = ", ".join(f"{value:.2f}" for value in data["wall_s"])
        cpu = ", ".join(f"{value:.2f}" for value in data["cpu_s"])
        print(
            f"{variant:<14}{wall:<28}{data['wall_median_s']:>9.2f}{data['wall_gain_pct']:>7.1f}%   "
            f"{cpu:<28}{data['cpu_median_s']:>9.2f}{data['cpu_gain_pct']:>7.1f}%"
            f"   razao {data['wall_ratio']:.3f} / {data['cpu_ratio']:.3f}"
        )


def main() -> int:
    """Ponto de entrada: modo pai (benchmark) ou modo filho (um render medido)."""

    parser = argparse.ArgumentParser(description="Mede relógio e CPU do render por variante.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="windows")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="imprime só o relatório JSON")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--workdir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--variant", choices=sorted(VARIANTS), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return _child(args.workdir, args.variant)
    if not 1 <= args.reps <= 20:
        parser.error("--reps deve ficar entre 1 e 20")
    report = run_scenario(args.scenario, args.reps)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        (print_live_table if SCENARIOS[args.scenario].live else print_table)(report)
        print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
