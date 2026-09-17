# Spec: Robustez da renderização local e da transcrição real
# Alvo: phase-2-robustez-renderizacao-local
# Status: aprovado

## Objetivo

Permitir que Marco e agentes compatíveis executem o fluxo normal da CLI com planos
editoriais reais e longos no Windows, sem scripts laterais, materialização obrigatória
de rough cuts ou monkey patches. A entrega corrige os limites observados no primeiro
trabalho de 42 minutos, preserva a aprovação por hashes e torna transcrição, perfil
final e fontes de texto determinísticos.

## Inventário

1. Grafo FFmpeg fora da linha de comando.
2. Políticas distintas para fontes de entrada e artefatos renderizados.
3. Perfil final limitado e compatível com planos schema v2 existentes.
4. Normalização tolerante e coerente da resposta do Whisper.
5. Resolução determinística de arquivos de fonte no Windows.
6. Gate editorial obrigatório na skill antes da aprovação.

## Suposições e decisões

1. O schema do plano permanece na versão 2. `profile.max_full_width` será opcional,
   terá padrão `1920` e aceitará múltiplos de 32 entre `32` e `3840`. A largura
   efetiva será `min(largura da primeira fonte visual, max_full_width)`, sem upscale;
   a altura continuará derivada do perfil 16:9. Assim, planos existentes continuam
   válidos e fontes pequenas preservam sua resolução.
2. Preview e full usarão o mesmo grafo editorial. A preview continua limitada a 720
   pixels; `max_full_width` afeta somente a full.
3. O filtro complexo será sempre gravado em arquivo temporário regular dentro de
   `<workdir>/.tmp` e fornecido ao FFmpeg por `-filter_complex_script`. Um único caminho
   reduz variação entre Windows e outros hosts e elimina dependência do tamanho do plano.
4. O limite vigente de 4 GiB e 3.600 segundos continua exclusivo das fontes de entrada.
   Preview e full serão validadas por formato, streams, codecs, dimensões e duração
   esperada, sem limite arbitrário de 4 GiB.
5. A normalização do Whisper tolera somente anomalias observadas: palavras com
   `end <= start` são removidas; início ou fim até 0,5 segundo fora do segmento é
   limitado à fronteira; desvios maiores continuam falhando como `ANALYSIS_FAILED`.
6. Depois do ajuste temporal, palavras ainda sem duração são removidas. Segmentos sem
   palavra válida são omitidos; se nenhum segmento válido restar, a operação falha.
   O texto de cada segmento e o texto global são reconstruídos das palavras publicadas,
   impedindo divergência entre `text` e `words[]`.
7. O artefato de transcrição permanece em `schema_version: 1`. Os contadores
   `discarded_word_count`, `clamped_word_count` e `discarded_segment_count` entram no
   envelope de sucesso da CLI, não no artefato canônico.
8. Nomes de fonte continuam sendo a única entrada permitida no plano. O editor resolve
   internamente o arquivo registrado no Windows, valida arquivo regular e extensão de
   fonte suportada e passa `fontfile` ao `drawtext`. O plano nunca aceita caminho de fonte.
9. Esta fase elimina a necessidade técnica de rough cuts para contornar o tamanho do
   comando. Não cria grafo de proveniência para derivados produzidos externamente.
10. A CLI continua sem decisão editorial automática. A skill passa a exigir auditoria
    das fronteiras e inspeção visual antes de solicitar aprovação humana.

## Escopo e comportamento

### 1. Transporte seguro do grafo FFmpeg

- `render preview` e `render full` validam o plano e constroem o grafo como hoje, mas
  gravam seu conteúdo UTF-8 em arquivo exclusivo dentro de `.tmp`.
- O processo recebe apenas o caminho regular do script de filtro por argumento
  estruturado, com `shell=False`; o conteúdo do plano não retorna à linha de comando.
- O arquivo de filtro é removido em sucesso, erro do FFmpeg, timeout, falha de validação
  ou publicação. Preview/full vigentes permanecem intactas em qualquer falha.
- Um plano equivalente ao caso real, com ao menos 231 segmentos, 69 fontes declaradas
  e 43 camadas, deve chegar ao FFmpeg no Windows sem erro de criação do processo.
- O render não deve materializar segmentos intermediários nem alterar o plano para
  reduzir o grafo.

### 2. Validação separada de entrada e saída

- A inspeção de fontes mantém allowlists e limites vigentes antes do processamento.
- A validação de artefato renderizado usa contrato próprio e não chama a política de
  tamanho/duração destinada a fontes.
- Uma full H.264/AAC regular acima de 4 GiB pode ser validada e publicada quando
  dimensões, duração, proporção e presença de áudio coincidem com o render esperado.
- Contêiner, codec ou metadado incompatível continua falhando como `RENDER_FAILED`,
  sem publicar o temporário nem expor stderr do FFmpeg.

### 3. Perfil determinístico da full

- `profile` aceita somente `aspect_ratio` e o novo `max_full_width`; qualquer outro
  campo continua rejeitado por allowlist.
- Ausência de `max_full_width` equivale a `1920`. O valor efetivo aparece no resultado
  de `plan validate`, `render preview` e `render full` como parte do perfil calculado.
- A full usa a largura efetiva definida nas decisões desta spec; não herda mais
  irrestritamente a resolução nativa da primeira fonte.
- Como `profile` pertence ao JSON canônico, uma alteração explícita em
  `max_full_width` invalida a aprovação vigente antes de renderizar a full.

### 4. Normalização da transcrição

- Segmentos e palavras continuam exigindo valores numéricos finitos, texto válido e
  timestamps dentro da duração da fonte.
- A normalização ocorre em funções internas testáveis, sem monkey patch ou script
  externo, antes da serialização e da publicação atômica.
- Tokens removidos nunca permanecem apenas em `text`; a representação textual publicada
  é derivada das mesmas palavras que conservam timestamps.
- O envelope de sucesso informa os três contadores de normalização. Uma execução sem
  anomalias retorna zero nos três campos.
- Resposta malformada, desvio superior a 0,5 segundo ou resultado inteiramente vazio
  preserva o artefato anterior e retorna `ANALYSIS_FAILED`.

### 5. Arquivo de fonte determinístico

- No Windows, o nome do plano é comparado aos nomes registrados em HKLM e HKCU e
  resolvido para o arquivo correspondente em diretórios locais confiáveis de fontes.
- O resolvedor aceita somente arquivos regulares locais com extensão suportada e não
  segue symlink, junction ou reparse point.
- A validação disponibiliza ao renderer apenas metadados internos; o caminho resolvido
  não entra no plano canônico nem no envelope público.
- `drawtext` usa `fontfile` escapado. Nome instalado sem arquivo utilizável falha como
  `PLAN_INVALID` antes do FFmpeg.
- Outros sistemas mantêm a descoberta por fontconfig, mas também resolvem o arquivo
  concreto antes da construção do grafo.

### 6. Gate editorial da skill

- Antes da preview, o agente cruza cada fronteira de fala com `words[]` e registra cortes
  intencionais que removem fala, distinguindo-os de pausas naturais.
- O agente extrai frames próximos a cortes com movimento, revelação visual ou fim do
  vídeo e confirma continuidade e encerramento natural.
- Falas em idioma diferente do destino editorial exigem decisão explícita entre manter,
  traduzir ou legendar.
- Textos de apoio são verificados na preview quanto a fonte real, caixa, safe area,
  quebra, legibilidade e corte. A validação estrutural isolada não conta como prova visual.
- Arquivos excluídos e trechos de fala removidos intencionalmente entram no resumo
  apresentado ao usuário antes da aprovação.

### Fora

- Decisão automática de cortes, tradução ou qualidade editorial pelo núcleo da CLI.
- Novos codecs, contêineres, transições, legendas sincronizadas ou formatos de fonte.
- Proveniência de rough cuts criados por ferramentas externas; o fluxo principal deve
  renderizar diretamente o plano detalhado.
- Estimativa antecipada de bitrate, tamanho final ou espaço livre em disco.
- Encoder configurável, aceleração por hardware ou presets adicionais de qualidade.

## Checklist de entrega

### Aceite

- [x] A1: Preview e full usam script de filtro temporário, nunca grafo inline.
- [x] A2: O plano detalhado de regressão inicia o FFmpeg no Windows sem exceder a linha de comando.
- [x] A3: O script de filtro e o MP4 temporário são limpos em todos os caminhos de saída.
- [ ] A4: Fontes continuam limitadas a 4 GiB/3.600 s, mas saídas válidas acima de 4 GiB não são rejeitadas por essa política.
- [x] A5: `max_full_width` opcional aplica teto padrão de 1920 sem upscale e invalida a aprovação quando explicitamente alterado.
- [ ] A6: Anomalias Whisper dentro do contrato tolerante são normalizadas com texto e palavras coerentes.
- [ ] A7: Anomalias fora do contrato falham sem substituir a transcrição vigente.
- [ ] A8: Arial Bold registrada produz `fontfile` apontando para o arquivo bold real, sem `fontconfig.xml` lateral.
- [ ] A9: Preview e full continuam consumindo o mesmo grafo, plano e fontes aprovados.
- [ ] A10: A skill impede pedido de aprovação antes das auditorias editorial, temporal e visual documentadas.

### Critérios de sucesso

- [x] C1: Um teste de regressão confirma filtro acima de 100.000 caracteres passado por arquivo e argumento de processo abaixo do limite do Windows.
- [x] C2: Testes de falha de render confirmam preservação do artefato vigente e `.tmp` sem arquivos operacionais residuais.
- [ ] C3: Uma full simulada com tamanho superior a 4 GiB passa pela política de saída, enquanto uma fonte do mesmo tamanho continua retornando `MEDIA_LIMIT_EXCEEDED`.
- [x] C4: Planos v2 sem `max_full_width` validam com teto efetivo 1920; valores desconhecidos, fora da faixa ou não múltiplos de 32 retornam `PLAN_INVALID`.
- [ ] C5: Testes cobrem palavra de duração zero, clamp nas duas fronteiras, clamp que zera a palavra, segmento esvaziado, desvio acima de 0,5 s e resultado totalmente vazio.
- [ ] C6: Testes confirmam que `transcription.text` equivale à concatenação dos textos reconstruídos dos segmentos publicados.
- [ ] C7: Testes do resolvedor de fonte cobrem HKLM, HKCU, arquivo ausente, extensão recusada e reparse point; o grafo usa `fontfile`.
- [ ] C8: A jornada E2E vigente e a suíte completa continuam passando sem scripts de `A:\Youtube\Teste\.tmp`.

## Implementação

### Stack

| Área | Escolha |
|---|---|
| Processo | existente: `subprocess` com lista de argumentos e `shell=False`; delta: `-filter_complex_script` |
| Mídia | existente: FFmpeg/FFprobe locais; delta: política específica para saída renderizada |
| Plano | existente: schema v2 estrito; delta: `profile.max_full_width` opcional |
| Transcrição | existente: faster-whisper local; delta: normalização limitada antes da serialização |
| Fontes | existente: Registro/fontconfig; delta: resolver arquivo concreto para `fontfile` |
| Dependências novas | Nenhuma |

### Estrutura tocada

```text
src/editor/render/preview.py           # script temporário, argumentos e validação de render
src/editor/render/full.py              # teto de resolução final calculado
src/editor/render/ffmpeg_graph.py      # drawtext com fontfile resolvido
src/editor/media.py                    # separa políticas de fonte e saída
src/editor/plan/schema.py              # allowlist de max_full_width
src/editor/plan/validation.py          # perfil efetivo e resolução segura de fontes
src/editor/analysis/transcription.py   # normalização temporal tolerante e coerente
tests/test_plan_validation.py          # perfil e fonte concreta
tests/test_transcription.py            # matriz de anomalias do Whisper
tests/test_preview_render.py            # filtro por arquivo e limpeza
tests/test_full_render.py               # saída >4 GiB e teto final
tests/e2e/test_phase_2_flow.py          # plano detalhado sem rough cuts
skills/vibe-editor/SKILL.md             # gates temporal, visual e editorial
skills/vibe-editor/references/          # checklist operacional quando necessário
.vibeflow/REGRAS.md                     # mapa vivo da fase após implementação
```

### Estilo e padrões

- Reutilizar os layouts e publicações atômicas de `workdir.py`.
- Centralizar a diferença entre inspeção de entrada e validação de saída; não
  distribuir flags de bypass pelos chamadores.
- Manter comentário semântico em toda função nova ou alterada.
- Rejeitar entradas na borda e nunca expor caminho interno de fonte, comando ou stderr.
- Nenhum script específico do trabalho `A:\Youtube\Teste` entra no produto.

### Contratos e módulos

- Limites de fonte: 4 GiB e 3.600 segundos; imagens mantêm limites vigentes.
- Limites do perfil: `max_full_width` inteiro, não booleano, múltiplo de 32, entre
  32 e 3840; padrão 1920.
- Limite de normalização: desvio de palavra de no máximo 0,5 segundo em relação
  ao segmento.
- Catálogo de erros permanece: `PLAN_INVALID` para perfil/fonte inválidos,
  `ANALYSIS_FAILED` para resposta Whisper irrecuperável, `RENDER_FAILED` para saída
  incompatível e `OUTPUT_PUBLISH_FAILED` para falha de publicação.

## Como provar

### Seams

- Documento do plano para perfil calculado e hash canônico.
- Grafo em memória para arquivo temporário e argumentos do processo.
- FFmpeg concluído para validação de saída sem política de fonte.
- Resposta do Whisper para palavras/segmentos normalizados e artefato canônico.
- Nome de fonte do plano para arquivo local confiável e filtro `drawtext`.
- Evidências de transcrição/frames para checklist de aprovação da skill.

### Estratégia

- Unitário: limites do perfil, normalização Whisper, resolvedor de fonte e políticas
  distintas de inspeção.
- Integração: capturar argumentos reais do FFmpeg, ler o arquivo de filtro durante a
  chamada e confirmar limpeza/preservação em sucesso e falha.
- E2E: plano sintético equivalente ao detalhado, usando fontes pequenas repetidas para
  provar escala estrutural sem adicionar fixtures grandes ao Git.
- Manual: renderizar uma amostra com Arial Bold e inspecionar preview 720p e full 1080p.

### Comandos

```powershell
.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_transcription tests.test_preview_render tests.test_full_render -v
.venv\Scripts\python.exe -B -m unittest tests.e2e.test_phase_2_flow -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -v
python -X utf8 C:\Users\marco\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\vibe-editor
gitleaks detect --source . --no-banner
.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip
```

## Boundaries

### Always

- Preservar plano, preview, aprovação, full e transcrição vigentes em qualquer falha.
- Manter execução local, offline, sem shell intermediário e sem download implícito.
- Vincular mudanças explícitas do perfil ao hash canônico e à aprovação.
- Provar o caso estrutural grande no Windows, onde a regressão ocorreu.

### Ask first

- Alterar o padrão de 1920, o teto de 3840 ou a regra de não fazer upscale.
- Mudar schema version, formato do artefato de transcrição ou approval.json.
- Adicionar proveniência de derivados, encoder configurável ou aceleração por hardware.

### Never

- Reintroduzir o grafo completo na linha de comando ou usar shell para contornar limites.
- Aceitar caminho de fonte vindo do plano ou confiar apenas no nome resolvido pelo FFmpeg.
- Desabilitar limites das fontes para permitir uma saída grande.
- Corrigir transcrição silenciosamente além da tolerância definida.
- Gerar rough cuts como fallback automático ou esconder deles a proveniência aprovada.
- Solicitar aprovação humana sem a auditoria editorial e visual da skill.

## Handoff

vibe-plan

- Chat: recomende novo chat para `vibe-plan`; continuidade no mesmo chat só por escolha consciente. O `spec.md` vivo é a ponte.

- [x] Aprovação humana (leu o arquivo e confirmou)
