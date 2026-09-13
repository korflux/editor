# Implement: Novas capacidades editoriais e preparação de trabalhos
# Alvo: phase-1-novas-capacidades-editoriais
# Status: aprovado
# Route: high · modo A · T1

## Reconhecimento

- A fila da phase tinha T1 e T2 elegíveis. T1 foi escolhida pelo operador.
- O ponto de entrada real é `src/editor/cli.py`, que converte `CliError` em
  envelope JSON por `contracts.py`. A validação compartilhada de caminhos fica
  em `src/editor/workdir.py` e é consumida por análise, plano, aprovação e
  renderização.
- A preparação precisava aceitar somente a criação do último componente do
  trabalho, validar todos os caminhos gerenciados antes de criar subpastas e
  preservar estado editorial existente.

## Implementação

- Adicionado `editor init --workdir <caminho-absoluto> --json` ao parser e ao
  dispatcher central.
- Adicionado `init_workdir()` com preflight de diretórios, arquivos conhecidos,
  links, junctions, reparse points e permissões.
- A estrutura é criada em ordem fixa: `.tmp`, `analysis`, `analysis/audio`,
  `analysis/transcription` e `frames`. `sources` e artefatos editoriais não são
  criados.
- A resposta separa diretórios `created` e `existing`, lista somente artefatos
  conhecidos em `preserved_artifacts` e retorna `state: "ready"`.
- Falhas durante a criação removem somente diretórios vazios criados pela
  própria execução, em ordem reversa.
- `validate_workdir()` passou a reutilizar a normalização absoluta comum sem
  mudar seu contrato público.

## Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_init tests.test_cli_contract -v`
  -> `Ran 10 tests ... OK`.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  -> `Ran 75 tests ... OK`.
- A prova inclui processo real, segunda execução idempotente, preservação por
  hash, caminho relativo, arquivo no destino, pai inexistente, destino não
  gravável, falha de criação e junction gerenciada.
- `git diff --check` passou.

## Arquivos

- `src/editor/cli.py`
- `src/editor/workdir.py`
- `tests/test_init.py`
- `tests/test_cli_contract.py`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/analyze.md`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/spec.md`

## Handoff

Próxima T* elegível: T2. Recomenda-se novo chat para a próxima fatia.
