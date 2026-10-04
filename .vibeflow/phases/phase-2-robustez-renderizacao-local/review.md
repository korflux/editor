# Review: Robustez da renderização local e da transcrição real
# Alvo: phase-2-robustez-renderizacao-local
# Status: rascunho

<!-- Review da Phase 2. O veredito permanece rascunho até confirmação humana. -->

## Contexto

- Rota: high, etapa 2 re-run após fila R1-R4.
- Alvo: `phase-2-robustez-renderizacao-local`.
- Cadeia: `spec.md` / `plan.md` / `analyze.md` / `implement.md` e este `review.md`. Sem `interview.md` (fase de correção técnica, sem ambiguidade de intenção).
- Diff auditado: `origin/main..HEAD` (11 commits, T1 a T7 mais R1 a R4). Working tree limpo; `git status --short --branch` mostra apenas `main...origin/main [ahead 11]`.
- O que muda: grafo FFmpeg por arquivo, perfil `max_full_width` sem upscale, validação separada de saída, normalização do Whisper, `fontfile` concreto, gate editorial da skill e jornada E2E detalhada.

## Cobertura

| Chave | Código | Notas |
|---|---|---|
| A1 | ok | T1/T7. `src/editor/render/preview.py:172-253` grava UTF-8 em `.tmp` e passa `-/filter_complex`; E2E com 105.925 caracteres prova transporte por arquivo. |
| A2 | ok | T1/T7. Linha de comando abaixo de 32.767 caracteres, sem rough cuts nem script lateral em `tests/e2e/test_phase_2_flow.py`. |
| A3 | ok | T1/T7. Limpeza de script e staging em sucesso, retorno não zero, exceção e timeout; `.tmp` limpo na E2E. |
| A4 | ok | T3/T7. `src/editor/media.py:366` `inspect_rendered_artifact()` sem teto de 4 GiB/3.600 s; fonte continua bloqueada por `inspect_source()`. |
| A5 | ok | T2/T7. `src/editor/plan/schema.py:23`, `validation.py:350-378`, `render/full.py:47`; padrão 1920, sem upscale, mutação invalida aprovação. |
| A6 | ok | T4/T7. `src/editor/analysis/transcription.py:114,184`; clamp até 0,5 s, texto reconstruído, contadores no envelope. |
| A7 | ok | T4. Desvio acima de 0,5 s e resultado vazio retornam `ANALYSIS_FAILED` sem substituir artefato vigente. |
| A8 | ok | T5. `validation.py:970-994` resolve arquivo concreto, `ffmpeg_graph.py:399` emite `fontfile`; Arial Bold renderiza sem `fontconfig.xml` lateral. |
| A9 | ok | T7. Preview e full consomem mesmo grafo, plano e fontes aprovados; hashes vigentes confirmados na E2E. |
| A10 | ok | T6. `skills/vibe-editor/SKILL.md` e `youtube-editing.md` bloqueiam aprovação antes de prova estrutural mais inspeção visual/editorial; `tests/test_vibe_editor_skill.py` com 4 testes. |
| C1 | ok | T1/T7. Filtro acima de 100.000 caracteres por arquivo, argumentos do processo curtos. |
| C2 | ok | T1/T3/T7. Falhas preservam preview/full vigentes e limpam `.tmp`. |
| C3 | ok | T3. Full simulada acima de 4 GiB passa na política de saída; fonte do mesmo tamanho retorna `MEDIA_LIMIT_EXCEEDED`. |
| C4 | ok | T2. Matriz de `max_full_width` (tipo, faixa 32..3840, múltiplo de 32) e compatibilidade v2 sem o campo. |
| C5 | ok | T4. Matriz cobre duração zero, clamp inicial/final, clamp que zera palavra, segmento esvaziado, desvio superior e vazio total. |
| C6 | ok | T4. `transcription.text` equivale à concatenação dos textos reconstruídos publicados. |
| C7 | ok | T5. HKLM, HKCU, ausente, extensão recusada e reparse point; grafo usa `fontfile`. |
| C8 | ok | T7 sem ressalva na etapa 2. E2E vigente passa; suíte completa `Ran 118 tests ... OK` reexecutada nesta review, sem erros do instalador. |

## Checklist de correções

<!-- Fila pedida pelo operador para os .erros-encontrados está em R1, R2 e R4. R3 é deriva documental da T1. -->

### Required

- [x] R1: **Required** - `tests/test_installation.py` + `scripts/install.ps1` - 5 testes quebravam com `UnicodeDecodeError` (bytes `0x82`, `0x87`, `0xA1`, `0xC6`) na thread de leitura do subprocesso via `pwsh`; causa raiz era saída do instalador na página de código legada mais decodificação estrita do harness - corrigido com `$OutputEncoding`/`[Console]::OutputEncoding` UTF-8 no instalador e `errors="replace"` nos 5 `subprocess.run` do teste, sem mudar contrato - prova: `.venv\Scripts\python.exe -B -m unittest tests.test_installation -v` -> `Ran 7 tests ... OK` - source: C8/diff - gap: fechado
- [x] R2: **Required** - `src/editor/**/__pycache__/*.pyc` + `.gitignore` - dezenas de `.pyc` (311 e 313) estavam versionados; causa raiz era `.gitignore` sem `__pycache__/` nem `*.pyc` - corrigido com `git rm --cached` dos 5 `__pycache__` e ampliação do `.gitignore`, sem apagar arquivos locais - prova: `git ls-files | Select-String pyc` -> `0` e `git check-ignore` confirma `__pycache__/` mais smoke `--help` verde - source: diff - gap: fechado

### Optional / Nit

- [x] R3: **Nit** - `.vibeflow/phases/phase-2-robustez-renderizacao-local/spec.md` citava `-filter_complex_script` no delta e na decisão 3, enquanto o código usa `-/filter_complex` no FFmpeg 9.0.1 - sincronizado para `-/filter_complex <arquivo>` com nota da opção legada indisponível; ocorrências restantes são histórico de decisão - prova: busca mostra `filter_complex_script` só em contexto histórico - source: A1/diff - gap: fechado
- [x] R4: **Optional** - `.erros-encontrados/2026-09-10-python-global-inquirer-editor.md` - conflito `inquirer 3.4.1` pede `editor>=1.6.0` contra editável local no Python global; verificado que o venv está limpo e o global persiste por colisão de nomes com o pacote PyPI `editor` (dependência do `inquirer`/`open-interpreter`), sem alteração no global ou rename - prova: venv `pip check` limpo, global ainda falha, registro atualizado com verificação de 2026-09-19 - source: `.erros-encontrados/` - gap: fechado

## Segurança

- `process.py:35-52,98` e `dependencies.py:70`, `validation.py:240` usam lista de argumentos com `shell=False`; nenhum `shell=True`, `os.system`, `eval` ou interpolação de caminho em shell no diff da fase.
- Fontes e plano validados na borda: allowlists de extensão, limites de tamanho e duração, rejeição de symlink/junction/reparse point, caminho de fonte nunca aceito do plano e nunca exposto no envelope público.
- Erros públicos usam envelope com código estável, sem stack trace nem stderr do FFmpeg; gitleaks limpo nesta revisão.
- Nada no que o diff tocou indica auth, pagamento, segredo ou dado pessoal.

## DoD

- [x] Smoke: `.venv\Scripts\python.exe -B -m editor --help` com exit 0.
- [x] Fatias rápidas reexecutadas: `tests.test_transcription` mais `tests.test_capabilities` mais `tests.test_vibe_editor_skill` -> `Ran 22 tests ... OK`.
- [x] `git diff --check` limpo; `gitleaks detect --source . --no-banner` -> `no leaks found`.
- [x] T1-T7 marcadas `[x]` em `plan.md` com aceite, verificação, prova e arquivos; `implement.md` declara fila zerada.
- [x] Suíte completa verde: `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` -> `Ran 118 tests in 47.179s ... OK` (etapa 2); E2E `tests.e2e.test_phase_2_flow` -> `Ran 1 test in 40.763s ... OK`.
- [x] `git status` limpo: apenas `main...origin/main [ahead 11]`, sem arquivos modificados; `git ls-files` com `pyc` -> `0`.

## Notas

- Fila de correção zerada na etapa 2: R1, R2, R3 e R4 permanecem `[x]` com provas reexecutadas abaixo.
- Push pendente: `main` está 11 commits à frente de `origin/main` (T1-T7 mais R1-R4); finalização Git só após Approve confirmado e sem `Co-Authored-By`.

## Veredito vigente

- [x] **Approve**: nenhum Critical/Required em `[ ]`
- [ ] **Request changes**: há Critical/Required em `[ ]`
- [ ] **Approve com defer**: <qual R* e por quê>

## Handoff

finalização Git da phase

- Nenhum R* bloqueante em `[ ]`; fila R1-R4 fechada e provada na etapa 2. O `review.md` vivo e o diff `origin/main..HEAD` são a ponte.
- Após confirmação humana, executar suíte final, `git diff --check` e gitleaks, commitar somente residuais autorizados e fazer `git push` sem `--force`.
- [ ] Aprovação humana (leu o arquivo e confirmou)

## Finalização Git da phase

- Pré-condições: Approve confirmado pelo humano, R1-R2 fechados, suíte completa verde, `git diff --check` e gitleaks verdes.
- Commit final: `chore(phase-2): finalize review`, somente com paths residuais autorizados e sem `Co-Authored-By`; sem residual, manter o último commit da task.
- Push: `git push` para o upstream atual, sem `--force`. Registrar hash/HEAD, paths e resultado; falha mantém o handoff bloqueado.

## Etapas

### Etapa 1 - first-pass - T1-T7, diff origin/main..HEAD, provas e gates

- Leu: `spec.md`, `plan.md`, `analyze.md`, `implement.md`, `.erros-encontrados/` (2 arquivos) e código/testes do fluxo real.
- Reexecutou: smoke `--help`, 22 testes rápidos, `test_installation` (confirma 5 erros preexistentes), `git diff --check`, gitleaks e `rg` de segurança/higiene.
- Abriu: R1, R2 (Required, deferidos), R3 (Nit), R4 (Optional).
- Fechou: nenhum.
- Veredito desta etapa: Approve com defer (R1, R2 na fila; R3, R4 não bloqueiam).

### Etapa 2 - re-run - T1-T7 mais R1-R4, diff origin/main..HEAD, provas verdes

- Leu: `spec.md`, `plan.md`, `analyze.md`, `implement.md`, `.erros-encontrados/` (2 arquivos) e código/testes do fluxo real. `rg` indisponível no host; usados `Get-ChildItem` e `Select-String` como equivalentes para inventário e busca de símbolos.
- Reexecutou: smoke `--help` (exit 0), `tests.test_installation` (`Ran 7 tests ... OK`), fatias rápidas `test_transcription` mais `test_capabilities` mais `test_vibe_editor_skill` (`Ran 22 tests ... OK`), E2E `test_phase_2_flow` (`Ran 1 test in 40.763s ... OK`), suíte completa `discover -s tests` (`Ran 118 tests in 47.179s ... OK`), `git diff --check` limpo, gitleaks (`no leaks found`), `pip_audit` (`No known vulnerabilities found`) e `quick_validate.py skills/vibe-editor` (`Skill is valid!`).
- Cobertura: A1-A10 e C1-C8 confirmados no código real (`preview.py:172-253` com `-/filter_complex`, `schema.py:23-27`, `validation.py:350-378,970-994`, `media.py:292-373`, `transcription.py:25,83-185`, `ffmpeg_graph.py:399`, `capabilities.py:261-332`); T1-T7 todas `[x]` em `plan.md`, sem stubs, `TODO` ou mocks falsos (`pass` restantes são criação de arquivo e limpeza best-effort).
- Segurança: `process.py:41,51,98` com `shell=False`; nenhum `shell=True`, `os.system` ou interpolação em shell no diff; fontes validadas na borda, caminho de fonte fora do envelope público, erros sem stack trace nem stderr do FFmpeg.
- Abriu: nenhum.
- Fechou: R1, R2, R3, R4 (todos já `[x]`; etapa 2 apenas revalidou com provas verdes, sem renumerar).
- Veredito desta etapa: Approve.
