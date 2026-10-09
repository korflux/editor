# Plan: proporções de saída e reenquadramento por keyframes
# Alvo: phase-5-proporcoes-e-reenquadramento
# Status: aprovado
# Spec: spec.md (mesma pasta)

## Overview

Esta entrega faz o editor renderizar planos em 9:16, 1:1 e 4:5, além de 16:9, e enquadrar fontes horizontais com foco estático ou keyframes interpolados (`ease` ou `linear`) sobre a imagem original, conforme a [spec](spec.md). A fila primeiro abre as proporções no perfil, no canvas e na checagem do MP4 com corte central, provando que a full 16:9 vigente não muda. Depois adiciona o `reframe` com aviso de ampliação, corrige a escala do `size_px` na preview e, por fim, prova a convivência com aprovação, janelas de decode e proxies e publica o contrato em `capabilities`, skill e `REGRAS.md`.

- **Design:** N/A, entrega sem UI; a superfície é a CLI e os envelopes JSON.

## Prontidão das provas

- **Requisitos verificados (todos disponíveis localmente; o repositório não tem CI):**
  - Python 3.11.15 em `.venv\Scripts\python.exe` para `unittest`.
  - FFmpeg e FFprobe 9.0.1 no PATH, com `libx264` e `aac`; o filtro `crop` aceita expressões em `x`, `y`, `w` e `h`, e o `scale` aceita `eval=frame`, já usado pelo `motion`.
  - `gitleaks` 8.30.1 e `pip_audit` 2.10.1 (via `.venv`) para os controles de segurança da última task.
  - Fontes de teste (16:9 com marcadores de posição conhecidos, 1080p e 4K curtos) geradas no próprio teste por `ffmpeg -f lavfi`, sem fixture grande no repositório.
- **Ausências e ação na fila:** nenhuma.

## Tasks

### T1: Abrir as quatro proporções no perfil, no canvas e na checagem do MP4

- [x] T1 concluída
- **Spec:** A1, A4, A6 (full 16:9 inalterada), C3
- **O quê:** `profile.aspect_ratio` aceita `16:9`, `9:16`, `1:1` e `4:5`; `max_full_width` só com 16:9; `data.profile` ganha `full_width`, `full_height` (e `upscale` vazio até a T2); o canvas de preview e full segue as decisões 2 e 3; a regra de `fit: "preserve"`, o `crop` central, o `contain` e a checagem do MP4 renderizado usam a proporção do perfil, com as mensagens da spec. Uma única fonte da verdade para proporções e dimensões, consumida por validação, render e (na T4) `capabilities`.
- **Aceite:**
  - [x] `plan validate` aceita as quatro proporções e informa 1080x1920, 1080x1080 e 1080x1350 fora do 16:9; recusa proporção desconhecida e `max_full_width` com proporção não 16:9 com `PLAN_INVALID`.
  - [x] Preview e full de um plano 9:16, 1:1 e 4:5 com `crop` central sobre fonte 16:9 saem com as dimensões das decisões 2 e 3, sem barras; com `contain`, com barras.
  - [x] O `framemd5` da full de um plano 16:9 vigente (fixture de `tests/fixtures/workflow/`) é igual ao gerado antes da task, e todos os testes de plano e render existentes passam sem alteração de expectativa.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_aspect_ratio -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render tests.test_full_render tests.test_approval tests.test_decode_windows -v`
- **Deps:** nenhuma
- **Arquivos:** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/preview.py`, `src/editor/render/full.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_aspect_ratio.py`
- **Risco:** o `framemd5` da full 16:9 é a prova de que o refatoramento do canvas não mudou a saída vigente; capturar a referência antes da primeira edição.
- **Prova:** `tests.test_aspect_ratio` -> 20 testes OK; `tests.test_plan_validation tests.test_preview_render tests.test_full_render tests.test_approval tests.test_decode_windows` -> 75 testes OK; suíte completa (`discover -s tests`, com E2E) -> 204 testes OK. `framemd5` de vídeo e áudio de preview e full em 4 planos 16:9 (fixture `workflow`, fixture com camadas e corte J, sintético 1280x720 e sintético com `max_full_width` 640) igual antes e depois da task, e o `filter_complex` e o canvas 16:9 de um plano sintético estão fixados em teste. 9:16, 1:1 e 4:5 sobre fonte 16:9: dimensões de preview (404x720, 720x720, 576x720) e full (1080x1920, 1080x1080, 1080x1350) por FFprobe, `crop` sem pixel preto e `contain` com faixas pretas.
- **Arquivos (efetivos):** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/preview.py`, `src/editor/render/full.py`, `src/editor/render/ffmpeg_graph.py` (só docstring), `tests/test_aspect_ratio.py`, `tests/test_plan_validation.py` (fora da lista planejada: duas asserções de `data.profile` ganharam `full_width`, `full_height` e `upscale`, exigidos pelo F1 da spec), `.erros-encontrados/2026-10-08-full-16x9-falha-com-largura-multipla-impar-de-16.md`.
- **Decisões de implementação:** fonte única em `plan/schema.py` (`ASPECT_RATIOS`, `FIXED_CANVAS_DIMENSIONS`, `LANDSCAPE_ASPECT_RATIO`, `canvas_dimensions`), que a T4 importa em `capabilities`; `MAX_PREVIEW_DIMENSION` segue em `render/preview.py`. Nas proporções fixas a checagem do MP4 exige as dimensões exatas do canvas e não a razão (404x720 difere de 9:16 em ~0,0014); no 16:9 a checagem é a de antes. `upscale` é sempre `[]` até a T2.
- **Limitação registrada:** a prova de regressão 16:9 em teste usa canvas e hash do `filter_complex`, e não `framemd5` fixo, porque o x264 depende do número de threads; o `framemd5` foi comparado à mão antes e depois.

### T2: Reenquadrar com foco e keyframes e informar a ampliação

- [x] T2 concluída
- **Spec:** A2, A3, A5, C2
- **O quê:** `segments[].video.reframe` com 1 a 32 keyframes é validado pela allowlist e pelas regras das decisões 4 a 8 e 10, normalizado e incluído no plano canônico; o grafo recorta a fonte original quadro a quadro na proporção do canvas, com centro em `x`/`y`, encosto na borda, `zoom` de 1.0 a 4.0 e interpolação `ease` (`3p² - 2p³`) ou `linear` por trecho, mantendo o estado fora do intervalo; `motion` segue aplicado depois do enquadramento quando não há `reframe`. `data.profile.upscale` lista segmento e fator (decisão 9).
- **Aceite:**
  - [x] Cada violação (sem `crop`, com `motion`, `t_s` fora de ordem ou da duração, `x`/`y`/`zoom` fora da faixa, `interpolation` desconhecida, mais de 32 keyframes, campo desconhecido, booleano como número) é recusada com `PLAN_INVALID` antes de inspecionar mídia; plano válido muda o hash canônico frente ao mesmo plano sem `reframe`.
  - [x] Numa fonte sintética 16:9 com marcadores de posição, frames da full 9:16 nos instantes dos keyframes, no meio de um trecho `ease` e de um `linear` mostram o recorte esperado com até 2 pixels de diferença; keyframe na borda não produz preto; antes do primeiro e depois do último keyframe o estado é mantido; um keyframe único equivale ao foco estático.
  - [x] `upscale` informa ~1.78 para fonte 1920x1080 em 9:16 com `zoom` 1.0, o fator do maior `zoom` quando há keyframes, e lista vazia para fonte 3840x2160 com `zoom` 1.0.
  - [x] O grafo continua dentro do teto de 32.767 caracteres num plano com 32 keyframes em vários segmentos, e os números entram pelo formatador vigente. (Correção de premissa: o teto de 32.767 é da linha de comando do FFmpeg no Windows, não do grafo, que viaja por arquivo e não tem limite próprio; provado que 5 segmentos x 32 keyframes animados e 8 x 32 só com panorâmica cabem em 32.767 caracteres de grafo, e que 6 x 32 animados, ~34 mil caracteres de grafo, renderizam com a linha de comando abaixo do teto. Ver `.erros-encontrados/2026-10-08-teto-32767-e-da-linha-de-comando-e-nao-do-grafo.md`.)
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_reframe -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render tests.test_full_render -v`
- **Deps:** T1
- **Arquivos:** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_reframe.py`
- **Risco:** o `crop` do FFmpeg avalia `x`/`y` por quadro, mas o `zoom` animado exige tamanho variável; a implementação deve escolher entre `crop` com tamanho fixo (zoom constante no segmento) e `scale eval=frame` seguido de `crop` (zoom animado), registrando o custo como `ponytail` se o zoom animado dominar o tempo de render, como já ocorre no `motion`.
- **Prova:** `tests.test_reframe` -> 22 testes OK; `tests.test_plan_validation tests.test_preview_render tests.test_full_render` -> 57 testes OK; suíte completa (`discover -s tests`, com E2E e o golden do `filter_complex` 16:9 da T1 sem alteração de hash) -> 226 testes OK. Recorte medido em 15 instantes (antes do primeiro keyframe, nos keyframes, a 25/50/75% de `ease` e de `linear`, bordas e depois do último) na preview 404x720 e na full 1080x1920 9:16 com janela de decode, erro máximo ~1,3 px (tolerância 2 px); proxy real 1280x720 com janela, erro máximo 0,18 px; teste por mutação (trocar `ease` por `linear`, deslocar o tempo, remover o clamp, ignorar o zoom) faz o teste falhar; 250 planos aleatórios comparam as expressões geradas ao oráculo com erro < 1e-9. `upscale`: 1920x1080 em 9:16 -> 1.78; 3840x2160 em 9:16 -> vazio; maior `zoom` dos keyframes; 16:9 com zoom 2 -> 2.0. As 31 violações de validação são recusadas antes de qualquer FFprobe (espião em `inspect_source`, sem fonte em disco).
- **Arquivos (efetivos):** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_reframe.py`, `tests/test_aspect_ratio.py` (fora da lista planejada: duas expectativas de `upscale` deixaram de ser o placeholder `[]` da T1, pois a fonte 640x360 em canvas fixo de 1080 de largura é de fato ampliada), `.erros-encontrados/2026-10-08-teto-32767-e-da-linha-de-comando-e-nao-do-grafo.md`.
- **Decisões de implementação:** o quadro inteiro é escalado para que o recorte do `zoom` preencha a largura do canvas e o canvas é posicionado por `overlay` com clamp (nunca preto); posição `s(t)` por soma plana de `delta*clip(s-j,0,1)`, sem profundidade de expressão crescente com os keyframes; `scale` estático quando o zoom é constante e `eval=frame` só com zoom animado; `_motion_filter_graph` e o reframe compartilham `_scaled_overlay_graph` (texto do `motion` idêntico ao anterior). `upscale` é `[{"segment": <index 1-based da timeline>, "factor": <2 casas>}]` e, além do `crop`, também lista `contain` e `preserve` quando a imagem inteira é ampliada no canvas (fator = min(largura do canvas / largura da fonte, altura do canvas / altura da fonte)), porque a decisão 9 fala em "cada segmento com vídeo" e uma lista vazia mentiria nesse caso; decisão a confirmar com o humano na confirmação final. Constantes para a T4 em `plan/schema.py`: `REFRAME_FIELDS`, `KEYFRAME_FIELDS`, `MAX_REFRAME_KEYFRAMES`, `REFRAME_ZOOM_MIN`, `REFRAME_ZOOM_MAX`, `REFRAME_INTERPOLATIONS`, `DEFAULT_REFRAME_ZOOM`, `DEFAULT_REFRAME_INTERPOLATION`.
- **Limitações registradas (`ponytail`):** zoom alto em fonte grande escala o quadro inteiro e `eval=frame` recria o scaler a cada quadro; overlay `yuv420p` alinha a posição a pixel par (erro de até 1 px); 32 keyframes por segmento.

### T3: Escalar o `size_px` da preview pela largura da full

- [x] T3 concluída
- **Spec:** A6 (texto), decisão 11
- **O quê:** a camada de texto usa `size_px` na full e `size_px × largura da preview / largura da full` (arredondado, mínimo 1) na preview, aplicando o mesmo fator à quebra de linha; a full não muda.
- **Aceite:**
  - [x] Num plano 16:9 e num 9:16 com texto, a largura medida do texto ocupa a mesma fração da largura do quadro na preview e na full, com tolerância de 2% da largura.
  - [x] O `framemd5` da full do plano 16:9 vigente continua igual ao da referência da T1.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_preview_render tests.test_full_render tests.test_aspect_ratio -v`
- **Deps:** T1
- **Arquivos:** `src/editor/render/ffmpeg_graph.py`, `src/editor/render/preview.py`, `tests/test_preview_render.py`
- **Prova:** `tests.test_preview_render tests.test_full_render tests.test_aspect_ratio` -> 51 testes OK; suíte completa (`discover -s tests`, com E2E, T1 e T2) -> 230 testes OK. Largura do texto medida nos frames (pixels brancos sobre fundo escuro): 16:9, preview 704x396 e full 1280x720, 45,88% contra 45,86% da largura do quadro; 9:16 com `crop` sobre fonte horizontal, preview 404x720 e full 1080x1920, duas linhas, 79,70% contra 79,44% (diferença máxima 0,26%, tolerância 2%). O mesmo teste no código anterior falha (83,4% contra 45,9% em 16:9; 88,6% contra 79,4% em 9:16). Full inalterada medida antes e depois em 3 planos (fixture `workflow`, sintético 16:9 com texto, sintético 9:16 `crop` com texto): hash do `filter_complex` da full, `framemd5` de vídeo e de áudio iguais; o golden de `tests/test_aspect_ratio.py` não tem camada de texto e ficou intocado.
- **Arquivos (efetivos):** `src/editor/render/ffmpeg_graph.py`, `tests/test_preview_render.py` (classe `TextScaleTests`, 4 testes novos). `render/preview.py` não precisou mudar.
- **Decisões de implementação:** o grafo lê a largura da full em `validation["profile"]["full_width"]` (mesma fonte do canvas da full, T1), sem parâmetro novo em `build_ffmpeg_graph`; na preview o `fontsize` é `(2*size_px*canvas + ref)//(2*ref)` (meio para cima, mínimo 1; `_scaled_font_size`) e a quebra de linha usa o `size_px` e a largura de caixa da full (`_layer_span` na largura de referência), então a preview quebra nos mesmos pontos da full. Na full o fator é 1 e o filtro é idêntico ao anterior.
- **Limitação registrada (`ponytail`):** corpo da fonte arredondado a pixel inteiro (mínimo 1); em fonte pequena a dica de glifos pode variar a largura da preview em poucos por cento (medido: no máximo 0,26% do quadro).
- **Pendência para a T4:** marcar o Problema 1 como resolvido em `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md` (mantendo o Problema 2 aberto), remover da skill a nota de que `size_px` não escala, e reescrever em `REGRAS.md` a menção a "teto de 32.767 caracteres do grafo" como teto da linha de comando (ver erro registrado na T2).

### T4: Provar a jornada vertical completa e publicar o contrato

- [ ] T4 concluída
- **Spec:** A7, A8, C1, C3, C4
- **O quê:** um plano 9:16 com `reframe` e texto percorre validação, preview com e sem proxy, aprovação, bloqueio por mutação e full com janelas de decode; `capabilities` expõe proporções, dimensões de preview e full e limites do `reframe` lidos das constantes da T1 e T2; a skill `vibe-editor` documenta a escolha da proporção, a marcação de keyframes pelos frames extraídos, a preferência por `reframe` sobre `motion` e as zonas seguras do Shorts e do Reels; `REGRAS.md` registra a ferramenta, as limitações (`ponytail`) e o mapa de testes; o registro em `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md` marca o Problema 1 como resolvido e mantém o Problema 2 aberto.
- **Aceite:**
  - [ ] Na jornada 9:16, `data.decode.windowed` e `data.proxy` são os esperados, a preview com proxy sai em 404x720, a aprovação vincula os originais e uma mutação do `reframe` bloqueia a full.
  - [ ] `capabilities --json` contém as quatro proporções, as dimensões e os limites do `reframe`, com os mesmos valores das constantes.
  - [ ] A validação da skill passa e o texto cobre vertical, keyframes e zonas seguras.
  - [ ] A suíte inteira, o E2E, `gitleaks` e `pip_audit` passam.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.e2e.test_phase_5_flow tests.test_capabilities tests.test_vibe_editor_skill -v`
  - [ ] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  - [ ] `gitleaks detect --source . --no-banner`
  - [ ] `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip`
- **Deps:** T2, T3
- **Arquivos:** `tests/e2e/test_phase_5_flow.py`, `src/editor/capabilities.py`, `tests/test_capabilities.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/youtube-editing.md`, `tests/test_vibe_editor_skill.py`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md`
- **Risco:** a skill, `test_capabilities.py`, `test_vibe_editor_skill.py` e `REGRAS.md` têm alterações não commitadas anteriores a esta phase; a task edita sobre o estado atual sem descartá-las e o commit da T4 deve separar ou declarar o que é anterior.

## Paralelização

- Grupo: T2 e T3
- Motivo: ambas dependem só da T1 e entregam resultados independentes (reenquadramento do vídeo e escala do texto).
- Isolamento: as duas editam `src/editor/render/ffmpeg_graph.py` em funções distintas (`_video_segment_filter` e a camada de texto); executar em paralelo exige worktrees separados e merge da T3 sobre a T2. Em caso de dúvida, rodar em sequência.

## Handoff

vibe-implement

- Chat: recomendo novo chat para `vibe-implement`, que roda em Modo A e conduz a fila inteira com uma confirmação final. No Modo B (uma T* por run), um chat por T*; o grupo T2 e T3 é a exceção coordenada. Continuar no chat atual também é válido; o `plan.md` vivo é a ponte.
