"""Testes da T2 da phase 5: `segments[].video.reframe` (foco e keyframes) e o aviso de ampliação.

Cobre, nesta ordem:

1. Validação (A2): normalização com padrões, hash canônico, cada violação recusada com
   `PLAN_INVALID` ANTES de inspecionar mídia (o inspetor de mídia é substituído por um espião e
   nenhuma fonte existe em disco), e os limites aceitos (32 keyframes, `t_s` na borda).
2. Ampliação (A3): `data.profile.upscale` com o fator de cada segmento, por metadados simulados e
   por FFprobe real (fontes 1080p e 4K), sem precisar de render.
3. Grafo: as expressões geradas contra as curvas da spec (keyframes aleatórios, sem render),
   tamanho com 32 keyframes em vários segmentos, números só pelo formatador do grafo e ausência
   de `eval=frame`/tempo nos casos estáticos.
4. Render real (A5, C2): fonte sintética com uma grade de linhas brancas em posições conhecidas; a
   posição das linhas nos quadros da preview e da full (9:16 e 16:9) é comparada com um oráculo
   que implementa a semântica da spec de forma independente do grafo, com tolerância de 2 pixels.

O oráculo (`expected_lines`) usa só as decisões 5 a 7 da spec: recorte da proporção do canvas,
centro em `(x, y)` normalizado sobre a fonte original, encosto na borda, `ease` = `3p² - 2p³` e
`linear`, estado mantido antes do primeiro e depois do último keyframe. Medir por posição e cor
(e não por hash de MP4) é necessário porque o x264 é com perda e depende de threads.

Sobre o "teto de 32.767 caracteres": é o limite da LINHA DE COMANDO do Windows. O grafo de filtros
viaja por arquivo (`-/filter_complex`) e não tem teto próprio, então os testes de tamanho usam o
valor como referência de compactação e o teste de render prova que um plano maior que ele
renderiza com a linha de comando abaixo do teto (ver `.erros-encontrados/` desta data).

As fontes são geradas aqui por `ffmpeg -f lavfi`, numa pasta descartável removida ao final.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
from typing import Any
import unittest
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies, process
from editor.contracts import CliError
from editor.integrity.approval import approve_workdir
from editor.media import MediaMetadata
from editor.plan.canonical import canonical_sha256
from editor.plan.schema import MAX_REFRAME_KEYFRAMES
from editor.plan.validation import load_validated_plan, validate_plan
from editor.render.ffmpeg_graph import _reframe_axis, _reframe_position_statements, build_ffmpeg_graph
from editor.render.full import render_full
from editor.render.preview import render_preview


PROJECT_ROOT = Path(__file__).resolve().parents[1]

POSITION_TOLERANCE_PX = 2.0
"""Tolerância de C2: diferença máxima entre a posição medida e a esperada, em pixels do arquivo."""

COMMAND_CEILING = 32_767
"""Teto de caracteres da linha de comando do Windows, também usado como referência de tamanho do grafo."""

# Grade da fonte sintética: linhas brancas de `max(2, largura // 480)` px a cada `largura / 40` (x)
# e `altura / 20` (y) px, sobre fundo cinza escuro; 1920x1080 dá 4 px de espessura e 48x54 de passo.
GRID_BACKGROUND = 40
LINE_THRESHOLD = 150
"""Luma (0 a 255) acima da qual um pixel conta como parte de uma linha (fundo ~40, linha ~255)."""

EDGE_STRIP_PX = 4
MIN_EDGE_LUMA = 20
"""Média mínima de luma nas 4 faixas de borda: barras pretas dariam ~0, o fundo da grade dá ~40."""

FRAME_RATE = 25
NO_AUDIO = {"mode": "mute", "tracks": []}


# --- Infraestrutura: planos, metadados simulados, fontes e leitura de quadros -----------------


def plan_document(aspect_ratio: str, segments: list[dict[str, Any]], **profile_extra: Any) -> dict[str, Any]:
    """Plano v2 mudo com um segmento por item; cada item traz `source` (`<L>x<A>`), `in_s`, `out_s`, `fit`.

    O id da fonte é derivado do nome (`src1920x1080`) e as fontes repetidas são declaradas uma vez.
    As chaves restantes do item (`reframe`, `motion`) vão para o objeto `video` do segmento.
    """

    sources: dict[str, dict[str, str]] = {}
    plan_segments = []
    for original in segments:
        item = dict(original)
        name = str(item.pop("source"))
        source_id = f"src{name}"
        sources[source_id] = {"id": source_id, "kind": "video", "path": f"sources/{name}.mp4"}
        video = {
            "source_id": source_id,
            "in_s": item.pop("in_s", 0),
            "out_s": item.pop("out_s", 2.0),
            "fit": item.pop("fit", "crop"),
            **item,
        }
        plan_segments.append({"video": video, "audio": dict(NO_AUDIO)})
    return {
        "schema_version": 2,
        "profile": {"aspect_ratio": aspect_ratio, **profile_extra},
        "sources": list(sources.values()),
        "segments": plan_segments,
        "cuts": [],
        "layers": [],
        "audio_processing": {
            "normalize_loudness": False, "target_lufs": -16,
            "limiter": {"enabled": False, "ceiling_dbfs": -1}, "ducking": False,
        },
    }


def keyframe(t_s: Any, x: Any, y: Any, zoom: Any = None, interpolation: Any = None) -> dict[str, Any]:
    """Keyframe com `zoom` e `interpolation` só quando informados (para provar os padrões)."""

    item: dict[str, Any] = {"t_s": t_s, "x": x, "y": y}
    if zoom is not None:
        item["zoom"] = zoom
    if interpolation is not None:
        item["interpolation"] = interpolation
    return item


def fake_inspect_by_name(path: Path, *, require_audio: bool, require_video: bool, **_: Any) -> MediaMetadata:
    """Metadados falsos: lê `<largura>x<altura>.mp4` do nome do arquivo, 25 fps e 60 s de duração."""

    width, height = (int(value) for value in path.stem.split("x"))
    return MediaMetadata(
        path=path, container="mp4", audio_codec=None, video_codec="h264", duration_seconds=60.0,
        size_bytes=path.stat().st_size, sample_rate=None, channels=None,
        video_width=width, video_height=height, video_frame_rate=25.0,
    )


def make_grid_source(path: Path, width: int, height: int, seconds: float) -> None:
    """Gera uma fonte H.264 sem áudio com a grade de linhas brancas de posições conhecidas."""

    thickness = max(2, width // 480)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
            (
                f"color=c=0x282828:s={width}x{height}:r={FRAME_RATE}:d={seconds},"
                f"drawgrid=w={width // 40}:h={height // 20}:t={thickness}:c=white,format=yuv420p"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "12", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )


def read_gray_frames(path: Path, indices: list[int], width: int, height: int) -> dict[int, bytes]:
    """Decodifica os quadros de índice dado em luma de 8 bits, numa única passada do FFmpeg."""

    ordered = sorted(set(indices))
    selector = "+".join(f"eq(n,{index})" for index in ordered)
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", f"select='{selector}'", "-fps_mode", "passthrough",
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        capture_output=True,
        check=True,
    )
    size = width * height
    if len(result.stdout) != size * len(ordered):
        raise AssertionError(f"{path.name}: esperados {len(ordered)} quadros de {width}x{height}")
    return {index: result.stdout[position * size:(position + 1) * size] for position, index in enumerate(ordered)}


def line_centroids(values: bytes) -> list[float]:
    """Centros (coordenada contínua, pixel `j` ocupa `[j, j+1)`) de cada trecho claro de uma linha de pixels."""

    centers: list[float] = []
    index = 0
    while index < len(values):
        if values[index] <= LINE_THRESHOLD:
            index += 1
            continue
        total = weighted = 0.0
        while index < len(values) and values[index] > LINE_THRESHOLD:
            weight = values[index] - GRID_BACKGROUND
            weighted += weight * (index + 0.5)
            total += weight
            index += 1
        centers.append(weighted / total)
    return centers


def quietest(frame: bytes, width: int, height: int, *, rows: bool) -> bytes:
    """Linha (ou coluna) de pixels com menos brilho do quadro: a que não cruza as linhas do outro eixo."""

    candidates = (
        (frame[y * width:(y + 1) * width] for y in range(0, height, 3))
        if rows
        else (frame[x::width] for x in range(0, width, 3))
    )
    return min(candidates, key=lambda values: sum(values[::4]))


def scale_options(graph: str) -> str:
    """Opções do primeiro `scale` do grafo, do `scale=` até o rótulo de saída."""

    match = re.search(r"scale=[^\[]*\[", graph)
    assert match is not None, "o grafo não tem `scale`"
    return match.group(0)


# --- Oráculo da spec (independente do grafo) --------------------------------------------------


def interpolated_state(keyframes: list[dict[str, Any]], t: float) -> tuple[float, float, float]:
    """Estado `(x, y, zoom)` no instante `t` segundo as decisões 6 e 7: mantém nas pontas e interpola por trecho."""

    def state(item: dict[str, Any]) -> tuple[float, float, float]:
        return float(item["x"]), float(item["y"]), float(item.get("zoom", 1.0))

    if t <= float(keyframes[0]["t_s"]):
        return state(keyframes[0])
    for first, second in zip(keyframes, keyframes[1:]):
        if t <= float(second["t_s"]):
            progress = (t - float(first["t_s"])) / (float(second["t_s"]) - float(first["t_s"]))
            if first.get("interpolation", "ease") == "ease":
                progress = progress * progress * (3 - 2 * progress)
            (x0, y0, z0), (x1, y1, z1) = state(first), state(second)
            return x0 + (x1 - x0) * progress, y0 + (y1 - y0) * progress, z0 + (z1 - z0) * progress
    return state(keyframes[-1])


def expected_lines(
    current: tuple[float, float, float],
    source: tuple[int, int],
    canvas: tuple[int, int],
) -> tuple[list[float], list[float]]:
    """Posições esperadas (pixels do canvas) das linhas da grade, verticais e horizontais.

    Recorte: maior janela da proporção do canvas que cabe na fonte, dividida pelo `zoom`, com o
    ponto `(x, y)` no centro e encostada na borda quando sairia da fonte (decisão 5). Só devolve
    linhas inteiramente visíveis e a pelo menos 4 px da borda, onde o centroide não é enviesado.
    """

    x, y, zoom = current
    source_width, source_height = source
    canvas_width, canvas_height = canvas
    crop_width = min(source_width, source_height * canvas_width / canvas_height) / zoom
    crop_height = crop_width * canvas_height / canvas_width
    left = min(max(x * source_width - crop_width / 2, 0), source_width - crop_width)
    top = min(max(y * source_height - crop_height / 2, 0), source_height - crop_height)
    scale = canvas_width / crop_width
    thickness = max(2, source_width // 480)

    def visible(spacing: float, count: int, window_start: float, limit: int) -> list[float]:
        """Centros das linhas de um eixo (a cada `spacing` px da fonte) inteiramente dentro do canvas."""

        positions = [(index * spacing + thickness / 2 - window_start) * scale for index in range(count)]
        half_line = thickness * scale / 2
        return [value for value in positions if half_line + 4 <= value <= limit - half_line - 4]

    return (
        visible(source_width / 40, 41, left, canvas_width),
        visible(source_height / 20, 21, top, canvas_height),
    )


class GridCase(unittest.TestCase):
    """Base com pasta descartável e criação de trabalhos com plano e fontes reais ou vazias."""

    @classmethod
    def setUpClass(cls) -> None:
        """Cria a pasta temporária do teste, removida ao final da classe."""

        cls.root = PROJECT_ROOT / f"tmp-reframe-{uuid4().hex}"
        cls.root.mkdir()
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)

    def new_workdir(self, plan: dict[str, Any], sources: dict[str, Path] | None = None) -> Path:
        """Cria `work-*` com o plano; cada fonte (`<L>x<A>`) de `sources` é copiada para `sources/`."""

        workdir = self.root / f"work-{uuid4().hex[:10]}"
        (workdir / "sources").mkdir(parents=True)
        for name, origin in (sources or {}).items():
            shutil.copy2(origin, workdir / "sources" / f"{name}.mp4")
        (workdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        return workdir

    def fake_media_workdir(self, plan: dict[str, Any]) -> Path:
        """Trabalho cujas fontes são arquivos vazios; só serve com `fake_inspect_by_name` aplicado."""

        workdir = self.new_workdir(plan)
        for source in plan["sources"]:
            (workdir / "sources" / Path(source["path"]).name).write_bytes(b"x")
        return workdir


# --- 1. Validação: normalização, hash e violações antes da mídia ---------------------------------


class ReframeValidationTests(GridCase):
    """Cobre A2: allowlist, normalização, hash e cada violação recusada antes de inspecionar mídia."""

    def validate_with_fake_media(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Valida o plano com metadados simulados (sem FFprobe) e devolve o envelope público."""

        workdir = self.fake_media_workdir(plan)
        with patch("editor.plan.validation.media.inspect_source", side_effect=fake_inspect_by_name):
            return validate_plan(workdir)

    def test_reframe_is_normalized_with_defaults_and_rounding(self) -> None:
        """Zoom 1.0 e `ease` são preenchidos, números arredondados a 6 casas e a ordem preservada."""

        plan = plan_document("9:16", [{
            "source": "1920x1080", "out_s": 4.0,
            "reframe": {"keyframes": [
                keyframe(0, 0.1234567, 0.5),
                keyframe(2, 0.9, 0.25, zoom=2.5, interpolation="linear"),
                keyframe(4, 0, 1, zoom=1.0000004),
            ]},
        }])
        segment = self.validate_with_fake_media(plan)["timeline"]["segments"][0]

        self.assertEqual(
            segment["reframe"],
            {"keyframes": [
                {"t_s": 0.0, "x": 0.123457, "y": 0.5, "zoom": 1.0, "interpolation": "ease"},
                {"t_s": 2.0, "x": 0.9, "y": 0.25, "zoom": 2.5, "interpolation": "linear"},
                {"t_s": 4.0, "x": 0.0, "y": 1.0, "zoom": 1.0, "interpolation": "ease"},
            ]},
        )

    def test_reframe_changes_the_canonical_hash_and_plans_without_it_are_unchanged(self) -> None:
        """O mesmo plano com `reframe` tem hash diferente; sem `reframe` o hash é o do documento puro."""

        base = plan_document("9:16", [{"source": "1920x1080"}])
        framed = plan_document("9:16", [{"source": "1920x1080", "reframe": {"keyframes": [keyframe(0, 0.5, 0.5)]}}])
        moved = plan_document("9:16", [{"source": "1920x1080", "reframe": {"keyframes": [keyframe(0, 0.6, 0.5)]}}])

        hashes = [self.validate_with_fake_media(plan)["plan_sha256"] for plan in (base, framed, moved)]

        self.assertEqual(hashes[0], canonical_sha256(base))
        self.assertEqual(len(set(hashes)), 3)

    def test_boundary_values_and_exact_keyframe_limit_are_accepted(self) -> None:
        """`t_s` em 0 e na duração, x/y/zoom nos extremos e exatamente 32 keyframes são válidos."""

        edges = plan_document("9:16", [{"source": "1920x1080", "out_s": 4.0, "reframe": {"keyframes": [
            keyframe(0, 0, 0, zoom=1.0, interpolation="ease"), keyframe(4.0, 1, 1, zoom=4.0),
        ]}}])
        packed = plan_document("1:1", [{"source": "1920x1080", "out_s": 3.2, "reframe": {"keyframes": [
            keyframe(round(index * 0.1, 1), 0.5, 0.5) for index in range(MAX_REFRAME_KEYFRAMES)
        ]}}])

        for name, plan, count in (("extremos", edges, 2), ("32 keyframes", packed, 32)):
            with self.subTest(case=name):
                segment = self.validate_with_fake_media(plan)["timeline"]["segments"][0]
                self.assertEqual(len(segment["reframe"]["keyframes"]), count)

    def test_every_violation_is_plan_invalid_before_probing_media(self) -> None:
        """Cada regra recusa com `PLAN_INVALID` e mensagem segura sem chamar o inspetor de mídia.

        Nenhuma fonte existe em disco: se a validação chegasse à mídia, o erro seria o da fonte
        ausente, e não o do plano. O espião `inspect_source` também não pode ser chamado.
        """

        two = [keyframe(0, 0.2, 0.5), keyframe(1, 0.8, 0.5)]
        order = "Os keyframes devem ter t_s crescente dentro da duração do segmento."
        count = f"O reenquadramento deve declarar de 1 a {MAX_REFRAME_KEYFRAMES} keyframes."
        point = "Os pontos x e y do keyframe devem estar entre 0 e 1."
        zoom_range = "O zoom do keyframe deve estar entre 1.0 e 4.0."
        interpolation = "A interpolação do keyframe deve ser ease ou linear."
        number = "deve ser um número finito."
        still = {"from": {"x": 0.5, "y": 0.5, "zoom": 1}, "to": {"x": 0.5, "y": 0.5, "zoom": 1}}
        cases: list[tuple[str, dict[str, Any], str]] = [
            # (nome, campos do `video`, trecho esperado da mensagem)
            ("sem crop (contain)", {"fit": "contain", "reframe": {"keyframes": two}}, "O reenquadramento exige fit crop."),
            ("sem crop (preserve)", {"fit": "preserve", "reframe": {"keyframes": two}}, "O reenquadramento exige fit crop."),
            ("com motion", {"motion": still, "reframe": {"keyframes": two}}, "Um segmento não pode declarar motion e reframe ao mesmo tempo."),
            ("t_s repetido", {"reframe": {"keyframes": [keyframe(1, 0.2, 0.5), keyframe(1, 0.8, 0.5)]}}, order),
            ("t_s decrescente", {"reframe": {"keyframes": [keyframe(1, 0.2, 0.5), keyframe(0.5, 0.8, 0.5)]}}, order),
            ("t_s iguais após arredondar", {"reframe": {"keyframes": [keyframe(1.0000001, 0.2, 0.5), keyframe(1.0000002, 0.8, 0.5)]}}, order),
            ("t_s negativo", {"reframe": {"keyframes": [keyframe(-0.1, 0.2, 0.5)]}}, order),
            ("t_s além da duração", {"reframe": {"keyframes": [keyframe(0, 0.2, 0.5), keyframe(2.01, 0.8, 0.5)]}}, order),
            ("x abaixo de 0", {"reframe": {"keyframes": [keyframe(0, -0.01, 0.5)]}}, point),
            ("x acima de 1", {"reframe": {"keyframes": [keyframe(0, 1.01, 0.5)]}}, point),
            ("y abaixo de 0", {"reframe": {"keyframes": [keyframe(0, 0.5, -0.5)]}}, point),
            ("y acima de 1", {"reframe": {"keyframes": [keyframe(0, 0.5, 2)]}}, point),
            ("zoom abaixo de 1.0", {"reframe": {"keyframes": [keyframe(0, 0.5, 0.5, zoom=0.99)]}}, zoom_range),
            ("zoom acima de 4.0", {"reframe": {"keyframes": [keyframe(0, 0.5, 0.5, zoom=4.01)]}}, zoom_range),
            ("interpolation desconhecida", {"reframe": {"keyframes": [keyframe(0, 0.5, 0.5, interpolation="cubic")]}}, interpolation),
            ("interpolation numérica", {"reframe": {"keyframes": [keyframe(0, 0.5, 0.5, interpolation=1)]}}, interpolation),
            ("interpolation lista", {"reframe": {"keyframes": [keyframe(0, 0.5, 0.5, interpolation=["ease"])]}}, interpolation),
            ("33 keyframes", {"reframe": {"keyframes": [keyframe(index * 0.05, 0.5, 0.5) for index in range(MAX_REFRAME_KEYFRAMES + 1)]}}, count),
            ("lista vazia", {"reframe": {"keyframes": []}}, count),
            ("campo desconhecido no reframe", {"reframe": {"keyframes": two, "easing": "ease"}}, "campo não permitido em segments[].video.reframe"),
            ("campo desconhecido no keyframe", {"reframe": {"keyframes": [{**keyframe(0, 0.5, 0.5), "w": 1}]}}, "campo não permitido em segments[].video.reframe.keyframes[]"),
            ("reframe fora de objeto", {"reframe": [keyframe(0, 0.5, 0.5)]}, "deve ser um objeto JSON"),
            ("reframe sem keyframes", {"reframe": {}}, "deve ser uma lista JSON"),
            ("keyframes fora de lista", {"reframe": {"keyframes": keyframe(0, 0.5, 0.5)}}, "deve ser uma lista JSON"),
            ("keyframe fora de objeto", {"reframe": {"keyframes": [[0, 0.5, 0.5]]}}, "deve ser um objeto JSON"),
            ("t_s booleano", {"reframe": {"keyframes": [keyframe(True, 0.5, 0.5)]}}, number),
            ("x booleano", {"reframe": {"keyframes": [keyframe(0, True, 0.5)]}}, number),
            ("y booleano", {"reframe": {"keyframes": [keyframe(0, 0.5, False)]}}, number),
            ("zoom booleano", {"reframe": {"keyframes": [keyframe(0, 0.5, 0.5, zoom=True)]}}, number),
            ("zoom nulo", {"reframe": {"keyframes": [{**keyframe(0, 0.5, 0.5), "zoom": None}]}}, number),
            ("x texto", {"reframe": {"keyframes": [keyframe(0, "0.5", 0.5)]}}, number),
            ("t_s ausente", {"reframe": {"keyframes": [{"x": 0.5, "y": 0.5}]}}, number),
        ]
        for name, video_fields, expected in cases:
            with self.subTest(case=name):
                plan = plan_document("9:16", [{"source": "1920x1080", "out_s": 2.0, **video_fields}])
                workdir = self.new_workdir(plan)  # nenhuma fonte em disco
                with patch("editor.plan.validation.media.inspect_source") as inspect:
                    with self.assertRaises(CliError) as raised:
                        validate_plan(workdir)
                self.assertEqual(raised.exception.code, "PLAN_INVALID")
                self.assertIn(expected, raised.exception.message)
                inspect.assert_not_called()


# --- 2. Ampliação (`data.profile.upscale`) -------------------------------------------------------


class UpscaleTests(GridCase):
    """Cobre A3: fator por segmento, índice 1-based, lista vazia e as fontes reais 1080p e 4K."""

    def upscale_for(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """Valida o plano com metadados simulados e devolve `data.profile.upscale`."""

        workdir = self.fake_media_workdir(plan)
        with patch("editor.plan.validation.media.inspect_source", side_effect=fake_inspect_by_name):
            return validate_plan(workdir)["profile"]["upscale"]

    def test_factors_follow_the_decision_9_examples(self) -> None:
        """1080p em 9:16 sem reframe amplia ~1.78; 4K em 9:16 não amplia (lista vazia)."""

        self.assertEqual(
            self.upscale_for(plan_document("9:16", [{"source": "1920x1080"}])),
            [{"segment": 1, "factor": 1.78}],
        )
        self.assertEqual(self.upscale_for(plan_document("9:16", [{"source": "3840x2160"}])), [])

    def test_factor_uses_the_highest_zoom_of_the_keyframes(self) -> None:
        """Com keyframes vale o maior `zoom` (3.56 = 1080 * 2 / 607,5), inclusive em 16:9 (punch-in)."""

        plan = plan_document("9:16", [{"source": "1920x1080", "out_s": 4.0, "reframe": {"keyframes": [
            keyframe(0, 0.5, 0.5, zoom=1.0), keyframe(2, 0.5, 0.5, zoom=2.0), keyframe(4, 0.5, 0.5, zoom=1.5),
        ]}}])
        self.assertEqual(self.upscale_for(plan), [{"segment": 1, "factor": 3.56}])

        landscape = plan_document("16:9", [{"source": "1920x1080", "reframe": {"keyframes": [
            keyframe(0, 0.5, 0.5), keyframe(1, 0.5, 0.5, zoom=2.0),
        ]}}])
        self.assertEqual(self.upscale_for(landscape), [{"segment": 1, "factor": 2.0}])

    def test_list_is_empty_when_nobody_is_enlarged_and_indexes_are_one_based(self) -> None:
        """Só o segmento ampliado é listado, pelo índice 1-based da timeline; o resto fica de fora."""

        same_size = plan_document("16:9", [{"source": "1920x1080"}, {"source": "1920x1080"}])
        self.assertEqual(self.upscale_for(same_size), [])

        mixed = plan_document("16:9", [{"source": "1920x1080"}, {"source": "1280x720"}, {"source": "1920x1080"}])
        self.assertEqual(self.upscale_for(mixed), [{"segment": 2, "factor": 1.5}])

    def test_contain_enlarges_a_source_smaller_than_the_canvas(self) -> None:
        """`contain` num canvas maior que a fonte também amplia: fator = menor razão canvas/fonte."""

        small = plan_document("9:16", [{"source": "640x360", "fit": "contain"}])
        self.assertEqual(self.upscale_for(small), [{"segment": 1, "factor": 1.69}])

        wide = plan_document("9:16", [{"source": "1920x1080", "fit": "contain"}])
        self.assertEqual(self.upscale_for(wide), [])

    def test_real_ffprobe_metadata_gives_the_same_factors_through_the_cli(self) -> None:
        """Fontes reais 1080p e 4K pela CLI: `upscale` ~1.78 no 1080p e vazio no 4K, `reframe` normalizado."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            self.skipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        sources = self.root / "upscale-sources"
        sources.mkdir(exist_ok=True)
        for name, (width, height) in {"1920x1080": (1920, 1080), "3840x2160": (3840, 2160)}.items():
            if not (sources / f"{name}.mp4").exists():
                make_grid_source(sources / f"{name}.mp4", width, height, 0.6)
        for name, expected in (("1920x1080", [{"segment": 1, "factor": 1.78}]), ("3840x2160", [])):
            with self.subTest(source=name):
                plan = plan_document("9:16", [{
                    "source": name, "out_s": 0.5, "reframe": {"keyframes": [keyframe(0, 0.5, 0.5)]},
                }])
                workdir = self.new_workdir(plan, {name: sources / f"{name}.mp4"})
                result = subprocess.run(
                    [sys.executable, "-m", "editor", "plan", "validate", "--workdir", str(workdir.resolve()), "--json"],
                    cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
                )
                payload = json.loads(result.stdout)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(payload["data"]["profile"]["upscale"], expected)
                self.assertEqual(
                    payload["data"]["timeline"]["segments"][0]["reframe"],
                    {"keyframes": [{"t_s": 0.0, "x": 0.5, "y": 0.5, "zoom": 1.0, "interpolation": "ease"}]},
                )


# --- 3. Grafo: tamanho, formatação e casos estáticos ---------------------------------------------


def dense_keyframes(count: int, duration: float) -> list[dict[str, Any]]:
    """`count` keyframes igualmente espaçados com x, y e zoom variando e `ease`/`linear` alternados."""

    return [
        keyframe(
            round(index * duration / (count - 1), 3),
            round(0.5 + 0.4 * math.sin(index * 0.7), 3),
            round(0.5 + 0.4 * math.cos(index * 0.5), 3),
            zoom=round(1.0 + 3.0 * abs(math.sin(index * 0.3)), 3),
            interpolation="ease" if index % 2 == 0 else "linear",
        )
        for index in range(count)
    ]


def evaluate_ffmpeg_expression(statements: list[str], expression: str, t: float) -> float:
    """Avalia, com o `eval` do Python, o subconjunto de expressões do FFmpeg que o grafo de `reframe` usa.

    Esse subconjunto (`st`, `ld`, `clip`, `floor`, `ceil`, `eq`, `min`, `max`, aritmética e `t`) tem a
    mesma sintaxe e semântica no Python, então as instruções de `s(t)` e a expressão do eixo geradas
    para o FFmpeg podem ser comparadas com o oráculo sem renderizar. A memória de `st`/`ld` é nova a
    cada avaliação, como a das expressões `scale eval=frame` (reanalisadas a cada quadro).
    """

    memory: dict[int, float] = {}

    def store(slot: float, value: float) -> float:
        memory[int(slot)] = value
        return value

    namespace: dict[str, Any] = {
        "t": t, "st": store, "ld": lambda slot: memory[int(slot)],
        "clip": lambda value, low, high: min(max(value, low), high),
        "floor": math.floor, "ceil": math.ceil, "min": min, "max": max,
        "eq": lambda first, second: 1.0 if first == second else 0.0,
    }
    # `eval` só recebe texto gerado pelo próprio grafo (números do `_number` e funções da allowlist acima).
    for statement in statements:
        eval(statement, {"__builtins__": {}}, namespace)
    return float(eval(expression, {"__builtins__": {}}, namespace))


class ReframeExpressionTests(unittest.TestCase):
    """Compara as expressões geradas com as curvas da spec para keyframes aleatórios, sem render."""

    @staticmethod
    def random_keyframes(rng: random.Random, count: int, mode: str) -> list[dict[str, Any]]:
        """Keyframes crescentes com valores de 3 casas; `mode` fixa a mistura de `ease` e `linear`."""

        eased = {"ease": 1.0, "linear": 0.0, "mostly-ease": 0.8, "mostly-linear": 0.2, "mixed": 0.5}[mode]
        keyframes = []
        t_s = round(rng.uniform(0, 2), 3)
        for _ in range(count):
            keyframes.append(keyframe(
                t_s,
                round(rng.uniform(0, 1), 3),
                # Alguns planos mantêm um eixo parado para exercitar eixos constantes e deltas nulos.
                0.5 if mode == "mixed" and rng.random() < 0.3 else round(rng.uniform(0, 1), 3),
                zoom=round(rng.uniform(1, 4), 3),
                interpolation="ease" if rng.random() < eased else "linear",
            ))
            t_s = round(t_s + rng.uniform(0.001, 1.5), 3)
        return keyframes

    def test_generated_expressions_match_the_ease_and_linear_curves(self) -> None:
        """`s(t)` e os três eixos reproduzem o oráculo (erro < 1e-9) em 5 misturas de curvas e 2 a 32 keyframes.

        Cobre as duas formas do peso de `ease` (lista de índices `ease` ou o complemento dos `linear`),
        o instante exato de cada keyframe e seus vizinhos de 1e-9 s (onde `floor(u)` troca de trecho),
        e os instantes antes do primeiro e depois do último keyframe.
        """

        rng = random.Random(20261008)
        for case in range(250):
            mode = ("ease", "linear", "mostly-ease", "mostly-linear", "mixed")[case % 5]
            count = rng.choice([2, 2, 3, 4, 5, 8, 16, 31, 32])
            keyframes = self.random_keyframes(rng, count, mode)
            statements = _reframe_position_statements(keyframes)
            axes = {axis: _reframe_axis(keyframes, axis)[0] for axis in ("x", "y", "zoom")}
            first, last = float(keyframes[0]["t_s"]), float(keyframes[-1]["t_s"])
            instants = [first - 0.5, last + 0.5, *(rng.uniform(first, last) for _ in range(20))]
            for item in keyframes:
                instants += [float(item["t_s"]), float(item["t_s"]) - 1e-9, float(item["t_s"]) + 1e-9]
            for t in instants:
                expected = interpolated_state(keyframes, t)
                for position, (axis, expression) in enumerate(axes.items()):
                    got = evaluate_ffmpeg_expression(statements, expression, t)
                    self.assertAlmostEqual(
                        got, expected[position], delta=1e-9,
                        msg=f"caso {case} ({mode}, {count} keyframes), eixo {axis}, t={t}",
                    )


class ReframeGraphTests(GridCase):
    """Cobre o grafo gerado sem renderizar: tamanho, números formatados e casos estáticos."""

    def graph_for(self, plan: dict[str, Any], canvas: tuple[int, int]) -> str:
        """Monta o `filter_complex` do plano (metadados simulados, sem janelas de decode)."""

        workdir = self.fake_media_workdir(plan)
        with patch("editor.plan.validation.media.inspect_source", side_effect=fake_inspect_by_name):
            document, validation = load_validated_plan(workdir)
        return build_ffmpeg_graph(document, validation, canvas_width=canvas[0], canvas_height=canvas[1]).filter_complex

    def test_graph_with_32_keyframes_in_8_panning_segments_stays_under_the_ceiling(self) -> None:
        """8 segmentos de 32 keyframes com panorâmica (x e y variam, zoom fixo) cabem em 32.767 caracteres.

        Cada expressão com eixo animado repete a posição `s(t)` (uma soma de `clip` por trecho) e
        paga um termo `delta * clip(s - j, 0, 1)` por keyframe; o zoom fixo dispensa a expressão
        do `scale`. Planos maiores que o teto continuam renderizando (grafo por arquivo): ver o
        teste de render de 6 segmentos.
        """

        segments = [
            {
                "source": "1920x1080", "out_s": 4.96,
                "reframe": {"keyframes": [{**item, "zoom": 1.5} for item in dense_keyframes(32, 4.96)]},
            }
            for _ in range(8)
        ]
        graph = self.graph_for(plan_document("9:16", segments), (1080, 1920))

        self.assertLessEqual(len(graph), COMMAND_CEILING)
        self.assertEqual(graph.count("overlay="), 8)

    def test_graph_with_32_keyframes_and_animated_zoom_in_5_segments_stays_under_the_ceiling(self) -> None:
        """5 segmentos de 32 keyframes com x, y e zoom animados (o caso mais caro) cabem em 32.767 caracteres."""

        segments = [
            {"source": "1920x1080", "out_s": 4.96, "reframe": {"keyframes": dense_keyframes(32, 4.96)}}
            for _ in range(5)
        ]
        graph = self.graph_for(plan_document("9:16", segments), (1080, 1920))

        self.assertLessEqual(len(graph), COMMAND_CEILING)
        self.assertEqual(graph.count("overlay="), 5)

    def test_every_number_in_the_graph_comes_from_the_number_formatter(self) -> None:
        """Nenhum literal com mais de 6 casas, notação científica ou valor não finito entra no grafo."""

        odd = [keyframe(0, 1 / 3, 2 / 3, zoom=1.0 + 1 / 7), keyframe(1 / 3, 0.000001, 0.999999, zoom=3.999999)]
        graph = self.graph_for(
            plan_document("4:5", [{"source": "1920x1080", "out_s": 1.0, "reframe": {"keyframes": odd}}]),
            (1080, 1350),
        )

        self.assertNotRegex(graph, r"\d\.\d{7,}")
        self.assertNotRegex(graph, r"\d[eE][-+]?\d")
        self.assertNotRegex(graph, r"\b(nan|inf)\b")
        self.assertIn("0.333333", graph)

    def test_static_focus_has_no_time_expression_and_no_per_frame_scale(self) -> None:
        """Um keyframe, ou keyframes iguais, geram um grafo sem `clip`, `st()` nem `eval=frame` no `scale`."""

        single = [keyframe(0.5, 0.3, 0.6, zoom=2.0)]
        equal = [keyframe(0, 0.3, 0.6, zoom=2.0), keyframe(1, 0.3, 0.6, zoom=2.0)]
        for name, keyframes in (("um keyframe", single), ("keyframes iguais", equal)):
            with self.subTest(case=name):
                graph = self.graph_for(
                    plan_document("9:16", [{"source": "1920x1080", "out_s": 1.0, "reframe": {"keyframes": keyframes}}]),
                    (1080, 1920),
                )
                self.assertNotIn("st(", graph)
                self.assertNotIn("clip(", graph)
                self.assertNotIn("floor(", graph)
                self.assertNotIn("eval=frame", scale_options(graph))

    def test_constant_zoom_keeps_the_scale_static_while_position_still_animates(self) -> None:
        """Só o zoom animado paga `scale eval=frame`; panorâmica com zoom fixo mantém o `scale` estático."""

        pan = [keyframe(0, 0.2, 0.5), keyframe(1, 0.8, 0.5)]
        zoom = [keyframe(0, 0.5, 0.5), keyframe(1, 0.5, 0.5, zoom=2.0)]
        pan_graph = self.graph_for(
            plan_document("9:16", [{"source": "1920x1080", "out_s": 1.0, "reframe": {"keyframes": pan}}]), (1080, 1920))
        zoom_graph = self.graph_for(
            plan_document("9:16", [{"source": "1920x1080", "out_s": 1.0, "reframe": {"keyframes": zoom}}]), (1080, 1920))

        self.assertNotIn("eval=frame", scale_options(pan_graph))
        self.assertIn("clip(", pan_graph)
        self.assertIn("eval=frame", scale_options(zoom_graph))


# --- 4. Render real: posição do recorte contra o oráculo -----------------------------------------


class ReframeRenderTests(GridCase):
    """Renderiza preview e full reais e compara a posição das linhas da grade com a semântica da spec."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFmpeg/FFprobe e gera as fontes de grade (1080p de 7 s e a mesma grade em 540p)."""

        if dependencies.resolve_executable("ffmpeg") is None or dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffmpeg e ffprobe não estão disponíveis localmente")
        super().setUpClass()
        cls.sources = cls.root / "shared-sources"
        cls.sources.mkdir()
        make_grid_source(cls.sources / "1920x1080.mp4", 1920, 1080, 7.0)
        make_grid_source(cls.sources / "960x540.mp4", 960, 540, 7.0)

    def assert_positions(
        self,
        video: Path,
        keyframes: list[dict[str, Any]],
        instants: list[float],
        source: tuple[int, int],
        canvas: tuple[int, int],
        *,
        offset_s: float = 0.0,
    ) -> None:
        """Mede os quadros dos instantes dados e exige as linhas esperadas com até 2 px de erro e sem barras.

        Os instantes são da timeline; `offset_s` é o início do segmento, de onde `t_s` dos keyframes é contado.
        """

        indices = [round(instant * FRAME_RATE) for instant in instants]
        frames = read_gray_frames(video, indices, *canvas)
        for instant, index in zip(instants, indices):
            frame = frames[index]
            current = interpolated_state(keyframes, index / FRAME_RATE - offset_s)
            expected_x, expected_y = expected_lines(current, source, canvas)
            measured_x = line_centroids(quietest(frame, *canvas, rows=True))
            measured_y = line_centroids(quietest(frame, *canvas, rows=False))
            with self.subTest(video=video.name, t=instant, state=tuple(round(value, 3) for value in current)):
                self.assertGreaterEqual(len(expected_x), 2, "o instante precisa expor linhas verticais")
                self.assertGreaterEqual(len(expected_y), 2, "o instante precisa expor linhas horizontais")
                for axis, expected, measured in (("x", expected_x, measured_x), ("y", expected_y, measured_y)):
                    self.assertTrue(measured, f"nenhuma linha medida no eixo {axis}")
                    worst = max(min(abs(found - wanted) for found in measured) for wanted in expected)
                    self.assertLessEqual(worst, POSITION_TOLERANCE_PX, f"eixo {axis}: erro de {worst:.2f} px")
                self.assert_no_black_edges(frame, *canvas)

    def assert_no_black_edges(self, frame: bytes, width: int, height: int) -> None:
        """Nenhuma das quatro faixas de borda é escura: o recorte nunca mostra barras pretas."""

        strips = {
            "topo": frame[: EDGE_STRIP_PX * width],
            "base": frame[-EDGE_STRIP_PX * width:],
            "esquerda": b"".join(frame[x::width] for x in range(EDGE_STRIP_PX)),
            "direita": b"".join(frame[x::width] for x in range(width - EDGE_STRIP_PX, width)),
        }
        for name, strip in strips.items():
            self.assertGreaterEqual(sum(strip) / len(strip), MIN_EDGE_LUMA, f"faixa de borda {name} escura")

    def render(self, plan: dict[str, Any], names: dict[str, Path], *, full: bool) -> tuple[Path, dict[str, Any]]:
        """Cria o trabalho, renderiza a preview (e a full aprovada se pedido) e devolve o trabalho e a preview."""

        workdir = self.new_workdir(plan, names)
        preview = render_preview(workdir)
        if full:
            approve_workdir(workdir)
            render_full(workdir)
        return workdir, preview

    def test_9x16_full_and_preview_follow_ease_linear_hold_and_borders(self) -> None:
        """Quadros nos keyframes, a 25%, 50% e 75% de cada trecho, nas pontas e nas bordas (A5, C2).

        O segmento usa o trecho 2 s a 6 s da fonte: a preview e a full leem uma janela de decode
        (`data.decode.windowed`), então `t` das expressões também é provado relativo ao segmento.
        Trechos (segundos do segmento, todos em quadros inteiros a 25 fps): `ease` de 0,4 a 2,0;
        `linear` de 2,0 a 2,8 (x, y e zoom variam); `ease` de 2,8 a 3,6 (com zoom). Os quartos
        distinguem `ease` de `linear` (a curva só coincide com a reta nos extremos e no meio: em
        p = 0,25 o `ease` anda 15,6% do trecho, o `linear` 25%). O keyframe de 3,6 s tem x = 0 e
        y = 0,9 com zoom 2 e encosta nas bordas esquerda e inferior; 0,2 s fica antes do primeiro
        keyframe e 3,8 s depois do último.
        """

        keyframes = [
            keyframe(0.4, 0.3, 0.5, zoom=1.0, interpolation="ease"),
            keyframe(2.0, 0.7, 0.5, zoom=1.0, interpolation="linear"),
            keyframe(2.8, 0.5, 0.9, zoom=2.0, interpolation="ease"),
            keyframe(3.6, 0.0, 0.9, zoom=2.0),
        ]
        plan = plan_document("9:16", [{
            "source": "1920x1080", "in_s": 2.0, "out_s": 6.0, "reframe": {"keyframes": keyframes},
        }])
        workdir, preview = self.render(plan, {"1920x1080": self.sources / "1920x1080.mp4"}, full=True)

        self.assertTrue(preview["decode"]["windowed"])
        instants = [0.2, 0.4, 0.8, 1.2, 1.6, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8]
        self.assert_positions(workdir / "preview.mp4", keyframes, instants, (1920, 1080), (404, 720))
        self.assert_positions(workdir / "full.mp4", keyframes, instants, (1920, 1080), (1080, 1920))

    def test_oracle_implements_the_ease_and_linear_curves_of_the_spec(self) -> None:
        """Confere o oráculo contra a spec: média exata em p = 0,5 nas duas curvas e 15,625% (ease) ou 25% (linear) em p = 0,25."""

        for interpolation, quarter in (("ease", 0.15625), ("linear", 0.25)):
            pair = [keyframe(0, 0.2, 0.4, zoom=1.0, interpolation=interpolation), keyframe(2, 0.8, 0.6, zoom=3.0)]
            midpoint = interpolated_state(pair, 1.0)
            self.assertEqual(tuple(round(value, 9) for value in midpoint), (0.5, 0.5, 2.0))
            self.assertAlmostEqual(interpolated_state(pair, 0.5)[0], 0.2 + 0.6 * quarter, places=9)

    def test_single_keyframe_is_a_static_focus_in_any_instant(self) -> None:
        """Um keyframe com `t_s` > 0 vale como foco fixo antes e depois dele (A5)."""

        keyframes = [keyframe(1.0, 0.3, 0.5)]
        plan = plan_document("9:16", [{"source": "1920x1080", "out_s": 2.0, "reframe": {"keyframes": keyframes}}])
        workdir, _ = self.render(plan, {"1920x1080": self.sources / "1920x1080.mp4"}, full=False)

        self.assert_positions(workdir / "preview.mp4", keyframes, [0.0, 0.2, 1.0, 1.8], (1920, 1080), (404, 720))

    def test_pan_with_constant_zoom_uses_the_static_scale_and_follows_the_keyframes(self) -> None:
        """Panorâmica com zoom 1.5 fixo (`scale` estático, só o overlay anima) acompanha o oráculo."""

        keyframes = [
            keyframe(0.2, 0.15, 0.3, zoom=1.5, interpolation="linear"),
            keyframe(1.0, 0.85, 0.75, zoom=1.5, interpolation="ease"),
            keyframe(1.8, 0.5, 0.5, zoom=1.5),
        ]
        plan = plan_document("9:16", [{"source": "1920x1080", "out_s": 2.0, "reframe": {"keyframes": keyframes}}])
        workdir, _ = self.render(plan, {"1920x1080": self.sources / "1920x1080.mp4"}, full=False)

        self.assert_positions(
            workdir / "preview.mp4", keyframes, [0.0, 0.2, 0.4, 0.6, 1.0, 1.2, 1.4, 1.8, 1.96], (1920, 1080), (404, 720)
        )

    def test_positions_are_independent_of_the_source_resolution(self) -> None:
        """A mesma grade em 960x540 e os mesmos keyframes dão as mesmas posições (vale para o proxy reduzido)."""

        keyframes = [
            keyframe(0.2, 0.25, 0.5, zoom=1.0, interpolation="ease"),
            keyframe(1.4, 0.75, 0.5, zoom=1.5, interpolation="linear"),
            keyframe(2.0, 0.6, 0.2, zoom=2.0),
        ]
        plan = plan_document("9:16", [{"source": "960x540", "out_s": 2.4, "reframe": {"keyframes": keyframes}}])
        workdir, _ = self.render(plan, {"960x540": self.sources / "960x540.mp4"}, full=False)

        self.assert_positions(workdir / "preview.mp4", keyframes, [0.2, 0.8, 1.4, 2.0, 2.2], (960, 540), (404, 720))

    def test_32_keyframes_with_zoom_up_to_4_render_in_16x9(self) -> None:
        """Um segmento com 32 keyframes, `ease` e `linear` alternados e zoom até 4.0 renderiza e acompanha o oráculo."""

        keyframes = dense_keyframes(MAX_REFRAME_KEYFRAMES, 4.96)
        self.assertGreater(max(float(item["zoom"]) for item in keyframes), 3.5)
        plan = plan_document("16:9", [{"source": "1920x1080", "out_s": 4.96, "reframe": {"keyframes": keyframes}}])
        workdir, preview = self.render(plan, {"1920x1080": self.sources / "1920x1080.mp4"}, full=False)

        self.assertEqual((preview["video"]["width"], preview["video"]["height"]), (704, 396))
        # Quadros 4*i caem nos keyframes (0,16 s * 25 fps = 4 quadros) e 4*i+2 no meio de cada trecho.
        instants = [index * 4 / FRAME_RATE for index in (0, 5, 13, 22, 30)]
        instants += [(index * 4 + 2) / FRAME_RATE for index in (3, 10, 21, 27)]
        self.assert_positions(workdir / "preview.mp4", keyframes, instants, (1920, 1080), (704, 396))

    def test_six_segments_with_32_keyframes_each_render_through_the_file_transport(self) -> None:
        """6 segmentos de 32 keyframes (x, y e zoom animados) renderizam e o tempo recomeça em cada segmento.

        O grafo desse plano passa de 32.767 caracteres (cerca de 34 mil), mas viaja por arquivo: a
        linha de comando do FFmpeg, que é o que o teto do Windows limita, fica abaixo dele. Os mesmos
        keyframes em cada segmento provam que `t` das expressões é o tempo desde o início do segmento
        (e não o da timeline) no primeiro, no quarto e no último. Cada segmento tem 1,28 s (32 quadros).
        """

        keyframes = dense_keyframes(MAX_REFRAME_KEYFRAMES, 1.24)
        segments = [
            {"source": "1920x1080", "in_s": index * 0.5, "out_s": index * 0.5 + 1.28, "reframe": {"keyframes": keyframes}}
            for index in range(6)
        ]
        workdir = self.new_workdir(plan_document("9:16", segments), {"1920x1080": self.sources / "1920x1080.mp4"})
        captured: dict[str, int] = {}
        original_run_tool = process.run_tool

        def spy_run_tool(tool: str, arguments: list[str], *, timeout_seconds: float) -> Any:
            """Mede a linha de comando do render sem alterá-lo (o FFmpeg real é executado)."""

            if tool == "ffmpeg" and dependencies.ffmpeg_filter_option() in arguments:
                captured["command_length"] = len(tool) + 1 + sum(len(str(argument)) + 1 for argument in arguments)
            return original_run_tool(tool, arguments, timeout_seconds=timeout_seconds)

        with patch("editor.render.preview.process.run_tool", side_effect=spy_run_tool):
            preview = render_preview(workdir)

        self.assertLess(captured["command_length"], COMMAND_CEILING)
        self.assertEqual(preview["segment_count"], 6)
        for index in (0, 3, 5):
            start = index * 1.28
            instants = [start + frame / FRAME_RATE for frame in (0, 8, 20, 31)]
            self.assert_positions(workdir / "preview.mp4", keyframes, instants, (1920, 1080), (404, 720), offset_s=start)


if __name__ == "__main__":
    unittest.main()
