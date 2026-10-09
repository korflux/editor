---
name: vibe-editor
description: >-
  Use quando o usuário solicitar editar, montar, cortar, juntar, sonorizar,
  legendar ou exportar um vídeo a partir de arquivos locais. Conduza o trabalho
  do briefing à análise, plano, preview, aprovação explícita e full.mp4 usando
  a CLI local editor. Não use para editar imagens isoladas ou apenas explicar o projeto.
---

# vibe-editor

Use esta skill para transformar um pedido de edição de vídeo em uma execução local,
reproduzível e aprovada pelo usuário. O agente conduz a conversa e usa a CLI
`editor` para analisar fontes, montar o plano, gerar a preview e publicar a full.

## Limites da skill

- Use somente quando o usuário pedir uma edição de vídeo real com fontes locais.
- Não envie vídeos, áudios, transcrições, frames ou planos para serviços externos.
- Não baixe silenciosamente FFmpeg, modelos ou qualquer dependência.
- Não crie cópias convertidas das fontes sem avisar o custo (tempo, disco, uma
  geração de compressão) e obter o aceite do usuário. Os originais nunca são alterados.
- Não escolha cortes, música, textos ou ordem dos trechos sem confirmar a intenção
  do usuário quando ela não estiver clara.
- Nunca trate a existência da preview como aprovação. A aprovação precisa ser
  explícita e humana antes da full.
- Não use esta skill para editar imagens isoladas, criar um vídeo sem fontes locais,
  ou apenas explicar como o editor funciona.

## Primeira execução e dependências

Se o ambiente ainda não tiver sido verificado, se uma dependência estiver ausente
ou se o comando não estiver disponível, leia
[references/installation.md](references/installation.md) antes de orientar o usuário.

O editor possui `editor init` para preparar a estrutura técnica sem criar estado
editorial. Use um diretório de trabalho local, absoluto, regular e aprovado pelo
usuário. Não invente um caminho de fonte ou de saída. Prefira executar a CLI pelo
Python do ambiente virtual do repositório:

```text
.venv\Scripts\python.exe -B -m editor <comando>
```

Use `editor <comando>` somente quando o executável instalado estiver confirmado no
ambiente atual.

Para qualquer vídeo, de qualquer pessoa, leia
[references/youtube-editing.md](references/youtube-editing.md) antes de fechar o
briefing. Ele define o padrão de qualidade da skill (processo com prova, cortes que
não comem palavras, texto na tela com fonte, áudio nivelado, imagem íntegra, entrega),
como esse padrão vira campos do plano (inclusive proporção vertical e reenquadramento) e
as limitações do editor (texto sem caixa de fundo, uma proporção por plano). O
estilo do vídeo (ritmo, b-roll,
zoom, música, aparência do texto, gancho e fecho) é decisão do briefing: pergunte só
o que o usuário ainda não definiu. Se o vídeo for do canal Sonni Viaja (vlog de
viagem de Marco Kormoczi), leia também
[references/canal-sonni-viaja.md](references/canal-sonni-viaja.md), que traz o estilo
do canal pronto. Não imponha o estilo, os arquivos nem o fecho desse canal a vídeos de
outras pessoas.

O editor lê direto fontes `.mp4` e `.mov` com vídeo H.264 ou HEVC (Main e Main 10, 8
ou 10 bits) e áudio AAC. Os brutos de câmera (DJI) e os vídeos de celular (HEVC, MOV)
entram sem conversão: trilhas de dados (`tmcd`, telemetria) e miniatura anexada são
ignoradas, e toda saída é H.264/AAC `yuv420p` de 8 bits. Uma fonte HDR (HLG ou PQ,
`color_transfer` `arib-std-b67` ou `smpte2084`) é convertida para 8 bits sem tone mapping e
aparece lavada: avise o usuário antes de seguir com ela.

Se o editor recusar uma fonte (`MEDIA_UNSUPPORTED` ou "formato ou metadados
compatíveis"), o que acontece com `.mkv` e outros contêineres, mais de uma faixa de
áudio, vídeo extra, legenda, codecs fora da lista (VP9, AV1) e HEVC em 4:2:2, 4:4:4 ou
12 bits, leia [references/source-preparation.md](references/source-preparation.md)
antes de orientar o usuário. A conversão para cópias H.264 é testada e resolve, mas
só se faz depois de avisar o custo e obter o aceite.

## Fluxo obrigatório

### 1. Confirmar o pedido e localizar os arquivos

Entenda, em linguagem simples:

- qual vídeo o usuário quer obter;
- quais arquivos são vídeo principal, apoio, voz e música;
- quais trechos devem entrar ou sair;
- quais textos devem aparecer;
- onde o vídeo será publicado, que define a proporção (YouTube horizontal, Shorts,
  Reels e TikTok vertical, feed quadrado ou 4:5);
- duração, formato e restrições relevantes.

Se os arquivos não estiverem acessíveis localmente, peça ao usuário que os coloque
em uma pasta acessível ao agente. Não substitua essa etapa por uma URL ou upload.

Para um trabalho novo, depois de confirmar o diretório absoluto com o usuário,
prepare a estrutura antes de analisar as fontes:

```text
.venv\Scripts\python.exe -B -m editor init --workdir "<workdir>" --json
```

`init` é idempotente, cria somente `.tmp`, `analysis/audio`,
`analysis/transcription` e `frames`, e preserva qualquer plano, análise, preview,
aprovação ou full já existente. Não crie `plan.json` ou fontes nessa etapa.

### 2. Consultar as capacidades locais

Execute primeiro:

```text
.venv\Scripts\python.exe -B -m editor capabilities --json
```

Confira se as operações necessárias estão disponíveis, especialmente FFmpeg,
FFprobe, transcrição local, modelo Whisper e os encoders de renderização. Se faltar
algo, informe o bloqueio e siga [references/installation.md](references/installation.md).
Não faça fallback remoto.

### 3. Fechar o briefing com o usuário

Use o resultado das capacidades para evitar perguntas desnecessárias. Pergunte
somente as decisões editoriais que ainda faltarem. Antes de criar `plan.json`,
apresente um resumo do que será feito e confirme se esse resumo está correto.

Não peça ao usuário para preencher JSON ou comandos. Traduza as respostas dele para
o plano internamente.

### 4. Gerar as evidências das fontes

Execute as análises necessárias, sempre preservando os originais:

```text
.venv\Scripts\python.exe -B -m editor analyze transcribe --workdir "<workdir>" --source-id "<id>" --input "<arquivo>" --device auto --json
.venv\Scripts\python.exe -B -m editor analyze audio --workdir "<workdir>" --source-id "<id>" --input "<arquivo>" --json
.venv\Scripts\python.exe -B -m editor extract frames --workdir "<workdir>" --source-id "<id>" --input "<video>" --interval-s "<segundos>" --json
```

Atalhos planos `audio`, `transcribe` e `frames` equivalem aos longos acima e retornam os mesmos envelopes canônicos (`analyze audio`, `analyze transcribe`, `extract frames`). Os exemplos desta skill usam os longos, que permanecem válidos.

Use transcrição para localizar falas e momentos importantes, análise de áudio para
entender níveis e silêncios e frames para conferir o conteúdo visual. Não declare
que um intervalo é fala, ruído ou intenção editorial apenas por causa da análise de
amplitude.

#### Como cruzar a análise de um vídeo

Para um vídeo que contém imagem e fala, execute transcrição e áudio sobre o mesmo
arquivo, usando o mesmo `source-id`. Depois extraia frames para conferir o que
aparece na imagem nos momentos relevantes:

```text
.venv\Scripts\python.exe -B -m editor analyze transcribe --workdir "<workdir>" --source-id "main" --input "<video-principal>" --device auto --json
.venv\Scripts\python.exe -B -m editor analyze audio --workdir "<workdir>" --source-id "main" --input "<video-principal>" --json
.venv\Scripts\python.exe -B -m editor extract frames --workdir "<workdir>" --source-id "main" --input "<video-principal>" --interval-s "5" --json
```

Use os resultados assim:

- a transcrição mostra o que foi dito e em que momento, incluindo as palavras;
- a análise de áudio mostra volume, picos e possíveis silêncios;
- os frames mostram o que estava aparecendo na imagem;
- o plano combina essas três evidências com o pedido do usuário.

Por exemplo, uma fala importante entre 12 e 18 segundos pode indicar um trecho
relevante. Os frames próximos mostram se a imagem acompanha essa fala, e o áudio
ajuda a identificar uma pausa. O agente ainda precisa decidir o corte de acordo com
o briefing. A CLI não transforma silêncio em corte automático nem interpreta a
intenção do vídeo.

Os resultados publicados ficam em:

```text
<workdir>\analysis\transcription\main.json
<workdir>\analysis\audio\main.json
<workdir>\frames\main\manifest.json
```

Se a fonte de voz for um arquivo separado, use outro `source-id` e analise esse
arquivo separadamente. O agente deve relacionar os tempos entre as fontes antes de
escrever o plano.

### 5. Escrever e validar o plano

Escreva `<workdir>\plan.json` apenas com decisões confirmadas. O contrato vigente
é o schema v2: a raiz exige `schema_version: 2`, `profile`, `sources`, `segments`,
`cuts`, `layers` e `audio_processing`. `cuts` e `layers` devem ser listas explícitas,
mesmo quando vazias.

O plano deve descrever explicitamente a timeline linear, as faixas de áudio, ganhos,
música, normalização, limiter e letterings. Use `video.motion` opcional com estados
`from` e `to`, cada um com `x`, `y` e `zoom` entre `0.5` e `4.0`, interpolados
linearmente. Declare cortes J/L em `cuts[]`, com `type` `j` ou `l` e sua duração.

`layers[]` é a composição visual ordenada sobre o vídeo-base e aceita
`type: "text"`, `type: "image"` e `type: "video"`, sempre com `start_s`, `end_s`, `x`,
`y`, `width` e `height` normalizados sobre o canvas do perfil. O `size_px` do texto vale
na largura da full e a preview o escala na mesma proporção, então o texto da preview
representa o da full. Imagens aceitas são PNG, JPEG/JPG e WebP estáticos, até 100 MiB
e 8.192 pixels por dimensão. Use `fit: contain` ou `fit: crop` nas camadas e nas
fontes quando necessário. Não use campos fora do contrato.

#### Sombra e contorno do texto

A camada `text` aceita `shadow` e `outline` opcionais, para texto claro ler sobre fundo
claro. Em camada `image` ou `video` o plano é recusado.

```json
{"type": "text", "start_s": 3.0, "end_s": 6.5, "x": 0.04, "y": 0.78, "width": 0.92, "height": 0.14,
 "text": "Nome do lugar", "font": "Arial", "size_px": 70, "color": "#FFFFFF", "align": "center", "wrap": true,
 "shadow": {"offset_x_px": 4, "offset_y_px": 4, "blur_px": 6, "color": "#000000", "opacity": 0.5},
 "outline": {"width_px": 2, "color": "#000000"}}
```

- `shadow` é uma cópia do texto (com o mesmo contorno) atrás dele. `offset_x_px` e
  `offset_y_px` são inteiros de -100 a 100 (positivo desloca para a direita e para
  baixo) e `blur_px` é um inteiro de 0 a 50 (0 é sombra nítida); os três são
  obrigatórios. `color` (`#RRGGBB`, padrão `#000000`) e `opacity` (0 a 1, padrão 0.5)
  são opcionais.
- `outline` contorna as letras: `width_px` é um inteiro de 1 a 30, obrigatório, e
  `color` (`#RRGGBB`, padrão `#000000`) é opcional.
- Os valores em pixels valem na largura da full, como o `size_px`, e a preview os
  escala na mesma proporção; o efeito da preview representa o da full.
- Sombra e contorno são recortados pela caixa da camada, e o texto parte do topo dela:
  dê folga à caixa. Não há caixa de fundo; o editor não a oferece.

#### Proporção de saída e reenquadramento

`profile.aspect_ratio` aceita `16:9`, `9:16`, `1:1` e `4:5`. Um plano tem uma única
proporção, com uma preview, uma aprovação e uma full; para entregar em outra
proporção, abra outro trabalho (outra pasta) com outro plano. Escolha pelo destino:
`16:9` para YouTube, `9:16` para Shorts, Reels e TikTok, `1:1` e `4:5` para feed.
Fora do 16:9 o tamanho é fixo de plataforma, qualquer que seja a fonte: a full sai em
1080x1920 (9:16), 1080x1080 (1:1) ou 1080x1350 (4:5) e a preview em até 720 pixels
(404x720, 720x720 e 576x720). `max_full_width` só vale no 16:9; em outra proporção o
plano é recusado.

Uma fonte horizontal vira vertical por `fit: crop`, que recorta a fonte na proporção
do canvas, ou por `fit: contain`, que mantém a imagem inteira com barras. Para seguir
o sujeito use `video.reframe` (só com `fit: crop`):

```json
"video": {"source_id": "main", "in_s": 12.0, "out_s": 18.0, "fit": "crop",
  "reframe": {"keyframes": [
    {"t_s": 0, "x": 0.3, "y": 0.5},
    {"t_s": 2.5, "x": 0.7, "y": 0.45, "zoom": 1.5, "interpolation": "linear"},
    {"t_s": 6.0, "x": 0.7, "y": 0.45, "zoom": 1.5}
  ]}}
```

- Cada keyframe tem `t_s` (segundos desde o início do segmento, estritamente
  crescente, de 0 até `out_s - in_s`), `x` e `y` (0 a 1), `zoom` opcional (1.0 a 4.0,
  padrão 1.0) e `interpolation` opcional (`ease`, o padrão, parte e chega suave;
  `linear`, velocidade constante). A interpolação do keyframe vale até o seguinte.
  São de 1 a 32 keyframes por segmento; um só é um foco estático.
- `x` e `y` são a posição do sujeito sobre a largura e a altura da fonte original, com
  origem no canto superior esquerdo, e ficam no centro do recorte. Quando o centro
  levaria o recorte para fora da fonte, ele encosta na borda e nunca mostra preto.
  Antes do primeiro keyframe e depois do último vale o estado do primeiro e do último.
- Para marcar, extraia frames da fonte original com `extract frames`, abra cada frame,
  leia onde está o sujeito como fração do quadro inteiro e converta o tempo do frame
  para `t_s = tempo na fonte - video.in_s`. Crie um keyframe quando o sujeito muda de
  lugar; use `zoom` acima de 1.0 só para aproximar de propósito.
- Em trabalhos novos prefira `reframe` a `motion`, inclusive para um punch-in em 16:9.
  O mesmo segmento não aceita os dois. `motion` continua válido nos planos que o usam.

`plan validate` informa `data.profile.full_width`, `full_height` e `upscale`: a lista
de segmentos (`segment`, a posição de 1 em diante na timeline) em que a fonte original é
ampliada no canvas da full, com o `factor` arredondado em 2 casas. No `crop` o fator é a
largura do canvas sobre a do recorte usado (o do maior `zoom` do `reframe`, ou o recorte
central sem `reframe`); no `contain` e no `preserve` é a escala da imagem inteira no
canvas; em qualquer `fit`, um `motion` multiplica o fator pelo maior `zoom` entre `from`
e `to`. Uma fonte 1080p em 9:16 com `crop` amplia cerca de 1.78 vezes; uma 4K em 9:16 não
amplia. Lista vazia significa que nenhum segmento é ampliado. Ampliar não bloqueia o
render: informe o usuário que a imagem perde nitidez nesses trechos (mais com `zoom`
alto) e siga se ele aceitar.

Zonas seguras do Shorts, Reels e TikTok são orientação editorial; o editor não as valida.
Essas plataformas cobrem a faixa inferior (título, legenda e botões) e o lado direito
com a interface. Mantenha rosto e texto longe delas, com margens conservadoras
sugeridas, por exemplo cerca de 20% da altura livre embaixo, 10% no topo e 10% nas
laterais, com um pouco mais à direita. São valores sugeridos, não medidas oficiais, e as
plataformas mudam a interface: confira os quadros da preview e ajuste `y` e `height`
do texto e os keyframes.

Valide antes de renderizar:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
```

O atalho `validate` equivale ao longo acima com envelope `plan validate`. Os longos permanecem válidos.

Se falhar, corrija a causa no plano ou nos arquivos declarados e valide novamente.
Não ignore erro nem force a renderização.

### 6. Gerar a preview e colher revisão

Execute:

```text
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

O atalho `preview` equivale ao longo acima com envelope `render preview`. Os longos permanecem válidos.

`render preview` e `render full` aceitam `--accel gpu` (padrão `none`), que usa decode CUDA e NVENC e cai
para CPU se a GPU falhar. Ele deixa o render até 8x mais leve para a CPU, mas não é mais rápido; use
somente quando `capabilities` informar `render.gpu_acceleration.status` como `available` e o usuário
precisar da CPU livre. O envelope informa o modo realmente usado em `video.accel`.

Os renders leem só os trechos das fontes que a timeline usa (janelas de decode), por padrão e
sem flag; `data.decode` informa se isso ocorreu. Com fontes grandes (acima de 1280x720) e várias
iterações de preview previstas, construa proxies leves uma vez, sempre por comando explícito:

```text
.venv\Scripts\python.exe -B -m editor proxy build --workdir "<workdir>" [--source-id "<id>"] --json
```

O comando decodifica cada fonte inteira, por isso a preview nunca o dispara; ele reutiliza proxies
válidos e refaz os obsoletos em `<workdir>\proxy\`. A preview usa `--proxy auto` (padrão), que lê
o proxy válido de cada fonte e o original das demais; `--proxy off` lê só os originais. O
envelope informa `data.proxy` com `sources` e `stale`. A full nunca usa proxy e a aprovação
continua vinculada aos originais; apagar `<workdir>\proxy\` é sempre seguro.

Informe ao usuário onde está `preview.mp4` e peça que ele assista. Traduza o
feedback em alterações no plano. Depois de qualquer alteração, repita a validação
e gere uma nova preview. Ainda não peça aprovação: execute primeiro o gate editorial
e visual da seção 7.

Se o usuário disser apenas “parece bom”, registre isso como feedback e conclua as
auditorias obrigatórias. Não interprete aprovação implícita nem pule o gate.

### 7. Auditar a preview antes de pedir aprovação

A preview só fica elegível para aprovação depois de duas provas independentes:
validação estrutural do plano e inspeção editorial/visual da mídia renderizada.
Renderizar com sucesso ou passar no `plan validate` não substitui assistir à
preview. Se qualquer uma das duas provas faltar, não peça aprovação e não rode a
operação `approve`.

#### 7.1 Prova estrutural

Execute novamente os comandos que certificam o contrato e a saída publicada:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Registre o resultado como evidência estrutural, incluindo a timeline, os cortes,
as camadas, as fontes e os caminhos dos artefatos. Essa prova confirma que o
plano é permitido e que a preview é um MP4 válido, mas não confirma continuidade,
legibilidade ou intenção editorial.

#### 7.2 Auditoria temporal e editorial

Cruze cada fronteira de segmento e cada corte com o campo `words[]` e as palavras publicadas em
`<workdir>\analysis\transcription\<source-id>.json`. Para cada fronteira, registre
se ela preserva a palavra inteira, termina em uma pausa natural ou remove fala de
forma intencional. Não use apenas o nível de áudio para classificar uma pausa.

Extraia frames nos dois lados dos cortes que envolvam movimento, revelação visual
ou mudança de enquadramento. Use os tempos reais do plano e da transcrição, não
valores inventados:

```text
.venv\Scripts\python.exe -B -m editor extract frames --workdir "<workdir>" --source-id "preview" --input "<workdir>\preview.mp4" --timestamps "<corte-1-0.5>,<corte-1>,<corte-1+0.5>" --json
```

Repita a extração para o início e fim de cada movimento e para o último segundo
do vídeo. Com `reframe`, extraia também um frame da preview em cada keyframe (tempo
da timeline = início do segmento na timeline + `t_s`) e confira se o sujeito está no
enquadramento esperado e dentro das zonas seguras. Na auditoria, confirme
continuidade de ação, olhar, enquadramento e áudio; confirme que o movimento não
corta o assunto nem começa ou termina de forma abrupta; e confirme que o último
plano e o áudio encerram naturalmente, sem frame preto, congelamento ou corte
prematuro.

#### 7.3 Auditoria visual do lettering

Assista à preview em tamanho normal e confira cada camada de texto. A validação
estrutural só prova que a fonte e a geometria são aceitas pelo schema. A inspeção
visual deve confirmar a fonte real, a caixa, a safe area, a quebra de linha, o
contraste, o tamanho e a leitura, sem texto cortado ou sobreposto a informação
essencial. Se houver dúvida de leitura, ajuste o plano (por exemplo com `shadow` ou
`outline`), valide e gere outra preview.

#### 7.4 Decisões humanas e resumo de exclusões

Antes de pedir aprovação, apresente um resumo textual com uma decisão humana para
cada item aplicável:

- falas em idioma diferente do destino: manter o original, traduzir ou legendar,
  com intervalo e justificativa;
- arquivos excluídos: ID ou caminho declarado e motivo da exclusão;
- falas removidas intencionalmente: fonte, intervalo, palavras afetadas e motivo,
  separadas de pausas naturais;
- cortes, movimentos, encerramento e textos: observação objetiva e evidência usada.

Não traduza, escolha cortes ou declare qualidade editorial automaticamente. Quando
um item não se aplicar, registre explicitamente “não se aplica”. O resumo e as
evidências precisam estar completos antes de perguntar ao usuário se a preview
está aprovada.

O gate só passa quando os estados abaixo forem verdadeiros ao mesmo tempo:

1. `plan validate` e `render preview` passaram, prova estrutural registrada.
2. A preview foi assistida e os frames de cortes, movimentos e encerramento foram
   conferidos, prova visual registrada.
3. As decisões sobre tradução, arquivos excluídos e fala removida estão no resumo.

Se algum estado estiver ausente, corrija o plano ou a análise e repita a validação
e a preview. Somente depois mostre o resumo ao usuário e solicite uma confirmação
explícita.

### 8. Registrar a aprovação e gerar a full

Somente após uma confirmação clara como “Aprovado, pode gerar a versão final”,
execute:

```text
.venv\Scripts\python.exe -B -m editor approve --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render full --workdir "<workdir>" --json
```

O atalho `full` equivale a `render full` com envelope canônico. `init`, `approve` e `capabilities` não têm atalhos e permanecem inalterados.

O primeiro comando registra qual plano, preview e fontes foram aprovados. O segundo
confere se nada mudou e só então publica `full.mp4`. Se o editor informar que a
aprovação ficou desatualizada, não force a execução: gere uma nova preview e peça
aprovação novamente.

#### Qualidade da full (`--quality`)

`render full` (e o atalho `full`) aceita `--quality standard|high|max`, padrão `standard`. A opção
escolhe só o preset de qualidade do vídeo e existe apenas na full: a preview usa sempre `standard`
(`render preview --quality` é recusado), e a escolha não entra no plano, no hash nem em
`approval.json`, então a mesma aprovação vale para uma full re-renderizada com outra qualidade. O
envelope informa o preset usado em `video.quality` e `capabilities` lista os presets e os parâmetros
em `render.quality`. Valor fora da lista é recusado com `ARGUMENT_INVALID`, sem renderizar.

| Preset | CPU (`libx264`) | GPU (`--accel gpu`, NVENC) | Uso |
|---|---|---|---|
| `standard` | `crf 23` | `cq 31` | padrão: conferência e entrega leve |
| `high` | `crf 18` | `cq 22` | entrega final |
| `max` | `crf 14` | `cq 18` | entrega final de filmagem de câmera em 2,7K ou 4K, quando o bitrate da full ficou abaixo do da fonte |

`high` e `max` reduzem `crf` e `cq` pela equivalência de SSIM medida (o `cq` de cada preset acompanha o
SSIM do `crf` do mesmo nome) e, na GPU, fixam o nível H.264 5.2: sem ele o NVENC limita a full a cerca de
17 Mb/s e `max` sairia igual a `high`. Se o fallback trocar a GPU pela CPU, vale o `crf` do preset
pedido. Custo medido, como **orientação** (fonte sintética exigente de 1080p30, i5-13600K e RTX 3060 Ti; o
efeito real depende da fonte): o arquivo cresce 2,1x no `high` e 3,3x no `max` em relação ao
`standard` na CPU (3,2x e 4,9x na GPU, onde o `standard` é menor; 4,4x no `max` da CPU com ruído leve)
e o tempo de codificação da CPU sobe até 1,3x no `high` e 1,7x no `max` (praticamente igual na GPU).
Depois da full, meça o bitrate com `ffprobe` e, se ainda ficar abaixo da fonte, suba o preset e avise
o usuário do tamanho final.

```text
.venv\Scripts\python.exe -B -m editor render full --workdir "<workdir>" --quality high --json
```

### 9. Entregar o resultado

Informe ao usuário o caminho da `full.mp4`, a duração e as principais escolhas
realizadas. Também informe se algo ficou fora do pedido. Preserve os artefatos do
trabalho para inspeção:

```text
<workdir>\
  .tmp\
  analysis\
  plan.json
  approval.json
  preview.mp4
  full.mp4
  analysis\
  frames\
```

## Comunicação com o usuário

O usuário não precisa conhecer nomes de comandos, JSON, hashes ou codecs. Faça as
perguntas em linguagem editorial comum e mostre apenas o necessário para ele
decidir. Detalhes técnicos entram na resposta somente quando explicam um bloqueio
ou uma limitação real.

O fluxo termina em uma destas condições:

- `full.mp4` foi gerada e entregue;
- faltam arquivos ou dependências locais, com orientação objetiva para resolver;
- o usuário ainda precisa decidir algo editorial;
- o usuário pediu para parar.
