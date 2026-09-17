"""Testes do schema estrito, da timeline linear e do hash canônico do plano."""

from __future__ import annotations

import json
from contextlib import contextmanager
import os
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
from editor.plan import validation as plan_validation
from editor.plan.canonical import canonical_sha256
from editor.plan.validation import validate_plan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = PROJECT_ROOT / "tests" / "fixtures" / "workflow"
WORKFLOW_PLAN = WORKFLOW_DIR / "plan.json"
WORKFLOW_SOURCES = WORKFLOW_DIR / "sources"
IMAGE_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "phase-1" / "images"


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


class FakeRegistryKey:
    """Representa uma chave de registro somente para provar a leitura das entradas de fonte."""

    def __init__(self, values: list[tuple[str, str, int]]) -> None:
        self.values = values

    def __enter__(self) -> "FakeRegistryKey":
        """Permite que o resolvedor use o mesmo ciclo de vida da API winreg real."""

        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        """Fecha a chave simulada sem suprimir erros da operação sob teste."""

        return False


class FakeWinreg:
    """Fornece as operações mínimas das colmeias HKLM e HKCU usadas pelo resolvedor."""

    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, values_by_hive: dict[str, list[tuple[str, str, int]]]) -> None:
        self.values_by_hive = values_by_hive

    def OpenKey(self, hive: str, path: str) -> FakeRegistryKey:
        """Abre somente a chave de fontes esperada pelo contrato do Windows."""

        if path != plan_validation.WINDOWS_FONT_REGISTRY_PATH:
            raise OSError("chave inesperada")
        return FakeRegistryKey(self.values_by_hive.get(hive, []))

    def QueryInfoKey(self, key: FakeRegistryKey) -> tuple[int, int, int]:
        """Retorna a quantidade de valores para reproduzir a enumeração do registro."""

        return 0, len(key.values), 0

    def EnumValue(self, key: FakeRegistryKey, index: int) -> tuple[str, str, int]:
        """Entrega o valor registrado na posição solicitada pela implementação."""

        return key.values[index]


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
        self.assertEqual(
            data["profile"],
            {
                "aspect_ratio": "16:9",
                "max_full_width": 1920,
                "effective_full_width": 320,
            },
        )
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

    def test_windows_font_registry_resolves_hklm_and_hkcu_files(self) -> None:
        """Confirma que cada colmeia resolve o arquivo concreto dentro do diretório permitido."""

        with workspace_tempdir() as workdir:
            system_fonts = workdir / "Windows" / "Fonts"
            user_fonts = workdir / "LocalAppData" / "Microsoft" / "Windows" / "Fonts"
            system_fonts.mkdir(parents=True)
            user_fonts.mkdir(parents=True)
            system_file = system_fonts / "arialbd.ttf"
            user_file = user_fonts / "arial-user.ttf"
            system_file.write_bytes(b"system-font")
            user_file.write_bytes(b"user-font")

            cases = (
                ("HKLM", system_file.name, system_file),
                ("HKCU", user_file.name, user_file),
            )
            with patch.dict(
                os.environ,
                {
                    "WINDIR": str(workdir / "Windows"),
                    "SystemRoot": str(workdir / "Windows"),
                    "LOCALAPPDATA": str(workdir / "LocalAppData"),
                },
            ), patch.object(plan_validation.sys, "platform", "win32"):
                for hive, registered_file, expected_path in cases:
                    with self.subTest(hive=hive):
                        registry = FakeWinreg({
                            hive: [("Arial Bold (TrueType)", registered_file, 1)],
                        })
                        with patch.object(plan_validation, "winreg", registry):
                            self.assertEqual(
                                plan_validation._resolve_font_file("Arial Bold"),
                                expected_path,
                            )

    def test_windows_font_resolver_rejects_missing_extension_and_reparse(self) -> None:
        """Confirma rejeição pública para arquivo ausente, extensão não suportada e reparse point."""

        with workspace_tempdir() as workdir:
            shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")
            system_root = workdir / "Windows"
            system_fonts = system_root / "Fonts"
            user_root = workdir / "LocalAppData"
            user_fonts = user_root / "Microsoft" / "Windows" / "Fonts"
            system_fonts.mkdir(parents=True)
            user_fonts.mkdir(parents=True)
            document = load_workflow_plan()

            cases = (
                ("Missing Font", "missing.ttf", None),
                ("Rejected Font", "rejected.txt", None),
                ("Reparse Font", "reparse.ttf", "reparse"),
            )
            for name, filename, failure_kind in cases:
                with self.subTest(name=name):
                    document["layers"][0]["font"] = name
                    write_plan(workdir, document)
                    font_path = system_fonts / filename
                    if failure_kind == "reparse":
                        font_path.write_bytes(b"reparse-font")
                    registry = FakeWinreg({
                        "HKLM": [(f"{name} (TrueType)", filename, 1)],
                    })
                    patches = [
                        patch.object(plan_validation, "winreg", registry),
                        patch.object(plan_validation.sys, "platform", "win32"),
                        patch.dict(
                            os.environ,
                            {
                                "WINDIR": str(system_root),
                                "SystemRoot": str(system_root),
                                "LOCALAPPDATA": str(user_root),
                            },
                        ),
                        patch("editor.plan.validation.media.inspect_source"),
                    ]
                    if failure_kind == "reparse":
                        patches.append(
                            patch.object(
                                plan_validation,
                                "resolve_source_path",
                                side_effect=CliError(
                                    code="SOURCE_INVALID",
                                    message="reparse point",
                                    exit_code=3,
                                ),
                            )
                        )
                    with patches[0], patches[1], patches[2], patches[3] as inspect:
                        if failure_kind == "reparse":
                            with patches[4]:
                                with self.assertRaises(CliError) as raised:
                                    validate_plan(workdir)
                        else:
                            with self.assertRaises(CliError) as raised:
                                validate_plan(workdir)
                        self.assertEqual(raised.exception.code, "PLAN_INVALID")
                        inspect.assert_not_called()

    def test_resolved_font_path_stays_internal_and_out_of_public_plan_contract(self) -> None:
        """Confirma que o caminho concreto serve ao renderer, mas não ao hash ou envelope público."""

        with workspace_tempdir() as workdir:
            shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")
            system_root = workdir / "Windows"
            system_fonts = system_root / "Fonts"
            user_root = workdir / "LocalAppData"
            user_fonts = user_root / "Microsoft" / "Windows" / "Fonts"
            system_fonts.mkdir(parents=True)
            user_fonts.mkdir(parents=True)
            font_path = system_fonts / "arialbd.ttf"
            font_path.write_bytes(b"arial-bold-font")
            document = load_workflow_plan()
            document["layers"][0]["font"] = "Arial Bold"
            write_plan(workdir, document)
            registry = FakeWinreg({
                "HKLM": [("Arial Bold (TrueType)", font_path.name, 1)],
            })
            with patch.object(plan_validation, "winreg", registry), patch.object(
                plan_validation.sys, "platform", "win32"
            ), patch.dict(
                os.environ,
                {
                    "WINDIR": str(system_root),
                    "SystemRoot": str(system_root),
                    "LOCALAPPDATA": str(user_root),
                },
            ):
                _, internal = plan_validation.load_validated_plan(workdir)
                public = validate_plan(workdir)

            self.assertEqual(internal["_resolved_font_files"]["Arial Bold"], str(font_path))
            self.assertNotIn("_resolved_font_files", public)
            self.assertNotIn(str(font_path), internal["canonical_json"])
            self.assertNotIn(str(font_path), json.dumps(public))

    def test_max_full_width_accepts_default_and_rejects_invalid_values_before_probe(self) -> None:
        """Prova compatibilidade v2 e a allowlist numérica do teto final na borda do plano."""

        document = load_workflow_plan()
        with workspace_tempdir() as workdir:
            shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")
            write_plan(workdir, document)
            with patch(
                "editor.plan.validation.media.inspect_source", side_effect=fake_inspect_source
            ):
                self.assertEqual(validate_plan(workdir)["profile"]["max_full_width"], 1920)

            document["profile"]["max_full_width"] = 160
            write_plan(workdir, document)
            with patch(
                "editor.plan.validation.media.inspect_source", side_effect=fake_inspect_source
            ):
                self.assertEqual(
                    validate_plan(workdir)["profile"],
                    {
                        "aspect_ratio": "16:9",
                        "max_full_width": 160,
                        "effective_full_width": 160,
                    },
                )

            for invalid in (True, 32.0, 0, 16, 48, 3_872):
                with self.subTest(value=invalid):
                    document["profile"]["max_full_width"] = invalid
                    write_plan(workdir, document)
                    with patch("editor.plan.validation.media.inspect_source") as inspect:
                        with self.assertRaises(CliError) as raised:
                            validate_plan(workdir)
                    self.assertEqual(raised.exception.code, "PLAN_INVALID")
                    inspect.assert_not_called()

    def test_image_sources_require_static_matching_raster_and_correct_layer_kind(self) -> None:
        """Exercita formatos reais e impede extensão falsa, animação e referência audiovisual."""

        with workspace_tempdir() as workdir:
            shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")
            document = load_workflow_plan()
            document["layers"] = [{
                "type": "image", "start_s": 0.25, "end_s": 1.25,
                "x": 0.1, "y": 0.1, "width": 0.3, "height": 0.4,
                "source_id": "still", "fit": "contain",
            }]
            for name in ("alpha.png", "static.jpg", "static.webp"):
                shutil.copy2(IMAGE_FIXTURES / name, workdir / "sources" / name)
                document["sources"].append({"id": "still", "kind": "image", "path": f"sources/{name}"})
                write_plan(workdir, document)
                result = validate_plan(workdir)
                self.assertEqual(result["sources"][-1]["duration_seconds"], None)
                self.assertEqual(result["sources"][-1]["audio_codec"], None)
                document["sources"].pop()
            shutil.copy2(IMAGE_FIXTURES / "static.jpg", workdir / "sources" / "static.jpeg")
            document["sources"].append({"id": "still", "kind": "image", "path": "sources/static.jpeg"})
            write_plan(workdir, document)
            self.assertEqual(validate_plan(workdir)["sources"][-1]["video_codec"], "mjpeg")
            document["sources"].pop()

            document["layers"][0]["source_id"] = "main"
            write_plan(workdir, document)
            with self.assertRaisesRegex(CliError, "imagem"):
                validate_plan(workdir)

            document["layers"][0]["source_id"] = "still"
            invalid = workdir / "sources" / "fake.png"
            shutil.copy2(IMAGE_FIXTURES / "static.jpg", invalid)
            document["sources"].append({"id": "still", "kind": "image", "path": "sources/fake.png"})
            write_plan(workdir, document)
            with self.assertRaises(CliError):
                validate_plan(workdir)
            document["sources"].pop()

            for name in ("animated.png", "animated.webp", "rejected.gif", "rejected.bmp"):
                shutil.copy2(IMAGE_FIXTURES / name, workdir / "sources" / name)
                document["sources"].append({"id": "still", "kind": "image", "path": f"sources/{name}"})
                write_plan(workdir, document)
                with self.assertRaises(CliError, msg=name):
                    validate_plan(workdir)
                document["sources"].pop()

            document["sources"].append({"id": "still", "kind": "image", "path": "sources/alpha.png"})
            write_plan(workdir, document)
            with patch("editor.media.MAX_IMAGE_SIZE_BYTES", 1):
                with self.assertRaises(CliError):
                    validate_plan(workdir)
            with patch("editor.media.MAX_IMAGE_DIMENSION", 10):
                with self.assertRaises(CliError):
                    validate_plan(workdir)

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

    def test_schema_version_requires_json_integer_before_media_probe(self) -> None:
        """Confirma que formas numéricas equivalentes a 2 não substituem o inteiro JSON exigido."""

        for raw_version in ("2.0", "2e0"):
            with self.subTest(raw_version=raw_version):
                document = load_workflow_plan()
                serialized = json.dumps(document).replace(
                    '"schema_version": 2',
                    f'"schema_version": {raw_version}',
                    1,
                )
                with workspace_tempdir() as workdir:
                    (workdir / "plan.json").write_text(serialized, encoding="utf-8")
                    with patch("editor.plan.validation.media.inspect_source") as inspect:
                        with self.assertRaises(CliError) as raised:
                            validate_plan(workdir)

                self.assertEqual(raised.exception.code, "PLAN_INVALID")
                inspect.assert_not_called()

    def test_audio_source_kind_rejects_mp4_via_real_cli(self) -> None:
        """Confirma no processo real que áudio declarado não aceita contêiner audiovisual MP4."""

        document = load_workflow_plan()
        document["sources"][0]["kind"] = "audio"
        with workspace_tempdir() as workdir:
            shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")
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

    def test_video_layer_requires_matching_source_duration_and_fit(self) -> None:
        """Rejeita referências e trechos inválidos antes de construir qualquer render."""

        document = load_workflow_plan()
        layer = {
            "type": "video", "start_s": 0.5, "end_s": 1.0,
            "x": 0.1, "y": 0.2, "width": 0.4, "height": 0.5,
            "source_id": "main", "in_s": 0.25, "out_s": 0.75, "fit": "contain",
        }
        document["layers"] = [layer]
        with workspace_tempdir() as workdir:
            shutil.copytree(WORKFLOW_SOURCES, workdir / "sources")
            write_plan(workdir, document)
            with patch("editor.plan.validation.media.inspect_source", side_effect=fake_inspect_source):
                self.assertEqual(validate_plan(workdir)["layer_count"], 1)
                for change in (
                    {"source_id": "music-01"}, {"in_s": -0.1}, {"out_s": 2.5},
                    {"out_s": 0.8}, {"fit": "preserve"}, {"x": 0.8},
                    {"start_s": 1.5, "end_s": 2.5}, {"loop": True},
                ):
                    with self.subTest(change=change):
                        document["layers"] = [{**layer, **change}]
                        write_plan(workdir, document)
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
