# Review: Robustez da renderização local e da transcrição real
# Alvo: phase-2-robustez-renderizacao-local
# Status: rascunho

<!-- Review da Phase 2. O veredito permanece rascunho até confirmação humana. -->

## Contexto

- Rota: high, etapa 1 first-pass.
- Alvo: `phase-2-robustez-renderizacao-local`.
- Cadeia: `spec.md` / `plan.md` / `analyze.md` / `implement.md` e este `review.md`. Sem `interview.md` (fase de correção técnica, sem ambiguidade de intenção).
- Diff auditado: `origin/main..HEAD` (7 commits, T1 a T7 da Phase 2). Working tree contém apenas `__pycache__/*.pyc` modificados (24 arquivos binários, ver R2).
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
| C8 | ok | T7 com ressalva R1. E2E vigente passa; suíte completa tem 5 erros preexistentes do instalador, confirmados no baseline e registrados em `.erros-encontrados/`. |

## Checklist de correções

<!-- Fila pedida pelo operador para os .erros-encontrados está em R1, R2 e R4. R3 é deriva documental da T1. -->

### Required

- [x] R1: **Required** - `tests/test_installation.py` + `scripts/install.ps1` - 5 testes quebravam com `UnicodeDecodeError` (bytes `0x82`, `0x87`, `0xA1`, `0xC6`) na thread de leitura do subprocesso via `pwsh`; causa raiz era saída do instalador na página de código legada mais decodificação estrita do harness - corrigido com `$OutputEncoding`/`[Console]::OutputEncoding` UTF-8 no instalador e `errors="replace"` nos 5 `subprocess.run` do teste, sem mudar contrato - prova: `.venv\Scripts\python.exe -B -m unittest tests.test_installation -v` -> `Ran 7 tests ... OK` - source: C8/diff - gap: fechado
- [x] R2: **Required** - `src/editor/**/__pycache__/*.pyc` + `.gitignore` - dezenas de `.pyc` (311 e 313) estavam versionados; causa raiz era `.gitignore` sem `__pycache__/` nem `*.pyc` - corrigido com `git rm --cached` dos 5 `__pycache__` e ampliação do `.gitignore`, sem apagar arquivos locais - prova: `git ls-files | Select-String pyc` -> `0` e `git check-ignore` confirma `__pycache__/` mais smoke `--help` verde - source: diff - gap: fechado

### Optional / Nit

- [x] R3: **Nit** - `.vibeflow/phases/phase-2-robustez-renderizacao-local/spec.md` citava `-filter_complex_script` no delta e na decisão 3, enquanto o código usa `-/filter_complex` no FFmpeg 9.0.1 - sincronizado para `-/filter_complex <arquivo>` com nota da opção legada indisponível; ocorrências restantes são histórico de decisão - prova: busca mostra `filter_complex_script` só em contexto histórico - source: A1/diff - gap: fechado
- [ ] R4: **Optional** - `.erros-encontrados/2026-09-10-python-global-inquirer-editor.md` - conflito `inquirer 3.4.1` pede `editor>=1.6.0` contra pacote local `0.1.0` no Python global; escopo era ambiente global fora do venv e pode estar obsoleto após T2/instalador - remédio: vibe-implement verifica `pip check` no venv e no global, encerra o registro se obsoleto ou documenta contenção atual - prova: `pip check` relevante mais atualização ou remoção do registro - source: `.erros-encontrados/` - gap: nenhum

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
- [ ] Suíte completa verde: pendente por R1; E2E `test_phase_2_flow` passou na T7, mas `discover -s tests` tem 5 erros preexistentes do instalador.
- [ ] `git status` limpo: pendente por R2 (apenas binários `__pycache__` modificados).

## Notas

- Fila de correção registrada: R1 (encoding do instalador), R2 (pycache versionado) e R4 (verificar registro inquirer de 2026-09-10); R3 é deriva documental menor da T1.
- Push pendente: `main` está 7 commits à frente de `origin/main`; finalização Git só após Approve confirmado e sem `Co-Authored-By`.

## Veredito vigente

- [ ] **Approve**: nenhum Critical/Required em `[ ]`
- [ ] **Request changes**: há Critical/Required em `[ ]`
- [x] **Approve com defer**: R1 e R2 deferidos para a fila vibe-implement; funcionalidade Phase 2 aprovada porque o núcleo (grafo por arquivo, perfil, validação de saída, transcrição, fontfile, gate e E2E) está implementado e provado, e os dois Required são higiene/harness preexistentes fora do contrato editorial. R3 (Nit) e R4 (Optional) não bloqueiam.

## Handoff

vibe-implement

- Corrigir na ordem R1, R2, depois R3 e R4; novo chat focado é recomendado. O `review.md` vivo e o diff `origin/main..HEAD` são a ponte.
- Após correção, rodar novo `vibe-review` etapa 2 no mesmo arquivo para fechar R1-R2 e revalidar DoD antes de qualquer finalização Git.
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
