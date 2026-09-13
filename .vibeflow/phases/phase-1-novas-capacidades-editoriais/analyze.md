# Analyze: Novas capacidades editoriais e preparação de trabalhos
# Alvo: phase-1-novas-capacidades-editoriais
# Status: rascunho
# Plan: plan.md (mesma pasta)

## Fontes

| Arquivo | Estado |
|---|---|
| interview.md | presente |
| spec.md | presente |
| plan.md | presente, aprovado pelo gate desta análise |
| REGRAS.md | lido |

## Cobertura e Rastreabilidade

| Chave | Origem | T* | Notas |
|---|---|---|---|
| Resultado.Sucesso | interview.md | T1–T10 | O plano v2 combina as cinco capacidades, preserva execução local e exige preview, aprovação e full verificáveis. |
| A1 | spec.md | T1, T10 | `editor init` cria, completa, repete e preserva o trabalho. |
| A2 | spec.md | T1, T10 | Caminhos inválidos, links, reparse points e falhas de criação são rejeitados com preservação de estado. |
| A3 | spec.md | T2, T6, T7, T10 | Schema v2, fontes de imagem e vídeo em camada, allowlists e limites são cobertos antes do FFmpeg. |
| A4 | spec.md | T3, T10 | Movimento `from`/`to`, interpolação linear e prova por frames. |
| A5 | spec.md | T4, T10 | Áudio global com J/L, cobertura, mistura e duração visual exata. |
| A6 | spec.md | T5, T6, T7, T10 | Texto, imagem e vídeo em camadas, caixa, intervalo, z-order, alpha e ausência de áudio implícito. |
| A7 | spec.md | T2, T5, T10 | Coordenadas livres normalizadas substituem posições nomeadas. |
| A8 | spec.md | T8, T10 | Preview e full reutilizam o grafo v2 e publicam atomicamente. |
| A9 | spec.md | T8, T10 | Aprovação verifica plano, preview e toda mídia referenciada por hash. |
| A10 | spec.md | T2, T9, T10 | CLI, capacidades, documentação, fixtures e provas migram para v2. |
| C1 | spec.md | T1, T7, T10 | A jornada E2E inicia o trabalho e combina as capacidades editoriais. |
| C2 | spec.md | T3, T6, T10 | Frames verificam movimento, camadas e preservação do vídeo-base. |
| C3 | spec.md | T4, T10 | J/L é verificado por janelas de áudio, sem ducking ou crossfade. |
| C4 | spec.md | T1, T10 | Reexecução de `init` é idempotente e não cria estado editorial. |
| C5 | spec.md | T8, T10 | Mutação de plano, preview ou fontes bloqueia a full e preserva o estado vigente. |
| C6 | spec.md | T2, T9, T10 | Suíte, auditoria de dependências, segredo e whitespace possuem comandos executáveis. |

## Achados e Resoluções

| ID | Categoria | Gravidade | Onde | Problema | Resolução Aplicada |
|---|---|---|---|---|---|
| F1 | inconsistencia | MEDIUM | plan.md | T2 migrava o fixture workflow e listava `approval.json`, mas não atribuía `preview.mp4` e `full.mp4` derivados a nenhuma task. | T2 passou a migrar somente o plano; T8 passou a ser responsável pela regeneração v2 e lista `preview.mp4`, `full.mp4` e `approval.json`. |
| F2 | qualidade_teste | MEDIUM | plan.md | A aceitação de A10 exigia documentação v2, mas a T9 verificava apenas capacidades e a estrutura da skill, sem teste semântico do conteúdo legado. | T9 passou a exigir uma checagem em `tests/test_capabilities.py` para os marcadores v2 obrigatórios e a ausência de declarações legadas em toda a documentação atualizada. |

### F1: Artefatos derivados do workflow sem task responsável

- **Gravidade:** MEDIUM
- **Categoria:** inconsistencia
- **Onde:** `plan.md`
- **Evidência:** T2 listava `tests/fixtures/workflow/approval.json`, mas não listava os arquivos derivados `preview.mp4` e `full.mp4`; a migração do hash do plano v1 para v2 poderia deixar o fixture aprovado obsoleto.
- **Resolução:** T2 foi delimitada à migração estrutural do `plan.json`. T8, que já implementa o grafo v2 e a aprovação, recebeu a responsabilidade explícita pelos três artefatos derivados.

### F2: Verificação documental sem asserção semântica

- **Gravidade:** MEDIUM
- **Categoria:** qualidade_teste
- **Onde:** `plan.md`
- **Evidência:** A10 exige que `REGRAS.md` e `skills/vibe-editor` descrevam `init`, v2, camadas e J/L sem declarar v1 ou `overlays`; `quick_validate.py` não prova esse conteúdo.
- **Resolução:** A T9 passou a incluir no escopo uma checagem textual executada por `tests.test_capabilities`, mantendo `quick_validate.py` como validação estrutural complementar.

## Decisões críticas

| ID | Interview | Spec | Plan | Estado |
|---|---|---|---|---|
| SCOPE-01 | Cinco capacidades na mesma phase. | mantém a mesma phase. | T9, T10. | consistente |
| CONTRACT-01 | Plano vigente em `schema_version: 2`. | substitui v1 por v2, sem alterar a versão do envelope. | T2, T8, T9, T10. | consistente |
| MOTION-01 | Estados inicial/final e interpolação linear. | cria `from`/`to`, `x`/`y`/`zoom` e limites de `0.5` a `4.0`. | T3. | consistente |
| CUT-01 | J/L declarado na fronteira com duração validada. | cria `cuts[]` para `j`/`l` e sobreposição temporal explícita. | T4. | consistente |
| LAYER-01 | Camadas ordenadas de texto, imagem e vídeo. | cria `layers[]` com z-order determinístico. | T5, T6, T7. | consistente |
| TEXT-01 | `x`/`y` normalizados com origem superior esquerda. | substitui posições nomeadas por caixa normalizada. | T2, T5. | consistente |
| MEDIA-02 | N/A na interview. | cria allowlist raster, 100 MiB e 8.192 pixels por dimensão. | T6. | consistente |
| INIT-01 | `init` seguro, idempotente e sem estado editorial. | cria ou completa somente a estrutura técnica. | T1, T10. | consistente |
| STATE-01 | Aprovação por hash obrigatória para full. | mantém hashes do plano, preview e fontes usadas. | T8, T10. | consistente |

## Métricas

- A*/C* na spec: 16, sendo A1–A10 e C1–C6.
- T* no plan: 10, sendo T1–T10.
- Cobertura (A*/C* com >= 1 T*): 16/16.
- Qualidade de Testes: T1 possui smoke test em processo separado; todas as tasks têm comandos executáveis; baseline validado com `69 testes ... OK`, sem skips; FFmpeg/FFprobe, PowerShell 7 e gitleaks 8.30.1 disponíveis; `pip-audit 2.10.1` sem vulnerabilidades conhecidas; Chrome-devtools é N/A para a CLI sem UI.
- Achados corrigidos / resolvidos: F1 e F2.
- Achados bloqueantes pendentes: 0.

## Veredito

limpo

## Handoff

vibe-implement

- Chat: recomendo novo chat para `vibe-implement`; continuidade no mesmo chat somente por escolha consciente. O `analyze.md` e os artefatos vivos são a ponte.

- [ ] Aprovação humana (leu o arquivo e confirmou)
