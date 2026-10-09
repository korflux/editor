# Spec: proporções de saída e reenquadramento por keyframes
# Alvo: phase-5-proporcoes-e-reenquadramento
# Status: aprovado

## Objetivo

O operador (agente ou humano) monta, a partir de gravações 16:9, vídeos 9:16, 1:1 e 4:5 para Shorts, Reels e feed, além do 16:9 atual, com o sujeito enquadrado. O enquadramento pode ser estático ou deslizar suavemente entre pontos marcados no tempo, como keyframes de Posição e Escala no Premiere, sem corte. Sucesso é um plano vertical sobre fonte horizontal produzir preview e full na proporção e resolução declaradas, com o enquadramento seguindo os keyframes, a preview representando a full em proporção e tamanho de texto, e aprovação por hash, janelas de decode e proxies funcionando como hoje.

## Cobertura da origem

| Origem | Destino na spec | Motivo se fora/N/A |
|---|---|---|
| Proporções 9:16, 1:1, 4:5 e 16:9, uma por plano (Trilha 1) | F1, F2, decisões 1 a 3 | N/A |
| Foco estático no `crop` (Trilha 2) | F1, decisões 4 e 5 | N/A |
| Keyframes com ease in/out e `linear` (Trilha 2 e 3) | F1, F2, decisões 4 a 8 | N/A |
| Resolução de plataforma com ampliação e aviso (Trilha 4) | F1, F2, decisões 2, 3 e 9 | N/A |
| Keyframes em todas as proporções e convivência com `motion` (Trilha 5) | F1, decisão 10 | N/A |
| Escala do `size_px` entre preview e full (Trilha 6) | F2, decisão 11 | Fecha o Problema 1 de `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md` |
| Aprovação por hash, janelas de decode e proxies | F2, decisão 13 | Comportamento vigente preservado |
| `capabilities`, skill `vibe-editor` e `REGRAS.md` | F3 | N/A |
| Sombra e contorno de texto, zonas seguras no código, tracking, várias proporções por plano, fundo desfocado, keyframes em camadas, transições | Fora | Decididos fora na interview |

## Suposições e decisões

1. `profile.aspect_ratio` aceita exatamente `"16:9"`, `"9:16"`, `"1:1"` e `"4:5"`. Qualquer outro valor é `PLAN_INVALID`. Um plano tem uma única proporção e gera uma preview, uma aprovação e uma full, como hoje. (produto; já fechado)
2. Resolução da full: `16:9` mantém a regra vigente (largura da primeira fonte de vídeo limitada por `max_full_width`, sem upscale, altura 16:9). As demais saem em tamanho fixo de plataforma: `9:16` em 1080x1920, `1:1` em 1080x1080 e `4:5` em 1080x1350, independentemente da resolução das fontes. `max_full_width` só é aceito com `16:9`; declarado com outra proporção é `PLAN_INVALID`, para não haver dois tetos com significados diferentes. (produto; já fechado)
3. Resolução da preview: mesma proporção da full, com o lado maior em até 720 pixels e ambas as dimensões pares. O 16:9 mantém a regra vigente; `9:16` sai em 404x720, `1:1` em 720x720 e `4:5` em 576x720. (produto; já fechado)
4. O reenquadramento é um campo opcional `segments[].video.reframe`, aceito somente com `fit: "crop"`. Ele contém `keyframes`, uma lista de 1 a 32 itens, cada um com `t_s` (segundos relativos ao início do segmento), `x` e `y` (0 a 1) e, opcionalmente, `zoom` (1.0 a 4.0, padrão 1.0) e `interpolation` (`"ease"` ou `"linear"`, padrão `"ease"`). Um único keyframe é o foco estático. Sem `reframe`, o `crop` continua central, idêntico a hoje. (produto; já fechado)
5. Semântica: o quadro de saída é um recorte da fonte original na proporção do canvas. Com `zoom` 1.0, é o maior recorte dessa proporção que cabe na fonte; com `zoom` 2.0, metade da largura e da altura desse recorte. `x` e `y` indicam o ponto da fonte, normalizado sobre a largura e a altura originais, que fica no centro do recorte. Quando esse centro levaria o recorte para fora da fonte, o recorte é encostado na borda, nunca mostra preto e o ponto deixa de ficar centralizado. Assim, o agente marca a posição do sujeito lida nos frames extraídos, sem calcular deslocamentos. (produto; já fechado)
6. Tempo: `t_s` deve ser estritamente crescente, de 0 até a duração do segmento (`out_s - in_s`). Antes do primeiro keyframe vale o estado do primeiro e depois do último vale o estado do último. Os valores são arredondados como os do `motion` atual, e o plano canônico e o hash incluem o `reframe`. (produto; já fechado)
7. Interpolação: o campo `interpolation` de um keyframe rege o trecho até o keyframe seguinte. `"linear"` interpola `x`, `y` e `zoom` em velocidade constante. `"ease"` usa a curva cúbica suave `3p² - 2p³` (p é o progresso de 0 a 1 no trecho), que parte e chega com velocidade zero. O `interpolation` do último keyframe não tem efeito. (produto; já fechado)
8. Teto de 32 keyframes por segmento e zoom até 4.0, em linha com o teto do `motion`. Acima disso é `PLAN_INVALID`. `ponytail: 32 keyframes por segmento, ampliar quando um acompanhamento real precisar de marcação mais densa`. (segurança e operação; já fechado)
9. Ampliação: para cada segmento com vídeo, o fator de ampliação é a razão entre a largura do canvas da full e a largura do menor recorte usado na fonte (o recorte do maior `zoom` entre os keyframes, ou o recorte central quando não há `reframe`). Um fator acima de 1.0 não bloqueia: `plan validate` lista o segmento em `data.profile.upscale` com o índice e o fator arredondado. Lista vazia significa que nenhum segmento é ampliado. No 16:9 a regra vigente impede upscale do canvas, mas o `zoom` ainda pode ampliar o recorte, e isso também é informado. (produto; já fechado)
10. Convivência com `motion`: `reframe` funciona em todas as proporções, inclusive para punch-in em 16:9. `motion` continua aceito exatamente como hoje, aplicado depois do enquadramento, em qualquer proporção. Um segmento com `motion` e `reframe` ao mesmo tempo é `PLAN_INVALID`. (produto; já fechado)
11. Texto: `size_px` passa a ser definido na resolução da full. A full renderiza com o valor declarado e a preview com `size_px × largura da preview / largura da full`, arredondado e com mínimo de 1. O mesmo fator vale para a quebra de linha (`wrap`). A full de planos existentes não muda; a preview desses planos passa a mostrar o texto na mesma proporção da full. (produto; já fechado)
12. A regra de `fit: "preserve"` passa a comparar a proporção da fonte com a proporção do perfil, e não mais com 16:9. A mensagem de erro cita a proporção do perfil. A checagem do MP4 renderizado passa a exigir a proporção e as dimensões do perfil. (produto; já fechado)
13. Janelas de decode e proxies não mudam: o `reframe` usa coordenadas normalizadas, que valem igualmente para o original e para o proxy reduzido, e não altera os intervalos lidos de cada fonte. A aprovação continua vinculando plano canônico, preview e originais por SHA-256. (produto e segurança; já fechado)
14. A entrega é minor: o schema continua `schema_version: 2`, só ganha valores e campos opcionais, e todo plano v2 válido hoje continua válido e gera a mesma full. A única mudança observável em planos existentes é o tamanho do texto na preview (decisão 11), que é correção de defeito registrado. (processo; já fechado)

## Escopo e comportamento

### 1. Fluxo F1: validar um plano com proporção e reenquadramento

- Jornada: N/A, CLI sem UI; etapa "escrever e validar `plan.json`" da jornada vigente da skill `vibe-editor`.
- Rota: `editor plan validate --workdir <caminho-absoluto> --json`.
- Gatilho: o operador valida o plano antes de renderizar.
- Pré-condição: trabalho iniciado e fontes locais declaradas (inalterado).
- Superfície por passo: N/A, sem UI; a resposta é o envelope JSON em `stdout` e o diagnóstico em `stderr`.
- Passos:
  1. Valida a estrutura do `profile` e de `segments[].video.reframe` pela allowlist -> campo desconhecido, tipo errado ou valor fora da faixa é recusado antes de inspecionar mídia.
  2. Valida as fontes e os segmentos -> regras de `fit`, `reframe` e `motion` pelas decisões 4 a 6, 10 e 12.
  3. Calcula o perfil efetivo -> `data.profile` traz `aspect_ratio`, as dimensões da full (`full_width`, `full_height`), `max_full_width` e `effective_full_width` apenas no 16:9, e `upscale` (decisão 9).
  4. Responde -> envelope de sucesso com timeline, `reframe` normalizado em cada segmento que o declara, JSON canônico e SHA-256.
- Validações: `aspect_ratio` na allowlist; `max_full_width` só com 16:9; `reframe` só com `fit: "crop"` e sem `motion`; `keyframes` com 1 a 32 itens; `t_s` finito, estritamente crescente e dentro de `[0, out_s - in_s]`; `x` e `y` em `[0, 1]`; `zoom` em `[1.0, 4.0]`; `interpolation` em `{"ease", "linear"}`; nenhum campo fora da allowlist.
- Erros: `PLAN_INVALID` (mensagem segura que nomeia o campo e a regra, por exemplo "O perfil de saída deve usar aspect_ratio 16:9, 9:16, 1:1 ou 4:5.", "O reenquadramento exige fit crop.", "Um segmento não pode declarar motion e reframe ao mesmo tempo.", "Os keyframes devem ter t_s crescente dentro da duração do segmento.").
- Estados: sem `reframe` e com 16:9, o envelope é o atual, acrescido apenas de `full_width`, `full_height` e `upscale`.
- Aceite: A1, A2, A3.
- Reutilizar: `_validate_profile`, `_calculated_profile`, `_validate_motion` (padrão de normalização e arredondamento), `reject_unknown_fields`, `require_object` e `plan_error` em `src/editor/plan/`.

### 2. Fluxo F2: renderizar preview e full na proporção do perfil

- Jornada: N/A, CLI sem UI; etapas de preview, aprovação e full da skill `vibe-editor`.
- Rota: `editor render preview` e `editor render full` (e os atalhos `preview` e `full`), com `--accel` e `--proxy` inalterados.
- Gatilho: o operador renderiza.
- Pré-condição: plano válido; na full, aprovação vigente (inalterado).
- Superfície por passo: N/A, sem UI; envelope JSON em `stdout`.
- Passos:
  1. Determina o canvas pelas decisões 2 e 3 -> preview e full na proporção do perfil.
  2. Monta o grafo -> cada segmento com `crop` recorta a fonte conforme o `reframe` (ou o centro, sem `reframe`), quadro a quadro, pelas decisões 5 a 7; `contain` e `preserve` usam o canvas do perfil; `motion` é aplicado depois; o texto usa o `size_px` escalado (decisão 11); as camadas mantêm a geometria normalizada sobre o canvas.
  3. Renderiza, valida e publica -> o MP4 temporário só é publicado se for H.264/AAC com as dimensões e a proporção do perfil e a duração da timeline.
  4. Responde -> envelope atual, com as dimensões efetivas do vídeo.
- Validações: as mesmas da preview e da full vigentes, com a proporção do perfil no lugar de 16:9.
- Erros: `RENDER_FAILED` quando o MP4 não respeita o perfil ("A preview renderizada não respeita o perfil <proporção> de até 720 pixels." / "A full renderizada não respeita o perfil final <proporção>."); os demais erros e o fallback sem janelas são inalterados.
- Estados: plano 16:9 sem `reframe` gera a mesma full de hoje.
- Aceite: A4, A5, A6, A7.
- Reutilizar: `_preview_dimensions` e a checagem de metadados de `src/editor/render/preview.py`, `_video_segment_filter` e `_motion_filter_graph` de `src/editor/render/ffmpeg_graph.py`, janelas de `render/windows.py` e `lookup_proxy` de `render/proxy.py`.

### 3. Fluxo F3: expor e ensinar o contrato

- Jornada: N/A, CLI sem UI; etapa "verificar o ambiente" da skill.
- Rota: `editor capabilities --json` e a skill `skills/vibe-editor/`.
- Gatilho: o agente consulta capacidades ou carrega a skill.
- Pré-condição: nenhuma.
- Superfície por passo: N/A, sem UI; envelope JSON e documentos da skill.
- Passos:
  1. `capabilities` -> o perfil informa as quatro proporções aceitas e as dimensões de full e preview de cada uma; o plano informa `reframe` com os limites (32 keyframes, `zoom` de 1.0 a 4.0, interpolações `ease` e `linear`) e a exclusão mútua com `motion`.
  2. A skill -> orienta escolher a proporção, marcar keyframes a partir dos frames extraídos, preferir `reframe` a `motion` em trabalhos novos e respeitar as zonas seguras do Shorts e do Reels como orientação editorial.
- Validações: os valores de `capabilities` são lidos das mesmas constantes usadas pela validação.
- Erros: N/A, sem entrada nova.
- Estados: N/A.
- Aceite: A8.
- Reutilizar: estrutura atual de `src/editor/capabilities.py` e das referências da skill.

### Fora

- Várias proporções a partir do mesmo plano: quebraria o contrato de uma preview, uma aprovação e uma full; use um trabalho por proporção.
- Tracking automático de rosto ou sujeito: exige visão computacional e outro contrato; os keyframes são marcados pelo agente.
- Sombra e contorno de texto: Problema 2 do erro registrado, phase própria.
- Validação de zonas seguras no código: só orientação na skill.
- Fundo desfocado no `contain` e keyframes em camadas de imagem ou vídeo: não pedidos nesta entrega.
- Transições e curvas Bezier arbitrárias: a curva `ease` é fixa.
- Resoluções fixas acima de 1080 de largura nas proporções não 16:9.

## Checklist de entrega

### Aceite

- [x] A1: um plano com cada uma das quatro proporções valida e informa as dimensões da full e da preview; outra proporção, ou `max_full_width` fora do 16:9, é recusada com `PLAN_INVALID`.
- [x] A2: `reframe` válido é normalizado no envelope e entra no hash canônico; cada violação da decisão 4, 6, 8 ou 10 (sem `crop`, com `motion`, `t_s` fora de ordem ou da duração, faixa de `x`, `y` ou `zoom`, mais de 32 keyframes, campo desconhecido) é recusada com `PLAN_INVALID` e mensagem segura.
- [x] A3: `data.profile.upscale` lista os segmentos ampliados com o fator correto e fica vazio quando nenhum é ampliado.
- [x] A4: preview e full de um plano 9:16 sobre fonte 16:9 saem em 404x720 e 1080x1920; as de 1:1 e 4:5 saem nas dimensões da decisão 3 e 2.
- [x] A5: com keyframes, o recorte segue as posições declaradas nos instantes dos keyframes, desliza entre eles conforme `ease` ou `linear`, encosta na borda sem preto e mantém o estado fora do intervalo dos keyframes.
- [x] A6: um plano 16:9 existente, sem `reframe`, gera full idêntica à de antes da phase, e a preview mostra o texto na mesma proporção da full.
- [x] A7: aprovação, bloqueio da full por mutação, janelas de decode, fallback e preview com proxy funcionam num plano 9:16 com `reframe`.
- [x] A8: `capabilities` expõe proporções, dimensões e limites do `reframe`, e a skill documenta o fluxo vertical, os keyframes e as zonas seguras.

### Critérios de sucesso

- [x] C1: todas as provas de A1 a A8 passam junto com a suíte vigente, sem regressão.
- [x] C2: a posição do recorte nos instantes dos keyframes coincide com a esperada com tolerância de até 2 pixels na full, e nenhum quadro com `crop` contém barras pretas.
- [x] C3: nenhum plano v2 válido antes da phase passa a ser recusado (mudança minor).
- [x] C4: `REGRAS.md`, `capabilities` e a skill descrevem o mesmo contrato, com as limitações registradas como `ponytail`.

## Contratos e restrições necessárias

- Dados e invariantes: o plano canônico e o hash incluem `reframe` e `aspect_ratio`; tempos e coordenadas do plano nunca são alterados pelo render; a full nunca usa proxy; fontes originais nunca são modificadas.
- Integrações e interfaces: campos novos em `profile` (valores de `aspect_ratio`), `segments[].video.reframe`, `data.profile.full_width`, `data.profile.full_height` e `data.profile.upscale`; `capabilities` ganha as proporções e os limites do `reframe`. Nada é removido nem renomeado.
- Segurança e operação: o `plan.json` é entrada não confiável. Allowlist: `profile` com `aspect_ratio` (quatro valores) e `max_full_width` (só 16:9, regra vigente); `reframe` só com a chave `keyframes`; cada keyframe só com `t_s`, `x`, `y`, `zoom` e `interpolation`. Tamanho: até 32 keyframes por segmento, dentro dos tetos vigentes de segmentos, camadas e tamanho do plano. Números finitos, sem booleanos como número. Os valores entram no grafo apenas como números formatados pelo helper atual, nunca como texto livre. O grafo continua transportado por arquivo (`-/filter_complex`) e não tem teto próprio; o teto de 32.767 caracteres vigente é o da linha de comando do FFmpeg no Windows, que não cresce com o grafo (correção de premissa registrada na T2).
- Tecnologia imposta: FFmpeg e FFprobe locais, `libx264` e `aac`, sem rede e sem shell, como na pipeline vigente.

## Como provar

| Aceite | Evidência esperada |
|---|---|
| A1 | `plan validate` aceita as quatro proporções com as dimensões esperadas no envelope e recusa proporção desconhecida e `max_full_width` com 9:16 |
| A2 | envelope com `reframe` normalizado e hash diferente do plano sem `reframe`; um caso recusado por regra com o código e a mensagem esperados |
| A3 | fonte 1920x1080 em 9:16 com `zoom` 1.0 informa fator ~1.78; fonte 3840x2160 em 9:16 com `zoom` 1.0 informa lista vazia |
| A4 | FFprobe da preview e da full de cada proporção mostra as dimensões esperadas e H.264/AAC |
| A5 | fonte sintética com marcadores de posição conhecidos: frames extraídos da full nos instantes dos keyframes e no meio de um trecho `ease` e de um `linear` mostram o recorte esperado; um keyframe na borda não produz preto |
| A6 | `framemd5` da full de um plano de fixture 16:9 igual antes e depois; texto medido na preview e na full ocupa a mesma fração da largura |
| A7 | aprovação, mutação bloqueada, `data.decode.windowed` e `data.proxy` corretos num plano 9:16 com `reframe` |
| A8 | `capabilities --json` contém as proporções e os limites; validação da skill passa e o texto cobre vertical, keyframes e zonas seguras |

## Boundaries

### Always

- Tratar `plan.json` como entrada não confiável e validar na borda antes de inspecionar mídia.
- Manter preview e full no mesmo grafo e a full sempre dos originais.
- Manter planos v2 existentes válidos e com a mesma full.

### Ask first

- Mudar a resolução fixa das proporções não 16:9, a curva `ease` ou os tetos de keyframes e `zoom`.
- Qualquer mudança que exija `schema_version` 3 ou remova o `motion`.

### Never

- Publicar preview ou full fora da proporção e das dimensões do perfil.
- Usar proxy na full ou incluí-lo em hash de aprovação.
- Montar o grafo com texto vindo do plano fora dos campos já previstos para texto.

## Adendo de escopo (08/10/2026): achados da review e registros de `.erros-encontrados/`

Origem: depois da review final, o humano pediu em chat para resolver todos os achados (R1 a R4) e incluir a resolução dos registros abertos de `.erros-encontrados/`. As decisões abaixo foram tomadas pelo coordenador com a recomendação mais segura e reversível, sem alterar nenhum contrato já aprovado; ficam sujeitas à confirmação do humano na finalização. O trecho "Fora" desta spec que listava sombra e contorno de texto passa a ser parcialmente atendido (a caixa de fundo continua fora). O registro `2026-09-10-python-global-inquirer-editor.md` (colisão de nome com o Python global do host, decisão já registrada de não alterar o ambiente global nem renomear o pacote) e o `2026-09-19-test-installation-encoding.md` (já corrigido) não entram.

### Decisões do adendo

15. `data.profile.upscale` considera também o `motion`: o fator de um segmento é o do enquadramento (decisão 9) multiplicado pelo maior `zoom` entre `motion.from` e `motion.to` (1.0 sem `motion`), então "lista vazia" volta a significar que nenhum segmento é ampliado. Só o campo informativo muda; nenhum plano deixa de ser válido. (R1)
16. A full 16:9 mantém exatamente o canvas atual sempre que ele já é aceito pela checagem do MP4 (largura alinhada a 16, altura par, razão dentro de 0,001 de 16:9), o que inclui as larguras múltiplas ímpares de 16 entre 3184 e 3824, cuja razão fica dentro da tolerância; só quando o canvas atual seria recusado (largura múltipla ímpar de 16 com razão fora da tolerância, por exemplo fonte 1200x674, que hoje falha com `RENDER_FAILED`) a largura passa a ser alinhada a 32, como a da preview, com altura exata `largura * 9 / 16`, sempre par (1200x674 passa a sair em 1184x666). A tolerância é uma constante única, usada pelo cálculo do canvas e pela checagem do MP4. Medição do implementador da T5: sem essa preservação, 21 larguras mudariam 16 px, o que contraria a decisão 14.
17. Com janelas de decode, o áudio renderizado coincide amostra a amostra com o renderizado sem janelas, inclusive quando o corte cai num keyframe de vídeo; a afirmação do `REGRAS.md` de que quadros e áudio são provados iguais por `framemd5` passa a valer para esse caso e tem prova.
18. A camada de texto aceita `shadow` e `outline` opcionais. `shadow`: `offset_x_px`, `offset_y_px` (inteiros de -100 a 100), `blur_px` (0 a 50), `color` (`#RRGGBB`, padrão `#000000`) e `opacity` (0 a 1, padrão 0.5). `outline`: `width_px` (inteiro de 1 a 30) e `color` (`#RRGGBB`, padrão `#000000`). Todos os valores em pixels são definidos na largura da full e escalados na preview pelo mesmo fator do `size_px` (decisão 11), com mínimo 1 quando maior que zero. Sem `shadow` e sem `outline` o grafo do texto é idêntico ao atual. Sombra e contorno são recortados pela caixa da camada. Caixa de fundo não entra (`ponytail`).
19. Fontes HEVC e MOV: `.mov` e `.mp4` são aceitos para fontes audiovisuais, com vídeo `h264` ou `hevc` (perfis Main e Main 10, `yuv420p` ou `yuv420p10le`) e áudio AAC; streams de dados (por exemplo `tmcd`) e miniatura anexada (`attached_pic`) são ignorados quando não são o stream de vídeo selecionado; continuam recusados MKV e outros contêineres, mais de um stream de áudio, mais de um stream de vídeo real, legendas, codecs fora da allowlist e todos os tetos vigentes. A saída é sempre `yuv420p` de 8 bits (a conversão de 10 bits acontece no grafo e no proxy), e a análise de áudio, a extração de frames, o proxy, a preview e a full tratam essas fontes como as H.264. O caminho `--accel gpu` decodifica HEVC por CUDA quando possível e cai para CPU como hoje.
20. Preset de qualidade da full: `render full --quality standard|high|max` (padrão `standard`, que mantém exatamente os argumentos atuais de encoder: x264 `crf 23` e NVENC `p3`/`cq 31`). `high` e `max` reduzem `crf` e `cq` por equivalência de SSIM medida e documentada; a preview não recebe a opção e a qualidade não entra na aprovação nem no hash (não é conteúdo editorial). `capabilities` expõe os presets e `data.video.quality` informa o usado.

### Aceite do adendo

- [x] A9: uma fonte 16:9 de 1200x674 gera preview, aprovação e full válidas (full 1184x666, razão exata 16:9), e o canvas de toda largura cuja full já era gerada permanece idêntico. (decisão 16)
- [x] A10: o PCM do áudio renderizado com janelas de decode é idêntico ao sem janelas em fonte com GOP 25 (cortes em 1,0 s e 8,0 s) e com GOP 12, e o `framemd5` de vídeo continua igual. (decisão 17)
- [x] A11: `upscale` inclui o zoom do `motion` e fica vazio só quando nenhum segmento é ampliado; os textos de `youtube-editing.md`, `capabilities` e `REGRAS.md` descrevem a mesma regra. (decisão 15, R1, R2)
- [x] A12: um plano com texto com `shadow` e `outline` é validado, renderizado e mostra sombra e contorno medidos em pixels na preview e na full, com o mesmo aspecto proporcional; sem eles o grafo e a full são idênticos; cada violação de faixa, tipo ou campo desconhecido é recusada com `PLAN_INVALID` antes de inspecionar mídia. (decisão 18)
- [x] A13: uma fonte HEVC 10 bits em `.mov` e em `.mp4` com stream de dados e miniatura anexada percorre `analyze audio`, `extract frames`, `plan validate`, `proxy build`, preview e full (com janelas de decode e, quando disponível, `--accel gpu`) e sai em H.264 8 bits; MKV, dois áudios, codec fora da allowlist e vídeo extra continuam recusados. (decisão 19)
- [ ] A14: `render full --quality standard` gera exatamente a full de antes; `high` e `max` geram arquivos de qualidade crescente (SSIM e tamanho medidos) tanto na CPU quanto na GPU; `capabilities` expõe os presets. (decisão 20)
- [ ] A15: R3 e R4 aplicados (registro do teto de 32.767 marcado como resolvido; o perfil do canal proíbe também `video.reframe`), e cada registro aberto de `.erros-encontrados/` tratado por este adendo está marcado como resolvido com o commit correspondente.

## Handoff

vibe-plan (sem UI visível)

- Chat: recomendo novo chat para `vibe-plan`; se preferir, continuar neste chat não bloqueia. O `spec.md` vivo é a ponte.

- [x] Aprovação humana (leu o arquivo e confirmou)
