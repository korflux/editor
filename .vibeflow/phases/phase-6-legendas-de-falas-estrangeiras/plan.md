# Plan: legendas de falas estrangeiras a partir da transcrição
# Alvo: phase-6-legendas-de-falas-estrangeiras
# Status: aprovado
# Spec: spec.md (mesma pasta)

## Overview

Esta entrega permite legendar trechos de fala com o texto escrito pelo agente, preso ao tempo da fonte, conforme a [spec](spec.md). A fila tem duas frentes independentes no início: o comando somente leitura `captions suggest`, que quebra a transcrição publicada em frases curtas, e o bloco `captions` do plano, com validação e mapeamento das legendas para a timeline pelas faixas de áudio. Depois o grafo desenha as ocorrências na preview e na full, e por fim o contrato é publicado em `capabilities`, skill e `AGENTS.md`, com a versão 1.3.0.

- **Design:** N/A, entrega sem UI; a superfície é a CLI, os envelopes JSON e os quadros renderizados.

## Prontidão das provas

- **Requisitos verificados (todos disponíveis localmente; o repositório não tem CI):**
  - Python 3.11 em `.venv\Scripts\python.exe` para `unittest` (suíte de 336 testes verde em `faf6b0b`).
  - FFmpeg e FFprobe locais no PATH com `libx264`, `aac` e `drawtext`, já usados pelas camadas de texto da phase 5.
  - Fonte `fontfile` resolvível no Windows (a mesma dos testes de texto vigentes, `tests/test_text_style.py`).
  - `gitleaks` 8.30.1 e `pip_audit` 2.10.1 (via `.venv`) para os controles da T4.
  - Transcrições de teste escritas pelo próprio teste no formato publicado por `analysis/transcription.py`, sem modelo Whisper; fontes de vídeo e áudio geradas por `ffmpeg -f lavfi`.
- **Ausências e ação na fila:** nenhuma.

## Tasks

### T1: Entregar a fala original em frases curtas com `captions suggest`

- [x] T1 concluída
- **Spec:** A1 (F1, decisões 2, 11 a 13)
- **O quê:** comando `editor captions suggest --workdir <abs> --source-id <id> [--start-s] [--end-s] [--max-chars] [--max-duration-s] --json`, somente leitura, que lê `analysis/transcription/<id>.json`, valida o formato, quebra as palavras pela regra da decisão 12 e responde `source_id`, `language`, `source_sha256` e `chunks[]` (`in_s`, `out_s`, `text`, `words[]`, `min_probability`). Os padrões e faixas (`max_chars` 84 de 20 a 200, `max_duration_s` 6 de 1 a 10, pausa 0,6 s, pontuação de fecho) ficam numa fonte única de constantes lida na T4 por `capabilities`.
- **Aceite:**
  - [x] Pelo processo real da CLI, uma transcrição sintética com dois segmentos, pausas, pontuação, palavra sem `probability` e frase longa gera exatamente as frases esperadas pelas cinco regras de fecho, com tempos da primeira e da última palavra e `min_probability` correto; `--start-s`/`--end-s` restringem a janela e uma janela sem palavras devolve `chunks: []`.
  - [x] Argumentos fora da faixa dão `ARGUMENT_INVALID` sem eco; transcrição ausente dá `ANALYSIS_REQUIRED`; transcrição fora do formato (campo faltando, tempo não finito, palavra fora de ordem, JSON inválido) dá `ANALYSIS_INVALID`; trabalho inválido dá `WORKDIR_INVALID`; códigos de saída da família STATE ou ARGUMENT conforme `contracts.py`.
  - [x] O trabalho não ganha nenhum arquivo depois do comando (inventário antes e depois igual).
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m unittest tests.test_captions_suggest tests.test_cli_contract -v`
- **Deps:** nenhuma
- **Arquivos:** `src/editor/analysis/captions.py` (novo), `src/editor/cli.py`, `src/editor/contracts.py` (só se os códigos novos exigirem registro), `tests/test_captions_suggest.py` (novo)
- **Risco:** a ajuda da CLI é conferida por `tests/test_cli_contract.py`; o subcomando novo entra no smoke dos pontos de entrada.

- **Prova:** `.venv\Scripts\python.exe -B -m unittest tests.test_captions_suggest tests.test_cli_contract -v` -> 24 testes, 40,032 s, OK, sem skips; execução autorizada fora do sandbox para junction descartável.
- **Visual:** dispensada, saída estruturada da CLI conferida por processo real.
- **Limitações:** palavra indivisível pode ultrapassar os limites sozinha; janela seleciona pelo início da palavra; uma transcrição por fonte. Probabilidades seguem o contrato finito do publicador.

### T2: Validar o bloco `captions` e mapear as legendas para a timeline

- [ ] T2 concluída
- **Spec:** A2, A3 (F2, decisões 1, 4 a 9, 14)
- **O quê:** `captions` opcional no schema v2 com allowlist de `captions`, `captions.style` e itens; validação estrutural antes de inspecionar mídia (texto, tamanhos, números, estilo reaproveitando as regras e `_validate_text_style` da camada `text` e `_validate_layer_geometry`), validação contra as fontes (existência, áudio, duração, 30 s por item, sobreposição na mesma fonte) e mapeamento pelas faixas de áudio e janelas `audio_start_s`/`audio_end_s` (decisões 4 a 6), com recusa de ocorrências sobrepostas na timeline (decisão 7). `data.captions` (`items`, `occurrences`, `unplaced`) só quando o plano declara legendas; o estilo normalizado com a caixa padrão da proporção e as ocorrências ficam num campo privado da validação para a T3. Constantes (limites, mínimo de 0,3 s, caixas padrão por proporção, tolerância de 1 ms) em `plan/schema.py`.
- **Aceite:**
  - [ ] Um plano com jump cut que atravessa uma fala, reordenação, gancho repetido, corte J, corte L, segmento `replace`, faixa `loop` e um item totalmente cortado produz `occurrences` iguais aos intervalos calculados à parte no teste, omite o fragmento abaixo de 0,3 s, junta ocorrências contíguas do mesmo item e lista o item cortado em `unplaced`.
  - [ ] Cada violação de A3 é recusada com `PLAN_INVALID` e mensagem sem o valor recebido; as estruturais sem chamar o FFprobe.
  - [ ] Um plano sem `captions` gera o envelope, o JSON canônico e o SHA-256 de antes; o canônico de um plano com `captions` inclui o bloco como escrito.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_captions_plan tests.test_plan_validation -v`
- **Deps:** nenhuma
- **Arquivos:** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `tests/test_captions_plan.py` (novo)

### T3: Desenhar as legendas na preview e na full

- [ ] T3 concluída
- **Spec:** A4, A5 (F3, decisões 9 e 10)
- **O quê:** o grafo desenha cada ocorrência acima de todas as camadas, na ordem dos itens, de `start_s` a `end_s`, na caixa normalizada, com o texto quebrado na largura da caixa e ancorado na base dela, reaproveitando o desenho de texto vigente (`drawtext` com `fontfile`, escape com `expansion=none`, escala da preview, sombra e contorno da phase 5). Sem `captions` o `filter_complex` não muda.
- **Aceite:**
  - [ ] Em render real de preview e full, 16:9 e 9:16, pixels da cor da legenda aparecem dentro de cada ocorrência e não aparecem fora delas (inclusive no trecho cortado e no b-roll com `replace`); a base do texto de uma legenda de uma linha e a de outra de duas linhas coincidem com a base da caixa; a razão largura do texto sobre largura do quadro é igual na preview e na full dentro de 2%; caixa padrão e caixa declarada são respeitadas.
  - [ ] Os hashes dos goldens de `filter_complex` de `tests/test_preview_render.py` e `tests/test_aspect_ratio.py` ficam inalterados.
  - [ ] Depois de `approve`, alterar o texto de um item faz `render full` falhar com `APPROVAL_STALE` e a full vigente mantém o SHA-256.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_captions_render tests.test_preview_render tests.test_full_render tests.test_aspect_ratio tests.test_text_style -v`
- **Deps:** T2
- **Arquivos:** `src/editor/render/ffmpeg_graph.py`, `tests/test_captions_render.py` (novo)
- **Risco:** o texto da legenda é entrada não confiável que chega ao FFmpeg; reaproveitar só o caminho de escape vigente e provar com texto hostil (`'`, `:`, `,`, `;`, `[`, `\`, `%{`).

### T4: Publicar o contrato das legendas e fechar a versão 1.3.0

- [ ] T4 concluída
- **Spec:** A6, C1, C2, C3 (F4)
- **O quê:** `capabilities` expõe `operations.captions_suggest` e `plan.captions` (campos, obrigatórios, padrões, caixas por proporção, limites, regra de mapeamento, `ponytail` de uma transcrição por fonte) lidos das constantes da T1 e da T2; a skill `vibe-editor` (`SKILL.md`, `youtube-editing.md`, `canal-sonni-viaja.md`) ensina a jornada da spec (F4 passo 2) e `AGENTS.md` ganha a seção "Legendas, Phase 6" no formato das demais e o mapa de estrutura atualizado; versão 1.3.0 em `pyproject.toml`, `contracts.py` e testes de versão.
- **Aceite:**
  - [ ] `tests.test_capabilities` confere `operations.captions_suggest` e `plan.captions` contra as constantes e a versão 1.3.0; `tests.test_vibe_editor_skill` passa com a skill atualizada.
  - [ ] Suíte completa verde; gitleaks e pip-audit sem achados.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_capabilities tests.test_vibe_editor_skill tests.test_cli_contract -v`
  - [ ] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  - [ ] `gitleaks detect --source . --no-banner` e `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip`
- **Deps:** T1, T2, T3
- **Arquivos:** `src/editor/capabilities.py`, `pyproject.toml`, `src/editor/contracts.py`, `tests/test_capabilities.py`, `tests/test_cli_contract.py`, `skills/vibe-editor/SKILL.md`, `skills/vibe-editor/references/youtube-editing.md`, `skills/vibe-editor/references/canal-sonni-viaja.md`, `AGENTS.md`

## Paralelização (opcional, omitir quando não aplicável)

- Grupo: T1, T2
- Motivo: sem dependência entre si; T1 lê a transcrição publicada e T2 só o plano.
- Isolamento: T1 é dona de `src/editor/analysis/captions.py`, `src/editor/cli.py` e `tests/test_captions_suggest.py`; T2 de `src/editor/plan/` e `tests/test_captions_plan.py`; nenhum arquivo em comum.

## Handoff

vibe-implement

- Chat: recomendo novo chat com `/vibe-implement`, que roda em Modo A num host com subagentes (fila inteira, sequencial, e só volta ao humano na pergunta final). Em Modo B, um chat por T*, e o grupo T1 e T2 pode ser oferecido em paralelo. Continuar neste chat é válido; o `plan.md` vivo é a ponte.
