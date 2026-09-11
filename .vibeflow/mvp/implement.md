# Implement: MVP CLI local de análise e edição linear
# Alvo: mvp
# Status: aprovado
# Plan: plan.md (mesma pasta)

## Abertura

- **ROUTE:** max
- **Modo:** A
- **Fila:** T1 elegível; T2 a T11 bloqueadas por dependências
- **Analyze:** `analyze.md` aprovado, veredito `limpo`
- **Pergunta da T1:** a CLI instalável sobe pelos dois pontos de entrada e retorna envelopes JSON estáveis para sucesso e erro sem executar mídia ou interação?

## T1: Criar CLI instalável com smoke test

### Reconhecer

- O repositório não tinha código de aplicação, manifesto Python, pacote ou testes.
- A task não tinha dependências de runtime e os caminhos previstos estavam livres para criação.
- A implementação usa apenas `argparse`, `json`, `dataclasses` e `subprocess` da biblioteca padrão, sem shell intermediário ou rede.

### Codar

- `pyproject.toml` define o pacote `editor` em `src/` e o entry point `editor`.
- `src/editor/contracts.py` centraliza versão, schema, exit codes, erro tipado, envelopes e serialização JSON determinística.
- `src/editor/cli.py` centraliza parser, ajuda, despacho inicial, tratamento de erros e saída segura.
- `src/editor/__main__.py` mantém `python -m editor`; `--version --json` fornece um sucesso verificável até as operações do MVP serem implementadas.
- `tests/test_cli_contract.py` executa processos reais para módulo e entry point instalado, além dos envelopes de sucesso e erro.

### Testar

- `python -m pip install --no-deps -e .` -> instalação editável concluída.
- `python -m editor --help` -> ajuda exibida, exit code `0`.
- `editor --help` -> ajuda exibida, exit code `0`.
- `python -m unittest discover -s tests -p "test_cli_contract.py" -v` -> `Ran 4 tests ... OK`.

### Simplificar e re-testar

- A revisão manteve a implementação sem abstrações especulativas: ainda não há dispatcher de mídia, dependências externas ou comandos além da fundação contratual.
- A suíte foi executada novamente após a revisão e permaneceu verde.

### Registro

- **Plan:** T1 marcada como concluída.
- **Spec comprovada nesta fatia:** base de A11 e C2, sem marcar os critérios globais antes da cobertura dos subcomandos seguintes.
- **Mensagem de commit:** `task(T1): create installable cli contract`

## Handoff

Próxima T*: T2, provisionamento do ambiente local reproduzível.
