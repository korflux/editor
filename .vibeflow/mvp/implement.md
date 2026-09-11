# Implement: MVP CLI local de análise e edição linear
# Alvo: mvp
# Status: aprovado
# Plan: plan.md (mesma pasta)

## Abertura

- **ROUTE:** max
- **Modo:** A
- **Fila:** T2 elegível; T3 a T11 bloqueadas por dependências
- **Analyze:** `analyze.md` aprovado, veredito `limpo`
- **Pergunta da T2:** o ambiente local pode ser instalado e verificado de forma reproduzível, com dependências pinadas e nenhum componente externo instalado sem ação explícita?

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

## T2: Provisionar ambiente local reproduzível

### Reconhecer

- A T1 deixou uma CLI Python instalável, mas sem dependências de runtime, lockfile ou instalador.
- O plano exige Python 3.11 x64 no Windows, `faster-whisper` sobre CTranslate2, FFmpeg local, modelos locais e gates de segredo/SCA.
- O host atual expõe Python 3.12/3.13, FFmpeg 9.0.1 e gitleaks 8.30.1, mas não possui Python 3.11, o backend Whisper, modelos locais ou pip-audit. A task foi implementada para reportar esse estado sem tratá-lo como aprovado.

### Codar

- `requirements.lock` foi resolvido para Python 3.11 x64 no Windows com 50 pacotes pinados e 648 hashes SHA-256. Os pins diretos são `faster-whisper==1.2.1`, `ctranslate2==4.8.0`, `pip-audit==2.10.1`, `setuptools==80.9.0` e `wheel==0.45.1`.
- `scripts/install.ps1` separa verificação e instalação. `-Verify` é somente leitura; `-Install` cria o venv, instala o lock com `--require-hashes`, instala a CLI em modo editável e roda `pip check`.
- FFmpeg, gitleaks, modelos e CUDA só são instalados por opções explícitas e origens locais. O script valida caminhos regulares, rejeita reparse points e usa chamadas externas com argumentos estruturados.
- `-SecurityGates` executa gitleaks e pip-audit somente depois da checagem de disponibilidade, bloqueando o resultado quando uma ferramenta está ausente.
- `tests/test_installation.py` cobre lockfile, modo somente leitura, rejeição do Python incompatível, cópia de modelo local e bloqueio do SCA.
- `.vibeflow/REGRAS.md` foi sincronizado com os novos arquivos, versões e convenções de instalação.

### Testar

- `python -m unittest discover -s tests -p "test_installation.py" -v` -> `Ran 6 tests ... OK`.
- `python -m unittest discover -s tests -v` -> `Ran 10 tests ... OK`.
- `pwsh -NoProfile -File scripts/install.ps1 -Verify` -> exit `1` esperado. O script reportou lockfile e FFmpeg/gitleaks disponíveis, Python 3.11, venv, backend, modelos e pip-audit ausentes, sem criar artefatos.
- `gitleaks detect --source . --no-banner` -> exit `0`, `no leaks found`.
- `python -m pip_audit -r requirements.lock` -> exit `1` porque pip-audit não está instalado; esse estado é reportado como bloqueio seguro pelo instalador.
- `python -m pip check` -> exit `1` por conflito preexistente do Python global entre `inquirer 3.4.1` e o pacote local `editor 0.1.0`; o achado foi registrado em `.erros-encontrados/2026-09-10-python-global-inquirer-editor.md` e não foi alterado.

### Simplificar e re-testar

- A revisão removeu estado interno de checks que não era consumido e corrigiu o controle explícito de sobrescrita de modelos com `-Force`.
- A suíte específica permaneceu verde após a simplificação, e a suíte completa confirmou ausência de regressão na CLI da T1.

### Registro

- **Plan:** T2 marcada como concluída; a limitação do host e o conflito global foram registrados no `plan.md`.
- **Spec comprovada nesta fatia:** o contrato de provisionamento local de A12 e a ausência de fallback externo prevista em C5; a execução de mídia continua nas tasks seguintes.
- **Arquivos:** `requirements.lock`, `scripts/install.ps1`, `tests/test_installation.py`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-09-10-python-global-inquirer-editor.md`.
- **Mensagem de commit:** `task(T2): provision reproducible local environment`

## Handoff

Fila após T2: T3 ou T4 elegíveis. Na próxima execução, usar Q; recomendo T3 por ser a menor T* e fechar o contrato de capacidades antes da transcrição.
