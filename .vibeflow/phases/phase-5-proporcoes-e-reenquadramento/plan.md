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

- [x] T4 concluída
- **Spec:** A7, A8, C1, C3, C4
- **O quê:** um plano 9:16 com `reframe` e texto percorre validação, preview com e sem proxy, aprovação, bloqueio por mutação e full com janelas de decode; `capabilities` expõe proporções, dimensões de preview e full e limites do `reframe` lidos das constantes da T1 e T2; a skill `vibe-editor` documenta a escolha da proporção, a marcação de keyframes pelos frames extraídos, a preferência por `reframe` sobre `motion` e as zonas seguras do Shorts e do Reels; `REGRAS.md` registra a ferramenta, as limitações (`ponytail`) e o mapa de testes; o registro em `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md` marca o Problema 1 como resolvido e mantém o Problema 2 aberto.
- **Aceite:**
  - [x] Na jornada 9:16, `data.decode.windowed` e `data.proxy` são os esperados, a preview com proxy sai em 404x720, a aprovação vincula os originais e uma mutação do `reframe` bloqueia a full.
  - [x] `capabilities --json` contém as quatro proporções, as dimensões e os limites do `reframe`, com os mesmos valores das constantes.
  - [x] A validação da skill passa e o texto cobre vertical, keyframes e zonas seguras.
  - [x] A suíte inteira, o E2E, `gitleaks` e `pip_audit` passam.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.e2e.test_phase_5_flow tests.test_capabilities tests.test_vibe_editor_skill -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  - [x] `gitleaks detect --source . --no-banner`
  - [x] `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip`
- **Deps:** T2, T3
- **Arquivos:** `tests/e2e/test_phase_5_flow.py`, `src/editor/capabilities.py`, `tests/test_capabilities.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/youtube-editing.md`, `tests/test_vibe_editor_skill.py`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md`
- **Risco:** a skill, `test_capabilities.py`, `test_vibe_editor_skill.py` e `REGRAS.md` têm alterações não commitadas anteriores a esta phase; a task edita sobre o estado atual sem descartá-las e o commit da T4 deve separar ou declarar o que é anterior.
- **Prova:** `tests.e2e.test_phase_5_flow tests.test_capabilities tests.test_vibe_editor_skill` -> 30 testes OK; suíte completa (`discover -s tests`, com E2E e as provas das T1 a T3) -> 236 testes OK em 224 s; `gitleaks detect --source . --no-banner` -> 61 commits, no leaks found (e `--no-git` sobre tests, skills, src, .vibeflow e .erros-encontrados, repetido pelo coordenador, sem achados); `pip_audit -r requirements.lock --no-deps --disable-pip` -> No known vulnerabilities found; `quick_validate.py skillsibe-editor` -> Skill is valid. E2E 9:16 (fonte sintética 1920x1080 de 12 s, 2 trechos `crop` com keyframes `ease` e `linear` e foco estático, 1 texto): `plan validate` com perfil 1080x1920, `upscale` [2.67, 1.78] e `reframe` normalizado; preview sem proxy; `proxy build` 1280x720; preview com proxy em 404x720 com `data.proxy` = auto, 1 fonte, sem `stale`, `data.decode.windowed` verdadeiro; `approve` vincula o SHA-256 do original e não o do proxy; mutação de um `x` do `reframe` muda o hash e a full é bloqueada com `APPROVAL_STALE` (sem `full.mp4` e com `approval.json` intacto); full 1080x1920 H.264/AAC com janelas (`windowed` verdadeiro, 2 entradas, sem `data.proxy`) feita com o proxy deliberadamente corrompido, o que prova que a full não lê proxy.
- **Arquivos (efetivos):** `tests/e2e/test_phase_5_flow.py` (novo), `src/editor/capabilities.py`, `tests/test_capabilities.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/youtube-editing.md`, `skills/vibe-editor/references/source-preparation.md` (fora da lista planejada: três frases que ficariam falsas com o novo contrato), `tests/test_vibe_editor_skill.py`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md`. O diff anterior à phase nesses arquivos foi commitado à parte (`d89573d`), então o commit da T4 contém só o diff da task.
- **Decisões de implementação:** `capabilities` expõe `plan.profile.aspect_ratios`, `max_full_width_applies_to`, `canvas` (full e preview de cada proporção), `envelope_fields`, `envelope_fields_landscape_only` e `upscale`, `plan.reframe` (campos, limites, padrões, `requires_fit: crop`, `excludes: [motion]`), `limits.reframe_*` e `render.text_size`, tudo das constantes de `plan/schema.py` e `MAX_PREVIEW_DIMENSION`; `plan.profile.aspect_ratio` permanece `"16:9"` por compatibilidade (nada removido nem renomeado). Duas asserções existentes mudaram com justificativa no A8: a igualdade exata de `plan.profile` em `tests/test_capabilities.py` virou igualdade sobre as cinco chaves legadas, e a de `size_px não é escalado` em `tests/test_vibe_editor_skill.py` virou `size_px vale na largura da full`. A tabela "Decisões vigentes" do `REGRAS.md` não foi alterada: é sincronizada na finalização aprovada, depois da review.
- **Limitação registrada:** os literais `segment_base: 1`, `factor_decimals: 2` e `factor_greater_than: 1.0` de `upscale` em `capabilities.py` não vêm de constante compartilhada com `validation.py` e são provados pelo E2E, e não por constante; `installation.md` não cita proporções nem `reframe` (opcional).

### Adendo (08/10/2026): resolução dos achados da review e dos registros de `.erros-encontrados/`

Pedido do humano depois da review final: resolver R1 a R4 (rodada de correção, passo C, commit `task(R1-R4)`) e os registros abertos de `.erros-encontrados/` (T5 a T9, um commit por tarefa). Decisões 15 a 20 e aceites A9 a A15 no adendo da [spec](spec.md). Execução sequencial (Modo A). Prontidão: `libx265` e `hevc_cuvid` existem no FFmpeg local, então as fontes HEVC 10 bits dos testes são geradas por `ffmpeg -f lavfi` sem fixture grande; GPU é opcional nos testes (pula com mensagem quando ausente).

### Rodada de correção R1 a R4 (concluída)

- **Origem:** achados Nit e Optional da review final, aplicados a pedido do humano (corretor, passo C, commit `task(R1-R4)`).
- **R1 (decisão 15):** `_upscale_factor` (`plan/validation.py`) multiplica o fator do enquadramento pelo maior `zoom` entre `motion.from` e `motion.to`, em qualquer `fit`; 5 testes novos em `UpscaleTests` (`tests/test_reframe.py`): `crop` e `contain` com `motion` ampliando, `motion` com zoom menor que 1, sem `motion` inalterado e `reframe` inalterado. Nenhuma expectativa existente mudou.
- **R2:** a frase de `upscale` foi alinhada em `youtube-editing.md`, `SKILL.md`, comentário de `capabilities.py` e `REGRAS.md` (recorte do maior zoom no `crop`, escala da imagem inteira em `contain` e `preserve`, multiplicada pelo zoom do `motion`; lista vazia significa que ninguém é ampliado).
- **R3:** o registro do teto de 32.767 em `.erros-encontrados/` está marcado como resolvido (`05dd8eb` e `21aeea2`).
- **R4:** `canal-sonni-viaja.md` passou a dizer "sem `video.motion` e sem `video.reframe`".
- **Prova:** `tests.test_reframe tests.test_aspect_ratio tests.test_plan_validation tests.test_capabilities tests.test_vibe_editor_skill` -> 106 testes OK; suíte completa (`discover -s tests`) -> 241 testes OK em 214 s; `git diff --check` limpo.
- **Arquivos:** `src/editor/plan/validation.py`, `src/editor/capabilities.py` (só comentário), `tests/test_reframe.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/youtube-editing.md`, `skills/vibe-editor/references/canal-sonni-viaja.md`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-teto-32767-e-da-linha-de-comando-e-nao-do-grafo.md`.

### T5: Alinhar a largura da full 16:9 a 32

- [x] T5 concluída
- **Spec:** A9, decisão 16
- **O quê:** `canvas_dimensions` (`plan/schema.py`) mantém o canvas legado da full 16:9 quando a checagem do MP4 o aceita e usa largura alinhada a 32 com altura exata só onde o legado seria recusado (causa raiz: altura `largura * 9 // 16` ímpar reduzida em 1 deixa a razão fora da tolerância); a tolerância 0,001 vira constante única em `schema.py`, também usada por `_validate_rendered_preview` (`render/preview.py`); `capabilities` descreve a regra real; atualizar o texto de `REGRAS.md` e o comentário de `render/full.py`, e marcar o registro `2026-10-08-full-16x9-falha-com-largura-multipla-impar-de-16.md` como resolvido.
- **Aceite:**
  - [x] Fonte H.264 16:9 de 1200x674 (`fit: crop`, perfil padrão): `plan validate` informa `full_width` 1184 e `full_height` 666, `render preview`, `approve` e `render full` funcionam e a full sai em 1184x666.
  - [x] Para toda largura de fonte de 2 a 3840 (e todo `max_full_width` válido), o canvas novo é idêntico ao anterior sempre que o anterior era renderizável pela checagem real do MP4 (inclui as 21 larguras 3184 a 3824), e, onde o anterior era recusado, tem largura múltipla de 32, altura par e razão exata `largura * 9 == altura * 16`; o golden de `tests/test_aspect_ratio.py` e as fixtures vigentes não mudam.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_aspect_ratio tests.test_capabilities -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render tests.test_full_render tests.test_decode_windows -v`
- **Deps:** nenhuma
- **Arquivos:** `src/editor/plan/schema.py`, `src/editor/render/preview.py` (só a troca do literal da tolerância), `src/editor/render/full.py` (só comentário), `src/editor/capabilities.py` (se precisar descrever a regra), `tests/test_aspect_ratio.py`, `tests/test_capabilities.py`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-full-16x9-falha-com-largura-multipla-impar-de-16.md`
- **Prova:** `tests.test_aspect_ratio tests.test_capabilities` -> 44 testes OK; `tests.test_plan_validation tests.test_preview_render tests.test_full_render tests.test_decode_windows` -> 75 testes OK; suíte completa (`discover -s tests`) -> 248 testes OK em 237 s. Falha anterior reproduzida contra o código sem edição: fonte H.264/AAC 1200x674, `fit: crop`, perfil padrão -> `plan validate` 1200x674, `render full` falha com `RENDER_FAILED` ("A full renderizada não respeita o perfil final 16:9."). Depois: `plan validate` 1184x666, preview 704x396, `approve` e full 1184x666 H.264/AAC (FFprobe), `1184*9 == 666*16`, `data.profile` igual ao canvas renderizado, também pela CLI real; fonte real 3184x1790 (`max_full_width` 3200) continua saindo em 3184x1790. Tabela de equivalência com oráculo independente da fórmula antiga e das checagens reais do render: toda largura de fonte de 2 a 3840 x os 120 tetos válidos de `max_full_width` (460.680 pares): canvas IDÊNTICO ao legado em todos os 346.536 renderizáveis (largura múltipla de 32 e as 21 larguras 3184 a 3824), e nos 110.544 recusados o novo tem largura múltipla de 32, altura par e razão exata (sempre o legado menos 16 px); fonte menor que 32 px segue recusada como na preview. Golden do `filter_complex` 16:9 e fixtures vigentes inalterados.
- **Arquivos (efetivos):** `src/editor/plan/schema.py`, `src/editor/render/preview.py` (só a troca do literal da tolerância), `src/editor/render/full.py` (só comentário), `src/editor/capabilities.py` (campo aditivo `width_alignment_fallback` e import), `tests/test_aspect_ratio.py`, `tests/test_capabilities.py`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-full-16x9-falha-com-largura-multipla-impar-de-16.md`.
- **Decisões de implementação:** a primeira versão (alinhar toda a full a 32) mudaria 16 px em 21 larguras (3184 a 3824) cuja razão já passava na tolerância, o que contraria a decisão 14 e o aceite A6; a decisão 16 e o aceite foram reescritos e a regra final é: canvas legado (alinhado a 16) mantido quando a checagem do MP4 o aceita, e alinhamento 32 com altura exata só onde o legado seria recusado. `LANDSCAPE_RATIO_TOLERANCE` (0.001) é constante única usada por `canvas_dimensions` e por `_validate_rendered_preview`; `FULL_WIDTH_ALIGNMENT` segue 16 e `FULL_FALLBACK_WIDTH_ALIGNMENT` é 32.
- **Limitação registrada (`ponytail`):** a regra legada da full 16:9 é mantida (deixa a razão até 0,001 fora do 16:9 exato em larguras como 3184x1790); unificar em largura múltipla de 32 numa versão major. A comparação da tolerância existe em dois pontos (cálculo do canvas e checagem do MP4), ambos pela mesma constante, e a tabela de equivalência falha se divergirem.

### T6: Áudio idêntico com e sem janelas de decode

- [x] T6 concluída
- **Spec:** A10, decisão 17
- **O quê:** achar a causa raiz do deslocamento de até 1.024 amostras quando o `-ss` da janela cai num keyframe de vídeo (`atrim` seguido de `asetpts=PTS-STARTPTS` das entradas de áudio com janela, `render/ffmpeg_graph.py`, `render/windows.py`) e corrigi-la uma vez na origem; estender `tests/test_decode_windows.py` com a prova de PCM; manter a afirmação do `REGRAS.md` verdadeira e marcar o registro `2026-10-08-janelas-de-decode-deslocam-audio-em-keyframe-de-video.md` como resolvido.
- **Aceite:**
  - [x] Fonte com GOP 25 e cortes em 1,0 s e 8,0 s (mais cortes alinhados e não alinhados a keyframe, várias janelas, corte J/L): o PCM decodificado da saída com janelas é idêntico, amostra a amostra, ao sem janelas; com GOP 12 continua idêntico.
  - [x] O `framemd5` de vídeo com e sem janelas continua igual; o golden do grafo 16:9 sem janelas não muda.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_decode_windows -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_preview_render tests.test_full_render tests.test_aspect_ratio tests.test_reframe -v`
- **Deps:** nenhuma
- **Arquivos:** `src/editor/render/ffmpeg_graph.py`, `src/editor/render/windows.py` (se a causa estiver no planejador), `tests/test_decode_windows.py`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-janelas-de-decode-deslocam-audio-em-keyframe-de-video.md`
- **Risco:** a correção não pode mudar os tempos do plano nem o hash canônico; o áudio sem janelas é a referência e não deve mudar.
- **Prova:** `tests.test_decode_windows` -> 18 testes OK; `tests.test_preview_render tests.test_full_render tests.test_aspect_ratio tests.test_reframe` -> 85 testes OK (golden do grafo 16:9 sem janelas inalterado); suíte completa (`discover -s tests`) -> 252 testes OK em 279 s. Defeito reproduzido antes de editar (fonte do E2E, `-g 25 -preset ultrafast`, cortes 1,0-4,0 e 8,0-11,0): 286.976 amostras com janelas contra 288.000 sem; também a 44,1 kHz (287.478 contra 288.000). Os novos testes de PCM falham sem a correção (19 verificações: 18 subcasos e a full, caso do defeito `286976 != 288000`) e passam com ela. `WindowedAudioPcmTests` compara o PCM `s16le` amostra a amostra da preview real com e sem janelas, mais o `framemd5` de vídeo e áudio da saída e do grafo, em 3 fontes (GOP 25 sem B-frames a 48 kHz e a 44,1 kHz, GOP 12 com B-frames) e 11 cenários (cortes alinhados e não alinhados, a 0,01 e 0,02 s do keyframe, cinco janelas, janela no início e perto do início, trilha com `in_s`/`out_s`, `loop`, J/L, camada de vídeo), e a full do caso do defeito (288.000 amostras exatas). Varredura descartável de 256 cortes próximos de keyframes em 4 fontes: 0 divergências.
- **Causa raiz:** depois de um seek de entrada o demuxer MP4 começa o áudio no pacote que contém o instante do keyframe de vídeo e o decoder AAC descarta o primeiro quadro (1.024 amostras); com B-frames o `dts` do keyframe é anterior ao `pts` e a sobra cobre o corte, mas sem B-frames (`-preset ultrafast`, `-bf 0`) o início do áudio nunca chega ao grafo e o `asetpts=PTS-STARTPTS` esconde a lacuna adiantando a trilha. O gatilho não é o GOP 25 em si. A janela que começa em 0 também perdia o primeiro quadro AAC com `-ss 0`.
- **Arquivos (efetivos):** `src/editor/render/windows.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/render/preview.py`, `tests/test_decode_windows.py`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-janelas-de-decode-deslocam-audio-em-keyframe-de-video.md`.
- **Decisões de implementação:** `DecodeWindow` ganhou `seek_s` (`start_s` menos `AUDIO_PREROLL_S` de 0,25 s, 6 casas, 0 no início da fonte) e `read_duration_s`; o comando de entrada (preview e full) emite `-ss seek_s` só quando maior que 0 e `-t read_duration_s`, e `_GraphInputs.locate` subtrai o mesmo `seek_s` dos recortes de vídeo e de áudio (fonte única do deslocamento); o planejador (`duration_s`, estimativa de ganho) não muda; tempos do plano, hash canônico, grafo sem janelas e áudio sem janelas não mudam. A frase do `REGRAS.md` agora diz como a igualdade é provada (`framemd5` do grafo e PCM da preview e da full) e cita o pré-roll.
- **Limitação registrada (`ponytail`):** a estimativa de ganho de 20% e o teto de leitura ignoram o pré-roll de até 0,25 s por janela; somar o recuo à estimativa se o limiar passar a decidir casos de fronteira reais. `scripts/bench_render.py` não foi rodado de novo (as medições da phase 4 são anteriores); o teste de PCM acrescenta cerca de 35 s à suíte.

### T7: Sombra e contorno na camada de texto

- [x] T7 concluída
- **Spec:** A12, decisão 18
- **O quê:** `layers[].shadow` e `layers[].outline` opcionais na camada `text` (allowlist estrita, faixas e padrões da decisão 18, normalização e hash canônico), renderizados pelo mesmo grafo da preview e da full: contorno por `drawtext` (`borderw`/`bordercolor`) e sombra suave (deslocamento, desfoque e opacidade) composta atrás do texto na superfície da camada; valores em pixels definidos na largura da full e escalados na preview como o `size_px`; sem `shadow` e sem `outline` o filtro do texto é byte a byte o atual. Atualizar `capabilities`, a skill (remover "o texto não tem sombra, contorno nem fundo" mantendo "sem caixa de fundo" como limitação), `REGRAS.md` e marcar o Problema 2 do registro `2026-10-08-size-px-nao-escala-entre-preview-e-full.md` como resolvido.
- **Aceite:**
  - [x] Cada violação (campo desconhecido, tipo errado, booleano como número, faixa, cor inválida, `shadow` ou `outline` em camada que não é `text`) é recusada com `PLAN_INVALID` antes de inspecionar mídia; plano válido muda o hash canônico; padrões aplicados na normalização.
  - [x] Em preview e full reais, o contorno tem a cor e a espessura esperadas ao redor das letras, a sombra aparece deslocada para baixo e à direita com a cor e a opacidade declaradas e some com `opacity` 0, e a razão sombra/largura do quadro é a mesma na preview e na full (tolerância de 2% da largura); sem `shadow` e `outline`, o grafo de texto e o hash do `filter_complex` da full de `TextScaleTests` não mudam.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_text_style -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render tests.test_full_render tests.test_aspect_ratio tests.test_capabilities tests.test_vibe_editor_skill -v`
- **Deps:** nenhuma
- **Arquivos:** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/capabilities.py`, `tests/test_text_style.py`, `tests/test_capabilities.py`, `tests/test_vibe_editor_skill.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/youtube-editing.md`, `skills/vibe-editor/references/canal-sonni-viaja.md`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md`
- **Risco:** `drawtext` não faz desfoque; o desfoque exige compor uma cópia do texto desfocada atrás da nítida, sujeita ao recorte da caixa da camada (`ponytail`).
- **Prova:** `tests.test_text_style` -> 20 testes OK; `tests.test_plan_validation tests.test_preview_render tests.test_full_render tests.test_aspect_ratio tests.test_capabilities tests.test_vibe_editor_skill` -> 122 testes OK; suíte completa (`discover -s tests`) -> 277 testes OK em 241 s; `gitleaks detect --no-git` em src, tests, skills e .vibeflow sem achados (um falso positivo inicial por nome de variável `secret` em teste foi renomeado); `quick_validate.py` da skill válido. Validação: 50 casos em tabela, todos `PLAN_INVALID` antes de inspecionar mídia (espião em `inspect_source`, sem fonte em disco), incluindo campo desconhecido, tipo errado, booleano, decimal, faixa, cor inválida, obrigatório ausente, objeto vazio, `null`/lista/texto no lugar do objeto e `shadow`/`outline` em camada `image` e `video`; a mensagem não ecoa o valor recebido; o hash difere nos 4 planos comparados. Render real em quadros brutos (letreiros "I" em Arial Bold sobre fundo claro): 16:9 (preview 704x396, full 1280x720) contorno 12/11/11 px na preview (esperado 11) e 20/20/20 na full, cor (253,0,0) e (254,0,3); sombra direita/baixo/esquerda 11/9/0 na preview e 20/16/0 na full; gradiente de `blur_px` 20 com 14 px na preview e 24 na full contra 1 e 0 px da sombra nítida; `opacity` 0 sem sombra. 9:16 (preview 404x720, full 1080x1920): contorno 7/7/7 e 20/20/20, sombra 7/6/0 e 20/16/0. Razão deslocamento/largura do quadro entre preview e full dentro de 0,5% (tolerância 2%). Mutações do código (sem escala na preview, sombra sem contorno, opacidade ignorada, sinal do offset invertido, sem `gblur`, `trim` atrasado) reprovam os testes correspondentes. Compatibilidade: sem `shadow` e `outline` o `filter_complex` da full do `workflow` com texto (1280x720) bate com o golden e `TextScaleTests` e o golden 16:9 seguem verdes. `--accel gpu` renderiza com sombra.
- **Arquivos (efetivos):** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/capabilities.py`, `tests/test_text_style.py` (novo), `tests/test_capabilities.py`, `tests/test_vibe_editor_skill.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/youtube-editing.md`, `skills/vibe-editor/references/canal-sonni-viaja.md`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-08-size-px-nao-escala-entre-preview-e-full.md`.
- **Decisões de implementação:** constantes em `plan/schema.py` (`SHADOW_*`, `OUTLINE_*`, padrões) lidas por validação, grafo e `capabilities` (`plan.layers.text_style`); estilo normalizado por índice de camada em `validation["_text_styles"]` (privado, o envelope público não ganha campo); o hash continua o do documento escrito, como no `reframe`. A sombra é uma segunda superfície RGBA na cor da sombra (alfa 0) com o texto opaco deslocado e com o mesmo contorno, depois `trim` (limita o desfoque ao intervalo da camada, o que cortou um caso de 10 s de 12 s de relógio para 3,5 s), `gblur=sigma=blur_px/2` (`SHADOW_SIGMA_PER_BLUR_PX`, raio no sentido do CSS), `colorchannelmixer=aa=opacity` (a opacidade entra uma vez, depois do desfoque) e `overlay=format=auto` atrás do texto nítido, mantendo o rótulo `textlayerN`. A escala da preview reutiliza `_scaled_font_size` (`_scaled_style_pixels`: magnitude mínima 1, sinal e zero preservados). Exemplo mínimo na skill validado pelo validador real; valores de partida do canal (full 2688: offset 4/4, blur 6, opacity 0.5, 1.0 em fundo claro) rotulados como sugestão.
- **Limitações registradas (`ponytail`):** sombra e contorno são recortados pela caixa da camada e o texto parte do canto superior esquerdo, então o contorno da primeira linha encosta no topo (dar folga à caixa); custo da sombra de cerca de 30 ms de CPU por quadro do intervalo da camada; caixa de fundo não implementada (sem demanda registrada). O Problema 2 do registro de `size-px` está marcado como resolvido e o registro inteiro está resolvido.

### T8: Fontes HEVC e MOV nativas

- [ ] T8 concluída
- **Spec:** A13, decisão 19
- **O quê:** aceitar `hevc` (Main e Main 10, `yuv420p` e `yuv420p10le`) e a extensão `.mov` nas fontes audiovisuais, ignorar streams de dados e miniatura anexada que não são o stream selecionado, e garantir que todo consumidor da fonte (análise de áudio, extração de frames, `plan validate`, `proxy build`, preview, full, janelas de decode, `--accel gpu`) mapeie explicitamente o stream de vídeo e o de áudio escolhidos e converta 10 bits para `yuv420p` de 8 bits; atualizar `capabilities` (`formats`), `source-preparation.md`, a skill e `REGRAS.md`; marcar o registro `2026-10-05-fontes-dji-hevc-rejeitadas.md` como resolvido (a preparação continua documentada para MKV e fontes com mais de um áudio).
- **Aceite:**
  - [ ] Fontes sintéticas geradas no teste (`libx265` 10 bits em `.mov` e em `.mp4`, com stream `data` e miniatura MJPEG anexada, vídeo 2688x1512 curto e uma de 1080p) percorrem `analyze audio`, `extract frames`, `plan validate`, `proxy build` (proxy H.264 8 bits), preview com e sem proxy, aprovação e full (com janelas de decode), com FFprobe das saídas H.264 `yuv420p`; os frames convertidos têm a cor correta (comparação com a fonte dentro de tolerância); `--accel gpu` renderiza ou cai para CPU sem falhar.
  - [ ] Continuam recusadas com o código estável: `.mkv`, dois streams de áudio, dois streams de vídeo reais, miniatura anexada como primeiro stream de vídeo, codec fora da allowlist (por exemplo `vp9`, `av1`), legenda; os tetos de tamanho e duração valem.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_hevc_sources -v`
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_audio_analysis tests.test_frame_extraction tests.test_plan_validation tests.test_proxy_build tests.test_preview_proxy tests.test_decode_windows tests.test_gpu_accel tests.test_capabilities tests.test_vibe_editor_skill -v`
- **Deps:** nenhuma
- **Arquivos:** `src/editor/media.py`, `src/editor/render/preview.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/render/proxy.py`, `src/editor/analysis/audio.py`, `src/editor/analysis/frames.py`, `src/editor/capabilities.py`, `src/editor/dependencies.py` (só se o decode CUDA exigir), `tests/test_hevc_sources.py`, `tests/test_capabilities.py`, `tests/test_vibe_editor_skill.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/source-preparation.md`, `skills/vibe-editor/references/canal-sonni-viaja.md`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-05-fontes-dji-hevc-rejeitadas.md`
- **Risco:** superfície de entrada não confiável maior (demux e decode de HEVC); a allowlist segue estrita, e o plano e a CLI continuam sem aceitar nada fora dela. O stream escolhido precisa ser mapeado pelo índice real do FFprobe, nunca por `v:0` quando há miniatura anexada.

### T9: Preset de qualidade da full

- [ ] T9 concluída
- **Spec:** A14, decisão 20
- **O quê:** `render full --quality standard|high|max` (e o atalho `full`), padrão `standard` com os argumentos atuais de encoder (x264 `crf 23`, NVENC `p3`/`cq 31`); `high` e `max` com `crf` e `cq` equivalentes medidos por SSIM no mesmo material (o comentário atual de `_video_encoder_arguments` registra a equivalência `cq 31` ~ `crf 23`); `data.video.quality` no envelope; a preview não recebe a opção; a qualidade não entra em aprovação nem em hash; `capabilities` expõe `render.quality` (presets e parâmetros); skill e `REGRAS.md` documentam; marcar a sugestão de preset do registro `2026-10-05-fontes-dji-hevc-rejeitadas.md` como atendida.
- **Aceite:**
  - [ ] `--quality standard` (e a omissão) gera exatamente a full de antes (`framemd5` de vídeo e áudio iguais) na CPU e o mesmo comando de encoder na GPU; `high` e `max` geram arquivos de qualidade crescente com SSIM e tamanho medidos e registrados; valor fora da allowlist é recusado com `ARGUMENT_INVALID`.
  - [ ] `capabilities --json` informa os presets lidos das mesmas constantes que o render usa; a preview, a aprovação e o hash não mudam.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_render_quality -v`
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_full_render tests.test_gpu_accel tests.test_cli_contract tests.test_capabilities tests.test_vibe_editor_skill -v`
- **Deps:** T8
- **Arquivos:** `src/editor/render/preview.py`, `src/editor/render/full.py`, `src/editor/cli.py`, `src/editor/capabilities.py`, `tests/test_render_quality.py`, `tests/test_capabilities.py`, `tests/test_vibe_editor_skill.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/youtube-editing.md`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-10-05-fontes-dji-hevc-rejeitadas.md`

## Paralelização

- Grupo: T2 e T3
- Motivo: ambas dependem só da T1 e entregam resultados independentes (reenquadramento do vídeo e escala do texto).
- Isolamento: as duas editam `src/editor/render/ffmpeg_graph.py` em funções distintas (`_video_segment_filter` e a camada de texto); executar em paralelo exige worktrees separados e merge da T3 sobre a T2. Em caso de dúvida, rodar em sequência.

## Handoff

vibe-implement

- Chat: recomendo novo chat para `vibe-implement`, que roda em Modo A e conduz a fila inteira com uma confirmação final. No Modo B (uma T* por run), um chat por T*; o grupo T2 e T3 é a exceção coordenada. Continuar no chat atual também é válido; o `plan.md` vivo é a ponte.
