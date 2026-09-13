# Instalação das dependências do editor local

Esta referência é lida quando a CLI não é encontrada ou quando o inventário de
capacidades aponta uma dependência ausente.

## Dependências para executar o editor

O editor opera localmente no Windows e precisa de:

- Python 3.11 x64;
- PowerShell 7 para o instalador;
- FFmpeg e FFprobe locais;
- `faster-whisper` e CTranslate2 instalados pelo `requirements.lock`;
- pelo menos um modelo Whisper local completo, `tiny`, `base` ou `small`;
- encoders `libx264` e `aac` para gerar vídeo.

CUDA é opcional. CPU é o caminho padrão. `gitleaks` e `pip-audit` são usados nos
gates de segurança do repositório, não são necessários para a edição em si.

Nada é baixado automaticamente pelo editor. Os binários, instaladores e modelos
devem ser fornecidos localmente pelo operador.

## Provisionar o repositório

Execute os comandos a partir da raiz do repositório do editor.

Primeiro, crie o ambiente Python e instale as dependências pinadas:

```powershell
pwsh -NoProfile -File scripts/install.ps1 -Install
```

Se FFmpeg e FFprobe ainda não estiverem no PATH, forneça uma pasta local que
contenha `ffmpeg.exe` e `ffprobe.exe`:

```powershell
pwsh -NoProfile -File scripts/install.ps1 `
  -InstallFfmpeg `
  -FfmpegSource "C:\origens\ffmpeg"
```

Forneça uma pasta local contendo os modelos CTranslate2 completos:

```powershell
pwsh -NoProfile -File scripts/install.ps1 `
  -InstallModels `
  -ModelSource "C:\origens\whisper-models"
```

Se o gate de segredos também precisar ser executado, forneça o executável local
`gitleaks.exe` na versão pinada:

```powershell
pwsh -NoProfile -File scripts/install.ps1 `
  -InstallGitleaks `
  -GitleaksSource "C:\origens\gitleaks.exe"
```

CUDA só deve ser instalada quando houver um instalador local compatível e uma
necessidade real de usar GPU. Depois, confira o runtime com `-Verify`.

## Verificar antes do primeiro trabalho

Use o modo somente leitura:

```powershell
pwsh -NoProfile -File scripts/install.ps1 -Verify
```

Depois confirme a visão que será consumida pelo agente:

```powershell
.venv\Scripts\python.exe -B -m editor capabilities --json
```

Ao iniciar um trabalho novo, use um diretório absoluto aprovado pelo usuário e
prepare sua estrutura técnica:

```powershell
.venv\Scripts\python.exe -B -m editor init --workdir "<workdir>" --json
```

O comando é idempotente e cria somente `.tmp`, `analysis/audio`,
`analysis/transcription` e `frames`. Ele não escolhe fontes nem cria plano,
preview, aprovação ou full.

Se uma operação estiver marcada como indisponível, não tente contorná-la com
serviço remoto. Informe ao usuário exatamente qual componente local falta e leia
novamente esta referência para orientar a instalação.

## Contrato atual e limites editoriais

- O plano usa schema v2, com `cuts` e `layers` explícitos na raiz. A timeline visual
  continua linear, e a composição aceita movimento, cortes J/L e camadas de texto,
  imagem ou vídeo.
- Fontes de imagem aceitas são PNG, JPEG/JPG e WebP estáticos, com até 100 MiB e
  8.192 pixels por dimensão. Vídeo e áudio continuam restritos aos formatos e
  codecs informados por `capabilities`.
- Movimento usa `from` e `to`, interpolação linear e `zoom` entre `0.5` e `4.0`.
  Cortes J/L usam duração explícita de até 30 segundos, sujeita à validação dos
  segmentos adjacentes.
- O trabalho é local, linear e processado um por vez. A skill não instala
  componentes globais sem autorização explícita do usuário.
