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
<!-- evidência: pyproject.toml, src/editor/* implementados na T1; lockfile e instalador verificados na T2 em 2026-09-10; capacidades locais adicionadas na T3 em 2026-09-12 -->

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
