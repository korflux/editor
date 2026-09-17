# Plan: Robustez da renderização local e da transcrição real
# Alvo: phase-2-robustez-renderizacao-local
# Status: aprovado
# Spec: spec.md (mesma pasta)

## Overview

Esta phase implementa a spec aprovada em sete fatias verticais: remove o grafo FFmpeg
da linha de comando, limita deterministicamente a resolução final, separa a validação
de saída dos limites de fonte, normaliza as anomalias reais do Whisper, fixa a seleção
do arquivo de fonte, reforça o gate editorial do agente e fecha com uma jornada E2E
equivalente ao primeiro plano detalhado.

## Ordem

### Fase 1: Caminho de render robusto

- T1: Transportar o grafo FFmpeg por arquivo temporário.
- T2: Aplicar perfil final limitado e compatível.
- T3: Validar artefatos renderizados sem limites de fonte.

### Fase 2: Entradas auxiliares determinísticas

- T4: Normalizar timestamps do Whisper com coerência textual.
- T5: Resolver o arquivo real das fontes de texto.

### Fase 3: Operação e prova combinada

- T6: Impor o gate editorial antes da aprovação.
- T7: Provar a jornada detalhada da Phase 2.

## Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Script de filtro ser removido cedo ou permanecer em `.tmp` | alto | T1 mantém o arquivo durante o processo e testa limpeza em sucesso, retorno não zero, exceção e timeout. |
| Nova regra de largura alterar fulls de planos existentes | alto | T2 preserva schema v2, não faz upscale e cobre planos sem o campo opcional. |
| Separar a validação de saída enfraquecer a validação de entrada | alto | T3 cria contrato específico e prova que a fonte continua bloqueada em 4 GiB. |
| Tolerância do Whisper mascarar corrupção temporal | médio | T4 limita o clamp a 0,5 s, reconstrói texto e manté falha para desvios maiores ou resultado vazio. |
| Registro e fontconfig apontarem para arquivo diferente | médio | T5 resolve e valida o arquivo concreto antes de construir o grafo. |
| Fixture de escala tornar a suíte lenta ou pesada | médio | T7 repete fontes pequenas e captura a chamada sem versionar mídia grande. |

## Paralelização

- Paralelo ok: T4, T5 e T6 são independentes entre si e do caminho principal após T1.
- Sequencial: T1 antes de T2 e T3, porque os três alteram o pipeline compartilhado de render; T7 depende de T1 a T6.
- Contrato primeiro, depois paralelo: T2 fecha o perfil calculado antes de T3 validar as dimensões da saída final.
- Commits permanecem por task; execução paralela não autoriza editar simultaneamente os mesmos arquivos.

## Tasks

### T1: Transportar o grafo FFmpeg por arquivo temporário

- [x] T1 concluída
- **Spec:** A1, A2, A3, C1, C2
- **O quê:** Substituir `-filter_complex <grafo>` por um script UTF-8 exclusivo em
  `<workdir>/.tmp`, passado pela opção de arquivo do FFmpeg (`-/filter_complex` no
  FFmpeg 9.0.1). Reutilizar o layout seguro e
  a limpeza central da renderização, preservando preview/full vigentes em qualquer falha.
- **Aceite:**
  - [x] O smoke test executa `python -m editor --help` e uma preview real pelo ponto de entrada da CLI.
  - [x] Um grafo acima de 100.000 caracteres chega ao runner por arquivo, sem aparecer nos argumentos do processo, e a linha de comando total permanece abaixo de 32.767 caracteres no Windows.
  - [x] Sucesso, retorno não zero, exceção e timeout removem somente o script e o staging da operação.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m editor --help`
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_preview_render.PreviewRenderTests.test_real_cli_publishes_preview_with_profile_and_audio -v`
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_preview_render tests.test_full_render -v`
- **Prova:** `--help` -> exit code 0; preview real -> `Ran 1 test ... OK`; preview/full -> `Ran 23 tests in 141.435s ... OK`; prova adicional do grafo grande -> `Ran 2 tests ... OK`.
- **Arquivos produzidos:** `src/editor/render/preview.py`, `src/editor/render/full.py`, `tests/test_preview_render.py`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/spec.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/plan.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/analyze.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/implement.md`
- **Decisões:** A1, A2, A3, C1 e C2 implementadas. No FFmpeg 9.0.1 do host, `-/filter_complex <arquivo>` substitui a opção legada indisponível `-filter_complex_script`, preservando o contrato de transportar o grafo por arquivo.
- **Deps:** nenhuma
- **Arquivos:** `src/editor/render/preview.py`, `src/editor/render/full.py`, `tests/test_preview_render.py`, `tests/test_full_render.py`
- **Size:** `medium, 5/10, pipeline compartilhado e prova de integração com processo externo`
- **Risk:** `medium, qualquer falha no ciclo de vida pode deixar temporário ou substituir um render vigente`

### T2: Aplicar perfil final limitado e compatível

- [x] T2 concluída
- **Spec:** A5, C4
- **O quê:** Acrescentar `profile.max_full_width` opcional ao schema v2, calcular o
  padrão 1920 sem upscale e usar o perfil efetivo na full e nos resultados estruturados.
  A preview permanece em até 720 pixels e o JSON canônico continua vinculando valores explícitos.
- **Aceite:**
  - [x] Plano sem o campo valida com teto efetivo 1920; valor explícito válido controla a full sem alterar a preview.
  - [x] Booleano, não inteiro, valor fora de 32..3840 ou não múltiplo de 32 retorna `PLAN_INVALID` antes do FFmpeg.
  - [x] Fonte menor não sofre upscale e mudança explícita do perfil invalida a aprovação vigente.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_approval tests.test_preview_render tests.test_full_render -v`
- **Prova:** suíte da fatia -> `Ran 55 tests in 178.115s ... OK`; render real manteve a preview em 320x180, limitou a full explícita a 160x90 e bloqueou a aprovação após mudar o perfil.
- **Arquivos produzidos:** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/preview.py`, `src/editor/render/full.py`, `tests/test_plan_validation.py`, `tests/test_approval.py`, `tests/test_preview_render.py`, `tests/test_full_render.py`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/spec.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/plan.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/implement.md`
- **Decisões:** A5 e C4 implementadas. O documento canônico preserva somente o valor explícito; o resultado calculado expõe o teto resolvido e a largura efetiva sem upscale.
- **Deps:** T1
- **Arquivos:** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/render/preview.py`, `src/editor/render/full.py`, `tests/test_plan_validation.py`, `tests/test_approval.py`, `tests/test_preview_render.py`, `tests/test_full_render.py`
- **Size:** `high, 7/10, altera schema compartilhado, perfil calculado, aprovação e render com regressão real`
- **Risk:** `medium, muda a resolução efetiva padrão da full para fontes maiores que 1920`

### T3: Validar artefatos renderizados sem limites de fonte

- [x] T3 concluída
- **Spec:** A4, C2, C3
- **O quê:** Separar a inspeção de fonte da validação de preview/full para que
  saídas sejam julgadas por contêiner, streams, codecs, perfil e duração esperada,
  sem herdar 4 GiB ou 3.600 segundos.
- **Aceite:**
  - [x] Uma saída H.264/AAC simulada acima de 4 GiB passa pela política de render.
  - [x] Uma fonte acima de 4 GiB continua falhando como `MEDIA_LIMIT_EXCEEDED` antes do FFprobe.
  - [x] Codec, stream, dimensão ou duração incompatível falha como `RENDER_FAILED` e preserva o artefato vigente.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_audio_analysis tests.test_frame_extraction tests.test_plan_validation tests.test_preview_render tests.test_full_render -v`
- **Prova:** suíte da fatia executada após implementação, simplificação e registro; confirmação final -> `Ran 70 tests in 159.169s ... OK`.
- **Arquivos produzidos:** `src/editor/media.py`, `src/editor/render/preview.py`, `tests/test_audio_analysis.py`, `tests/test_preview_render.py`, `tests/test_full_render.py`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/spec.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/plan.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/implement.md`
- **Decisões:** A4 e C3 implementadas; a sondagem FFprobe é compartilhada, mas somente `inspect_source()` aplica os limites de ingestão, enquanto `inspect_rendered_artifact()` exige MP4/H.264 e os streams declarados sem teto arbitrário de tamanho ou duração.
- **Deps:** T2
- **Arquivos:** `src/editor/media.py`, `src/editor/render/preview.py`, `tests/test_audio_analysis.py`, `tests/test_frame_extraction.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`, `tests/test_full_render.py`
- **Size:** `medium, 6/10, contrato interno compartilhado com provas negativas em entrada e saída`
- **Risk:** `medium, uma separação incorreta pode liberar fonte excessiva ou rejeitar render válido`

### T4: Normalizar timestamps do Whisper com coerência textual

- [x] T4 concluída
- **Spec:** A6, A7, C5, C6
- **O quê:** Incorporar a normalização tolerante ao módulo oficial de transcrição,
  limitando palavras em até 0,5 segundo, removendo intervalos sem duração e
  reconstruindo textos apenas das palavras publicadas. Expor os contadores no envelope.
- **Aceite:**
  - [x] A matriz cobre duração zero, clamp inicial/final, clamp que zera palavra, segmento esvaziado e resultado vazio.
  - [x] Desvio acima de 0,5 segundo ou resposta irrecuperável retorna `ANALYSIS_FAILED` sem substituir o artefato vigente.
  - [x] Textos de segmento/global correspondem às palavras publicadas e o envelope retorna os três contadores, inclusive zeros.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_transcription -v`
- **Prova:** confirmação final da suíte da fatia -> `Ran 11 tests in 1.122s ... OK`; a matriz confirmou três descartes de palavra, três clamps, um segmento descartado, coerência textual e preservação atômica nos casos irrecuperáveis.
- **Arquivos produzidos:** `src/editor/analysis/transcription.py`, `tests/test_transcription.py`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/spec.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/plan.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/implement.md`
- **Decisões:** A6, A7, C5 e C6 implementadas. Somente deriva de até 0,5 segundo é limitada às fronteiras; palavras sem duração e segmentos sem palavra válida são descartados, e o texto publicado deriva exclusivamente das palavras restantes.
- **Deps:** nenhuma
- **Arquivos:** `src/editor/analysis/transcription.py`, `tests/test_transcription.py`
- **Size:** `medium, 4/10, contrato temporal local com matriz unitária e preservação atômica`
- **Risk:** `medium, tolerância excessiva poderia alterar silenciosamente evidência editorial`

### T5: Resolver o arquivo real das fontes de texto

- [x] T5 concluída
- **Spec:** A8, C7
- **O quê:** Trocar a verificação booleana de fonte instalada por um resolvedor de
  arquivo regular confiável em Windows e fontconfig. Entregar o caminho apenas como
  metadado interno validado e gerar `drawtext=fontfile=...`.
- **Aceite:**
  - [x] HKLM, HKCU e fontconfig resolvem arquivo permitido; ausência, extensão recusada e reparse point retornam `PLAN_INVALID`.
  - [x] O caminho não entra no plano canônico nem no envelope público e nunca pode ser informado pelo plano.
  - [x] Arial Bold gera grafo com o arquivo bold real e renderiza sem `fontconfig.xml` lateral.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v`
- **Prova:** `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v` -> `Ran 45 tests in 25.264s ... OK`; os testes confirmaram resolução em HKLM/HKCU, rejeição de arquivo ausente, extensão recusada e reparse point, isolamento do caminho no contrato público e render real de Arial Bold.
- **Arquivos produzidos:** `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/spec.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/plan.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/implement.md`
- **Decisões:** A8 e C7 implementadas. O Windows consulta HKCU antes de HKLM e só aceita arquivos `.ttf`, `.otf`, `.ttc` ou `.otc` dentro das pastas de fontes do sistema/usuário; outros sistemas usam `fc-match` e rejeitam fallback de família. O caminho resolvido fica somente em metadado interno e o grafo usa `fontfile` escapado.
- **Deps:** nenhuma
- **Arquivos:** `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_plan_validation.py`, `tests/test_preview_render.py`
- **Size:** `medium, 6/10, integra descoberta específica de plataforma com validação e render real`
- **Risk:** `medium, seleção errada muda silenciosamente layout e identidade visual do lettering`

### T6: Impor o gate editorial antes da aprovação

- [x] T6 concluída
- **Spec:** A10
- **O quê:** Atualizar a skill para exigir auditoria de palavras nas fronteiras,
  continuidade visual, tradução de falas estrangeiras, fonte/safe area do lettering e
  resumo de exclusões antes de pedir aprovação, sem automatizar decisões editoriais.
- **Aceite:**
  - [x] O fluxo documenta comandos/evidências para auditar cortes, movimento, fim do vídeo e textos na preview.
  - [x] A skill distingue validação estrutural de inspeção visual e bloqueia a solicitação de aprovação sem ambas.
  - [x] Tradução, arquivos excluídos e fala removida intencionalmente aparecem como decisões humanas registradas.
- **Verificação:**
  - [x] `python -X utf8 C:\Users\marco\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\vibe-editor`
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_vibe_editor_skill -v`
- **Prova:** `quick_validate.py skills\vibe-editor` -> `Skill is valid!`; `.venv\Scripts\python.exe -B -m unittest tests.test_vibe_editor_skill -v` -> `Ran 4 tests ... OK`; `git diff --check` passou.
- **Deps:** nenhuma
- **Arquivos:** `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/youtube-editing.md`, `tests/test_vibe_editor_skill.py`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/plan.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/implement.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/spec.md`
- **Decisões:** o pedido de aprovação só ocorre após duas provas registradas, estrutural e visual/editorial; auditorias de palavras, cortes, movimento, encerramento e lettering usam a transcrição e frames da preview; tradução, arquivos excluídos e falas removidas permanecem decisões humanas explícitas, sem automação.
- **Size:** `low, 3/10, contrato operacional documental com validação estática existente`
- **Risk:** `low`

### T7: Provar a jornada detalhada da Phase 2

- [ ] T7 concluída
- **Spec:** A1 a A10, C1 a C8
- **O quê:** Criar a prova ponta a ponta com estrutura equivalente ao plano real,
  atualizar capacidades e Documento Vivo e executar a suíte completa e gates. A fixture
  repete mídias pequenas para atingir 69 fontes, 231 segmentos, 43 camadas e filtro acima
  de 100.000 caracteres sem incorporar os arquivos do trabalho do usuário.
- **Aceite:**
  - [ ] A jornada valida plano, renderiza preview, aprova e renderiza full sem rough cuts, monkey patch ou script lateral.
  - [ ] O teste confirma grafo por arquivo, perfil efetivo, hashes vigentes, fonte concreta e `.tmp` limpo.
  - [ ] Capacidades, regras e mapa estrutural documentam os contratos implementados; suíte, gitleaks e SCA passam.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.e2e.test_phase_2_flow -v`
  - [ ] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  - [ ] `gitleaks detect --source . --no-banner`
  - [ ] `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip`
- **Deps:** T1, T2, T3, T4, T5, T6
- **Arquivos:** `src/editor/capabilities.py`, `tests/test_capabilities.py`, `tests/e2e/test_phase_2_flow.py`, `.vibeflow/REGRAS.md`, `.vibeflow/phases/phase-2-robustez-renderizacao-local/implement.md`
- **Size:** `high, 7/10, cruza todos os contratos em E2E com FFmpeg real e gates externos`
- **Risk:** `medium, é o gate final contra regressão combinada do fluxo local`

## Conferência

- [x] Spec aprovada como fonte; mesma pasta
- [x] Fatias verticais; score de Size ≤ 8; Risk separado
- [x] Toda T* tem aceite + verificação + Spec: A*/C* quando couber
- [x] Gitleaks 8.30.1, FFmpeg 9.0.1 e FFprobe 9.0.1 validados no host; chrome-devtools N/A porque não há UI/DOM
- [x] T1 estabelece ponto de entrada real com `python -m editor --help` e preview pela CLI
- [x] UI greenfield/sem DS: N/A
- [x] Cada T* possui unidade de execução, verificação e commit própria
- [ ] Aprovação humana (leu o arquivo e confirmou)

## Handoff

vibe-implement

- Chat: recomende novo chat para `vibe-implement`; continuidade no mesmo chat só por escolha consciente. O `plan.md` vivo é a ponte.
