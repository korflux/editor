# Erro encontrado em 2026-09-10

- **Onde:** Python global do host, ao executar `python -m pip check` na raiz do repositório.
- **Problema:** `inquirer 3.4.1` declara `editor>=1.6.0`, mas o pacote local instalado pelo T1 está na versão `editor 0.1.0`.
- **Escopo:** ambiente global, fora da implementação da T2.
- **Estado:** não corrigido nesta task, porque alterar ou remover dependências globais poderia afetar outros projetos.

## Verificação em 2026-09-19 (R4)

- **Venv:** `.venv\Scripts\python.exe -B -m pip check` -> `No broken requirements found`; `inquirer` não está instalado no venv e o `editor` editável local não tem dependentes quebrados.
- **Global:** `python -m pip check` ainda falha com `inquirer 3.4.1 has requirement editor>=1.6.0, but you have editor 0.1.0`; `pip show editor` confirma instalação editável a partir de `A:\Projetos\editor`, que sombreia o pacote PyPI `editor` exigido pelo `inquirer` (colisão de nomes, projeto externo `open-interpreter` depende de `inquirer`).
- **Decisão:** manter sem alteração no global (remover afetaria outros projetos) e sem renomear o pacote (quebra major fora do escopo); caminho suportado permanece o venv provisionado por `scripts/install.ps1`.
