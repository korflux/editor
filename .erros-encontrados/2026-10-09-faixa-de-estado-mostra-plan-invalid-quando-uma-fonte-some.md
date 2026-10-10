# Faixa de estado mostra `PLAN_INVALID`, não `SOURCE_INVALID`, quando uma fonte do plano some

- **Data:** 2026-10-09
- **Onde:** `src/editor/ui/state.py` (`PlanState._reload`, que publica o erro de `load_validated_plan` sem traduzi-lo) e `tests/test_ui_server.py::PlanStateTests.test_a_missing_source_is_an_error_and_a_missing_plan_is_plan_required` (a T3 fixou `PLAN_INVALID` para a fonte removida).
- **Descrição:** a spec (F6, "Erros") e o design (modo Fonte, "Estados") dizem que a fonte que sumiu aparece com `SOURCE_INVALID` na faixa de estado e como "Indisponível" na lista. Quando o arquivo sai do disco, a vigia revalida o plano e `plan validate` responde `PLAN_INVALID` ("Uma fonte do plano não é um arquivo local regular válido."), porque a validação do plano embrulha qualquer falha de leitura de fonte nesse código. A faixa de estado, que mostra o erro do plano, mostra `PLAN_INVALID`. O `SOURCE_INVALID` só aparece onde a T5 o produz diretamente: na linha "Indisponível" da lista (`plan.sources[].error`), na mensagem do player ao abrir a fonte e na resposta de `/api/source/stream`, `/api/source/frame` (409).
- **Impacto conhecido:** só de leitura. A faixa traz um código genérico em vez do específico, mas a mensagem é segura e a linha da fonte e o player dizem `SOURCE_INVALID`. Nenhum dado nem a aprovação dependem disso.
- **Sugestão de correção:** em `PlanState._reload`, quando a validação falha, o `plan.json` não mudou desde a última versão válida e alguma fonte dessa versão deixou de ser arquivo regular, publicar `SOURCE_INVALID` (mensagem de `live.source_unavailable()`) em vez de `PLAN_INVALID`. Muda o contrato do estado fixado pela T3 e o teste citado acima, então fica fora da T5.
- **Relação com a tarefa atual:** T5 (fonte ausente como "Indisponível" com `SOURCE_INVALID`). A T5 cobre a lista, o player e as rotas; a faixa de estado segue o contrato da T3.

## Correção em 2026-10-10

- **Estado: RESOLVIDO.** PlanState traduz a falha para SOURCE_INVALID quando o plano mantém a assinatura da última versão válida e uma fonte ficou indisponível. Plano realmente alterado/inválido conserva PLAN_INVALID. Teste HTTP atualizado conforme o aceite da spec; navegador 1440x900 confirmou a faixa SOURCE_INVALID ao renomear a fonte da fixture.
- Evidências integradas: plan.md e review.md da Phase 7, rodada de correções de 2026-10-10.
