# Plan: instalação portátil Mac com atalhos compatíveis
# Alvo: phase-3-instalacao-portatil-mac
# Status: aprovado
# Spec: spec.md (mesma pasta)

## Overview

Entregar instalação por comando único no Windows e no Mac com `editor` no PATH, comandos curtos compatíveis com envelopes canônicos e paridade de contrato no Mac com CPU garantida, conforme `spec.md` desta fase.

## Ordem

### Fase 1: Instalação portátil e PATH

- T1 valida esqueleto e leitura, T2 registra shim no Windows, T3 seleciona lock por plataforma, T4 provê espelho Unix (cada task com verificação e commit próprios)

### Fase 2: Contrato CLI e paridade Mac

- T5 aplica defaults opcionais, T6 mapeia atalhos canônicos, T7 reporta Metal com CPU garantida, T8 aplica fontes e filtro por sistema, T9 prova jornada ponta a ponta (cada task com verificação e commit próprios)

## Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| PATH do usuário diverge entre hosts | médio | shim por usuário reversível sem admin, `Verify` somente leitura, teste de shim e orientação de novo terminal |
| Lock macOS diverge do Windows | médio | hashes obrigatórios nos dois locks, falha fechada antes de criar venv, teste de lock por plataforma |
| Prova real no Mac indisponível neste host Windows | médio | mocks de plataforma nos testes, jornada Mac documentada como manual, sem transformar ausência em passe |
| Python local 3.13 difere do exigido 3.11 | médio | `-Install` mantém exigência 3.11 x64 e falha fechada, `-Verify` somente leitura, CI e jornada usam venv 3.11 |
| Mudança na CLI quebra agentes e skill | alto | envelopes canônicos byte a byte, longos permanecem válidos, testes de contrato travam help e envelope |

## Paralelização

- Paralelo ok: T2 e T5 após T1 verde partem de seams distintos (instalador Windows e parser CLI)
- Sequencial: T2 → T3 → T4 na mesma seam de instalação; T5 → T6 no mesmo parser; T7 → T8 no mesmo reporte de capabilities
- Contrato primeiro, depois paralelo: T1 estabelece esqueleto verde antes de qualquer delta; T9 por último integra instalação, CLI e paridade

## Tasks

### T1: Validar esqueleto de CLI com verificação somente leitura

- [x] T1 concluída
- **Spec:** C2, C4
- **O quê:** Travar o baseline verde com ponto de entrada real e verificação somente leitura, sem alterar comportamento.
- **Aceite:**
  - [x] `python -m editor --help` e `editor --help` executam de forma não interativa
  - [x] `scripts/install.ps1 -Verify` não cria venv nem modelos
  - [x] Contrato JSON de sucesso e erro permanece sem stack trace
- **Verificação:**
  - [x] `python -m editor --help`
  - [x] `pwsh -NoProfile -File scripts/install.ps1 -Verify`
  - [x] `python -m unittest discover -s tests -p "test_cli_contract.py" -v`
- **Prova:** `python -m editor --help` -> exit 0 com `usage: editor`; `editor --help` -> exit 0; `scripts/install.ps1 -Verify` -> exit 0 somente leitura; `discover -s tests -p "test_cli_contract.py" -v` -> `Ran 4 tests ... OK`; `git diff --check` -> 0.
- **Decisões:** sem delta de código nesta fatia; baseline travado antes de T2/T5. C2/C4 seguem com aceite final em T9.
- **Deps:** nenhuma
- **Arquivos:** `src/editor/cli.py`, `src/editor/contracts.py`, `scripts/install.ps1`, `tests/test_cli_contract.py`
- **Size:** `low, 2/10 (1+0+1+0+0), leitura em duas seams sem delta`
- **Risk:** `low`

### T2: Registrar shim no PATH do usuário no Windows

- [x] T2 concluída
- **Spec:** A1, C2, C4
- **O quê:** Adicionar `-AddToPath` em `scripts/install.ps1` com shim por usuário reversível que chama o Python do venv por caminho absoluto com `-m editor`.
- **Aceite:**
  - [x] Clone novo no Windows deixa `editor` utilizável em novo terminal após `-Install -AddToPath`
  - [x] Registro reversível por usuário sem admin e sem mudar semântica de `-Verify` e `-Install`
  - [x] Shim sem shell intermediário e sem montar comando a partir de entrada externa
- **Verificação:**
  - [x] `pwsh -NoProfile -File scripts/install.ps1 -Verify`
  - [x] `python -m unittest discover -s tests -p "test_installation.py" -v`
- **Prova:** `scripts/install.ps1 -Verify` -> exit 0 sem criar `.tools\bin`; `discover -s tests -p "test_installation.py" -v` -> `Ran 10 tests ... OK` (7 vigentes + 3 novos de shim); `git diff --check` -> 0.
- **Decisões:** shim padrão em `.tools\bin\editor.cmd` com Python do venv por caminho absoluto; PATH por usuário via .NET sem admin e idempotente; `-Verify` rejeita `-AddToPath`; `-ShimDir` segue o padrão isolável de `-VenvPath`/`-ModelDir`; A1/C2/C4 seguem com aceite final em T9.
- **Deps:** T1
- **Arquivos:** `scripts/install.ps1`, `tests/test_installation.py`
- **Size:** `medium, 5/10 (1+1+1+1+1), shim Windows com PATH reversível sobre contrato vigente`
- **Risk:** `low`

### T3: Selecionar lock por plataforma com falha fechada

- [x] T3 concluída
- **Spec:** A1, A2, C2
- **O quê:** Criar `requirements.macos.lock` pinado com hashes e selecionar lock por plataforma antes de criar o venv, com falha fechada em lock ausente ou sem hashes.
- **Aceite:**
  - [x] Lock ausente ou sem hashes falha antes de criar venv nos dois sistemas
  - [x] Windows usa `requirements.lock`, Mac usa `requirements.macos.lock` com mesma exigência
  - [x] Instalação do venv usa `--require-hashes` sem rede implícita
- **Verificação:**
  - [x] `pwsh -NoProfile -File scripts/install.ps1 -Verify`
  - [x] `python -m unittest discover -s tests -p "test_installation.py" -v`
- **Prova:** `scripts/install.ps1 -Verify` -> exit 0 com `requirements.lock: 50 pacotes pinados, 648 hashes SHA-256`; `discover -s tests -p "test_installation.py" -v` -> `Ran 13 tests ... OK` (10 vigentes + 3 novos de lock por plataforma); `git diff --check` -> 0.
- **Decisões:** seleção central em `Get-PlatformLockName` (`requirements.lock` no Windows, `requirements.macos.lock` fora dele) com `Get-PlatformLockPath` para o SCA; `Test-Lockfile` valida o lock da plataforma com mesma exigência de pins e hashes; `Install-PythonEnvironment` falha antes de criar venv com mensagem do lock vigente; mac lock resolvido via `uv pip compile --python-platform aarch64-apple-darwin` com mesmos diretos (`faster-whisper==1.2.1`, `ctranslate2==4.8.0`, `pip-audit==2.10.1`, `setuptools==84.0.0`, `wheel==0.48.0`); `ponytail` em `Get-PlatformLockName` registra alvo arm64, universal só com prova real em Intel x64; A1/A2/C2 seguem com aceite final em T9.
- **Deps:** T2
- **Arquivos:** `scripts/install.ps1`, `requirements.macos.lock`, `tests/test_installation.py`
- **Size:** `medium, 4/10 (1+1+1+0+1), seleção de lock sobre instalador vigente`
- **Risk:** `medium, contrato compartilhado de instalação nas duas plataformas`

### T4: Prover espelho Unix de instalação explícita

- [x] T4 concluída
- **Spec:** A2, C1, C2
- **O quê:** Criar `scripts/install.sh` com `--verify`, `--install`, `--add-to-path`, `--venv-path` e `--model-dir`, espelhando provisionamento explícito e leitura do `ps1`.
- **Aceite:**
  - [x] `bash scripts/install.sh --verify` somente leitura sem criar venv nem modelos
  - [x] Origens locais de ffmpeg, modelos e gitleaks seguem semântica explícita sem download implícito
  - [x] Shim Unix no PATH por usuário com Python do venv por caminho absoluto
- **Verificação:**
  - [x] `pwsh -NoProfile -File scripts/install.ps1 -Verify`
  - [x] `python -m unittest discover -s tests -p "test_installation.py" -v`
  - [x] `bash scripts/install.sh --verify`
- **Prova:** `scripts/install.ps1 -Verify` -> exit 0 sem criar estado; `discover -s tests -p "test_installation.py" -v` -> `Ran 17 tests ... OK` (13 vigentes + 4 novos); `bash scripts/install.sh --verify` -> exit 0 somente leitura com lock, venv 3.11, backend, ffmpeg, modelos, gitleaks e pip check; `git diff --check` -> 0.
- **Decisões:** espelho bash com mesma semântica explícita do ps1 e sem rede para binários/pesos; lock por plataforma via `uname` (`requirements.lock` no Windows/MSYS, `requirements.macos.lock` fora) com `EDITOR_FAKE_UNAME` só para prova isolada; shim `editor` com `exec "<venv>/bin/python" -m editor "$@"` e PATH por usuário reversível via perfil (`EDITOR_TEST_PROFILE` para testes); `.gitattributes` pinando `scripts/install.sh` em LF para o shebang sobreviver ao checkout Windows; CUDA best effort com CPU baseline e `ponytail` arm64 herdado de T3; `install.ps1` só atualiza o `ponytail` para o espelho T4; lock do Mac reutilizado sem delta; A2/C1/C2 seguem com aceite final em T9.
- **Deps:** T3
- **Arquivos:** `scripts/install.sh`, `.gitattributes`, `scripts/install.ps1`, `tests/test_installation.py`
- **Size:** `medium, 6/10 (1+1+2+1+1), novo instalador com prova Mac externa ao host`
- **Risk:** `medium, novo caminho de instalação com regressão possível`

### T5: Aplicar defaults opcionais de contexto sem criar estado

- [x] T5 concluída
- **Spec:** A3, C4
- **O quê:** Tornar `--workdir` opcional com default para diretório atual absoluto e `--source-id` opcional derivado do basename do `--input`, mantendo validação vigente e sem criar estado em caminho inválido.
- **Aceite:**
  - [x] Dentro do trabalho, comandos longos executam sem `--workdir` com mesma validação e `WORKDIR_INVALID` sem criar estado
  - [x] `--source-id` omitido deriva para `^[a-z0-9][a-z0-9_-]{0,63}$`, derivação impossível retorna `ARGUMENT_INVALID`
  - [x] Ajuda, envelopes, códigos e artefatos dos longos permanecem byte a byte
- **Verificação:**
  - [x] `python -m editor capabilities --json`
  - [x] `python -m unittest discover -s tests -p "test_cli_contract.py" -v`
- **Prova:** `capabilities --json` -> exit 0; `discover -s tests -p "test_cli_contract.py" -v` -> `Ran 9 tests ... OK` (4 vigentes + 5 novos); `unittest tests.test_init tests.test_cli_contract -v` -> `Ran 15 tests ... OK`; `git diff --check` -> 0.
- **Decisões:** `resolve_workdir` devolve cwd quando omitido sem validar nem criar; `derive_source_id` normaliza basename para minúsculas com `-` para inválidos, `lstrip -_` e teto 64, `ARGUMENT_INVALID` se vazio; `effective_source_id` centraliza a seleção na borda; parser com `required=False default=None` e help documentando defaults; `contracts.py` sem delta pois `WORKDIR_INVALID`/`ARGUMENT_INVALID` já cobrem; `ponytail` registra colisão por basename, exigir `--source-id` explícito com nomes iguais; A3/C4 seguem com aceite final em T9.
- **Deps:** T1
- **Arquivos:** `src/editor/cli.py`, `src/editor/workdir.py`, `tests/test_cli_contract.py`
- **Size:** `medium, 4/10 (1+1+1+0+1), defaults sobre parser e validadores vigentes`
- **Risk:** `medium, contrato CLI compartilhado com agentes`

### T6: Mapear atalhos planos para envelopes canônicos

- [x] T6 concluída
- **Spec:** A3, A4, C4
- **O quê:** Adicionar `audio`, `transcribe`, `frames`, `validate`, `preview` e `full` como equivalentes exatos aos canônicos, com envelopes usando nomes canônicos e longos válidos em help e execução.
- **Aceite:**
  - [x] Atalhos executam sem `--workdir` e geram mesmos artefatos dos longos
  - [x] Envelopes usam `analyze audio`, `plan validate`, `render preview` e demais nomes canônicos
  - [x] `init`, `approve` e `capabilities` permanecem inalterados
- **Verificação:**
  - [x] `python -m unittest discover -s tests -p "test_cli_contract.py" -v`
  - [x] `python -m editor capabilities --json`
- **Prova:** `discover -s tests -p "test_cli_contract.py" -v` -> `Ran 16 tests ... OK` (9 vigentes + 7 novos); `unittest tests.test_init tests.test_cli_contract -v` -> `Ran 22 tests ... OK`; `capabilities --json` -> exit 0 com `command: capabilities`; `audio` vs `analyze audio` com mesma fixture gerou artefatos byte a byte com `command: analyze audio`; `validate` vs `plan validate` com mesmo `PLAN_INVALID`; `git diff --check` -> 0.
- **Decisões:** `register_shortcut_parsers` com helpers `_add_workdir_argument`/`_add_source_id_argument`/`_add_json_argument` espelhando os longos; despacho estendido em `main` para os 6 atalhos com os mesmos handlers e envelopes canônicos; `contracts.py` sem delta pois já aceita nomes canônicos; skill documenta equivalências mantendo os longos como exemplos; A3/A4/C4 seguem com aceite final em T9.
- **Deps:** T5
- **Arquivos:** `src/editor/cli.py`, `skills/vibe-editor/SKILL.md`, `tests/test_cli_contract.py`
- **Size:** `medium, 4/10 (1+1+1+0+1), mapeamento sobre resolução de T5`
- **Risk:** `medium, quebra de help ou envelope afeta testes vigentes`

### T7: Reportar Metal best effort com transcrição CPU garantida

- [x] T7 concluída
- **Spec:** A5, C3
- **O quê:** Sondar Metal sem tratar ausência como erro, expor `devices.metal` em `capabilities` e garantir transcrição CPU `int8` com `auto` sem falha por ausência de Metal.
- **Aceite:**
  - [x] `capabilities` informa `devices.metal` sem erro e recomenda CPU quando Metal ausente
  - [x] Transcrição real com modelo `base` local publica artefato em CPU `int8`
  - [x] `--device` permanece `auto|cpu|cuda` nesta fatia sem novo flag obrigatório
- **Verificação:**
  - [x] `python -m editor capabilities --json`
  - [x] `python -m unittest discover -s tests -p "test_capabilities.py" -v`
- **Prova:** `capabilities --json` -> `devices.metal: missing` sem erro com `recommendation: cpu/base`; `discover -s tests -p "test_capabilities.py" -v` -> `Ran 11 tests ... OK` (7 vigentes + 4 novos); `discover -s tests -p "test_transcription.py" -v` -> `Ran 11 tests ... OK`; transcrição real `speech.mp3` com `--model base --device auto` -> `ok:true` com `segment_count:1`; `resolve_config('base','auto')` -> `cpu/int8`; `--device metal` -> `ARGUMENT_INVALID`; `git diff --check` -> 0.
- **Decisões:** `detect_metal` best effort só com `available` no Mac com framework, senão `missing` sem erro; `devices.metal` só reporte com `recommended:false`, compute permanece CPU `int8`/CUDA `float16`; `resolve_config` ignora Metal e garante `auto` sem falha; `ponytail` em `detect_metal` registra compute Metal só com prova real no Mac; A5/C3 seguem com aceite final em T9.
- **Deps:** T1
- **Arquivos:** `src/editor/dependencies.py`, `src/editor/capabilities.py`, `src/editor/whisper_backend.py`, `tests/test_capabilities.py`
- **Size:** `medium, 5/10 (2+1+1+1+0), três módulos com sonda best effort`
- **Risk:** `low`

### T8: Aplicar fontes e filtro por sistema mantendo renderer

- [ ] T8 concluída
- **Spec:** A2, C1, C3
- **O quê:** Manter renderer `fontfile` com origem por sistema e transporte de filtro por arquivo com flag e teto reportados em `capabilities`, sem fallback silencioso de família.
- **Aceite:**
  - [ ] Windows mantém HKCU e HKLM, demais sistemas usam `fc-match` e rejeitam fallback
  - [ ] Windows mantém `-/filter_complex` com teto 32767, demais usam `-filter_complex` com limite do sistema reportado
  - [ ] Resolução sem `.exe` no Mac prioriza `.tools/ffmpeg` e exige `libx264` e `aac`
- **Verificação:**
  - [ ] `python -m editor capabilities --json`
  - [ ] `python -m unittest discover -s tests -p "test_capabilities.py" -v`
- **Deps:** T7
- **Arquivos:** `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/capabilities.py`, `src/editor/dependencies.py`, `tests/test_capabilities.py`
- **Size:** `medium, 6/10 (2+1+1+1+1), quatro módulos com variação por sistema`
- **Risk:** `medium, render e fontes compartilhados com preview e full`

### T9: Provar jornada local ponta a ponta nos dois sistemas

- [ ] T9 concluída
- **Spec:** A1, A2, A3, A4, A5, C1, C2, C3, C4
- **O quê:** Executar jornada curta com plano, preview, aprovação e full nos dois sistemas, com transcrição `base` local e verificação somente leitura.
- **Aceite:**
  - [ ] Suíte completa verde no Windows e jornada curta com preview, aprovação e full íntegras por hashes
  - [ ] Verificação somente leitura nos dois sistemas sem criar venv nem modelos, com `capabilities` sem erro no Mac sem Metal
  - [ ] Nenhum teste vigente de contrato muda por quebra de help ou envelope
- **Verificação:**
  - [ ] `pwsh -NoProfile -File scripts/install.ps1 -Verify`
  - [ ] `python -m editor capabilities --json`
  - [ ] `python -m unittest discover -s tests -v`
- **Deps:** T4, T6, T8
- **Arquivos:** `tests/e2e/test_mvp_flow.py`, `tests/fixtures/e2e/plan.json`, `tests/test_installation.py`, `tests/test_cli_contract.py`, `tests/test_capabilities.py`
- **Size:** `high, 7/10 (2+1+2+0+2), integra instalação, CLI e paridade numa prova única; sem ela as fatias isoladas não provam A1 a A5`
- **Risk:** `medium, regressão relevante de jornada`

## Conferência

- [x] Spec aprovada como fonte; mesma pasta
- [x] Fatias verticais; score de Size ≤ 8; Risk separado
- [x] Toda T* tem aceite + verificação + Spec: A*/C* quando couber
- [x] Ferramentas essenciais (gitleaks, chrome-devtools) validadas ou com task de setup
- [x] T1 estabelece ponto de entrada real (smoke test / walking skeleton)
- [x] UI greenfield/sem DS → T* de kit antes das telas (ou N/A)
- [x] Cada T* possui unidade de execução, verificação e commit própria
- [ ] Aprovação humana (leu o arquivo e confirmou)

## Handoff

vibe-implement

- Chat: recomende novo chat para `vibe-implement`; continuidade no mesmo chat só por escolha consciente. O `plan.md` vivo é a ponte.
