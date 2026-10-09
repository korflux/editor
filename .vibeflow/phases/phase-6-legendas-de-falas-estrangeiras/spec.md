# Spec: legendas de falas estrangeiras a partir da transcrição
# Alvo: phase-6-legendas-de-falas-estrangeiras
# Status: aprovado

## Objetivo

O operador (agente ou humano) legenda, no idioma do público, os trechos de fala em outro idioma de um vídeo, como exige o perfil do canal Sonni Viaja ("legendar toda troca em outro idioma, mesmo curta"). Hoje o agente escreve uma camada `text` por legenda, convertendo à mão o tempo da fala na fonte para o tempo da timeline, e qualquer recorte posterior desalinha tudo. Sucesso é o agente obter da CLI a fala original já quebrada em frases curtas com tempos, escrever só a tradução num bloco `captions` preso ao tempo da fonte, e a preview e a full mostrarem cada legenda exatamente enquanto aquela fala é ouvida, inclusive depois de recortes, reordenação e cortes J/L, sem retrabalho.

## Cobertura da origem

Sem interview em disco: as três decisões de produto foram fechadas pelo humano no chat em 09/10/2026 e estão nas decisões 1 a 3.

| Origem | Destino na spec | Motivo se fora/N/A |
|---|---|---|
| Fala original quebrada em frases curtas com tempos por palavra | F1, decisões 2, 11 a 13 | N/A |
| Bloco `captions` ancorado no tempo da fonte | F2, decisões 1, 4 a 9 | N/A |
| Legenda some quando a fala é cortada e acompanha cortes J/L | F2, decisões 5 a 8 | N/A |
| Estilo único configurável (fonte, tamanho, cor, posição, sombra, contorno) | F2, F3, decisão 9 | N/A |
| Preview e full com a mesma legenda, aprovação por hash | F3, decisão 10 | Comportamento vigente preservado |
| `capabilities`, skill `vibe-editor` e `AGENTS.md` | F4 | N/A |
| Tradução, legenda corrida, legenda embutida como trilha, `.srt` | Fora | Decididos fora pelo humano ou sem demanda |

## Suposições e decisões

1. As legendas ficam num bloco opcional de topo `captions` do `plan.json`, com `style` e `items`. Cada item aponta para a fala na fonte (`source_id`, `in_s`, `out_s`, em segundos da fonte) e traz o texto já no idioma do público (`text`). O editor calcula onde cada item aparece na timeline; o agente nunca escreve tempo de timeline para legenda. (produto; fechado pelo humano)
2. A CLI não traduz e não usa rede nem modelo novo. Ela entrega a fala original quebrada em frases curtas (F1); o agente escreve a tradução e o humano a confere na preview. (produto; fechado pelo humano)
3. Escopo: legendar os trechos que o agente escolher, com um estilo único por plano. Legenda corrida do vídeo inteiro fica fora; o mesmo bloco serve para ela no futuro sem mudar o contrato. (escopo; fechado pelo humano)
4. Uma legenda aparece onde a fala é **ouvida**, não onde a imagem da fonte aparece: o mapeamento segue as faixas de áudio dos segmentos (`segments[].audio.tracks[]`). Assim um corte J ou L, que estende o áudio sobre o segmento vizinho, leva a legenda junto, e um trecho de b-roll com `replace` ou `mute` não mostra a legenda da fonte que não está tocando. (produto)
5. Regra de mapeamento: para cada segmento, cada faixa de áudio com o mesmo `source_id` do item e `loop` falso toca a fonte de `track.in_s` até `track.in_s + audio_duration_s` do segmento, na timeline de `audio_start_s` a `audio_end_s` (as janelas de áudio que `plan validate` já calcula com os cortes J/L). A interseção do intervalo do item com esse trecho da fonte, deslocada para a timeline, é uma **ocorrência**. Faixas com `loop: true` não levam legenda (são música). Um item pode ter várias ocorrências, por exemplo um gancho copiado do próprio episódio ou uma fala atravessada por um jump cut; ocorrências do mesmo item que se encostam na timeline aparecem como uma legenda contínua. (produto)
6. Ocorrência com menos de 0,3 s é omitida, porque não dá tempo de ler e costuma ser sobra de corte. Item sem nenhuma ocorrência não é erro: o plano valida e o envelope lista o item em `unplaced`, para o agente decidir. (produto)
7. Dois itens da mesma fonte com intervalos que se sobrepõem são `PLAN_INVALID`. Duas ocorrências de itens diferentes que se sobrepõem na timeline (o caso de um corte J ou L que faz duas falas legendadas tocarem juntas) também são `PLAN_INVALID`, com a mensagem citando os índices dos itens; o agente resolve encurtando um deles. Tolerância de sobreposição: 1 ms. (produto)
8. Item cujo `source_id` não existe ou cuja fonte não tem áudio é `PLAN_INVALID`. `in_s` e `out_s` devem ser finitos, `0 <= in_s < out_s <= duração da fonte`, com duração de no máximo 30 s por item. (produto e segurança)
9. Estilo (`captions.style`): `font`, `size_px` e `color` obrigatórios, com as mesmas regras da camada `text`; `align` opcional (`left`, `center`, `right`, padrão `center`); `shadow` e `outline` opcionais, com os mesmos campos, faixas e padrões da camada `text` (phase 5); `x`, `y`, `width` e `height` opcionais (normalizados de 0 a 1, com as regras de geometria das camadas), formando a caixa da legenda. Sem caixa declarada vale o padrão da proporção do perfil: `x` 0.05 e `width` 0.9 em todas; `y` 0.80 e `height` 0.14 no 16:9; `y` 0.78 e `height` 0.14 no 1:1 e no 4:5; `y` 0.62 e `height` 0.14 no 9:16, acima da faixa coberta pela interface do Shorts e do Reels. O texto quebra sempre na largura da caixa e fica **ancorado na base da caixa**, para que uma legenda de uma linha e outra de duas tenham a mesma linha de base. Valores em pixels valem na largura da full e a preview os escala como o texto (RENDER-04). (produto)
10. As legendas são desenhadas acima de todas as camadas, na ordem dos itens, e entram no plano canônico e no hash de aprovação como qualquer campo do plano: mudar uma legenda depois da aprovação bloqueia a full com `APPROVAL_STALE`. Janelas de decode e proxies não mudam, porque as legendas não leem mídia. Sem `captions`, o `filter_complex` é byte a byte o anterior. (produto e segurança)
11. A fala original em frases curtas vem de um comando novo, somente leitura: `editor captions suggest`. Ele lê a transcrição publicada da fonte (`analysis/transcription/<source-id>.json`), não escreve arquivo e responde só no `stdout`. (produto)
12. Regra de quebra do `suggest`: percorre as palavras em ordem dentro da janela pedida e fecha a frase quando (a) muda o segmento da transcrição, (b) a próxima palavra passaria de `max_chars` caracteres, (c) a frase passaria de `max_duration_s` segundos, (d) há uma pausa de 0,6 s ou mais até a próxima palavra, ou (e) a palavra termina em `.`, `?`, `!` ou `…`. `in_s` é o início da primeira palavra e `out_s` o fim da última. Padrões: `max_chars` 84 (duas linhas de 42) e `max_duration_s` 6. (produto)
13. A transcrição usada é a publicada para a fonte, num único idioma (a de `analyze transcribe` mais recente). Para legendar fala estrangeira o agente transcreve a fonte no idioma da fala, como a skill já orienta, antes do `suggest`. `ponytail: uma transcrição por fonte, guardar uma por idioma quando o fluxo de cruzar idiomas pesar`. (escopo)
14. Entrega minor: o schema segue `schema_version: 2`, `captions` é opcional e todo plano válido hoje continua válido e gera a mesma preview e a mesma full. (processo)

## Escopo e comportamento

### 1. Fluxo F1: obter a fala original em frases curtas

- Jornada: etapa "analisar a fonte" da skill `vibe-editor`, depois de `analyze transcribe`.
- Rota: `editor captions suggest --workdir <caminho-absoluto> --source-id <id> [--start-s <s>] [--end-s <s>] [--max-chars <n>] [--max-duration-s <s>] --json`.
- Gatilho: o agente localizou um trecho de fala estrangeira e quer o texto original com tempos para traduzir.
- Pré-condição: trabalho válido e transcrição publicada da fonte.
- Superfície por passo: N/A, CLI sem UI; envelope JSON no `stdout` e diagnóstico no `stderr`.
- Passos:
  1. Valida os argumentos na borda -> fora da faixa é recusado antes de ler o trabalho.
  2. Lê e valida a transcrição publicada -> ausente ou fora do formato é recusada.
  3. Quebra as palavras da janela pela decisão 12 -> lista de frases.
  4. Responde -> `data.source_id`, `data.language`, `data.source_sha256` (os da transcrição) e `data.chunks[]`, cada um com `in_s`, `out_s`, `text` (palavras unidas como na transcrição), `words[]` (`word`, `start_s`, `end_s` e `probability` quando houver) e `min_probability` (a menor das palavras, ou `null`). Janela sem palavras devolve `chunks: []` com sucesso.
- Validações: `--source-id` no padrão vigente de source IDs; `--start-s` e `--end-s` finitos, `>= 0`, `start < end` (padrão: a transcrição inteira); `--max-chars` inteiro de 20 a 200; `--max-duration-s` de 1 a 10.
- Erros: `ARGUMENT_INVALID` (argumento fora da faixa, mensagem fixa sem eco); `ANALYSIS_REQUIRED` (estado; "A transcrição da fonte ainda não foi publicada."); `ANALYSIS_INVALID` (estado; "A transcrição publicada da fonte não está no formato esperado."); `WORKDIR_INVALID` (vigente).
- Estados: sucesso com frases; sucesso com `chunks: []`; erro.
- Aceite: A1.
- Reutilizar: validação de `workdir` e source IDs de `workdir.py`, layout `analysis/transcription/` e o formato publicado por `analysis/transcription.py`, envelope e conversão central de erros de `cli.py` e `contracts.py`.

### 2. Fluxo F2: validar um plano com legendas

- Jornada: etapa "escrever e validar `plan.json`".
- Rota: `editor plan validate --workdir <caminho-absoluto> --json` (vigente).
- Gatilho: o agente acrescentou ou alterou `captions` e valida o plano.
- Pré-condição: a vigente do `plan validate`.
- Superfície por passo: N/A, CLI sem UI.
- Passos:
  1. Valida a estrutura de `captions` pela allowlist -> campo desconhecido, tipo errado ou valor fora da faixa é recusado antes de inspecionar mídia.
  2. Valida as fontes e a timeline (vigente) e depois os itens contra as fontes (decisão 8) e entre si (decisão 7).
  3. Mapeia cada item para as ocorrências na timeline (decisões 4 a 6).
  4. Responde -> envelope vigente acrescido de `data.captions` com `items` (quantidade), `occurrences[]` (`item` em base 1, `start_s`, `end_s`, na ordem da timeline) e `unplaced` (índices em base 1 dos itens sem ocorrência). Sem `captions`, o envelope não ganha o campo.
- Validações: `captions` objeto só com `style` e `items`; `style` só com os campos da decisão 9; `items` com 1 a 2.000 itens, cada um só com `source_id`, `in_s`, `out_s` e `text`; `text` string não vazia após `strip`, até 200 caracteres, sem NUL (quebra de linha explícita `\n` é aceita); números finitos e sem booleano.
- Erros: `PLAN_INVALID`, com mensagem fixa que nomeia o campo e a regra sem ecoar o valor recebido (por exemplo "O intervalo da legenda está fora da duração da fonte.", "As legendas 3 e 4 se sobrepõem na timeline.").
- Estados: sem `captions` (envelope vigente), com ocorrências, com itens em `unplaced`.
- Aceite: A2, A3.
- Reutilizar: `reject_unknown_fields`, `require_object`, `require_array`, `_finite_number`, `plan_error`, a validação de fonte, cor, tamanho, alinhamento e `_validate_text_style` da camada `text`, `_validate_layer_geometry` e as janelas `audio_start_s`/`audio_end_s` da timeline.

### 3. Fluxo F3: renderizar preview e full com legendas

- Jornada: etapas "gerar a preview", "aprovar" e "gerar a full".
- Rota: `editor render preview`, `editor approve` e `editor render full` (vigentes, sem argumento novo).
- Gatilho: o operador renderiza um plano válido com `captions`.
- Pré-condição: plano válido (F2).
- Superfície por passo: N/A, CLI sem UI; o resultado visível está nos quadros de `preview.mp4` e `full.mp4`.
- Passos:
  1. Renderiza -> cada ocorrência vira um texto desenhado acima das camadas, de `start_s` a `end_s`, com o estilo da decisão 9.
  2. Publica -> as checagens vigentes de preview e full continuam.
  3. Aprova e gera a full -> a legenda entra no hash do plano; alterar `captions` depois da aprovação bloqueia a full.
- Validações: as vigentes.
- Erros: os vigentes (`RENDER_FAILED`, `APPROVAL_STALE` e demais).
- Estados: os vigentes.
- Aceite: A4, A5.
- Reutilizar: o desenho de texto do grafo (`drawtext` com `fontfile`, escala da preview, sombra e contorno da phase 5).

### 4. Fluxo F4: descobrir e ensinar o recurso

- Jornada: descoberta por `capabilities` e condução pela skill.
- Rota: `editor capabilities --json` (vigente), skill `vibe-editor` e `AGENTS.md`.
- Gatilho: o agente consulta o inventário ou segue a skill.
- Pré-condição: nenhuma.
- Superfície por passo: N/A, CLI e documentação.
- Passos:
  1. `capabilities` expõe a operação `captions_suggest` e `plan.captions` (campos, obrigatórios, padrões de caixa por proporção, limites e regras de mapeamento), lidos das constantes do código.
  2. A skill ensina a jornada: localizar a fala estrangeira, transcrever no idioma dela, `captions suggest`, traduzir, escrever `captions`, conferir `occurrences` e `unplaced` e conferir a legenda nos quadros da preview.
- Validações: N/A, sem entrada nova.
- Erros: N/A.
- Estados: N/A.
- Aceite: A6.
- Reutilizar: estrutura vigente de `capabilities.py` e das referências da skill.

### Fora

- Tradução automática na CLI: decidido pelo humano; o agente traduz.
- Legenda corrida automática do vídeo inteiro: decidido pelo humano; o bloco já suporta quando houver demanda.
- Exportar `.srt`/`.vtt` ou trilha de legenda no MP4: sem demanda; o canal usa legenda queimada.
- Estilo por item, animação, caixa de fundo e karaokê por palavra: um estilo único basta ao canal.
- Medir a largura real dos glifos para limitar linhas: a quebra vigente é aproximada (`ponytail` existente em `ffmpeg_graph.py`).

## Checklist de entrega

### Aceite

- [x] A1: `captions suggest` devolve frases que respeitam `max_chars`, `max_duration_s`, pausa de 0,6 s, pontuação e troca de segmento, com tempos e `min_probability` das palavras, recusa argumentos fora da faixa, transcrição ausente e transcrição fora do formato com os códigos de F1, e não escreve arquivo no trabalho.
- [x] A2: `plan validate` mapeia legendas para a timeline pelas faixas de áudio: num plano com jump cut, reordenação, gancho repetido, corte J, corte L, segmento `replace` e faixa `loop`, `occurrences` traz exatamente os intervalos esperados, omite fragmentos abaixo de 0,3 s e lista em `unplaced` o item totalmente cortado.
- [x] A3: `plan validate` recusa com `PLAN_INVALID`, antes de inspecionar mídia quando a regra é estrutural, campo fora da allowlist, tipos errados, texto vazio ou acima de 200 caracteres, `items` vazio ou acima de 2.000, intervalo inválido ou acima de 30 s, fonte inexistente ou sem áudio, itens da mesma fonte sobrepostos e ocorrências sobrepostas na timeline, sem ecoar valores recebidos.
- [ ] A4: na preview e na full, a legenda aparece nos quadros de cada ocorrência e não aparece fora delas, na caixa padrão da proporção ou na declarada, ancorada na base, com sombra e contorno quando declarados e a mesma fração da largura do quadro na preview e na full; em 16:9 e 9:16.
- [ ] A5: sem `captions` o `filter_complex` é byte a byte o anterior (goldens intactos); com `captions`, alterar um item depois da aprovação faz `render full` falhar com `APPROVAL_STALE` e preservar a full vigente.
- [ ] A6: `capabilities` expõe `operations.captions_suggest` e `plan.captions` lidos das constantes, e a skill, `youtube-editing.md`, `canal-sonni-viaja.md` e `AGENTS.md` descrevem o fluxo e as limitações sem contradizer o código.

### Critérios de sucesso

- [ ] C1: suíte completa verde, com a prova de render real das legendas (A4) e a de mapeamento (A2).
- [ ] C2: gitleaks e pip-audit sem achados.
- [ ] C3: versão minor (1.3.0), sem mudança de `schema_version`.

## Contratos e restrições necessárias

- Dados e invariantes: os tempos de `captions.items` são sempre da fonte; os tempos de timeline existem só no envelope e no grafo. O plano canônico e o hash incluem `captions` como escrito. Planos sem `captions` geram o mesmo grafo e a mesma full de antes.
- Integrações e interfaces: `captions suggest` é somente leitura e responde no envelope JSON vigente; `plan validate` ganha `data.captions` só quando o plano declara legendas; `capabilities` ganha campos aditivos. Nenhum campo existente é removido ou renomeado.
- Segurança e operação: `plan.json` e a transcrição publicada são entradas não confiáveis. Allowlist de campos em `captions`, `captions.style` e em cada item; até 2.000 itens, texto até 200 caracteres sem NUL, números finitos sem booleano, cor só `#RRGGBB`, fonte pelo mesmo resolvedor `fontfile` do texto. O texto chega ao FFmpeg só pelo escape vigente com `expansion=none`, nunca como opção de filtro. No `suggest`, os argumentos têm faixa fechada, o caminho da transcrição é derivado só do source ID validado dentro do trabalho, sem links nem reparse points, e uma transcrição fora do formato é recusada sem ecoar conteúdo. Mensagens de erro fixas, sem stack trace. Sem rede, sem shell, sem processo novo.
- Tecnologia imposta: FFmpeg local com `drawtext` (vigente); nenhuma biblioteca nova.

## Como provar

| Aceite | Evidência esperada |
|---|---|
| A1 | Processo real da CLI sobre uma transcrição publicada sintética: frases e tempos conferidos contra a regra; recusas com código e exit code; trabalho sem arquivo novo. |
| A2 | Envelope de `plan validate` de um plano com os casos listados, com `occurrences` e `unplaced` iguais aos intervalos calculados à parte. |
| A3 | Cada violação recusada com `PLAN_INVALID` e mensagem sem o valor recebido; as estruturais sem chamar o FFprobe. |
| A4 | Quadros brutos da preview e da full renderizadas: pixels da cor da legenda presentes dentro das ocorrências e ausentes fora delas, base do texto na base da caixa e razão largura do texto sobre largura do quadro igual na preview e na full dentro de 2%. |
| A5 | Hash dos goldens de `filter_complex` inalterado; `render full` depois de mudar um item devolve `APPROVAL_STALE` e a full vigente tem o mesmo SHA-256. |
| A6 | Teste de `capabilities` contra as constantes e leitura cruzada da documentação. |

## Boundaries

### Always

- Validar `captions` antes de inspecionar mídia no que for estrutural, com mensagens fixas.
- Ler os padrões, limites e faixas de uma fonte única de constantes, consumida por validação, grafo, `capabilities` e testes.
- Preservar o grafo e a full de planos sem `captions`.

### Ask first

- Qualquer tradução, rede ou modelo novo na CLI.
- Mudar a semântica de `layers[]` de texto ou dos estilos da phase 5.

### Never

- Escrever tempo de timeline no plano em nome do agente ou alterar `plan.json` a partir da CLI.
- Ecoar texto de legenda, caminho ou conteúdo da transcrição em mensagem de erro.

## Handoff

vibe-plan (sem UI visível).

- Chat: recomendo novo chat para `vibe-plan`; se o humano preferir, continuar neste chat não bloqueia. O `spec.md` vivo é a ponte.

- [x] Aprovação humana (leu o arquivo e confirmou)
