"""Testes do preset de qualidade da full (`render full --quality`, decisão 20 e aceite A14).

Cobre o contrato em quatro camadas: a tabela de presets e o vetor de argumentos do encoder
(sem mídia), a recusa de valores fora da allowlist pela CLI real, a independência do preset em
relação à aprovação e à preview, e a ordem de qualidade medida em renders reais (tamanho e SSIM
contra a fonte), na CPU e, quando existir NVENC, na GPU.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies, process
from editor.contracts import EXIT_CODES, CliError
from editor.render.ffmpeg_graph import FfmpegGraph
from editor.render.full import render_full
from editor.render.preview import (
    DEFAULT_QUALITY,
    QUALITY_CHOICES,
    QUALITY_PRESETS,
    _build_ffmpeg_arguments,
    _quality_preset,
    _video_encoder_arguments,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Vetores de encoder de antes da T9, escritos à mão: o `standard` e a omissão têm de ser iguais a eles.
LEGACY_CPU_ENCODER = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
LEGACY_GPU_ENCODER = [
    "-c:v", "h264_nvenc", "-preset", "p3", "-rc", "vbr", "-cq", "31", "-b:v", "0", "-pix_fmt", "yuv420p",
]

# Margens mínimas entre degraus, bem abaixo do medido na fonte sintética de 640x360 (tamanho 1,5x a 3x e
# SSIM +0,005 a +0,025): o teste prova a ordem, não valores absolutos que variam com a versão do encoder.
MIN_SIZE_RATIO = 1.25
MIN_SSIM_STEP = 0.002


def run_module(*arguments: str, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess[str]:
    """Executa `python -m editor` no ponto de entrada real, como o agente o chama."""

    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def remove_tree(path: Path) -> None:
    """Remove a pasta descartável com algumas tentativas: no Windows o handle do último render demora a soltar."""

    for _ in range(10):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(0.2)


@contextmanager
def workspace_tempdir(prefix: str = "tmp-render-quality") -> Iterator[Path]:
    """Pasta descartável dentro do workspace (padrão dos testes), removida ao final."""

    path = PROJECT_ROOT / f"{prefix}-{uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        remove_tree(path)


def synthetic_arguments(accel: str, quality: str | None) -> list[str]:
    """Comando completo do FFmpeg para uma fonte de vídeo sintética, sem executar nada."""

    graph = FfmpegGraph("x", "v", "a", ("s0",), True)
    records = [{"id": "s0", "kind": "video", "path": "s0.bin"}]
    extra = {} if quality is None else {"quality": quality}
    return _build_ffmpeg_arguments(
        graph,
        records,
        Path("out.mp4"),
        filter_script=Path("filter.txt"),
        duration_seconds=1.0,
        frame_rate=30.0,
        accel=accel,
        **extra,
    )


class PresetTableTests(unittest.TestCase):
    """A tabela de presets e o vetor de argumentos do encoder, sem mídia."""

    def test_ladder_is_declared_once_and_rises_in_quality(self) -> None:
        self.assertEqual(QUALITY_CHOICES, ("standard", "high", "max"))
        self.assertEqual(tuple(QUALITY_PRESETS), QUALITY_CHOICES)
        self.assertEqual(DEFAULT_QUALITY, "standard")
        crfs = [QUALITY_PRESETS[name].crf for name in QUALITY_CHOICES]
        cqs = [QUALITY_PRESETS[name].cq for name in QUALITY_CHOICES]
        # Menor `crf` e menor `cq` são mais qualidade: degraus estritos em ambos os encoders.
        self.assertEqual(crfs, sorted(crfs, reverse=True))
        self.assertEqual(cqs, sorted(cqs, reverse=True))
        self.assertEqual(len(set(crfs)), len(crfs))
        self.assertEqual(len(set(cqs)), len(cqs))
        # Valores da decisão 20, escritos à parte para uma troca de constante não passar calada.
        self.assertEqual((QUALITY_PRESETS["standard"].crf, QUALITY_PRESETS["standard"].cq), (23, 31))
        self.assertIsNone(QUALITY_PRESETS["standard"].nvenc_level)

    def test_standard_and_omission_keep_the_pre_phase_5_encoder_arguments(self) -> None:
        """A14: a GPU mantém o mesmo vetor de argumentos e a CPU o mesmo `-crf`, com e sem o nome do preset."""

        self.assertEqual(_video_encoder_arguments("cpu"), LEGACY_CPU_ENCODER)
        self.assertEqual(_video_encoder_arguments("cpu", "standard"), LEGACY_CPU_ENCODER)
        self.assertEqual(_video_encoder_arguments("gpu"), LEGACY_GPU_ENCODER)
        self.assertEqual(_video_encoder_arguments("gpu", "standard"), LEGACY_GPU_ENCODER)
        # Comando inteiro (entradas, filtro, áudio, container): omitir é igual a pedir `standard`.
        for accel in ("cpu", "gpu"):
            with self.subTest(accel=accel):
                self.assertEqual(synthetic_arguments(accel, None), synthetic_arguments(accel, "standard"))

    def test_high_and_max_change_only_the_rate_parameter_and_the_nvenc_level(self) -> None:
        for name in ("high", "max"):
            preset = QUALITY_PRESETS[name]
            with self.subTest(preset=name):
                expected_cpu = list(LEGACY_CPU_ENCODER)
                expected_cpu[expected_cpu.index("-crf") + 1] = str(preset.crf)
                self.assertEqual(_video_encoder_arguments("cpu", name), expected_cpu)
                expected_gpu = list(LEGACY_GPU_ENCODER)
                expected_gpu[expected_gpu.index("-cq") + 1] = str(preset.cq)
                # O nível entra antes do formato de pixel e só nos presets acima do padrão.
                self.assertIsNotNone(preset.nvenc_level)
                expected_gpu[expected_gpu.index("-pix_fmt"):expected_gpu.index("-pix_fmt")] = ["-level", preset.nvenc_level]
                self.assertEqual(_video_encoder_arguments("gpu", name), expected_gpu)

    def test_command_carries_the_requested_preset_to_the_encoder(self) -> None:
        cpu = synthetic_arguments("cpu", "max")
        self.assertEqual(cpu[cpu.index("-crf") + 1], str(QUALITY_PRESETS["max"].crf))
        self.assertNotIn("-level", cpu)
        gpu = synthetic_arguments("gpu", "high")
        self.assertEqual(gpu[gpu.index("-cq") + 1], str(QUALITY_PRESETS["high"].cq))
        self.assertEqual(gpu[gpu.index("-level") + 1], QUALITY_PRESETS["high"].nvenc_level)
        self.assertEqual(gpu[gpu.index("-c:v") + 1], "h264_nvenc")

    def test_values_outside_the_allowlist_are_refused_without_being_echoed(self) -> None:
        for value in ("ultra", "", "HIGH", " high", "high ", "high;rm", None, 3, ["high"]):
            with self.subTest(value=value):
                with self.assertRaises(CliError) as raised:
                    _quality_preset(value)  # type: ignore[arg-type]
                self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")
                self.assertEqual(raised.exception.exit_code, EXIT_CODES["ARGUMENT"])
                # A mensagem é fixa: nenhum valor recebido aparece nela.
                if isinstance(value, str) and value.strip():
                    self.assertNotIn(value.strip(), raised.exception.message)
        with self.assertRaises(CliError) as raised:
            _video_encoder_arguments("cpu", "ultra")
        self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")

    def test_render_full_checks_the_allowlist_before_touching_the_workdir(self) -> None:
        """Valor inválido falha como `ARGUMENT_INVALID` mesmo num trabalho que nem existe (nada é lido nem criado)."""

        with workspace_tempdir() as parent:
            missing = parent / "nao-existe"
            with self.assertRaises(CliError) as raised:
                render_full(missing, quality="ultra")
            self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")
            self.assertFalse(missing.exists())
            self.assertEqual(list(parent.iterdir()), [])


class QualityCliTests(unittest.TestCase):
    """A borda da CLI: valores inválidos e a preview, que não recebe a opção."""

    def test_invalid_values_are_refused_by_the_real_cli_without_rendering(self) -> None:
        invocations = (
            ("render", "full", "--quality", "ultra"),
            ("render", "full", "--quality", ""),
            ("render", "full", "--quality", "HIGH"),
            ("render", "full", "--quality"),
            ("full", "--quality", "ultra"),
            ("full", "--quality", ""),
        )
        for arguments in invocations:
            with self.subTest(arguments=arguments), workspace_tempdir() as workdir:
                result = run_module(*arguments, "--workdir", str(workdir), "--json")
                envelope = json.loads(result.stdout)

                self.assertEqual(result.returncode, EXIT_CODES["ARGUMENT"], result.stdout)
                self.assertEqual(result.stderr, "")
                self.assertFalse(envelope["ok"])
                self.assertEqual(envelope["error"]["code"], "ARGUMENT_INVALID")
                self.assertEqual(envelope["error"]["exit_code"], EXIT_CODES["ARGUMENT"])
                self.assertNotIn("Traceback", result.stdout)
                self.assertNotIn("ultra", result.stdout.lower())
                # Nada foi lido nem renderizado: o trabalho continua vazio.
                self.assertEqual(list(workdir.iterdir()), [])

    def test_preview_does_not_accept_the_quality_option(self) -> None:
        for arguments in (("render", "preview", "--quality", "high"), ("preview", "--quality", "high")):
            with self.subTest(arguments=arguments), workspace_tempdir() as workdir:
                result = run_module(*arguments, "--workdir", str(workdir), "--json")
                envelope = json.loads(result.stdout)

                self.assertEqual(result.returncode, EXIT_CODES["ARGUMENT"], result.stdout)
                self.assertEqual(envelope["error"]["code"], "ARGUMENT_INVALID")
                self.assertNotIn("Traceback", result.stdout)
                self.assertEqual(list(workdir.iterdir()), [])

    def test_help_lists_the_option_only_on_the_full(self) -> None:
        for arguments in (("render", "full", "--help"), ("full", "--help")):
            with self.subTest(arguments=arguments):
                result = run_module(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--quality", result.stdout)
                for name in QUALITY_CHOICES:
                    self.assertIn(name, result.stdout)
        for arguments in (("render", "preview", "--help"), ("preview", "--help")):
            with self.subTest(arguments=arguments):
                result = run_module(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("--quality", result.stdout)


def tool_path(name: str) -> str:
    """Executável local do FFmpeg ou do FFprobe, como o editor o resolve."""

    resolved = dependencies.resolve_executable(name)
    if resolved is None:
        raise unittest.SkipTest(f"{name} local é necessário")
    return str(resolved)


def framemd5(path: Path) -> str:
    """Hash do conteúdo decodificado (todos os quadros de vídeo e áudio), independente do muxer."""

    output = subprocess.run(
        [tool_path("ffmpeg"), "-v", "error", "-i", str(path), "-map", "0", "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    body = "\n".join(line for line in output.splitlines() if not line.startswith("#"))
    return hashlib.sha256(body.encode()).hexdigest()


def sha256(path: Path) -> str:
    """Hash do arquivo, para provar que preview e aprovação não mudaram."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def ssim_against(encoded: Path, reference: Path, seconds: float) -> float:
    """SSIM médio (`All`) do vídeo codificado contra o início da fonte, pelo filtro `ssim` do FFmpeg.

    A fonte é limitada a `seconds` (a duração do trecho renderizado): sem isso o filtro repete o último
    quadro codificado contra os quadros restantes da fonte e o SSIM cai por pareamento, não por qualidade.
    """

    result = subprocess.run(
        [tool_path("ffmpeg"), "-hide_banner", "-i", str(encoded), "-t", str(seconds), "-i", str(reference),
         "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    match = re.search(r"All:([0-9.]+)", result.stderr)
    assert match is not None, result.stderr
    return float(match.group(1))


class QualityRenderTests(unittest.TestCase):
    """Renders reais com uma fonte sintética exigente (mandelbrot e testsrc2) de 640x360 e 3 s."""

    SOURCE_NAME = "main.mp4"
    SEGMENT_OUT_S = 2.5

    @classmethod
    def setUpClass(cls) -> None:
        for name in ("ffmpeg", "ffprobe"):
            if dependencies.resolve_executable(name) is None:
                raise unittest.SkipTest(f"{name} local é necessário")
        cls.directory = PROJECT_ROOT / f"tmp-render-quality-fixtures-{uuid4().hex}"
        cls.directory.mkdir()
        cls.addClassCleanup(remove_tree, cls.directory)
        cls.source = cls.directory / cls.SOURCE_NAME
        # Fonte H.264 de alta qualidade (crf 6) com detalhe fractal e movimento: exige bits dos presets.
        subprocess.run(
            [tool_path("ffmpeg"), "-v", "error", "-y",
             "-f", "lavfi", "-i", "mandelbrot=s=640x360:r=30:end_scale=0.4",
             "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
             "-filter_complex",
             "[0:v]format=yuv420p[a];[1:v]format=yuv420p[b];[a][b]blend=all_mode=average,format=yuv420p[v]",
             "-map", "[v]", "-map", "2:a", "-t", "3",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "6", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", str(cls.source)],
            check=True,
        )

    @contextmanager
    def approved_workdir(self) -> Iterator[Path]:
        """Trabalho com plano, fonte, preview e aprovação vigentes, pronto para `render full`."""

        with workspace_tempdir() as workdir:
            (workdir / "sources").mkdir()
            shutil.copy2(self.source, workdir / "sources" / self.SOURCE_NAME)
            end = self.SEGMENT_OUT_S
            plan = {
                "schema_version": 2,
                "profile": {"aspect_ratio": "16:9"},
                "sources": [{"id": "main", "kind": "video", "path": f"sources/{self.SOURCE_NAME}"}],
                "segments": [{
                    "video": {"source_id": "main", "in_s": 0, "out_s": end, "fit": "preserve"},
                    "audio": {"mode": "keep", "tracks": [{"source_id": "main", "in_s": 0, "out_s": end, "gain_db": 0}]},
                }],
                "cuts": [],
                "layers": [],
                "audio_processing": {
                    "normalize_loudness": True, "target_lufs": -16,
                    "limiter": {"enabled": True, "ceiling_dbfs": -1}, "ducking": False,
                },
            }
            (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            for arguments in (("render", "preview"), ("approve",)):
                result = run_module(*arguments, "--workdir", str(workdir), "--json")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            yield workdir

    def render_full_cli(self, workdir: Path, *extra: str) -> dict:
        """Roda `render full` pela CLI real e devolve `data`; falha o teste se o render falhar."""

        result = run_module("render", "full", "--workdir", str(workdir), *extra, "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        envelope = json.loads(result.stdout)
        self.assertTrue(envelope["ok"], result.stdout)
        return envelope["data"]

    def test_standard_and_omission_render_the_same_full_and_leave_preview_and_approval_alone(self) -> None:
        """A14: sem `--quality` e com `--quality standard` a full é a mesma; a qualidade não toca preview nem aprovação."""

        with self.approved_workdir() as workdir:
            approval_before = (workdir / "approval.json").read_bytes()
            preview_before = sha256(workdir / "preview.mp4")

            omitted = self.render_full_cli(workdir)
            omitted_content = framemd5(workdir / "full.mp4")
            standard = self.render_full_cli(workdir, "--quality", "standard")
            standard_content = framemd5(workdir / "full.mp4")

            self.assertEqual(omitted["video"]["quality"], "standard")
            self.assertEqual(standard["video"]["quality"], "standard")
            self.assertEqual(omitted_content, standard_content)

            # `high` renderiza contra a mesma aprovação (não é conteúdo editorial) e muda só a full.
            high = self.render_full_cli(workdir, "--quality", "high")
            high_content = framemd5(workdir / "full.mp4")
            self.assertEqual(high["video"]["quality"], "high")
            self.assertNotEqual(high_content, standard_content)
            self.assertEqual(high["plan_sha256"], standard["plan_sha256"])
            self.assertEqual(high["approval"], standard["approval"])

            # Voltar ao `standard` devolve exatamente o conteúdo anterior, com a aprovação intacta.
            self.render_full_cli(workdir, "--quality", "standard")
            self.assertEqual(framemd5(workdir / "full.mp4"), standard_content)
            self.assertEqual((workdir / "approval.json").read_bytes(), approval_before)
            self.assertEqual(sha256(workdir / "preview.mp4"), preview_before)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_the_shortcut_accepts_the_option_and_reports_the_preset(self) -> None:
        with self.approved_workdir() as workdir:
            result = run_module("full", "--workdir", str(workdir), "--quality", "max", "--json")
            envelope = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(envelope["command"], "render full")
            self.assertEqual(envelope["data"]["video"]["quality"], "max")
            self.assertEqual(envelope["data"]["video"]["accel"], "cpu")

    def measure(self, workdir: Path, accel: str) -> dict[str, dict[str, float]]:
        """Renderiza cada preset e mede o tamanho do arquivo e o SSIM do vídeo contra a fonte."""

        measured: dict[str, dict[str, float]] = {}
        for name in QUALITY_CHOICES:
            data = self.render_full_cli(workdir, "--accel", accel, "--quality", name)
            # Sem queda silenciosa de modo: a medição vale para o encoder que o teste diz medir.
            self.assertEqual(data["video"]["accel"], "gpu" if accel == "gpu" else "cpu")
            self.assertEqual(data["video"]["quality"], name)
            full = workdir / "full.mp4"
            measured[name] = {
                "size": float(full.stat().st_size),
                "ssim": ssim_against(full, self.source, self.SEGMENT_OUT_S),
            }
        return measured

    def assert_strictly_increasing(self, measured: dict[str, dict[str, float]]) -> None:
        """Tamanho e SSIM sobem a cada degrau, com margem mínima (a ordem, não valores absolutos)."""

        for lower, upper in zip(QUALITY_CHOICES, QUALITY_CHOICES[1:]):
            with self.subTest(step=f"{lower}->{upper}", measured=measured):
                self.assertGreater(measured[upper]["size"], measured[lower]["size"] * MIN_SIZE_RATIO)
                self.assertGreater(measured[upper]["ssim"], measured[lower]["ssim"] + MIN_SSIM_STEP)

    def test_cpu_presets_rise_in_size_and_ssim(self) -> None:
        with self.approved_workdir() as workdir:
            self.assert_strictly_increasing(self.measure(workdir, "none"))

    @unittest.skipUnless(
        shutil.which("nvidia-smi") and dependencies.gpu_acceleration_available(),
        "requer GPU NVIDIA operante e FFmpeg com NVENC e CUDA",
    )
    def test_gpu_presets_rise_in_size_and_ssim(self) -> None:
        with self.approved_workdir() as workdir:
            self.assert_strictly_increasing(self.measure(workdir, "gpu"))

    def test_gpu_failure_falls_back_to_the_cpu_with_the_requested_preset(self) -> None:
        """O fallback da GPU para a CPU mantém a qualidade pedida: o `crf` do preset, e `video.accel` informa `cpu`."""

        calls: list[list[str]] = []
        original = process.run_tool

        def failing_nvenc(tool: str, arguments: list[str], *, timeout_seconds: float):
            if tool == "ffmpeg" and ("-/filter_complex" in arguments or "-filter_complex" in arguments):
                calls.append(list(arguments))
                if "h264_nvenc" in arguments:
                    return subprocess.CompletedProcess(arguments, 1, "", "nvenc indisponivel")
            return original(tool, arguments, timeout_seconds=timeout_seconds)

        with self.approved_workdir() as workdir, patch(
            "editor.render.preview.dependencies.gpu_acceleration_available", return_value=True
        ), patch("editor.render.preview.process.run_tool", side_effect=failing_nvenc):
            result = render_full(workdir, accel="gpu", quality="high")

        self.assertEqual(result["video"]["accel"], "cpu")
        self.assertEqual(result["video"]["quality"], "high")
        self.assertEqual(len(calls), 2)
        high = QUALITY_PRESETS["high"]
        self.assertEqual(calls[0][calls[0].index("-cq") + 1], str(high.cq))
        self.assertEqual(calls[0][calls[0].index("-level") + 1], high.nvenc_level)
        self.assertEqual(calls[1][calls[1].index("-c:v") + 1], "libx264")
        self.assertEqual(calls[1][calls[1].index("-crf") + 1], str(high.crf))
        self.assertNotIn("-level", calls[1])


if __name__ == "__main__":
    unittest.main()
