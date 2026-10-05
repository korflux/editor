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

Quando o usuário disser que o vídeo é para YouTube, leia
[references/youtube-editing.md](references/youtube-editing.md) antes de fechar o
briefing. Use esse manual como ponto de partida para as decisões editoriais e os
comandos do fluxo, mas respeite o estilo solicitado explicitamente pelo usuário.

## Fluxo obrigatório

### 1. Confirmar o pedido e localizar os arquivos

Entenda, em linguagem simples:

- qual vídeo o usuário quer obter;
- quais arquivos são vídeo principal, apoio, voz e música;
- quais trechos devem entrar ou sair;
- quais textos devem aparecer;
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

O perfil é horizontal 16:9. `layers[]` é a composição visual ordenada sobre o
vídeo-base e aceita `type: "text"`, `type: "image"` e `type: "video"`, sempre com
`start_s`, `end_s`, `x`, `y`, `width` e `height` normalizados. Imagens aceitas são
PNG, JPEG/JPG e WebP estáticos, até 100 MiB e 8.192 pixels por dimensão. Use
`fit: contain` ou `fit: crop` nas camadas e nas fontes quando necessário. Não use
campos fora do contrato.

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
do vídeo. Na auditoria, confirme continuidade de ação, olhar, enquadramento e
áudio; confirme que o movimento não corta o assunto nem começa ou termina de
forma abrupta; e confirme que o último plano e o áudio encerram naturalmente,
sem frame preto, congelamento ou corte prematuro.

#### 7.3 Auditoria visual do lettering

Assista à preview em tamanho normal e confira cada camada de texto. A validação
estrutural só prova que a fonte e a geometria são aceitas pelo schema. A inspeção
visual deve confirmar a fonte real, a caixa, a safe area, a quebra de linha, o
contraste, o tamanho e a leitura, sem texto cortado ou sobreposto a informação
essencial. Se houver dúvida de leitura, ajuste o plano, valide e gere outra
preview.

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
