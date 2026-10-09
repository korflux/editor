# Preparar as fontes para o editor

Esta referência é lida quando o editor rejeita uma fonte (`MEDIA_UNSUPPORTED` em `analyze audio`, `extract frames` ou na renderização, ou `PLAN_INVALID` com "formato ou metadados compatíveis") ou quando o diagnóstico da seção 2 mostra que a fonte não cabe no contrato. Vídeos de câmera e de celular em HEVC (inclusive 10 bits) e em `.mov` **não precisam** deste passo: o editor os lê direto. A preparação serve ao que continua recusado (`.mkv` e outros contêineres, mais de um áudio, vídeo extra, legenda, codecs como VP9 e AV1, HEVC 4:2:2, 4:4:4 ou 12 bits) e resolve: com a cópia preparada, o editor aceita análise de áudio, extração de frames, transcrição com CUDA, validação e renderização.

## 1. O que o editor aceita

| Item | Aceito |
|---|---|
| Contêiner | `.mp4` e `.mov` (o mesmo demuxer). `.mkv` e os demais são recusados pela extensão (`MEDIA_UNSUPPORTED`, "A extensão da fonte não é aceita") |
| Vídeo | Um stream de vídeo real, H.264 (qualquer perfil) ou HEVC Main e Main 10 em `yuv420p` ou `yuv420p10le`. HEVC 4:2:2, 4:4:4, 12 bits e extensões de alcance (rext) são recusados com `MEDIA_UNSUPPORTED`; VP9, AV1 e MPEG-4 também |
| Áudio | **Exatamente um** stream AAC para `analyze audio`: nenhum ou mais de um recebe `MEDIA_UNSUPPORTED` ("deve conter exatamente um stream de áudio"). Fonte sem áudio funciona em `extract frames` e no plano com `mode: mute` e `tracks: []`. Fontes só de áudio: MP3 ou WAV PCM de 16 bits |
| Streams extras | Trilhas de dados (`data`, como o timecode `tmcd` e a telemetria das câmeras) e miniatura anexada (`attached_pic`) são **ignoradas**. Legenda, anexo (`attachment`), segundo stream de vídeo real e miniatura anexada como primeiro stream de vídeo são recusados |
| Saída | Sempre H.264/AAC `yuv420p` de 8 bits: o HEVC de 10 bits é convertido para 8 bits no render, no proxy e no PNG de frame |
| HDR | HLG e PQ (BT.2020) **não** recebem tone mapping: a conversão para 8 bits sem ele deixa a imagem lavada. Avisar o usuário antes de usar essa fonte |
| Tamanho | Até 4 GiB e 1 hora por arquivo |
| Proporção | A do perfil direto (`fit: preserve`); outras proporções só com `fit: contain` ou `fit: crop` no segmento (ver `youtube-editing.md`, seção 1.4) |

As câmeras DJI (HEVC de 10 bits, três streams de dados e uma miniatura MJPEG), os iPhone e muitos Android (HEVC em MOV ou MP4, às vezes com taxa de quadros variável) entram direto. Os testes de aceite usam fontes sintéticas (`.mp4` e `.mov`, 2688 × 1512 e 1920 × 1080, 8 e 10 bits, com `tmcd` e miniatura); a leitura de um bruto real de câmera sem conversão ainda precisa de confirmação com um arquivo real.

ponytail: fonte HDR sai sem tone mapping, adicionar tone mapping no editor quando uma fonte HDR real precisar sair fiel; até lá avisar o usuário.

## 2. Diagnóstico

Antes de converter, listar os streams da fonte:

```text
ffprobe -v error -show_entries stream=index,codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate,avg_frame_rate,color_transfer,channels,sample_rate -of compact "<arquivo>"
```

Conferir:

- `codec_name` do vídeo diferente de `h264` e de `hevc` (VP9, AV1, MPEG-4, ProRes): converter (seção 4);
- `hevc` com `profile` fora de `Main` e `Main 10`, ou `pix_fmt` diferente de `yuv420p` e `yuv420p10le` (4:2:2, 4:4:4, 12 bits): converter. `yuv420p10le` em `hevc` Main 10 **não** precisa de conversão;
- stream `data` (`tmcd`, telemetria) ou `mjpeg` com `attached_pic=1`: o editor os ignora e não precisam sair; legenda, `attachment` ou um segundo vídeo real: a conversão os descarta;
- áudio que não seja `aac`, ou com mais de 2 canais: converter o áudio;
- mais de um áudio (gravações de tela e de stream costumam ter 2 ou 3 faixas, uma por fonte de som): o editor recusa o MP4 com várias faixas, então perguntar ao usuário qual é a fala principal e mapear só essa;
- `color_transfer` igual a `smpte2084` ou `arib-std-b67` (HDR): **avisar o usuário**. O editor lê a fonte, mas converte para 8 bits sem tone mapping e a imagem sai lavada. Um tone mapping prévio (por exemplo `zscale` e `tonemap` do FFmpeg) muda a aparência do vídeo, não foi testado com o editor e não se faz em silêncio;
- `r_frame_rate` diferente de `avg_frame_rate` (taxa variável, comum em celular): ver a seção 5.

## 3. Pedir autorização

A conversão cria cópias novas. Antes de rodar, informar o usuário em linguagem comum:

- quais arquivos serão convertidos e por quê (o editor não lê esse formato);
- que os originais **não são alterados** e que as cópias ficam em `<workdir>\sources\`;
- o custo: tempo (medido em um bruto 2,7K: cerca de 2 a 3 vezes o tempo real do vídeo) e disco (cerca de 24 GB por hora de vídeo em 2,7K);
- que a cópia acrescenta uma geração de compressão, em qualidade praticamente transparente.

Não converter sem esse aviso e sem o aceite.

## 4. Conversão

Esta linha cria uma cópia H.264 limpa a partir de qualquer vídeo que o FFmpeg leia. Trocar `<origem>` e `<destino>` pelos caminhos reais. Se o áudio da origem já for AAC estéreo, `-c:a copy` mantém o áudio sem recodificar.

Com a CPU (libx264):

```text
ffmpeg -i "<origem>" -map 0:v:0 -map 0:a:0 -map_metadata -1 -c:v libx264 -preset veryfast -crf 14 -pix_fmt yuv420p -c:a copy -write_tmcd 0 -movflags +faststart "<workdir>\sources\<id>.mp4"
```

Com a GPU NVIDIA (NVENC), mais rápida:

```text
ffmpeg -i "<origem>" -map 0:v:0 -map 0:a:0 -map_metadata -1 -c:v h264_nvenc -preset p5 -tune hq -rc vbr -cq 17 -b:v 0 -pix_fmt yuv420p -c:a copy -write_tmcd 0 -movflags +faststart "<workdir>\sources\<id>.mp4"
```

O que cada opção faz, e por que ela está na linha:

- `-map 0:v:0 -map 0:a:0`: copia só o primeiro vídeo e o primeiro áudio, descartando dados e miniaturas;
- `-map_metadata -1`: não herda os metadados da câmera;
- `-write_tmcd 0`: evita que o FFmpeg recrie uma trilha de timecode a partir dos metadados. O editor ignora essa trilha, então ela não impede a leitura; a opção só deixa a cópia mais limpa;
- `-pix_fmt yuv420p`: converte 10 bits para 8 bits e 4:2:2 ou 4:4:4 para 4:2:0 (o que o editor aceita);
- `-crf 14` (CPU) e `-cq 17` (GPU): qualidade alta o bastante para o editor cortar e renderizar sem perda visível.

Variações:

- **Áudio que não é AAC estéreo** (Opus, FLAC, PCM de 24 bits, 5.1): trocar `-c:a copy` por `-c:a aac -b:a 256k -ar 48000 -ac 2`. Para escolher outra faixa de áudio, usar `-map 0:a:<n>`.
- **Contêiner MKV (ou outro recusado pela extensão) que já é H.264 com AAC:** dispensa a recodificação. Usar `-c copy` no lugar das opções de vídeo e áudio (um `.mov` H.264 com AAC não precisa disso, o editor o lê direto):

```text
ffmpeg -i "<origem>" -map 0:v:0 -map 0:a:0 -map_metadata -1 -c copy -write_tmcd 0 -movflags +faststart "<workdir>\sources\<id>.mp4"
```

- **Fonte longa:** os limites são 4 GiB e 1 hora por arquivo preparado. Uma conversão a 52 a 56 Mb/s chega a 4 GiB em cerca de 10 minutos de vídeo 2,7K, e uma gravação de 1 hora (3621 s) passa do teto de duração mesmo sem converter. Dividir em partes, que saem nos quadros-chave (duração aproximada). Se a fonte já é H.264, a divisão **não recodifica** (testado num MKV 1080p60 de 1 hora, 10 GB):

```text
ffmpeg -i "<origem>" -map 0:v:0 -map 0:a:0 -map_metadata -1 -c copy -write_tmcd 0 -f segment -segment_time 600 -reset_timestamps 1 -segment_format_options movflags=+faststart "<workdir>\sources\<id>_%03d.mp4"
```

  Se a fonte precisar ser recodificada, usar a mesma opção `-f segment` com as opções de vídeo da linha principal, ou aumentar o `-crf`. As partes viram fontes separadas no plano.

Medido neste ambiente, num bruto DJI 2688 × 1512 HEVC de 10 bits com 54 s (antes de o editor lê-lo direto): CPU a 2,0 vezes o tempo real e 56 Mb/s; GPU a 2,9 vezes e 52 Mb/s.

## 5. Taxa de quadros variável

O editor aceita fontes de taxa variável, mas usa a taxa média como se fosse constante (num teste, uma fonte de média 19,55 quadros/s foi renderizada a 19,55). Para cortes na grade de quadros e sincronia confiáveis, converter para taxa constante usando a taxa média (`avg_frame_rate`) do `ffprobe`, acrescentando `-fps_mode cfr -r <taxa>` à linha da seção 4. Esta recomendação segue o comportamento padrão do FFmpeg e não foi validada com o editor.

## 6. Conferir a cópia

Cada cópia deve passar na análise de áudio, que é o teste mais barato do contrato:

```text
.venv\Scripts\python.exe -B -m editor analyze audio --workdir "<workdir>" --source-id "<id>" --input "<workdir>\sources\<id>.mp4" --json
```

Se ainda vier `MEDIA_UNSUPPORTED`, repetir o diagnóstico da seção 2 sobre a **cópia** e procurar o stream que sobrou.

## 7. Casos testados

Materiais reais passados pelo editor, com o resultado de cada tratamento:

| Material | Como veio | Tratamento | Resultado |
|---|---|---|---|
| Bruto de câmera DJI, 2688 × 1512 (antes da leitura nativa de HEVC) | HEVC 10 bits, AAC, 3 streams de dados, miniatura MJPEG, trilha `tmcd` | Conversão da seção 4 (CPU ou GPU) | Aceito em análise de áudio, frames, transcrição com CUDA, validação e renderização |
| Fontes sintéticas HEVC, `.mp4` e `.mov`, 2688 × 1512 e 1920 × 1080 | Main 10 e Main (8 bits), AAC, trilhas de dados `tmcd`, miniatura MJPEG anexada (só no MP4) | Nenhuma | Aceitas direto em análise de áudio, frames (PNG de 8 bits), validação, proxy, preview com e sem proxy, aprovação, full com janelas de decode e `--accel gpu`; saída H.264/AAC `yuv420p`. Sem bruto real de câmera no teste |
| HEVC 4:2:2, 4:4:4 e 12 bits; VP9; AV1; legenda; dois áudios; dois vídeos; `.mkv` | Fontes sintéticas | Conversão da seção 4, quando o conteúdo for necessário | Originais recusados com `MEDIA_UNSUPPORTED` |
| Gravação de tela, 1920 × 1080 a 60 quadros/s, 1 h (3621 s), 10 GB | MKV com H.264 e 3 faixas de áudio AAC | Remux `-c copy` escolhendo uma faixa; divisão em partes sem recodificar | Original recusado pela extensão; MP4 de qualquer das faixas e as 3 partes aceitos; MP4 com as 3 faixas recusado |
| Clipe curto de jogo, 1920 × 1072 | H.264 em MP4, sem áudio, taxa variável | Nenhuma conversão | Aceito em `extract frames`; `analyze audio` recusa (sem áudio); plano só passa com `fit: contain` ou `crop` e `mode: mute` |
| Fonte sintética 1366 × 768 | H.264 em MP4 | Nenhuma | Aceita com `fit: contain` e `max_full_width` 1376; a full sai em 1366 |
| Fontes sintéticas 4:3, 21:9 e vertical | H.264 em MP4 | Nenhuma | Aceitas só com `fit: contain` ou `crop`; num perfil 16:9 a saída tem a largura da fonte (o vertical vira um quadro 16:9 pequeno) |
| `.mov` com H.264 e AAC | MOV | Nenhuma | Aceito direto (a recusa e o remux para MP4 anteriores deixaram de ser necessários) |

## 8. Depois da preparação

- Quando houve conversão, o plano usa as cópias em `sources\`. A aprovação por hash fica vinculada às cópias, então não as renomear nem recriá-las depois de aprovar o plano.
- Guardar os originais. Registrar no resumo do briefing quais cópias foram criadas, a partir de qual original e com qual comando.
- A preparação não resolve a proporção: o perfil do plano decide (`youtube-editing.md`, seção 1.4). Uma fonte vertical num perfil 16:9 gera um quadro 16:9 com barras ou recorte pesado; para entregar vertical, usar um perfil `9:16`. Avisar o usuário antes de seguir.
- A preparação não substitui a aprovação humana da preview.
