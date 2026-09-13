"""Testes do schema estrito, da timeline linear e do hash canônico do plano."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator
from unittest.mock import patch
from uuid import uuid4

from editor import dependencies
from editor.contracts import CliError
from editor.media import MediaMetadata
from editor.plan.canonical import canonical_sha256
from editor.plan.validation import validate_plan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / "tests" / "fixtures" / "workflow"
WORKFLOW_PLAN = WORKFLOW_DIR / "plan.json"
WORKFLOW_SOURCES = WORKFLOW_DIR / "sources"


def run_module(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Executa o ponto de entrada real para comprovar o envelope público da T7."""

    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    """Cria um trabalho isolado dentro do workspace gerenciado para os testes de erro."""

    temporary_path = PROJECT_ROOT / f"tmp-plan-validation-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


def load_workflow_plan() -> dict[str, object]:
    """Lê o fixture canônico para que os testes de mutação partam de um plano válido."""

    return json.loads(WORKFLOW_PLAN.read_text(encoding="utf-8"))


def write_plan(workdir: Path, document: dict[str, object]) -> None:
    """Escreve somente o plan.json do trabalho de teste, preservando fontes existentes."""

    (workdir / "plan.json").write_text(json.dumps(document), encoding="utf-8")


def fake_inspect_source(
    path: Path,
    *,
    require_audio: bool,
    require_video: bool,
) -> MediaMetadata:
    """Fornece metadados determinísticos para testes que devem parar antes do FFprobe."""

    is_audio_only = path.suffix.lower() == ".mp3"
    return MediaMetadata(
        path=path,
        container="mp3" if is_audio_only else "mp4",
        audio_codec="mp3" if is_audio_only else "aac",
        video_codec=None if is_audio_only else "h264",
        duration_seconds=2.0,
        size_bytes=path.stat().st_size,
        sample_rate=48_000,
        channels=1,
        video_width=None if is_audio_only else 320,
        video_height=None if is_audio_only else 180,
        video_frame_rate=None if is_audio_only else 25.0,
    )


def fake_long_video_source(path: Path, *, require_audio: bool, require_video: bool) -> MediaMetadata:
    """Fornece uma fonte longa determinística para testar o limite da timeline."""

    return MediaMetadata(
        path=path,
        container="mp4",
        audio_codec="aac",
        video_codec="h264",
        duration_seconds=3_600.0,
        size_bytes=path.stat().st_size,
        sample_rate=48_000,
        channels=1,
        video_width=320,
        video_height=180,
        video_frame_rate=25.0,
    )


class PlanValidationTests(unittest.TestCase):
    """Garante que somente planos explícitos e temporalmente consistentes sejam aceitos."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFprobe para o cenário de aceitação que valida fontes reais do fixture."""

        if dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffprobe não está disponível localmente")
        if not WORKFLOW_PLAN.is_file() or not WORKFLOW_SOURCES.is_dir():
            raise unittest.SkipTest("fixture do plano não está disponível")

    def test_real_cli_validates_linear_travel_workflow(self) -> None:
        """Confirma fontes reais, timeline concatenada, b-roll, mixagem e camada de texto."""

        result = run_module(
            "plan",
            "validate",
            "--workdir",
            str(WORKFLOW_DIR.resolve()),
            "--json",
        )
        payload = json.loads(result.stdout)
        data = payload["data"]

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["command"], "plan validate")
        self.assertEqual(data["source_count"], 3)
        self.assertEqual(data["segment_count"], 3)
        self.assertEqual(data["cut_count"], 0)
        self.assertEqual(data["layer_count"], 1)
        self.assertEqual(data["timeline"]["duration_seconds"], 2.0)
        self.assertEqual(
            [segment["video_source_id"] for segment in data["timeline"]["segments"]],
            ["main", "broll-01", "main"],
        )
        self.assertEqual(data["timeline"]["segments"][1]["audio_source_ids"], ["main"])
        self.assertEqual(
            data["timeline"]["segments"][2]["audio_source_ids"],
            ["main", "music-01"],
        )
        self.assertEqual(len(data["plan_sha256"]), 64)
        self.assertEqual(json.loads(data["canonical_json"])["schema_version"], 2)

    def test_j_and_l_define_global_audio_windows(self) -> None:
        """Confirma que J antecipa e L prolonga a janela do grupo sem mudar a timeline visual."""

        document = load_workflow_plan()
        document["segments"][1]["audio"]["tracks"][0]["out_s"] = 1.65
        document["cuts"] = [
            {"after_segment": 1, "type": "j", "duration_s": 0.2},
            {"after_segment": 2, "type": "l", "duration_s": 0.2},
        ]
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")
            with patch(
                "editor.plan.validation.media.inspect_source",
                side_effect=fake_inspect_source,
            ):
                result = validate_plan(workdir)

        timeline = result["timeline"]["segments"]
        self.assertEqual(result["cut_count"], 2)
        self.assertEqual(result["timeline"]["duration_seconds"], 2.0)
        self.assertEqual(
            (timeline[1]["audio_start_s"], timeline[1]["audio_end_s"]),
            (0.55, 1.45),
        )
        self.assertEqual(timeline[1]["audio_duration_s"], 0.9)

    def test_invalid_j_l_definitions_are_rejected(self) -> None:
        """Confirma fronteiras, sobreposição, mute e cobertura inválidos como PLAN_INVALID."""

        cases = {
            "unknown_type": [{"after_segment": 1, "type": "x", "duration_s": 0.2}],
            "zero_duration": [{"after_segment": 1, "type": "j", "duration_s": 0}],
            "missing_boundary": [{"after_segment": 3, "type": "j", "duration_s": 0.2}],
            "duplicate_boundary": [
                {"after_segment": 1, "type": "j", "duration_s": 0.2},
                {"after_segment": 1, "type": "l", "duration_s": 0.2},
            ],
            "adjacent_duration": [{"after_segment": 1, "type": "j", "duration_s": 0.5}],
            "mute_j": [{"after_segment": 1, "type": "j", "duration_s": 0.2}],
            "mute_l": [{"after_segment": 1, "type": "l", "duration_s": 0.2}],
            "insufficient_coverage": [{"after_segment": 1, "type": "j", "duration_s": 0.2}],
        }
        for name, cuts in cases.items():
            with self.subTest(name=name):
                document = load_workflow_plan()
                document["cuts"] = cuts
                if name == "mute_j":
                    document["segments"][1]["audio"] = {
                        "mode": "mute",
                        "tracks": [],
                    }
                elif name == "mute_l":
                    document["segments"][0]["audio"] = {
                        "mode": "mute",
                        "tracks": [],
                    }

                with workspace_tempdir() as workdir:
                    write_plan(workdir, document)
                    shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")
                    with patch(
                        "editor.plan.validation.media.inspect_source",
                        side_effect=fake_inspect_source,
                    ) as inspect:
                        with self.assertRaises(CliError) as raised:
                            validate_plan(workdir)

                self.assertEqual(raised.exception.code, "PLAN_INVALID")
                if name in {"unknown_type", "zero_duration"}:
                    inspect.assert_not_called()

    def test_canonical_hash_ignores_json_formatting(self) -> None:
        """Confirma que espaços e ordem textual não alteram o hash do plano semântico."""

        document = load_workflow_plan()
        first = canonical_sha256(document)
        formatted = json.dumps(document, ensure_ascii=False, indent=4, sort_keys=True)
        reformatted = json.loads(formatted)

        self.assertEqual(canonical_sha256(reformatted), first)
        self.assertNotIn(" ", json.dumps(reformatted, sort_keys=True, separators=(",", ":")))

    def test_unknown_root_field_is_rejected_before_media_probe(self) -> None:
        """Confirma rejeição precoce de campo desconhecido sem consultar dependências externas."""

        document = load_workflow_plan()
        document["unexpected"] = True
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch("editor.plan.validation.media.inspect_source") as inspect:
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            inspect.assert_not_called()

    def test_v1_plan_is_rejected_before_media_probe(self) -> None:
        """Confirma que o contrato v1 não alcança a inspeção de fontes após a migração."""

        document = load_workflow_plan()
        document["schema_version"] = 1
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch("editor.plan.validation.media.inspect_source") as inspect:
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            inspect.assert_not_called()

    def test_legacy_overlays_are_rejected_before_media_probe(self) -> None:
        """Confirma que `overlays` não permanece como uma segunda interpretação do plano."""

        document = load_workflow_plan()
        document["overlays"] = []
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch("editor.plan.validation.media.inspect_source") as inspect:
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            inspect.assert_not_called()

    def test_v2_lists_are_required_and_structural_failures_precede_media_probe(self) -> None:
        """Confirma que `cuts` e `layers` existem como listas antes de consultar FFprobe."""

        for field in ("cuts", "layers"):
            with self.subTest(field=field):
                document = load_workflow_plan()
                document.pop(field)
                with workspace_tempdir() as workdir:
                    write_plan(workdir, document)
                    with patch("editor.plan.validation.media.inspect_source") as inspect:
                        with self.assertRaises(CliError) as raised:
                            validate_plan(workdir)

                    self.assertEqual(raised.exception.code, "PLAN_INVALID")
                    inspect.assert_not_called()

    def test_named_text_position_is_rejected_before_media_probe(self) -> None:
        """Confirma que coordenadas nomeadas não atravessam a fronteira estrutural v2."""

        document = load_workflow_plan()
        document["layers"][0]["position"] = "bottom-right"
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch("editor.plan.validation.media.inspect_source") as inspect:
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            inspect.assert_not_called()

    def test_text_box_and_font_fail_before_media_probe(self) -> None:
        """Bloqueia caixa fora do canvas, campos legados e fonte ausente na fronteira do plano."""

        invalid_changes = (
            {"x": 0.9, "width": 0.2},
            {"height": 0},
            {"font": "FonteInexistenteParaTeste123"},
            {"font": "Arial\x00"},
            {"size_px": 513},
            {"wrap": "yes"},
            {"align": "top"},
            {"color": "red"},
            {"margin": 5},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                document = load_workflow_plan()
                document["layers"][0].update(changes)
                with workspace_tempdir() as workdir:
                    write_plan(workdir, document)
                    with patch("editor.plan.validation.media.inspect_source") as inspect:
                        with self.assertRaises(CliError) as raised:
                            validate_plan(workdir)
                    self.assertEqual(raised.exception.code, "PLAN_INVALID")
                    inspect.assert_not_called()

    def test_non_finite_layer_number_is_rejected_before_media_probe(self) -> None:
        """Confirma que NaN é rejeitado pelo parser antes de qualquer sondagem de mídia."""

        document = load_workflow_plan()
        document["layers"][0]["x"] = float("nan")
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch("editor.plan.validation.media.inspect_source") as inspect:
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            inspect.assert_not_called()

    def test_motion_is_optional_and_normalized_in_timeline(self) -> None:
        """Confirma movimento opcional, estados completos e valores normalizados no relatório."""

        document = load_workflow_plan()
        document["segments"][0]["video"]["motion"] = {
            "from": {"x": 0.2, "y": 0.35, "zoom": 0.75},
            "to": {"x": 0.8, "y": 0.65, "zoom": 1.75},
        }
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")
            with patch(
                "editor.plan.validation.media.inspect_source",
                side_effect=fake_inspect_source,
            ):
                result = validate_plan(workdir)

        self.assertEqual(
            result["timeline"]["segments"][0]["motion"],
            {
                "from": {"x": 0.2, "y": 0.35, "zoom": 0.75},
                "to": {"x": 0.8, "y": 0.65, "zoom": 1.75},
            },
        )
        self.assertNotIn("motion", result["timeline"]["segments"][1])

    def test_invalid_motion_is_rejected_before_media_probe(self) -> None:
        """Confirma que estrutura, finitude e limites do movimento falham antes do FFprobe."""

        valid_from = {"x": 0.5, "y": 0.5, "zoom": 1.0}
        valid_to = {"x": 0.5, "y": 0.5, "zoom": 1.0}
        invalid_motions = {
            "unknown_field": {
                "from": valid_from,
                "to": valid_to,
                "easing": "linear",
            },
            "missing_state": {"from": valid_from},
            "focus_outside_bounds": {
                "from": {"x": 1.1, "y": 0.5, "zoom": 1.0},
                "to": valid_to,
            },
            "zoom_outside_bounds": {
                "from": valid_from,
                "to": {"x": 0.5, "y": 0.5, "zoom": 4.1},
            },
            "non_finite_zoom": {
                "from": valid_from,
                "to": {"x": 0.5, "y": 0.5, "zoom": float("nan")},
            },
        }
        for name, motion in invalid_motions.items():
            with self.subTest(name=name):
                document = load_workflow_plan()
                document["segments"][0]["video"]["motion"] = motion
                with workspace_tempdir() as workdir:
                    write_plan(workdir, document)
                    with patch("editor.plan.validation.media.inspect_source") as inspect:
                        with self.assertRaises(CliError) as raised:
                            validate_plan(workdir)

                self.assertEqual(raised.exception.code, "PLAN_INVALID")
                inspect.assert_not_called()

    def test_relative_source_cannot_escape_workdir(self) -> None:
        """Confirma que um caminho relativo com traversal falha antes da inspeção de mídia."""

        document = load_workflow_plan()
        document["sources"][0]["path"] = "../outside.mp4"
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch("editor.plan.validation.media.inspect_source") as inspect:
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            inspect.assert_not_called()

    def test_huge_numeric_value_returns_plan_invalid_envelope(self) -> None:
        """Confirma que um inteiro JSON não conversível não cai em erro interno."""

        document = load_workflow_plan()
        document["segments"][0]["video"]["in_s"] = 10**1000
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            result = run_module(
                "plan",
                "validate",
                "--workdir",
                str(workdir),
                "--json",
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["error"]["code"], "PLAN_INVALID")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        """Confirma que objetos JSON ambíguos falham antes de qualquer inspeção de mídia."""

        with workspace_tempdir() as workdir:
            (workdir / "plan.json").write_text(
                '{"schema_version":2,"schema_version":2}',
                encoding="utf-8",
            )
            with patch("editor.plan.validation.media.inspect_source") as inspect:
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")
            inspect.assert_not_called()

    def test_segment_count_limit_is_rejected(self) -> None:
        """Confirma que uma lista compacta de segmentos não amplia o custo sem limite."""

        document = load_workflow_plan()
        document["segments"] = document["segments"] * 334
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            result = run_module(
                "plan",
                "validate",
                "--workdir",
                str(workdir),
                "--json",
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["error"]["code"], "PLAN_INVALID")

    def test_timeline_duration_limit_is_rejected(self) -> None:
        """Confirma que a timeline total não ultrapassa o limite operacional do MVP."""

        document = {
            "schema_version": 2,
            "profile": {"aspect_ratio": "16:9"},
            "sources": [
                {
                    "id": "main",
                    "kind": "video",
                    "path": str(WORKFLOW_SOURCES / "main.mp4"),
                }
            ],
            "segments": [
                {
                    "video": {
                        "source_id": "main",
                        "in_s": 0,
                        "out_s": 3_600,
                        "fit": "preserve",
                    },
                    "audio": {"mode": "mute", "tracks": []},
                },
                {
                    "video": {
                        "source_id": "main",
                        "in_s": 0,
                        "out_s": 1,
                        "fit": "preserve",
                    },
                    "audio": {"mode": "mute", "tracks": []},
                },
            ],
            "cuts": [],
            "layers": [],
        }
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch(
                "editor.plan.validation.media.inspect_source",
                side_effect=fake_long_video_source,
            ):
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_broll_cannot_keep_audio_from_a_different_source(self) -> None:
        """Confirma a regra que exige áudio explícito de A quando B ocupa a imagem."""

        document = load_workflow_plan()
        segment = document["segments"][1]
        segment["audio"]["mode"] = "keep"
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch(
                "editor.plan.validation.media.inspect_source",
                side_effect=fake_inspect_source,
            ):
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

            self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_invalid_layer_interval_is_rejected(self) -> None:
        """Confirma que uma camada não pode ultrapassar a duração calculada da timeline."""

        document = load_workflow_plan()
        document["layers"][0]["end_s"] = 2.01
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            with patch(
                "editor.plan.validation.media.inspect_source",
                side_effect=fake_inspect_source,
            ):
                with self.assertRaises(CliError) as raised:
                    validate_plan(workdir)

        self.assertEqual(raised.exception.code, "PLAN_INVALID")

    def test_nul_in_layer_returns_plan_invalid_envelope(self) -> None:
        """Confirma que controle NUL falha na validação antes de alcançar o filtro FFmpeg."""
        document = load_workflow_plan()
        document["layers"][0]["text"] = "texto\x00inválido"
        with workspace_tempdir() as workdir:
            write_plan(workdir, document)
            result = run_module(
                "plan",
                "validate",
                "--workdir",
                str(workdir),
                "--json",
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["error"]["code"], "PLAN_INVALID")

    def test_cli_returns_plan_invalid_envelope_for_malformed_json(self) -> None:
        """Confirma que JSON malformado vira erro estruturado sem traceback no stdout."""

        with workspace_tempdir() as workdir:
            (workdir / "plan.json").write_text("{", encoding="utf-8")
            result = run_module(
                "plan",
                "validate",
                "--workdir",
                str(workdir),
                "--json",
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 5)
        self.assertEqual(result.stderr, "")
        self.assertEqual(payload["error"]["code"], "PLAN_INVALID")


if __name__ == "__main__":
    unittest.main()
