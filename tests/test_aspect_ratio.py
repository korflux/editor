"""Testes da T1 da phase 5: as quatro proporções no perfil, no canvas e na checagem do MP4.

Cobre, nesta ordem:

1. Tabela única de proporções e dimensões (`plan/schema.py`), sem FFmpeg.
2. Validação do perfil por `plan validate`: proporções aceitas, `max_full_width` só no 16:9,
   `fit: "preserve"` contra a proporção do perfil e envelope `data.profile`.
3. Render real de preview e full em 9:16, 1:1 e 4:5 com `crop` (sem barras) e `contain` (com
   barras), provado por FFprobe das dimensões e por inspeção de pixels.
3b. SAR 1:1 com ramos de `fit` misturados (R5): preview e full reais com `crop`, `contain`, `preserve`,
   `reframe` e `motion` no mesmo plano, publicados e declarados com pixel quadrado por FFprobe.
4. Mensagens da checagem do MP4 renderizado, inclusive a exigência de H.264/AAC (R7).
5. Regressão do 16:9 (A6): o canvas e o grafo de filtros de um plano 16:9 são os mesmos de antes.
6. Largura da full 16:9 (T5, A9, decisão 16): canvas legado (alinhado a 16) preservado sempre que a
   checagem do MP4 o aceita e alinhamento a 32 só onde ele seria recusado; tabela de equivalência
   contra a regra antiga e jornada real das fontes de 1200x674 e de 3184x1790.

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

from dataclasses import replace
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
    FULL_FALLBACK_WIDTH_ALIGNMENT,
    FULL_WIDTH_ALIGNMENT,
    LANDSCAPE_ASPECT_RATIO,
    LANDSCAPE_RATIO_TOLERANCE,
    MAX_FULL_WIDTH_STEP,
    MAX_MAX_FULL_WIDTH,
    MIN_MAX_FULL_WIDTH,
    PREVIEW_WIDTH_ALIGNMENT,
    aspect_ratio_value,
    canvas_dimensions,
)
from editor.plan.validation import load_validated_plan, validate_plan
from editor.render.ffmpeg_graph import build_ffmpeg_graph
from editor.render.full import render_full
from editor.render.preview import (
    MAX_PREVIEW_DIMENSION,
    _preview_dimensions,
    _require_renderable_canvas,
    _validate_rendered_preview,
    render_preview,
)
from tests.support import remove_tree


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
# R5 (re-review final): os dois hashes foram regravados só porque o grafo ganhou `setsar=1` ao fim dos
# ramos `crop`, `contain` e `preserve` (o texto de cada ramo é o anterior mais essa inserção; removê-la
# devolve os hashes antigos 2889f5d4... e 5f4bff18...). A decisão 14 (full 16:9 de plano existente
# inalterada) foi provada por PIXELS: `framemd5` de vídeo e de áudio da preview e da full deste plano,
# da fixture `workflow`, da `e2e` e de um plano com texto são idênticos antes e depois (SAR já 1:1).
GOLDEN_PREVIEW_GRAPH_SHA256 = "bc6363f67e485cc239641990b42630dd969c2ffc539f66d85a2b58aec7859d76"
GOLDEN_FULL_GRAPH_SHA256 = "80af524289963d02faac5bd3da719ff5d634afb4ceef65ba617f8c06b58743a8"


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
        cls.addClassCleanup(remove_tree, cls.root)
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
        """Fixa a regra vigente do 16:9 (sem upscale; preview a 32; full a 16, com fallback a 32, T5)."""

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


# --- 3b. SAR 1:1 com ramos de `fit` misturados (R5 da re-review final) -----------------------

MIXED_SOURCE_SIZES = {
    "hd": (1_920, 1_080),
    "tall1080": (1_080, 1_920),
    "odd": (1_000, 562),
}
"""Fontes onde a escala para o canvas é inexata: 1080p e 1000x562 deixam o `scale` gravar SAR != 1:1."""

BRANCH_VIDEO_FIELDS: dict[str, dict[str, object]] = {
    "crop": {"fit": "crop"},
    "contain": {"fit": "contain"},
    "preserve": {"fit": "preserve"},
    "reframe": {"fit": "crop", "reframe": {"keyframes": [{"t_s": 0, "x": 0.3, "y": 0.5}]}},
    "motion": {
        "fit": "crop",
        "motion": {"from": {"x": 0.5, "y": 0.5, "zoom": 1.0}, "to": {"x": 0.4, "y": 0.5, "zoom": 1.5}},
    },
}
"""Campos de `segments[].video` de cada ramo do `_video_segment_filter`. `reframe` e `motion` terminam
num `overlay` sobre `color` (SAR 1:1); `crop`, `contain` e `preserve` terminam no `crop`/`pad` da fonte."""


def mixed_plan(aspect_ratio: str, *branches: tuple[str, str]) -> dict[str, object]:
    """Plano com um segmento por par `(source_id, ramo)`, cada um com o áudio AAC da própria fonte."""

    sources: dict[str, dict[str, str]] = {}
    segments = []
    for source_id, branch in branches:
        sources[source_id] = {"id": source_id, "kind": "video", "path": f"sources/{source_id}.mp4"}
        segments.append({
            "video": {"source_id": source_id, "in_s": 0, "out_s": SEGMENT_SECONDS, **BRANCH_VIDEO_FIELDS[branch]},
            "audio": keep_audio(source_id),
        })
    plan = single_segment_plan(aspect_ratio, "hd", "crop")
    plan["sources"] = list(sources.values())
    plan["segments"] = segments
    return plan


def sample_aspect_ratio(path: Path) -> str | None:
    """SAR declarado no MP4 pelo FFprobe (`1:1` quando o pixel é quadrado), ou `None` se ausente."""

    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=sample_aspect_ratio", "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)["streams"][0].get("sample_aspect_ratio")


class MixedFitSampleAspectRatioTests(AspectRatioCase):
    """R5: todo ramo do `fit` termina em pixel quadrado, então misturar ramos publica e declara SAR 1:1.

    Causa raiz provada aqui: o `setsar=1` só vinha ANTES do `scale`, e um `scale` de razão inexata (1080p
    para 1080x1920, 1000x562 para 1280x720...) grava SAR != 1:1 no fim do ramo. O `concat` recusa
    segmentos de SAR diferentes (os ramos `reframe` e `motion` saem do `overlay` com SAR 1:1) e o MP4
    publicado de um ramo só saía com SAR como 10240:10239.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """Gera, além das fontes base, as três fontes de escala inexata."""

        super().setUpClass()
        for name, (width, height) in MIXED_SOURCE_SIZES.items():
            make_source(cls.sources_dir / f"{name}.mp4", width, height)

    def assert_publishes_with_square_pixels(self, plan: dict[str, object]) -> None:
        """Renderiza preview, aprova e renderiza a full; as duas publicam e declaram SAR 1:1."""

        source_ids = [str(source["id"]) for source in plan["sources"]]
        workdir = self.new_workdir(plan, *source_ids)
        preview = render_preview(workdir)
        approve_workdir(workdir)
        full = render_full(workdir)
        for name, payload in (("preview.mp4", preview), ("full.mp4", full)):
            with self.subTest(artifact=name):
                self.assertEqual(sample_aspect_ratio(workdir / name), "1:1")
                metadata = inspect_source(workdir / name, require_audio=True, require_video=True)
                self.assertEqual(
                    (metadata.video_width, metadata.video_height),
                    (payload["video"]["width"], payload["video"]["height"]),
                )

    def test_mixing_branches_publishes_in_every_ratio(self) -> None:
        """9:16, 1:1 e 4:5 sobre 1080p: `crop` com `reframe`, `motion` e `contain`, e `reframe` com `contain`."""

        combinations = (
            ("crop", "reframe"),
            ("crop", "motion"),
            ("crop", "contain"),
            ("reframe", "contain"),
        )
        for aspect_ratio in ("9:16", "1:1", "4:5"):
            for first, second in combinations:
                with self.subTest(aspect_ratio=aspect_ratio, branches=f"{first}+{second}"):
                    self.assert_publishes_with_square_pixels(
                        mixed_plan(aspect_ratio, ("hd", first), ("hd", second))
                    )

    def test_single_branch_plans_declare_square_pixels(self) -> None:
        """Um ramo só também sai em 1:1: antes o SAR publicado era 10240:10239, 908:909, 1216:1215..."""

        cases = (
            ("9:16", "hd", "crop"),
            ("9:16", "hd", "contain"),
            ("1:1", "hd", "contain"),
            ("4:5", "hd", "contain"),
            ("9:16", "tall1080", "preserve"),
            ("16:9", "odd", "crop"),
        )
        for aspect_ratio, source_id, branch in cases:
            with self.subTest(aspect_ratio=aspect_ratio, source=source_id, branch=branch):
                self.assert_publishes_with_square_pixels(mixed_plan(aspect_ratio, (source_id, branch)))

    def test_preserve_of_a_native_vertical_source_mixes_with_crop(self) -> None:
        """9:16 `preserve` de fonte 1080x1920 (escala inexata na preview) com um trecho `crop` de 1080p."""

        self.assert_publishes_with_square_pixels(
            mixed_plan("9:16", ("tall1080", "preserve"), ("hd", "crop"))
        )

    def test_landscape_source_off_the_exact_ratio_mixes_crop_and_motion(self) -> None:
        """16:9 com fonte 1000x562, `crop` e `crop` com `motion`: já falhava em `eaa8473`, antes da phase 5."""

        self.assert_publishes_with_square_pixels(
            mixed_plan("16:9", ("odd", "crop"), ("odd", "motion"))
        )


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

    def test_rendered_artifact_must_be_h264_and_aac(self) -> None:
        """R7: HEVC no vídeo ou áudio fora do AAC não é publicado, com mensagem fixa e sem eco do codec.

        A allowlist das fontes (`media.py`) aceita HEVC desde a T8 e é a que `inspect_rendered_artifact`
        aplica; sem a conferência própria de `_validate_rendered_preview` um MP4 renderizado em HEVC passava.
        """

        video_only = self.metadata(704, 396)
        with_audio = replace(video_only, audio_codec="aac")
        rejected = (
            ("vídeo HEVC", replace(video_only, video_codec="hevc"), False),
            ("vídeo HEVC com áudio AAC", replace(with_audio, video_codec="hevc"), True),
            ("áudio MP3", replace(with_audio, audio_codec="mp3"), True),
            ("sem codec de vídeo", replace(video_only, video_codec=None), False),
        )
        for name, metadata, expected_audio in rejected:
            for artifact_name, max_dimension, message in (
                ("preview", 720, "A preview renderizada não usa os codecs H.264 e AAC do contrato."),
                ("full", None, "A full renderizada não usa os codecs H.264 e AAC do contrato."),
            ):
                with self.subTest(case=name, artifact=artifact_name):
                    with patch(
                        "editor.render.preview.media.inspect_rendered_artifact", return_value=metadata
                    ):
                        with self.assertRaises(CliError) as raised:
                            _validate_rendered_preview(
                                Path("staging.mp4"), expected_duration=0.6,
                                expected_audio=expected_audio, canvas_width=704, canvas_height=396,
                                max_dimension=max_dimension, artifact_name=artifact_name,
                            )
                    self.assertEqual(raised.exception.code, "RENDER_FAILED")
                    self.assertEqual(raised.exception.message, message)
        with patch("editor.render.preview.media.inspect_rendered_artifact", return_value=with_audio):
            accepted = _validate_rendered_preview(
                Path("staging.mp4"), expected_duration=0.6, expected_audio=True,
                canvas_width=704, canvas_height=396,
            )
        self.assertEqual((accepted.video_codec, accepted.audio_codec), ("h264", "aac"))


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


# --- 6. Largura da full 16:9 (T5, A9, decisão 16) -------------------------------------------

LEGACY_FULL_WIDTH_ALIGNMENT = 16
"""Alinhamento da full 16:9 antes da T5; só existe aqui, como parte do oráculo."""


def legacy_full_canvas(source_width: int, max_width: int) -> tuple[int, int]:
    """Canvas da full 16:9 anterior à T5, reimplementado como oráculo da tabela de equivalência.

    Cópia literal da regra antiga de `schema.canvas_dimensions` (`final=True`): largura limitada
    por `max_width`, alinhada a 16, e altura `largura * 9 // 16` reduzida a par. Não importa
    nada de `schema.py` de propósito, para que a prova não mude junto com a regra nova.
    """

    width = min(source_width, max_width)
    width -= width % LEGACY_FULL_WIDTH_ALIGNMENT
    height = width * 9 // 16
    height -= height % 2
    return width, height


def full_canvas_is_renderable(canvas: tuple[int, int]) -> bool:
    """Diz se uma full com este canvas passaria pelas checagens reais do render.

    Usa as mesmas funções que `render full` aplica (`_require_renderable_canvas` antes do grafo e
    `_validate_rendered_preview` sobre o MP4 publicado, com a tolerância de razão intacta),
    alimentando a segunda com os metadados que um MP4 exatamente nesse canvas teria.
    """

    width, height = canvas
    if (width % 2, height % 2) != (0, 0):
        return False
    try:
        _require_renderable_canvas(width, height, "full")
        with patch(
            "editor.render.preview.media.inspect_rendered_artifact",
            return_value=RenderedArtifactCheckTests.metadata(width, height),
        ):
            _validate_rendered_preview(
                Path("staging.mp4"), expected_duration=0.6, expected_audio=False,
                canvas_width=width, canvas_height=height, max_dimension=None, artifact_name="full",
            )
    except CliError:
        return False
    return True


class FullWidthTableTests(unittest.TestCase):
    """Prova, sem FFmpeg, que a full 16:9 só muda onde o canvas legado seria recusado."""

    def test_constants_keep_the_legacy_alignment_and_a_fallback_equal_to_the_preview(self) -> None:
        """A full segue em 16 (full inalterada, decisão 14), com 32 de fallback e tolerância 0,001."""

        self.assertEqual(FULL_WIDTH_ALIGNMENT, 16)
        self.assertEqual(FULL_FALLBACK_WIDTH_ALIGNMENT, 32)
        self.assertEqual(FULL_FALLBACK_WIDTH_ALIGNMENT, PREVIEW_WIDTH_ALIGNMENT)
        # A tolerância da checagem do MP4 não foi relaxada ao virar constante.
        self.assertEqual(LANDSCAPE_RATIO_TOLERANCE, 0.001)

    def test_rejected_legacy_canvases_fall_back_to_32_and_accepted_ones_are_kept(self) -> None:
        """1200 vira 1184x666 (legado recusado); 3184x1790, 3600x2024 e 3824x2150 não mudam."""

        cases = (
            # (largura da fonte, teto, canvas legado, canvas novo)
            (1_200, 1_920, (1_200, 674), (1_184, 666)),
            (1_360, 1_920, (1_360, 764), (1_344, 756)),
            (1_680, 1_920, (1_680, 944), (1_664, 936)),
            (1_200, 3_840, (1_200, 674), (1_184, 666)),
            (2_000, 1_200, (1_200, 674), (1_184, 666)),
            (3_152, 3_840, (3_152, 1_772), (3_136, 1_764)),
            # Largura múltipla de 32: o legado é exato e é mantido.
            (1_280, 1_920, (1_280, 720), (1_280, 720)),
            (3_856, 3_840, (3_840, 2_160), (3_840, 2_160)),
            # Múltiplas ímpares de 16 de 3184 a 3824: o legado passa pela tolerância e é mantido.
            (3_184, 3_840, (3_184, 1_790), (3_184, 1_790)),
            (3_600, 3_616, (3_600, 2_024), (3_600, 2_024)),
            (3_824, 3_840, (3_824, 2_150), (3_824, 2_150)),
        )
        for source_width, max_width, legacy, expected in cases:
            with self.subTest(source_width=source_width, max_width=max_width):
                self.assertEqual(legacy_full_canvas(source_width, max_width), legacy)
                self.assertEqual(
                    canvas_dimensions(LANDSCAPE_ASPECT_RATIO, source_width, max_width, final=True),
                    expected,
                )

    def test_preview_canvas_is_unchanged_by_the_full_rule(self) -> None:
        """A preview segue alinhada a 32 com altura exata, inclusive nas larguras que a full recua."""

        for source_width, expected in ((1_200, (704, 396)), (3_184, (704, 396)), (320, (320, 180))):
            with self.subTest(source_width=source_width):
                self.assertEqual(
                    canvas_dimensions(LANDSCAPE_ASPECT_RATIO, source_width, MAX_PREVIEW_DIMENSION, final=False),
                    expected,
                )

    def test_every_width_and_cap_keeps_the_legacy_canvas_when_accepted_and_is_exact_otherwise(self) -> None:
        """Tabela completa: toda largura de fonte de 2 a 3840 contra todo `max_full_width` válido.

        Os tetos válidos são os múltiplos de 32 de 32 a 3840 (inclui 32, 640, 1280, 1920 e 3840).
        O canvas legado é julgado pelas checagens reais do render, e o canvas novo é:

        1. IDÊNTICO ao legado sempre que o legado era renderável, inclusive as 21 larguras
           múltiplas ímpares de 16 de 3184 a 3824, que passam pela tolerância (3184x1790);
        2. onde o legado era recusado (o defeito, 1200x674), de largura múltipla de 32 (a maior que
           não excede a fonte nem o teto, sem upscale), altura par, razão exata
           `largura * 9 == altura * 16` e aceito pelas checagens reais; é o legado menos 16 pixels
           de largura. Fonte menor que 32 pixels gera 0x0, recusado como na preview.
        """

        landscape = LANDSCAPE_ASPECT_RATIO
        caps = range(MIN_MAX_FULL_WIDTH, MAX_MAX_FULL_WIDTH + 1, MAX_FULL_WIDTH_STEP)
        for named_cap in (32, 640, 1_280, 1_920, 3_840):
            self.assertIn(named_cap, caps)

        # Cada canvas distinto é julgado uma só vez pelas checagens reais (poucas centenas).
        renderable: dict[tuple[int, int], bool] = {}

        def is_renderable(canvas: tuple[int, int]) -> bool:
            if canvas not in renderable:
                renderable[canvas] = full_canvas_is_renderable(canvas)
            return renderable[canvas]

        violations: list[str] = []
        pairs = identical = rejected = changed = degenerate = 0
        kept_by_tolerance: set[int] = set()
        for max_width in caps:
            for source_width in range(2, 3_841):
                pairs += 1
                new = canvas_dimensions(landscape, source_width, max_width, final=True)
                old = legacy_full_canvas(source_width, max_width)
                new_width, new_height = new
                old_width = old[0]
                limit = min(source_width, max_width)
                where = f"fonte {source_width}, teto {max_width}: novo {new}, legado {old}"

                if is_renderable(old):
                    # Grupo 1: o legado já era gerado, então a full é a mesma de antes.
                    identical += 1
                    if new != old:
                        violations.append(f"{where}: canvas legado renderável mudou")
                    if old_width % 32 == 16:
                        kept_by_tolerance.add(old_width)
                    continue

                rejected += 1
                if limit < 32:
                    # Menor que um bloco de 32: canvas degenerado, recusado como na preview.
                    degenerate += 1
                    if new != (0, 0) or is_renderable(new):
                        violations.append(f"{where}: canvas degenerado deveria ser (0, 0) e recusado")
                    continue
                # Grupo 2: o fallback de largura múltipla de 32, exato e aceito pelo render.
                if new_width % 32 != 0 or new_height % 2 != 0 or new_width * 9 != new_height * 16:
                    violations.append(f"{where}: novo não é 16:9 exato, par e múltiplo de 32")
                if not limit - 32 < new_width <= limit:
                    violations.append(f"{where}: novo não é a maior largura alinhada sem upscale")
                if not is_renderable(new):
                    violations.append(f"{where}: novo não passa nas checagens do render")
                if old_width % 32 != 16 or new_width != old_width - 16:
                    violations.append(f"{where}: mudança diferente de um degrau de 16 pixels")
                changed += 1

        self.assertEqual(violations[:10], [])
        self.assertEqual(pairs, len(caps) * (3_841 - 2))
        self.assertEqual(identical + rejected, pairs)
        # A tabela não é vacuosa: cada grupo e as larguras degeneradas aparecem.
        for count in (identical, rejected, changed, degenerate):
            self.assertGreater(count, 0)
        # As 21 larguras de 3184 a 3824 foram preservadas pela regra (decisão 14), e só elas.
        self.assertEqual(kept_by_tolerance, set(range(3_184, 3_825, 32)))


class FullWidthRenderTests(AspectRatioCase):
    """Jornadas reais: 1200x674 (legado recusado, fallback) e 3184x1790 (legado preservado), A9."""

    @classmethod
    def setUpClass(cls) -> None:
        """Gera, além das fontes base, as fontes H.264/AAC de 1200x674 e de 3184x1790."""

        super().setUpClass()
        make_source(cls.sources_dir / "odd16.mp4", 1_200, 674)
        make_source(cls.sources_dir / "tolerance.mp4", 3_184, 1_790)

    def assert_real_full(
        self, source_id: str, expected: tuple[int, int], **profile_extra: object
    ) -> None:
        """Valida, gera preview, aprova e renderiza a full; confere envelope e arquivo (FFprobe).

        `data.profile` de `plan validate` deve coincidir com o canvas da full renderizada, e o MP4
        sai H.264/AAC no canvas esperado, sem relaxar a checagem de razão da publicação.
        """

        plan = single_segment_plan("16:9", source_id, "crop", **profile_extra)
        workdir = self.new_workdir(plan, source_id)
        validated = validate_plan(workdir)["profile"]
        self.assertEqual((validated["full_width"], validated["full_height"]), expected)

        render_preview(workdir)
        approve_workdir(workdir)
        full = render_full(workdir)

        self.assertEqual((full["video"]["width"], full["video"]["height"]), expected)
        self.assertEqual(full["profile"], validated)
        metadata = inspect_source(workdir / "full.mp4", require_audio=True, require_video=True)
        self.assertEqual((metadata.video_width, metadata.video_height), expected)
        self.assertEqual((metadata.video_codec, metadata.audio_codec), ("h264", "aac"))
        self.assertLessEqual(
            abs(metadata.video_width / metadata.video_height - 16 / 9), LANDSCAPE_RATIO_TOLERANCE
        )

    def test_source_1200x674_renders_a_1184x666_full_with_exact_ratio(self) -> None:
        """Antes da T5 a full falhava com RENDER_FAILED; agora sai em 1184x666, razão 16:9 exata."""

        self.assert_real_full("odd16", (1_184, 666))
        self.assertEqual(1_184 * 9, 666 * 16)

    def test_source_3184x1790_keeps_the_legacy_full_canvas(self) -> None:
        """O legado 3184x1790 passa pela tolerância e já era gerado: a full continua a mesma."""

        self.assert_real_full("tolerance", (3_184, 1_790), max_full_width=3_200)

    def test_real_cli_plan_validate_reports_the_canvas_before_rendering(self) -> None:
        """O contrato público já mostra 1184x666 em `plan validate`, antes de qualquer render."""

        workdir = self.new_workdir(single_segment_plan("16:9", "odd16", "crop"), "odd16")
        result = subprocess.run(
            [sys.executable, "-m", "editor", "plan", "validate", "--workdir", str(workdir.resolve()), "--json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        profile = payload["data"]["profile"]
        self.assertEqual((profile["full_width"], profile["full_height"]), (1_184, 666))


if __name__ == "__main__":
    unittest.main()
