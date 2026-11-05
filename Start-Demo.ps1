param(
    [switch]$NoBrowser,
    [ValidateRange(1024, 65525)][int]$Port = 8787,
    [ValidateSet('tev1', 'tev1:0.8b')][string]$Model = 'tev1'
)

# Ablauf:
#   1. Kleine Python-Laufzeit bereitstellen (einmalig, ca. 11 MB)
#   2. Lokalen Demo-Server starten
#   3. Browser oeffnen. Ollama-Installation, Modell-Download und Modellstart
#      laufen im Demo-Server und werden im Browser mit Ladebalken angezeigt.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$AppVersion = 4
$taskRoot = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\')
$taskRuntime = Join-Path $taskRoot '.runtime'
New-Item -ItemType Directory -Path $taskRuntime -Force | Out-Null

function Get-DemoHealth([int]$DemoPort) {
    try { return Invoke-RestMethod "http://127.0.0.1:$DemoPort/api/health" -TimeoutSec 2 } catch { return $null }
}

function Format-Size([double]$Bytes) {
    if ($Bytes -ge 1e9) { return ('{0:N2} GB' -f ($Bytes / 1e9)) }
    return ('{0:N0} MB' -f ($Bytes / 1e6))
}

function Get-PortablePython {
    $taskPythonVersion = '3.13.16'
    $taskArch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { 'arm64' } else { 'amd64' }
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'Diese Demo benoetigt Windows 10/11 (64 Bit).' }
    $taskPythonDir = Join-Path $taskRuntime "python-$taskPythonVersion-$taskArch"
    $taskPythonExe = Join-Path $taskPythonDir 'python.exe'
    if (-not (Test-Path -LiteralPath $taskPythonExe)) {
        Write-Host '[1/3] Kleine Python-Laufzeit wird einmalig heruntergeladen (ca. 11 MB)...'
        $taskZip = Join-Path $taskRuntime "python-$taskPythonVersion-$taskArch.zip"
        $taskHash = if ($taskArch -eq 'arm64') { '790d097697a2020477549a6764d89d22194c88d6e42caebcb2db63791e004c94' } else { '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297' }
        Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/$taskPythonVersion/python-$taskPythonVersion-embed-$taskArch.zip" -OutFile $taskZip
        if ((Get-FileHash -LiteralPath $taskZip -Algorithm SHA256).Hash -ne $taskHash) { Remove-Item -LiteralPath $taskZip -Force; throw 'Python-Download unvollstaendig oder Pruefsumme ungueltig. Bitte erneut starten.' }
        Expand-Archive -LiteralPath $taskZip -DestinationPath $taskPythonDir -Force
        Remove-Item -LiteralPath $taskZip
    } else { Write-Host '[1/3] Python-Laufzeit ist vorhanden.' }
    & $taskPythonExe -c 'import http.server, urllib.request, ssl, json' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Die lokale Python-Laufzeit konnte nicht gestartet werden.' }
    return $taskPythonExe
}

try {
    $taskPython = Get-PortablePython

    Write-Host '[2/3] Demo-Server starten...'
    # Veraltete Demo-Server aus diesem Ordner (alte Version/anderes Modell) beenden.
    foreach ($taskCandidatePort in $Port..($Port + 9)) {
        $taskHealth = Get-DemoHealth $taskCandidatePort
        if ($taskHealth -and $taskHealth.app -eq 'tev1-consulting-demo' -and $taskHealth.directory -eq $taskRoot -and ($taskHealth.version -ne $AppVersion -or $taskHealth.model -ne $Model)) {
            Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like "*$taskRoot*server.py*" -and $_.CommandLine -like "*--port $taskCandidatePort*" } |
                ForEach-Object { Write-Host "Alte Demo-Version auf Port $taskCandidatePort wird beendet."; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
    Start-Sleep -Milliseconds 500

    # Laufenden Server dieses Ordners wiederverwenden, aber nur in der aktuellen Version.
    $taskReady = $false
    $taskSelectedPort = $null
    foreach ($taskCandidatePort in $Port..($Port + 9)) {
        $taskHealth = Get-DemoHealth $taskCandidatePort
        if ($taskHealth -and $taskHealth.app -eq 'tev1-consulting-demo' -and $taskHealth.version -eq $AppVersion -and $taskHealth.directory -eq $taskRoot -and $taskHealth.model -eq $Model) {
            $taskSelectedPort = $taskCandidatePort
            $taskReady = $true
            break
        }
        $taskTcp = New-Object Net.Sockets.TcpClient
        try { $taskTcp.Connect('127.0.0.1', $taskCandidatePort); $taskOccupied = $true } catch { $taskOccupied = $false } finally { $taskTcp.Dispose() }
        if (-not $taskOccupied) { $taskSelectedPort = $taskCandidatePort; break }
    }
    if (-not $taskSelectedPort) { throw 'Kein freier Demo-Port gefunden. Bitte den Rechner neu starten und erneut versuchen.' }
    $taskUrl = "http://127.0.0.1:$taskSelectedPort"
    if (-not $taskReady) {
        $taskServer = Join-Path $taskRoot 'server.py'
        $taskProcess = Start-Process -FilePath $taskPython -ArgumentList @('-u', ('"' + $taskServer + '"'), '--port', $taskSelectedPort, '--model', $Model) -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRuntime "server-$taskSelectedPort-output.log") -RedirectStandardError (Join-Path $taskRuntime "server-$taskSelectedPort-error.log")
        $taskDeadline = [DateTime]::UtcNow.AddSeconds(20)
        do {
            $taskHealth = Get-DemoHealth $taskSelectedPort
            if ($taskHealth -and $taskHealth.version -eq $AppVersion -and $taskHealth.directory -eq $taskRoot -and $taskHealth.model -eq $Model) { $taskReady = $true; break }
            if ($taskProcess.HasExited) { break }
            Start-Sleep -Milliseconds 300
        } while ([DateTime]::UtcNow -lt $taskDeadline)
    }
    if (-not $taskReady) { throw 'Der Demo-Server konnte nicht starten. Details stehen im Ordner .runtime.' }

    Write-Host "[3/3] Demo oeffnet sich im Browser: $taskUrl" -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process $taskUrl }
    Write-Host ''
    Write-Host 'Beim ersten Start werden Ollama und das Modell heruntergeladen (mehrere GB).'
    Write-Host 'Der Fortschritt steht im Browser und hier. Dieses Fenster bitte offen lassen.'
    Write-Host ''

    $taskLast = ''
    $taskShownError = ''
    while ($true) {
        try { $taskSetup = Invoke-RestMethod "$taskUrl/api/setup" -TimeoutSec 5 } catch { $taskSetup = $null }
        if (-not $taskSetup) {
            Write-Host "`nDer Demo-Server antwortet nicht mehr. Bitte Start-Demo.cmd erneut starten." -ForegroundColor Red
            exit 1
        }
        if ($taskSetup.ready) {
            Write-Host "`rAlles bereit. Die Demo laeuft unter $taskUrl                                        " -ForegroundColor Green
            break
        }
        if ($taskSetup.error) {
            if ($taskShownError -ne $taskSetup.error) {
                Write-Host "`n$($taskSetup.error)" -ForegroundColor Red
                Write-Host 'Im Browser kann "Erneut versuchen" geklickt werden.'
                $taskShownError = $taskSetup.error
            }
        } else {
            $taskShownError = ''
            $taskLine = $taskSetup.title
            if ($taskSetup.total_bytes -gt 0) {
                $taskPercent = [math]::Floor(100 * $taskSetup.done_bytes / $taskSetup.total_bytes)
                $taskLine += " $taskPercent % ($(Format-Size $taskSetup.done_bytes) von $(Format-Size $taskSetup.total_bytes))"
                if ($taskSetup.eta) { $taskLine += " - noch ca. $([math]::Ceiling($taskSetup.eta / 60)) Min." }
            }
            if ($taskLine -ne $taskLast) {
                Write-Host ("`r" + $taskLine.PadRight(100)) -NoNewline
                $taskLast = $taskLine
            }
        }
        Start-Sleep -Seconds 1
    }
    Write-Host 'Dieses Fenster kann jetzt geschlossen werden. Die Demo laeuft im Hintergrund weiter.'
    Start-Sleep -Seconds 8
} catch {
    Write-Host "`nStart fehlgeschlagen: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
