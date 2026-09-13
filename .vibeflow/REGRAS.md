# Regras do projeto

<!-- VIBEFLOW:CADEIA start -->
| esforço | fluxo | quando |
|---|---|---|
| — | init | primeira vez no repo, ou disco quebrado, de novo só para reparar |
| low | implement | pedido claro, direto, simples (cor, texto, documento, landing page / página de captura estática) |
| medium | implement → review | pedido claro e direto sem regressão de backend, ou página visual com validação chrome-devtools |
| high | spec → plan → implement → review | pedido claro, execução difícil, ou sistema com auth, painel admin, banco ou regressão |
| xhigh | interview → spec → plan → implement → review | pedido ambíguo, confiança baixa, intenção ou sucesso de software em aberto |
| max | interview → spec → plan → analyze → implement → review | pedido toca auth, pagamento, segredo, perda de dados, produção, alto blast radius ou baseline MVP de software |
<!-- VIBEFLOW:CADEIA end -->

## Projeto
O projeto ainda não possui nome definido. Ele será um editor de vídeo composto por ferramentas acessíveis a agentes de IA por CLI, como Codex, Grok Build e similares. O foco inicial é criar um núcleo de ferramentas que possa ser operado de forma previsível por esses agentes.
<!-- evidência: contexto fornecido pelo usuário; o diretório técnico atual é `editor` -->

## Ambiente
**MVP.** O núcleo inicial é uma CLI Python local, instalável em modo editável, com dependências pinadas e provisionamento explícito no Windows. O objetivo desta etapa é validar o contrato essencial com integridade básica, sem tratar o projeto como homologação ou produção.
<!-- evidência: pyproject.toml e src/editor criados na T1; requirements.lock e scripts/install.ps1 definidos na T2 em 2026-09-10 -->

## Versão (semver)
- **Major:** quebra contrato (API, schema, comportamento que caller já usa).
- **Minor:** adiciona sem quebrar.
- **Patch:** correção sem mudança de contrato.
- Produção: migration só com plano de rollback; não rodar migration destrutiva sem o humano pedir.

## Git
- Sem `Co-Authored-By` de ferramenta em commit/push.

## Padrões de Código e Engenharia
- **Comentários Semânticos Obrigatórios:** Toda função, componente ou módulo deve ter comentário explicando o que faz, para que serve e a justificativa de decisões não óbvias. Zero funções órfãs.
- **Desenvolvimento Eficiente (YAGNI):** O melhor código é o mínimo necessário. Antes de escrever código novo, reutilizar o que já existe no projeto, na standard library ou recursos nativos da plataforma. Não criar abstrações para necessidades especulativas futuras.
- **Causa Raiz:** Correção de bugs deve atuar na causa raiz e não no sintoma. Se o problema estiver em função compartilhada, corrigir no ponto de origem.
- **Limitações Conhecidas:** Registrar limitações de soluções simplificadas com `ponytail: <limitação>, <quando será necessário melhorar>`.
- **Erros Fora do Escopo:** Nunca ignorar em silêncio. Corrigir se for rápido e seguro, ou documentar na pasta `.erros-encontrados/` se for um problema maior.
- **Comunicação:** Ao explicar código, priorizar o que está acontecendo, o porquê e o impacto arquitetural, sem exigir domínio de sintaxe da linguagem.

## Segurança por Padrão
- **Entradas Não Confiáveis:** Toda informação recebida de fora (request, query, headers, forms) deve ser validada na borda com allowlist e limite de tamanho. SQL sempre parametrizado.
- **Credenciais e Segredos:** Chaves, tokens e segredos nunca ficam no código ou repositório. Usar variáveis de ambiente (`.env`).
- **Autorização no Servidor:** Toda ação sensível deve validar permissões no servidor (proteção anti-IDOR).
- **Tratamento de Erros na Borda:** Respostas externas (APIs, webhooks, CLIs) devem retornar envelope padronizado com código estável em maiúsculas (`code`), mensagem segura (`message`) e status HTTP/CLI. Não expor stack traces, queries ou dados internos do banco.
- **Erros no Núcleo Interno:** Lógica interna e serviços devem lançar exceções de domínio tipadas. A conversão da exceção para o código público de erro é responsabilidade exclusiva do middleware/handler central na borda.

## Estrutura

- `pyproject.toml`: metadados do pacote Python e entry point `editor`.
- `requirements.lock`: resolução pinada para Python 3.11 x64 no Windows, com hashes dos artefatos Python.
- `scripts/install.ps1`: verificação e instalação explícita do venv, dependências, ferramentas locais, modelos e CUDA opcional.
- `src/editor/`: pacote da CLI.
  - `__main__.py`: execução por `python -m editor`.
  - `cli.py`: parser, ajuda, despacho inicial e conversão central de erros.
  - `contracts.py`: schema de envelopes, exit codes e erro de domínio público.
- `src/editor/capabilities.py`: contrato de inventário local, limites, operações e recomendação.
- `src/editor/dependencies.py`: sondagem segura de ferramentas, hardware, backend e modelos locais.
- `src/editor/workdir.py`: validação do trabalho, source IDs, diretórios permitidos e publicação atômica.
- `src/editor/media.py`: inspeção FFprobe e allowlist de áudio, vídeo e imagens raster estáticas, com limites de tamanho, duração e dimensão.
- `src/editor/process.py`: execução estruturada e streaming PCM de processos locais sem shell intermediário.
- `src/editor/analysis/audio.py`: análise de loudness, pico verdadeiro, níveis por janela e silêncio por amplitude.
- `src/editor/analysis/frames.py`: seleção temporal, extração de PNGs e manifesto de frames efetivos.
- `src/editor/analysis/transcription.py`: validação da resposta do Whisper e publicação de transcrição com timestamps por palavra.
- `src/editor/plan/schema.py`: allowlist estrutural e opções suportadas do schema v2 do plano.
- `src/editor/plan/validation.py`: validação de fontes, segmentos, áudio, movimento, cortes J/L, camadas e timeline linear.
- `src/editor/plan/canonical.py`: serialização UTF-8 canônica e SHA-256 do plano.
- `src/editor/integrity/hashes.py`: hashing em blocos de artefatos locais para registros de integridade.
- `src/editor/integrity/approval.py`: validação do plano, cálculo de hashes e publicação atômica de `approval.json`.
- `src/editor/render/ffmpeg_graph.py`: composição determinística de vídeo, áudio, texto e camadas raster compartilhada pela preview e full.
- `src/editor/render/preview.py`: renderização, validação de metadados e publicação atômica da única `preview.mp4`.
- `src/editor/render/full.py`: revalidação da aprovação e renderização final da única `full.mp4`.
- `src/editor/whisper_backend.py`: seleção local de modelo/dispositivo e ponte lazy com faster-whisper sem download.
- `tests/test_audio_analysis.py`: processo real da CLI, validação precoce e preservação em falha de FFmpeg.
- `tests/test_frame_extraction.py`: seleção explícita/regular, timestamps efetivos, PNGs e preservação em falha de FFmpeg.
- `tests/test_plan_validation.py`: schema v2 estrito, movimento, timeline do cenário de vlog, regras de áudio, cortes, camadas e hash canônico.
- `tests/test_preview_render.py`: composição real da preview, áudio explícito do b-roll, perfil, mute e preservação em falha.
- `tests/test_approval.py`: aprovação real por hashes, ordenação das fontes, invariância de formatação e preservação do estado vigente.
- `tests/test_full_render.py`: full real com aprovação vigente, bloqueio por mutação, aprovação inválida e preservação em falha de FFmpeg.
- `tests/test_transcription.py`: contrato canônico, seleção CPU/CUDA, timestamps obrigatórios e erros seguros da transcrição.
- `tests/fixtures/media/main.mp4`: fixture curta H.264/AAC com trechos silenciosos para o contrato de áudio.
- `tests/fixtures/workflow/`: plan.json v2, fontes locais, preview, aprovação e full do cenário de validação do MVP.
- `tests/test_cli_contract.py`: smoke test dos dois pontos de entrada e dos envelopes JSON.
- `tests/test_installation.py`: testes do lockfile, do modo somente leitura, dos gates e da cópia de modelos locais.
- `tests/test_capabilities.py`: teste de processo do inventário v2, limites, operação init, documentação e recomendação determinística.
- `tests/e2e/test_mvp_flow.py`: jornada E2E dos comandos locais, artefatos publicados, saída JSON e bloqueio por aprovação obsoleta.
- `tests/fixtures/e2e/`: plano curto de vlog, b-roll, música e lettering usado pela prova ponta a ponta.
- `tests/fixtures/phase-1/images/`: raster estático, alpha e animações rejeitadas na prova de camadas da Phase 1.
- `.vibeflow/`: fonte de governança e Documento Vivo de regras.
- `.vibeflow/mvp/`: entrevista, spec, plan, analyze e implement do MVP.
- `.vibeflow/phases/`: artefatos de entrevista, especificação, plano, análise, implementação e revisão por fase.
- `.gitignore`: proteção mínima para segredos locais e backups gerados.
- `AGENTS.md`: ponteiro para `.vibeflow/REGRAS.md`, usado por agentes compatíveis com AGENTS.
- `CLAUDE.md`: ponteiro para `.vibeflow/REGRAS.md`, usado por agentes compatíveis com CLAUDE.
- `.erros-encontrados/`: registros de problemas fora do escopo encontrados durante as verificações.
- `skills/vibe-editor/`: skill que ensina agentes a conduzir o fluxo de edição de vídeo local, incluindo verificação do ambiente, análise, preview, aprovação e full.
- Pontos de entrada da aplicação: `python -m editor` e o comando instalado `editor`.
- Stack técnica: Python 3.11 x64, setuptools como backend de empacotamento, `faster-whisper==1.2.1`, `ctranslate2==4.8.0`, FFmpeg local, `pip-audit==2.10.1` e gitleaks 8.30.1. A lista transitiva e os hashes ficam em `requirements.lock`.
<!-- evidência: MVP implementado até a jornada E2E em 2026-09-12; Phase 1 T1 a T8 implementadas e T9 de capacidades/documentação verificada em 2026-09-13. -->

## Regras deste repo
- O nome do produto permanece em aberto. Usar `editor` apenas como identificador técnico do diretório até que o nome seja decidido.
- A primeira implementação deve definir o contrato mínimo da CLI, incluindo ferramenta inicial, entradas aceitas, saídas, códigos de erro, limites e forma de processamento de arquivos de vídeo.
- Ferramentas destinadas a agentes devem ter comportamento determinístico, mensagens de erro seguras e um formato de saída estável. Preferir resultado estruturado em `stdout` e diagnósticos em `stderr`, para não misturar dados de máquina com logs humanos.
- Argumentos, stdin, headers, arquivos e URLs recebidos pela CLI são não confiáveis. Validar allowlists, tamanho, formato, diretórios permitidos, timeouts e limites de processamento antes de executar operações de mídia.
- Qualquer processo externo deve ser executado com argumentos estruturados, sem shell intermediário e sem comandos montados a partir de entrada externa.
- O instalador só cria ou atualiza o ambiente quando uma opção `-Install*` é informada; `-Verify` é somente leitura e não baixa componentes.
- Dependências Python são instaladas somente de `requirements.lock` com hashes obrigatórios; FFmpeg, gitleaks, CUDA e modelos devem ser fornecidos localmente por ação explícita.
- A stack, a organização dos módulos e os pontos de entrada reais devem ser registrados neste arquivo assim que a primeira implementação for criada. Não antecipar framework, linguagem ou serviço sem necessidade concreta.
- Ao adicionar uma nova ferramenta, registrar seu propósito, contrato de entrada e saída, dependências, limitações conhecidas e estratégia mínima de verificação.
- Sempre que uma fase adicionar módulos, serviços ou convenções, atualizar o mapa de estrutura e estas regras para manter o Documento Vivo sincronizado.

## Ferramentas implementadas

### CLI `editor`, T1

- **Propósito:** disponibilizar a fundação local e não interativa para os subcomandos de análise e edição linear.
- **Entrada:** argumentos de linha de comando; `--help`, `--version` e `--json` estão disponíveis nesta fatia.
- **Saída:** ajuda ou versão para uso humano; sucesso e erro JSON versionados no `stdout` quando `--json` é informado, sem stack trace.
- **Dependências:** Python >= 3.11 e biblioteca padrão; não faz rede nem executa processos externos.
- **Limitação conhecida:** os subcomandos de mídia ainda não existem; `ponytail: adicionar cada operação somente na task correspondente, mantendo a fundação sem dispatcher especulativo`.
- **Verificação:** `python -m pip install --no-deps -e .`, `python -m editor --help`, `editor --help` e `python -m unittest discover -s tests -p "test_cli_contract.py" -v`.

### Instalador local, T2

- **Propósito:** provisionar e verificar o ambiente local reproduzível necessário para análise e edição, sem download implícito durante a operação.
- **Entrada:** `-Verify`, `-Install`, `-InstallFfmpeg`, `-InstallGitleaks`, `-InstallModels`, `-InstallCuda`, caminhos locais correspondentes, `-SecurityGates`, `-VenvPath`, `-ModelDir` e `-PythonPath`.
- **Saída:** diagnóstico humano por item, exit code não zero quando um requisito obrigatório está ausente ou divergente, e nenhum estado de gate aprovado quando pip-audit ou gitleaks não estão disponíveis.
- **Dependências:** Python 3.11 x64, `requirements.lock`, PowerShell 7, FFmpeg/ffprobe e gitleaks 8.30.1 quando instalados; CUDA é opcional; os modelos Whisper devem ser diretórios CTranslate2 locais.
- **Limitação conhecida:** o instalador não baixa FFmpeg, CUDA, gitleaks ou pesos Whisper e exige suas origens locais; `ponytail: usar um catálogo assinado de artefatos quando o provisionamento precisar ser automatizado fora da máquina local`.
- **Verificação:** `pwsh -NoProfile -File scripts/install.ps1 -Verify`, `python -m pip check`, `gitleaks detect --source . --no-banner`, `python -m pip_audit -r requirements.lock` e `python -m unittest discover -s tests -p "test_installation.py" -v`.

### Preparação segura de trabalhos, Phase 1 T1

- **Propósito:** criar ou completar a estrutura técnica de um trabalho sem selecionar fontes ou criar estado editorial.
- **Entrada:** `editor init --workdir <caminho-absoluto> --json`; o diretório final e seus componentes devem ser regulares, seguros e graváveis.
- **Saída:** envelope JSON com `command: "init"`, `state: "ready"`, listas `created` e `existing` e artefatos preservados; a estrutura contém `.tmp`, `analysis/audio`, `analysis/transcription` e `frames`.
- **Dependências:** biblioteca padrão; não faz rede, não copia fontes e não cria `plan.json`, `preview.mp4`, `approval.json` ou `full.mp4`.
- **Limitação conhecida:** a criação aceita somente o último componente inexistente e rejeita links, junctions, reparse points e destinos inseguros; `ponytail: suportar provisionamento de árvores novas mais profundas somente quando houver necessidade operacional real`.
- **Verificação:** `.venv\Scripts\python.exe -B -m unittest tests.test_init tests.test_cli_contract -v`.

### Capacidades locais, T3

- **Propósito:** expor o inventário local consumível por agentes antes de iniciar análise ou edição, incluindo o contrato editorial da Phase 1.
- **Entrada:** `editor capabilities --json`; o comando também preserva a saída estruturada quando `--json` é omitido.
- **Saída:** envelope JSON com sistema, CPU, memória, GPU/VRAM, FFmpeg/FFprobe, backend Whisper, modelos locais, dispositivos, formatos de vídeo, áudio e imagem, plano schema v2, limites, operações incluindo `init` e recomendação.
- **Dependências:** biblioteca padrão; FFmpeg/FFprobe, `nvidia-smi`, faster-whisper, CTranslate2 e modelos são sondados somente quando fornecidos localmente.
- **Limitação conhecida:** GPU é detectada via `nvidia-smi` e a recomendação só usa modelos `tiny`, `base` ou `small` com os arquivos CTranslate2 mínimos; movimento usa interpolação linear, cortes J/L têm duração máxima de 30 segundos, e imagens aceitas são PNG, JPEG/JPG e WebP estáticos até 100 MiB e 8.192 pixels por dimensão; `ponytail: adicionar adaptadores para outros fornecedores de GPU ou catálogos de modelos somente quando houver necessidade real`.
- **Verificação:** `python -m editor capabilities --json` e `python -m unittest discover -s tests -p "test_capabilities.py" -v`.

### Análise de áudio, T4

- **Propósito:** analisar uma fonte MP4, MP3 ou WAV local antes da edição, produzindo um artefato canônico com loudness, pico verdadeiro, níveis por janela e silêncio detectado somente por amplitude.
- **Entrada:** `editor analyze audio --workdir <caminho-absoluto> --source-id <id> --input <arquivo> [--window-s <segundos>] --json`.
- **Saída:** envelope JSON em `stdout` e `analysis/audio/<source-id>.json` publicado somente após o processamento completo. O artefato registra SHA-256, metadados validados, métricas e intervalos de silêncio com limiar de `-45 dBFS` e duração mínima de `0,3 s`.
- **Dependências:** Python 3.11 x64, FFmpeg e FFprobe locais; a análise decodifica PCM em streaming e não modifica fontes ou usa rede.
- **Limitação conhecida:** a análise usa o primeiro stream de áudio e exige os codecs do contrato, MP4/AAC com vídeo H.264 opcional, MP3 e WAV PCM S16LE; `ponytail: ampliar a allowlist somente quando uma fonte real do MVP exigir outro codec ou múltiplos streams`.
- **Verificação:** `python -m editor analyze audio --workdir <caminho-absoluto> --source-id main --input tests/fixtures/media/main.mp4 --json` e `python -m unittest discover -s tests -p "test_audio_analysis.py" -v`.

### Transcrição local, T5

- **Propósito:** transcrever uma fonte MP4, MP3 ou WAV usando somente um modelo CTranslate2 local, preservando idioma, texto, segmentos, timestamps por palavra e hash da fonte.
- **Entrada:** `editor analyze transcribe --workdir <caminho-absoluto> --source-id <id> --input <arquivo> [--language auto|<codigo>] [--model <nome-ou-diretorio-local>] [--device auto|cpu|cuda] --json`.
- **Saída:** envelope JSON em `stdout` e `analysis/transcription/<source-id>.json` publicado atomicamente somente depois da transcrição completa. Timestamps ausentes ou inválidos falham sem substituir a análise vigente.
- **Dependências:** Python 3.11 x64, FFprobe, `faster-whisper==1.2.1`, `ctranslate2==4.8.0` e modelo CTranslate2 local. CPU usa `int8`; CUDA usa `float16` somente quando detectada.
- **Limitação conhecida:** CPU `int8` é o baseline verificado; CUDA usa `float16` somente quando detectada e a allowlist de modelos permanece em `tiny`, `base` e `small`; `ponytail: adicionar adaptadores para outros fornecedores de GPU ou catálogos de modelos somente quando uma fonte real do MVP exigir isso`.
- **Verificação:** `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_transcription.py" -v`, `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` e o comando real com modelo `base` local, que publicou o artefato canônico com exit code `0`.

### Extração de frames, T6

- **Propósito:** extrair evidências visuais de uma fonte MP4 compatível e associar cada PNG ao timestamp solicitado e ao frame efetivo disponível.
- **Entrada:** `editor extract frames --workdir <caminho-absoluto> --source-id <id> --input <arquivo> (--timestamps <t1,t2,...> | --interval-s <segundos> [--start-s <segundos>] [--end-s <segundos>]) --json`.
- **Saída:** envelope JSON em `stdout` e `frames/<source-id>/manifest.json`, com PNGs `frame-0001.png` numerados deterministicamente, SHA-256 da fonte e timestamps solicitado/efetivo. A pasta inteira só é publicada após todos os frames e o manifesto estarem completos.
- **Dependências:** Python 3.11 x64, FFmpeg e FFprobe locais; processamento sem rede, sem shell intermediário e sem alteração da fonte original.
- **Limitação conhecida:** a operação reutiliza a allowlist de mídia e exige MP4/H.264, aceitando no máximo um stream AAC quando houver áudio; o timestamp efetivo é o primeiro frame disponível a partir do solicitado, ou o último frame quando o pedido fica entre o último frame e a duração reportada; `ponytail: ampliar a allowlist de vídeo e otimizar a extração em lote somente quando uma fonte real do MVP exigir outro codec ou volume maior`.
- **Verificação:** `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_frame_extraction.py" -v`, `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` e `python -m editor extract frames --workdir <caminho-absoluto> --source-id main --input tests/fixtures/media/main.mp4 --interval-s 5 --json`.

### Validação de plano linear, T7

- **Propósito:** validar o único `plan.json` do trabalho antes de qualquer renderização, garantindo schema v2 estrito, metadados de mídia, timeline linear, movimento, áudio explícito, cortes J/L, camadas e perfil 16:9.
- **Entrada:** `editor plan validate --workdir <caminho-absoluto> --json`; fontes relativas são resolvidas no trabalho e fontes absolutas locais continuam permitidas como originais de entrada.
- **Saída:** envelope JSON em `stdout` com metadados das fontes, timeline concatenada, estados de movimento, processamento de áudio, `cut_count`, `layer_count`, JSON canônico e SHA-256; nenhum arquivo de saída é criado ou renderizado.
- **Dependências:** Python 3.11 x64, biblioteca padrão e FFprobe local para validar contêiner, codecs, duração e proporção.
- **Limitação conhecida:** a timeline é linear, sem transições, easing, keyframes arbitrários ou decisão editorial automática; `ponytail: ampliar o schema somente quando uma operação real do MVP exigir composição fora da timeline explícita`.
- **Verificação:** `.venv\Scripts\python.exe -B -m editor plan validate --workdir <caminho-absoluto> --json`, `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_plan_validation.py" -v` e `.venv\Scripts\python.exe -B -m unittest discover -s tests -v`.

### Renderização de preview, T8

- **Propósito:** transformar um plano v2 validado em uma única preview linear com cortes, junções, movimento, b-roll, áudio explícito, processamento e camadas de texto, imagem e vídeo.
- **Entrada:** `editor render preview --workdir <caminho-absoluto> --json`, usando somente o `plan.json` e as fontes locais declaradas.
- **Saída:** envelope JSON em `stdout` e `<workdir>/preview.mp4`, publicado somente depois de o arquivo temporário ser validado como MP4 H.264/AAC, 16:9, até 720 pixels e com a duração da timeline.
- **Dependências:** Python 3.11 x64, FFmpeg/FFprobe locais com encoders `libx264` e `aac`; nenhum processo usa shell intermediário ou rede.
- **Limitação conhecida:** a preview usa um único canvas 16:9 derivado da largura do primeiro vídeo e codificação H.264 `libx264` com preset `veryfast`; camadas de vídeo não têm loop, freeze, áudio implícito ou alteração de velocidade; `ponytail: permitir seleção de encoder ou perfil de qualidade somente quando o MVP precisar operar em hosts sem libx264 ou expor presets`.
- **Verificação:** `python -m editor render preview --workdir <caminho-absoluto> --json` e `python -m unittest discover -s tests -p "test_preview_render.py" -v`.

### Aprovação por hashes, T9

- **Propósito:** registrar a aprovação explícita do plano e da preview vigente, vinculando também todas as fontes referenciadas por SHA-256.
- **Entrada:** `editor approve --workdir <caminho-absoluto> --json`; o comando valida `plan.json` e exige `preview.mp4` regular já publicada.
- **Saída:** envelope JSON em `stdout` e `<workdir>/approval.json`, contendo `schema_version`, instante UTC, hash canônico do plano, hash da preview e lista ordenada de hashes das fontes. O registro é publicado por arquivo temporário dentro de `.tmp` e substituição atômica.
- **Dependências:** biblioteca padrão, FFprobe para a validação do plano e fontes locais já declaradas; não faz rede nem altera originais.
- **Limitação conhecida:** a CLI registra a aprovação fornecida pelo operador e não decide se a preview foi editorialmente aceita; `ponytail: exigir assinatura ou identidade de aprovação somente quando o MVP tiver autenticação ou múltiplos operadores`.
- **Verificação:** `python -m editor approve --workdir <caminho-absoluto> --json` e `python -m unittest discover -s tests -p "test_approval.py" -v`.

### Renderização final com aprovação, T10

- **Propósito:** transformar a timeline aprovada em uma única full linear, sem reinterpretar o plano ou o conteúdo.
- **Entrada:** `editor render full --workdir <caminho-absoluto> --json`; exige `plan.json`, `preview.mp4`, `approval.json` e todas as fontes locais declaradas.
- **Saída:** envelope JSON em `stdout` e `<workdir>/full.mp4`, publicado somente depois de a aprovação, o plano, a preview e as fontes coincidirem por SHA-256 e o MP4 final passar pela inspeção de mídia.
- **Dependências:** biblioteca padrão, FFmpeg/FFprobe locais com encoders `libx264` e `aac`; usa a mesma timeline, grafo de filtros e processamento de áudio da preview, sem rede.
- **Limitação conhecida:** a full usa a largura do primeiro vídeo e canvas 16:9, com codificação H.264 `libx264` e preset `veryfast`; `ponytail: permitir seleção de encoder ou perfil de qualidade somente quando o MVP precisar operar em hosts sem libx264 ou expor presets`.
- **Verificação:** `python -m editor render full --workdir <caminho-absoluto> --json` e `python -m unittest discover -s tests -p "test_full_render.py" -v`.

### Jornada E2E local do MVP, T11

- **Propósito:** comprovar a jornada completa de inventário, análise, plano, preview, aprovação e full usando somente fontes e modelo locais.
- **Entrada:** `python -m unittest tests.e2e.test_mvp_flow -v`; a prova usa `tests/fixtures/e2e/` e um diretório de trabalho descartável dentro do workspace.
- **Saída:** artefatos de áudio, transcrição, frames, preview, aprovação e full; a full é comparada à timeline e aos hashes da aprovação vigente.
- **Dependências:** Python 3.11 x64, FFmpeg/FFprobe com `libx264` e `aac`, `faster-whisper==1.2.1`, `ctranslate2==4.8.0` e modelo `base` CTranslate2 local. O backend é executado em modo offline e não baixa pesos.
- **Limitação conhecida:** a fixture é curta e usa o cenário linear atual, sem testar transições, múltiplos operadores ou fontes fora da allowlist; `ponytail: ampliar a jornada somente quando o schema ou uma operação real do MVP ganhar esses comportamentos`.
- **Verificação:** `.venv\Scripts\python.exe -B -m unittest tests.e2e.test_mvp_flow -v`, `gitleaks detect --source . --no-banner` e `.venv\Scripts\python.exe -B -m pip_audit -r requirements.lock --no-deps --disable-pip`.

### Skill de agente `vibe-editor`

- **Propósito:** ensinar agentes compatíveis a reconhecer pedidos de edição de vídeo e conduzir o trabalho do briefing até a `full.mp4` aprovada, usando a CLI local; quando o destino é YouTube, aplica boas práticas de edição.
- **Entrada:** pedido do usuário para editar, montar, cortar, juntar, sonorizar, legendar ou exportar vídeo com fontes locais.
- **Saída:** execução guiada de `capabilities`, `init`, análises, plano schema v2, `preview.mp4`, aprovação explícita e `full.mp4`, sem upload ou fallback remoto.
- **Dependências:** o ambiente local descrito em `skills/vibe-editor/references/installation.md` e as práticas de YouTube em `skills/vibe-editor/references/youtube-editing.md`.
- **Limitação conhecida:** a skill ainda não produz legendas sincronizadas, easing, keyframes arbitrários, rotação, tracking ou transições; `ponytail: adicionar essas capacidades somente quando uma necessidade editorial real exigir contrato e verificação próprios`.
- **Verificação:** `python -X utf8 C:\Users\marco\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\vibe-editor` e revisão dos comandos documentados contra `scripts/install.ps1` e `src/editor/cli.py`.

### Contrato editorial v2 da Phase 1

- **Capacidades:** `capabilities --json` informa o plano schema v2, a operação `init`, fontes de vídeo, áudio e imagem, camadas `text`, `image` e `video`, cortes `j` e `l`, movimento linear e formatos raster PNG, JPEG/JPG e WebP.
- **Limites:** o inventário expõe até 1.000 segmentos, 1.000 camadas, 999 cortes, 30 segundos por corte, `zoom` entre `0.5` e `4.0`, imagens de até 100 MiB e 8.192 pixels por dimensão, além dos limites de mídia já vigentes.
- **Fluxo:** o agente executa `init` em um trabalho novo, fecha o briefing, escreve e valida `plan.json`, gera a preview, obtém aprovação humana explícita e somente então chama `render full`.
- **Segurança:** `init` é idempotente e não cria estado editorial; fontes e artefatos vigentes são preservados, e a aprovação por hash continua obrigatória para a full.
- **Verificação:** `.venv\Scripts\python.exe -B -m editor capabilities --json`, `.venv\Scripts\python.exe -B -m unittest tests.test_capabilities -v` e o `quick_validate.py` da skill.

### Camadas raster estáticas, Phase 1 T6

- **Propósito:** compor PNG com alpha, JPEG/JPG e WebP estáticos no plano v2, respeitando caixa, intervalo, `contain`/`crop` e a ordem de `layers[]`.
- **Entrada:** fonte local `kind: "image"` e camada `type: "image"` com `source_id`, `fit` e geometria normalizada.
- **Saída:** `plan validate` reporta metadados sem duração/áudio; preview e full usam a mesma composição sem áudio implícito.
- **Dependências:** FFprobe para a allowlist real e FFmpeg com `libx264` para a renderização local.
- **Limitação conhecida:** somente imagens estáticas das três famílias raster listadas; `ponytail: ampliar formatos apenas quando houver fonte editorial real que justifique outro decoder`.
- **Verificação:** `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v` e `.venv\Scripts\python.exe -B -m unittest tests.test_full_render -v`.

### Vídeo em camada, Phase 1 T7

- **Propósito:** compor um trecho MP4/H.264 sobre o vídeo-base no intervalo e na caixa normalizada declarados, sem áudio implícito.
- **Entrada:** fonte local `kind: "video"` e camada `type: "video"` com `source_id`, `in_s`, `out_s`, `start_s`, `end_s`, `fit` e geometria normalizada; as durações do trecho e da camada devem coincidir.
- **Saída:** `plan validate` rejeita fonte, intervalo ou duração incompatíveis; preview e full usam o mesmo grafo, com `contain` ou `crop`, ordem de `layers[]` e áudio apenas de `segments[].audio`.
- **Dependências:** FFprobe para validar a fonte e FFmpeg local com `libx264` para a composição.
- **Limitação conhecida:** não há loop, freeze, alteração de velocidade ou áudio de camada; `ponytail: adicionar essas operações somente se uma necessidade editorial real exigir contrato e validação próprios`.
- **Verificação:** `.venv\Scripts\python.exe -B -m unittest tests.test_plan_validation tests.test_preview_render -v` e `.venv\Scripts\python.exe -B -m unittest tests.test_full_render -v`.
