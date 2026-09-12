# Plan: MVP CLI local de análise e edição linear
# Alvo: mvp
# Status: aprovado
# Spec: spec.md (mesma pasta)

## Overview

O MVP será entregue como uma CLI Python local, instalável no Windows, que consulta capacidades, analisa fontes, valida um plano JSON linear, gera uma única preview, registra aprovação por hash e renderiza a full somente quando o estado aprovado continuar íntegro. O fluxo mantém os arquivos originais, usa envelopes JSON estáveis, subprocessos sem shell intermediário, temporários isolados e publicação atômica. A spec aprovada é a fonte dos critérios A1 a A12, C1 a C5 e das decisões críticas preservadas abaixo.

## Ordem

### Fase 1: Contrato executável, instalação local, capacidades

- T1, T2, T3, em sequência, porque o entry point deve existir antes da instalação e a descoberta de capacidades precisa consultar o ambiente preparado.

### Fase 2: Fontes locais, análises, plano

- T4 primeiro para estabelecer a validação comum de mídia, trabalho, temporários e publicação.
- T5, T6 e T7 podem executar em paralelo depois de T3 e T4, pois usam contratos separados e não compartilham saídas de trabalho nos testes.

### Fase 3: Preview, aprovação, full

- T8 → T9 → T10, nesta ordem, porque cada etapa consome o artefato verificável produzido pela anterior.

### Fase 4: Aceite do MVP

- T11 consolida a jornada do vlog de viagens, os cenários de falha, os gates de segurança e o mapa real do repositório.

## Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Variação entre FFmpeg, faster-whisper, CUDA e modelos locais | médio | T2 e T3 expõem capacidades, estados `missing`/`unsupported` e recomendação determinística; nenhum download silencioso durante o trabalho. |
| Python 3.11 x64 não está disponível no ambiente atual, que expõe 3.12 e 3.13 | médio | T2 trata o runtime como pré-requisito explícito do instalador e bloqueia a execução fora do alvo suportado. |
| `pip-audit` não está disponível no ambiente atual | médio | T2 aloca a instalação e a execução do gate SCA junto ao lockfile; `gitleaks 8.30.1` já foi validado. |
| Locks de arquivo do Windows durante renderização ou substituição | médio | Todas as saídas passam por `.tmp`, são fechadas e validadas antes da substituição atômica; falhas preservam o destino anterior. |
| Alteração de plano, preview ou fonte após aprovação | alto | T9 grava hashes canônicos e T10 recalcula todos antes de publicar `full.mp4`. |

## Paralelização

- Paralelo ok: T5, T6 e T7 após T4; cada task possui fixtures e artefatos próprios.
- Sequencial: T1 → T2 → T3 → T4; T7 → T8 → T9 → T10; T11 por último.
- Contrato primeiro, depois paralelo: T1 define envelopes e entry point; T4 centraliza validação de fonte, limites, temporários e publicação; T7 fecha o contrato do plano antes da renderização.

## Tasks

### T1: Criar CLI instalável com smoke test

- [x] T1 concluída
- **Spec:** A11, C2
- **Decisões:** CLI-01 (mantém CLI local não interativa), CONTRACT-01 (cria envelope estável), DEP-01 (mantém pacote Python)
- **O quê:** Montar `pyproject.toml`, o pacote em `src/editor`, o entry point `editor` e o caminho `python -m editor`. Centralizar o envelope de sucesso, o envelope de erro seguro e os exit codes, com comentários semânticos nos módulos e funções.
- **Aceite:**
  - [x] `python -m editor --help` e o entry point instalado sobem sem prompt; uma chamada inválida em modo JSON retorna somente um envelope de erro válido em `stdout`, sem stack trace.
  - [x] O smoke test executa o ponto de entrada real antes de qualquer integração com mídia ou modelo externo.
  - [x] O contrato inclui `schema_version`, `command`, `data` nos sucessos e `error.code`, `error.message`, `error.exit_code` nas falhas.
- **Verificação:**
  - [x] `python -m pip install --no-deps -e .`
  - [x] `python -m editor --help`
  - [x] `editor --help`
  - [x] `python -m unittest discover -s tests -p "test_cli_contract.py" -v`
  - **Prova:** instalação editável concluída; os dois comandos de ajuda retornaram `0`; a suíte retornou `Ran 4 tests ... OK`.
- **Deps:** nenhuma
- **Arquivos:** `pyproject.toml`, `src/editor/__init__.py`, `src/editor/__main__.py`, `src/editor/cli.py`, `src/editor/contracts.py`, `tests/test_cli_contract.py`
- **Size:** medium, 5/10, entry point, contrato interno, smoke de CLI e uma dependência de execução
- **Risk:** low

### T2: Provisionar ambiente local reproduzível

- [x] T2 concluída
- **Spec:** A12, C5
- **Decisões:** DEP-01 (mantém instalação explícita em ambiente virtual), TECH-01 (pina faster-whisper/CTranslate2), AI-01 (mantém CPU como baseline), PRIV-01 (mantém instalação sem telemetria)
- **O quê:** Criar o lockfile e o instalador PowerShell para Python 3.11 x64, dependências Python, FFmpeg, runtime CUDA opcional e modelos locais. O instalador deve verificar o estado antes de instalar, disponibilizar o `pip-audit` em versão pinada para o gate SCA e exigir ação explícita para qualquer componente externo.
- **Aceite:**
  - [x] `scripts/install.ps1 -Verify` valida Python 3.11 x64, o lockfile, FFmpeg, o backend Whisper, modelos locais e os componentes CUDA disponíveis, sem instalar durante a verificação.
  - [x] As opções de instalação são explícitas, o ambiente virtual usa o lockfile com hashes e `pip check` é executado após a instalação.
  - [x] `pip-audit` e `gitleaks` são verificados antes dos gates; quando ausentes, a validação falha com instrução segura e não reporta o gate como aprovado. A instalação de qualquer ferramenta é explícita.
  - [x] O gate de segredo com gitleaks e o gate SCA com pip-audit são executáveis a partir do repositório e bloqueiam a validação quando encontram um problema.
- **Verificação:**
  - [x] `pwsh -NoProfile -File scripts/install.ps1 -Verify` -> exit `1` esperado no host atual, reportando Python 3.11, venv, backend, modelos e pip-audit ausentes sem criar arquivos; o comportamento de bloqueio é coberto pela suíte.
  - [ ] `python -m pip check` -> exit `1` por conflito preexistente do Python global (`inquirer 3.4.1` exige `editor>=1.6.0` e o pacote local está em `0.1.0`); registrado em `.erros-encontrados/2026-09-10-python-global-inquirer-editor.md`.
  - [x] `gitleaks detect --source . --no-banner` -> exit `0`, `no leaks found`.
  - [x] `python -m pip_audit -r requirements.lock` -> exit `1` porque a ferramenta não está instalada; `-Verify` bloqueia com instrução para executar `-Install`, sem declarar o gate como aprovado.
  - [x] `python -m unittest discover -s tests -p "test_installation.py" -v` -> `Ran 6 tests ... OK`.
- **Deps:** T1
- **Arquivos:** `requirements.lock`, `scripts/install.ps1`, `tests/test_installation.py`
- **Arquivos adicionais necessários:** `.vibeflow/REGRAS.md`, `.erros-encontrados/2026-09-10-python-global-inquirer-editor.md`
- **Prova:** lockfile resolvido para Python 3.11 x64 com 50 pacotes e 648 hashes SHA-256; `-Verify` é somente leitura; instalação de FFmpeg, gitleaks, CUDA e modelos exige caminhos locais explícitos.
- **Limitação de ambiente:** o host disponível expõe Python 3.12/3.13, não possui Python 3.11, backend Whisper, modelos locais ou pip-audit; por isso a verificação completa de instalação não pode ser aprovada neste host e permanece bloqueada até provisionamento explícito.
- **Size:** medium, 6/10, runtime, dependências externas, verificação de instalação e gates operacionais
- **Risk:** medium, a máquina precisa do runtime Python 3.11 x64 e do tooling SCA para executar o gate completo

### T3: Detectar capacidades locais com recomendação determinística

- [x] T3 concluída
- **Spec:** A1, A10, A11, C2, C5
- **Decisões:** TECH-01 (usa faster-whisper/CTranslate2), AI-01 (mantém CPU baseline e CUDA opcional), AI-02 (recomendação `base`/`small`/`tiny` somente entre modelos locais), PRIV-01 (não envia inventário para fora)
- **O quê:** Implementar `capabilities --json` com detecção de sistema, arquitetura, CPU, memória, GPU/VRAM, FFmpeg, backend Whisper, modelos e dispositivos. A resposta deve distinguir `available`, `missing`, `unsupported` e `recommended`, sem executar edição nem fazer rede.
- **Aceite:**
  - [x] A saída é um envelope JSON estável com versão da CLI, capacidades detectadas, formatos, limites, operações e recomendação de dispositivo/modelo.
  - [x] A ausência de FFmpeg, CUDA ou modelo não impede a consulta; apenas operações dependentes ficam marcadas como indisponíveis, sem fallback remoto ou download.
  - [x] A recomendação escolhe `base` no CPU quando instalado, `small` no CUDA quando instalado e `tiny` como fallback local determinístico; override indisponível fica explícito.
- **Verificação:**
  - [x] `python -m editor capabilities --json` -> exit `0`, envelope `capabilities` válido; no host atual FFmpeg/FFprobe e GPU foram detectados, enquanto backend Whisper e modelos ficaram explicitamente como ausentes.
  - [x] `python -m unittest discover -s tests -p "test_capabilities.py" -v` -> `Ran 4 tests ... OK`.
- **Prova:** a descoberta usa somente APIs locais e subprocessos estruturados com `shell=False`, e o fixture de modelos confirmou `base` disponível e `tiny` incompleto como `unsupported`.
- **Deps:** T1, T2
- **Arquivos:** `src/editor/capabilities.py`, `src/editor/dependencies.py`, `tests/test_capabilities.py`
- **Size:** medium, 5/10, descoberta local, subprocesso de FFmpeg, estados de dependência e teste de contrato
- **Risk:** medium, hardware e componentes opcionais variam entre máquinas

### T4: Analisar áudio com guardrails de mídia

- [x] T4 concluída
- **Spec:** A3, A9, A10, A11, C2, C4, C5
- **Decisões:** MEDIA-01 (restringe formatos, duração e tamanho), DATA-01 (usa diretório local por trabalho), OPS-01 (processa um trabalho por vez), PRIV-01 (preserva originais e mantém processamento local), CONTRACT-01 (mapeia falhas para códigos estáveis)
- **O quê:** Implementar a validação comum de caminho, diretório de trabalho, arquivo regular, tamanho, duração, codec, streams, temporários e publicação atômica dentro de `analyze audio`. Gerar a análise de loudness, picos, média por janela e intervalos de silêncio por amplitude, sem classificá-los como fala ou ruído.
- **Aceite:**
  - [ ] Uma fonte MP4, MP3 ou WAV válida gera `analysis/audio/<source-id>.json` com SHA-256, loudness integrado, pico verdadeiro, média e pico por janela e silêncio com limiar de `-45 dBFS` por no mínimo `0,3 s`.
  - [ ] Caminho inválido, diretório de trabalho ou destino com reparse point, formato, codec, stream, tamanho acima de 4 GiB ou duração acima de 3.600 s falham antes do processamento, com envelope seguro e exit code estável.
  - [ ] Uma falha simulada do FFmpeg preserva originais, plano, análises existentes e destino publicado, removendo temporários sem deixar saída parcial.
- **Verificação:**
  - [x] `python -m editor analyze audio --workdir A:\Projetos\editor\tmp-t4-run --source-id main --input tests/fixtures/media/main.mp4 --window-s 0.5 --json` -> exit `0`, envelope `analyze audio` e `analysis/audio/main.json` publicados.
  - [x] `python -m unittest discover -s tests -p "test_audio_analysis.py" -v` -> `Ran 4 tests ... OK`.
- **Deps:** T1, T2
- **Arquivos:** `src/editor/workdir.py`, `src/editor/media.py`, `src/editor/process.py`, `src/editor/analysis/audio.py`, `tests/test_audio_analysis.py`, `tests/fixtures/media/`
- **Prova:** fonte MP4 H.264/AAC de 2 segundos produziu hash SHA-256, loudness integrado, pico verdadeiro, quatro janelas de 0,5 s e dois intervalos de silêncio; janela/formato inválidos falharam antes do subprocesso; falha simulada do FFmpeg preservou `plan.json`, análise vigente e fonte, removendo temporários.
- **Decisões:** MEDIA-01, DATA-01, OPS-01, PRIV-01 e CONTRACT-01 implementadas para análise de áudio.
- **Size:** high, 7/10, validação de entrada, FFmpeg, artefato persistente, falhas de publicação e integração com fixtures
- **Risk:** medium, a análise depende dos metadados e filtros disponíveis no FFmpeg local

### T5: Transcrever fonte local com timestamps por palavra

- [x] T5 concluída
- **Spec:** A2, A9, A10, A11, C2, C5
- **Decisões:** TECH-01 (usa faster-whisper/CTranslate2), AI-01 (CPU `int8` como baseline), AI-02 (seleciona somente modelo local recomendado ou explicitado), MEDIA-01 (reusa allowlist de fontes), PRIV-01 (não envia áudio nem transcrição)
- **O quê:** Implementar `analyze transcribe` sobre o backend local, reusando a validação de fonte e a escolha de dispositivo/modelo. O JSON canônico deve preservar hash da fonte, idioma, texto, segmentos e timestamps de início/fim por palavra.
- **Aceite:**
  - [x] Uma fonte válida gera `analysis/transcription/<source-id>.json` com todos os campos obrigatórios e timestamps por palavra; ausência desses timestamps falha o comando.
  - [x] Modelo ausente, dispositivo indisponível ou fonte inválida retornam `MODEL_MISSING`, `DEVICE_UNAVAILABLE` ou código de entrada correspondente, sem modificar a fonte ou publicar análise parcial.
  - [x] `--device cpu|cuda` e `--model` explícitos são respeitados; nenhum download ou fallback para serviço externo ocorre durante a análise.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -m editor analyze transcribe --workdir A:\Projetos\editor\tmp-t5-run --source-id main --input tests\fixtures\media\main.mp4 --device cpu --model base --json` -> exit `0`, publicou `analysis/transcription/main.json`, idioma `en` e `segment_count: 0`.
  - [x] `.venv\Scripts\python.exe -m editor analyze transcribe --workdir A:\Projetos\editor\tmp-t5-run --source-id main --input tests\fixtures\media\main.mp4 --device cpu --json` -> exit `0` também sem modelo explícito, usando a recomendação local `base`.
  - [x] `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_transcription.py" -v` -> `Ran 8 tests ... OK`.
  - [x] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` e `python -B -m unittest discover -s tests -v` -> `Ran 26 tests ... OK` em cada runtime.
- **Deps:** T3, T4
- **Arquivos:** `src/editor/analysis/transcription.py`, `src/editor/whisper_backend.py`, `tests/test_transcription.py`, `tests/test_installation.py`, `requirements.lock`
- **Size:** high, 8/10, backend local externo, contrato de timestamps, seleção de dispositivo e integração com modelo
- **Risk:** medium, qualidade e disponibilidade dos timestamps dependem do modelo local instalado

### T6: Extrair frames com manifesto temporal

- [x] T6 concluída
- **Spec:** A4, A9, A10, A11, C2, C4
- **Decisões:** MEDIA-01 (aceita somente vídeo compatível e limita a fonte), DATA-01 (grava em `frames/<source-id>`), PRIV-01 (não altera o original), CONTRACT-01 (retorna erros estruturados)
- **O quê:** Implementar `extract frames` com exatamente um modo de seleção, timestamps explícitos ou intervalo regular. Validar faixa, duplicação, início/fim e duração, gerar PNGs deterministicamente numerados e manifestar timestamp solicitado, efetivo e caminho.
- **Aceite:**
  - [x] Timestamps explícitos e intervalos válidos produzem PNGs e `frames/<source-id>/manifest.json` com associação temporal determinística.
  - [x] Usar os dois modos, nenhum modo, timestamps duplicados ou fora da duração falha antes do FFmpeg com `ARGUMENT_INVALID` ou `MEDIA_LIMIT_EXCEEDED` apropriado.
  - [x] A fonte original permanece inalterada e falhas no meio da extração não publicam um manifesto incompleto.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m editor extract frames --workdir <workdir-isolado> --source-id main --input tests/fixtures/media/main.mp4 --interval-s 5 --json` -> exit `0`, um PNG e manifesto publicados.
  - [x] `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_frame_extraction.py" -v` -> `Ran 9 tests ... OK`.
- **Deps:** T4
- **Arquivos:** `.vibeflow/REGRAS.md`, `src/editor/analysis/frames.py`, `src/editor/cli.py`, `src/editor/media.py`, `src/editor/workdir.py`, `tests/test_frame_extraction.py`, `tests/fixtures/media/`
- **Size:** medium, 6/10, validação temporal, FFmpeg, múltiplos PNGs, manifesto e teste de integração
- **Risk:** medium, timestamps efetivos podem variar conforme keyframes e precisam ser registrados

### T7: Validar plano linear com schema estrito

- [x] T7 concluída
- **Spec:** A5, A6, A10, A11, C2, C3
- **Decisões:** DATA-01 (mantém `plan.json` como estado explícito), MEDIA-01 (valida fontes e duração), AUDIO-01 (mantém trilhas, ganhos, loudness e limiter explícitos), TEXT-01 (mantém lettering estático nomeado), CONTRACT-01 (rejeita campos fora da allowlist)
- **O quê:** Implementar `plan validate` para ler exclusivamente `plan.json`, validar schema versão 1, IDs, caminhos, formatos, intervalos, duração de áudio, timeline, b-roll, `fit`, overlays, perfil 16:9 e processamento de áudio. A forma canônica do JSON deve ficar disponível para hashing posterior.
- **Aceite:**
  - [x] O plano de cenário com corte, junção, b-roll em tela cheia mantendo áudio de A, música mixada, ganhos explícitos, normalização, limiter e lettering passa e retorna a timeline linear calculada.
  - [x] JSON inválido, campo desconhecido, ID duplicado, caminho fora do trabalho, intervalo fora da fonte, áudio incompatível, overlay inválido, perfil de saída diferente de 16:9 ou fonte de proporção diferente sem `fit: contain|crop` retorna `PLAN_INVALID` sem renderizar.
  - [x] A serialização canônica ordena chaves, usa UTF-8 sem espaços e mantém o mesmo hash para alterações apenas de formatação.
- **Verificação:**
  - [x] `.venv\Scripts\python.exe -B -m editor plan validate --workdir A:\Projetos\editor\tests\fixtures\workflow --json` -> exit `0`, três fontes validadas e timeline de `2.0 s` retornada.
  - [x] `.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_plan_validation.py" -v` -> `Ran 7 tests ... OK`.
  - [x] `.venv\Scripts\python.exe -B -m unittest discover -s tests -v` -> `Ran 42 tests ... OK`.
- **Deps:** T4
- **Prova:** o fixture real validou corte, junção, b-roll com áudio de `main`, mixagem com `music-01`, ganhos explícitos, normalização, limiter, lettering, proporção 16:9 e hash canônico; campos desconhecidos e intervalos inválidos falharam com `PLAN_INVALID` antes do processamento externo.
- **Arquivos:** `.vibeflow/REGRAS.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`, `src/editor/cli.py`, `src/editor/media.py`, `src/editor/plan/__init__.py`, `src/editor/plan/schema.py`, `src/editor/plan/validation.py`, `src/editor/plan/canonical.py`, `tests/test_plan_validation.py`, `tests/fixtures/workflow/plan.json`, `tests/fixtures/workflow/sources/main.mp4`, `tests/fixtures/workflow/sources/broll.mp4`, `tests/fixtures/workflow/sources/music.mp3`
- **Decisões:** DATA-01, MEDIA-01, AUDIO-01, TEXT-01 e CONTRACT-01 implementadas para validação do plano.
- **Size:** high, 7/10, schema estrito, metadados de mídia, regras de áudio/texto, hash canônico e integração de contrato
- **Risk:** medium, erros de fronteira na timeline podem alterar a duração ou a segurança da renderização

### T8: Renderizar preview a partir do plano validado

- [x] T8 concluída
- **Spec:** A5, A6, A7, A9, A11, C1, C3, C4, C5
- **Decisões:** RENDER-01 (mantém uma única preview vigente), AUDIO-01 (aplica ganho, normalização e limiter sem ducking), TEXT-01 (renderiza lettering estático com margem segura), MEDIA-01 (preserva perfil e codecs aceitos), PRIV-01 (publica somente artefato completo)
- **O quê:** Implementar `render preview` para transformar a timeline validada em uma única `preview.mp4`, incluindo cortes, junções, b-roll em tela cheia com áudio de A, mixagem de música, `fit`, lettering e processamento de áudio. O FFmpeg deve receber argumentos estruturados e a saída deve passar por `.tmp`, validação de metadados e substituição atômica.
- **Aceite:**
  - [x] O plano de cenário gera `preview.mp4` com a timeline linear, proporção 16:9, áudio conforme as faixas declaradas e maior dimensão de no máximo 720 pixels.
  - [x] O áudio de B não é usado quando B ocupa a imagem do segmento com áudio de A; ganhos, normalização e limiter respeitam o plano e não há ducking automático.
  - [x] Nova preview substitui a anterior somente após sucesso; falha de FFmpeg preserva a anterior, não publica arquivo parcial e retorna `RENDER_FAILED` ou `OUTPUT_PUBLISH_FAILED` seguro.
- **Verificação:**
  - [x] `python -m editor render preview --workdir A:\Projetos\editor\tests\fixtures\workflow --json`
  - [x] `python -m unittest discover -s tests -p "test_preview_render.py" -v` -> `Ran 4 tests ... OK`.
- **Deps:** T4, T7
- **Prova:** o fixture real gerou `preview.mp4` de `2.0 s`, H.264/AAC, `320x180`, com timeline de três segmentos; o grafo não incluiu o áudio de B no b-roll, aplicou ganho, `amix` sem normalização automática, `loudnorm`, limiter e lettering; a falha simulada preservou a preview anterior e deixou `.tmp` vazio.
- **Arquivos:** `.vibeflow/REGRAS.md`, `.vibeflow/mvp/implement.md`, `.vibeflow/mvp/plan.md`, `.vibeflow/mvp/spec.md`, `src/editor/render/__init__.py`, `src/editor/render/preview.py`, `src/editor/render/ffmpeg_graph.py`, `src/editor/cli.py`, `src/editor/plan/validation.py`, `src/editor/workdir.py`, `tests/test_preview_render.py`.
- **Decisões:** RENDER-01, AUDIO-01, TEXT-01, MEDIA-01 e PRIV-01 implementadas para a preview.
- **Size:** high, 8/10, composição FFmpeg, áudio independente, lettering, escala, publicação atômica e integração de mídia
- **Risk:** medium, filtros e codecs podem ter diferenças entre builds do FFmpeg

### T9: Registrar aprovação vinculada a hashes

- [ ] T9 concluída
- **Spec:** A8, A11, C2, C3, C4
- **Decisões:** STATE-01 (registra plano, preview e fontes), RENDER-01 (aprovação refere a preview vigente), DATA-01 (grava `approval.json` no trabalho), CONTRACT-01 (falha sem aprovação válida usa código estável)
- **O quê:** Implementar `approve` após validação do plano e existência da preview publicada. Calcular o hash SHA-256 do plano canônico, da preview e de cada fonte referenciada, preservando a lista ordenada de fontes e o instante UTC.
- **Aceite:**
  - [ ] `approval.json` contém `schema_version`, `approved_at`, `plan_sha256`, `preview_sha256` e `{source_id, sha256}` ordenados.
  - [ ] Plano inválido, preview ausente ou saída não publicada impedem a aprovação sem substituir um registro válido por estado incompleto.
  - [ ] Alterações somente de formatação em `plan.json` não mudam `plan_sha256`; qualquer alteração semântica muda o hash.
- **Verificação:**
  - [ ] `python -m editor approve --workdir tests/fixtures/workflow --json`
  - [ ] `python -m unittest discover -s tests -p "test_approval.py" -v`
- **Deps:** T7, T8
- **Arquivos:** `src/editor/integrity/hashes.py`, `src/editor/integrity/approval.py`, `tests/test_approval.py`, `tests/fixtures/workflow/approval.json`
- **Size:** medium, 6/10, estado persistente, hashing de múltiplos artefatos, contrato de aprovação e integração com preview
- **Risk:** medium, a aprovação inválida precisa ser impossível de confundir com autorização vigente

### T10: Renderizar full com aprovação vigente

- [ ] T10 concluída
- **Spec:** A8, A9, A11, C1, C3, C4, C5
- **Decisões:** STATE-01 (recalcula todos os hashes antes da full), RENDER-01 (usa a mesma timeline aprovada), PRIV-01 (publica full somente após sucesso)
- **O quê:** Implementar `render full` para revalidar plano, approval, preview e todas as fontes antes de chamar o FFmpeg. A renderização deve usar exatamente a timeline aprovada, sem reanálise ou decisão editorial, e publicar `full.mp4` no perfil final definido pelo primeiro vídeo.
- **Aceite:**
  - [ ] Com hashes coincidentes, a full é publicada no perfil final do plano e corresponde à timeline aprovada, sem nova interpretação do conteúdo.
  - [ ] Alterar separadamente plano, preview ou qualquer fonte retorna `APPROVAL_STALE` antes da renderização e não altera uma full existente.
  - [ ] Falha no FFmpeg ou na publicação preserva plano, análises, approval e full anterior, sem saída parcial.
- **Verificação:**
  - [ ] `python -m editor render full --workdir tests/fixtures/workflow --json`
  - [ ] `python -m unittest discover -s tests -p "test_full_render.py" -v`
- **Deps:** T9
- **Arquivos:** `src/editor/render/full.py`, `src/editor/integrity/approval.py`, `tests/test_full_render.py`, `tests/fixtures/workflow/`
- **Size:** high, 7/10, revalidação de estado, renderização final, cenários de mutação e publicação atômica
- **Risk:** medium, qualquer divergência deve bloquear a saída para preservar a integridade da aprovação

### T11: Validar jornada local completa do MVP

- [ ] T11 concluída
- **Spec:** A1 a A12, C1 a C5
- **Decisões:** PROD-01 (aceita vlog de viagens em 16:9), SCOPE-01 (fecha análise e edição linear ponta a ponta), OPS-01 (mantém um trabalho por vez), PRIV-01 (mantém execução local sem telemetria)
- **O quê:** Consolidar fixtures curtas de vlog, b-roll, música e lettering para executar a jornada completa `capabilities` → análise → plano → preview → aprovação explícita → full. A task também registra no Documento Vivo a stack, os pontos de entrada e os módulos realmente criados, além das limitações verificadas.
- **Aceite:**
  - [ ] O cenário E2E percorre todos os comandos do contrato, produz os artefatos esperados e confirma que a full usa a mesma timeline aprovada.
  - [ ] A prova confirma `stdout` JSON, diagnósticos técnicos somente em `stderr`, ausência de prompts, ausência de rede, originais intactos e ausência de publicação parcial nos cenários de falha.
  - [ ] `REGRAS.md` fica sincronizado com a stack, estrutura, ferramentas, contratos, limitações conhecidas e estratégia de verificação implementados.
- **Verificação:**
  - [ ] `python -m unittest tests.e2e.test_mvp_flow -v`
  - [ ] `gitleaks detect --source . --no-banner`
  - [ ] `python -m pip_audit -r requirements.lock`
- **Deps:** T3, T5, T6, T7, T8, T9, T10
- **Arquivos:** `tests/e2e/test_mvp_flow.py`, `tests/fixtures/e2e/`, `.vibeflow/REGRAS.md`
- **Size:** high, 8/10, jornada E2E, dependências locais, verificação de falhas, gates de segurança e sincronização de governança
- **Risk:** medium, a prova depende de fixtures de mídia e de modelo local compatíveis com o runtime alvo

## Conferência

- [x] Spec aprovada como fonte; mesma pasta
- [x] Fatias verticais; score de Size ≤ 8; Risk separado
- [x] Toda T* tem aceite, verificação e `Spec: A*/C*` quando couber
- [x] Ferramentas essenciais, gitleaks validado; pip-audit alocado em T2; chrome-devtools N/A por não haver UI
- [x] T1 estabelece o ponto de entrada real com smoke test de CLI
- [x] UI greenfield/sem DS: N/A, o MVP não possui frontend
- [x] Cada T* possui unidade de execução, verificação e commit própria
- [ ] Aprovação humana, leu o arquivo e confirmou

## Handoff

rota: max

O MVP passa por `vibe-analyze` antes do código, conforme a rota `max`. O `plan.md` entra no Git; `.vibeflow/plan-report.json` fica fora do Git. Recomenda-se abrir um novo chat para `vibe-analyze`; continuar neste chat é uma escolha consciente. O plano vivo é a ponte para a próxima etapa.
