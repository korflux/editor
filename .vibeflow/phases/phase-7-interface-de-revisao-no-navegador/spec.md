# Spec: interface de revisão no navegador com player ao vivo
# Alvo: phase-7-interface-de-revisao-no-navegador
# Status: aprovado

## Objetivo

O operador revisa a montagem que a IA cria e altera por CLI numa interface no navegador, aberta pela própria IA com `editor ui start`. A interface tem player ao vivo, timeline, visualizador de cada fonte inteira e seletor de trecho que copia contexto preciso para a IA. O player roda o mesmo grafo FFmpeg da full, na resolução escolhida, então o que aparece é o que sai na full, sem renderizar `preview.mp4`. A aprovação passa a ser um botão da interface vinculado ao plano exibido e às fontes. Sucesso é revisar um episódio inteiro, comunicar alterações e conferir o resultado sem renderizar preview nem calcular timestamps, e então aprovar e pedir a full.

## Cobertura da origem

| Origem | Destino na spec | Motivo se fora/N/A |
|---|---|---|
| Abrir a interface pela IA (`ui start`/`ui stop`, uma instância por trabalho) | F1 | N/A |
| Sessão local protegida (127.0.0.1, token, `Host`) | F2, Contratos | N/A |
| Player ao vivo com seletor Full, 1/2, 1/3, 1/4 | F3 | N/A |
| Play, pausa, quadro a quadro, salto de corte, scrub | F3, F4 | N/A |
| Timeline visual (vídeo, camadas, áudio, legendas) para navegar e selecionar | F4 | N/A |
| Recarga automática mantendo o cursor | F5 | N/A |
| Visualizador da fonte inteira com usados e descartados | F6 | N/A |
| Seletor de trecho com "Copiar para IA" | F7 | N/A |
| Aprovação pelo botão, sem `preview.mp4`; `editor approve` sem preview | F8 | N/A |
| `render preview` como exportação opcional | F8 | N/A |
| Prova técnica do player ao vivo primeiro | Suposições 2, C3 | Ordem de execução é do plan |
| Identidade estável de segmentos e camadas | Fora | A cópia usa tempos da fonte e hash do plano, estáveis; índices são informativos |
| Edição por arraste, efeitos, colaboração, chat, compositor no navegador, full pela interface | Fora | Decidido na interview |

## Suposições e decisões

1. O player ao vivo usa o grafo de `build_ffmpeg_graph`, o mesmo da preview e da full; mudam só canvas, entradas (proxy ou original), intervalo de tempo e encoder de transmissão. Nenhum efeito é reimplementado no navegador. (produto; já fechado, UI-01)
2. O maior risco técnico é iniciar a renderização num instante arbitrário da timeline sem processar o trecho anterior. A primeira tarefa do plan prova isso contra C3 antes de construir a interface. (processo; já fechado)
3. Resoluções: Full é o canvas da full do perfil (`full_width` x `full_height`); 1/2, 1/3 e 1/4 dividem as duas dimensões e arredondam para baixo ao número par, com mínimo de 2 px. O texto escala pela regra vigente da preview (RENDER-04). (produto)
4. Full lê sempre os originais. 1/2, 1/3 e 1/4 leem o proxy válido de cada fonte quando existir, como `--proxy auto`, e o original nas demais. A interface indica quando há proxy em uso. A UI nunca dispara `proxy build`. (produto; reversível)
5. A transmissão é MP4 fragmentado H.264/AAC por HTTP, consumido pelo `<video>` nativo. A pressão do pipe limita o FFmpeg ao ritmo de consumo, então ele não roda à frente sem limite. (técnica; já fechado, UI-03)
6. O timecode é `HH:MM:SS:FF` na taxa de quadros de saída que preview e full já aplicam com `-r` (a da fonte do primeiro segmento; 25 quando ela não informa taxa, como o `color` do grafo já usa). A cópia para IA também leva os segundos com 3 casas. (produto)
7. A interface é só leitura: a única escrita é a aprovação (F8). Ela nunca altera `plan.json`, fontes, proxies ou artefatos. (segurança; já fechado, SEC-01)
8. Depois de uma recarga com hash diferente, o cursor fica no mesmo tempo da montagem (limitado à nova duração), e a seleção ativa é descartada com aviso. Não há reassociação automática de seleção nesta phase. (escopo)
9. O servidor encerra sozinho depois de 2 h sem requisições, para não deixar processos esquecidos. (operação; reversível)
10. Aprovação passa para `schema_version` 2, sem `preview_sha256`. Um `approval.json` v1 é tratado como obsoleto (`APPROVAL_STALE`) e pede nova aprovação. A versão da CLI vai para 2.0.0, porque os envelopes de `approve` e `render full` perdem `preview_sha256` e `approve` deixa de exigir preview. `schema_version` do plano segue 2. (contrato; já fechado, APPROVAL-01)
11. A aprovação pela interface vincula o `plan_sha256` exibido. Também vincula a impressão leve das fontes (tamanho e `mtime_ns`) do carregamento. Se qualquer um tiver mudado no momento do clique, a aprovação é recusada. O `approval.json` continua guardando o SHA-256 completo das fontes, calculado no clique. (segurança)
12. Interface em PT-BR, com atalhos de teclado no padrão do Premiere: espaço para play e pausa, setas esquerda e direita para quadro, setas cima e baixo para corte anterior e seguinte. (produto)
13. Uma sessão de navegador tem no máximo um stream do player, um do visualizador de fonte e uma extração de quadro em andamento. Um pedido novo do mesmo tipo encerra o anterior. O servidor aceita no máximo 2 sessões simultâneas; conta como simultânea a sessão que fez pedido nos últimos 120 s. Quando chega uma terceira e há sessão ociosa além disso, a ociosa mais antiga é descartada (o cookie dela passa a ser recusado); sem sessão ociosa, a nova recebe `UI_SESSION_LIMIT`. O token da URL vale enquanto a instância viver. (operação)

## Escopo e comportamento

### 1. Fluxo F1: abrir e encerrar a interface pela CLI

- Jornada: a IA abre a interface para o operador.
- Rota: N/A (CLI).
- Gatilho: `editor ui start --workdir <absoluto> [--no-open] --json` e `editor ui stop --workdir <absoluto> --json`.
- Pré-condição: workdir válido e seguro, com `plan.json`. O plano pode estar inválido (F5 mostra o erro).
- Superfície por passo: CLI (stdout JSON); abertura do navegador padrão como redirect para a tela da interface.
- Passos:
  1. `ui start` valida o workdir → inicia o servidor em segundo plano, preso a `127.0.0.1` numa porta livre, e devolve na hora o envelope com `data.state: "started"`, `url` (com token) e `pid`. Sem `--no-open`, abre o navegador padrão na URL. [superfície: redirect]
  2. `ui start` com instância viva e respondendo no mesmo workdir → devolve `state: "running"` com a mesma `url`, sem novo processo. [superfície: redirect]
  3. `ui stop` → encerra o servidor e os FFmpeg filhos e devolve `state: "stopped"`. Sem instância viva, devolve `state: "not_running"` com exit 0. [superfície: CLI]
  4. Inatividade de 2 h → o servidor encerra sozinho e limpa seu estado. [superfície: CLI, sem saída]
- Validações: workdir pela mesma regra de `validate_workdir`; registro da instância só em arquivo regular do workdir, sem links nem reparse points; instância registrada com processo morto é tratada como ausente.
- Erros: `WORKDIR_INVALID` (trabalho inseguro ou ausente); `UI_START_FAILED` (servidor não ficou pronto no prazo); `ARGUMENT_INVALID` (argumento fora da allowlist).
- Estados: started, running, stopped, not_running, erro.
- Aceite: A1.
- Reutilizar: `validate_workdir`, `contracts.success_envelope`/`error_envelope`, `process.py` sem shell, `webbrowser` da biblioteca padrão.

### 2. Fluxo F2: sessão local protegida

- Jornada: o operador abre a URL entregue pela IA.
- Rota: `/?token=<token>` → `/`.
- Gatilho: o navegador acessa a URL de F1.
- Pré-condição: servidor de F1 vivo.
- Superfície por passo: redirect, depois tela.
- Passos:
  1. Pedido com token válido → define cookie de sessão `HttpOnly`, `SameSite=Strict` e `Path=/`, com nome que inclui a porta da instância (cookies não são isolados por porta, então duas instâncias abertas no mesmo navegador não podem sobrescrever a sessão uma da outra), e redireciona para `/` sem token na URL. [superfície: redirect]
  2. Pedido sem cookie válido ou com token inválido → página de erro curta, sem dados do trabalho. [superfície: tela]
- Validações: `Host` igual a `127.0.0.1:<porta>`; token comparado em tempo constante; `Origin`, quando presente, igual à própria origem; POST exige `Origin` igual à própria origem.
- Erros: `UI_TOKEN_INVALID` (401, "Sessão inválida. Abra a interface de novo pela CLI."); `UI_HOST_INVALID` (403); `UI_ORIGIN_INVALID` (403); `UI_SESSION_LIMIT` (429, terceira sessão).
- Estados: autenticado, sessão inválida.
- Aceite: A2.
- Reutilizar: `contracts.error_envelope` para as respostas JSON da API.

### 3. Fluxo F3: player ao vivo da montagem

- Jornada: assistir à montagem.
- Rota: `/` (área do player).
- Gatilho: play, atalho ou clique na timeline.
- Pré-condição: plano válido carregado.
- Superfície por passo: tela (player) e inline (indicadores).
- Passos:
  1. Play no tempo T → o servidor inicia o FFmpeg com o grafo da montagem a partir de T, na resolução escolhida, e transmite. O player toca vídeo e áudio, e o timecode mostra o tempo da montagem (T mais o tempo do stream). Enquanto o primeiro quadro não chega, o player mostra estado "carregando". [superfície: tela]
  2. Pausa → o player para. Com pausa por mais de 30 s, o stream é encerrado no servidor e o play seguinte reinicia em T. [superfície: tela]
  3. Quadro a quadro (seta esquerda/direita) pausado → avança ou volta um quadro dentro do trecho já transmitido. Fora dele, mostra o quadro composto daquele tempo (F4, passo 2). [superfície: tela]
  4. Salto de corte (seta cima/baixo) → move o cursor para o início do segmento anterior ou seguinte e, se estava tocando, reinicia o stream ali. [superfície: tela]
  5. Troca de resolução → reinicia o stream no tempo atual na nova resolução e mantém play ou pausa. A seleção fica no topo do player e é lembrada por trabalho no navegador. [superfície: inline]
  6. Fim da timeline → o player para no último quadro. [superfície: tela]
- Validações: T finito em `[0, duração]`; resolução na allowlist `full`, `1/2`, `1/3`, `1/4`.
- Erros: `PLAYBACK_FAILED` (FFmpeg falhou; mensagem segura inline, "Não foi possível reproduzir este trecho." e o botão tentar de novo); `PLAN_INVALID` mantém o player na última versão válida (F5).
- Estados: carregando, tocando, pausado, erro inline, proxy em uso (indicador), versão desatualizada (F5).
- Aceite: A3, A4.
- Reutilizar: `build_ffmpeg_graph`, `_select_proxies`/`lookup_proxy`, `plan_decode_windows`, transporte do filtro por arquivo em `.tmp`.

### 4. Fluxo F4: timeline, scrub e seleção de trecho

- Jornada: localizar e selecionar um trecho da montagem.
- Rota: `/` (timeline embaixo do player).
- Gatilho: clique ou arraste na timeline.
- Pré-condição: plano válido carregado.
- Superfície por passo: tela (timeline em canvas) e drawer lateral (painel do trecho).
- Passos:
  1. A timeline mostra régua com timecode e faixas separadas para: segmentos de vídeo (com `source_id`), camadas por tipo (texto, imagem, vídeo), áudio dos segmentos (com J/L passando do limite visual do segmento), trilhas de áudio adicionais e legendas (`occurrences`). Tem zoom horizontal até a largura de quadro. [superfície: tela]
  2. Clique ou arraste do cursor (scrub) → pausa o player, move o cursor e mostra o quadro composto daquele tempo, extraído pelo mesmo grafo na resolução escolhida. Durante o arraste, só o último pedido é atendido. [superfície: tela]
  3. Arraste com Shift, ou as teclas I e O no cursor → define início e fim da seleção. Clique num bloco com Alt seleciona o intervalo exato daquele segmento, camada ou legenda. [superfície: tela]
  4. Com seleção ativa → o painel lateral mostra o intervalo da montagem, a duração e cada segmento, camada e legenda sobrepostos, com fonte e intervalo na fonte. O botão "Reproduzir seleção" toca do início ao fim da seleção e para. [superfície: drawer]
  5. Esc → limpa a seleção. [superfície: drawer]
- Validações: seleção com fim maior que o início em pelo menos um quadro, dentro da duração.
- Erros: `FRAME_FAILED` (quadro não extraído; o cursor fica e a área do player mostra aviso inline).
- Estados: sem seleção (painel com instrução curta), com seleção, carregando quadro.
- Aceite: A5.
- Reutilizar: `timeline`, `occurrences` e `profile` de `validate_plan`.

### 5. Fluxo F5: recarga automática quando a IA altera o plano

- Jornada: conferir a alteração feita pela IA.
- Rota: `/`.
- Gatilho: mudança no `plan.json` ou na impressão de uma fonte.
- Pré-condição: interface aberta.
- Superfície por passo: toast e inline (faixa de estado no topo).
- Passos:
  1. O servidor revalida o plano quando o `plan.json` ou uma fonte declarada muda (tamanho ou `mtime_ns`), com no máximo 1 s de atraso, e expõe o novo estado. [superfície: inline]
  2. Plano válido com hash novo, ou mesmo hash com impressão de fonte nova → a interface recarrega timeline, painel e player sozinha, mantém o cursor no mesmo tempo (limitado à nova duração), descarta a seleção e mostra o toast "Plano atualizado". Se estava tocando, retoma no mesmo tempo. [superfície: toast]
  3. Plano inválido → a faixa de estado mostra o código e a mensagem segura do erro. A interface continua exibindo a última versão válida, marcada como desatualizada, e a aprovação fica desabilitada. [superfície: inline]
  4. Plano volta a ficar válido → recarrega como no passo 2 e limpa a faixa de erro. [superfície: toast]
- Validações: a mesma `load_validated_plan` da CLI, sem regra própria.
- Erros: os códigos públicos de `plan validate` (`PLAN_INVALID`, `SOURCE_INVALID`, `MEDIA_UNSUPPORTED` etc.), exibidos sem ecoar conteúdo.
- Estados: em dia, atualizado, inválido (desatualizado), sem plano (`PLAN_REQUIRED` exibido; player e timeline vazios).
- Aceite: A6.
- Reutilizar: `load_validated_plan`.

### 6. Fluxo F6: visualizador da fonte inteira

- Jornada: conferir se a IA não tirou algo errado.
- Rota: `/` (modo "Fonte", alternado ao modo "Montagem" no topo do player).
- Gatilho: escolher uma fonte na lista do modo Fonte, ou "Ver fonte" no painel de um segmento.
- Pré-condição: plano válido carregado.
- Superfície por passo: tela (player e barra da fonte) e drawer (painel do trecho).
- Passos:
  1. O modo Fonte lista as fontes de vídeo e áudio do plano por `source_id`, com duração e quantas vezes cada uma é usada. [superfície: tela]
  2. Escolher uma fonte → o player toca a fonte original inteira (proxy nas frações, quando válido), transcodificada ao vivo, com o mesmo controle de F3. A barra embaixo mostra a duração inteira da fonte com os intervalos usados (segmentos, trilhas e camadas de vídeo, como `source_intervals`) destacados e o restante como descartado. [superfície: tela]
  3. "Ver fonte" a partir de um segmento → abre a fonte no `in_s` daquele segmento, com o intervalo dele destacado. [superfície: tela]
  4. Seleção na barra da fonte (mesmos gestos de F4) → o painel mostra o intervalo da fonte, se está usado, descartado ou parcialmente usado, e onde cada parte usada aparece na montagem. [superfície: drawer]
  5. Clique num intervalo usado com Alt → volta ao modo Montagem no tempo correspondente. [superfície: tela]
- Validações: `source_id` pelo padrão vigente e existente no plano; fontes de imagem não entram na lista.
- Erros: `PLAYBACK_FAILED` como em F3; `SOURCE_INVALID` se a fonte sumiu (F5 também sinaliza).
- Estados: sem fonte escolhida, tocando, pausado, erro inline; fonte só de áudio mostra player sem imagem com a barra de uso.
- Aceite: A7.
- Reutilizar: `windows.source_intervals`, `lookup_proxy`.

### 7. Fluxo F7: copiar trecho para a IA

- Jornada: escrever o prompt de alteração para a IA.
- Rota: `/` (botão "Copiar para IA" no painel lateral).
- Gatilho: clique no botão ou Ctrl+Shift+C com seleção ativa.
- Pré-condição: seleção ativa em F4 ou F6.
- Superfície por passo: drawer e toast.
- Passos:
  1. Clique → copia para a área de transferência um bloco de texto em PT-BR, estável e legível por humano e máquina, e mostra o toast "Trecho copiado". [superfície: toast]
  2. Sem permissão de área de transferência → abre o texto num campo selecionável dentro do painel, com instrução para copiar manualmente. [superfície: drawer]
- Conteúdo do bloco (montagem):
  - cabeçalho com nome da pasta do trabalho e `plan_sha256`;
  - intervalo da montagem em timecode e em segundos com 3 casas, e a duração;
  - um item por segmento sobreposto: índice (base 1), `source_id` e intervalo na fonte correspondente à parte selecionada;
  - um item por camada sobreposta: índice em `layers[]` (base 1), tipo e, no texto, os primeiros 60 caracteres;
  - um item por legenda sobreposta: índice do item e texto;
  - uma linha final `Pedido:` vazia, para o operador completar.
- Conteúdo do bloco (fonte): cabeçalho igual, `source_id`, intervalo da fonte, situação (usado, descartado, parcial) e, para cada parte usada, o intervalo da montagem e o segmento.
- Validações: o texto nunca inclui caminhos absolutos, conteúdo de fontes ou token.
- Erros: N/A (falha de cópia cai no passo 2).
- Estados: copiado, cópia manual.
- Aceite: A8.
- Reutilizar: o mapeamento entre montagem e fonte da seleção é calculado no servidor (Python, a mesma fonte dos dados de F4 e F6, testável por unittest); a interface só envia o intervalo e copia o texto devolvido.

### 8. Fluxo F8: aprovar pela interface e contrato de aprovação sem preview

- Jornada: aprovar a versão revisada e pedir a full.
- Rota: `/` (botão "Aprovar esta versão" no topo) e `POST /api/approve`.
- Gatilho: clique no botão; ou `editor approve --workdir <absoluto> --json` pela CLI.
- Pré-condição: plano válido, em dia (não desatualizado).
- Superfície por passo: popup (confirmação), toast e inline (selo de estado).
- Passos:
  1. Clique → popup de confirmação com `plan_sha256` curto, duração e a nota "visualizado com proxy" quando a última reprodução usou proxy. [superfície: popup]
  2. Confirmar → a interface envia o `plan_sha256` e a `sources_fingerprint` (SHA-256 da impressão das fontes, tamanho e `mtime_ns` por `source_id`, como o estado a entregou) da versão exibida. O servidor revalida o plano, compara os dois com os atuais, calcula os SHA-256 das fontes, confere a impressão de novo depois do cálculo e publica `approval.json` v2 atômico. O topo passa a mostrar o selo "Aprovado" com o instante. [superfície: toast]
  3. Hash ou impressão divergentes → recusa sem gravar e mostra "O plano mudou desde que foi carregado. Revise a versão atual antes de aprovar." A interface recarrega pelo F5. [superfície: inline]
  4. Plano alterado depois de aprovado → o selo vira "Aprovação desatualizada". [superfície: inline]
  5. CLI `editor approve` → mesmo registro v2, sem exigir `preview.mp4` e sem campo `preview_sha256` no envelope. [superfície: CLI]
  6. `render full` → verifica `approval.json` v2 contra plano e fontes, sem olhar `preview.mp4`. Registro v1 ou divergente → `APPROVAL_STALE`. [superfície: CLI]
  7. `render preview` → segue disponível como exportação opcional, sem efeito na aprovação. [superfície: CLI]
- Validações: corpo JSON só com `plan_sha256` e `sources_fingerprint` (64 hex minúsculos cada), até 1 KiB; `Origin` e cookie de F2.
- Erros: `APPROVAL_PLAN_CHANGED` (409, divergência do passo 3); `PLAN_INVALID` (409, plano inválido no clique); `SOURCE_INVALID` (fonte ilegível); `APPROVAL_STALE` e `APPROVAL_REQUIRED` na full, como hoje.
- Estados: não aprovado, confirmando, aprovado, desatualizado, recusado.
- Aceite: A9, A10.
- Reutilizar: `approve_workdir`, `verify_approval`, `publish_approval_atomic`, `_hash_sources`.

### Fora

- Edição por arraste na timeline e escrita no `plan.json` pela interface: a IA é a única que altera o plano.
- Compositor no navegador: duplicaria os efeitos do grafo (UI-01).
- Disparar `render full` ou `proxy build` pela interface: a full continua pedida à IA.
- Identidade estável de segmentos e camadas no schema e reassociação de seleção depois de recarga: a cópia usa tempos da fonte e hash, estáveis; entra quando a falta pesar no uso.
- Forma de onda do áudio na timeline: não pedida; pode entrar depois.
- Anotações persistidas, lista de observações, marcação de região no quadro, chat integrado e colaboração.
- Acesso por outra máquina ou rede: o servidor só escuta em `127.0.0.1`.

## Direção visual

- Modo: greenfield (o projeto não tem interface nem design system).
- Tom: denso e sóbrio. Uma ferramenta profissional de edição no padrão do Premiere, com tema escuro, player dominante, timeline embaixo e painel lateral recolhível.
- Evitar: aparência de template genérico, cards e espaçamento de landing page, cores decorativas que disputem com o vídeo, texto pequeno demais na timeline.
- Copy de UI: PT-BR, vocabulário de edição ("montagem", "fonte", "trecho", "corte", "camada", "legenda"); erros com a mensagem segura do código, sem jargão interno.
- Prova: navegação real no navegador com screenshots do player em montagem e em fonte, timeline com seleção, painel, toast de recarga e popup de aprovação.

## Checklist de entrega

### Aceite

- [x] A1: `ui start` devolve na hora o envelope com `url` e `pid` e abre o navegador (ou não, com `--no-open`); um segundo `start` reaproveita a instância (`running`); `ui stop` encerra o servidor e os FFmpeg filhos (`stopped`, ou `not_running` sem instância); o servidor encerra sozinho após 2 h sem requisições.
- [x] A2: sem cookie ou com token inválido, toda rota responde `UI_TOKEN_INVALID` sem dados do trabalho; `Host` diferente de `127.0.0.1:<porta>` recebe `UI_HOST_INVALID`; POST sem `Origin` próprio recebe `UI_ORIGIN_INVALID`; a terceira sessão recebe `UI_SESSION_LIMIT`; o token sai da URL depois do primeiro acesso; as respostas trazem CSP restritiva, `nosniff`, `Referrer-Policy: no-referrer` e `frame-ancestors 'none'`.
- [x] A3: em Full, os quadros que o grafo do player produz a partir de T (vídeo cru, antes do encoder de transmissão) são idênticos aos do grafo da full nos mesmos tempos (mesmo grafo e mesmo canvas), e o PCM também; o stream codificado tem as dimensões do canvas, e em 1/2 o quadro tem metade das dimensões, arredondada a par. A compressão do encoder de transmissão é a única diferença aceita entre o que o player mostra e a full.
- [x] A4: play em qualquer tempo da timeline mostra o primeiro quadro naquele tempo, com timecode da montagem correto; pausa, quadro a quadro, salto de corte e troca de resolução se comportam como em F3; com proxy em uso o indicador aparece.
- [x] A5: a timeline mostra as faixas de F4 com J/L passando do limite do segmento; scrub mostra o quadro composto do tempo; a seleção por arraste, I/O e Alt+clique preenche o painel com intervalos da montagem e da fonte corretos.
- [x] A6: alterar o `plan.json` com a interface aberta recarrega sozinho em até 2 s, mantém o cursor e mostra o toast; um plano inválido mostra o erro, mantém a última versão válida como desatualizada e desabilita a aprovação.
- [ ] A7: no modo Fonte, cada fonte de vídeo e áudio toca inteira, com usados e descartados na barra iguais aos intervalos do plano; "Ver fonte" e Alt+clique levam entre montagem e fonte nos tempos corretos.
- [ ] A8: "Copiar para IA" coloca na área de transferência o bloco de F7 com hash, timecodes, segundos, segmentos, camadas e legendas corretos para a seleção, sem caminho absoluto nem token.
- [ ] A9: o botão aprova a versão exibida e grava `approval.json` v2 (sem `preview_sha256`); com o plano ou uma fonte alterados depois do carregamento, recusa com `APPROVAL_PLAN_CHANGED` sem gravar; o selo mostra aprovado e, após nova alteração, desatualizado.
- [x] A10: `editor approve` funciona sem `preview.mp4`; `render full` aceita a aprovação v2 sem preview e recusa v1, mutação de plano e mutação de fonte com `APPROVAL_STALE`, preservando a full vigente; `render preview` continua funcionando e não afeta a aprovação.
- [ ] A11: `capabilities` expõe `operations.ui` (comandos, resoluções, limites de sessão e de inatividade) e o contrato de aprovação v2, lidos das constantes; skill `vibe-editor`, suas referências e `AGENTS.md` descrevem o fluxo ui start → revisão → aprovação → full sem contradizer o código.

### Critérios de sucesso

- [ ] C1: suíte completa verde, com as provas de A3 (quadros e áudio do player iguais aos da full) e A10 (aprovação v2).
- [ ] C2: gitleaks e pip-audit sem achados; nenhuma dependência nova no `requirements.lock`.
- [x] C3: na máquina de referência (i5-13600K), com a fonte sintética 4K do `bench_render.py` e proxy, em 1/2: o primeiro quadro aparece em até 3 s depois do play, sem crescer com a posição na timeline (início, meio e fim de uma timeline de 60 s), e 30 s tocam sem pausa de buffer.
- [ ] C4: versão 2.0.0, com a quebra (aprovação v2 e `preview_sha256` removido dos envelopes) descrita em `AGENTS.md`.
- [ ] C5: jornada real no navegador do início ao fim: `ui start`, play, seleção, cópia, alteração do plano por fora, recarga, aprovação e `render full` aceitando a aprovação.

## Contratos e restrições necessárias

- Dados e invariantes:
  - O hash canônico do plano não muda: resolução, proxy, cursor e seleção nunca entram no plano nem na aprovação.
  - `approval.json` v2 tem exatamente `schema_version` (2), `approved_at`, `plan_sha256` e `sources` (`source_id`, `sha256`), publicado atômico como hoje.
  - O player e a full usam a mesma função de grafo; quadros em Full são idênticos aos da full.
  - O estado da instância (porta, pid, token) fica em arquivo regular dentro de `.tmp` do workdir e é removido no `stop` e no encerramento por inatividade.
- Integrações e interfaces:
  - CLI: `editor ui start --workdir <absoluto> [--no-open] --json` → `data`: `state` (`started` ou `running`), `url`, `pid`, `port`. `editor ui stop --workdir <absoluto> --json` → `data.state` (`stopped` ou `not_running`).
  - `editor approve` e `render full` perdem `preview_sha256` nos envelopes (quebra, major 2.0.0).
  - API HTTP interna: a forma das rotas é escolha do plan; respostas JSON usam o envelope padrão (`ok`, `data` ou `error.code`/`error.message`). A interface não é contrato externo.
- Segurança e operação:
  - Servidor só em `127.0.0.1`; token de 32 bytes aleatórios por instância, comparado em tempo constante; cookie `HttpOnly`, `SameSite=Strict`; checagem de `Host` em todo pedido; POST exige `Origin` próprio; sem CORS (nenhuma origem externa permitida).
  - Entradas aceitas (allowlist): métodos GET e POST; rotas fixas definidas pelo plan; query `t`, `start` e `end` (decimais finitos em `[0, duração]` da montagem ou da fonte consultada, até 32 caracteres cada, `end` maior que `start`), `res` (`full`, `1/2`, `1/3`, `1/4`), `source_id` (padrão `^[a-z0-9][a-z0-9_-]{0,63}$`, existente no plano), `token` (só na entrada, até 128 caracteres); query string até 2 KiB; corpo só no POST de aprovação, JSON só com os campos `plan_sha256` e `sources_fingerprint` (64 hex cada), até 1 KiB; qualquer outro campo, rota ou método é recusado.
  - Arquivos servidos: só os estáticos empacotados da interface; mídia sai só pelo FFmpeg a partir das fontes e proxies declarados, nunca por caminho vindo do navegador.
  - Processos: FFmpeg com argumentos estruturados, sem shell, com timeout e encerramento junto com o stream ou a sessão; no máximo 3 FFmpeg por sessão e 2 sessões.
  - Headers: CSP `default-src 'self'; media-src 'self' blob:; img-src 'self' blob: data:; frame-ancestors 'none'` sem script inline; `X-Content-Type-Options: nosniff`; `Referrer-Policy: no-referrer`. HSTS N/A (HTTP local, sem TLS).
  - Rate limit N/A além dos tetos de sessão e de processos: servidor local de um operador, autenticado por token.
  - Erros sem stack trace, caminho absoluto, conteúdo de fonte ou token; logs sem o token.
- Tecnologia imposta: servidor só com a biblioteca padrão do Python; interface em HTML, CSS e JavaScript puros, sem etapa de build e sem dependência de CDN, empacotada em `src/editor`; FFmpeg local já exigido.

## Como provar

| Aceite | Evidência esperada |
|---|---|
| A1 | Processo real: envelopes de `start` (duas vezes) e `stop`, pid inexistente depois do stop, nenhum FFmpeg órfão; encerramento por inatividade com prazo reduzido em teste |
| A2 | Pedidos HTTP reais sem cookie, com token errado, `Host` trocado, POST com `Origin` estranho e terceira sessão, com os códigos esperados e headers presentes |
| A3 | `framemd5` do vídeo cru e do PCM do grafo do player a partir de T iguais aos do grafo da full nos mesmos tempos; dimensões do stream em Full, 1/2, 1/3, 1/4 por FFprobe |
| A4 | Stream pedido em vários tempos começa no quadro esperado; navegação real no navegador com screenshots de play, pausa, quadro a quadro, salto de corte e troca de resolução |
| A5 | Screenshot da timeline com J/L e legendas; painel com intervalos conferidos contra `plan validate` |
| A6 | Edição do `plan.json` durante a navegação real: toast e cursor mantido; plano quebrado: faixa de erro e aprovação desabilitada |
| A7 | Barra de uso conferida contra `source_intervals`; navegação montagem ↔ fonte nos tempos corretos |
| A8 | Texto copiado lido da área de transferência e conferido campo a campo contra o plano |
| A9 | `approval.json` v2 gravado pelo botão; recusa com plano e com fonte alterados, sem arquivo novo |
| A10 | `approve` sem preview; `render full` aceita v2 e recusa v1, plano e fonte alterados, preservando a full |
| A11 | `capabilities --json` conferido contra as constantes; teste da skill e revisão de `AGENTS.md` |

## Boundaries

### Always

- Usar o mesmo `build_ffmpeg_graph` da full no player; qualquer diferença de quadro em Full na saída do grafo (antes do encoder) é defeito.
- Validar toda entrada HTTP na borda com a allowlist acima antes de abrir arquivo ou processo.
- Revalidar o plano pela `load_validated_plan` da CLI, sem regra paralela.
- Encerrar FFmpeg filhos quando o stream, a sessão ou o servidor acabam.

### Ask first

- Se a prova técnica não atingir C3, antes de mudar o mecanismo (B) ou relaxar o critério.
- Qualquer escrita da interface além da aprovação.
- Acrescentar dependência ao `requirements.lock` ou ferramenta de build para a interface.
- Escutar fora de `127.0.0.1` ou permitir outra origem.

### Never

- Reimplementar efeitos do grafo no navegador.
- Servir arquivo por caminho recebido do navegador ou do plano sem passar pelas fontes declaradas.
- Colocar token, caminho absoluto ou conteúdo de fonte em log, erro ou texto copiado.
- Incluir resolução, proxy ou estado da interface no hash do plano ou na aprovação.

## Handoff

vibe-design

- Chat: `interview`, `spec` e `design` podem continuar no mesmo chat. Recomende novo chat para `vibe-plan`; se o humano preferir, continuar no mesmo chat não bloqueia. O `spec.md` vivo é a ponte.

- [x] Aprovação humana (leu o arquivo e confirmou)
