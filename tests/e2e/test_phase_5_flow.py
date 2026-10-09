"""Prova E2E da Phase 5: jornada 9:16 com `reframe`, proxy, aprovação por hash e full com janelas.

Percorre, pelo ponto de entrada real da CLI e com uma fonte 1080p sintética gerada aqui por
`ffmpeg -f lavfi` (nada grande no repositório): `init`, `capabilities`, `plan validate` (perfil
9:16, `upscale` e `reframe` normalizado), `render preview --proxy off`, `proxy build` explícito,
`render preview` com proxy, `approve`, uma mutação do `reframe` que bloqueia a full e, por fim, a
full 9:16 lida em janelas de decode e sempre dos originais (A7 e A8 da spec).

A fonte tem 12 s e o plano usa só dois trechos de 3 s separados por uma lacuna maior que a de
mescla das janelas, então a leitura estimada cai bem acima do ganho mínimo de 20% e o render é
janelado. As expectativas numéricas de `upscale` e das janelas vêm da spec (decisões 5 e 9 e
`render/windows.py`), não do código que as gera.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from editor import dependencies
from editor.media import inspect_rendered_artifact
from editor.plan.schema import FIXED_CANVAS_DIMENSIONS
from editor.render.windows import MERGE_GAP_S
from tests.e2e.test_mvp_flow import (
    assert_json_error,
    assert_json_success,
    run_cli,
    sha256_file,
    workspace_tempdir,
)


ASPECT_RATIO = "9:16"
SOURCE_ID = "main"
SOURCE_SIZE = (1_920, 1_080)
SOURCE_SECONDS = 12
FRAME_RATE = 25
SEGMENT_SECONDS = 3.0
FIRST_SEGMENT = (1.0, 4.0)
SECOND_SEGMENT = (8.0, 11.0)
TIMELINE_DURATION_S = SEGMENT_SECONDS * 2
FULL_SIZE = FIXED_CANVAS_DIMENSIONS[ASPECT_RATIO]["full"]
PREVIEW_SIZE = FIXED_CANVAS_DIMENSIONS[ASPECT_RATIO]["preview"]
PROXY_SIZE = (1_280, 720)

# Primeiro trecho: `ease` até 1,5 s, `linear` até 3 s e o último keyframe volta ao zoom 1.0 (o
# `interpolation` dele não tem efeito). O maior `zoom` (1.5) define o aviso de ampliação do trecho.
FIRST_KEYFRAMES = [
    {"t_s": 0, "x": 0.3, "y": 0.5, "zoom": 1.0, "interpolation": "ease"},
    {"t_s": 1.5, "x": 0.7, "y": 0.45, "zoom": 1.5, "interpolation": "linear"},
    {"t_s": 3.0, "x": 0.5, "y": 0.5},
]
# Segundo trecho: um único keyframe é o foco estático, com os padrões de `zoom` e `interpolation`.
SECOND_KEYFRAMES = [{"t_s": 0, "x": 0.25, "y": 0.5}]


def make_source(path: Path) -> None:
    """Gera a fonte 1080p H.264/AAC de 12 s por lavfi, com GOP de 1 s para o seek das janelas."""

    ffmpeg = dependencies.resolve_executable("ffmpeg")
    assert ffmpeg is not None
    subprocess.run(
        [
            str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"testsrc2=size={SOURCE_SIZE[0]}x{SOURCE_SIZE[1]}:rate={FRAME_RATE}:duration={SOURCE_SECONDS}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={SOURCE_SECONDS}",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", str(FRAME_RATE), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )


def plan_document() -> dict[str, object]:
    """Plano 9:16 com dois trechos `crop` reenquadrados por keyframes, áudio da fonte e um texto.

    O `size_px` do texto vale na largura da full (1080) e a preview o escala; a lacuna entre os
    trechos (4 s) passa de `MERGE_GAP_S`, o que separa a leitura da fonte em duas janelas.
    """

    def segment(window: tuple[float, float], keyframes: list[dict[str, object]]) -> dict[str, object]:
        """Trecho da fonte com `fit: crop`, `reframe` e a faixa de áudio do mesmo intervalo."""

        in_s, out_s = window
        return {
            "video": {
                "source_id": SOURCE_ID, "in_s": in_s, "out_s": out_s, "fit": "crop",
                "reframe": {"keyframes": keyframes},
            },
            "audio": {
                "mode": "keep",
                "tracks": [{"source_id": SOURCE_ID, "in_s": in_s, "out_s": out_s, "gain_db": 0}],
            },
        }

    return {
        "schema_version": 2,
        "profile": {"aspect_ratio": ASPECT_RATIO},
        "sources": [{"id": SOURCE_ID, "kind": "video", "path": "sources/main.mp4"}],
        "segments": [segment(FIRST_SEGMENT, FIRST_KEYFRAMES), segment(SECOND_SEGMENT, SECOND_KEYFRAMES)],
        "cuts": [],
        "layers": [{
            "type": "text",
            "start_s": 1.0,
            "end_s": 4.0,
            "x": 0.08,
            "y": 0.72,
            "width": 0.84,
            "height": 0.1,
            "text": "Reenquadrado",
            "font": "Arial",
            "size_px": 96,
            "color": "#FFFFFF",
            "align": "center",
            "wrap": True,
        }],
        "audio_processing": {
            "normalize_loudness": False,
            "target_lufs": -16,
            "limiter": {"enabled": False, "ceiling_dbfs": -1},
            "ducking": False,
        },
    }


def expected_upscale_factor(canvas: tuple[int, int], zoom: float) -> float:
    """Decisão 9 da spec: largura do canvas da full sobre a largura do menor recorte, em 2 casas.

    O recorte de `zoom` 1.0 é o maior da proporção do canvas que cabe na fonte, e o de `zoom` Z tem
    a largura dividida por Z.
    """

    base = min(SOURCE_SIZE[0], SOURCE_SIZE[1] * canvas[0] / canvas[1])
    return round(canvas[0] * zoom / base, 2)


def tmp_entries(workdir: Path) -> list[str]:
    """Nomes que sobraram em `.tmp`; o contrato exige a pasta vazia após qualquer desfecho."""

    return sorted(path.name for path in (workdir / ".tmp").iterdir())


class PhaseFiveFlowTests(unittest.TestCase):
    """Confirma a jornada 9:16 completa: contrato, proxy, aprovação, bloqueio por mutação e full."""

    @classmethod
    def setUpClass(cls) -> None:
        """Falha o gate quando FFmpeg e FFprobe locais não estão disponíveis."""

        if any(dependencies.resolve_executable(name) is None for name in ("ffmpeg", "ffprobe")):
            raise AssertionError("ffmpeg e ffprobe não estão disponíveis localmente")

    def test_vertical_reframe_journey_with_proxy_approval_and_windowed_full(self) -> None:
        """Executa validate, preview (sem e com proxy), approve, mutação bloqueada e full 9:16."""

        with workspace_tempdir("phase-5-e2e") as workdir:
            workdir_argument = str(workdir.resolve())
            assert_json_success(self, run_cli("init", "--workdir", workdir_argument, "--json"), "init")
            (workdir / "sources").mkdir()
            source = workdir / "sources" / "main.mp4"
            make_source(source)
            plan_path = workdir / "plan.json"
            plan_path.write_text(json.dumps(plan_document()), encoding="utf-8")
            original_source_hash = sha256_file(source)

            # --- Contrato publicado: o inventário descreve o que o plano e os renders entregam.
            capabilities = assert_json_success(self, run_cli("capabilities", "--json"), "capabilities")
            plan_capabilities = capabilities["data"]["plan"]
            canvas_capabilities = plan_capabilities["profile"]["canvas"][ASPECT_RATIO]
            self.assertIn(ASPECT_RATIO, plan_capabilities["profile"]["aspect_ratios"])
            self.assertEqual(plan_capabilities["reframe"]["interpolations"], ["ease", "linear"])
            self.assertLessEqual(len(FIRST_KEYFRAMES), plan_capabilities["reframe"]["max_keyframes"])

            # --- Validação: perfil 9:16, aviso de ampliação e `reframe` normalizado.
            validated = assert_json_success(
                self, run_cli("plan", "validate", "--workdir", workdir_argument, "--json"), "plan validate"
            )
            profile = validated["data"]["profile"]
            self.assertEqual(profile["aspect_ratio"], ASPECT_RATIO)
            self.assertEqual((profile["full_width"], profile["full_height"]), (1_080, 1_920))
            self.assertEqual(
                (canvas_capabilities["full"]["width"], canvas_capabilities["full"]["height"]),
                (profile["full_width"], profile["full_height"]),
            )
            # Fora do 16:9 o perfil não tem `max_full_width`, e os campos são os que o inventário anuncia.
            self.assertEqual(set(profile), set(plan_capabilities["profile"]["envelope_fields"]))
            self.assertEqual(
                profile["upscale"],
                [
                    {"segment": 1, "factor": expected_upscale_factor(FULL_SIZE, 1.5)},
                    {"segment": 2, "factor": expected_upscale_factor(FULL_SIZE, 1.0)},
                ],
            )
            self.assertEqual([item["factor"] for item in profile["upscale"]], [2.67, 1.78])
            segments = validated["data"]["timeline"]["segments"]
            self.assertEqual(validated["data"]["timeline"]["duration_seconds"], TIMELINE_DURATION_S)
            self.assertEqual(
                segments[0]["reframe"],
                {"keyframes": [
                    {"t_s": 0.0, "x": 0.3, "y": 0.5, "zoom": 1.0, "interpolation": "ease"},
                    {"t_s": 1.5, "x": 0.7, "y": 0.45, "zoom": 1.5, "interpolation": "linear"},
                    {"t_s": 3.0, "x": 0.5, "y": 0.5, "zoom": 1.0, "interpolation": "ease"},
                ]},
            )
            self.assertEqual(
                segments[1]["reframe"],
                {"keyframes": [{"t_s": 0.0, "x": 0.25, "y": 0.5, "zoom": 1.0, "interpolation": "ease"}]},
            )
            plan_sha256 = validated["data"]["plan_sha256"]

            # --- Preview sem proxy: 404x720, janelada, sem nenhum proxy lido.
            self.assertGreater(SECOND_SEGMENT[0] - FIRST_SEGMENT[1], MERGE_GAP_S)
            plain = assert_json_success(
                self,
                run_cli("render", "preview", "--workdir", workdir_argument, "--proxy", "off", "--json"),
                "render preview",
            )
            self.assertEqual(plain["data"]["proxy"], {"mode": "off", "sources": [], "stale": []})
            self.assertEqual(plain["data"]["decode"], {"windowed": True, "inputs": 2, "sources": [SOURCE_ID]})
            self.assertEqual((plain["data"]["video"]["width"], plain["data"]["video"]["height"]), PREVIEW_SIZE)
            self.assertEqual(plain["data"]["profile"], profile)
            self.assertEqual(tmp_entries(workdir), [])

            # --- Proxy explícito: a fonte 1080p é elegível e vira 1280x720 em `proxy/`.
            built = assert_json_success(
                self, run_cli("proxy", "build", "--workdir", workdir_argument, "--json"), "proxy build"
            )
            [proxy_item] = built["data"]["proxies"]
            self.assertEqual(proxy_item["source_id"], SOURCE_ID)
            self.assertEqual(proxy_item["status"], "created")
            self.assertEqual((proxy_item["width"], proxy_item["height"]), PROXY_SIZE)
            proxy_video = workdir / "proxy" / "main.mp4"
            self.assertTrue(proxy_video.is_file())
            self.assertTrue((workdir / "proxy" / "main.json").is_file())
            self.assertEqual(tmp_entries(workdir), [])

            # --- Preview com proxy: mesma saída 404x720, lida do proxy, sem proxy obsoleto.
            proxied = assert_json_success(
                self, run_cli("render", "preview", "--workdir", workdir_argument, "--json"), "render preview"
            )
            self.assertEqual(proxied["data"]["proxy"], {"mode": "auto", "sources": [SOURCE_ID], "stale": []})
            self.assertEqual(proxied["data"]["decode"], {"windowed": True, "inputs": 2, "sources": [SOURCE_ID]})
            self.assertEqual((proxied["data"]["video"]["width"], proxied["data"]["video"]["height"]), PREVIEW_SIZE)
            self.assertEqual(proxied["data"]["plan_sha256"], plan_sha256)
            self.assertEqual(
                (canvas_capabilities["preview"]["width"], canvas_capabilities["preview"]["height"]),
                PREVIEW_SIZE,
            )
            preview_metadata = inspect_rendered_artifact(workdir / "preview.mp4", require_audio=True)
            self.assertEqual((preview_metadata.video_width, preview_metadata.video_height), PREVIEW_SIZE)
            self.assertEqual((preview_metadata.video_codec, preview_metadata.audio_codec), ("h264", "aac"))
            self.assertAlmostEqual(preview_metadata.duration_seconds, TIMELINE_DURATION_S, delta=0.3)
            self.assertEqual(tmp_entries(workdir), [])

            # --- Aprovação: vincula plano, preview e o ORIGINAL (nunca o proxy) por SHA-256.
            assert_json_success(self, run_cli("approve", "--workdir", workdir_argument, "--json"), "approve")
            approval_path = workdir / "approval.json"
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            self.assertEqual(approval["plan_sha256"], plan_sha256)
            self.assertEqual(approval["preview_sha256"], sha256_file(workdir / "preview.mp4"))
            self.assertEqual(approval["sources"], [{"source_id": SOURCE_ID, "sha256": original_source_hash}])
            self.assertNotEqual(sha256_file(proxy_video), original_source_hash)
            self.assertNotIn(sha256_file(proxy_video), approval_path.read_text(encoding="utf-8"))
            approval_bytes = approval_path.read_bytes()

            # --- Mutação do `reframe` (um `x`): o hash do plano muda e a full é bloqueada.
            original_plan = plan_path.read_bytes()
            mutated = json.loads(original_plan.decode("utf-8"))
            mutated["segments"][0]["video"]["reframe"]["keyframes"][1]["x"] = 0.75
            plan_path.write_text(json.dumps(mutated), encoding="utf-8")
            mutated_validation = assert_json_success(
                self, run_cli("plan", "validate", "--workdir", workdir_argument, "--json"), "plan validate"
            )
            self.assertNotEqual(mutated_validation["data"]["plan_sha256"], plan_sha256)
            blocked = assert_json_error(
                self, run_cli("render", "full", "--workdir", workdir_argument, "--json")
            )
            self.assertEqual(blocked["error"]["code"], "APPROVAL_STALE")
            self.assertFalse((workdir / "full.mp4").exists())
            self.assertEqual(approval_path.read_bytes(), approval_bytes)
            self.assertEqual(tmp_entries(workdir), [])
            plan_path.write_bytes(original_plan)

            # --- Full independente do proxy: com o proxy corrompido a full ainda sai dos originais,
            # pois só a preview lê `proxy/` e a aprovação continua vigente.
            proxy_video.write_bytes(b"conteudo que nao e um video")
            full = assert_json_success(
                self, run_cli("render", "full", "--workdir", workdir_argument, "--json"), "render full"
            )
            self.assertEqual(full["data"]["plan_sha256"], plan_sha256)
            self.assertEqual(full["data"]["approval"]["preview_sha256"], approval["preview_sha256"])
            self.assertEqual(full["data"]["decode"], {"windowed": True, "inputs": 2, "sources": [SOURCE_ID]})
            self.assertNotIn("proxy", full["data"])
            self.assertEqual((full["data"]["video"]["width"], full["data"]["video"]["height"]), FULL_SIZE)
            self.assertEqual(full["data"]["profile"], profile)
            full_metadata = inspect_rendered_artifact(workdir / "full.mp4", require_audio=True)
            self.assertEqual((full_metadata.video_width, full_metadata.video_height), FULL_SIZE)
            self.assertEqual((full_metadata.video_codec, full_metadata.audio_codec), ("h264", "aac"))
            self.assertAlmostEqual(full_metadata.duration_seconds, TIMELINE_DURATION_S, delta=0.3)
            self.assertEqual(approval_path.read_bytes(), approval_bytes)
            self.assertEqual(tmp_entries(workdir), [])

            # A jornada só publica preview.mp4 e full.mp4 e nunca toca na fonte original.
            self.assertEqual(
                sorted(path.name for path in workdir.iterdir() if path.suffix == ".mp4"),
                ["full.mp4", "preview.mp4"],
            )
            self.assertEqual(sha256_file(source), original_source_hash)


if __name__ == "__main__":
    unittest.main()
