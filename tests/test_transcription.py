"""Testes do contrato de transcrição local e de seus guardrails."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from editor import dependencies
from editor.analysis.transcription import analyze_transcription
from editor.contracts import CliError
from editor.whisper_backend import WhisperConfig, _load_whisper_model


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEDIA_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "media" / "main.mp4"


def run_module(*arguments: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Executa a CLI em processo separado para comprovar o envelope público."""

    child_environment = os.environ.copy()
    if environment:
        child_environment.update(environment)
    return subprocess.run(
        [sys.executable, "-m", "editor", *arguments],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_environment,
        check=False,
    )


@contextmanager
def workspace_tempdir() -> Iterator[Path]:
    """Cria e remove um trabalho isolado dentro do workspace gerenciado."""

    temporary_path = PROJECT_ROOT / f"tmp-transcription-{uuid4().hex}"
    temporary_path.mkdir()
    try:
        yield temporary_path
    finally:
        shutil.rmtree(temporary_path, ignore_errors=True)


def create_model_root(workdir: Path) -> Path:
    """Cria um diretório CTranslate2 mínimo para testar seleção sem pesos reais."""

    workdir.mkdir(parents=True)
    model = workdir / "base"
    model.mkdir()
    for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (model / filename).write_bytes(b"fixture")
    return workdir


def fake_segments(*, with_words: bool = True) -> tuple[list[SimpleNamespace], SimpleNamespace]:
    """Monta uma resposta determinística compatível com os objetos do faster-whisper."""

    words = (
        [
            SimpleNamespace(word=" Olá", start=0.1, end=0.4, probability=0.98),
            SimpleNamespace(word=" mundo", start=0.4, end=0.8, probability=0.97),
        ]
        if with_words
        else None
    )
    segments = [SimpleNamespace(id=0, start=0.1, end=0.8, text=" Olá mundo", words=words)]
    info = SimpleNamespace(language="pt", language_probability=0.99)
    return segments, info


@contextmanager
def configured_backend(model_root: Path) -> Iterator[None]:
    """Substitui somente sondagens externas para testar o fluxo com um modelo fake local."""

    with (
        patch("editor.whisper_backend.default_model_root", return_value=model_root),
        patch(
            "editor.whisper_backend.dependencies.detect_whisper_backend",
            return_value={"status": "available"},
        ),
        patch(
            "editor.whisper_backend.dependencies.detect_gpu",
            return_value={"status": "missing"},
        ),
    ):
        yield


class TranscriptionTests(unittest.TestCase):
    """Garante seleção local, timestamps obrigatórios e publicação sem estado parcial."""

    @classmethod
    def setUpClass(cls) -> None:
        """Exige FFprobe local para validar a integração com a fonte de mídia real."""

        if dependencies.resolve_executable("ffprobe") is None:
            raise unittest.SkipTest("ffprobe não está disponível localmente")
        if not MEDIA_FIXTURE.is_file():
            raise unittest.SkipTest("fixture MP4 da transcrição não está disponível")

    def test_local_transcription_publishes_canonical_artifact(self) -> None:
        """Confirma hash, idioma, texto, palavras e parâmetros locais do backend."""

        with workspace_tempdir() as workdir:
            model_root = create_model_root(workdir / "models")
            fake_model = Mock()
            fake_model.transcribe.return_value = fake_segments()
            with configured_backend(model_root), patch(
                "editor.whisper_backend._load_whisper_model",
                return_value=fake_model,
            ):
                result = analyze_transcription(
                    workdir,
                    "main",
                    MEDIA_FIXTURE,
                    model="base",
                    device="cpu",
                )

            artifact_path = Path(result["artifact_path"])
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            transcription = artifact["transcription"]
            self.assertEqual(artifact["schema_version"], 1)
            self.assertEqual(artifact["source"]["id"], "main")
            self.assertEqual(len(artifact["source"]["sha256"]), 64)
            self.assertEqual(transcription["language"], "pt")
            self.assertEqual(transcription["text"], " Olá mundo")
            self.assertEqual(transcription["segments"][0]["words"][1]["end_s"], 0.8)
            call = fake_model.transcribe.call_args
            self.assertIsNone(call.kwargs["language"])
            self.assertTrue(call.kwargs["word_timestamps"])

    def test_cpu_baseline_and_local_only_are_used(self) -> None:
        """Confirma que o modelo explícito usa a configuração CPU int8 do MVP."""

        with workspace_tempdir() as workdir:
            model_root = create_model_root(workdir / "models")
            fake_model = Mock()
            fake_model.transcribe.return_value = fake_segments()
            with configured_backend(model_root), patch(
                "editor.whisper_backend._load_whisper_model",
                return_value=fake_model,
            ) as load_model:
                analyze_transcription(
                    workdir,
                    "main",
                    MEDIA_FIXTURE,
                    model="base",
                    device="cpu",
                )

            config = load_model.call_args.args[0]
            self.assertEqual(config.model_name, "base")
            self.assertEqual(config.device, "cpu")
            self.assertEqual(config.compute_type, "int8")

    def test_backend_loader_forces_local_files_only(self) -> None:
        """Confirma diretamente que o adaptador não pode solicitar download de pesos."""

        constructor = Mock(return_value=object())
        fake_module = SimpleNamespace(WhisperModel=constructor)
        config = WhisperConfig(
            model_name="base",
            model_path=Path("models/base"),
            device="cpu",
            compute_type="int8",
        )
        with patch.dict(sys.modules, {"faster_whisper": fake_module}):
            _load_whisper_model(config)

        constructor.assert_called_once()
        call = constructor.call_args
        self.assertEqual(Path(call.args[0]), Path("models/base"))
        self.assertEqual(call.kwargs["device"], "cpu")
        self.assertEqual(call.kwargs["compute_type"], "int8")
        self.assertTrue(call.kwargs["local_files_only"])

    def test_missing_word_timestamps_preserve_existing_artifact(self) -> None:
        """Confirma que resposta sem timestamps falha sem substituir análise vigente."""

        with workspace_tempdir() as workdir:
            model_root = create_model_root(workdir / "models")
            destination = workdir / "analysis" / "transcription" / "main.json"
            destination.parent.mkdir(parents=True)
            previous = b'{"previous":true}'
            destination.write_bytes(previous)
            fake_model = Mock()
            fake_model.transcribe.return_value = fake_segments(with_words=False)
            with configured_backend(model_root), patch(
                "editor.whisper_backend._load_whisper_model",
                return_value=fake_model,
            ):
                with self.assertRaises(CliError) as raised:
                    analyze_transcription(
                        workdir,
                        "main",
                        MEDIA_FIXTURE,
                        model="base",
                        device="cpu",
                    )

            self.assertEqual(raised.exception.code, "ANALYSIS_FAILED")
            self.assertEqual(destination.read_bytes(), previous)
            self.assertEqual(list((workdir / ".tmp").iterdir()), [])

    def test_cuda_override_fails_before_loading_model(self) -> None:
        """Confirma que CUDA explícita indisponível retorna código estável sem carregar pesos."""

        with workspace_tempdir() as workdir:
            model_root = create_model_root(workdir / "models")
            with configured_backend(model_root), patch(
                "editor.whisper_backend._load_whisper_model"
            ) as load_model:
                with self.assertRaises(CliError) as raised:
                    analyze_transcription(
                        workdir,
                        "main",
                        MEDIA_FIXTURE,
                        model="base",
                        device="cuda",
                    )

            self.assertEqual(raised.exception.code, "DEVICE_UNAVAILABLE")
            load_model.assert_not_called()
            self.assertFalse((workdir / "analysis").exists())

    def test_invalid_language_is_rejected_before_media_probe(self) -> None:
        """Confirma validação precoce de idioma sem chamar FFprobe ou o backend."""

        with workspace_tempdir() as workdir:
            with patch("editor.analysis.transcription.media.inspect_source") as inspect_source:
                with self.assertRaises(CliError) as raised:
                    analyze_transcription(
                        workdir,
                        "main",
                        MEDIA_FIXTURE,
                        language="idioma-invalido",
                    )

            self.assertEqual(raised.exception.code, "ARGUMENT_INVALID")
            inspect_source.assert_not_called()

    def test_unsupported_source_is_rejected_before_model_resolution(self) -> None:
        """Confirma que extensão não permitida falha antes de sondar modelo ou backend."""

        with workspace_tempdir() as workdir:
            unsupported = workdir / "source.txt"
            unsupported.write_text("not media", encoding="utf-8")
            with patch("editor.whisper_backend.resolve_config") as resolve_config:
                with self.assertRaises(CliError) as raised:
                    analyze_transcription(workdir, "main", unsupported)

            self.assertEqual(raised.exception.code, "MEDIA_UNSUPPORTED")
            resolve_config.assert_not_called()
            self.assertFalse((workdir / "analysis").exists())

    def test_cli_reports_missing_model_without_partial_artifact(self) -> None:
        """Confirma envelope JSON seguro quando o host não tem modelo local instalado."""

        with workspace_tempdir() as workdir:
            model_root = workdir / "empty-models"
            model_root.mkdir()
            result = run_module(
                "analyze",
                "transcribe",
                "--workdir",
                str(workdir),
                "--source-id",
                "main",
                "--input",
                str(MEDIA_FIXTURE),
                "--device",
                "cpu",
                "--json",
                environment={"EDITOR_MODEL_DIR": str(model_root)},
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "MODEL_MISSING")
            self.assertFalse((workdir / "analysis").exists())


if __name__ == "__main__":
    unittest.main()
