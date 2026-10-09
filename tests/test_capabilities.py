"""Testes do contrato local de capacidades e da recomendação determinística."""

from __future__ import annotations

import json
from contextlib import contextmanager
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Iterator
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies as editor_dependencies
from editor import media
from editor.analysis.frames import FRAME_PIXEL_FORMAT
from editor.capabilities import choose_recommendation, collect_capabilities
from editor.plan import schema as plan_schema
from editor.render.preview import MAX_PREVIEW_DIMENSION
from editor.whisper_backend import resolve_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCUMENTATION_PATHS = (
    PROJECT_ROOT / "skills" / "vibe-editor" / "SKILL.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "installation.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "youtube-editing.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "canal-sonni-viaja.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "source-preparation.md",
    PROJECT_ROOT / ".vibeflow" / "REGRAS.md",
)


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para validar o contrato consumido por agentes."""

    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    """Cria fixture temporária dentro do workspace, compatível com a ACL do host gerenciado."""

    temporary_path = PROJECT_ROOT / f"tmp-capabilities-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


class CapabilitiesTests(unittest.TestCase):
    """Garante consulta local estável mesmo quando componentes opcionais estão ausentes."""

    def test_process_returns_stable_json_envelope(self) -> None:
        """Confirma que a CLI real retorna capacidades sem prompt, traceback ou stderr."""

        result = run_module("capabilities", "--json")
        payload = json.loads(result.stdout)
        data = payload["data"]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["command"], "capabilities")
        self.assertEqual(data["cli_version"], "1.0.0")
        for key in (
            "system",
            "dependencies",
            "models",
            "devices",
            "formats",
            "limits",
            "operations",
            "recommendation",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["operations"]["capabilities"]["status"], "available")
        self.assertEqual(data["operations"]["init"]["status"], "available")
        self.assertEqual(data["plan"]["schema_version"], 2)
        # Phase 5 acrescentou campos ao perfil (proporções, canvas, upscale); a igualdade exata do
        # contrato anterior vale sobre as chaves que já existiam, que não foram removidas nem renomeadas.
        legacy_profile_keys = (
            "aspect_ratio",
            "max_full_width_default",
            "max_full_width_min",
            "max_full_width_max",
            "max_full_width_step",
        )
        self.assertEqual(
            {key: data["plan"]["profile"][key] for key in legacy_profile_keys},
            {
                "aspect_ratio": "16:9",
                "max_full_width_default": 1_920,
                "max_full_width_min": 32,
                "max_full_width_max": 3_840,
                "max_full_width_step": 32,
            },
        )
        self.assertEqual(data["render"]["filter_transport"], "file")
        self.assertEqual(data["render"]["preview_max_dimension"], 720)
        self.assertEqual(data["render"]["full_default_max_width"], 1_920)
        self.assertEqual(data["render"]["max_command_characters"], 32_767)
        self.assertEqual(data["transcription"]["word_drift_tolerance_seconds"], 0.5)
        self.assertEqual(
            data["transcription"]["counters"],
            ["discarded_word_count", "clamped_word_count", "discarded_segment_count"],
        )
        self.assertEqual(data["fonts"]["renderer"], "fontfile")
        self.assertEqual(data["limits"]["max_full_width_default"], 1_920)
        self.assertEqual(data["limits"]["max_command_characters"], 32_767)
        self.assertEqual(data["formats"]["image"], ["png", "jpg", "jpeg", "webp"])
        self.assertEqual(data["limits"]["max_layers"], 1_000)
        self.assertEqual(data["limits"]["max_cuts"], 999)
        self.assertEqual(data["limits"]["max_cut_duration_seconds"], 30.0)
        self.assertEqual(data["limits"]["motion_zoom_min"], 0.5)
        self.assertEqual(data["limits"]["motion_zoom_max"], 4.0)
        self.assertEqual(data["limits"]["max_image_size_bytes"], 100 * 1024**2)
        self.assertEqual(data["limits"]["max_image_dimension"], 8_192)
        self.assertNotIn("max_overlays", data["limits"])
        self.assertIn(data["recommendation"]["status"], {"recommended", "missing"})

        # Phase 4: operação `proxy build`, janelas de decode e `--proxy` da preview.
        proxy_build = data["operations"]["proxy_build"]
        self.assertIn(proxy_build["status"], {"available", "missing", "unsupported"})
        self.assertEqual(proxy_build["requires"], ["ffmpeg", "ffprobe", "libx264"])
        windows = data["render"]["decode_windows"]
        self.assertEqual(windows["default"], "on")
        self.assertEqual(windows["applies_to"], ["render_preview", "render_full"])
        self.assertEqual(windows["source_kinds"], ["video"])
        self.assertEqual(windows["merge_gap_seconds"], 2.0)
        self.assertEqual(windows["end_slack_seconds"], 0.5)
        self.assertEqual(windows["max_windows_per_source"], 8)
        self.assertEqual(windows["max_video_inputs"], 24)
        self.assertEqual(windows["min_read_reduction"], 0.2)
        self.assertEqual(windows["fallback"], "unwindowed")
        proxy = data["render"]["proxy"]
        self.assertEqual(proxy["flag"], "--proxy")
        self.assertEqual(proxy["modes"], ["auto", "off"])
        self.assertEqual(proxy["default"], "auto")
        self.assertEqual(proxy["applies_to"], ["render_preview"])
        self.assertEqual(proxy["build_command"], "proxy build")
        self.assertIs(proxy["automatic_build"], False)
        self.assertEqual(proxy["directory"], "proxy")
        self.assertEqual(
            proxy["eligibility"],
            {
                "source_kinds": ["video"],
                "width_greater_than": 1_280,
                "height_greater_than": 720,
                "match": "any_dimension",
            },
        )
        self.assertEqual(
            proxy["parameters"],
            {
                "version": 1,
                "max_width": 1_280,
                "max_height": 720,
                "upscale": False,
                "encoder": "libx264",
                "preset": "veryfast",
                "pixel_format": "yuv420p",
                "gop": 15,
                "crf": 20,
                "audio": "copy",
            },
        )

    def test_plan_exposes_aspect_ratios_canvas_and_reframe_from_the_constants(self) -> None:
        """Prova (A8) que proporções, dimensões e limites do `reframe` são os das constantes da validação."""

        with workspace_tempdir() as root:
            data = collect_capabilities(root)
        profile = data["plan"]["profile"]
        reframe = data["plan"]["reframe"]

        # As quatro proporções e, para cada uma, as dimensões de full e preview das mesmas constantes.
        self.assertEqual(profile["aspect_ratios"], ["16:9", "9:16", "1:1", "4:5"])
        self.assertEqual(profile["aspect_ratios"], list(plan_schema.ASPECT_RATIOS))
        self.assertEqual(profile["max_full_width_applies_to"], ["16:9"])
        self.assertEqual(list(profile["canvas"]), list(plan_schema.ASPECT_RATIOS))
        for aspect_ratio, fixed in plan_schema.FIXED_CANVAS_DIMENSIONS.items():
            canvas = profile["canvas"][aspect_ratio]
            self.assertEqual(canvas["kind"], "fixed")
            self.assertEqual((canvas["full"]["width"], canvas["full"]["height"]), fixed["full"])
            self.assertEqual((canvas["preview"]["width"], canvas["preview"]["height"]), fixed["preview"])
            # O lado maior da preview é o teto exposto em `render`, e a full é a de plataforma do plano.
            self.assertEqual(max(fixed["preview"]), MAX_PREVIEW_DIMENSION)
            self.assertEqual(
                plan_schema.canvas_dimensions(aspect_ratio, 3_840, None, final=True), fixed["full"]
            )
            self.assertEqual(
                plan_schema.canvas_dimensions(aspect_ratio, 3_840, None, final=False), fixed["preview"]
            )
        self.assertEqual(
            {
                ratio: (canvas["full"]["width"], canvas["full"]["height"])
                for ratio, canvas in profile["canvas"].items()
                if ratio != "16:9"
            },
            {"9:16": (1_080, 1_920), "1:1": (1_080, 1_080), "4:5": (1_080, 1_350)},
        )

        # O 16:9 é uma regra, e a regra exposta vale na função que o render e a validação usam.
        landscape = profile["canvas"]["16:9"]
        self.assertEqual(landscape["kind"], "source_width")
        self.assertIs(landscape["upscale"], False)
        self.assertEqual(landscape["full"]["max_width"], "profile.max_full_width")
        self.assertEqual(landscape["full"]["max_width_default"], plan_schema.DEFAULT_MAX_FULL_WIDTH)
        self.assertEqual(landscape["preview"]["max_width"], MAX_PREVIEW_DIMENSION)
        full_width, full_height = plan_schema.canvas_dimensions("16:9", 10_000, 1_920, final=True)
        self.assertEqual((full_width, full_height), (1_920, 1_080))
        self.assertEqual(full_width % landscape["full"]["width_alignment"], 0)
        # T5: a full alinha a 16 (canvas legado, preservado enquanto a checagem do MP4 o aceita) e a
        # 32, o da preview, onde o legado seria recusado; os dois valores expostos são os usados.
        self.assertEqual(landscape["full"]["width_alignment"], 16)
        self.assertEqual(landscape["full"]["width_alignment_fallback"], 32)
        self.assertEqual(
            landscape["full"]["width_alignment_fallback"], landscape["preview"]["width_alignment"]
        )
        self.assertEqual(plan_schema.canvas_dimensions("16:9", 1_200, 1_920, final=True), (1_184, 666))
        self.assertEqual(plan_schema.canvas_dimensions("16:9", 3_184, 3_840, final=True), (3_184, 1_790))
        preview_width, preview_height = plan_schema.canvas_dimensions(
            "16:9", 10_000, landscape["preview"]["max_width"], final=False
        )
        self.assertLessEqual(preview_width, MAX_PREVIEW_DIMENSION)
        self.assertEqual(preview_width % landscape["preview"]["width_alignment"], 0)
        self.assertEqual(preview_height, preview_width * 9 // 16)
        # Sem upscale: uma fonte menor que o teto mantém a própria largura.
        self.assertEqual(plan_schema.canvas_dimensions("16:9", 640, 1_920, final=True), (640, 360))

        # Campos novos de `data.profile` e o item de `upscale`.
        self.assertEqual(
            profile["envelope_fields"], ["aspect_ratio", "full_width", "full_height", "upscale"]
        )
        self.assertEqual(
            profile["envelope_fields_landscape_only"], ["max_full_width", "effective_full_width"]
        )
        self.assertEqual(
            profile["upscale"],
            {
                "envelope": "profile.upscale",
                "item_fields": ["segment", "factor"],
                "segment_base": 1,
                "factor_decimals": 2,
                "factor_greater_than": 1.0,
                "blocks_render": False,
            },
        )

        # `reframe`: limites, padrões e regras de convivência, todos das constantes do schema.
        self.assertEqual(reframe["status"], "available")
        self.assertEqual(reframe["field"], "segments[].video.reframe")
        self.assertEqual(reframe["keyframe_fields"], sorted(plan_schema.KEYFRAME_FIELDS))
        self.assertEqual(reframe["max_keyframes"], plan_schema.MAX_REFRAME_KEYFRAMES)
        self.assertEqual(reframe["zoom_min"], plan_schema.REFRAME_ZOOM_MIN)
        self.assertEqual(reframe["zoom_max"], plan_schema.REFRAME_ZOOM_MAX)
        self.assertEqual(reframe["zoom_default"], plan_schema.DEFAULT_REFRAME_ZOOM)
        self.assertEqual(reframe["interpolations"], list(plan_schema.REFRAME_INTERPOLATIONS))
        self.assertEqual(reframe["interpolations"], ["ease", "linear"])
        self.assertEqual(reframe["interpolation_default"], plan_schema.DEFAULT_REFRAME_INTERPOLATION)
        self.assertEqual(reframe["requires_fit"], "crop")
        self.assertEqual(reframe["excludes"], ["motion"])
        self.assertEqual(
            (reframe["max_keyframes"], reframe["zoom_min"], reframe["zoom_max"]), (32, 1.0, 4.0)
        )
        self.assertEqual(data["limits"]["reframe_max_keyframes"], plan_schema.MAX_REFRAME_KEYFRAMES)
        self.assertEqual(data["limits"]["reframe_zoom_min"], plan_schema.REFRAME_ZOOM_MIN)
        self.assertEqual(data["limits"]["reframe_zoom_max"], plan_schema.REFRAME_ZOOM_MAX)

        # `motion` segue como estava e o texto informa onde `size_px` é definido.
        self.assertEqual(data["plan"]["motion"]["zoom_min"], plan_schema.MOTION_ZOOM_MIN)
        self.assertEqual(data["plan"]["motion"]["zoom_max"], plan_schema.MOTION_ZOOM_MAX)
        self.assertEqual(data["render"]["text_size"]["size_px_reference"], "full_width")

    def test_plan_exposes_text_shadow_and_outline_from_the_constants(self) -> None:
        """Prova (A12) que as faixas, os campos e os padrões de `shadow` e `outline` são os das constantes da validação."""

        with workspace_tempdir() as root:
            data = collect_capabilities(root)
        style = data["plan"]["layers"]["text_style"]
        shadow, outline = style["shadow"], style["outline"]

        self.assertEqual(style["status"], "available")
        self.assertEqual(style["applies_to"], ["text"])
        self.assertEqual(style["pixel_reference"], "full_width")
        self.assertEqual(style["preview_scaling"], "proportional_to_width")
        self.assertEqual(style["clipped_by"], "layer_box")
        self.assertEqual(style["background_box"], "unsupported")

        self.assertEqual(shadow["field"], "layers[].shadow")
        self.assertEqual(shadow["fields"], sorted(plan_schema.SHADOW_FIELDS))
        self.assertEqual(shadow["required"], sorted(plan_schema.SHADOW_REQUIRED_FIELDS))
        self.assertEqual(shadow["offset_px_min"], plan_schema.SHADOW_OFFSET_MIN)
        self.assertEqual(shadow["offset_px_max"], plan_schema.SHADOW_OFFSET_MAX)
        self.assertEqual(shadow["blur_px_min"], plan_schema.SHADOW_BLUR_MIN)
        self.assertEqual(shadow["blur_px_max"], plan_schema.SHADOW_BLUR_MAX)
        self.assertEqual(shadow["color_default"], plan_schema.DEFAULT_SHADOW_COLOR)
        self.assertEqual(shadow["opacity_min"], plan_schema.SHADOW_OPACITY_MIN)
        self.assertEqual(shadow["opacity_max"], plan_schema.SHADOW_OPACITY_MAX)
        self.assertEqual(shadow["opacity_default"], plan_schema.DEFAULT_SHADOW_OPACITY)

        self.assertEqual(outline["field"], "layers[].outline")
        self.assertEqual(outline["fields"], sorted(plan_schema.OUTLINE_FIELDS))
        self.assertEqual(outline["required"], sorted(plan_schema.OUTLINE_REQUIRED_FIELDS))
        self.assertEqual(outline["width_px_min"], plan_schema.OUTLINE_WIDTH_MIN)
        self.assertEqual(outline["width_px_max"], plan_schema.OUTLINE_WIDTH_MAX)
        self.assertEqual(outline["color_default"], plan_schema.DEFAULT_OUTLINE_COLOR)

        # Os valores da decisão 18 da spec, escritos à parte para uma troca de constante não passar calada.
        self.assertEqual(
            (shadow["offset_px_min"], shadow["offset_px_max"], shadow["blur_px_min"], shadow["blur_px_max"]),
            (-100, 100, 0, 50),
        )
        self.assertEqual((shadow["opacity_min"], shadow["opacity_max"], shadow["opacity_default"]), (0.0, 1.0, 0.5))
        self.assertEqual((outline["width_px_min"], outline["width_px_max"]), (1, 30))
        self.assertEqual(shadow["fields"], ["blur_px", "color", "offset_x_px", "offset_y_px", "opacity"])
        self.assertEqual(outline["fields"], ["color", "width_px"])
        self.assertEqual(shadow["required"], ["blur_px", "offset_x_px", "offset_y_px"])
        self.assertEqual(outline["required"], ["width_px"])
        self.assertEqual((shadow["color_default"], outline["color_default"]), ("#000000", "#000000"))
        # Só a camada `text` os aceita: a allowlist das demais não os lista.
        self.assertTrue({"shadow", "outline"} <= plan_schema.TEXT_LAYER_FIELDS)
        self.assertFalse({"shadow", "outline"} & plan_schema.IMAGE_LAYER_FIELDS)
        self.assertFalse({"shadow", "outline"} & plan_schema.VIDEO_LAYER_FIELDS)

    def test_formats_expose_the_source_contract_from_the_media_constants(self) -> None:
        """Prova (A13) que contêineres, codecs, perfis e formato de pixel das fontes são os das constantes de `media.py`."""

        with workspace_tempdir() as root:
            data = collect_capabilities(root)
        formats = data["formats"]

        # Lido das constantes que `_validate_probe_payload` consulta, sem lista paralela em `capabilities`.
        self.assertEqual(formats["video"], [suffix.removeprefix(".") for suffix in media.VIDEO_SOURCE_EXTENSIONS])
        self.assertEqual(formats["codecs"]["video"], list(media.SOURCE_VIDEO_CODECS))
        self.assertEqual(
            formats["hevc"],
            {"profiles": list(media.HEVC_PROFILES), "pixel_formats": list(media.HEVC_PIXEL_FORMATS)},
        )
        self.assertEqual(formats["output_pixel_format"], media.OUTPUT_PIXEL_FORMAT)
        self.assertEqual(
            formats["ignored_streams"],
            {
                "codec_types": list(media.IGNORED_STREAM_CODEC_TYPES),
                "dispositions": list(media.IGNORED_STREAM_DISPOSITIONS),
            },
        )
        self.assertEqual(
            formats["audio"], sorted(suffix.removeprefix(".") for suffix in media.PLAN_AUDIO_SOURCE_EXTENSIONS)
        )
        self.assertEqual(
            formats["codecs"]["audio"],
            sorted({codec for codecs in media.SOURCE_AUDIO_CODECS.values() for codec in codecs}),
        )
        # Os valores da decisão 19, escritos à parte para uma troca de constante não passar calada.
        self.assertEqual(formats["video"], ["mp4", "mov"])
        self.assertEqual(formats["codecs"]["video"], ["h264", "hevc"])
        self.assertEqual(formats["codecs"]["audio"], ["aac", "mp3", "pcm_s16le"])
        self.assertEqual(formats["hevc"]["profiles"], ["Main", "Main 10"])
        self.assertEqual(formats["hevc"]["pixel_formats"], ["yuv420p", "yuv420p10le"])
        self.assertEqual(formats["output_pixel_format"], "yuv420p")
        self.assertEqual(formats["ignored_streams"], {"codec_types": ["data"], "dispositions": ["attached_pic"]})
        # Todo contêiner anunciado é aceito pela validação, com seu codec de áudio, e `.mkv` não é.
        for suffix in media.VIDEO_SOURCE_EXTENSIONS:
            self.assertIn(suffix, media.SUPPORTED_SOURCE_EXTENSIONS)
            self.assertEqual(media.SOURCE_AUDIO_CODECS[suffix], frozenset({"aac"}))
        self.assertNotIn(".mkv", media.SUPPORTED_SOURCE_EXTENSIONS)
        # Render, proxy e PNG de frame entregam o mesmo formato de 8 bits que o inventário anuncia.
        self.assertEqual(data["render"]["proxy"]["parameters"]["pixel_format"], media.OUTPUT_PIXEL_FORMAT)
        self.assertEqual(FRAME_PIXEL_FORMAT, "rgb24")

    def test_documentation_covers_native_hevc_and_mov_sources(self) -> None:
        """Prova (A13) que a documentação descreve a leitura nativa de HEVC e MOV e não diz mais que eles são recusados."""

        contents = {path: " ".join(path.read_text(encoding="utf-8").split()) for path in DOCUMENTATION_PATHS}
        combined = "\n".join(contents.values())

        for marker in (
            "HEVC",
            "`.mov`",
            "`tmcd`",
            "miniatura anexada",
            "tone mapping",
            "Fontes HEVC e MOV, Phase 5",
            "tests/test_hevc_sources.py",
            "`formats`",
            "`output_pixel_format`",
            *media.HEVC_PROFILES,
            *media.HEVC_PIXEL_FORMATS,
        ):
            self.assertIn(marker, combined)
        # Frases anteriores à T8 que o contrato tornou falsas.
        for stale in (
            "o editor só lê H.264 em MP4",
            "O editor só aceita vídeo H.264 em MP4",
            "`.mov` recebe `PLAN_INVALID`",
            "fontes HEVC ou MOV exigem a conversão",
            "comprovadamente faz o editor recusar a fonte",
            "formats.codecs.video: [\"h264\"]",
        ):
            self.assertNotIn(stale, combined)

    def test_documentation_describes_only_the_v2_contract(self) -> None:
        """Impede que a descoberta e o fluxo do agente voltem a documentar o contrato antigo."""

        contents = {path: path.read_text(encoding="utf-8") for path in DOCUMENTATION_PATHS}
        combined = "\n".join(contents.values())

        for marker in (
            "schema v2",
            "editor init",
            "cuts",
            "layers",
            "J/L",
            "0.5",
            "4.0",
            "PNG",
            "WebP",
            "100 MiB",
            "8.192",
            "9:16",
            "1:1",
            "4:5",
            "reframe",
            "keyframes",
            "upscale",
            "linha de comando",
        ):
            self.assertIn(marker, combined)

        # Phase 5: as dimensões de full e preview de cada proporção fixa aparecem na documentação,
        # lidas das mesmas constantes que o inventário expõe.
        for sizes in plan_schema.FIXED_CANVAS_DIMENSIONS.values():
            for width, height in (sizes["full"], sizes["preview"]):
                self.assertIn(f"{width}x{height}", combined)
        self.assertIn(f"{plan_schema.MAX_REFRAME_KEYFRAMES} keyframes", combined)
        # Phase 5, A12: sombra e contorno do texto (campos, faixas lidas das constantes e a limitação que resta).
        for marker in (
            "layers[].shadow",
            "layers[].outline",
            "offset_x_px",
            "offset_y_px",
            "blur_px",
            "width_px",
            f"de {plan_schema.SHADOW_OFFSET_MIN} a {plan_schema.SHADOW_OFFSET_MAX}",
            f"de {plan_schema.SHADOW_BLUR_MIN} a {plan_schema.SHADOW_BLUR_MAX}",
            f"de {plan_schema.OUTLINE_WIDTH_MIN} a {plan_schema.OUTLINE_WIDTH_MAX}",
            "caixa de fundo",
        ):
            self.assertIn(marker, combined)

        for path, content in contents.items():
            self.assertNotIn('"schema_version": 1', content, str(path))
            self.assertNotIn('"overlays"', content, str(path))

        self.assertNotIn("não possui `editor init`", combined)
        self.assertNotIn("`editor init` ainda não existe", combined)
        self.assertNotIn("subcomandos de mídia ainda não existem", combined)
        self.assertNotIn("zoom/movimento, inserções em camada e cortes J/L", combined)
        # Afirmações anteriores à phase 5 que o contrato atual tornou falsas.
        for stale in (
            "saída sempre 16:9",
            "saída é sempre 16:9",
            "não existe entregável 9:16",
            "`size_px` não é escalado entre preview e full",
            "não escala entre preview e full",
            "`-/filter_complex`, teto de 32.767 caracteres",
            # A12 (T7): a camada de texto ganhou `shadow` e `outline`; só a caixa de fundo continua ausente.
            "a camada de texto não tem sombra",
            "texto sem sombra nem contorno",
            "o texto não tem sombra",
            "O editor não tem sombra",
            "sem sombra nem contorno",
            "não tem sombra, contorno nem fundo",
            "Fora do contrato: sombra e contorno de texto",
        ):
            self.assertNotIn(stale, combined)

    def test_root_json_flag_is_preserved_before_subcommand(self) -> None:
        """Confirma as duas posições aceitas para `--json` sem alterar o envelope."""

        result = run_module("--json", "capabilities")
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["command"], "capabilities")

    def test_recommendation_prefers_cuda_small_then_cpu_base_then_tiny(self) -> None:
        """Confirma a ordem explícita de recomendação sem inventar modelo remoto."""

        self.assertEqual(
            choose_recommendation({"available": ["base", "small"]}, "available")["model"],
            "small",
        )
        self.assertEqual(
            choose_recommendation({"available": ["base"]}, "missing")["device"],
            "cpu",
        )
        self.assertEqual(
            choose_recommendation({"available": ["tiny"]}, "missing")["model"],
            "tiny",
        )
        self.assertEqual(
            choose_recommendation({"available": []}, "missing")["status"],
            "missing",
        )

    def test_model_inventory_uses_installer_contract(self) -> None:
        """Confirma que modelo completo é disponível e modelo sem vocabulário é rejeitado."""

        with workspace_tempdir() as root:
            base = root / "base"
            base.mkdir()
            for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (base / filename).write_bytes(b"fixture")

            tiny = root / "tiny"
            tiny.mkdir()
            for filename in ("config.json", "model.bin", "tokenizer.json"):
                (tiny / filename).write_bytes(b"fixture")

            data = collect_capabilities(root)
            models = {item["name"]: item for item in data["models"]["models"]}

            self.assertEqual(models["base"]["status"], "available")
            self.assertEqual(models["tiny"]["status"], "unsupported")
            self.assertEqual(data["recommendation"]["model"], "base")

    def test_render_capabilities_require_the_declared_encoders(self) -> None:
        """Confirma que render indisponível aparece antes da execução sem libx264."""

        ffmpeg = {
            "status": "available",
            "version": "fixture",
            "tools": {
                "ffmpeg": {"status": "available", "path": "ffmpeg", "version": "fixture"},
                "ffprobe": {"status": "available", "path": "ffprobe", "version": "fixture"},
            },
            "encoders": {
                "status": "unsupported",
                "available": ["aac"],
                "missing": ["libx264"],
                "reason": "libx264 ausente",
            },
        }
        with workspace_tempdir() as root:
            with patch(
                "editor.capabilities.dependencies.detect_ffmpeg",
                return_value=ffmpeg,
            ):
                data = collect_capabilities(root)

        self.assertEqual(data["operations"]["render_preview"]["status"], "unsupported")
        self.assertEqual(data["operations"]["render_full"]["status"], "unsupported")
        self.assertEqual(data["operations"]["proxy_build"]["status"], "unsupported")
        self.assertEqual(data["render"]["proxy"]["status"], "unsupported")

    def test_proxy_build_requires_only_the_video_encoder(self) -> None:
        """Confirma que `proxy build` segue disponível sem AAC, pois copia o áudio da fonte."""

        ffmpeg = {
            "status": "available",
            "version": "fixture",
            "tools": {
                "ffmpeg": {"status": "available", "path": "ffmpeg", "version": "fixture"},
                "ffprobe": {"status": "available", "path": "ffprobe", "version": "fixture"},
            },
            "encoders": {
                "status": "unsupported",
                "available": ["libx264"],
                "missing": ["aac"],
                "reason": "aac ausente",
            },
        }
        with workspace_tempdir() as root:
            with patch(
                "editor.capabilities.dependencies.detect_ffmpeg",
                return_value=ffmpeg,
            ):
                data = collect_capabilities(root)

        self.assertEqual(data["operations"]["render_preview"]["status"], "unsupported")
        self.assertEqual(data["operations"]["proxy_build"]["status"], "available")

    def test_cuda_capability_requires_a_working_ctranslate_runtime(self) -> None:
        """Confirma que driver e backend presentes não bastam para recomendar CUDA."""

        gpu = {
            "status": "available",
            "path": "nvidia-smi",
            "devices": [{"status": "available", "name": "fixture", "memory_bytes": 1}],
        }
        whisper = {"status": "available", "packages": {}}
        runtime = {
            "status": "unsupported",
            "device_count": 0,
            "reason": "runtime ausente",
        }
        models = {
            "status": "available",
            "root": "models",
            "available": ["base", "small"],
            "models": [],
        }
        with (
            patch("editor.capabilities.dependencies.detect_gpu", return_value=gpu),
            patch("editor.capabilities.dependencies.detect_whisper_backend", return_value=whisper),
            patch("editor.capabilities.dependencies.detect_cuda_runtime", return_value=runtime),
            patch("editor.capabilities.dependencies.discover_models", return_value=models),
        ):
            data = collect_capabilities(Path("models"))

        self.assertEqual(data["dependencies"]["cuda"]["status"], "unsupported")
        self.assertEqual(data["devices"]["cuda"]["status"], "unsupported")
        self.assertEqual(data["recommendation"]["device"], "cpu")
        self.assertEqual(data["devices"]["auto"]["resolves_to"], "cpu")

    def test_metal_probe_never_fails_and_reports_without_error(self) -> None:
        """Confirma que a sonda Metal best effort nunca levanta e informa status sem erro."""

        probe = editor_dependencies.detect_metal()

        self.assertIn(probe["status"], {"available", "missing"})
        self.assertTrue(probe["reason"])

    def test_metal_missing_keeps_cpu_recommendation(self) -> None:
        """Confirma que Metal ausente mantém CPU como recomendação sem falhar capabilities."""

        models = {
            "status": "available",
            "root": "models",
            "available": ["base"],
            "models": [],
        }
        with (
            patch(
                "editor.capabilities.dependencies.detect_metal",
                return_value={"status": "missing", "reason": "fixture sem Metal"},
            ),
            patch(
                "editor.capabilities.dependencies.discover_models",
                return_value=models,
            ),
        ):
            with workspace_tempdir() as root:
                data = collect_capabilities(root)

        self.assertEqual(data["devices"]["metal"]["status"], "missing")
        self.assertFalse(data["devices"]["metal"]["recommended"])
        self.assertEqual(data["recommendation"]["device"], "cpu")
        self.assertEqual(data["devices"]["auto"]["resolves_to"], "cpu")

    def test_metal_available_does_not_change_cpu_baseline(self) -> None:
        """Confirma que Metal disponível é só reporte e o baseline continua CPU nesta fatia."""

        models = {
            "status": "available",
            "root": "models",
            "available": ["base"],
            "models": [],
        }
        with (
            patch(
                "editor.capabilities.dependencies.detect_metal",
                return_value={"status": "available", "reason": "fixture com Metal"},
            ),
            patch(
                "editor.capabilities.dependencies.discover_models",
                return_value=models,
            ),
        ):
            with workspace_tempdir() as root:
                data = collect_capabilities(root)

        self.assertEqual(data["devices"]["metal"]["status"], "available")
        self.assertFalse(data["devices"]["metal"]["recommended"])
        self.assertEqual(data["recommendation"]["device"], "cpu")

    def test_auto_resolves_cpu_int8_when_metal_missing(self) -> None:
        """Confirma que auto resolve CPU int8 com modelo base mesmo sem Metal."""

        with workspace_tempdir() as workdir:
            model_root = workdir / "models"
            model_root.mkdir()
            base = model_root / "base"
            base.mkdir()
            for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
                (base / filename).write_bytes(b"fixture")
            models = {
                "status": "available",
                "root": str(model_root),
                "available": ["base"],
                "models": [
                    {"name": "base", "status": "available", "path": str(base)},
                ],
            }
            with (
                patch("editor.whisper_backend.default_model_root", return_value=model_root),
                patch(
                    "editor.whisper_backend.dependencies.discover_models",
                    return_value=models,
                ),
                patch(
                    "editor.whisper_backend.dependencies.detect_gpu",
                    return_value={"status": "missing"},
                ),
                patch(
                    "editor.whisper_backend.dependencies.detect_whisper_backend",
                    return_value={"status": "available"},
                ),
                patch(
                    "editor.whisper_backend.dependencies.detect_metal",
                    return_value={"status": "missing", "reason": "fixture sem Metal"},
                ),
            ):
                config = resolve_config(None, "auto")

        self.assertEqual(config.device, "cpu")
        self.assertEqual(config.compute_type, "int8")
        self.assertEqual(config.model_name, "base")


    def test_filter_flag_and_ceiling_follow_host(self) -> None:
        """Confirma que Windows usa -/filter_complex com 32767 e demais usam -filter_complex com limite do sistema."""

        with patch("editor.dependencies.is_windows", return_value=True):
            self.assertEqual(editor_dependencies.ffmpeg_filter_option(), "-/filter_complex")
            self.assertEqual(editor_dependencies.max_command_characters(), 32_767)
            with workspace_tempdir() as root:
                data = collect_capabilities(root)
            self.assertEqual(data["render"]["filter_transport"], "file")
            self.assertEqual(data["render"]["filter_option"], "-/filter_complex")
            self.assertEqual(data["render"]["max_command_characters"], 32_767)
            self.assertEqual(data["limits"]["max_command_characters"], 32_767)

        with (
            patch("editor.dependencies.is_windows", return_value=False),
            patch("editor.dependencies.os.sysconf", return_value=262_144, create=True),
        ):
            self.assertEqual(editor_dependencies.ffmpeg_filter_option(), "-filter_complex")
            self.assertEqual(editor_dependencies.max_command_characters(), 262_144)
            with workspace_tempdir() as root:
                data = collect_capabilities(root)
            self.assertEqual(data["render"]["filter_option"], "-filter_complex")
            self.assertEqual(data["render"]["max_command_characters"], 262_144)
            self.assertEqual(data["limits"]["max_command_characters"], 262_144)

    def test_filter_ceiling_falls_back_without_sysconf(self) -> None:
        """Confirma que sem SC_ARG_MAX a capabilities informa o fallback POSIX sem falhar."""

        with (
            patch("editor.dependencies.is_windows", return_value=False),
            patch("editor.dependencies.os.sysconf", side_effect=OSError("sem limite"), create=True),
        ):
            self.assertEqual(
                editor_dependencies.max_command_characters(),
                editor_dependencies.FALLBACK_POSIX_MAX_COMMAND_CHARACTERS,
            )

    def test_font_origin_follows_host_without_silent_fallback(self) -> None:
        """Confirma que Windows informa registry HKCU/HKLM e demais informam fc-match com fallback rejeitado."""

        with patch("editor.dependencies.is_windows", return_value=True):
            self.assertEqual(editor_dependencies.font_lookup_source(), "registry")
            self.assertEqual(editor_dependencies.font_registry_hives(), ["HKCU", "HKLM"])
            with workspace_tempdir() as root:
                data = collect_capabilities(root)
            self.assertEqual(data["fonts"]["renderer"], "fontfile")
            self.assertEqual(data["fonts"]["source"], "registry")
            self.assertEqual(data["fonts"]["registry"], ["HKCU", "HKLM"])
            self.assertEqual(data["fonts"]["fallback"], "reject")

        with patch("editor.dependencies.is_windows", return_value=False):
            self.assertEqual(editor_dependencies.font_lookup_source(), "fc-match")
            self.assertEqual(editor_dependencies.font_registry_hives(), [])
            with workspace_tempdir() as root:
                data = collect_capabilities(root)
            self.assertEqual(data["fonts"]["renderer"], "fontfile")
            self.assertEqual(data["fonts"]["source"], "fc-match")
            self.assertEqual(data["fonts"]["registry"], [])
            self.assertEqual(data["fonts"]["fallback"], "reject")

    def test_resolve_without_exe_prioritizes_tools_bundle(self) -> None:
        """Confirma que fora do Windows a resolução prioriza .tools/ffmpeg sem sufixo .exe."""

        with workspace_tempdir() as root:
            bundle = root / ".tools" / "ffmpeg"
            bundle.mkdir(parents=True)
            candidate = bundle / "ffmpeg"
            candidate.write_bytes(b"fixture")
            with (
                patch("editor.dependencies.is_windows", return_value=False),
                patch("editor.dependencies._package_root", return_value=root),
                patch("editor.dependencies.Path.cwd", return_value=root),
                patch("editor.dependencies.shutil.which", return_value=None),
            ):
                resolved = editor_dependencies.resolve_executable("ffmpeg")
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved.name, "ffmpeg")
            self.assertNotEqual(resolved.suffix, ".exe")


if __name__ == "__main__":
    unittest.main()
