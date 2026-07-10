param(
    [string]$ApiUrl = 'http://127.0.0.1:8000',
    [int]$VoiceDevice = -1,
    [string]$VoiceHint = 'en-US-AnaNeural'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$pidFile = Join-Path $PSScriptRoot '.mk1_voice_monitor.pid'
$pythonExe = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$voiceScript = Join-Path $PSScriptRoot 'mina_windows_voice_loop.py'
$logPath = Join-Path $PSScriptRoot 'logs\voice_monitor.log'
$logDir = Split-Path -Parent $logPath

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found at $pythonExe"
}
if (-not (Test-Path $voiceScript)) {
    throw "Voice loop script not found at $voiceScript"
}

if (Test-Path $pidFile) {
    try {
        $oldPid = [int](Get-Content -Path $pidFile -Raw).Trim()
        $oldProc = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($oldProc) {
            Write-Host "Voice monitor already running (PID: $oldPid)."
            exit 0
        }
    }
    catch {
    }
}

Set-Content -Path $pidFile -Value $PID -NoNewline

function Write-MonitorLog {
    param([string]$Message)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $Message"
    Write-Host $line
    Add-Content -Path $logPath -Value $line
}

function Test-ApiUp {
    param([string]$Url)
    try {
        $resp = Invoke-WebRequest -Uri ($Url.TrimEnd('/') + '/status') -Method Get -TimeoutSec 5 -UseBasicParsing
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    }
    catch {
        return $false
    }
}

try {
    Write-MonitorLog "Voice monitor started. ApiUrl=$ApiUrl VoiceDevice=$VoiceDevice VoiceHint=$VoiceHint"

    while ($true) {
        if (-not (Test-ApiUp -Url $ApiUrl)) {
            Write-MonitorLog "API not reachable. Waiting before retry..."
            Start-Sleep -Seconds 2
            continue
        }

        $args = @(
            $voiceScript,
            '--api', $ApiUrl,
            '--speak-response',
            '--voice-hint', $VoiceHint,
            '--continuous',
            '--speech-threshold', '0.002',
            '--min-speech-ms', '120'
        )
        if ($VoiceDevice -ge 0) {
            $args += @('--device', [string]$VoiceDevice)
        }

        Write-MonitorLog "Launching voice loop..."
        & $pythonExe @args
        $exitCode = $LASTEXITCODE
        Write-MonitorLog "Voice loop exited with code $exitCode. Restarting in 2 seconds..."
        Start-Sleep -Seconds 2
    }
}
finally {
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}
