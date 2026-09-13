# Implement: Novas capacidades editoriais e preparação de trabalhos
# Alvo: phase-1-novas-capacidades-editoriais
# Status: aprovado
# Route: high · modo A · T6

## Reconhecimento

- A fila da phase tinha T1 e T2 elegíveis. T1 foi escolhida pelo operador.
- O ponto de entrada real é `src/editor/cli.py`, que converte `CliError` em
  envelope JSON por `contracts.py`. A validação compartilhada de caminhos fica
  em `src/editor/workdir.py` e é consumida por análise, plano, aprovação e
  renderização.
- A preparação precisava aceitar somente a criação do último componente do
  trabalho, validar todos os caminhos gerenciados antes de criar subpastas e
  preservar estado editorial existente.

## Implementação

- Adicionado `editor init --workdir <caminho-absoluto> --json` ao parser e ao
  dispatcher central.
- Adicionado `init_workdir()` com preflight de diretórios, arquivos conhecidos,
  links, junctions, reparse points e permissões.
- A estrutura é criada em ordem fixa: `.tmp`, `analysis`, `analysis/audio`,
  `analysis/transcription` e `frames`. `sources` e artefatos editoriais não são
  criados.
- A resposta separa diretórios `created` e `existing`, lista somente artefatos
  conhecidos em `preserved_artifacts` e retorna `state: "ready"`.
- Falhas durante a criação removem somente diretórios vazios criados pela
  própria execução, em ordem reversa.
- `validate_workdir()` passou a reutilizar a normalização absoluta comum sem
  mudar seu contrato público.

## Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_init tests.test_cli_contract -v`
  -> `Ran 10 tests ... OK`.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  -> `Ran 75 tests ... OK`.
- A prova inclui processo real, segunda execução idempotente, preservação por
  hash, caminho relativo, arquivo no destino, pai inexistente, destino não
  gravável, falha de criação e junction gerenciada.
- `git diff --check` passou.

## Arquivos

- `src/editor/cli.py`
- `src/editor/workdir.py`
- `tests/test_init.py`
- `tests/test_cli_contract.py`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/analyze.md`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/spec.md`

## T2: Adotar contrato estrutural v2 estrito

#### Reconhecimento

- A fila recalculada após T1 tinha somente T2 elegível.
- A fronteira única do plano está em `src/editor/plan/schema.py` e
  `src/editor/plan/validation.py`; o fixture compartilhado é
  `tests/fixtures/workflow/plan.json`.
- O plano v1 ainda aceitava `overlays`, não exigia `cuts`/`layers` e retornava
  `overlay_count`. A versão publicada da CLI ainda era `0.1.0`.

#### Implementação

- O root passou a aceitar somente o contrato v2 com `cuts` e `layers` explícitos,
  rejeitando v1 e `overlays` antes da inspeção de mídia.
- A validação estrutural agora ocorre antes do FFprobe para fontes, segmentos,
  cortes, camadas, coordenadas normalizadas, campos desconhecidos e números não
  finitos.
- Camadas de texto usam `x`, `y`, `width` e `height`, sem posições nomeadas; o
  resultado de `plan validate` expõe `cut_count` e `layer_count`.
- O pacote e a CLI foram promovidos para `1.0.0`, sem adicionar dependências.
- O fixture linear `workflow/plan.json` foi migrado para v2. Os artefatos
  derivados continuam fora desta fatia e serão regenerados na T8.

#### Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_cli_contract tests.test_plan_validation -v`
  -> `Ran 21 tests ... OK`.
- A prova cobre processo real, hash canônico, rejeição de v1, `overlays`,
  posições nomeadas, NaN e listas v2 ausentes antes da inspeção de mídia.
- `git diff --check` passou.

#### Arquivos

- `pyproject.toml`
- `src/editor/contracts.py`
- `src/editor/plan/schema.py`
- `src/editor/plan/validation.py`
- `tests/test_cli_contract.py`
- `tests/test_capabilities.py`
- `tests/test_plan_validation.py`
- `tests/fixtures/workflow/plan.json`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/spec.md`

## T3: Interpolar movimento no vídeo-base

#### Reconhecimento

- A fila recalculada após T2 tinha T3, T4 e T9 elegíveis. A T3 foi escolhida
  explicitamente pelo operador.
- O contrato estrutural do vídeo estava centralizado em
  `src/editor/plan/schema.py` e `src/editor/plan/validation.py`, enquanto o
  render compartilhado era construído por `src/editor/render/ffmpeg_graph.py`.
- A validação ainda rejeitava `motion`, e o grafo só aplicava `fit`, portanto o
  movimento precisava ser validado antes da inspeção de mídia e aplicado após a
  normalização do vídeo no canvas.

#### Implementação

- Adicionado `video.motion` opcional com estados obrigatórios `from` e `to`,
  campos estritos `x`, `y` e `zoom`, números finitos e limites normalizados de
  foco e zoom entre `0.5` e `4.0`.
- O relatório da timeline passa a expor os estados de movimento normalizados;
  a ausência de movimento mantém o caminho estático existente.
- O grafo aplica interpolação linear pelo tempo local do segmento, escala por
  frame, canvas preto fixo até o zoom máximo e crop com limites nas bordas.
  Assim, zoom maior ou igual a um não revela áreas fora da fonte e zoom menor
  que um revela preto sem alterar a duração ou o canvas final.
- Corrigida a saída residual da migração v2 em preview/full, que ainda buscava
  `overlay_count` depois de o validador passar a produzir `cut_count` e
  `layer_count`.
- A prova visual gera uma fonte `testsrc2` somente no diretório temporário do
  teste, comparando frames do mesmo instante com e sem movimento e verificando
  o preenchimento preto no afastamento.

#### Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v`
  -> `Ran 26 tests ... OK`.
- A prova cobre estados opcionais e normalizados, campos desconhecidos, estado
  incompleto, foco fora do intervalo, zoom fora do intervalo e NaN antes do
  FFprobe.
- A prova de preview confirma mudança real de pixels, canvas 320x180, duração
  de 2 segundos e áreas pretas no zoom-out.
- `git diff --check` passou.

#### Arquivos

- `src/editor/plan/schema.py`
- `src/editor/plan/validation.py`
- `src/editor/render/ffmpeg_graph.py`
- `src/editor/render/preview.py`
- `src/editor/render/full.py`
- `tests/test_plan_validation.py`
- `tests/test_preview_render.py`

## T4: Montar áudio global para cortes J/L

#### Reconhecimento

- A fila recalculada após T3 tinha T4 e T9 elegíveis. A T4 foi escolhida
  explicitamente pelo operador.
- `_validate_cuts_structure()` já protegia a forma básica de `cuts[]`, mas não
  conhecia a quantidade de segmentos, as durações adjacentes, o modo de áudio
  afetado ou a cobertura real das faixas.
- `build_ffmpeg_graph()` concatenava vídeo e áudio por segmento. Isso impedia
  posicionar um grupo antes ou depois da fronteira sem deslocar a timeline
  visual.

#### Implementação

- Adicionada a resolução centralizada das janelas globais de áudio, com
  rejeição de fronteira inexistente, duplicada, duração incompatível, extensão
  de segmento em mute e cobertura insuficiente.
- A timeline validada passa a expor `audio_start_s`, `audio_end_s` e
  `audio_duration_s`; a duração visual e o hash canônico permanecem separados
  e inalterados.
- O grafo mantém a concatenação somente do vídeo, monta cada grupo de áudio
  com sua duração necessária, aplica `adelay` para J/L e mistura os grupos
  posicionados com `normalize=0` e `dropout_transition=0`.
- O áudio global é recortado para `[0, timeline_duration)`. Segmentos mute não
  geram silêncio artificial e, quando todos estão mute, a saída continua sem
  stream de áudio.
- A cobertura existente de `loop: true` foi preservada, agora aplicada à
  janela estendida do grupo. A prova reutiliza as fontes reais do fixture de
  workflow, sem criar fixture binário nova.

#### Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v`
  -> `Ran 29 tests ... OK`.
- A prova real compara J+L com o mesmo plano sem cortes, confirma antecipação
  em 0,60–0,70 s, extensão em 1,30–1,40 s, duração visual de 2 s e stream AAC.
- A inspeção do grafo confirma `adelay`, mixagem sem normalização automática e
  ausência de `acrossfade` ou `sidechaincompress`.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  -> `Ran 87 tests ... OK`.
- `git diff --check` passou.

#### Arquivos

- `src/editor/plan/validation.py`
- `src/editor/render/ffmpeg_graph.py`
- `tests/test_plan_validation.py`
- `tests/test_preview_render.py`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/spec.md`

## T5: Renderizar texto em caixas normalizadas

#### Reconhecimento

- O plano v2 já validava geometria e campos de texto, mas o grafo compartilhado
  ainda percorria `overlays` e exigia `position`, ambos removidos na T2.
- A preview e a full usam o mesmo `build_ffmpeg_graph()`. A correção precisava
  acontecer nessa origem, preservando a timeline e o áudio existentes.

#### Implementação

- `layers[]` de texto agora são desenhadas na ordem declarada sobre o vídeo-base.
  Cada texto usa uma superfície transparente do tamanho da caixa normalizada,
  com alinhamento interno e sobreposição somente em `[start_s, end_s)`.
- Quebras explícitas e `wrap: true` são transmitidos como novas linhas reais ao
  FFmpeg. A área fora da caixa é recortada pela própria superfície, sem mover o
  vídeo-base ou alterar a duração.
- A validação rejeita famílias de fonte não instaladas antes da inspeção de
  mídia. O escape do filtro trata apóstrofos e pontuação sem quebrar o grafo.
- A estimativa de largura usada em `wrap` segue registrada como limitação
  `ponytail` até haver necessidade de medição exata de glifos.

#### Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v`
  -> `Ran 31 tests ... OK` após a simplificação.
- Frames reais confirmam caixa, recorte horizontal e vertical, intervalo,
  preservação do vídeo-base e quebra de linha. O teste também renderiza texto
  com pontuação e apóstrofo.
- `.venv\Scripts\python.exe -B -m unittest tests.test_full_render -v`
  -> `Ran 6 tests ... OK`, cobrindo o consumidor compartilhado do grafo.
- `git diff --check` passou.

#### Arquivos

- `src/editor/plan/validation.py`
- `src/editor/render/ffmpeg_graph.py`
- `tests/test_plan_validation.py`
- `tests/test_preview_render.py`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/implement.md`

## T6: Camadas raster estáticas

#### Reconhecimento

- O plano v2 já discriminava camadas de imagem na estrutura, mas `media` aceitava
  apenas mídia audiovisual; o grafo incluía somente camadas de texto.
- `build_ffmpeg_graph` é compartilhado pela preview e full, e os argumentos de
  entrada são montados centralmente em `render/preview.py`.

#### Implementação

- A inspeção central aceita PNG, JPEG/JPG e WebP estáticos de formato e codec
  coerentes, até 100 MiB e 8.192 pixels por dimensão. APNG e WebP animado são
  detectados antes de alcançar o render.
- O plano exige `kind: image` para a camada e registra a fonte sem duração ou
  áudio. A imagem entra em loop controlado no FFmpeg e é composta após o vídeo
  base em ordem de `layers[]`, com alpha, `contain`, `crop` e intervalo fechado
  à esquerda e aberto à direita.
- `CliError` deixou de ser uma dataclass imutável após a prova expor que o Python
  não conseguia atribuir `__traceback__` em falhas propagadas pelo `unittest`.
  Esse erro preexistente foi corrigido com a remoção de `frozen=True`.
- O Documento Vivo foi atualizado com o contrato de imagem da Phase 1.

#### Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render tests.test_full_render -v`
  -> `Ran 39 tests ... OK`.
- Fixtures reais verificam transparência, caixa, `contain`, `crop`, z-order,
  intervalo, ausência de áudio, formatos permitidos, animação rejeitada e limites.
- A revisão do diff preservou o único caminho de composição usado por preview
  e full. `git diff --check` passou.

#### Arquivos

- `src/editor/contracts.py`, `src/editor/media.py`, `src/editor/plan/schema.py`,
  `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py` e
  `src/editor/render/preview.py`.
- `tests/test_plan_validation.py`, `tests/test_preview_render.py` e
  `tests/fixtures/phase-1/images/`.
- `.vibeflow/REGRAS.md`, `plan.md` e `implement.md` desta phase.

## T7: Vídeo em camada sem áudio implícito

#### Reconhecimento

- O schema v2 já descrevia `type: "video"`, mas a validação de camadas conferia apenas imagens e o grafo ignorava camadas de vídeo.
- Preview e full compartilham `build_ffmpeg_graph`; corrigir esse ponto preserva uma única composição visual.

#### Implementação

- O validador exige fonte `kind: "video"`, trecho crescente dentro da duração da fonte e duração igual ao intervalo global, além das regras estruturais de caixa e `fit` já existentes.
- O grafo inclui o vídeo da camada como entrada visual, recorta `in_s`/`out_s`, posiciona o primeiro frame em `start_s` e compõe na ordem de `layers[]` sobre o vídeo-base. Áudio só é mapeado a partir de `segments[].audio`.
- Imagem e vídeo reutilizam o mesmo filtro espacial, mantendo `contain`, `crop` e o intervalo `[start_s, end_s)`.

#### Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v` -> `Ran 35 tests ... OK` após simplificação.
- `.venv\Scripts\python.exe -B -m unittest tests.test_full_render -v` -> `Ran 6 tests ... OK`.
- O clipe real de teste alterna vermelho e verde e contém áudio. Frames provam o recorte, a caixa, a ordem visual e o vídeo-base preservado; a preview final não contém stream de áudio quando os segmentos estão mute.
- `git diff --check` passou.

#### Arquivos

- `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`.
- `.vibeflow/REGRAS.md`, `plan.md`, `spec.md` e `implement.md` desta phase.

## Handoff

Próximas T* elegíveis: T8 e T9. T10 depende das duas.
