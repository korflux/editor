#Requires -Version 7.0

[CmdletBinding()]
param(
    [switch]$Verify,
    [switch]$Install,
    [switch]$InstallFfmpeg,
    [string]$FfmpegSource,
    [switch]$InstallCuda,
    [string]$CudaInstallerPath,
    [string[]]$CudaInstallerArguments = @(),
    [switch]$InstallModels,
    [string]$ModelSource,
    [switch]$InstallGitleaks,
    [string]$GitleaksSource,
    [switch]$Force,
    [switch]$SecurityGates,
    [string]$VenvPath = '.venv',
    [string]$ModelDir = 'models',
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'

$script:PythonExecutable = $null
$script:PythonPrefix = @()
$script:RuntimePython = $null
$script:HasFailures = $false
$script:RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$script:VenvAbsolutePath = $null
$script:ModelAbsolutePath = $null
$script:GitleaksVersion = '8.30.1'
$script:AllowedModels = @('tiny', 'base', 'small')

# Resolve um executável pelo PATH ou por um caminho literal, mantendo a chamada sem shell.
function Resolve-Executable {
    param([Parameter(Mandatory)][string]$Name)

    $command = Get-Command -Name $Name -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command -and $command.CommandType -eq 'Application') {
        return $command.Source
    }

    if (Test-Path -LiteralPath $Name -PathType Leaf) {
        return (Get-Item -LiteralPath $Name -Force).FullName
    }

    return $null
}

# Verifica se um arquivo ou diretório não é reparse point antes de usá-lo como origem ou destino.
function Test-RegularPath {
    param([Parameter(Mandatory)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item) {
        return $false
    }

    return (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0)
}

# Verifica todos os componentes existentes de um caminho, pois checar só o destino
# permite atravessar uma junction em um ancestral e escapar da raiz física esperada.
function Test-RegularPathChain {
    param(
        [Parameter(Mandatory)][string]$Path,
        [string]$StopAt
    )

    $current = [System.IO.DirectoryInfo]::new([System.IO.Path]::GetFullPath($Path))
    $stopPath = if ($StopAt) {
        [System.IO.Path]::GetFullPath($StopAt).TrimEnd('\')
    } else {
        $null
    }

    while ($current) {
        $currentPath = $current.FullName
        if ((Test-Path -LiteralPath $currentPath) -and -not (Test-RegularPath $currentPath)) {
            return $false
        }
        if ($stopPath -and [System.String]::Equals(
                $currentPath.TrimEnd('\'),
                $stopPath,
                [System.StringComparison]::OrdinalIgnoreCase
            )) {
            return $true
        }
        $current = $current.Parent
    }

    return $null -eq $stopPath
}

# Converte um caminho relativo ao repositório e impede que destinos operacionais escapem dele.
function Resolve-RepoPath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [switch]$AllowMissing
    )

    $candidate = if ([System.IO.Path]::IsPathRooted($Path)) {
        [System.IO.Path]::GetFullPath($Path)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $script:RepoRoot $Path))
    }
    $rootPrefix = $script:RepoRoot.TrimEnd('\') + '\'
    if (($candidate -ne $script:RepoRoot) -and
        (-not $candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "O caminho deve permanecer dentro do repositório: $Path"
    }

    if (-not (Test-RegularPathChain -Path $candidate -StopAt $script:RepoRoot)) {
        throw "O caminho contém um componente linkado ou inválido: $Path"
    }
    if (-not $AllowMissing -and -not (Test-Path -LiteralPath $candidate)) {
        throw "Caminho não encontrado: $Path"
    }
    if ((Test-Path -LiteralPath $candidate) -and -not (Test-RegularPath $candidate)) {
        throw "Caminho linkado ou inválido: $Path"
    }

    return $candidate
}

# Valida uma origem local existente sem permitir links ou objetos de tipo incompatível.
function Resolve-LocalPath {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Origem local não encontrada: $Path"
    }
    if (-not (Test-RegularPathChain -Path $Path)) {
        throw "Origem local linkada ou inválida: $Path"
    }

    return (Get-Item -LiteralPath $Path -Force).FullName
}

# Registra o estado de uma verificação e mantém o processo falhando quando um requisito obrigatório não passa.
function Add-Check {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][bool]$Passed,
        [Parameter(Mandatory)][bool]$Optional,
        [Parameter(Mandatory)][string]$Detail
    )

    $status = if ($Passed) { 'available' } elseif ($Optional) { 'missing (optional)' } else { 'missing' }
    Write-Host ('[{0}] {1}: {2}' -f $status, $Name, $Detail)
    if (-not $Passed -and -not $Optional) {
        $script:HasFailures = $true
    }
}

# Executa um processo com argumentos estruturados e captura apenas o resultado necessário para a decisão.
function Invoke-External {
    param(
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    try {
        $output = @(& $Executable @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
        return [pscustomobject]@{
            exit_code = $exitCode
            output    = (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine)
        }
    } catch {
        return [pscustomobject]@{
            exit_code = 1
            output    = $_.Exception.Message
        }
    }
}

# Seleciona o Python alvo, priorizando o launcher do Windows para exigir exatamente a versão 3.11.
function Initialize-PythonInvoker {
    if ($PythonPath) {
        $script:PythonExecutable = Resolve-Executable $PythonPath
        $script:PythonPrefix = @()
    } else {
        $launcher = Resolve-Executable 'py'
        if ($launcher) {
            $script:PythonExecutable = $launcher
            $script:PythonPrefix = @('-3.11')
        } else {
            $script:PythonExecutable = Resolve-Executable 'python'
            $script:PythonPrefix = @()
        }
    }
}

# Executa o Python alvo ou o interpretador do ambiente virtual atual com a mesma forma estruturada de argumentos.
function Invoke-Python {
    param([Parameter(Mandatory)][string[]]$Arguments)

    if (-not $script:RuntimePython) {
        if (-not $script:PythonExecutable) {
            return [pscustomobject]@{ exit_code = 1; output = 'Python não localizado.' }
        }
        return Invoke-External $script:PythonExecutable (@($script:PythonPrefix) + @($Arguments))
    }

    return Invoke-External $script:RuntimePython $Arguments
}

# Retorna informações mínimas do interpretador para validar versão e arquitetura sem depender de APIs externas.
function Get-PythonInfo {
    $code = "import json,struct,sys; print(json.dumps({'version': '.'.join(map(str, sys.version_info[:3])), 'major': sys.version_info.major, 'minor': sys.version_info.minor, 'bits': struct.calcsize('P') * 8}))"
    $result = Invoke-Python @('-c', $code)
    if ($result.exit_code -ne 0) {
        return $null
    }

    try {
        return ($result.output | ConvertFrom-Json)
    } catch {
        return $null
    }
}

# Valida o interpretador exigido pelo MVP antes de criar ambiente ou instalar dependências.
function Test-PythonTarget {
    $info = Get-PythonInfo
    if (-not $info) {
        Add-Check 'Python 3.11 x64' $false $false 'Python 3.11 não foi localizado pelo launcher ou caminho informado.'
        return $false
    }

    $passed = ($info.major -eq 3) -and ($info.minor -eq 11) -and ($info.bits -eq 64)
    $detail = '{0}, {1} bits' -f $info.version, $info.bits
    if (-not $passed) {
        $detail = '{0}; esperado Python 3.11 x64' -f $detail
    }
    Add-Check 'Python 3.11 x64' $passed $false $detail
    return $passed
}

# Extrai a versão pinada de um pacote do lockfile para evitar duplicar contratos de dependência no script.
function Get-LockedVersion {
    param(
        [Parameter(Mandatory)][string]$LockText,
        [Parameter(Mandatory)][string]$Package
    )

    $pattern = '(?im)^' + [regex]::Escape($Package) + '==([^\s\\]+)'
    $match = [regex]::Match($LockText, $pattern)
    if ($match.Success) {
        return $match.Groups[1].Value
    }
    return $null
}

# Confirma que o lockfile tem os pacotes críticos pinados e hashes para impedir resolução flutuante.
function Test-Lockfile {
    $lockPath = Join-Path $script:RepoRoot 'requirements.lock'
    if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf) -or -not (Test-RegularPath $lockPath)) {
        Add-Check 'requirements.lock' $false $false 'Arquivo ausente ou linkado.'
        return $null
    }

    $lockText = Get-Content -Raw -LiteralPath $lockPath
    $requiredPackages = @('faster-whisper', 'ctranslate2', 'pip-audit', 'setuptools', 'wheel')
    $missing = @($requiredPackages | Where-Object { -not (Get-LockedVersion $lockText $_) })
    $hashCount = ([regex]::Matches($lockText, '(?im)^\s+--hash=sha256:[0-9a-f]{64}')).Count
    $passed = ($missing.Count -eq 0) -and ($hashCount -gt 0)
    $detail = if ($passed) {
        '{0} pacotes pinados, {1} hashes SHA-256' -f ([regex]::Matches($lockText, '(?im)^[a-z0-9][a-z0-9_.-]*==[^\s\\]+').Count), $hashCount
    } else {
        'Pacotes sem pin ou hashes ausentes: ' + ($missing -join ', ')
    }
    Add-Check 'requirements.lock' $passed $false $detail
    return [pscustomobject]@{ path = $lockPath; text = $lockText }
}

# Define o interpretador do ambiente virtual quando ele já existe, sem aceitar um destino linkado.
function Set-RuntimeToVenv {
    $venvPython = Join-Path $script:VenvAbsolutePath 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf) -or -not (Test-RegularPath $venvPython)) {
        return $false
    }

    $script:RuntimePython = (Get-Item -LiteralPath $venvPython -Force).FullName
    return $true
}

# Verifica a existência do ambiente virtual e confirma que ele usa Python 3.11 x64.
function Test-VirtualEnvironment {
    if (-not (Set-RuntimeToVenv)) {
        Add-Check 'ambiente virtual' $false $false "Python não encontrado em $($script:VenvAbsolutePath)\Scripts. Execute -Install."
        return $false
    }

    $info = Get-PythonInfo
    $passed = $null -ne $info -and $info.major -eq 3 -and $info.minor -eq 11 -and $info.bits -eq 64
    $detail = if ($info) { '{0}, {1} bits' -f $info.version, $info.bits } else { 'Não foi possível consultar o interpretador do ambiente.' }
    Add-Check 'ambiente virtual' $passed $false $detail
    return $passed
}

# Confirma a presença e a execução básica de ffmpeg e ffprobe, usando a cópia local quando instalada no repositório.
function Test-Ffmpeg {
    $localTools = Join-Path $script:RepoRoot '.tools\ffmpeg'
    $ffmpegLocal = Join-Path $localTools 'ffmpeg.exe'
    $ffprobeLocal = Join-Path $localTools 'ffprobe.exe'
    $ffmpeg = if ((Test-Path -LiteralPath $ffmpegLocal -PathType Leaf) -and (Test-RegularPath $ffmpegLocal)) { $ffmpegLocal } else { Resolve-Executable 'ffmpeg' }
    $ffprobe = if ((Test-Path -LiteralPath $ffprobeLocal -PathType Leaf) -and (Test-RegularPath $ffprobeLocal)) { $ffprobeLocal } else { Resolve-Executable 'ffprobe' }

    $ffmpegResult = if ($ffmpeg) { Invoke-External $ffmpeg @('-version') } else { $null }
    $ffprobeResult = if ($ffprobe) { Invoke-External $ffprobe @('-version') } else { $null }
    $passed = $null -ne $ffmpegResult -and $null -ne $ffprobeResult -and
        $ffmpegResult.exit_code -eq 0 -and $ffprobeResult.exit_code -eq 0
    $detail = if ($passed) { 'ffmpeg e ffprobe executaram -version.' } else { 'Instale ou forneça ambos os executáveis localmente.' }
    Add-Check 'FFmpeg' $passed $false $detail
}

# Consulta os módulos Python pinados para garantir que o backend local e o auditor estejam instalados no venv.
function Test-PythonPackages {
    param([Parameter(Mandatory)][pscustomobject]$Lock)

    $code = "import importlib.metadata as m, importlib.util, json; specs=[('faster-whisper','faster_whisper'),('ctranslate2','ctranslate2'),('pip-audit','pip_audit')]; out={}; [out.update({d:{'version':m.version(d),'module':importlib.util.find_spec(mod) is not None}}) for d,mod in specs]; print(json.dumps(out))"
    $result = Invoke-Python @('-c', $code)
    if ($result.exit_code -ne 0) {
        Add-Check 'backend Whisper' $false $false 'faster-whisper/CTranslate2 não puderam ser consultados no ambiente.'
        Add-Check 'pip-audit' $false $false 'pip-audit não pôde ser consultado no ambiente.'
        return
    }

    try {
        $installed = $result.output | ConvertFrom-Json
    } catch {
        Add-Check 'backend Whisper' $false $false 'Resposta inválida do ambiente Python.'
        Add-Check 'pip-audit' $false $false 'Resposta inválida do ambiente Python.'
        return
    }

    $backendPassed = $true
    foreach ($package in @('faster-whisper', 'ctranslate2')) {
        $entry = $installed.$package
        $expected = Get-LockedVersion $Lock.text $package
        $ok = $null -ne $entry -and $entry.module -eq $true -and $entry.version -eq $expected
        if (-not $ok) { $backendPassed = $false }
    }
    $backendDetail = if ($backendPassed) { 'faster-whisper e CTranslate2 estão pinados e importáveis.' } else { 'Backend ausente ou divergente do requirements.lock.' }
    Add-Check 'backend Whisper' $backendPassed $false $backendDetail

    $auditEntry = $installed.'pip-audit'
    $auditExpected = Get-LockedVersion $Lock.text 'pip-audit'
    $auditPassed = $null -ne $auditEntry -and $auditEntry.module -eq $true -and $auditEntry.version -eq $auditExpected
    $auditDetail = if ($auditPassed) { "pip-audit $auditExpected está disponível no venv." } else { 'pip-audit ausente ou divergente do requirements.lock; execute -Install.' }
    Add-Check 'pip-audit' $auditPassed $false $auditDetail
}

# Confirma gitleaks na versão pinada antes de permitir que os gates de segurança sejam considerados disponíveis.
function Test-Gitleaks {
    $localPath = Join-Path $script:RepoRoot '.tools\gitleaks\gitleaks.exe'
    $executable = if ((Test-Path -LiteralPath $localPath -PathType Leaf) -and (Test-RegularPath $localPath)) { $localPath } else { Resolve-Executable 'gitleaks' }
    $result = if ($executable) { Invoke-External $executable @('version') } else { $null }
    $passed = $null -ne $result -and $result.exit_code -eq 0 -and $result.output -match [regex]::Escape($script:GitleaksVersion)
    $detail = if ($passed) { "gitleaks $($script:GitleaksVersion) está disponível." } else { "Instale explicitamente gitleaks $($script:GitleaksVersion) ou use -InstallGitleaks -GitleaksSource <gitleaks.exe>." }
    Add-Check 'gitleaks' $passed $false $detail
    return $executable
}

# Verifica arquivos mínimos de modelos CTranslate2 locais e retorna os diretórios válidos para a recomendação posterior.
function Get-ValidModelDirectories {
    param([Parameter(Mandatory)][string]$Root)

    if (-not (Test-Path -LiteralPath $Root -PathType Container) -or -not (Test-RegularPath $Root)) {
        return @()
    }

    $valid = [System.Collections.Generic.List[object]]::new()
    foreach ($directory in @(Get-ChildItem -LiteralPath $Root -Directory -Force)) {
        if ($directory.Name -notin $script:AllowedModels -or -not (Test-RegularPath $directory.FullName)) {
            continue
        }
        $unsafeChildren = @(Get-ChildItem -LiteralPath $directory.FullName -Recurse -Force |
            Where-Object { -not (Test-RegularPath $_.FullName) })
        if ($unsafeChildren.Count -gt 0) {
            continue
        }
        $required = @('config.json', 'model.bin', 'tokenizer.json')
        $hasRequired = $true
        foreach ($file in $required) {
            $path = Join-Path $directory.FullName $file
            if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or -not (Test-RegularPath $path)) {
                $hasRequired = $false
            }
        }
        $vocabulary = @(Get-ChildItem -LiteralPath $directory.FullName -File -Force -Filter 'vocabulary.*' |
            Where-Object { Test-RegularPath $_.FullName })
        if ($hasRequired -and $vocabulary.Count -gt 0) {
            [void]$valid.Add($directory)
        }
    }
    return $valid.ToArray()
}

# Exige ao menos um modelo local válido, sem baixar pesos nem aceitar diretórios fora do repositório.
function Test-WhisperModels {
    $valid = @(Get-ValidModelDirectories $script:ModelAbsolutePath)
    $passed = $valid.Count -gt 0
    $names = if ($passed) { ($valid | ForEach-Object { $_.Name }) -join ', ' } else { 'nenhum modelo tiny, base ou small completo foi encontrado' }
    Add-Check 'modelos Whisper locais' $passed $false $names
}

# Verifica driver, bibliotecas e enumeração do CTranslate2, mas mantém CPU como baseline obrigatório do MVP.
function Test-Cuda {
    $nvidia = Resolve-Executable 'nvidia-smi'
    $driver = if ($nvidia) { Invoke-External $nvidia @('--query-gpu=name,driver_version,memory.total', '--format=csv,noheader') } else { $null }
    $binDirectories = [System.Collections.Generic.List[string]]::new()
    if ($env:CUDA_PATH) { [void]$binDirectories.Add((Join-Path $env:CUDA_PATH 'bin')) }
    foreach ($directory in ($env:Path -split ';')) {
        if ($directory) { [void]$binDirectories.Add($directory) }
    }
    $cublas = $false
    $cudnn = $false
    foreach ($directory in $binDirectories) {
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) { continue }
        if (Get-ChildItem -LiteralPath $directory -Filter 'cublas64_12.dll' -File -ErrorAction SilentlyContinue) { $cublas = $true }
        if (Get-ChildItem -LiteralPath $directory -Filter 'cudnn*.dll' -File -ErrorAction SilentlyContinue) { $cudnn = $true }
    }
    $runtimeCount = 0
    if ($script:RuntimePython) {
        # A presença de DLLs não prova que o backend consegue inicializar CUDA; confirme o mesmo runtime usado pela CLI.
        $runtimeProbe = Invoke-Python @('-c', 'import ctranslate2; print(int(ctranslate2.get_cuda_device_count()))')
        try { $runtimeCount = [int]$runtimeProbe.output.Trim() } catch { $runtimeCount = 0 }
    }
    $passed = $null -ne $driver -and $driver.exit_code -eq 0 -and $cublas -and $cudnn -and $runtimeCount -gt 0
    $detail = if ($passed) { 'GPU, driver, cuBLAS 12, cuDNN e runtime CTranslate2 foram validados.' } else { 'CUDA não está completo; CPU continua disponível como baseline.' }
    Add-Check 'CUDA' $passed $true $detail
}

# Executa pip check no ambiente virtual para detectar inconsistência entre as versões instaladas.
function Test-PipCheck {
    $result = Invoke-Python @('-m', 'pip', 'check')
    $passed = $result.exit_code -eq 0
    $detail = if ($passed) { 'pip check sem conflitos.' } else { 'pip check encontrou conflitos; corrija o ambiente antes dos gates.' }
    Add-Check 'pip check' $passed $false $detail
}

# Cria o venv apenas em modo explícito e instala exatamente o lockfile com hashes obrigatórios.
function Install-PythonEnvironment {
    if (-not (Test-PythonTarget)) {
        throw 'Python 3.11 x64 é obrigatório para -Install.'
    }
    $lock = Test-Lockfile
    if (-not $lock) {
        throw 'requirements.lock inválido ou ausente.'
    }
    if (Test-Path -LiteralPath $script:VenvAbsolutePath) {
        if (-not (Test-RegularPath $script:VenvAbsolutePath)) {
            throw 'O destino do ambiente virtual é um reparse point ou objeto inválido.'
        }
        $existingPython = Join-Path $script:VenvAbsolutePath 'Scripts\python.exe'
        $existingItems = @(Get-ChildItem -LiteralPath $script:VenvAbsolutePath -Force)
        if (-not (Test-Path -LiteralPath $existingPython -PathType Leaf) -and $existingItems.Count -gt 0) {
            throw 'O destino do ambiente virtual não está vazio e não contém um venv utilizável.'
        }
    } else {
        New-Item -ItemType Directory -Path $script:VenvAbsolutePath -Force | Out-Null
    }
    [void](Resolve-RepoPath $script:VenvAbsolutePath -AllowMissing)

    $venvPython = Join-Path $script:VenvAbsolutePath 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $create = Invoke-Python @('-m', 'venv', $script:VenvAbsolutePath)
        if ($create.exit_code -ne 0) {
            throw 'Falha ao criar o ambiente virtual.'
        }
    }
    if (-not (Set-RuntimeToVenv)) {
        throw 'O ambiente virtual não produziu Scripts\python.exe.'
    }
    $venvInfo = Get-PythonInfo
    if (-not $venvInfo -or $venvInfo.major -ne 3 -or $venvInfo.minor -ne 11 -or $venvInfo.bits -ne 64) {
        throw 'O ambiente virtual existente não usa Python 3.11 x64.'
    }

    $install = Invoke-Python @('-m', 'pip', 'install', '--require-hashes', '--no-input', '--disable-pip-version-check', '--requirement', $lock.path)
    if ($install.exit_code -ne 0) {
        throw 'Falha ao instalar requirements.lock; o ambiente pode estar incompleto.'
    }
    $editable = Invoke-Python @('-m', 'pip', 'install', '--no-deps', '--no-build-isolation', '--no-input', '--disable-pip-version-check', '--editable', $script:RepoRoot)
    if ($editable.exit_code -ne 0) {
        throw 'Falha ao instalar a CLI em modo editável.'
    }
    Test-PipCheck
    if ($script:HasFailures) {
        throw 'O ambiente foi criado, mas pip check não passou.'
    }
    Write-Host "Ambiente Python instalado em $($script:VenvAbsolutePath)."
}

# Copia ffmpeg e ffprobe fornecidos localmente para uma pasta versionável do ambiente, sem baixar binários.
function Install-LocalFfmpeg {
    if (-not $FfmpegSource) {
        throw 'Informe -FfmpegSource com um diretório local que contenha ffmpeg.exe e ffprobe.exe.'
    }
    $source = Resolve-LocalPath $FfmpegSource
    if (-not (Get-Item -LiteralPath $source -Force).PSIsContainer) {
        throw '-FfmpegSource deve ser um diretório local.'
    }
    $ffmpeg = Get-ChildItem -LiteralPath $source -Recurse -File -Filter 'ffmpeg.exe' |
        Where-Object { Test-RegularPath $_.FullName } | Select-Object -First 1
    $ffprobe = Get-ChildItem -LiteralPath $source -Recurse -File -Filter 'ffprobe.exe' |
        Where-Object { Test-RegularPath $_.FullName } | Select-Object -First 1
    if (-not $ffmpeg -or -not $ffprobe) {
        throw 'A origem local precisa conter ffmpeg.exe e ffprobe.exe regulares.'
    }

    $destination = Resolve-RepoPath '.tools\ffmpeg' -AllowMissing
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    [void](Resolve-RepoPath $destination -AllowMissing)
    foreach ($target in @((Join-Path $destination 'ffmpeg.exe'), (Join-Path $destination 'ffprobe.exe'))) {
        if ((Test-Path -LiteralPath $target) -and -not (Test-RegularPath $target)) {
            throw 'O destino de FFmpeg contém um executável linkado.'
        }
    }
    Copy-Item -LiteralPath $ffmpeg.FullName -Destination (Join-Path $destination 'ffmpeg.exe') -Force
    Copy-Item -LiteralPath $ffprobe.FullName -Destination (Join-Path $destination 'ffprobe.exe') -Force
    Write-Host "FFmpeg instalado localmente em $destination."
}

# Copia um executável gitleaks local e exige a versão pinada antes de disponibilizá-lo para os gates.
function Install-LocalGitleaks {
    if (-not $GitleaksSource) {
        throw "Informe -GitleaksSource com gitleaks.exe $($script:GitleaksVersion)."
    }
    $source = Resolve-LocalPath $GitleaksSource
    $item = Get-Item -LiteralPath $source -Force
    if ($item.PSIsContainer -or $item.Name -ne 'gitleaks.exe') {
        throw '-GitleaksSource deve apontar para o arquivo gitleaks.exe.'
    }
    $version = Invoke-External $item.FullName @('version')
    if ($version.exit_code -ne 0 -or $version.output -notmatch [regex]::Escape($script:GitleaksVersion)) {
        throw "O gitleaks fornecido não é a versão $($script:GitleaksVersion)."
    }

    $destination = Resolve-RepoPath '.tools\gitleaks' -AllowMissing
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    [void](Resolve-RepoPath $destination -AllowMissing)
    $target = Join-Path $destination 'gitleaks.exe'
    if ((Test-Path -LiteralPath $target) -and -not (Test-RegularPath $target)) {
        throw 'O destino de gitleaks contém um executável linkado.'
    }
    Copy-Item -LiteralPath $item.FullName -Destination $target -Force
    Write-Host "gitleaks $($script:GitleaksVersion) instalado localmente em $destination."
}

# Valida uma fonte de modelos e copia somente diretórios tiny, base ou small para a pasta local do repositório.
function Install-LocalModels {
    param([switch]$Overwrite)

    if (-not $ModelSource) {
        throw 'Informe -ModelSource com um diretório local de modelos CTranslate2.'
    }
    $source = Resolve-LocalPath $ModelSource
    if (-not (Get-Item -LiteralPath $source -Force).PSIsContainer) {
        throw '-ModelSource deve ser um diretório local.'
    }

    $sourceDirectories = @(Get-ValidModelDirectories $source)
    if ($sourceDirectories.Count -eq 0) {
        throw 'Nenhum modelo local completo foi encontrado na origem.'
    }
    [void](Resolve-RepoPath $script:ModelAbsolutePath -AllowMissing)
    if (-not (Test-Path -LiteralPath $script:ModelAbsolutePath)) {
        New-Item -ItemType Directory -Path $script:ModelAbsolutePath -Force | Out-Null
    }
    foreach ($model in $sourceDirectories) {
        $destination = Resolve-RepoPath (Join-Path $script:ModelAbsolutePath $model.Name) -AllowMissing
        if ((Test-Path -LiteralPath $destination) -and -not (Test-RegularPath $destination)) {
            throw "O destino do modelo $($model.Name) é um reparse point."
        }
        if ((Test-Path -LiteralPath $destination) -and -not $Overwrite) {
            throw "O modelo $($model.Name) já existe; remova-o manualmente antes de reinstalar."
        }
        Copy-Item -LiteralPath $model.FullName -Destination $destination -Recurse -Force
    }
    Write-Host ("Modelos locais instalados: " + (($sourceDirectories | ForEach-Object { $_.Name }) -join ', '))
}

# Executa explicitamente um instalador CUDA local, sem download, shell intermediário ou argumento implícito.
function Install-LocalCuda {
    if (-not $CudaInstallerPath) {
        throw 'Informe -CudaInstallerPath com um instalador CUDA local. A instalação é opcional e explícita.'
    }
    $source = Resolve-LocalPath $CudaInstallerPath
    $item = Get-Item -LiteralPath $source -Force
    if ($item.PSIsContainer -or $item.Extension -ne '.exe') {
        throw '-CudaInstallerPath deve apontar para um instalador .exe local.'
    }

    $result = Invoke-External $item.FullName $CudaInstallerArguments
    if ($result.exit_code -ne 0) {
        throw 'O instalador CUDA local retornou erro.'
    }
    Write-Host 'Instalador CUDA local concluído; execute -Verify para conferir cuBLAS, cuDNN e driver.'
}

# Executa os gates de segredo e SCA somente quando solicitado e bloqueia qualquer falha ou ferramenta ausente.
function Invoke-SecurityGates {
    $gitleaks = Test-Gitleaks
    if (-not $gitleaks) {
        throw 'Gate de segredo indisponível: instale gitleaks antes de executá-lo.'
    }
    $secretGate = Invoke-External $gitleaks @('detect', '--source', $script:RepoRoot, '--no-banner')
    if ($secretGate.exit_code -ne 0) {
        throw 'Gate de segredo bloqueado por gitleaks.'
    }

    if (-not $script:RuntimePython) {
        throw 'Gate SCA indisponível: ambiente virtual não validado.'
    }
    $lockPath = Join-Path $script:RepoRoot 'requirements.lock'
    $scaGate = Invoke-Python @('-m', 'pip_audit', '-r', $lockPath)
    if ($scaGate.exit_code -ne 0) {
        throw 'Gate SCA bloqueado por pip-audit.'
    }
    Write-Host 'Gates gitleaks e pip-audit concluídos sem bloqueio.'
}

# Executa toda a verificação de instalação sem criar, atualizar ou baixar qualquer componente.
function Invoke-Verification {
    $lock = Test-Lockfile
    [void](Test-VirtualEnvironment)
    # Um venv válido já é o runtime operacional; só use o Python base quando ele ainda não existe.
    [void](Test-PythonTarget)
    if ($lock) { Test-PythonPackages $lock }
    Test-Ffmpeg
    Test-WhisperModels
    [void](Test-Gitleaks)
    Test-Cuda
    if ($script:RuntimePython) { Test-PipCheck }
    if ($SecurityGates) { Invoke-SecurityGates }
}

try {
    $modeCount = @($Verify, $Install, $InstallFfmpeg, $InstallCuda, $InstallModels, $InstallGitleaks |
        Where-Object { $_ }).Count
    if ($modeCount -eq 0) {
        throw 'Nenhuma ação selecionada. Use -Verify ou uma opção -Install explícita.'
    }
    if ($Verify -and ($Install -or $InstallFfmpeg -or $InstallCuda -or $InstallModels -or $InstallGitleaks)) {
        throw '-Verify não pode ser combinado com opções de instalação.'
    }
    if ($SecurityGates -and -not $Verify) {
        throw '-SecurityGates exige -Verify para deixar o modo de execução explícito.'
    }

    $script:VenvAbsolutePath = Resolve-RepoPath $VenvPath -AllowMissing
    $script:ModelAbsolutePath = Resolve-RepoPath $ModelDir -AllowMissing
    Initialize-PythonInvoker

    if ($Install) { Install-PythonEnvironment }
    if ($InstallFfmpeg) { Install-LocalFfmpeg }
    if ($InstallGitleaks) { Install-LocalGitleaks }
    if ($InstallModels) { Install-LocalModels -Overwrite:$Force }
    if ($InstallCuda) { Install-LocalCuda }
    if ($Verify) { Invoke-Verification }

    if ($script:HasFailures) {
        exit 1
    }
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
