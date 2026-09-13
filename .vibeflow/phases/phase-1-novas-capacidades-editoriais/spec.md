# Spec: Novas capacidades editoriais e preparação de trabalhos
# Alvo: phase-1-novas-capacidades-editoriais
# Status: aprovado

## Objetivo

Esta phase amplia a CLI local para que agentes descrevam movimentos de câmera,
cortes J/L, inserções visuais em camadas e texto em coordenadas livres, além de
prepararem um trabalho com `editor init`. O núcleo continua não interativo,
local, determinístico e orientado por um único `plan.json`. Sucesso significa que
um plano v2 válido combina as cinco capacidades, gera preview e full com a mesma
composição, e que planos inválidos falham antes do FFmpeg.

## Inventário

1. Evolução do contrato de plano para `schema_version: 2`, sem caminho paralelo
   de renderização para o schema v1.
2. Movimento temporal em segmentos de vídeo, com zoom e deslocamento interpolados.
3. Cortes J/L declarados nas fronteiras dos segmentos e áudio montado em linha
   temporal independente da concatenação visual.
4. Camadas ordenadas de texto, imagem e vídeo, com posição e tamanho normalizados.
5. `editor init` idempotente, seguro e sem criação de estado editorial.
6. Migração dos fixtures, da skill `vibe-editor`, das capacidades reportadas e
   das provas do MVP para o contrato vigente.

## Suposições e decisões

1. `editor` continua sendo o identificador técnico provisório do executável; o
   nome do produto permanece em aberto.
2. A mudança de `plan.json` é uma quebra controlada. Novos planos exigem
   `schema_version: 2`; planos v1, o campo `overlays` e campos desconhecidos são
   rejeitados. Fixtures internos são atualizados, sem importador ou fallback v1.
3. O `schema_version` do envelope da CLI, dos artefatos de análise e de
   `approval.json` continua versionando esses artefatos separadamente. A mudança
   desta phase é especificamente a versão do plano.
4. A timeline visual continua sendo a concatenação linear dos segmentos. J/L
   altera somente a posição temporal dos grupos de áudio, e camadas podem
   sobrepor qualquer trecho visual declarado.
5. Todos os tempos são números JSON finitos em segundos, com ponto decimal, e
   intervalos usam `[início, fim)`. Coordenadas normalizadas usam intervalo
   fechado `[0, 1]`, salvo dimensões, que devem ser maiores que zero.
6. A origem de coordenadas é o canto superior esquerdo. Para texto, `x` e `y`
   são o ponto superior esquerdo da caixa. Para movimento, `x` e `y` são o ponto
   focal normalizado do conteúdo enquadrado.
7. O canvas continua horizontal 16:9. A primeira imagem de segmento define a
   resolução e a taxa de quadros da full; a preview mantém o limite de 720 pixels.
8. O processamento continua local, sem rede, sem telemetria, sem decisão
   editorial automática e sem dependência Python nova.
9. A versão pública da CLI deve avançar para o próximo major conforme o semver
   do repositório, pois o schema aceito deixa de ser compatível com o contrato v1.

## Decisões críticas

| ID | Ação | Decisão | Motivo e impacto | Fonte anterior |
|---|---|---|---|---|
| SCOPE-01 | mantém | As cinco capacidades pertencem à mesma phase. | Elas compartilham o contrato do plano, a validação temporal/espacial e o grafo de composição. | interview Phase 1 |
| CONTRACT-01 | substitui | O plano vigente passa de `schema_version: 1` para `schema_version: 2`; o envelope da CLI continua na versão própria. | As semânticas de áudio, composição e posicionamento mudam. Rejeitar v1 evita interpretar o mesmo JSON com duas regras. | spec MVP `CONTRACT-01` e interview Phase 1 |
| MOTION-01 | cria | Cada movimento usa estados `from` e `to`, com `x`, `y` e `zoom`, interpolação linear por frame e `zoom` entre `0.5` e `4.0`. | É o modelo mínimo determinístico para aproximar, afastar e deslocar sem linguagem de animação ou easing. | interview Phase 1 |
| CUT-01 | cria | `cuts[]` declara `j` ou `l` em uma fronteira entre segmentos, com duração entre `0` e `30` segundos e menor que os dois segmentos vizinhos. | A intenção fica explícita e o áudio pode ser montado em uma linha global, sem simulação acidental. | interview Phase 1 |
| LAYER-01 | cria | `layers[]` é uma lista ordenada de camadas discriminadas por `text`, `image` ou `video`; a primeira fica abaixo e a última acima. | O z-order se torna reproduzível para picture-in-picture e inserções laterais. | interview Phase 1 |
| TEXT-01 | substitui | O lettering v2 usa `x`, `y`, `width` e `height` normalizados, sem posições nomeadas ou margem fixa obrigatória. | Permite posicionamento livre e mantém o alinhamento como propriedade interna da caixa. | spec MVP `TEXT-01` e interview Phase 1 |
| MEDIA-02 | cria | Camadas de imagem aceitam somente PNG, JPEG/JPG e WebP estáticos, até 100 MiB e 8.192 pixels por dimensão. | Fecha uma allowlist raster verificável e limita custo de decodificação. | N/A, decisão necessária para LAYER-01 |
| INIT-01 | cria | `editor init` cria ou completa somente a estrutura técnica do trabalho, é idempotente e nunca cria estado editorial. | Prepara o agente sem selecionar fontes, apagar artefatos ou substituir aprovação. | interview Phase 1 |
| STATE-01 | mantém | `approval.json` continua sendo a barreira obrigatória para `render full`, incluindo hashes das fontes usadas pelo plano. | Qualquer alteração em plano, camada, corte ou mídia usada deve invalidar a full autorizada. | spec MVP `STATE-01` e interview Phase 1 |

## Escopo e comportamento

### 1. Preparação segura com `editor init`

- O comando é `editor init --workdir <caminho-absoluto> --json` e mantém o
  envelope JSON da CLI, com `command: "init"`.
- Para um caminho inexistente, somente o último componente pode ser criado. O
  pai deve existir, ser uma pasta regular, gravável e não conter symlink,
  junction ou outro reparse point. O caminho final também não pode ser um
  arquivo, link ou reparse point.
- Para um caminho existente, a CLI valida a pasta e todos os componentes
  relevantes antes de criar qualquer subpasta. Arquivos, links, junctions,
  reparse points ou diretórios incompatíveis em qualquer caminho gerenciado são
  rejeitados com `WORKDIR_INVALID`, exit code de estado e mensagem segura.
- A estrutura criada, na ordem determinística, é:

  ```text
  <workdir>\
    .tmp\
    analysis\
      audio\
      transcription\
    frames\
  ```

- `sources` não é criado. O comando não escolhe, copia, move ou inspeciona
  arquivos de mídia.
- `plan.json`, `approval.json`, `preview.mp4`, `full.mp4`, análises, frames e
  qualquer outro arquivo existente são preservados byte a byte. Um artefato
  existente em caminho gerenciado precisa ser arquivo regular, nunca linkado.
- Uma segunda execução retorna os mesmos diretórios como `existing`, não altera
  o conteúdo e não afirma que há fontes, preview ou aprovação.
- O sucesso retorna, em ordem fixa, `workdir`, `state: "ready"`, a lista
  `created` de diretórios criados nesta execução, a lista `existing` dos que já
  estavam presentes e `preserved_artifacts` somente para os artefatos conhecidos
  já existentes. Não há prompt no terminal.
- Se uma criação falhar, a operação retorna erro seguro. Ela pode remover apenas
  diretórios vazios criados pela própria execução, sem tocar em diretórios ou
  artefatos preexistentes.

### 2. Contrato estrutural do plano v2

O root aceita somente `schema_version`, `profile`, `sources`, `segments`,
`cuts`, `layers` e `audio_processing`. `schema_version` deve ser o inteiro `2`,
`profile.aspect_ratio` deve ser `16:9`, e `cuts` e `layers` são listas explícitas,
inclusive quando vazias. `audio_processing` mantém a semântica do MVP e pode
usar seus valores padrão documentados.

As fontes continuam sendo arquivos locais regulares com IDs seguros e únicos.
`kind` passa a aceitar `video`, `audio` e `image`:

- `video` continua sendo MP4 com vídeo H.264, podendo conter no máximo um áudio
  AAC, dentro dos limites atuais de 4 GiB e 3.600 segundos.
- `audio` continua sendo MP3 ou WAV PCM S16LE, dentro dos limites atuais de 4
  GiB e 3.600 segundos.
- `image` é uma imagem raster estática PNG, JPEG/JPG ou WebP, validada pelo
  formato real e pela extensão, com até 100 MiB e 8.192 pixels de largura e de
  altura. Imagens animadas, SVG, GIF, BMP e formatos não listados são rejeitados.
  Imagens não possuem duração nem áudio.

A inspeção de fontes continua centralizada em `media`, usa FFprobe ou a mesma
validação local equivalente, rejeita streams inesperados e ocorre antes de
qualquer construção de filtro ou chamada de renderização.

O hash canônico continua sendo UTF-8, ordenado e sem espaços. A ordem dos arrays
continua significativa, porque define a ordem dos segmentos, o z-order das
camadas e a identidade do plano aprovado.

### 3. Segmentos, movimento e timeline visual

- `segments[]` mantém `video` e `audio`, os intervalos de fonte, `fit`, os modos
  `keep`, `mute`, `replace` e `mix`, ganhos explícitos, loop de áudio,
  normalização, limiter e a regra de narração explícita para b-roll.
- `video.motion` é opcional. Quando ausente, o segmento permanece estático. Quando
  presente, aceita exatamente:

  ```json
  {
    "from": {"x": 0.50, "y": 0.50, "zoom": 1.00},
    "to":   {"x": 0.55, "y": 0.48, "zoom": 1.25}
  }
  ```

- `from` e `to` exigem `x`, `y` e `zoom`. `x` e `y` ficam entre `0` e `1`; `zoom`
  fica entre `0.5` e `4.0`. Números booleanos, NaN, infinito e valores fora dos
  limites retornam `PLAN_INVALID`.
- O `zoom` é a intensidade do movimento. `1.0` representa o enquadramento
  normalizado pelo `fit`, valor maior aproxima e valor menor afasta. A imagem é
  ajustada ao canvas conforme `fit`, depois transformada pelo estado interpolado.
  A interpolação é linear pelo tempo local do segmento, sem easing e sem
  keyframes adicionais.
- O ponto focal usa origem no canto superior esquerdo. Em zoom maior ou igual a
  `1`, o enquadramento é limitado às bordas da fonte para não revelar área fora
  da imagem. Em zoom menor que `1`, áreas descobertas são preenchidas com preto.
- A duração visual permanece `video.out_s - video.in_s`. A timeline é a soma dos
  segmentos, sem lacunas, sobreposição visual ou transição implícita. O limite
  total de 3.600 segundos e o máximo de 1.000 segmentos permanecem vigentes.
- O relatório de `plan validate` expõe os segmentos efetivos, a duração total,
  os estados de movimento normalizados quando presentes e os contadores de
  segmentos, cortes e camadas.

### 4. Cortes J/L e áudio global

`cuts[]` usa o formato:

```json
{"after_segment": 1, "type": "j", "duration_s": 0.20}
```

- `after_segment` é um índice inteiro baseado em 1. O valor `1` representa a
  fronteira entre `segments[0]` e `segments[1]`; deve ser menor que a quantidade
  de segmentos e não pode se repetir.
- `type` aceita somente `j` e `l`. `duration_s` deve ser finito, maior que zero,
  no máximo `30` e menor que a duração visual de ambos os segmentos adjacentes.
- Sem corte, o grupo de áudio de um segmento ocupa seu intervalo visual normal.
  A duração de cada faixa declarada deve cobrir a duração necessária, respeitando
  o comportamento de `loop` já existente.
- Em um corte J na fronteira `b`, o grupo de áudio do segmento seguinte começa em
  `b - duration_s` e cobre a duração visual desse segmento mais a antecipação.
  O áudio do segmento anterior termina no limite visual `b`.
- Em um corte L na fronteira `b`, o grupo de áudio do segmento anterior continua
  até `b + duration_s` e cobre a duração visual mais a extensão. O áudio do
  segmento seguinte começa em `b`.
- O segmento cujo áudio for estendido não pode estar em `mode: "mute"`. Cada
  faixa desse grupo deve cobrir a duração estendida, ou declarar `loop: true`.
  A fonte e seus intervalos continuam sujeitos aos limites de mídia.
- Durante a sobreposição, os grupos ativos são mixados na linha temporal global
  usando os ganhos declarados. Não há crossfade, ducking, redução automática ou
  alteração de ganho para esconder a sobreposição.
- A linha de áudio é recortada ao intervalo `[0, timeline_duration)` e a saída
  mantém exatamente a duração visual. Se todos os grupos forem mute, o arquivo
  continua sem stream de áudio, como no MVP.
- A validação rejeita fronteira inexistente, índice duplicado, tipo desconhecido,
  duração nula ou fora do limite, áudio estendido insuficiente e qualquer
  inconsistência antes de chamar o FFmpeg.

### 5. Camadas visuais

`layers[]` é a composição visual global sobre o vídeo-base concatenado. Cada
camada exige `type`, `start_s`, `end_s`, `x`, `y`, `width` e `height`. O intervalo
fica dentro da timeline e usa `[início, fim)`; `x + width` e `y + height` não
podem exceder `1`.

As camadas podem se sobrepor temporalmente. A ordem do array é o z-order, da
primeira camada abaixo para a última acima. O vídeo-base sempre fica abaixo de
todas as camadas. O máximo é 1.000 camadas.

#### `type: "text"`

- Exige `text`, `font`, `size_px`, `color`, `align` e `wrap`, além dos campos
  comuns. Reutiliza os limites do lettering do MVP: texto até 4.096 caracteres,
  fonte instalada sem separadores de caminho ou NUL, tamanho inteiro de 1 a 512,
  cor `#RRGGBB`, alinhamento `left`, `center` ou `right`.
- `x` e `y` são o ponto superior esquerdo da caixa. `width` e `height` são a
  caixa normalizada de layout. `wrap: true` quebra linhas dentro da largura;
  texto que exceder a caixa é recortado, sem redimensionamento automático.
- Não há posição nomeada, margem segura fixa, animação, opacidade, template ou
  coordenada alternativa. O alinhamento atua dentro da caixa e não muda `x` ou
  `y`.

#### `type: "image"`

- Exige `source_id` de uma fonte `kind: "image"` e `fit` `contain` ou `crop`.
- `contain` preserva a imagem inteira dentro da caixa e deixa a área não ocupada
  transparente para revelar o vídeo-base. `crop` preenche a caixa e corta
  simetricamente a partir do centro.
- PNG com transparência preserva seu canal alpha. A imagem é estática durante
  todo o intervalo declarado e não possui áudio.

#### `type: "video"`

- Exige `source_id` de uma fonte `kind: "video"`, `in_s`, `out_s` e `fit`
  `contain` ou `crop`. A fonte continua limitada a MP4/H.264 e aos limites
  atuais.
- `out_s - in_s` deve ser igual a `end_s - start_s` dentro da tolerância temporal
  canônica. Não há loop, freeze, áudio implícito ou ajuste de velocidade nesta
  phase. O áudio de uma camada de vídeo é sempre descartado; áudio editorial
  deve ser declarado em `segments[].audio`.
- A camada é composta no retângulo normalizado declarado. `contain` preserva a
  imagem inteira e `crop` preenche o retângulo com corte central.

O render deve construir a composição de camadas depois da concatenação visual e
antes da publicação. O grafo deve ser o mesmo para preview e full, mudando apenas
resolução, taxa de quadros e limite de dimensão próprios de cada artefato.

### 6. Preview, aprovação e full

- `plan validate` lê somente `plan.json`, valida v2, fontes, movimento, J/L,
  camadas, coordenadas e limites, mas não renderiza nem cria saída.
- `render preview` usa o mesmo grafo da full, incluindo movimento, áudio global e
  camadas. Publica somente um `preview.mp4` H.264/AAC quando aplicável, em canvas
  16:9 com no máximo 720 pixels na maior dimensão, após validação de metadados.
- `approve` continua exigindo plano válido e preview regular. `approval.json`
  mantém seu schema de artefato, registra o hash canônico do plano v2, o hash da
  preview e os hashes das fontes declaradas, inclusive imagens e vídeos usados
  em camadas.
- `render full` revalida o plano, a preview, a aprovação e todas as fontes antes
  do FFmpeg. Qualquer mudança em `plan.json`, em uma fonte ou na preview retorna
  `APPROVAL_STALE` e preserva uma full existente.
- A full usa a mesma timeline e o mesmo grafo da preview, sem reanálise,
  reinterpretação ou decisão editorial. Continua sendo publicada como o único
  `full.mp4`, por temporário em `.tmp`, validação de mídia e substituição
  atômica.
- Os resultados de validação e renderização usam `cut_count` e `layer_count` no
  lugar de `overlay_count`. O envelope externo, os códigos públicos e os exit
  codes existentes permanecem estáveis, salvo o novo comando `init` e os erros
  específicos de estado já previstos para caminhos inválidos.

### 7. Capacidades e documentação da skill

- `capabilities --json` passa a informar `init` como operação local disponível,
  o schema de plano v2, formatos raster suportados, limites de camadas, cortes,
  sobreposição J/L, tamanho e dimensão máxima de imagens, e movimento aceito.
- A recomendação e a sondagem de FFmpeg, FFprobe, Whisper, CUDA e modelos locais
  continuam sem rede e sem alteração de estado.
- `skills/vibe-editor/SKILL.md` e suas referências deixam de declarar `editor
  init`, zoom, camadas e J/L como ausentes. A skill passa a executar `init` antes
  de preparar um trabalho novo e a orientar o agente a usar o schema v2 sem pedir
  JSON manual ao operador.

### Fora

- Compatibilidade ou migração automática de planos v1, porque a phase adota um
  contrato único e evita dois validadores ou renderizadores.
- Easing configurável, keyframes arbitrários, rotação, tracking facial, zoom
  automático, máscaras, chroma key, transparência de vídeo, transições visuais e
  edição não linear geral, porque não são necessários para o contrato declarado.
- Crossfade de áudio, ducking, equalização, redução de ruído, áudio implícito de
  camadas e sincronização automática entre fontes, porque todo áudio editorial
  deve continuar explícito.
- GIF, SVG, BMP, imagens animadas, formatos de vídeo ou codecs novos, formatos
  verticais e múltiplas variantes publicadas, porque a allowlist e o perfil do
  MVP permanecem restritos.
- UI, MCP, upload, banco, autenticação, telemetria, fila e processamento
  concorrente, porque a execução continua local e com um trabalho por vez.

## Checklist de entrega

### Aceite

- [x] A1: `editor init --workdir <caminho-absoluto> --json` cria a estrutura
  técnica em um diretório novo com pai seguro, completa um diretório existente,
  é idempotente e preserva todos os artefatos preexistentes.
- [x] A2: `init` rejeita caminho relativo, arquivo no destino, componente
  linkado, reparse point e destino não gravável com envelope seguro, sem deixar
  arquivo editorial ou estado de aprovação parcial.
- [ ] A3: `plan validate` aceita um plano v2 com movimento, J/L, texto em
  coordenadas livres e camadas de imagem e vídeo, e rejeita v1, `overlays`,
  campos desconhecidos, coordenadas fora da caixa, fontes incompatíveis e limites
  excedidos antes do FFmpeg.
- [x] A4: O movimento usa `from` e `to`, interpola linearmente por frame, respeita
  `zoom` entre `0.5` e `4.0`, mantém `fit` separado e produz frames verificáveis
  em aproximação, afastamento e deslocamento.
- [ ] A5: Um plano com J e L válidos antecipa ou prolonga o áudio exatamente pela
  duração declarada, mistura somente os grupos ativos e preserva a duração visual
  final; fronteiras ou coberturas inválidas falham na validação.
- [ ] A6: Camadas de texto, imagem e vídeo respeitam intervalo, caixa, fit,
  transparência, ordem do array e ausência de áudio implícito. Camadas não
  substituem o vídeo-base.
- [x] A7: O texto v2 usa `x`, `y`, `width` e `height` normalizados, e posições
  nomeadas ou coordenadas inválidas são rejeitadas.
- [ ] A8: Preview e full usam o mesmo grafo v2, publicam somente arquivos MP4
  completos, mantêm os limites de resolução e preservam preview/full vigentes em
  caso de falha.
- [ ] A9: `approve` e `render full` continuam vinculados aos hashes do plano v2,
  preview e fontes. Alterar uma camada, um corte, uma imagem ou qualquer fonte
  retorna `APPROVAL_STALE` sem renderizar nem substituir a full.
- [ ] A10: `capabilities --json`, a documentação da `vibe-editor` e os fixtures
  internos descrevem o v2 e nenhuma dependência nova é adicionada.

### Critérios de sucesso

- [ ] C1: O E2E da phase executa `init` e percorre validação, preview, aprovação e
  full com um único plano que combina movimento, J/L, camada de imagem ou vídeo,
  lettering por coordenadas e o áudio explícito do MVP.
- [x] C2: A prova de frames confirma em timestamps distintos que o movimento
  muda o enquadramento e que a imagem-base continua presente sob as camadas.
- [ ] C3: A prova de áudio confirma o início antecipado do J, a extensão do L e a
  duração final exata sem ducking ou crossfade implícito.
- [x] C4: A execução repetida de `init` não altera hashes nem bytes dos artefatos
  existentes e não cria `plan.json`, `approval.json`, `preview.mp4` ou `full.mp4`.
- [ ] C5: A full só é liberada com aprovação vigente e fica bloqueada após
  mutação independente do plano, da preview, da fonte de vídeo, áudio ou imagem.
- [ ] C6: A suíte completa, os gates de dependência e segredo e a verificação de
  whitespace passam sem `SkipTest` por ausência de pré-requisito local.

## Implementação

### Stack

| Área | Escolha |
|---|---|
| CLI e runtime | Python 3.11 x64, biblioteca padrão e setuptools já existentes |
| Mídia | FFmpeg/FFprobe locais, `libx264` e `aac` já exigidos pelo MVP |
| Plano | Validador estrito e hash canônico existentes, estendidos para v2 |
| Publicação | Temporários em `.tmp`, validação e substituição atômica existentes |
| Dependências novas | Nenhuma |

### Estrutura tocada

```text
pyproject.toml                         # versão major do pacote
src/editor/contracts.py                # versão pública, sem mudar o envelope da CLI
src/editor/cli.py                      # comando init e despacho
src/editor/workdir.py                  # validação e criação segura da estrutura
src/editor/media.py                    # inspeção de imagens raster e metadados v2
src/editor/plan/schema.py               # allowlist, tipos e limites do schema v2
src/editor/plan/validation.py           # movimento, cortes, camadas e fontes v2
src/editor/render/ffmpeg_graph.py       # movimento, áudio global e composição de camadas
src/editor/render/preview.py            # contadores e validação do render v2
src/editor/render/full.py               # render v2 sob aprovação vigente
src/editor/integrity/approval.py        # aprovação e hashes de fontes v2
src/editor/capabilities.py              # operações, formatos e limites v2
tests/test_init.py                      # criação, idempotência e segurança de init
tests/test_plan_validation.py            # schema v2 e invariantes temporais/espaciais
tests/test_preview_render.py             # grafo e preview com movimento, J/L e camadas
tests/test_full_render.py                # aprovação e full v2
tests/test_approval.py                   # invalidação por mutação de fontes e plano
tests/test_capabilities.py               # inventário v2 e operação init
tests/e2e/test_phase_1_flow.py           # jornada completa da phase
tests/fixtures/phase-1/                 # mídias curtas e planos v2 verificáveis
tests/fixtures/workflow/plan.json        # migração do fixture interno para v2
tests/fixtures/e2e/plan.json              # migração do fixture E2E para v2
skills/vibe-editor/SKILL.md              # fluxo do agente com init e plano v2
skills/vibe-editor/references/           # instalação e práticas atualizadas
.vibeflow/REGRAS.md                      # mapa vivo da nova CLI e das limitações
```

### Estilo e padrões

- Reutilizar `validate_workdir`, `resolve_source_path`, `media.inspect_source`,
  `build_ffmpeg_graph`, os envelopes e os publicadores atômicos existentes.
- Centralizar limites, allowlists, interpolação temporal e semântica de áudio no
  validador ou no grafo compartilhado; não duplicar regras em preview e full.
- Manter comentários semânticos em todo módulo e função nova, incluindo o motivo
  das decisões de clamp de movimento, áudio global e segurança de `init`.
- Passar caminhos e filtros ao processo como argumentos estruturados, sem shell,
  e nunca usar conteúdo do plano como caminho ou comando sem validação.
- Registrar no Documento Vivo as novas limitações, incluindo a ausência de easing,
  áudio de camada e formatos raster fora da allowlist.

### Contratos e módulos

- O root v2 exige `cuts` e `layers` explícitos; `overlays` não é alias aceito.
- O máximo permanece 2 MiB para `plan.json`, 1.000 segmentos, 1.000 camadas,
  999 fronteiras potenciais, 3.600 segundos de timeline, 30 segundos por J/L,
  4 GiB e 3.600 segundos por fonte audiovisual, 100 MiB por imagem e 8.192
  pixels por dimensão raster.
- `plan validate` é a única fronteira de schema, mídia, intervalo, coordenada,
  áudio e limite antes do render. O grafo recebe somente documento validado.
- Erros públicos continuam usando códigos em maiúsculas e mensagens seguras:
  `PLAN_INVALID` para contrato v2, `MEDIA_UNSUPPORTED` ou
  `MEDIA_LIMIT_EXCEEDED` para fontes, `WORKDIR_INVALID` para caminhos de
  trabalho, `APPROVAL_STALE` para estado aprovado divergente e os códigos de
  dependência/operação existentes para FFmpeg e publicação.
- `editor init` não publica artefatos editoriais. `render full` não contorna
  `verify_approval`.

## Como provar

### Seams

- A validação distingue versão do plano, versão do envelope e versão do registro
  de aprovação, sem aceitar um v1 por acidente.
- A interpolação de movimento é verificável por frames e não altera a duração ou
  o fit fora dos estados declarados.
- A fronteira J/L altera apenas o áudio e respeita cobertura, limites e duração.
- Camadas preservam o vídeo-base, a ordem do array, a caixa normalizada e o alpha
  de imagens, sem criar áudio implícito.
- `init` não segue links, não sobrescreve estado e é idempotente.
- A aprovação detecta mutação em qualquer recurso que participa da composição.

### Estratégia

- Unitário: allowlists v2, números finitos, coordenadas e caixas, tipos de fonte,
  limites de imagem, índices de corte, cobertura de áudio estendida, estados de
  movimento, hash canônico e códigos de erro.
- Integração: FFmpeg real com fixtures curtas de vídeo, áudio, PNG/JPEG/WebP,
  movimento, texto, imagem e vídeo em camada. Verificar metadados, duração,
  pixels amostrados, alpha e áudio por janelas.
- E2E: executar `init`, escrever um plano v2 combinado, validar, renderizar
  preview, aprovar, renderizar full e repetir após mutações independentes de
  plano, preview e fontes.
- Segurança operacional: testar diretório inexistente, existente, repetido,
  arquivo no destino, links quebrados, junctions e falha de criação sem tocar em
  artefatos vigentes.
- Gates: suíte completa sem skips, `pip-audit`, gitleaks e `git diff --check`.
- Manual: abrir preview e full, conferir movimento, sobreposição, posição de
  texto, ordem das camadas e diferença temporal do áudio J/L.

### Comandos

```text
.venv\Scripts\python.exe -B -m editor init --workdir <workdir> --json
.venv\Scripts\python.exe -B -m editor capabilities --json
.venv\Scripts\python.exe -B -m editor plan validate --workdir <workdir> --json
.venv\Scripts\python.exe -B -m editor render preview --workdir <workdir> --json
.venv\Scripts\python.exe -B -m editor approve --workdir <workdir> --json
.venv\Scripts\python.exe -B -m editor render full --workdir <workdir> --json
.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.venv\Scripts\python.exe -B -m unittest tests.e2e.test_phase_1_flow -v
.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip
gitleaks detect --source . --no-banner
git diff --check
```

## Boundaries

### Always

- Validar o plano v2, as fontes, os caminhos, os limites e a segurança de
  diretórios antes do FFmpeg ou de qualquer publicação.
- Processar localmente, sem telemetria, usando stdout JSON estável e stderr para
  diagnósticos humanos.
- Preservar originais, plano, análises, preview, aprovação e full vigentes;
  publicar somente arquivos completos por operação atômica.
- Reutilizar a aprovação por hash e incluir toda mídia referenciada por camadas
  na verificação de integridade.
- Manter `schema_version` do envelope separado da versão do plano.

### Ask first

- A conversa deve confirmar intenção editorial, fontes, papéis, intensidade do
  movimento, tipo e duração de J/L, posição/tamanho de cada camada, texto e
  fluxo preview/full antes de escrever o plano.
- A conversa deve obter aprovação humana explícita da preview antes de chamar
  `approve` e nunca inferir aprovação pela existência do arquivo.
- Instalação ou atualização de FFmpeg, modelos, CUDA ou dependências exige ação
  explícita do operador.

### Never

- Aceitar plano v1, `overlays` como alias, URL, stdin, caminho linkado ou fonte
  fora da allowlist.
- Montar comando shell com entrada do plano, seguir reparse point ou escrever
  fora do diretório do trabalho.
- Escolher cortes, movimentos, camadas, textos ou fontes automaticamente dentro
  da CLI.
- Usar áudio de uma camada de vídeo sem declaração explícita, introduzir ducking
  ou crossfade implícito, ou publicar uma saída parcial.
- Renderizar `full.mp4` sem aprovação válida ou substituir os originais.

## Handoff

vibe-plan

- Chat: recomendo novo chat para `vibe-plan`; continuidade no mesmo chat só por
  escolha consciente. O `spec.md` vivo é a ponte.
- O arquivo vivo `spec.md` entra no Git. `.vibeflow/spec-report.json` é evidência
  operacional e fica fora do Git.
- [ ] Aprovação humana, leitura e confirmação do arquivo.
