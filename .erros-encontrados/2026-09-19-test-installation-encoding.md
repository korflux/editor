# Erro encontrado em 2026-09-19

- **Onde:** `tests/test_installation.py`, ao executar `scripts/install.ps1` via `pwsh` com `encoding="utf-8"` durante a T7 da Phase 2.
- **Problema:** 5 testes falham com `UnicodeDecodeError` na thread de leitura do subprocesso (`byte 0x82`, `0x87`, `0xA1`, `0xC6`), deixando `stdout`/`stderr` como `None` e quebrando as asserções com `TypeError`. O instalador emite bytes fora do UTF-8 no host Windows.
- **Escopo:** harness do teste do instalador e saída do `install.ps1`, fora da implementação da T7 (capacidades, E2E e Documento Vivo).
- **Estado:** não corrigido nesta task; confirmado como preexistente ao rodar `tests/test_installation` no baseline sem as alterações da T7, com os mesmos 5 erros.
