param(
    [switch]$NoBrowser,
    [switch]$SkipWarmup,
    [ValidateRange(1024, 65525)][int]$Port = 8787,
    [ValidateSet('tev1', 'tev1:0.8b')][string]$Model = 'tev1'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$taskRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$taskRuntime = Join-Path $taskRoot '.runtime'
New-Item -ItemType Directory -Path $taskRuntime -Force | Out-Null

function Get-DemoHealth([int]$DemoPort) {
    try { return Invoke-RestMethod "http://127.0.0.1:$DemoPort/api/health" -TimeoutSec 2 } catch { return $null }
}

function Get-OllamaVersion {
    try { return [version](Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2).version } catch { return $null }
}

function Find-Ollama {
    $taskCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($taskCommand) { return $taskCommand.Source }
    $taskCandidate = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    if (Test-Path -LiteralPath $taskCandidate) { return $taskCandidate }
    return $null
}

function Get-PortablePython {
    $taskPythonVersion = '3.13.16'
    $taskArch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'arm64' } else { 'amd64' }
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Diese Demo benoetigt Windows 10/11 (64 Bit).' }
    $taskPythonDir = Join-Path $taskRuntime "python-$taskPythonVersion-$taskArch"
    $taskPythonExe = Join-Path $taskPythonDir 'python.exe'
    if (-not (Test-Path -LiteralPath $taskPythonExe)) {
        Write-Host '[2/4] Kleine Python-Laufzeit wird einmalig heruntergeladen (ca. 11 MB)...'
        $taskZip = Join-Path $taskRuntime "python-$taskPythonVersion-$taskArch.zip"
        $taskHash = if ($taskArch -eq 'arm64') { '790d097697a2020477549a6764d89d22194c88d6e42caebcb2db63791e004c94' } else { '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297' }
        Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/$taskPythonVersion/python-$taskPythonVersion-embed-$taskArch.zip" -OutFile $taskZip
        if ((Get-FileHash -LiteralPath $taskZip -Algorithm SHA256).Hash -ne $taskHash) { throw 'Python-Download unvollstaendig oder Pruefsumme ungueltig. Bitte erneut starten.' }
        Expand-Archive -LiteralPath $taskZip -DestinationPath $taskPythonDir -Force
        Remove-Item -LiteralPath $taskZip
    } else { Write-Host '[2/4] Python-Laufzeit ist vorhanden.' }
    & $taskPythonExe -c 'import http.server, urllib.request, ssl, json' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Die lokale Python-Laufzeit konnte nicht gestartet werden.' }
    return $taskPythonExe
}

try {
    Write-Host '[1/4] Ollama pruefen...'
    $taskOllama = Find-Ollama
    $taskVersion = Get-OllamaVersion
    if (-not $taskOllama -or ($taskVersion -and $taskVersion -lt [version]'0.35.0')) {
        Write-Host 'Ollama wird ueber das offizielle Windows-Skript installiert/aktualisiert.'
        Write-Host 'Der erste Download ist groesser und kann mehrere Minuten dauern.'
        $taskInstallerScript = Join-Path $taskRuntime 'install-ollama.ps1'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://ollama.com/install.ps1' -OutFile $taskInstallerScript
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $taskInstallerScript
        if ($LASTEXITCODE -ne 0) { throw 'Ollama-Installation fehlgeschlagen. Bitte https://ollama.com/download/windows oeffnen.' }
        $taskOllama = Find-Ollama
    }
    if (-not $taskOllama) { throw 'Ollama wurde nicht gefunden. Bitte die Demo nach der Ollama-Installation erneut starten.' }
    if (-not (Get-OllamaVersion)) {
        Start-Process -FilePath $taskOllama -ArgumentList 'serve' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskRuntime 'ollama-output.log') -RedirectStandardError (Join-Path $taskRuntime 'ollama-error.log') | Out-Null
    }
    $taskDeadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $taskVersion = Get-OllamaVersion
        if ($taskVersion) { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    if (-not $taskVersion) { throw 'Ollama startet nicht. Bitte .runtime\ollama-error.log ansehen.' }
    if ($taskVersion -lt [version]'0.35.0') { throw 'Der laufende Ollama-Dienst ist zu alt. Bitte Ollama beenden und diese Datei erneut starten.' }

    $taskPython = Get-PortablePython
    Write-Host "[3/4] Modell $Model pruefen..."
    $taskTags = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 10
    $taskModelName = if ($Model -eq 'tev1') { 'tev1:latest' } else { $Model }
    if (-not (@($taskTags.models | Where-Object { $_.name -eq $taskModelName }).Count)) {
        Write-Host 'Das Modell wird einmalig heruntergeladen (Tev1 4B: ca. 4,6 GB).'
        & $taskOllama pull $Model
        if ($LASTEXITCODE -ne 0) { throw 'Modell-Download fehlgeschlagen. Internet und freien Speicher pruefen, dann erneut starten. Ollama setzt den Download fort.' }
    }

    # Reuse this checkout's server; avoid interfering with other local projects.
    $taskReady = $false
    $taskSelectedPort = $null
    foreach ($taskCandidatePort in $Port..($Port + 9)) {
        $taskHealth = Get-DemoHealth $taskCandidatePort
        if ($taskHealth -and $taskHealth.app -eq 'tev1-consulting-demo' -and $taskHealth.directory -eq $taskRoot -and $taskHealth.model -eq $Model) {
            $taskSelectedPort = $taskCandidatePort
            $taskReady = $true
            break
        }
        $taskTcp = New-Object Net.Sockets.TcpClient
        try { $taskTcp.Connect('127.0.0.1', $taskCandidatePort); $taskOccupied=$true } catch { $taskOccupied=$false } finally { $taskTcp.Dispose() }
        if (-not $taskOccupied) { $taskSelectedPort=$taskCandidatePort; break }
    }
    if (-not $taskSelectedPort) { throw 'Kein freier Demo-Port gefunden. Bitte spaeter erneut versuchen.' }
    $taskUrl = "http://127.0.0.1:$taskSelectedPort"
    if (-not $taskReady) {
        $taskServer = Join-Path $taskRoot 'server.py'
        $taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-u', ('"' + $taskServer + '"'), '--port', $taskSelectedPort, '--model', $Model) -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRuntime "server-$taskSelectedPort-output.log") -RedirectStandardError (Join-Path $taskRuntime "server-$taskSelectedPort-error.log")
        $taskDeadline = [DateTime]::UtcNow.AddSeconds(20)
        do {
            $taskHealth = Get-DemoHealth $taskSelectedPort
            if ($taskHealth -and $taskHealth.directory -eq $taskRoot -and $taskHealth.model -eq $Model) { $taskReady=$true; break }
            if ($taskProcess.HasExited) { break }
            Start-Sleep -Milliseconds 300
        } while ([DateTime]::UtcNow -lt $taskDeadline)
    }
    if (-not $taskReady) { throw 'Der Demo-Server konnte nicht starten. Details stehen im Ordner .runtime.' }
    Write-Host '[4/4] Modell laden und Demo vorbereiten...'
    if (-not $SkipWarmup) {
        Write-Host 'Auf einem Laptop ohne starke Grafikkarte kann das Laden und die erste Anfrage laenger dauern.'
        $taskWarmup = Invoke-RestMethod "$taskUrl/api/warmup" -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 660
        Write-Host 'Modell ist geladen.'
    }
    Write-Host "Fertig: $taskUrl" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $taskUrl }
} catch {
    Write-Host "`nStart fehlgeschlagen: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
