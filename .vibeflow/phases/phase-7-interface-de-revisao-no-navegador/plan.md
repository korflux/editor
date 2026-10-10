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

- [x] T2 concluída
- **Spec:** A10, parte de C4 (suposições 10 e 11; F8 passos 5 a 7)
- **O quê:** o `approval.json` passa para `schema_version` 2, com `approved_at`, `plan_sha256` e `sources`. `editor approve` deixa de exigir preview, e `render full` verifica só plano e fontes. Registro v1 vira `APPROVAL_STALE`. Os envelopes de `approve` e `render full` perdem `preview_sha256`. O núcleo de aprovação ganha a forma condicionada que o servidor vai usar: recebe o `plan_sha256` esperado e a impressão das fontes (tamanho e `mtime_ns`) do carregamento, e recusa com `APPROVAL_PLAN_CHANGED`, sem gravar, se algum divergir; depois de calcular os SHA-256 das fontes, confere a impressão de novo, para não gravar o hash de uma fonte trocada durante o cálculo. A impressão é comparada pela `sources_fingerprint` (SHA-256 da lista canônica de `source_id`, tamanho e `mtime_ns`), a mesma que o estado da T3 expõe. `render preview` segue igual e sem efeito na aprovação. Fixtures e jornadas E2E existentes são ajustadas ao contrato novo.
- **Aceite:**
  - [x] `approve` publica v2 sem preview no trabalho; `render full` aceita v2 e recusa v1, plano alterado e fonte alterada com `APPROVAL_STALE`, preservando a full vigente.
  - [x] A aprovação condicionada recusa hash divergente e impressão divergente (antes e depois do cálculo dos SHA-256) com `APPROVAL_PLAN_CHANGED`, sem criar nem alterar `approval.json`.
  - [x] `render preview` funciona e não altera a aprovação.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_approval tests.test_full_render tests.test_render_quality tests.test_preview_proxy tests.test_cli_contract tests.test_hevc_sources tests.e2e.test_mvp_flow tests.e2e.test_phase_2_flow tests.e2e.test_phase_5_flow -v`
- **Deps:** nenhuma
- **Prova:** verificação da T2 -> 94 testes OK; suíte completa `unittest discover -s tests` -> 380 testes OK.
- **API para T3/T6:** `editor.integrity.approval.sources_fingerprint(validation) -> str` (SHA-256 da lista canônica `source_id`, `size_bytes`, `mtime_ns` lida por `os.stat`; `SOURCE_INVALID` se ilegível) e `approve_workdir_if_unchanged(workdir, expected_plan_sha256, expected_sources_fingerprint)` (recusa `APPROVAL_PLAN_CHANGED`, exit 5, sem gravar). `contracts.py` não mudou: os códigos nascem no `CliError`.
- **Notas:** ajustados também `tests/e2e/test_phase_1_flow.py` (mutação da preview deixou de bloquear a full, por A10) e `tests/test_preview_proxy.py` (asserção de `preview_sha256`). `tests/fixtures/workflow/preview.mp4` permanece no fixture, sem uso obrigatório. `ponytail` no código: a impressão por tamanho e `mtime_ns` não detecta troca que preserve os dois; o SHA-256 da aprovação cobre o conteúdo.
- **Decisões:** APPROVAL-01 (substitui o vínculo da aprovação com a preview)
- **Arquivos:** `src/editor/integrity/approval.py`, `src/editor/render/full.py`, `src/editor/cli.py` (só `approve`), `src/editor/contracts.py` (código novo, se necessário), `tests/test_approval.py`, `tests/test_full_render.py`, `tests/fixtures/workflow/`, `tests/e2e/`
- **Risco:** é a quebra de contrato da phase. A versão 2.0.0 só é publicada na T7, junto com a documentação.

### T3: Abrir a interface por `editor ui start` com servidor local protegido

- [x] T3 concluída
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
  - [x] Processo real: dois `start` (o segundo devolve `running` com a mesma URL), `stop` sem processo nem FFmpeg órfão, `stop` sem instância devolve `not_running`, e encerramento por inatividade com prazo reduzido no teste.
  - [x] Pedidos HTTP reais recebem `UI_TOKEN_INVALID`, `UI_HOST_INVALID`, `UI_ORIGIN_INVALID` e `UI_SESSION_LIMIT` nos casos da spec. Query ou corpo fora da allowlist recebem `ARGUMENT_INVALID`. Os headers de segurança estão presentes.
  - [x] O estado muda de hash em até 2 s depois de alterar `plan.json`, e de `sources_fingerprint` depois de alterar uma fonte. Plano inválido devolve código e mensagem segura, mantendo a última versão válida.
  - [x] Duas instâncias em trabalhos diferentes, abertas no mesmo cliente HTTP com cookies, mantêm as duas sessões válidas; uma terceira sessão descarta a ociosa mais antiga e só recebe `UI_SESSION_LIMIT` quando as duas estão ativas.
  - [x] Rota e método fora da allowlist recebem 404 e 405; o stderr e o log do servidor, capturados no teste, não contêm o token.
  - [x] O texto da seleção confere campo a campo com `plan validate` (segmentos, J/L, camadas, legendas, timecode e segundos) e não contém caminho absoluto nem token.
  - [x] O stream servido pela API é o mesmo da T1 e termina quando o cliente fecha a conexão.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_ui_server tests.test_cli_contract -v`
- **Deps:** T1
- **Prova:** `unittest tests.test_ui_server tests.test_cli_contract` -> 65 testes OK (46 novos); suíte completa -> 426 testes OK. Processos, HTTP e FFmpeg reais; sem órfãos depois da suíte (conferido por CIM). Smoke no navegador: tela "Sessão inválida" sem violação de CSP, `/` com estáticos 200, cookie invisível ao JS, `<video>` consumindo `/api/stream`. Wheel descartável confirmou os estáticos no pacote.
- **API:** `GET /?token=` (302 e cookie `editor_ui_session_<porta>`), `GET /`, `GET /static/<nome>`, `GET /api/state`, `GET /api/stream?t=&res=`, `GET /api/frame?t=&res=`, `GET /api/selection?start=&end=`. Constantes para a T7 em `ui/limits.py` e `render.live.RESOLUTIONS`. Pontos de extensão: rota `/api/approve` (T6) usa `read_json_body` já pronto; tipo de processo `source` (T5) já existe em `PROCESS_KINDS`.
- **Notas:** decisões do implementador dentro da spec: estados `loading|ready|outdated|missing|invalid` com vigia de 0,25 s e assinatura estável por 2 ciclos; `UI_STOP_FAILED` (servidor ainda vivo 10 s depois do kill); HTTP/1.0 sem `Range`, timeout de 30 s de socket encerra o stream parado; `ui stop` só mata a árvore depois de provar a instância pela porta. `ponytail` no código: cookie não isolado por porta, estado inteiro a cada consulta, assinatura por tamanho e mtime, `ui start` concorrente, `playback.capture_frame` repete parte de `live.extract_frame` para poder cancelar o scrub.
- **Decisões:** UI-02 (cria), SEC-01 (cria), UI-03 (cria, lado do servidor)
- **Arquivos:** `src/editor/ui/` (novo: servidor, instância, sessão, estado, seleção e estáticos), `src/editor/cli.py`, `pyproject.toml` (inclusão dos estáticos no pacote), `tests/test_ui_server.py` (novo)
- **Risco:** é a superfície de segurança da phase. Toda entrada é validada antes de abrir arquivo ou processo, e a mídia só sai das fontes e proxies declarados.

### T4: Revisar a montagem no navegador com player, timeline, seleção e recarga

- [x] T4 concluída
- **Spec:** A4, A5, A6, A8 (F3, F4, F5, F7; design: tela Revisão modo Montagem)
- **O quê:** a tela Montagem do design sobre a API da T3, em HTML, CSS e JS puros, com o kit de tokens do design:
  - **Barra superior:** hash e alternância de modo (Fonte desabilitado até a T5); a aprovação entra na T6.
  - **Player e transporte:** play, pausa, quadro a quadro, salto de corte e resolução com selo "Proxy". A resolução escolhida é lembrada por trabalho no navegador.
  - **Timeline:** em canvas, com as faixas na ordem do design, J/L hachurado, zoom e playhead. Tem scrub por quadro composto e seleção por Shift+arraste, I e O e Alt+clique.
  - **Painel do trecho:** recolhível, com "Copiar para IA". Usa a área de transferência e, sem permissão dela, mostra campo selecionável.
  - **Recarga e estados:** recarga automática que mantém o cursor e descarta a seleção com toast; faixa de estado de plano inválido; atalhos do Premiere; estados vazios e de erro do design.
- **Aceite:**
  - [x] Play em vários pontos começa no tempo certo, com timecode da montagem. Pausa, quadro a quadro, salto de corte e troca de resolução seguem F3, e o selo "Proxy" aparece nas frações com proxy.
  - [x] A timeline mostra as faixas com J/L e legendas. A seleção pelos três gestos preenche o painel com os intervalos de `plan validate`.
  - [x] Editar o `plan.json` por fora recarrega com toast e cursor mantido. O plano quebrado mostra a faixa de erro.
  - [x] "Copiar para IA" coloca o texto da T3 na área de transferência.
  - [x] Navegação inteira por teclado com foco visível; console sem erro e sem violação de CSP; nenhum pedido externo.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_ui_server -v`
- **Visual:** necessária. O aceite depende de player, timeline, painel, toast e faixa renderizados e interagidos. Rota `/`, viewports 1440x900 e 1024x700, com fixture 16:9 e 9:16 (as provas listadas no design para o modo Montagem), registradas com screenshots.
- **Prova:** `unittest tests.test_ui_server` -> 46 testes OK com os estáticos finais (nenhum Python alterado). `timecode.js` contra `format_timecode`/`snap_start` em 5.607 casos (7 taxas), 0 divergências. Visual no navegador integrado, rota `/`: 16:9 (fonte 1080p com proxy, J/L, 3 camadas, 3 legendas, trilha extra) e 9:16 (reframe, sem proxy), em 1440x900 e 1024x700. Play em 0, 11,8 e 12,84 s com quadro e timecode corretos; pausa e retomada sem novo stream; quadro a quadro, salto de corte, Home/End; selo Proxy em 1/2 e 1/4 (stream 480x270 em 1/4), ausente em Full e no 9:16 sem proxy; seleção por Alt+clique, I/O e Shift+arraste (este por PointerEvents sintéticos, a ferramenta não aplica modificadores) com painel igual ao servidor; Copiar para IA entrega a `writeText` o texto de `/api/selection` e cai no campo manual com o clipboard negado; edição externa recarrega com toast e cursor mantido; plano quebrado mostra a faixa `PLAN_INVALID` com a última versão válida; Tab com foco visível; 1024x700 com painel sobreposto e sem rolagem horizontal; console sem erro de CSP; rede só em 127.0.0.1.
- **Notas:** o stream não aceita `Range`, então quadro a quadro, salto e scrub mostram o quadro composto e o play seguinte reinicia no cursor (`ponytail` em `player.js`). Cópia real ao clipboard não lida no navegador integrado (permissão negada); conferir uma vez em Chrome normal. Fora do escopo: `.erros-encontrados/2026-10-09-preferencias-da-interface-se-perdem-a-cada-ui-start.md`.
- **Deps:** T3
- **Decisões:** UI-03 (lado da interface: HTML, CSS e JS sem build, timeline em canvas)
- **Arquivos:** `src/editor/ui/static/` (HTML, CSS, JS e sprite SVG)

### T5: Conferir cada fonte inteira no modo Fonte

- [x] T5 concluída
- **Spec:** A7, parte de A8 (F6, F7 no modo Fonte; design: tela Revisão modo Fonte)
- **O quê:**
  - **Servidor:** stream da fonte inteira, em transcodificação ao vivo do original em `full` e do proxy válido nas frações, a partir de T; lista de fontes com uso; e texto da seleção na fonte (usado, descartado ou parcial e onde cada parte usada aparece na montagem), a partir de `source_intervals`.
  - **Interface:** lista de fontes (vira `<select>` abaixo de 1280px), barra de uso com usados e descartados, "Ver fonte" a partir de um segmento, Alt+clique e "Ir para a montagem", e painel com "Copiar para IA".
- **Aceite:**
  - [x] A barra de uso de cada fonte de vídeo e áudio coincide com os intervalos do plano. Uma fonte só de áudio toca sem imagem.
  - [x] "Ver fonte" abre no `in_s` do segmento, e "Ir para a montagem" volta ao tempo correspondente.
  - [x] O texto da seleção na fonte confere com o plano, e a fonte ausente aparece como "Indisponível" com `SOURCE_INVALID`.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_ui_server tests.test_live_stream -v`
- **Visual:** necessária. A barra de uso e a navegação entre modos são julgadas renderizadas: rota `/` em modo Fonte, viewports 1440x900 e 1024x700, com as provas do design para esse modo.
- **Prova:** `unittest tests.test_ui_server tests.test_live_stream` -> 87 testes OK (21 novos); suíte completa -> 447 testes OK. Stream da fonte conferido por `framemd5` contra a fonte decodificada (T = 0, 10, 17,04, 17,05, 29,9), original em Full e proxy nas frações, só áudio sem vídeo, término ao fechar; uso e seleção na fonte contra `plan validate`; `SOURCE_INVALID` 409 sem abrir FFmpeg; recusas da allowlist. Visual no navegador integrado, modo Fonte em 1440x900 e 1024x700: barra de uso igual ao plano, Full 1920x1080 e 1/2 960x540 com selo Proxy, fonte de áudio tocando sem imagem, seleção parcial/descartada com texto conferido, Ver fonte abrindo no `in_s`, Ir para a montagem no tempo certo, fonte renomeada como "Indisponível" com volta automática, lista em `<select>` a 1024 sem rolagem horizontal, console vazio, rede só local.
- **API nova:** `GET /api/source/stream`, `GET /api/source/frame`, `GET /api/source/selection` (`source_id`, `t`/`start`/`end`, `res`); `/api/state` ganha em `plan.sources[]` `frame_rate`, `uses`, `used_count`, `available` e `error` (aditivo).
- **Notas:** a barra de uso de uma trilha de áudio mostra o trecho ouvido (limitado à janela de áudio do segmento), não o `in_s/out_s` declarado; idêntico a `source_intervals` quando a trilha tem a duração da janela (documentado em `ui/usage.py`). Canvas do stream da fonte com teto UHD em Full. Fora do escopo: `.erros-encontrados/2026-10-09-faixa-de-estado-mostra-plan-invalid-quando-uma-fonte-some.md` (faixa global diz `PLAN_INVALID`; linha, player e rotas dizem `SOURCE_INVALID`).
- **Deps:** T4
- **Arquivos:** `src/editor/ui/` (servidor, seleção na fonte e estáticos), `src/editor/render/live.py` (stream da fonte), `tests/test_ui_server.py`, `tests/test_live_stream.py`

### T6: Aprovar a versão exibida pela interface

- [x] T6 concluída
- **Spec:** A9 (F8 passos 1 a 4; design: popup de aprovação)
- **O quê:**
  - **Servidor:** `POST` de aprovação com corpo `{plan_sha256, sources_fingerprint}` (até 1 KiB, `Origin` e cookie obrigatórios), chamando a aprovação condicionada da T2 com o hash e a impressão da versão exibida.
  - **Interface:** botão "Aprovar esta versão" e popup com hash, duração e nota de proxy. Selo "Aprovado às HH:MM" e "Aprovação desatualizada". Recusa por mudança na faixa de estado, seguida de recarga. O botão fica desabilitado com o plano inválido ou desatualizado.
- **Aceite:**
  - [x] A aprovação pela interface grava `approval.json` v2, e o `render full` seguinte a aceita.
  - [x] Com o plano ou uma fonte alterados depois do carregamento, a recusa é `APPROVAL_PLAN_CHANGED`, sem arquivo novo.
  - [x] POST sem `Origin` próprio, sem cookie ou com corpo fora da allowlist é recusado.
  - [x] O popup prende o foco, o Esc fecha e o foco volta ao botão.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_ui_server tests.test_approval -v`
- **Visual:** necessária. Popup, selo e recusa são julgados renderizados: abrir e cancelar, aprovar e alterar o plano com o popup aberto, em 1440x900.
- **Prova:** `unittest tests.test_ui_server tests.test_approval` -> 82 testes OK; suíte completa com o código final -> 458 testes OK. HTTP, CLI e FFmpeg reais: aprovação grava v2 e `render full` aceita; plano, fonte (mtime), plano inválido e fonte ausente depois do carregamento -> 409 sem alterar `approval.json`; sem cookie 401, sem `Origin` ou alheia 403, corpo fora da allowlist 400, GET 405; estado `none|checking|current|stale`. Visual no navegador integrado, 1440x900: popup com hash, duração e nota; foco preso entre Cancelar e Aprovar, Esc e Cancelar devolvem o foco ao botão; aprovar mostra "Aprovado às HH:MM" e a full é aceita; editar o plano mostra "Aprovação desatualizada" e a full passa a `APPROVAL_STALE`; plano ou fonte alterados com o popup aberto -> faixa `APPROVAL_PLAN_CHANGED`, recarga e `approval.json` idêntico; botão desabilitado com plano inválido; nota de proxy em 1/4; 900x700 sem overflow; console sem exceção nem CSP (só os 409 intencionais).
- **API nova:** `POST /api/approve` (corpo `{plan_sha256, sources_fingerprint}`, resposta `{approved_at, plan_sha256}`); `/api/state` ganha `approval: {state, approved_at}` (aditivo).
- **Notas:** `integrity/approval.py` ganhou `read_approval` público (aditivo). `ApprovalTracker` em `ui/approval.py` só refaz SHA-256 das fontes quando o `plan_sha256` do registro é o vigente, uma vez por versão e fora do pedido de estado; `ponytail`: aprovação pela CLI ou servidor reiniciado mostram "Verificando aprovação…" pelo tempo do hash completo.
- **Deps:** T2, T4
- **Decisões:** APPROVAL-01 (botão da interface)
- **Arquivos:** `src/editor/ui/` (rota de aprovação e estáticos do popup e do selo), `tests/test_ui_server.py`

### T7: Publicar o contrato da interface e fechar a versão 2.0.0

- [x] T7 concluída
- **Spec:** A11, C1, C2, C4, C5
- **O quê:**
  - **Capacidades:** `capabilities` expõe `operations.ui` (comandos, resoluções, tetos de sessão e de FFmpeg, inatividade) e o contrato de aprovação v2, lidos das constantes da T1, T2 e T3.
  - **Documentação:** a skill `vibe-editor` ensina a jornada ui start → revisão → aprovação na interface → `render full`, com `render preview` como opcional. `AGENTS.md` ganha a seção "Interface de revisão, Phase 7", o mapa de estrutura e a decisão APPROVAL-01, com a quebra descrita.
  - **Versão:** 2.0.0 em `pyproject.toml`, `contracts.py` e testes de versão.
  - **Jornada E2E:** `ui start`, stream, seleção, alteração do plano por fora, recarga, aprovação pela API e `render full` aceitando a aprovação.
- **Aceite:**
  - [x] `capabilities --json` confere com as constantes, e a skill e o `AGENTS.md` não contradizem o código.
  - [x] Jornada E2E verde, suíte completa verde, gitleaks e pip-audit sem achados, `requirements.lock` sem dependência nova.
  - [x] Jornada real no navegador do `ui start` à aprovação, com a full aceita (C5).
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_capabilities tests.test_vibe_editor_skill tests.e2e.test_phase_7_flow -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  - [x] `gitleaks detect --source . --no-banner`
  - [x] `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip`
- **Visual:** necessária. C5 é a jornada real no navegador, reaproveitando as provas das T4 a T6 quando os inputs não mudaram.
- **Prova:** verificação 1 -> 50 testes OK; suíte completa -> 468 testes OK; gitleaks sem vazamento (histórico e árvore com `--no-git`); pip-audit "No known vulnerabilities found"; `requirements.lock` sem diff; `quick_validate.py` da skill válido. E2E `tests/e2e/test_phase_7_flow.py` (2 testes): jornada `ui start` → stream e scrub → seleção → plano alterado por fora → aprovação antiga recusada (409) e atual aceita → `render full` aceita → mutação com selo `stale` e `APPROVAL_STALE` → `ui stop` sem órfãos; e `approve` pela CLI sem preview com v1 recusado. Bench `live` remedido: 1/2 com proxy 0,16/0,17/0,18 s até o primeiro quadro, 30 s em 1,71 s. C5 no navegador integrado, 1440x900: play, seleção por I/O com painel correto, Copiar para IA pelo campo manual, recarga com toast e cursor mantido, aprovação pelo popup com selo, `render full` aceito (1920x1080 h264/aac), console vazio, rede só local.
- **Notas:** `AGENTS.md` editado pelo implementador com autorização do coordenador (seção "Interface de revisão, Phase 7", mapa, contratos de aprovação e preview, quebra 2.0.0); tabela "Decisões vigentes" intacta, linhas UI-01, UI-02, UI-03, SEC-01 e APPROVAL-01 propostas para o pós-review. `capabilities.py` importa `_APPROVAL_FIELDS` e `_SOURCE_APPROVAL_FIELDS` privados de `integrity/approval.py`. Fora do escopo: `.erros-encontrados/2026-10-09-docstring-do-pacote-ui-desatualizada.md`. Cópia real ao clipboard segue não conferida em Chrome normal.
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

## Correções solicitadas em 2026-10-10

- Escopo autorizado: R1 a R4 da review e registros pendentes em `.erros-encontrados/`, preservando registros históricos resolvidos.
- Concluído e provado: serialização da conferência de aprovação; constantes públicas; página de limite de sessões; cache do quadro de imagem; SOURCE_INVALID para fonte ausente; preferências entre portas; docstring do pacote UI.
- Ambiente e Git: colisão do Python global e modo 120000 das regras resolvidos e provados no commit `7731a91`.
- R3: clique real em `Copiar para IA` no Chrome, seleção 0 a 0,720 s da fixture workflow copiada para `tmp-phase7-review`; `navigator.clipboard.readText()` retornou o texto completo. Comparação da captura com `/api/selection?start=0&end=0.72` -> igualdade após normalizar CRLF do Windows. Nenhuma alteração no código de cópia foi necessária.
- Coordenação: relatório, paths e diff conferidos; provas de código e navegador verdes. Próxima etapa: review independente. Base inicial `430874c9eeb3958b3e4968580249f469fa68d1d2`; alterações preexistentes de roadmap e review da Phase 6 ficam fora dos commits.
- **Provas finais:** `unittest tests.test_capabilities tests.test_approval -q` -> 38 OK; `unittest tests.test_ui_server -q` -> 73 OK (75,597 s); `unittest tests.test_capabilities tests.test_approval tests.test_ui_server.SessionSecurityTests tests.test_ui_server.PlanStateTests tests.test_ui_server.ApprovalApiTests tests.test_live_stream -q` -> 102 OK; `unittest tests.test_preview_render tests.test_aspect_ratio tests.test_full_render tests.test_decode_windows -q` -> 83 OK (138,133 s). Todos via `.venv\Scripts\python.exe -B -m`; grupos se sobrepõem e não representam soma de testes distintos. Loopback exige execução fora do sandbox neste host. Teste novo de imagem prova framemd5 e PCM iguais ao grafo anterior; goldens intactos. Prova Node de store.js: troca de porta, isolamento, allowlist e limites OK.
- **Arquivos de código:** src/editor/capabilities.py, integrity/approval.py, render/ffmpeg_graph.py, render/live.py, render/preview.py, ui/__init__.py, ui/approval.py, ui/server.py, ui/state.py, ui/static/reload.js, ui/static/store.js; testes test_capabilities.py, test_live_stream.py, test_ui_server.py.
- **Visual:** necessária e executada. Chrome 1440x900, `/` da fixture: resolução 1/2, volume 0,35 e mudo persistem após restart em outra porta; fonte removida gera faixa SOURCE_INVALID e bloqueia aprovação. Limite de sessões: página segura e legível em 900x700. Console da montagem limpo; página de limite com HTTP 429 e favicon 401 esperados. R3 conforme leitura real do clipboard acima.
- **Gates:** gitleaks histórico e árvore de src/tests limpos; pip-audit sem vulnerabilidades; git diff --check limpo após normalizar fim dos registros.
