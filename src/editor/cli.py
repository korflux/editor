"""Parser e handler central da CLI local do editor."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .analysis.audio import analyze_audio
from .analysis.frames import extract_frames
from .analysis.transcription import analyze_transcription
from .capabilities import collect_capabilities
from .contracts import (
    CLI_VERSION,
    EXIT_CODES,
    CliError,
    error_envelope,
    serialize_envelope,
    success_envelope,
)
from .integrity.approval import approve_workdir
from .plan.validation import validate_plan
from .render.full import render_full
from .render.preview import render_preview
from .workdir import derive_source_id, init_workdir, resolve_workdir


class HelpRequested(Exception):
    """Sinal interno para encerrar ajuda sem deixar `argparse` emitir traceback."""


class CliArgumentParser(argparse.ArgumentParser):
    """Parser que transforma falhas de sintaxe em erro de domínio controlado."""

    def error(self, message: str) -> None:
        """Converte erro do parser em código estável tratado pelo handler central."""

        raise CliError(
            code="ARGUMENT_INVALID",
            message="Argumentos inválidos.",
            exit_code=EXIT_CODES["ARGUMENT"],
        )

    def exit(self, status: int = 0, message: str | None = None) -> None:
        """Emite ajuda no fluxo humano e evita `SystemExit` antes do handler."""

        if message:
            stream = sys.stdout if status == 0 else sys.stderr
            stream.write(message)
        raise HelpRequested(status)


def build_parser() -> CliArgumentParser:
    """Cria o parser raiz e registra os comandos locais já implementados."""

    parser = CliArgumentParser(
        prog="editor",
        description="CLI local, não interativa, para análise e edição linear de vídeo.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="retorna resultados de máquina em JSON pelo stdout",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="exibe a versão da CLI",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        title="comandos",
        metavar="COMMAND",
    )
    capabilities_parser = subparsers.add_parser(
        "capabilities",
        help="consulta capacidades locais sem editar arquivos ou fazer rede",
        description="consulta capacidades locais sem editar arquivos ou fazer rede",
    )
    # Permite o contrato documentado `editor capabilities --json` sem sobrescrever
    # um `--json` informado antes do subcomando na raiz do parser.
    capabilities_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o inventário estruturado em JSON pelo stdout",
    )
    init_parser = subparsers.add_parser(
        "init",
        help="prepara a estrutura técnica do trabalho sem criar estado editorial",
        description="prepara o diretório de trabalho sem escolher fontes ou criar artefatos",
    )
    init_parser.add_argument("--workdir", required=False, default=None, help="diretório absoluto do trabalho (padrão: diretório atual)")
    init_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="analisa fontes locais sem alterar os originais",
        description="analisa fontes locais sem alterar os originais",
    )
    analyze_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    analyze_subparsers = analyze_parser.add_subparsers(
        dest="analyze_command",
        title="análises",
        metavar="ANALYSIS",
    )
    audio_parser = analyze_subparsers.add_parser(
        "audio",
        help="gera análise de loudness, níveis por janela e silêncio",
        description="gera análise local de áudio com guardrails de mídia",
    )
    audio_parser.add_argument("--workdir", required=False, default=None, help="diretório absoluto do trabalho (padrão: diretório atual)")
    audio_parser.add_argument("--source-id", required=False, default=None, help="identificador seguro da fonte (padrão: basename do --input)")
    audio_parser.add_argument("--input", required=True, help="arquivo local MP4, MP3 ou WAV")
    audio_parser.add_argument(
        "--window-s",
        type=float,
        default=1.0,
        help="duração das janelas em segundos, padrão: 1",
    )
    audio_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    transcribe_parser = analyze_subparsers.add_parser(
        "transcribe",
        help="gera transcrição local com timestamps por palavra",
        description="gera transcrição local sem enviar a fonte para a rede",
    )
    transcribe_parser.add_argument("--workdir", required=False, default=None, help="diretório absoluto do trabalho (padrão: diretório atual)")
    transcribe_parser.add_argument("--source-id", required=False, default=None, help="identificador seguro da fonte (padrão: basename do --input)")
    transcribe_parser.add_argument("--input", required=True, help="arquivo local MP4, MP3 ou WAV")
    transcribe_parser.add_argument(
        "--language",
        default="auto",
        help="idioma da transcrição ou auto, padrão: auto",
    )
    transcribe_parser.add_argument(
        "--model",
        help="nome ou diretório de modelo Whisper local validado",
    )
    transcribe_parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="dispositivo de processamento, padrão: auto",
    )
    transcribe_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    extract_parser = subparsers.add_parser(
        "extract",
        help="extrai evidências visuais de fontes locais",
        description="extrai frames de vídeo sem alterar a fonte original",
    )
    extract_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    extract_subparsers = extract_parser.add_subparsers(
        dest="extract_command",
        title="extrações",
        metavar="EXTRACTION",
    )
    frames_parser = extract_subparsers.add_parser(
        "frames",
        help="extrai PNGs por timestamps explícitos ou intervalo regular",
        description="extrai frames de vídeo e publica um manifesto temporal",
    )
    frames_parser.add_argument("--workdir", required=False, default=None, help="diretório absoluto do trabalho (padrão: diretório atual)")
    frames_parser.add_argument("--source-id", required=False, default=None, help="identificador seguro da fonte (padrão: basename do --input)")
    frames_parser.add_argument("--input", required=True, help="arquivo local MP4 com vídeo H.264")
    selection = frames_parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--timestamps",
        help="timestamps em segundos separados por vírgula",
    )
    selection.add_argument(
        "--interval-s",
        help="intervalo regular em segundos",
    )
    frames_parser.add_argument("--start-s", help="início do intervalo, padrão: 0")
    frames_parser.add_argument(
        "--end-s",
        help="fim exclusivo do intervalo, padrão: duração da fonte",
    )
    frames_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    plan_parser = subparsers.add_parser(
        "plan",
        help="valida o plano linear do trabalho sem renderizar",
        description="valida plan.json e calcula a timeline linear sem renderizar",
    )
    plan_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    plan_subparsers = plan_parser.add_subparsers(
        dest="plan_command",
        title="operações de plano",
        metavar="OPERATION",
    )
    validate_parser = plan_subparsers.add_parser(
        "validate",
        help="valida exclusivamente plan.json no diretório de trabalho",
        description="valida exclusivamente plan.json no diretório de trabalho",
    )
    validate_parser.add_argument("--workdir", required=False, default=None, help="diretório absoluto do trabalho (padrão: diretório atual)")
    validate_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    approve_parser = subparsers.add_parser(
        "approve",
        help="registra aprovação do plano e da preview por hash",
        description="valida o plano e publica approval.json com hashes locais",
    )
    approve_parser.add_argument("--workdir", required=False, default=None, help="diretório absoluto do trabalho (padrão: diretório atual)")
    approve_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    render_parser = subparsers.add_parser(
        "render",
        help="renderiza artefatos locais a partir do plano validado",
        description="renderiza artefatos locais a partir do plano validado",
    )
    render_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    render_subparsers = render_parser.add_subparsers(
        dest="render_command",
        title="renders",
        metavar="RENDER",
    )
    preview_parser = render_subparsers.add_parser(
        "preview",
        help="gera ou substitui a única preview.mp4 do trabalho",
        description="gera ou substitui preview.mp4 a partir de um plano validado",
    )
    preview_parser.add_argument("--workdir", required=False, default=None, help="diretório absoluto do trabalho (padrão: diretório atual)")
    preview_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    full_parser = render_subparsers.add_parser(
        "full",
        help="gera ou substitui a full.mp4 após validar a aprovação vigente",
        description="valida aprovação, fontes e plano antes de gerar full.mp4",
    )
    full_parser.add_argument("--workdir", required=False, default=None, help="diretório absoluto do trabalho (padrão: diretório atual)")
    full_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    return parser


def write_json(payload: dict[str, object]) -> None:
    """Escreve exatamente um envelope JSON no stdout, sem misturar logs humanos."""

    sys.stdout.write(f"{serialize_envelope(payload)}\n")


def configure_output_encoding() -> None:
    """Fixa stdout e stderr em UTF-8 para preservar o contrato JSON no Windows."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            # Test doubles e streams já fechados podem não expor reconfigure.
            continue


def effective_source_id(explicit: str | None, input_path: str) -> str:
    """Devolve o source-id informado ou o derivado do input, mantendo o erro público estável."""

    if explicit is not None:
        return explicit
    return derive_source_id(input_path)


def handle_error(error: CliError, json_output: bool) -> int:
    """Publica erro estruturado ou diagnóstico curto conforme o modo solicitado."""

    if json_output:
        write_json(error_envelope(error))
    else:
        sys.stderr.write(f"{error.message}\n")
    return error.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a borda da CLI, despacha capacidades e mantém exceções internas fora da resposta pública."""

    configure_output_encoding()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in raw_args

    try:
        args = build_parser().parse_args(raw_args)
        if args.version:
            if args.json_output:
                write_json(success_envelope("version", {"version": CLI_VERSION}))
            else:
                sys.stdout.write(f"editor {CLI_VERSION}\n")
            return 0

        if args.command == "capabilities":
            # O inventário é sempre estruturado para que agentes não dependam de
            # texto humano, mesmo quando o operador omite o sinalizador `--json`.
            write_json(success_envelope("capabilities", collect_capabilities()))
            return 0

        if args.command == "init":
            result = init_workdir(resolve_workdir(args.workdir))
            write_json(success_envelope("init", result))
            return 0

        if args.command == "analyze" and args.analyze_command == "audio":
            result = analyze_audio(
                workdir=resolve_workdir(args.workdir),
                source_id=effective_source_id(args.source_id, args.input),
                input_path=args.input,
                window_seconds=args.window_s,
            )
            write_json(success_envelope("analyze audio", result))
            return 0

        if args.command == "analyze" and args.analyze_command == "transcribe":
            result = analyze_transcription(
                workdir=resolve_workdir(args.workdir),
                source_id=effective_source_id(args.source_id, args.input),
                input_path=args.input,
                language=args.language,
                model=args.model,
                device=args.device,
            )
            write_json(success_envelope("analyze transcribe", result))
            return 0

        if args.command == "extract" and args.extract_command == "frames":
            result = extract_frames(
                workdir=resolve_workdir(args.workdir),
                source_id=effective_source_id(args.source_id, args.input),
                input_path=args.input,
                timestamps=args.timestamps,
                interval_seconds=args.interval_s,
                start_seconds=args.start_s,
                end_seconds=args.end_s,
            )
            write_json(success_envelope("extract frames", result))
            return 0

        if args.command == "plan" and args.plan_command == "validate":
            result = validate_plan(resolve_workdir(args.workdir))
            write_json(success_envelope("plan validate", result))
            return 0

        if args.command == "approve":
            result = approve_workdir(resolve_workdir(args.workdir))
            write_json(success_envelope("approve", result))
            return 0

        if args.command == "render" and args.render_command == "preview":
            result = render_preview(resolve_workdir(args.workdir))
            write_json(success_envelope("render preview", result))
            return 0

        if args.command == "render" and args.render_command == "full":
            result = render_full(resolve_workdir(args.workdir))
            write_json(success_envelope("render full", result))
            return 0

        if args.command is None:
            raise CliError(
                code="ARGUMENT_INVALID",
                message="Informe um subcomando.",
                exit_code=EXIT_CODES["ARGUMENT"],
            )

        raise CliError(
            code="ARGUMENT_INVALID",
            message="Comando ou análise indisponível nesta versão.",
            exit_code=EXIT_CODES["ARGUMENT"],
        )
    except HelpRequested as signal:
        return int(signal.args[0]) if signal.args else 0
    except CliError as error:
        return handle_error(error, json_requested)
    except Exception:
        # O envelope evita stack trace no contrato público; o diagnóstico interno
        # deve ser registrado por uma camada de logging quando ela existir.
        return handle_error(
            CliError(
                code="INTERNAL_ERROR",
                message="Falha interna.",
                exit_code=EXIT_CODES["INTERNAL"],
            ),
            json_requested,
        )
