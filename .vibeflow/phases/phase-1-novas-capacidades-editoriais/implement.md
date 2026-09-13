# Implement: Novas capacidades editoriais e preparação de trabalhos
# Alvo: phase-1-novas-capacidades-editoriais
# Status: aprovado
# Route: high · modo A · T2

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

## T2: Adotar contrato estrutural v2 estrito

#### Reconhecimento

- A fila recalculada após T1 tinha somente T2 elegível.
- A fronteira única do plano está em `src/editor/plan/schema.py` e
  `src/editor/plan/validation.py`; o fixture compartilhado é
  `tests/fixtures/workflow/plan.json`.
- O plano v1 ainda aceitava `overlays`, não exigia `cuts`/`layers` e retornava
  `overlay_count`. A versão publicada da CLI ainda era `0.1.0`.

#### Implementação

- O root passou a aceitar somente o contrato v2 com `cuts` e `layers` explícitos,
  rejeitando v1 e `overlays` antes da inspeção de mídia.
- A validação estrutural agora ocorre antes do FFprobe para fontes, segmentos,
  cortes, camadas, coordenadas normalizadas, campos desconhecidos e números não
  finitos.
- Camadas de texto usam `x`, `y`, `width` e `height`, sem posições nomeadas; o
  resultado de `plan validate` expõe `cut_count` e `layer_count`.
- O pacote e a CLI foram promovidos para `1.0.0`, sem adicionar dependências.
- O fixture linear `workflow/plan.json` foi migrado para v2. Os artefatos
  derivados continuam fora desta fatia e serão regenerados na T8.

#### Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_cli_contract tests.test_plan_validation -v`
  -> `Ran 21 tests ... OK`.
- A prova cobre processo real, hash canônico, rejeição de v1, `overlays`,
  posições nomeadas, NaN e listas v2 ausentes antes da inspeção de mídia.
- `git diff --check` passou.

#### Arquivos

- `pyproject.toml`
- `src/editor/contracts.py`
- `src/editor/plan/schema.py`
- `src/editor/plan/validation.py`
- `tests/test_cli_contract.py`
- `tests/test_capabilities.py`
- `tests/test_plan_validation.py`
- `tests/fixtures/workflow/plan.json`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/plan.md`
- `.vibeflow/phases/phase-1-novas-capacidades-editoriais/spec.md`

## Handoff

Próxima T* elegível: T3. Recomenda-se novo chat para a próxima fatia.
