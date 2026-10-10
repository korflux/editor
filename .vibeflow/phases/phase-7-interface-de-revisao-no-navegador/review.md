# Review: interface de revisão no navegador com player ao vivo
# Alvo: phase-7-interface-de-revisao-no-navegador
# Status: rascunho

## Contexto

- Alvo: phase 7, commits `4885810..e39847a` (T1 `4b94d56`, T2 `fffcba2`, T3 `64b4eb7`, T4 `b6fc7f8`, T5 `85e613d`, T6 `5a2e308`, T7 `e39847a`).
- Cadeia: interview.md / spec.md / design.md / plan.md / analyze.md (rota max, todos aprovados).
- O que muda: servidor HTTP local (`editor ui start|stop`) com sessão por token, player ao vivo pelo mesmo grafo da full, timeline, modo Fonte, cópia de trecho para a IA e aprovação por botão; aprovação v2 sem `preview.mp4` e CLI 2.0.0.

## Tipo de review

- Tipo: final (fila T1 a T7 concluída, sem checkpoint pendente).
- Marco e justificativa: integração final da phase.
- T* abertas fora do marco: nenhuma.
- Provas reaproveitadas: provas visuais das T4 a T7 no navegador integrado (estáticos sem alteração depois da T6; T7 não tocou `ui/static/`); bench `live` da T7 (1/2 com proxy 0,16 a 0,18 s até o primeiro quadro, 30 s em 1,71 s), porque `render/live.py`, `windows.py` e `ffmpeg_graph.py` não mudaram depois dela.
- Provas executadas: suíte completa, gitleaks, pip-audit, `git diff --check` e conferência de órfãos (detalhe em Etapa 1).

## Cobertura

| Chave | Código | Notas |
|---|---|---|
| A1 | ok | `src/editor/ui/instance.py`, `cli.py`; testes de processo real em `tests/test_ui_server.py` (start duplo, stop, registro forjado, junction, inatividade) |
| A2 | ok | `src/editor/ui/server.py`, `sessions.py`, `limits.py`; Host, token em tempo constante, Origin, teto de sessões, headers |
| A3 | ok | `src/editor/render/live.py`, `windows.py`, `ffmpeg_graph.py`; `tests/test_live_stream.py` (framemd5 e PCM em J/L, reframe, texto, imagem, 29,97); goldens de `test_preview_render.py` e `test_aspect_ratio.py` intactos no diff |
| A4 | ok | `ui/static/player.js`; prova visual T4 |
| A5 | ok | `ui/static/timeline.js`, `ui/selection.py`; seleção conferida contra `plan validate` |
| A6 | ok | `ui/state.py` (vigia, última versão válida); prova visual T4 |
| A7 | ok | `ui/usage.py`, `render/live.py` (fonte inteira); prova visual T5 |
| A8 | ok | R3: clique e leitura real do clipboard no Chrome; captura igual à API após normalizar CRLF (2026-10-10) |
| A9 | ok | `ui/server.py` `_serve_approve`, `integrity/approval.py`; recusas 409 sem gravar |
| A10 | ok | `integrity/approval.py`, `render/full.py`; `tests/test_approval.py`, `tests/test_full_render.py`, E2E |
| A11 | ok | `capabilities.py` lido das constantes; `AGENTS.md` e skill coerentes com o código |
| C1 | ok | 468 testes OK nesta review |
| C2 | ok | gitleaks e pip-audit limpos; `requirements.lock` sem diff |
| C3 | ok | bench `live` da T1 e da T7 |
| C4 | ok | `CLI_VERSION` e `pyproject.toml` em 2.0.0; quebra descrita em `AGENTS.md` |
| C5 | ok | jornada real da T7 no navegador; E2E `tests/e2e/test_phase_7_flow.py` |

## Checklist de correções

nenhum bloqueio

### Optional / Nit

- [x] R1: **Nit** - `src/editor/ui/approval.py` (`_start_check`) - o docstring promete conferência das fontes "uma thread por vez", mas `_pending` é sobrescrito por uma chave nova e outra thread de `verify_approval` começa enquanto a anterior ainda lê as fontes; alternar versões do plano com aprovação vinda da CLI pode somar leituras completas de fontes grandes em paralelo - remédio: vibe-implement serializar a conferência (trava ou thread única) ou corrigir o docstring - prova: teste com duas chaves seguidas e contagem de threads ativas.
- [x] R2: **Nit** - `src/editor/capabilities.py` - importa `_APPROVAL_FIELDS` e `_SOURCE_APPROVAL_FIELDS`, nomes privados de `integrity/approval.py` - remédio: vibe-implement expor constantes públicas (`APPROVAL_FIELDS`, `SOURCE_APPROVAL_FIELDS`) e usá-las nos dois lados - prova: `unittest tests.test_capabilities tests.test_approval`.
- [x] R3: **Optional** - A8 - a cópia real à área de transferência não foi lida num Chrome normal (o navegador integrado nega a permissão); o código chama `navigator.clipboard.writeText` com o texto do servidor e o fallback manual foi provado - remédio: o operador conferir uma vez colando o texto num editor - prova: texto colado igual ao de `/api/selection`.
- [x] R4: **Nit** - `src/editor/ui/server.py` (`_send_error`) - `UI_SESSION_LIMIT` numa navegação (`GET /?token=` com duas sessões ativas) devolve o envelope JSON cru em vez de uma página como a "Sessão inválida" - remédio: vibe-implement servir página curta para `UI_SESSION_LIMIT` fora de `/api/` - prova: teste HTTP com terceira sessão por navegação.

## Visual

- Browser: navegador integrado (`mcp__Claude_Browser__*`), evidência das T4 a T7 reaproveitada: estáticos inalterados depois da T6 e T7 sem mudança de interface; rotas `/` em Montagem e Fonte, 1440 x 900, 1024x700 e 900 x 700, popup de aprovação, recarga e faixa de erro, console sem CSP e rede só em 127.0.0.1.
- Leitura: ok, exceto a cópia real ao clipboard (R3).

## Segurança

- Borda HTTP (`ui/server.py`): ordem Host → sessão → Origin → rota/método → query/corpo antes de abrir arquivo ou processo; `Host` exato contra DNS rebinding; token e cookie comparados com `hmac.compare_digest`; cookie `HttpOnly`, `SameSite=Strict`, nome com a porta; token sai da URL por 302; POST exige `Origin` própria; sem CORS. Query até 2 KiB e 32 caracteres por valor, decimais sem sinal nem expoente, `source_id` pelo padrão do trabalho e existente no plano; corpo só no POST, JSON sem chave repetida, só os dois campos de 64 hex, até 1 KiB; `Transfer-Encoding` recusado.
- Arquivos: estáticos por `STATIC_NAME_PATTERN` (sem separador) e só dentro do pacote; mídia só pelos caminhos validados do plano ou do proxy; nenhum caminho vindo do navegador.
- Processos: FFmpeg sem shell, stdin e stderr descartados; um por tipo e sessão (ponytail do instante de sobreposição registrado); prazos de primeiro byte e de pausa de 30 s; kill na troca, no descarte da sessão e no encerramento; `ui stop` só mata a árvore depois de provar identidade e pid pela porta (registro forjado não mata outro processo, coberto por teste).
- Segredos e logs: token só no registro `.tmp` e no envelope de `ui start`; log com método, rota casada e status; servidor sem stdout/stderr herdado (coberto por teste); erros sem caminho nem stderr. Frontend sem `innerHTML`/`eval`; `localStorage` só para a resolução.
- Aprovação: hash do plano e impressão conferidos antes do SHA-256 das fontes e a impressão de novo depois, publicação atômica; plano alterado depois do carregamento grava nada (testado com troca de fonte durante o hash). Limitação conhecida e registrada: troca de fonte que preserve tamanho e `mtime_ns` escapa da impressão (o SHA-256 gravado e conferido pela full cobre o conteúdo).
- Nada explorável encontrado no que o diff tocou.

## Notas

- `render/live.py` documenta as diferenças de áudio com `limiter`/`loudnorm` ligados e de quadros com taxas misturadas; ambas estão em `AGENTS.md` e na skill, fora do que A3 prova (plano padrão e taxa única).
- Registros fora do escopo da phase em `.erros-encontrados/2026-10-09-*.md` (camada de imagem, faixa de estado com `PLAN_INVALID`, preferências por porta, docstring do pacote `ui`) seguem abertos e não bloqueiam.

## Veredito vigente

- [x] **Approve**: nenhum Critical/Required em `[ ]`
- [ ] **Request changes**: há Critical/Required em `[ ]`
- [ ] **Approve com defer**: <qual R* e por quê>

## Decisões para vigência

| ID | Ação | Decisão aprovada | Fonte da decisão | Evidência de implementação |
|---|---|---|---|---|
| UI-01 | cria | O player ao vivo e o scrub usam o mesmo `build_ffmpeg_graph` da full com janela de tempo a partir de T; em `full` lê os originais e o vídeo cru e o PCM do grafo são idênticos aos da full (plano sem `limiter`/`loudnorm`, taxa única); as frações dividem o canvas da full a par e leem o proxy válido; sem janela de tempo o grafo e o comando são byte a byte os anteriores | spec suposições 1 a 4, A3 | plan T1; `tests/test_live_stream.py`, goldens intactos |
| UI-02 | cria | `editor ui start` sobe um servidor por trabalho em segundo plano em `127.0.0.1` numa porta livre e reaproveita a instância viva; `ui stop` encerra o servidor e os FFmpeg filhos só depois de provar identidade e pid pela porta; encerra sozinho após 2 h sem pedidos | spec F1, suposição 9 | plan T3; `tests/test_ui_server.py` |
| UI-03 | cria | Servidor só com biblioteca padrão e stream MP4 fragmentado H.264/AAC por HTTP limitado pela pressão do pipe; interface em HTML, CSS e JS puros sem build nem CDN, empacotada em `editor/ui/static`, timeline em canvas | spec suposição 5, Tecnologia imposta | plan T3 e T4 |
| SEC-01 | cria | A interface é só leitura (única escrita: `approval.json`, além do registro e dos scripts de filtro em `.tmp`); token de 32 bytes trocado por cookie `HttpOnly`/`SameSite=Strict` por porta, `Host` exato, `Origin` obrigatória no POST, sem CORS, allowlist de rotas, query e corpo, até 2 sessões e 3 FFmpeg por sessão, CSP restritiva, log sem token | spec suposições 7 e 13, F2, Contratos | plan T3 e T6; `tests/test_ui_server.py` |
| APPROVAL-01 | substitui (T9: vínculo com a preview) | `approval.json` v2 vincula só `plan_sha256` e o SHA-256 das fontes, sem `preview.mp4`; v1 é `APPROVAL_STALE`; o botão da interface aprova a versão exibida pelo `plan_sha256` e pela `sources_fingerprint` e recusa com `APPROVAL_PLAN_CHANGED` sem gravar se divergirem antes ou depois do hash; `render preview` é exportação opcional; CLI 2.0.0 | spec suposições 10 e 11, F8, A9, A10 | plan T2, T6 e T7; `tests/test_approval.py`, `tests/e2e/test_phase_7_flow.py` |

## Handoff

finalização Git da phase (após confirmação humana)

- [x] Aprovação humana (leu o arquivo e confirmou) em 2026-10-10, com publicação das decisões UI-01, UI-02, UI-03, SEC-01 e APPROVAL-01 e push autorizados

- Chat: review executada em subagente de contexto limpo (Modo A do `vibe-implement`). Os R* abertos são Nit/Optional e não bloqueiam; podem entrar numa correção posterior por subagente corretor, se o humano quiser. Após Approve e confirmação humana, o coordenador faz a finalização Git da phase. O `review.md` vivo e o diff são a ponte.

## Finalização Git da phase

- Pré-condições: Approve final confirmado, todas as `T*` concluídas, Critical/Required fechados, prova necessária da integração verde, `git diff --check` e gitleaks quando previsto.
- Commit final: `chore(phase-7): finalize review`, somente com paths residuais autorizados e sem `Co-Authored-By`; sem mudanças residuais, manter o último commit da task.
- Push: `git push` para o upstream atual, sem `--force`. Registrar hash/HEAD, paths e resultado; falha mantém o handoff bloqueado.

## Etapas

### Etapa 1 - first-pass - T1 a T7, diff `4885810..e39847a`, provas

- Tipo: final
- Marco e justificativa: integração final da phase 7.
- T* abertas fora do marco: nenhuma
- Provas reaproveitadas: visuais das T4 a T7 (estáticos sem mudança depois da T6), bench `live` da T7 (núcleo de render sem mudança depois dela).
- Provas executadas: `.venv\Scripts\python.exe -B -m unittest discover -s tests` (integração de todas as T*) -> 468 testes OK em 777 s; nenhum `ffmpeg.exe` vivo depois da suíte (`tasklist`); `gitleaks detect --source . --no-banner` -> 88 commits, no leaks found; `pip_audit -r requirements.lock --no-deps --disable-pip` -> No known vulnerabilities found; `git diff --check 4885810..HEAD` -> limpo; `requirements.lock` sem diff.
- Leu: plan.md sim; spec, analyze e AGENTS.md; código de `ui/server.py`, `sessions.py`, `playback.py`, `instance.py`, `state.py`, `approval.py`, `limits.py`, `integrity/approval.py`, `render/live.py`, trechos de `preview.py`, `capabilities.py` e `ui/static/api.js`/`approval.js`; inventário dos testes de `test_ui_server.py`, `test_live_stream.py` e `test_approval.py`.
- Abriu: R1, R2, R3, R4 (todos Nit/Optional)
- Fechou: nenhum
- Veredito desta etapa: Approve final, confirmado pelo humano em 2026-10-10

## Correções de 2026-10-10, solicitadas pelo humano

- R1: worker única de hash com fila que guarda somente a chave mais recente; teste retém a primeira leitura e prova máximo de uma leitura ativa e descarte da versão intermediária.
- R2: APPROVAL_FIELDS e SOURCE_APPROVAL_FIELDS públicas e consumidas por aprovação, capacidades e testes.
- R3: Chrome, seleção 0 a 0,720 s da fixture workflow; clique real em Copiar para IA, navigator.clipboard.readText retornou texto completo com CRLF. A captura coincide com /api/selection?start=0&end=0.72 após normalização de quebras de linha. Código de cópia intacto.
- R4: navegação sem vaga recebe página Limite de sessões com status 429 e CSP por hash; API conserva envelope. Inspeção visual 900 x 700: texto legível, sem clipping; HTTP 429 e favicon 401 esperados, sem erro de CSP.
- .erros-encontrados: colisão do Python global e modo das regras corrigidos no commit `7731a91`; imagem, fonte ausente, preferências e docstring corrigidos e registrados nos respectivos arquivos.
- Visual integrado: Chrome 1440 x 900, resolução 1/2, volume 0,35, mudo; ui stop/start troca porta 51659 por 60751, preferências restauradas e unmute retorna 0,35. Renomear main.mp4 da fixture exibe SOURCE_INVALID na faixa, mantém última versão e bloqueia aprovação. Fonte restaurada após a prova. Console da montagem sem erros.
- Gates: gitleaks histórico de 90 commits e diretórios src/tests limpos; pip-audit requirements.lock sem vulnerabilidades; pip check global e venv sem dependências quebradas.
- A aprovação humana da Etapa 1 permanece histórica. Esta rodada aguarda revisão independente e confirmação final antes de push.