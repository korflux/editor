# Plan: interface de revisão no navegador com player ao vivo
# Alvo: phase-7-interface-de-revisao-no-navegador
# Status: aprovado
# Spec: spec.md (mesma pasta)

## Overview

Entrega a interface de revisão da spec: um servidor local aberto por `editor ui start`, um player ao vivo que transmite o mesmo grafo FFmpeg da full em Full, 1/2, 1/3 ou 1/4, a timeline com seleção, o modo Fonte, a cópia de trecho para a IA e a aprovação pela interface. A aprovação passa para a v2 sem `preview.mp4`, e a CLI vai para 2.0.0. A fila começa pela prova técnica do stream ao vivo (risco principal da spec, C3) e pela aprovação v2, que são independentes. Depois vem o servidor, as telas por modo e, no fim, o contrato público. Rota max: a phase toca sessão autenticada por token e o portão de aprovação da full.
- **Design:** `.vibeflow/phases/phase-7-interface-de-revisao-no-navegador/design.md` (aprovado)

## Prontidão das provas

- **Requisitos verificados (todos locais; o repositório não tem CI):**
  - Python 3.11 em `.venv\Scripts\python.exe` para `unittest`. O servidor usa só a biblioteca padrão (`http.server`, `secrets`, `hmac`, `webbrowser`).
  - FFmpeg e FFprobe locais no PATH com `libx264`, `aac`, `drawtext` e muxer `mp4` com `frag_keyframe+empty_moov`, já usados pelas phases anteriores.
  - Fonte 4K sintética e cenários de `scripts/bench_render.py`, para a medição de C3 na máquina de referência (i5-13600K).
  - `gitleaks` 8.30.1 e `pip_audit` 2.10.1 (via `.venv`) para a T7.
  - Navegador integrado do Claude desktop (`mcp__Claude_Browser__*`) para as provas visuais das T4 a T7, com o MCP `chrome-devtools` como alternativa. Não há Playwright no repo e ele não será adicionado.
  - A lógica testável fica em Python, sem Node: mapeamento de seleção, texto copiado, estado do plano e respostas HTTP. O JavaScript da interface só desenha e encaminha eventos, provado no navegador.
- **Ausências e ação na fila:** nenhuma.

## Tasks

### T1: Provar e entregar o stream ao vivo da montagem a partir de qualquer instante

- [x] T1 concluída
- **Spec:** A3, C3 (suposições 1 a 6; F3 passos 1 e 3; F4 passo 2)
- **O quê:** um núcleo de render ao vivo, sem HTTP, com duas funções:
  1. Gerar os argumentos do FFmpeg para transmitir a montagem do instante T até o fim, em MP4 fragmentado H.264/AAC no stdout, na resolução escolhida (`full`, `1/2`, `1/3`, `1/4`, dimensões pares).
  2. Extrair o quadro composto de um instante, para o scrub.

  Ambas usam o `build_ffmpeg_graph` vigente. As entradas são os originais em `full` e o proxy válido nas frações. Para começar em T sem processar o trecho anterior, o grafo recebe uma janela de tempo da timeline: as janelas de decode passam a cobrir só o que alimenta `[T, fim]`, e a saída é recortada em T com o mesmo desconto de seek que as janelas já aplicam. O encoder de transmissão (preset rápido, GOP curto para seek) fica numa constante própria e não toca nos presets da full. A taxa de quadros do grafo é exposta para o timecode. O cenário `live` do `bench_render.py` mede o tempo até o primeiro quadro no início, no meio e no fim de uma timeline de 60 s, e a reprodução de 30 s em 1/2 com proxy.
- **Aceite:**
  - [x] Em `full`, o vídeo cru e o PCM que o grafo do stream produz a partir de T (mesmo comando com a saída trocada por `framemd5`, antes do encoder de transmissão) são idênticos, quadro a quadro e amostra a amostra, aos do grafo da full nos mesmos tempos. Em T igual a 0 e em T no meio de um segmento, num corte J/L, num `reframe` e numa camada de texto. O stream codificado sai H.264/AAC em MP4 fragmentado com as dimensões do canvas.
  - [x] Em 1/2, 1/3 e 1/4 as dimensões são as do canvas da full divididas e arredondadas para baixo a par; com proxy válido, as frações leem o proxy, e `full` nunca lê.
  - [x] O quadro de scrub em T é igual ao quadro cru do grafo do stream em T (comparação antes de qualquer encoder com perda).
  - [x] C3: no cenário `live`, o primeiro quadro sai em até 3 s depois do pedido no início, no meio e no fim, sem crescer com a posição, e 30 s em 1/2 são produzidos mais rápido que o tempo real.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_live_stream tests.test_decode_windows tests.test_preview_render tests.test_aspect_ratio -v`
  - [x] `.venv\Scripts\python.exe -B scripts\bench_render.py --scenario live --reps 3`
- **Deps:** nenhuma
- **Prova:** `unittest tests.test_live_stream tests.test_decode_windows tests.test_preview_render tests.test_aspect_ratio` -> 89 testes OK (goldens intactos; suíte completa 371 OK). `bench_render.py --scenario live --reps 3` (4K H.264 60 s, 12 trechos de 5 s, mediana): 1/2 com proxy, primeiro quadro 0,11 s (T=0), 0,13 s (T=30), 0,10 s (T=57,5); 30 s do meio em 1,15 s (26x o tempo real); full com originais 4K, primeiro quadro 0,66 a 0,73 s.
- **Decisões:** UI-01 (cria)
- **Arquivos:** `src/editor/render/live.py` (novo), `src/editor/render/ffmpeg_graph.py` e `src/editor/render/windows.py` (só a janela de tempo, sem mudar o grafo sem ela), `scripts/bench_render.py`, `tests/test_live_stream.py` (novo)
- **Notas:** `TimeWindow`/`remaining_reads` em `windows.py` alimentam janelas de decode e `build_ffmpeg_graph(time_window=)`; sem janela, grafo e comando byte a byte iguais. Limitações registradas como `ponytail:` em `live.py` (taxas misturadas, 29,97 fora da grade de 48 kHz, `limiter`/`loudnorm` frios em T, fonte só de áudio sem janela, áudio 44,1 kHz). Reuso de `preview._build_ffmpeg_arguments` por fatia do comando; extrair `_input_arguments` quando `preview.py` puder ser tocado. Fora do escopo: `.erros-encontrados/2026-10-09-camada-de-imagem-decodifica-a-timeline-inteira.md`.
- **Risco:**
  - Se C3 não for atingido, parar e levar ao humano antes de mudar o mecanismo B ou o critério (Ask first da spec).
  - Sem a janela de tempo, o `filter_complex` precisa sair byte a byte igual ao de hoje, e os goldens de `tests/test_preview_render.py` e `tests/test_aspect_ratio.py` ficam intactos.

### T2: Aprovar e liberar a full sem `preview.mp4` (aprovação v2)

- [ ] T2 concluída
- **Spec:** A10, parte de C4 (suposições 10 e 11; F8 passos 5 a 7)
- **O quê:** o `approval.json` passa para `schema_version` 2, com `approved_at`, `plan_sha256` e `sources`. `editor approve` deixa de exigir preview, e `render full` verifica só plano e fontes. Registro v1 vira `APPROVAL_STALE`. Os envelopes de `approve` e `render full` perdem `preview_sha256`. O núcleo de aprovação ganha a forma condicionada que o servidor vai usar: recebe o `plan_sha256` esperado e a impressão das fontes (tamanho e `mtime_ns`) do carregamento, e recusa com `APPROVAL_PLAN_CHANGED`, sem gravar, se algum divergir; depois de calcular os SHA-256 das fontes, confere a impressão de novo, para não gravar o hash de uma fonte trocada durante o cálculo. A impressão é comparada pela `sources_fingerprint` (SHA-256 da lista canônica de `source_id`, tamanho e `mtime_ns`), a mesma que o estado da T3 expõe. `render preview` segue igual e sem efeito na aprovação. Fixtures e jornadas E2E existentes são ajustadas ao contrato novo.
- **Aceite:**
  - [ ] `approve` publica v2 sem preview no trabalho; `render full` aceita v2 e recusa v1, plano alterado e fonte alterada com `APPROVAL_STALE`, preservando a full vigente.
  - [ ] A aprovação condicionada recusa hash divergente e impressão divergente (antes e depois do cálculo dos SHA-256) com `APPROVAL_PLAN_CHANGED`, sem criar nem alterar `approval.json`.
  - [ ] `render preview` funciona e não altera a aprovação.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_approval tests.test_full_render tests.test_render_quality tests.test_preview_proxy tests.test_cli_contract tests.test_hevc_sources tests.e2e.test_mvp_flow tests.e2e.test_phase_2_flow tests.e2e.test_phase_5_flow -v`
- **Deps:** nenhuma
- **Decisões:** APPROVAL-01 (substitui o vínculo da aprovação com a preview)
- **Arquivos:** `src/editor/integrity/approval.py`, `src/editor/render/full.py`, `src/editor/cli.py` (só `approve`), `src/editor/contracts.py` (código novo, se necessário), `tests/test_approval.py`, `tests/test_full_render.py`, `tests/fixtures/workflow/`, `tests/e2e/`
- **Risco:** é a quebra de contrato da phase. A versão 2.0.0 só é publicada na T7, junto com a documentação.

### T3: Abrir a interface por `editor ui start` com servidor local protegido

- [ ] T3 concluída
- **Spec:** A1, A2 (F1, F2, F5 passo 1; suposições 7, 9 e 13; Contratos de segurança)
- **O quê:**
  - **CLI:** `editor ui start --workdir <abs> [--no-open] --json` inicia o servidor em segundo plano, preso a `127.0.0.1` numa porta livre, e devolve `state`, `url`, `pid` e `port`. Uma instância viva é reaproveitada (`running`). `editor ui stop` encerra o servidor e os FFmpeg filhos (`stopped` ou `not_running`). O registro da instância fica em arquivo regular de `.tmp`, sem links. O servidor encerra sozinho depois de 2 h sem requisições.
  - **Sessão:** token de 32 bytes trocado por cookie `HttpOnly` e `SameSite=Strict`, com nome que inclui a porta, e redirect que tira o token da URL. Checagem de `Host` e de `Origin` no POST, teto de 2 sessões simultâneas (ativas nos últimos 120 s, com descarte da ociosa mais antiga, suposição 13) e de 3 FFmpeg por sessão (um pedido novo do mesmo tipo encerra o anterior). Allowlist de rotas, métodos, query e corpo conforme a spec. CSP, `nosniff`, `Referrer-Policy` e `frame-ancestors`. Erros no envelope padrão, sem token nem caminho, com status HTTP por código: 400 para `ARGUMENT_INVALID`, 404 para rota e 405 para método fora da allowlist (ambos com `ARGUMENT_INVALID`), os da spec para `UI_*` e `APPROVAL_PLAN_CHANGED`, 409 para plano inválido e 500 para `PLAYBACK_FAILED` e `FRAME_FAILED` antes do primeiro byte; falha depois do primeiro byte do stream encerra a conexão e a interface mostra `PLAYBACK_FAILED`.
  - **Logs e saída:** o processo do servidor não herda stdout nem stderr da CLI, então o envelope de `ui start` é a única saída no stdout; o log padrão de `http.server`, que imprime a linha do pedido com a query, é substituído por um que nunca registra o token.
  - **API:**
    - estado do trabalho: plano validado por `load_validated_plan`, timeline, fontes com impressão, `sources_fingerprint`, hash e erro seguro. É revalidado quando `plan.json` ou uma fonte muda, com no máximo 1 s de atraso;
    - stream da montagem e quadro de scrub, encaminhando o núcleo da T1;
    - texto da seleção: o mapeamento de um intervalo da montagem para segmentos, camadas e legendas com tempos da fonte, e o bloco "Copiar para IA" de F7.
  - **Estáticos:** a página da interface vazia e a tela "Sessão inválida", servidas do pacote.
- **Aceite:**
  - [ ] Processo real: dois `start` (o segundo devolve `running` com a mesma URL), `stop` sem processo nem FFmpeg órfão, `stop` sem instância devolve `not_running`, e encerramento por inatividade com prazo reduzido no teste.
  - [ ] Pedidos HTTP reais recebem `UI_TOKEN_INVALID`, `UI_HOST_INVALID`, `UI_ORIGIN_INVALID` e `UI_SESSION_LIMIT` nos casos da spec. Query ou corpo fora da allowlist recebem `ARGUMENT_INVALID`. Os headers de segurança estão presentes.
  - [ ] O estado muda de hash em até 2 s depois de alterar `plan.json`, e de `sources_fingerprint` depois de alterar uma fonte. Plano inválido devolve código e mensagem segura, mantendo a última versão válida.
  - [ ] Duas instâncias em trabalhos diferentes, abertas no mesmo cliente HTTP com cookies, mantêm as duas sessões válidas; uma terceira sessão descarta a ociosa mais antiga e só recebe `UI_SESSION_LIMIT` quando as duas estão ativas.
  - [ ] Rota e método fora da allowlist recebem 404 e 405; o stderr e o log do servidor, capturados no teste, não contêm o token.
  - [ ] O texto da seleção confere campo a campo com `plan validate` (segmentos, J/L, camadas, legendas, timecode e segundos) e não contém caminho absoluto nem token.
  - [ ] O stream servido pela API é o mesmo da T1 e termina quando o cliente fecha a conexão.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_ui_server tests.test_cli_contract -v`
- **Deps:** T1
- **Decisões:** UI-02 (cria), SEC-01 (cria), UI-03 (cria, lado do servidor)
- **Arquivos:** `src/editor/ui/` (novo: servidor, instância, sessão, estado, seleção e estáticos), `src/editor/cli.py`, `pyproject.toml` (inclusão dos estáticos no pacote), `tests/test_ui_server.py` (novo)
- **Risco:** é a superfície de segurança da phase. Toda entrada é validada antes de abrir arquivo ou processo, e a mídia só sai das fontes e proxies declarados.

### T4: Revisar a montagem no navegador com player, timeline, seleção e recarga

- [ ] T4 concluída
- **Spec:** A4, A5, A6, A8 (F3, F4, F5, F7; design: tela Revisão modo Montagem)
- **O quê:** a tela Montagem do design sobre a API da T3, em HTML, CSS e JS puros, com o kit de tokens do design:
  - **Barra superior:** hash e alternância de modo (Fonte desabilitado até a T5); a aprovação entra na T6.
  - **Player e transporte:** play, pausa, quadro a quadro, salto de corte e resolução com selo "Proxy". A resolução escolhida é lembrada por trabalho no navegador.
  - **Timeline:** em canvas, com as faixas na ordem do design, J/L hachurado, zoom e playhead. Tem scrub por quadro composto e seleção por Shift+arraste, I e O e Alt+clique.
  - **Painel do trecho:** recolhível, com "Copiar para IA". Usa a área de transferência e, sem permissão dela, mostra campo selecionável.
  - **Recarga e estados:** recarga automática que mantém o cursor e descarta a seleção com toast; faixa de estado de plano inválido; atalhos do Premiere; estados vazios e de erro do design.
- **Aceite:**
  - [ ] Play em vários pontos começa no tempo certo, com timecode da montagem. Pausa, quadro a quadro, salto de corte e troca de resolução seguem F3, e o selo "Proxy" aparece nas frações com proxy.
  - [ ] A timeline mostra as faixas com J/L e legendas. A seleção pelos três gestos preenche o painel com os intervalos de `plan validate`.
  - [ ] Editar o `plan.json` por fora recarrega com toast e cursor mantido. O plano quebrado mostra a faixa de erro.
  - [ ] "Copiar para IA" coloca o texto da T3 na área de transferência.
  - [ ] Navegação inteira por teclado com foco visível; console sem erro e sem violação de CSP; nenhum pedido externo.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_ui_server -v`
- **Visual:** necessária. O aceite depende de player, timeline, painel, toast e faixa renderizados e interagidos. Rota `/`, viewports 1440x900 e 1024x700, com fixture 16:9 e 9:16 (as provas listadas no design para o modo Montagem), registradas com screenshots.
- **Deps:** T3
- **Decisões:** UI-03 (lado da interface: HTML, CSS e JS sem build, timeline em canvas)
- **Arquivos:** `src/editor/ui/static/` (HTML, CSS, JS e sprite SVG)

### T5: Conferir cada fonte inteira no modo Fonte

- [ ] T5 concluída
- **Spec:** A7, parte de A8 (F6, F7 no modo Fonte; design: tela Revisão modo Fonte)
- **O quê:**
  - **Servidor:** stream da fonte inteira, em transcodificação ao vivo do original em `full` e do proxy válido nas frações, a partir de T; lista de fontes com uso; e texto da seleção na fonte (usado, descartado ou parcial e onde cada parte usada aparece na montagem), a partir de `source_intervals`.
  - **Interface:** lista de fontes (vira `<select>` abaixo de 1280px), barra de uso com usados e descartados, "Ver fonte" a partir de um segmento, Alt+clique e "Ir para a montagem", e painel com "Copiar para IA".
- **Aceite:**
  - [ ] A barra de uso de cada fonte de vídeo e áudio coincide com os intervalos do plano. Uma fonte só de áudio toca sem imagem.
  - [ ] "Ver fonte" abre no `in_s` do segmento, e "Ir para a montagem" volta ao tempo correspondente.
  - [ ] O texto da seleção na fonte confere com o plano, e a fonte ausente aparece como "Indisponível" com `SOURCE_INVALID`.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_ui_server tests.test_live_stream -v`
- **Visual:** necessária. A barra de uso e a navegação entre modos são julgadas renderizadas: rota `/` em modo Fonte, viewports 1440x900 e 1024x700, com as provas do design para esse modo.
- **Deps:** T4
- **Arquivos:** `src/editor/ui/` (servidor, seleção na fonte e estáticos), `src/editor/render/live.py` (stream da fonte), `tests/test_ui_server.py`, `tests/test_live_stream.py`

### T6: Aprovar a versão exibida pela interface

- [ ] T6 concluída
- **Spec:** A9 (F8 passos 1 a 4; design: popup de aprovação)
- **O quê:**
  - **Servidor:** `POST` de aprovação com corpo `{plan_sha256, sources_fingerprint}` (até 1 KiB, `Origin` e cookie obrigatórios), chamando a aprovação condicionada da T2 com o hash e a impressão da versão exibida.
  - **Interface:** botão "Aprovar esta versão" e popup com hash, duração e nota de proxy. Selo "Aprovado às HH:MM" e "Aprovação desatualizada". Recusa por mudança na faixa de estado, seguida de recarga. O botão fica desabilitado com o plano inválido ou desatualizado.
- **Aceite:**
  - [ ] A aprovação pela interface grava `approval.json` v2, e o `render full` seguinte a aceita.
  - [ ] Com o plano ou uma fonte alterados depois do carregamento, a recusa é `APPROVAL_PLAN_CHANGED`, sem arquivo novo.
  - [ ] POST sem `Origin` próprio, sem cookie ou com corpo fora da allowlist é recusado.
  - [ ] O popup prende o foco, o Esc fecha e o foco volta ao botão.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_ui_server tests.test_approval -v`
- **Visual:** necessária. Popup, selo e recusa são julgados renderizados: abrir e cancelar, aprovar e alterar o plano com o popup aberto, em 1440x900.
- **Deps:** T2, T4
- **Decisões:** APPROVAL-01 (botão da interface)
- **Arquivos:** `src/editor/ui/` (rota de aprovação e estáticos do popup e do selo), `tests/test_ui_server.py`

### T7: Publicar o contrato da interface e fechar a versão 2.0.0

- [ ] T7 concluída
- **Spec:** A11, C1, C2, C4, C5
- **O quê:**
  - **Capacidades:** `capabilities` expõe `operations.ui` (comandos, resoluções, tetos de sessão e de FFmpeg, inatividade) e o contrato de aprovação v2, lidos das constantes da T1, T2 e T3.
  - **Documentação:** a skill `vibe-editor` ensina a jornada ui start → revisão → aprovação na interface → `render full`, com `render preview` como opcional. `AGENTS.md` ganha a seção "Interface de revisão, Phase 7", o mapa de estrutura e a decisão APPROVAL-01, com a quebra descrita.
  - **Versão:** 2.0.0 em `pyproject.toml`, `contracts.py` e testes de versão.
  - **Jornada E2E:** `ui start`, stream, seleção, alteração do plano por fora, recarga, aprovação pela API e `render full` aceitando a aprovação.
- **Aceite:**
  - [ ] `capabilities --json` confere com as constantes, e a skill e o `AGENTS.md` não contradizem o código.
  - [ ] Jornada E2E verde, suíte completa verde, gitleaks e pip-audit sem achados, `requirements.lock` sem dependência nova.
  - [ ] Jornada real no navegador do `ui start` à aprovação, com a full aceita (C5).
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_capabilities tests.test_vibe_editor_skill tests.e2e.test_phase_7_flow -v`
  - [ ] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  - [ ] `gitleaks detect --source . --no-banner`
  - [ ] `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip`
- **Visual:** necessária. C5 é a jornada real no navegador, reaproveitando as provas das T4 a T6 quando os inputs não mudaram.
- **Deps:** T5, T6
- **Arquivos:** `src/editor/capabilities.py`, `src/editor/contracts.py`, `pyproject.toml`, `skills/vibe-editor/`, `AGENTS.md`, `tests/test_capabilities.py`, `tests/test_vibe_editor_skill.py`, `tests/e2e/test_phase_7_flow.py` (novo)

## Paralelização (opcional, omitir quando não aplicável)

- Grupo: T1, T2
- Motivo: as duas não dependem de nada e entregam resultados independentes (stream ao vivo e aprovação v2).
- Isolamento: a T1 fica em `render/live.py`, `ffmpeg_graph.py`, `windows.py`, `bench_render.py` e testes novos. A T2 fica em `integrity/approval.py`, `render/full.py`, no parser `approve` da `cli.py`, nas fixtures de workflow e nos E2E. Nenhum arquivo é compartilhado.

## Checkpoint de review (opcional, omitir quando não houver justificativa)

- Depois da T1: resultado do C3 e equivalência quadro a quadro com a full. Se o mecanismo B falhar no critério, a decisão volta ao humano antes de construir o servidor e as telas.
- Depois da T3: superfície de segurança do servidor (sessão, allowlist, processos), antes de expor a interface nas T4 a T6.

## Handoff

vibe-analyze
rota: max

- Chat: recomende novo chat ao iniciar `vibe-plan` após spec/design. Na rota max, o `vibe-analyze` roda em subagente a partir do chat do plan e o fechamento recomenda com ênfase um chat novo com `/vibe-implement`, que rodará em Modo A; sem subagentes, recomende chat novo para o analyze. Nas demais rotas, recomende chat novo para `vibe-implement`. No Modo B (uma T* por run), recomende um chat por T*; grupo paralelo aprovado é a exceção coordenada. O humano pode continuar no chat atual; o `plan.md` vivo é a ponte.
