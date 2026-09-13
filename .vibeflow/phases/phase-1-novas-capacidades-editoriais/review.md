# Review: Novas capacidades editoriais e preparação de trabalhos
# Alvo: phase-1-novas-capacidades-editoriais
# Status: rascunho

<!-- Review da Phase 1. O veredito permanece rascunho até confirmação humana. -->

## Contexto

- Rota: high, etapa 1 first-pass.
- Alvo: `phase-1-novas-capacidades-editoriais`.
- Cadeia: `interview.md`, `spec.md`, `plan.md`, `analyze.md`, `implement.md` e este `review.md`.
- Diff auditado: `0851d64..HEAD`, com a Phase 1 em `T1` a `T10`; o working tree continha apenas resíduos gerados pela revisão e este arquivo novo.
- O que muda: revisão ponta a ponta do contrato v2, `editor init`, timeline, J/L, camadas, aprovação/full, documentação, testes e gates.

## Cobertura

| Chave | Código | Notas |
|---|---|---|
| A1 | ok | T1/T10 e `tests.test_init` comprovam criação, idempotência e preservação de estado. |
| A2 | ok | Caminhos relativos, arquivos, links, junctions, reparse points e falhas de criação são exercitados. |
| A3 | ok | A validação cobre schema, mídia, imagem e vídeo em camada; `kind: "audio"` agora aceita somente MP3/WAV PCM S16LE e rejeita MP4 antes do FFprobe. |
| A4 | ok | Movimento linear, limites e efeito real em frames foram comprovados por FFmpeg. |
| A5 | ok | J/L, janelas globais, cobertura, mixagem e duração visual foram comprovados por áudio real. |
| A6 | ok | Texto, imagem e vídeo em camadas, intervalos, caixa, ordem, alpha e áudio explícito foram comprovados. |
| A7 | ok | Coordenadas normalizadas e rejeição de posições nomeadas foram comprovadas. |
| A8 | ok | Preview e full reutilizam o grafo e publicam atomicamente, com preservação em falha. |
| A9 | ok | Plano, preview e fontes declaradas ficam vinculados por hash antes da full. |
| A10 | ok | O aceite T2 está sincronizado, o Documento Vivo descreve o estado atual e a regressão textual impede o retorno da declaração obsoleta. |
| C1 | ok | A jornada E2E combina init, validação, preview, aprovação, full e as capacidades editoriais. |
| C2 | ok | Frames reais comprovam movimento, composição e preservação do vídeo-base. |
| C3 | ok | J/L é comprovado por janelas de áudio sem crossfade ou ducking implícito. |
| C4 | ok | Reexecução de init preserva bytes e não cria artefatos editoriais. |
| C5 | ok | Mutações independentes do plano, preview e fontes bloqueiam a full e preservam o estado. |
| C6 | ok | Suíte, pip-audit, gitleaks, whitespace e validação estrutural da skill passaram no host atual. |

## Checklist de correções

### Required

- [x] R1: **Required** - `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md:75-77` - T2 e seus três itens de aceite agora estão marcados como concluídos, mantendo o plano vivo consistente com `implement.md` e as provas registradas - prova: `.venv\Scripts\python.exe -B -m unittest tests.test_cli_contract tests.test_plan_validation -v` -> `Ran 30 tests in 6.894s`, `OK` - source: `plan.md`/T2 - gap: fechado
- [x] R2: **Required** - `src/editor/plan/validation.py` - a fronteira estrita exige inteiro JSON exatamente igual a `PLAN_SCHEMA_VERSION`; regressões para `2.0` e `2e0` falham como `PLAN_INVALID` antes da inspeção de mídia - prova: `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation -v` -> `OK` dentro da suíte direcionada - source: A3/diff - gap: fechado
- [x] R3: **Required** - `src/editor/plan/validation.py` e `src/editor/media.py` - a allowlist de fonte de áudio do plano foi centralizada e MP4 audiovisual declarado como áudio é rejeitado antes do FFprobe, sem alterar a análise genérica de MP4 - prova: `test_audio_source_kind_rejects_mp4_via_real_cli` retornou `PLAN_INVALID` no processo real e a suíte passou - source: A3/diff - gap: fechado
- [x] R4: **Required** - `.vibeflow/REGRAS.md` e `tests/test_capabilities.py` - a declaração obsoleta foi removida e a documentação agora descreve as operações existentes - prova: `.venv\Scripts\python.exe -B -m unittest tests.test_capabilities -v` -> `Ran 7 tests in 2.023s`, `OK` - source: A10/diff - gap: fechado

## Segurança

- Não foram encontrados segredos pelo gitleaks. Os caminhos e argumentos de FFmpeg são validados e passados sem shell intermediário, os limites de plano/mídia existem e os erros públicos não expõem stack trace. A allowlist de fonte de áudio do plano agora é validada na fronteira de entrada.

## DoD

- [x] Suíte completa: `Ran 99 tests in 201.651s`, `OK`, sem skips no ambiente real.
- [x] `pip-audit -r requirements.lock --no-deps --disable-pip`: `No known vulnerabilities found`.
- [x] `gitleaks detect --source . --no-banner`: `no leaks found`.
- [x] `git diff --check` no intervalo da Phase e no estado atual.
- [x] `quick_validate.py skills\vibe-editor`: `Skill is valid!`.
- [x] T2 e a documentação do Documento Vivo foram sincronizadas, conforme R1 e R4.
- [x] A validação do contrato fechou R2 e R3 com regressões executáveis.
- [x] Reexecução pós-correção: `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` -> `Ran 101 tests in 202.520s`, `OK`.

## Veredito vigente

- [ ] **Approve**: aguardando novo `vibe-review` para revalidar o patch completo.
- [x] **Request changes**: veredito histórico da Etapa 1; R1-R4 foram corrigidos nesta etapa e aguardam revalidação.
- [ ] **Approve com defer**: não aplicável, nenhum achado foi deferido

## Handoff

vibe-review

- R1-R4 foram corrigidos em uma fatia focada; executar novo `vibe-review` no mesmo arquivo vivo para confirmar o veredito final.
- [ ] Aprovação humana (leu o arquivo e confirmou)

## Finalização Git da phase

- Pré-condições: Approve confirmado, R1-R4 fechados, suíte final verde, `git diff --check` e gitleaks executados novamente.
- Commit final: `chore(phase-1): finalize review`, somente com paths residuais autorizados e sem `Co-Authored-By`; sem mudanças residuais, manter o último commit da task.
- Push: `git push` para o upstream atual, sem `--force`. Registrar hash/HEAD, paths e resultado no fechamento aprovado.

## Etapas

### Etapa 1 - first-pass - T1-T10, diff da Phase, provas e gates

- Leu: `interview.md`, `spec.md`, `plan.md`, `analyze.md`, `implement.md`, `REGRAS.md` e código/testes do fluxo real.
- Reexecutou: suíte completa, `pip-audit`, gitleaks, `git diff --check` e `quick_validate.py`.
- Abriu: R1, R2, R3 e R4.
- Fechou: nenhum.
- Veredito desta etapa: Request changes.

### Etapa 2 - correções R1-R4 - contrato, documentação e provas

- Reutilizou: `media.PLAN_AUDIO_SOURCE_EXTENSIONS`, a fronteira `_validate_loaded_plan`/`_inspect_sources`, os helpers de workspace e os envelopes da CLI.
- Corrigiu: aceite vivo da T2, tipo estrito de `schema_version`, allowlist de fonte de áudio do plano e declaração obsoleta do Documento Vivo.
- Reexecutou: `tests.test_cli_contract` + `tests.test_plan_validation` (`30` testes), `tests.test_capabilities` (`7` testes) e a suíte completa (`101` testes), todos verdes.
- Fechou: R1, R2, R3 e R4.
- Veredito desta etapa: correções implementadas; novo `vibe-review` pendente.
