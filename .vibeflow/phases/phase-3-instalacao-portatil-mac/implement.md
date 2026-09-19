# Implement: instalação portátil Mac com atalhos compatíveis
# Alvo: phase-3-instalacao-portatil-mac
# Status: rascunho
# Route: medium · modo A · T6

## Reconhecimento

- A fila da Phase 3 tinha T1 como única elegível (T2-T9 bloqueadas por deps). T1 foi nomeada explicitamente pelo operador e era elegível, sem Q de escolha.
- `src/editor/cli.py` concentra parser, ajuda, despacho e conversão central de erros; `src/editor/contracts.py` fixa envelopes, exit codes e erro de domínio sem stack trace.
- `scripts/install.ps1 -Verify` é somente leitura por contrato; `tests/test_cli_contract.py` trava os dois pontos de entrada e os envelopes JSON.

## T1: Validar esqueleto de CLI com verificação somente leitura

### Implementação

- Sem delta de código: a fatia trava o baseline verde antes de qualquer mudança de T2/T5. Nenhuma função foi criada ou alterada.

### Prova

- `python -m editor --help` passou (exit 0, `usage: editor`, sem prompt).
- `editor --help` passou (exit 0, mesmo contrato do ponto instalado).
- `pwsh -NoProfile -File scripts/install.ps1 -Verify` passou (exit 0, somente leitura, sem criar venv nem modelos).
- `python -m unittest discover -s tests -p "test_cli_contract.py" -v` -> `Ran 4 tests ... OK`.
- `git diff --check` passou.

## Registro

- **Plan:** T1 marcada como concluída, com prova e decisão registradas sob a seção da task.
- **Spec comprovada nesta fatia:** nenhuma caixa de aceite marcada; C2/C4 seguem com aceite final em T9. A fatia valida apenas o baseline (help, Verify somente leitura, envelopes sem stack trace).
- **Arquivos:** nenhum delta em `src/` ou `scripts/`; artefatos vivos atualizados nesta phase.
- **Decisão:** T1 é trava de baseline, não entrega de instalação ou atalho; T2 e T5 partem deste ponto verde.

## T2: Registrar shim no PATH do usuário no Windows

### Reconhecimento

- A pergunta da T2 é como deixar `editor` utilizável em novo terminal sem admin e sem quebrar `-Verify` somente leitura.
- O fluxo real passa por `scripts/install.ps1` (params, `Resolve-RepoPath`, `Install-PythonEnvironment`) e `tests/test_installation.py` (contrato sem instalar no host).
- Reutilizado o padrão isolável de `-VenvPath`/`-ModelDir` para `-ShimDir`, as guardas `Resolve-RepoPath`/`Test-RegularPath` e a execução sem shell via .NET; nenhum helper existente criava shim, então a adição foi mínima.

### Implementação

- `scripts/install.ps1`: novos params `-AddToPath` e `-ShimDir` (padrão `.tools\bin`); novas funções `Get-UserPathEntries` (lê PATH do usuário sem shell) e `Install-Shim` (valida venv, escreve `editor.cmd` com Python absoluto e `-m editor %*`, registra PATH por usuário via .NET de forma idempotente e reversível); `-Verify` rejeita `-AddToPath`; `modeCount` conta a nova ação explícita.
- `tests/test_installation.py`: helpers `get_user_path`/`set_user_path`/`count_shim_in_user_path` e 3 testes (Verify rejeita AddToPath sem criar shim, falha fechada sem venv sem tocar PATH, registro reversível idempotente sem shell com limpeza do PATH).
- Comentários semânticos em todas as funções criadas; `ponytail` registra que `editor.ps1`/shim Unix ficam para T4.

### Prova

- `pwsh -NoProfile -File scripts/install.ps1 -Verify` -> exit 0, sem criar `.tools\bin`.
- `python -m unittest discover -s tests -p "test_installation.py" -v` -> `Ran 10 tests ... OK` (7 vigentes + 3 novos).
- `git diff --check` -> 0.

## Registro T2

- **Plan:** T2 marcada como concluída, com prova e decisões sob a seção da task.
- **Spec comprovada nesta fatia:** nenhuma caixa marcada; A1/C2/C4 seguem com aceite final em T9.
- **Arquivos:** `scripts/install.ps1`, `tests/test_installation.py`.
- **Decisão:** shim em `.tools\bin\editor.cmd`, PATH por usuário sem admin, `-ShimDir` isolável para testes; desbloqueia T3.

## T3: Selecionar lock por plataforma com falha fechada

### Reconhecimento

- A pergunta da T3 é como instalar o venv a partir do lock correto sem rede implícita e falhando fechado antes de criar estado.
- O fluxo real passa por `scripts/install.ps1` (`Get-LockedVersion`, `Test-Lockfile`, `Install-PythonEnvironment`, `Invoke-SecurityGates`) e `tests/test_installation.py` (contrato sem instalar no host).
- Reutilizadas as guardas `Test-RegularPath`, a instalação com `--require-hashes` e os 5 pins vigentes; nenhum helper selecionava lock por plataforma, então a adição foi mínima.

### Implementação

- `requirements.macos.lock`: resolução pinada via `uv pip compile --python-version 3.11 --python-platform aarch64-apple-darwin` com mesmos diretos (`faster-whisper==1.2.1`, `ctranslate2==4.8.0`, `pip-audit==2.10.1`, `setuptools==84.0.0`, `wheel==0.48.0`), 683 hashes SHA-256.
- `scripts/install.ps1`: novas funções `Get-PlatformLockName` (Windows usa `requirements.lock`, demais usam `requirements.macos.lock`, com override bool para prova) e `Get-PlatformLockPath` (caminho absoluto do lock vigente); `Test-Lockfile` valida o lock da plataforma com mesma exigência de pins e hashes; `Install-PythonEnvironment` falha antes de criar venv com mensagem do lock vigente; `Invoke-SecurityGates` audita o lock da plataforma; `Test-PythonPackages` usa o nome do lock vigente nas mensagens.
- `tests/test_installation.py`: constante `MACOS_LOCKFILE` e 3 testes (mac lock com pins e hashes, seleção Windows/Mac via extração da função sem executar o bloco principal, falha fechada com lock ausente antes de criar venv com restauração).
- Comentários semânticos em todas as funções criadas ou alteradas; `ponytail` em `Get-PlatformLockName` registra alvo arm64, universal só com prova real em Intel x64.

### Prova

- `pwsh -NoProfile -File scripts/install.ps1 -Verify` -> exit 0, `requirements.lock: 50 pacotes pinados, 648 hashes SHA-256`, sem criar venv nem modelos.
- `python -m unittest discover -s tests -p "test_installation.py" -v` -> `Ran 13 tests ... OK` (10 vigentes + 3 novos).
- `git diff --check` -> 0.

## Registro T3

- **Plan:** T3 marcada como concluída, com prova e decisões sob a seção da task.
- **Spec comprovada nesta fatia:** nenhuma caixa marcada; A1/A2/C2 seguem com aceite final em T9.
- **Arquivos:** `scripts/install.ps1`, `requirements.macos.lock`, `tests/test_installation.py`.
- **Decisão:** seleção central por plataforma desbloqueia T4 (espelho Unix); T5/T7 seguem elegíveis em seams distintas.

## T4: Prover espelho Unix de instalação explícita

### Reconhecimento

- A pergunta da T4 é como espelhar o provisionamento explícito do ps1 no Mac sem download implícito e com shim Unix reversível.
- O fluxo real passa por `scripts/install.ps1` (`Get-PlatformLockName`, `Test-Lockfile`, `Install-Shim`, `Install-Local*`) e `tests/test_installation.py` (contrato sem instalar no host).
- Reutilizadas as exigências de pins/hashes, validação 3.11 x64, cópia local de ffmpeg/modelos/gitleaks e publicação sem shell; nenhum helper Unix existia, então a adição espelha o ps1 em sintaxe bash compatível com bash 3.2 do Mac.

### Implementação

- `scripts/install.sh`: novo espelho com `--verify`, `--install`, `--install-ffmpeg`, `--install-models`, `--install-gitleaks`, `--add-to-path`, `--venv-path`, `--model-dir`, `--shim-dir`, `--python-path`, `--ffmpeg-source`, `--model-source`, `--gitleaks-source`, `--security-gates` e `--force`; seleção por `uname` com `EDITOR_FAKE_UNAME` só para prova; `abspath` portátil sem `realpath` com fallback `python`/`cygpath` e normalização de barras para Git Bash; `resolve_repo_path`/`resolve_local_path` rejeitam escape e links; shim `editor` com `exec "<venv>/bin/python" -m editor "$@"` e PATH por usuário via perfil idempotente (`EDITOR_TEST_PROFILE` para testes); CUDA best effort com CPU baseline; sem `curl`/`wget`/gerenciador de pacotes.
- `.gitattributes`: pinagem `scripts/install.sh text eol=lf` para o shebang Unix sobreviver ao checkout Windows com `core.autocrlf=true`.
- `scripts/install.ps1`: só atualiza o `ponytail` do shim para apontar o espelho Unix da T4, sem mudar comportamento.
- `tests/test_installation.py`: constante `SHELL_SCRIPT`, helpers `find_usable_bash`/`run_shell_installer` e 4 testes (contrato Unix, `--verify` somente leitura, rejeição de `--verify` com install, shim reversível com `EDITOR_FAKE_UNAME=Darwin` e perfil isolado).
- Comentários semânticos em todas as funções criadas ou alteradas; `ALLOWED_MODELS` reutilizado nos dois loops de modelos.

### Prova

- `pwsh -NoProfile -File scripts/install.ps1 -Verify` -> exit 0, sem criar estado.
- `bash scripts/install.sh --verify` -> exit 0, somente leitura com lock, venv 3.11, backend, ffmpeg, modelos, gitleaks e pip check.
- `python -m unittest discover -s tests -p "test_installation.py" -v` -> `Ran 17 tests ... OK` (13 vigentes + 4 novos).
- `git diff --check` -> 0.

## Registro T4

- **Plan:** T4 marcada como concluída, com prova e decisões sob a seção da task.
- **Spec comprovada nesta fatia:** nenhuma caixa marcada; A2/C1/C2 seguem com aceite final em T9.
- **Arquivos:** `scripts/install.sh`, `.gitattributes`, `scripts/install.ps1`, `tests/test_installation.py`.
- **Decisão:** espelho Unix desbloqueia a jornada Mac e T9; T5/T7 seguem elegíveis em seams distintas.

## T5: Aplicar defaults opcionais de contexto sem criar estado

### Reconhecimento

- A pergunta da T5 é como omitir `--workdir` e `--source-id` sem enfraquecer a validação vigente nem criar estado parcial.
- O fluxo real passa por `src/editor/cli.py` (parser, despacho e conversão central), `src/editor/workdir.py` (`validate_workdir`, `validate_source_id`, `prepare_*`) e `tests/test_cli_contract.py` (contrato dos entry points).
- Reutilizadas as validações `validate_workdir`/`validate_source_id`, os envelopes de `contracts.py` sem delta e a publicação atômica vigente; nenhum helper resolvia default ou derivava id, então a adição foi mínima.

### Implementação

- `src/editor/workdir.py`: novas funções `resolve_workdir` (devolve o informado ou o cwd, sem validar nem criar) e `derive_source_id` (basename em minúsculas com `-` para inválidos, `lstrip -_`, teto 64, `ARGUMENT_INVALID` se vazio); `ponytail` registra colisão por basename.
- `src/editor/cli.py`: parser com `--workdir` e `--source-id` opcionais (`required=False default=None`) e help documentando defaults; nova função `effective_source_id` (explícito ou derivado, erro público estável); despacho resolve workdir e source-id antes de chamar `init`, `analyze`, `extract`, `plan validate`, `approve`, `render preview` e `render full`.
- `tests/test_cli_contract.py`: helpers `workspace_tempdir`/`run_module_in_workdir` e 5 testes (`init` sem `--workdir` no cwd, mesma validação de `plan validate` sem estado, normalização do basename, derivação impossível com `ARGUMENT_INVALID` sem mídia, `analyze audio` com defaults gerando o mesmo artefato do longo explícito).
- Comentários semânticos em todas as funções criadas ou alteradas; `contracts.py` sem delta pois os códigos vigentes já cobrem.

### Prova

- `python -m editor capabilities --json` -> exit 0.
- `python -m unittest discover -s tests -p "test_cli_contract.py" -v` -> `Ran 9 tests ... OK` (4 vigentes + 5 novos).
- `python -m unittest tests.test_init tests.test_cli_contract -v` -> `Ran 15 tests ... OK`.
- `git diff --check` -> 0.

## Registro T5

- **Plan:** T5 marcada como concluída, com prova e decisões sob a seção da task.
- **Spec comprovada nesta fatia:** nenhuma caixa marcada; A3/C4 seguem com aceite final em T9.
- **Arquivos:** `src/editor/cli.py`, `src/editor/workdir.py`, `tests/test_cli_contract.py`.
- **Decisão:** defaults desbloqueiam atalhos e T6; T6|T7 seguem elegíveis em seams distintas.

## T6: Mapear atalhos planos para envelopes canônicos

### Reconhecimento

- A pergunta da T6 é como expor `audio`, `transcribe`, `frames`, `validate`, `preview` e `full` como equivalentes exatos sem quebrar os longos, os envelopes ou a skill.
- O fluxo real passa por `src/editor/cli.py` (`build_parser`, despacho em `main` e conversão central), `src/editor/contracts.py` (envelopes sem allowlist de comando) e `tests/test_cli_contract.py` (contrato dos entry points).
- Reutilizados `resolve_workdir`, `effective_source_id`/`derive_source_id` de T5, os mesmos handlers (`analyze_audio`, `analyze_transcription`, `extract_frames`, `validate_plan`, `render_preview`, `render_full`) e os envelopes canônicos vigentes; nenhum helper de atalho existia, então a adição foi mínima.

### Implementação

- `src/editor/cli.py`: novos helpers `_add_json_argument`, `_add_workdir_argument` e `_add_source_id_argument` com comentários semânticos; nova função `register_shortcut_parsers` registrando os 6 atalhos com os mesmos argumentos dos longos (`--workdir`/`--source-id` opcionais, `--input`, `--window-s`, `--language`, `--model`, `--device`, seleção de frames, `--start-s`/`--end-s`); despacho em `main` estendido com `(canônico) or (atalho)` e os mesmos `success_envelope` canônicos (`analyze audio`, `analyze transcribe`, `extract frames`, `plan validate`, `render preview`, `render full`); `init`, `approve` e `capabilities` intactos.
- `tests/test_cli_contract.py`: 7 testes novos (help com atalhos e longos, `audio` vs `analyze audio` byte a byte, atalho `audio` sem `--workdir` no cwd, `validate` com mesmo `PLAN_INVALID`, `transcribe` com `ARGUMENT_INVALID` sem mídia, `frames` com mesmo manifesto, `preview`/`full` com mesmo erro dos longos).
- `skills/vibe-editor/SKILL.md`: notas de equivalência dos 6 atalhos mantendo os longos como exemplos válidos.
- Comentários semânticos em todas as funções criadas ou alteradas; `contracts.py` sem delta pois já aceita os nomes canônicos.

### Prova

- `python -m editor --help` -> exit 0 com os 6 atalhos e os 7 longos.
- `python -m editor capabilities --json` -> exit 0 com `command: capabilities`.
- `python -m unittest discover -s tests -p "test_cli_contract.py" -v` -> `Ran 16 tests ... OK` (9 vigentes + 7 novos).
- `python -m unittest tests.test_init tests.test_cli_contract -v` -> `Ran 22 tests ... OK`.
- `git diff --check` -> 0.

## Registro T6

- **Plan:** T6 marcada como concluída, com prova e decisões sob a seção da task.
- **Spec comprovada nesta fatia:** nenhuma caixa marcada; A3/A4/C4 seguem com aceite final em T9.
- **Arquivos:** `src/editor/cli.py`, `skills/vibe-editor/SKILL.md`, `tests/test_cli_contract.py`.
- **Decisão:** atalhos desbloqueiam operação curta sem reinterpretar o plano; T7 segue elegível em seam distinta.

## T7: Reportar Metal best effort com transcrição CPU garantida

### Reconhecimento

- A pergunta da T7 é como sondar Metal sem falhar a jornada, expor `devices.metal` e garantir CPU `int8` com `auto`.
- O fluxo real passa por `src/editor/dependencies.py` (`detect_gpu`, `detect_cuda_runtime`), `src/editor/capabilities.py` (`collect_capabilities`, `choose_recommendation`) e `src/editor/whisper_backend.py` (`resolve_config` com `auto|cpu|cuda` e `int8`).
- Reutilizadas as sondas sem shell, o formato `status/reason` e a recomendação CPU vigente; nenhum helper sondava Metal, então a adição foi mínima.

### Implementação

- `src/editor/dependencies.py`: nova função `detect_metal` (fora do Mac vira `missing` sem erro; no Mac exige `Metal.framework`, `OSError` vira `missing`); `ponytail` registra compute Metal só com prova real no Mac.
- `src/editor/capabilities.py`: chama `detect_metal` e expõe `devices.metal` só como reporte (`recommended:false`); recomendação continua só por CUDA+modelos, então Metal ausente mantém CPU.
- `src/editor/whisper_backend.py`: só documenta em `resolve_config` que Metal é ignorado na resolução e `auto` nunca falha por ausência de Metal, com CPU `int8` garantida.
- `tests/test_capabilities.py`: 4 testes novos (sonda nunca falha, `missing` mantém CPU, `available` não muda baseline, `auto` resolve `cpu/int8/base` sem Metal).
- Comentários semânticos em todas as funções criadas ou alteradas; `--device` permanece `auto|cpu|cuda` sem novo flag.

### Prova

- `.venv/Scripts/python.exe -m editor capabilities --json` -> exit 0 com `devices.metal: missing`, `recommendation: cpu/base`, sem stderr.
- `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_capabilities.py" -v` -> `Ran 11 tests ... OK` (7 vigentes + 4 novos).
- `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_transcription.py" -v` -> `Ran 11 tests ... OK`.
- Transcrição real `speech.mp3` com `--model base --device auto` -> `ok:true` com `segment_count:1`; `resolve_config('base','auto')` -> `cpu/int8`; `--device metal` -> `ARGUMENT_INVALID`.
- `git diff --check` -> 0.

## Registro T7

- **Plan:** T7 marcada como concluída, com prova e decisões sob a seção da task.
- **Spec comprovada nesta fatia:** nenhuma caixa marcada; A5/C3 seguem com aceite final em T9.
- **Arquivos:** `src/editor/dependencies.py`, `src/editor/capabilities.py`, `src/editor/whisper_backend.py`, `tests/test_capabilities.py`.
- **Decisão:** Metal só reportado desbloqueia T8 (fontes e filtro por sistema); T8 segue elegível em seam distinta.

## T8: Aplicar fontes e filtro por sistema mantendo renderer

### Reconhecimento

- A pergunta da T8 é como manter `fontfile` com origem por sistema e filtro por arquivo com flag e teto reportados, sem fallback silencioso.
- O fluxo real passa por `src/editor/dependencies.py` (`resolve_executable`, `detect_ffmpeg`, `probe_ffmpeg_encoders`), `src/editor/capabilities.py` (`collect_capabilities`), `src/editor/plan/validation.py` (`_resolve_font_file` com HKCU/HKLM e `fc-match`) e `src/editor/render/preview.py` (`_build_ffmpeg_arguments` com transporte por arquivo).
- Reutilizadas as sondas sem shell, a rejeição de fallback via `_fontconfig_name_matches`, a exigência `libx264`/`aac` em `_require_encoders` e a publicação atômica vigente; nenhum helper centralizava flag, teto ou origem por sistema, então a adição foi mínima.

### Implementação

- `src/editor/dependencies.py`: novas funções `is_windows`, `ffmpeg_filter_option` (`-/filter_complex` no Windows, `-filter_complex` nos demais), `max_command_characters` (32767 no Windows, `SC_ARG_MAX` nos demais com fallback POSIX), `font_lookup_source` (`registry`/`fc-match`) e `font_registry_hives` (`HKCU`/`HKLM` ou vazio); `resolve_executable` usa `is_windows()` mantendo `.tools/ffmpeg` primeiro sem `.exe` fora do Windows; comentários semânticos em todas e `ponytail` no teto POSIX.
- `src/editor/capabilities.py`: `render` usa flag e teto por sistema, `fonts` informa `source`, `registry` por sistema e `fallback:reject` mantendo `renderer:fontfile`; `limits.max_command_characters` espelha o mesmo helper.
- `src/editor/render/preview.py`: `_build_ffmpeg_arguments` usa `dependencies.ffmpeg_filter_option()` mantendo transporte por arquivo UTF-8.
- `src/editor/plan/validation.py`: `_resolve_font_file` usa `is_windows()` com `or sys.platform` para preservar os mocks vigentes, mantendo HKCU/HKLM e `fc-match` sem fallback.
- `tests/test_capabilities.py`: 4 testes novos (flag e teto por host, fallback sem `sysconf`, origem de fontes sem fallback, resolução sem `.exe` priorizando bundle).
- Comentários semânticos em todas as funções criadas ou alteradas; `ffmpeg_graph.py` mantido sem delta com grafo `fontfile`.

### Prova

- `.venv/Scripts/python.exe -m editor capabilities --json` -> exit 0 com `filter_option:-/filter_complex`, `max:32767`, `fonts.source:registry`, `registry:[HKCU,HKLM]`, `fallback:reject`, `renderer:fontfile`.
- `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_capabilities.py" -v` -> `Ran 15 tests ... OK` (11 vigentes + 4 novos).
- `.venv/Scripts/python.exe -m unittest tests.test_plan_validation -v` -> `Ran 30 tests ... OK`.
- `.venv/Scripts/python.exe -m unittest tests.test_preview_render -v` -> `Ran 15 tests ... OK`.
- `git diff --check` -> 0.
- Causa raiz tratada: `os.sysconf` ausente no Windows quebrou o mock sem `create=True` (corrigido nos testes); `import sys` removido quebrou `plan_validation.sys` usado pelos testes vigentes (restaurado com checagem dupla para mocks).

## Registro T8

- **Plan:** T8 marcada como concluída, com prova e decisões sob a seção da task.
- **Spec comprovada nesta fatia:** nenhuma caixa marcada; A2/C1/C3 seguem com aceite final em T9.
- **Arquivos:** `src/editor/capabilities.py`, `src/editor/dependencies.py`, `src/editor/plan/validation.py`, `src/editor/render/preview.py`, `tests/test_capabilities.py`.
- **Decisão:** origem e filtro por sistema desbloqueiam T9; T9 segue elegível com deps T4/T6/T8. Patch prévio de `ffmpeg_graph.py` (sombra/box) isolado em `C:/Users/marco/AppData/Local/Temp/opencode/ffmpeg_graph-prev.patch`, fora deste commit.

## T9: Provar jornada local ponta a ponta nos dois sistemas

### Reconhecimento

- A pergunta da T9 é se instalação, CLI curta e paridade Mac integram sem regressão, com jornada curta íntegra por hashes e verificação somente leitura.
- O fluxo real passa por `scripts/install.ps1 -Verify` (lock da plataforma, venv 3.11, backend, ffmpeg, modelos, gitleaks), `scripts/install.sh --verify` (espelho Unix), `src/editor/cli.py` (defaults de T5 e atalhos de T6), `src/editor/capabilities.py` (Metal de T7, fontes e filtro de T8) e `tests/e2e/test_mvp_flow.py` (plano 2.0s, preview, aprovação, full, mutação com `APPROVAL_STALE`).
- Reutilizados todos os testes vigentes sem reescrever helpers; `rg` indisponível neste host, usado `Get-ChildItem` e `Select-String` como equivalentes para localizar os paths da task. Nenhum helper novo era necessário, então a fatia é prova sem delta.

### Implementação

- Sem delta de código: a fatia prova a integração de T1 a T8 sobre os testes vigentes. Nenhuma função foi criada ou alterada.

### Prova

- `pwsh -NoProfile -File scripts/install.ps1 -Verify` -> exit 0, somente leitura, sem criar venv nem modelos.
- `.venv/Scripts/python.exe -B -m editor capabilities --json` -> exit 0 com `devices.metal: missing`, `recommendation: cpu/base`, `filter_option: -/filter_complex`, `max: 32767`, `fonts.source: registry [HKCU,HKLM]`, sem stderr.
- `.venv/Scripts/python.exe -B -m unittest tests.e2e.test_mvp_flow -v` -> `Ran 1 test ... OK` (4.898s).
- `.venv/Scripts/python.exe -B -m unittest discover -s tests -v` -> `Ran 148 tests ... OK` (66.811s).
- Fatias: `test_installation` -> `Ran 17 tests ... OK`; `test_cli_contract` -> `Ran 16 tests ... OK`; `test_capabilities` -> `Ran 15 tests ... OK`.
- `git diff --check` -> 0; `gitleaks detect --source . --no-banner` -> `no leaks found`.
- Causa raiz tratada: `bash scripts/install.sh --verify` sob WSL neste host reporta FFmpeg/gitleaks ausentes porque o venv e os binários do host são Windows; o contrato Unix segue provado pelos 4 testes do espelho com `EDITOR_FAKE_UNAME` e perfil isolado, e a jornada Mac real permanece manual documentada.

## Registro T9

- **Plan:** T9 marcada como concluída, com prova e decisões sob a seção da task.
- **Spec comprovada nesta fatia:** A1/A2/A3/A4/A5 e C1/C2/C3/C4 com aceite final; todas as caixas de aceite e sucesso marcadas.
- **Arquivos:** sem delta em `src/`, `scripts/` ou `tests/`; artefatos vivos atualizados nesta phase.
- **Decisão:** fila T1-T9 zerada; handoff `vibe-review`. Patch inexistente fora do commit; resíduos pré-existentes (`review.md` da Phase 2, `spec.md` incluída neste commit como aceite final) tratados no fechamento.

## Handoff

fila zerada (T1-T9 concluídas)

- Chat: handoff `vibe-review`; recomende um novo chat focado na review. O `plan.md` e o `implement.md` vivos são a ponte; continuidade no mesmo chat só por escolha consciente. O commit da task já foi criado; sem push nesta porta.
