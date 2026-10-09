"""Testes da T7 da phase 5: sombra e contorno na camada de texto (A12 e decisão 18 da spec).

Cobre, nesta ordem:

1. Validação: `layers[].shadow` e `layers[].outline` normalizados com os padrões, hash canônico
   diferente do plano sem eles e cada violação recusada com `PLAN_INVALID` ANTES de inspecionar
   mídia (o inspetor de mídia é um espião e nenhuma fonte existe em disco), inclusive em camadas
   `image` e `video`, que não aceitam os campos.
2. Grafo: sem estilo o `filter_complex` da full é o de antes (hash dos goldens da T3), o contorno é o
   `borderw` do `drawtext`, a sombra é uma superfície própria desfocada e composta atrás do texto, e
   a preview escala todos os valores em pixels pela mesma regra do `size_px`.
3. Render real: uma só fonte clara com seis letreiros "I" lado a lado (um por cenário), renderizada
   em preview e full, 16:9 e 9:16. O "I" em Arial Bold é uma haste vertical, então contorno e sombra
   aparecem como faixas de largura conhecida ao lado dela e são medidas em pixels de quadros brutos
   (nada de comparar hash de MP4, que é com perda).

Medição: o quadro é lido em RGB e a luma é recalculada; a haste é o retângulo de pixels brancos
dentro da caixa do letreiro. Contorno vermelho (luma ~76) e sombra (cinza ~112, ou vermelha) são
"escuros" (luma abaixo de 150) contra o fundo claro (224). Tolerância de 2 px nas larguras das
faixas: a borda de cada faixa tem um pixel de anti-aliasing e o x264 em crf 23 espalha o croma em
blocos de 2 px. A razão entre preview e full usa a tolerância da spec (A6/A12): 2% da largura do
quadro.

A fonte e os quadros são gerados aqui por `ffmpeg -f lavfi`, numa pasta descartável removida ao final.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import unittest
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies
from editor.contracts import CliError
from editor.integrity.approval import approve_workdir
from editor.plan import schema as plan_schema
from editor.plan.canonical import canonical_sha256
from editor.plan.validation import load_validated_plan, validate_plan
from editor.render.ffmpeg_graph import SHADOW_SIGMA_PER_BLUR_PX, _scaled_style_pixels, build_ffmpeg_graph
from editor.render.full import render_full
from editor.render.preview import render_preview
from tests import test_reframe as reframe_helpers
from tests.test_preview_render import (
    GOLDEN_WORKFLOW_FULL_TEXT_GRAPH_SHA256_1280,
    WORKFLOW_PLAN,
    copy_workflow,
    with_full_width,
    workspace_tempdir,
)
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]


# --- Camada de texto de referência e helpers de plano ---------------------------------------------


def text_layer(**extra: Any) -> dict[str, Any]:
    """Camada de texto válida e simples; `extra` acrescenta (ou troca) campos, como `shadow` e `outline`."""

    return {
        "type": "text", "start_s": 0.2, "end_s": 1.3,
        "x": 0.1, "y": 0.5, "width": 0.8, "height": 0.2,
        "text": "Estilo", "font": "Arial", "size_px": 60,
        "color": "#FFFFFF", "align": "center", "wrap": True,
        **extra,
    }


def plan_with_layers(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """Plano 16:9 de um segmento mudo de 2 s sobre a fonte simulada `1920x1080` com as camadas dadas."""

    plan = reframe_helpers.plan_document("16:9", [{"source": "1920x1080", "fit": "preserve"}])
    plan["layers"] = layers
    return plan


# --- 1. Validação ----------------------------------------------------------------------------------


class TextStyleValidationTests(reframe_helpers.GridCase):
    """Cobre a validação: normalização, hash canônico e violações recusadas antes da mídia."""

    def load_with_fake_media(self, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Valida o plano com metadados simulados (sem FFprobe) e devolve o documento e o resultado interno."""

        workdir = self.fake_media_workdir(plan)
        with patch("editor.plan.validation.media.inspect_source", side_effect=reframe_helpers.fake_inspect_by_name):
            return load_validated_plan(workdir)

    def test_defaults_are_applied_and_indexed_by_layer(self) -> None:
        """`color` e `opacity` da sombra e `color` do contorno assumem os padrões; só camadas com estilo entram."""

        plan = plan_with_layers([
            text_layer(),
            text_layer(
                shadow={"offset_x_px": 4, "offset_y_px": -6, "blur_px": 8},
                outline={"width_px": 3},
            ),
            text_layer(outline={"width_px": 2, "color": "#ff0000"}),
        ])
        _, validation = self.load_with_fake_media(plan)

        self.assertEqual(
            validation["_text_styles"],
            {
                1: {
                    "shadow": {
                        "offset_x_px": 4, "offset_y_px": -6, "blur_px": 8,
                        "color": plan_schema.DEFAULT_SHADOW_COLOR, "opacity": plan_schema.DEFAULT_SHADOW_OPACITY,
                    },
                    "outline": {"width_px": 3, "color": plan_schema.DEFAULT_OUTLINE_COLOR},
                },
                2: {"outline": {"width_px": 2, "color": "#ff0000"}},
            },
        )
        self.assertEqual((plan_schema.DEFAULT_SHADOW_COLOR, plan_schema.DEFAULT_SHADOW_OPACITY), ("#000000", 0.5))
        self.assertEqual(plan_schema.DEFAULT_OUTLINE_COLOR, "#000000")
        self.assertEqual(validation["layer_count"], 3)

    def test_explicit_values_and_rounding_are_kept(self) -> None:
        """Valores explícitos passam como escritos e a opacidade é arredondada a 6 casas como os demais números."""

        plan = plan_with_layers([text_layer(shadow={
            "offset_x_px": 10, "offset_y_px": 12, "blur_px": 5, "color": "#112233", "opacity": 0.12345678,
        })])
        _, validation = self.load_with_fake_media(plan)

        self.assertEqual(
            validation["_text_styles"][0]["shadow"],
            {"offset_x_px": 10, "offset_y_px": 12, "blur_px": 5, "color": "#112233", "opacity": 0.123457},
        )

    def test_plans_without_style_have_no_style_and_the_hash_is_the_document_hash(self) -> None:
        """Sem `shadow` e `outline` o mapa é vazio; com eles o hash difere e continua sendo o do documento."""

        base = plan_with_layers([text_layer()])
        shadowed = plan_with_layers([text_layer(shadow={"offset_x_px": 1, "offset_y_px": 1, "blur_px": 0})])
        outlined = plan_with_layers([text_layer(outline={"width_px": 1})])
        moved = plan_with_layers([text_layer(shadow={"offset_x_px": 2, "offset_y_px": 1, "blur_px": 0})])

        results = [self.load_with_fake_media(plan)[1] for plan in (base, shadowed, outlined, moved)]

        self.assertEqual(results[0]["_text_styles"], {})
        self.assertEqual(results[0]["plan_sha256"], canonical_sha256(base))
        self.assertEqual(len({result["plan_sha256"] for result in results}), 4)
        # O envelope público não ganha campo novo: o estilo normalizado é privado, como o caminho da fonte.
        public = validate_plan_public(self, shadowed)
        self.assertNotIn("_text_styles", public)

    def test_boundary_values_are_accepted(self) -> None:
        """Os extremos de cada faixa são válidos: offsets -100 e 100, blur 0 e 50, opacidade 0 e 1, largura 1 e 30."""

        plan = plan_with_layers([
            text_layer(
                shadow={"offset_x_px": plan_schema.SHADOW_OFFSET_MIN, "offset_y_px": plan_schema.SHADOW_OFFSET_MAX,
                        "blur_px": plan_schema.SHADOW_BLUR_MIN, "opacity": 0},
                outline={"width_px": plan_schema.OUTLINE_WIDTH_MIN},
            ),
            text_layer(
                shadow={"offset_x_px": plan_schema.SHADOW_OFFSET_MAX, "offset_y_px": plan_schema.SHADOW_OFFSET_MIN,
                        "blur_px": plan_schema.SHADOW_BLUR_MAX, "opacity": 1},
                outline={"width_px": plan_schema.OUTLINE_WIDTH_MAX},
            ),
        ])
        _, validation = self.load_with_fake_media(plan)

        low, high = validation["_text_styles"][0], validation["_text_styles"][1]
        self.assertEqual((low["shadow"]["opacity"], high["shadow"]["opacity"]), (0.0, 1.0))
        self.assertEqual((low["outline"]["width_px"], high["outline"]["width_px"]), (1, 30))
        self.assertEqual((plan_schema.SHADOW_OFFSET_MIN, plan_schema.SHADOW_OFFSET_MAX), (-100, 100))
        self.assertEqual((plan_schema.SHADOW_BLUR_MIN, plan_schema.SHADOW_BLUR_MAX), (0, 50))
        self.assertEqual((plan_schema.OUTLINE_WIDTH_MIN, plan_schema.OUTLINE_WIDTH_MAX), (1, 30))

    def test_every_violation_is_plan_invalid_before_probing_media(self) -> None:
        """Cada regra recusa com `PLAN_INVALID` e mensagem segura sem chamar o inspetor de mídia.

        Nenhuma fonte existe em disco: se a validação chegasse à mídia (ou à resolução da fonte
        textual), o erro seria outro. O espião `inspect_source` também não pode ser chamado.
        """

        ok_shadow = {"offset_x_px": 3, "offset_y_px": 3, "blur_px": 2}
        integer_x = "O campo layers[].shadow.offset_x_px deve ser um inteiro entre -100 e 100."
        integer_y = "O campo layers[].shadow.offset_y_px deve ser um inteiro entre -100 e 100."
        integer_blur = "O campo layers[].shadow.blur_px deve ser um inteiro entre 0 e 50."
        integer_width = "O campo layers[].outline.width_px deve ser um inteiro entre 1 e 30."
        shadow_color = "O campo layers[].shadow.color deve estar no formato hexadecimal #RRGGBB."
        outline_color = "O campo layers[].outline.color deve estar no formato hexadecimal #RRGGBB."
        opacity_range = "O campo layers[].shadow.opacity deve estar entre 0 e 1."
        not_object = "deve ser um objeto JSON."
        cases: list[tuple[str, dict[str, Any], str]] = [
            # (nome, campos extras da camada de texto, trecho esperado da mensagem)
            ("shadow: campo desconhecido", {"shadow": {**ok_shadow, "spread_px": 2}}, "campo não permitido em layers[].shadow"),
            ("shadow: objeto vazio", {"shadow": {}}, "O campo layers[].shadow.blur_px é obrigatório."),
            ("shadow: sem offset_x_px", {"shadow": {"offset_y_px": 1, "blur_px": 1}}, "O campo layers[].shadow.offset_x_px é obrigatório."),
            ("shadow: sem offset_y_px", {"shadow": {"offset_x_px": 1, "blur_px": 1}}, "O campo layers[].shadow.offset_y_px é obrigatório."),
            ("shadow: sem blur_px", {"shadow": {"offset_x_px": 1, "offset_y_px": 1}}, "O campo layers[].shadow.blur_px é obrigatório."),
            ("shadow: nulo", {"shadow": None}, not_object),
            ("shadow: lista", {"shadow": [1, 1, 0]}, not_object),
            ("shadow: texto", {"shadow": "suave"}, not_object),
            ("shadow: booleano", {"shadow": True}, not_object),
            ("offset_x_px: texto", {"shadow": {**ok_shadow, "offset_x_px": "5"}}, integer_x),
            ("offset_x_px: decimal", {"shadow": {**ok_shadow, "offset_x_px": 5.5}}, integer_x),
            ("offset_x_px: decimal inteiro", {"shadow": {**ok_shadow, "offset_x_px": 5.0}}, integer_x),
            ("offset_x_px: booleano", {"shadow": {**ok_shadow, "offset_x_px": True}}, integer_x),
            ("offset_x_px: nulo", {"shadow": {**ok_shadow, "offset_x_px": None}}, integer_x),
            ("offset_x_px: acima de 100", {"shadow": {**ok_shadow, "offset_x_px": 101}}, integer_x),
            ("offset_x_px: abaixo de -100", {"shadow": {**ok_shadow, "offset_x_px": -101}}, integer_x),
            ("offset_y_px: booleano", {"shadow": {**ok_shadow, "offset_y_px": False}}, integer_y),
            ("offset_y_px: acima de 100", {"shadow": {**ok_shadow, "offset_y_px": 1_000}}, integer_y),
            ("offset_y_px: abaixo de -100", {"shadow": {**ok_shadow, "offset_y_px": -100.5}}, integer_y),
            ("blur_px: negativo", {"shadow": {**ok_shadow, "blur_px": -1}}, integer_blur),
            ("blur_px: acima de 50", {"shadow": {**ok_shadow, "blur_px": 51}}, integer_blur),
            ("blur_px: decimal", {"shadow": {**ok_shadow, "blur_px": 1.5}}, integer_blur),
            ("blur_px: booleano", {"shadow": {**ok_shadow, "blur_px": True}}, integer_blur),
            ("shadow.color: nome", {"shadow": {**ok_shadow, "color": "black"}}, shadow_color),
            ("shadow.color: curta", {"shadow": {**ok_shadow, "color": "#12345"}}, shadow_color),
            ("shadow.color: sem #", {"shadow": {**ok_shadow, "color": "112233"}}, shadow_color),
            ("shadow.color: não hexadecimal", {"shadow": {**ok_shadow, "color": "#GGGGGG"}}, shadow_color),
            ("shadow.color: quebra de linha final", {"shadow": {**ok_shadow, "color": "#112233\n"}}, shadow_color),
            ("shadow.color: injeção de filtro", {"shadow": {**ok_shadow, "color": "#000000,drawtext"}}, shadow_color),
            ("shadow.color: número", {"shadow": {**ok_shadow, "color": 123456}}, shadow_color),
            ("shadow.color: nulo", {"shadow": {**ok_shadow, "color": None}}, shadow_color),
            ("opacity: acima de 1", {"shadow": {**ok_shadow, "opacity": 1.01}}, opacity_range),
            ("opacity: negativa", {"shadow": {**ok_shadow, "opacity": -0.01}}, opacity_range),
            ("opacity: booleana", {"shadow": {**ok_shadow, "opacity": True}}, "deve ser um número finito."),
            ("opacity: texto", {"shadow": {**ok_shadow, "opacity": "0.5"}}, "deve ser um número finito."),
            ("opacity: nula", {"shadow": {**ok_shadow, "opacity": None}}, "deve ser um número finito."),
            ("outline: campo desconhecido", {"outline": {"width_px": 2, "style": "dashed"}}, "campo não permitido em layers[].outline"),
            ("outline: objeto vazio", {"outline": {}}, "O campo layers[].outline.width_px é obrigatório."),
            ("outline: só cor", {"outline": {"color": "#FF0000"}}, "O campo layers[].outline.width_px é obrigatório."),
            ("outline: nulo", {"outline": None}, not_object),
            ("outline: lista", {"outline": [2]}, not_object),
            ("outline: número", {"outline": 2}, not_object),
            ("width_px: zero", {"outline": {"width_px": 0}}, integer_width),
            ("width_px: negativo", {"outline": {"width_px": -2}}, integer_width),
            ("width_px: acima de 30", {"outline": {"width_px": 31}}, integer_width),
            ("width_px: decimal", {"outline": {"width_px": 2.5}}, integer_width),
            ("width_px: texto", {"outline": {"width_px": "2"}}, integer_width),
            ("width_px: booleano", {"outline": {"width_px": True}}, integer_width),
            ("width_px: nulo", {"outline": {"width_px": None}}, integer_width),
            ("outline.color: inválida", {"outline": {"width_px": 2, "color": "vermelho"}}, outline_color),
            ("outline.color: nula", {"outline": {"width_px": 2, "color": None}}, outline_color),
            # A sombra válida não mascara um contorno inválido na mesma camada.
            ("outline inválido com shadow válido", {"shadow": ok_shadow, "outline": {"width_px": 0}}, integer_width),
        ]
        for name, extra, expected in cases:
            with self.subTest(case=name):
                workdir = self.new_workdir(plan_with_layers([text_layer(**extra)]))  # nenhuma fonte em disco
                with patch("editor.plan.validation.media.inspect_source") as inspect:
                    with self.assertRaises(CliError) as raised:
                        validate_plan(workdir)
                self.assertEqual(raised.exception.code, "PLAN_INVALID")
                self.assertIn(expected, raised.exception.message)
                inspect.assert_not_called()

    def test_error_messages_never_echo_the_rejected_value(self) -> None:
        """A mensagem nomeia o campo e a regra, mas não devolve o valor recebido (que pode ser texto livre)."""

        sentinel = "VALOR-RECUSADO-123"
        for extra in (
            {"shadow": {"offset_x_px": sentinel, "offset_y_px": 1, "blur_px": 1}},
            {"shadow": {"offset_x_px": 1, "offset_y_px": 1, "blur_px": 1, "color": sentinel}},
            {"shadow": {"offset_x_px": 1, "offset_y_px": 1, "blur_px": 1, "opacity": sentinel}},
            {"shadow": {"offset_x_px": 1, "offset_y_px": 1, "blur_px": 1, sentinel: 1}},
            {"outline": {"width_px": sentinel}},
            {"outline": {"width_px": 1, "color": sentinel}},
            {"outline": {"width_px": 1, sentinel: 1}},
        ):
            with self.subTest(extra=sorted(extra)):
                workdir = self.new_workdir(plan_with_layers([text_layer(**extra)]))
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)
                self.assertEqual(raised.exception.code, "PLAN_INVALID")
                self.assertNotIn(sentinel, raised.exception.message)

    def test_style_is_refused_on_image_and_video_layers_before_probing_media(self) -> None:
        """`shadow` e `outline` só existem em camada `text`: em `image` e `video` são campos não permitidos."""

        box = {"start_s": 0.2, "end_s": 1.3, "x": 0.1, "y": 0.1, "width": 0.3, "height": 0.3}
        layers = {
            "image": {"type": "image", "source_id": "logo", "fit": "contain", **box},
            "video": {"type": "video", "source_id": "clip", "in_s": 0, "out_s": 1.1, "fit": "crop", **box},
        }
        styles = {
            "shadow": {"offset_x_px": 3, "offset_y_px": 3, "blur_px": 2},
            "outline": {"width_px": 2},
        }
        for layer_type, layer in layers.items():
            for field, value in styles.items():
                with self.subTest(layer=layer_type, field=field):
                    workdir = self.new_workdir(plan_with_layers([{**layer, field: value}]))
                    with patch("editor.plan.validation.media.inspect_source") as inspect:
                        with self.assertRaises(CliError) as raised:
                            validate_plan(workdir)
                    self.assertEqual(raised.exception.code, "PLAN_INVALID")
                    self.assertIn("O plano contém campo não permitido em layers[].", raised.exception.message)
                    inspect.assert_not_called()


def validate_plan_public(case: reframe_helpers.GridCase, plan: dict[str, Any]) -> dict[str, Any]:
    """Valida o plano com mídia simulada e devolve o envelope público (sem as chaves privadas)."""

    workdir = case.fake_media_workdir(plan)
    with patch("editor.plan.validation.media.inspect_source", side_effect=reframe_helpers.fake_inspect_by_name):
        return validate_plan(workdir)


# --- 2. Grafo --------------------------------------------------------------------------------------


class TextStyleGraphTests(unittest.TestCase):
    """O grafo é o de antes sem estilo e, com estilo, tem as ramificações e os valores escalados esperados."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFprobe local (o plano do fixture é validado de verdade) e o fixture do workflow."""

        if dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffprobe não está disponível localmente")
        if not WORKFLOW_PLAN.is_file():
            raise unittest.SkipTest("fixture do workflow não está disponível")

    def graph(self, **style: dict[str, Any]) -> tuple[str, str]:
        """Grafos da full (1280x720) e da preview (704x396) do plano do workflow com o estilo na camada de texto."""

        with workspace_tempdir() as workdir:
            copy_workflow(workdir)
            plan = json.loads((workdir / "plan.json").read_text(encoding="utf-8"))
            plan["layers"][0].update(style)
            (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
            document, validation = load_validated_plan(workdir)
            full = build_ffmpeg_graph(
                document, with_full_width(validation, 1_280), canvas_width=1_280, canvas_height=720
            )
            preview = build_ffmpeg_graph(
                document, with_full_width(validation, 1_280), canvas_width=704, canvas_height=396
            )
        return full.filter_complex, preview.filter_complex

    def test_without_style_the_full_graph_is_the_recorded_one(self) -> None:
        """Compatibilidade: sem `shadow` e `outline` o `filter_complex` da full tem o hash anterior ao estilo."""

        full, _ = self.graph()

        self.assertEqual(
            hashlib.sha256(full.encode("utf-8")).hexdigest(), GOLDEN_WORKFLOW_FULL_TEXT_GRAPH_SHA256_1280
        )
        self.assertNotIn("borderw", full)
        self.assertNotIn("gblur", full)
        self.assertNotIn("textshadow", full)

    def test_outline_only_adds_the_drawtext_border_and_keeps_one_surface(self) -> None:
        """O contorno é `borderw`/`bordercolor` do próprio `drawtext`, sem superfície nem desfoque extras."""

        plain, _ = self.graph()
        full, preview = self.graph(outline={"width_px": 6, "color": "#FF0000"})

        self.assertIn("fontcolor=#FFFFFF:borderw=6:bordercolor=#FF0000:text=", full)
        self.assertEqual(full.count("drawtext="), 1)
        self.assertNotIn("gblur", full)
        self.assertNotIn("textshadow", full)
        # A preview usa a largura escalada: 6 x 704 / 1280 = 3,3.
        self.assertIn("borderw=3:bordercolor=#FF0000", preview)
        # Só a superfície do texto muda: o resto do grafo (da composição em diante) é o mesmo.
        self.assertEqual(
            full.split("[textlayer0];")[1], plain.split("[textlayer0];")[1],
            "a composição da camada sobre o vídeo deve continuar igual",
        )

    def test_shadow_is_a_blurred_offset_copy_composed_behind_the_text(self) -> None:
        """A sombra é uma superfície na cor dela, deslocada, desfocada e com alfa, sob o texto nítido."""

        full, _ = self.graph(shadow={
            "offset_x_px": 20, "offset_y_px": -8, "blur_px": 10, "color": "#112233", "opacity": 0.4,
        })

        parts = full.split(";")
        shadow_surface = next(part for part in parts if part.endswith("[textshadow0]"))
        face_surface = next(part for part in parts if part.endswith("[textface0]"))
        behind = next(part for part in parts if part.endswith("[textlayer0]"))
        # Superfície da sombra: nasce na cor da sombra (alfa 0), texto opaco deslocado, `gblur` com
        # sigma = blur / 2 e o alfa multiplicado pela opacidade uma única vez, depois do desfoque.
        self.assertTrue(shadow_surface.startswith("color=c=#112233@0:"), shadow_surface)
        self.assertIn("fontcolor=#112233:text=", shadow_surface)
        self.assertIn("x=w-text_w+20:y=0-8", shadow_surface)
        self.assertIn(f"gblur=sigma={10 * SHADOW_SIGMA_PER_BLUR_PX:g}", shadow_surface)
        self.assertEqual(SHADOW_SIGMA_PER_BLUR_PX, 0.5)
        self.assertTrue(shadow_surface.endswith("colorchannelmixer=aa=0.4[textshadow0]"), shadow_surface)
        self.assertLess(shadow_surface.index("gblur"), shadow_surface.index("colorchannelmixer"))
        # O `trim` no início da camada (0,5 s no fixture) vem antes do `gblur`, o passo caro, para o
        # desfoque não rodar nos quadros anteriores à camada.
        self.assertIn("trim=start=0.5,gblur=", shadow_surface)
        # Texto nítido: posição sem deslocamento e na cor do plano.
        self.assertTrue(face_surface.startswith("color=c=black@0:"), face_surface)
        self.assertIn("fontcolor=#FFFFFF:text=", face_surface)
        self.assertTrue(face_surface.endswith("x=w-text_w:y=0[textface0]"), face_surface)
        # Composição: sombra atrás (primeira entrada), texto na frente, e o rótulo de saída é o de sempre.
        self.assertTrue(behind.startswith("[textshadow0][textface0]overlay=format=auto[textlayer0]"), behind)
        self.assertIn("[vconcat][textlayer0]overlay=x=", full)
        self.assertIn("[vtext0]", full)

    def test_sharp_shadow_has_no_blur_and_the_shadow_copy_carries_the_outline(self) -> None:
        """Com `blur_px` 0 não há `gblur`; com contorno a cópia da sombra o repete na cor da sombra."""

        full, _ = self.graph(
            shadow={"offset_x_px": 5, "offset_y_px": 5, "blur_px": 0, "color": "#000000"},
            outline={"width_px": 4, "color": "#FF0000"},
        )

        self.assertNotIn("gblur", full)
        self.assertEqual(full.count("drawtext="), 2)
        self.assertIn("fontcolor=#000000:borderw=4:bordercolor=#000000:text=", full)  # cópia da sombra
        self.assertIn("fontcolor=#FFFFFF:borderw=4:bordercolor=#FF0000:text=", full)  # texto nítido

    def test_preview_scales_every_pixel_value_like_the_font_size(self) -> None:
        """Na preview deslocamentos, desfoque e contorno saem escalados pela razão das larguras (704/1280)."""

        style = {
            "shadow": {"offset_x_px": 20, "offset_y_px": -20, "blur_px": 20, "opacity": 0.5},
            "outline": {"width_px": 20},
        }
        full, preview = self.graph(**style)

        # Full: valores declarados. Preview: 20 x 704 / 1280 = 11 em todos (sinal preservado).
        self.assertIn("x=w-text_w+20:y=0-20", full)
        self.assertIn(f"gblur=sigma={20 * SHADOW_SIGMA_PER_BLUR_PX:g},", full)
        self.assertIn("borderw=20", full)
        self.assertIn("x=w-text_w+11:y=0-11", preview)
        self.assertIn(f"gblur=sigma={11 * SHADOW_SIGMA_PER_BLUR_PX:g},", preview)
        self.assertIn("borderw=11", preview)
        self.assertNotIn("borderw=20", preview)

    def test_scaled_style_pixels_keeps_sign_zero_and_minimum_one(self) -> None:
        """Regra da decisão 18: arredondado como o `size_px`, mínimo 1 em magnitude, sinal e zero preservados."""

        self.assertEqual(_scaled_style_pixels(20, 704, 1_280), 11)  # 11,0
        self.assertEqual(_scaled_style_pixels(-20, 704, 1_280), -11)
        self.assertEqual(_scaled_style_pixels(20, 404, 1_080), 7)  # 7,48
        self.assertEqual(_scaled_style_pixels(5, 1, 2), 3)  # 2,5 arredonda para cima
        self.assertEqual(_scaled_style_pixels(-5, 1, 2), -3)  # simétrico, afastando do zero
        self.assertEqual(_scaled_style_pixels(1, 404, 1_080), 1)  # 0,37 sobe ao mínimo
        self.assertEqual(_scaled_style_pixels(-1, 404, 1_080), -1)
        self.assertEqual(_scaled_style_pixels(0, 404, 1_080), 0)
        self.assertEqual(_scaled_style_pixels(0, 1_280, 1_280), 0)
        # Na full (fator 1) todo valor válido passa sem alteração, inclusive negativos e o zero.
        for value in range(-plan_schema.SHADOW_OFFSET_MAX, plan_schema.SHADOW_OFFSET_MAX + 1):
            self.assertEqual(_scaled_style_pixels(value, 1_080, 1_080), value)
            self.assertEqual(_scaled_style_pixels(value, 1_280, 1_280), value)


# --- 3. Render real: preview e full, 16:9 e 9:16 ---------------------------------------------------

BACKGROUND_LUMA = 224
"""Luma do fundo (cinza claro 0xE0E0E0): claro o bastante para a sombra e o contorno se destacarem."""

DARK_LUMA = 150
"""Abaixo disto o pixel é contorno ou sombra (vermelho ~76, sombra preta a 50% ~112, preta ~0)."""

WHITE_GREEN = 244
"""Canal verde acima disto é a haste branca do texto (o fundo tem 224 e o vermelho tem 0)."""

MIN_STEM_PIXELS = 5
"""Pixels brancos mínimos para uma linha ou coluna contar como haste (a haste mais fina mede 10 px)."""

STRIP_TOLERANCE_PX = 2
"""Tolerância da largura de uma faixa: um pixel de anti-aliasing por borda e o croma 4:2:0 do x264."""

FRACTION_TOLERANCE = 0.02
"""Tolerância da spec (A12): 2% da largura do quadro entre a razão da preview e a da full."""

SAMPLE_SECONDS = 0.7
"""Instante com os letreiros visíveis (as camadas vão de 0,2 s a 1,3 s)."""

LAYER_BOX_WIDTH = 0.15
LAYER_BOX_STEP = 0.16
"""Cada cenário é um letreiro "I" numa caixa de 15% da largura do quadro, em colunas de passo 16%."""

LAYER_Y = 0.3
LAYER_HEIGHT = 0.5
SIZE_PX = 240

SCENARIOS: dict[str, dict[str, Any]] = {
    "outline": {"outline": {"width_px": 20, "color": "#FF0000"}},
    "shadow": {"shadow": {"offset_x_px": 20, "offset_y_px": 16, "blur_px": 0}},
    "shadow_opacity_zero": {"shadow": {"offset_x_px": 20, "offset_y_px": 16, "blur_px": 0, "opacity": 0}},
    "shadow_blur": {"shadow": {"offset_x_px": 12, "offset_y_px": 0, "blur_px": 20}},
    "shadow_red_left": {
        "shadow": {"offset_x_px": -20, "offset_y_px": 16, "blur_px": 0, "color": "#FF0000", "opacity": 1}
    },
    "shadow_with_outline": {
        "shadow": {"offset_x_px": 20, "offset_y_px": 0, "blur_px": 0, "color": "#000000", "opacity": 1},
        "outline": {"width_px": 8, "color": "#000000"},
    },
}
"""Estilo de cada letreiro; a ordem define a coluna. Valores em pixels da largura da full."""

ASPECT_CASES = {
    # proporção: (fit da fonte, canvas da preview, canvas da full)
    "16:9": ("preserve", (704, 396), (1_280, 720)),
    "9:16": ("crop", (404, 720), (1_080, 1_920)),
}


def create_light_source(path: Path, frame_rate: int = 25) -> None:
    """Gera a fonte 1280x720 cinza claro, sem áudio, para que só o texto estilizado apareça no quadro."""

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=0xE0E0E0:s=1280x720:r={frame_rate}:d=1.5",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.decode(errors="replace"))


def style_render_plan(aspect_ratio: str, fit: str) -> dict[str, Any]:
    """Plano de um segmento mudo com um letreiro "I" estilizado por cenário, lado a lado."""

    layers = []
    for index, style in enumerate(SCENARIOS.values()):
        layers.append({
            "type": "text", "start_s": 0.2, "end_s": 1.3,
            "x": round(0.02 + index * LAYER_BOX_STEP, 2), "y": LAYER_Y,
            "width": LAYER_BOX_WIDTH, "height": LAYER_HEIGHT,
            "text": "I", "font": "Arial Bold", "size_px": SIZE_PX,
            "color": "#FFFFFF", "align": "center", "wrap": False, **copy.deepcopy(style),
        })
    return {
        "schema_version": 2,
        "profile": {"aspect_ratio": aspect_ratio},
        "sources": [{"id": "light", "kind": "video", "path": "sources/light.mp4"}],
        "segments": [{
            "video": {"source_id": "light", "in_s": 0, "out_s": 1.5, "fit": fit},
            "audio": {"mode": "mute", "tracks": []},
        }],
        "cuts": [],
        "layers": layers,
        "audio_processing": {
            "normalize_loudness": False, "target_lufs": -16,
            "limiter": {"enabled": False, "ceiling_dbfs": -1}, "ducking": False,
        },
    }


def read_rgb_frame(path: Path, width: int, height: int, frame_index: int | None = None) -> bytes:
    """Decodifica um quadro RGB de 8 bits do vídeo: o de índice `frame_index` ou o do instante `SAMPLE_SECONDS`."""

    seek = ["-vf", f"select=eq(n\,{frame_index})"] if frame_index is not None else []
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            *([] if frame_index is not None else ["-ss", str(SAMPLE_SECONDS)]),
            "-i", str(path), *seek, "-frames:v", "1", "-fps_mode", "passthrough",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
        check=True,
    )
    if len(result.stdout) != width * height * 3:
        raise AssertionError(f"quadro de {path.name} com tamanho inesperado")
    return result.stdout


class Frame:
    """Quadro RGB bruto com leitura de luma por pixel e medição das faixas ao redor da haste do "I"."""

    def __init__(self, data: bytes, width: int, height: int) -> None:
        """Guarda os bytes RGB (3 por pixel, linha a linha) e as dimensões do quadro."""

        self.data = data
        self.width = width
        self.height = height

    def rgb(self, x: int, y: int) -> tuple[int, int, int]:
        """Cor (R, G, B) do pixel (x, y)."""

        offset = (y * self.width + x) * 3
        return self.data[offset], self.data[offset + 1], self.data[offset + 2]

    def luma(self, x: int, y: int) -> int:
        """Luma BT.601 do pixel (x, y), de 0 a 255."""

        red, green, blue = self.rgb(x, y)
        return (299 * red + 587 * green + 114 * blue) // 1000

    def box(self, index: int) -> tuple[int, int, int, int]:
        """Caixa em pixels `(esquerda, direita, topo, base)` do letreiro do cenário `index` neste quadro.

        Mesma regra de arredondamento da caixa da camada (início truncado, fim arredondado para cima),
        escrita aqui de forma independente do grafo.
        """

        x = round(0.02 + index * LAYER_BOX_STEP, 2)
        return (
            int(x * self.width),
            min(self.width, math.ceil((x + LAYER_BOX_WIDTH) * self.width)),
            int(LAYER_Y * self.height),
            min(self.height, math.ceil((LAYER_Y + LAYER_HEIGHT) * self.height)),
        )

    def stem(self, index: int) -> tuple[int, int, int, int]:
        """Retângulo `(esquerda, direita, topo, base)` dos pixels brancos da haste dentro da caixa do cenário.

        `direita` e `base` são exclusivos. Falha se não houver haste, o que denuncia texto ausente.
        Colunas e linhas só contam com `MIN_STEM_PIXELS` pixels brancos: o x264 deixa pixels soltos
        de croma esverdeado nos cantos do contorno vermelho, que não fazem parte da haste.
        """

        left, right, top, bottom = self.box(index)
        columns = [0] * (right - left)
        rows = [0] * (bottom - top)
        for y in range(top, bottom):
            for x in range(left, right):
                if self.data[(y * self.width + x) * 3 + 1] > WHITE_GREEN:
                    columns[x - left] += 1
                    rows[y - top] += 1
        stem_columns = [left + offset for offset, count in enumerate(columns) if count >= MIN_STEM_PIXELS]
        stem_rows = [top + offset for offset, count in enumerate(rows) if count >= MIN_STEM_PIXELS]
        if not stem_columns or not stem_rows:
            raise AssertionError(f"nenhuma haste branca na caixa do cenário {index}")
        return min(stem_columns), max(stem_columns) + 1, min(stem_rows), max(stem_rows) + 1

    def dark_in_row(self, index: int, y: int, start: int, stop: int) -> list[int]:
        """Posições x escuras (luma abaixo de `DARK_LUMA`) da linha `y` entre `start` e `stop` (exclusivo).

        A janela é recortada pela caixa do cenário `index`, para a medição nunca ler o letreiro vizinho.
        """

        left, right, _, _ = self.box(index)
        return [x for x in range(max(left, start), min(right, stop)) if self.luma(x, y) < DARK_LUMA]

    def dark_in_column(self, index: int, x: int, start: int, stop: int) -> list[int]:
        """Posições y escuras da coluna `x` entre `start` e `stop` (exclusivo), recortadas pela caixa do cenário."""

        _, _, top, bottom = self.box(index)
        return [y for y in range(max(top, start), min(bottom, stop)) if self.luma(x, y) < DARK_LUMA]


def overlap_column(left: int, right: int, dx: int) -> int:
    """Coluna x dentro da haste `[left, right)` e da sombra dela deslocada `dx` px, onde se mede a faixa de baixo.

    A sombra desloca a haste inteira; só no cruzamento dos dois intervalos há sombra logo abaixo da haste.
    """

    first, last = max(left, left + dx), min(right, right + dx)
    if last - first < 2:
        raise AssertionError("a haste é estreita demais para o deslocamento medido")
    return (first + last) // 2


def round_half_up(value: float) -> int:
    """Arredonda meio para cima, a regra da spec (decisão 11), sem depender do código sob teste."""

    return int(math.floor(value + 0.5))


class TextStyleRenderTests(unittest.TestCase):
    """A12: contorno e sombra medidos em pixels de quadros reais da preview e da full, em 16:9 e 9:16."""

    @classmethod
    def setUpClass(cls) -> None:
        """Renderiza uma vez, por proporção, a preview e a full do plano com os seis cenários."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        cls.root = PROJECT_ROOT / f"tmp-text-style-{uuid4().hex}"
        cls.root.mkdir()
        cls.addClassCleanup(remove_tree, cls.root)
        cls.frames: dict[str, dict[str, Frame]] = {}
        for aspect_ratio, (fit, preview_size, full_size) in ASPECT_CASES.items():
            workdir = cls.root / aspect_ratio.replace(":", "x")
            (workdir / "sources").mkdir(parents=True)
            create_light_source(workdir / "sources" / "light.mp4")
            (workdir / "plan.json").write_text(
                json.dumps(style_render_plan(aspect_ratio, fit)), encoding="utf-8"
            )
            preview = render_preview(workdir)
            approve_workdir(workdir)
            full = render_full(workdir)
            if (preview["video"]["width"], preview["video"]["height"]) != preview_size:
                raise AssertionError(f"preview {aspect_ratio} com tamanho inesperado")
            if (full["video"]["width"], full["video"]["height"]) != full_size:
                raise AssertionError(f"full {aspect_ratio} com tamanho inesperado")
            cls.frames[aspect_ratio] = {
                "preview": Frame(read_rgb_frame(workdir / "preview.mp4", *preview_size), *preview_size),
                "full": Frame(read_rgb_frame(workdir / "full.mp4", *full_size), *full_size),
            }

    def expected_pixels(self, declared: int, kind: str, aspect_ratio: str) -> int:
        """Valor esperado em pixels: o declarado na full e, na preview, o escalado pela razão das larguras."""

        _, preview_size, full_size = ASPECT_CASES[aspect_ratio]
        if kind == "full":
            return declared
        return max(1, round_half_up(declared * preview_size[0] / full_size[0]))

    def each_frame(self, aspect_ratio: str) -> list[tuple[str, Frame]]:
        """Pares `(preview|full, quadro)` da proporção, na ordem em que as asserções os percorrem."""

        return [(kind, self.frames[aspect_ratio][kind]) for kind in ("preview", "full")]

    def assert_same_fraction(self, aspect_ratio: str, measured: dict[str, float]) -> None:
        """Preview e full têm a mesma fração da largura do quadro, dentro dos 2% da spec."""

        if set(measured) != {"preview", "full"}:
            return  # uma medição já falhou no subTest dela; a razão não tem o que comparar
        _, preview_size, full_size = ASPECT_CASES[aspect_ratio]
        self.assertAlmostEqual(
            measured["preview"] / preview_size[0], measured["full"] / full_size[0], delta=FRACTION_TOLERANCE
        )

    def test_outline_has_the_declared_color_and_thickness_around_the_letter(self) -> None:
        """Contorno vermelho de 20 px (full): faixa da largura esperada nos três lados e nenhum pixel além dela."""

        index = list(SCENARIOS).index("outline")
        declared = SCENARIOS["outline"]["outline"]["width_px"]
        for aspect_ratio in ASPECT_CASES:
            measured: dict[str, float] = {}
            for kind, frame in self.each_frame(aspect_ratio):
                with self.subTest(aspect_ratio=aspect_ratio, frame=kind):
                    width = self.expected_pixels(declared, kind, aspect_ratio)
                    left, right, top, bottom = frame.stem(index)
                    middle_row = (top + bottom) // 2
                    middle_column = (left + right) // 2  # sem deslocamento: o contorno cerca a haste inteira
                    sides = {
                        "esquerda": frame.dark_in_row(index, middle_row, left - width - 12, left),
                        "direita": frame.dark_in_row(index, middle_row, right, right + width + 12),
                        "baixo": frame.dark_in_column(index, middle_column, bottom, bottom + width + 12),
                    }
                    for side, dark in sides.items():
                        self.assertAlmostEqual(len(dark), width, delta=STRIP_TOLERANCE_PX, msg=side)
                    # Nenhum pixel de contorno além da espessura (com folga de 3 px de borda).
                    self.assertEqual(frame.dark_in_row(index, middle_row, left - width - 12, left - width - 3), [])
                    self.assertEqual(frame.dark_in_row(index, middle_row, right + width + 3, right + width + 12), [])
                    self.assertEqual(frame.dark_in_column(index, middle_column, bottom + width + 3, bottom + width + 12), [])
                    # Cor do contorno: vermelho no meio da faixa esquerda.
                    red, green, blue = frame.rgb(left - width // 2 - 1, middle_row)
                    self.assertTrue(red > 170 and green < 110 and blue < 110, (red, green, blue))
                    measured[kind] = float(len(sides["direita"]))
            self.assert_same_fraction(aspect_ratio, measured)

    def test_shadow_is_offset_down_and_right_with_the_declared_opacity(self) -> None:
        """Sombra preta a 50% deslocada (20, 16): faixa à direita e embaixo, nada à esquerda, cinza ~112."""

        index = list(SCENARIOS).index("shadow")
        shadow = SCENARIOS["shadow"]["shadow"]
        for aspect_ratio in ASPECT_CASES:
            horizontal: dict[str, float] = {}
            vertical: dict[str, float] = {}
            for kind, frame in self.each_frame(aspect_ratio):
                with self.subTest(aspect_ratio=aspect_ratio, frame=kind):
                    dx = self.expected_pixels(shadow["offset_x_px"], kind, aspect_ratio)
                    dy = self.expected_pixels(shadow["offset_y_px"], kind, aspect_ratio)
                    left, right, top, bottom = frame.stem(index)
                    middle_row = (top + bottom) // 2
                    middle_column = overlap_column(left, right, dx)
                    right_strip = frame.dark_in_row(index, middle_row, right, right + dx + 12)
                    bottom_strip = frame.dark_in_column(index, middle_column, bottom, bottom + dy + 12)
                    self.assertAlmostEqual(len(right_strip), dx, delta=STRIP_TOLERANCE_PX)
                    self.assertAlmostEqual(len(bottom_strip), dy, delta=STRIP_TOLERANCE_PX)
                    # O lado oposto (esquerda) não tem sombra.
                    self.assertEqual(frame.dark_in_row(index, middle_row, left - dx - 12, left), [])
                    # Opacidade 0.5 de preto sobre o fundo de luma 224 dá ~112 (tolerância de 14 níveis
                    # pela quantização do x264 e pelo alfa de 8 bits, 127,5).
                    levels = sorted(frame.luma(x, middle_row) for x in right_strip)
                    self.assertAlmostEqual(levels[len(levels) // 2], BACKGROUND_LUMA * 0.5, delta=14)
                    horizontal[kind] = float(len(right_strip))
                    vertical[kind] = float(len(bottom_strip))
            self.assert_same_fraction(aspect_ratio, horizontal)
            self.assert_same_fraction(aspect_ratio, vertical)

    def test_shadow_disappears_with_opacity_zero(self) -> None:
        """Com `opacity` 0 não sobra pixel de sombra em lado nenhum da haste."""

        index = list(SCENARIOS).index("shadow_opacity_zero")
        shadow = SCENARIOS["shadow_opacity_zero"]["shadow"]
        for aspect_ratio in ASPECT_CASES:
            for kind, frame in self.each_frame(aspect_ratio):
                with self.subTest(aspect_ratio=aspect_ratio, frame=kind):
                    dx = self.expected_pixels(shadow["offset_x_px"], kind, aspect_ratio)
                    dy = self.expected_pixels(shadow["offset_y_px"], kind, aspect_ratio)
                    left, right, top, bottom = frame.stem(index)
                    middle_row = (top + bottom) // 2
                    middle_column = overlap_column(left, right, dx)
                    self.assertEqual(frame.dark_in_row(index, middle_row, left - dx - 12, left), [])
                    self.assertEqual(frame.dark_in_row(index, middle_row, right, right + dx + 12), [])
                    self.assertEqual(frame.dark_in_column(index, middle_column, bottom, bottom + dy + 12), [])
                    around = [frame.luma(x, middle_row) for x in range(right + 2, right + dx + 10)]
                    self.assertGreaterEqual(min(around), BACKGROUND_LUMA - 8)

    def test_blur_spreads_the_shadow_into_a_gradient(self) -> None:
        """`blur_px` > 0 troca a borda dura por um gradiente medido em pixels, na mesma fração em preview e full."""

        sharp_index = list(SCENARIOS).index("shadow")
        blur_index = list(SCENARIOS).index("shadow_blur")
        # Preto a 50% dá luma ~112 no centro de uma sombra nítida. Pixel de gradiente é o que fica
        # entre o patamar (112 + 8) e o fundo (224 - 12), com margem para a quantização do x264.
        low, high = BACKGROUND_LUMA * 0.5 + 8, BACKGROUND_LUMA - 12

        def transition(frame: Frame, index: int, span: int) -> int:
            """Pixels à direita da haste com luma entre o patamar da sombra e o fundo (o gradiente)."""

            _, right, top, bottom = frame.stem(index)
            middle_row = (top + bottom) // 2
            box_right = frame.box(index)[1]
            return sum(
                1 for x in range(right, min(right + span, box_right)) if low < frame.luma(x, middle_row) < high
            )

        for aspect_ratio in ASPECT_CASES:
            measured: dict[str, float] = {}
            for kind, frame in self.each_frame(aspect_ratio):
                with self.subTest(aspect_ratio=aspect_ratio, frame=kind):
                    blur = self.expected_pixels(SCENARIOS["shadow_blur"]["shadow"]["blur_px"], kind, aspect_ratio)
                    span = 6 * blur  # bem além da cauda do gradiente, ainda dentro da caixa do letreiro
                    sharp = transition(frame, sharp_index, span)
                    blurred = transition(frame, blur_index, span)
                    self.assertLessEqual(sharp, 3, "a sombra nítida só tem o pixel de anti-aliasing da borda")
                    # O gradiente de um desfoque gaussiano de sigma = blur/2 ocupa bem mais que `blur`
                    # pixels (medido: 1,3 a 1,6 vezes); exige-se 75% disso e ao menos 3 pixels além da borda.
                    self.assertGreaterEqual(blurred, 0.75 * blur)
                    self.assertGreaterEqual(blurred, sharp + 3)
                    measured[kind] = float(blurred)
            self.assert_same_fraction(aspect_ratio, measured)

    def test_shadow_color_and_negative_offset_move_the_shadow_left(self) -> None:
        """Sombra vermelha opaca com `offset_x_px` -20: faixa vermelha à esquerda e embaixo, nada à direita."""

        index = list(SCENARIOS).index("shadow_red_left")
        shadow = SCENARIOS["shadow_red_left"]["shadow"]
        for aspect_ratio in ASPECT_CASES:
            measured: dict[str, float] = {}
            for kind, frame in self.each_frame(aspect_ratio):
                with self.subTest(aspect_ratio=aspect_ratio, frame=kind):
                    dx = self.expected_pixels(abs(shadow["offset_x_px"]), kind, aspect_ratio)
                    dy = self.expected_pixels(shadow["offset_y_px"], kind, aspect_ratio)
                    left, right, top, bottom = frame.stem(index)
                    middle_row = (top + bottom) // 2
                    middle_column = overlap_column(left, right, -dx)
                    left_strip = frame.dark_in_row(index, middle_row, left - dx - 12, left)
                    self.assertAlmostEqual(len(left_strip), dx, delta=STRIP_TOLERANCE_PX)
                    self.assertEqual(frame.dark_in_row(index, middle_row, right, right + dx + 12), [])
                    bottom_strip = frame.dark_in_column(index, middle_column, bottom, bottom + dy + 12)
                    self.assertAlmostEqual(len(bottom_strip), dy, delta=STRIP_TOLERANCE_PX)
                    red, green, blue = frame.rgb(left - dx // 2 - 1, middle_row)
                    self.assertTrue(red > 170 and green < 110 and blue < 110, (red, green, blue))
                    measured[kind] = float(len(left_strip))
            self.assert_same_fraction(aspect_ratio, measured)

    def test_first_visible_frame_has_text_and_shadow_when_start_is_off_the_25fps_grid(self) -> None:
        """Fonte de 60 fps e `start_s` 0,53 (fora da grade de 25 fps): o primeiro quadro da camada já tem texto e sombra.

        A superfície do texto é de 25 fps e a sombra é cortada por `trim` (para só ser desfocada a partir
        do início da camada). O `trim` mantém o quadro que contém `start_s` (0,52 s); se passasse a
        descartá-lo, o primeiro quadro visível (t = 0,5333 s, índice 32) cairia antes do primeiro quadro
        da sombra e a camada apareceria vazia por um quadro.
        """

        workdir = self.root / "off-grid"
        (workdir / "sources").mkdir(parents=True)
        create_light_source(workdir / "sources" / "light.mp4", frame_rate=60)
        plan = style_render_plan("16:9", "preserve")
        plan["layers"] = [{**plan["layers"][1], "start_s": 0.53, "x": 0.4, "width": 0.2}]  # cenário "shadow"
        (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        preview = render_preview(workdir)
        self.assertEqual(preview["video"]["frame_rate"], 60.0)

        width, height = ASPECT_CASES["16:9"][1]
        first = Frame(read_rgb_frame(workdir / "preview.mp4", width, height, frame_index=32), width, height)
        before = Frame(read_rgb_frame(workdir / "preview.mp4", width, height, frame_index=31), width, height)
        shadow = SCENARIOS["shadow"]["shadow"]
        dx = self.expected_pixels(shadow["offset_x_px"], "preview", "16:9")
        # A camada ocupa a faixa de 40% a 60% da largura; a caixa em pixels segue a regra do grafo.
        box = (int(0.4 * width), math.ceil(0.6 * width), int(LAYER_Y * height), math.ceil((LAYER_Y + LAYER_HEIGHT) * height))
        left, right, top, bottom = box
        middle_row = top + 40  # dentro da haste, que parte do topo da caixa e tem ~95 px de altura na preview
        # O quadro 31 (t = 0,5167 s) ainda não mostra a camada; o 32 (t = 0,5333 s) mostra a haste e a sombra.
        self.assertFalse(any(before.data[(middle_row * width + x) * 3 + 1] > WHITE_GREEN for x in range(left, right)))
        stem_pixels = [x for x in range(left, right) if first.data[(middle_row * width + x) * 3 + 1] > WHITE_GREEN]
        self.assertGreaterEqual(len(stem_pixels), MIN_STEM_PIXELS, "o primeiro quadro visível da camada está sem texto")
        shadow_pixels = [
            x for x in range(max(stem_pixels) + 1, right) if first.luma(x, middle_row) < DARK_LUMA
        ]
        self.assertAlmostEqual(len(shadow_pixels), dx, delta=STRIP_TOLERANCE_PX)

    def test_shadow_copy_includes_the_outline(self) -> None:
        """Sombra e contorno pretos e opacos: à direita a faixa escura soma o contorno e o deslocamento."""

        index = list(SCENARIOS).index("shadow_with_outline")
        style = SCENARIOS["shadow_with_outline"]
        for aspect_ratio in ASPECT_CASES:
            for kind, frame in self.each_frame(aspect_ratio):
                with self.subTest(aspect_ratio=aspect_ratio, frame=kind):
                    dx = self.expected_pixels(style["shadow"]["offset_x_px"], kind, aspect_ratio)
                    ring = self.expected_pixels(style["outline"]["width_px"], kind, aspect_ratio)
                    left, right, top, bottom = frame.stem(index)
                    middle_row = (top + bottom) // 2
                    strip = frame.dark_in_row(index, middle_row, right, right + ring + dx + 12)
                    # Contorno (ring) + deslocamento (dx): sem o contorno na cópia a faixa mediria só dx.
                    self.assertAlmostEqual(len(strip), ring + dx, delta=STRIP_TOLERANCE_PX + 1)
                    self.assertGreater(len(strip), dx + ring // 2)
                    # À esquerda só o contorno da letra (a sombra está à direita).
                    left_strip = frame.dark_in_row(index, middle_row, left - ring - 12, left)
                    self.assertAlmostEqual(len(left_strip), ring, delta=STRIP_TOLERANCE_PX)


if __name__ == "__main__":
    unittest.main()
