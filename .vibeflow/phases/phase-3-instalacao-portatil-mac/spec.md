# Spec: instalacao portatil mac
# Alvo: phase-3-instalacao-portatil-mac
# Status: aprovado

## Objetivo

O operador instala o editor a partir de um clone local com um comando único no Windows e no Mac, encontra o comando `editor` no PATH em um novo terminal e opera com comandos curtos dentro do trabalho. Sucesso é instalar nos dois sistemas sem PyPI, rodar a mesma jornada local com CPU garantida e manter os comandos longos e envelopes atuais válidos.

## Suposições e decisões

1. Fonte é o repo local clonado, sem PyPI nesta fatia. (escopo; já fechado)
2. Instalador continua explícito e offline para binários e pesos. FFmpeg, modelos e gitleaks entram só por origem local informada. Nenhum download implícito. (segurança / processo; já fechado)
3. `-Verify` e `--verify` são somente leitura. Sem flag explícita de instalação, nada é criado ou atualizado. (processo; já fechado)
4. Comandos longos atuais são canônicos e permanecem válidos. Atalhos adicionam sem quebrar, por isso a mudança é minor. (produto; já fechado)
5. Envelopes dos atalhos usam o nome canônico (`analyze audio`, `plan validate`, `render preview` e demais), para não quebrar agentes, skill e testes. (produto; já fechado)
6. `--json` permanece opcional e inalterado. (produto; já fechado)
7. Python exigido é 3.11 nos dois sistemas. Windows aceita x64. Mac aceita arm64 ou x64. (processo; já fechado)
8. Mac é CPU only como garantia. Metal é best effort com fallback para CPU: ausência de Metal nunca falha uma jornada. (produto; já fechado)
9. Nesta fatia, `--device` permanece `auto|cpu|cuda`. Metal aparece em `capabilities` como `devices.metal` quando provado no Mac, sem novo flag de dispositivo obrigatório. (escopo; já fechado)
10. CUDA permanece opcional e só no Windows. Mac não tem CUDA nem `nvidia-smi`. (produto; já fechado)

## Escopo e comportamento

### 1. Instalação por comando único e PATH

- A partir do clone local, um comando instala o venv a partir do lock com hashes obrigatórios e instala a CLI em modo editável.
  - Windows: `scripts/install.ps1` com `-Verify` somente leitura e `-Install` explícito, mais `-AddToPath` novo para registrar o shim no PATH do usuário.
  - Mac: `scripts/install.sh` espelho com `--verify`, `--install`, `--add-to-path`, `--venv-path` e `--model-dir`; fontes locais de ffmpeg, modelos e gitleaks seguem a mesma semântica explícita do ps1 com sintaxe Unix.
- O shim chama o Python do venv por caminho absoluto com `-m editor`, sem shell intermediário e sem montar comando a partir de entrada externa.
- O registro no PATH é por usuário, reversível e sem admin.
- Lock por plataforma com hashes obrigatórios: `requirements.lock` no Windows e `requirements.macos.lock` novo no Mac. Lock ausente ou sem hashes falha fechado antes de criar o venv.
- Reutilizar: `scripts/install.ps1`, `requirements.lock`, `pyproject.toml`, `src/editor/dependencies.py`, `tests/test_installation.py`.

### 2. Comandos curtos compatíveis

- `--workdir` passa a ser opcional em todos os subcomandos que hoje exigem. Quando omitido, resolve para o diretório atual convertido em absoluto e aplica a mesma validação vigente em `src/editor/workdir.py`. Caminho inválido retorna `WORKDIR_INVALID` sem criar estado.
- Atalhos planos, todos equivalentes aos canônicos:
  - `editor audio` equivale a `editor analyze audio`
  - `editor transcribe` equivale a `editor analyze transcribe`
  - `editor frames` equivale a `editor extract frames`
  - `editor validate` equivale a `editor plan validate`
  - `editor preview` equivale a `editor render preview`
  - `editor full` equivale a `editor render full`
- `init`, `approve` e `capabilities` permanecem como estão.
- `--source-id` passa a ser opcional em `analyze` e `extract`. Quando omitido, deriva do nome base do `--input` normalizado para `^[a-z0-9][a-z0-9_-]{0,63}$`. Derivação impossível retorna `ARGUMENT_INVALID`.
- Ajuda, envelopes, códigos de saída e artefatos dos comandos longos permanecem byte a byte.
- Reutilizar: `src/editor/cli.py`, `src/editor/contracts.py`, `src/editor/workdir.py`, `skills/vibe-editor`.

### 3. Mac com paridade de contrato

- Mesmos comandos, envelopes, exit codes, allowlists, limites de mídia, publicação atômica e preservação do artefato vigente em falha.
- Resolução de executáveis sem `.exe` no Mac, priorizando `.tools/ffmpeg` e depois o PATH, exigindo `libx264` e `aac` como em `src/editor/dependencies.py`.
- Modelos locais com o mesmo layout `models/tiny|base|small` e os mesmos arquivos mínimos CTranslate2.
- Transcrição garantida em CPU com `int8`. `devices.metal` em `capabilities` reporta `available` somente com prova no Mac; caso contrário reporta `missing` e a recomendação usa CPU. `auto` nunca falha apenas por ausência de Metal.
- Fontes: Windows mantém HKCU e HKLM; demais sistemas usam `fc-match` e rejeitam fallback de família. O renderer permanece `fontfile` e `capabilities` informa a origem consultada por sistema.
- Filtro FFmpeg sempre por arquivo. Windows mantém `-/filter_complex` e teto 32.767. Demais sistemas usam `-filter_complex` e o limite do sistema, reportado em `capabilities`.
- Reutilizar: `src/editor/dependencies.py`, `src/editor/capabilities.py`, `src/editor/whisper_backend.py`, `src/editor/plan/validation.py`, `src/editor/render/ffmpeg_graph.py`.

### Fora

- Publicar no PyPI, Homebrew tap, PKG, MSI, Gatekeeper ou notarização. Motivo: registro central e assinatura ficam para depois do Mac funcional por script.
- Garantir Metal, criar flag `--device metal` ou trocar o backend de transcrição. Motivo: exige prova no Mac com as versões pinadas antes de virar contrato.
- Mudar schema v2 do plano, envelopes, exit codes ou artefatos. Motivo: quebra agentes e jornada vigente sem necessidade.
- Download implícito de FFmpeg, modelos, gitleaks ou CUDA. Motivo: viola a premissa offline e explícita do instalador.

## Checklist de entrega

### Aceite

- [x] A1: Em um clone novo no Windows, a instalação por comando único deixa `editor` utilizável em um novo terminal.
- [x] A2: Em um clone novo no Mac, a instalação espelho deixa `editor` utilizável e a jornada funciona com CPU.
- [x] A3: Dentro do trabalho, `editor preview`, `editor approve` e `editor full` executam sem `--workdir` e geram os mesmos artefatos dos comandos longos.
- [x] A4: Atalhos retornam os envelopes canônicos e os comandos longos continuam válidos no help e na execução.
- [x] A5: No Mac sem Metal, a transcrição real com modelo `base` local publica o artefato e nenhuma jornada falha por ausência de Metal.

### Critérios de sucesso

- [x] C1: Suíte completa verde no Windows e no Mac.
- [x] C2: Verificação é somente leitura nos dois sistemas e não cria venv nem modelos.
- [x] C3: `capabilities` no Mac informa `devices.metal` sem erro e recomenda CPU quando Metal está ausente.
- [x] C4: Nenhum teste vigente de contrato precisa mudar por quebra de help ou envelope.

## Implementação

### Stack

| Área | Escolha |
|---|---|
| Instalação Windows | existente: `scripts/install.ps1` / delta: `-AddToPath` com shim por usuário |
| Instalação Mac | existente: sem espelho / delta: `scripts/install.sh` com mesma semântica em sintaxe Unix |
| Locks | existente: `requirements.lock` Windows / delta: `requirements.macos.lock` com hashes obrigatórios |
| CLI | existente: `src/editor/cli.py` / delta: `--workdir` e `--source-id` opcionais mais atalhos planos |
| Aceleração Mac | existente: CPU `int8` / delta: sonda Metal best effort sem novo flag obrigatório |
| Dependências novas | Nenhuma |

### Estrutura tocada

```text
scripts/install.ps1                 # shim no PATH por usuário sem mudar o contrato Verify e Install
scripts/install.sh                  # espelho Unix do provisionamento explícito e somente leitura
requirements.macos.lock             # resolução pinada com hashes para o Mac
src/editor/cli.py                   # defaults e atalhos com envelopes canônicos preservados
src/editor/capabilities.py          # reporte por sistema de filtro, fontes e devices.metal
src/editor/dependencies.py          # resolução sem .exe e sonda Metal sem tratar ausência como erro
src/editor/whisper_backend.py       # manter auto|cpu|cuda nesta fatia, com recomendação documentada
src/editor/plan/validation.py       # manter fontfile, com origem por sistema
src/editor/render/ffmpeg_graph.py   # flag de filtro por sistema, mantendo transporte por arquivo
tests/test_installation.py          # cobrir shim, readonly e lock por plataforma
tests/test_cli_contract.py          # cobrir atalhos sem mudar o contrato longo
tests/test_capabilities.py          # cobrir reporte por sistema e Metal ausente sem erro
```

### Estilo e padrões

- reutilizar: parser e handler central da CLI, validadores de workdir e source-id, sondas sem shell, publicação atômica e envelopes seguros existentes.
- Registrar limitações simplificadas com `ponytail` quando uma escolha postergar trabalho real.

### Contratos e módulos

- limites: mesmos tetos de mídia, plano, áudio, camadas e timeline; teto de comando e flag de filtro variam por sistema e são reportados em `capabilities`.
- API e schema: sem mudança no schema v2 do plano e sem mudança nos envelopes. Atalhos mapeiam para os nomes canônicos.
- Catálogo de erros na borda: `WORKDIR_INVALID` (estado, mensagem segura e causa: trabalho ausente ou não regular), `ARGUMENT_INVALID` (argumento, derivação de source-id impossível ou subcomando inválido), `DEVICE_UNAVAILABLE` (dependência, CUDA ou Metal exigido sem runtime utilizável), sem stack trace no stdout.

## Como provar

### Seams

- lock por plataforma com hashes obrigatórios antes de criar o venv.
- shim no PATH chamando o venv por caminho absoluto.
- mapeamento de atalhos para envelopes canônicos.
- default de workdir para o diretório atual com a mesma validação.
- sonda Metal que nunca transforma ausência em erro de jornada.

### Estratégia

- Unitário e contrato: testes de CLI, instalação, capabilities e validação nos dois sistemas.
- E2E e integração: jornada local com plano curto, preview, aprovação e full no Windows e no Mac, mais transcrição real com modelo `base` local.
- Manual: abrir um novo terminal após a instalação e confirmar `editor --help` e `editor capabilities --json` nos dois sistemas.

### Comandos

```bash
pwsh -NoProfile -File scripts/install.ps1 -Verify
python -m editor capabilities --json
python -m unittest discover -s tests -p "test_cli_contract.py" -v
python -m unittest discover -s tests -p "test_installation.py" -v
python -m unittest discover -s tests -v
# Espelho acordado no Mac após a implementação:
# bash scripts/install.sh --verify
```

## Boundaries

### Always

- Operação local, não interativa e sem rede para mídia, modelos e render.
- Provisionamento explícito, lock com hashes e verificação somente leitura.
- Entradas externas validadas na borda com allowlist e limite.
- Processos sem shell intermediário e sem comando montado a partir de entrada.
- Publicação atômica com preservação do artefato vigente em falha.
- Erros públicos seguros, sem stack trace e sem segredo em log.

### Ask first

- Publicar no PyPI, criar tap, PKG, MSI ou assinar artefatos.
- Tornar Metal garantido, adicionar valor novo em `--device` ou trocar o backend.
- Mudar comandos canônicos, envelopes, exit codes ou schema do plano.

### Never

- Baixar FFmpeg, pesos, gitleaks ou CUDA sem origem local explícita.
- Colocar segredo no código, no repo ou no envelope público.
- Tratar ausência de Metal ou CUDA opcional como falha de jornada garantida.
- Sobrescrever `plan.md` de outra fase ou gravar fora do alvo desta phase.

## Handoff

vibe-plan

- Chat: recomende novo chat para `vibe-plan`; continuidade no mesmo chat só por escolha consciente do humano. O `spec.md` vivo é a ponte.

- [ ] Aprovação humana (leu o arquivo e confirmou)
