"""Legendas queimadas: jornada de render, posição por pixels e integridade da aprovação.

As fontes cinza com áudio permitem distinguir texto branco, contorno vermelho e sombra
azul. Os tempos esperados são calculados à parte do mapeador: cortes e replace retiram
duas falas; as demais aparecem em 0,4..1,2, 2,2..3,2 e 5,1..5,8 na timeline.
"""

from __future__ import annotations

import copy
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

from editor.integrity.approval import approve_workdir
from editor.plan.schema import DEFAULT_CAPTION_BOXES
from editor.plan.validation import load_validated_plan
from editor.render.ffmpeg_graph import _drawtext_filter, build_ffmpeg_graph
from editor.render.full import render_full
from editor.render.preview import render_preview
from tests.test_captions_plan import caption, segment
from tests.test_preview_render import run_module, workspace_tempdir
from tests.test_text_style import read_rgb_frame


EVIDENCE = Path(__file__).resolve().parents[1] / "tmp-captions-evidence"
"""PNGs locais ignorados para a inspeção renderizada do coordenador, sem mídia versionada."""


def render_plan(source: Path, aspect: str, declared: bool) -> dict:
    """Uma jornada com jump cut, replace, texto hostil e camada branca sob a legenda."""
    style = {
        "font": "Arial Bold", "size_px": 48, "color": "#FFFFFF",
        "outline": {"width_px": 3, "color": "#FF0000"},
        "shadow": {"offset_x_px": 12, "offset_y_px": -5, "blur_px": 2,
                   "color": "#0000FF", "opacity": 1},
    }
    if declared:
        style.update({"x": 0.15, "y": 0.3, "width": 0.7, "height": 0.25, "align": "right"})
    # Uma sequência de hastes tem base sem descendentes e mede duas linhas pela quebra
    # aproximada existente. A full 16:9 usa 1280, e a 9:16 usa 1080 de largura.
    repeats = (34 if declared else 44) if aspect == "16:9" else (22 if declared else 27)
    long_text = "I " * repeats
    return {
        "schema_version": 2, "profile": {"aspect_ratio": aspect},
        "sources": [{"id": name, "kind": "video", "path": str(source)} for name in ("main", "other")],
        "segments": [segment(0, 2), segment(3, 5), segment(5, 6, source="other", mode="replace"), segment(6, 7)],
        "cuts": [], "layers": [{
            "type": "text", "start_s": 0, "end_s": 6, "x": 0, "y": 0,
            "width": 1, "height": 0.1, "text": "CAMADA", "font": "Arial Bold",
            "size_px": 32, "color": "#00FF00", "align": "left", "wrap": False,
        }],
        "captions": {"style": style, "items": [
            caption(0.4, 1.2, text="I I I I I I I I"),
            caption(3.2, 4.2, text=long_text),
            caption(2.2, 2.8, text="CORTADO"),
            caption(5.1, 5.8, text="REPLACE"),
            caption(6.1, 6.8, text="' : , ; [ ] \\ %{texto}"),
        ]},
    }


def colored_bounds(data: bytes, width: int, height: int, color: str) -> tuple[int, int, int, int] | None:
    """Retângulo de pixels de texto/efeito, com limiares tolerantes à compressão 4:2:0.

    A camada verde no topo serve de consumidor independente e nunca passa pelos
    limiares branco, vermelho ou azul da legenda.
    """
    points = []
    for index in range(0, len(data), 3):
        r, g, b = data[index:index + 3]
        # Hastes na preview 9:16 têm poucos pixels e anti-aliasing; 232 ainda está
        # acima do fundo uniforme 224 e permite medir também linhas em y fracionário.
        match = (min(r, g, b) > 232 if color == "white" else
                 r > 150 and r > g * 1.5 and r > b * 1.5 if color == "red" else
                 b > 140 and b > r * 1.5 and b > g * 1.5)
        if match:
            pixel = index // 3
            points.append((pixel % width, pixel // width))
    if not points:
        return None
    if color == "white":
        # Ringing do x264 gera pixels claros isolados até fora da caixa. Linhas de
        # glifos têm oito hastes; cinco pixels por linha retiram só esses pontos.
        rows = Counter(y for _, y in points)
        points = [(x, y) for x, y in points if rows[y] >= 5]
        if not points:
            return None
    return min(x for x, _ in points), max(x for x, _ in points), min(y for _, y in points), max(y for _, y in points)


class CaptionsRenderTests(unittest.TestCase):
    """Executa preview, aprovação, full e recusa stale nas quatro caixas/proporções."""

    @classmethod
    def setUpClass(cls) -> None:
        """Gera uma fonte única local e preserva somente PNGs ignorados de evidência."""
        cls.workspace = workspace_tempdir()
        cls.root = cls.workspace.__enter__()
        cls.source = cls.root / "source.mp4"
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=0xE0E0E0:s=1280x720:r=25:d=8",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(cls.source),
        ], capture_output=True, check=True)
        EVIDENCE.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls) -> None:
        """Limpa os vídeos descartáveis, mantendo as imagens para a inspeção visual."""
        cls.workspace.__exit__(None, None, None)

    def test_render_journey_default_and_declared_boxes_in_both_aspects(self) -> None:
        """Pixels provam tempo, caixa, base comum, escala, estilos, escape e full preservada."""
        for aspect in ("16:9", "9:16"):
            for declared in (False, True):
                with self.subTest(aspect=aspect, declared=declared):
                    workdir = self.root / f"{aspect.replace(':', '-')}-{declared}"
                    workdir.mkdir()
                    plan = render_plan(self.source, aspect, declared)
                    plan_path = workdir / "plan.json"
                    plan_path.write_text(json.dumps(plan), encoding="utf-8")
                    document, validation = load_validated_plan(workdir)
                    self.assertEqual(validation["_captions"]["occurrences"], [
                        {"item": 1, "start_s": 0.4, "end_s": 1.2},
                        {"item": 2, "start_s": 2.2, "end_s": 3.2},
                        {"item": 5, "start_s": 5.1, "end_s": 5.8},
                    ])
                    self.assertEqual(validation["captions"]["unplaced"], [3, 4])
                    preview = render_preview(workdir)
                    approve_workdir(workdir)
                    full = render_full(workdir)
                    fractions = []
                    box = plan["captions"]["style"] if declared else DEFAULT_CAPTION_BOXES[aspect]
                    for kind, result in (("preview", preview), ("full", full)):
                        width, height = result["video"]["width"], result["video"]["height"]
                        path = workdir / f"{kind}.mp4"
                        bounds = []
                        for index in (20, 65):  # 0,8 s e 2,6 s: uma linha e duas linhas.
                            frame = read_rgb_frame(path, width, height, frame_index=index)
                            white = colored_bounds(frame, width, height, "white")
                            self.assertIsNotNone(white)
                            left, right, top, bottom = white
                            self.assertGreaterEqual(left, int(box["x"] * width) - 2)
                            self.assertLessEqual(right, (box["x"] + box["width"]) * width + 2)
                            self.assertGreaterEqual(top, int(box["y"] * height) - 2)
                            self.assertAlmostEqual(bottom, (box["y"] + box["height"]) * height - 1, delta=3)
                            self.assertIsNotNone(colored_bounds(frame, width, height, "red"))
                            self.assertIsNotNone(colored_bounds(frame, width, height, "blue"))
                            bounds.append(white)
                            # FFmpeg publica PNGs dos mesmos quadros conferidos acima.
                            subprocess.run([
                                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path),
                                "-vf", f"select=eq(n\\,{index})", "-frames:v", "1",
                                str(EVIDENCE / f"{workdir.name}-{kind}-{index}.png"),
                            ], capture_output=True, check=True)
                        self.assertAlmostEqual(bounds[0][3], bounds[1][3], delta=2)
                        self.assertGreater(bounds[1][3] - bounds[1][2], 1.5 * (bounds[0][3] - bounds[0][2]))
                        fractions.append((bounds[0][1] - bounds[0][0] + 1) / width)
                        for index in (0, 30, 49, 85, 112, 145):
                            # Inclui borda de fim exclusiva, corte e áudio replace.
                            data = read_rgb_frame(path, width, height, frame_index=index)
                            self.assertIsNone(colored_bounds(data, width, height, "white"), (kind, index))
                        hostile = read_rgb_frame(path, width, height, frame_index=135)
                        self.assertIsNotNone(colored_bounds(hostile, width, height, "white"))
                    self.assertAlmostEqual(fractions[0], fractions[1], delta=0.02)

                    # A aprovação pertence ao plano completo; mutação de texto não substitui a full.
                    full_path = workdir / "full.mp4"
                    before = hashlib.sha256(full_path.read_bytes()).hexdigest()
                    plan["captions"]["items"][0]["text"] = "Mudança posterior"
                    plan_path.write_text(json.dumps(plan), encoding="utf-8")
                    response = run_module("render", "full", "--workdir", str(workdir), "--json")
                    self.assertNotEqual(response.returncode, 0)
                    self.assertEqual(json.loads(response.stdout)["error"]["code"], "APPROVAL_STALE")
                    self.assertEqual(hashlib.sha256(full_path.read_bytes()).hexdigest(), before)

    def test_captions_follow_layers_and_no_captions_preserves_graph(self) -> None:
        """O caminho opcional não altera o grafo existente e os labels nunca colidem."""
        plan = render_plan(self.source, "16:9", False)
        workdir = self.root / "graph"
        workdir.mkdir()
        (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        document, validation = load_validated_plan(workdir)
        graph = build_ffmpeg_graph(document, validation, canvas_width=1280, canvas_height=720)
        self.assertIn("[vtext0][textlayer1]overlay", graph.filter_complex)
        self.assertIn("y=h-text_h", graph.filter_complex)
        self.assertIn("expansion=none", graph.filter_complex)
        baseline = copy.deepcopy(document)
        del baseline["captions"]
        (workdir / "plan.json").write_text(json.dumps(baseline), encoding="utf-8")
        old_document, old_validation = load_validated_plan(workdir)
        original = build_ffmpeg_graph(old_document, old_validation, canvas_width=1280, canvas_height=720)
        without = dict(validation)
        del without["_captions"]
        self.assertEqual(original.filter_complex, build_ffmpeg_graph(
            document, without, canvas_width=1280, canvas_height=720,
        ).filter_complex)

    def test_shared_drawtext_receives_hostile_text_literally(self) -> None:
        """Os dois parsers preservam cada caractere e `expansion=none` como opção real.

        Este helper é compartilhado com layers text: prova a correção do apóstrofo
        no consumidor anterior, sem comparar só o sucesso de um encode.
        """
        plan = render_plan(self.source, "16:9", False)
        workdir = self.root / "literal"
        workdir.mkdir()
        (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        _, validation = load_validated_plan(workdir)
        font = validation["_resolved_font_files"]["Arial Bold"]
        text = "' : , ; [ ] \\ %{texto}"
        drawtext = _drawtext_filter(font, 32, "#FFFFFF", text, "left", "0", "0", None)
        response = subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "debug", "-f", "lavfi", "-i",
            "color=c=black:s=320x180:d=0.1," + drawtext,
            "-frames:v", "1", "-f", "null", "-",
        ], capture_output=True, text=True, check=True)
        self.assertIn(f"Setting 'text' to value '{text}'", response.stderr)
        self.assertIn("Setting 'expansion' to value 'none'", response.stderr)


if __name__ == "__main__":
    unittest.main()
