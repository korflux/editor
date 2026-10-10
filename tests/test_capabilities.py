"""Testes do contrato local de capacidades e da recomendação determinística."""

from __future__ import annotations

import json
from contextlib import contextmanager
import shutil
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path
from typing import Iterator
from unittest.mock import patch
from uuid import uuid4

from editor.analysis import captions as caption_suggestions
from editor import dependencies as editor_dependencies
from editor import media
from editor.analysis.frames import FRAME_PIXEL_FORMAT
from editor.capabilities import choose_recommendation, collect_capabilities
from editor.contracts import CLI_VERSION
from editor.integrity import approval as approval_module
from editor.plan import schema as plan_schema
from editor.render import live
from editor.render.preview import (
    CPU_ENCODER_PRESET,
    CPU_VIDEO_ENCODER,
    DEFAULT_QUALITY,
    GPU_ENCODER_PRESET,
    MAX_PREVIEW_DIMENSION,
    QUALITY_CHOICES,
    QUALITY_PRESETS,
)
from editor.ui import limits as ui_limits
from editor.whisper_backend import resolve_config
from tests.support import remove_tree


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "editor"
DOCUMENTATION_PATHS = (
    PROJECT_ROOT / "skills" / "vibe-editor" / "SKILL.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "installation.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "youtube-editing.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "canal-sonni-viaja.md",
    PROJECT_ROOT / "skills" / "vibe-editor" / "references" / "source-preparation.md",
    PROJECT_ROOT / "AGENTS.md",
)


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para validar o contrato consumido por agentes."""

    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        # A borda emite UTF-8; o decoder do teste não deve depender da code page do Windows.
        encoding="utf-8",
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
        remove_tree(temporary_path)


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
        self.assertEqual(data["cli_version"], "2.0.0")
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
        # Phase 6: descoberta usa as mesmas fontes de contrato da sugestão e validação.
        suggest = data["operations"]["captions_suggest"]
        self.assertEqual(suggest["status"], "available")
        self.assertFalse(suggest["writes_files"])
        self.assertFalse(suggest["translates"])
        self.assertEqual(suggest["max_chars"], {"default": caption_suggestions.SUGGEST_MAX_CHARS_DEFAULT, "min": caption_suggestions.SUGGEST_MAX_CHARS_MIN, "max": caption_suggestions.SUGGEST_MAX_CHARS_MAX})
        self.assertEqual(suggest["max_duration_s"], {"default": caption_suggestions.SUGGEST_MAX_DURATION_DEFAULT_S, "min": caption_suggestions.SUGGEST_MAX_DURATION_MIN_S, "max": caption_suggestions.SUGGEST_MAX_DURATION_MAX_S})
        self.assertEqual(suggest["pause_break_s"], caption_suggestions.SUGGEST_PAUSE_BREAK_S)
        self.assertEqual(suggest["sentence_end_chars"], list(caption_suggestions.SUGGEST_SENTENCE_END_CHARS))
        self.assertEqual(suggest["max_transcription_bytes"], caption_suggestions.MAX_TRANSCRIPTION_BYTES)
        captions = data["plan"]["captions"]
        for key, constant in (("fields", "CAPTIONS_FIELDS"), ("required", "CAPTIONS_FIELDS"), ("item_fields", "CAPTION_ITEM_FIELDS"), ("item_required", "CAPTION_ITEM_FIELDS"), ("style_fields", "CAPTION_STYLE_FIELDS")):
            self.assertEqual(captions[key], sorted(getattr(plan_schema, constant)))
        for key, constant in (("align_default", "DEFAULT_CAPTION_ALIGN"), ("default_boxes", "DEFAULT_CAPTION_BOXES"), ("min_items", "MIN_CAPTION_ITEMS"), ("max_items", "MAX_CAPTION_ITEMS"), ("max_text_chars", "MAX_CAPTION_TEXT_CHARS"), ("max_duration_s", "MAX_CAPTION_DURATION_SECONDS"), ("min_occurrence_s", "MIN_CAPTION_OCCURRENCE_SECONDS"), ("overlap_tolerance_s", "CAPTION_OVERLAP_TOLERANCE_SECONDS")):
            self.assertEqual(captions[key], getattr(plan_schema, constant))
        self.assertEqual(captions["time_reference"], "source")
        self.assertEqual(captions["style_required"], ["font", "size_px", "color"])
        self.assertEqual(captions["mapping"]["loop_tracks"], "ignored")
        self.assertEqual(captions["mapping"]["envelope_fields"], ["items", "occurrences", "unplaced"])

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
        self.assertEqual(data["limits"]["max_video_dimension"], 8_192)
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

    def test_render_exposes_the_quality_presets_from_the_constants(self) -> None:
        """Prova (A14) que `render.quality` é lido das mesmas constantes que o encoder da full usa."""

        with workspace_tempdir() as root:
            data = collect_capabilities(root)
        quality = data["render"]["quality"]

        self.assertEqual(quality["status"], "available")
        self.assertEqual(quality["flag"], "--quality")
        self.assertEqual(quality["presets"], list(QUALITY_CHOICES))
        self.assertEqual(quality["default"], DEFAULT_QUALITY)
        # Só a full recebe a opção; a preview usa sempre o padrão e a escolha fica fora da aprovação.
        self.assertEqual(quality["applies_to"], ["render_full"])
        self.assertEqual(quality["preview"], DEFAULT_QUALITY)
        self.assertIs(quality["in_approval_hash"], False)
        self.assertEqual(quality["fallback"], "gpu_to_cpu_keeps_preset")
        self.assertEqual(quality["envelope"], "video.quality")
        cpu, gpu = quality["parameters"]["cpu"], quality["parameters"]["gpu"]
        self.assertEqual(
            (cpu["encoder"], cpu["preset"], cpu["rate_control"]),
            (CPU_VIDEO_ENCODER, CPU_ENCODER_PRESET, "crf"),
        )
        self.assertEqual(
            (gpu["encoder"], gpu["preset"], gpu["rate_control"], gpu["quality_parameter"]),
            (editor_dependencies.GPU_VIDEO_ENCODER, GPU_ENCODER_PRESET, "vbr", "cq"),
        )
        for name in QUALITY_CHOICES:
            self.assertEqual(cpu["values"][name], QUALITY_PRESETS[name].crf)
            self.assertEqual(gpu["values"][name], QUALITY_PRESETS[name].cq)
            self.assertEqual(gpu["levels"][name], QUALITY_PRESETS[name].nvenc_level)
        # Valores da decisão 20, escritos à parte para uma troca de constante não passar calada.
        self.assertEqual(quality["presets"], ["standard", "high", "max"])
        self.assertEqual(quality["default"], "standard")
        self.assertEqual((cpu["encoder"], cpu["preset"]), ("libx264", "veryfast"))
        self.assertEqual((gpu["encoder"], gpu["preset"]), ("h264_nvenc", "p3"))
        self.assertEqual(cpu["values"], {"standard": 23, "high": 18, "max": 14})
        self.assertEqual(gpu["values"], {"standard": 31, "high": 22, "max": 18})
        self.assertEqual(gpu["levels"], {"standard": None, "high": "5.2", "max": "5.2"})

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

    def test_documentation_covers_the_quality_presets(self) -> None:
        """Prova (A14) que a documentação ensina `--quality`, a tabela medida e não diz mais que o encoder não é configurável."""

        contents = {path: " ".join(path.read_text(encoding="utf-8").split()) for path in DOCUMENTATION_PATHS}
        combined = "\n".join(contents.values())

        markers = [
            "--quality",
            "render.quality",
            "video.quality",
            "Qualidade da full, Phase 5",
            "tests/test_render_quality.py",
            "fora do hash",
            "SSIM",
        ]
        for name in QUALITY_CHOICES:
            markers.append(f"`{name}`")
            markers.append(f"crf {QUALITY_PRESETS[name].crf}")
            markers.append(f"cq {QUALITY_PRESETS[name].cq}")
        for marker in markers:
            self.assertIn(marker, combined)
        # A skill e o guia de entrega ensinam a opção e citam o custo medido rotulado como orientação.
        for path in (DOCUMENTATION_PATHS[0], DOCUMENTATION_PATHS[2]):
            for marker in ("--quality", "orientação", "SSIM", "ffprobe"):
                self.assertIn(marker, contents[path], f"{path.name}: {marker}")
        # Frases anteriores à T9 que o contrato tornou falsas.
        for stale in (
            "Esses encoders não têm perfil de qualidade configurável",
            "permitir seleção de encoder ou perfil de qualidade somente quando o MVP precisar operar em hosts sem libx264 ou expor presets",
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

    def test_version_is_2_0_0_in_the_package_metadata_and_in_the_cli(self) -> None:
        """C4 (phase 7): a quebra da aprovação v2 fecha a versão 2.0.0, igual no pacote, na CLI e no inventário."""

        metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["version"], "2.0.0")
        self.assertEqual(CLI_VERSION, "2.0.0")
        self.assertEqual(metadata["project"]["version"], CLI_VERSION)
        with workspace_tempdir() as root:
            data = collect_capabilities(root)
        self.assertEqual(data["cli_version"], CLI_VERSION)

    def test_ui_operation_exposes_the_contract_from_the_constants(self) -> None:
        """A11 (phase 7): `operations.ui` é lido das constantes que o servidor e o núcleo ao vivo usam."""

        with workspace_tempdir() as root:
            data = collect_capabilities(root)
        interface = data["operations"]["ui"]

        self.assertEqual(interface["status"], data["operations"]["render_preview"]["status"])
        self.assertEqual(interface["requires"], data["operations"]["render_full"]["requires"])
        # Comandos e estados públicos (`ui start` e `ui stop`), sem a URL com o token no inventário.
        start, stop = interface["commands"]["start"], interface["commands"]["stop"]
        self.assertEqual((start["command"], stop["command"]), ("ui start", "ui stop"))
        self.assertEqual((start["required"], start["optional"]), ([], ["workdir", "no_open"]))
        self.assertEqual((stop["required"], stop["optional"]), ([], ["workdir"]))
        self.assertEqual(start["states"], ["started", "running"])
        self.assertEqual(stop["states"], ["stopped", "not_running"])
        self.assertEqual(start["envelope_fields"], ["state", "url", "pid", "port"])
        self.assertEqual(stop["envelope_fields"], ["state"])
        self.assertTrue(start["reuses_running_instance"])
        self.assertTrue(stop["terminates_ffmpeg_children"])
        # Endereço, sessão, processos e prazos vêm de `ui/limits.py`.
        self.assertEqual(interface["host"], ui_limits.HOST)
        self.assertEqual(interface["instance"]["registry"], f".tmp/{ui_limits.REGISTRY_NAME}")
        self.assertEqual(interface["instance"]["start_timeout_s"], ui_limits.START_TIMEOUT_S)
        self.assertEqual(interface["instance"]["stop_timeout_s"], ui_limits.STOP_TIMEOUT_S)
        limits = interface["limits"]
        self.assertEqual(limits["sessions"], ui_limits.SESSION_LIMIT)
        self.assertEqual(limits["session_active_s"], ui_limits.SESSION_ACTIVE_S)
        self.assertEqual(limits["ffmpeg_per_session"], ui_limits.MAX_PROCESSES_PER_SESSION)
        self.assertEqual(limits["ffmpeg_kinds"], list(ui_limits.PROCESS_KINDS))
        self.assertEqual(limits["idle_timeout_s"], ui_limits.IDLE_TIMEOUT_S)
        self.assertEqual(limits["query_max_bytes"], ui_limits.QUERY_MAX_BYTES)
        self.assertEqual(limits["body_max_bytes"], ui_limits.BODY_MAX_BYTES)
        security = interface["security"]
        self.assertEqual(security["token_bytes"], ui_limits.TOKEN_BYTES)
        self.assertEqual(security["cookie_prefix"], ui_limits.COOKIE_PREFIX)
        self.assertEqual(security["content_security_policy"], ui_limits.CONTENT_SECURITY_POLICY)
        self.assertEqual(security["referrer_policy"], ui_limits.REFERRER_POLICY)
        self.assertTrue(security["origin_required_on_post"])
        # Resoluções e stream vêm de `render/live.py`.
        resolutions = interface["resolutions"]
        self.assertEqual(resolutions["allowed"], list(live.RESOLUTIONS))
        self.assertEqual(resolutions["divisors"], dict(live.RESOLUTION_DIVISORS))
        self.assertEqual(resolutions["reads"], {"full": "originals", "fractions": "valid_proxy_else_original"})
        self.assertIs(resolutions["builds_proxy"], False)
        stream = interface["stream"]
        self.assertEqual(stream["movflags"], live.LIVE_MOVFLAGS)
        self.assertEqual(
            stream["video"],
            {
                "encoder": live.LIVE_VIDEO_ENCODER,
                "preset": live.LIVE_VIDEO_PRESET,
                "tune": live.LIVE_VIDEO_TUNE,
                "crf": live.LIVE_VIDEO_CRF,
                "keyframe_interval_s": live.LIVE_KEYFRAME_INTERVAL_S,
            },
        )
        self.assertEqual(stream["audio"], {"codec": "aac", "bitrate": live.LIVE_AUDIO_BITRATE})
        self.assertEqual(stream["first_byte_timeout_s"], ui_limits.STREAM_FIRST_BYTE_TIMEOUT_S)
        self.assertEqual(stream["stall_timeout_s"], ui_limits.STREAM_STALL_TIMEOUT_S)
        self.assertEqual(
            interface["scrub"],
            {"format": "jpeg", "quality": live.SCRUB_JPEG_QUALITY, "timeout_s": live.SCRUB_TIMEOUT_S},
        )
        self.assertEqual(
            interface["source_mode"],
            {"kinds": list(live.SOURCE_VIEW_KINDS), "max_side_full_px": live.SOURCE_MAX_SIDE, "canvas": "source"},
        )
        # Só leitura: a única escrita é o `approval.json` da aprovação.
        self.assertIs(interface["read_only"], True)
        self.assertEqual(interface["writes"], ["approval.json"])
        # Valores da spec escritos à parte, para uma troca de constante não passar calada.
        self.assertEqual(interface["host"], "127.0.0.1")
        self.assertEqual(resolutions["allowed"], ["full", "1/2", "1/3", "1/4"])
        self.assertEqual((limits["sessions"], limits["ffmpeg_per_session"], limits["idle_timeout_s"]), (2, 3, 7200))
        self.assertEqual(limits["ffmpeg_kinds"], ["stream", "source", "frame"])
        self.assertEqual(limits["session_active_s"], 120.0)
        self.assertEqual(security["token_bytes"], 32)
        self.assertEqual(security["cookie_attributes"], ["HttpOnly", "SameSite=Strict", "Path=/"])
        self.assertIn("frame-ancestors 'none'", security["content_security_policy"])
        # As limitações com `ponytail:` conhecidas da interface seguem no inventário.
        self.assertTrue(any("Range" in item for item in interface["limitations"]))
        self.assertTrue(any(item.startswith("ponytail:") for item in interface["limitations"]))

    def test_approve_operation_exposes_the_v2_contract_without_the_preview(self) -> None:
        """A11 e A10 (phase 7): `operations.approve` descreve o registro v2 lido das constantes de `integrity/approval.py`."""

        with workspace_tempdir() as root:
            data = collect_capabilities(root)
        approve = data["operations"]["approve"]

        self.assertEqual(approve["status"], data["operations"]["plan_validate"]["status"])
        self.assertEqual(approve["schema_version"], approval_module.APPROVAL_SCHEMA_VERSION)
        self.assertEqual(approve["obsolete_versions"], [1])
        self.assertEqual(approve["record_fields"], sorted(approval_module._APPROVAL_FIELDS))
        self.assertEqual(approve["source_fields"], sorted(approval_module._SOURCE_APPROVAL_FIELDS))
        self.assertEqual(approve["max_record_bytes"], approval_module.MAX_APPROVAL_BYTES)
        # Sem preview: nem exigida, nem vinculada, nem no registro, nem no que a full verifica.
        self.assertIs(approve["requires_preview"], False)
        self.assertEqual(approve["preview_sha256"], "removed")
        self.assertNotIn("preview_sha256", approve["record_fields"])
        self.assertEqual(approve["binds"], ["plan_sha256", "sources"])
        self.assertEqual(approve["release_gate"]["verifies"], ["plan_sha256", "sources"])
        self.assertIn("preview.mp4", approve["release_gate"]["ignores"])
        self.assertEqual(approve["channels"], ["cli", "ui"])
        self.assertEqual(approve["ui"]["binds_loaded_version"], ["plan_sha256", "sources_fingerprint"])
        self.assertIs(approve["ui"]["writes_on_refusal"], False)
        self.assertNotIn("preview", " ".join(approve["envelope_fields"]))
        # Valores do contrato escritos à parte.
        self.assertEqual(approve["schema_version"], 2)
        self.assertEqual(approve["record_fields"], ["approved_at", "plan_sha256", "schema_version", "sources"])
        self.assertEqual(approve["source_fields"], ["sha256", "source_id"])
        # A preview é só exportação opcional, sem efeito na aprovação, e a full lê só plano e fontes.
        preview = data["operations"]["render_preview"]
        self.assertIs(preview["optional"], True)
        self.assertIs(preview["affects_approval"], False)
        self.assertIn("sem olhar a preview", data["operations"]["render_full"]["reason"])

    def test_ui_states_and_public_codes_exist_in_the_code(self) -> None:
        """Os estados e os códigos de erro que o inventário cita são textos reais do código (nada inventado à mão)."""

        with workspace_tempdir() as root:
            data = collect_capabilities(root)
        interface = data["operations"]["ui"]
        approve = data["operations"]["approve"]
        sources = "\n".join(
            module.read_text(encoding="utf-8") for module in sorted(SOURCE_ROOT.rglob("*.py"))
        )
        instance = (SOURCE_ROOT / "ui" / "instance.py").read_text(encoding="utf-8")

        for command in interface["commands"].values():
            for state in command["states"]:
                self.assertIn(f'"{state}"', instance, state)
            for code in command["errors"]:
                self.assertIn(f'"{code}"', sources, code)
        for code in (
            *interface["security"]["errors"],
            approve["release_gate"]["stale"],
            approve["release_gate"]["required"],
            approve["ui"]["changed"],
        ):
            self.assertIn(f'"{code}"', sources, code)

    def test_documentation_covers_the_review_interface_and_the_v2_approval(self) -> None:
        """A11 e C4 (phase 7): a documentação descreve `ui`, a aprovação v2 e a quebra da 2.0.0, sem o contrato anterior."""

        contents = {path: " ".join(path.read_text(encoding="utf-8").split()) for path in DOCUMENTATION_PATHS}
        combined = "\n".join(contents.values())
        agents = contents[DOCUMENTATION_PATHS[-1]]
        skill = contents[DOCUMENTATION_PATHS[0]]

        for marker in (
            "Interface de revisão, Phase 7",
            "ui start",
            "ui stop",
            "operations.ui",
            "operations.approve",
            "tests/test_ui_server.py",
            "tests/test_live_stream.py",
            "tests/e2e/test_phase_7_flow.py",
            "render/live.py",
            "Quebra de contrato (2.0.0, major)",
            "`schema_version` 2",
            "`preview_sha256`",
            "`APPROVAL_STALE`",
            "`APPROVAL_PLAN_CHANGED`",
            "Aprovar esta versão",
            "exportação opcional",
        ):
            self.assertIn(marker, agents, marker)
        # Resoluções, tetos e inatividade citados no AGENTS.md saem das mesmas constantes do servidor.
        for resolution in live.RESOLUTIONS:
            self.assertIn(f"`{resolution}`", agents)
        self.assertIn(f"{ui_limits.SESSION_LIMIT} sessões", agents)
        self.assertIn(f"{ui_limits.MAX_PROCESSES_PER_SESSION} FFmpeg por sessão", agents)
        self.assertIn(f"{ui_limits.IDLE_TIMEOUT_S // 3600} h sem pedidos", agents)
        self.assertIn(f"{int(ui_limits.SESSION_ACTIVE_S)} s", agents)
        self.assertIn(ui_limits.CONTENT_SECURITY_POLICY, agents)
        for marker in ("ui start", "ui stop", "Aprovar esta versão", "`APPROVAL_STALE`", "`approve` não exige `preview.mp4`"):
            self.assertIn(marker, skill, marker)
        # Todo módulo e todo estático da interface aparecem no mapa de estrutura.
        for module in sorted((SOURCE_ROOT / "ui").glob("*.py")):
            if module.name != "__init__.py":
                self.assertIn(f"`{module.name}`", agents, module.name)
        for asset in sorted((SOURCE_ROOT / "ui" / "static").iterdir()):
            self.assertIn(f"`{asset.name}`", agents, asset.name)
        # Afirmações anteriores à phase 7 que o contrato 2.0.0 tornou falsas.
        for stale in (
            "aprovação explícita do plano e da preview vigente",
            "exige `preview.mp4` regular já publicada",
            "hash da preview e lista ordenada de hashes das fontes",
            "exige `plan.json`, `preview.mp4`, `approval.json`",
            "o plano, a preview e as fontes coincidirem por SHA-256",
            "A aprovação continua vinculando plano canônico, preview e originais",
            "Versão 1.3.0",
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
