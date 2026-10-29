# Backlog do editor

Fonte única das lacunas e dos escopos candidatos do editor. Não há roadmap definido: a ordem,
o recorte e o início de cada item são decisões do usuário. Nenhum item aqui substitui
interview, spec ou plan, nem autoriza implementação.

O arquivo tem duas partes:

- **Escopos já definidos pelo usuário:** os temas que estavam reservados como phases 8 a 15 em
  `.vibeflow/roadmap.md` (removido em 2026-10-29), com as entregas, dependências e limites
  originais. Os complementos levantados depois estão separados em cada item.
- **Lacunas levantadas em 2026-10-29:** o que não estava previsto em nenhum escopo, com uma
  sugestão de prioridade.

Três referências guiaram o levantamento de 2026-10-29:

- o produto "Máquina de Edição IA" (edicaomia.com.br), que vende edição dirigida por Claude
  Code ou Codex com cortes, legendas, imagens, motion graphics explicativos e composições 3D;
- o pedido de um resultado cinematográfico, que depende sobretudo de cor, ritmo de velocidade,
  fades e desenho de som;
- a edição de podcast e a extração de cortes para Shorts e Reels, que dependem de fontes
  longas, várias câmeras e microfones, saber quem fala e gerar muitas saídas de uma só análise.

Cada item diz o que falta, por que importa e o caminho técnico provável. Item entregue sai
da lista.

## Escopos já definidos pelo usuário

A ordem abaixo é a ordem anterior das phases 8 a 15, mantida só como referência. As
dependências apontam para os itens deste arquivo. "Interface de revisão" é a phase 7, já
implementada. O marco pretendido ao fim do item E5 era montar, revisar, tratar áudio e cor,
ajustar ritmo, recuperar versões e verificar a entrega. Os itens E6 a E8 ampliavam acabamento
e produtividade.

### E1. Áudio editorial e fades (antiga phase 8)

- **Entregas:**
  - trilhas de música, voz e efeitos com posição própria na timeline, atravessando cortes de vídeo;
  - fade-in e fade-out configuráveis por trilha;
  - automação de volume por pontos de ganho ao longo do tempo;
  - crossfade entre trechos de áudio;
  - ducking configurável para reduzir música durante a fala, com controle de intensidade e
    tempos de resposta;
  - integração com a normalização e o limiter existentes;
  - resultado consistente no player, na preview e na full.
- **Dependências:** interface de revisão.
- **Orientação:** implementar primeiro trilhas contínuas, fades e curvas explícitas de ganho; o
  ducking se apoia nessa base.
- **Contexto técnico:** o campo `audio_processing.ducking` já existe no schema, mas a validação
  rejeita `true` ("não faz parte do contrato do MVP"). O caminho provável é `sidechaincompress`
  com a voz como sidechain. Este item também resolve o desenho de som posicionado (whoosh,
  impacto, ambiente num instante exato da timeline).

### E2. Cor e tratamento de HDR (antiga phase 9)

- **Entregas:**
  - identificação do espaço de cor e das características SDR/HDR das fontes;
  - conversão explícita de HDR para SDR com tone mapping;
  - ajustes por trecho de exposição, balanço de branco, contraste e saturação;
  - reutilização de um conjunto de ajustes em vários trechos;
  - tratamento consistente de fontes com características de cor diferentes na mesma montagem;
  - mesma interpretação de cor nos proxies, no player, na preview e na full;
  - validação com arquivos reais de câmera e celular.
- **Dependências:** interface de revisão. Independente do E1.
- **Fora do escopo inicial:** saída HDR, LUTs criativas e correspondência automática entre câmeras.
- **Complemento levantado em 2026-10-29: LUT de conversão de perfil log e LUT criativa.**
  Perfil log (DJI D-Log, por exemplo) é SDR, então não passa pelo tone mapping de HDR, e sai
  lavado sem a LUT oficial do fabricante. O look cinematográfico depende de LUT criativa.
  Caminho: `lut3d` com arquivos `.cube` locais, por fonte (conversão) e por trecho (look),
  validando o caminho com a mesma regra de diretórios permitidos das fontes. É o maior ganho
  isolado para um resultado cinematográfico.

### E3. Velocidade e congelamento de quadro (antiga phase 10)

- **Entregas:**
  - velocidade constante configurável por trecho;
  - aceleração e câmera lenta usando os quadros disponíveis na fonte;
  - congelamento de um quadro escolhido por uma duração definida;
  - política explícita para manter, adaptar ou silenciar o áudio dos trechos com velocidade alterada;
  - preservação de tom do áudio quando aplicável;
  - recálculo correto da duração da montagem e do mapeamento entre fonte e timeline;
  - sincronização de legendas, camadas, automação de áudio e seleção na interface.
- **Dependências:** interface de revisão e E1.
- **Fora do escopo inicial:** rampas de velocidade e criação de quadros por interpolação de movimento.
- **Complemento levantado em 2026-10-29: speed ramp.** Aceleração e desaceleração dentro do
  mesmo plano são recurso comum de vídeo de viagem e de ação. Caminho: velocidade por keyframes
  no segmento, com o mapeamento entre fonte e timeline calculado pela integral da curva. Câmera
  lenta suave exige fonte gravada em 50 ou 60 fps; interpolação artificial (`minterpolate`) é
  lenta e gera artefatos.

### E4. Histórico e revisão recuperável (antiga phase 11)

- **Entregas:**
  - salvamento de versões nomeadas da montagem;
  - histórico com identificação, data e descrição de cada revisão;
  - comparação entre versões, indicando alterações em cortes, áudio, textos, legendas e efeitos;
  - restauração de uma versão anterior como uma nova revisão;
  - identificação estável de segmentos, camadas e demais elementos editáveis;
  - anotações de revisão vinculadas a uma versão, elemento ou intervalo;
  - estados para anotações pendentes, resolvidas ou desatualizadas;
  - reavaliação da aprovação quando uma versão é restaurada ou alterada;
  - histórico sem duplicação dos vídeos originais.
- **Dependências:** interface de revisão e integração com o mapeamento temporal do E3.
- **Orientação:** versionar decisões editoriais e referências às fontes; a restauração
  preserva o histórico existente.
- **Relação com L2:** versões são revisões de uma mesma montagem, não saídas paralelas. Se L2
  (várias saídas por análise) for escolhido, os dois modelos precisam ser desenhados juntos.

### E5. Controle de qualidade da entrega (antiga phase 12)

- **Entregas:**
  - comando de inspeção técnica da full;
  - decodificação completa para detectar arquivos truncados ou corrompidos;
  - conferência de duração, resolução, proporção, taxa de quadros, codecs e características de
    cor contra o contrato de saída;
  - medição do áudio final: loudness, picos e possíveis saturações;
  - detecção de candidatos a preto, congelamento ou silêncio inesperado;
  - comparação com o plano para reconhecer ocorrências intencionais;
  - relatório estruturado com códigos, gravidade e intervalos afetados;
  - separação entre violações objetivas e avisos que exigem julgamento editorial;
  - acesso aos trechos sinalizados pela interface de revisão;
  - vinculação do relatório à versão da montagem e ao arquivo final analisado.
- **Dependências:** E1, E2, E3 e E4.
- **Orientação:** não corrigir automaticamente suspeitas editoriais: preto, silêncio e imagem
  parada podem ser intencionais.

### E6. Acabamento visual e legendas (antiga phase 13)

- **Entregas:**
  - transição dissolve entre segmentos;
  - fade de entrada e saída para preto;
  - controle de opacidade das camadas;
  - entrada e saída suave de textos, imagens e vídeos sobrepostos;
  - regras explícitas de duração e sobreposição das transições, incluindo impacto no áudio;
  - caixa de fundo configurável para textos e legendas;
  - quebra de linha baseada nas medidas reais da fonte;
  - validação de texto que ultrapassa a caixa disponível;
  - exportação de legendas SRT e VTT com os tempos da montagem final;
  - consistência dos efeitos e das legendas entre player, preview e full.
- **Dependências:** E3 e E4, com integração ao controle de qualidade do E5.
- **Fora do escopo inicial:** catálogo extenso de transições e animações complexas.
- **Complementos levantados em 2026-10-29:**
  - **Movimento e escala animados nas camadas.** Letreiro que desliza ou cresce é padrão em
    anúncio e Reels, e a barra de progresso é comum em corte de podcast. Caminho: reaproveitar
    o modelo de keyframes do `reframe` nas camadas (`x`, `y` e escala, com `ease` ou `linear`).
  - **Espaçamento entre letras.** O letreiro de cinema usa letras espaçadas, e o `drawtext` não
    tem esse controle. Caminho: avaliar renderizar o letreiro como imagem com alfa antes do grafo.
  - **Destaque da palavra falada nas legendas (karaokê), estilo por item e cor por pessoa.** É
    o padrão de Reels e Shorts e praticamente obrigatório em corte de podcast. Caminho: usar os
    tempos de `words[]` que a transcrição já publica. Cor por pessoa depende de L3.

### E7. Preparação de fontes e voz (antiga phase 14)

- **Entregas:**
  - diagnóstico estruturado das incompatibilidades de cada fonte;
  - seleção explícita das trilhas de vídeo e áudio a utilizar;
  - conversão controlada para cópias compatíveis com o editor;
  - preservação dos originais e registro da origem e dos parâmetros de cada derivado;
  - reutilização de derivados válidos, evitando conversões repetidas;
  - redução de ruído configurável;
  - filtro de graves indesejados e equalização básica da voz;
  - comparação auditiva entre áudio original e tratado;
  - avaliação dos limites de tamanho e duração com arquivos reais;
  - ampliação dos limites comprovadamente suportados, mantendo controles de recursos e falhas seguras.
- **Dependências:** E1, E2 e E5.
- **Orientação:** ampliar limites somente com provas em arquivos longos e grandes; preservar as
  fontes originais.
- **Complementos levantados em 2026-10-29:**
  - **Os limites atuais bloqueiam podcast.** `media.py` limita cada fonte a 1 hora
    (`MAX_SOURCE_DURATION_SECONDS = 3_600`) e a 4 GiB (`MAX_SOURCE_SIZE_BYTES`), e a timeline
    herda 1 hora (`MAX_TIMELINE_DURATION_SECONDS`). Um episódio de 1h30 é recusado, e uma
    gravação em 4K de 40 a 60 minutos passa de 4 GiB. Não verificado: o que esses limites
    protegem (memória da transcrição, tempo de render, interface, proxies).
  - **Compressor e noise gate por microfone.** Em podcast cada microfone precisa de ajuste
    próprio, e o vazamento de um microfone no outro sem gate deixa o som com eco. Caminho:
    `acompressor` e `agate` por faixa, com presets fechados (voz falada, voz na rua) em vez de
    expor todos os parâmetros.

### E8. Busca e organização de material (antiga phase 15)

- **Entregas:**
  - busca textual nas transcrições de todas as fontes do trabalho;
  - resultados com contexto, identificação da fonte e timestamps;
  - navegação direta do resultado para o trecho no player;
  - geração de folhas de contato com imagens e tempos das fontes;
  - detecção de candidatos a mudanças de cena;
  - marcadores e anotações para organizar o material;
  - seleção e agrupamento de trechos candidatos à montagem;
  - indicação de quais trechos já estão usados e onde aparecem;
  - saída estruturada para o agente reutilizar os resultados na edição;
  - atualização ou invalidação dos índices quando fontes e análises mudarem.
- **Dependências:** interface de revisão e E4, com integração aos derivados do E7.
- **Fora do escopo inicial:** escolha automática das melhores tomadas e montagem editorial autônoma.
- **Relação com o levantamento de 2026-10-29:** a saída estruturada para o agente cobre a
  leitura enxuta da transcrição para escolher trechos de podcast sem gastar tokens com o JSON
  por palavra.

## Lacunas levantadas em 2026-10-29

A prioridade é sugestão, não decisão.

### Prioridade alta

#### L1. Impedir que a skill instalada fique desatualizada de novo

- **Situação:** a cópia em `~/.claude/skills/vibe-editor/` ficou para trás de
  `skills/vibe-editor/` (ainda dizia "texto sem sombra" e "saída sempre 16:9"). Ela foi
  sincronizada à mão em 2026-10-29, mas nada impede que isso se repita.
- **Impacto:** o agente subestima a ferramenta e orienta o usuário com base em limites que
  não existem mais.
- **Caminho:** um passo de instalação que copie `skills/vibe-editor/` para o diretório de
  skills do agente, ou um link simbólico. Verificar também a cópia usada pelo Codex.

#### L2. Várias saídas a partir de uma só análise

- **Situação:** um workdir gera uma preview e uma full. Extrair 10 cortes de um episódio vira
  10 workdirs, e a transcrição, a análise de áudio e os proxies são refeitos em cada um, a menos
  que se copiem os arquivos à mão.
- **Impacto:** é o fluxo central de cortes de podcast. Transcrever uma hora de áudio custa
  minutos, e repetir isso por corte inviabiliza o uso. O mesmo vale para variações de anúncio
  (gancho, proporção ou CTA diferentes a partir da mesma base).
- **Caminho:** vários planos num mesmo workdir, cada um com sua preview, aprovação e full, e
  `analysis/` e `proxy/` compartilhados. A aprovação continua presa ao hash de cada plano e
  das fontes. Exige rever a publicação atômica e os nomes únicos `preview.mp4` e `full.mp4`, e
  desenhar junto com o histórico do E4.

### Prioridade média

#### L3. Identificar quem fala (diarização)

- **Situação:** a transcrição tem palavras com tempo, mas não separa as vozes.
- **Impacto:** sem isso, trocar a câmera para quem está falando, colorir a legenda por pessoa
  ou mostrar o nome de quem fala é trabalho manual, trecho a trecho, num episódio de uma hora.
- **Caminho:** uma dependência local de diarização (pyannote ou similar, com modelo local e sem
  envio externo) que publique `speaker` por palavra ou por trecho junto da transcrição. Quando
  cada pessoa tem microfone próprio, a alternativa barata é comparar a energia por faixa, sem
  modelo nenhum. Integra com a busca do E8.

#### L4. Sincronizar câmeras e microfones pela forma de onda

- **Situação:** dá para alinhar fontes ajustando `in_s` em cada uma, mas o deslocamento entre
  elas precisa ser descoberto à mão.
- **Impacto:** podcast costuma ter 2 ou 3 câmeras e um gravador de áudio separado. Com
  sincronização e diarização (L3), o agente monta a multicâmera sozinho; sem elas, são
  centenas de segmentos escritos à mão.
- **Caminho:** comando de análise somente leitura que calcule o deslocamento de cada fonte em
  relação a uma referência por correlação cruzada do áudio, e publique o resultado com a
  confiança da medida. A decisão de usar o deslocamento fica no plano.

#### L5. Reenquadramento que segue o rosto

- **Situação:** o `reframe` com keyframes existe e é bom, mas os keyframes são escritos à mão.
- **Impacto:** passar um podcast de 16:9 para 9:16 exige acompanhar o rosto de quem fala.
  Fazer isso à mão em cada corte é o que mais toma tempo nesse fluxo.
- **Caminho:** comando de análise somente leitura que detecte rostos por frame amostrado e
  sugira keyframes de `reframe` (limitados a 32 por segmento, `MAX_REFRAME_KEYFRAMES`).
  Exige uma dependência local de detecção facial. Cruzar com a diarização (L3) para
  enquadrar quem fala quando houver duas pessoas no mesmo plano.

#### L6. Sugestão de cortes de silêncio e vícios de fala

- **Situação:** a CLI entrega transcrição por palavra e silêncio por amplitude, mas não propõe
  cortes. O E5 detecta silêncio só na full pronta, e o E8 deixa a escolha automática de tomadas
  fora.
- **Impacto:** "remover silêncio e enxugar" é a tarefa mais repetida e hoje custa muitas
  chamadas e tokens por vídeo.
- **Caminho:** um comando somente leitura no estilo de `captions suggest`, que devolve
  intervalos candidatos (pausa maior que X segundos, "é...", "tipo", repetições) com as palavras
  das bordas. A decisão continua sendo do agente e do usuário, como a skill exige; por isso não
  conflita com o limite do E8.

#### L7. Camada de vídeo com transparência (alfa)

- **Situação:** as allowlists de mídia aceitam H.264 e HEVC em `yuv420p`, sem canal alfa.
- **Impacto:** impede usar motion graphics gerados por fora (Remotion, After Effects, pacotes
  de assets, overlays de light leak e película) como elemento flutuando sobre o vídeo. Hoje
  eles entram como retângulo opaco.
- **Caminho:** aceitar ProRes 4444 e WebM VP9 com `yuva420p` como fontes de camada `video` e
  preservar o alfa no `overlay`. Isso destrava o L8 sem acoplar o editor a um renderizador.

#### L8. Motion graphics explicativos

- **Situação:** não existem (gráficos, setas, contadores, listas animadas, lower thirds).
- **Impacto:** é o diferencial que o produto de referência vende para conteúdo explicativo
  e anúncio de oferta.
- **Caminho recomendado:** gerar fora do editor (Remotion ou HTML renderizado) e importar como
  camada com alfa (L7). Um renderizador embutido acopla Node e Chromium ao pipeline local
  e só se justifica se o uso for frequente.

#### L9. Preset de letterbox 2.39:1

- **Situação:** não há proporção scope. Dá para improvisar com duas camadas de imagem preta,
  sem garantia de que legendas e letreiros respeitem a área útil.
- **Impacto:** as faixas pretas são o sinal visual mais imediato de "cinema" no YouTube.
- **Caminho:** opção de máscara no `profile` (sobre o 16:9) aplicada com `drawbox` ou `pad`
  depois da composição, com as caixas padrão de legenda recalculadas para a área visível.

### Prioridade baixa

#### L10. Grão e vinheta

- **Situação:** não há textura de imagem.
- **Impacto:** grão leve e vinheta suave dão acabamento de película, e o grão ainda mascara
  banding em céu e em gradientes.
- **Caminho:** `noise` (com semente fixa para manter o render determinístico) e `vignette`,
  globais no plano, com intensidades limitadas. Pode entrar junto do E2.

#### L11. Capítulos para o YouTube

- **Situação:** não há exportação de capítulos. Os marcadores do E8 organizam o material das
  fontes, não a timeline final.
- **Impacto:** episódio longo sem capítulos tem retenção e navegação piores no YouTube.
- **Caminho:** lista de tempos e títulos no plano, convertida para tempos de timeline e
  exportada como texto pronto para a descrição, no mesmo molde do SRT do E6.

#### L12. Layouts de tela dividida

- **Situação:** já dá para montar com camadas `video` posicionadas, mas as caixas de um layout
  de 2 ou 3 pessoas são calculadas à mão em cada proporção.
- **Impacto:** é um enquadramento comum em podcast, principalmente no 9:16 com uma pessoa em
  cima e outra embaixo.
- **Caminho:** documentar na skill as caixas normalizadas dos layouts comuns por proporção,
  antes de criar qualquer campo novo no plano.

#### L13. Taxa de quadros da saída

- **Situação:** a saída segue a taxa da fonte, e as camadas usam uma grade fixa de 25 fps
  (`render/ffmpeg_graph.py`). Não há como pedir 24 fps. O E5 confere a taxa, mas não a define.
- **Impacto:** 24 fps é a cadência do cinema. O ganho só é real se a fonte foi gravada em
  24 fps ou num múltiplo; converter 30 para 24 descarta quadros e gera trepidação.
- **Caminho:** campo opcional de fps no `profile`, com a grade das camadas derivada dele em vez
  da constante de 25 fps. Avisar na validação quando a conversão não for por múltiplo inteiro.

#### L14. Corte no ritmo da música

- **Situação:** não há detecção de batida.
- **Impacto:** montagem cortada no tempo da trilha é recurso comum em trailer e vlog.
- **Caminho:** comando de análise somente leitura que publique os tempos de batida da música
  (o `analyze audio` atual mede nível, não ritmo). Exige avaliar uma dependência local de
  análise de áudio; o agente usa os tempos para propor cortes.

#### L15. Estabilização

- **Situação:** não há.
- **Impacto:** imagem de celular e câmera de ação em movimento.
- **Caminho:** `vidstabdetect` e `vidstabtransform`, que exigem um FFmpeg compilado com
  libvidstab. Confirmar no `capabilities` antes de expor.

## Fora do escopo por decisão

- Composição 3D, perspectiva e desfoque de profundidade simulado: custo alto e uso raro. Quando
  necessário, gerar por fora e importar como camada com alfa (L7).
- Geração de imagem ou vídeo por IA: depende de serviço remoto, o que contraria o princípio
  local da skill. Assets gerados por fora já entram como camada `image` ou `video`.
- Catálogo extenso de transições (wipes e similares): já estava fora do escopo inicial do E6.
