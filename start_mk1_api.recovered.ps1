param(
    [int]$Port = 8000,
    [string]$ApiHost = '127.0.0.1',
    [int]$EmbedPort = 8084,
    [string]$EmbedHost = '127.0.0.1',
    [switch]$Foreground
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -Path $PSScriptRoot
$pidFile = Join-Path $PSScriptRoot '.mk1_api.pid'
$embedScript = Join-Path $PSScriptRoot 'Memory_server\mk1_embed_server.py'
$embedHealthUrl = "http://${EmbedHost}:${EmbedPort}/health"

function Test-EmbedHealth {
    param(
        [string]$Url,
        [int]$TimeoutSec = 4
    )

    try {
        $resp = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec $TimeoutSec
        if ($resp.ok) {
            return $true
        }
        return $false
    }
    catch {
        return $false
    }
}

function Resolve-PythonPath {
    param([string]$PreferredPath)

    if (Test-Path $PreferredPath) {
        return $PreferredPath
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd -and $pythonCmd.Source) {
        return $pythonCmd.Source
    }

    throw "No Python executable found for embed server startup."
}

function Ensure-EmbedServer {
    param(
        [string]$HealthUrl,
        [string]$ScriptPath,
        [string]$PythonPath
    )

    if (Test-EmbedHealth -Url $HealthUrl) {
        Write-Host "Embed server healthy at $HealthUrl"
        return
    }

    if (-not (Test-Path $ScriptPath)) {
        throw "Embed server script not found: $ScriptPath"
    }

    Write-Host "Embed server not healthy; starting detached..."
    Start-Process -FilePath $PythonPath -ArgumentList $ScriptPath -WorkingDirectory $PSScriptRoot | Out-Null

    for ($i = 0; $i -lt 30; $i++) {
        if (Test-EmbedHealth -Url $HealthUrl) {
            Write-Host "Embed server started and healthy."
            return
        }
        Start-Sleep -Milliseconds 250
    }

    throw "Embed server failed to become healthy at $HealthUrl"
}

if (Test-Path $pidFile) {
    try {
        $oldPid = [int](Get-Content -Path $pidFile -Raw).Trim()
        $oldProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($oldProc) {
            Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped PID from pidfile: $oldPid"
        }
    }
    catch {
        Write-Warning "Could not process pidfile: $($_.Exception.Message)"
    }
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}

$pids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)

if ($pids.Count -gt 0) {
    foreach ($procId in $pids) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "Stopped process on port ${Port}: $procId"
        }
        catch {
            Write-Warning "Could not stop process $procId on port ${Port}: $($_.Exception.Message)"
        }
    }
}

$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found at $pythonExe"
}

$embedPython = Resolve-PythonPath -PreferredPath $pythonExe
Ensure-EmbedServer -HealthUrl $embedHealthUrl -ScriptPath $embedScript -PythonPath $embedPython

$uvicornArgs = @(
    "-m", "uvicorn", "agent.server.mk1_api:app",
    "--host", "$ApiHost",
    "--port", "$Port",
    "--no-access-log"
)

if ($Foreground) {
    Write-Host "Starting MK1 API in foreground on http://${ApiHost}:$Port"
    & $pythonExe @uvicornArgs
    exit $LASTEXITCODE
}

Write-Host "Starting MK1 API detached on http://${ApiHost}:$Port"
$proc = Start-Process -FilePath $pythonExe -ArgumentList $uvicornArgs -WorkingDirectory $PSScriptRoot -PassThru

$listenerPid = $null
for ($i = 0; $i -lt 20; $i++) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1 OwningProcess
    if ($listener) {
        $listenerPid = [int]$listener.OwningProcess
        break
    }
    Start-Sleep -Milliseconds 200
}

if ($null -ne $listenerPid) {
    Set-Content -Path $pidFile -Value $listenerPid -NoNewline
    Write-Host "MK1 API PID: $listenerPid"
}
else {
    Set-Content -Path $pidFile -Value $proc.Id -NoNewline
    Write-Warning "Could not resolve listener PID quickly; recorded parent PID $($proc.Id)"
}
