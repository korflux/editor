# Interface de revisão no navegador com player ao vivo
# Status: confirmado

## Solicitação

Uma interface parecida com a do Premiere para revisar a edição feita pela IA:

1. Timeline visual e preview do que está editado.
2. Alteração em tempo real conforme a IA altera o plano.
3. Seletor de "trecho" com botão de copiar, para escrever o prompt para a IA.
4. Visualizador do "trecho" inteiro: quando a IA junta 6 vídeos em 1, ver cada vídeo inteiro para conferir se a IA não tirou nada errado.

Isso deve dispensar a renderização da preview e facilitar o trabalho como um todo. O preview deve ser ao vivo, com seleção de resolução Full, 1/2, 1/3 ou 1/4 para não pesar.

## Hipótese inicial

```text
HIPÓTESE: interface de revisão no navegador com player ao vivo pelo grafo FFmpeg real, timeline, visualizador de fontes, seletor de trecho e aprovação sem preview.mp4
CONFIDENCE: ~85%, falta: mecânica da aprovação
```

## Trilha

### 1

ANOTEI: proposta inicial de "mesa de revisão" com player, timeline, seleção de regiões, observações e cópia de contexto para a IA. Riscos levantados: reproduzir sem renderizar exige reimplementar efeitos ou usar o grafo real; a aprovação vincula a `preview.mp4`; segmentos e camadas não têm identidade estável; regiões no quadro precisam ser convertidas para coordenadas da fonte.
Q: qual é o escopo real da interface?
GUESS: player sobre a `preview.mp4` renderizada, com timeline do `plan validate`.
R: corrigiu. O escopo são os quatro itens da Solicitação, sem renderizar preview.

### 2

ANOTEI: escopo reduzido a timeline e preview ao vivo, atualização em tempo real, seletor de trecho com cópia e visualizador da fonte inteira com trechos usados e descartados.
Q: como reproduzir ao vivo, compositor no navegador (A) ou FFmpeg ao vivo com o mesmo grafo da full (B)?
GUESS: B, pela fidelidade exata, reaproveitamento do grafo e leitura direta de HEVC e MOV, com seletor de resolução que reduz custo real; atraso no salto do cursor aceitável.
R: confirmou B, numa phase só, com seletor Full, 1/2, 1/3 e 1/4.

### 3

ANOTEI: B confirmada; a interface roda no navegador e a IA precisa abri-la.
Q: como a interface é aberta e qual a stack?
GUESS: `editor ui start --workdir ... --json` sobe servidor local em segundo plano e abre o navegador padrão; `editor ui stop` encerra; servidor na biblioteca padrão do Python, MP4 fragmentado por HTTP, HTML/CSS/JS puros sem build, timeline em canvas, scrub por quadros extraídos.
R: confirmou a stack e o atraso no salto do cursor; decidiu aprovar pelo player, sem preview, seguindo direto para a render full.

### 4

ANOTEI: aprovação pelo player confirmada; a página foi definida como só leitura, então a mecânica da aprovação estava em aberto.
Q: a aprovação é um botão na interface ou continua pela IA rodando `editor approve`?
GUESS: botão "Aprovar esta versão" como única ação de escrita da página, vinculando o hash do plano carregado e os das fontes e recusando se o plano mudou depois do carregamento; `editor approve` segue na CLI sem exigir `preview.mp4`; `render preview` vira exportação opcional; a full continua pedida à IA.
R: confirmou o guess.

## Resultado

Entendi assim:

- O quê: interface de revisão no navegador, aberta por `editor ui start --workdir ... --json` e encerrada por `editor ui stop` (uma instância por trabalho), com player ao vivo em que o servidor local roda o mesmo grafo FFmpeg da full em Full, 1/2, 1/3 ou 1/4 e transmite MP4 fragmentado; play, pausa, quadro a quadro, salto de corte e scrub por quadros extraídos sob demanda; timeline em canvas com vídeo, camadas, áudio e legendas, só para navegar e selecionar; recarga automática quando o `plan.json` muda, mantendo o cursor; visualizador da fonte inteira com trechos usados e descartados; seletor de trecho com "Copiar para IA" (tempo da montagem, fonte, tempo na fonte e hash do plano); botão "Aprovar esta versão".
- Pra quem: o operador humano revisando a montagem que a IA cria e altera por CLI, na mesma máquina.
- Por quê: eliminar a renderização de preview a cada rodada e o cálculo manual de timestamps, e permitir conferir se a IA cortou algo errado.
- Sucesso: revisar um episódio inteiro, comunicar as alterações à IA e conferir o resultado sem renderizar preview; aprovar pelo player e pedir a full.
- Limite: fidelidade exata pelo mesmo grafo da full, com atraso aceito ao saltar o cursor; servidor só em `127.0.0.1`, token por sessão, checagem de `Host`, só arquivos do workdir; página só leitura exceto o botão de aprovação, que recusa se o plano mudou desde o carregamento; stack na biblioteca padrão do Python e HTML/CSS/JS puros sem build; a aprovação deixa de vincular a `preview.mp4` (mudança major de contrato, versionada); `editor approve` segue na CLI sem exigir preview e `render preview` vira exportação opcional; a primeira tarefa é a prova técnica do player ao vivo.
- Fora: edição por arraste, efeitos novos, colaboração, chat integrado, compositor no navegador e disparo da full pela interface.
- Visual: layout de editor estilo Premiere (player central, timeline embaixo, painel do trecho ao lado); detalhamento na `vibe-design`.

## Direção

| ID | Decisão | Motivo e impacto |
|---|---|---|
| UI-01 | Player ao vivo pelo grafo FFmpeg real (opção B), sem compositor no navegador | Fonte única dos efeitos; fidelidade exata com a full; resolução reduz custo real; salto do cursor com atraso aceito |
| UI-02 | Interface no navegador servida por `editor ui start/stop`, servidor local em segundo plano | A IA abre para o usuário via navegador padrão; requer IA e vídeo na mesma máquina |
| UI-03 | Stack: biblioteca padrão do Python, MP4 fragmentado por HTTP, HTML/CSS/JS sem build, timeline em canvas, scrub por quadros extraídos | Nenhuma dependência nova no lockfile nem toolchain Node |
| APPROVAL-01 | Aprovação por botão na interface, vinculando plano carregado e fontes, sem `preview.mp4` | Mudança major do contrato de `approval.json`; `editor approve` segue sem exigir preview |
| SEC-01 | Servidor só em `127.0.0.1`, token por sessão, checagem de `Host`, só arquivos do workdir, sem CORS | Única escrita da página é a aprovação |

Pontos levantados para a spec: identidade estável de segmentos e camadas para reassociar seleções depois de mudanças no plano, recorte do grafo a partir de um instante arbitrário da timeline (risco técnico da prova) e dimensões pares nas resoluções reduzidas.

## Handoff

vibe-spec

- Chat: `init → interview → spec → design` pode continuar no mesmo chat. Recomende novo chat ao iniciar `vibe-plan`; se o humano preferir, a continuidade não bloqueia. Use este arquivo vivo como ponte.
