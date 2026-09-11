# editor

## Solicitação

> Esse projeto ainda não tem nome, ele será um "editor de vídeo", meu objetivo com esse projeto é criar "ferramentas" que sejam acessíveis por um agente de ia via CLI, como Codex, Grok Build e afins.
>
> Quero começar criando ferramentas para a IA entender o vídeo:
>
> 1. Realizar uma transcrição do áudio via whisper local, assim não tem gasto de token.
> 2. Dar a capacidade de ver os decibeis da onda de áudio, para entender a onde tem fala e a onde não tem, o que é barulho e afins.
> 3. Dar a capacidade de tirar "prints" do vídeo, para interpretar situações sem áudio e tomar decisões.
>
> Depois, a IA precisa ser capaz de editar o vídeo:
>
> 1. Precisamos de uma ferramenta de "Juntar" e "cortar" o vídeo, possivelmente com ffmpeg.
> 2. Precisamos manipular o áudio junto ou separadamente do vídeo, como remover o áudio de uma parte, colocar uma música, adicionar uma música de fundo mantendo o áudio original, ou colocar um vídeo sobre o áudio.
>
> Por enquanto isso.

## Hipótese inicial

```text
HIPÓTESE: o MVP será um conjunto de ferramentas CLI sem painel, usado por agentes de IA para analisar arquivos de vídeo e executar uma edição básica baseada nessa análise.
CONFIDENCE: ~70%, faltam o resultado mínimo que define o MVP, o contrato de execução da CLI, os formatos e limites de mídia e a prioridade entre análise e edição.
```

## Trilha

### 1

Q: Qual resultado precisa estar funcionando para considerarmos este MVP bem-sucedido: o agente apenas analisa um vídeo e recebe dados utilizáveis, ou ele também precisa, nesta primeira versão, transformar essa análise em um vídeo editado e exportado?
GUESS: Recomendo que o MVP entregue análise e edição básica ponta a ponta, com as três capacidades de compreensão e um conjunto pequeno de cortes, junções e composição de áudio. Isso valida o valor central do produto, que é permitir que um agente tome decisões sobre um vídeo e execute a alteração sem painel. O impacto é deixar efeitos avançados, legendas, transições complexas e edição não linear fora desta primeira versão.
R: Considero um MVP se o agente for capaz de ver o vídeo, interpretar corretamente, fazer os cortes solicitados, juntar os vídeos e exportar uma versão preview, e uma versão full quando a preview estiver aprovada. Não quero muita invenção de moda, vamos focar no mínimo necessário para todas as solicitações fazerem sentido.

### 2

Q: Confirmamos que o agente externo será o responsável por interpretar transcrição, níveis de áudio e frames, enquanto a CLI fornece essas evidências e executa um plano de edição estruturado que o agente envia? Ou você espera que esta ferramenta também contenha outro modelo para interpretar o conteúdo e decidir os cortes?
GUESS: Recomendo que o agente externo seja o cérebro da operação e a CLI seja determinística. O MVP deve expor análise local e execução de edição, sem adicionar outro LLM ou modelo de visão. O impacto é reduzir custo, dependências e complexidade, além de tornar o contrato entre agente e ferramenta testável.
R: Não quero um "agente" separado. Quero as ferramentas que o CLI pode usar. O CLI é o agente, que pode ser, por exemplo, uma conversa de chat como esta.

### 3

Q: Para o MVP, confirmamos que o contrato principal será uma CLI local com comandos explícitos para analisar e editar arquivos, sem um loop conversacional ou modelo próprio dentro do projeto? Você quer também uma integração direta com um protocolo de ferramentas, como MCP, já nesta primeira versão, ou basta que qualquer host de IA consiga chamar os comandos pelo terminal?
GUESS: Recomendo usar a CLI executável como contrato primário e deixar adaptadores como MCP para depois, salvo se o host escolhido exigir essa integração para chamar ferramentas. Assim, a lógica de mídia fica independente da conversa e pode ser usada por Codex, Grok Build ou qualquer outro ambiente com acesso ao terminal.
R: CLI como contrato principal.

### 4

Q: Quais operações de edição são obrigatórias no MVP? Minha proposta mínima é: cortar trechos por timestamp, ordenar e juntar clipes, manter ou remover o áudio de cada trecho, substituir o áudio de um trecho e adicionar uma música de fundo com volume controlável. A ideia de colocar um vídeo sobre outro áudio significa substituir a imagem mantendo o áudio, como em b-roll sobre uma narração, ou deve ficar fora desta primeira versão?
GUESS: Recomendo começar com uma timeline linear de vídeo e áudio, sem camadas visuais, transições, efeitos ou edição não linear. O MVP deve fazer cortes, junções e composição simples de áudio; sobreposição de vídeo entra apenas se for indispensável para o primeiro job, porque muda o modelo de timeline e aumenta a superfície de renderização.
R: Quando digo colocar um vídeo sobre o áudio, quero dizer que temos o vídeo A com o áudio A e colocamos o vídeo B sobre o áudio do vídeo A. Nesse trecho, a imagem deve vir do vídeo B e o áudio deve continuar vindo do vídeo A.

### 5

Q: Nesse caso, o vídeo B deve substituir a imagem em tela cheia durante o trecho, como um cutaway ou b-roll, mantendo o áudio A e descartando ou silenciando o áudio B? Ou você também precisa de picture-in-picture, com o vídeo B aparecendo como uma janela sobre a imagem A?
GUESS: Recomendo suportar apenas a substituição da imagem em tela cheia no MVP. Isso cobre o caso de b-roll sobre narração usando duas trilhas independentes, sem introduzir posicionamento, escala, composição de camadas e novos parâmetros visuais. Picture-in-picture pode ser uma fase posterior.
R: Apenas substituição de tela cheia por enquanto. Também considero interessante uma ferramenta para colocar texto em uma posição da tela durante um intervalo específico, por exemplo, sempre que a transcrição indicar a palavra "valor", adicionar esse valor na tela com um lettering.

### 6

Q: Essa capacidade de lettering deve entrar como requisito do MVP? Minha proposta mínima é uma ferramenta que receba texto, início, fim, posição e um estilo básico, enquanto a conversa de IA usa a transcrição para descobrir os timestamps e decidir o conteúdo. A ferramenta não faria interpretação semântica nem tentaria aplicar automaticamente a regra "sempre que falar valor".
GUESS: Recomendo incluir o overlay de texto básico no MVP, porque ele cobre o caso de uso informado sem exigir outro modelo de IA. Manter o acionamento semântico no agente conversacional e o render determinístico na CLI evita criar uma automação difícil de testar. O impacto é limitar inicialmente animações, templates, estilos complexos e reconhecimento automático de palavras dentro da própria ferramenta.
R: Sim, gostei.

### 7

Q: Como não haverá painel, confirmamos que a aprovação acontece na conversa de IA e que a CLI deve salvar um plano de edição estruturado, gerar a preview a partir dele e só gerar a versão full quando receber uma chamada explícita de aprovação? A versão full deve reutilizar exatamente esse plano, sem reanalisar ou reinterpretar os arquivos.
GUESS: Recomendo persistir um plano de edição versionado, contendo as fontes, cortes, ordem dos clipes, composição de áudio, letterings e configurações de renderização. A CLI pode expor operações separadas para preview, aprovação e render full. Isso torna o resultado reproduzível e permite conferir que a versão aprovada é a mesma que será exportada; o impacto é introduzir apenas um artefato JSON de projeto, sem banco ou painel.
R: Sim. Não quero exportar várias versões de preview, apenas uma. Também faz sentido ter uma pequena entrevista no começo para saber o que quero fazer e quais capacidades estão disponíveis.

### 8

Q: Se a preview for rejeitada, a CLI deve permitir alterar o plano e gerar novamente uma única preview, substituindo o arquivo anterior, ou o MVP deve permitir somente uma renderização de preview por trabalho?
GUESS: Recomendo permitir a re-renderização da mesma preview após a alteração do plano, sem criar versões ou variantes paralelas. Assim existe apenas um arquivo preview vigente, mas o fluxo continua iterável quando a primeira edição não atende ao pedido.
R: Concordo com o guess.

### 9

Q: A pequena entrevista inicial deve acontecer na interface de IA ou conversa que está coordenando o trabalho, usando uma ferramenta da CLI para consultar capacidades e limites, ou você quer que o próprio executável faça perguntas interativas no terminal?
GUESS: Recomendo que a conversa conduza a entrevista e que a CLI ofereça uma saída estruturada de capacidades, formatos e operações suportadas. A CLI deve permanecer não interativa para funcionar bem em automações e hosts diferentes; não é necessário criar um agente conversacional próprio.
R: Concordo com o guess.

### 10

Q: Em qual ambiente a CLI precisa funcionar no MVP: apenas Windows local, ou Windows, macOS e Linux? E quais formatos e limites de mídia devemos suportar primeiro, por exemplo MP4/MOV com vídeo H.264 e áudio AAC, com um limite de duração ou tamanho definido?
GUESS: Recomendo começar com execução local no Windows, que é o ambiente atual, e uma faixa pequena e explícita de formatos comuns, priorizando MP4 com H.264/AAC. A CLI deve rejeitar formatos ou arquivos acima dos limites declarados, em vez de tentar suportar qualquer mídia desde o início. Ampliar sistemas, codecs e limites depois fica condicionado a uma necessidade real.
R: Concordo com as recomendações de Windows e da mídia inicial.

### 11

Q: Para o limite operacional inicial, a proposta é aceitar arquivos de até 60 minutos e 4 GB por fonte, com rejeição segura acima disso. Essa faixa atende ao material que você pretende editar no MVP, ou os vídeos normalmente são maiores?
GUESS: Recomendo começar com 60 minutos e 4 GB por fonte. É uma restrição explícita e revisável que reduz consumo imprevisível de Whisper, renderização e espaço temporário, sem limitar a estrutura do produto. O impacto é processar um trabalho por vez e exigir que entradas maiores sejam divididas antes da análise.
R: Está bom, essa faixa atende.

### 12

Q: Você aceita que cada trabalho tenha um diretório local próprio, definido pela CLI, contendo o plano de edição estruturado, os resultados da análise, uma única `preview.mp4` e, após aprovação, uma `full.mp4`, sempre preservando os arquivos originais? A CLI pode receber o diretório de trabalho e o caminho dos arquivos como parâmetros, sem banco de dados.
GUESS: Recomendo um diretório de trabalho por edição e nenhum banco. Isso deixa todos os artefatos reproduzíveis e fáceis de inspecionar por uma conversa de IA ou por uma pessoa, além de evitar estado oculto. O impacto é exigir uma convenção simples de nomes e permitir que o usuário limpe trabalhos antigos manualmente.
R: Sim.

### 13

Q: Para a transcrição, podemos definir como saída principal um JSON com idioma detectado, texto, segmentos com início e fim e timestamps por palavra? Isso permite que a conversa localize termos como "valor" para criar cortes e letterings. O MVP deve priorizar apenas português ou precisa aceitar qualquer idioma que o modelo Whisper local consiga detectar?
GUESS: Recomendo JSON como formato canônico, sem gerar SRT ou outros formatos no MVP, e priorizar português do Brasil com opção de informar outro idioma quando necessário. Timestamps por palavra são importantes para o caso de uso de letterings; formatos de legenda podem ser adicionados quando houver uma solicitação concreta.
R: O Whisper naturalmente gera SRT, acredito que já venha de base, mas concordo com a definição do JSON como formato principal.

### 14

Q: Na ferramenta de áudio, você precisa de uma classificação explícita de cada trecho como fala, ruído ou silêncio, ou basta retornar a intensidade ao longo do tempo, intervalos de silêncio e dados que possam ser cruzados com os timestamps da transcrição? Decibéis isolados mostram volume, mas não distinguem fala de ruído.
GUESS: Recomendo retornar dados de loudness por intervalo, picos, média, silêncio detectado e os timestamps da transcrição como evidência de fala. Não incluiria um classificador de ruído no MVP, porque isso exigiria outro modelo ou heurística frágil; a conversa de IA pode combinar o texto reconhecido com o volume e os frames para decidir.
R: Concordo com o guess.

### 15

Q: Para os frames, você quer que a CLI aceite timestamps específicos, um intervalo regular, ou os dois? Por exemplo, o agente poderia pedir imagens nos segundos 12.5, 28 e 41, ou uma imagem a cada 5 segundos. O resultado pode incluir arquivos de imagem e um manifesto com o timestamp de cada frame.
GUESS: Recomendo suportar os dois modos, com timestamps explícitos como caminho principal e intervalo regular como atalho. O MVP não precisa detectar cenas automaticamente nem interpretar as imagens; deve extrair os frames solicitados, preservar a associação com o tempo e devolver caminhos que a conversa consiga visualizar.
R: Concordo.

### 16

Q: Para editar, você aceita um único plano de timeline linear em JSON, referenciando os arquivos originais e descrevendo segmentos de vídeo na ordem final, origem do áudio de cada trecho, remoção ou mixagem de músicas, substituições de imagem em tela cheia e overlays de texto? A CLI validaria esse plano e geraria a preview ou a full sem criar vídeos intermediários para cada operação.
GUESS: Recomendo uma timeline linear única. Ela representa o mínimo necessário para combinar cortes, junções, b-roll, áudio independente e letterings, evita perda de qualidade por renders intermediários e permite que preview e full usem exatamente a mesma edição. Transições, camadas visuais simultâneas e edição não linear ficam fora do MVP.
R: Atende.

### 17

Q: A preview deve ser uma renderização mais rápida e leve, limitada a 720 pixels na maior dimensão e mantendo a proporção do projeto, enquanto a full usa o perfil final definido para o trabalho? Para fontes com resoluções ou proporções diferentes, podemos normalizar todos os trechos para o perfil do primeiro vídeo da timeline.
GUESS: Recomendo preview limitada a 720 pixels na maior dimensão, com áudio suficiente para aprovação, e full no perfil final da timeline. Usar o primeiro vídeo como referência evita criar um sistema de presets prematuro; a CLI deve preservar a proporção e aplicar barras ou corte somente quando isso estiver explícito no plano.
R: Concordo.

### 18

Q: Para o áudio, o MVP deve aceitar o áudio embutido nos vídeos e arquivos externos comuns, como MP3 ou WAV, permitindo em cada trecho manter, silenciar, substituir ou mixar fontes com volume explícito? A música de fundo deve ter volume definido pelo plano, sem ducking automático durante a fala, ou esse ajuste automático é obrigatório?
GUESS: Recomendo volume explícito por fonte, corte ou loop da música para preencher o intervalo solicitado e nenhum ducking automático no MVP. A conversa de IA pode definir o volume com base na análise de loudness; assim evitamos uma regra automática difícil de prever, mantendo a composição reproduzível.
R: Concordo. Também acho que a ferramenta deve ser capaz de "equalizar".

### 19

Q: Quando você diz "equalizar", quer dizer normalizar e equilibrar o loudness entre voz, vídeos e música, ou aplicar equalização de frequências, como graves e agudos? São capacidades diferentes. Podemos incluir uma normalização simples de loudness no MVP e deixar um equalizador paramétrico para depois.
GUESS: Recomendo incluir normalização de loudness e um limiter básico para evitar variações e clipping na saída. Isso se conecta diretamente à análise de decibéis e ao objetivo de uma preview compreensível. Eu deixaria ajustes de frequência, presets de equalização e restauração de ruído fora do MVP, salvo se o problema principal for qualidade sonora e não apenas volume inconsistente.
R: Concordo com a guess.

### 20

Q: Para o lettering, basta definir posições nomeadas com margem segura, como topo-esquerda, topo-centro, centro, baixo-centro e baixo-direita, ou você precisa de coordenadas livres? A ferramenta também deve aceitar fonte, tamanho, cor e quebra de linha simples, sem animação ou estilo complexo?
GUESS: Recomendo posições nomeadas e previsíveis, com uma configuração básica de fonte, tamanho, cor, alinhamento e quebra de linha. Isso cobre letterings informativos sem introduzir editor visual, coordenadas difíceis de validar ou animações no MVP.
R: Concordo.

### 21

Q: A entrevista inicial pode começar com um comando `capabilities --json`, que informe operações disponíveis, formatos aceitos, limites de duração e tamanho, status e versões das dependências locais, regras de preview/full e capacidades de análise e edição? A conversa usaria esse resultado para fazer apenas as perguntas necessárias antes de montar o plano.
GUESS: Recomendo esse comando como primeira chamada. Ele mantém a conversa adaptável ao ambiente real, evita assumir que FFmpeg ou o modelo Whisper estão disponíveis e cria um contrato simples de descoberta sem implementar uma entrevista interativa na CLI.
R: Concordo.

### 22

Q: Você prefere uma única CLI, com subcomandos estáveis para `capabilities`, transcrição, análise de áudio, extração de frames, validação do plano, preview, aprovação e full, ou executáveis separados para cada ferramenta? O nome do executável pode ser provisoriamente `editor` até o produto receber um nome.
GUESS: Recomendo uma única CLI com subcomandos e uma opção de saída estruturada, como `--json`, em cada operação. Isso simplifica a descoberta pelo host de IA, centraliza validação, códigos de saída e tratamento de erros, sem impedir que a implementação separe os módulos internamente.
R: Concordo.

### 23

Q: Qual é o primeiro tipo de vídeo que o MVP deve resolver de ponta a ponta? Por exemplo, um vídeo falado de marketing com uma fonte principal, trechos de b-roll, música de fundo e letterings, ou outro formato que você realmente produz hoje? Precisamos de um caso concreto para validar se a análise e a edição têm valor antes de tentar cobrir qualquer estilo de vídeo.
GUESS: Recomendo escolher um único job representativo, como transformar uma gravação falada em uma versão editada com cortes, b-roll, áudio equilibrado e letterings. A CLI pode continuar geral, mas os testes e a definição de pronto devem começar por esse fluxo. Se o seu material principal for diferente, o caso real deve substituir este exemplo.
R: Será a edição de um vlog sobre viagens.

### 24

Q: Esse vlog será publicado inicialmente em qual formato: horizontal 16:9, vertical 9:16 ou outro? O MVP precisa escolher um perfil principal, mesmo que a timeline continue capaz de referenciar vídeos de proporções diferentes.
GUESS: Recomendo começar com horizontal 16:9, por ser o formato mais comum para vlog de viagens e por evitar adaptar o MVP para múltiplas plataformas ao mesmo tempo. O impacto é deixar presets verticais e exportação para Shorts ou Reels fora da primeira entrega, salvo se esse for o destino real do conteúdo.
R: 16:9.

### 25

Q: Para esse vlog, a conversa de IA deve receber o pedido em linguagem natural, consultar as análises e montar o plano, enquanto a CLI executa somente as operações explicitamente descritas no JSON? Você quer também comandos de alto nível que escolham cortes ou destaques automaticamente, como remover silêncios ou selecionar os melhores momentos?
GUESS: Recomendo não incluir decisão editorial automática no MVP. A conversa pode interpretar o pedido e decidir os cortes com base em transcrição, loudness e frames; a CLI deve permanecer composta por primitivas determinísticas e executar apenas o plano validado. Assim não precisamos definir heurísticas para “melhor momento” antes de ter exemplos reais.
R: Concordo.

### 26

Q: Confirmamos processamento 100% local no MVP: vídeos, áudios, frames, transcrições e planos não são enviados para serviços externos, e a execução do trabalho não depende de rede depois que FFmpeg e o modelo Whisper estiverem instalados? A rede seria usada apenas para instalação ou atualização explícita das dependências.
GUESS: Recomendo processamento local por padrão, sem upload e sem telemetria. Isso atende ao objetivo de não consumir tokens, preserva a privacidade do material de viagem e torna o comportamento previsível. O impacto é a CLI precisar informar claramente quando uma dependência ou modelo local estiver ausente, sem tentar substituí-los silenciosamente por uma API.
R: Sim.

### 27

Q: O MVP deve funcionar em CPU como baseline e usar GPU apenas quando disponível, com o modelo Whisper local configurável por parâmetro? Ou o ambiente de uso terá uma GPU NVIDIA obrigatória? Também precisamos decidir se começamos com um modelo padrão leve, priorizando tempo de resposta, ou um modelo maior, priorizando precisão.
GUESS: Recomendo CPU como baseline, GPU opcional e modelo configurável, com um padrão leve adequado para validar o fluxo. O comando `capabilities --json` deve informar dispositivo, modelo disponível e limitações. Isso mantém a instalação utilizável em diferentes máquinas Windows sem esconder o impacto de desempenho; um modelo maior pode ser escolhido explicitamente quando a precisão justificar o tempo.
R: Aceito a recomendação. A CLI deve verificar a configuração da máquina e fazer uma recomendação de dispositivo e modelo Whisper.

### 28

Q: A CLI deve inspecionar localmente sistema operacional, CPU, memória, GPU e memória de vídeo quando disponível, verificar FFmpeg e os modelos Whisper instalados e retornar uma recomendação de dispositivo e modelo em `capabilities --json`? Essa recomendação pode ser aplicada automaticamente como padrão, mas o agente deve poder sobrescrever modelo e dispositivo explicitamente.
GUESS: Recomendo usar a recomendação local como padrão e permitir override explícito. O resultado deve distinguir capacidade detectada, dependência ausente e recomendação estimada, sem alegar precisão garantida. Nenhuma informação de hardware deve sair da máquina.
R: Sim.

### 29

Q: Para instalar o MVP no Windows, você aceita uma distribuição inicial como pacote Python com ambiente virtual e um script de instalação PowerShell, que valide ou instale explicitamente FFmpeg, prepare o runtime Whisper e exponha o comando `editor`? Ou precisa que a primeira versão seja um executável único que não dependa de Python instalado?
GUESS: Recomendo começar com pacote Python e ambiente virtual reproduzível, com dependências pinadas e comandos separados para instalar dependências e modelos. Isso reduz a complexidade de empacotar Whisper e FFmpeg em um executável único. O instalador deve falhar de forma explícita quando uma dependência não estiver disponível e nunca baixar componentes durante uma edição sem autorização.
R: Concordo.

### 30

Q: Para liberar a full, a CLI deve exigir um comando explícito de aprovação que registre o hash do plano e da preview, e depois verificar que o plano não mudou antes de renderizar? Se o plano for alterado, a aprovação deve ser invalidada e uma nova única preview deve ser gerada.
GUESS: Recomendo um estado explícito de aprovação com hash. O fluxo fica `preview` → aprovação na conversa → `approve` → `full`, e a CLI bloqueia a full quando o plano ou as fontes relevantes mudarem. Isso implementa a exigência de aprovação sem painel e evita exportar uma edição diferente da preview aprovada.
R: Sim, concordo.

### 31

Q: Você pode descrever uma edição típica de vlog de viagens que o MVP precisa resolver? Por exemplo: usar uma gravação principal, cortar um trecho por timestamp, colocar um b-roll em tela cheia mantendo a narração, adicionar uma música de fundo com volume definido e inserir um lettering entre dois timestamps. Esse exemplo deve virar o primeiro cenário de aceitação da ferramenta.
GUESS: Recomendo um cenário que use pelo menos uma fonte principal, um b-roll, uma música e um lettering. Assim validamos em uma única jornada a análise, a timeline linear, o áudio independente, a substituição visual, o texto, a preview, a aprovação e a full.
R: Sim, o cenário proposto serve como primeiro cenário de aceitação.

### 32

Q: Em caso de falha durante análise ou renderização, confirmamos que a CLI nunca altera os arquivos originais, escreve saídas temporárias e só publica `preview.mp4` ou `full.mp4` após concluir com sucesso, preserva o plano e as análises para diagnóstico e retorna erro estruturado com código não zero?
GUESS: Recomendo esse comportamento como requisito de integridade. Um arquivo de saída parcial não pode ser confundido com uma preview ou full válida, e os artefatos de análise e plano devem permanecer disponíveis para repetir ou corrigir o trabalho. O impacto é exigir limpeza segura de temporários e publicação atômica dos resultados.
R: Concordo.

### 33

Q: A entrevista inicial deve perguntar somente o que estiver faltando para montar o plano, cobrindo no mínimo: resultado desejado, arquivos e papéis das fontes, duração ou cortes, formato 16:9, uso de b-roll, áudio e música, letterings, restrições e confirmação do fluxo preview/full? Ela deve pular perguntas que não se aplicarem às capacidades detectadas.
GUESS: Recomendo uma entrevista adaptativa e curta, guiada por capacidades. Para um vlog, ela começa pelo objetivo editorial e pelas fontes, pergunta apenas sobre áudio, b-roll ou lettering quando necessários e termina com um resumo que a conversa confirma antes de criar o plano. Isso reduz atrito sem esconder decisões importantes.
R: Sim.

## Resultado

Entendi assim:

- **O quê:** uma CLI local, provisoriamente chamada `editor`, que fornece ferramentas de análise e edição de vídeo por meio de um plano JSON em timeline linear.
- **Pra quem:** conversas e CLIs de IA com acesso ao terminal, operadas por uma pessoa. O projeto não terá agente próprio, painel ou modelo conversacional.
- **Por quê:** permitir editar um vlog de viagens usando análise local, sem upload, APIs externas ou custo de tokens para transcrição.
- **Sucesso:** consultar capacidades da máquina, entrevistar o usuário na conversa, analisar vídeo, áudio e frames, montar e validar um plano, gerar uma única preview, aprová-la por hash e exportar a full com o mesmo plano aprovado.
- **Limite:** Windows inicialmente, MP4 com H.264/AAC, MP3 ou WAV, até 60 minutos e 4 GB por fonte, 16:9, um trabalho por vez, CPU como baseline e GPU opcional.
- **Fora:** MCP, executável único sem Python, suporte amplo a codecs, formatos verticais, picture-in-picture, transições, efeitos avançados, edição não linear, decisão editorial automática, classificador de ruído, ducking automático, equalização paramétrica, remoção de ruído e templates complexos.
- **Visual:** nenhum painel. Preview limitada a 720 pixels na maior dimensão; full no perfil final da timeline. Letterings estáticos usam posições nomeadas, margem segura e estilo básico.

## Direção

A conversa de IA conduz uma entrevista curta e adaptativa, consulta `capabilities --json` e monta um plano explícito. A CLI não interpreta intenção editorial, não escolhe cortes sozinha e não contém outro modelo conversacional. Uma única CLI, provisoriamente `editor`, expõe subcomandos estáveis para capacidades, transcrição, análise de áudio, frames, validação, preview, aprovação e full, com saída estruturada via `--json`.

O plano JSON referencia fontes originais e descreve a timeline linear, segmentos de vídeo, fontes de áudio, b-roll em tela cheia, composição de música e letterings. A preview pode ser substituída quando o plano for corrigido. A full exige aprovação explícita e hash correspondente ao plano, à preview e às fontes relevantes.

## Cobertura do MVP

| Domínio | Estado | Evidência ou premissa |
|---|---|---|
| Produto | DECIDIDO | O primeiro job é editar um vlog de viagens para permitir análise e edição por ferramentas CLI. |
| Escopo | DECIDIDO | Análise local, timeline linear, cortes, junções, áudio independente, b-roll em tela cheia, loudness, limiter, letterings, preview e full. |
| Jornadas | DECIDIDO | `capabilities` → entrevista na conversa → análise → plano → preview → aprovação → full. |
| Telas | N/A | Não haverá painel ou interface visual do produto. |
| Acesso | N/A | Não haverá login, cadastro ou acesso remoto no MVP. |
| Usuários | ASSUMIDO | Um operador humano usa uma conversa ou CLI de IA local para coordenar cada trabalho. |
| Permissões | ASSUMIDO | A proteção necessária é validação de caminhos e acesso aos arquivos locais; não há papéis ou tenancy. |
| Dados | DECIDIDO | Plano, análises, aprovação e saídas vivem no diretório local do trabalho; não haverá banco. |
| Arquivos | DECIDIDO | MP4 H.264/AAC, MP3 e WAV, até 60 minutos e 4 GB por fonte, com originais preservados. |
| Integrações | DECIDIDO | FFmpeg e Whisper locais; nenhum serviço externo durante o processamento. |
| Notificações | N/A | O resultado é retornado pela CLI; não haverá e-mail, SMS ou push. |
| Visual | DECIDIDO | Saída principal 16:9, preview em até 720 pixels na maior dimensão e lettering básico. |
| Repositório | ASSUMIDO | O projeto continua no repositório Git atual; política de hospedagem e CI serão definidas antes da distribuição. |
| Stack | DECIDIDO | Pacote Python com ambiente virtual e instalador PowerShell; FFmpeg e Whisper locais. O backend exato e versões serão fechados na especificação. |
| Infraestrutura | N/A | Execução local no Windows, sem servidor, domínio, banco ou deploy. |
| Ambientes | DECIDIDO | Apenas ambiente local no MVP; GPU é opcional e a máquina será detectada. |
| Segurança | DECIDIDO | Entradas validadas, subprocessos sem shell montado, erros seguros, temporários isolados, publicação atômica e aprovação por hash. |
| Privacidade | DECIDIDO | Vídeos, áudios, frames, transcrições e planos não saem da máquina; sem telemetria. |
| Operação | ASSUMIDO | Um trabalho por vez, dependências e modelos instalados explicitamente, limpeza manual de trabalhos antigos e sem backup automático. |
| Métricas | N/A | Não haverá analytics no MVP. |
| Comercial | N/A | Não haverá cobrança, planos ou entitlement. |
| Conteúdo | N/A | O conteúdo é fornecido pelo operador e não será administrado pelo produto. |
| SEO e localização | N/A | Ferramenta local sem páginas públicas. |
| Legal | ASSUMIDO | Licenças de Python, FFmpeg, Whisper e modelos serão verificadas antes de uma distribuição além do ambiente local. |

## Mapa do produto

### Jornadas e telas

1. A conversa chama `capabilities --json` e recebe operações, formatos, limites, dependências, hardware detectado e recomendação de dispositivo/modelo.
2. A conversa conduz a entrevista inicial apenas sobre decisões que ainda faltam, identifica as fontes e seus papéis e confirma o resumo.
3. A CLI executa transcrição, análise de áudio e extração de frames. Cada comando retorna JSON e caminhos dos artefatos, enquanto imagens e vídeos ficam no diretório do trabalho.
4. A conversa interpreta os dados e cria um plano JSON com cortes, ordem dos clipes, áudio, b-roll e letterings. A CLI valida o plano antes de renderizar.
5. `preview` publica uma única `preview.mp4`. Se for rejeitada, o plano pode ser alterado e a mesma preview é substituída.
6. `approve` registra os hashes necessários. `full` bloqueia se o plano, a preview ou as fontes relevantes mudarem.

Estados mínimos: dependência ausente, modelo ausente, formato não suportado, limite excedido, caminho inválido, plano inválido, análise com erro e renderização com erro. Falhas retornam código não zero e erro estruturado, preservando plano e análises. Originais nunca são alterados, e saídas parciais não são publicadas.

### Dados e integrações

Cada trabalho terá um diretório local com o plano JSON, análises de transcrição e áudio, manifesto dos frames, imagens extraídas, uma `preview.mp4`, um registro de aprovação e uma `full.mp4` quando liberada. Os nomes finais podem ser refinados na especificação, mas não haverá estado oculto em banco.

A transcrição terá JSON como contrato canônico, com idioma, texto, segmentos e timestamps por palavra; SRT pode ser gerado como artefato secundário. A análise de áudio retornará loudness por intervalo, picos, média e silêncio. Frames serão solicitados por timestamps ou intervalo regular e virão acompanhados de seu manifesto temporal.

FFmpeg será a integração local de demux, corte, composição, normalização e renderização. Whisper será executado localmente, com CPU como baseline, GPU opcional, recomendação de hardware/modelo e override explícito.

### Acesso e usuários

Não haverá contas, login, recuperação, papéis ou tenancy. O operador humano controla a conversa e aprova a preview. A CLI apenas valida que os caminhos recebidos estão dentro das regras do trabalho e que o processo tem permissão para ler fontes e escrever artefatos.

## Direção técnica

O alvo inicial é Windows local. A distribuição será um pacote Python em ambiente virtual, preparado por script PowerShell, com dependências pinadas. FFmpeg e os componentes do Whisper serão verificados e instalados explicitamente. O produto não deve baixar modelo ou dependência durante uma edição sem uma ação explícita de instalação.

A CLI terá subcomandos estáveis, saída JSON opcional e códigos de saída previsíveis. O plano será validado na borda, com caminhos permitidos, limites de 60 minutos e 4 GB por fonte, formatos aceitos e limites de processamento. Processos externos serão chamados com argumentos estruturados, sem shell intermediário. A implementação deverá incluir lockfile e gates de SCA e detecção de segredos antes do primeiro push.

O modelo Whisper e o backend local exatos serão escolhidos na especificação conforme compatibilidade do Windows, suporte a CPU/GPU e qualidade necessária. `capabilities --json` deve distinguir capacidade detectada, dependência ausente e recomendação estimada, sem enviar inventário de hardware para fora.

## Direção visual

Não haverá UI do produto, logo ou sistema visual de painel. O perfil inicial do vídeo é 16:9. A preview terá até 720 pixels na maior dimensão; a full usará o perfil final definido pela primeira fonte da timeline, preservando proporção. Fontes com perfis diferentes serão normalizadas, e barras ou cortes só ocorrerão quando explicitados no plano.

O lettering aceitará texto, início, fim, posição nomeada, fonte, tamanho, cor, alinhamento e quebra de linha simples, com margem segura. Não haverá animação, coordenadas livres, picture-in-picture, transições ou templates complexos.

## Operação

Todo o processamento será local e sem telemetria. O operador será responsável pelos diretórios de trabalho e pela limpeza de trabalhos antigos; os arquivos originais nunca serão removidos ou sobrescritos. O sistema deverá preservar plano e análises após falhas, limpar temporários com segurança e publicar resultados somente após conclusão atômica.

Logs técnicos ficam no ambiente local e não devem conter tokens, segredos ou dados pessoais desnecessários. Mensagens públicas são seguras e estruturadas. Não haverá monitoramento remoto, SLA, backup automático ou suporte operacional no MVP; os artefatos podem ser copiados pelo operador quando necessário.

## Decisões críticas

| ID | Estado | Decisão | Motivo e impacto |
|---|---|---|---|
| PROD-01 | DECIDIDO | Primeiro job: vlog de viagens em 16:9. | Dá um cenário concreto para validar análise, b-roll, áudio, letterings e renderização sem transformar o MVP em editor genérico. |
| SCOPE-01 | DECIDIDO | Análise e edição linear básica ponta a ponta. | O valor só é comprovado quando a conversa consegue chegar da compreensão à full aprovada. |
| CLI-01 | DECIDIDO | Uma CLI local, não interativa, com subcomandos e `--json`. | Mantém o contrato reutilizável por Codex, Grok Build e outros hosts sem agente ou painel próprio. |
| DATA-01 | DECIDIDO | Diretório local por trabalho, plano JSON e nenhum banco. | Torna o estado inspecionável e reproduzível, sem infraestrutura prematura. |
| MEDIA-01 | DECIDIDO | MP4 H.264/AAC, MP3/WAV, até 60 minutos e 4 GB por fonte. | Restringe a primeira faixa de compatibilidade e protege tempo, memória e espaço temporário. |
| AI-01 | DECIDIDO | Whisper local, CPU baseline, GPU opcional e recomendação por hardware. | Evita custo de tokens e adapta o desempenho ao computador sem esconder dependências. |
| RENDER-01 | DECIDIDO | Uma preview vigente, aprovação por hash e full baseada no mesmo plano. | Evita versões paralelas e impede que a full seja diferente da preview aprovada. |
| AUDIO-01 | DECIDIDO | Trilhas independentes, mixagem com volume explícito, normalização de loudness e limiter. | Cobre voz, música e b-roll sem ducking, equalizador paramétrico ou restauração de ruído. |
| TEXT-01 | DECIDIDO | Lettering estático por intervalo e posição nomeada. | Cobre o caso de destacar termos da transcrição sem criar editor visual ou animação. |
| PRIV-01 | DECIDIDO | Processamento e artefatos 100% locais, sem telemetria. | Preserva os vídeos e mantém o custo previsível. |
| DEP-01 | ASSUMIDO | Pacote Python com ambiente virtual e instalador PowerShell. | Reduz a complexidade inicial de empacotar Whisper e FFmpeg em executável único; versões e backend serão fechados na especificação. |
| OPS-01 | ASSUMIDO | Um trabalho por vez e limpeza manual de diretórios antigos. | É suficiente para validar o MVP sem fila, servidor ou armazenamento compartilhado. |

## Handoff

vibe-spec
rota: max
