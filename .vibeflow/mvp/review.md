# Review: MVP da CLI local

# Status: rascunho

- Rota: mvp
- Etapa: 3, revalidação de hardening
- Alvo: MVP
- plan: sim, .vibeflow/mvp/plan.md
- spec: sim, .vibeflow/mvp/spec.md
- analyze: sim, .vibeflow/mvp/analyze.md
- diff auditado: working tree pós-correções
- Status do documento: rascunho

## Contexto

O review reavalia as tasks T1 a T11, as correções R1-R9/N1-N2, a implementação em `src/editor`, o instalador, as fixtures e os testes do MVP. A suíte completa foi executada com 66 testes e terminou em OK. `scripts/install.ps1 -Verify`, `pip check`, gitleaks, pip-audit e `git diff --check` também passaram.

O venv operacional Python 3.11.15 x64 foi validado; o instalador continua exigindo um Python base compatível somente quando for necessário criar um novo venv. O E2E é um gate estrito e agora comprova transcrição real com segmentos e palavras temporizadas. A revalidação adicional reproduziu três gaps fora dos cenários cobertos, relacionados a reparse points no instalador, controle NUL em overlay e arredondamento do limite de janelas de áudio.

## Cobertura

| Critério | Estado | Evidência ou gap |
|---|---|---|
| A1 | coberto | capabilities, formatos, limites e operações têm teste de processo e execução local |
| A2 | coberto | E2E estrito com fixture local de fala comprova segmentos e palavras com timestamps no backend real; os testes de contrato cobrem falhas sem timestamps |
| A3 | coberto | análise de áudio real, métricas, janelas e falhas preservadas |
| A4 | coberto | extração real de PNGs, seleção temporal e manifesto |
| A5 | coberto | plano de vlog, timeline linear e rejeição de overlay com NUL passam |
| A6 | coberto | ganhos explícitos, normalização, limiter, mute e regra de b-roll |
| A7 | coberto | preview real, perfil, duração, encoders, publicação atômica e bloqueio de overlay com NUL passam |
| A8 | coberto | aprovação, mutações pós-aprovação e colisões entre fontes e destinos são bloqueadas antes do FFmpeg |
| A9 | coberto | falhas nominais e colisões preservam estado vigente, fontes e temporários |
| A10 | coberto | allowlists, limites de mídia, números, complexidade do plano, aliases, reparse points físicos, NUL em overlay e janela quantizada passam |
| A11 | coberto | testes de processo e E2E comprovam stdout JSON, stderr vazio, ausência de prompts, `shell=False` e execução local |
| A12 | coberto | `-Verify` e a instalação de modelos rejeitam destinos atravessando junction |
| C1 | coberto | os artefatos de entrevista/spec cobrem a condução conversacional e o E2E estrito percorre a timeline completa |
| C2 | coberto | comandos nominais e overlay com NUL retornam envelopes estáveis, com `PLAN_INVALID` na borda |
| C3 | coberto | full revalida plano, preview, fontes e hashes antes de renderizar |
| C4 | coberto | alias físico entre fonte e `preview.mp4`/`full.mp4` é rejeitado e a suíte confirma preservação dos insumos |
| C5 | coberto | o caminho E2E usa modelo local offline, a seleção CUDA exige runtime utilizável e não há fallback remoto |

## Critical findings

- [x] R1: **Critical**. Evidência histórica: src/editor/plan/validation.py e os renders permitiam alias entre fonte e destino. Remédio aplicado em `workdir.ensure_sources_do_not_alias_output`, com regressões reais para preview e full. source: A9, C4, STATE-01. gap: fechado.

- [x] R10: **Critical**. `scripts/install.ps1:65-98` (`Resolve-RepoPath`) validava somente o primeiro ancestral existente do caminho. Como `-VenvPath` e `-ModelDir` usam esse resolvedor em `scripts/install.ps1:632-633`, uma junction ancestral podia redirecionar a escrita para fora da árvore lexical do repositório; o mesmo risco existia nos destinos fixos `.tools\ffmpeg` e `.tools\gitleaks`. Remédio aplicado: `Test-RegularPathChain` percorre todos os componentes existentes até `RepoRoot`, e os destinos configuráveis e fixos reutilizam essa guarda antes de `mkdir` ou cópia. A regressão de `tests/test_installation.py` reproduz a junction e confirma falha sem criar `target\deep\models`. source: A10, A12, DEP-01, STATE-01. gap: fechado.

## Required findings

- [x] R2: **Required**. Evidência histórica: `--window-s` aceitava magnitude enorme ou subnormal. Remédio aplicado em `analysis/audio.py`, com limite de janela e de quantidade de janelas antes do processamento. `test_unrepresentable_window_is_rejected_before_media_probe` e `test_window_count_limit_is_rejected_before_processing` passaram com `ARGUMENT_INVALID`. source: A10, OPS-01. gap: fechado.

- [x] R3: **Required**. Evidência histórica: inteiro JSON de magnitude extrema caía em erro interno. `_finite_number` agora captura magnitude inválida e converte para `PLAN_INVALID`; `test_huge_numeric_value_returns_plan_invalid_envelope` passou sem traceback. source: A10, CONTRACT-01. gap: fechado.

- [x] R4: **Required**. Evidência histórica: capabilities não refletia a ausência de `libx264` ou `aac`. `dependencies.probe_ffmpeg_encoders` agora é a sondagem central, reutilizada por capabilities e render; `test_render_capabilities_require_the_declared_encoders` passou. source: A1, A10, DEP-01. gap: fechado.

- [x] R5: **Required**. Evidência histórica: driver presente não garantia runtime CUDA utilizável. `detect_cuda_runtime` agora consulta `ctranslate2.get_cuda_device_count`, capabilities/backend exigem esse resultado e o `-Verify` do instalador faz a mesma sondagem no venv; os testes de capability e transcrição confirmam fallback para CPU. source: A1, A2, C5, AI-02. gap: fechado.

- [x] R6: **Required**. Evidência histórica: o E2E podia ser pulado e a fixture não comprovava fala. O gate agora lança falha quando pré-requisitos faltam, usa `speech.mp3.b64` local e exige segmentos com `words` não vazios; `tests.e2e.test_mvp_flow` passou no backend real. source: A2, C1, C2, DoD. gap: fechado.

- [x] R7: **Required**. Evidência histórica: os artefatos tinham marcações e provas divergentes, e `-Verify` dependia indevidamente do Python base. Spec, plan e implement agora registram a mesma cobertura; `-Verify` passou no venv operacional Python 3.11.15 x64. `REGRAS.md` não foi alterado, pois não houve decisão nova de vigência e sua sincronização aguarda aprovação humana. source: cadeia de governança, A10-A12, C1-C5. gap: fechado.

- [x] R8: **Required**. Evidência histórica: o plano não limitava a complexidade temporal. `MAX_SEGMENTS`, `MAX_OVERLAYS` e `MAX_TIMELINE_DURATION_SECONDS` agora são expostos por capabilities e validados antes do FFmpeg; `test_segment_count_limit_is_rejected` e `test_timeline_duration_limit_is_rejected` passaram. source: A10, OPS-01. gap: fechado.

- [x] R9: **Required**. Evidência histórica: `stream_pcm` podia bloquear no `stdout.read`. A leitura agora ocorre em thread daemon com watchdog e encerramento do processo; `test_stream_pcm_watchdog_interrupts_blocking_stdout` passou. source: A9, A10, OPS-01. gap: fechado.

- [x] R11: **Required**. `src/editor/plan/validation.py:350-364` aceitava `\x00` em `text` e `font`, que chegava ao argumento do filtro e causava `ValueError: embedded null character` em vez de `PLAN_INVALID`. Remédio aplicado: `_reject_nul` rejeita ambos os campos na borda, antes do hash e do render. `test_nul_in_overlay_returns_plan_invalid_envelope` confirmou exit code `5`, `stderr` vazio e envelope `PLAN_INVALID`. source: A5, A10, C2, TEXT-01. gap: fechado.

- [x] R12: **Required**. `src/editor/analysis/audio.py:295-300` comparava a janela decimal antes da quantização; para `3600.0` e `0.0144`, isso permitia `250073` janelas efetivas. Remédio aplicado: `_window_sample_count` é compartilhado pelo acumulador e pela validação, que calcula a quantidade com `ceil` de amostras antes do streaming. `test_window_count_limit_accounts_for_sample_rounding` confirmou `ARGUMENT_INVALID` sem hash ou streaming. source: A3, A9, A10, OPS-01. gap: fechado.

## Optional / Nit

- [x] N1: **Nit**. Evidência histórica: o filtro incluía o instante final. A expressão agora usa `gte(t,start)*lt(t,end)` e respeita `[início, fim)`. source: A5, TEXT-01. gap: fechado.

- [x] N2: **Nit**. Evidência histórica: os parsers aceitavam chaves duplicadas. `reject_duplicate_json_keys` agora é usado no plano e na aprovação, com regressão de processo para `PLAN_INVALID`. source: CONTRACT-01. gap: fechado.

## Segurança

Foram auditados limites de entrada, caminhos locais, aliases entre entrada e saída, processos externos, execução offline, dependências locais, publicação atômica, hashes e envelopes de erro. Não foram encontrados segredos pelo gitleaks, e o pip-audit não reportou vulnerabilidades conhecidas. R1-R12 e N1-N2 estão fechados após as regressões focadas e a suíte completa.

## DoD

- [x] O diff origin/main...HEAD passa em git diff --check.
- [x] A suíte completa passa: 69 testes, incluindo mídia real e transcrição com fala no host atual.
- [x] pip check passa sem dependências quebradas.
- [x] gitleaks detect --source . --no-banner passa sem segredos encontrados.
- [x] pip-audit -r requirements.lock --no-deps --disable-pip passa sem vulnerabilidades conhecidas.
- [x] O gate de instalação é reproduzível no host atual usando o venv operacional Python 3.11.15, sem exigir um launcher base adicional quando o venv está íntegro.
- [x] Não há findings Critical ou Required abertos.

## Notes

- O diff não toca UI nem banco de dados; não há revisão visual ou de migrações aplicável.
- Não foi criada nem substituída nenhuma decisão de produto. REGRAS.md não foi alterado nesta review.
- Não há seção de Open Questions nos artefatos do MVP. A divergência de cobertura foi registrada em R7.
- A primeira etapa registrou falhas reproduzíveis fora dos cenários nominais; a segunda fechou R1-R9/N1-N2. A terceira abriu R10-R12; esta etapa de implementação fechou os três com código e regressões, aguardando uma nova revisão independente.

## Veredito vigente

- [ ] Approve, proposta aguardando confirmação humana
- [x] Request changes
- [ ] Approve com defer

## Handoff

vibe-review, confirmar R10-R12 corrigidos e emitir novo veredito

Os findings R1-R12 e N1-N2 foram corrigidos e reavaliados por testes. A etapa de review anterior permanece como `Request changes` até a nova revisão independente confirmar o patch; a aprovação humana ainda não foi concedida.

## Aprovação humana

- [ ] Aguardando resposta explícita: aprovado

## Etapas

### Etapa 1, primeira revisão

- Cobertura lida: interview.md, spec.md, plan.md, analyze.md, implement.md e diff origin/main...HEAD.
- Verificações executadas: suíte completa, gitleaks, pip-audit, pip check, git diff --check e probes reproduzíveis dos findings.
- Abriu: R1, R2, R3, R4, R5, R6, R7, R8, R9.
- Fechou: nenhum.
- Veredito: Request changes.

### Etapa 2, revisão pós-correções

- Cobertura lida: interview.md, spec.md, plan.md, analyze.md, implement.md, código alterado, testes focados e diff da working tree.
- Verificações executadas: `tests.e2e.test_mvp_flow` passou com fala real; suíte completa com 66 testes passou; `scripts/install.ps1 -Verify`, `.venv\Scripts\python.exe -B -m pip check`, gitleaks, pip-audit e `git diff --check` passaram.
- Abriu: nenhum achado novo.
- Fechou: R1, R2, R3, R4, R5, R6, R7, R8, R9, N1 e N2.
- Veredito: Approve, como proposta aguardando confirmação humana explícita.

### Etapa 3, revalidação de hardening

- Cobertura lida: interview.md, spec.md, plan.md, analyze.md, implement.md, código alterado, testes e working tree atual.
- Verificações executadas: suíte completa com 66 testes e nenhum `skipped`; `scripts/install.ps1 -Verify`; `.venv\Scripts\python.exe -B -m pip check`; gitleaks; pip-audit com acesso direto autorizado à base; `git diff --check`; reprodução de escrita através de junction no instalador; probe de NUL em overlay; cálculo de fronteira de janelas de áudio.
- Abriu: R10, R11 e R12.
- Fechou: nenhum achado novo nesta etapa.
- Veredito: Request changes.

### Etapa 4, correções R10-R12 implementadas

- Cobertura lida: `scripts/install.ps1`, `src/editor/analysis/audio.py`, `src/editor/plan/validation.py`, os três testes focados, `plan.md`, `spec.md` e este review.
- Verificações executadas: `tests.test_installation tests.test_plan_validation tests.test_audio_analysis` com 26 testes; suíte completa com 69 testes; `scripts/install.ps1 -Verify`; `pip check`; gitleaks; pip-audit; `git diff --check`.
- Abriu: nenhum achado novo.
- Fechou: R10, R11 e R12.
- Veredito: a etapa anterior continua `Request changes`; handoff para nova `vibe-review`.
