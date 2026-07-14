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
$privacyFile = Join-Path $env:TEMP 'mina_voice_monitor.mute'

if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found at $pythonExe"
}
if (-not (Test-Path $voiceScript)) {
    throw "Voice loop script not found at $voiceScript"
}

# Recover from prior crashed runs that may leave mute state latched.
if (Test-Path $privacyFile) {
    Remove-Item -Path $privacyFile -Force -ErrorAction SilentlyContinue
}

function Get-DefaultVoiceDevice {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe
    )

    $code = @'
import json
import sounddevice as sd

devices = sd.query_devices()
default_input = None
try:
    default_input = sd.default.device[0]
except Exception:
    default_input = None

if default_input is not None:
    try:
        default_input = int(default_input)
    except Exception:
        default_input = None

if default_input is not None:
    try:
        d = devices[default_input]
        if int(d.get("max_input_channels", 0) or 0) > 0:
            print(default_input)
            raise SystemExit(0)
    except Exception:
        pass

for i, d in enumerate(devices):
    try:
        if int(d.get("max_input_channels", 0) or 0) > 0:
            print(i)
            raise SystemExit(0)
    except Exception:
        continue

raise SystemExit(1)
'@

    $tmpPy = [System.IO.Path]::GetTempFileName() + '.py'
    try {
        Set-Content -Path $tmpPy -Value $code -Encoding UTF8
        $out = & $PythonExe $tmpPy
        if ($LASTEXITCODE -ne 0) {
            return $null
        }

        $text = ($out | Select-Object -Last 1)
        if ([string]::IsNullOrWhiteSpace($text)) {
            return $null
        }

        return [int]$text.Trim()
    }
    catch {
        return $null
    }
    finally {
        if (Test-Path $tmpPy) {
            Remove-Item -Path $tmpPy -Force -ErrorAction SilentlyContinue
        }
    }
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
    if ($VoiceDevice -lt 0) {
        $detectedDevice = Get-DefaultVoiceDevice -PythonExe $pythonExe
        if ($null -ne $detectedDevice) {
            $VoiceDevice = [int]$detectedDevice
            Write-MonitorLog "Auto-selected input device index: $VoiceDevice"
        }
        else {
            Write-MonitorLog "No usable input device detected; voice loop may fail until a device is configured."
        }
    }

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
