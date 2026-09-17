# Implement: Robustez da renderização local e da transcrição real
# Alvo: phase-2-robustez-renderizacao-local
# Status: aprovado
# Route: medium · modo A · T1

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

## Handoff

Próxima task elegível: T4, T5 ou T6. A T2 e a T3 permanecem bloqueadas pela dependência sequencial.
