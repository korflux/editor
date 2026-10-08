# Review: decodificação seletiva e cache de proxy
# Alvo: phase-4-decodificacao-seletiva-e-cache-de-proxy
# Status: aprovado

## Contexto

- Alvo: fase, diff `bf3eca2..HEAD` (commits `2bb3867` T1, `8b8d6d5` T2, `6ec46ba` T3, `77d32cb` T4, `1f4013f` T5).
- Cadeia: spec.md / plan.md (sem interview e sem analyze nesta phase).
- O que muda: preview e full passam a ler só as janelas usadas de cada fonte de vídeo (`-ss`/`-t` por entrada, tempos deslocados só no grafo), com fallback sem janelas; novo comando `proxy build` e `--proxy auto|off` na preview; `capabilities`, skill `vibe-editor` e `REGRAS.md` documentam as capacidades e as medições.

## Tipo de review

- Tipo: final.
- Marco e justificativa: integração final; T1 a T5 marcadas `[x]` no plan.md, com prova e commit por task.
- T* abertas fora do marco: nenhuma.
- Provas reaproveitadas: benchmarks de C1 (T2 janelas: CPU -27,9%, relógio -13,7%; T5 proxy: razão de relógio 0,186) e jornadas E2E (`test_mvp_flow`, `test_phase_1_flow`, `test_phase_2_flow`) da T5, `quick_validate.py`, gitleaks e pip-audit da T5. O HEAD é o commit da T5 e não há edição posterior de código, teste ou lockfile, então os inputs são os mesmos.
- Provas executadas: ver Etapa 1.

## Cobertura

| Chave | Código | Notas |
|---|---|---|
| A1 | ok | `render/windows.py`, `ffmpeg_graph.py` (`_GraphInputs.locate`), `tests/test_decode_windows.py` (`framemd5` de vídeo e áudio da preview e da full publicadas, com e sem janelas) |
| A2 | ok | 6 cenários de borda em `test_windowed_graph_decodes_the_same_frames_and_audio`; sonda da review com `start_time` 0,5 s e áudio atrasado 0,3 s também deu 0 linhas diferentes |
| A3 | ok | `_render_attempts` em `preview.py`; `assert_unchanged` (sem ganho e estouro de teto sem `-ss`); fallback janelado -> sem janelas com `windowed: false`; timeout com tentativa única |
| A4 | ok | `render/proxy.py` `build_proxies`/`lookup_proxy`; `test_journey_create_reuse_stale_and_rebuild` |
| A5 | ok | `_verify_proxy` antes de `publish_proxy_atomic`; cenários `STREAM_MISMATCH`, `FFMPEG_FAILED`, `TIMEOUT` e exceção preservam o proxy anterior e limpam `.tmp` |
| A6 | ok | `_select_proxies` + `read_paths` em `_render_artifact`; `tests/test_preview_proxy.py` |
| A7 | ok | `full.py` nunca passa `read_paths`; `--proxy` em `render full` dá `ARGUMENT_INVALID` (conferido na CLI); aprovação com mesmos hashes de plano e originais |
| A8 | ok | `capabilities.py` (`operations.proxy_build`, `render.decode_windows`, `render.proxy`), `skills/vibe-editor/SKILL.md`, `.vibeflow/REGRAS.md` |
| C1 | ok | medições registradas no `REGRAS.md` e no plan; critério de janelas ajustado pelo humano em 2026-10-07 |
| C2 | ok | discover 177 OK nesta review; E2E reaproveitadas da T5 |
| C3 | ok | sem ganho o grafo janelado é igual ao atual e só o grafo atual é tentado |
| C4 | partial | ids, caminhos, publicação atômica, sem shell e erros sem stderr conferidos; gitleaks limpo; pip-audit com 3 achados pré-existentes em `urllib3==2.7.0` (ver R1) |

## Checklist de correções

### Required

- [x] R1: **Required (defer aceito pelo humano em 2026-10-08; corrigido em 2026-10-08: `urllib3` 2.8.0 no lockfile, `pip_audit` sem achados)** - `requirements.lock` - `pip_audit` acusa PYSEC-2026-4175, 4176 e 4177 em `urllib3==2.7.0`, o que deixa C4 da spec parcial. O achado é anterior à phase (registrado em `.erros-encontrados/2026-10-04-urllib3-vulneravel-no-lockfile.md`, lockfile intocado desde `05671c8`) e o diff da phase não adiciona uso de rede. - remédio: `vibe-implement` em entrega própria, atualizar `urllib3` para versão corrigida (>= 2.8.0) compatível com a resolução, regenerar hashes do lockfile e repetir suíte, gitleaks e pip-audit. - prova: `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip` sem achados. - source: C4 - gap: partial

### Optional / Nit

- [x] R2: **FYI (corrigido: `tests/e2e/__init__.py`, o discover passa a rodar 180 testes)** - `tests/e2e/` - o `discover` não entra nas jornadas E2E (sem `__init__.py`); já registrado em `.erros-encontrados/2026-10-08-discover-nao-executa-e2e.md`. Não bloqueia: as três jornadas foram executadas à parte na T5.
- [x] R3: **Nit (corrigido: `PROXY_PRESET` exposto em `render.proxy.parameters`)** - `src/editor/capabilities.py` - `render.proxy.parameters` não expõe o preset `veryfast` usado em `_ffmpeg_arguments`; incluir se agentes precisarem reproduzir o proxy.

## Segurança

- `--source-id`: `validate_source_id` (allowlist `^[a-z0-9][a-z0-9_-]{0,63}$`) antes de carregar o plano e conferência de existência no plano em `_select_records`; `../x` devolve `ARGUMENT_INVALID` sem eco de caminho (conferido na CLI).
- `--proxy`: `choices` no parser e allowlist repetida no núcleo (`render_preview`); `editor proxy` sem operação devolve `ARGUMENT_INVALID`.
- Caminhos: `proxy_paths` deriva destino só do id validado dentro do trabalho validado; `lookup_proxy` e `_ensure_proxy_destinations` rejeitam links, reparse points e tipos especiais e tratam qualquer divergência como `stale`; metadado limitado a 4 KiB, chaves exatas e inteiros estritos.
- Processos: argumentos estruturados por `process.run_tool`, sem shell; `-ss`/`-t` formatados por `_number` (finito, 6 casas); timeouts proporcionais à duração.
- Publicação atômica: proxy troca o `.mp4` antes do `.json`, de modo que o intervalo lê como obsoleto; temporários em `.tmp` removidos em sucesso, erro, exceção e timeout (testado).
- Erros públicos: `RENDER_FAILED` e motivos por fonte estáveis, sem stderr do FFmpeg (teste com "falha simulada" e "erro interno").
- Integridade: full nunca recebe `read_paths`; aprovação continua por hash do plano, da preview e dos originais. Dependências: ver R1.

## Notas

- AAC com PNS diverge de forma inaudível após seek: aceito na decisão 4 e registrado como `ponytail` em `windows.py`.
- Proxy com metadados válidos e conteúdo corrompido falha a preview com `RENDER_FAILED` (`ponytail` em `_select_proxies`, contorno documentado).
- `CLAUDE.md` e `AGENTS.md` divergem do `REGRAS.md` por desenho; a replicação fica para a finalização aprovada.

## Veredito vigente

- [ ] **Approve**: nenhum Critical/Required em `[ ]`
- [ ] **Request changes**: há Critical/Required em `[ ]`
- [x] **Approve com defer**: R1 (urllib3 no lockfile) é pré-existente, fora do escopo da phase e não foi introduzido por ela; proposta de tratá-lo em entrega própria, sujeita à confirmação humana de aceitar C4 parcial.

## Decisões para vigência

| ID | Ação | Decisão aprovada | Fonte da decisão | Evidência de implementação |
|---|---|---|---|---|
| RENDER-01 | cria | Preview e full leem por padrão só as janelas usadas de cada fonte de vídeo, com fallback sem janelas; tempos do plano e hash canônico nunca mudam | spec.md decisões 1 a 6 | plan.md T1 e T2, `tests/test_decode_windows.py` |
| RENDER-02 | cria | Proxy só na preview, por `proxy build` explícito; full e aprovação usam sempre os originais e o proxy nunca entra em hash | spec.md decisões 10 e 11 | plan.md T3 e T4, `tests/test_preview_proxy.py` |

## Handoff

finalização Git da phase (após confirmação humana do defer de R1)

- [x] Aprovação humana (leu o arquivo e confirmou) em 2026-10-08: Approve com defer de R1, publicação de RENDER-01 e RENDER-02 e push autorizados.

- Chat: review executada em subagente de contexto limpo (Modo A). Com a confirmação humana, o coordenador executa a finalização Git; se o humano recusar o defer, o handoff é `vibe-implement` para R1. O `review.md` vivo e o diff são a ponte.

## Finalização Git da phase

- Pré-condições: Approve final confirmado, todas as `T*` concluídas, Critical/Required fechados ou defer aceito, prova da integração verde, `git diff --check` e gitleaks.
- Commit final: `chore(phase-4): finalize review`, somente com paths residuais autorizados e sem `Co-Authored-By`.
- Push: `git push` para o upstream atual, sem `--force`.
- Executada em 2026-10-08 pelo coordenador: `git diff --check` limpo, gitleaks sem vazamentos, suíte de 177 testes verde no HEAD `1f4013f` (sem mudança de código depois). Paths residuais: este `review.md`, `.vibeflow/REGRAS.md` (seção `## Decisões vigentes`, lida por `CLAUDE.md` e `AGENTS.md` via symlink) e `.erros-encontrados/2026-10-05-fontes-dji-hevc-rejeitadas.md`. O hash do commit final fica no Git (`git log --grep '^chore(phase-4)'`).

## Etapas

### Etapa 1 - first-pass - diff `bf3eca2..HEAD`, T1 a T5, provas

- Tipo: final.
- Marco e justificativa: integração final com a fila concluída.
- T* abertas fora do marco: nenhuma.
- Provas reaproveitadas: benchmarks C1 da T2 e T5, jornadas E2E, `quick_validate.py`, gitleaks e pip-audit da T5 (HEAD = commit da T5, sem edição posterior).
- Provas executadas:
  - `.venv\Scripts\python.exe -B -m unittest discover -s tests`, para confirmar a integração no HEAD: Ran 177 tests, OK (135,6 s).
  - Sonda da review (script no scratchpad, não versionado) com `decoded_framemd5` dos testes, fonte com `start_time` 0,5 s e fonte com áudio atrasado 0,3 s, plano com 3 trechos e trilha J: 444 linhas de `framemd5`, 0 diferentes nos dois casos; motivo: cobrir fontes de câmera com início deslocado, ausentes dos cenários do teste.
  - CLI: `editor proxy --json`, `editor render full --proxy off --json` e `editor proxy build --source-id "../x" --json`: os três devolvem `ARGUMENT_INVALID` (exit 2) com mensagem segura.
  - `git diff --check bf3eca2..HEAD`: limpo.
- Leu: plan.md sim.
- Abriu: R1 (Required, defer proposto), R2 (FYI), R3 (Nit).
- Fechou: nenhum.
- Veredito desta etapa: Approve com defer.

### Etapa 2 - correção pós-aprovação dos R1 a R3, a pedido do humano (2026-10-08)

- Tipo: correção (sem nova review delegada; conferida pelo coordenador contra o diff e a prova).
- R1: só o bloco `urllib3` do `requirements.lock` mudou (2.7.0 para 2.8.0, hashes do PyPI); instalação com `--require-hashes` verde, `pip_audit` sem achados, `pip check` limpo.
- R2: `tests/e2e/__init__.py` adicionado; `unittest discover -s tests` passa de 177 para 180 testes, todos OK, incluindo as três jornadas E2E.
- R3: constante `PROXY_PRESET` usada no encode e exposta em `capabilities`; argumentos do FFmpeg idênticos, `PROXY_PARAMETERS_VERSION` inalterada.
- Registros resolvidos removidos de `.erros-encontrados/` (urllib3 e discover sem E2E). Gitleaks do histórico sem achados; varredura do working tree sem achados fora de `.venv/`.
- Veredito: Approve final, sem R* abertos.
