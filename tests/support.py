"""Infraestrutura compartilhada pelos testes: limpeza confiável das pastas descartáveis `tmp-*`.

Módulo neutro e leve (só biblioteca padrão, nada executa na importação) para que qualquer módulo de teste
use a mesma limpeza sem importar outro módulo de teste. A prova do comportamento está em
`tests/test_support.py`.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import time
import warnings


# --- Limpeza de pastas descartáveis ---------------------------------------------------------------

# Orçamento de espera da remoção: 25 x 0,2 s = até 5 s, gasto só quando a pasta resiste. No caminho
# normal a primeira tentativa remove tudo e nenhuma espera acontece.
REMOVE_TREE_ATTEMPTS = 25
REMOVE_TREE_WAIT_S = 0.2


def remove_tree(path: Path) -> None:
    """Remove a pasta descartável de teste e AVISA se ela sobrar; é a limpeza única dos testes que criam `tmp-*`.

    Causa raiz do lixo: no Windows o `rmdir` da pasta pode falhar por instantes mesmo com o conteúdo já
    apagado (um processo externo, como indexador, antivírus ou observador de arquivos, ainda a mantém
    aberta logo depois do render), e `shutil.rmtree(..., ignore_errors=True)` engole a falha e deixa uma
    pasta vazia na raiz do repositório. Aqui a remoção é repetida com espera curta, o que resolve o caso
    comum.

    Se a pasta ainda existir ao fim das tentativas, o resultado é um `ResourceWarning` e uma linha em
    `stderr` (a segunda sobrevive a um filtro de avisos) com o NOME da pasta, e a função retorna: pasta
    presa por um processo externo é sujeira inofensiva e não deve reprovar um teste de produto.
    """

    for _ in range(REMOVE_TREE_ATTEMPTS):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(REMOVE_TREE_WAIT_S)
    message = (
        f"pasta temporária de teste não removida após {REMOVE_TREE_ATTEMPTS} tentativas: {path.name} "
        "(provável processo externo com a pasta aberta; pode ser apagada à mão)"
    )
    warnings.warn(message, ResourceWarning, stacklevel=2)
    print(f"AVISO: {message}", file=sys.stderr)
