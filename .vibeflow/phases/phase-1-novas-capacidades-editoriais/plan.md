# Plan: Novas capacidades editoriais e preparação de trabalhos
# Alvo: phase-1-novas-capacidades-editoriais
# Status: aprovado
# Spec: spec.md (mesma pasta)

## Overview

Implementar o contrato v2 do plano e as cinco capacidades da spec, começando pela preparação segura com `editor init`, passando pela validação estrita, movimento, áudio J/L e camadas visuais, e terminando com aprovação, full, capacidades, documentação e uma prova E2E combinada. Cada fatia entrega comportamento executável e uma verificação real, preservando a execução local, determinística, sem rede e sem compatibilidade paralela com o schema v1.

## Ordem

### Fase 1: Fundação e contrato

- T1 prepara um trabalho com `editor init` e valida o ponto de entrada real.
- T2 adota o schema v2 estrito e migra o fixture linear compartilhado.

### Fase 2: Timeline e composição

- T3 adiciona movimento determinístico aos segmentos de vídeo.
- T4 monta a linha global de áudio para cortes J/L.
- T5 compõe texto em caixas normalizadas.
- T6 compõe imagens raster com alpha e z-order.
- T7 compõe vídeos em camadas sem áudio implícito.

### Fase 3: Estado e fechamento operacional

- T8 vincula preview, aprovação e full ao plano v2 e a toda mídia usada.
- T9 atualiza capacidades, documentação da skill e o Documento Vivo.
- T10 prova a jornada E2E da phase e executa os gates finais.

## Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Quebra controlada do plano v1 | alto | T2 rejeita v1 e `overlays` na fronteira única de validação e migra somente fixtures internos para v2. |
| Complexidade do grafo FFmpeg compartilhado | alto | T3 a T7 reutilizam um único grafo validado, com fixtures reais e amostras de frames, áudio e metadados. |
| Imagens com formato real ou alpha divergente da extensão | médio | T6 valida extensão, formato, tamanho e dimensões reais antes do render e testa PNG, JPEG/JPG e WebP. |
| Caminhos linkados, junctions ou falha parcial no `init` | alto | T1 valida todos os componentes antes de criar subpastas, preserva estado existente e testa rollback limitado a diretórios vazios criados pela execução. |
| Aprovação liberando composição diferente da preview | alto | T8 mantém hash canônico do plano, preview e todas as fontes, incluindo camadas, e testa mutações independentes. |
| Gate local mascarado por pré-requisito ausente | médio | T10 exige a suíte sem `SkipTest`, `pip-audit`, gitleaks e `git diff --check`; as ferramentas essenciais já foram validadas no ambiente. |

## Paralelização

- Paralelo ok: preparação de casos de teste e fixtures de T3 a T7 após T2, desde que a integração nos arquivos compartilhados seja aplicada em ordem.
- Sequencial: T1 estabelece o smoke test; T2 fecha a fronteira v2; T3 → T4 → T5 → T6 → T7 evolui o validador e o grafo sem duas interpretações concorrentes.
- Contrato primeiro, depois paralelo: T8 só entra após a composição v2 completa; T9 pode ser preparado em paralelo com T8, mas é validado contra o contrato final; T10 fecha a integração e os gates.

## Tasks

### T1: Preparar trabalho seguro com `editor init`

- [x] T1 concluída
- **Spec:** A1, A2, C1, C4
- **Decisões:** INIT-01 (cria a estrutura técnica idempotente sem estado editorial)
- **O quê:** Adicionar o comando `editor init --workdir <caminho-absoluto> --json` ao parser e ao handler central, reutilizando o envelope e as validações de caminho existentes. Criar ou completar apenas `.tmp`, `analysis/audio`, `analysis/transcription` e `frames`, preservando artefatos e removendo, em falha, somente diretórios vazios criados pela própria execução.
- **Aceite:**
  - [x] Um processo real cria a estrutura na ordem declarada, retorna `command: "init"`, `state: "ready"`, `created`, `existing` e `preserved_artifacts`, e uma segunda execução é idempotente.
  - [x] Caminho relativo, arquivo, link, junction, reparse point, pai inseguro ou destino não gravável retornam `WORKDIR_INVALID` em envelope seguro antes de criar estado editorial.
  - [x] `plan.json`, `approval.json`, `preview.mp4`, `full.mp4`, análises, frames e arquivos preexistentes permanecem byte a byte; `sources` não é criado.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_init tests.test_cli_contract -v`
- **Prova:** `.venv\Scripts\python.exe -B -m unittest tests.test_init tests.test_cli_contract -v` -> `Ran 10 tests ... OK`; suíte completa -> `Ran 74 tests ... OK`
- **Arquivos:** `src/editor/cli.py`, `src/editor/workdir.py`, `tests/test_init.py`, `tests/test_cli_contract.py`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/analyze.md`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/spec.md`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/implement.md`
- **Deps:** nenhuma
- **Size:** `medium, 6/10, mudança de CLI e workdir com validação de processo no Windows e rollback local`
- **Risk:** `medium, manipula criação de diretórios e precisa preservar estado operacional existente`

### T2: Adotar contrato estrutural v2 estrito

- [x] T2 concluída
- **Spec:** A3, A7, A10, C6
- **Decisões:** CONTRACT-01 (substitui o plano vigente por `schema_version: 2`), TEXT-01 (remove posições nomeadas do contrato v2)
- **O quê:** Substituir a allowlist v1 por `schema_version`, `profile`, `sources`, `segments`, `cuts`, `layers` e `audio_processing`, mantendo `cuts` e `layers` explícitos e rejeitando `overlays`, v1 e campos desconhecidos antes de consultar mídia. Atualizar a versão do pacote e da CLI para o próximo major, `1.0.0`, e migrar o fixture linear e suas expectativas para v2 sem manter fallback. Os artefatos derivados do workflow serão regenerados na T8, junto com o grafo v2, preservando a versão própria de `approval.json`.
- **Aceite:**
  - [ ] Um plano v2 linear válido preserva a timeline e o hash canônico; a resposta expõe `cut_count` e `layer_count`, sem `overlay_count`.
  - [ ] Plano v1, `overlays`, posições nomeadas, campos desconhecidos, números não finitos e listas ausentes ou inválidas falham como `PLAN_INVALID` antes do FFprobe quando a falha for estrutural.
  - [ ] `pyproject.toml`, `CLI_VERSION`, testes e fixtures do plano deixam de declarar v1 como contrato vigente, enquanto o schema separado de `approval.json` permanece estável e nenhuma dependência Python nova é adicionada.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_cli_contract tests.test_plan_validation -v`
- **Prova:** `.venv\Scripts\python.exe -B -m unittest tests.test_cli_contract tests.test_plan_validation -v` -> `Ran 21 tests ... OK`; `git diff --check` passou
- **Arquivos:** `pyproject.toml`, `src/editor/contracts.py`, `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `tests/test_cli_contract.py`, `tests/test_capabilities.py`, `tests/test_plan_validation.py`, `tests/fixtures/workflow/plan.json`
- **Deps:** nenhuma
- **Arquivos:** `pyproject.toml`, `src/editor/contracts.py`, `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `tests/test_cli_contract.py`, `tests/test_plan_validation.py`, `tests/fixtures/workflow/plan.json`
- **Size:** `high, 7/10, quebra de contrato compartilhado com migração do validador, CLI e fixture de workflow`
- **Risk:** `high, callers e artefatos internos que ainda usam v1 precisam falhar ou ser migrados explicitamente`

### T3: Interpolar movimento no vídeo-base

- [x] T3 concluída
- **Spec:** A4, C2
- **Decisões:** MOTION-01 (cria estados `from`/`to`, interpolação linear e limites de zoom)
- **O quê:** Validar `video.motion` com estados completos, números finitos e limites declarados, e incorporar ao grafo compartilhado a transformação por tempo local do segmento. O movimento deve manter `fit` e duração separados, limitar o enquadramento em zoom maior ou igual a um e preencher com preto as áreas descobertas em zoom menor que um.
- **Aceite:**
  - [x] Movimento ausente mantém o comportamento estático; movimento presente aceita somente `from` e `to` com `x`, `y` e `zoom` válidos, rejeitando valores fora dos limites antes do FFmpeg.
  - [x] A interpolação é linear por frame, reproduz aproximação, afastamento e deslocamento em frames distintos e não altera a duração nem o canvas 16:9.
  - [x] O teste real da preview demonstra mudança de enquadramento sem remover o vídeo-base e sem deixar saída parcial em caso de falha.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v`
- **Prova:** `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v` -> `Ran 26 tests ... OK`; `git diff --check` passou
- **Deps:** T2
- **Arquivos:** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/render/preview.py`, `src/editor/render/full.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`
- **Size:** `high, 8/10, validação temporal e filtro FFmpeg com prova por frames reais`
- **Risk:** `high, erro de clamp ou interpolação altera o enquadramento de toda a composição`

### T4: Montar áudio global para cortes J/L

- [x] T4 concluída
- **Spec:** A5, C3
- **Decisões:** CUT-01 (cria cortes declarados na fronteira e sobreposição temporal explícita)
- **O quê:** Validar `cuts[]` por fronteira, tipo, duração, adjacência e cobertura de áudio, e substituir a concatenação implícita por grupos de áudio posicionados na timeline global. Os grupos ativos devem ser mixados apenas durante a sobreposição, mantendo ganhos explícitos e sem crossfade, ducking ou ganho automático.
- **Aceite:**
  - [x] J inicia o grupo seguinte em `b - duration_s` e L prolonga o grupo anterior até `b + duration_s`; fronteiras duplicadas, índices inválidos, mute incompatível e cobertura insuficiente falham como `PLAN_INVALID` antes do FFmpeg.
  - [x] A saída mistura os grupos ativos, recorta o áudio para `[0, timeline_duration)`, mantém duração visual exata e permanece sem stream de áudio quando todos os segmentos estão em mute.
  - [x] A prova com fixture real identifica, por janelas de áudio, a antecipação do J, a extensão do L e a ausência de ducking ou crossfade implícito.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v`
- **Deps:** T2
- **Prova:** `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v` -> `Ran 29 tests ... OK`; `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` -> `Ran 87 tests ... OK`; `git diff --check` passou
- **Arquivos:** `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`
- **Size:** `high, 8/10, validação de fronteiras e montagem de áudio global com integração FFmpeg real`
- **Risk:** `high, deslocamento incorreto pode alterar fala, duração ou sincronização de todos os segmentos seguintes`

### T5: Renderizar texto em caixas normalizadas

- [x] T5 concluída
- **Spec:** A6, A7
- **Decisões:** TEXT-01 (usa `x`, `y`, `width` e `height` normalizados com origem superior esquerda), LAYER-01 (preserva ordem da lista)
- **O quê:** Implementar a camada discriminada `type: "text"` com caixa normalizada, fonte, tamanho, cor, alinhamento e quebra de linha. O texto deve ser desenhado somente no intervalo declarado, recortado na caixa quando exceder seus limites e aplicado sobre o vídeo-base sem alterar a timeline.
- **Aceite:**
  - [x] `x`, `y`, `width` e `height` respeitam os limites normalizados, dimensões positivas e a caixa; posições nomeadas ou campos alternativos são rejeitados.
  - [x] Texto, fonte, tamanho, cor, alinhamento e `wrap` obedecem aos limites existentes, com NUL e fonte inválida bloqueados antes da construção do filtro.
  - [x] Preview real confirma posição, intervalo, recorte da caixa e preservação do vídeo-base, e o resultado informa `layer_count`.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v`
- **Prova:** `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v` -> `Ran 31 tests ... OK`; `.venv\Scripts\python.exe -B -m unittest tests.test_full_render -v` -> `Ran 6 tests ... OK`; `git diff --check` passou
- **Deps:** T4
- **Arquivos:** `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/implement.md`
- **Size:** `high, 8/10, camada temporal e espacial integrada ao grafo e verificada em frames reais`
- **Risk:** `medium, erro de expressão ou escape pode deslocar texto ou alterar a sintaxe do filtro`

### T6: Compor imagens raster com transparência

- [x] T6 concluída
- **Spec:** A3, A6, C2
- **Decisões:** LAYER-01 (camadas ordenadas abaixo para cima), MEDIA-02 (allowlist raster, 100 MiB e 8.192 pixels por dimensão)
- **O quê:** Estender a inspeção centralizada de mídia para fontes `kind: "image"` estáticas e validar a camada de imagem com `contain` ou `crop`. Incorporar imagens no grafo com duração controlada, alpha preservado e composição sobre o vídeo-base na ordem declarada, sem criar áudio.
- **Aceite:**
  - [x] PNG, JPEG/JPG e WebP estáticos são aceitos somente quando extensão, formato real, tamanho e dimensões estiverem dentro da allowlist; GIF, SVG, BMP, imagens animadas e limites excedidos falham antes do render.
  - [x] `contain` revela o vídeo-base nas áreas não ocupadas, `crop` preenche a caixa com corte central, alpha de PNG é preservado e a ordem de `layers[]` define o z-order.
  - [x] Fixture real com transparência e sobreposição confirma que a imagem cobre apenas seu intervalo e nunca fornece stream de áudio.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v`
- **Prova:** `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render tests.test_full_render -v` -> `Ran 39 tests ... OK`; fixtures raster reais cobrem alpha, `contain`, `crop`, z-order, tempo, áudio ausente, rejeição de animação e limites; `git diff --check` passou.
- **Deps:** T5
- **Arquivos:** `src/editor/media.py`, `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/render/preview.py`, `src/editor/contracts.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`, `tests/fixtures/phase-1/images/`, `.vibeflow/REGRAS.md`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/implement.md`
- **Size:** `high, 8/10, nova família de mídia e composição alpha com FFmpeg real`
- **Risk:** `high, decodificação fora da allowlist ou perda de alpha quebra segurança e resultado visual`

### T7: Compor vídeo em camada sem áudio implícito

- [x] T7 concluída
- **Spec:** A3, A6, C1
- **Decisões:** LAYER-01 (vídeo em camada permanece acima do vídeo-base e sem áudio editorial implícito)
- **O quê:** Adicionar `type: "video"` às camadas, validando fonte de vídeo, intervalo, duração exata, caixa e `fit`, e compor o clipe no retângulo normalizado durante o intervalo declarado. O áudio da fonte da camada será descartado; áudio editorial continuará vindo apenas de `segments[].audio`.
- **Aceite:**
  - [x] A camada aceita somente fonte `kind: "video"`, `in_s`/`out_s` com duração igual ao intervalo da camada dentro da tolerância, `contain` ou `crop`, sem loop, freeze ou ajuste de velocidade.
  - [x] O vídeo-base continua presente abaixo da camada, a ordem do array permanece determinística e nenhum áudio da camada chega ao output.
  - [x] Intervalo, caixa, fonte incompatível e duração divergente são rejeitados antes do FFmpeg, enquanto a preview real confirma composição temporal e metadados.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v`
- **Prova:** verificação da T7, `35 tests ... OK`; regressão do consumidor full, `6 tests ... OK`. Frames reais confirmam recorte, intervalo, fit, ordem e ausência de áudio da camada.
- **Arquivos executados:** `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`, `.vibeflow/REGRAS.md`, `spec.md`, `plan.md` e `implement.md` desta phase.
- **Deps:** T6
- **Arquivos:** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/render/preview.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`, `tests/fixtures/phase-1/`
- **Size:** `high, 8/10, fonte audiovisual em camada, mapeamento temporal e composição espacial real`
- **Risk:** `high, mapear áudio ou duração da camada por engano altera o contrato editorial`

### T8: Vincular aprovação e full à composição v2

- [x] T8 concluída
- **Spec:** A8, A9, C5
- **Decisões:** STATE-01 (mantém aprovação obrigatória por hash), CONTRACT-01 (hash do plano v2 sem caminho v1)
- **O quê:** Atualizar preview e full para consumir o mesmo grafo v2, retornar `cut_count` e `layer_count`, e manter publicação atômica. Fazer `approve` e `verify_approval` incluírem toda fonte declarada, inclusive imagens e vídeos em camadas, para bloquear qualquer divergência antes de chamar o FFmpeg da full.
- **Aceite:**
  - [x] Preview e full produzem MP4 completo com a mesma semântica de movimento, J/L e camadas, mudando apenas resolução, taxa de quadros e limite próprios de cada artefato.
  - [x] Alterar semanticamente plano, corte, camada, preview, vídeo, áudio ou imagem retorna `APPROVAL_STALE` sem renderizar, substituir a full vigente ou modificar `approval.json`.
  - [x] Falha de FFmpeg ou validação preserva preview/full vigentes e limpa temporários, enquanto full sem aprovação continua bloqueada.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_preview_render tests.test_approval tests.test_full_render -v`
- **Prova:** verificação da T8 -> `Ran 25 tests ... OK`; preview e full reais com texto, imagem e vídeo em camadas, hashes ordenados de toda fonte declarada, mutações de corte/camada/preview/vídeo/áudio/imagem bloqueadas por `APPROVAL_STALE`, falhas preservando artefatos e temporários limpos; fixtures workflow v2 regenerados; `git diff --check` passou.
- **Arquivos executados:** `src/editor/integrity/approval.py`, `tests/test_approval.py`, `tests/test_full_render.py`, `tests/fixtures/workflow/approval.json`, `tests/fixtures/workflow/preview.mp4` e `tests/fixtures/workflow/full.mp4`.
- **Deps:** T7
- **Arquivos:** `src/editor/render/preview.py`, `src/editor/render/full.py`, `src/editor/integrity/approval.py`, `src/editor/integrity/hashes.py`, `tests/test_preview_render.py`, `tests/test_approval.py`, `tests/test_full_render.py`, `tests/fixtures/workflow/preview.mp4`, `tests/fixtures/workflow/full.mp4`, `tests/fixtures/workflow/approval.json`
- **Size:** `high, 8/10, estado aprovado, grafo compartilhado e publicação atômica com integração real`
- **Risk:** `high, falha de integridade pode liberar uma full diferente da preview aprovada`

### T9: Atualizar capacidades e documentação do agente

- [x] T9 concluída
- **Spec:** A10, C6
- **Decisões:** SCOPE-01 (mantém as cinco capacidades no mesmo fluxo documentado), CONTRACT-01 (documenta v2 como contrato vigente)
- **O quê:** Atualizar `capabilities --json`, a skill `vibe-editor`, suas referências e o Documento Vivo para refletir `init`, schema v2, movimento, J/L, camadas, formatos raster e limites. Remover declarações de ausência ou exemplos v1 sem criar dependência nova. Estender `tests/test_capabilities.py` com uma checagem textual dos marcadores v2 obrigatórios e da ausência de declarações legadas em toda a documentação atualizada.
- **Aceite:**
  - [x] O inventário reporta `init`, schema v2, formatos e limites de camadas, cortes, movimento e imagens, mantendo a sondagem local sem efeitos colaterais.
  - [x] A skill orienta `init` antes de um trabalho novo, descreve o plano v2, as camadas e J/L, e mantém aprovação humana explícita antes da full.
  - [x] `REGRAS.md` registra os módulos, fixtures, comandos e limitações novas sem declarar o contrato anterior ou capacidades implementadas como ausentes.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_capabilities -v` -> `Ran 7 tests ... OK`.
  - [x] `python -X utf8 C:\Users\marco\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\vibe-editor` -> `Skill is valid!`.
- **Deps:** T2
- **Prova:** o inventário real publicou `init` sem dependências, schema v2, formatos raster `png/jpg/jpeg/webp`, camadas `text/image/video`, cortes `j/l`, movimento linear, limites de `zoom`, cortes e imagens, sem alterar arquivos. A checagem textual cobriu os quatro documentos operacionais e rejeita o contrato anterior; a validação estrutural da skill passou.
- **Arquivos:** `src/editor/capabilities.py`, `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `tests/test_capabilities.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/installation.md`, `skills/vibe-editor/references/youtube-editing.md`, `.vibeflow/REGRAS.md`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`, `.vibeflow/phases/phase-1-novas-capacidades-editoriais/implement.md`
- **Size:** `medium, 5/10, contrato operacional distribuído entre inventário, skill e Documento Vivo`
- **Risk:** `low, documentação e saída de descoberta podem divergir do comportamento implementado se não forem verificadas juntas`

### T10: Provar jornada E2E da phase e gates finais

- [x] T10 concluída
- **Spec:** A1 a A10, C1 a C6
- **Decisões:** SCOPE-01 (prova as cinco capacidades combinadas), INIT-01 (inicia a jornada sem estado editorial), STATE-01 (repete após mutações e exige aprovação vigente)
- **O quê:** Criar fixtures curtas locais e uma jornada E2E que execute `init`, escreva um plano v2 combinado com movimento, J/L, texto, imagem e vídeo em camada, valide, gere preview, aprove e gere full. Migrar a prova E2E do MVP para o contrato v2 e cobrir mutações independentes de plano, preview e todas as famílias de fonte.
- **Aceite:**
  - [x] O E2E executa a jornada completa sem `SkipTest`, verifica artefatos, frames em timestamps distintos, áudio antecipado/prolongado, duração final e preservação do vídeo-base sob as camadas.
  - [x] Repetir `init` não cria artefatos editoriais nem altera hashes ou bytes preexistentes; mutar plano, preview, vídeo, áudio ou imagem bloqueia a full e preserva o estado aprovado.
  - [x] A suíte completa, `pip-audit`, gitleaks e `git diff --check` passam sem segredo, dependência nova ou whitespace inválido.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.e2e.test_phase_1_flow -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.e2e.test_mvp_flow -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  - [x] `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip`
  - [x] `gitleaks detect --source . --no-banner`
  - [x] `git diff --check`
- **Prova:** `tests.e2e.test_phase_1_flow` -> `Ran 1 test ... OK`; `tests.e2e.test_mvp_flow` -> `Ran 1 test ... OK`; suíte -> `Ran 99 tests in 204.932s ... OK`; `pip-audit` -> `No known vulnerabilities found`; gitleaks -> `no leaks found`; `git diff --check` passou.
- **Deps:** T8, T9
- **Arquivos:** `tests/e2e/test_phase_1_flow.py`, `tests/e2e/test_mvp_flow.py`, `tests/fixtures/phase-1/images/`, `tests/fixtures/e2e/plan.json`
- **Size:** `high, 8/10, jornada ponta a ponta com mídia real, estado aprovado e gates de segurança`
- **Risk:** `high, falha de cobertura pode deixar uma regressão de composição ou integridade passar para a revisão`

## Conferência

- [x] Spec aprovada como fonte; mesma pasta
- [x] Fatias verticais; score de Size ≤ 8; Risk separado
- [x] Toda T* tem aceite + verificação + Spec: A*/C* quando couber
- [x] Ferramentas essenciais validadas: `gitleaks 8.30.1`, `pip-audit 2.10.1`, FFmpeg/FFprobe e PowerShell 7; Chrome-devtools é N/A porque não há UI
- [x] T1 estabelece ponto de entrada real (`editor init` em processo separado)
- [x] UI greenfield/sem DS: N/A, a phase é uma CLI local sem interface
- [x] Cada T* possui unidade de execução, verificação e commit própria
- [ ] Aprovação humana, leu o arquivo e confirmou

## Handoff

vibe-implement no phase sem `analyze` obrigatório.

- Chat: recomendo novo chat para `vibe-implement`; continuidade no mesmo chat só por escolha consciente. O `plan.md` vivo é a ponte.
- O arquivo vivo `plan.md` entra no Git. `.vibeflow/plan-report.json` é evidência operacional e fica fora do Git.
