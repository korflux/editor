# Design: interface de revisão no navegador com player ao vivo
# Alvo: phase-7-interface-de-revisao-no-navegador
# Status: aprovado

## Entrada

- Spec: `.vibeflow/phases/phase-7-interface-de-revisao-no-navegador/spec.md` (aprovada)
- Interview: `.vibeflow/phases/phase-7-interface-de-revisao-no-navegador/interview.md`
- Modo: greenfield
- Referências de leitura: N/A. O projeto não tem interface, DS nem DESIGN.md. A referência é o padrão de layout do Adobe Premiere (player dominante, timeline embaixo, painel lateral), citado pelo operador; nada é copiado dele.
- Uso: criar

## Inventário de telas

| Tela | Jornada F | Rota | Componentes candidatos | Origem |
|---|---|---|---|---|
| Revisão, modo Montagem | F3, F4, F5, F7, F8 | `/` | barra superior, player, barra de transporte, timeline em canvas, painel do trecho, faixa de estado, toast, popup de aprovação | kit novo |
| Revisão, modo Fonte | F6, F7 | `/` (alternância no topo do player) | lista de fontes, player, barra de uso da fonte, painel do trecho | kit novo, mesmo shell do modo Montagem |
| Sessão inválida | F2 | qualquer rota sem sessão | página de mensagem única | kit novo |
| Popup de aprovação | F8 | `/` (sobreposto) | modal de confirmação | kit novo |

## Kit mínimo

Tema escuro único. Uma ferramenta de edição vive em ambiente escuro para não competir com o vídeo; não há tema claro nesta phase. Os valores são finais, para a implement não inventar.

- Cores (papel → token → valor):
  - Fundo da página e do player → `--bg-base` → `#121214`
  - Painéis (topo, lateral, timeline) → `--bg-panel` → `#1b1b1f`
  - Superfície elevada (popup, menu, toast) → `--bg-raised` → `#26262c`
  - Hover e foco de linha → `--bg-hover` → `#2f2f36`
  - Borda e divisória → `--border` → `#34343c`
  - Texto principal → `--text` → `#e6e6ea`
  - Texto de apoio → `--text-muted` → `#9a9aa6`
  - Ação primária (aprovar, botão principal) → `--accent` → `#4c8dff`, texto `#0b1220`
  - Cursor da timeline (playhead) → `--playhead` → `#ff5c5c`
  - Seleção de trecho → `--selection` → `#4c8dff` a 22% de opacidade, borda `#4c8dff`
  - Sucesso (aprovado) → `--ok` → `#3ecf8e`
  - Aviso (desatualizado, proxy) → `--warn` → `#f2b84b`
  - Erro → `--danger` → `#ff6b6b`
  - Faixas da timeline: vídeo `--lane-video` `#3b6fd8`, áudio `--lane-audio` `#2fa37a`, texto `--lane-text` `#b46ad6`, imagem e vídeo em camada `--lane-layer` `#d08a3a`, legenda `--lane-caption` `#c9b458`, descartado (modo Fonte) `--lane-discarded` `#3a3a42`
  - Todo texto sobre fundo atinge contraste mínimo de 4,5:1 (o `--text-muted` sobre `--bg-panel` passa).
- Tipografia: pilha do sistema (`system-ui, "Segoe UI", sans-serif`), sem fonte externa, porque a CSP não permite CDN. Timecode e números em `ui-monospace, "Cascadia Mono", Consolas, monospace` com `font-variant-numeric: tabular-nums`, para não tremer durante a reprodução.
  - Título da barra superior: 14px, peso 600
  - Corpo e controles: 13px, peso 400
  - Apoio e rótulos de faixa: 12px
  - Timecode do player: 20px monoespaçado
  - Régua da timeline: 11px monoespaçado (menor tamanho permitido)
- Espaçamento e raio: escala 4, 8, 12, 16, 24 px. Painéis colados, sem margem entre si e separados por borda de 1px. Raio 4px em botões, campos e blocos da timeline; 8px em popup e toast. Nenhum card com sombra decorativa.
- Componentes base:
  - Botão: primário (`--accent`), secundário (borda `--border`, fundo transparente) e ícone (32x32, com nome acessível e tooltip). Altura 28px.
  - Grupo segmentado: alternância Montagem/Fonte e seletor de resolução.
  - Toast: canto inferior direito acima da timeline, 4 s, um por vez, com `role="status"`.
  - Faixa de estado: barra de 32px sob a barra superior, só quando há aviso ou erro, com `role="alert"` no erro.
  - Modal: centrado, largura 420px, com foco preso, Esc fecha e foco volta ao botão de origem.
  - Painel lateral: 320px, recolhível pelo botão da barra superior ou pela tecla `\`.
  - Empty state: texto de apoio centralizado com uma frase de instrução, sem ilustração.

## Tela: Revisão, modo Montagem

- Hierarquia de cima para baixo:
  1. Barra superior (44px): nome da pasta do trabalho, `plan_sha256` curto (8 caracteres, com o completo no tooltip), selo de aprovação, alternância Montagem/Fonte, seletor de resolução, botão "Aprovar esta versão" e botão de recolher o painel.
  2. Faixa de estado (32px, condicional): plano inválido, versão desatualizada ou fonte ausente.
  3. Área central: player à esquerda, que ocupa o espaço restante, e painel do trecho à direita (320px).
  4. Barra de transporte (40px), colada sob o player.
  5. Timeline (34% da altura da janela, mínimo 220px).
- Layout e grid: CSS grid de janela inteira (`100dvh`, sem rolagem da página). Linhas: barra superior, faixa de estado (auto), área central (1fr), timeline. A área central tem colunas `1fr 320px`, ou `1fr` com o painel recolhido. O vídeo é centralizado com `object-fit: contain` e barras `--bg-base`, mantendo a proporção do perfil (16:9, 9:16, 1:1 ou 4:5). A ordem de leitura e de Tab segue barra superior → transporte → timeline → painel.
- Componente por zona:
  - Barra superior: título em texto, hash e selo inline e grupos segmentados. "Aprovar esta versão" é o único botão primário da tela.
  - Player: `<video>` sem controles nativos. Por cima dele, só o indicador de carregamento (centro) e o selo "Proxy" (canto superior esquerdo, `--warn`, quando aplicável).
  - Transporte, da esquerda para a direita: corte anterior, quadro anterior, play/pausa, quadro seguinte e corte seguinte (ícones); timecode atual / duração (monoespaçado); "Reproduzir seleção" (texto, só com seleção); volume (ícone mais slider).
  - Timeline: régua no topo; faixas na ordem Legendas, Texto, Camadas (imagem e vídeo), V1 Segmentos, A1 Áudio dos segmentos, A2+ Trilhas. Rótulo fixo à esquerda (72px) e área de blocos rolável na horizontal. Controle de zoom (slider mais "Ajustar") no canto superior direito da timeline.
  - Blocos: retângulo com a cor da faixa e texto interno (`source_id` no vídeo, texto inicial no texto e na legenda), truncado com reticências. A parte J/L do áudio fica hachurada além do limite do segmento de vídeo correspondente.
  - Playhead: linha vertical de 1px `--playhead`, com alça na régua.
  - Seleção: retângulo `--selection` em todas as faixas, com alças de início e fim na régua.
  - Painel do trecho: título "Trecho"; intervalo (início, fim e duração em timecode); lista "Segmentos", "Camadas" e "Legendas", com cada item mostrando índice, `source_id` ou tipo e o intervalo na fonte, e o link "Ver fonte" no segmento; botão "Copiar para IA" (secundário, largura total) no rodapé fixo do painel.
- Tokens: barra superior, painel e timeline em `--bg-panel`; player em `--bg-base`; textos `--text` e `--text-muted`; botão de aprovar em `--accent`; faixas com os tokens `--lane-*`; seleção `--selection`; playhead `--playhead`; espaçamento interno de 8px nos painéis e 12px no painel do trecho.
- Iconografia: ícones só no transporte (play, pausa, quadro, corte, volume) e no recolher do painel, todos universais de player, com `aria-label`, tooltip com o atalho ("Quadro seguinte (→)"), área de 32x32 e foco visível (anel de 2px `--accent`). "Aprovar esta versão", "Copiar para IA", "Reproduzir seleção", "Ver fonte" e "Ajustar" mantêm texto. Ícones em SVG inline num sprite local, sem biblioteca externa.
- Motion: playhead e reprodução sem animação (alta frequência). Painel recolhendo: N/A, troca instantânea, porque a ação é frequente. Toast: entrada com opacidade e translação de 8px, `ease-out` em 180ms, propósito feedback. Modal: opacidade e `scale(0.95)` para 1 em 200ms `ease-out`, propósito evitar teleporte. Com `prefers-reduced-motion`, toast e modal aparecem sem movimento.
- Responsivo: destino desktop. De 1024px a 1279px de largura, o painel abre recolhido e vira sobreposição ao abrir. Abaixo de 1024px, a timeline mantém a altura mínima e o player encolhe, os rótulos das faixas viram a sigla (V1, A1, T, L, C) e "Aprovar esta versão" vira "Aprovar". Abaixo de 720px, um aviso inline diz que a interface é para tela de computador, e ela continua usável sem quebrar. A página nunca rola: a rolagem existe só na área de blocos da timeline (horizontal e vertical quando houver muitas trilhas) e na lista do painel. Texto longo trunca com reticências e mostra o completo no tooltip.
- Estados:
  - Sem plano (`PLAN_REQUIRED`): player com empty state "Nenhum plano neste trabalho. Peça à IA para montar o plano." Timeline vazia; aprovar desabilitado.
  - Carregando stream ou quadro: indicador circular no centro do player, sem cobrir o último quadro.
  - Erro de reprodução: mensagem inline no centro do player, "Não foi possível reproduzir este trecho.", com botão "Tentar de novo".
  - Plano atualizado: toast "Plano atualizado".
  - Plano inválido: faixa de estado `--danger` com código e mensagem; selo "Versão desatualizada" ao lado do hash; aprovar desabilitado com tooltip explicando o motivo.
  - Sem seleção: o painel mostra "Selecione um trecho na timeline: arraste com Shift ou use I e O."
  - Aprovado: selo `--ok` "Aprovado às HH:MM". Desatualizado: selo `--warn` "Aprovação desatualizada".
  - Sem permissão: N/A nesta tela (a sessão inválida tem tela própria).
- Prova visual esperada: rota `/`, viewports 1440x900 e 1024x700. Play numa fixture 16:9 e numa 9:16; screenshot com seleção cobrindo um corte J/L e painel preenchido; troca para 1/4 com selo "Proxy" visível; edição externa do `plan.json` mostrando o toast; plano quebrado mostrando a faixa de erro e o aprovar desabilitado. Navegação inteira por teclado com foco visível.

## Tela: Revisão, modo Fonte

- Hierarquia de cima para baixo: mesma barra superior (com "Fonte" ativo no grupo); área central com a lista de fontes (240px, à esquerda do player), o player e o painel do trecho; barra de transporte; barra de uso da fonte no lugar da timeline (altura mínima 120px).
- Layout e grid: área central em colunas `240px 1fr 320px`. A lista some (vira seletor no topo do player) abaixo de 1280px.
- Componente por zona:
  - Lista de fontes: uma linha por fonte de vídeo ou áudio, com `source_id`, tipo (ícone mais texto "vídeo" ou "áudio"), duração e "usada N×". A linha ativa fica em `--bg-hover`, com borda esquerda `--accent`.
  - Player: igual ao modo Montagem; fonte só de áudio mostra fundo `--bg-base` com o `source_id` centralizado.
  - Barra de uso: régua com a duração inteira da fonte; trechos usados em `--lane-video` (ou `--lane-audio` em fonte de áudio) e o restante em `--lane-discarded`; playhead e seleção iguais aos da timeline. Ao passar o cursor sobre um trecho usado, o tooltip mostra "Montagem HH:MM:SS:FF, segmento N".
  - Painel do trecho: intervalo da fonte, situação ("Usado", "Descartado" ou "Parcialmente usado") como texto com marcador de cor, lista das partes usadas com o tempo da montagem e o link "Ir para a montagem", e "Copiar para IA" no rodapé.
- Tokens: os do modo Montagem, mais `--lane-discarded`.
- Iconografia: igual à do modo Montagem; o tipo da fonte na lista sempre com texto ao lado do ícone.
- Motion: N/A, a troca de modo e de fonte é instantânea (ação frequente).
- Responsivo: abaixo de 1280px, a lista vira um `<select>` acima do player; o resto segue as regras do modo Montagem.
- Estados: sem fonte escolhida ("Escolha uma fonte para ver o vídeo inteiro"); fonte ausente (`SOURCE_INVALID` na faixa de estado e linha da lista em `--danger` com "Indisponível"); carregando, erro e proxy como no modo Montagem.
- Prova visual esperada: rota `/`, modo Fonte, viewport 1440x900. Screenshot de uma fonte com trechos usados e descartados conferidos contra o plano; seleção atravessando usado e descartado, com o painel em "Parcialmente usado"; "Ir para a montagem" levando ao tempo correto.

## Tela: Sessão inválida

- Hierarquia de cima para baixo: bloco único centralizado com o título "Sessão inválida", a frase "Abra a interface de novo pela CLI: editor ui start." e nada mais.
- Layout e grid: centralizado vertical e horizontalmente em `--bg-base`, largura máxima 420px.
- Componente por zona: texto apenas, sem botões e sem dados do trabalho.
- Tokens: `--text` no título (14px, peso 600), `--text-muted` na frase.
- Iconografia: N/A.
- Motion: N/A.
- Responsivo: o texto quebra normalmente; nenhum overflow.
- Estados: único.
- Prova visual esperada: acesso a `/` sem cookie, viewport 1024x700, mostrando só a mensagem, sem hash, nome do trabalho ou erro técnico.

## Tela: Popup de aprovação

- Hierarquia de cima para baixo: título "Aprovar esta versão?"; resumo com o hash curto, a duração da montagem e a nota `--warn` "Visualizado com proxy" quando aplicável; frase "A full será renderizada a partir desta versão."; botões "Cancelar" (secundário) e "Aprovar" (primário), alinhados à direita.
- Layout e grid: modal de 420px, padding 16px e espaçamento de 12px entre blocos.
- Componente por zona: modal base do kit.
- Tokens: `--bg-raised`, raio 8px, botão primário `--accent`.
- Iconografia: N/A, as ações mantêm texto.
- Motion: entrada do modal do kit (200ms `ease-out`).
- Responsivo: em viewport estreita, a largura vai a `calc(100vw - 32px)`.
- Estados:
  - Aprovando: botão com indicador e desabilitado.
  - Recusado por mudança: o modal fecha e a faixa de estado mostra "O plano mudou desde que foi carregado. Revise a versão atual antes de aprovar."
  - Sucesso: o modal fecha, o toast diz "Versão aprovada" e o selo muda.
- Prova visual esperada: abrir o modal, cancelar (o foco volta ao botão), aprovar (selo e toast) e, num segundo teste, alterar o plano com o modal aberto e confirmar a recusa.

## Tokens e iconografia

- Tokens por tela: o kit vale para todas as telas; os `--lane-*` só na timeline e na barra de uso; `--playhead` só no cursor; `--accent` só na ação primária, no foco e na linha ativa da lista.
- Regra de ícone: icon-only só nos controles universais do player e no recolher do painel, sempre com nome acessível, tooltip com atalho, área de 32x32 e foco visível. Toda ação de decisão (aprovar, copiar, reproduzir seleção, ver fonte, ir para a montagem) mantém texto.

## Responsivo e estados

- Viewport estreita: abaixo de 1280px, o painel e a lista de fontes viram sobreposição ou seletor; abaixo de 1024px, os rótulos encurtam; abaixo de 720px, aviso inline de tela de computador.
- Overflow e truncamento: a página nunca rola; só a timeline e as listas rolam; textos de bloco, `source_id` e legendas truncam com reticências e tooltip.
- Estados globais: faixa de estado para erro e desatualização; toast para atualização, cópia e aprovação; indicador no player para carregamento.

## Motion e baseline mobile

- Motion por tela: só toast (180ms) e modal (200ms), ambos `ease-out` e removidos com `prefers-reduced-motion`; reprodução, playhead, scrub, painel e modos sem animação.
- Física: só `transform` e `opacity`; o modal entra de `scale(0.95)`; nada anima largura ou altura.
- Acessibilidade: hover só em `@media (hover: hover) and (pointer: fine)`; foco visível em todo controle; atalhos de teclado de F3, F4 e F7 documentados nos tooltips; o canvas da timeline tem rótulo acessível e espelha a seleção no painel, que é texto.
- Baseline mobile: N/A. A ferramenta é desktop; apenas `100dvh` no shell para não gerar rolagem.

## Prova visual esperada

- Por tela: as provas listadas em cada bloco acima, em 1440x900 e 1024x700, via navegação real no navegador com os gestos de cada fluxo.
- Console, rede e assets sem erro: console sem erro e sem violação de CSP; nenhum pedido a domínio externo; todos os estáticos servidos pelo servidor local com status 200.

## Decisões

| Decisão | Origem | Motivo e impacto |
|---|---|---|
| Tema escuro único com valores finais | kit novo | Não competir com o vídeo; evita a implement inventar cores |
| Fontes do sistema e monoespaçada tabular no timecode | kit novo | A CSP proíbe CDN; o timecode não treme na reprodução |
| Ordem das faixas: legendas e camadas acima, vídeo no meio, áudio embaixo | padrão Premiere | Leitura familiar para o operador |
| Painel do trecho como drawer fixo de 320px, recolhível | kit novo | O vídeo fica dominante e o contexto do trecho, sempre a um clique |
| Sem animação em ações frequentes | references/motion.md | Reprodução e scrub precisam de resposta instantânea |
| Icon-only só nos controles universais do player | regra de ícone | Ações de decisão mantêm texto para não gerar ambiguidade |

## Handoff

vibe-plan

- Chat: `interview`, `spec` e `design` podem continuar no mesmo chat. Recomende novo chat para `vibe-plan`; se o humano preferir, continuar aqui não bloqueia. O `design.md` vivo é a ponte e entra no git.

- [x] Aprovação humana (leu o arquivo e confirmou)
