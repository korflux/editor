# `unittest discover` não executa as jornadas E2E

- Data: 2026-10-08.
- Local: `tests/e2e/` (sem `__init__.py`) e o comando `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`.
- Problema: o discover a partir de `tests` não entra em `tests/e2e/` porque a pasta não é pacote; os 177 testes executados não incluem `test_mvp_flow`, `test_phase_1_flow` nem `test_phase_2_flow`.
- Impacto: o REGRAS.md e os planos tratam o discover como "suíte completa"; uma regressão nas jornadas E2E passaria despercebida se só esse comando fosse rodado.
- Sugestão: adicionar `tests/e2e/__init__.py` (confirmando que os imports das jornadas continuam válidos) ou documentar o comando E2E separado como parte obrigatória da suíte.
- Relação com a tarefa: encontrado na verificação da T5 da phase 4; as três jornadas foram executadas à parte e passaram. Nada foi alterado.
