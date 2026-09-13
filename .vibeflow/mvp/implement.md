# Implement: MVP CLI local de análise e edição linear
# Alvo: mvp
# Status: aprovado
# Plan: plan.md (mesma pasta)

## Abertura

- **ROUTE:** max
- **Modo:** A
- **Fila:** T11 e correções pós-review R1-R12 e N1-N2 implementadas, aguardando nova `vibe-review` e confirmação humana
- **Analyze:** `analyze.md` aprovado, veredito `limpo`
- **Pergunta da T10:** uma aprovação vigente libera a full somente quando plano, preview e fontes continuam com os mesmos hashes?

## T1: Criar CLI instalável com smoke test

### Reconhecer

- O repositório não tinha código de aplicação, manifesto Python, pacote ou testes.
- A task não tinha dependências de runtime e os caminhos previstos estavam livres para criação.
- A implementação usa apenas `argparse`, `json`, `dataclasses` e `subprocess` da biblioteca padrão, sem shell intermediário ou rede.

### Codar

- `pyproject.toml` define o pacote `editor` em `src/` e o entry point `editor`.
- `src/editor/contracts.py` centraliza versão, schema, exit codes, erro tipado, envelopes e serialização JSON determinística.
- `src/editor/cli.py` centraliza parser, ajuda, despacho inicial, tratamento de erros e saída segura.
- `src/editor/__main__.py` mantém `python -m editor`; `--version --json` fornece um sucesso verificável até as operações do MVP serem implementadas.
- `tests/test_cli_contract.py` executa processos reais para módulo e entry point instalado, além dos envelopes de sucesso e erro.

### Testar

- `python -m pip install --no-deps -e .` -> instalação editável concluída.
- `python -m editor --help` -> ajuda exibida, exit code `0`.
- `editor --help` -> ajuda exibida, exit code `0`.
- `python -m unittest discover -s tests -p "test_cli_contract.py" -v` -> `Ran 4 tests ... OK`.

### Simplificar e re-testar

- A revisão manteve a implementação sem abstrações especulativas: ainda não há dispatcher de mídia, dependências externas ou comandos além da fundação contratual.
- A suíte foi executada novamente após a revisão e permaneceu verde.

### Registro

- **Plan:** T1 marcada como concluída.
- **Spec comprovada nesta fatia:** base de A11 e C2, sem marcar os critérios globais antes da cobertura dos subcomandos seguintes.
- **Mensagem de commit:** `task(T1): create installable cli contract`

## T2: Provisionar ambiente local reproduzível

### Reconhecer

- A T1 deixou uma CLI Python instalável, mas sem dependências de runtime, lockfile ou instalador.
- O plano exige Python 3.11 x64 no Windows, `faster-whisper` sobre CTranslate2, FFmpeg local, modelos locais e gates de segredo/SCA.
- A primeira verificação ocorreu antes do provisionamento do venv operacional e encontrou somente Python 3.12/3.13 no PATH. Após o provisionamento explícito, o `.venv` passou a fornecer Python 3.11.15 x64, backend Whisper, modelo base e pip-audit para os gates do MVP.

### Codar

- `requirements.lock` foi resolvido para Python 3.11 x64 no Windows com 50 pacotes pinados e 648 hashes SHA-256. Os pins diretos são `faster-whisper==1.2.1`, `ctranslate2==4.8.0`, `pip-audit==2.10.1`, `setuptools==80.9.0` e `wheel==0.45.1`.
- `scripts/install.ps1` separa verificação e instalação. `-Verify` é somente leitura; `-Install` cria o venv, instala o lock com `--require-hashes`, instala a CLI em modo editável e roda `pip check`.
- FFmpeg, gitleaks, modelos e CUDA só são instalados por opções explícitas e origens locais. O script valida caminhos regulares, rejeita reparse points e usa chamadas externas com argumentos estruturados.
- `-SecurityGates` executa gitleaks e pip-audit somente depois da checagem de disponibilidade, bloqueando o resultado quando uma ferramenta está ausente.
- `tests/test_installation.py` cobre lockfile, modo somente leitura, rejeição do Python incompatível, cópia de modelo local e bloqueio do SCA.
- `.vibeflow/REGRAS.md` foi sincronizado com os novos arquivos, versões e convenções de instalação.

### Testar

- `python -m unittest discover -s tests -p "test_installation.py" -v` -> `Ran 6 tests ... OK`.
- `python -m unittest discover -s tests -v` -> `Ran 10 tests ... OK`.
- `pwsh -NoProfile -File scripts/install.ps1 -Verify` -> exit `0`, validando o lockfile, o `.venv` Python 3.11.15 x64, backend, modelo base, FFmpeg, gitleaks, CUDA opcional e `pip check`, sem instalar nem criar artefatos.
- `gitleaks detect --source . --no-banner` -> exit `0`, `no leaks found`.
- `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip` -> exit `0`, sem vulnerabilidades conhecidas.
- `.venv\Scripts\python.exe -B -m pip check` -> exit `0`; o conflito observado no Python global permanece registrado em `.erros-encontrados/2026-09-10-python-global-inquirer-editor.md`, fora do ambiente operacional.

### Simplificar e re-testar

- A revisão removeu estado interno de checks que não era consumido e corrigiu o controle explícito de sobrescrita de modelos com `-Force`.
- A suíte específica permaneceu verde após a simplificação, e a suíte completa confirmou ausência de regressão na CLI da T1.

### Registro

- **Plan:** T2 marcada como concluída; a limitação do host e o conflito global foram registrados no `plan.md`.
- **Spec comprovada nesta fatia:** o contrato de provisionamento local de A12 e a ausência de fallback externo prevista em C5; a execução de mídia continua nas tasks seguintes.
- **Arquivos:** `requirements.lock`, `scripts/install.ps1`, `tests/test_installation.py`, `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-09-10-python-global-inquirer-editor.md`.
- **Mensagem de commit:** `task(T2): provision reproducible local environment`

## T3: Detectar capacidades locais com recomendação determinística

### Reconhecer

- A T1 deixou o parser e os envelopes centrais, mas qualquer subcomando ainda era recusado como indisponível.
- A T2 definiu as origens locais aceitas: FFmpeg/FFprobe em `.tools/ffmpeg`, backend `faster-whisper`/CTranslate2 e modelos CTranslate2 `tiny`, `base` e `small` em `models`.
- O fluxo precisava responder mesmo com dependências opcionais ausentes, sem rede, download, edição de arquivos ou fallback remoto.

### Codar

- `src/editor/dependencies.py` concentra sondagem de executáveis com `shell=False`, timeout, versão do FFmpeg, encoders `libx264`/`aac`, pacotes Whisper, GPU/VRAM via `nvidia-smi`, runtime CUDA pelo CTranslate2, memória do sistema e validação dos arquivos mínimos de modelos.
- `src/editor/capabilities.py` monta o inventário versionado de sistema, dependências, dispositivos, formatos, limites, operações e recomendação determinística. A ordem é `small` em CUDA, `base` em CPU e `tiny` como fallback local.
- `src/editor/cli.py` registra `capabilities` e preserva `--json` antes ou depois do subcomando. O comando publica somente o envelope JSON.
- `tests/test_capabilities.py` cobre o processo real, as duas posições de `--json`, a ordem da recomendação, o contrato de modelos, os encoders exigidos e o fallback quando o runtime CUDA não está utilizável.

### Testar

- `python -m editor capabilities --json` -> exit `0`, envelope `capabilities` válido e stdout estruturado; o host atual reportou FFmpeg/FFprobe, encoders de render, backend Whisper, modelo base e runtime local disponíveis.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_capabilities.py" -v` -> `Ran 5 tests ... OK`.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` -> suíte completa verde após a correção pós-review.

### Simplificar e re-testar

- A falha inicial do teste foi diagnosticada como bloqueio de ACL em `%TEMP%`, não como falha do detector. O fixture foi movido para um diretório temporário dentro do workspace, seguindo o padrão de `test_installation.py`.
- A descoberta mantém somente os campos necessários ao contrato, sem importar o backend pesado nem adicionar dependências novas.

### Registro

- **Plan:** T3 marcada como concluída.
- **Spec comprovada nesta fatia:** A1.
- **Arquivos:** `src/editor/cli.py`, `src/editor/capabilities.py`, `src/editor/dependencies.py`, `tests/test_capabilities.py`, `.vibeflow/REGRAS.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`.
- **Mensagem de commit:** `task(T3): add local capabilities discovery`

## T4: Analisar áudio com guardrails de mídia

### Reconhecer

- T3 deixou o parser de envelopes e a sondagem local, mas não havia dispatcher de análise, validação de mídia ou layout de trabalho.
- A T4 exige validação antes do FFmpeg, um único artefato em `analysis/audio/<source-id>.json`, preservação de estado em falhas e métricas de amplitude sem classificação semântica.
- O fluxo foi separado em `workdir.py` para caminhos e publicação, `media.py` para FFprobe e limites, `process.py` para subprocessos estruturados e `analysis/audio.py` para a operação de áudio.

### Codar

- `src/editor/cli.py` registra `analyze audio`, aceita `--json` antes ou depois dos subcomandos e encaminha a operação sem prompts.
- `src/editor/workdir.py` valida diretório absoluto gravável, `source-id`, componentes regulares, destinos internos e publicação atômica por `.tmp`.
- `src/editor/media.py` valida extensão, contêiner, streams, codecs, tamanho de 4 GiB e duração de 3.600 s com `ffprobe`; a fonte é identificada por SHA-256.
- `src/editor/process.py` executa FFmpeg/FFprobe com argumentos estruturados, `shell=False`, timeout e PCM float32 em streaming.
- `src/editor/analysis/audio.py` usa `ebur128` para loudness integrado e pico verdadeiro, calcula média/RMS e pico por janela e detecta somente amplitude abaixo de `-45 dBFS` por pelo menos `0,3 s`.
- `tests/fixtures/media/main.mp4` fornece uma fonte curta H.264/AAC com silêncio inicial e final; `tests/test_audio_analysis.py` cobre processo real, rejeição precoce e falha simulada.

### Testar

- `python -m editor analyze audio --workdir A:\Projetos\editor\tmp-t4-run --source-id main --input tests/fixtures/media/main.mp4 --window-s 0.5 --json` -> exit `0`; envelope e artefato publicados.
- `python -m unittest discover -s tests -p "test_audio_analysis.py" -v` -> `Ran 4 tests ... OK`.
- `python -B -m unittest discover -s tests -v` -> `Ran 18 tests ... OK`.
- `gitleaks detect --source . --no-banner` -> exit `0`, `no leaks found`.

### Simplificar e re-testar

- A análise mantém somente o primeiro stream de áudio validado, usa processamento PCM em streaming e descarta stderr técnico do processo, porque o contrato público não precisa do diagnóstico bruto e a memória não deve crescer com a duração da fonte.
- A publicação só ocorre depois de `ffprobe`, loudness, janelas, silêncio e serialização concluírem; falhas preservam os arquivos existentes e removem o temporário.
- A suíte específica foi executada novamente após a simplificação e permaneceu verde; a suíte completa confirmou ausência de regressão nas T1 a T3.

### Registro

- **Plan:** T4 marcada como concluída.
- **Spec comprovada nesta fatia:** A3. Os critérios globais de preservação, contrato completo e jornada permanecem abertos até suas tasks correspondentes.
- **Arquivos:** `.vibeflow/REGRAS.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`, `src/editor/cli.py`, `src/editor/capabilities.py`, `src/editor/workdir.py`, `src/editor/media.py`, `src/editor/process.py`, `src/editor/analysis/__init__.py`, `src/editor/analysis/audio.py`, `tests/test_audio_analysis.py`, `tests/fixtures/media/main.mp4`.
- **Decisões:** MEDIA-01, DATA-01, OPS-01, PRIV-01 e CONTRACT-01 implementadas para análise de áudio.
- **Mensagem de commit:** `task(T4): add guarded audio analysis`

## T5: Transcrever fonte local com timestamps por palavra

### Reconhecer

- T4 já centralizava validação de fonte, hash, limites, diretório de trabalho, temporários e publicação atômica.
- T3 já centralizava inventário de backend, GPU e modelos `tiny`, `base` e `small`, além da recomendação determinística.
- O provisionamento explícito instalou CPython 3.11.15 x64, as dependências do `requirements.lock`, o backend local e o modelo CTranslate2 `base`; `Verify` passou sem CUDA, que é opcional.

### Codar

- `src/editor/cli.py` registra `analyze transcribe`, suas opções de idioma, modelo, dispositivo e `--json`; a borda também fixa stdout/stderr em UTF-8 para manter envelopes determinísticos no Windows.
- `src/editor/whisper_backend.py` resolve somente modelos locais válidos, reaproveita `choose_recommendation`, aplica CPU `int8` ou CUDA `float16`, rejeita dispositivo indisponível e carrega `WhisperModel` com `local_files_only=True`.
- `src/editor/analysis/transcription.py` reutiliza `media.inspect_source`, valida idioma e timestamps, normaliza segmentos/palavras e publica `analysis/transcription/<source-id>.json` somente após a resposta completa.
- `src/editor/workdir.py` generaliza o layout e a publicação atômica para `audio` e `transcription`; `dependencies.py` valida diretórios de modelo sem links ou reparse points.
- `tests/test_transcription.py` cobre artefato canônico, seleção CPU/CUDA, bloqueio de download, ausência de timestamps, rejeição precoce e envelope de modelo ausente.
- `tests/test_installation.py` mantém o cenário de Python incompatível usando o launcher `py`, evitando que a própria suíte deixe de testar esse contrato quando executada no Python 3.11 válido.

### Testar

- `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_transcription.py" -v` -> `Ran 8 tests ... OK`.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` e `python -B -m unittest discover -s tests -v` -> `Ran 26 tests ... OK` em cada runtime.
- `.venv\Scripts\python.exe -m editor analyze transcribe --workdir A:\Projetos\editor\tmp-t5-run --source-id main --input tests\fixtures\media\main.mp4 --device cpu --model base --json` -> exit code `0`, artefato canônico publicado, idioma `en` e `segment_count: 0`; sem `--model`, a seleção automática também retornou exit code `0` usando `base`.
- `pwsh -NoProfile -File scripts/install.ps1 -Verify -PythonPath C:\Users\marco\AppData\Roaming\uv\python\cpython-3.11-windows-x86_64-none\python.exe` -> exit code `0`, backend, modelo, FFmpeg, gitleaks, pip-audit e `pip check` disponíveis.
- `pip-audit -r requirements.lock --no-deps --disable-pip` -> `No known vulnerabilities found` após atualizar os pins vulneráveis de `setuptools` e `wheel`.
- `gitleaks detect --source . --no-banner` -> nenhum segredo encontrado.
- A API pinada do `WhisperModel` aceita `local_files_only`; o teste dedicado garante que a opção permaneça verdadeira.

### Simplificar e re-testar

- A lógica de seleção não duplica a recomendação de T3 e o artefato usa a mesma publicação atômica de T4.
- A suíte específica e a suíte completa permaneceram verdes nos runtimes Python 3.11 e global.
- O fixture real não contém fala reconhecida, por isso o artefato de integração tem zero segmentos; a serialização de palavras e a rejeição de timestamps ausentes são cobertas pelos testes dedicados.
- O lockfile manteve 50 pacotes e hashes pinados; apenas `setuptools` e `wheel` foram atualizados por causa dos achados do SCA.

### Registro

- **Plan:** T5 marcada como concluída com prova real e suíte de regressão verde.
- **Spec comprovada nesta fatia:** A2.
- **Arquivos:** `.gitignore`, `.vibeflow/REGRAS.md`, `.vibeflow/mvp/implement.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`, `requirements.lock`, `src/editor/cli.py`, `src/editor/capabilities.py`, `src/editor/dependencies.py`, `src/editor/workdir.py`, `src/editor/whisper_backend.py`, `src/editor/analysis/transcription.py`, `tests/test_installation.py`, `tests/test_transcription.py`.
- **Commit:** `task(T5): add local transcription` (hash registrado no resultado da execução).

## T6: Extrair frames com manifesto temporal

### Reconhecer

- T4 já centralizava a validação de caminho, source ID, allowlist de mídia, duração, hash da fonte, FFprobe e execução de processos sem shell.
- O mapa de capacidades já expunha `extract_frames`, mas ainda não existiam parser, operação, layout de `frames/<source-id>` ou teste de integração.
- A T6 precisava registrar o frame efetivo porque um timestamp solicitado pode cair entre dois frames ou depois do último frame disponível, sem alterar a fonte nem publicar estado parcial.

### Codar

- `src/editor/cli.py` registra `extract frames`, exige exatamente `--timestamps` ou `--interval-s` e preserva o envelope JSON centralizado.
- `src/editor/analysis/frames.py` valida timestamps, duplicação, intervalo, início/fim, duração e limite de seleção; consulta timestamps de vídeo via FFprobe, extrai PNGs com argumentos estruturados do FFmpeg e cria manifesto canônico com timestamp solicitado, efetivo e caminho relativo.
- `src/editor/media.py` permite que operações visuais exijam vídeo sem exigir áudio, preservando a exigência de áudio para análise e transcrição.
- `src/editor/workdir.py` adiciona o layout de frames e troca a pasta completa por staging somente depois que todos os PNGs e o manifesto estão íntegros, preservando a versão anterior em caso de falha.
- `tests/test_frame_extraction.py` cobre os dois modos, ausência/ambiguidade de seleção, duplicação, timestamp fora da duração, PNGs reais, fonte intacta e falha de FFmpeg sem publicação parcial.
- `.vibeflow/REGRAS.md` foi sincronizado com o novo módulo, contrato, limitação e estratégia de verificação.

### Testar

- `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_frame_extraction.py" -v` -> `Ran 9 tests ... OK`.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` -> `Ran 35 tests ... OK`.
- `.venv\Scripts\python.exe -B -m editor extract frames --workdir A:\Projetos\editor\tmp-t6-run --source-id main --input tests/fixtures/media/main.mp4 --interval-s 5 --json` -> exit code `0`, um PNG e `frames/main/manifest.json` publicados; o workdir foi removido após a prova.

### Simplificar e re-testar

- A primeira execução revelou que `tempfile.mkdtemp()` produzia staging com ACL incompatível com o processo FFmpeg no Windows gerenciado. O staging passou a usar `Path.mkdir()` com nome UUID, mantendo isolamento e validação, e a suíte foi executada novamente com sucesso.
- A publicação usa uma troca de diretório única, por isso uma falha intermediária não deixa manifesto incompleto publicado; o temporário é removido e a versão anterior é preservada.
- A extração mantém a allowlist MP4/H.264 e aceita áudio AAC quando presente; não adiciona codecs, rede ou dependências novas. Análise e transcrição continuam exigindo áudio.

### Registro

- **Plan:** T6 marcada como concluída.
- **Spec comprovada nesta fatia:** A4. A9, A10, A11, C2 e C4 permanecem critérios globais para a revisão do MVP.
- **Arquivos:** `.vibeflow/REGRAS.md`, `src/editor/analysis/frames.py`, `src/editor/cli.py`, `src/editor/media.py`, `src/editor/workdir.py`, `tests/test_frame_extraction.py`.
- **Decisões:** MEDIA-01, DATA-01, PRIV-01 e CONTRACT-01 implementadas para extração de frames.
- **Mensagem de commit:** `task(T6): add temporal frame extraction`

## T7: Validar plano linear com schema estrito

### Reconhecer

- T4 já centralizava workdir, fontes locais, limites de mídia e inspeção FFprobe, mas ainda não havia parser para `plan.json` nem regra de timeline.
- A spec exige allowlist recursiva, fontes de vídeo/áudio compatíveis, intervalos `[início, fim)`, áudio explícito por segmento, b-roll com narração de A, overlays nomeados, perfil 16:9 e nenhum ducking.
- A T7 precisa expor a forma JSON canônica e seu SHA-256 sem criar artefato ou iniciar FFmpeg de renderização.

### Codar

- `src/editor/plan/schema.py` concentra as allowlists, enums e validações estruturais comuns do schema v1.
- `src/editor/plan/validation.py` lê somente `plan.json`, consulta metadados das fontes uma vez por caminho, valida formatos, proporções, cortes, trilhas, ganhos, loops, overlays e processamento de áudio, e calcula a timeline concatenada.
- `src/editor/plan/canonical.py` serializa UTF-8 com chaves ordenadas, sem espaços e sem constantes não finitas, calculando o SHA-256 consumido pelas próximas tasks.
- `src/editor/cli.py` registra `plan validate` com o mesmo envelope JSON da CLI; `src/editor/media.py` passa a expor dimensões e taxa de quadros do vídeo para validação de `fit`.
- `tests/test_plan_validation.py` cobre o cenário real de vlog, hash invariável por formatação, rejeição precoce de campos desconhecidos, regra de b-roll e erros de overlay/JSON.
- `tests/fixtures/workflow/plan.json` e suas fontes MP4/MP3 exercitam corte, junção, b-roll, música, ganhos, normalização, limiter e lettering.

### Testar

- `.venv\Scripts\python.exe -B -m editor plan validate --workdir A:\Projetos\editor\tests\fixtures\workflow --json` -> exit code `0`, três fontes validadas, três segmentos concatenados e duração de `2.0 s`.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_plan_validation.py" -v` -> `Ran 7 tests ... OK`.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` -> `Ran 42 tests ... OK`.

### Simplificar e re-testar

- A validação reutiliza `media.inspect_source`, `resolve_source_path`, `validate_workdir` e os envelopes existentes, sem criar um segundo caminho de segurança ou uma camada de renderização especulativa.
- A rejeição de `NaN`/infinito foi mantida no parser antes da canonicalização; campos desconhecidos falham antes do FFprobe, e fontes originais permanecem somente leitura.
- A suíte específica, a suíte completa e o comando real da task foram executados novamente após a revisão e permaneceram verdes.

### Registro

- **Plan:** T7 marcada como concluída.
- **Spec comprovada nesta fatia:** A5. A6, A10, A11, C2 e C3 permanecem critérios globais ou dependem da renderização e aprovação seguintes.
- **Arquivos:** `.vibeflow/REGRAS.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`, `src/editor/cli.py`, `src/editor/media.py`, `src/editor/plan/__init__.py`, `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/plan/canonical.py`, `tests/test_plan_validation.py`, `tests/fixtures/workflow/plan.json`, `tests/fixtures/workflow/sources/main.mp4`, `tests/fixtures/workflow/sources/broll.mp4`, `tests/fixtures/workflow/sources/music.mp3`.
- **Decisões:** DATA-01, MEDIA-01, AUDIO-01, TEXT-01 e CONTRACT-01 implementadas para validação do plano.
- **Mensagem de commit:** `task(T7): validate linear editing plan`

## T8: Renderizar preview a partir do plano validado

### Reconhecer

- T7 já centralizava a leitura estrita do plano, a inspeção das fontes, a timeline concatenada, os modos de áudio, o `fit`, os overlays e o hash canônico.
- T4 já fornecia a execução estruturada sem shell e as regras de diretório temporário; a publicação da preview precisava de um destino de arquivo separado da publicação das análises.
- A renderização foi mantida em um único grafo FFmpeg para evitar intermediários publicados e preservar a timeline explícita do plano.

### Codar

- `src/editor/plan/validation.py` passou a expor `load_validated_plan`, permitindo renderizar exatamente o documento que acabou de ser validado, sem uma segunda leitura divergente.
- `src/editor/render/ffmpeg_graph.py` cria entradas determinísticas, cortes, `fit` preserve/contain/crop, concatenação linear, áudio por faixa, loop, ganho em dB, `amix` sem normalização automática, `loudnorm`, limiter e drawtext com posições nomeadas e margem segura.
- `src/editor/render/preview.py` escolhe canvas 16:9 de até 720 pixels, verifica encoders locais, executa FFmpeg com lista de argumentos, valida o MP4 temporário via FFprobe e publica `preview.mp4` atomicamente.
- `src/editor/workdir.py` adiciona o layout e a publicação segura da preview, preservando a saída anterior em falha.
- `src/editor/cli.py` registra `render preview` e mantém o envelope JSON centralizado.
- `tests/test_preview_render.py` cobre render real, perfil e codecs, mute sem faixa publicada, áudio de A no b-roll, processamento explícito e preservação em falha.

### Testar

- `python -m editor render preview --workdir A:\Projetos\editor\tests\fixtures\workflow --json` -> exit code `0`, `preview.mp4` H.264/AAC, `320x180` e `2.0 s`.
- `python -m unittest discover -s tests -p "test_preview_render.py" -v` -> `Ran 4 tests ... OK`.
- `python -m unittest discover -s tests -v` -> `Ran 46 tests ... OK`.
- A prova confirmou que a fonte original, o plano e o temporário permanecem íntegros; a simulação de falha do FFmpeg retornou `RENDER_FAILED` e preservou a preview anterior.

### Simplificar e re-testar

- O grafo reutiliza a validação de plano e os metadados já consultados, sem uma segunda implementação das regras de mídia ou de timeline.
- A preview usa arquivo temporário dentro do próprio trabalho, porque a pasta TEMP do host já demonstrou ACL incompatível com processos de mídia em fases anteriores.
- A sondagem de encoders falha com código explícito quando `libx264` ou `aac` não estão disponíveis; filtros de normalização e limiter compensam os limites de faixa do FFmpeg sem relaxar o plano.

### Registro

- **Plan:** T8 marcada como concluída.
- **Spec comprovada nesta fatia:** A6, A7 e A9. A8, A10, A11, C1, C2, C3, C4 e C5 permanecem globais ou dependem das tasks seguintes.
- **Arquivos:** `.vibeflow/REGRAS.md`, `.vibeflow/mvp/implement.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`, `src/editor/cli.py`, `src/editor/plan/validation.py`, `src/editor/render/__init__.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/render/preview.py`, `src/editor/workdir.py`, `tests/test_preview_render.py`.
- **Decisões:** RENDER-01, AUDIO-01, TEXT-01, MEDIA-01 e PRIV-01 implementadas para a preview.
- **Mensagem de commit:** `task(T8): render validated preview`

## T9: Registrar aprovação vinculada a hashes

### Reconhecer

- T7 já expunha o hash canônico do plano e T8 publicava uma única `preview.mp4` atomicamente, mas ainda não havia um estado explícito que vinculasse plano, preview e fontes.
- A aprovação precisa revalidar o plano antes de calcular hashes, exigir uma preview regular já publicada e preservar o `approval.json` anterior se qualquer pré-condição falhar.
- O hashing de fontes foi centralizado em `src/editor/integrity/hashes.py`; `media.sha256_file` passou a reutilizar o mesmo helper para não duplicar a leitura em blocos.

### Codar

- `src/editor/integrity/approval.py` valida o plano, exige `preview.mp4`, calcula o hash canônico já produzido pela validação, calcula o hash da preview e registra as fontes na ordem declarada pelo plano.
- `src/editor/workdir.py` adiciona layout e publicação atômica de `approval.json` dentro de `.tmp`, com rejeição de destinos não regulares ou linkados.
- `src/editor/cli.py` registra `approve --workdir <caminho-absoluto> --json` e mantém o envelope centralizado.
- `tests/test_approval.py` cobre o processo real, o schema, a ordenação dos hashes, a invariância de formatação, mudança semântica e preservação da aprovação vigente em ausência da preview.
- `tests/fixtures/workflow/preview.mp4` e `approval.json` registram um estado completo do cenário usado na prova do comando real.

### Testar

- `python -m editor render preview --workdir A:\Projetos\editor\tests\fixtures\workflow --json` -> exit code `0`, preview H.264/AAC, `320x180` e `2.0 s`.
- `python -m editor approve --workdir A:\Projetos\editor\tests\fixtures\workflow --json` -> exit code `0`, `approval.json` publicado com três fontes ordenadas.
- `python -m unittest discover -s tests -p "test_approval.py" -v` -> `Ran 3 tests in 13.264s ... OK`.
- A primeira tentativa com caminho relativo falhou com `WORKDIR_INVALID`, conforme o contrato de caminho absoluto; a prova foi refeita com o caminho absoluto exigido pela CLI.

### Simplificar e re-testar

- O hash de arquivos reutiliza um único helper em blocos; o hash semântico do plano continua no módulo canônico existente.
- A aprovação só monta o documento depois de concluir todas as validações e hashes, então uma falha anterior à publicação não substitui o registro vigente.
- A suíte específica permaneceu verde após a revisão e o fluxo completo existente continuou verde com `Ran 46 tests ... OK`.

### Registro

- **Plan:** T9 marcada como concluída.
- **Spec comprovada nesta fatia:** a parte de aprovação por hashes de A8, além de C2 e C4 no caminho de aprovação; a validação da full permanece na T10.
- **Arquivos:** `.vibeflow/REGRAS.md`, `.vibeflow/mvp/implement.md`, `.vibeflow/mvp/plan.md`, `src/editor/cli.py`, `src/editor/media.py`, `src/editor/workdir.py`, `src/editor/integrity/__init__.py`, `src/editor/integrity/hashes.py`, `src/editor/integrity/approval.py`, `tests/test_approval.py`, `tests/fixtures/workflow/approval.json`, `tests/fixtures/workflow/preview.mp4`.
- **Decisões:** STATE-01, DATA-01, RENDER-01 e CONTRACT-01 implementadas para a aprovação; `render full` permanece fora da T9.
- **Mensagem de commit:** `task(T9): record hash-bound approval`

## T10: Renderizar full com aprovação vigente

### Reconhecer

- T8 já concentrava o grafo linear, os filtros de áudio e lettering, a validação do MP4 temporário e a publicação atômica da preview.
- T9 já registrava o hash canônico do plano, o hash da preview e os hashes das fontes, mas ainda não existia uma operação que consumisse esse estado como autorização.
- A full precisava reutilizar a mesma timeline e os mesmos filtros, usando a resolução do primeiro vídeo sem o limite de 720 pixels da preview.

### Codar

- `src/editor/integrity/approval.py` passou a validar o schema de `approval.json` e recalcular plano, preview e fontes antes de liberar a full; divergências retornam `APPROVAL_STALE`.
- `src/editor/render/full.py` adiciona `render full`, reutiliza o grafo e os helpers de renderização da preview, valida o MP4 final via FFprobe e publica somente depois do sucesso.
- `src/editor/workdir.py` adiciona o layout e a publicação atômica de `full.mp4`; `src/editor/cli.py` registra o subcomando e mantém o envelope centralizado.
- `tests/test_full_render.py` cobre publicação real, aprovação ausente ou corrompida, mutações isoladas e falha do FFmpeg com preservação da full anterior.
- `.vibeflow/REGRAS.md`, `spec.md` e `plan.md` foram sincronizados com a ferramenta e os critérios comprovados.

### Testar

- `python -m editor render full --workdir A:\Projetos\editor\tests\fixtures\workflow --json` -> exit code `0`, `full.mp4` H.264/AAC, `320x180` e `2.0 s`.
- `python -m unittest discover -s tests -p "test_full_render.py" -v` -> `Ran 5 tests in 33.461s ... OK`.
- `python -m unittest discover -s tests -v` -> `Ran 54 tests in 63.757s ... OK`.

### Simplificar e re-testar

- A verificação de estado foi centralizada em `verify_approval`, evitando cópias de comparação em cada render.
- A full reaproveita o grafo, o comando estruturado, a sondagem de encoders, a validação de mídia e a limpeza de temporários da preview, variando somente o perfil de resolução e o destino.
- O registro inválido, qualquer hash divergente e a falha de FFmpeg são tratados antes da publicação, preservando `plan.json`, `approval.json`, fontes e `full.mp4` vigente.

### Registro

- **Plan:** T10 marcada como concluída.
- **Spec comprovada nesta fatia:** A8, C3 e C4. A9 já estava comprovada na T8; A11, C1 e C5 permanecem critérios globais da jornada E2E.
- **Arquivos:** `.vibeflow/REGRAS.md`, `.vibeflow/mvp/implement.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`, `src/editor/cli.py`, `src/editor/integrity/approval.py`, `src/editor/render/__init__.py`, `src/editor/render/full.py`, `src/editor/render/preview.py`, `src/editor/workdir.py`, `tests/test_full_render.py`, `tests/fixtures/workflow/full.mp4`.
- **Decisões:** STATE-01, RENDER-01 e PRIV-01 implementadas para a full.
- **Mensagem de commit:** `task(T10): render approved full`

## T11: Validar jornada local completa do MVP

### Reconhecer

- T3 já expunha capacidades locais, mas os estados de `plan_validate`, `render_preview`, `approve` e `render_full` ainda diziam `unsupported` mesmo com os comandos implementados nas tasks seguintes.
- T4 a T10 já forneciam os comandos e artefatos isolados; faltava uma prova em processo separado que os encadeasse com as mesmas fontes, o modelo local e o estado aprovado.
- A jornada foi isolada em `tests/fixtures/e2e/` para não usar nem modificar os artefatos versionados de `tests/fixtures/workflow/`. O teste força o backend Whisper a operar offline e usa somente o modelo CTranslate2 `base` local.

### Codar

- `src/editor/capabilities.py` passou a derivar os estados das operações implementadas a partir das dependências reais: `ffprobe` para plano/aprovação e FFmpeg/FFprobe para renders.
- `tests/e2e/test_mvp_flow.py` executa `capabilities`, análise de áudio, transcrição CPU, extração de frames, validação do plano, preview, aprovação e full por subprocessos reais, conferindo envelopes JSON de uma linha, stderr vazio, ausência de prompts e falha explícita quando um pré-requisito não existe.
- A mesma prova confere a existência dos artefatos, a duração/timeline de `2.0 s`, a igualdade do hash do plano entre aprovação e full, hashes das fontes e limpeza de `.tmp`.
- Depois da full aprovada, a fixture de trabalho sofre uma mutação controlada em uma fonte. O novo `render full` precisa retornar `APPROVAL_STALE` e preservar a full e o approval anteriores.
- `tests/fixtures/e2e/plan.json` e as fontes curtas `main.mp4`, `broll.mp4`, `music.mp3` e `speech.mp3.b64` materializam o cenário de vlog com b-roll, música, fala real e lettering `Paraty`.
- `.vibeflow/REGRAS.md` foi sincronizado com a fixture E2E, o teste, a capacidade corrigida, as dependências locais, os limites e a estratégia de verificação.

### Testar

- `.venv\Scripts\python.exe -B -m unittest tests.e2e.test_mvp_flow -v` -> `Ran 1 test ... OK`; todos os oito comandos da jornada publicaram os artefatos esperados, a transcrição real retornou segmentos com words temporizadas e a mutação retornou `APPROVAL_STALE` sem alterar a full vigente.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` -> suíte completa verde após as correções pós-review.
- `gitleaks detect --source . --no-banner` -> exit `0`, `no leaks found`.
- `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip` -> `No known vulnerabilities found`, com acesso direto autorizado à base do PyPI porque o proxy padrão foi recusado.

### Simplificar e re-testar

- A prova reutiliza os subprocessos e os envelopes existentes, sem criar um segundo executor de comandos ou uma camada de fixture compartilhada; o único estado novo é o trabalho temporário do próprio teste e a fixture Base64 mínima de fala.
- A correção de capacidades ficou no ponto de origem, evitando que cada agente precise inferir disponibilidade a partir de mensagens de erro posteriores.
- A revisão do plano marcou como concluídos os aceites de T4 e T9 que já tinham prova verde documentada, e as correções pós-review fecharam os limites, alias, readiness de CUDA, parser estrito e watchdog sem duplicar regras.
- A suíte E2E e a suíte completa foram executadas novamente após a revisão, mantendo a jornada real de transcrição com fala e todos os testes verdes.

### Registro

- **Plan:** T11 marcada como concluída.
- **Spec comprovada nesta fatia:** A1 a A12 e C1 a C5 pela jornada integrada e pelos testes de hardening, incluindo stdout JSON, processamento local, rejeição de entradas fora dos limites, preservação de fontes, timeline aprovada e bloqueio de estado obsoleto.
- **Arquivos:** `src/editor/capabilities.py`, `tests/e2e/test_mvp_flow.py`, `tests/fixtures/e2e/`, `.vibeflow/REGRAS.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/implement.md`.
- **Decisões:** PROD-01, SCOPE-01, OPS-01, PRIV-01 e CONTRACT-01 comprovadas no fluxo completo.
- **Mensagem de commit:** `task(T11): validate local mvp journey`

## Correções pós-review R1-R9 e N1-N2

### Reconhecer

- A primeira review encontrou uma falha crítica de alias entre fontes e destinos, limites numéricos e operacionais incompletos, readiness de encoder/CUDA insuficiente, E2E permissivo, documentação divergente, watchdog bloqueável e dois nits de contrato.
- A fila de T* estava vazia; a correção foi tratada como uma fatia de implementação derivada diretamente dos findings, sem criar uma task especulativa.

### Codar

- `src/editor/workdir.py`, `render/preview.py` e `render/full.py` passaram a rejeitar alias físico entre fontes e destinos antes do FFmpeg, preservando entradas e saídas vigentes.
- `src/editor/analysis/audio.py` e `src/editor/plan/validation.py` passaram a limitar magnitude numérica, janelas, segmentos, overlays e duração total antes do processamento pesado; os limites também aparecem em `capabilities`.
- `src/editor/dependencies.py`, `capabilities.py`, `whisper_backend.py` e `scripts/install.ps1` passaram a compartilhar a validação do runtime CUDA pelo CTranslate2, além da sondagem central de encoders.
- `src/editor/process.py` passou a consumir PCM com watchdog independente da leitura bloqueante de stdout.
- `contracts.py`, validação de plano e aprovação passaram a rejeitar chaves JSON duplicadas; o filtro de lettering passou a respeitar `[início, fim)`.
- `scripts/install.ps1` passou a aceitar um venv Python 3.11 x64 já validado como runtime operacional durante `-Verify`, sem exigir um launcher base adicional quando o venv está íntegro.
- `tests/e2e/test_mvp_flow.py` deixou de pular por dependência ausente e passou a exigir uma fixture local de fala, segmentos e palavras temporizadas no backend real.

### Testar

- Os testes focados de áudio, plano, capacidades, preview, full, transcrição e processo passaram.
- A jornada E2E passou com fala real, oito comandos, envelopes JSON de uma linha, stderr vazio, artifacts publicados, hashes preservados e bloqueio `APPROVAL_STALE` após mutação.
- `scripts/install.ps1 -Verify` passou no venv operacional Python 3.11.15 x64; `pip check`, gitleaks e pip-audit passaram.

### Simplificar e re-testar

- As correções reutilizam `workdir`, `dependencies` e os envelopes existentes; não foram adicionadas bibliotecas, serviços, modos de compatibilidade ou abstrações especulativas.
- `REGRAS.md` não foi alterado nesta fatia, pois não houve decisão de vigência nova. A sincronização de decisões continua condicionada à aprovação humana explícita da review.

### Registro

- **Findings tratados:** R1, R2, R3, R4, R5, R6, R7, R8, R9, N1 e N2.
- **Artefatos atualizados:** `spec.md`, `plan.md` e este `implement.md`; `review.md` será atualizado pela nova revisão.

## Correções pós-review R10-R12

### Reconhecer

- A etapa 3 da review abriu R10 por validação incompleta de ancestrais reparse no instalador, R11 por NUL aceito em dados de overlay e R12 por arredondamento que podia ultrapassar o limite de janelas de áudio após o início do streaming.
- A análise estava aprovada e a fila de T* vazia; os três findings eram a única fatia elegível e foram tratados diretamente, sem criar task especulativa.

### Codar

- `scripts/install.ps1` ganhou `Test-RegularPathChain`, que verifica todos os componentes existentes até `RepoRoot`; `Resolve-RepoPath`, `Resolve-LocalPath`, o venv, os modelos e os destinos fixos `.tools` passaram a reutilizar essa guarda antes de criar ou copiar arquivos.
- `src/editor/plan/validation.py` passou a rejeitar NUL em `text` e `font` de overlays, preservando o envelope `PLAN_INVALID` antes do filtro FFmpeg.
- `src/editor/analysis/audio.py` centralizou a quantização da janela em amostras e calcula a quantidade efetiva de janelas antes do processamento.
- `tests/test_installation.py`, `tests/test_plan_validation.py` e `tests/test_audio_analysis.py` ganharam regressões para os três casos de fronteira.

### Testar

- `.venv\Scripts\python.exe -B -m unittest tests.test_installation tests.test_plan_validation tests.test_audio_analysis -v` -> `Ran 26 tests ... OK`.
- `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` -> `Ran 69 tests ... OK`, sem testes pulados no host atual.
- `scripts/install.ps1 -Verify` -> Python 3.11.15 x64, lockfile, backend, modelo, FFmpeg, gitleaks e `pip check` disponíveis; CUDA ausente reportada como opcional.
- `gitleaks detect --source . --no-banner` -> exit `0`, nenhum segredo encontrado.
- `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip` -> `No known vulnerabilities found`.
- `git diff --check` -> sem erros de whitespace; os avisos restantes são apenas normalização LF/CRLF do Git.

### Simplificar e re-testar

- A validação física foi centralizada em um helper, evitando três implementações independentes para venv, modelos e ferramentas locais.
- A quantização de áudio é compartilhada entre a validação e o acumulador, eliminando divergência no cálculo do limite.
- A rejeição de NUL ficou na borda do plano, sem alterar o escape de filtros ou adicionar uma camada de compatibilidade.

### Registro

- **Findings tratados:** R10, R11 e R12.
- **Spec comprovada nesta fatia:** A3, A5, A7, A10, A12 e C2, com regressões focadas e suíte completa verde.
- **Arquivos:** `scripts/install.ps1`, `src/editor/analysis/audio.py`, `src/editor/plan/validation.py`, `tests/test_audio_analysis.py`, `tests/test_installation.py`, `tests/test_plan_validation.py`, `.vibeflow/mvp/spec.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/review.md` e este `implement.md`.
- **Decisões:** nenhuma decisão de produto nova; R10-R12 apenas fecham hardening já previsto em A3, A5, A7, A10, A12 e C2.
- **Mensagens de commit:** `task(R10): protect installer path chain`, `task(R11): reject nul overlay input` e `task(R12): quantize audio window limits`.

## Handoff

Fila após T11: nenhuma T elegível e nenhum R* aberto. Handoff para `vibe-review`.
