# Backlog do editor

Lacunas do editor para edição de vídeo, ordenadas por impacto. Cada item diz o que falta,
por que importa e o caminho técnico provável. Itens entregues saem desta lista.

Três referências guiaram a comparação:

- o produto "Máquina de Edição IA" (edicaomia.com.br), que vende edição dirigida por Claude
  Code ou Codex com cortes, legendas, imagens, motion graphics explicativos e composições 3D;
- o pedido de um resultado cinematográfico, que depende sobretudo de cor, ritmo de velocidade,
  fades e desenho de som;
- a edição de podcast e a extração de cortes para Shorts e Reels, que dependem de fontes
  longas, várias câmeras e microfones, saber quem fala e gerar muitas saídas de uma só análise.

Registrado em 2026-10-29.

## Prioridade alta

### 1. Impedir que a skill instalada fique desatualizada de novo

- **Situação:** a cópia em `~/.claude/skills/vibe-editor/` ficou para trás de
  `skills/vibe-editor/` (ainda dizia "texto sem sombra" e "saída sempre 16:9"). Ela foi
  sincronizada à mão em 2026-10-29, mas nada impede que isso se repita.
- **Impacto:** o agente subestima a ferramenta e orienta o usuário com base em limites que
  não existem mais.
- **Caminho:** um passo de instalação que copie `skills/vibe-editor/` para o diretório de
  skills do agente, ou um link simbólico. Verificar também a cópia usada pelo Codex.

### 2. Limites de duração e tamanho das fontes

- **Situação:** `media.py` limita cada fonte a 1 hora (`MAX_SOURCE_DURATION_SECONDS = 3_600`) e
  a 4 GiB (`MAX_SOURCE_SIZE_BYTES`). A timeline herda o limite de 1 hora
  (`MAX_TIMELINE_DURATION_SECONDS` em `plan/validation.py`).
- **Impacto:** um episódio de podcast de 1h30 é recusado na entrada, e uma gravação em 4K de
  40 a 60 minutos já passa de 4 GiB. O podcast nem entra no editor.
- **Caminho:** primeiro descobrir o que esses limites protegem (memória da transcrição, tempo de
  render, interface de revisão, proxies); não verificado. Depois, subir os limites com base em
  medição (`scripts/bench_render.py`) ou documentar na skill que o episódio deve ser dividido
  antes, com o custo de cada opção.

### 3. Correção de cor e LUT

- **Situação:** não há ajuste de exposição, contraste, saturação, temperatura nem LUT.
- **Impacto:** é o maior ganho isolado para um resultado cinematográfico. Fontes de câmera em
  perfil log (DJI D-Log, por exemplo) saem lavadas sem uma LUT de conversão.
- **Caminho:** `lut3d` (arquivos `.cube` locais) e `eq` ou `colorbalance`, por fonte ou por
  segmento. Validar o caminho da LUT com a mesma regra de diretórios permitidos das fontes.

### 4. Fades e transições entre segmentos

- **Situação:** `cuts[]` aceita só J e L, que são transições de áudio. Não há transição visual,
  nem fade de ou para preto no início e no fim do vídeo.
- **Impacto:** abrir e fechar em fade e usar dissolve para passagem de tempo são a base da
  linguagem cinematográfica e de vlog (Sonni Viaja).
- **Caminho:** `fade` e `afade` nas pontas da timeline; `xfade` e `acrossfade` com um tipo novo
  em `cuts[]` (dissolve, fade para preto e um pequeno conjunto de wipes). A timeline linear
  precisa descontar a sobreposição na duração total.

### 5. Velocidade por segmento (câmera lenta e speed ramp)

- **Situação:** não há controle de velocidade. Cada segmento toca em 1x.
- **Impacto:** câmera lenta em momentos-chave e aceleração entre planos são recursos centrais
  de vídeo cinematográfico e de viagem.
- **Caminho:** campo de velocidade em `segments[].video` aplicado com `setpts` no vídeo e
  `atempo` no áudio (ou áudio mudo no trecho). A duração do segmento na timeline passa a ser
  `(out_s - in_s) / velocidade`, e as legendas ancoradas na fonte precisam seguir esse mapeamento.
  Speed ramp (velocidade variável dentro do segmento) é uma segunda etapa.
- **Limite:** câmera lenta suave exige fonte gravada em 50 ou 60 fps. Interpolação artificial
  (`minterpolate`) é lenta e gera artefatos; só expor se houver caso real.

### 6. Animação de entrada e saída das camadas (fade, slide, scale)

- **Situação:** camadas `text`, `image` e `video` aparecem e somem em corte seco, em posição fixa.
  Só o vídeo-base tem movimento (`reframe` com `ease`). O texto também não tem espaçamento
  entre letras.
- **Impacto:** é a maior diferença visual para anúncio e Reels. Um letreiro de cinema entra
  e sai em fade, com letras espaçadas. Também é pré-requisito da barra de progresso dos cortes
  de podcast.
- **Caminho:** reaproveitar o modelo de keyframes do `reframe` nas camadas (`x`, `y`, escala e
  opacidade por keyframe, com `ease` ou `linear`), no grafo do FFmpeg com expressões em
  `overlay` e `fade` com alfa, sem dependência nova. O espaçamento entre letras não existe no
  `drawtext`: avaliar renderizar o letreiro como imagem com alfa antes do grafo.

### 7. Várias saídas a partir de uma só análise

- **Situação:** um workdir gera uma preview e uma full. Extrair 10 cortes de um episódio vira
  10 workdirs, e a transcrição, a análise de áudio e os proxies são refeitos em cada um, a menos
  que se copiem os arquivos à mão.
- **Impacto:** é o fluxo central de cortes de podcast. Transcrever uma hora de áudio custa
  minutos, e repetir isso por corte inviabiliza o uso. O mesmo vale para variações de anúncio
  (gancho, proporção ou CTA diferentes a partir da mesma base).
- **Caminho:** vários planos num mesmo workdir, cada um com sua preview, aprovação e full, e
  `analysis/` e `proxy/` compartilhados. A aprovação continua presa ao hash de cada plano e
  das fontes. Exige rever a publicação atômica e os nomes únicos `preview.mp4` e `full.mp4`.

### 8. Ducking automático da música sob a voz

- **Situação:** o campo `audio_processing.ducking` existe, mas a validação rejeita `true`
  ("não faz parte do contrato do MVP").
- **Impacto:** sem ducking, a música compete com a fala ou exige ganho manual por trecho.
  É o ajuste de áudio mais pedido depois da normalização.
- **Caminho:** `sidechaincompress` do FFmpeg, com a faixa de voz como sidechain da música.
  Parâmetros mínimos: redução em dB, ataque e release.

## Prioridade média

### 9. Identificar quem fala (diarização)

- **Situação:** a transcrição tem palavras com tempo, mas não separa as vozes.
- **Impacto:** sem isso, trocar a câmera para quem está falando, colorir a legenda por pessoa
  ou mostrar o nome de quem fala é trabalho manual, trecho a trecho, num episódio de uma hora.
- **Caminho:** uma dependência local de diarização (pyannote ou similar, com modelo local e sem
  envio externo) que publique `speaker` por palavra ou por trecho junto da transcrição. Quando
  cada pessoa tem microfone próprio, a alternativa barata é comparar a energia por faixa, sem
  modelo nenhum.

### 10. Sincronizar câmeras e microfones pela forma de onda

- **Situação:** dá para alinhar fontes ajustando `in_s` em cada uma, mas o deslocamento entre
  elas precisa ser descoberto à mão.
- **Impacto:** podcast costuma ter 2 ou 3 câmeras e um gravador de áudio separado. Com
  sincronização e diarização (item 9), o agente monta a multicâmera sozinho; sem elas, são
  centenas de segmentos escritos à mão.
- **Caminho:** comando de análise somente leitura que calcule o deslocamento de cada fonte em
  relação a uma referência por correlação cruzada do áudio, e publique o resultado com a
  confiança da medida. A decisão de usar o deslocamento fica no plano.

### 11. Tratamento de voz por microfone

- **Situação:** o áudio tem normalização e limiter, aplicados à mistura. Não há compressor,
  noise gate, EQ nem redução de ruído, e nada é aplicado por faixa.
- **Impacto:** em podcast cada microfone precisa de ajuste próprio, e o vazamento de um
  microfone no outro sem gate deixa o som com eco. Em vlog gravado na rua, vento e ruído de
  fundo pedem limpeza.
- **Caminho:** processamento por faixa em `segments[].audio.tracks[]`: `acompressor`, `agate`,
  `highpass` e `equalizer`, e `afftdn` ou `arnndn` (com modelo local) para ruído. Começar com
  presets fechados (voz falada, voz na rua) em vez de expor todos os parâmetros.

### 12. Reenquadramento que segue o rosto

- **Situação:** o `reframe` com keyframes existe e é bom, mas os keyframes são escritos à mão.
- **Impacto:** passar um podcast de 16:9 para 9:16 exige acompanhar o rosto de quem fala.
  Fazer isso à mão em cada corte é o que mais toma tempo nesse fluxo.
- **Caminho:** comando de análise somente leitura que detecte rostos por frame amostrado e
  sugira keyframes de `reframe` (limitados a 32 por segmento, `MAX_REFRAME_KEYFRAMES`).
  Exige uma dependência local de detecção facial. Cruzar com a diarização (item 9) para
  enquadrar quem fala quando houver duas pessoas no mesmo plano.

### 13. Desenho de som: efeitos posicionados na timeline

- **Situação:** o plano aceita várias faixas de áudio com ganho e loop, mas elas ficam dentro
  dos segmentos. Não verificado: se é possível colocar um efeito sonoro (whoosh, impacto,
  ambiente) num instante exato da timeline, independente das bordas do segmento.
- **Impacto:** boa parte da sensação de cinema vem do som. Efeitos e ambiente sincronizados
  com transições e cortes são o que dá peso à montagem.
- **Caminho:** confirmar o comportamento atual em `plan/validation.py` e no grafo de áudio. Se
  não houver, criar camadas de áudio com `start_s` na timeline, no mesmo molde de `layers[]`,
  com `adelay` e `amix`.

### 14. Fundo e destaque de palavra nas legendas

- **Situação:** `captions` aceita sombra e contorno, mas a própria skill registra que não há
  fundo (caixa atrás do texto), estilo por item, animação nem karaokê.
- **Impacto:** legenda com caixa e palavra ativa destacada é o padrão de Reels e Shorts e
  praticamente obrigatória em corte de podcast. Também melhora a leitura sobre fundos claros.
- **Caminho:** caixa com `drawbox` ou `box=1` do `drawtext`, e destaque por palavra usando
  os tempos de `words[]` que a transcrição já publica. Cor por pessoa depende do item 9.

### 15. Preset de letterbox 2.39:1

- **Situação:** não há proporção scope. Dá para improvisar com duas camadas de imagem preta,
  sem garantia de que legendas e letreiros respeitem a área útil.
- **Impacto:** as faixas pretas são o sinal visual mais imediato de "cinema" no YouTube.
- **Caminho:** opção de máscara no `profile` (sobre o 16:9) aplicada com `drawbox` ou `pad`
  depois da composição, com as caixas padrão de legenda recalculadas para a área visível.

### 16. Grão e vinheta

- **Situação:** não há textura de imagem.
- **Impacto:** grão leve e vinheta suave dão acabamento de película, e o grão ainda mascara
  banding em céu e em gradientes.
- **Caminho:** `noise` (com semente fixa para manter o render determinístico) e `vignette`,
  globais no plano, com intensidades limitadas.

### 17. Camada de vídeo com transparência (alfa)

- **Situação:** as allowlists de mídia aceitam H.264 e HEVC em `yuv420p`, sem canal alfa.
- **Impacto:** impede usar motion graphics gerados por fora (Remotion, After Effects, pacotes
  de assets, overlays de light leak e película) como elemento flutuando sobre o vídeo. Hoje
  eles entram como retângulo opaco.
- **Caminho:** aceitar ProRes 4444 e WebM VP9 com `yuva420p` como fontes de camada `video` e
  preservar o alfa no `overlay`. Isso destrava o item 18 sem acoplar o editor a um renderizador.

### 18. Motion graphics explicativos

- **Situação:** não existem (gráficos, setas, contadores, listas animadas, lower thirds).
- **Impacto:** é o diferencial que o produto de referência vende para conteúdo explicativo
  e anúncio de oferta.
- **Caminho recomendado:** gerar fora do editor (Remotion ou HTML renderizado) e importar como
  camada com alfa (item 17). Um renderizador embutido acopla Node e Chromium ao pipeline local
  e só se justifica se o uso for frequente.

### 19. Sugestão de cortes de silêncio e vícios de fala

- **Situação:** a CLI entrega transcrição por palavra e silêncio por amplitude, mas não propõe
  cortes. O agente monta tudo à mão.
- **Impacto:** "remover silêncio e enxugar" é a tarefa mais repetida e hoje custa muitas
  chamadas e tokens por vídeo.
- **Caminho:** um comando somente leitura no estilo de `captions suggest`, que devolve
  intervalos candidatos (pausa maior que X segundos, "é...", "tipo", repetições) com as palavras
  das bordas. A decisão continua sendo do agente e do usuário, como a skill exige.

### 20. Leitura enxuta da transcrição para escolher trechos

- **Situação:** o agente já consegue achar ganchos e frases de efeito lendo a transcrição, mas
  o JSON publicado traz cada palavra com tempo e probabilidade.
- **Impacto:** num episódio de uma hora, ler a transcrição inteira nesse formato gasta muitos
  tokens só para escolher os cortes.
- **Caminho:** comando somente leitura que devolva texto por frase com tempo de início e fim
  (e `speaker`, quando o item 9 existir), filtrável por intervalo. Pode reaproveitar a quebra
  em frases de `analysis/captions.py`.

## Prioridade baixa

### 21. Exportar SRT e VTT

- **Situação:** as legendas só saem gravadas na imagem.
- **Impacto:** o YouTube indexa legendas enviadas como arquivo, e a acessibilidade melhora
  com legenda desligável.
- **Caminho:** serializar as ocorrências de `captions` já calculadas no `plan validate` em
  tempos de timeline, sem análise nova.

### 22. Capítulos para o YouTube

- **Situação:** não há exportação de capítulos.
- **Impacto:** episódio longo sem capítulos tem retenção e navegação piores no YouTube.
- **Caminho:** lista de tempos e títulos no plano, convertida para tempos de timeline e
  exportada como texto pronto para a descrição. Barato, porque a transcrição já existe e o
  agente propõe os títulos.

### 23. Layouts de tela dividida

- **Situação:** já dá para montar com camadas `video` posicionadas, mas as caixas de um layout
  de 2 ou 3 pessoas são calculadas à mão em cada proporção.
- **Impacto:** é um enquadramento comum em podcast, principalmente no 9:16 com uma pessoa em
  cima e outra embaixo.
- **Caminho:** documentar na skill as caixas normalizadas dos layouts comuns por proporção,
  antes de criar qualquer campo novo no plano.

### 24. Taxa de quadros da saída

- **Situação:** a saída segue a taxa da fonte, e as camadas usam uma grade fixa de 25 fps
  (`render/ffmpeg_graph.py`). Não há como pedir 24 fps.
- **Impacto:** 24 fps é a cadência do cinema. O ganho só é real se a fonte foi gravada em
  24 fps ou num múltiplo; converter 30 para 24 descarta quadros e gera trepidação.
- **Caminho:** campo opcional de fps no `profile`, com a grade das camadas derivada dele em vez
  da constante de 25 fps. Avisar na validação quando a conversão não for por múltiplo inteiro.

### 25. Corte no ritmo da música

- **Situação:** não há detecção de batida.
- **Impacto:** montagem cortada no tempo da trilha é recurso comum em trailer e vlog.
- **Caminho:** comando de análise somente leitura que publique os tempos de batida da música
  (o `analyze audio` atual mede nível, não ritmo). Exige avaliar uma dependência local de
  análise de áudio; o agente usa os tempos para propor cortes.

### 26. Estabilização

- **Situação:** não há.
- **Impacto:** imagem de celular e câmera de ação em movimento.
- **Caminho:** `vidstabdetect` e `vidstabtransform`, que exigem um FFmpeg compilado com
  libvidstab. Confirmar no `capabilities` antes de expor.

## Fora do escopo por decisão

- Composição 3D, perspectiva e desfoque de profundidade simulado: custo alto e uso raro. Quando
  necessário, gerar por fora e importar como camada com alfa (item 17).
- Geração de imagem ou vídeo por IA: depende de serviço remoto, o que contraria o princípio
  local da skill. Assets gerados por fora já entram como camada `image` ou `video`.
