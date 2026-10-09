# Proporções de saída e reenquadramento por keyframes
# Status: confirmado

## Solicitação

Eu gostaria de abrir uma fase para o editor ser capaz de editar vídeos em outras proporções também.

## Hipótese inicial

```text
HIPÓTESE: o plan.json passa a aceitar outras proporções em profile.aspect_ratio (hoje só "16:9") e preview, full, canvas e validação seguem a proporção escolhida
CONFIDENCE: ~45%, falta: quais proporções, uma ou várias saídas por plano, regra de largura/altura e reenquadramento
```

Evidência no código na abertura: `profile.aspect_ratio` já existe no schema v2, mas só aceita `"16:9"` (`src/editor/plan/validation.py:350`); 16:9 também está fixo no canvas (`src/editor/render/preview.py:73` e `:116`), na checagem da mídia renderizada (`preview.py:527`), na regra de `fit` (`validation.py:715`) e em `capabilities.py:300`. O `crop` corta sempre pelo centro (`src/editor/render/ffmpeg_graph.py:198`) e o `motion` atua depois do corte, dentro do canvas.

## Trilha

### 1

ANOTEI: o humano quer uma phase para gerar vídeos em proporções além de 16:9; proporções e uso ainda abertos.
Q: Quais proporções a phase precisa suportar, e para qual uso?
GUESS: 9:16 (Shorts, Reels, TikTok), 1:1 e 4:5 (feed), além do 16:9; cada plano gera uma única proporção, preservando o contrato de uma preview, uma full e uma aprovação por hash.
R: sim, confirmou o guess.

### 2

ANOTEI: confirmadas as quatro proporções e uma proporção por plano; o `crop` atual é central e o `motion` não alcança a largura original da fonte.
Q: Na conversão de gravações horizontais para vertical, o agente precisa escolher o enquadramento do corte, ou o corte central basta nesta phase?
GUESS: precisa escolher; o `crop` ganha ponto de foco opcional por segmento (x/y normalizados 0 a 1, centro por padrão), estático; pan animado e tracking ficam fora.
R: sim, e ampliou: o sujeito nem sempre está no centro e é preciso, como os keyframes de Posição no Premiere, marcar pontos no tempo em certa posição horizontal e até vertical, e o enquadramento desliza até lá sem cortar. Pan animado entra no escopo.

### 3

ANOTEI: foco estático confirmado; o pedido passa a incluir keyframes de reenquadramento sobre a imagem original dentro do mesmo segmento, o que traz keyframes arbitrários e easing para o escopo.
Q: Como o movimento entre um ponto e outro deve se comportar?
GUESS: lista opcional de keyframes por segmento com tempo relativo ao segmento, x/y normalizados sobre a fonte original e zoom opcional; interpolação ease in/out por padrão e `linear` opcional por keyframe; sem keyframes vale o foco estático.
R: sim, confirmou o guess.

### 4

ANOTEI: keyframes confirmados; a regra vigente de full sem upscale e altura derivada da largura não funciona no vertical (4K daria 1920x3413; 1080p recortado só tem 607x1080 reais).
Q: No vertical (e 1:1 e 4:5), a full deve sair sempre no padrão das plataformas, mesmo ampliando gravações 1080p, ou manter a regra de nunca ampliar?
GUESS: sempre no padrão das plataformas, 1080x1920, 1080x1080 e 1080x1350, com o fator de ampliação por segmento informado como aviso no `plan validate`, sem bloquear; 16:9 inalterado; preview na mesma proporção com lado maior até 720 pixels.
R: sim, confirmou o guess, aceitando inverter a garantia "sem upscale" fora do 16:9.

### 5

ANOTEI: resolução confirmada; resta a relação entre keyframes e o `motion` atual, usado por planos e fixtures existentes.
Q: Os keyframes de reenquadramento devem valer também em saídas 16:9, e o que acontece com o `motion` atual?
GUESS: valem em todas as proporções, inclusive punch-in em 16:9; `motion` continua aceito sem mudança (minor); um segmento com `motion` e keyframes ao mesmo tempo é recusado; a skill orienta keyframes em trabalhos novos.
R: sim, confirmou o guess.

### 6

ANOTEI: relação com `motion` confirmada; o erro registrado em `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md` faz a preview não representar a full em tamanho de texto, o que piora a aprovação no vertical.
Q: Esta phase deve corrigir a escala do `size_px` entre preview e full, e a sombra do texto fica dentro ou fora?
GUESS: a escala entra (`size_px` interpretado na resolução da full e escalado na preview); a sombra vira phase própria; zonas seguras de Shorts e Reels entram só como orientação na skill.
R: sim, confirmou o guess.

## Resultado

Entendi assim:

- O quê: `profile.aspect_ratio` do plano v2 aceita `9:16`, `1:1` e `4:5`, além de `16:9`; canvas, validação da mídia renderizada, regra de `fit` e `capabilities` seguem a proporção declarada. Um plano gera uma única proporção (uma preview, uma aprovação, uma full). O `crop` ganha foco opcional (x/y normalizados sobre a fonte original, centro por padrão) e lista opcional de keyframes por segmento (tempo relativo, x, y, zoom opcional), com ease in/out por padrão e `linear` opcional, válidos em todas as proporções. `motion` continua aceito sem mudança e não pode coexistir com keyframes no mesmo segmento (mudança minor). 16:9 mantém a resolução atual; as demais saem em 1080x1920, 1080x1080 e 1080x1350, com ampliação permitida e aviso por segmento no `plan validate`; preview com lado maior até 720 pixels. `size_px` passa a ser interpretado na resolução da full e escalado na preview, fechando o Problema 1 do erro registrado.
- Pra quem: agentes que operam a CLI para gerar Shorts, Reels e feed, começando pelo canal Sonni Viaja.
- Por quê: reaproveitar gravações 16:9 em formatos verticais e quadrados com o sujeito enquadrado, sem editor externo.
- Sucesso: um plano 9:16 sobre fonte 16:9 com keyframes gera preview e full na proporção certa, com o enquadramento deslizando suavemente entre os pontos; a preview representa a full em proporção e tamanho de texto; aprovação por hash, janelas de decode e proxies continuam funcionando.
- Limite: allowlist de quatro proporções; teto de keyframes por segmento a definir na spec; mesma pipeline local FFmpeg, sem rede.
- Fora: várias proporções no mesmo plano; tracking automático de rosto ou sujeito; sombra e contorno de texto (Problema 2 do erro registrado, phase própria); validação de zonas seguras no código (só orientação na skill); fundo desfocado no `contain`; keyframes em camadas; transições.
- Visual: N/A, CLI sem interface.

## Handoff

Próxima etapa: `vibe-spec`. Rota sugerida: high, pois amplia o schema do plano, o grafo compartilhado de preview e full e a validação da mídia renderizada, com regressão relevante nos testes de render.
