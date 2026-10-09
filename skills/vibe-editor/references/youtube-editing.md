# Padrão de qualidade de edição para YouTube

Esta referência define o padrão de qualidade que a skill aplica a **qualquer vídeo, de qualquer pessoa**: como conduzir o fluxo com a CLI `editor` (plano, preview, aprovação e full) e o que um corte, um letreiro, o áudio e a entrega precisam cumprir para o resultado sair limpo e confiável.

O padrão nasceu da prática do canal Sonni Viaja (de Marco Kormoczi), analisada no `S04E31` e refinada nos episódios seguintes. Ele separa duas coisas:

- **Padrão de qualidade** (este arquivo): processo, cortes que não comem palavras, texto na tela com fonte, áudio nivelado, imagem íntegra, auditoria antes da aprovação. Vale sempre.
- **Estilo** (seção 3): ritmo, uso de b-roll, zoom, música, aparência do texto, gancho e fecho. É decisão do briefing de cada vídeo. O estilo do canal Sonni Viaja é um perfil pronto em [canal-sonni-viaja.md](canal-sonni-viaja.md).

Os números deste arquivo são **padrões ajustáveis**: o briefing explícito do usuário prevalece, e o agente registra no resumo qualquer valor que mudar. Os números do canal (cortes por minuto, durações) descrevem episódios já feitos, não são metas.

## 1. Fluxo completo de edição

O agente segue esta ordem. A decisão editorial vem antes do plano, e o plano vem antes da renderização.

### 1.1 Entender o vídeo antes de cortar

Antes de abrir a timeline, confirmar:

- qual é a promessa do vídeo, para quem ele é e em que idioma está o público (os textos na tela usam o idioma do público);
- o formato: vlog, tutorial, opinião, entrevista, demonstração ou outro;
- quais arquivos são fala principal, apoio, voz, música e imagens, e qual é a ordem dos acontecimentos;
- o estilo (seção 3): se o vídeo é do canal Sonni Viaja, usar o perfil dele; se não for, perguntar só o que o usuário ainda não definiu e propor o padrão da tabela da seção 3;
- fatos que vão aparecer na tela (preços, nomes, datas) e de onde cada um vem: comprovantes, prints, anotações do apresentador;
- se a fala cita mídia que não está nos arquivos (uma foto, um vídeo de terceiros), isso vira pendência.

Assistir ao bruto ou ler a transcrição antes de definir a ordem: identificar fala, gesto, reação, objeto mostrado e a sequência dos acontecimentos. Se uma resposta não estiver definida, fazer uma pergunta objetiva. Não escolher sozinho um corte, uma ordem ou um texto que mude o sentido do vídeo.

### 1.2 Conferir o ambiente e as fontes

Usar o inventário local antes de começar:

```text
.venv\Scripts\python.exe -B -m editor capabilities --json
```

Esse comando confirma quais ferramentas e modelos estão disponíveis. Depois, usar um diretório de trabalho absoluto, com uma pasta própria para aquele vídeo, e preservar os arquivos originais. Para um trabalho novo, preparar a estrutura antes de analisar ou escrever o plano:

```text
.venv\Scripts\python.exe -B -m editor init --workdir "<workdir>" --json
```

O `init` é idempotente, não cria estado editorial e não substitui artefatos já publicados.

**Formato das fontes.** O editor lê direto `.mp4` e `.mov` com vídeo H.264 ou HEVC (Main e Main 10, 8 ou 10 bits) e no máximo um stream de áudio AAC; trilhas de dados (`tmcd`, telemetria) e miniatura anexada são ignoradas, então os brutos de câmera e de celular entram sem conversão. Fontes HDR saem lavadas (sem tone mapping): avisar o usuário. Contêineres como `.mkv`, mais de um áudio, vídeo extra, legenda, VP9, AV1 e HEVC em 4:2:2, 4:4:4 ou 12 bits recebem `MEDIA_UNSUPPORTED`. Isso não impede a edição: leia [source-preparation.md](source-preparation.md), que traz o diagnóstico com `ffprobe` e a conversão testada para cópias H.264 limpas. As cópias ocupam disco e acrescentam uma geração de compressão, então só se criam com autorização do usuário.

### 1.3 Analisar fala, áudio e imagem

Para cada fonte de fala, executar as três análises abaixo. Trocar os valores entre `<...>` pelos caminhos e identificadores reais. Informar o idioma da fala com `--language` (por exemplo `pt`) quando for conhecido.

```text
.venv\Scripts\python.exe -B -m editor analyze transcribe --workdir "<workdir>" --source-id "main" --input "<video-principal>" --language "<idioma>" --device auto --json
.venv\Scripts\python.exe -B -m editor analyze audio --workdir "<workdir>" --source-id "main" --input "<video-principal>" --json
.venv\Scripts\python.exe -B -m editor extract frames --workdir "<workdir>" --source-id "main" --input "<video-principal>" --interval-s "5" --json
```

Cruzar os resultados, em vez de confiar em uma análise isolada:

- a transcrição mostra o que foi dito e em que momento, inclusive as palavras;
- a análise de áudio mostra níveis, picos e possíveis intervalos de silêncio;
- os frames mostram o que estava aparecendo na imagem;
- o briefing decide se aquele momento deve ser mantido, cortado ou destacado.

Os tempos de palavra da transcrição têm erro da ordem de 0,2 s (o editor tolera até 0,5 s e só usa os modelos locais `tiny`, `base` e `small`), então as folgas de corte da seção 2.3 são ordens de grandeza que a revisão da preview confirma. Em trechos de imagem sem fala a "fala" que a transcrição acha costuma ser lixo; não usar essas palavras para cortar.

Para localizar falas em outro idioma, transcrever também com `--language en` (ou o idioma esperado) e cruzar com os trechos de baixa confiança da primeira transcrição. A transcrição forçada em outro idioma alucina em trechos sem fala e traduz a fala do idioma principal, então serve só para localizar a troca; o texto da legenda vem do áudio ou do relato do usuário.

Os artefatos ficam em:

```text
<workdir>\analysis\transcription\main.json
<workdir>\analysis\audio\main.json
<workdir>\frames\main\manifest.json
```

Se houver uma fonte separada, como uma gravação de voz, analisá-la com outro `source-id`. Nunca presumir que o segundo arquivo começa no mesmo instante do primeiro sem conferir os tempos.

### 1.4 Montar o plano

O `plan.json` registra as decisões da edição. Ele deve dizer, de forma explícita:

- quais trechos entram e em qual ordem;
- qual arquivo aparece em cada trecho;
- qual áudio acompanha cada imagem;
- quais textos, imagens ou vídeos entram em camada e quando;
- quais ajustes de áudio serão usados.

O exemplo abaixo é uma edição básica: dois trechos da mesma fonte com um jump cut entre eles, um rótulo de texto e áudio nivelado. Os tempos são ilustrativos. O JSON foi validado com `plan validate` e renderizado com preview e full. Um exemplo no estilo do canal, com gancho, QR code e tela final, está em [canal-sonni-viaja.md](canal-sonni-viaja.md).

```json
{
  "schema_version": 2,
  "profile": {"aspect_ratio": "16:9", "max_full_width": 1920},
  "sources": [
    {"id": "main", "kind": "video", "path": "sources/main.mp4"}
  ],
  "segments": [
    {
      "video": {"source_id": "main", "in_s": 2.1, "out_s": 8.4, "fit": "preserve"},
      "audio": {"mode": "keep", "tracks": [{"source_id": "main", "in_s": 2.1, "out_s": 8.4, "gain_db": 0, "loop": false}]}
    },
    {
      "video": {"source_id": "main", "in_s": 9.2, "out_s": 14.0, "fit": "preserve"},
      "audio": {"mode": "keep", "tracks": [{"source_id": "main", "in_s": 9.2, "out_s": 14.0, "gain_db": 0, "loop": false}]}
    }
  ],
  "cuts": [],
  "layers": [
    {
      "type": "text",
      "start_s": 3.0,
      "end_s": 6.5,
      "x": 0.04,
      "y": 0.78,
      "width": 0.92,
      "height": 0.14,
      "text": "Nome do lugar · tradução",
      "font": "Arial",
      "size_px": 70,
      "color": "#FFFFFF",
      "align": "center",
      "wrap": true
    }
  ],
  "audio_processing": {
    "normalize_loudness": true,
    "target_lufs": -17,
    "limiter": {"enabled": true, "ceiling_dbfs": -2},
    "ducking": false
  }
}
```

Como o padrão de qualidade vira campos do plano:

- **Resolução (16:9):** `profile.max_full_width` deve ser a largura da fonte arredondada **para cima** a um múltiplo de 32, no máximo 3840. Sem isso a full sai limitada a 1920: um bruto de 2560 de largura saiu em 1920 e um de 2688 só sai inteiro com `max_full_width` em 2688. O editor não faz upscale.
- **Cortes:** cada jump cut é um segmento novo da mesma fonte, com `in_s` e `out_s` na grade de quadros da fonte (a 29,97 quadros/s, múltiplos de 1001/30000 s).
- **Áudio:** `mode: keep` com a faixa da própria fonte e `gain_db: 0`. O nivelamento fica em `audio_processing`, uma vez para o vídeo inteiro.
- **Proporção do perfil e fontes de outra proporção:** `fit: preserve` só passa quando a proporção da fonte é a do perfil. Uma fonte de outra proporção (4:3, 21:9, 1366 × 768, vertical, ou uma fonte 16:9 num perfil vertical) só passa com `fit: contain` (imagem inteira, com barras) ou `fit: crop` (recorte, que `reframe` conduz). Um vídeo vertical se faz com um perfil `9:16` (seção 1.4, "Proporção de saída"). Uma fonte vertical num perfil 16:9 vira um quadro 16:9 de largura pequena, com barras ou recorte pesado: avisar o usuário antes de seguir.
- **Fonte sem áudio** (clipe de jogo, gravação de tela sem som): não passa por `analyze audio`, que exige exatamente um stream de áudio. Entra no plano com `"audio": {"mode": "mute", "tracks": []}`; com `mode: keep` e uma faixa de uma fonte sem áudio o plano é recusado.
- **Texto:** uma camada `type: "text"` por letreiro. O tamanho padrão de um rótulo é cerca de 3,7% da largura da full (70 px a 1920 de largura, 100 px a 2688, 40 px a 1080). Em vertical o rótulo costuma pedir corpo maior; conferir na preview.
- **Tamanho do texto na preview:** `size_px` vale na largura da full e a preview o escala pela razão das larguras (arredondado, mínimo 1), com a mesma quebra de linha. O texto da preview ocupa a mesma fração do quadro que na full, então o tamanho pode ser avaliado na preview. Os tamanhos desta referência valem para a full.
- **Sombra e contorno do texto:** `layers[].shadow` e `layers[].outline`, opcionais e só na camada `text` (campos e exemplo em `SKILL.md`, seção 5). `shadow` leva `offset_x_px` e `offset_y_px` (inteiros de -100 a 100, positivo para a direita e para baixo) e `blur_px` (inteiro de 0 a 50), obrigatórios, mais `color` (padrão `#000000`) e `opacity` (0 a 1, padrão 0.5). `outline` leva `width_px` (inteiro de 1 a 30), obrigatório, e `color` (padrão `#000000`). Os valores em pixels valem na largura da full, como `size_px`, e a preview os escala na mesma proporção. Texto claro sobre céu ou janela estourada, e texto escuro sobre fundo escuro, perdem leitura sem eles: usar sombra suave ou contorno fino e conferir os momentos de fundo claro na preview.

Duas limitações do editor que afetam o texto e que o agente deve avisar ao usuário: **sombra e contorno são recortados pela caixa da camada** (o texto parte do topo dela, então dar folga à caixa) e **não há caixa de fundo**. Na auditoria da seção 1.6, tratar legibilidade como risco explícito e decidir com o usuário o que fazer. Não inventar campos fora do schema.

#### Proporção de saída, vertical e reenquadramento

`profile.aspect_ratio` aceita `16:9`, `9:16`, `1:1` e `4:5`, uma por plano. Cada proporção é um trabalho próprio (pasta, plano, preview, aprovação e full), porque o contrato é de uma preview, uma aprovação e uma full. O destino decide:

| Destino | Proporção | Full | Preview |
|---|---|---|---|
| YouTube | `16:9` | largura da primeira fonte, até `max_full_width`, sem upscale | até 720 px |
| Shorts, Reels, TikTok | `9:16` | 1080x1920 | 404x720 |
| Feed quadrado | `1:1` | 1080x1080 | 720x720 |
| Feed vertical | `4:5` | 1080x1350 | 576x720 |

Fora do 16:9 o tamanho é fixo de plataforma, independente da fonte, e `max_full_width` é recusado (`PLAN_INVALID`). Uma gravação horizontal vira vertical por `fit: crop` (o canvas recorta a fonte, com a faixa visível definida por `reframe`) ou por `fit: contain` (imagem inteira com barras pretas). Para o vertical de Shorts e Reels, `crop` com `reframe` é o caminho: o sujeito ocupa o quadro e a câmera o acompanha.

**Marcar os keyframes.** `video.reframe.keyframes` tem de 1 a 32 itens por segmento, só com `fit: crop`, e não convive com `motion` no mesmo segmento (em trabalhos novos, usar `reframe`, inclusive para punch-in em 16:9). Cada keyframe leva:

- `t_s`: segundos desde o início do segmento (não da fonte), estritamente crescente, de 0 até `out_s - in_s`;
- `x` e `y`: onde o sujeito está sobre a fonte original, de 0 a 1 (origem no canto superior esquerdo), que passa a ser o centro do recorte;
- `zoom` (1.0 a 4.0, padrão 1.0): 1.0 é o maior recorte da proporção que cabe na fonte e 2.0 mostra metade da largura e da altura dele;
- `interpolation` (`ease` ou `linear`, padrão `ease`): a curva até o keyframe seguinte. `ease` parte e chega com velocidade zero, o movimento natural de uma câmera conduzida; `linear` mantém velocidade constante, para um acompanhamento mecânico. No último keyframe não tem efeito.

O processo, a partir dos frames extraídos (seção 1.3, `extract frames` da fonte original, em intervalo curto): olhar cada frame e anotar onde está o sujeito como fração da largura e da altura; converter o tempo do frame na fonte para `t_s = tempo na fonte - in_s`; criar um keyframe onde o sujeito muda de lugar e repetir a posição (dois keyframes iguais) para segurar o enquadramento. Um keyframe só é um foco estático. Antes do primeiro keyframe e depois do último vale o estado do primeiro e do último, e quando o centro levaria o recorte para fora da fonte ele encosta na borda e nunca mostra preto, então não é preciso calcular deslocamentos.

**Aviso de ampliação.** `plan validate` devolve `data.profile.upscale`, uma lista de `{"segment", "factor"}` com a posição do segmento na timeline (de 1 em diante) e o fator, em 2 casas, com que a fonte original é ampliada no canvas da full; só entra quem passa de 1.0. No `fit: crop` o fator é a largura do canvas da full sobre a largura do recorte usado (o do maior `zoom` dos keyframes do `reframe`, ou o recorte central quando não há `reframe`). No `contain` e no `preserve` é a escala da imagem inteira no canvas (o menor entre largura e altura do canvas sobre as da fonte). Em qualquer `fit`, um `motion` multiplica o fator pelo maior `zoom` entre `from` e `to` (abaixo de 1.0 reduz). Uma fonte 1920x1080 em 9:16 com `crop` e `zoom` 1.0 informa 1.78; uma 3840x2160 em 9:16 informa lista vazia; uma 640x360 em 9:16 com `contain` informa 1.69. Lista vazia significa que nenhum segmento é ampliado. O aviso não bloqueia: o agente informa ao usuário que esses trechos perdem nitidez (mais com `zoom` alto) e segue se ele aceitar.

**Zonas seguras do Shorts e do Reels.** Orientação editorial, sem validação no código. A interface dessas plataformas cobre a faixa inferior (título, legenda, botões) e o lado direito. Manter rosto e texto fora dessas faixas, com margens conservadoras sugeridas: cerca de 20% da altura livres embaixo, 10% no topo e 10% nas laterais, um pouco mais à direita. Esses valores são uma sugestão prudente, não medidas oficiais, e a interface das plataformas muda. Posicionar `y` e `height` das camadas de texto e os keyframes de `reframe` dentro dessa área e conferir nos quadros da preview (seção 1.6).

Validar o plano antes de gerar qualquer vídeo:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
```

Se a validação falhar, corrigir a fonte, o intervalo ou a decisão no `plan.json`. Não ignorar o erro e não tentar compensá-lo durante a renderização.

### 1.5 Gerar, assistir e ajustar a preview

Renderizar uma versão de revisão:

```text
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Assistir à `preview.mp4` procurando seis coisas: a história está clara, os cortes tiram pausas e falsos inícios sem comer palavras, o texto pode ser lido, o letreiro não cobre rosto nem o objeto que a pessoa aponta, a imagem acompanha a fala e o áudio está uniforme de um corte ao outro. Se algo precisar mudar, editar o plano, validar novamente e gerar outra preview.

### 1.6 Auditar a preview antes da aprovação

A revisão da preview tem duas camadas que precisam ser registradas separadamente. Validação estrutural confirma o contrato do plano e o arquivo produzido. Inspeção visual e editorial confirma o que o espectador realmente verá e ouvirá. Uma camada sem a outra bloqueia o pedido de aprovação.

#### Evidência estrutural

Execute novamente:

```text
.venv\Scripts\python.exe -B -m editor plan validate --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render preview --workdir "<workdir>" --json
```

Guarde os resultados e confirme a timeline, fontes, cortes, camadas e os caminhos de `preview.mp4` e das análises. O sucesso desses comandos não prova continuidade visual, leitura do lettering ou naturalidade do encerramento.

#### Auditoria temporal e visual

Para cada fronteira de fala, compare os tempos de `words[]` em `analysis\transcription\<source-id>.json` com o corte no plano. Registre se o corte preserva palavras, termina em pausa natural ou remove fala intencionalmente. Para cortes com movimento ou revelação, para o início e fim de cada movimento e para o encerramento, extraia frames da preview:

```text
.venv\Scripts\python.exe -B -m editor extract frames --workdir "<workdir>" --source-id "preview" --input "<workdir>\preview.mp4" --timestamps "<corte-0.5>,<corte>,<corte+0.5>,<movimento-inicio>,<movimento-fim>,<fim-1>,<fim>" --json
```

Com `reframe`, extrair também um frame da preview em cada keyframe (tempo da timeline = início do segmento na timeline + `t_s`) e conferir sujeito e zonas seguras. Assista à preview inteira e confirme continuidade de ação, olhar, enquadramento e áudio. Confira que movimentos não cortam o assunto nem iniciam ou terminam de forma abrupta, e que o último plano não congela, fica preto ou termina o áudio antes da imagem. Para cada lettering, confirme fonte real, caixa, safe area, quebra de linha, contraste, tamanho e legibilidade. `plan validate` não substitui essa inspeção.

#### Registro das decisões humanas

Antes de pedir aprovação, apresente um resumo com:

- cada fala estrangeira e a decisão entre manter, traduzir ou legendar, com intervalo e justificativa;
- cada arquivo excluído, identificado por ID ou caminho, e o motivo;
- cada fala removida intencionalmente, com fonte, intervalo, palavras afetadas e motivo, separada de pausas naturais;
- a observação e a evidência usada para cortes, movimentos, textos e encerramento.

Itens que não se aplicam devem ser registrados como “não se aplica”. A skill não traduz nem decide cortes automaticamente. Sem a prova estrutural, a inspeção visual e o resumo de decisões, não pedir aprovação ao usuário.

### 1.7 Aprovar e gerar a versão final

A full só deve ser gerada depois de uma aprovação explícita do usuário:

```text
.venv\Scripts\python.exe -B -m editor approve --workdir "<workdir>" --json
.venv\Scripts\python.exe -B -m editor render full --workdir "<workdir>" --json
```

A aprovação registra o plano, a preview e as fontes que foram revisados. Se qualquer um deles mudar depois da aprovação, repetir a preview e pedir aprovação novamente.

## 2. Padrão de qualidade

### 2.1 O padrão em uma página

1. **Processo com prova.** Briefing confirmado, análise cruzada, plano validado, preview auditada e aprovação explícita antes da full. Nada de aprovação implícita.
2. **Cortes que não comem palavras.** O corte tira pausa, falso início e hesitação e preserva a ideia inteira. Fala só sai de propósito, e isso é registrado.
3. **Ordem que o espectador entende.** A ordem vem do briefing. Quando o vídeo acompanha acontecimentos, segue a ordem em que aconteceram; qualquer inversão (gancho, conclusão antecipada) é decisão justificada e registrada.
4. **Texto na tela com fonte.** O texto entrega informação que o espectador não teria como saber, no idioma do público. Todo fato, nome e valor tem fonte registrada; o que não tem fonte não vai para a tela.
5. **Áudio uniforme.** Um único tratamento para o vídeo inteiro, nivelado no alvo e com pico controlado, sem ajuste de volume por clipe.
6. **Imagem íntegra.** Resolução e taxa de quadros da fonte, sem recorte, escala ou efeito sem motivo perceptível.
7. **Começo e fim naturais.** O vídeo dá um motivo para continuar logo no início e termina com imagem e áudio juntos, sem frame preto nem corte no meio de uma palavra.
8. **Pendências à vista.** Tudo que depende do usuário (valor sem comprovante, nome sem placa, mídia citada e ausente, fala dele que ficou sem confirmação) é entregue em uma lista, não escondido.
9. **Estilo é decisão do briefing.** O agente propõe o padrão da seção 3 e ajusta ao que o usuário pedir.

### 2.2 Ordem e estrutura

- Quando o vídeo acompanha uma experiência (viagem, evento, processo), a montagem conserva a ordem vivida. A fala organiza as cenas, e a imagem fica quando acrescenta prova visual ou reação útil à fala.
- Deslocamentos e mudanças de ambiente toleram mais cortes. Mapas, demonstrações, interações e explicações longas pedem planos longos para dar tempo de compreender.
- Reações, dúvidas e hesitações breves que fazem parte da experiência ficam, em vez de serem reduzidas a uma palavra.
- Antes de cortar, escrever uma frase que descreva a função de cada bloco. Se um trecho não apresenta, desenvolve, prova ou conclui nada, ele é candidato a corte.
- Visitas ou etapas parecidas pedem corte mais agressivo que o resto, para não repetir a mesma estrutura.
- Ao fundir vários arquivos ou episódios, manter a ordem cronológica entre eles. Se o bruto da conclusão tiver fala anterior a ela, dividir o bruto: a fala anterior fica no lugar cronológico e a conclusão vai por último.

### 2.3 Ritmo e regras de corte

O corte acontece quando a situação muda, o enquadramento passa a mostrar algo novo ou um trecho deixa de contribuir. Ritmo não é uma quantidade fixa de cortes por minuto. A regra de pausas padrão, calibrada em vídeo falado em primeira pessoa, é aplicada sobre as palavras de `analysis\transcription\<source-id>.json`:

- uma pausa entre duas palavras de **0,5 s ou mais** vira corte;
- sobram **0,05 s** antes da primeira palavra e **0,23 s** depois da última de cada corte;
- quando a pausa passa de **3 s** (geralmente uma ação visual, como mostrar algo), mantêm-se até 3 s dela antes de cortar;
- cortes menores que **0,6 s** são descartados. Uma palavra isolada no fim (um "tchau") cai nessa regra e sumiria: tratar a janela final como um corte contínuo;
- trechos de imagem sem fala não passam pela regra: entram como um corte só, no intervalo escolhido.

A regra faz o corte fino. O que entra e o que fica de fora de cada fonte continua sendo decisão de conteúdo, e ela não remove falsos inícios nem muletas, então a primeira passada tem cortes mais longos que os de um editor humano. Espere lapidação na revisão.

Os valores se ajustam ao gênero: um vídeo com pausas intencionais (entrevista, demonstração, humor) pede pausa mínima maior ou mais silêncio mantido. O agente propõe o ajuste, registra no resumo e confirma com o usuário. Não existe corte universal baseado só em decibéis: a análise de áudio aponta possibilidades e o plano registra a decisão editorial.

Preservar a duração necessária para compreender uma fala, um gesto, uma reação ou um objeto mostrado: quando a pessoa aponta algo, o plano fica o tempo para o espectador localizar o que ela mostra.

### 2.4 Abertura

- Um vídeo precisa dar um motivo para continuar assistindo nos primeiros segundos.
- O padrão: copiar do próprio vídeo a **frase mais forte**, sem a preparação, de **um assunto só**, e decidir **depois de o corpo estar cortado**, porque depende de ver o que ficou. A duração é consequência da cena.
- Se a abertura precisar de mais fôlego, escolher outra cena que se sustente, em vez de emendar a anterior.
- Quando o estilo pedir outra forma (frase de abertura em texto, resultado final mostrado antes), o agente confirma com o usuário e segue a mesma regra: específico, curto, sem cobrir rosto ou informação essencial.
- O gancho tem o mesmo tratamento de áudio do resto, e a sonoridade dele é comparada com a do trecho seguinte, para evitar saltos.

### 2.5 Texto na tela

#### 2.5.1 Quando pôr texto

Põe-se texto quando o espectador não teria como saber a informação:

- toda fala em outro idioma que o público não entende, na íntegra, incluindo trocas curtas de cortesia;
- nomes próprios, lugares, estabelecimentos, termos técnicos e siglas que o público precisa para acompanhar, com o nome oficial;
- texto estrangeiro que a pessoa diz não entender ou descreve com um palpite, com a tradução ou a identificação pesquisada;
- valores, sempre com moeda, base (impostos, data, quantidade) e conversão para a moeda do público quando for outra;
- correção de algo dito errado, seja valor ou nome.

Não se põe legenda corrida da fala no idioma do público, salvo se o usuário pedir.

#### 2.5.2 Fonte do que vai na tela

Nome de lugar sai da placa visível no próprio vídeo e do site oficial; tradução, de fonte confiável; valor, do comprovante, de anotações da pessoa ou de cálculo feito por ferramenta, nunca de cabeça. O que não tem fonte fica marcado "confirmar" e é listado ao usuário.

- **Nome sem placa legível nem fonte direta não vai para a tela**, mesmo marcado "confirmar".
- **Valor falado diferente do comprovante ou das anotações da pessoa:** vale o que ela documentou, com um cartão "Correção: ...", no instante em que o valor é falado.
- **Fato dito errado, com evidência no vídeo e fonte oficial**, entra como "Correção: ...". Correção de frase solta sem evidência no vídeo fica como proposta ao usuário, não vai para a tela.
- **Leitura de pouco risco, com fonte indicada,** pode ficar na primeira passada e entra na lista de pendências.
- Antes de qualquer correção, olhar o quadro em resolução total, não a miniatura.
- Cotação de moeda: a do dia de cada gravação, em valores arredondados ("cerca de"), com a fonte registrada.

#### 2.5.3 Legibilidade

- Texto curto, que se lê no tempo em que fica na tela: durações de cerca de 2 a 4 s para rótulos, mais tempo para cartões de preço e legendas que seguem a fala.
- O letreiro não cobre rosto, o objeto que a pessoa aponta, comprovante ou prato. Manter na faixa inferior ou em um canto, fora da safe area das bordas.
- O texto entra **quando o assunto é dito**, não quando o plano corta.
- Contraste suficiente sobre o fundo real daquele instante. Em fundo claro, usar `shadow` ou `outline` (seção 1.4) e conferir esses momentos na preview.
- Quebra de linha em ponto natural, no máximo duas linhas.

#### 2.5.4 Varredura (fazer depois do corte, antes de criar os letreiros)

1. **Nomes próprios e termos.** Listar tudo que é nomeado na fala e tudo que tem placa legível no quadro. Para cada item, pesquisar o nome oficial e conferir com a placa.
2. **Frases de dúvida.** Buscar na transcrição "não sei", "acho que", "parece", "será que", "deve ser", "como é o nome" e leituras fonéticas de texto estrangeiro. Cada ocorrência vira pesquisa e, se houver resposta, letreiro.
3. **Falas em outro idioma.** Cruzar transcrições em idiomas diferentes (seção 1.3) e localizar as trocas. Legendar todas.
4. **Valores.** Toda cifra dita vira cartão, com conversão e fonte.
5. **Verso de tudo que a pessoa mostra e não identifica** (nota, prato, objeto).
6. **Conferir no quadro antes de afirmar.** Nome, placa e correção saem do quadro em resolução total e de fonte confiável, nessa ordem.

O produto da varredura é uma tabela com tempo, texto e **fonte do dado**, que o usuário aprova antes de os letreiros irem para o plano. O que o usuário recusar sai do plano.

### 2.6 Áudio

- Um único tratamento para o vídeo inteiro, sem ajuste de volume por clipe. No plano: `audio_processing` com `normalize_loudness`.
- Alvo padrão: cerca de **-17 LUFS** integrados e pico real **abaixo de -1 dBFS** (`target_lufs: -17` e `limiter.ceiling_dbfs: -2`, que deixa margem para o AAC). Faixa de sonoridade (LRA) de 7 a 8 LU é o esperado; mais que isso indica material muito variado. Se o usuário tiver outro alvo, usar o dele.
- O nivelamento do editor **não remove ruído de fundo**. Fontes fracas e irregulares precisam dele (um bruto de vlog em -20 a -24 LUFS e LRA de 13 a 16 é comum), mas o ruído continua.
- Preservar a frase inteligível e completa, não cortar reação só porque há intervalo sem palavras, manter som ambiente útil à presença e ajustar níveis sem apagar o caráter da gravação.
- Música e efeitos só entram se o estilo pedir: fonte de áudio no plano, nível claramente abaixo da voz, conferido de ouvido na preview.
- Comparar a sonoridade do começo com a do bloco seguinte e a do fecho com a média. Medir o arquivo final com `ffmpeg -af ebur128=peak=true` e informar o resultado.

### 2.7 Imagem

- Resolução e taxa de quadros da fonte (seção 1.4, `max_full_width`). `fit: preserve` quando a fonte tem a proporção do perfil. Em vertical o recorte é o próprio formato, conduzido por `reframe` (seção 1.4).
- Sem escala, recorte, zoom, mudança de velocidade ou correção de cor sem motivo perceptível. Quando o estilo pedir uma técnica (cobertura de imagem, zoom em ênfase, corte em J ou L, música), o editor suporta (ver `SKILL.md`, seção do plano) e a regra é uma só: ela precisa explicar ou reforçar a fala, não competir com ela, e é conferida na preview.
- Câmera na mão, tremida e variações de exposição ficam como gravadas, salvo pedido contrário.
- Pessoas que aparecem na cena (acompanhantes, equipe) ficam nas reações e falas; o plano não as esconde.

### 2.8 Fecho

- O último plano e o áudio terminam juntos, sem congelar, sem preto e sem cortar palavra. A auditoria da seção 1.6 confere o último segundo.
- Convite para inscrição, QR code, tela final e teaser do próximo vídeo só entram se o estilo pedir. Se entrarem, o convite falado, o QR e a tela final seguem o desenho do usuário. O perfil do canal Sonni Viaja tem o dele.

### 2.9 Antes de entregar

- Todos os cortes preservam palavras (auditoria por `words[]`) e o áudio está sincronizado com o vídeo.
- Ortografia de todos os letreiros conferida.
- Todos os valores e conversões conferidos por ferramenta, com a cotação do dia.
- Varredura da seção 2.5.4 concluída; cada nome e tradução com fonte registrada.
- Letreiro que cubra rosto, objeto apontado ou comprovante: reposicionado.
- Sonoridade e pico do arquivo final medidos e dentro do alvo.
- Resolução e taxa de quadros do final iguais às da fonte.
- Lista de pendências entregue ao usuário: valores sem comprovante, leituras de confiança média, mídias citadas e ausentes, falas que ficaram sem correção por falta de fonte, cópias H.264 criadas e onde estão.

## 3. Estilo: decisões do briefing

O estilo é decisão de cada vídeo. O agente confirma com o usuário só o que ainda não estiver definido e propõe, para o resto, o padrão da tabela. Registrar o que foi decidido no resumo antes de criar o plano.

| Decisão | Padrão se o usuário não disser | Como o editor suporta |
|---|---|---|
| Cortes | Jump cuts pela regra de pausas (seção 2.3) | Segmentos da mesma fonte |
| Transições | Nenhuma, corte seco | Não há transições no schema |
| Cobertura de imagem (b-roll) | Nenhuma | Segmento de outra fonte, ou camada de vídeo sem áudio |
| Proporção de saída | `16:9` | `profile.aspect_ratio` `16:9`, `9:16`, `1:1` ou `4:5`, uma por plano (seção 1.4) |
| Enquadramento, zoom e movimento | Nenhum (`crop` central se o formato exigir recorte) | `video.reframe` (foco ou keyframes, `ease` ou `linear`, zoom de 1,0 a 4,0, só com `crop`) ou `video.motion` (interpolação linear, zoom de 0,5 a 4,0); o mesmo segmento não aceita os dois |
| Cortes em J e L | Nenhum, áudio sempre junto da imagem | `cuts[]`, até 30 s |
| Música e efeitos | Nenhum | Fonte `kind: audio` com `mode: mix` |
| Texto na tela | Só informativo (seção 2.5); fonte e posição escolhidas com o usuário | Camada `text`, com `shadow` e `outline` opcionais (seção 1.4), sem caixa de fundo |
| Abertura | Cena copiada do próprio vídeo (seção 2.4) | Segmentos |
| Fecho | Término natural; convite e tela final só se pedir | Camadas de imagem e de vídeo |
| Idioma dos textos | O do público | |
| Alvo de áudio | -17 LUFS, pico abaixo de -1 dBFS | `audio_processing` |

- **Vídeo do canal Sonni Viaja:** usar o perfil [canal-sonni-viaja.md](canal-sonni-viaja.md), que fixa o estilo (cronologia vivida, corte seco, letreiros, QR code e tela final com o teaser do próximo episódio).
- **Vídeo de outra pessoa ou de outro gênero:** montar o perfil a partir do briefing, com perguntas objetivas e em linguagem editorial, sem exigir JSON. Não impor arquivos, fontes, cores ou fechos do canal.

## 4. Perfil de entrega

Para um vídeo horizontal para o YouTube, o ponto de partida é MP4, vídeo H.264, áudio AAC em 48 kHz, proporção 16:9 e a mesma resolução e taxa de quadros da gravação. Evitar barras pretas incorporadas quando for possível preservar ou recortar a imagem corretamente. Essas escolhas seguem as [recomendações oficiais de codificação do YouTube](https://support.google.com/youtube/answer/1722171?hl=pt-BR) e as [orientações oficiais de proporção e resolução](https://support.google.com/youtube/answer/6375112?co=GENIE.Platform%3DDesktop&hl=en), mas ainda é necessário conferir as exigências específicas da publicação.

Para Shorts, Reels e TikTok o ponto de partida é o mesmo formato (MP4, H.264, AAC em 48 kHz) em 9:16, 1080x1920, e para feed em 1:1 (1080x1080) ou 4:5 (1080x1350); conferir as exigências e a duração máxima da plataforma na hora de publicar.

No editor, o perfil escolhe a proporção (seção 1.4): no `16:9` a full segue `max_full_width`, e nas demais proporções ela sai no tamanho fixo de plataforma. A full usa H.264 com preset `veryfast`, ou NVENC `p3` com `cq 31` quando se passa `--accel gpu`, que usa decode CUDA e alivia a CPU mas não é mais rápido. **Esses encoders não têm perfil de qualidade configurável e podem entregar bitrate bem abaixo do ideal para filmagem de câmera em 2,7K ou 4K.** Depois da full, medir o bitrate com `ffprobe` e avisar o usuário se ficar muito abaixo do que a fonte tinha.

A preview (até 720 px, na mesma proporção da full) é a referência de enquadramento, ritmo, cortes e proporção do texto (seção 1.4); nitidez e bitrate só valem na full. O arquivo final só é entregue depois da validação e da aprovação explícita.

## 5. Comandos por finalidade

| Objetivo | Comando |
|---|---|
| Verificar o ambiente local | `editor capabilities --json` |
| Saber o que foi falado e quando | `editor analyze transcribe ... --json` |
| Encontrar níveis e possíveis silêncios | `editor analyze audio ... --json` |
| Conferir a imagem em momentos específicos | `editor extract frames ... --json` |
| Conferir se o plano é válido | `editor plan validate ... --json` |
| Preparar proxies leves para a preview | `editor proxy build ... --json` |
| Gerar uma versão para revisão | `editor render preview ... --json` |
| Registrar a aprovação humana | `editor approve ... --json` |
| Gerar a versão final | `editor render full ... --json` |

Os comandos completos devem ser executados com o Python do ambiente virtual, como nos exemplos desta referência. Os três pontos na tabela representam os argumentos obrigatórios, especialmente `--workdir`, `--source-id` e `--input` nas análises.

O agente não precisa pedir ao usuário que escreva comandos ou JSON. Ele deve traduzir o pedido para o plano, executar as análises, mostrar a preview e perguntar em linguagem comum se a edição está aprovada.
