# Implement: instalação portátil Mac com atalhos compatíveis
# Alvo: phase-3-instalacao-portatil-mac
# Status: rascunho
# Route: medium · modo A · T4

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

## Handoff

próxima T* (T5)

- Chat: fila restante T5-T9 com T5|T7 elegíveis após este commit; recomendo T5 (menor n, desbloqueia atalhos e T9). Recomende um novo chat focado na próxima T*; continuidade no mesmo chat só por escolha consciente. O commit da task já foi criado; sem push nesta porta.
