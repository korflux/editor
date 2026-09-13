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

O editor atual não possui `editor init`. Prepare ou use um diretório de trabalho
local, absoluto, regular e aprovado pelo usuário. Não invente um caminho de fonte
ou de saída. Prefira executar a CLI pelo Python do ambiente virtual do repositório:

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

Escreva `<workdir>\plan.json` apenas com decisões confirmadas. O plano deve
descrever explicitamente a timeline linear, os cortes, b-roll, faixas de áudio,
ganhos, música, normalização, limiter e letterings.

O perfil do MVP é horizontal 16:9. Use `fit: contain` ou `fit: crop` quando uma
fonte tiver proporção diferente. Não acrescente transições, camadas, animações,
picture-in-picture ou campos fora do contrato.

Valide antes de renderizar:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
```

Se falhar, corrija a causa no plano ou nos arquivos declarados e valide novamente.
Não ignore erro nem force a renderização.

### 6. Gerar a preview e colher revisão

Execute:

```text
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Informe ao usuário onde está `preview.mp4` e peça que ele assista. Traduza o
feedback em alterações no plano. Depois de qualquer alteração, repita a validação
e gere uma nova preview.

Se o usuário disser apenas “parece bom”, pergunte se pode considerar a preview
aprovada. Não interprete aprovação implícita.

### 7. Registrar a aprovação e gerar a full

Somente após uma confirmação clara como “Aprovado, pode gerar a versão final”,
execute:

```text
.venv\Scripts\python.exe -B -m editor approve --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render full --workdir "<workdir>" --json
```

O primeiro comando registra qual plano, preview e fontes foram aprovados. O segundo
confere se nada mudou e só então publica `full.mp4`. Se o editor informar que a
aprovação ficou desatualizada, não force a execução: gere uma nova preview e peça
aprovação novamente.

### 8. Entregar o resultado

Informe ao usuário o caminho da `full.mp4`, a duração e as principais escolhas
realizadas. Também informe se algo ficou fora do pedido. Preserve os artefatos do
trabalho para inspeção:

```text
<workdir>\
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
