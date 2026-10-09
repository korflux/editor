"""Testes do `editor proxy build` (phase 4, T3): jornada real, verificação e falhas por fonte.

Cobre A4, A5 e C4 da spec: criação de proxy H.264 até 1280x720 com timestamps preservados,
reuso sem tocar nos arquivos, obsolescência por fonte alterada ou metadado fora do formato,
falhas por fonte sem publicar nem remover proxy anterior e `.tmp` sempre limpo.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies, process
from editor.contracts import CliError
from editor.render import proxy as proxy_module
from editor.render.proxy import build_proxies, lookup_proxy
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para comprovar o envelope público do comando."""

    return subprocess.run(
        [sys.executable, "-B", "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def make_source(path: Path, size: str, offset_s: float = 0.0) -> None:
    """Gera fonte H.264/AAC curta por lavfi; `offset_s` desloca o `start_time` do original."""

    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30000/1001:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-bf", "2", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-output_ts_offset", str(offset_s), str(path),
        ],
        check=True,
        capture_output=True,
    )


def probe_video(path: Path) -> dict[str, str]:
    """Lê codec, formato de pixel, dimensões e tempos do primeiro stream de vídeo."""

    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height,start_time,duration,nb_read_packets",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["streams"][0]


def write_plan(workdir: Path, source_ids: list[str]) -> None:
    """Escreve um plano v2 mínimo com um segmento de 1 s por fonte de vídeo."""

    plan = {
        "schema_version": 2,
        "profile": {"aspect_ratio": "16:9"},
        "sources": [
            {"id": source_id, "kind": "video", "path": f"sources/{source_id}.mp4"}
            for source_id in source_ids
        ],
        "segments": [
            {
                "video": {"source_id": source_id, "in_s": 0, "out_s": 1, "fit": "preserve"},
                "audio": {
                    "mode": "keep",
                    "tracks": [{"source_id": source_id, "in_s": 0, "out_s": 1, "gain_db": 0}],
                },
            }
            for source_id in source_ids
        ],
        "cuts": [],
        "layers": [],
        "audio_processing": {"normalize_loudness": False},
    }
    (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")


def file_state(path: Path) -> tuple[int, str]:
    """Resume um arquivo por `mtime_ns` e SHA-256 para provar que nada foi tocado."""

    return path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest()


def tmp_entries(workdir: Path) -> list[str]:
    """Lista o que sobrou em `.tmp`; o contrato exige pasta limpa após qualquer desfecho."""

    temporary = workdir / ".tmp"
    return sorted(os.listdir(temporary)) if temporary.is_dir() else []


def touch_source(path: Path) -> None:
    """Altera só o `mtime_ns` da fonte, o bastante para tornar o proxy obsoleto."""

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


class ProxyBuildTests(unittest.TestCase):
    """Valida o comando `proxy build` e a leitura de validade consumida pela preview."""

    @classmethod
    def setUpClass(cls) -> None:
        """Gera uma vez as fontes 1080p (com `start_time` deslocado) e 360p usadas nos trabalhos."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        cls.media = PROJECT_ROOT / f"tmp-proxy-media-{uuid4().hex}"
        cls.media.mkdir()
        make_source(cls.media / "big.mp4", "1920x1080", offset_s=0.5)
        make_source(cls.media / "small.mp4", "640x360")

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove as fontes geradas para a classe."""

        remove_tree(cls.media)

    @contextmanager
    def workdir(self, sources: dict[str, str]) -> Iterator[Path]:
        """Cria um trabalho descartável com cópias das fontes e plano; `sources` mapeia id -> fonte."""

        root = PROJECT_ROOT / f"tmp-proxy-build-{uuid4().hex}"
        (root / "sources").mkdir(parents=True)
        try:
            for source_id, name in sources.items():
                shutil.copy2(self.media / name, root / "sources" / f"{source_id}.mp4")
            write_plan(root, list(sources))
            yield root
        finally:
            remove_tree(root)

    def build_cli(self, workdir: Path, *extra: str) -> dict:
        """Roda `proxy build` pela CLI real e devolve o envelope JSON."""

        result = run_module("proxy", "build", "--workdir", str(workdir), *extra, "--json")
        return json.loads(result.stdout)

    def test_journey_create_reuse_stale_and_rebuild(self) -> None:
        """A4/A5: cria, reutiliza sem tocar, refaz após fonte alterada e trata erros de argumento."""

        with self.workdir({"big": "big.mp4", "small": "small.mp4"}) as workdir:
            video = workdir / "proxy" / "big.mp4"
            metadata = workdir / "proxy" / "big.json"
            source = workdir / "sources" / "big.mp4"

            # 1. Criação: só a fonte maior que 1280x720 recebe proxy verificado.
            envelope = self.build_cli(workdir)
            self.assertTrue(envelope["ok"], envelope)
            self.assertEqual(envelope["command"], "proxy build")
            big, small = envelope["data"]["proxies"]
            self.assertEqual(
                small,
                {"source_id": "small", "status": "skipped", "reason": "NOT_ELIGIBLE",
                 "width": None, "height": None, "size_bytes": None},
            )
            self.assertEqual((big["source_id"], big["status"]), ("big", "created"))
            self.assertNotIn("reason", big)
            self.assertEqual((big["width"], big["height"]), (1280, 720))
            self.assertEqual(big["size_bytes"], video.stat().st_size)
            self.assertFalse((workdir / "proxy" / "small.mp4").exists())
            self.assertEqual(tmp_entries(workdir), [])

            original, produced = probe_video(source), probe_video(video)
            self.assertEqual(
                (produced["codec_name"], produced["pix_fmt"], produced["width"], produced["height"]),
                ("h264", "yuv420p", 1280, 720),
            )
            self.assertEqual(produced["nb_read_packets"], original["nb_read_packets"])
            self.assertAlmostEqual(float(produced["start_time"]), 0.5, delta=0.001)
            self.assertAlmostEqual(float(produced["start_time"]), float(original["start_time"]), delta=0.001)
            self.assertAlmostEqual(float(produced["duration"]), float(original["duration"]), delta=1001 / 30000)

            document = json.loads(metadata.read_text(encoding="utf-8"))
            self.assertEqual(
                set(document),
                {"version", "source_id", "source_size_bytes", "source_mtime_ns",
                 "proxy_size_bytes", "width", "height"},
            )
            self.assertEqual(document["source_mtime_ns"], source.stat().st_mtime_ns)
            self.assertEqual(lookup_proxy(workdir, "big", source).state, "valid")
            self.assertEqual(lookup_proxy(workdir, "small", workdir / "sources" / "small.mp4").state, "absent")

            # 2. Reuso: nenhum arquivo do proxy é tocado.
            before = (file_state(video), file_state(metadata))
            envelope = self.build_cli(workdir)
            self.assertEqual(
                envelope["data"]["proxies"][0],
                {"source_id": "big", "status": "reused", "width": 1280, "height": 720,
                 "size_bytes": big["size_bytes"]},
            )
            self.assertEqual((file_state(video), file_state(metadata)), before)

            # 3. Fonte alterada: proxy fica obsoleto e a execução seguinte o refaz.
            touch_source(source)
            self.assertEqual(lookup_proxy(workdir, "big", source).state, "stale")
            envelope = self.build_cli(workdir, "--source-id", "big")
            self.assertEqual([item["status"] for item in envelope["data"]["proxies"]], ["created"])
            self.assertEqual(lookup_proxy(workdir, "big", source).state, "valid")
            self.assertEqual(tmp_entries(workdir), [])

            # 4. Metadado fora do formato conta como obsoleto, nunca como erro.
            valid_metadata = metadata.read_bytes()
            invalid_documents = {
                "campo extra": {**document, "source_mtime_ns": source.stat().st_mtime_ns, "extra": 1},
                "tipo errado": {**document, "source_mtime_ns": source.stat().st_mtime_ns, "width": "1280"},
                "versao booleana": {**document, "source_mtime_ns": source.stat().st_mtime_ns, "version": True},
                "acima de 4 KiB": {"padding": "x" * 5000},
            }
            for label, invalid in invalid_documents.items():
                with self.subTest(metadado=label):
                    metadata.write_text(json.dumps(invalid), encoding="utf-8")
                    self.assertEqual(lookup_proxy(workdir, "big", source).state, "stale")
            with self.subTest(metadado="json invalido"):
                metadata.write_bytes(b"{nao json")
                self.assertEqual(lookup_proxy(workdir, "big", source).state, "stale")
            metadata.write_bytes(valid_metadata)
            self.assertEqual(lookup_proxy(workdir, "big", source).state, "valid")
            metadata.write_text(json.dumps({**document, "extra": 1}), encoding="utf-8")
            envelope = self.build_cli(workdir, "--source-id", "big")
            self.assertEqual(envelope["data"]["proxies"][0]["status"], "created")

            # 5. Erros de comando com códigos estáveis e sem efeitos colaterais.
            cases = {
                "id fora da allowlist": (("--source-id", "Big!"), "ARGUMENT_INVALID"),
                "id ausente do plano": (("--source-id", "ghost"), "ARGUMENT_INVALID"),
            }
            for label, (extra, code) in cases.items():
                with self.subTest(erro=label):
                    envelope = self.build_cli(workdir, *extra)
                    self.assertFalse(envelope["ok"])
                    self.assertEqual(envelope["error"]["code"], code)
            with self.subTest(erro="workdir relativo"):
                envelope = json.loads(
                    run_module("proxy", "build", "--workdir", "relativo", "--json").stdout
                )
                self.assertEqual(envelope["error"]["code"], "WORKDIR_INVALID")
            with self.subTest(erro="plano invalido"):
                (workdir / "plan.json").write_text("{}", encoding="utf-8")
                envelope = self.build_cli(workdir)
                self.assertEqual(envelope["error"]["code"], "PLAN_INVALID")

    def test_only_small_sources_succeed_with_all_skipped(self) -> None:
        """Sem fonte elegível o comando tem sucesso e não cria a pasta de proxies."""

        with self.workdir({"small": "small.mp4"}) as workdir:
            self.assertEqual(
                build_proxies(workdir)["proxies"],
                [{"source_id": "small", "status": "skipped", "reason": "NOT_ELIGIBLE",
                  "width": None, "height": None, "size_bytes": None}],
            )
            self.assertFalse((workdir / "proxy").exists())

    def test_failures_keep_previous_proxy_and_do_not_stop_other_sources(self) -> None:
        """A5/C4: FFmpeg, timeout e verificação reprovada viram `failed` sem publicar nada."""

        real_run_tool = process.run_tool
        real_probe = proxy_module._probe_video_stream

        def ffmpeg_fails(tool, arguments, *, timeout_seconds):
            """Simula FFmpeg com código de saída não zero só para a fonte `first`."""

            if tool == "ffmpeg" and any("first.mp4" in str(item) for item in arguments):
                return subprocess.CompletedProcess(arguments, 1, "", "erro interno")
            return real_run_tool(tool, arguments, timeout_seconds=timeout_seconds)

        def ffmpeg_times_out(tool, arguments, *, timeout_seconds):
            """Simula timeout depois de o FFmpeg já ter criado saída parcial em `.tmp`."""

            if tool == "ffmpeg" and any("first.mp4" in str(item) for item in arguments):
                Path(arguments[-1]).write_bytes(b"parcial")
                raise process.ProcessExecutionError("ffmpeg", timed_out=True)
            return real_run_tool(tool, arguments, timeout_seconds=timeout_seconds)

        def proxy_loses_packet(path, duration_seconds):
            """Reprova a verificação da `first` com um pacote a menos no proxy temporário."""

            measured = real_probe(path, duration_seconds)
            if path.parent.name == ".tmp" and "first" in path.name:
                return replace(measured, packets=measured.packets - 1)
            return measured

        scenarios = {
            "FFMPEG_FAILED": patch.object(proxy_module.process, "run_tool", ffmpeg_fails),
            "TIMEOUT": patch.object(proxy_module.process, "run_tool", ffmpeg_times_out),
            "STREAM_MISMATCH": patch.object(proxy_module, "_probe_video_stream", proxy_loses_packet),
        }
        with self.workdir({"first": "big.mp4", "second": "big.mp4"}) as workdir:
            source = workdir / "sources" / "first.mp4"
            build_proxies(workdir, ["first"])
            touch_source(source)
            previous = [file_state(workdir / "proxy" / name) for name in ("first.mp4", "first.json")]

            for reason, patcher in scenarios.items():
                with self.subTest(motivo=reason):
                    (workdir / "proxy" / "second.mp4").unlink(missing_ok=True)
                    with patcher:
                        proxies = build_proxies(workdir)["proxies"]
                    self.assertEqual(
                        proxies[0],
                        {"source_id": "first", "status": "failed", "reason": reason,
                         "width": None, "height": None, "size_bytes": None},
                    )
                    self.assertEqual(proxies[1]["status"], "created")
                    self.assertEqual(
                        [file_state(workdir / "proxy" / name) for name in ("first.mp4", "first.json")],
                        previous,
                    )
                    self.assertEqual(lookup_proxy(workdir, "first", source).state, "stale")
                    self.assertEqual(tmp_entries(workdir), [])

            with self.subTest(desfecho="excecao inesperada"):
                def explode(tool, arguments, *, timeout_seconds):
                    """Simula falha não prevista no meio do encode; FFprobe segue real."""

                    if tool != "ffmpeg":
                        return real_run_tool(tool, arguments, timeout_seconds=timeout_seconds)
                    Path(arguments[-1]).write_bytes(b"parcial")
                    raise RuntimeError("falha inesperada")

                with patch.object(proxy_module.process, "run_tool", explode):
                    with self.assertRaises(RuntimeError):
                        build_proxies(workdir, ["first"])
                self.assertEqual(tmp_entries(workdir), [])

            with self.subTest(erro="DEPENDENCY_MISSING"):
                real_resolve = dependencies.resolve_executable
                with patch.object(
                    proxy_module.dependencies,
                    "resolve_executable",
                    lambda name: None if name == "ffmpeg" else real_resolve(name),
                ):
                    with self.assertRaises(CliError) as caught:
                        build_proxies(workdir, ["first"])
                self.assertEqual(caught.exception.code, "DEPENDENCY_MISSING")


if __name__ == "__main__":
    unittest.main()
