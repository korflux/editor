# Review: proporções de saída e reenquadramento por keyframes
# Alvo: phase-5-proporcoes-e-reenquadramento
# Status: aprovado

## Contexto

- Alvo: phase 5. Etapa 1: commits `7bc75a1` (T1), `05dd8eb` (T2), `21cfa3b` (T3) e `21aeea2` (T4) sobre a base `eaa8473`. Etapa 2: adendo de escopo `1a3bc21`, rodada `3ea7054` (R1 a R4), `23e71b2` (T5), `800118b` (T6), `a193ff3` (T7), `1b6e25f` (T8), `ae32ffd` (T9), `394820f` (T10) e `61b42cd` (docs), sobre `21aeea2`. Etapa 3: rodada de correção `acf9fc7` (R5 a R8), sobre `61b42cd`. Etapa 4: `f27991c` (T11, versão 1.1.0 e `tmp-*/` no `.gitignore`, decididos pelo humano), sobre `acf9fc7`. O commit `d89573d` é trabalho do humano anterior à phase e ficou fora do escopo.
- Cadeia: `interview.md` (confirmado, untracked) / `spec.md` (aprovado; o adendo de 08/10, decisões 15 a 20 e aceites A9 a A15, foi decidido pelo coordenador; o humano respondeu "siga suas recomendações" às escolhas de produto, o que gerou as decisões 21 e 22 e o aceite A16; a leitura do adendo inteiro entra no "aprovado" final) / `plan.md` (aprovado, T1 a T11 concluídas) / sem `analyze.md` (rota high).
- O que muda: `profile.aspect_ratio` aceita 16:9, 9:16, 1:1 e 4:5; `segments[].video.reframe` recorta por keyframes; `size_px`, `shadow` e `outline` do texto valem na largura da full; `upscale` considera `motion`; a full 16:9 mantém o canvas legado e usa largura alinhada a 32 só onde ele era recusado; o seek das janelas de decode recua 0,25 s para o áudio coincidir com o sem janelas; fontes `.mov` e HEVC Main/Main 10 são lidas direto com saída sempre em 8 bits; `render full --quality standard|high|max`; ajuda de `--input` e limpeza das pastas temporárias dos testes.

## Tipo de review

- Tipo: final.
- Marco e justificativa: integração final; a fila do `plan.md` (T1 a T11 e as rodadas R1 a R4 e R5 a R8) está concluída, sem checkpoint declarado.
- T* abertas fora do marco: nenhuma.
- Provas reaproveitadas:
  - A suíte completa de `f27991c` rodada pelo corretor no estado final (335 testes OK, sem `tmp-*` na raiz). Depois de `acf9fc7` só mudaram 4 constantes de versão e uma regra de `.gitignore`.
  - As provas de código da Etapa 3 em `acf9fc7`.
  - O `pip_audit` da T4: `requirements.lock` não mudou desde `eaa8473`, e em `pyproject.toml` só mudou a linha `version` (`dependencies = []` igual).
- Provas executadas: ver Etapas 2, 3 e 4.

## Cobertura

| Chave | Código | Notas |
|---|---|---|
| A1 | ok | `plan/schema.py` (`ASPECT_RATIOS`, `FIXED_CANVAS_DIMENSIONS`, `canvas_dimensions`), `plan/validation.py`; `tests/test_aspect_ratio.py`. |
| A2 | ok | `_validate_reframe`; violações recusadas antes de `inspect_source` em `tests/test_reframe.py`. |
| A3 | ok | `_upscale_factor` com `motion` (decisão 15); golden 16:9 informa 2.0 e 2.25, fixture `e2e` 1.5 (Etapa 2). |
| A4 | ok | Etapa 2 achou o R5 (mistura de ramos falhava fora do 16:9 e o SAR publicado não era 1:1); na Etapa 3, com `acf9fc7`, 29 combinações e uma matriz com 4 fontes de proporções diferentes, camadas e texto publicam preview e full com SAR 1:1 nas quatro proporções. |
| A5 | ok | Oráculo de posição em `tests/test_reframe.py` (etapa 1). |
| A6 | ok | Full 16:9 idêntica ao código pré-phase em 5 planos, inclusive sem e com janelas (Etapa 2); texto da preview proporcional. |
| A7 | ok | `tests/e2e/test_phase_5_flow.py` (só segmentos com `reframe`, por isso não pegou o R5). |
| A8 | ok | `capabilities --json` conferido na Etapa 2. |
| A9 | ok | Tabela independente de 479.880 pares (largura 2 a 4000 x 120 tetos): 0 mudanças onde o legado renderizava; 110.544 recusados viram canvas exato; fonte 1200x674 sai 1184x666 pela CLI (antes `RENDER_FAILED`). |
| A10 | ok | 50 renders independentes com e sem janelas (GOP 25 sem B a 48 e 32 kHz, GOP 50 com B a 44,1 kHz, proxy, `--accel gpu`): PCM e vídeo idênticos; os mesmos 10 cenários falham no código `21aeea2`. |
| A11 | ok | R1 e R2 conferidos no diff e no envelope. |
| A12 | ok | 23 ataques de validação recusados sem eco; grafo do texto conferido; render real com sombra e contorno em HEVC 10 bits 9:16. |
| A13 | ok | 42 payloads FFprobe forjados sem furo de `v:0`/`a:0`; jornada HEVC Main 10 `.mov` com `tmcd`: proxy, preview janelada com proxy, full CPU e GPU em H.264 `yuv420p`. Só fontes sintéticas (nota ao humano). |
| A14 | ok | Vetores `standard` idênticos aos de `21aeea2` (CPU e GPU); full `standard` idêntica ao pré-phase; `high`/`max` na GPU saem em nível 5.2 com bitrate crescente. |
| A15 | ok | R3, R4 e os 8 registros conferidos com o hash certo (datas: ver Notas). |
| A16 | ok | Versão `1.1.0` em `pyproject.toml`, `contracts.CLI_VERSION`, `--version` (texto e JSON) e `capabilities.cli_version`; `tomllib` coincide com a constante; `tmp-*/` no `.gitignore` (`git check-ignore` aponta `.gitignore:21`) sem casar arquivo versionado (Etapa 4). |
| C1 | ok | Suíte completa de 335 testes OK em `acf9fc7` e em `f27991c` (corretor) e amostras nas Etapas 2 e 3; o caminho do R5 ganhou `MixedFitSampleAspectRatioTests`. |
| C2 | ok | Etapa 1. |
| C3 | ok | Nenhum plano ou fonte antes aceito passa a ser recusado (T7 só acrescenta campos; T8 só amplia a allowlist). |
| C4 | ok | `REGRAS.md`, `capabilities` e skill coerentes; as três imprecisões da Etapa 2 (R6, R7 e R8) foram corrigidas e conferidas na Etapa 3. |

## Checklist de correções

Nenhum bloqueio aberto: o R5, único Required, foi confirmado na Etapa 3.

### Required

- [x] R5: **Required** - `src/editor/render/ffmpeg_graph.py:385-435` (`_video_segment_filter`) - o `setsar=1` é aplicado à fonte ANTES do `scale`, e os ramos `crop` central (`scale ... increase,crop`) e `contain`/`preserve` (`scale ... decrease,pad`) terminam sem normalizar o SAR, enquanto os ramos `reframe` e `motion` terminam num `overlay` sobre `color` (SAR 1:1). Quando a escala não é exata o `scale` grava SAR diferente de 1:1 e o `concat` recusa segmentos com SAR diferentes (`Input link in0:v0 parameters (size 1080x1920, SAR 1:1) do not match the corresponding output link in0:v0 parameters (1080x1920, SAR 10240:10239)`). No 16:9 com fonte 16:9 a escala é exata e o defeito não aparece; fora do 16:9 ele atinge o caso central da phase. Cenários reproduzidos só com H.264 1920x1080: 9:16 com um trecho `crop` central e outro com `reframe` (ou `motion`): preview OK, `approve` OK e `render full` falha com `RENDER_FAILED`; 9:16 `crop` + `contain`, `reframe` + `contain` e `preserve` de fonte 1080x1920 + `crop`: preview falha; 1:1 e 4:5 `crop` + `contain` e `reframe` + `contain`: full falha depois da aprovação. Os MP4 publicados de um só ramo saem com SAR diferente de 1:1 (9:16 `crop`: full 10240:10239; `contain`: preview 908:909 e full 1216:1215; 9:16 `preserve` nativo: preview 3231:3232). Origem na T1 (o código `21aeea2` reproduz igual); a mesma causa já existia no 16:9 com fonte fora de 16:9 (fonte 1000x562, `crop` + `motion`, falha também em `eaa8473`). As provas da phase não pegaram porque `tests/test_aspect_ratio.py` usa `single_segment_plan` e o E2E 9:16 só tem trechos com `reframe`; nenhum teste confere SAR. - remédio: `vibe-implement` normalizar o SAR na origem comum, `_video_segment_filter`: acrescentar `setsar=1` depois do `crop`/`pad` dos ramos `crop` central, `contain` e `preserve` (antes do `format=yuv420p` e da entrada do `motion`); escolher, com prova, entre emitir sempre (regravando os goldens de `filter_complex` só com `framemd5` da full 16:9 inalterado nas fixtures `workflow`, `e2e` e no golden) ou só quando a escala não é exata (goldens intactos), respeitando a decisão 14; acrescentar teste de render real de preview e full em 9:16, 1:1 e 4:5 sobre fonte 1920x1080 misturando `crop` central com `reframe`, com `motion` e com `contain`, e 9:16 `preserve` de fonte 1080x1920 com `crop`, exigindo publicação e `sample_aspect_ratio` 1:1 por FFprobe, mais o caso 16:9 com fonte 1000x562; opcionalmente exigir SAR 1:1 na checagem pós-render. - prova: `sar_check.py` e `sar_check.py --extra` no scratchpad (HEAD e `21aeea2` com o mesmo resultado), `odd_check.py` (`eaa8473` e HEAD) e o comando montado por `_build_ffmpeg_arguments` com o stderr do `concat` (Etapa 2). - source: A4, F2 passos 2 e 3, C1 - gap: partial. Confirmado na Etapa 3: `acf9fc7` acrescenta `setsar=1` sempre ao fim dos ramos `crop`, `contain` e `preserve` (antes do `motion` e do `format=yuv420p`). Os 29 cenários da Etapa 2 publicam preview e full com SAR 1:1 no HEAD, inclusive 16:9 com fonte 1000x562. Uma matriz por proporção com fontes 1920x1080, 1080x1920, 640x480 e 1000x562, todos os ramos, camadas de imagem e de vídeo, texto com sombra e contorno, corte J, janelas, `--proxy off` e `auto`, full CPU e full GPU `high` também publicou tudo em SAR 1:1. Pixels: `framemd5` de vídeo e áudio da full (com e sem janelas) e da preview idênticos a `eaa8473` e a `61b42cd` no golden, no `workflow` e no `e2e`, e a `61b42cd` no sintético. A fonte 1200x674 só muda o cabeçalho `#sar` (de 199800:199841 para 1:1), com quadros idênticos. Os grafos diferem só pelo `,setsar=1` inserido: normalizados, voltam ao texto antigo. Os quatro goldens regravados têm a justificativa ao lado, e o texto normalizado devolve o hash antigo.

### Optional / Nit

- [x] R1: **Nit** - `src/editor/plan/validation.py:386` - `_upscale_factor` ignora o `zoom` do `motion`: um segmento 16:9 1920x1080 com `motion` de zoom 1.0 a 2.0 devolve `upscale: []`, enquanto o mesmo punch-in por `reframe` devolve `factor: 2.0`; a frase "lista vazia significa que nenhum segmento é ampliado" (spec decisão 9, `capabilities.py:361`, `SKILL.md:235`) fica falsa nesse caso. A fórmula da spec ("o recorte central quando não há reframe") exclui o `motion`, então não é desvio de implementação. - remédio: `vibe-implement` multiplicar o fator pelo maior `zoom` de `motion.from`/`motion.to` ou registrar a exclusão como `ponytail` em `REGRAS.md`, `capabilities` e skill, conforme a decisão do humano. - prova: script descartável de validação com metadados simulados (Etapa 1). - source: A3 / decisão 9 - gap: partial. Confirmado na Etapa 2: `3ea7054` multiplica pelo maior `zoom` do `motion` em qualquer `fit` (golden 16:9: `contain` + `motion` 1.5 = 2.25; fixture `e2e`: `preserve` + `motion` = 1.5).
- [x] R2: **Nit** - `skills/vibe-editor/references/youtube-editing.md:168` - descreve `upscale` como "razão entre a largura do canvas da full e a largura do menor recorte usado", mas a implementação também lista `contain` e `preserve` com `min(canvas/fonte)` (`validation.py:403`); `SKILL.md`, `REGRAS.md` e `capabilities` usam a formulação genérica. - remédio: `vibe-implement` alinhar a frase à semântica confirmada pelo humano (recorte no `crop`, escala da imagem inteira no `contain`/`preserve`). - prova: leitura cruzada dos quatro textos. - source: C4 - gap: partial. Confirmado na Etapa 2: a mesma regra (recorte no `crop`, escala inteira em `contain`/`preserve`, multiplicada pelo `motion`, lista vazia = ninguém ampliado) está em `youtube-editing.md:169`, `SKILL.md`, no comentário de `capabilities.py` e em `REGRAS.md`.
- [x] R3: **Nit** - `.erros-encontrados/2026-10-08-teto-32767-e-da-linha-de-comando-e-nao-do-grafo.md` - a sugestão de correção (reescrever spec, plan e `REGRAS.md`) foi aplicada na T2 e na T4, mas o registro não está marcado como resolvido, ao contrário do Problema 1 do registro do `size_px`. - remédio: `vibe-implement` marcar o registro como resolvido, com os commits. - prova: `rg -n "32.767" .vibeflow/REGRAS.md .vibeflow/phases/phase-5-proporcoes-e-reenquadramento/spec.md`. - source: diff - gap: nenhum. Confirmado na Etapa 2: marcado como resolvido com `05dd8eb` e `21aeea2`.
- [x] R4: **Optional** - `skills/vibe-editor/references/canal-sonni-viaja.md:125` - "No editor, `fit: preserve` nos clipes e sem `video.motion`" não cita `video.reframe`, que agora também aplica zoom e recorte proibidos pelo estilo do canal. - remédio: `vibe-implement` acrescentar "nem `video.reframe`". - prova: leitura do trecho. - source: diff (T4, skill) - gap: partial. Confirmado na Etapa 2: "sem `video.motion` e sem `video.reframe`".
- [x] R6: **Nit** - `.vibeflow/REGRAS.md:340` - "os tetos de 4 GiB, 3.600 s e dimensão seguem" para fontes audiovisuais, mas não existe teto de dimensão de vídeo: `media._validate_probe_payload` só aplica tamanho e duração, `MAX_IMAGE_DIMENSION` vale só para imagens e `capabilities.limits` não lista teto de vídeo (um payload HEVC Main de 16384x16384 é aceito). Vale também para H.264 desde o MVP; a T8 ampliou a superfície (HEVC chega legitimamente a 8192x4320). - remédio: `vibe-implement` corrigir o texto (tirar "e dimensão"); criar um teto de dimensão de vídeo (constante única em `media.py`, `capabilities` e teste) só se o humano decidir endurecer a superfície. - prova: `payload_attack.py` e o payload 16384x16384 da Etapa 2. - source: C4 / decisão 19 - gap: contradicts. Confirmado na Etapa 3: `REGRAS.md` (linhas 59 e 340) diz que não existe teto de dimensão de vídeo e registra o `ponytail`, também anotado em `media.py`. Nenhum texto de `REGRAS.md`, da skill, da spec, do plan ou de `capabilities` afirma um teto de vídeo: as menções a 8.192 pixels são só de imagem. O teto não foi criado; a escolha de endurecer fica com o humano.
- [x] R7: **Nit** - `src/editor/media.py:224-235` com `src/editor/render/preview.py:626` - a checagem pós-render (`inspect_rendered_artifact`) usa a mesma allowlist das fontes e, desde a T8, aceita um MP4 renderizado em HEVC Main/Main 10; antes ela só aceitava `h264`. A spec (F2 passo 3: "só é publicado se for H.264/AAC") e `REGRAS.md:208` ("validado como MP4 H.264/AAC") descrevem a checagem antiga. Sem impacto prático, porque o encoder é fixo (`libx264`/`h264_nvenc`). - remédio: `vibe-implement` exigir `video_codec == "h264"` (e `audio_codec == "aac"` quando há áudio) em `_validate_rendered_preview` ou em `inspect_rendered_artifact`, com teste por metadados simulados. - prova: `payload_attack.py` ("ARTEFATO RENDERIZADO HEVC ACEITO"). - source: F2 / decisão 19 - gap: contradicts. Confirmado na Etapa 3 com uma recusa real (`r7_refusal.py`, encoder trocado por `libx265` via patch): a preview e a full saem com `RENDER_FAILED` e mensagem fixa ("A preview renderizada não usa os codecs H.264 e AAC do contrato.", sem o codec). Preview, full e `approval.json` vigentes ficam preservados byte a byte e `.tmp` fica vazio. Áudio MP3 também é recusado, já pela allowlist de `.mp4`. `libx264` e `h264_nvenc` (preview e full por GPU) seguem publicando `h264`/`aac`.
- [x] R8: **Nit** - `src/editor/render/proxy.py:207-211` - o docstring afirma "O proxy só leva esses dois streams, sem trilhas de dados nem miniatura", mas com fonte que tem timecode o muxer recria a trilha `tmcd` no proxy (FFprobe: 3 streams em `proxy/cam.mp4` de uma fonte `.mov` HEVC com `tmcd`). Inofensivo: os consumidores usam `v:0`/`a:0` e a validação do proxy passou. - remédio: `vibe-implement` acrescentar `-write_tmcd 0` ao `proxy build` (como `source-preparation.md` já orienta), sem mudar `PROXY_PARAMETERS_VERSION` se o conteúdo de vídeo e áudio não mudar, ou corrigir o docstring. - prova: `integration.py` (Etapa 2). - source: decisão 19 - gap: contradicts. Confirmado na Etapa 3 (`r8_proxy.py`, código `61b42cd` contra o HEAD): o proxy de fonte HEVC `.mov` e `.mp4` com `tmcd` passa de 3 para 2 streams (vídeo e áudio). O MD5 por pacote é idêntico (360 de vídeo e 564 de áudio no `.mov`; 150 e 260 no `.mp4`), então `PROXY_PARAMETERS_VERSION` segue 1. O proxy publicado pelo código anterior é `reused` pelo `proxy build` do HEAD e lido pela preview (`proxy.sources` com a fonte, `stale` vazio).

## Decisões do humano

Na confirmação de 09/10 (depois da Etapa 3), o humano respondeu "siga suas recomendações" à lista de escolhas de produto. Situação de cada item:

| # | Escolha | Decisão | Situação |
|---|---|---|---|
| 1 | `upscale` lista também `contain` e `preserve` (escala da imagem inteira) e multiplica pelo `zoom` do `motion` | Manter | Aplicada (decisão 15, já no código) |
| 2 | Full 16:9: canvas legado (alinhado a 16) mantido onde já era renderizado, alinhamento a 32 só onde era recusado | Manter; unificar em 32 numa versão major (`ponytail`) | Aplicada (decisão 16, já no código) |
| 3 | `-level 5.2` no NVENC em `high` e `max` (desvio da decisão 20) | Aceitar, com o `ponytail` de derivar o nível se algum player recusar | Aplicada (já no código) |
| 4 | `standard` da GPU (`cq 31`) com SSIM menor que o da CPU | Não mexer; usar `high` quando a full for por GPU | Aplicada (contrato herdado mantido) |
| 5 | Teto de dimensão de vídeo (R6) | Fazer depois, em tarefa própria, com sugestão de 8192 por lado; não agora | Adiada (`ponytail` em `media.py` e `REGRAS.md`) |
| 6 | Versão do pacote | Subir para `1.1.0` | Aplicada na T11 (`f27991c`, decisão 21, A16) |
| 7 | Limpeza dos testes que avisa em vez de falhar | Aceitar e acrescentar `tmp-*/` ao `.gitignore` | Aplicada na T11 (`f27991c`, decisão 22, A16) |
| 8 | HEVC e MOV provados só com fontes sintéticas; HDR sem tone mapping | Aceitar, com um teste em bruto real de câmera antes de editar um episódio | Pendente: o humano precisa indicar o arquivo |

## Decisão para o humano

- "Aprovado" final: ler este `review.md` e confirmar o Approve. A confirmação cobre também o adendo inteiro (decisões 15 a 22, aceites A9 a A16, T5 a T11). Cobre ainda a correção de metadado do R5: os goldens de `filter_complex` foram regravados com prova por pixels, e os MP4 de fonte 16:9 fora da proporção exata passam a declarar SAR 1:1 com os mesmos quadros.
- Item 8: indicar um bruto real de câmera (DJI) ou um MOV de celular para o teste de `analyze audio`, `extract frames` e `render preview` antes de editar um episódio. Não bloqueia a finalização da phase.
- Item 5: abrir, quando o humano quiser, a tarefa própria do teto de dimensão de vídeo (sugestão 8192 por lado, constante única em `media.py`, `capabilities` e teste). Não bloqueia a finalização da phase.

## Segurança

- Superfícies novas: `plan.json` (`reframe`, `shadow`, `outline`) virando filtros do FFmpeg; arquivo de mídia não confiável (`.mov`, HEVC) lido por FFprobe, FFmpeg e, na transcrição, pelo PyAV do faster-whisper; argumento `--quality`.
- `shadow`/`outline` (T7): allowlist (`SHADOW_FIELDS`, `OUTLINE_FIELDS` com `reject_unknown_fields`), obrigatórios, inteiros sem booleano nem decimal, opacidade por `_finite_number` (recusa texto, `null`, booleano e inteiro gigante sem exceção não tratada), cor por `HEX_COLOR_PATTERN.fullmatch` (recusa `\n` final, `@alfa`, `:s=...`, nome de cor e dígito Unicode); tudo em `_validate_layers_structure`, antes de inspecionar mídia; mensagens fixas sem eco. No grafo entram só a cor validada, inteiros escalados e números por `_number`; o texto continua pelo `_escape_filter_value` com `expansion=none` (conferido com texto hostil `a':b,c;[x]\%{n}`). Custo: `gblur` por quadro limitado ao intervalo da camada por `trim`; com até 1.000 camadas o render fica lento mas é limitado pelo timeout do FFmpeg (ferramenta local, plano escrito pelo operador): FYI, sem achado.
- HEVC/MOV (T8): allowlist fechada de contêiner, codec, perfil e `pix_fmt`; 42 payloads forjados (disposição como texto, `True`, lista ou ausente; miniatura antes, depois ou sozinha; perfil com caixa e espaços; `p010le`, `yuvj420p`, Rext, 4:2:2, 12 bits; áudio antes do vídeo; vídeo depois de dado; dois vídeos ou dois áudios; legenda, `attachment`, `codec_type` maiúsculo ou ausente; `.mov` com `format_name` matroska; `.MOV`; `.wav` com vídeo; `.mp3` com capa) e nenhum caso em que `v:0`/`a:0` deixa de ser o stream validado (as duas divergências do roteiro foram recusas mais estritas que o esperado: miniatura como único vídeo). Conversão para 8 bits conferida no grafo, na camada de vídeo (`format=rgba`), no proxy, no PNG e no `--accel gpu`. Demux de arquivo não confiável: o mesmo demuxer `mov` que já lia `.mp4`; decode HEVC no FFmpeg local, fora do processo Python; limites de 4 GiB, 3.600 s e timeouts mantidos; não há teto de dimensão de vídeo (R6, agora documentado e mantido como decisão do humano). A checagem pós-render, que tinha deixado de exigir H.264 (R7), voltou a exigir `h264` e `aac` na Etapa 3, com recusa real conferida.
- `--quality` (T9): `choices` no `argparse` e `_quality_preset` no núcleo; `render full --quality 'ultra;rm'` e `render preview --quality high` devolvem `ARGUMENT_INVALID` (exit 2, "Argumentos inválidos.", sem eco); o FFmpeg só recebe os inteiros e o nível da tabela.
- Segredos: `gitleaks detect --source . --no-banner` sem achados (71 commits na Etapa 2 e de novo na Etapa 3, com `acf9fc7`).
- Nada explorável no que o diff tocou.

## Notas

- R5 (o único bloqueio da Etapa 2) e os Nits R6 a R8 foram corrigidos em `acf9fc7` e confirmados na Etapa 3. O erro de áudio das janelas registrado na etapa 1 foi resolvido pela T6 e conferido na Etapa 2.
- FYI (R7): a conferência de `aac` em `_validate_rendered_preview` é quase sempre redundante, porque a allowlist de `.mp4` de `media.py` já recusa outro áudio antes (o MP3 cai em "não passou na validação de mídia"). É defesa barata e não precisa mudar. A checagem pós-render não confere o SAR (`MediaMetadata` não o carrega), como o plan registra; quem prova o SAR 1:1 são os testes por FFprobe.
- FYI (T6): o `ponytail` diz que a estimativa de ganho ignora "até 0,25 s por janela", mas quando o corte cai até 0,25 s depois de um keyframe o recuo cruza para o GOP anterior e o decode extra chega a um GOP inteiro por janela; a decisão de janelar e o número de entradas não mudam (o planejador usa `duration_s`), então nenhum plano deixa de janelar nem estoura o teto. Vale remedir `scripts/bench_render.py` quando o desempenho importar.
- FYI (registros): os registros de `800118b` e `a193ff3` dizem "RESOLVIDO em 08/10/2026", mas os commits são de 09/10 às 00:08 e 00:37; hashes e provas conferem.
- Escopo: a phase 5 cresceu para 11 tarefas e duas rodadas de correção (R1 a R4 e R5 a R8), com ~50 arquivos; não bloqueia, mas na finalização vale registrar no `review.md` aprovado que T7 a T9 são capacidades novas (poderiam ter sido phases próprias).
- A skill pede migrar `.vibeflow/REGRAS.md` (com `AGENTS.md` e `CLAUDE.md` como ponteiros) via `vibe-init`; não executado, porque o modo subagente proíbe editar `REGRAS.md` e `AGENTS.md`.

## Veredito vigente

- [x] **Approve**: nenhum Critical/Required em `[ ]` (proposta do revisor, Etapas 3 e 4; as escolhas de produto já foram decididas pelo humano, e falta só o "aprovado" final; os itens 5 e 8 de "Decisões do humano" não bloqueiam)
- [ ] **Request changes**: há Critical/Required em `[ ]` (foi o veredito da Etapa 2, pelo R5, fechado na Etapa 3)
- [ ] **Approve com defer**: não se aplica

## Decisões para vigência

Proposta, sujeita ao "aprovado" final do humano; nada foi escrito em `.vibeflow/REGRAS.md`. Conferida na Etapa 4: as decisões do humano (itens 1 a 4 e 6 a 7) confirmam as linhas abaixo sem mudança (o nível 5.2 de RENDER-05 foi aceito no item 3). A versão `1.1.0` (decisão 21) não vira linha: um número de versão muda a cada entrega, e a regra semver que o justifica já está em `REGRAS.md`. Também não vira linha o `tmp-*/` do `.gitignore`, que é configuração de repositório.

| ID | Ação | Decisão aprovada | Fonte da decisão | Evidência de implementação |
|---|---|---|---|---|
| PLAN-01 | cria | `profile.aspect_ratio` aceita 16:9, 9:16, 1:1 e 4:5, uma por plano; fora do 16:9 o canvas é fixo (1080x1920, 1080x1080, 1080x1350; preview com lado maior 720) e `max_full_width` é recusado; no 16:9 a full usa a largura da primeira fonte sem upscale e limitada por `max_full_width`, mantém o canvas legado (alinhado a 16) onde a checagem do MP4 o aceita e usa largura múltipla de 32 com altura exata só onde ele seria recusado; fonte única em `plan/schema.py` | `spec.md` decisões 1 a 3, 12 e 16 | `plan.md` T1 e T5; `tests/test_aspect_ratio.py` (tabela de equivalência) |
| RENDER-01 | substitui | Preview e full leem por padrão só as janelas usadas de cada fonte de vídeo, com fallback sem janelas; o `-ss` de cada janela recua 0,25 s antes do primeiro uso (sem `-ss` quando isso chega ao início da fonte) e o grafo desconta o mesmo seek, então quadros e áudio saem iguais, amostra a amostra, aos do render sem janelas; tempos do plano e hash canônico nunca mudam | `spec.md` decisão 17; review da phase 4 | `plan.md` T6; `tests/test_decode_windows.py` (`WindowedAudioPcmTests`) |
| RENDER-03 | cria | `segments[].video.reframe` recorta a fonte original na proporção do canvas: `zoom` 1.0 é o maior recorte, `(x, y)` normalizado fica no centro com clamp na borda (nunca preto), `t_s` relativo ao segmento, `ease` `3p²-2p³` ou `linear` por trecho e estado mantido fora dos keyframes; só com `crop`, nunca com `motion`, até 32 keyframes e `zoom` 1 a 4, valores no grafo só por `_number`; `upscale` avisa sem bloquear e inclui `contain`, `preserve` e o `zoom` do `motion` | `spec.md` decisões 4 a 10 e 15 | `plan.md` T2 e rodada R1 a R4; `tests/test_reframe.py` |
| RENDER-04 | cria | `size_px` e os pixels de `shadow` e `outline` do texto são definidos na largura da full: a full renderiza o valor declarado e a preview o escala pela razão das larguras (meio para cima, magnitude mínima 1, sinal preservado), com a mesma quebra de linha; `shadow`/`outline` só em camada `text`, cor só `#RRGGBB`, recortados pela caixa, e sem eles o grafo é o anterior | `spec.md` decisões 11 e 18 | `plan.md` T3 e T7; `tests/test_preview_render.py` (`TextScaleTests`), `tests/test_text_style.py` |
| MEDIA-01 | cria | Fontes audiovisuais: `.mp4` e `.mov`, vídeo H.264 (qualquer perfil) ou HEVC Main/Main 10 em `yuv420p`/`yuv420p10le`, áudio AAC; trilhas de dados e miniatura anexada são ignoradas, há no máximo um vídeo real e um áudio e a miniatura nunca é o primeiro vídeo, então todo consumidor seleciona `v:0`/`a:0`; toda saída (preview, full, proxy, PNG) é de 8 bits; fonte única em `media.py` | `spec.md` decisão 19 | `plan.md` T8; `tests/test_hevc_sources.py` |
| RENDER-05 | cria | `render full --quality` com `standard`, `high` ou `max` (padrão `standard`, o encoder anterior) escolhe só o preset do encoder (`QUALITY_PRESETS`: crf 23/18/14 na CPU, cq 31/22/18 na GPU, nível 5.2 na GPU em `high` e `max`); fica fora do plano, do hash e da aprovação, a preview usa sempre `standard` e o fallback GPU para CPU mantém o preset | `spec.md` decisão 20 (com o desvio do nível 5.2) | `plan.md` T9; `tests/test_render_quality.py` |
| RENDER-06 | cria | Todo ramo de segmento entrega pixel quadrado ao `concat`: `crop`, `contain` e `preserve` terminam em `setsar=1`, e `reframe` e `motion` saem do `overlay` sobre `color`. Preview e full só são publicadas com vídeo `h264` e, havendo áudio, `aac`, conferidos à parte da allowlist das fontes. | `review.md` R5 e R7 (spec F2 passo 3) | `plan.md` rodada R5 a R8; `tests/test_aspect_ratio.py` (`MixedFitSampleAspectRatioTests`, `RenderedArtifactCheckTests`) |

## Handoff

Finalização Git da phase, depois do "aprovado" final do humano (os itens 5 e 8 de "Decisões do humano" seguem fora da phase e não bloqueiam). Se o humano pedir ajustes, o handoff volta a ser `vibe-implement`.

- [x] Aprovação humana (leu o arquivo e confirmou): "Pode aprovar.", em 09/10/2026. As decisões para vigência foram sincronizadas em `.vibeflow/REGRAS.md`. Registro de escopo: T7 (sombra e contorno), T8 (HEVC e MOV) e T9 (qualidade da full) são capacidades novas que poderiam ter sido phases próprias.

- Chat: review executada em subagente de contexto limpo (Modo A do `vibe-implement`). Após Approve e confirmação humana, o coordenador executa a finalização Git da phase (o revisor delegado não a executa). Para eventuais ajustes, recomendo um subagente corretor (Modo A) ou outro chat; se o humano preferir continuar no chat atual, siga sem bloquear. O `review.md` vivo e o diff são a ponte.

## Finalização Git da phase

- Pré-condições: Approve final confirmado, todas as `T*` concluídas, Critical/Required fechados, prova necessária da integração verde, `git diff --check` e gitleaks quando previsto.
- Commit final: `chore(phase-5): finalize review`, somente com paths residuais autorizados (`interview.md`, `review.md` e, com a sincronização aprovada, `.vibeflow/REGRAS.md`) e sem `Co-Authored-By`; sem mudanças residuais, manter o último commit da task.
- Push: `git push` para o upstream atual, sem `--force`. Registrar hash/HEAD, paths e resultado; falha mantém o handoff bloqueado.

## Etapas

### Etapa 1 - first-pass - T1 a T4, diff `eaa8473..21aeea2` (sem `d89573d`), provas e regressão independente

- Tipo: final.
- Marco e justificativa: integração final com a fila concluída.
- T* abertas fora do marco: nenhuma.
- Provas reaproveitadas: suíte completa da T4 (236 OK) e `pip_audit` da T4, com inputs inalterados desde `21aeea2`.
- Provas executadas:
  - `git diff --check eaa8473..HEAD`: sem saída (limpo).
  - `gitleaks detect --source . --no-banner`: 62 commits, no leaks found.
  - `.venv\Scripts\python.exe -B -m unittest tests.test_reframe tests.test_aspect_ratio tests.e2e.test_phase_5_flow tests.test_preview_render.TextScaleTests tests.test_capabilities tests.test_vibe_editor_skill`: 76 testes OK em 57,6 s (amostra pedida para ceticismo do `reframe`, do golden 16:9, do E2E e do contrato).
  - Regressão 16:9 independente (scripts descartáveis no scratchpad, código de `eaa8473` extraído por `git archive`): hash do `filter_complex` de full e preview, com e sem janelas, canvas e `plan_sha256` idênticos entre o código pré-phase e o HEAD em 3 planos (golden de `tests/test_aspect_ratio.py`, fixture `workflow` com texto e `motion`, fixture `e2e` com camadas e cortes). O código pré-phase reproduz exatamente as constantes `GOLDEN_PREVIEW_GRAPH_SHA256`/`GOLDEN_FULL_GRAPH_SHA256` e os dois goldens de `TextScaleTests`, então os goldens não são falso positivo.
  - Fallback sem janelas com `reframe`: plano 9:16 com 2 trechos (keyframes `ease`/`linear` até zoom 4 e foco estático zoom 3 no canto), `framemd5` dos 150 quadros de vídeo idêntico com e sem janelas em 404x720 e 1080x1920.
  - Proxy real com janela: `proxy build` 1280x720 e preview `--proxy auto` janelada de um plano 9:16 com keyframes; posições medidas pelo oráculo de `tests/test_reframe.py` em 15 instantes dentro de 2 px e sem bordas pretas.
  - `upscale`: 16:9 `crop` + `motion` zoom 2 = `[]`; 16:9 `reframe` zoom 2 = 2.0; 9:16 `contain` 640x360 = 1.69; 16:9 `contain` vertical = `[]` (base de R1 e da decisão para o humano).
  - Full 9:16 com zoom 4.0 estático e animado: 1080x1920 em 1,9 s, `upscale` 7.11.
  - Áudio janelado (fora do escopo, ver Notas): comparação de PCM com e sem janelas no código pré-phase, fontes GOP 25 e GOP 12.
- Leu: plan.md sim; spec.md, interview.md, `REGRAS.md`, diffs de código, testes, skill e `.erros-encontrados/`.
- Rastreabilidade: todos os itens aplicáveis da interview têm destino na spec; cada T* tem código e prova correspondentes; asserções existentes alteradas (T1 em `test_plan_validation.py`, T2 em `test_aspect_ratio.py`, T4 em `test_capabilities.py` e `test_vibe_editor_skill.py`) são aditivas ou substituem placeholders e frases que o contrato tornou falsas.
- Qualidade: toda função criada ou alterada tem comentário semântico; `_scaled_overlay_graph` é compartilhado por `motion` e `reframe` sem mudar o texto do `motion` (golden idêntico); `_layer_span` substitui a conta duplicada da caixa de texto; limitações registradas como `ponytail` no código e em `REGRAS.md`; o teto de 32.767 está correto em `dependencies.py:25`, spec, plan, `REGRAS.md` e `capabilities` (`render.max_command_characters`).
- Abriu: R1, R2, R3 (Nit) e R4 (Optional).
- Fechou: nenhum.
- Veredito desta etapa: Approve final (proposta, sujeita à confirmação humana e à decisão sobre `upscale`).

### Etapa 2 - re-review final - adendo (decisões 15 a 20), rodada R1 a R4 e T5 a T10, diff `21aeea2..HEAD` (`61b42cd`), regressão, ataques e provas independentes

- Tipo: final (re-review no mesmo arquivo, fila T1 a T10 concluída).
- Marco e justificativa: integração final do escopo ampliado; o adendo da spec e as tarefas T5 a T10 foram julgados com rigor por não terem sido lidos pelo humano.
- T* abertas fora do marco: nenhuma.
- Motor: `review.ps1` lido (só cria `review.md` ausente, não escreve `AGENTS.md`); o alvo automático apontava a phase 3 (plan sem review), então rodou `-Apply -Dir phase-5-proporcoes-e-reenquadramento` (`modo: atualizar`, sem ações).
- Provas reaproveitadas: suíte completa de `394820f` (330 OK) e `pip_audit` da T4, com inputs de código, teste e lockfile inalterados depois de `394820f` (só `61b42cd`, registros e spec) e `git diff --stat eaa8473..HEAD -- requirements.lock pyproject.toml` vazio; `pip_audit` não repetido (sem mudança de dependência).
- Provas executadas (scripts descartáveis só no scratchpad, `r2/`, fontes só por `ffmpeg -f lavfi`):
  - `git diff --check 21aeea2..HEAD`: sem saída. `gitleaks detect --source . --no-banner`: 71 commits, no leaks found. `quick_validate.py skills\vibe-editor`: Skill is valid.
  - Amostra: `.venv\Scripts\python.exe -B -m unittest tests.test_hevc_sources tests.test_text_style tests.test_render_quality tests.test_decode_windows tests.test_aspect_ratio tests.test_support tests.test_reframe`: 138 testes OK em 141,5 s, sem `AVISO`/`ResourceWarning` e sem `tmp-*` na raiz depois.
  - `python -m editor capabilities --json`: `formats` (`mp4`/`mov`, `h264`/`hevc`, perfis, `pix_fmt`, `output_pixel_format`, `ignored_streams`), `plan.profile.canvas.16:9.full` (`width_alignment` 16 e `width_alignment_fallback` 32), `plan.layers.text_style` e `render.quality` batem com as constantes, `REGRAS.md` e a skill.
  - Regressão independente (ponto 2, `regress.py`, `eaa8473` por `git archive` contra o HEAD, render real de preview, approve e full): golden 16:9, fixture `workflow` e fixture `e2e` idênticos em `plan_sha256`, canvas, hash do `filter_complex` (full e preview, com e sem janelas), entradas do comando janelado e `framemd5` de vídeo e áudio da full sem janelas e padrão; só `data.profile` ganhou `full_width`, `full_height` e `upscale` (golden 2.0 e 2.25, `e2e` 1.5 pelo `motion`). Plano sintético com duas fontes (GOP 25 sem B a 48 kHz e GOP 50 com B a 44,1 kHz), `crop`+`motion`, `contain`, `preserve`, `mix`, `replace`, J/L, dois textos, camada de vídeo e loudnorm: full sem janelas idêntica (grafo, vídeo e áudio); com janelas, o grafo e o `-ss` mudam pelo pre-roll (esperado), o vídeo é idêntico e o áudio passa de diferente do sem janelas (`eaa8473`) para idêntico a ele (HEAD); a preview muda pelo texto escalado da T3 (esperado). Fonte 1200x674: `RENDER_FAILED` antes, 1184x666 agora. Nenhuma outra diferença.
  - Canvas (ponto 3, `canvas_table.py`, oráculo reimplementado de `eaa8473` com a tolerância literal 0.001): 479.880 pares (fonte 2 a 4000 x tetos 32 a 3840): 365.736 renderáveis antes e idênticos agora, 0 diferentes; 110.544 recusados antes viram largura múltipla de 32, altura par, razão exata e aceitos; 3.600 degenerados (fonte menor que 32) recusados antes e agora; preview idêntica; nenhum upscale. A tabela de `tests/test_aspect_ratio.py` reimplementa o legado sem importar `schema.py` e julga pelas checagens reais com a tolerância fixada em teste: oráculo válido.
  - Áudio das janelas (ponto 4, `pcm_attack.py`, render real `render_preview` com e sem janelas, PCM `s16le` e `framemd5` de vídeo): 10 cenários fora da suíte (seek de 0,01 s, keyframe em 0,3 s, corte até o fim e no último quadro, janelas separadas por 2,01 s, `mix` de duas trilhas, `loop` curto, J/L em keyframe, keyframe menos 1 ms, oito janelas) em 3 fontes (GOP 25 sem B a 48 kHz e 32 kHz, GOP 50 com B a 44,1 kHz), mais `--accel gpu` e preview com proxy real de fonte 1080p: 50 de 50 idênticos. Sensibilidade: o código `21aeea2` falha os 10 cenários da fonte sem B-frames (ex.: 107.520 contra 107.008 amostras), então `WindowedAudioPcmTests` e esta prova não são falso positivo. O planejador usa `duration_s` (inalterado): mesma decisão de janelar e mesmo número de entradas (5 no sintético antes e depois).
  - Estilo de texto (ponto 5, `style_attack.py`): 23 casos (opacidade inteira gigante, texto, `null`, booleano, negativa; deslocamento decimal ou gigante; blur booleano ou 51; cor com `\n`, `@`, `:s=`, nome, dígito Unicode; `outline` vazio, `null`, lista, 0, 31, campo extra; `shadow` em `image`) recusados com `PLAN_INVALID` sem eco e sem exceção não tratada; `#ff00aa` e opacidade 1e-300 aceitas como valores válidos. Filtro gerado conferido: só cores validadas, inteiros escalados (offset 4 na full 1280 vira 2 na preview 704), `gblur=sigma=1.5`, `colorchannelmixer=aa=0.333333`.
  - HEVC e MOV (ponto 6, `payload_attack.py`): 42 payloads FFprobe forjados, nenhum furo da invariante `v:0`/`a:0` (ver Segurança); o artefato renderizado HEVC é aceito pela checagem pós-render (R7); payload 16384x16384 aceito (R6).
  - Jornada integrada (`integration.py`, CLI real): fonte HEVC Main 10 `.mov` 2688x1512 com `tmcd` e fonte H.264 1080p, plano 9:16 com `reframe`, `motion`, texto com `shadow` e `outline` e camada de vídeo HEVC: `plan validate` (upscale 2.54, 2.31, 1.52), `proxy build` (H.264 `yuv420p` 1280x720, com uma trilha `tmcd` recriada: R8), preview janelada com proxy 404x720, `approve`, full CPU `standard` (nível 40, 3,1 Mb/s), GPU `high` (nível 52, 8,7 Mb/s), GPU `max` (nível 52, 12,4 Mb/s) e CPU `max` (9,7 Mb/s), todas H.264 `yuv420p` 1080x1920 com 2 streams. A primeira versão do plano, com um trecho `contain`, falhou com `RENDER_FAILED` e levou ao R5.
  - SAR (R5, `sar_check.py`, `sar_check.py --extra`, `odd_check.py`): matriz de proporções e combinações de `fit` só com H.264 no HEAD e em `21aeea2` (mesmo resultado) e em `eaa8473` para o 16:9 com fonte 1000x562; stderr do `concat` capturado pelo comando de `_build_ffmpeg_arguments`.
  - `--quality` (ponto 7): vetores do encoder de `21aeea2` e do HEAD idênticos em `standard` (CPU `-crf 23`, GPU `-cq 31` sem `-level`); `high`/`max` só trocam `crf`/`cq` e, na GPU, acrescentam `-level 5.2`; `_video_encoder_arguments('cpu', 'ultra')` e a CLI recusam com `ARGUMENT_INVALID` sem eco; o fallback recebe o mesmo `quality`.
  - Testes (ponto 8): `git diff 21aeea2..HEAD -- tests/` não remove nem altera nenhuma linha de asserção (0 linhas removidas com `assert`; só `rmtree` trocado por `remove_tree`, imports, docstrings e o `Remove-Item` de `test_installation.py`); `tests/test_support.py` prova `remove_tree` com handle real de diretório do Windows e com a pasta que nunca sai.
  - Registros (ponto 9): os 8 registros do adendo estão marcados como resolvidos com o hash certo (`1b6e25f`, `ae32ffd`, `23e71b2`, `800118b`, `21cfa3b`, `a193ff3`, `05dd8eb`, `21aeea2`, `394820f`) e as afirmações batem com o código; datas de dois registros em Notas.
- Leu: plan.md sim; spec.md (adendo), `review.md` da etapa 1, `REGRAS.md` (diff), diffs de código, testes, skill e `.erros-encontrados/`.
- Conferência de R1 a R4: os quatro procedem no diff e na prova; mantidos em `[x]`.
- Rastreabilidade: decisões 15 a 20 e aceites A9 a A15 têm código e prova; arquivos fora das listas planejadas estão justificados no plan (exceção menor: `render/preview.py` na T6, onde nasce o `-ss`, listado sem a nota "fora da lista"). A4 fica `partial` pelo R5, que vem da T1 e escapou à etapa 1 e às provas da phase por só usarem planos de um `fit`.
- Qualidade: funções novas e alteradas comentadas (`_drawtext_filter`, `_scaled_style_pixels`, `_signed_offset`, `_validate_text_style`, `_quality_preset`, `DecodeWindow.seek_s`, `_is_attached_picture`, `_hevc_is_supported`, `remove_tree`); `_drawtext_filter` evita duplicar o `drawtext` entre texto e sombra; constantes únicas em `media.py`, `plan/schema.py` e `QUALITY_PRESETS`; limitações como `ponytail`; tabela "Decisões vigentes" de `REGRAS.md` intocada.
- Abriu: R5 (Required), R6, R7 e R8 (Nit).
- Fechou: R1, R2, R3 e R4 (confirmados contra o diff e a prova).
- Veredito desta etapa: Request changes (R5).

### Etapa 3 - re-review final - rodada de correção R5 a R8, diff `61b42cd..acf9fc7`, regressão por pixels e caça a vazamentos de SAR

- Tipo: final (re-review no mesmo arquivo, fila concluída).
- Marco e justificativa: conferir a rodada `acf9fc7`. O coordenador marcou R5 a R8 como `[x]` antes desta etapa, e cada item foi refeito contra o diff e uma prova independente antes de ser aceito.
- T* abertas fora do marco: nenhuma.
- Diff lido: `_video_segment_filter` com `filters.append("setsar=1")` depois do `crop`/`pad` (antes do `motion` e do `format=yuv420p`); `RENDERED_VIDEO_CODEC`/`RENDERED_AUDIO_CODEC` e a recusa em `_validate_rendered_preview`; `-write_tmcd 0` no `proxy build`; comentário `ponytail` em `media.py`; `REGRAS.md`, a entrada no `plan.md` e os testes novos `MixedFitSampleAspectRatioTests` e `test_rendered_artifact_must_be_h264_and_aac`. A asserção de `tests/test_hevc_sources.py` passou a exigir 2 streams no proxy. Os quatro goldens regravados têm a justificativa ao lado. Nenhuma asserção foi removida: as linhas removidas são só os quatro hashes antigos e duas linhas de docstring.
- Provas reaproveitadas: suíte completa de `acf9fc7` pelo corretor (335 OK, `gitleaks --no-git` limpo, `quick_validate` válido). Nada de código ou teste mudou depois de `acf9fc7` (HEAD; `git status` só mostra `interview.md` e este arquivo).
- Provas executadas (scripts descartáveis em `scratchpad\r2`, fontes só por `ffmpeg -f lavfi`):
  - `git diff --check 21aeea2..HEAD`: sem saída. `gitleaks detect --source . --no-banner`: no leaks found.
  - Amostra `.venv\Scripts\python.exe -B -m unittest tests.test_aspect_ratio tests.test_preview_render tests.test_full_render tests.test_text_style tests.test_reframe tests.test_decode_windows tests.test_proxy_build tests.test_hevc_sources`: 159 testes OK em 205,4 s. Sem `AVISO`/`ResourceWarning` e sem `tmp-*` depois.
  - R5, pixels (`regress2.py` + `compare3.py`, render real de preview, approve e full, código de `eaa8473` e de `61b42cd` por `git archive` contra o HEAD):
    - golden 16:9, `workflow` e `e2e`: `framemd5` de vídeo e de áudio da full (sem janelas e padrão) e da preview, `plan_sha256`, canvas e entradas do comando são idênticos às duas referências.
    - Sintético com texto, `motion`, `contain`, `preserve`, J/L e loudnorm: idêntico a `61b42cd` em tudo. Contra `eaa8473`, as únicas diferenças são as já conhecidas: o áudio janelado da T6 e a preview do texto da T3.
    - Grafos: removendo o `,setsar=1` inserido depois de `crop=...` ou `pad=...`, todo grafo volta exatamente ao texto anterior (3 ou 4 inserções por plano). O golden normalizado devolve `5f4bff18...` e `2889f5d4...`, e os de `TextScaleTests` devolvem `0d1f6b60...` e `d548e988...`, como os comentários dos testes dizem.
    - Fonte 1200x674: o `framemd5` sem o cabeçalho é idêntico (50 quadros na full e na preview); só a linha `#sar` passa de 199800:199841 e 15840:15839 para 1:1.
  - R5, cobertura:
    - `sar_check.py`, `sar_check.py --extra` e `odd_check.py` no HEAD: os 29 cenários (9:16, 1:1, 4:5 e 16:9; `crop` com `crop`, `contain`, `reframe` e `motion`; `contain` sozinho; `preserve` nativo sozinho e com `crop`) e o 16:9 com fonte 1000x562 (`crop` + `motion`) publicam preview e full com SAR 1:1.
    - `sar_matrix.py`, à caça de outros vazamentos: um plano por proporção com fontes 1920x1080, 1080x1920, 640x480 e 1000x562, `crop`, `contain`, `preserve` (quando cabe), `reframe` animado e estático, `motion` sobre `crop` e sobre `contain`, corte J, camadas de imagem `contain` e `crop`, camadas de vídeo `crop` e `contain` e texto com sombra e contorno.
    - Nesse plano, `render preview --proxy off`, `proxy build` (2 proxies criados), preview `auto` janelada com proxy, approve, full CPU e full GPU `high` publicam H.264 com SAR 1:1 nas quatro proporções: 0 falhas.
    - Leitura do grafo: camadas e texto passam por `overlay`, que herda o SAR da base, e o canvas final é o do `concat`, então não sobra vazamento fora de `_video_segment_filter`.
  - R6: `rg` de "dimens" em `REGRAS.md`, na skill, na spec, no plan, em `capabilities.py` e em `media.py`. Só imagens têm teto (8.192); `REGRAS.md` (linhas 59 e 340) e `media.py` dizem que não há teto de vídeo; nenhum teto foi criado.
  - R7 (`r7_refusal.py`):
    - Com o encoder trocado por `libx265` via patch, a preview e a full são recusadas com `RENDER_FAILED` e mensagem fixa sem o codec.
    - Preview, full e `approval.json` vigentes ficam idênticos por SHA-256, e `.tmp` fica vazio.
    - Áudio `libmp3lame` também é recusado (pela allowlist de `.mp4`).
    - Preview e full por `h264_nvenc` (`max`) publicam `h264`/`aac`.
  - R8 (`r8_proxy.py`):
    - Com fontes HEVC `.mov` e `.mp4` com `tmcd`, o proxy de `61b42cd` tem 3 streams e o do HEAD tem 2.
    - MD5 por pacote idêntico (vídeo 360 e áudio 564 no `.mov`; 150 e 260 no `.mp4`). `PROXY_PARAMETERS_VERSION` segue 1.
    - O proxy publicado pelo código anterior é `reused` pelo HEAD e lido pela preview (`proxy.sources` com a fonte, `stale` vazio).
- Leu: diff `61b42cd..acf9fc7`, a entrada "Rodada de correção R5 a R8" do `plan.md` e as alterações de `REGRAS.md`.
- Conferência dos `[x]` do coordenador: R5, R6, R7 e R8 procedem no diff e na prova; mantidos em `[x]` com a nota "Confirmado na Etapa 3".
- Decisões para vigência: proposta RENDER-06 (pixel quadrado em todo ramo e MP4 publicado só como H.264/AAC), por ser duradoura e não óbvia.
- Abriu: nenhum.
- Fechou: R5 (Required), R6, R7 e R8 (Nit).
- Veredito desta etapa: Approve final (proposta, sujeita à confirmação humana do adendo e das decisões listadas em "Decisão para o humano").

### Etapa 4 - re-review final curta - T11 (versão 1.1.0 e `tmp-*/` no `.gitignore`), diff `acf9fc7..f27991c`

- Tipo: final (re-review no mesmo arquivo, fila T1 a T11 concluída).
- Marco e justificativa: conferir a T11, que aplica as escolhas 6 e 7 decididas pelo humano ("siga suas recomendações"): decisões 21 e 22 e aceite A16 no adendo da spec.
- T* abertas fora do marco: nenhuma.
- Diff lido: só as 4 menções à versão do pacote mudam de `1.0.0` para `1.1.0`: `pyproject.toml:7`, `contracts.CLI_VERSION`, e as asserções de `tests/test_cli_contract.py` e de `tests/test_capabilities.py`. O `.gitignore` ganha `tmp-*/` com comentário. A spec ganha as decisões 21 e 22 e o A16, e o plan ganha a T11. Nenhuma asserção foi enfraquecida: as duas trocam só o valor esperado. `requirements.lock`, `SCHEMA_VERSION` (1), `PLAN_SCHEMA_VERSION` (2) e `PROXY_PARAMETERS_VERSION` não mudaram.
- Provas reaproveitadas: suíte completa de `f27991c` rodada pelo corretor (335 OK, sem `tmp-*` na raiz), porque depois de `acf9fc7` só mudaram 4 constantes de versão e uma regra de `.gitignore`.
- Provas executadas:
  - `.venv\Scripts\python.exe -B -m unittest tests.test_cli_contract tests.test_capabilities tests.test_installation`: 57 testes OK em 40,3 s, sem `tmp-*` depois.
  - `python -m editor --version`: `editor 1.1.0`. `--version --json`: `{"command":"version","data":{"version":"1.1.0"},"ok":true,"schema_version":1}`. `capabilities --json`: `cli_version` `1.1.0`. `tomllib` sobre `pyproject.toml` dá `1.1.0`, igual a `CLI_VERSION`.
  - `rg -n --hidden "1\.0\.0"` sem `requirements.lock`, `.venv` e `.git`: só aparecem textos históricos dos artefatos de `.vibeflow` (a promoção para `1.0.0` na phase 1, a decisão 21 e a nota de versão da Etapa 2 deste arquivo). Nenhuma menção em código, configuração, skill ou `REGRAS.md`.
  - `git diff --stat eaa8473..HEAD -- requirements.lock`: vazio. Em `pyproject.toml` só a linha `version` mudou desde `eaa8473`.
  - `git check-ignore -v tmp-x/a.mp4`: `.gitignore:21:tmp-*/`. `git ls-files` não tem caminho que case `tmp-*/`.
  - `git diff --check 21aeea2..HEAD`: sem saída. `gitleaks detect --source . --no-banner`: no leaks found.
  - Pacote editável do `.venv` (atualizado pelo coordenador fora do repositório): `pip show editor` dá `Version: 1.1.0` e `Editable project location: A:\Projetos\editor`, e `pip check` dá "No broken requirements found". `git status --short` mostra só `interview.md` e este `review.md` (untracked), então nada do repositório mudou por isso.
- Atualizado neste arquivo: seção "Decisões do humano" com as 8 escolhas (1 a 4, 6 e 7 aplicadas; 5 adiada para tarefa própria; 8 pendente de o humano indicar um arquivo); "Decisão para o humano" reduzida ao "aprovado" final e aos itens 5 e 8; A16 na cobertura; "Decisões para vigência" confirmadas sem linha nova.
- Abriu: nenhum.
- Fechou: nenhum (nenhum R* aberto antes desta etapa).
- Veredito desta etapa: Approve final (proposta, sujeita ao "aprovado" final do humano; status `rascunho`).
