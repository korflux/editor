# Analyze: interface de revisão no navegador com player ao vivo
# Alvo: phase-7-interface-de-revisao-no-navegador
# Status: aprovado
# Plan: plan.md (mesma pasta)

## Fontes

| Arquivo | Estado |
|---|---|
| interview.md | presente (confirmado) |
| spec.md | presente (aprovado) |
| design.md | presente (aprovado); UI visível, cruzado |
| plan.md | presente (aprovado) |
| AGENTS.md | lido |
| Código conferido | `src/editor/integrity/approval.py`, `src/editor/render/full.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/render/windows.py`, `src/editor/render/preview.py`, `src/editor/cli.py`, `src/editor/contracts.py`, `src/editor/workdir.py`; consumidores de `preview_sha256` e `approve_workdir` em `tests/` |

## Cobertura e Rastreabilidade

| Chave | Origem | T* | Notas |
|---|---|---|---|
| A1 | spec.md | T3 | `ui start`/`stop`, reaproveitamento e inatividade por processo real |
| A2 | spec.md | T3 | sessão, `Host`, `Origin`, teto de sessões, headers; reforçado por F2 e F3 |
| A3 | spec.md | T1 | reescrito por F1: igualdade na saída do grafo, antes do encoder |
| A4 | spec.md | T4 | player e transporte no navegador |
| A5 | spec.md | T4 | timeline, scrub e seleção |
| A6 | spec.md | T3, T4 | estado revalidado (T3) e recarga na tela (T4) |
| A7 | spec.md | T5 | modo Fonte |
| A8 | spec.md | T3, T4, T5 | texto no servidor (T3, T5) e cópia na tela (T4) |
| A9 | spec.md | T2, T6 | aprovação condicionada (T2) e botão (T6); reforçado por F4 |
| A10 | spec.md | T2 | aprovação v2 sem preview |
| A11 | spec.md | T7 | `capabilities`, skill e `AGENTS.md` |
| C1 | spec.md | T1, T2, T7 | provas de A3 e A10 nas T1 e T2; suíte completa na T7 |
| C2 | spec.md | T7 | gitleaks, pip-audit e lockfile |
| C3 | spec.md | T1 | cenário `live` do `bench_render.py`; Ask first se falhar |
| C4 | spec.md | T2, T7 | quebra na T2, versão 2.0.0 e documentação na T7 |
| C5 | spec.md | T7 | jornada real no navegador |
| Sucesso da interview | interview.md | T1 a T7 | revisar sem preview, comunicar, conferir, aprovar e pedir a full: C5 |
| Design (4 telas) | design.md | T4, T5, T6 | Montagem (T4), Fonte (T5), popup (T6), Sessão inválida (T3, estáticos) |

Interview → spec: os quatro itens da Solicitação, as decisões UI-01, UI-02, UI-03, APPROVAL-01 e SEC-01, o Fora e os pontos levantados para a spec têm destino na Cobertura da origem; a identidade estável de segmentos está em Fora com motivo. Nenhuma T* entrega item do Fora (arraste, efeitos novos, colaboração, chat, compositor no navegador, full ou proxy build pela interface).

## Achados e Resoluções

| ID | Categoria | Gravidade | Onde | Problema | Resolução Aplicada |
|---|---|---|---|---|---|
| F1 | furo | HIGH | spec.md, plan.md | A3 e T1 exigiam quadros e PCM do stream idênticos aos da full "após decodificar", impossível entre dois encodes com perda e GOPs diferentes | Igualdade movida para a saída do grafo (vídeo cru e PCM por `framemd5`), com o encoder de transmissão como única diferença aceita; scrub idem |
| F2 | furo | HIGH | spec.md, plan.md | Cookie de sessão com nome fixo: cookies não são isolados por porta, então duas instâncias (uma por trabalho) no mesmo navegador se derrubariam | Nome do cookie inclui a porta (F2 da spec, T3) e aceite com duas instâncias |
| F3 | furo | MEDIUM | spec.md, plan.md | Teto de 2 sessões sem expiração: reabrir a URL três vezes travaria a interface até reiniciar o servidor | "Simultânea" definida como ativa nos últimos 120 s, com descarte da ociosa mais antiga (suposição 13, T3) |
| F4 | furo | HIGH | spec.md, plan.md | A aprovação deveria recusar fonte alterada "desde o carregamento", mas o corpo só levava `plan_sha256`, que não inclui as fontes; o servidor não sabia o que a tela exibiu, e F5 não recarregava em troca de fonte com o mesmo hash | Corpo passa a `{plan_sha256, sources_fingerprint}`; F5 recarrega também em impressão nova; T2 confere a impressão de novo após o SHA-256 (TOCTOU) |
| F5 | furo | HIGH | plan.md | Spec manda logs sem token, mas o log padrão de `http.server` imprime a linha do pedido com `?token=`, e o servidor em segundo plano herdaria o stdout da CLI, sujando o envelope JSON | T3 ganha regra e aceite: stdio do servidor separado e log substituído, conferido no teste |
| F6 | furo | MEDIUM | plan.md | AGENTS.md exige status HTTP no envelope de erro da borda; a T3 não mapeava status para `ARGUMENT_INVALID`, rota e método fora da allowlist, nem falhas de stream depois do primeiro byte | T3 com o mapeamento de status e o comportamento após o primeiro byte |
| F7 | qualidade_teste | MEDIUM | plan.md | T2 remove `preview_sha256`, mas a verificação omitia `tests/e2e/test_phase_2_flow.py` (lê `preview_sha256`) e `tests/test_hevc_sources.py` (`approve` pela CLI) | Os dois entram no comando da T2 |
| F8 | qualidade_teste | MEDIUM | plan.md | O risco da T1 cita os goldens de `tests/test_aspect_ratio.py`, mas o comando não o rodava | `tests.test_aspect_ratio` entra no comando da T1 |
| F9 | inconsistencia | MEDIUM | plan.md | UI-01 e UI-03 sem T* no campo `Decisões:`; T6 implementa APPROVAL-01 sem registrá-la | `Decisões:` em T1 (UI-01), T3 e T4 (UI-03) e T6 (APPROVAL-01) |
| F10 | furo | LOW | plan.md | T5 e T6 sem campo `Arquivos:` | Campos acrescentados |
| F11 | inconsistencia | LOW | spec.md | Suposição 6 dizia "primeira fonte de vídeo"; o código usa o `-r` da fonte do primeiro segmento (`preview.py`) | Redação alinhada ao código |

### F1: igualdade de quadros impossível após encoders com perda

- **Gravidade:** HIGH
- **Categoria:** furo
- **Onde:** `spec.md` (A3, Como provar, Boundaries Always), `plan.md` (T1)
- **Evidência:** a full usa `libx264 veryfast crf 23` (`_video_encoder_arguments`), e o stream usa um encoder próprio de GOP curto começando em T. Os quadros decodificados de dois encodes assim nunca são idênticos. As provas vigentes de janelas (`test_decode_windows`) comparam `framemd5` do grafo ou o mesmo encoder com a mesma entrada.
- **Resolução:** A3 e T1 comparam vídeo cru e PCM na saída do grafo, antes do encoder, e o stream codificado só por dimensões e formato. A intenção de UI-01 (mesmo grafo, fidelidade exata) é preservada.

### F2: colisão de cookie entre instâncias

- **Gravidade:** HIGH
- **Categoria:** furo
- **Onde:** `spec.md` (F2 passo 1), `plan.md` (T3)
- **Evidência:** a spec permite uma instância por trabalho, todas em `127.0.0.1`, e o navegador compartilha cookies entre portas do mesmo host.
- **Resolução:** nome do cookie inclui a porta; T3 prova duas instâncias simultâneas válidas.

### F3: teto de sessões sem liberação

- **Gravidade:** MEDIUM
- **Categoria:** furo
- **Onde:** `spec.md` (suposição 13), `plan.md` (T3)
- **Evidência:** "no máximo 2 sessões simultâneas", sem definir quando uma sessão deixa de contar.
- **Resolução:** sessão ativa é a que fez pedido nos últimos 120 s (a tela consulta o estado a cada segundo). A terceira descarta a ociosa mais antiga e só recebe `UI_SESSION_LIMIT` com duas ativas. O token vale enquanto a instância viver.

### F4: aprovação sem saber a impressão exibida

- **Gravidade:** HIGH
- **Categoria:** furo
- **Onde:** `spec.md` (F5 passo 2, F8 passo 2 e Validações, Contratos), `plan.md` (T2, T3, T6)
- **Evidência:** `plan_sha256` não cobre o conteúdo das fontes (`_hash_sources` é separado), e o corpo aceito era só `{plan_sha256}`. Com a mesma API servindo várias sessões, "a impressão do carregamento" não tinha origem. Além disso, uma fonte trocada entre a checagem e o SHA-256 gravaria o hash de conteúdo não visto.
- **Resolução:** o estado expõe `sources_fingerprint`, e a interface a devolve no POST. F5 recarrega em impressão nova, e a T2 confere a impressão de novo depois do cálculo. A API interna não é contrato externo, então a forma é do plan e da spec.

### F5: token no log e stdout da CLI

- **Gravidade:** HIGH
- **Categoria:** furo
- **Onde:** `plan.md` (T3)
- **Evidência:** `BaseHTTPRequestHandler.log_message` grava a linha do pedido com a query no stderr. A spec (Contratos: "logs sem o token") e a regra de segurança do usuário proíbem token em log. O AGENTS.md exige dados de máquina sozinhos no stdout.
- **Resolução:** T3 separa o stdio do servidor, substitui o log e prova que o stderr capturado não contém o token.

### F6: status HTTP dos erros da API

- **Gravidade:** MEDIUM
- **Categoria:** furo
- **Onde:** `plan.md` (T3)
- **Evidência:** AGENTS.md, "Tratamento de Erros na Borda": envelope com `code`, `message` e status HTTP/CLI. A spec deixa a forma das rotas ao plan e só dá status aos `UI_*` e à aprovação.
- **Resolução:** mapeamento de status na T3 e regra para falha depois do primeiro byte do stream.

### F7: consumidores de `preview_sha256` fora da prova da T2

- **Gravidade:** MEDIUM
- **Categoria:** qualidade_teste
- **Onde:** `plan.md` (T2)
- **Evidência:** `rg preview_sha256` aponta `tests/e2e/test_phase_2_flow.py` (linhas 241 e 253); `tests/test_hevc_sources.py` chama `approve` pela CLI.
- **Resolução:** os dois entram no comando de verificação da T2.

### F8: goldens de proporção fora da prova da T1

- **Gravidade:** MEDIUM
- **Categoria:** qualidade_teste
- **Onde:** `plan.md` (T1)
- **Evidência:** o Risco exige os goldens de `tests/test_aspect_ratio.py` intactos, mas o comando não os rodava.
- **Resolução:** `tests.test_aspect_ratio` no comando da T1.

### F9: decisões sem task responsável

- **Gravidade:** MEDIUM
- **Categoria:** inconsistencia
- **Onde:** `plan.md` (T1, T3, T4, T6)
- **Evidência:** a interview cria UI-01 e UI-03 e o plan não as atribuía; APPROVAL-01 só aparecia na T2.
- **Resolução:** campos `Decisões:` acrescentados. Nenhuma task implementa opção divergente.

### F10: campos `Arquivos:` ausentes

- **Gravidade:** LOW
- **Categoria:** furo
- **Onde:** `plan.md` (T5, T6)
- **Evidência:** as demais T* listam arquivos; T5 e T6 não.
- **Resolução:** campos acrescentados, sem sair dos módulos já previstos (`src/editor/ui/`, `render/live.py`).

### F11: origem da taxa de quadros do timecode

- **Gravidade:** LOW
- **Categoria:** inconsistencia
- **Onde:** `spec.md` (suposição 6)
- **Evidência:** `preview.py` aplica `-r` com a taxa da fonte do primeiro segmento; o fallback 25 é o do `color` em `ffmpeg_graph.py`.
- **Resolução:** redação alinhada ao código.

## Decisões críticas

| ID | Interview | Spec | Plan | Estado |
|---|---|---|---|---|
| UI-01 | player pelo grafo FFmpeg real | mantém (suposição 1) | T1 | consistente |
| UI-02 | `ui start/stop`, servidor em segundo plano | mantém (F1) | T3 | consistente |
| UI-03 | biblioteca padrão, fMP4 por HTTP, HTML/CSS/JS sem build, canvas, scrub por quadros | mantém (suposições 5, Tecnologia imposta) | T3, T4 | consistente |
| APPROVAL-01 | aprovação por botão, sem `preview.mp4` | mantém (suposições 10 e 11, F8); substitui o vínculo da T9 vigente com a preview | T2, T6, T7 (registro no AGENTS.md) | consistente |
| SEC-01 | `127.0.0.1`, token, `Host`, só workdir, sem CORS | mantém (F2, Contratos), reforçada por F2, F3 e F5 | T3 | consistente |

Decisões vigentes do AGENTS.md conferidas: RENDER-01 (janelas e pre-roll) é reaproveitada pela T1 sem mudar o grafo sem janela de tempo; RENDER-02 segue (full e aprovação sempre nos originais, proxy só nas frações); RENDER-04 rege o texto das frações; RENDER-05 segue (qualidade fora da aprovação). A substituição do vínculo com a preview fica registrada pela T7, sem edição do AGENTS.md nesta análise.

## Constituição

- Regra: logs sem token e stdout só com dados de máquina (AGENTS.md, Regras deste repo; segurança do usuário) → Resolução: F5.
- Regra: envelope de erro da borda com status HTTP/CLI → Resolução: F6.
- Semver: quebra de `approval.json` e dos envelopes vai a 2.0.0 (C4, T7), com a ressalva da T2 de que a versão só sobe na T7; aceito porque não há publicação externa entre as T*.

## Métricas

- A*/C* na spec: 16 (A1 a A11, C1 a C5)
- T* no plan: 7
- Cobertura (A*/C* com >= 1 T*): 16/16
- Qualidade de Testes: toda T* tem comando `unittest` real; T1 e T7 acrescentam `bench_render.py`, gitleaks e pip-audit. O smoke do ponto de entrada `editor ui start/stop` fica na T3, que o entrega, por processo real. As provas visuais das T4 a T7 usam o navegador integrado (`mcp__Claude_Browser__*`), disponível no host, com `chrome-devtools` como alternativa. Nenhuma ferramenta nova a instalar.
- Achados corrigidos / resolvidos: 11/11
- Achados bloqueantes pendentes: 0

## Veredito

limpo

## Handoff

vibe-implement
rota: max

- Chat: com subagentes, o `vibe-analyze` roda a partir do chat do plan; sem eles, recomende chat novo para iniciar a análise. Recomende com ênfase um chat novo com `/vibe-implement`, que rodará em Modo A num host com subagentes e em Modo B sem eles. Se o humano preferir continuar no mesmo chat, isso não bloqueia. O `analyze.md` e os artefatos vivos são a ponte.

- [x] Aprovação humana (leu o arquivo e confirmou)
