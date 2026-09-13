"""Análise local de loudness, níveis por janela e silêncio por amplitude."""

from __future__ import annotations

from array import array
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

from .. import media, process
from ..contracts import CliError, EXIT_CODES
from ..workdir import (
    WorkdirLayout,
    prepare_layout,
    publish_atomic,
    relative_artifact_path,
    validate_source_id,
    validate_workdir,
)


PCM_SAMPLE_RATE = 48_000
SILENCE_THRESHOLD_DBFS = -45.0
SILENCE_MIN_DURATION_SECONDS = 0.3
MAX_AUDIO_WINDOW_SECONDS = float(media.MAX_SOURCE_DURATION_SECONDS)
MAX_AUDIO_WINDOWS = 250_000
_METRIC_PATTERN = r"([-+]?(?:\d+(?:\.\d+)?|\.\d+)|[-+]?inf)"


def _window_sample_count(window_seconds: float) -> int:
    """Converte a janela para amostras usando a mesma quantização do acumulador."""

    return int(round(window_seconds * PCM_SAMPLE_RATE))


def _dbfs(amplitude: float) -> float:
    """Converte amplitude linear para dBFS sem publicar infinito em JSON."""

    if amplitude <= 0 or not math.isfinite(amplitude):
        return -120.0
    return round(max(-120.0, 20 * math.log10(amplitude)), 6)


def _metric_value(raw_value: str) -> float:
    """Normaliza números do relatório do FFmpeg para valores JSON finitos."""

    if raw_value.lower().endswith("inf"):
        return -120.0
    return round(float(raw_value), 6)


def _parse_ebur128_report(report: str) -> dict[str, float]:
    """Extrai loudness integrado e pico verdadeiro do relatório do filtro ebur128."""

    integrated_match = re.search(
        rf"Integrated loudness:.*?I:\s*{_METRIC_PATTERN}\s*LUFS",
        report,
        flags=re.IGNORECASE | re.DOTALL,
    )
    true_peak_match = re.search(
        rf"True peak:.*?Peak:\s*{_METRIC_PATTERN}\s*dBFS",
        report,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not integrated_match or not true_peak_match:
        raise CliError(
            code="ANALYSIS_FAILED",
            message="A análise de loudness não retornou métricas completas.",
            exit_code=EXIT_CODES["OPERATION"],
        )
    return {
        "integrated_lufs": _metric_value(integrated_match.group(1)),
        "true_peak_dbfs": _metric_value(true_peak_match.group(1)),
    }


def _map_process_error(error: process.ProcessExecutionError) -> CliError:
    """Converte ausência ou falha do FFmpeg para o envelope público estável."""

    if error.missing:
        return CliError(
            code="DEPENDENCY_MISSING",
            message="FFmpeg não está disponível localmente.",
            exit_code=EXIT_CODES["DEPENDENCY"],
        )
    return CliError(
        code="ANALYSIS_FAILED",
        message="A análise de áudio falhou.",
        exit_code=EXIT_CODES["OPERATION"],
    )


def _run_loudness(source: media.MediaMetadata, timeout_seconds: float) -> dict[str, float]:
    """Executa ebur128 em saída descartável, sem criar artefato intermediário publicável."""

    arguments = [
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "info",
        "-i",
        str(source.path),
        "-map",
        "0:a:0",
        "-af",
        "ebur128=peak=true:framelog=verbose",
        "-f",
        "null",
        os.devnull,
    ]
    try:
        result = process.run_tool("ffmpeg", arguments, timeout_seconds=timeout_seconds)
    except process.ProcessExecutionError as error:
        raise _map_process_error(error) from error
    if result.returncode != 0:
        raise CliError(
            code="ANALYSIS_FAILED",
            message="A análise de loudness falhou.",
            exit_code=EXIT_CODES["OPERATION"],
        )
    return _parse_ebur128_report(result.stderr)


class _AudioAccumulator:
    """Acumula PCM em janelas e intervalos de silêncio sem manter o áudio completo."""

    def __init__(self, window_seconds: float, duration_seconds: float) -> None:
        """Inicializa acumuladores alinhados ao sample rate de análise."""

        _validate_window_seconds(window_seconds, duration_seconds)
        self.window_sample_count = _window_sample_count(window_seconds)
        self.duration_seconds = duration_seconds
        self.pending = bytearray()
        self.sample_count = 0
        self.total_square = 0.0
        self.total_peak = 0.0
        self.window_index = 0
        self.window_count = 0
        self.window_square = 0.0
        self.window_peak = 0.0
        self.windows: list[dict[str, float]] = []
        self.silence_start: int | None = None
        self.silence_intervals: list[dict[str, float]] = []

    def _finish_window(self) -> None:
        """Fecha a janela atual e preserva uma última janela parcial."""

        if self.window_count == 0:
            return
        start_sample = self.window_index * self.window_sample_count
        end_sample = start_sample + self.window_count
        start_seconds = start_sample / PCM_SAMPLE_RATE
        end_seconds = min(end_sample / PCM_SAMPLE_RATE, self.duration_seconds)
        if end_seconds > start_seconds:
            if len(self.windows) >= MAX_AUDIO_WINDOWS:
                raise CliError(
                    code="ANALYSIS_FAILED",
                    message="A análise de áudio excedeu o limite de janelas.",
                    exit_code=EXIT_CODES["OPERATION"],
                )
            self.windows.append(
                {
                    "start_s": round(start_seconds, 6),
                    "end_s": round(end_seconds, 6),
                    "average_dbfs": _dbfs(math.sqrt(self.window_square / self.window_count)),
                    "peak_dbfs": _dbfs(self.window_peak),
                }
            )
        self.window_index += 1
        self.window_count = 0
        self.window_square = 0.0
        self.window_peak = 0.0

    def _finish_silence(self, end_sample: int) -> None:
        """Publica somente trechos contínuos que atingiram o mínimo de silêncio."""

        if self.silence_start is None:
            return
        if end_sample - self.silence_start >= int(SILENCE_MIN_DURATION_SECONDS * PCM_SAMPLE_RATE):
            start_seconds = self.silence_start / PCM_SAMPLE_RATE
            end_seconds = min(end_sample / PCM_SAMPLE_RATE, self.duration_seconds)
            if end_seconds > start_seconds:
                self.silence_intervals.append(
                    {
                        "start_s": round(start_seconds, 6),
                        "end_s": round(end_seconds, 6),
                        "duration_s": round(end_seconds - start_seconds, 6),
                    }
                )
        self.silence_start = None

    def _add_sample(self, sample: float) -> None:
        """Atualiza métricas da janela, do arquivo e do detector de amplitude."""

        if self.window_count >= self.window_sample_count:
            self._finish_window()
        absolute = abs(sample)
        square = sample * sample
        self.window_count += 1
        self.window_square += square
        self.window_peak = max(self.window_peak, absolute)
        self.total_square += square
        self.total_peak = max(self.total_peak, absolute)
        if absolute <= 10 ** (SILENCE_THRESHOLD_DBFS / 20):
            if self.silence_start is None:
                self.silence_start = self.sample_count
        else:
            self._finish_silence(self.sample_count)
        self.sample_count += 1

    def feed(self, chunk: bytes) -> None:
        """Decodifica blocos little-endian float32 e os envia aos acumuladores."""

        self.pending.extend(chunk)
        complete_size = len(self.pending) - len(self.pending) % 4
        if complete_size == 0:
            return
        raw = bytes(self.pending[:complete_size])
        del self.pending[:complete_size]
        samples = array("f")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        for sample in samples:
            self._add_sample(float(sample))

    def result(self) -> dict[str, Any]:
        """Fecha o último estado e monta as métricas canônicas do artefato."""

        if self.pending:
            raise CliError(
                code="ANALYSIS_FAILED",
                message="A saída PCM da análise está incompleta.",
                exit_code=EXIT_CODES["OPERATION"],
            )
        self._finish_window()
        self._finish_silence(self.sample_count)
        if self.sample_count == 0:
            raise CliError(
                code="ANALYSIS_FAILED",
                message="A fonte não contém amostras de áudio analisáveis.",
                exit_code=EXIT_CODES["OPERATION"],
            )
        return {
            "loudness_integrated_lufs": None,
            "true_peak_dbfs": None,
            "average_dbfs": _dbfs(math.sqrt(self.total_square / self.sample_count)),
            "peak_dbfs": _dbfs(self.total_peak),
            "window_seconds": round(self.window_sample_count / PCM_SAMPLE_RATE, 6),
            "silence_threshold_dbfs": SILENCE_THRESHOLD_DBFS,
            "silence_min_duration_seconds": SILENCE_MIN_DURATION_SECONDS,
            "windows": self.windows,
            "silence": self.silence_intervals,
        }


def _run_windows(
    source: media.MediaMetadata,
    window_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Processa PCM em streaming e retorna métricas temporais sem criar arquivo parcial."""

    accumulator = _AudioAccumulator(window_seconds, source.duration_seconds)
    try:
        process.stream_pcm(
            source.path,
            PCM_SAMPLE_RATE,
            accumulator.feed,
            timeout_seconds=timeout_seconds,
        )
    except process.ProcessExecutionError as error:
        raise _map_process_error(error) from error
    return accumulator.result()


def _validate_window_seconds(
    window_seconds: float,
    duration_seconds: float | None = None,
) -> None:
    """Valida a janela antes do processamento e limita o volume do artefato temporal."""

    if (
        not math.isfinite(window_seconds)
        or window_seconds <= 0
        or window_seconds > MAX_AUDIO_WINDOW_SECONDS
        or window_seconds * PCM_SAMPLE_RATE < 1
    ):
        raise CliError(
            code="ARGUMENT_INVALID",
            message=(
                "A janela deve ser finita, representar ao menos uma amostra e não exceder "
                f"{MAX_AUDIO_WINDOW_SECONDS:g} segundos."
            ),
            exit_code=EXIT_CODES["ARGUMENT"],
        )
    if duration_seconds is not None:
        sample_count = _window_sample_count(window_seconds)
        total_samples = math.ceil(duration_seconds * PCM_SAMPLE_RATE)
        estimated_windows = (total_samples + sample_count - 1) // sample_count
        if estimated_windows > MAX_AUDIO_WINDOWS:
            raise CliError(
                code="ARGUMENT_INVALID",
                message="A janela produziria mais janelas do que o limite permitido.",
                exit_code=EXIT_CODES["ARGUMENT"],
            )


def _serialize_artifact(artifact: dict[str, Any]) -> bytes:
    """Serializa o artefato em UTF-8 canônico para leitura e hashing posteriores."""

    return json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def analyze_audio(
    workdir: str | Path,
    source_id: str,
    input_path: str | Path,
    window_seconds: float = 1.0,
) -> dict[str, Any]:
    """Valida a fonte, executa as métricas e publica uma análise completa atomicamente."""

    validate_workdir(workdir)
    validate_source_id(source_id)
    _validate_window_seconds(window_seconds)

    source = media.inspect_source(input_path)
    _validate_window_seconds(window_seconds, source.duration_seconds)
    source_hash = media.sha256_file(source.path)
    layout: WorkdirLayout = prepare_layout(workdir, source_id)
    timeout_seconds = max(60.0, min(7_200.0, source.duration_seconds * 4 + 30))
    loudness = _run_loudness(source, timeout_seconds)
    temporal = _run_windows(source, window_seconds, timeout_seconds)
    temporal["loudness_integrated_lufs"] = loudness["integrated_lufs"]
    temporal["true_peak_dbfs"] = loudness["true_peak_dbfs"]

    artifact = {
        "schema_version": 1,
        "source": {
            "id": source_id,
            "path": str(source.path),
            "sha256": source_hash,
            "size_bytes": source.size_bytes,
            "duration_seconds": round(source.duration_seconds, 6),
            "container": source.container,
            "audio_codec": source.audio_codec,
            "video_codec": source.video_codec,
        },
        "audio": temporal,
    }
    publish_atomic(layout, _serialize_artifact(artifact))
    return {
        "source_id": source_id,
        "artifact_path": str(layout.destination),
        "relative_artifact_path": relative_artifact_path(layout),
        "source_sha256": source_hash,
        "duration_seconds": round(source.duration_seconds, 6),
    }
