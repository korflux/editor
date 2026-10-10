# AGENTS.md versionado como symlink (modo 120000)

- **Data:** 2026-10-10
- **Local:** `AGENTS.md` no índice e no histórico do Git, desde o commit `5aca88e` (chore: migrar regras VibeFlow para AGENTS.md).
- **Descrição:** o Git registra `AGENTS.md` com modo `120000` (symlink), mas o blob guarda o conteúdo inteiro das regras e no disco ele é um arquivo regular. Funciona no Windows porque `core.symlinks` está `false`, então o checkout grava o "alvo" do link como texto.
- **Impacto provável:** num clone com `core.symlinks=true` (Linux, macOS ou Windows com symlinks habilitados) o checkout tenta criar um link cujo alvo é o texto inteiro das regras, falha ou gera um arquivo inválido, e agentes nessa máquina ficam sem as regras. `git cat-file -p` também recusa mostrar o blob.
- **Sugestão de correção:** `git update-index --chmod=-x` não resolve; regravar o modo com `git rm --cached AGENTS.md` seguido de `git add -- AGENTS.md` (ou `git update-index --cacheinfo 100644,<blob>,AGENTS.md`) num commit próprio, conferindo `git ls-files -s AGENTS.md` com `100644`.
- **Relação com a tarefa atual:** encontrado na finalização da phase 7 ao publicar as decisões vigentes; o modo foi preservado para não misturar a correção com o fechamento.

## Correção em 2026-10-10

- **Estado: RESOLVIDO.** Índice corrigido para `100644` usando o mesmo blob versionado `ce7bbf212d6bb057baa1c7fb9aaf23562b46733d`. O conteúdo das regras foi preservado; a adição preexistente do roadmap continua fora deste commit.
- **Prova:** `git ls-files -s AGENTS.md` -> modo `100644`; `git diff --cached --summary` -> somente `mode change 120000 => 100644 AGENTS.md` antes de adicionar este registro. O checkout passa a tratar as regras como arquivo regular independentemente de `core.symlinks`.
