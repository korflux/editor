"""Interface de revisão no navegador (phase 7): servidor local protegido e instância em segundo plano.

Mapa do pacote:
- `limits`: constantes únicas de segurança e operação (lidas pela CLI, pelo servidor e por `capabilities`);
- `instance`: lado da CLI, `ui start` e `ui stop` e o registro da instância em `.tmp` do trabalho;
- `server`: servidor HTTP local (`python -m editor.ui.server`), rotas, allowlists e cabeçalhos;
- `sessions`: sessões por cookie, teto de sessões simultâneas e processos FFmpeg por sessão;
- `state`: estado do plano (revalidação por arquivo alterado) e a visão entregue à interface;
- `selection`: mapeamento de um intervalo da montagem para segmentos, camadas e legendas e o texto "Copiar para IA";
- `playback`: abertura, relé e encerramento do FFmpeg do stream e do quadro de scrub;
- `static/`: HTML, CSS e JS empacotados, a única mídia que o servidor lê do disco.

A interface é só leitura (SEC-01): a única escrita futura é a aprovação, que chega na T6 pela rota própria.
"""
