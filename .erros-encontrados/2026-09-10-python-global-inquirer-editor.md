# Erro encontrado em 2026-09-10

- **Onde:** Python global do host, ao executar `python -m pip check` na raiz do repositório.
- **Problema:** `inquirer 3.4.1` declara `editor>=1.6.0`, mas o pacote local instalado pelo T1 está na versão `editor 0.1.0`.
- **Escopo:** ambiente global, fora da implementação da T2.
- **Estado:** não corrigido nesta task, porque alterar ou remover dependências globais poderia afetar outros projetos.
