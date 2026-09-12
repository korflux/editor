# Implement: MVP CLI local de análise e edição linear
# Alvo: mvp
# Status: aprovado
# Plan: plan.md (mesma pasta)

## Abertura

- **ROUTE:** max
- **Modo:** A
- **Fila:** T4 concluída; T5, T6 e T7 são elegíveis; T8 a T11 continuam bloqueadas pelas dependências do plano
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

## T3: Detectar capacidades locais com recomendação determinística

### Reconhecer

- A T1 deixou o parser e os envelopes centrais, mas qualquer subcomando ainda era recusado como indisponível.
- A T2 definiu as origens locais aceitas: FFmpeg/FFprobe em `.tools/ffmpeg`, backend `faster-whisper`/CTranslate2 e modelos CTranslate2 `tiny`, `base` e `small` em `models`.
- O fluxo precisava responder mesmo com dependências opcionais ausentes, sem rede, download, edição de arquivos ou fallback remoto.

### Codar

- `src/editor/dependencies.py` concentra sondagem de executáveis com `shell=False`, timeout, versão do FFmpeg, pacotes Whisper, GPU/VRAM via `nvidia-smi`, memória do sistema e validação dos arquivos mínimos de modelos.
- `src/editor/capabilities.py` monta o inventário versionado de sistema, dependências, dispositivos, formatos, limites, operações e recomendação determinística. A ordem é `small` em CUDA, `base` em CPU e `tiny` como fallback local.
- `src/editor/cli.py` registra `capabilities` e preserva `--json` antes ou depois do subcomando. O comando publica somente o envelope JSON.
- `tests/test_capabilities.py` cobre o processo real, as duas posições de `--json`, a ordem da recomendação e o contrato de modelos do instalador.

### Testar

- `python -m editor capabilities --json` -> exit `0`, envelope `capabilities` válido e stdout estruturado; o host atual reportou FFmpeg/FFprobe e GPU disponíveis, e backend Whisper/modelos ausentes sem bloquear a consulta.
- `python -m unittest discover -s tests -p "test_capabilities.py" -v` -> `Ran 4 tests ... OK`.
- `python -m unittest discover -s tests -v` -> `Ran 14 tests ... OK`.

### Simplificar e re-testar

- A falha inicial do teste foi diagnosticada como bloqueio de ACL em `%TEMP%`, não como falha do detector. O fixture foi movido para um diretório temporário dentro do workspace, seguindo o padrão de `test_installation.py`.
- A descoberta mantém somente os campos necessários ao contrato, sem importar o backend pesado nem adicionar dependências novas.

### Registro

- **Plan:** T3 marcada como concluída.
- **Spec comprovada nesta fatia:** A1.
- **Arquivos:** `src/editor/cli.py`, `src/editor/capabilities.py`, `src/editor/dependencies.py`, `tests/test_capabilities.py`, `.vibeflow/REGRAS.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`.
- **Mensagem de commit:** `task(T3): add local capabilities discovery`

## T4: Analisar áudio com guardrails de mídia

### Reconhecer

- T3 deixou o parser de envelopes e a sondagem local, mas não havia dispatcher de análise, validação de mídia ou layout de trabalho.
- A T4 exige validação antes do FFmpeg, um único artefato em `analysis/audio/<source-id>.json`, preservação de estado em falhas e métricas de amplitude sem classificação semântica.
- O fluxo foi separado em `workdir.py` para caminhos e publicação, `media.py` para FFprobe e limites, `process.py` para subprocessos estruturados e `analysis/audio.py` para a operação de áudio.

### Codar

- `src/editor/cli.py` registra `analyze audio`, aceita `--json` antes ou depois dos subcomandos e encaminha a operação sem prompts.
- `src/editor/workdir.py` valida diretório absoluto gravável, `source-id`, componentes regulares, destinos internos e publicação atômica por `.tmp`.
- `src/editor/media.py` valida extensão, contêiner, streams, codecs, tamanho de 4 GiB e duração de 3.600 s com `ffprobe`; a fonte é identificada por SHA-256.
- `src/editor/process.py` executa FFmpeg/FFprobe com argumentos estruturados, `shell=False`, timeout e PCM float32 em streaming.
- `src/editor/analysis/audio.py` usa `ebur128` para loudness integrado e pico verdadeiro, calcula média/RMS e pico por janela e detecta somente amplitude abaixo de `-45 dBFS` por pelo menos `0,3 s`.
- `tests/fixtures/media/main.mp4` fornece uma fonte curta H.264/AAC com silêncio inicial e final; `tests/test_audio_analysis.py` cobre processo real, rejeição precoce e falha simulada.

### Testar

- `python -m editor analyze audio --workdir A:\Projetos\editor\tmp-t4-run --source-id main --input tests/fixtures/media/main.mp4 --window-s 0.5 --json` -> exit `0`; envelope e artefato publicados.
- `python -m unittest discover -s tests -p "test_audio_analysis.py" -v` -> `Ran 4 tests ... OK`.
- `python -B -m unittest discover -s tests -v` -> `Ran 18 tests ... OK`.
- `gitleaks detect --source . --no-banner` -> exit `0`, `no leaks found`.

### Simplificar e re-testar

- A análise mantém somente o primeiro stream de áudio validado, usa processamento PCM em streaming e descarta stderr técnico do processo, porque o contrato público não precisa do diagnóstico bruto e a memória não deve crescer com a duração da fonte.
- A publicação só ocorre depois de `ffprobe`, loudness, janelas, silêncio e serialização concluírem; falhas preservam os arquivos existentes e removem o temporário.
- A suíte específica foi executada novamente após a simplificação e permaneceu verde; a suíte completa confirmou ausência de regressão nas T1 a T3.

### Registro

- **Plan:** T4 marcada como concluída.
- **Spec comprovada nesta fatia:** A3. Os critérios globais de preservação, contrato completo e jornada permanecem abertos até suas tasks correspondentes.
- **Arquivos:** `.vibeflow/REGRAS.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`, `src/editor/cli.py`, `src/editor/capabilities.py`, `src/editor/workdir.py`, `src/editor/media.py`, `src/editor/process.py`, `src/editor/analysis/__init__.py`, `src/editor/analysis/audio.py`, `tests/test_audio_analysis.py`, `tests/fixtures/media/main.mp4`.
- **Decisões:** MEDIA-01, DATA-01, OPS-01, PRIV-01 e CONTRACT-01 implementadas para análise de áudio.
- **Mensagem de commit:** `task(T4): add guarded audio analysis`

## Handoff

Fila após T4: T5, T6 e T7 são elegíveis; T8 a T11 continuam bloqueadas pelas dependências do plano. Em modo A, a próxima escolha deve ser feita entre essas três tasks.
