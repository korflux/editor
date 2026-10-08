# Spec: decodificação seletiva e cache de proxy
# Alvo: phase-4-decodificacao-seletiva-e-cache-de-proxy
# Status: aprovado

## Objetivo

O operador (agente ou humano) renderiza preview e full mais rápido com a mesma saída. O render deixa de decodificar os trechos de fonte que a timeline não usa (decodificação seletiva), e a preview deixa de decodificar o original em alta resolução quando existe um proxy leve da fonte, reaproveitado entre as iterações de edição. Sucesso é provar quadros e áudio equivalentes ao caminho atual, com ganho de tempo medido, e manter a full partindo sempre dos originais.

## Cobertura da origem

| Origem | Destino na spec | Motivo se fora/N/A |
|---|---|---|
| Segmentos de vídeo, camadas de vídeo e trilhas de áudio que usam a mesma fonte | F1 (união dos intervalos) | N/A |
| `render preview` e `render full` | F1 (mesmo grafo) e F3 | N/A |
| Decode por GPU (`--accel gpu`) | Decisão 7 | N/A |
| Aprovação por hash | Decisão 10, inalterada | N/A |
| `capabilities`, skill `vibe-editor` e `REGRAS.md` | Contratos e A8 | N/A |
| Fontes HEVC das filmagens DJI | Fora | Registrado em `.erros-encontrados/2026-10-05-fontes-dji-hevc-rejeitadas.md`; é outra entrega |
| `init` | N/A | `proxy build` cria sua própria pasta, `init` não muda |

## Suposições e decisões

1. A decodificação seletiva é o comportamento padrão, sem flag, porque a saída é provada equivalente. Se o render com janelas falhar por motivo diferente de timeout, ele é repetido uma vez sem janelas. (produto; já fechado)
2. Uma janela é um intervalo contínuo de uma fonte, lido por uma entrada própria do FFmpeg com `-ss` e `-t`. Os tempos do grafo (`trim`, `atrim`) são deslocados pelo início da janela; os tempos do plano nunca mudam. (produto; já fechado)
3. Os intervalos de uma fonte são a união dos `in_s`/`out_s` dos segmentos de vídeo, das camadas de vídeo e das trilhas de áudio que a usam. Trilha com `loop` é janelada pelo seu recorte, o loop continua no filtro. Só fontes de vídeo recebem janelas; fonte de áudio e imagem mantêm uma entrada como hoje. (produto; já fechado)
4. Lacuna entre intervalos menor que 2 s é mesclada. O início da janela é exato e o fim ganha folga de 0,5 s. O FFmpeg decodifica a partir do keyframe anterior ao início e descarta o excedente, sem alterar quadros. Pre-roll de áudio não foi necessário (provado na T1). Exceção aceita: áudio AAC codificado com PNS (substituição perceptual de ruído) não é bit a bit idêntico após seek, pois o ruído depende do estado acumulado do decoder; a diferença medida é inaudível (amplitude máxima de cerca de 0,004) e fica registrada como `ponytail` em `windows.py`. (produto; já fechado)
5. Tetos: no máximo 8 janelas por fonte e 24 entradas de vídeo no total, sempre pelo menos uma entrada por fonte. Acima do teto, mescla-se pela menor lacuna. A linha de comando continua dentro do limite informado por `capabilities`. (segurança e operação; já fechado)
6. Uma fonte só é janelada se a leitura estimada cair pelo menos 20% frente à atual (do início da fonte ao último `out_s`). Caso contrário ela mantém uma única entrada, sem `-ss`, idêntica à de hoje. (produto; já fechado)
7. A regra do decode por GPU passa a contar entradas de vídeo, não fontes: com mais de 4 entradas o decode fica em CPU e o encode segue como pedido. (produto; já fechado)
8. O proxy é H.264 `yuv420p` que cabe em 1280x720 sem upscale, com GOP curto (15) e `crf 20`, áudio por cópia e timestamps preservados. Só fontes de vídeo maiores que 1280 de largura ou 720 de altura recebem proxy. Ele fica em `<workdir>/proxy/<source-id>.mp4` com metadados em `<source-id>.json`. (produto; já fechado)
9. O proxy é válido quando os metadados casam com a fonte atual (tamanho e `mtime_ns`, versão dos parâmetros, tamanho do arquivo do proxy). Na criação ele só é publicado após verificar mesma contagem de pacotes de vídeo, diferença de `start_time` de até 1 ms e de duração de até um quadro. (produto; já fechado)
10. O proxy é usado somente na preview, por `--proxy auto|off` (padrão `auto`). A full nunca usa proxy. A aprovação continua por hash do plano, da preview e dos originais. A preview com proxy não é bit a bit idêntica à sem proxy, e isso é aceito: ela é rascunho de aprovação visual. (produto e segurança; já fechado)
11. `proxy build` é sempre explícito e nunca disparado por `render preview`, pois custa um decode completo de cada fonte. É idempotente: proxy válido é reutilizado, inválido ou obsoleto é refeito por substituição atômica. (processo; já fechado)
12. Não há limpeza automática nem comando de limpeza do cache; apagar `<workdir>/proxy/` é sempre seguro. `ponytail: sem limpeza automática, adicionar quando o consumo de disco do cache incomodar`. (escopo; já fechado)
13. A entrega é minor: adiciona o comando `proxy build`, o `--proxy` e campos novos nos envelopes, sem quebrar contratos existentes. (processo; já fechado)

## Escopo e comportamento

### 1. Fluxo F1: render com decodificação seletiva (preview e full)

- Jornada: N/A, CLI sem UI; entra na jornada vigente da skill `vibe-editor` (plano, preview, aprovação, full).
- Rota: `editor render preview` e `editor render full` (e os atalhos `preview` e `full`).
- Gatilho: o operador executa o render.
- Pré-condição: `plan.json` válido; na full, aprovação vigente (inalterado).
- Superfície por passo: N/A, sem UI; a resposta é o envelope JSON em `stdout` e o diagnóstico em `stderr`.
- Passos:
  1. Valida o plano e as fontes -> inalterado.
  2. Calcula as janelas de cada fonte de vídeo pelas decisões 3 a 6 -> o grafo passa a ter uma entrada por janela, com os tempos deslocados.
  3. Renderiza e valida o MP4 -> publicação atômica inalterada.
  4. Responde -> `data.decode` com `windowed` (booleano), `inputs` (número de entradas de vídeo) e `sources` (ids janelados).
- Validações: toda janela fica dentro da duração da fonte já validada; início e duração são números finitos formatados como hoje.
- Erros: `RENDER_FAILED` se o render falhar também sem janelas; timeout não repete e mantém o código atual.
- Estados: sem ganho de 20% ou sem intervalos em comum, `windowed` é `false` e o comando de entrada é o atual.
- Aceite: A1, A2, A3.
- Reutilizar: `build_ffmpeg_graph` e `_video_segment_filter` em `src/editor/render/ffmpeg_graph.py`, `_build_ffmpeg_arguments` e o laço de fallback de `src/editor/render/preview.py`.

### 2. Fluxo F2: construir proxies

- Jornada: N/A, CLI sem UI; etapa opcional entre a escolha das fontes e a primeira preview.
- Rota: `editor proxy build --workdir <caminho-absoluto> [--source-id <id>]... --json`.
- Gatilho: o operador executa o comando.
- Pré-condição: `plan.json` válido com fontes de vídeo declaradas.
- Superfície por passo: N/A, sem UI; envelope JSON em `stdout`.
- Passos:
  1. Valida o plano e escolhe as fontes elegíveis (decisão 8); `--source-id` restringe a escolha -> id desconhecido no plano retorna `ARGUMENT_INVALID`.
  2. Para cada fonte elegível: proxy válido existente -> `reused`; senão decodifica a fonte inteira, gera o proxy em `.tmp`, verifica (decisão 9) e publica por substituição atômica -> `created`.
  3. Fonte não elegível -> `skipped` com motivo; falha de uma fonte -> `failed` com motivo, sem interromper as demais nem remover proxy válido anterior.
  4. Responde -> `data.proxies[]` com `source_id`, `status` (`created`, `reused`, `skipped` ou `failed`), `reason` quando houver, `width`, `height` e `size_bytes`.
- Validações: `--source-id` pela allowlist de ids; caminho do proxy derivado só do id validado, dentro do trabalho; sem links nem reparse points.
- Erros: `WORKDIR_INVALID`, `PLAN_INVALID` e `DEPENDENCY_MISSING` como nos demais comandos; motivos por fonte estáveis em maiúsculas: `STREAM_MISMATCH`, `FFMPEG_FAILED`, `TIMEOUT`, `NOT_ELIGIBLE`.
- Estados: nenhuma fonte elegível -> sucesso com todas `skipped`.
- Aceite: A4, A5.
- Reutilizar: `load_validated_plan`, helpers de layout e publicação atômica de `src/editor/workdir.py`, `process.run_tool`, `media.inspect_source`.

### 3. Fluxo F3: preview com proxy

- Jornada: N/A, CLI sem UI; é a iteração mais frequente da edição.
- Rota: `editor render preview [--proxy auto|off]` (e o atalho `preview`).
- Gatilho: o operador renderiza a preview.
- Pré-condição: nenhuma além da preview atual; proxies são opcionais.
- Superfície por passo: N/A, sem UI; envelope JSON em `stdout`.
- Passos:
  1. Com `auto`, cada fonte elegível com proxy válido passa a ser lida pelo proxy; as demais, pelo original -> a escolha é por fonte.
  2. Renderiza como em F1; as janelas se aplicam ao proxy.
  3. Responde -> `data.proxy` com `mode`, `sources` (ids lidos pelo proxy) e `stale` (ids com proxy presente porém obsoleto ou inválido).
- Validações: `--proxy` aceita só `auto` e `off`; a validade do proxy é checada por metadados, sem decodificar.
- Erros: `ARGUMENT_INVALID` para valor fora da allowlist; metadado ilegível ou fora do formato conta como proxy obsoleto, nunca como erro.
- Estados: sem proxies -> `sources` vazio e render idêntico ao atual; `off` ignora a pasta de proxies.
- Aceite: A6, A7.
- Reutilizar: `render_preview` em `src/editor/render/preview.py` e o envelope existente. A full não recebe `--proxy`.

### Fora

- Aceitar HEVC ou 10 bits na allowlist de mídia — é outra entrega, registrada em `.erros-encontrados/2026-10-05-fontes-dji-hevc-rejeitadas.md`.
- Condensar fontes em arquivos intermediários por remux — complexidade e risco de sincronismo sem medição que a justifique.
- Decode paralelo CPU e GPU — ganho incerto e exige particionar o render.
- Geração automática ou em segundo plano de proxies — custo escondido no primeiro `render preview`.
- Comando ou rotina de limpeza do cache — decisão 12.
- Proxy para áudio ou imagem, `--proxy` na full e mudança no schema do plano — sem ganho ou quebram contrato.

## Checklist de entrega

### Aceite

- [ ] A1: Preview e full de um plano que usa menos da metade da fonte produzem os mesmos quadros e o mesmo áudio com e sem janelas.
- [ ] A2: Casos de borda produzem o mesmo resultado: trecho no início e no fim da fonte, trechos adjacentes, camada de vídeo e trilha de áudio da mesma fonte, trilha com `loop` e cortes J e L.
- [ ] A3: Quando o ganho é menor que 20% ou um teto estoura, o comando de entrada é o atual e `windowed` é `false`; falha com janelas repete sem janelas e informa `windowed: false`.
- [ ] A4: `proxy build` cria o proxy das fontes elegíveis e uma nova execução o reutiliza sem recodificar.
- [ ] A5: Proxy reprovado na verificação não é publicado; fonte alterada após o build torna o proxy obsoleto e ele deixa de ser usado.
- [ ] A6: A preview usa o proxy nas fontes que o têm válido e o original nas demais, e o envelope informa `mode`, `sources` e `stale`.
- [ ] A7: A full e a aprovação independem do proxy: a full parte dos originais, `approve` registra o hash da preview vigente (com ou sem proxy) e os hashes do plano e dos originais, que são idênticos nos dois casos, e `--proxy off` reproduz a preview sem proxy.
- [ ] A8: `capabilities` informa o comando `proxy build`, as janelas de decode e o `--proxy`, e a skill `vibe-editor` e o `REGRAS.md` os documentam.

### Critérios de sucesso

- [ ] C1: Em fonte 4K H.264 de GOP 1 s com 6 trechos de 5 s de 60 s, o render com janelas é pelo menos 20% mais rápido que sem janelas e a preview com proxy leva no máximo 50% do tempo da sem proxy (mediana de 3 execuções intercaladas, medindo também CPU consumida).
- [ ] C2: Suíte completa verde, sem alterar testes de contrato além dos campos novos nos envelopes.
- [ ] C3: Sem regressão quando não há ganho: o comando de entrada é o mesmo de hoje.
- [ ] C4: Segurança preservada: ids e caminhos validados, publicação atômica, sem shell, erros públicos sem stderr do FFmpeg, gitleaks e pip-audit sem achados.

## Contratos e restrições necessárias

- Dados e invariantes: os tempos de `plan.json` e o hash canônico do plano nunca mudam; o deslocamento existe só no grafo. O proxy não entra em nenhum hash de aprovação. O metadado do proxy é JSON de até 4 KiB com os campos `version`, `source_id`, `source_size_bytes`, `source_mtime_ns`, `proxy_size_bytes`, `width` e `height`; qualquer outro campo ou tipo faz o proxy contar como obsoleto.
- Integrações e interfaces: o FFmpeg local recebe `-ss` e `-t` por entrada; novos campos nos envelopes (`data.decode`, `data.proxy`, `data.proxies`); o comando novo `proxy build` e o `--proxy` entram em `capabilities` e na ajuda.
- Segurança e operação: `--source-id` repetível, cada valor por `^[a-z0-9][a-z0-9_-]{0,63}$` e existente no plano; `--proxy` só `auto` ou `off`; caminhos sempre dentro do trabalho e sem links; processos com argumentos estruturados, sem shell e com timeout proporcional à duração da fonte; arquivos temporários em `.tmp` com limpeza em sucesso, erro e timeout; CORS, autenticação, segredos e dados pessoais N/A, pois a CLI é local e não tem rede.
- Tecnologia imposta: biblioteca padrão do Python e FFmpeg/FFprobe locais, sem nova dependência.

## Como provar

| Aceite | Evidência esperada |
|---|---|
| A1 | Teste com fonte de GOP curto comparando os quadros (`framemd5`) e o áudio do grafo com e sem janelas, iguais |
| A2 | Casos de borda no mesmo teste de equivalência, cada um com resultado igual |
| A3 | Teste do comando FFmpeg sem `-ss` no caso sem ganho e no estouro de teto; teste de falha simulada com janelas repetida sem janelas, com `windowed: false` |
| A4 | Execução real do `proxy build` publicando proxy e metadado; segunda execução devolve `reused` sem alterar os arquivos |
| A5 | Teste com proxy de contagem de pacotes divergente não publicado; teste alterando a fonte e vendo o proxy em `stale` |
| A6 | Preview real com proxy de uma fonte e original de outra; envelope com `mode`, `sources` e `stale` corretos |
| A7 | `plan_sha256` e hashes dos originais iguais em `approval.json` com e sem proxy, com `preview_sha256` igual ao hash real da preview vigente; `full.mp4` com os mesmos quadros e áudio nos dois casos; preview com `--proxy off` igual à de antes da entrega |
| A8 | `capabilities --json` com os campos novos e o `quick_validate.py` da skill aprovado |
| C1 | Tabela de medições (relógio e CPU consumida) no `REGRAS.md` |

## Boundaries

### Always

- Provar a equivalência de quadros e áudio antes de tornar as janelas o padrão.
- Manter o comando de entrada atual quando não houver ganho.
- Publicar proxies por substituição atômica e preservar o proxy válido anterior em falha.

### Ask first

- Mudar os parâmetros do proxy (resolução, GOP, `crf`) ou os tetos de janelas.
- Gerar proxies automaticamente em qualquer comando.

### Never

- Usar proxy na full ou incluí-lo em hash de aprovação.
- Alterar o schema do plano, os tempos do plano ou o hash canônico.
- Ler ou gravar fora do trabalho, ou montar comando por concatenação de entrada externa.

## Handoff

vibe-plan

- Chat: sem UI visível; recomende novo chat para `vibe-plan`; se o humano preferir, continuar no mesmo chat não bloqueia. O `spec.md` vivo é a ponte.

- [x] Aprovação humana (leu o arquivo e confirmou)
