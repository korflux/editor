# Implement: Robustez da renderização local e da transcrição real
# Alvo: phase-2-robustez-renderizacao-local
# Status: aprovado
# Route: high · modo A · T5

## Reconhecimento

- A fila da Phase 2 tinha T1, T4, T5 e T6 elegíveis. T1 foi escolhida
  explicitamente pelo operador.
- `render_preview()` e `render_full()` compartilham `_build_ffmpeg_arguments()`
  e `process.run_tool()`. O grafo era inserido diretamente em `-filter_complex`,
  enquanto o MP4 já usava staging exclusivo em `.tmp` e limpeza no `finally`.
- O FFmpeg 9.0.1 do host não expõe a opção legada `-filter_complex_script`; a
  forma nativa `-/filter_complex <arquivo>` lê a mesma opção a partir de arquivo.

## T1: Transportar o grafo FFmpeg por arquivo temporário

### Implementação

- Adicionado arquivo UTF-8 exclusivo em `<workdir>/.tmp` para o conteúdo de
  `FfmpegGraph.filter_complex`, validado como arquivo regular antes da execução.
- Preview e full passaram a fornecer somente o caminho do arquivo ao FFmpeg,
  mantendo a lista de argumentos estruturada e sem shell intermediário.
- O ciclo de vida passou a limpar separadamente o script e o MP4 staging em
  sucesso, retorno não zero, exceção, timeout, validação e publicação, sem tocar
  preview/full vigentes.
- Adicionadas provas de grafo acima de 100.000 caracteres, limite de 32.767
  caracteres da linha de comando, limpeza em exceção/timeout e integração real.

### Prova

- `python -m editor --help` passou.
- A preview real pela CLI passou após a adaptação ao FFmpeg 9.0.1.
- `.venv\Scripts\python.exe -B -m unittest tests.test_preview_render.PreviewRenderTests.test_large_filter_graph_uses_file_and_keeps_process_arguments_short tests.test_preview_render.PreviewRenderTests.test_render_exception_and_timeout_clean_all_operation_temporaries -v` -> `Ran 2 tests ... OK`.
- `.venv\Scripts\python.exe -B -m unittest tests.test_preview_render.PreviewRenderTests.test_real_cli_publishes_preview_with_profile_and_audio -v` -> `Ran 1 test ... OK`.
- `.venv\Scripts\python.exe -B -m unittest tests.test_preview_render tests.test_full_render -v` -> `Ran 23 tests in 141.435s ... OK`.
- `git diff --check` passou.

## Registro

- **Plan:** T1 marcada como concluída.
- **Spec comprovada nesta fatia:** A1, A2, A3, C1 e C2.
- **Arquivos:** `src/editor/render/preview.py`, `src/editor/render/full.py`, `tests/test_preview_render.py` e os artefatos vivos da Phase 2.
- **Decisão:** o FFmpeg 9.0.1 instalado não reconhece `-filter_complex_script`; a forma suportada `-/filter_complex <arquivo>` foi usada para manter o grafo fora da linha de comando.

## T2: Aplicar perfil final limitado e compatível

### Reconhecimento

- `profile` já era validado por allowlist no schema v2, mas aceitava somente
  `aspect_ratio`; preview e full compartilhavam `_preview_dimensions()`.
- O hash canônico já inclui o documento original e `verify_approval()` já compara
  esse hash antes de iniciar o FFmpeg, portanto não foi criada lógica paralela de aprovação.

### Implementação

- `profile.max_full_width` passou a aceitar somente inteiro não booleano, múltiplo
  de 32 entre 32 e 3840, com padrão compatível de 1920.
- A validação publica o perfil calculado com teto resolvido e largura efetiva da
  primeira fonte visual, sem inserir o padrão no JSON canônico.
- A preview continua limitada a 720 pixels; a full usa `effective_full_width` e
  preserva fontes menores sem upscale.
- `plan validate`, `render preview` e `render full` retornam o mesmo perfil calculado.
- A prova cobre rejeição antes do FFprobe, render real, compatibilidade v2 e
  invalidação da aprovação por mudança explícita do perfil.

### Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_approval tests.test_preview_render tests.test_full_render -v` -> `Ran 55 tests in 178.115s ... OK`.
- `git diff --check` passou.

### Registro

- **Plan:** T2 marcada como concluída.
- **Spec comprovada nesta fatia:** A5 e C4.
- **Arquivos:** `src/editor/plan/schema.py`, `src/editor/plan/validation.py`,
  `src/editor/render/preview.py`, `src/editor/render/full.py`, quatro testes da
  fatia e os artefatos vivos da Phase 2.
- **Decisão:** o perfil calculado é saída derivada; somente campos explícitos do
  plano participam da serialização canônica, mantendo compatibilidade e distinguindo
  ausência do campo de uma alteração editorial explícita.

## T3: Validar artefatos renderizados sem limites de fonte

### Reconhecimento

- `_validate_rendered_preview()` era o validador compartilhado por preview e full,
  mas chamava `media.inspect_source()` e herdava os tetos de ingestão de 4 GiB e
  3.600 segundos.
- A mesma sondagem FFprobe já centralizava contêiner, streams e codecs; a separação
  necessária era de política, não de parser nem de pipeline de publicação.

### Implementação

- A sondagem comum foi isolada em `_inspect_media_file()`, mantendo uma única
  validação de metadados e codecs.
- `inspect_source()` continua aplicando os limites antes do FFprobe e também após
  obter a duração. O novo `inspect_rendered_artifact()` exige vídeo MP4/H.264 e o
  áudio esperado, mas não aplica tetos arbitrários de tamanho ou duração.
- O validador compartilhado de preview/full passou a usar a política de artefato;
  perfil, dimensões, proporção, áudio e duração esperada continuam sendo verificados
  antes da publicação atômica.
- As provas cobrem fonte acima de 4 GiB rejeitada antes da sondagem, full simulada
  acima de 4 GiB e 7.200 segundos aceita, e falhas de codec/stream, dimensão e
  duração preservando a preview vigente e limpando `.tmp`.

### Prova

- Testes novos isolados -> `Ran 3 tests in 0.553s ... OK`.
- `.venv\Scripts\python.exe -B -m unittest tests.test_audio_analysis tests.test_frame_extraction tests.test_plan_validation tests.test_preview_render tests.test_full_render -v` -> `Ran 70 tests in 160.606s ... OK`.
- Reexecução após a revisão de simplificação -> `Ran 70 tests in 158.593s ... OK`.
- Confirmação final antes do commit -> `Ran 70 tests in 159.169s ... OK`.
- `git diff --check` passou antes do registro final.

### Registro

- **Plan:** T3 marcada como concluída.
- **Spec comprovada nesta fatia:** A4 e C3; C2 permaneceu comprovada pela T1 e ganhou cobertura adicional de validação incompatível.
- **Arquivos:** `src/editor/media.py`, `src/editor/render/preview.py`, três testes da fatia e os artefatos vivos da Phase 2.
- **Decisão:** os limites de segurança pertencem à ingestão de fontes; artefatos gerados continuam estritos em estrutura e aderência ao render esperado, sem herdar tetos arbitrários de entrada.

## T4: Normalizar timestamps do Whisper com coerência textual

### Reconhecimento

- A transcrição já centralizava validação, serialização e publicação atômica em
  `analysis/transcription.py`, mas recusava qualquer palavra degenerada ou fora do
  segmento e publicava o texto fornecido pelo backend sem reconciliá-lo com `words[]`.
- A publicação existente já preservava o artefato vigente quando a construção da
  transcrição falhava, portanto a correção ficou no normalizador compartilhado.

### Implementação

- Palavras com timestamps até 0,5 segundo fora do segmento são limitadas à fronteira;
  deriva superior continua falhando como `ANALYSIS_FAILED`.
- Palavras sem duração, inclusive após clamp, são removidas. Segmentos sem palavra
  válida também são omitidos, e uma resposta totalmente vazia continua irrecuperável.
- Textos de segmento e global são reconstruídos somente das palavras publicadas.
- O envelope de sucesso expõe `discarded_word_count`, `clamped_word_count` e
  `discarded_segment_count`, inclusive quando todos são zero, sem alterar o schema 1
  do artefato canônico.

### Prova

- Primeira execução após a implementação -> `Ran 11 tests in 1.112s ... OK`.
- Reexecução após simplificação -> `Ran 11 tests in 1.113s ... OK`.
- Confirmação final antes do commit -> `Ran 11 tests in 1.122s ... OK`.
- A matriz cobre duração zero, clamps inicial e final, clamp que zera palavra,
  segmento esvaziado, deriva superior a 0,5 segundo e resultado totalmente vazio.
- `git diff --check` passou.

### Registro

- **Plan:** T4 marcada como concluída.
- **Spec comprovada nesta fatia:** A6, A7, C5 e C6.
- **Arquivos:** `src/editor/analysis/transcription.py`, `tests/test_transcription.py`
  e os artefatos vivos da Phase 2.
- **Decisão:** a tolerância corrige somente deriva limitada e mensurável; qualquer
  desvio maior continua bloqueando a publicação para não alterar evidência editorial.

## T5: Resolver o arquivo real das fontes de texto

### Reconhecimento

- A validação estrutural aceitava somente o booleano `_font_is_installed()`, consultando o Registro no Windows ou apenas as famílias do `fc-list`; o grafo emitia `drawtext=font=...` e deixava o FFmpeg resolver o arquivo.
- O resultado de `load_validated_plan()` já era o ponto compartilhado por preview e full. A alteração ficou nesse fluxo, com metadado interno separado do resultado público de `validate_plan()`.

### Implementação

- O resolvedor Windows consulta HKCU e HKLM, relaciona o nome registrado ao valor de arquivo e aceita somente `.ttf`, `.otf`, `.ttc` e `.otc` em diretórios de fontes do sistema ou do usuário.
- A validação física reutiliza `resolve_source_path()`, preservando a rejeição de arquivo ausente, extensão não permitida, symlink, junction e reparse point, antes da inspeção de mídia.
- Em hosts com fontconfig, `fc-match` retorna família, estilo e arquivo concreto; fallback para outra família é rejeitado e nenhum `fontconfig.xml` é criado no trabalho.
- O caminho resolvido permanece em `_resolved_font_files`, usado somente pelo grafo interno; `validate_plan()`, JSON canônico e envelopes de renderização não o publicam.
- `drawtext` passou a usar `fontfile` com escape próprio para o caminho real. Arial Bold foi renderizada no fluxo real da preview.

### Prova

- `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v` -> `Ran 45 tests in 25.264s ... OK`.
- A matriz confirmou HKLM, HKCU, arquivo ausente, extensão recusada e reparse point como `PLAN_INVALID`, além da ausência do caminho no contrato público.
- O teste de integração real confirmou `Arial Bold`, `fontfile` apontando para `arialbd.ttf`, publicação da preview e ausência de `fontconfig.xml` lateral.
- `git diff --check` passou.

### Registro

- **Plan:** T5 marcada como concluída.
- **Spec comprovada nesta fatia:** A8 e C7.
- **Arquivos:** `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`, `tests/test_plan_validation.py` e `tests/test_preview_render.py`, além dos artefatos vivos da Phase 2.
- **Decisão:** HKCU tem precedência sobre HKLM quando os dois registram o mesmo nome; a resolução continua limitada às pastas confiáveis e às extensões suportadas, sem aceitar caminho vindo do plano.

## Handoff

Próxima task elegível: T6.
