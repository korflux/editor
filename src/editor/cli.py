"""Parser e handler central da CLI local do editor."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from .capabilities import collect_capabilities
from .contracts import (
    CLI_VERSION,
    EXIT_CODES,
    CliError,
    error_envelope,
    serialize_envelope,
    success_envelope,
)


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
    """Cria o parser raiz e registra somente o comando de capacidades já implementado."""

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
    return parser


def write_json(payload: dict[str, object]) -> None:
    """Escreve exatamente um envelope JSON no stdout, sem misturar logs humanos."""

    sys.stdout.write(f"{serialize_envelope(payload)}\n")


def handle_error(error: CliError, json_output: bool) -> int:
    """Publica erro estruturado ou diagnóstico curto conforme o modo solicitado."""

    if json_output:
        write_json(error_envelope(error))
    else:
        sys.stderr.write(f"{error.message}\n")
    return error.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a borda da CLI, despacha capacidades e mantém exceções internas fora da resposta pública."""

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

        if args.command is None:
            raise CliError(
                code="ARGUMENT_INVALID",
                message="Informe um subcomando.",
                exit_code=EXIT_CODES["ARGUMENT"],
            )

        raise CliError(
            code="ARGUMENT_INVALID",
            message="Comando indisponível nesta versão.",
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
