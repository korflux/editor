# Analyze: consistência do MVP de análise e edição linear local
# Alvo: mvp
# Status: aprovado
# Plan: plan.md (mesma pasta)

## Fontes

| Arquivo | Estado |
|---|---|
| interview.md | presente |
| spec.md | presente |
| plan.md | presente, aprovado durante esta análise |
| REGRAS.md | lido |

## Cobertura e Rastreabilidade

| Chave | Origem | T* | Notas |
|---|---|---|---|
| A1 | spec.md | T3, T11 | Capacidades locais e jornada ponta a ponta |
| A2 | spec.md | T5, T11 | Transcrição local com timestamps por palavra |
| A3 | spec.md | T4, T11 | Loudness, picos, média e silêncio por amplitude |
| A4 | spec.md | T6, T11 | Frames por timestamps ou intervalo |
| A5 | spec.md | T7, T8, T11 | Plano linear e composição da preview |
| A6 | spec.md | T7, T8, T11 | Mixagem, ganhos, normalização e limiter |
| A7 | spec.md | T8, T11 | Uma preview vigente, publicada atomicamente |
| A8 | spec.md | T9, T10, T11 | Aprovação por hashes e full condicionada |
| A9 | spec.md | T4, T5, T6, T8, T10, T11 | Preservação e falhas sem publicação parcial |
| A10 | spec.md | T3, T4, T5, T6, T7, T11 | Limites, formatos, caminhos e dependências |
| A11 | spec.md | T1, T3, T4, T5, T6, T7, T8, T9, T10, T11 | CLI não interativa, local e sem shell intermediário |
| A12 | spec.md | T2, T11 | Instalação explícita e ambiente reproduzível |
| C1 | spec.md | T8, T10, T11 | Jornada do vlog de viagens |
| C2 | spec.md | T1, T3, T4, T5, T6, T7, T9, T11 | Resultados de máquina em stdout JSON |
| C3 | spec.md | T7, T8, T9, T10, T11 | Full baseada na mesma timeline aprovada |
| C4 | spec.md | T4, T6, T8, T9, T10, T11 | Originais intactos e saídas completas |
| C5 | spec.md | T2, T3, T5, T8, T10, T11 | Processamento local sem fallback externo |

## Achados e Resoluções

| ID | Categoria | Gravidade | Onde | Problema | Resolução Aplicada |
|---|---|---|---|---|---|
| F1 | constituicao | CRITICAL | spec.md, plan.md | O diretório de trabalho e os destinos não tinham rejeição explícita de symlink, junction ou reparse point, deixando a proteção de caminhos incompleta. | A spec passou a exigir workdir e destinos regulares, sem reparse point; T4 recebeu o aceite correspondente. |
| F2 | inconsistencia | MEDIUM | spec.md, plan.md | O texto de validação dizia rejeitar qualquer proporção diferente de 16:9, contradizendo a permissão de fontes diferentes com `contain` ou `crop`. | O bloqueio ficou restrito ao perfil de saída; fontes diferentes só falham sem `fit: contain|crop`. |
| F3 | qualidade_teste | HIGH | plan.md | O smoke test usava layout `src/`, mas não instalava o pacote antes de executar `python -m editor` nem verificava o entry point instalado. | T1 passou a instalar o pacote em modo editável sem dependências e a verificar `python -m editor --help` e `editor --help`. |
| F4 | qualidade_teste | HIGH | plan.md | `pip-audit` não está disponível no host e o plano apenas o citava no gate, sem exigir sua disponibilização explícita. | T2 passou a alocar `pip-audit` pinado, verificar as ferramentas antes dos gates e falhar com instrução segura quando ausentes. |
| F5 | furo | MEDIUM | spec.md, plan.md | O comportamento de `--window-s`, do intervalo regular de frames e do limite final dos timestamps não estava fechado o suficiente para uma implementação determinística. | A spec definiu `window-s` finito e positivo, preservação da janela parcial, defaults de frames e a convenção `[0, duração)`. |
| F6 | furo | MEDIUM | spec.md | `fit: contain` e `fit: crop` eram aceitos, mas o resultado visual não definia barras, centralização ou âncora do corte. | A spec definiu contain centralizado com barras pretas e crop centralizado simétrico. |
| F7 | inconsistencia | LOW | plan.md | A ordem da fase dizia que T5, que depende de T3 e T4, poderia começar apenas depois de T4. | A ordem foi normalizada para declarar explicitamente T3 e T4 como pré-requisitos do paralelo. |

### F1: integridade de caminhos no diretório de trabalho

- **Gravidade:** CRITICAL
- **Categoria:** constituicao
- **Onde:** `spec.md` e `plan.md`
- **Evidência:** A entrada `workdir` é externa e controla onde artefatos são escritos; a regra do repositório exige validação de caminhos e diretórios permitidos.
- **Resolução:** Rejeição explícita de workdir, diretórios de destino e componentes linkados ou reparse points antes da análise e durante a publicação.

### F2: proporção do perfil versus proporção da fonte

- **Gravidade:** MEDIUM
- **Categoria:** inconsistencia
- **Onde:** `spec.md` e `plan.md`
- **Evidência:** A spec permite fontes fora de 16:9 com `fit`, mas o aceite usava “proporção diferente de 16:9” sem qualificar o objeto.
- **Resolução:** O perfil de saída continua obrigatório em 16:9; a proporção da fonte é aceita quando `contain` ou `crop` está declarado.

### F3: smoke test em pacote com layout `src/`

- **Gravidade:** HIGH
- **Categoria:** qualidade_teste
- **Onde:** `plan.md`
- **Evidência:** Sem instalação editável ou `PYTHONPATH`, `python -m editor` não encontra o pacote em `src/editor` a partir da raiz.
- **Resolução:** T1 instala o pacote com `python -m pip install --no-deps -e .` e verifica os dois pontos de entrada.

### F4: setup explícito do gate SCA

- **Gravidade:** HIGH
- **Categoria:** qualidade_teste
- **Onde:** `plan.md`
- **Evidência:** `gitleaks` está disponível na versão 8.30.1; `pip-audit` não está instalado no host atual.
- **Resolução:** T2 exige que ambas as ferramentas sejam verificadas antes do gate, com instalação explícita e versão pinada para `pip-audit`; ausência bloqueia a validação.

### F5: limites temporais da análise e dos frames

- **Gravidade:** MEDIUM
- **Categoria:** furo
- **Onde:** `spec.md` e `plan.md`
- **Evidência:** A CLI expõe `--window-s`, `--start-s` e `--end-s`, mas sem semântica completa para valores inválidos, defaults e o ponto final.
- **Resolução:** Foram definidos valor finito e positivo para a janela, inclusão da janela parcial, defaults de intervalo e geração estritamente antes da duração.

### F6: transformação de fontes com proporção diferente

- **Gravidade:** MEDIUM
- **Categoria:** furo
- **Onde:** `spec.md`
- **Evidência:** `contain` e `crop` alteram materialmente a imagem e precisavam de uma regra estável para renderização.
- **Resolução:** `contain` usa barras pretas e centralização; `crop` usa corte simétrico central.

### F7: pré-requisitos do paralelo

- **Gravidade:** LOW
- **Categoria:** inconsistencia
- **Onde:** `plan.md`
- **Evidência:** T5 lista T3 e T4 em `Deps`, mas a ordem resumida citava somente T4.
- **Resolução:** A ordem da fase agora cita T3 e T4.

## Decisões críticas

| ID | Interview | Spec | Plan | Estado |
|---|---|---|---|---|
| PROD-01 | vlog de viagens em 16:9 | mantém: vlog de viagens em 16:9 | T11 | consistente |
| SCOPE-01 | análise e edição linear ponta a ponta | mantém: análise e edição linear ponta a ponta | T11 | consistente |
| CLI-01 | CLI local, não interativa, com subcomandos e `--json` | mantém a decisão | T1 | consistente |
| DATA-01 | diretório local por trabalho, plano JSON, sem banco | mantém a decisão | T4, T7, T9 | consistente |
| MEDIA-01 | MP4 H.264/AAC, MP3/WAV, 60 min e 4 GB por fonte | mantém a decisão | T4, T5, T6, T7, T8, T10 | consistente |
| AI-01 | Whisper local, CPU baseline, GPU opcional | mantém a decisão | T2, T3, T5 | consistente |
| RENDER-01 | uma preview, aprovação por hash, full do mesmo plano | mantém a decisão | T8, T9, T10 | consistente |
| AUDIO-01 | trilhas independentes, volume explícito, loudness e limiter | mantém a decisão | T7, T8 | consistente |
| TEXT-01 | lettering estático por intervalo e posição nomeada | mantém a decisão | T7, T8 | consistente |
| PRIV-01 | processamento e artefatos locais, sem telemetria | mantém a decisão | T2, T3, T4, T5, T8, T11 | consistente |
| DEP-01 | pacote Python, ambiente virtual, instalador PowerShell | mantém a decisão | T1, T2 | consistente |
| OPS-01 | um trabalho por vez, limpeza manual | mantém a decisão | T4, T11 | consistente |
| TECH-01 | não havia decisão anterior | cria backend `faster-whisper`/CTranslate2 | T2, T3, T5 | consistente |
| AI-02 | não havia decisão anterior | cria recomendação local `base`/`small`/`tiny` | T3, T5 | consistente |
| CONTRACT-01 | não havia decisão anterior | cria envelopes, códigos e exit codes estáveis | T1, T4, T7, T9 | consistente |
| STATE-01 | não havia decisão anterior | cria hashes de plano, preview e fontes para `approve`/`full` | T9, T10 | consistente |

## Constituição

- Regra de validação de entradas e diretórios permitidos: workdir e destinos agora rejeitam reparse points, além das validações de fontes e limites já previstas.
- Regra de ferramentas de segurança: o plano aloca setup explícito para `pip-audit`; `gitleaks` foi validado no host e `chrome-devtools` é N/A porque o MVP não possui UI.
- Regras de processamento externo e privacidade: spec e plan mantêm execução local, subprocessos estruturados, sem shell intermediário, sem upload e sem telemetria.

## Métricas

- A*/C* na spec: 17, sendo A1-A12 e C1-C5.
- T* no plan: 11.
- Cobertura (A*/C* com >= 1 T*): 17/17.
- Qualidade de Testes: T1 possui smoke test real; comandos de teste estão definidos; `gitleaks 8.30.1` foi validado; `pip-audit` está alocado para setup explícito em T2; `chrome-devtools` é N/A.
- Achados corrigidos / resolvidos: F1, F2, F3, F4, F5, F6 e F7.
- Achados bloqueantes pendentes: 0.

## Veredito

limpo

## Handoff

vibe-implement
rota: max

- Chat: recomendo novo chat para `vibe-implement`; continuidade no mesmo chat só por escolha consciente. O `analyze.md` e os artefatos vivos são a ponte.

- [ ] Aprovação humana (leu o arquivo e confirmou)
