# Plan: decodificação seletiva e cache de proxy
# Alvo: phase-4-decodificacao-seletiva-e-cache-de-proxy
# Status: aprovado
# Spec: spec.md (mesma pasta)

## Overview

Esta entrega reduz o tempo de render do editor sem alterar o resultado: o grafo passa a ler só os intervalos de cada fonte que a timeline usa (janelas de decode, F1) e a preview pode ler um proxy leve da fonte, construído uma vez e reaproveitado entre as iterações (F2 e F3). A fila prova primeiro a equivalência de quadros e áudio das janelas, depois liga o render com fallback e medição, e só então constrói os proxies, sua leitura na preview e a exposição em `capabilities`, skill e `REGRAS.md`. Detalhes de comportamento, tetos e decisões estão na [spec](spec.md).

- **Design:** N/A, entrega sem UI; a superfície é a CLI e os envelopes JSON.

## Prontidão das provas

- **Requisitos verificados (todos disponíveis localmente; o repositório não tem CI):**
  - Python 3.11.15 em `.venv\Scripts\python.exe` para `unittest`.
  - FFmpeg e FFprobe 9.0.1 no PATH, com os encoders `libx264`, `aac` e `h264_nvenc`; `-ss`, `-t`, `-fps_mode` e `-enc_time_base` existem nesta versão.
  - GPU NVIDIA com `-hwaccel cuda` para o único teste de interação com `--accel gpu`, que usa `skipUnless`.
  - `gitleaks` 8.30.1 e `pip_audit` 2.10.1 (via `.venv`) para os controles de segurança de C4.
  - As fontes de teste (GOP curto de 25 a 30 s em baixa resolução, e um clipe 1080p curto para a elegibilidade do proxy) são geradas no próprio teste por `ffmpeg -f lavfi`, sem fixture grande no repositório.
- **Ausências e ação na fila:** nenhuma. O benchmark 4K de C1 gera sua fonte sintética no disco temporário (cerca de 700 MB) e a apaga ao terminar.

## Tasks

### T1: Planejar janelas de decode e aplicá-las ao grafo com saída equivalente

- [x] T1 concluída
- **Prova:** `tests.test_decode_windows` -> Ran 10 tests, OK (equivalência `framemd5` de vídeo e áudio em 6 cenários de borda; mutação de +1 quadro no início da janela derruba os 6); `tests.test_preview_render tests.test_full_render tests.test_plan_validation tests.test_gpu_accel` -> Ran 64 tests, OK.
- **Arquivos:** `src/editor/render/windows.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/render/preview.py`, `tests/test_decode_windows.py`
- **Decisões:** pre-roll de áudio dispensado (decisão 4 inalterada no comportamento); AAC com PNS diverge após seek de forma inaudível, aceito e registrado na decisão 4 e como `ponytail`; a fonte de teste usa `-aac_pns 0`. O áudio é comparado após `asetnsamples=n=1024`, porque o seek muda só o tamanho dos blocos entregues pelo decoder; a T2 compara o MP4 decodificado. `input_source_ids` repete o id por janela, então a regra do decode GPU já conta entradas de vídeo (decisão 7). Teto: só sai idêntico ao atual quando as fontes de vídeo distintas passam de 24; nos demais estouros mescla pela menor lacuna e reavalia o ganho de 20% a cada mescla, com desempate por ordem de referência.
- **Spec:** A1, A2, A3, C3
- **O quê:** um planejador puro calcula, por fonte de vídeo, as janelas a partir da união dos intervalos de segmentos, camadas de vídeo e trilhas de áudio; `build_ffmpeg_graph` ganha o parâmetro `windows` (padrão `False`, o render ainda não o liga) e `FfmpegGraph` ganha `input_windows`; `_build_ffmpeg_arguments` emite `-ss`/`-t` por entrada e o grafo desloca `trim`, `atrim` e os recortes das camadas de vídeo pelo início da janela.
- **Aceite:**
  - [ ] O planejador mescla lacunas menores que 2 s, aplica folga final de 0,5 s, respeita 8 janelas por fonte e 24 entradas de vídeo no total (sempre uma por fonte), só janela fonte `video` e só quando a leitura estimada cai pelo menos 20%; é determinístico para o mesmo plano.
  - [ ] Sem ganho ou com teto estourado, o grafo e o comando de entrada saem idênticos aos de hoje (nenhum `-ss`).
  - [ ] Com janelas, o `framemd5` do vídeo e do áudio produzidos pelo grafo é igual ao do grafo sem janelas, numa fonte H.264/AAC de GOP curto gerada no teste.
  - [ ] Casos de borda iguais: trecho no início da fonte, no fim, trechos adjacentes, tempos exatamente sobre fronteira de quadro, camada de vídeo e trilha de áudio da mesma fonte, trilha com `loop` e cortes J e L.
  - [ ] Se o áudio divergir com início exato de janela (contexto do decoder AAC), a task acrescenta pre-roll de início no planejador e atualiza a decisão 4 da spec no mesmo commit.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_decode_windows -v`
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_preview_render tests.test_full_render tests.test_plan_validation tests.test_gpu_accel -v`
- **Deps:** nenhuma
- **Arquivos:** `src/editor/render/windows.py` (novo), `src/editor/render/ffmpeg_graph.py`, `src/editor/render/preview.py` (`_build_ffmpeg_arguments`), `tests/test_decode_windows.py` (novo)
- **Risco:** a equivalência depende de detalhes do FFmpeg (início do `start_time`, contexto do decoder de áudio, fronteiras de quadro); por isso a prova por `framemd5` entra nesta task e antes de qualquer ligação ao render. Os tempos do plano e o hash canônico nunca mudam; só o grafo desloca.

### T2: Renderizar preview e full com janelas por padrão, com fallback, envelope e ganho medido

- [x] T2 concluída
- **Prova:** suíte da Verificação -> 50 testes OK em 114,5 s (`WindowedRenderTests`: preview e full reais com `framemd5` de vídeo e áudio iguais com e sem janelas; fallback gpu+janelas -> cpu+janelas -> cpu sem janelas; timeout com tentativa única preservando a preview vigente; `.tmp` vazio e mensagem sem stderr; 4 janelas com `-hwaccel`, 5 sem e encoder NVENC mantido). `bench_render.py --scenario windows --reps 3` (4K, GOP 1 s, 60 s, 6 trechos de 5 s, preview CPU): relógio mediano 12,58 s -> 10,86 s (-13,7%), CPU mediana 97,20 s -> 70,05 s (-27,9%).
- **Arquivos:** `src/editor/render/preview.py`, `src/editor/render/full.py`, `scripts/bench_render.py`, `tests/test_decode_windows.py`, `tests/test_preview_render.py`
- **Decisões:** corpo de render único (`_render_artifact`) para preview e full; o laço `_render_with_fallback` recebe tentativas (grafo, modo) e cria o script `-/filter_complex` por tentativa, removendo todos no `finally`; `_video_input_count` é a fonte única da contagem de entradas (GPU e `data.decode`). O teste de grafo grande repassa `windows`/`input_windows` no fake, sem mudar asserção. O benchmark mede CPU de descendentes por Job Object no Windows (sem `psutil`) e força a variante sem janelas por patch no processo filho, sem flag no produto. C1 da T2 ajustado pelo humano para CPU.
- **Spec:** A1, A3, C1, C2
- **O quê:** `render_preview` e `render_full` constroem o grafo com e sem janelas e tentam, em ordem, o modo pedido com janelas, o modo CPU com janelas e o CPU sem janelas; o envelope informa `data.decode` (`windowed`, `inputs`, `sources`); a regra do decode por GPU passa a contar entradas de vídeo; `scripts/bench_render.py` mede relógio e CPU consumida com repetições intercaladas.
- **Aceite:**
  - [ ] Preview e full reais com janelas publicam MP4 válido com os mesmos quadros e áudio do render sem janelas (comparação por `framemd5` da saída decodificada).
  - [ ] Falha simulada do render com janelas (sem timeout) repete sem janelas e informa `windowed: false`; timeout não repete.
  - [ ] Sem ganho, `windowed` é `false` e o comando de entrada é o atual.
  - [ ] Com mais de 4 entradas de vídeo, o decode por GPU não é usado e o encode segue como pedido.
  - [ ] Medição no cenário sintético de C1 (fonte 4K H.264 de GOP 1 s, 6 trechos de 5 s de 60 s): render com janelas consome pelo menos 20% menos CPU que sem janelas, sem piora de relógio (mediana de 3 execuções intercaladas). Critério ajustado pelo humano em 2026-10-07; a meta original era 20% de relógio.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_decode_windows tests.test_gpu_accel tests.test_preview_render tests.test_full_render tests.e2e.test_mvp_flow tests.e2e.test_phase_2_flow -v`
  - [ ] `.venv\Scripts\python.exe -B scripts\bench_render.py --scenario windows --reps 3`
- **Deps:** T1
- **Arquivos:** `src/editor/render/preview.py`, `src/editor/render/full.py`, `scripts/bench_render.py` (novo), `tests/test_decode_windows.py`
- **Risco:** altera o caminho de render usado por todas as jornadas; a ordem de tentativas combina `--accel gpu` e janelas e precisa preservar a limpeza de `.tmp` e a preservação do artefato vigente em falha.

### T3: Construir e validar proxies com `editor proxy build`

- [ ] T3 concluída
- **Checkpoint:** delegada ao implementador (em paralelo à pergunta da T2, paths disjuntos); próximo passo: conferir o relatório; HEAD `2bb386709bb0e01fdfb9efc4fb55bb60d0e77123`.
- **Spec:** A4, A5, C4
- **O quê:** comando `editor proxy build --workdir <abs> [--source-id <id>]... --json` que gera, para cada fonte de vídeo elegível, `proxy/<id>.mp4` e `proxy/<id>.json` por substituição atômica, reutiliza proxy válido e reporta `created`, `reused`, `skipped` ou `failed` por fonte.
- **Aceite:**
  - [ ] Fonte maior que 1280x720 gera proxy H.264 `yuv420p` que cabe em 1280x720 sem upscale, GOP 15, `crf 20` e áudio por cópia; fonte menor é `skipped`.
  - [ ] O proxy só é publicado depois de a verificação confirmar a mesma contagem de pacotes de vídeo, `start_time` com diferença de até 1 ms e duração com diferença de até um quadro; reprovado vira `failed` (`STREAM_MISMATCH`) e nada é publicado.
  - [ ] Segunda execução devolve `reused` sem tocar nos arquivos; fonte alterada (tamanho ou `mtime_ns`) torna o proxy obsoleto e a execução seguinte o refaz.
  - [ ] Falha de uma fonte não remove proxy válido anterior nem interrompe as demais; `.tmp` fica limpo em sucesso, erro, exceção e timeout.
  - [ ] `--source-id` fora da allowlist ou ausente do plano retorna `ARGUMENT_INVALID`; metadado fora do formato (campo extra, tipo errado ou mais de 4 KiB) conta como proxy obsoleto, nunca como erro.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_proxy_build tests.test_cli_contract -v`
- **Deps:** nenhuma
- **Arquivos:** `src/editor/render/proxy.py` (novo), `src/editor/workdir.py` (layout e publicação da pasta `proxy`), `src/editor/cli.py` (grupo `proxy`), `tests/test_proxy_build.py` (novo)
- **Risco:** preservar os timestamps exatos do original no proxy (`-fps_mode passthrough` e base de tempo do encoder) é o ponto crítico; a verificação por pacotes, `start_time` e duração é a prova, e a task investiga a combinação de opções que a satisfaz antes de fixar os argumentos.

### T4: Usar proxies na preview com `--proxy auto|off`

- [ ] T4 concluída
- **Spec:** A6, A7, C2
- **O quê:** `render preview` (e o atalho `preview`) aceita `--proxy auto|off` (padrão `auto`); com `auto`, cada fonte elegível com proxy válido é lida pelo proxy, as demais pelo original, e as janelas da T2 se aplicam ao proxy; o envelope informa `data.proxy` (`mode`, `sources`, `stale`); `render full` não muda.
- **Aceite:**
  - [ ] Preview com proxy numa fonte e original noutra usa cada uma como esperado e informa `mode`, `sources` e `stale` corretos; `--proxy off` e ausência de proxies reproduzem a preview de antes da entrega.
  - [ ] Proxy obsoleto aparece em `stale` e a fonte original é usada.
  - [ ] `approve` registra o hash da preview vigente e os mesmos `plan_sha256` e hashes dos originais com e sem proxy; a full parte dos originais e tem os mesmos quadros e áudio nos dois casos.
  - [ ] `--proxy` com valor fora de `auto` e `off` retorna `ARGUMENT_INVALID`.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_preview_proxy tests.test_approval tests.test_full_render tests.test_cli_contract tests.test_preview_render -v`
- **Deps:** T2, T3
- **Arquivos:** `src/editor/render/preview.py`, `src/editor/cli.py`, `tests/test_preview_proxy.py` (novo)
- **Risco:** a preview com proxy deixa de ser bit a bit idêntica à sem proxy (aceito na spec); o ponto a vigiar é a full e a aprovação nunca lerem proxy.

### T5: Expor, documentar e provar a entrega

- [ ] T5 concluída
- **Spec:** A8, C1, C2, C4
- **O quê:** `capabilities` informa a operação `proxy_build`, as janelas de decode e o `--proxy`; a skill `vibe-editor` e o `REGRAS.md` documentam as capacidades, o mapa de estrutura e as medições; o benchmark prova o ganho do proxy e fecha a tabela de C1.
- **Aceite:**
  - [ ] `capabilities --json` expõe `operations.proxy_build`, `render.decode_windows` e `render.proxy` sem quebrar os campos existentes.
  - [ ] `SKILL.md` descreve `proxy build` e `--proxy`; o `REGRAS.md` atualiza a estrutura (novos módulos, testes e script), as seções das ferramentas e as limitações com `ponytail`, e registra a tabela de medições de relógio e CPU consumida.
  - [ ] A preview com proxy leva no máximo 50% do tempo da preview sem proxy no cenário de C1; se ficar acima, o valor real é registrado e informado ao humano.
  - [ ] Suíte completa verde, `gitleaks` e `pip-audit` sem achados.
- **Verificação:**
  - [ ] `.venv\Scripts\python.exe -B -m unittest tests.test_capabilities tests.test_cli_contract -v`
  - [ ] `.venv\Scripts\python.exe -B scripts\bench_render.py --scenario proxy --reps 3`
  - [ ] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`
  - [ ] `python -X utf8 C:\Users\marco\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\vibe-editor`
  - [ ] `gitleaks detect --source . --no-banner` e `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip`
- **Deps:** T4
- **Arquivos:** `src/editor/capabilities.py`, `skills/vibe-editor/SKILL.md`, `.vibeflow/REGRAS.md`, `scripts/bench_render.py`, `tests/test_capabilities.py`

## Paralelização

- Grupo: T1 e T3
- Motivo: não dependem uma da outra e usam conjuntos de arquivos disjuntos (T1 em `windows.py`, `ffmpeg_graph.py` e `_build_ffmpeg_arguments`; T3 em `proxy.py`, `workdir.py` e o grupo `proxy` do `cli.py`).
- Isolamento: T1 não toca `cli.py` nem `workdir.py`; T3 não toca `preview.py`, `full.py` nem `ffmpeg_graph.py`; cada uma cria o próprio arquivo de teste.

## Checkpoint de review

- Após a T2: o grafo com janelas é o contrato compartilhado por preview, full e proxies, e a equivalência de quadros e áudio é o risco central; julgar o planejador, o deslocamento dos tempos e a ordem de tentativas antes de a T4 construir sobre ele.

## Handoff

vibe-implement

- Chat: recomende novo chat para `vibe-implement`, que roda em Modo A e, num host com subagentes, conduz a fila inteira e só volta ao humano na pergunta final; o grupo T1 e T3 só é oferecido em paralelo no Modo B. Continuar neste chat é válido; o `plan.md` vivo é a ponte.
