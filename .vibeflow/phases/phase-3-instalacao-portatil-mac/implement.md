# Implement: instalação portátil Mac com atalhos compatíveis
# Alvo: phase-3-instalacao-portatil-mac
# Status: rascunho
# Route: medium · modo A · T2

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

## Handoff

próxima T* (T3)

- Chat: fila restante T3-T9 com T3|T5|T7 elegíveis após este commit; recomendo T3 (menor n, desbloqueia T4). Recomende um novo chat focado na próxima T*; continuidade no mesmo chat só por escolha consciente. O commit da task já foi criado; sem push nesta porta.
