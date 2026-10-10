# Docstring do pacote `editor.ui` ainda trata a aprovação como futura e não lista `usage` nem `approval`

- **Data:** 2026-10-09
- **Onde:** `src/editor/ui/__init__.py` (docstring do pacote, "Mapa do pacote" e a última frase).
- **Descrição:** o texto foi escrito na T3 e diz que "a única escrita futura é a aprovação, que chega na T6 pela rota própria". A T6 já entregou a rota `POST /api/approve` e a T5 já entregou o modo Fonte, mas o mapa do pacote não lista `usage` (uso de cada fonte) nem `approval` (selo da aprovação).
- **Impacto conhecido:** só documentação. O código e o `AGENTS.md` (seção "Interface de revisão, Phase 7") estão corretos; quem lê só o `__init__.py` acha que a aprovação ainda não existe e não encontra dois dos módulos.
- **Sugestão de correção:** reescrever a frase final para "a única escrita é a aprovação (`POST /api/approve`, `approval.json` v2)" e acrescentar `usage` e `approval` ao mapa do pacote.
- **Relação com a tarefa atual:** T7 (contrato da interface e documentação). O arquivo fica fora dos paths permitidos da T7, então não foi alterado.
