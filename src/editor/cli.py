"""Parser e handler central da CLI local do editor."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .analysis.audio import analyze_audio
from .analysis.captions import (
    SUGGEST_MAX_CHARS_DEFAULT,
    SUGGEST_MAX_CHARS_MAX,
    SUGGEST_MAX_CHARS_MIN,
    SUGGEST_MAX_DURATION_DEFAULT_S,
    SUGGEST_MAX_DURATION_MAX_S,
    SUGGEST_MAX_DURATION_MIN_S,
    suggest_captions,
)
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
from .render.preview import ACCEL_CHOICES, DEFAULT_QUALITY, PROXY_CHOICES, QUALITY_CHOICES, render_preview
from .render.proxy import build_proxies
from .ui.instance import start_ui, stop_ui
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


def _add_json_argument(parser: argparse.ArgumentParser) -> None:
    """Registra `--json` no subparser sem sobrescrever o sinalizador informado na raiz."""

    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )


def _add_workdir_argument(parser: argparse.ArgumentParser) -> None:
    """Registra `--workdir` opcional com default para o diretório atual, como nos longos."""

    parser.add_argument("--workdir", required=False, default=None, help="diretório absoluto do trabalho (padrão: diretório atual)")


def _add_accel_argument(parser: argparse.ArgumentParser) -> None:
    """Registra `--accel` nos renders; `gpu` usa decode CUDA e NVENC com fallback para CPU."""

    parser.add_argument(
        "--accel",
        choices=ACCEL_CHOICES,
        default="none",
        help="aceleração do render, padrão: none (CPU); gpu usa NVIDIA e cai para CPU se falhar",
    )


def _add_quality_argument(parser: argparse.ArgumentParser) -> None:
    """Registra `--quality` só na full (decisão 20); a preview não recebe a opção e usa sempre `standard`."""

    parser.add_argument(
        "--quality",
        choices=QUALITY_CHOICES,
        default=DEFAULT_QUALITY,
        help="preset de qualidade do vídeo da full, padrão: standard; high e max geram arquivos maiores e mais fiéis",
    )


def _add_proxy_argument(parser: argparse.ArgumentParser) -> None:
    """Registra `--proxy` só na preview (decisão 10); a full não recebe a opção e nunca lê proxy."""

    parser.add_argument(
        "--proxy",
        choices=PROXY_CHOICES,
        default="auto",
        help="leitura de proxies, padrão: auto (usa proxy válido de cada fonte); off lê só os originais",
    )


def _add_source_id_argument(parser: argparse.ArgumentParser) -> None:
    """Registra `--source-id` opcional derivado do basename do `--input`, como nos longos."""

    parser.add_argument("--source-id", required=False, default=None, help="identificador seguro da fonte (padrão: basename do --input)")


def register_shortcut_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Registra atalhos planos como equivalentes exatos aos canônicos, sem mudar os longos."""

    # Atalho `audio` equivale a `analyze audio`; o envelope usa o nome canônico.
    audio_shortcut = subparsers.add_parser(
        "audio",
        help="atalho para analyze audio",
        description="atalho para analyze audio",
    )
    _add_workdir_argument(audio_shortcut)
    _add_source_id_argument(audio_shortcut)
    audio_shortcut.add_argument("--input", required=True, help="arquivo local MP4, MOV, MP3 ou WAV")
    audio_shortcut.add_argument(
        "--window-s",
        type=float,
        default=1.0,
        help="duração das janelas em segundos, padrão: 1",
    )
    _add_json_argument(audio_shortcut)
    # Atalho `transcribe` equivale a `analyze transcribe`; o envelope usa o nome canônico.
    transcribe_shortcut = subparsers.add_parser(
        "transcribe",
        help="atalho para analyze transcribe",
        description="atalho para analyze transcribe",
    )
    _add_workdir_argument(transcribe_shortcut)
    _add_source_id_argument(transcribe_shortcut)
    transcribe_shortcut.add_argument("--input", required=True, help="arquivo local MP4, MOV, MP3 ou WAV")
    transcribe_shortcut.add_argument(
        "--language",
        default="auto",
        help="idioma da transcrição ou auto, padrão: auto",
    )
    transcribe_shortcut.add_argument(
        "--model",
        help="nome ou diretório de modelo Whisper local validado",
    )
    transcribe_shortcut.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda"),
        default="auto",
        help="dispositivo de processamento, padrão: auto",
    )
    _add_json_argument(transcribe_shortcut)
    # Atalho `frames` equivale a `extract frames`; o envelope usa o nome canônico.
    frames_shortcut = subparsers.add_parser(
        "frames",
        help="atalho para extract frames",
        description="atalho para extract frames",
    )
    _add_workdir_argument(frames_shortcut)
    _add_source_id_argument(frames_shortcut)
    frames_shortcut.add_argument("--input", required=True, help="arquivo local MP4 ou MOV com vídeo H.264 ou HEVC")
    frames_selection = frames_shortcut.add_mutually_exclusive_group(required=True)
    frames_selection.add_argument(
        "--timestamps",
        help="timestamps em segundos separados por vírgula",
    )
    frames_selection.add_argument(
        "--interval-s",
        help="intervalo regular em segundos",
    )
    frames_shortcut.add_argument("--start-s", help="início do intervalo, padrão: 0")
    frames_shortcut.add_argument(
        "--end-s",
        help="fim exclusivo do intervalo, padrão: duração da fonte",
    )
    _add_json_argument(frames_shortcut)
    # Atalho `validate` equivale a `plan validate`; o envelope usa o nome canônico.
    validate_shortcut = subparsers.add_parser(
        "validate",
        help="atalho para plan validate",
        description="atalho para plan validate",
    )
    _add_workdir_argument(validate_shortcut)
    _add_json_argument(validate_shortcut)
    # Atalho `preview` equivale a `render preview`; o envelope usa o nome canônico.
    preview_shortcut = subparsers.add_parser(
        "preview",
        help="atalho para render preview",
        description="atalho para render preview",
    )
    _add_workdir_argument(preview_shortcut)
    _add_accel_argument(preview_shortcut)
    _add_proxy_argument(preview_shortcut)
    _add_json_argument(preview_shortcut)
    # Atalho `full` equivale a `render full`; o envelope usa o nome canônico.
    full_shortcut = subparsers.add_parser(
        "full",
        help="atalho para render full",
        description="atalho para render full",
    )
    _add_workdir_argument(full_shortcut)
    _add_accel_argument(full_shortcut)
    _add_quality_argument(full_shortcut)
    _add_json_argument(full_shortcut)


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
    audio_parser.add_argument("--input", required=True, help="arquivo local MP4, MOV, MP3 ou WAV")
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
    transcribe_parser.add_argument("--input", required=True, help="arquivo local MP4, MOV, MP3 ou WAV")
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
    frames_parser.add_argument("--input", required=True, help="arquivo local MP4 ou MOV com vídeo H.264 ou HEVC")
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
        help="registra a aprovação do plano e das fontes por hash",
        description="valida o plano e publica approval.json (v2, sem preview) com os hashes do plano e das fontes",
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
    _add_accel_argument(preview_parser)
    _add_proxy_argument(preview_parser)
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
    _add_accel_argument(full_parser)
    _add_quality_argument(full_parser)
    full_parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        default=argparse.SUPPRESS,
        help="retorna o resultado estruturado em JSON pelo stdout",
    )
    # Grupo `proxy`: cache explícito de proxies leves usado só pela preview (spec da phase 4, F2).
    proxy_parser = subparsers.add_parser(
        "proxy",
        help="gera proxies leves das fontes de vídeo para acelerar a preview",
        description="gera e reutiliza proxies das fontes de vídeo do plano validado",
    )
    _add_json_argument(proxy_parser)
    proxy_subparsers = proxy_parser.add_subparsers(
        dest="proxy_command",
        title="operações de proxy",
        metavar="OPERATION",
    )
    proxy_build_parser = proxy_subparsers.add_parser(
        "build",
        help="cria ou reutiliza proxy/<id>.mp4 das fontes maiores que 1280x720",
        description="cria ou reutiliza proxies H.264 até 1280x720 das fontes de vídeo do plano",
    )
    _add_workdir_argument(proxy_build_parser)
    proxy_build_parser.add_argument(
        "--source-id",
        action="append",
        dest="source_ids",
        default=None,
        metavar="ID",
        help="restringe a uma fonte do plano; repetível (padrão: todas)",
    )
    _add_json_argument(proxy_build_parser)
    # Grupo `captions`: fala original em frases curtas para o agente traduzir (spec da phase 6, F1).
    captions_parser = subparsers.add_parser(
        "captions",
        help="prepara legendas a partir da transcrição publicada, sem escrever no trabalho",
        description="operações somente leitura de apoio às legendas das falas estrangeiras",
    )
    _add_json_argument(captions_parser)
    captions_subparsers = captions_parser.add_subparsers(
        dest="captions_command",
        title="operações de legenda",
        metavar="OPERATION",
    )
    captions_suggest_parser = captions_subparsers.add_parser(
        "suggest",
        help="quebra a fala original da fonte em frases curtas com tempos",
        description=(
            "lê a transcrição publicada da fonte e devolve a fala original em frases curtas, "
            "com tempos da fonte e por palavra, para o agente traduzir; não escreve arquivo"
        ),
    )
    _add_workdir_argument(captions_suggest_parser)
    captions_suggest_parser.add_argument(
        "--source-id",
        required=True,
        help="identificador da fonte já transcrita por analyze transcribe",
    )
    captions_suggest_parser.add_argument(
        "--start-s",
        help="início da janela em segundos, padrão: 0; entra a palavra que começa a partir daqui",
    )
    captions_suggest_parser.add_argument(
        "--end-s",
        help="fim exclusivo da janela em segundos, padrão: fim da transcrição",
    )
    captions_suggest_parser.add_argument(
        "--max-chars",
        help=(
            f"máximo de caracteres por frase, padrão: {SUGGEST_MAX_CHARS_DEFAULT}, "
            f"de {SUGGEST_MAX_CHARS_MIN} a {SUGGEST_MAX_CHARS_MAX}"
        ),
    )
    captions_suggest_parser.add_argument(
        "--max-duration-s",
        help=(
            f"máximo de segundos por frase, padrão: {SUGGEST_MAX_DURATION_DEFAULT_S:g}, "
            f"de {SUGGEST_MAX_DURATION_MIN_S:g} a {SUGGEST_MAX_DURATION_MAX_S:g}"
        ),
    )
    _add_json_argument(captions_suggest_parser)
    # Grupo `ui`: interface de revisão no navegador, aberta pela IA e encerrada por ela (spec da phase 7, F1).
    ui_parser = subparsers.add_parser(
        "ui",
        help="abre e encerra a interface de revisão da montagem no navegador",
        description="abre e encerra o servidor local da interface de revisão, uma instância por trabalho",
    )
    _add_json_argument(ui_parser)
    ui_subparsers = ui_parser.add_subparsers(
        dest="ui_command",
        title="operações da interface",
        metavar="OPERATION",
    )
    ui_start_parser = ui_subparsers.add_parser(
        "start",
        help="inicia o servidor local em segundo plano e abre o navegador",
        description=(
            "inicia o servidor local (127.0.0.1, porta livre) em segundo plano, devolve a URL com o token e "
            "abre o navegador padrão; com uma instância viva no trabalho, devolve a mesma URL"
        ),
    )
    _add_workdir_argument(ui_start_parser)
    ui_start_parser.add_argument(
        "--no-open",
        action="store_true",
        help="não abre o navegador; a URL segue no envelope",
    )
    _add_json_argument(ui_start_parser)
    ui_stop_parser = ui_subparsers.add_parser(
        "stop",
        help="encerra o servidor do trabalho e os FFmpeg filhos",
        description="encerra o servidor da interface e os processos FFmpeg filhos; sem instância, informa not_running",
    )
    _add_workdir_argument(ui_stop_parser)
    _add_json_argument(ui_stop_parser)
    register_shortcut_parsers(subparsers)
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

        if (args.command == "analyze" and args.analyze_command == "audio") or args.command == "audio":
            result = analyze_audio(
                workdir=resolve_workdir(args.workdir),
                source_id=effective_source_id(args.source_id, args.input),
                input_path=args.input,
                window_seconds=args.window_s,
            )
            write_json(success_envelope("analyze audio", result))
            return 0

        if (args.command == "analyze" and args.analyze_command == "transcribe") or args.command == "transcribe":
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

        if (args.command == "extract" and args.extract_command == "frames") or args.command == "frames":
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

        if (args.command == "plan" and args.plan_command == "validate") or args.command == "validate":
            result = validate_plan(resolve_workdir(args.workdir))
            write_json(success_envelope("plan validate", result))
            return 0

        if args.command == "approve":
            result = approve_workdir(resolve_workdir(args.workdir))
            write_json(success_envelope("approve", result))
            return 0

        if (args.command == "render" and args.render_command == "preview") or args.command == "preview":
            result = render_preview(resolve_workdir(args.workdir), accel=args.accel, proxy=args.proxy)
            write_json(success_envelope("render preview", result))
            return 0

        if (args.command == "render" and args.render_command == "full") or args.command == "full":
            result = render_full(resolve_workdir(args.workdir), accel=args.accel, quality=args.quality)
            write_json(success_envelope("render full", result))
            return 0

        if args.command == "proxy" and args.proxy_command == "build":
            result = build_proxies(resolve_workdir(args.workdir), args.source_ids)
            write_json(success_envelope("proxy build", result))
            return 0

        if args.command == "captions" and args.captions_command == "suggest":
            result = suggest_captions(
                resolve_workdir(args.workdir),
                args.source_id,
                start_s=args.start_s,
                end_s=args.end_s,
                max_chars=args.max_chars,
                max_duration_s=args.max_duration_s,
            )
            write_json(success_envelope("captions suggest", result))
            return 0

        if args.command == "ui" and args.ui_command == "start":
            result = start_ui(resolve_workdir(args.workdir), open_browser=not args.no_open)
            write_json(success_envelope("ui start", result))
            return 0

        if args.command == "ui" and args.ui_command == "stop":
            result = stop_ui(resolve_workdir(args.workdir))
            write_json(success_envelope("ui stop", result))
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
