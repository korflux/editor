# Perfil de estilo do canal Sonni Viaja

Use este perfil quando o vídeo for do canal Sonni Viaja (de Marco Kormoczi) ou quando o usuário pedir "o padrão do canal". Ele define o **estilo**: vlog de viagem em primeira pessoa, cronologia vivida, corte seco, fala como eixo e texto como camada de informação. O **padrão de qualidade** (processo, cortes limpos, letreiros com fonte, áudio, auditoria) vale para qualquer vídeo e está em [youtube-editing.md](youtube-editing.md), que deve ser lido antes.

O perfil nasceu da análise do `S04E31` final (feita sobre o projeto do Premiere, não só sobre o vídeo), das revisões do Marco no S04E33 e da montagem do S04E34-35, que fundiu dois episódios. As fontes completas, com números e exemplos, são:

- `A:\Youtube\padrao-de-montagem.md`: filosofia da montagem;
- `A:\Youtube\S04E33\padrao-de-edicao-S04E31.md`: parte operacional, letreiros e registro de revisões;
- `A:\Youtube\S04E34-35\montagem\LEIA-ME.md`: exemplo de montagem completa, com decisões e pendências.

Os números abaixo (cortes por minuto, durações, níveis) descrevem episódios já feitos, não são metas. O briefing do vídeo prevalece sobre o perfil.

## 1. Arquivos e preparos fixos do canal

- `A:\Youtube\QRtiger.png`: QR code de inscrição, sempre o mesmo arquivo.
- `A:\Youtube\Ending.mp4`: modelo da tela final, sempre o mesmo. Só o teaser que roda nele muda, e é sempre do **próximo** episódio, escolhido vendo o material dele.
- Os arquivos `gptSxxEyy.docx` da pasta do episódio são pedidos de título e descrição, não de montagem, mas descrevem o que foi gravado.
- Prints, mapas mentais e comprovantes de custos que o apresentador cita são a fonte dos valores (não só a fala).
- Se a fala cita mídia que não está na pasta (foto de mapa, vídeo gravado por terceiros), listar como pendência.
- As câmeras do canal (DJI) gravam HEVC de 10 bits em MP4, com trilhas de dados e miniatura: o editor lê esses brutos direto, sem conversão, e entrega em 8 bits `yuv420p` (os brutos são Rec.709, sem HDR). Só o que ele recusa (por exemplo gravação de tela em `.mkv`) passa por [source-preparation.md](source-preparation.md).

## 2. O estilo em uma página

1. **Cronologia vivida.** Os brutos entram na ordem em que foram gravados. As únicas inversões são o gancho de abertura e o fecho (bloco de custos ou despedida), quando a conclusão é gravada fora de ordem.
2. **Gancho copiado de dentro do episódio.** Uma cena de reação espontânea que aparece de novo, no mesmo trecho, no ponto cronológico dela. Sem letreiro de preço ou de nome, sem vinheta, sem título.
3. **Ritmo duplo.** Muitos cortes curtos para tirar respiros e poucos planos longos para demonstrar e dialogar. No E31: 278 cortes, mediana de 2,57 s, 58,6% deles com até 3 s, e ainda assim 18 planos acima de 12 s ocupando 32,4% do tempo.
4. **Jump cut é a ferramenta principal.** Quase todas as junções são cortes dentro do mesmo bruto, e em mais da metade o que sai é só silêncio.
5. **Corte seco e nada mais.** Sem transição, sem mudança de velocidade, sem zoom, sem correção de cor, sem música, sem efeito sonoro, sem cobertura de imagem (b-roll). Todos os pontos de corte caem na grade de quadros.
6. **Imagem e som sempre juntos.** Nenhum corte em J ou L: o áudio de cada clipe começa e termina com o vídeo dele.
7. **Um único tratamento de áudio** para o episódio inteiro, sem ajuste de volume por clipe.
8. **O texto carrega a informação.** Um só estilo (Tahoma branca com sombra suave), em posições fixas por função, sem animação. A fala do apresentador em português nunca é legendada.
9. **A posição do texto diz quem fala.** Interlocutor em outro idioma embaixo à esquerda; o grupo do apresentador no topo.
10. **O letreiro entra quando o assunto é dito, não quando o plano corta.**
11. **Fecho em três partes.** Convite falado, QR code na hora da frase e tela final de 12 s com o teaser do próximo episódio.
12. **Ordem de trabalho.** Cortes cronológicos, enxugamento, áudio, letreiros, QR code e, por último, o gancho e a tela final.

## 3. Identidade e estrutura

O apresentador conduz a história enquanto chega, se desloca, descobre o lugar, testa serviços e avalia custos. A câmera acompanha o que ele vê ou aponta. O interesse vem da situação real e das reações, não de uma sucessão de efeitos.

- **Espontaneidade preservada.** Dúvidas, brincadeiras, hesitações breves e reações (altura, atendimento, comida) ficam quando fazem parte da experiência.
- **Informação mostrada no local.** Mapa físico, placa, quarto, buffet e prato aparecem na imagem enquanto são comentados. Não substituir tudo por gráficos.
- **Estrutura de episódio.** Abertura direta no local, sem vinheta nem logo (o primeiro letreiro, o título do lugar, entra pouco depois do gancho), percurso na ordem vivida, descobertas, avaliação e fecho. Blocos de deslocamento têm mais cortes; mapas, interações, comida e a explicação final podem ficar em planos longos.
- **Fusão de episódios.** Ordem cronológica de um episódio para o outro, com o gancho copiado de dentro do vídeo e a despedida do último bruto por último. Se o bruto da despedida tiver fala anterior a ela, divide-se o bruto: a fala anterior fica no lugar cronológico e a despedida vai por último, depois de qualquer cena gravada depois dela.
- **Visitas parecidas** (salões de assembleia, museus, templos) pedem corte mais agressivo que o ritmo do resto, para não repetir a estrutura.

## 4. Cortes do canal

A regra de pausas do padrão de qualidade (seção 2.3 de [youtube-editing.md](youtube-editing.md)) foi calibrada neste canal, e vale com os parâmetros padrão. Medidas de referência, que não são metas:

| Episódio | Cortes por minuto | Mediana do plano | Brutos aproveitados |
|---|---|---|---|
| E31 (Premiere, edição do Marco) | 13,8 | 2,57 s | 62,9% |
| E33 (primeira passada) | 12,7 | 3,44 s | 66,5% |
| E34-35 (primeira passada, dois episódios) | 14,0 | 3,30 s | 64,1% |

No E31, o que sai entre dois cortes do mesmo bruto é só silêncio ou respiro em 60,6% das junções, até três palavras de conteúdo (falso início, hesitação) em 24,5% e mais de três palavras em só 2,4%. A entrada do clipe fica cerca de 50 ms antes da primeira palavra e a saída cerca de 230 ms depois da última. A primeira passada automática tem planos mais longos que os do Marco, então espere lapidação.

## 5. Gancho do canal

- Copiado de dentro do próprio episódio, decidido **depois de o corpo estar cortado**.
- Começa na frase mais forte, sem a preparação, e é de um assunto só. Revisão do E33: "começa em 'A Gabi já pegou o caldo de cana...', tira a parte do picante".
- Duração é consequência da cena: 17,15 s no E31, 6,3 s no E33, 13,5 s no E34-35.
- Critério que funcionou: reação espontânea e curiosa, fora do tema central de preço (macarrão, caldo de cana, encontro inesperado).
- **Não é gancho em texto.** O canal não usa frase de abertura na tela. Só propor texto de abertura se o usuário pedir.

## 6. Letreiros do canal

A política de texto do padrão de qualidade (seção 2.5 de [youtube-editing.md](youtube-editing.md)) vale com as regras abaixo. Idioma do texto: português.

### 6.1 Tipos, posições e estilo

Posições relativas ao quadro (0 a 1, origem no canto superior esquerdo). Os tamanhos são para a full de 2688 × 1512; se `profile.max_full_width` for outro, escalar `size_px` na mesma proporção (cerca de 3,7% da largura para o texto padrão).

| Tipo | Posição (x; y) | Aparência | `size_px` | Duração típica |
|---|---|---|---|---|
| Rótulo central inferior | 0,50; 0,83 | Branco, centralizado | 100 | cerca de 2 a 4 s |
| Fala de interlocutor (outro idioma) | 0,05; 0,88 | Branco, alinhado à esquerda | 100 | segue a fala (0,9 a 4,4 s) |
| Fala do grupo (apresentador e acompanhante) | 0,44 a 0,86; 0,12 a 0,14 | Branco, no topo, perto de quem fala | 100 | segue a fala |
| Cartão de preço ou de contexto | 0,03; 0,08 | Branco, canto superior esquerdo, duas linhas | 55 | 4 a 8 s, para dar tempo de ler |
| Título do lugar | 0,50; 0,81 e 0,88 | Primeira linha **vermelha** (RGB 232, 44, 66), segunda branca menor | 100 e 65 | cerca de 3,8 s |
| Lista acumulada de nomes | 0,97; de 0,10 a 0,56 em passos de 0,077 | Branco, alinhado à direita, um nome por faixa, todos saem juntos | 100 | até o corte seguinte |
| Palavra de ênfase | 0,50; 0,83 | Branco, bem maior | cerca de 160 | 0,8 s |
| Caixa de destaque | sobre o objeto apontado | Retângulo vermelho fino, sem preenchimento | | cerca de 2 s |

- **Fonte e cor:** Tahoma em todos os textos; branco, exceto o título (vermelho).
- **Sombra:** projetada suave, deslocada para baixo e para a direita, sem contorno, sem caixa de fundo e sem barra. No S04E34-35 a sombra ficou com o dobro da opacidade da sombra simples, para ler sobre fundo claro. No editor é `layers[].shadow` (campos e faixas em `SKILL.md`, seção 5), sem `outline`, como o canal faz; a caixa de fundo o editor não tem. Valores de partida **sugeridos** para a full de 2688 de largura, a ajustar olhando a preview, não uma medida oficial do canal: `offset_x_px` 4, `offset_y_px` 4, `blur_px` 6, `color` `#000000` e `opacity` 0.5 (1.0 em fundo claro). Em outra largura de full, escalar os pixels na mesma proporção.
- **Sem animação:** nenhum parâmetro de texto muda no tempo e os letreiros entram e saem de uma vez.
- **Caixa no editor:** como a caixa da camada de texto é alinhada pelo topo, para centralizar um rótulo em y 0,83 usar `y` cerca de 0,80 (uma linha) ou 0,76 (duas linhas), com `x 0.04` e `width 0.92`. Valores medidos num teste a 2688 px; conferir na full.
- **Safe area:** rótulos na faixa inferior (y de 0,80 a 0,91); o letreiro não cobre rosto, objeto apontado, comprovante ou prato.

### 6.2 Convenções de escrita

| Convenção | Exemplos | Quando usar |
|---|---|---|
| Nome original · tradução (separador "·") | "Land of the Sun · Reino do Sol", "Chợ Hàn · Mercado Han" | Nome em outro idioma que o público precisa entender |
| Termo = tradução | "Maple syrup = xarope de bordo", "Cà phê trứng = café com ovo" | Item de comida ou termo local |
| Descrição direta | "Mapa dos prédios do Mercure", "Nosso quarto: Hôtel de Paris" | Objeto na mão ou lugar sem nome oficial relevante |
| Valor em moeda local ≈ reais | "490.000 VND ≈ R$ 96" | Qualquer preço em moeda estrangeira. "Cerca de" quando arredondado |
| Base do preço | "R$ 801,79 no total, com impostos", "1 noite · 2 adultos · 7 a 8/4/2026" | Tarifas: sempre impostos, café e datas |
| Cálculo exposto | "R$ 558,88 + R$ 74,89 em impostos = R$ 633,77" | Quando o total é soma de partes |
| Correção explícita | "Correção: R$ 438,59 no total" | Valor ou nome falado que difere do comprovado |
| Dúvida assumida | "(não faço ideia)", "(ainda não entendi)" | Nome ou fala sem como ser pesquisado, entre parênteses, tom leve |
| Texto estrangeiro que ele não entende | "Năm mươi nghìn đồng = cinquenta mil dongs" | Quando ele lê e diz não saber; a tradução é pesquisada |
| Identificação do que ele chuta | "Verso da nota: Phú Văn Lâu · pavilhão de Huế" | Quando ele diz "parece ser..." de algo verificável |
| Estabelecimento: nome oficial · tipo | "La Rive Gauche · restaurante com buffet" | Todo restaurante, bar, loja, lounge nomeado |
| Fala em outro idioma | "Sim!!", "Olá, nós temos uma pequena surpresa para vocês!" | Mantém a pontuação e as ênfases originais, incluindo trocas curtas de cortesia |

Cotação: a do dia de cada gravação (Wise, taxa de mercado do dia, ou a fonte usada no episódio anterior), em reais inteiros ("cerca de"). O exchange-rates.org bloqueia acesso automatizado.

**Valor falado diferente do print ou do mapa mental do apresentador: vale o print.** O cartão "Correção: pelo Klook, R$ 34 para 2 (R$ 17 por pessoa)" entra no instante em que ele fala o valor.

## 7. Áudio do canal

- Origem: o som da câmera. Sem trilha musical, sem efeito sonoro, sem narração gravada à parte.
- O Premiere usava o Melhorar Fala do Essential Sound (nivela e reduz ruído), que não existe no editor. No editor o equivalente é `audio_processing` com `normalize_loudness`, que nivela mas **não remove ruído de fundo**.
- Alvo do canal: cerca de **-17 LUFS** integrados, LRA de 7 a 8 LU (aceitável até 9 em material muito variado) e pico real **abaixo de -1 dBFS**. No plano: `target_lufs: -17` e `limiter.ceiling_dbfs: -2`. Medidas dos finais: E31 -17,4 / 7,0 / -0,9; E33 -17,2 / 7,0 / -1,4; E34-35 -17,2 / 9,2 / -1,0.
- Os brutos do canal são fracos e irregulares (E33: -24,2 LUFS e LRA 13,3; E34-35: -20,6 e 15,8), então o nivelamento é necessário.
- A sonoridade do gancho deve ser comparada com a do bloco seguinte, para evitar saltos como o de cerca de 7 LU do E31.

## 8. Imagem e câmera

- Três tipos de plano se alternam: apresentador para a câmera (contexto, opinião, balanço final), ponto de vista do visitante (paisagem, rua, restaurantes, sinalização) e detalhe demonstrativo (mapa, placa, prato, item do quarto).
- Câmera na mão, sem correção: tremidas, movimentos de procura e variações de exposição ficam como gravados.
- Sem ajuste de imagem nos clipes de câmera: nada de escala, recorte, zoom, velocidade ou cor. Só o QR code e a tela final recebem escala. No editor, `fit: preserve` nos clipes, sem `video.motion` e sem `video.reframe` (os dois aplicam zoom e recorte).
- A acompanhante e a equipe aparecem nas reações e falas; o plano não as esconde.

## 9. Fecho

1. **Avaliação falada** em plano frontal contínuo, com jump cuts só para tirar pausas.
2. **Convite falado:** "se você gostou do vídeo, deixa o like, comenta aqui embaixo. Se inscreve pelo QR Code e até o próximo vídeo. Tchau!".
3. **QR code** (`A:\Youtube\QRtiger.png`) na hora da frase "pelo QR Code", como camada de imagem. Dura de 4 s a 8,5 s, o suficiente para a explicação falada.
4. **Tela final de 12 s**, com o modelo `Ending.mp4` (fundo subaquático, mascote de tuk-tuk, "Inscreva-se"), com três espaços: círculo de inscrição, caixa azul "Vídeo anterior" (vazia) e caixa preta "Próximo vídeo", onde rodam três cenas de teaser do **próximo** episódio, a última com a legenda do lugar. O áudio é a fala original das três cenas (de 3 a 5 s cada), não o áudio do `Ending.mp4`.
   - Composição **fora do editor**: o espaço "Próximo vídeo" é um retângulo azul sólido (RGB 36, 99, 224; cerca de 614 × 342 px em 1010, 544 num quadro de 1920 × 1080) removido por chroma key, deixando o teaser por trás. O script `A:\Youtube\S04E33\montagem\ferramentas\montar_ending.py` faz isso a partir de um `teaser.json` com as cenas.
   - O arquivo pronto (`Ending-com-teaser-<episodio>.mp4`, 1920 × 1080 a 60 quadros/s) entra no plano como o último segmento, com `fit: crop` (ampliação de 140%).
5. A tela final mantém o áudio próprio, sem o tratamento das outras cenas.

## 10. Exemplo de plano no estilo do canal

O exemplo segue o estilo do canal: gancho copiado do próprio episódio, jump cut dentro do mesmo bruto, rótulo em Tahoma, QR code como camada de imagem, tela final como último segmento e áudio nivelado. Os tempos são ilustrativos. O JSON foi validado com `plan validate` e renderizado com preview e full.

```json
{
  "schema_version": 2,
  "profile": {"aspect_ratio": "16:9", "max_full_width": 2688},
  "sources": [
    {"id": "b0376", "kind": "video", "path": "sources/b0376.mp4"},
    {"id": "b0341", "kind": "video", "path": "sources/b0341.mp4"},
    {"id": "final", "kind": "video", "path": "sources/Ending-com-teaser.mp4"},
    {"id": "qr", "kind": "image", "path": "sources/QRtiger.png"}
  ],
  "segments": [
    {
      "video": {"source_id": "b0376", "in_s": 0.0, "out_s": 6.2, "fit": "preserve"},
      "audio": {"mode": "keep", "tracks": [{"source_id": "b0376", "in_s": 0.0, "out_s": 6.2, "gain_db": 0, "loop": false}]}
    },
    {
      "video": {"source_id": "b0341", "in_s": 0.0, "out_s": 6.3, "fit": "preserve"},
      "audio": {"mode": "keep", "tracks": [{"source_id": "b0341", "in_s": 0.0, "out_s": 6.3, "gain_db": 0, "loop": false}]}
    },
    {
      "video": {"source_id": "b0341", "in_s": 7.2, "out_s": 11.0, "fit": "preserve"},
      "audio": {"mode": "keep", "tracks": [{"source_id": "b0341", "in_s": 7.2, "out_s": 11.0, "gain_db": 0, "loop": false}]}
    },
    {
      "video": {"source_id": "final", "in_s": 0.0, "out_s": 12.0, "fit": "crop"},
      "audio": {"mode": "keep", "tracks": [{"source_id": "final", "in_s": 0.0, "out_s": 12.0, "gain_db": 0, "loop": false}]}
    }
  ],
  "cuts": [],
  "layers": [
    {
      "type": "text",
      "start_s": 6.5,
      "end_s": 10.3,
      "x": 0.04,
      "y": 0.76,
      "width": 0.92,
      "height": 0.14,
      "text": "Chợ Hàn · Mercado Han",
      "font": "Tahoma",
      "size_px": 100,
      "color": "#FFFFFF",
      "align": "center",
      "wrap": true
    },
    {
      "type": "image",
      "start_s": 12.4,
      "end_s": 16.3,
      "x": 0.667,
      "y": 0.039,
      "width": 0.333,
      "height": 0.592,
      "source_id": "qr",
      "fit": "contain"
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

Equivalências do estilo para o plano:

- **QR code:** `x 0.667`, `y 0.039`, `width 0.333`, `height 0.592`, `fit: contain`. Equivale a escala de 179% e posição 0,84 e 0,34 do Premiere.
- **Tela final:** 1920 × 1080 a 60 quadros/s como último segmento, `fit: crop`, áudio do próprio arquivo.
- **Resolução:** `max_full_width: 2688` mantém a largura dos brutos; sem isso a full sai em 1920.
- **Cortes:** cada jump cut é um segmento novo da mesma fonte, com `in_s` e `out_s` na grade de quadros (múltiplos de 1001/30000 s a 29,97 quadros/s). `cuts[]` fica vazio.

## 11. Padrão do canal ou particularidade do episódio

- **Repete-se em todos os episódios analisados:** A-roll com áudio no mesmo clipe, preset único de voz e nenhuma trilha de música contínua.
- **Repete-se nos três episódios mais recentes analisados (E28, E31 e E32):** Tahoma e ausência de transições. Os episódios E25 a E27 usavam Cross Dissolve.
- **É particularidade de cada episódio:** a densidade de texto (E31 com 55 letreiros por causa de diálogo em inglês, mapas e preços; E34-35 com 36 e quase nenhuma fala em outro idioma), a duração do gancho e as durações de cada bloco.
- **Não é regra permanente** a ausência de gancho, QR code ou tela final num arquivo analisado. Esses elementos fazem parte do estilo atual e entram em todo episódio.

## 12. Registro das revisões do Marco

Cada ajuste pedido numa revisão virou uma regra para os próximos episódios.

| Data | O que o Marco pediu ou confirmou | Regra para replicar |
|---|---|---|
| 03/10/2026 | O QR code é sempre `QRtiger.png` e o modelo da tela final é sempre `Ending.mp4`. | Arquivos fixos do canal; só o teaser muda. |
| 03/10/2026 | O teaser da tela final é sempre do próximo episódio, escolhido vendo o material dele. | Olhar o próximo episódio antes de montar a tela final. |
| 03/10/2026 | O gancho se decide editando, depois do corpo cortado. | Gancho por último. |
| 03/10/2026 | Gancho do E33: "começa em 'A Gabi...', tira a parte do picante". | Um assunto só; entrar direto na frase de impacto. |
| 03/10/2026 | "Senti falta de colocar alguns textos na tela, falando o nome do local, nome do restaurante." | Todo lugar e estabelecimento nomeado ganha letreiro com o nome oficial. |
| 03/10/2026 | "Ele fala que não sabe o que está escrito na nota, devia procurar a tradução e acrescentar." | Texto que ele não entende ou chuta: pesquisar e acrescentar tradução ou identificação. |
| 03/10/2026 | Um garçom diz "Está tudo gostoso?" e a Gabi responde, e não havia legenda. | Legendar toda troca em outro idioma, mesmo curta; varrer com transcrição em inglês. |
| 03/10/2026 | "A construção está muito bem feita, parabéns." | Mantidos: cronologia com dois blocos fora de ordem justificados, regra de pausas, tela final com chroma key. |
| 05/10/2026 | "Segue o print." (preço falado diferente do mapa mental) | Vale o print; cartão "Correção: ..." na hora da fala. |
| 05/10/2026 | "Boa, deixa a correção." (Michelin: estrela contra Bib Gourmand) | Fato dito errado, com evidência no quadro e fonte oficial, entra como "Correção: ...". |
| 05/10/2026 | "Retira o nome da feira." (identificada só por indícios) | Nome de lugar sem placa legível ou fonte direta não vai para a tela. |
| 05/10/2026 | "Deixa como você deixou." (leituras de confiança média) | Leitura de pouco risco, com fonte, pode ficar na primeira passada, listada nas pendências. |

## 13. Entrega do canal

| Item | Valor |
|---|---|
| Contêiner e vídeo | MP4, H.264 Main, **2688 × 1512**, **29,97 quadros/s**, yuv420p, Rec.709, cerca de 38 a 43 Mb/s |
| Áudio | AAC-LC, 48 kHz, estéreo, cerca de 317 a 320 kb/s |
| Brutos | DJI, HEVC 10 bits, Rec.709 (sem perfil log), 2688 × 1512, 29,97 quadros/s, áudio AAC estéreo |
| Tela final | `Ending-com-teaser-<episodio>.mp4`, 12,00 s, 1920 × 1080 a 60 quadros/s, ampliado para 141% |
