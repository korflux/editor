"""Testes da preview com proxies (`--proxy auto|off`, phase 4, T4).

Cobre A5 (segunda metade), A6, A7 e C2 da spec: a preview lê o proxy válido de cada fonte e o
original das demais, informa `data.proxy`, ignora proxy obsoleto, mantém o canvas derivado do
original e nunca contamina aprovação ou full; `--proxy` fora da allowlist é `ARGUMENT_INVALID`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies, process
from editor.contracts import CliError
from editor.integrity.approval import approve_workdir
from editor.render.full import render_full
from editor.render.preview import render_preview
from tests.test_proxy_build import make_source, run_module, touch_source, write_plan
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def framemd5(path: Path) -> list[str]:
    """Resume quadros e áudio decodificados por pacote, sem o cabeçalho, para comparar saídas."""

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-f", "framemd5", "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line and not line.startswith("#")]


@contextmanager
def spy_ffmpeg_inputs() -> Iterator[list[list[str]]]:
    """Registra os `-i` de cada FFmpeg executado, delegando ao `run_tool` real."""

    calls: list[list[str]] = []
    real_run_tool = process.run_tool

    def recording(tool, arguments, *, timeout_seconds):
        """Anota as entradas do FFmpeg e executa a ferramenta de verdade."""

        if tool == "ffmpeg":
            calls.append([arguments[i + 1] for i, value in enumerate(arguments) if value == "-i"])
        return real_run_tool(tool, arguments, timeout_seconds=timeout_seconds)

    with patch.object(process, "run_tool", recording):
        yield calls


class PreviewProxyTests(unittest.TestCase):
    """Valida a escolha de proxy por fonte na preview e o isolamento da full e da aprovação."""

    @classmethod
    def setUpClass(cls) -> None:
        """Gera uma vez duas fontes 1080p elegíveis a proxy, uma com `start_time` deslocado."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        cls.media = PROJECT_ROOT / f"tmp-preview-proxy-media-{uuid4().hex}"
        cls.media.mkdir()
        make_source(cls.media / "a.mp4", "1920x1080", offset_s=0.5)
        make_source(cls.media / "b.mp4", "1920x1080")

    @classmethod
    def tearDownClass(cls) -> None:
        """Remove as fontes geradas para a classe."""

        remove_tree(cls.media)

    @contextmanager
    def workdir(self) -> Iterator[Path]:
        """Cria um trabalho descartável com as fontes `a` e `b` (mesmo `mtime`, via copy2) e plano."""

        root = PROJECT_ROOT / f"tmp-preview-proxy-{uuid4().hex}"
        (root / "sources").mkdir(parents=True)
        try:
            for source_id in ("a", "b"):
                shutil.copy2(self.media / f"{source_id}.mp4", root / "sources" / f"{source_id}.mp4")
            write_plan(root, ["a", "b"])
            yield root
        finally:
            remove_tree(root)

    def build_proxy(self, workdir: Path, *source_ids: str) -> None:
        """Roda `proxy build` real só para as fontes pedidas e exige proxy criado."""

        extra = [argument for source_id in source_ids for argument in ("--source-id", source_id)]
        result = run_module("proxy", "build", "--workdir", str(workdir), *extra, "--json")
        envelope = json.loads(result.stdout)
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual({item["status"] for item in envelope["data"]["proxies"]}, {"created"})

    def preview_cli(self, workdir: Path, *extra: str) -> dict:
        """Roda `render preview` pela CLI real e devolve o envelope JSON."""

        result = run_module("render", "preview", "--workdir", str(workdir), *extra, "--json")
        return json.loads(result.stdout)

    def journey(self, workdir: Path) -> dict:
        """Preview auto -> approve -> full em processo, registrando os `-i` de cada render."""

        with spy_ffmpeg_inputs() as preview_inputs:
            preview = render_preview(workdir)
        approve_workdir(workdir)
        with spy_ffmpeg_inputs() as full_inputs:
            full = render_full(workdir)
        return {
            "preview": preview,
            "preview_inputs": preview_inputs,
            "full_inputs": full_inputs,
            "approval": json.loads((workdir / "approval.json").read_text(encoding="utf-8")),
            "full_frames": framemd5(workdir / "full.mp4"),
            "full": full,
        }

    def test_journey_with_and_without_proxy_keeps_approval_and_full(self) -> None:
        """A6/A7: proxy só em `a` muda a leitura da preview, nunca a aprovação nem a full."""

        with self.workdir() as plain_dir, self.workdir() as proxy_dir:
            self.build_proxy(proxy_dir, "a")
            proxy_video = proxy_dir / "proxy" / "a.mp4"
            plain = self.journey(plain_dir)
            proxied = self.journey(proxy_dir)

            # Escolha por fonte: `a` pelo proxy, `b` (sem proxy) pelo original.
            self.assertEqual(plain["preview"]["proxy"], {"mode": "auto", "sources": [], "stale": []})
            self.assertEqual(proxied["preview"]["proxy"], {"mode": "auto", "sources": ["a"], "stale": []})
            preview_inputs = {Path(path).resolve() for call in proxied["preview_inputs"] for path in call}
            self.assertIn(proxy_video.resolve(), preview_inputs)
            self.assertIn((proxy_dir / "sources" / "b.mp4").resolve(), preview_inputs)
            self.assertNotIn((proxy_dir / "sources" / "a.mp4").resolve(), preview_inputs)

            # Canvas derivado do original validado: mesma dimensão com e sem proxy (proxy é 1280x720).
            for key in ("width", "height", "frame_rate"):
                self.assertEqual(proxied["preview"]["video"][key], plain["preview"]["video"][key])
            self.assertEqual((plain["preview"]["video"]["width"], plain["preview"]["video"]["height"]), (704, 396))

            # Aprovação v2: plano e originais idênticos; a preview (com ou sem proxy) não entra no registro.
            for result in (plain, proxied):
                self.assertEqual(result["approval"]["schema_version"], 2)
                self.assertNotIn("preview_sha256", result["approval"])
            self.assertEqual(proxied["approval"]["plan_sha256"], plain["approval"]["plan_sha256"])
            self.assertEqual(proxied["approval"]["sources"], plain["approval"]["sources"])

            # Full parte só dos originais, mesmo com proxy válido presente, e sai idêntica.
            full_inputs = {Path(path).resolve() for call in proxied["full_inputs"] for path in call}
            self.assertTrue(full_inputs)
            self.assertNotIn(proxy_video.resolve(), full_inputs)
            self.assertTrue(all("proxy" not in path.parts for path in full_inputs))
            self.assertNotIn("proxy", proxied["full"])
            self.assertEqual(proxied["full_frames"], plain["full_frames"])
            self.assertTrue(any(line.startswith("1,") for line in plain["full_frames"]), "full sem áudio")

    def test_off_and_absent_proxies_reproduce_the_preview_without_proxy(self) -> None:
        """A7: `--proxy off` com proxy válido presente lê só originais e reproduz a preview sem proxy."""

        with self.workdir() as plain_dir, self.workdir() as proxy_dir:
            self.build_proxy(proxy_dir, "a", "b")
            absent = self.preview_cli(plain_dir)
            self.assertTrue(absent["ok"], absent)
            self.assertEqual(absent["data"]["proxy"], {"mode": "auto", "sources": [], "stale": []})
            with spy_ffmpeg_inputs() as calls:
                off = render_preview(proxy_dir, proxy="off")
            self.assertEqual(off["proxy"], {"mode": "off", "sources": [], "stale": []})
            self.assertTrue(all("proxy" not in Path(path).parts for call in calls for path in call))
            self.assertEqual(framemd5(proxy_dir / "preview.mp4"), framemd5(plain_dir / "preview.mp4"))

    def test_stale_proxy_is_reported_and_original_is_read(self) -> None:
        """A5/A6: fonte alterada após o build torna o proxy obsoleto; a preview volta ao original."""

        with self.workdir() as workdir:
            self.build_proxy(workdir, "a", "b")
            touch_source(workdir / "sources" / "b.mp4")
            envelope = self.preview_cli(workdir)
            self.assertTrue(envelope["ok"], envelope)
            self.assertEqual(envelope["command"], "render preview")
            self.assertEqual(envelope["data"]["proxy"], {"mode": "auto", "sources": ["a"], "stale": ["b"]})
            with spy_ffmpeg_inputs() as calls:
                render_preview(workdir)
            inputs = {Path(path).resolve() for call in calls for path in call}
            self.assertIn((workdir / "sources" / "b.mp4").resolve(), inputs)
            self.assertNotIn((workdir / "proxy" / "b.mp4").resolve(), inputs)

    def test_invalid_proxy_value_is_argument_invalid(self) -> None:
        """C2: só `auto` e `off`; a full não aceita `--proxy`; nada é renderizado."""

        with self.workdir() as workdir:
            for arguments in (
                ("render", "preview", "--workdir", str(workdir), "--proxy", "on", "--json"),
                ("preview", "--workdir", str(workdir), "--proxy", "AUTO", "--json"),
                ("render", "full", "--workdir", str(workdir), "--proxy", "off", "--json"),
            ):
                result = run_module(*arguments)
                envelope = json.loads(result.stdout)
                self.assertFalse(envelope["ok"], arguments)
                self.assertEqual(envelope["error"]["code"], "ARGUMENT_INVALID", arguments)
                self.assertNotEqual(result.returncode, 0)
            self.assertFalse((workdir / "preview.mp4").exists())
            with self.assertRaises(CliError) as context:
                render_preview(workdir, proxy="on")
            self.assertEqual(context.exception.code, "ARGUMENT_INVALID")


if __name__ == "__main__":
    unittest.main()
