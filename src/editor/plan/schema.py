"""Allowlist estrutural do schema v2 do plano linear."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..contracts import CliError, EXIT_CODES


ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "profile",
        "sources",
        "segments",
        "cuts",
        "layers",
        "audio_processing",
        "captions",
    }
)
PLAN_SCHEMA_VERSION = 2
PROFILE_FIELDS = frozenset({"aspect_ratio", "max_full_width"})
DEFAULT_MAX_FULL_WIDTH = 1_920
MIN_MAX_FULL_WIDTH = 32
MAX_MAX_FULL_WIDTH = 3_840
MAX_FULL_WIDTH_STEP = 32

# --- Perfil de saída: proporções e canvas (fonte única de validação, render e capabilities) ---
LANDSCAPE_ASPECT_RATIO = "16:9"
"""Única proporção cujo canvas é derivado da largura da fonte e limitado por `max_full_width`."""

ASPECT_RATIOS = ("16:9", "9:16", "1:1", "4:5")
"""Allowlist de `profile.aspect_ratio`; a ordem é a de exibição em mensagens e em `capabilities`."""

ASPECT_RATIO_TERMS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5)}
"""Largura e altura relativas de cada proporção, usadas para comparar a proporção de uma fonte."""

FIXED_CANVAS_DIMENSIONS = {
    "9:16": {"full": (1_080, 1_920), "preview": (404, 720)},
    "1:1": {"full": (1_080, 1_080), "preview": (720, 720)},
    "4:5": {"full": (1_080, 1_350), "preview": (576, 720)},
}
"""Tamanho de plataforma das proporções não 16:9, independente da resolução das fontes.

Todas as dimensões são pares (exigência de `yuv420p`) e o lado maior da preview é 720.
"""

FULL_WIDTH_ALIGNMENT = 16
"""Alinhamento legado da largura da full 16:9, preservado para a full continuar inalterada (decisão 14).

A full 16:9 usa este alinhamento sempre que o canvas legado (largura alinhada a 16 e altura
`largura * 9 // 16` reduzida a par) é aceito pela checagem do MP4 (`LANDSCAPE_RATIO_TOLERANCE`);
só quando ele seria recusado entra `FULL_FALLBACK_WIDTH_ALIGNMENT`. Ver `canvas_dimensions`.
"""

FULL_FALLBACK_WIDTH_ALIGNMENT = 32
"""Alinhamento da full 16:9 onde o canvas legado seria recusado, o mesmo da preview.

Com altura exata `largura * 9 / 16`, largura e altura só são ambas pares (exigência de
`yuv420p`) quando a largura é múltipla de 32. Nas larguras múltiplas ímpares de 16 (1200, 1360,
1680...) o canvas legado tem altura ímpar reduzida a par e razão fora da tolerância, e a checagem
do MP4 recusava a full recém-renderizada (RENDER_FAILED); com 32 a fonte de 1200x674 sai em
1184x666, com razão 16:9 exata.
"""

LANDSCAPE_RATIO_TOLERANCE = 0.001
"""Desvio máximo aceito entre largura/altura de um MP4 16:9 renderizado e 16/9.

Fonte única da tolerância: `canvas_dimensions` a usa para saber se o canvas legado da full seria
aceito e `render/preview.py` (`_validate_rendered_preview`) a usa para recusar o MP4 renderizado.
"""

PREVIEW_WIDTH_ALIGNMENT = 32
"""Alinhamento da largura da preview 16:9, que mantém ambas as dimensões pares em `yuv420p`."""

SOURCE_FIELDS = frozenset({"id", "kind", "path"})
SEGMENT_FIELDS = frozenset({"video", "audio"})
VIDEO_FIELDS = frozenset({"source_id", "in_s", "out_s", "fit", "motion", "reframe"})
MOTION_FIELDS = frozenset({"from", "to"})
MOTION_STATE_FIELDS = frozenset({"x", "y", "zoom"})
MOTION_ZOOM_MIN = 0.5
MOTION_ZOOM_MAX = 4.0

# --- Reenquadramento por keyframes (`segments[].video.reframe`, phase 5) ---
# Fonte única da allowlist e dos limites, lida pela validação, pelo grafo e (T4) por `capabilities`.
REFRAME_FIELDS = frozenset({"keyframes"})
"""Única chave aceita em `reframe`; qualquer outra é campo não permitido."""

KEYFRAME_FIELDS = frozenset({"t_s", "x", "y", "zoom", "interpolation"})
"""Campos de cada keyframe; `zoom` e `interpolation` são opcionais."""

MAX_REFRAME_KEYFRAMES = 32
# ponytail: 32 keyframes por segmento, ampliar quando um acompanhamento real precisar de marcação mais densa
REFRAME_ZOOM_MIN = 1.0
REFRAME_ZOOM_MAX = 4.0
DEFAULT_REFRAME_ZOOM = REFRAME_ZOOM_MIN
"""Zoom 1.0 é o maior recorte da proporção do canvas que cabe na fonte."""

REFRAME_INTERPOLATIONS = ("ease", "linear")
"""Curvas por trecho: `ease` é `3p² - 2p³` e `linear` é velocidade constante."""

DEFAULT_REFRAME_INTERPOLATION = "ease"
MAX_CUT_DURATION_SECONDS = 30.0
AUDIO_FIELDS = frozenset({"mode", "tracks"})
TRACK_FIELDS = frozenset({"source_id", "in_s", "out_s", "gain_db", "loop"})
CUT_FIELDS = frozenset({"after_segment", "type", "duration_s"})
LAYER_COMMON_FIELDS = frozenset(
    {"type", "start_s", "end_s", "x", "y", "width", "height"}
)
TEXT_LAYER_FIELDS = LAYER_COMMON_FIELDS | frozenset(
    {"text", "font", "size_px", "color", "align", "wrap", "shadow", "outline"}
)
IMAGE_LAYER_FIELDS = LAYER_COMMON_FIELDS | frozenset({"source_id", "fit"})
VIDEO_LAYER_FIELDS = LAYER_COMMON_FIELDS | frozenset(
    {"source_id", "in_s", "out_s", "fit"}
)

# --- Estilo da camada de texto: sombra e contorno (`layers[].shadow` e `layers[].outline`, phase 5) ---
# Fonte única da allowlist, das faixas e dos padrões, lida pela validação, pelo grafo e por
# `capabilities`. Só a camada `text` aceita os dois campos (em `image` e `video` valem as allowlists
# acima, que não os listam). Todos os valores em pixels são definidos na largura da full e a
# preview os escala pela mesma razão do `size_px` (ver `render/ffmpeg_graph.py`).
SHADOW_FIELDS = frozenset({"offset_x_px", "offset_y_px", "blur_px", "color", "opacity"})
"""Campos aceitos em `shadow`; qualquer outro é campo não permitido."""

SHADOW_REQUIRED_FIELDS = frozenset({"offset_x_px", "offset_y_px", "blur_px"})
"""Campos obrigatórios de `shadow`; `color` e `opacity` têm padrão."""

SHADOW_OFFSET_MIN = -100
SHADOW_OFFSET_MAX = 100
"""Faixa inteira de `offset_x_px` e `offset_y_px`; positivo desloca para a direita e para baixo."""

SHADOW_BLUR_MIN = 0
SHADOW_BLUR_MAX = 50
"""Faixa inteira de `blur_px`; 0 é uma sombra nítida e o valor é o raio de desfoque no sentido do CSS."""

SHADOW_OPACITY_MIN = 0.0
SHADOW_OPACITY_MAX = 1.0
DEFAULT_SHADOW_OPACITY = 0.5
DEFAULT_SHADOW_COLOR = "#000000"

OUTLINE_FIELDS = frozenset({"width_px", "color"})
"""Campos aceitos em `outline`."""

OUTLINE_REQUIRED_FIELDS = frozenset({"width_px"})
OUTLINE_WIDTH_MIN = 1
OUTLINE_WIDTH_MAX = 30
DEFAULT_OUTLINE_COLOR = "#000000"

# --- Legendas ancoradas na fala da fonte (phase 6): contrato compartilhado com capabilities ---
CAPTIONS_FIELDS = frozenset({"style", "items"})
CAPTION_ITEM_FIELDS = frozenset({"source_id", "in_s", "out_s", "text"})
CAPTION_STYLE_FIELDS = frozenset({
    "font", "size_px", "color", "align", "shadow", "outline", "x", "y", "width", "height"
})
MIN_CAPTION_ITEMS = 1
MAX_CAPTION_ITEMS = 2_000
MAX_CAPTION_TEXT_CHARS = 200
MAX_CAPTION_DURATION_SECONDS = 30.0
MIN_CAPTION_OCCURRENCE_SECONDS = 0.3
CAPTION_OVERLAP_TOLERANCE_SECONDS = 0.001
DEFAULT_CAPTION_ALIGN = "center"
DEFAULT_CAPTION_BOXES = {
    "16:9": {"x": 0.05, "y": 0.80, "width": 0.9, "height": 0.14},
    "9:16": {"x": 0.05, "y": 0.62, "width": 0.9, "height": 0.14},
    "1:1": {"x": 0.05, "y": 0.78, "width": 0.9, "height": 0.14},
    "4:5": {"x": 0.05, "y": 0.78, "width": 0.9, "height": 0.14},
}
"""Caixas normalizadas; no retrato a base evita a interface de Shorts e Reels."""

AUDIO_PROCESSING_FIELDS = frozenset({"normalize_loudness", "target_lufs", "limiter", "ducking"})
LIMITER_FIELDS = frozenset({"enabled", "ceiling_dbfs"})

SOURCE_KINDS = frozenset({"video", "audio", "image"})
FIT_MODES = frozenset({"preserve", "contain", "crop"})
AUDIO_MODES = frozenset({"keep", "mute", "replace", "mix"})
LAYER_TYPES = frozenset({"text", "image", "video"})
LAYER_FIT_MODES = frozenset({"contain", "crop"})
CUT_TYPES = frozenset({"j", "l"})
TEXT_ALIGNS = frozenset({"left", "center", "right"})


def aspect_ratio_value(aspect_ratio: str) -> float:
    """Converte uma proporção da allowlist em largura/altura para comparar com a de uma fonte."""

    width, height = ASPECT_RATIO_TERMS[aspect_ratio]
    return width / height


def canvas_dimensions(
    aspect_ratio: str,
    source_width: int,
    max_width: int | None,
    *,
    final: bool,
) -> tuple[int, int]:
    """Devolve (largura, altura) do canvas da preview (`final=False`) ou da full (`final=True`).

    Fonte única do tamanho de saída, lida por `plan validate` (`data.profile.full_*`), pelo render
    e, na T4, por `capabilities`. As proporções 9:16, 1:1 e 4:5 têm tamanho fixo de plataforma. O
    16:9 mantém a regra vigente: largura da primeira fonte de vídeo limitada por `max_width`
    (teto da preview ou `max_full_width`) e sem upscale. A preview alinha a 32 (`width * 9 // 16`
    exato e par). A full usa o canvas legado, alinhado a 16 com altura reduzida a par, sempre que a
    razão dele fica dentro de `LANDSCAPE_RATIO_TOLERANCE` (o MP4 seria aceito, então a full segue
    idêntica à de antes); só onde ele seria recusado (larguras múltiplas ímpares de 16 como 1200,
    cuja altura ímpar tira a razão da tolerância) usa o alinhamento 32 de fallback, com altura
    exata. Fonte menor que 32 pixels de largura gera canvas 0x0 (recusado antes do grafo).
    `max_width` só é lido no 16:9; as demais proporções o ignoram.
    """

    fixed = FIXED_CANVAS_DIMENSIONS.get(aspect_ratio)
    if fixed is not None:
        return fixed["full" if final else "preview"]
    width = min(source_width, max_width)
    if not final:
        width -= width % PREVIEW_WIDTH_ALIGNMENT
        return width, width * 9 // 16
    # ponytail: a regra legada da full é mantida porque a full 16:9 de todo plano v2 válido deve
    # continuar a mesma (decisão 14), embora deixe a razão até 0,001 fora do 16:9 exato (3184x1790);
    # unificar tudo em largura múltipla de 32 numa versão major.
    legacy_width = width - width % FULL_WIDTH_ALIGNMENT
    legacy_height = legacy_width * 9 // 16
    legacy_height -= legacy_height % 2
    if (
        legacy_height >= 2
        and abs(legacy_width / legacy_height - 16 / 9) <= LANDSCAPE_RATIO_TOLERANCE
    ):
        return legacy_width, legacy_height
    # Largura múltipla de 32: `* 9 // 16` é exato (18 por bloco de 32) e a altura já é par.
    width -= width % FULL_FALLBACK_WIDTH_ALIGNMENT
    return width, width * 9 // 16


def plan_error(message: str) -> CliError:
    """Cria o único erro público usado para qualquer inconsistência do plano."""

    return CliError(code="PLAN_INVALID", message=message, exit_code=EXIT_CODES["STATE"])


def require_object(value: Any, location: str) -> dict[str, Any]:
    """Garante que um trecho do JSON seja objeto antes de acessar suas chaves."""

    if not isinstance(value, dict):
        raise plan_error(f"O campo {location} deve ser um objeto JSON.")
    return value


def require_array(value: Any, location: str) -> list[Any]:
    """Garante que um trecho do JSON seja uma lista antes de iterar seus itens."""

    if not isinstance(value, list):
        raise plan_error(f"O campo {location} deve ser uma lista JSON.")
    return value


def reject_unknown_fields(
    value: Mapping[str, Any], allowed: Iterable[str], location: str
) -> None:
    """Rejeita campos não documentados para impedir parâmetros silenciosamente ignorados."""

    unknown = set(value) - set(allowed)
    if unknown:
        raise plan_error(f"O plano contém campo não permitido em {location}.")
