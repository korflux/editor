# Como editar vídeos para YouTube

Esta referência orienta o agente a transformar um vídeo bruto em uma edição para
YouTube. Ela explica como tomar decisões editoriais, como verificar essas decisões
e quais comandos usar para analisar as fontes, montar o plano, revisar a preview e
gerar a versão final.

As práticas são um ponto de partida. O tema, o público, a personalidade do canal e
o pedido do usuário têm prioridade. Uma técnica só deve ser usada quando melhora a
compreensão, o ritmo ou a história. Efeito aplicado por hábito não é edição melhor.

## 1. Fluxo completo de edição

O agente deve seguir esta ordem. A decisão editorial vem antes do plano, e o plano
vem antes da renderização.

### 1.1 Entender o vídeo antes de cortar

Antes de abrir a timeline, confirmar:

- qual é a promessa principal do vídeo;
- para quem ele foi feito;
- qual resultado o espectador deve obter;
- se o vídeo é tutorial, opinião, vlog, entrevista, demonstração ou outro formato;
- quais arquivos são fala principal, b-roll, voz ou imagens de apoio;
- quais trechos precisam entrar, sair ou aparecer na tela;
- duração aproximada, formato de publicação e estilo desejado.

Se uma resposta não estiver definida, fazer uma pergunta objetiva. Não escolher
sozinho uma ordem narrativa, um texto ou um corte que mude o sentido do vídeo.

### 1.2 Conferir o ambiente e as fontes

Usar o inventário local antes de começar:

```text
.venv\Scripts\python.exe -B -m editor capabilities --json
```

Esse comando confirma quais ferramentas e modelos estão disponíveis. Depois, usar
um diretório de trabalho absoluto, com uma pasta própria para aquele vídeo, e
preservar os arquivos originais. Para um trabalho novo, preparar a estrutura antes
de analisar ou escrever o plano:

```text
.venv\Scripts\python.exe -B -m editor init --workdir "<workdir>" --json
```

O `init` é idempotente, não cria estado editorial e não substitui artefatos já
publicados.

### 1.3 Analisar fala, áudio e imagem

Para o vídeo principal, executar as três análises abaixo. Trocar os valores entre
`<...>` pelos caminhos e identificadores reais.

```text
.venv\Scripts\python.exe -B -m editor analyze transcribe --workdir "<workdir>" --source-id "main" --input "<video-principal>" --device auto --json
.venv\Scripts\python.exe -B -m editor analyze audio --workdir "<workdir>" --source-id "main" --input "<video-principal>" --json
.venv\Scripts\python.exe -B -m editor extract frames --workdir "<workdir>" --source-id "main" --input "<video-principal>" --interval-s "5" --json
```

Cruzar os resultados, em vez de confiar em uma análise isolada:

- a transcrição mostra o que foi dito e em que momento, inclusive as palavras;
- a análise de áudio mostra níveis, picos e possíveis intervalos de silêncio;
- os frames mostram o que estava aparecendo na imagem;
- o briefing decide se aquele momento deve ser mantido, cortado, coberto ou
  destacado.

Os artefatos ficam em:

```text
<workdir>\analysis\transcription\main.json
<workdir>\analysis\audio\main.json
<workdir>\frames\main\manifest.json
```

Se houver uma fonte separada, como uma gravação de voz ou um b-roll, analisá-la com
outro `source-id`. Nunca presumir que o segundo arquivo começa no mesmo instante do
primeiro sem conferir os tempos.

### 1.4 Montar o plano

O `plan.json` registra as decisões da edição. Ele deve dizer, de forma explícita:

- quais trechos entram e em qual ordem;
- qual arquivo aparece em cada trecho;
- qual áudio acompanha cada imagem;
- onde entram os b-rolls;
- quais textos aparecem na tela;
- quais ajustes de áudio serão usados.

Um exemplo reduzido de plano com gancho, corte e b-roll em tela cheia:

```json
{
  "schema_version": 2,
  "profile": {"aspect_ratio": "16:9"},
  "sources": [
    {"id": "main", "kind": "video", "path": "sources/main.mp4"},
    {"id": "broll", "kind": "video", "path": "sources/mesa.mp4"}
  ],
  "segments": [
    {
      "video": {"source_id": "main", "in_s": 0, "out_s": 12, "fit": "preserve"},
      "audio": {
        "mode": "keep",
        "tracks": [
          {"source_id": "main", "in_s": 0, "out_s": 12, "gain_db": 0, "loop": false}
        ]
      }
    },
    {
      "video": {"source_id": "broll", "in_s": 0, "out_s": 3, "fit": "crop"},
      "audio": {
        "mode": "replace",
        "tracks": [
          {"source_id": "main", "in_s": 12, "out_s": 15.2, "gain_db": 0, "loop": false}
        ]
      }
    },
    {
      "video": {"source_id": "main", "in_s": 15, "out_s": 30, "fit": "preserve"},
      "audio": {
        "mode": "keep",
        "tracks": [
          {"source_id": "main", "in_s": 15, "out_s": 30, "gain_db": 0, "loop": false}
        ]
      }
    }
  ],
  "cuts": [
    {"after_segment": 1, "type": "j", "duration_s": 0.20}
  ],
  "layers": [
    {
      "type": "text",
      "start_s": 0.2,
      "end_s": 3.5,
      "x": 0.08,
      "y": 0.08,
      "width": 0.60,
      "height": 0.16,
      "text": "O erro que deixa seu vídeo lento",
      "font": "Arial",
      "size_px": 48,
      "color": "#FFFFFF",
      "align": "center",
      "wrap": true
    }
  ],
  "audio_processing": {
    "normalize_loudness": true,
    "target_lufs": -16,
    "limiter": {"enabled": true, "ceiling_dbfs": -1},
    "ducking": false
  }
}
```

Validar o plano antes de gerar qualquer vídeo:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
```

Se a validação falhar, corrigir a fonte, o intervalo ou a decisão no `plan.json`.
Não ignorar o erro e não tentar compensá-lo durante a renderização.

### 1.5 Gerar, assistir e ajustar a preview

Renderizar uma versão de revisão:

```text
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Assistir à `preview.mp4` procurando quatro coisas: a história está clara, os cortes
parecem intencionais, o texto pode ser lido e a imagem está ajudando a fala. Se algo
precisar mudar, editar o plano, validar novamente e gerar outra preview.

### 1.6 Auditar a preview antes da aprovação

A revisão da preview tem duas camadas que precisam ser registradas separadamente.
Validação estrutural confirma o contrato do plano e o arquivo produzido. Inspeção
visual e editorial confirma o que o espectador realmente verá e ouvirá. Uma camada
sem a outra bloqueia o pedido de aprovação.

#### Evidência estrutural

Execute novamente:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Guarde os resultados e confirme a timeline, fontes, cortes, camadas e os caminhos
de `preview.mp4` e das análises. O sucesso desses comandos não prova continuidade
visual, leitura do lettering ou naturalidade do encerramento.

#### Auditoria temporal e visual

Para cada fronteira de fala, compare os tempos de `words[]` em
`analysis\transcription\<source-id>.json` com o corte no plano. Registre se o corte
preserva palavras, termina em pausa natural ou remove fala intencionalmente. Para
cortes com movimento ou revelação, para o início e fim de cada movimento e para o
encerramento, extraia frames da preview:

```text
.venv\Scripts\python.exe -B -m editor extract frames --workdir "<workdir>" --source-id "preview" --input "<workdir>\preview.mp4" --timestamps "<corte-0.5>,<corte>,<corte+0.5>,<movimento-inicio>,<movimento-fim>,<fim-1>,<fim>" --json
```

Assista à preview inteira e confirme continuidade de ação, olhar, enquadramento e
áudio. Confira que movimentos não cortam o assunto nem iniciam ou terminam de forma
abrupta, e que o último plano não congela, fica preto ou termina o áudio antes da
imagem. Para cada lettering, confirme fonte real, caixa, safe area, quebra de linha,
contraste, tamanho e legibilidade. `plan validate` não substitui essa inspeção.

#### Registro das decisões humanas

Antes de pedir aprovação, apresente um resumo com:

- cada fala estrangeira e a decisão entre manter, traduzir ou legendar, com intervalo
  e justificativa;
- cada arquivo excluído, identificado por ID ou caminho, e o motivo;
- cada fala removida intencionalmente, com fonte, intervalo, palavras afetadas e
  motivo, separada de pausas naturais;
- a observação e a evidência usada para cortes, movimentos, textos e encerramento.

Itens que não se aplicam devem ser registrados como “não se aplica”. A skill não
traduz nem decide cortes automaticamente. Sem a prova estrutural, a inspeção visual
e o resumo de decisões, não pedir aprovação ao usuário.

### 1.7 Aprovar e gerar a versão final

A full só deve ser gerada depois de uma aprovação explícita do usuário:

```text
.venv\Scripts\python.exe -B -m editor approve --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render full --workdir "<workdir>" --json
```

A aprovação registra o plano, a preview e as fontes que foram revisados. Se qualquer
um deles mudar depois da aprovação, repetir a preview e pedir aprovação novamente.

## 2. Técnicas de edição

### 2.1 Gancho em texto

O gancho em texto é uma frase curta que aparece logo no começo e deixa claro por que
vale a pena continuar assistindo. Ele pode apresentar o resultado, o problema ou a
pergunta central do vídeo. Deve ser específico e fácil de ler, não um resumo longo.

Exemplos:

- “Como editar um vídeo sem deixar o ritmo lento”;
- “O erro que faz seu tutorial perder clareza”;
- “Em 5 minutos, este projeto sai do bruto para a preview”.

Como escolher:

1. Identificar a promessa principal no briefing ou na transcrição.
2. Reduzir essa promessa a uma frase que o espectador entenda sem contexto.
3. Mostrar o texto no primeiro trecho, sem cobrir rosto, produto ou informação
   essencial.
4. Conferir a leitura na preview em uma tela pequena.

No plano, o gancho é uma camada `type: "text"` com caixa normalizada. O comando que
verifica e aplica a decisão é o fluxo normal do plano:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Não usar três frases, animações ou cores diferentes para compensar uma promessa
mal definida. Primeiro corrigir a frase, depois escolher o tratamento visual.

### 2.2 Decupagem

Decupagem é retirar erros de gravação, repetições, hesitações e silêncios vazios
para deixar a fala natural e objetiva. O objetivo não é cortar tudo que seja uma
pausa. Uma pausa pode transmitir emoção, dar tempo para o público pensar ou fazer
parte de uma piada.

Como fazer:

1. Ler a transcrição e marcar erros, frases repetidas e trechos que não levam a
   história adiante.
2. Usar a análise de áudio para encontrar possíveis silêncios, sem tratá-los como
   cortes automáticos.
3. Conferir os frames antes e depois do intervalo para descobrir se há mudança de
   ação, expressão ou enquadramento.
4. Cortar somente o intervalo necessário, preservando o início e o fim da palavra.
5. Ouvir a emenda na preview. Se a frase parecer apressada, devolver parte da
   pausa ou cobrir a emenda com b-roll.

Exemplo: se a transcrição mostra uma frase terminando em `12,40 s` e a próxima
começando em `13,10 s`, o intervalo de `0,70 s` é candidato a corte. Ele só deve
ser removido se não for uma pausa intencional. O plano deve então juntar o trecho
antes e o trecho depois, sem inventar uma nova fala.

Comandos usados para encontrar e revisar os cortes:

```text
.venv\Scripts\python.exe -B -m editor analyze transcribe --workdir "<workdir>" --source-id "main" --input "<video>" --device auto --json
.venv\Scripts\python.exe -B -m editor analyze audio --workdir "<workdir>" --source-id "main" --input "<video>" --json
.venv\Scripts\python.exe -B -m editor extract frames --workdir "<workdir>" --source-id "main" --input "<video>" --timestamps "12.0,12.5,13.0,13.5" --json
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Não existe um bom corte universal baseado apenas em decibéis. A análise aponta
possibilidades; o plano registra a decisão editorial.

### 2.3 B-roll e inserções

B-roll é uma imagem ou um clipe de apoio que aparece enquanto a fala continua. Ele
serve para mostrar o objeto que está sendo explicado, provar um resultado, variar a
imagem ou esconder uma emenda da decupagem. O melhor b-roll responde visualmente à
fala, em vez de apenas preencher espaço.

Escolher um b-roll quando:

- o espectador precisa ver algo para entender a explicação;
- a fala descreve uma ação, produto, tela, lugar ou resultado;
- a pessoa ficou tempo demais no mesmo enquadramento;
- o corte da fala ficaria perceptível sem uma cobertura visual.

Como montar:

1. Localizar na transcrição o intervalo que o b-roll deve ilustrar.
2. Conferir nos frames se a fala combina com o que a imagem mostra.
3. Escolher o trecho visual mais curto que comunique a ideia inteira.
4. Manter a fala principal no áudio enquanto o b-roll aparece.
5. Voltar para a imagem principal quando a informação visual deixar de ajudar.

No plano v2, um b-roll em tela cheia é um novo `segment` de vídeo. A faixa de
áudio pode continuar vindo do vídeo principal, usando `mode: replace`, como no
exemplo da seção 1.4. Depois, usar:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Para uma inserção em camada, como picture-in-picture ou uma imagem menor sobre o
vídeo, registrar `type`, `start_s`, `end_s`, `x`, `y`, `width` e `height` na lista
`layers[]`. Imagens podem ser PNG, JPEG/JPG ou WebP estáticos, e vídeos em camada
usam `fit: contain` ou `fit: crop` sem áudio implícito. A regra editorial continua
a mesma: a camada deve explicar ou reforçar a fala, e não competir com ela. Validar
e revisar na preview antes de gerar a full.

### 2.4 Zoom e movimento

Zoom, afastamento e deslocamento de enquadramento servem para dar ênfase ou variar
uma imagem estática. Usar movimento quando a pessoa revela um ponto importante,
quando um detalhe precisa ser destacado ou quando uma sequência longa precisa de
variação visual. O movimento deve ter uma razão perceptível.

Exemplos:

- aproximar suavemente o rosto durante a conclusão de uma ideia;
- aproximar um detalhe de uma tela no momento em que ele é explicado;
- afastar o enquadramento para marcar a mudança de assunto;
- acompanhar uma pessoa ou objeto somente quando o deslocamento ajuda a leitura.

Como decidir a intensidade:

1. Marcar na transcrição o início e o fim da ideia que merece destaque.
2. Conferir se há espaço visual para o movimento e se o recorte não corta o rosto
   ou o texto original.
3. Usar um movimento lento para ênfase e um movimento mais rápido apenas quando a
   própria narrativa pedir energia.
4. Verificar a preview para garantir que o movimento não cause desconforto nem
   pareça um efeito aplicado sem motivo.

O movimento deve ser declarado no plano, separado do ajuste de enquadramento. Não
usar `fit: crop` como sinônimo de zoom: `crop` define como a imagem cabe no canvas,
enquanto o movimento editorial precisa ter início, fim e comportamento próprios.
No schema v2, ele fica em `video.motion` com estados `from` e `to`, cada um com
`x`, `y` e `zoom`; os valores de `zoom` ficam entre `0.5` e `4.0` e a interpolação
é linear, sem easing.
Após incluir o movimento no contrato do plano, os comandos de verificação e revisão
continuam sendo:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

### 2.5 Cortes em J e em L

O corte em J começa o áudio da próxima cena antes de mostrar a imagem dela. O som
prepara a mudança e faz a troca visual parecer natural. O corte em L troca a imagem,
mas deixa o áudio da cena anterior continuar por alguns instantes. Ele permite que a
imagem nova entre sem interromper a frase ou a atmosfera da cena anterior.

Exemplo de corte em J:

- a imagem A termina em `10,0 s`;
- o áudio da cena B começa em `9,5 s`;
- a imagem B aparece em `10,0 s`.

Exemplo de corte em L:

- a imagem A termina em `10,0 s`;
- a imagem B começa em `10,0 s`;
- o áudio de A continua até `10,6 s` antes de o áudio de B assumir.

Usar J quando a fala seguinte ajuda a criar expectativa ou quando a troca de imagem
é mais importante que a troca de assunto. Usar L quando a fala atual deve terminar
com fluidez enquanto a imagem já mostra o próximo momento.

Como aplicar:

1. Escolher o ponto visual da troca.
2. Definir quanto do áudio deve atravessar a troca, normalmente apenas o necessário
   para preservar a frase.
3. Conferir se não há duas falas importantes competindo ao mesmo tempo.
4. Ouvir a transição na preview, principalmente com fones.

O intervalo de áudio e o intervalo visual devem ser registrados explicitamente no
plano. Em uma fronteira entre segmentos, declare o corte em `cuts[]` com
`after_segment`, `type: "j"` ou `type: "l"` e `duration_s`. Depois de alterar esses
intervalos, executar:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Não aplicar J ou L em todos os cortes. A técnica funciona quando resolve uma troca
específica, não quando vira um padrão perceptível.

### 2.6 Pattern interrupt, ou quebra de padrão

Pattern interrupt é uma mudança deliberada que interrompe uma sequência visual ou
sonora repetitiva. Pode ser um b-roll, uma mudança de enquadramento, um texto, uma
imagem, um som curto ou uma mudança de ritmo. A quebra deve chamar a atenção para a
próxima informação, não distrair do conteúdo.

Como usar:

1. Identificar uma sequência em que a imagem, a voz e o ritmo permaneceram iguais
   por tempo suficiente para o espectador perder atenção.
2. Encontrar a próxima ideia importante na transcrição.
3. Escolher uma única mudança que ajude a introduzir essa ideia.
4. Voltar ao padrão normal depois que a atenção foi recuperada.

Exemplo: depois de uma explicação longa olhando para a câmera, inserir por dois
segundos uma gravação da tela que será demonstrada e retornar à pessoa para a
conclusão. A inserção tem uma função narrativa, portanto não é apenas um efeito.

O trabalho é feito no `plan.json` com os mesmos comandos de validação e preview:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

### 2.7 Organização e fluxo de trabalho

Organização evita cortes feitos no arquivo errado, fontes perdidas e renderizações
que não podem ser reproduzidas. Para cada vídeo, manter uma pasta de trabalho com
nomes estáveis:

```text
<workdir>\
  .tmp\
  sources\
  analysis\
    audio\
    transcription\
  frames\
  plan.json
  preview.mp4
  approval.json
  full.mp4
```

Regras práticas:

- manter os originais em `sources` e nunca editar por cima deles;
- usar IDs curtos e estáveis, como `main`, `broll-desk` e `voice`;
- não renomear uma fonte depois de aprovar o plano;
- revisar o `plan.json` antes de cada preview;
- manter a `preview.mp4` separada da `full.mp4`;
- não substituir a full aprovada sem repetir a aprovação.

Comandos que materializam esse fluxo:

```text
.venv\Scripts\python.exe -B -m editor init --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor capabilities --json
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor approve --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render full --workdir "<workdir>" --json
```

### 2.8 Storytelling, ou narrativa

Storytelling é organizar as informações para que o espectador nunca precise
adivinhar por que está vendo aquele trecho. Uma estrutura simples para YouTube é:

1. **Início:** apresentar o gancho, o problema ou o resultado prometido.
2. **Desenvolvimento:** mostrar a explicação, o processo, as provas e os exemplos.
3. **Conclusão:** entregar o resultado, resumir a ideia ou fazer a chamada para ação.

Antes de cortar, escrever uma frase que descreva a função de cada bloco. Se um
trecho não apresenta, desenvolve, prova ou conclui nada, ele é candidato a corte ou
precisa ser reescrito na montagem.

A transcrição ajuda a reorganizar ideias e os frames ajudam a escolher a imagem que
melhor sustenta cada uma. Em seguida, registrar a ordem no plano e conferir o fluxo:

```text
.venv\Scripts\python.exe -B -m editor analyze transcribe --workdir "<workdir>" --source-id "main" --input "<video>" --device auto --json
.venv\Scripts\python.exe -B -m editor extract frames --workdir "<workdir>" --source-id "main" --input "<video>" --interval-s "5" --json
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Não usar b-roll ou texto para esconder uma estrutura que ainda não está clara.
Primeiro resolver a ordem da história.

### 2.9 Ritmo de edição

Ritmo é a velocidade com que o vídeo entrega informação e muda de estímulo. Ele não
é uma quantidade fixa de cortes por minuto. Uma explicação técnica pode precisar de
tempo para ser entendida, enquanto uma reação pode pedir cortes mais rápidos.

Para ajustar o ritmo:

1. Remover silêncios vazios, erros e repetições.
2. Alternar a imagem quando a mudança acrescentar informação ou energia.
3. Usar b-roll para mostrar o que está sendo explicado e cobrir cortes necessários.
4. Preservar pausas que dão clareza, emoção ou humor.
5. Assistir à preview sem pausar e observar onde a atenção cai.

O diagnóstico deve combinar transcrição, áudio e imagem. Se o vídeo parece lento,
não cortar automaticamente todos os intervalos: descobrir primeiro se o problema é
uma pausa longa, uma explicação repetida, uma imagem estática ou uma ideia mal
ordenada.

Para revisar o ritmo:

```text
.venv\Scripts\python.exe -B -m editor analyze transcribe --workdir "<workdir>" --source-id "main" --input "<video>" --device auto --json
.venv\Scripts\python.exe -B -m editor analyze audio --workdir "<workdir>" --source-id "main" --input "<video>" --json
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

## 3. Perfil de entrega para YouTube

Para um vídeo horizontal, usar como ponto de partida MP4, vídeo H.264, áudio AAC,
áudio em 48 kHz, proporção 16:9 e a mesma taxa de quadros da gravação. Evitar barras
pretas incorporadas quando for possível preservar ou recortar a imagem corretamente.
Essas escolhas seguem as [recomendações oficiais de codificação do YouTube](https://support.google.com/youtube/answer/1722171?hl=pt-BR)
e as [orientações oficiais de proporção e resolução](https://support.google.com/youtube/answer/6375112?co=GENIE.Platform%3DDesktop&hl=en),
mas ainda é necessário conferir as exigências específicas da publicação.

O perfil do plano usado neste projeto é `16:9`. A preview deve ser a referência para
avaliar enquadramento, leitura e ritmo antes da aprovação. O arquivo final só deve
ser entregue depois de passar pela validação e pela aprovação explícita.

## 4. Comandos por finalidade

| Objetivo | Comando |
|---|---|
| Verificar o ambiente local | `editor capabilities --json` |
| Saber o que foi falado e quando | `editor analyze transcribe ... --json` |
| Encontrar níveis e possíveis silêncios | `editor analyze audio ... --json` |
| Conferir a imagem em momentos específicos | `editor extract frames ... --json` |
| Conferir se o plano é válido | `editor plan validate ... --json` |
| Gerar uma versão para revisão | `editor render preview ... --json` |
| Registrar a aprovação humana | `editor approve ... --json` |
| Gerar a versão final | `editor render full ... --json` |

Os comandos completos devem ser executados com o Python do ambiente virtual, como
nos exemplos desta referência. Os três pontos na tabela representam os argumentos
obrigatórios, especialmente `--workdir`, `--source-id` e `--input` nas análises.

O agente não precisa pedir ao usuário que escreva comandos ou JSON. Ele deve
traduzir o pedido para o plano, executar as análises, mostrar a preview e perguntar
em linguagem comum se a edição está aprovada.
