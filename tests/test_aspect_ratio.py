"""Testes da T1 da phase 5: as quatro proporções no perfil, no canvas e na checagem do MP4.

Cobre, nesta ordem:

1. Tabela única de proporções e dimensões (`plan/schema.py`), sem FFmpeg.
2. Validação do perfil por `plan validate`: proporções aceitas, `max_full_width` só no 16:9,
   `fit: "preserve"` contra a proporção do perfil e envelope `data.profile`.
3. Render real de preview e full em 9:16, 1:1 e 4:5 com `crop` (sem barras) e `contain` (com
   barras), provado por FFprobe das dimensões e por inspeção de pixels.
4. Mensagens da checagem do MP4 renderizado.
5. Regressão do 16:9 (A6): o canvas e o grafo de filtros de um plano 16:9 são os mesmos de antes.

Sobre a prova de que a full 16:9 não mudou (A6): o `framemd5` do MP4 codificado depende do
número de threads do x264 e, portanto, da máquina; não serve como valor fixo em teste. A prova
automatizada é o par "canvas + `filter_complex` idênticos": a mesma entrada no FFmpeg com os
mesmos filtros produz a mesma saída. Os valores esperados (`GOLDEN_*`) foram registrados com o
código anterior à T1. A comparação de `framemd5` de vídeo e áudio de preview e full, antes e
depois da T1, foi feita à mão na mesma máquina (fixture `workflow`, fixture com camadas e corte e
dois planos sintéticos) e está no relatório da task.

As fontes são geradas aqui por `ffmpeg -f lavfi` (sem fixtures grandes), numa pasta descartável.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies
from editor.contracts import CliError
from editor.integrity.approval import approve_workdir
from editor.media import MediaMetadata, inspect_source
from editor.plan.schema import (
    ASPECT_RATIO_TERMS,
    ASPECT_RATIOS,
    FIXED_CANVAS_DIMENSIONS,
    LANDSCAPE_ASPECT_RATIO,
    aspect_ratio_value,
    canvas_dimensions,
)
from editor.plan.validation import load_validated_plan, validate_plan
from editor.render.ffmpeg_graph import build_ffmpeg_graph
from editor.render.full import render_full
from editor.render.preview import (
    MAX_PREVIEW_DIMENSION,
    _preview_dimensions,
    _validate_rendered_preview,
    render_preview,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Dimensões das decisões 2 e 3 da spec, escritas por extenso para não depender da própria tabela.
EXPECTED_CANVAS = {
    "9:16": {"preview": (404, 720), "full": (1_080, 1_920)},
    "1:1": {"preview": (720, 720), "full": (1_080, 1_080)},
    "4:5": {"preview": (576, 720), "full": (1_080, 1_350)},
}
INVALID_RATIO_MESSAGE = "O perfil de saída deve usar aspect_ratio 16:9, 9:16, 1:1 ou 4:5."
MAX_FULL_WIDTH_MESSAGE = "O campo profile.max_full_width só é aceito com aspect_ratio 16:9."

# Fontes sintéticas H.264/AAC de 1 s: vermelho nas laterais e verde no terço central, para que
# a posição do recorte e as barras pretas sejam legíveis por cor.
SOURCE_SIZES = {
    "wide": (640, 360),
    "tall": (360, 640),
    "square": (360, 360),
    "fourfive": (320, 400),
}
SOURCE_WIDTH, SOURCE_HEIGHT = SOURCE_SIZES["wide"]
BAND_START, BAND_END = SOURCE_WIDTH / 3, 2 * SOURCE_WIDTH / 3
BAND_MARGIN_PX = 8
"""Folga, em pixels da fonte, perto da fronteira vermelho/verde (a croma 4:2:0 borra a transição)."""

SEGMENT_SECONDS = 0.6
FRAME_SAMPLE_SECONDS = 0.3

# Valores registrados com o código anterior à T1 para o plano 16:9 sintético de `golden_plan`.
GOLDEN_PREVIEW_CANVAS = (704, 396)
GOLDEN_FULL_CANVAS = (1_280, 720)
GOLDEN_PREVIEW_GRAPH_SHA256 = "2889f5d4ca4a25626dcf259c4a60c5fa1fa06675a0090f82079e64a8eb792558"
GOLDEN_FULL_GRAPH_SHA256 = "5f4bff1886145f0b5aaf9896a0969a38f589f32d25b85cdbbff350917475e4c7"


# --- Infraestrutura: fontes, planos e leitura de pixels ------------------------------------


def make_source(path: Path, width: int, height: int, *, audio: bool = True) -> None:
    """Gera uma fonte H.264 de 1 s (AAC opcional) com laterais vermelhas e terço central verde."""

    arguments = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i",
        (
            f"color=c=0xFF0000:s={width}x{height}:r=25:d=1,"
            "drawbox=x=iw/3:y=0:w=iw/3:h=ih:color=0x00FF00:t=fill,format=yuv420p"
        ),
    ]
    if audio:
        arguments += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac", "-shortest"]
    else:
        arguments += ["-an"]
    arguments += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(arguments, check=True)


def keep_audio(source_id: str, out_s: float = SEGMENT_SECONDS) -> dict[str, object]:
    """Áudio 'keep' do próprio recorte, sem ganho, para o MP4 sair com AAC."""

    return {
        "mode": "keep",
        "tracks": [{"source_id": source_id, "in_s": 0, "out_s": out_s, "gain_db": 0}],
    }


def single_segment_plan(
    aspect_ratio: str,
    source_id: str,
    fit: str,
    **profile_extra: object,
) -> dict[str, object]:
    """Plano mínimo de um segmento da fonte `source_id` no perfil pedido, com áudio AAC."""

    return {
        "schema_version": 2,
        "profile": {"aspect_ratio": aspect_ratio, **profile_extra},
        "sources": [{"id": source_id, "kind": "video", "path": f"sources/{source_id}.mp4"}],
        "segments": [
            {
                "video": {"source_id": source_id, "in_s": 0, "out_s": SEGMENT_SECONDS, "fit": fit},
                "audio": keep_audio(source_id),
            }
        ],
        "cuts": [],
        "layers": [],
        "audio_processing": {
            "normalize_loudness": False,
            "target_lufs": -16,
            "limiter": {"enabled": False, "ceiling_dbfs": -1},
            "ducking": False,
        },
    }


def frame_rgb(path: Path, width: int, height: int) -> bytes:
    """Decodifica um quadro do MP4 em RGB24 bruto (largura x altura x 3 bytes)."""

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(FRAME_SAMPLE_SECONDS),
            "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
        check=True,
    )
    if len(result.stdout) != width * height * 3:
        raise AssertionError(f"quadro de {path.name} com tamanho inesperado")
    return result.stdout


def classify_pixel(frame: bytes, width: int, x: int, y: int) -> str:
    """Rotula o pixel como preto, vermelho, verde ou outro, com folga para a perda do H.264."""

    offset = (y * width + x) * 3
    red, green, blue = frame[offset], frame[offset + 1], frame[offset + 2]
    if max(red, green, blue) < 60:
        return "black"
    if red > 170 and green < 110 and blue < 110:
        return "red"
    if green > 170 and red < 110 and blue < 110:
        return "green"
    return "other"


def expected_crop_color(aspect_ratio: str, fraction_x: float) -> str | None:
    """Cor esperada, numa coluna do `crop` central, para a fonte `wide` (640x360).

    O recorte central cobre a altura inteira da fonte e `altura * a / b` de largura; a coluna em
    `fraction_x` do canvas corresponde a uma posição conhecida da fonte. Devolve `None` perto da
    fronteira vermelho/verde, onde a cor é ambígua.
    """

    ratio_width, ratio_height = ASPECT_RATIO_TERMS[aspect_ratio]
    visible = SOURCE_HEIGHT * ratio_width / ratio_height
    source_x = (SOURCE_WIDTH - visible) / 2 + fraction_x * visible
    if min(abs(source_x - BAND_START), abs(source_x - BAND_END)) < BAND_MARGIN_PX:
        return None
    return "green" if BAND_START < source_x < BAND_END else "red"


class AspectRatioCase(unittest.TestCase):
    """Base com fontes sintéticas compartilhadas e criação de trabalhos descartáveis."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFmpeg/FFprobe e gera uma vez as fontes que todos os cenários reutilizam."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        cls.root = PROJECT_ROOT / f"tmp-aspect-ratio-{uuid4().hex}"
        cls.sources_dir = cls.root / "shared-sources"
        cls.sources_dir.mkdir(parents=True)
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        for name, (width, height) in SOURCE_SIZES.items():
            make_source(cls.sources_dir / f"{name}.mp4", width, height)

    def new_workdir(self, plan: dict[str, object], *source_ids: str) -> Path:
        """Cria um trabalho com o plano e cópias das fontes pedidas, dentro da pasta descartável."""

        workdir = self.root / f"work-{uuid4().hex[:10]}"
        (workdir / "sources").mkdir(parents=True)
        for source_id in source_ids:
            shutil.copy2(self.sources_dir / f"{source_id}.mp4", workdir / "sources" / f"{source_id}.mp4")
        (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        return workdir


# --- 1. Tabela única de proporções ----------------------------------------------------------


class AspectRatioTableTests(unittest.TestCase):
    """Garante que a tabela única seja coerente e que o 16:9 mantenha a regra vigente."""

    def test_allowlist_and_terms_cover_the_same_four_ratios(self) -> None:
        """Mantém a allowlist, os termos e a tabela de tamanho fixo alinhados entre si."""

        self.assertEqual(ASPECT_RATIOS, ("16:9", "9:16", "1:1", "4:5"))
        self.assertEqual(set(ASPECT_RATIO_TERMS), set(ASPECT_RATIOS))
        self.assertEqual(set(FIXED_CANVAS_DIMENSIONS), set(ASPECT_RATIOS) - {LANDSCAPE_ASPECT_RATIO})

    def test_fixed_canvas_matches_spec_decisions_and_is_encodable(self) -> None:
        """Compara a tabela com as decisões 2 e 3 e confere paridade, razão e lado maior da preview."""

        for aspect_ratio, expected in EXPECTED_CANVAS.items():
            with self.subTest(aspect_ratio=aspect_ratio):
                self.assertEqual(FIXED_CANVAS_DIMENSIONS[aspect_ratio], expected)
                ratio_width, ratio_height = ASPECT_RATIO_TERMS[aspect_ratio]
                for kind, (width, height) in expected.items():
                    self.assertEqual((width % 2, height % 2), (0, 0), f"{kind} precisa de dimensões pares")
                    # Uma dimensão pode ficar a 1 px do valor exato por causa da paridade.
                    self.assertLessEqual(abs(width * ratio_height - height * ratio_width), max(ratio_width, ratio_height))
                self.assertEqual(max(expected["preview"]), MAX_PREVIEW_DIMENSION)
                self.assertEqual(expected["full"][0], 1_080)

    def test_aspect_ratio_value_matches_terms(self) -> None:
        """Converte cada proporção em largura/altura, valor usado pela regra de `preserve`."""

        self.assertEqual(aspect_ratio_value("16:9"), 16 / 9)
        self.assertEqual(aspect_ratio_value("9:16"), 9 / 16)
        self.assertEqual(aspect_ratio_value("1:1"), 1.0)
        self.assertEqual(aspect_ratio_value("4:5"), 0.8)

    def test_canvas_dimensions_keeps_landscape_rule_and_fixes_the_others(self) -> None:
        """Fixa a regra vigente do 16:9 (sem upscale, alinhamento 32 na preview e 16 na full)."""

        landscape = LANDSCAPE_ASPECT_RATIO
        cases = (
            # (largura da fonte, teto, full?, esperado)
            (1_280, MAX_PREVIEW_DIMENSION, False, (704, 396)),
            (1_920, MAX_PREVIEW_DIMENSION, False, (704, 396)),
            (320, MAX_PREVIEW_DIMENSION, False, (320, 180)),
            (1_280, 1_920, True, (1_280, 720)),
            (1_920, 1_920, True, (1_920, 1_080)),
            (3_840, 1_920, True, (1_920, 1_080)),
            (3_840, 3_840, True, (3_840, 2_160)),
            (640, 640, True, (640, 360)),
            (320, 160, True, (160, 90)),
        )
        for source_width, max_width, final, expected in cases:
            with self.subTest(source_width=source_width, max_width=max_width, final=final):
                self.assertEqual(
                    canvas_dimensions(landscape, source_width, max_width, final=final), expected
                )
        for aspect_ratio, expected in EXPECTED_CANVAS.items():
            for final, key in ((False, "preview"), (True, "full")):
                with self.subTest(aspect_ratio=aspect_ratio, key=key):
                    # O tamanho fixo ignora a largura da fonte e o teto.
                    self.assertEqual(canvas_dimensions(aspect_ratio, 3_840, None, final=final), expected[key])
                    self.assertEqual(canvas_dimensions(aspect_ratio, 160, 32, final=final), expected[key])


# --- 2. Validação do perfil -----------------------------------------------------------------


class AspectRatioValidationTests(AspectRatioCase):
    """Valida o perfil, `max_full_width` e `fit: "preserve"` pela API real de `plan validate`."""

    def test_accepts_four_ratios_and_reports_the_full_canvas(self) -> None:
        """Cada proporção valida e `data.profile` informa as dimensões da full e as ampliações.

        A fonte `wide` tem 640x360 e o canvas fixo tem 1080 de largura, então o `crop` central a
        amplia (fator = largura do canvas / largura do recorte central, decisão 9 da spec, T2):
        1080/202.5 no 9:16, 1080/360 no 1:1 e 1080/288 no 4:5. No 16:9 o canvas é a própria fonte.
        """

        expected_profiles = {
            "16:9": {
                "aspect_ratio": "16:9", "max_full_width": 1_920, "effective_full_width": 640,
                "full_width": 640, "full_height": 360, "upscale": [],
            },
            "9:16": {
                "aspect_ratio": "9:16", "full_width": 1_080, "full_height": 1_920,
                "upscale": [{"segment": 1, "factor": 5.33}],
            },
            "1:1": {
                "aspect_ratio": "1:1", "full_width": 1_080, "full_height": 1_080,
                "upscale": [{"segment": 1, "factor": 3.0}],
            },
            "4:5": {
                "aspect_ratio": "4:5", "full_width": 1_080, "full_height": 1_350,
                "upscale": [{"segment": 1, "factor": 3.75}],
            },
        }
        for aspect_ratio, expected in expected_profiles.items():
            with self.subTest(aspect_ratio=aspect_ratio):
                fit = "preserve" if aspect_ratio == "16:9" else "crop"
                workdir = self.new_workdir(single_segment_plan(aspect_ratio, "wide", fit), "wide")
                self.assertEqual(validate_plan(workdir)["profile"], expected)

    def test_real_cli_validates_vertical_profile_envelope(self) -> None:
        """Confirma o contrato público: envelope JSON de `plan validate` em 9:16, sem `stderr`."""

        workdir = self.new_workdir(single_segment_plan("9:16", "wide", "crop"), "wide")
        result = subprocess.run(
            [sys.executable, "-m", "editor", "plan", "validate", "--workdir", str(workdir.resolve()), "--json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertTrue(payload["ok"])
        self.assertEqual(
            payload["data"]["profile"],
            {
                "aspect_ratio": "9:16", "full_width": 1_080, "full_height": 1_920,
                "upscale": [{"segment": 1, "factor": 5.33}],
            },
        )

    def test_rejects_unknown_aspect_ratio_before_probing_media(self) -> None:
        """Proporção fora da allowlist (inclusive tipos errados e ausência) falha antes do FFprobe."""

        invalid_values = ("3:2", "16:10", "", " 16:9", "9:16\n", "16:09", 169, None, True, ["9:16"], {"r": 1})
        for value in invalid_values:
            with self.subTest(value=value):
                plan = single_segment_plan("16:9", "wide", "preserve")
                plan["profile"] = {"aspect_ratio": value}
                workdir = self.new_workdir(plan, "wide")
                with patch("editor.plan.validation.media.inspect_source") as inspect:
                    with self.assertRaises(CliError) as raised:
                        validate_plan(workdir)
                self.assertEqual(raised.exception.code, "PLAN_INVALID")
                self.assertEqual(raised.exception.message, INVALID_RATIO_MESSAGE)
                inspect.assert_not_called()

        missing = single_segment_plan("16:9", "wide", "preserve")
        missing["profile"] = {}
        workdir = self.new_workdir(missing, "wide")
        with self.assertRaises(CliError) as raised:
            validate_plan(workdir)
        self.assertEqual((raised.exception.code, raised.exception.message), ("PLAN_INVALID", INVALID_RATIO_MESSAGE))

    def test_rejects_max_full_width_unless_landscape_before_probing_media(self) -> None:
        """`max_full_width` com 9:16, 1:1 ou 4:5 é PLAN_INVALID, valendo ou não como teto do 16:9."""

        for aspect_ratio in ("9:16", "1:1", "4:5"):
            for value in (1_920, 640, 1_080, True, None):
                with self.subTest(aspect_ratio=aspect_ratio, value=value):
                    workdir = self.new_workdir(
                        single_segment_plan(aspect_ratio, "wide", "crop", max_full_width=value), "wide"
                    )
                    with patch("editor.plan.validation.media.inspect_source") as inspect:
                        with self.assertRaises(CliError) as raised:
                            validate_plan(workdir)
                    self.assertEqual(raised.exception.code, "PLAN_INVALID")
                    self.assertEqual(raised.exception.message, MAX_FULL_WIDTH_MESSAGE)
                    inspect.assert_not_called()

        workdir = self.new_workdir(single_segment_plan("16:9", "wide", "preserve", max_full_width=320), "wide")
        profile = validate_plan(workdir)["profile"]
        self.assertEqual((profile["max_full_width"], profile["effective_full_width"]), (320, 320))
        self.assertEqual((profile["full_width"], profile["full_height"]), (320, 180))

    def test_unknown_profile_field_is_still_rejected(self) -> None:
        """A allowlist do `profile` continua fechada nas quatro proporções."""

        plan = single_segment_plan("9:16", "wide", "crop")
        plan["profile"]["fps"] = 30
        workdir = self.new_workdir(plan, "wide")
        with self.assertRaises(CliError) as raised:
            validate_plan(workdir)
        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_preserve_compares_source_ratio_with_the_profile_ratio(self) -> None:
        """`preserve` aceita só a fonte na proporção do perfil e a mensagem cita essa proporção."""

        # (proporção do perfil, fonte, `preserve` deve ser aceito?)
        cases = (
            ("16:9", "wide", True),
            ("16:9", "tall", False),
            ("9:16", "tall", True),
            ("9:16", "wide", False),
            ("1:1", "square", True),
            ("1:1", "wide", False),
            ("4:5", "fourfive", True),
            ("4:5", "square", False),
        )
        for aspect_ratio, source_id, accepted in cases:
            with self.subTest(aspect_ratio=aspect_ratio, source=source_id):
                workdir = self.new_workdir(single_segment_plan(aspect_ratio, source_id, "preserve"), source_id)
                if accepted:
                    self.assertEqual(validate_plan(workdir)["profile"]["aspect_ratio"], aspect_ratio)
                    continue
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)
                self.assertEqual(raised.exception.code, "PLAN_INVALID")
                self.assertEqual(
                    raised.exception.message,
                    f"Uma fonte fora de {aspect_ratio} precisa declarar fit contain ou crop.",
                )
                # O mesmo plano com `contain` e `crop` continua válido: só `preserve` é restrito.
                for fit in ("contain", "crop"):
                    plan = single_segment_plan(aspect_ratio, source_id, fit)
                    (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
                    self.assertEqual(validate_plan(workdir)["profile"]["aspect_ratio"], aspect_ratio)


# --- 3. Render real nas proporções não 16:9 -------------------------------------------------


class AspectRatioRenderTests(AspectRatioCase):
    """Renderiza preview e full reais e prova dimensões, codecs e barras pelo conteúdo dos quadros."""

    def render_pair(self, aspect_ratio: str, fit: str) -> tuple[Path, dict[str, object], dict[str, object]]:
        """Valida, renderiza a preview, aprova e renderiza a full de um plano de `wide`."""

        workdir = self.new_workdir(single_segment_plan(aspect_ratio, "wide", fit), "wide")
        validated_profile = validate_plan(workdir)["profile"]
        preview = render_preview(workdir)
        approve_workdir(workdir)
        full = render_full(workdir)
        # O envelope da preview e da full repete o `data.profile` validado.
        self.assertEqual(preview["profile"], validated_profile)
        self.assertEqual(full["profile"], validated_profile)
        return workdir, preview, full

    def assert_dimensions_and_codecs(
        self, aspect_ratio: str, workdir: Path, preview: dict[str, object], full: dict[str, object]
    ) -> None:
        """Confere dimensões do envelope e do arquivo (FFprobe), H.264/AAC e lado maior da preview."""

        for name, payload, expected in (
            ("preview.mp4", preview, EXPECTED_CANVAS[aspect_ratio]["preview"]),
            ("full.mp4", full, EXPECTED_CANVAS[aspect_ratio]["full"]),
        ):
            with self.subTest(artifact=name):
                self.assertEqual((payload["video"]["width"], payload["video"]["height"]), expected)
                metadata = inspect_source(workdir / name, require_audio=True, require_video=True)
                self.assertEqual((metadata.video_width, metadata.video_height), expected)
                self.assertEqual((metadata.video_codec, metadata.audio_codec), ("h264", "aac"))
        self.assertLessEqual(preview["video"]["max_dimension"], MAX_PREVIEW_DIMENSION)

    def assert_crop_without_bars(self, aspect_ratio: str, path: Path, size: tuple[int, int]) -> None:
        """Prova o `crop` central: cor certa em cada coluna e nenhum pixel preto na grade amostrada."""

        width, height = size
        frame = frame_rgb(path, width, height)
        for fraction_y in (0.03, 0.5, 0.97):
            for fraction_x in (0.03, 0.2, 0.35, 0.5, 0.65, 0.8, 0.97):
                x, y = int(fraction_x * (width - 1)), int(fraction_y * (height - 1))
                found = classify_pixel(frame, width, x, y)
                expected = expected_crop_color(aspect_ratio, fraction_x)
                with self.subTest(path=path.name, x=x, y=y, found=found, expected=expected):
                    self.assertNotEqual(found, "black")
                    if expected is not None:
                        self.assertEqual(found, expected)

    def assert_contain_with_bars(self, path: Path, size: tuple[int, int]) -> None:
        """Prova o `contain`: faixas pretas em cima e embaixo e a imagem inteira (laterais) no meio."""

        width, height = size
        frame = frame_rgb(path, width, height)
        bar_height = (height - width * SOURCE_HEIGHT / SOURCE_WIDTH) / 2
        self.assertGreater(bar_height, height * 0.1, "o cenário precisa ter barras visíveis")
        inside_top = int(bar_height / 2)
        inside_bottom = height - 1 - inside_top
        for x in (3, width // 2, width - 4):
            for y in (inside_top, inside_bottom):
                with self.subTest(path=path.name, x=x, y=y):
                    self.assertEqual(classify_pixel(frame, width, x, y), "black")
        middle = height // 2
        # A fonte cabe inteira na largura: laterais vermelhas e terço central verde.
        self.assertEqual(classify_pixel(frame, width, int(width * 0.03), middle), "red")
        self.assertEqual(classify_pixel(frame, width, width // 2, middle), "green")
        self.assertEqual(classify_pixel(frame, width, int(width * 0.97), middle), "red")

    def test_crop_fills_the_vertical_and_square_canvases_without_bars(self) -> None:
        """9:16, 1:1 e 4:5 com `crop` central sobre fonte 16:9: dimensões da spec e sem barras."""

        for aspect_ratio in ("9:16", "1:1", "4:5"):
            with self.subTest(aspect_ratio=aspect_ratio):
                workdir, preview, full = self.render_pair(aspect_ratio, "crop")
                self.assert_dimensions_and_codecs(aspect_ratio, workdir, preview, full)
                self.assert_crop_without_bars(aspect_ratio, workdir / "preview.mp4", EXPECTED_CANVAS[aspect_ratio]["preview"])
                self.assert_crop_without_bars(aspect_ratio, workdir / "full.mp4", EXPECTED_CANVAS[aspect_ratio]["full"])

    def test_contain_letterboxes_the_vertical_and_square_canvases(self) -> None:
        """9:16, 1:1 e 4:5 com `contain` sobre fonte 16:9: mesmas dimensões, com barras pretas."""

        for aspect_ratio in ("9:16", "1:1", "4:5"):
            with self.subTest(aspect_ratio=aspect_ratio):
                workdir, preview, full = self.render_pair(aspect_ratio, "contain")
                self.assert_dimensions_and_codecs(aspect_ratio, workdir, preview, full)
                self.assert_contain_with_bars(workdir / "preview.mp4", EXPECTED_CANVAS[aspect_ratio]["preview"])
                self.assert_contain_with_bars(workdir / "full.mp4", EXPECTED_CANVAS[aspect_ratio]["full"])

    def test_preserve_on_a_source_in_the_profile_ratio_fills_the_canvas(self) -> None:
        """`preserve` numa fonte já na proporção do perfil (9:16) renderiza sem barras no canvas."""

        workdir = self.new_workdir(single_segment_plan("9:16", "tall", "preserve"), "tall")
        preview = render_preview(workdir)
        approve_workdir(workdir)
        full = render_full(workdir)

        self.assertEqual((preview["video"]["width"], preview["video"]["height"]), (404, 720))
        self.assertEqual((full["video"]["width"], full["video"]["height"]), (1_080, 1_920))
        for name, size in (("preview.mp4", (404, 720)), ("full.mp4", (1_080, 1_920))):
            frame = frame_rgb(workdir / name, *size)
            for fraction in (0.03, 0.5, 0.97):
                x, y = int(fraction * (size[0] - 1)), int(fraction * (size[1] - 1))
                self.assertNotEqual(classify_pixel(frame, size[0], x, y), "black")

    def test_full_is_still_blocked_by_a_stale_approval_in_vertical_profiles(self) -> None:
        """A aprovação por hash segue valendo: mudar a proporção invalida a full aprovada."""

        workdir, _, _ = self.render_pair("9:16", "crop")
        plan = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
        plan["profile"]["aspect_ratio"] = "1:1"
        (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaises(CliError) as raised:
            render_full(workdir)
        self.assertEqual(raised.exception.code, "APPROVAL_STALE")


# --- 4. Checagem do MP4 renderizado ---------------------------------------------------------


class RenderedArtifactCheckTests(unittest.TestCase):
    """Cobre as mensagens e o critério de `_validate_rendered_preview` sem renderizar."""

    @staticmethod
    def metadata(width: int | None, height: int | None) -> MediaMetadata:
        """Metadados de um MP4 renderizado sem áudio e com a duração esperada de 0,6 s."""

        return MediaMetadata(
            path=Path("staging.mp4"), container="mp4", audio_codec=None, video_codec="h264",
            duration_seconds=0.6, size_bytes=1, sample_rate=None, channels=None,
            video_width=width, video_height=height, video_frame_rate=25.0,
        )

    def check(self, found: tuple[int | None, int | None], **arguments: object) -> MediaMetadata:
        """Executa a checagem com o MP4 simulado nas dimensões `found`."""

        with patch(
            "editor.render.preview.media.inspect_rendered_artifact",
            return_value=self.metadata(*found),
        ):
            return _validate_rendered_preview(
                Path("staging.mp4"), expected_duration=0.6, expected_audio=False, **arguments
            )

    def test_accepts_the_exact_fixed_canvas_even_though_the_ratio_is_not_exact(self) -> None:
        """404x720 difere de 9:16 em ~0,0014, mas é o canvas do perfil e deve ser aceito."""

        for aspect_ratio, expected in EXPECTED_CANVAS.items():
            with self.subTest(aspect_ratio=aspect_ratio):
                preview = self.check(
                    expected["preview"], canvas_width=expected["preview"][0],
                    canvas_height=expected["preview"][1], aspect_ratio=aspect_ratio,
                )
                self.assertEqual((preview.video_width, preview.video_height), expected["preview"])
                full = self.check(
                    expected["full"], canvas_width=expected["full"][0],
                    canvas_height=expected["full"][1], max_dimension=None,
                    artifact_name="full", aspect_ratio=aspect_ratio,
                )
                self.assertEqual((full.video_width, full.video_height), expected["full"])

    def test_rejects_dimensions_outside_the_profile_with_the_spec_messages(self) -> None:
        """Preview e full fora do perfil falham com a proporção do perfil no texto."""

        with self.assertRaises(CliError) as raised:
            self.check((405, 720), canvas_width=404, canvas_height=720, aspect_ratio="9:16")
        self.assertEqual(raised.exception.code, "RENDER_FAILED")
        self.assertEqual(
            raised.exception.message,
            "A preview renderizada não respeita o perfil 9:16 de até 720 pixels.",
        )

        with self.assertRaises(CliError) as raised:
            self.check(
                (1_080, 1_920), canvas_width=1_080, canvas_height=1_350, max_dimension=None,
                artifact_name="full", aspect_ratio="4:5",
            )
        self.assertEqual(raised.exception.message, "A full renderizada não respeita o perfil final 4:5.")

        with self.assertRaises(CliError) as raised:
            self.check((None, None), canvas_width=720, canvas_height=720, aspect_ratio="1:1")
        self.assertEqual(
            raised.exception.message,
            "A preview renderizada não respeita o perfil 1:1 de até 720 pixels.",
        )

    def test_landscape_keeps_the_legacy_messages_and_ratio_tolerance(self) -> None:
        """No 16:9 (padrão do parâmetro) o texto vigente e a tolerância 0,001 da razão se mantêm."""

        with self.assertRaises(CliError) as raised:
            self.check((640, 360), canvas_width=704, canvas_height=396)
        self.assertEqual(
            raised.exception.message,
            "A preview renderizada não respeita o perfil 16:9 de até 720 pixels.",
        )
        with self.assertRaises(CliError) as raised:
            self.check(
                (1_920, 1_080), canvas_width=1_280, canvas_height=720, max_dimension=None,
                artifact_name="full",
            )
        self.assertEqual(raised.exception.message, "A full renderizada não respeita o perfil final 16:9.")
        # Canvas com razão distante de 16:9 não passa nem quando as dimensões coincidem.
        with self.assertRaises(CliError):
            self.check((704, 400), canvas_width=704, canvas_height=400)
        self.assertEqual(self.check((704, 396), canvas_width=704, canvas_height=396).video_width, 704)

    def test_preview_larger_than_the_cap_is_rejected_in_every_ratio(self) -> None:
        """O lado maior da preview continua limitado a 720, mesmo que o canvas informado seja maior."""

        with self.assertRaises(CliError) as raised:
            self.check((1_080, 1_920), canvas_width=1_080, canvas_height=1_920, aspect_ratio="9:16")
        self.assertEqual(
            raised.exception.message,
            "A preview renderizada não respeita o perfil 9:16 de até 720 pixels.",
        )


# --- 5. Regressão do 16:9 (A6) --------------------------------------------------------------


class LandscapeRegressionTests(AspectRatioCase):
    """Prova que um plano 16:9 mantém o canvas e o grafo de filtros anteriores à T1."""

    @classmethod
    def setUpClass(cls) -> None:
        """Gera, além das fontes base, as duas fontes sem áudio do plano golden (1280x720 e 640x480)."""

        super().setUpClass()
        make_source(cls.sources_dir / "golden-wide.mp4", 1_280, 720, audio=False)
        make_source(cls.sources_dir / "golden-fourthree.mp4", 640, 480, audio=False)

    @staticmethod
    def golden_plan() -> dict[str, object]:
        """Plano 16:9 sem áudio com `preserve`, `crop`, `contain` e `motion`, os quatro caminhos do canvas."""

        def mute() -> dict[str, object]:
            return {"mode": "mute", "tracks": []}

        return {
            "schema_version": 2,
            "profile": {"aspect_ratio": "16:9"},
            "sources": [
                {"id": "wide", "kind": "video", "path": "sources/golden-wide.mp4"},
                {"id": "fourthree", "kind": "video", "path": "sources/golden-fourthree.mp4"},
            ],
            "segments": [
                {"video": {"source_id": "wide", "in_s": 0, "out_s": 0.4, "fit": "preserve"}, "audio": mute()},
                {"video": {"source_id": "fourthree", "in_s": 0, "out_s": 0.4, "fit": "crop"}, "audio": mute()},
                {
                    "video": {
                        "source_id": "fourthree", "in_s": 0.4, "out_s": 0.9, "fit": "contain",
                        "motion": {
                            "from": {"x": 0.5, "y": 0.5, "zoom": 1.0},
                            "to": {"x": 0.25, "y": 0.75, "zoom": 1.5},
                        },
                    },
                    "audio": mute(),
                },
            ],
            "cuts": [],
            "layers": [],
            "audio_processing": {
                "normalize_loudness": False, "target_lufs": -16,
                "limiter": {"enabled": False, "ceiling_dbfs": -1}, "ducking": False,
            },
        }

    def test_landscape_canvas_and_filter_graph_are_unchanged(self) -> None:
        """Canvas de preview/full e `filter_complex` do plano 16:9 batem com o registrado antes da T1."""

        workdir = self.new_workdir(self.golden_plan())
        for name in ("golden-wide", "golden-fourthree"):
            shutil.copy2(self.sources_dir / f"{name}.mp4", workdir / "sources" / f"{name}.mp4")
        document, validation = load_validated_plan(workdir)

        preview_canvas = _preview_dimensions(document, validation)
        full_canvas = (validation["profile"]["full_width"], validation["profile"]["full_height"])
        self.assertEqual(preview_canvas, GOLDEN_PREVIEW_CANVAS)
        self.assertEqual(full_canvas, GOLDEN_FULL_CANVAS)
        self.assertEqual(validation["profile"]["effective_full_width"], 1_280)

        for label, (width, height), golden in (
            ("preview", preview_canvas, GOLDEN_PREVIEW_GRAPH_SHA256),
            ("full", full_canvas, GOLDEN_FULL_GRAPH_SHA256),
        ):
            with self.subTest(artifact=label):
                graph = build_ffmpeg_graph(document, validation, canvas_width=width, canvas_height=height)
                digest = hashlib.sha256(graph.filter_complex.encode("utf-8")).hexdigest()
                self.assertEqual(digest, golden)

    def test_landscape_plan_still_renders_at_the_legacy_resolution(self) -> None:
        """A full 16:9 sai na largura da fonte (sem upscale) e a preview em 704x396, como antes."""

        workdir = self.new_workdir(self.golden_plan())
        for name in ("golden-wide", "golden-fourthree"):
            shutil.copy2(self.sources_dir / f"{name}.mp4", workdir / "sources" / f"{name}.mp4")
        preview = render_preview(workdir)
        approve_workdir(workdir)
        full = render_full(workdir)

        self.assertEqual((preview["video"]["width"], preview["video"]["height"]), (704, 396))
        self.assertEqual((full["video"]["width"], full["video"]["height"]), (1_280, 720))


if __name__ == "__main__":
    unittest.main()
