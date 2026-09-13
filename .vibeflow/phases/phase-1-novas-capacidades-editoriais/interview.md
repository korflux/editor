# Novas capacidades editoriais e preparação de trabalhos
# Status: aprovado

## Solicitação

> Vamos criar uma phase para implementação dessas features:
>
> 1. **Zoom e movimento:** O editor deverá permitir aproximar, afastar ou deslocar a imagem ao longo do tempo, com início, fim e intensidade definidos no plano. Isso servirá para dar movimento a cenas estáticas, destacar rostos ou enfatizar detalhes. Atualmente, `crop` apenas ajusta o enquadramento, não cria movimento editorial.
> 2. **Cortes J e L:** O editor deverá representar claramente quando o áudio da próxima cena começa antes da imagem ou quando o áudio da cena anterior continua depois da troca visual. Hoje alguns casos podem ser simulados usando faixas de áudio independentes, mas ainda não existe uma operação própria nem validação específica para garantir esses cortes.
> 3. **Inserções visuais em camada:** O editor deverá permitir colocar uma imagem ou vídeo sobre o vídeo principal, definindo posição, tamanho e duração. Isso permitirá picture-in-picture, imagens laterais e inserções sem substituir totalmente a cena principal. Atualmente, o editor suporta apenas b-roll em tela cheia.
> 4. **`editor init`:** O editor deverá preparar um novo trabalho com segurança, criando ou validando a pasta de trabalho e suas subpastas. O comando não deverá escolher arquivos, tomar decisões editoriais ou substituir a aprovação do usuário. Atualmente, essa preparação ainda é manual.
> 5. **Localização de texto:** Hoje só dá para colocar texto nos cinco eixos, topo esquerdo, topo direito, baixo esquerdo, baixo direito e meio. Devemos mudar para eixos `x` e `y`, podendo colocar em qualquer lugar da tela.

## Hipótese inicial

```text
HIPÓTESE: a próxima phase deve ampliar o contrato de plano e o grafo FFmpeg para composição temporal e espacial, mantendo a execução local, determinística e orientada por agentes.
CONFIDENCE: ~80%, faltam apenas fechar na especificação os detalhes exatos do schema v2, dos limites de mídia e da semântica de âncora das coordenadas.
```

## Trilha

### 1

Q: Essas cinco capacidades devem formar uma única phase com `schema_version: 2`, mantendo a timeline linear do vídeo-base, mas permitindo áudio atravessar as fronteiras visuais e camadas independentes? Recomendo essa direção porque zoom, camadas, coordenadas livres e cortes J/L mudam a semântica do schema v1; manter as duas interpretações em paralelo prolongaria ambiguidade no validador e no renderizador.
GUESS: A phase terá uma quebra controlada de contrato. O schema v2 será o contrato vigente para novos planos, com migração dos fixtures internos. `editor init` será idempotente, criará somente a estrutura do trabalho e nunca escreverá plano, preview, aprovação ou decisões editoriais.
R: Sim.

## Resultado

Entendi assim:

- O quê: uma evolução do núcleo local do editor para zoom e movimento interpolados, cortes J/L explícitos, inserções visuais em camada, preparação segura com `editor init` e posicionamento livre de texto por `x` e `y`.
- Pra quem: agentes de IA que escrevem planos JSON e operadores humanos que inspecionam, aprovam e exportam o resultado pela CLI.
- Por quê: aumentar a expressividade editorial sem abandonar um contrato previsível, reproduzível e verificável por preview, aprovação e full.
- Sucesso: um plano v2 válido consegue renderizar preview e full com as cinco capacidades combinadas; J/L são rejeitados quando temporalmente inconsistentes; camadas preservam o vídeo-base; `editor init` prepara um trabalho novo ou existente sem sobrescrever estado; planos inválidos falham antes do FFmpeg.
- Limite: execução local no Windows, sem decisões editoriais automáticas, sem UI, sem upload e sem substituir a aprovação por uma etapa implícita. A timeline visual continua linear; a sobreposição será restrita às camadas e ao áudio explicitamente declarados.
- Fora: rotação, easing configurável, tracking automático de rosto, chroma key, máscaras, transições visuais, ducking automático, equalização paramétrica, edição não linear geral e múltiplas variantes publicadas.
- Visual: saída horizontal 16:9; coordenadas normalizadas de `0` a `1`, com origem no canto superior esquerdo e posição independente da resolução. A ordem das camadas define a profundidade, da primeira abaixo para a última acima.

## Direção

### Contrato do plano v2

- `schema_version: 2` será explícito e substituirá o contrato v1 para novos planos. A compatibilidade histórica dos fixtures internos será atualizada na própria phase, sem manter dois caminhos de renderização.
- O vídeo de cada segmento continuará definindo a sequência visual principal e o `fit` continuará sendo distinto do movimento. O movimento será declarado por estados inicial e final, com interpolação linear e intensidade limitada, sem easing configurável nesta phase.
- Os cortes J/L serão uma operação declarada na fronteira entre segmentos adjacentes, com tipo e duração de sobreposição validados. A renderização deverá montar o áudio em uma linha temporal independente da concatenação visual, em vez de simular o comportamento apenas com faixas coincidentes.
- O campo de composição visual será uma lista ordenada de camadas. Texto, imagem e vídeo serão tipos discriminados, com intervalo na timeline, `x`, `y`, largura e altura normalizados. A camada de vídeo terá reprodução local limitada às fontes já suportadas pelo contrato, e a camada de imagem terá uma allowlist explícita de formatos raster.
- O lettering deixará de depender de posições nomeadas no v2. `x` e `y` representarão o ponto superior esquerdo da caixa de texto; alinhamento e quebra de linha continuarão sendo propriedades de apresentação, não coordenadas alternativas.
- O hash canônico, `approval.json`, a verificação de fontes e a publicação atômica continuarão sendo a barreira entre preview aprovada e full. Qualquer mudança em plano, fonte, camada ou mídia usada deverá invalidar a aprovação.

### `editor init`

- Comando recomendado: `editor init --workdir <caminho-absoluto> --json`.
- O comando poderá criar um diretório novo quando seu pai for seguro e regular, ou validar e completar um diretório existente. Deve ser idempotente.
- A estrutura inicial incluirá `.tmp`, `analysis/audio`, `analysis/transcription` e `frames`. Não criará `plan.json`, `approval.json`, `preview.mp4` ou `full.mp4` vazios.
- Caminhos existentes que sejam arquivo, symlink, junction ou outro reparse point serão rejeitados. Nenhuma origem será selecionada, copiada ou alterada.
- A resposta estruturada informará o estado do trabalho e as pastas criadas ou já existentes, sem alegar que o trabalho foi aprovado ou que possui fontes.

### Estratégia mínima de verificação

- Validar schema v2 com casos positivos e negativos antes de executar o FFmpeg, incluindo limites de coordenadas, tamanho, duração, fontes, ordem de camadas e fronteiras J/L.
- Testar o grafo produzido com fixtures curtas reais, verificando movimento em frames amostrados, posição e dimensões de camadas, áudio antecipado ou prolongado e duração final.
- Testar `init` em diretório inexistente, diretório existente, execução repetida, arquivo no destino e componentes linkados, preservando qualquer estado já publicado.
- Reexecutar a suíte do MVP, o E2E ampliado, `pip-audit`, gitleaks e `git diff --check` antes da revisão da phase.

## Decisões críticas

| ID | Estado | Decisão | Motivo e impacto |
|---|---|---|---|
| SCOPE-01 | DECIDIDO | As cinco capacidades pertencem a uma única phase de evolução do núcleo. | Elas compartilham o contrato do plano, a validação temporal/espacial e o grafo de renderização; separá-las artificialmente duplicaria a migração. |
| CONTRACT-01 | DECIDIDO | O contrato vigente da phase será `schema_version: 2`. | As semânticas de áudio, composição e posicionamento mudam de forma relevante; a quebra explícita evita interpretar v1 com regras diferentes. |
| MOTION-01 | ASSUMIDO | Movimento com estado inicial/final, interpolação linear e parâmetros normalizados. | É o menor modelo determinístico que cobre zoom, afastamento e deslocamento sem introduzir uma linguagem de animação. |
| CUT-01 | ASSUMIDO | J/L será declarado na fronteira entre segmentos, com duração validada. | Torna a intenção editorial legível e permite construir uma linha de áudio global, em vez de depender de uma simulação acidental por faixa. |
| LAYER-01 | ASSUMIDO | Uma lista ordenada de camadas discrimina texto, imagem e vídeo. | Reutiliza a mesma composição temporal e torna o z-order determinístico para picture-in-picture e inserções laterais. |
| TEXT-01 | DECIDIDO | Texto usa `x` e `y` normalizados, com origem no canto superior esquerdo. | Mantém a posição estável entre preview e full e elimina o conjunto fechado de posições nomeadas. |
| INIT-01 | DECIDIDO | `editor init` é seguro, idempotente e não cria estado editorial. | Prepara o fluxo do agente sem escolher arquivos, apagar artefatos ou substituir aprovação humana. |
| STATE-01 | DECIDIDO | A aprovação por hash permanece obrigatória para `full`. | Novos efeitos e camadas não podem permitir que a full seja diferente da preview aprovada. |

## Handoff

vibe-spec
