# Spec: editor MVP, análise e edição linear local
# Alvo: mvp
# Status: aprovado

## Objetivo

O MVP entrega uma CLI local, provisoriamente chamada `editor`, para que uma conversa de IA analise um vídeo de vlog de viagens, monte um plano JSON e execute uma edição linear reproduzível. A CLI fornece evidências de transcrição, áudio e frames, mas não interpreta intenção editorial nem decide cortes sozinha. O sucesso é completar o fluxo `capabilities` → análise → plano validado → uma única preview → aprovação explícita por hash → `full.mp4`, sem upload, banco ou painel.

## Inventário

1. Descoberta local de capacidades, dependências, formatos, limites e recomendação de dispositivo/modelo.
2. Análise local de transcrição com timestamps por palavra, loudness e silêncio, e extração de frames solicitados.
3. Plano JSON versionado com timeline linear, cortes, junções, áudio independente, b-roll em tela cheia e letterings.
4. Validação, renderização de uma única preview, aprovação por hash e renderização da full baseada no mesmo plano.
5. Distribuição inicial como pacote Python em ambiente virtual, com dependências pinadas e instalação explícita no Windows.

## Suposições e decisões

1. `editor` é apenas o nome técnico provisório do executável; o nome do produto continua em aberto.
2. A CLI é local, não interativa e sem modelo conversacional próprio. A conversa de IA conduz a entrevista adaptativa, interpreta as análises e escreve `plan.json`.
3. Cada trabalho possui um diretório local próprio. O plano, as análises, a aprovação e as saídas são inspecionáveis nesse diretório; não existe banco nem estado remoto.
4. Todos os tempos de mídia são números em segundos, com ponto decimal, e intervalos usam a convenção `[início, fim)`. Datas de artefatos são ISO 8601 em UTC.
5. O JSON canônico é UTF-8, com chaves ordenadas e sem espaços para cálculo de hash. Alterações apenas de formatação não alteram o hash semântico do plano.
6. `--json` está disponível em todos os subcomandos. Em modo JSON, `stdout` contém somente um envelope estruturado e diagnósticos técnicos ficam em `stderr`.
7. O diretório de trabalho é um caminho absoluto, existente, gravável e sem symlink, junction ou outro reparse point. A CLI só cria ou substitui artefatos dentro dele e rejeita caminhos de saída fora desse diretório ou em componentes de destino linkados.
8. Fontes são caminhos locais para arquivos regulares. URLs, `stdin`, diretórios, dispositivos, caminhos com identificador de fonte inválido e reparse points não são entradas válidas.
9. O perfil final obrigatório do MVP é horizontal 16:9. A primeira fonte visual da timeline define dimensões e taxa de quadros da full; qualquer fonte com proporção diferente exige `fit` explícito como `contain` ou `crop` no segmento.
10. “Equalizar” significa normalização simples de loudness e limiter. Equalização paramétrica, restauração de ruído e ducking automático não fazem parte do contrato.
11. Um trabalho é processado por vez. A limpeza de trabalhos antigos é responsabilidade do operador e não é automatizada pela CLI.

## Decisões críticas

| ID | Ação | Decisão | Motivo e impacto | Fonte anterior |
|---|---|---|---|---|
| PROD-01 | mantém | O primeiro job de aceitação é a edição de um vlog de viagens em 16:9. | Oferece um cenário concreto para validar análise, cortes, b-roll, áudio, lettering, preview e full. | interview MVP |
| SCOPE-01 | mantém | O MVP cobre análise e edição linear básica ponta a ponta. | O valor só é comprovado quando a conversa chega da compreensão à full aprovada. | interview MVP |
| CLI-01 | mantém | Uma CLI local, não interativa, com subcomandos estáveis e `--json`. | Mantém o contrato utilizável por Codex, Grok Build e outros hosts com acesso ao terminal. | interview MVP |
| DATA-01 | mantém | Um diretório local por trabalho, plano JSON e nenhum banco. | Torna o estado reproduzível e inspecionável, sem infraestrutura prematura. | interview MVP |
| MEDIA-01 | mantém | MP4 com vídeo H.264 e áudio AAC quando houver áudio, MP3 e WAV, até 60 minutos e 4 GB por fonte. | Restringe a compatibilidade inicial e protege tempo, memória e espaço temporário. | interview MVP |
| AI-01 | mantém | Whisper local, CPU como baseline, GPU opcional e recomendação por hardware. | Evita custo de tokens e torna explícito o impacto de desempenho e dependências. | interview MVP |
| RENDER-01 | mantém | Uma preview vigente, aprovação por hash e full baseada no mesmo plano. | Impede exportar uma edição diferente da que foi aprovada. | interview MVP |
| AUDIO-01 | mantém | Trilhas independentes, volume explícito, normalização de loudness e limiter. | Cobre voz, música e b-roll sem ducking ou processamento de frequência. | interview MVP |
| TEXT-01 | mantém | Lettering estático por intervalo e posição nomeada. | Cobre destaques orientados pela transcrição sem criar um editor visual. | interview MVP |
| PRIV-01 | mantém | Processamento e artefatos 100% locais, sem telemetria. | Preserva os vídeos e mantém o fluxo previsível. | interview MVP |
| DEP-01 | mantém | Pacote Python em ambiente virtual, com instalador PowerShell e componentes instalados explicitamente. | Reduz a complexidade de empacotar Whisper e FFmpeg em um executável único. | interview MVP |
| OPS-01 | mantém | Um trabalho por vez e limpeza manual de diretórios antigos. | É suficiente para validar o MVP sem fila, servidor ou armazenamento compartilhado. | interview MVP |
| TECH-01 | cria | O backend de transcrição é `faster-whisper` sobre CTranslate2. CPU usa a configuração `int8` como baseline; CUDA é opcional e só é usada quando o runtime local for detectado e compatível. | O backend atende ao requisito de processamento local e oferece caminhos explícitos para CPU e GPU. A versão exata fica pinada no lockfile, nunca flutuante. | N/A |
| AI-02 | cria | A recomendação automática usa `base` no CPU quando instalado, `small` no CUDA quando instalado, e faz fallback determinístico para `tiny`, sempre entre modelos locais disponíveis. | Mantém uma configuração leve para o baseline e permite priorizar precisão quando a máquina suportar, sem download silencioso. | N/A |
| CONTRACT-01 | cria | Sucessos e erros usam envelope JSON estável, códigos de erro em maiúsculas e exit codes por classe. | Separa dados de máquina de diagnósticos humanos e permite que agentes tratem falhas sem interpretar texto livre. | N/A |
| STATE-01 | cria | `approve` registra SHA-256 do plano canônico, da preview e de todas as fontes; `full` recalcula e bloqueia qualquer divergência. | Torna a aprovação verificável e invalida a autorização quando o plano, a preview ou uma fonte muda. | N/A |

## Escopo e comportamento

### 1. Descoberta e entrevista na conversa

- `editor capabilities --json` não executa edição nem faz upload. Ele informa versão da CLI, sistema operacional, arquitetura, CPU, memória, GPU e VRAM quando disponíveis, status e versão do FFmpeg, backend Whisper, modelos locais, dispositivos utilizáveis, formatos aceitos, limites, operações e recomendação de dispositivo/modelo.
- A saída distingue `available`, `missing`, `unsupported` e `recommended`. A ausência de FFmpeg ou de um modelo não impede a consulta de capacidades, mas impede somente as operações dependentes dela.
- A recomendação de dispositivo respeita `auto`, `cpu` e `cuda`. Um override explícito não pode ser substituído silenciosamente; se estiver indisponível, a operação falha com `DEVICE_UNAVAILABLE`.
- A conversa consulta as capacidades antes de perguntar. Ela pergunta apenas o que ainda faltar para definir objetivo, fontes e papéis, cortes, uso de b-roll, áudio e música, letterings, formato, restrições e fluxo preview/full. A CLI não faz perguntas no terminal.
- Depois do resumo confirmado pelo operador, a conversa escreve o único `plan.json` do trabalho. A CLI executa somente o que estiver explicitamente descrito nesse JSON.

### 2. Diretório e artefatos do trabalho

Cada trabalho usa esta convenção, sem versões paralelas:

```text
<workdir>\
  plan.json                         # plano atual, escrito pela conversa e validado pela CLI
  approval.json                     # último registro de aprovação e seus hashes
  preview.mp4                       # única preview vigente, publicada somente após sucesso
  full.mp4                          # full publicada somente após aprovação válida
  analysis\
    transcription\<source-id>.json # transcrição canônica da fonte
    audio\<source-id>.json         # análise de loudness, picos e silêncio
  frames\<source-id>\
    manifest.json                   # associação entre timestamps e imagens
    frame-0001.png                  # imagens solicitadas, numeradas deterministicamente
  .tmp\                             # temporários isolados e removidos após cada operação
```

- `source-id` é obrigatório, único no plano e limitado a `[a-z0-9][a-z0-9_-]{0,63}`. Ele nunca é usado como caminho sem essa validação.
- Os originais permanecem em seus caminhos de origem e nunca são removidos, sobrescritos ou usados como destino.
- Toda operação que publique um artefato escreve primeiro em `.tmp`, fecha e valida o resultado, e só então substitui o destino com uma operação atômica. Diretórios e arquivos de destino devem permanecer regulares e sem reparse point durante a publicação. Falha deixa plano e análises preservados e não publica saída parcial.
- A execução aceita apenas arquivos regulares locais, com tamanho máximo de 4 GiB e duração máxima de 3.600 segundos por fonte. O tamanho é verificado antes do processamento e a duração/codec é verificada pelo leitor de mídia local.

### 3. Contrato mínimo da CLI

Os subcomandos abaixo são o contrato primário. Todos aceitam `--json`; comandos que gravam artefatos exigem `--workdir`.

```text
editor capabilities --json
editor analyze transcribe --workdir <dir> --source-id <id> --input <arquivo> [--language auto|<codigo>] [--model <modelo-local>] [--device auto|cpu|cuda] --json
editor analyze audio --workdir <dir> --source-id <id> --input <arquivo> [--window-s <número>] --json
editor extract frames --workdir <dir> --source-id <id> --input <video> (--timestamps <t1,t2,...> | --interval-s <número> [--start-s <número>] [--end-s <número>]) --json
editor plan validate --workdir <dir> --json
editor render preview --workdir <dir> --json
editor approve --workdir <dir> --json
editor render full --workdir <dir> --json
```

- `analyze transcribe` aceita MP4, MP3 ou WAV. Gera JSON com `schema_version`, fonte e SHA-256, idioma detectado, texto, segmentos com início/fim e palavras com início/fim. Timestamps por palavra são obrigatórios no contrato; se o backend não puder fornecê-los, a operação falha.
- `analyze audio` aceita MP4, MP3 ou WAV. Gera loudness integrado, pico verdadeiro, média e pico por janela, além de intervalos de silêncio. O padrão é janela de 1 segundo; `--window-s` deve ser um número finito maior que zero, e uma última janela parcial é preservada. Silêncio é somente uma detecção de amplitude, com limiar de `-45 dBFS` e duração mínima de `0,3 s`; não é classificação de fala, ruído ou conteúdo.
- `extract frames` aceita apenas vídeo. O operador escolhe timestamps explícitos ou intervalo regular, nunca os dois. Timestamps devem estar no intervalo `[0, duração)` da fonte, sem duplicação. No modo regular, `start_s` padrão é `0`, `end_s` padrão é a duração da fonte e são gerados os instantes `start_s + k * interval_s` menores que `end_s`. O manifesto registra timestamp solicitado, timestamp efetivo e caminho da imagem PNG.
- `plan validate` lê exclusivamente `<workdir>\plan.json`, rejeita JSON inválido, campos desconhecidos, IDs duplicados, caminhos inválidos, fontes incompatíveis, intervalos fora da duração, áudio incompatível com a duração do segmento, overlay inválido, perfil de saída diferente de 16:9 ou fonte de proporção diferente sem `fit: contain|crop`. Não renderiza.
- `render preview` valida o plano e gera ou substitui a única `<workdir>\preview.mp4`. A maior dimensão tem no máximo 720 pixels, a proporção é preservada e o áudio é mantido conforme o plano. As saídas publicadas usam contêiner MP4, vídeo H.264 e áudio AAC quando houver áudio; encoder ausente é uma falha explícita de dependência.
- `approve` exige plano válido e preview publicada. Registra em `approval.json` o hash canônico do plano, o SHA-256 dos bytes da preview, o SHA-256 de cada fonte referenciada e o instante UTC da aprovação.
- `render full` exige `approval.json` válido. Revalida o plano, recalcula os hashes e bloqueia se qualquer plano, preview ou fonte divergir. Quando liberada, usa exatamente a timeline aprovada, sem reanalisar nem reinterpretar o conteúdo, e publica `<workdir>\full.mp4` no perfil final do plano.
- Uma nova execução de `render preview` pode substituir a preview anterior. A aprovação anterior permanece apenas como registro verificável e deixa de autorizar `full` quando os hashes não coincidirem.

### 4. Plano JSON e timeline

`plan.json` usa `schema_version: 1` e uma lista ordenada de `sources`, `segments` e `overlays`. A validação é estrita, com allowlist de campos.

```json
{
  "schema_version": 1,
  "profile": {"aspect_ratio": "16:9"},
  "sources": [
    {"id": "main", "kind": "video", "path": "C:\\media\\main.mp4"},
    {"id": "broll-01", "kind": "video", "path": "C:\\media\\broll.mp4"},
    {"id": "music-01", "kind": "audio", "path": "C:\\media\\music.mp3"}
  ],
  "segments": [
    {
      "video": {"source_id": "main", "in_s": 0, "out_s": 12, "fit": "preserve"},
      "audio": {
        "mode": "keep",
        "tracks": [{"source_id": "main", "in_s": 0, "out_s": 12, "gain_db": 0}]
      }
    }
  ],
  "overlays": [
    {
      "text": "Praia em Paraty",
      "start_s": 3,
      "end_s": 6,
      "position": "bottom-right",
      "font": "Arial",
      "size_px": 48,
      "color": "#FFFFFF",
      "align": "right",
      "wrap": true
    }
  ],
  "audio_processing": {
    "normalize_loudness": true,
    "target_lufs": -16,
    "limiter": {"enabled": true, "ceiling_dbfs": -1},
    "ducking": false
  }
}
```

- A timeline é a concatenação dos segmentos na ordem do array. A duração de cada segmento é `video.out_s - video.in_s`; não existem sobreposições, lacunas, transições ou camadas visuais simultâneas.
- `video` define a imagem exibida. Um b-roll em tela cheia é representado por um segmento cujo `video.source_id` aponta para B, enquanto `audio.tracks` aponta para o áudio de A. O áudio embutido de B não é usado nesse caso.
- `audio.mode` aceita `keep`, `mute`, `replace` e `mix`. `keep` exige uma faixa da própria fonte visual, `mute` exige lista vazia, `replace` exige uma faixa e `mix` exige pelo menos duas. Cada faixa usa ganho explícito em dB, limitado a `-60` a `+12`; `loop: true` repete somente o trecho da faixa para preencher o segmento e depois o corta no fim.
- Para `keep` e `replace`, a faixa deve ter duração compatível com o segmento. Para `mix`, todas as faixas devem cobrir o segmento, por corte ou loop. Não há ducking automático.
- `profile.aspect_ratio` é obrigatório e deve ser `16:9`. O primeiro vídeo da timeline fornece resolução e taxa de quadros da full. `fit: preserve` só é aceito quando a proporção da fonte já coincide; fontes diferentes precisam declarar `contain` ou `crop`. `contain` preserva a imagem inteira, centraliza e usa barras pretas; `crop` preenche o perfil e corta simetricamente a partir do centro.
- `overlays` são textos estáticos por intervalo. As posições aceitas são `top-left`, `top-center`, `center`, `bottom-center` e `bottom-right`, sempre com margem segura fixa de 5%. O estilo aceita fonte instalada, tamanho em pixels, cor hexadecimal, alinhamento e quebra de linha simples. Não há coordenadas livres, animação, opacidade, template ou picture-in-picture.
- `audio_processing` pode normalizar para o alvo explícito, com padrão de `-16 LUFS` e limiter com teto de `-1 dBFS`. Equalização de frequência e restauração de ruído são rejeitadas como campos não suportados.

### Fora

- MCP ou outro adaptador de protocolo, painel, login, servidor, banco, telemetria e agente conversacional próprio, porque a CLI local é o contrato inicial.
- MOV, codecs além de MP4/H.264/AAC, MP3 e WAV, formatos verticais, múltiplas plataformas e presets de exportação, porque a primeira faixa de compatibilidade é 16:9 local.
- Picture-in-picture, composição de camadas visuais, transições, efeitos avançados, edição não linear e decisão editorial automática, porque o modelo do MVP é uma timeline linear explícita.
- Classificação automática de fala, ruído ou silêncio semântico, ducking, equalizador paramétrico, remoção de ruído, animações, templates complexos e exportação SRT, porque não são necessários para validar o fluxo principal.
- Mais de uma preview vigente, histórico de variantes, fila ou processamento concorrente, backup automático e limpeza automática de trabalhos, porque o MVP mantém um único estado local por trabalho.

## Checklist de entrega

### Aceite

- [x] A1: `capabilities --json` retorna um envelope válido, informa capacidades detectadas e recomendação local, e não envia dados para a rede.
- [x] A2: A transcrição de uma fonte aceita gera JSON com idioma, texto, segmentos e timestamps por palavra, preservando a fonte e seu hash.
- [x] A3: A análise de áudio gera loudness, picos, média e silêncio por intervalos, sem declarar classificação de fala ou ruído.
- [x] A4: A extração por timestamps e por intervalo gera PNGs e manifesto com a associação temporal correta, rejeitando entradas ambíguas ou fora da duração.
- [x] A5: Um plano com fonte principal, corte, junção, b-roll em tela cheia, áudio da fonte principal, música mixada e lettering passa na validação e gera uma timeline linear.
- [ ] A6: A mixagem respeita ganhos explícitos, normalização de loudness e limiter, sem ducking automático; o áudio de B não aparece quando B é usado como b-roll sobre A.
- [ ] A7: `render preview` publica exatamente `preview.mp4` em até 720 pixels na maior dimensão e substitui a preview anterior apenas após sucesso.
- [ ] A8: `approve` registra hashes do plano, preview e fontes; `render full` bloqueia plano, preview ou fonte alterados e libera a full quando todos coincidem.
- [ ] A9: Falhas de análise e renderização preservam originais, plano e análises, não publicam saída parcial e retornam erro JSON com exit code não zero.
- [ ] A10: Entradas não suportadas, acima de 60 minutos, acima de 4 GiB, com caminho inválido ou dependência ausente são rejeitadas antes do processamento correspondente; diretório de trabalho ou destino com reparse point também é rejeitado.
- [ ] A11: A CLI não interage por prompts, não executa shell intermediário e não usa rede durante análise, validação, aprovação ou renderização.
- [ ] A12: O instalador PowerShell cria o ambiente Python reproduzível, valida dependências e modelos e só instala componentes externos quando solicitado explicitamente.

### Critérios de sucesso

- [ ] C1: O cenário de vlog de viagens percorre `capabilities`, entrevista na conversa, análise, plano, preview, aprovação e full com uma única timeline.
- [ ] C2: Um agente consegue consumir todos os resultados de máquina por `stdout` JSON sem interpretar mensagens humanas ou stack traces.
- [ ] C3: A full aprovada é renderizada a partir do mesmo plano e das mesmas fontes verificadas, sem nova decisão editorial dentro da CLI.
- [ ] C4: Nenhuma execução altera ou remove as fontes originais, e nenhuma saída parcial é confundível com uma saída publicada.
- [ ] C5: A execução continua local quando as dependências e o modelo estão instalados, sem upload, telemetria ou fallback silencioso para serviço externo.

## Implementação

### Stack

| Área | Escolha |
|---|---|
| Runtime | Python 3.11 x64 em Windows local, com ambiente virtual por trabalho de instalação |
| Entrada da CLI | Pacote Python com entry point `editor`; `python -m editor` deve permanecer disponível para diagnóstico local |
| Transcrição | `faster-whisper` sobre CTranslate2, modelo local, CPU `int8` como baseline e CUDA opcional |
| Mídia | Executável FFmpeg local para inspeção, corte, composição, filtros de áudio e renderização |
| Validação e integridade | Biblioteca padrão para JSON, caminhos, SHA-256, temporários e subprocessos estruturados; `shell=False` |
| Dependências Python | Todas as versões fixadas em `requirements.lock`; nenhum download durante uma operação de trabalho |
| Dependências externas | FFmpeg, runtime CUDA e modelos Whisper são verificados localmente e instalados somente por ação explícita |

### Estrutura tocada

```text
pyproject.toml                 # pacote, versão e entry point editor
requirements.lock              # conjunto reproduzível de dependências Python
scripts/install.ps1            # criação do ambiente e instalação explícita
src/editor/                    # CLI, contratos, análise, plano e renderização
tests/                         # testes de contrato, integridade e cenário ponta a ponta
```

### Estilo e padrões

- Centralizar validação de caminhos, limites, mídia, plano, hashes e envelopes de erro; não duplicar regras entre subcomandos.
- Passar processos externos como arrays de argumentos, sem concatenar entrada externa em comandos, e registrar apenas diagnósticos seguros em `stderr`.
- Usar arquivos temporários dentro do trabalho e publicação atômica. `ponytail: o MVP mantém um único trabalho ativo por processo, trocar por isolamento por job quando houver execução concorrente real`.
- Rejeitar campos desconhecidos do plano e entradas fora da allowlist na borda. A lógica interna pode lançar exceções tipadas de domínio, convertidas em erro público somente no handler central da CLI.
- Não registrar tokens, caminhos desnecessários, conteúdo transcrito completo ou dados pessoais em logs técnicos.

### Contratos e módulos

- Envelopes de sucesso: `{"ok": true, "schema_version": 1, "command": "...", "data": {...}}`.
- Envelopes de erro: `{"ok": false, "schema_version": 1, "error": {"code": "...", "message": "...", "exit_code": 2}}`. A mensagem é segura, sem stack trace, query, comando completo ou detalhes internos.
- Exit codes estáveis: `0` sucesso; `1` erro interno inesperado; `2` argumento, caminho ou permissão inválida; `3` mídia não suportada ou limite excedido; `4` dependência, modelo, dispositivo ou fonte auxiliar ausente; `5` plano, estado ou aprovação inválida; `6` falha de análise, renderização ou publicação.
- Códigos mínimos de borda: `ARGUMENT_INVALID`, `PATH_INVALID`, `PERMISSION_DENIED`, `MEDIA_UNSUPPORTED`, `MEDIA_LIMIT_EXCEEDED`, `DEPENDENCY_MISSING`, `MODEL_MISSING`, `DEVICE_UNAVAILABLE`, `PLAN_INVALID`, `APPROVAL_REQUIRED`, `APPROVAL_STALE`, `ANALYSIS_FAILED`, `RENDER_FAILED` e `OUTPUT_PUBLISH_FAILED`.
- `approval.json` deve conter `schema_version`, `approved_at`, `plan_sha256`, `preview_sha256` e uma lista ordenada de `{source_id, sha256}`. Os hashes são sempre SHA-256 dos bytes relevantes, e o plano usa sua forma JSON canônica.

## Como provar

### Seams

- Descoberta com FFmpeg, modelo Whisper, CUDA ou fonte ausente, confirmando que `capabilities` descreve a limitação e que a operação dependente falha com código estável.
- Validação de limites e mídia, incluindo arquivo acima de 4 GiB, duração acima de 3.600 segundos, codec não permitido, fonte sem stream necessário e caminho de saída fora do trabalho.
- Validação da timeline, incluindo corte fora da fonte, duração de áudio incompatível, b-roll sem áudio A válido, overlay fora da timeline e perfil diferente de 16:9.
- Integridade de aprovação, alterando separadamente plano, preview e fonte e confirmando que `full` retorna `APPROVAL_STALE` sem publicar arquivo.
- Falha de FFmpeg ou Whisper no meio da operação, confirmando preservação dos artefatos existentes, limpeza segura do temporário e ausência de saída parcial.

### Estratégia

- Unitário: parser estrito, allowlists, normalização de caminhos, limites, intervalos, validação do plano, JSON canônico, hashes e mapeamento de erros para exit codes.
- Integração: fixtures curtas MP4 H.264/AAC, MP3, WAV e uma fonte de b-roll, com FFmpeg local e modelo Whisper instalado; verificar os JSONs e metadados dos renders.
- E2E: vlog de viagens com corte, junção, b-roll em tela cheia mantendo a narração, música de fundo, normalização, limiter e lettering, seguido de preview, aprovação e full.
- Manual: abrir os artefatos no diretório do trabalho, conferir os timestamps dos frames e da transcrição, reproduzir a preview e verificar que a full corresponde ao plano aprovado.

### Comandos

```text
editor capabilities --json
editor analyze transcribe --workdir <workdir> --source-id main --input <main.mp4> --json
editor analyze audio --workdir <workdir> --source-id main --input <main.mp4> --json
editor extract frames --workdir <workdir> --source-id main --input <main.mp4> --interval-s 5 --json
editor plan validate --workdir <workdir> --json
editor render preview --workdir <workdir> --json
editor approve --workdir <workdir> --json
editor render full --workdir <workdir> --json
```

## Boundaries

### Always

- Validar toda entrada externa antes de analisar, executar FFmpeg ou escrever artefatos.
- Processar localmente, sem telemetria, e retornar dados de máquina em JSON pelo `stdout`.
- Preservar originais, plano e análises; publicar somente resultados completos por substituição atômica.
- Recalcular os hashes na aprovação e na full; tratar mudança de fonte como invalidação da aprovação.
- Informar limitações detectadas em `capabilities`, sem alegar suporte que não foi verificado.

### Ask first

- A conversa deve obter do operador as decisões editoriais que faltarem antes de escrever o plano: objetivo, fontes e papéis, cortes, b-roll, áudio, música, letterings, perfil 16:9 e confirmação do fluxo preview/full.
- A conversa deve obter aprovação humana da preview antes de chamar `approve` e não pode tratar a existência do arquivo como aprovação.
- A instalação ou atualização de FFmpeg, runtime CUDA, dependências e modelos exige ação explícita do operador.

### Never

- Enviar mídia, transcrição, frames, plano ou hardware para serviço externo, nem fazer fallback silencioso para API.
- Montar comando shell com entrada externa, aceitar URL como fonte ou escrever fora do diretório de trabalho.
- Escolher cortes, destaques, classificação semântica ou conteúdo de lettering automaticamente dentro da CLI.
- Renderizar `full.mp4` sem aprovação válida, ou publicar `preview.mp4` e `full.mp4` parcialmente escritos.
- Alterar, remover ou sobrescrever os arquivos originais.

## Handoff

vibe-plan
rota: max

- Chat: recomendo novo chat para `vibe-plan`; continuidade no mesmo chat só por escolha consciente. O `spec.md` vivo é a ponte.
- O arquivo vivo `spec.md` entra no Git. `.vibeflow/spec-report.json` é evidência operacional e fica fora do Git.
- [ ] Aprovação humana (leu o arquivo e confirmou)
