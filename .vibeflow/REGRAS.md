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
- `src/editor/media.py`: inspeção FFprobe e allowlist de formatos, streams, codecs, tamanho e duração.
- `src/editor/process.py`: execução estruturada e streaming PCM de processos locais sem shell intermediário.
- `src/editor/analysis/audio.py`: análise de loudness, pico verdadeiro, níveis por janela e silêncio por amplitude.
- `src/editor/analysis/frames.py`: seleção temporal, extração de PNGs e manifesto de frames efetivos.
- `src/editor/analysis/transcription.py`: validação da resposta do Whisper e publicação de transcrição com timestamps por palavra.
- `src/editor/whisper_backend.py`: seleção local de modelo/dispositivo e ponte lazy com faster-whisper sem download.
- `tests/test_audio_analysis.py`: processo real da CLI, validação precoce e preservação em falha de FFmpeg.
- `tests/test_frame_extraction.py`: seleção explícita/regular, timestamps efetivos, PNGs e preservação em falha de FFmpeg.
- `tests/test_transcription.py`: contrato canônico, seleção CPU/CUDA, timestamps obrigatórios e erros seguros da transcrição.
- `tests/fixtures/media/main.mp4`: fixture curta H.264/AAC com trechos silenciosos para o contrato de áudio.
- `tests/test_cli_contract.py`: smoke test dos dois pontos de entrada e dos envelopes JSON.
- `tests/test_installation.py`: testes do lockfile, do modo somente leitura, dos gates e da cópia de modelos locais.
- `tests/test_capabilities.py`: teste de processo do inventário e da recomendação determinística.
- `.vibeflow/`: fonte de governança e Documento Vivo de regras.
- `.vibeflow/mvp/`: entrevista, spec, plan, analyze e implement do MVP.
- `.vibeflow/phases/`: artefatos de entrevista, especificação, plano, análise, implementação e revisão por fase.
- `.gitignore`: proteção mínima para segredos locais e backups gerados.
- `AGENTS.md`: ponteiro para `.vibeflow/REGRAS.md`, usado por agentes compatíveis com AGENTS.
- `CLAUDE.md`: ponteiro para `.vibeflow/REGRAS.md`, usado por agentes compatíveis com CLAUDE.
- `.erros-encontrados/`: registros de problemas fora do escopo encontrados durante as verificações.
- Pontos de entrada da aplicação: `python -m editor` e o comando instalado `editor`.
- Stack técnica: Python 3.11 x64, setuptools como backend de empacotamento, `faster-whisper==1.2.1`, `ctranslate2==4.8.0`, FFmpeg local, `pip-audit==2.10.1` e gitleaks 8.30.1. A lista transitiva e os hashes ficam em `requirements.lock`.
<!-- evidência: pyproject.toml, src/editor/* implementados na T1; lockfile e instalador verificados na T2 em 2026-09-10; capacidades locais adicionadas na T3, análise de áudio na T4, transcrição local na T5 e extração de frames na T6 em 2026-09-12 -->

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

### Capacidades locais, T3

- **Propósito:** expor o inventário local consumível por agentes antes de iniciar análise ou edição.
- **Entrada:** `editor capabilities --json`; o comando também preserva a saída estruturada quando `--json` é omitido.
- **Saída:** envelope JSON com sistema, CPU, memória, GPU/VRAM, FFmpeg/FFprobe, backend Whisper, modelos locais, dispositivos, formatos, limites, operações e recomendação.
- **Dependências:** biblioteca padrão; FFmpeg/FFprobe, `nvidia-smi`, faster-whisper, CTranslate2 e modelos são sondados somente quando fornecidos localmente.
- **Limitação conhecida:** GPU é detectada via `nvidia-smi` e a recomendação só usa modelos `tiny`, `base` ou `small` com os arquivos CTranslate2 mínimos; `ponytail: adicionar adaptadores para outros fornecedores de GPU ou catálogos de modelos somente quando houver necessidade real`.
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
