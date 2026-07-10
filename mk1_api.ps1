param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'restart', 'smoke', 'setup', 'gui', 'voice', 'voice-check', 'voice-devices', 'voice-monitor')]
    [string]$Action = 'status',

    [int]$Port = 8000,
    [int]$EmbedPort = 8084,
    [string]$EmbedHost = '127.0.0.1',
    [int]$VoiceDevice = -1,
    [string]$VoiceHint = 'en-US-AnaNeural',
    [switch]$GuiSpeak,
    [switch]$AutoVoice,
    [switch]$Foreground,
    [switch]$RunTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -Path $PSScriptRoot

$startScript = Join-Path $PSScriptRoot 'start_mk1_api.ps1'
$stopScript = Join-Path $PSScriptRoot 'stop_mk1_api.ps1'
$statusScript = Join-Path $PSScriptRoot 'status_mk1_api.ps1'
$setupScript = Join-Path $PSScriptRoot 'setup_mk1_env.ps1'
$guiScript = Join-Path $PSScriptRoot 'agent\gui\mina_gui.py'
$voiceScript = Join-Path $PSScriptRoot 'mina_windows_voice_loop.py'
$voiceMonitorScript = Join-Path $PSScriptRoot 'start_mina_voice_monitor.ps1'
$baseUrl = "http://127.0.0.1:$Port"
$embedBaseUrl = "http://${EmbedHost}:${EmbedPort}"

foreach ($scriptPath in @($startScript, $stopScript, $statusScript, $setupScript)) {
    if (-not (Test-Path $scriptPath)) {
        throw "Required script missing: $scriptPath"
    }
}

function Get-SafeExitCode {
    $last = Get-Variable -Name LASTEXITCODE -ErrorAction SilentlyContinue
    if ($null -eq $last) {
        return 0
    }
    return [int]$last.Value
}

function Test-VoicePythonDeps {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe
    )

    $code = @'
import importlib.util
mods = ["requests", "sounddevice", "soundfile", "multipart", "edge_tts", "pyttsx3", "faster_whisper"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
print("MISSING=" + ",".join(missing))
'@

    $tmpPy = [System.IO.Path]::GetTempFileName() + '.py'
    try {
        Set-Content -Path $tmpPy -Value $code -Encoding UTF8
        $out = & $PythonExe $tmpPy
    }
    finally {
        if (Test-Path $tmpPy) {
            Remove-Item -Path $tmpPy -Force -ErrorAction SilentlyContinue
        }
    }

    $line = ($out | Select-Object -Last 1)
    if ($null -eq $line -or $line -notmatch '^MISSING=') {
        return @()
    }

    $raw = ($line -replace '^MISSING=', '').Trim()
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @()
    }
    return ($raw -split ',') | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
}

function Show-VoiceInputDevices {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe
    )

    $code = @'
import sounddevice as sd
import re

devices = sd.query_devices()
for i, d in enumerate(devices):
    max_in = int(d.get("max_input_channels", 0) or 0)
    if max_in > 0:
        name = str(d.get("name", ""))
        # Remove control chars that can break terminal rendering (e.g. CR/backspace)
        name = re.sub(r"[\x00-\x1f\x7f]", " ", name)
        name = re.sub(r"\s+", " ", name).strip()
        name = name.encode("ascii", "replace").decode("ascii")
        hostapi = d.get("hostapi", "?")
        sr = d.get("default_samplerate", "?")
        print(f"[{i}] {name} | in={max_in} | hostapi={hostapi} | default_sr={sr}")
'@

    $tmpPy = [System.IO.Path]::GetTempFileName() + '.py'
    try {
        Set-Content -Path $tmpPy -Value $code -Encoding UTF8
        & $PythonExe $tmpPy
    }
    finally {
        if (Test-Path $tmpPy) {
            Remove-Item -Path $tmpPy -Force -ErrorAction SilentlyContinue
        }
    }
}

function Start-VoiceLoopDetached {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe,
        [Parameter(Mandatory = $true)]
        [string]$VoiceScript,
        [Parameter(Mandatory = $true)]
        [string]$ApiUrl,
        [int]$DeviceIndex = -1,
        [string]$VoiceName = 'en-US-AnaNeural'
    )

    if (-not (Test-Path $PythonExe)) {
        throw "Python venv not found at $PythonExe"
    }
    if (-not (Test-Path $VoiceScript)) {
        throw "Voice script missing: $VoiceScript"
    }

    $argParts = @(
        ('"' + $PythonExe + '"'),
        ('"' + $VoiceScript + '"'),
        '--api',
        ('"' + $ApiUrl + '"'),
        '--speak-response',
        '--voice-hint',
        ('"' + $VoiceName + '"')
    )

    if ($DeviceIndex -ge 0) {
        $argParts += @('--device', [string]$DeviceIndex)
    }

    $cmd = 'Set-Location -Path "' + $PSScriptRoot + '"; & ' + ($argParts -join ' ')
    Start-Process powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $cmd) | Out-Null
}

function Start-VoiceMonitorDetached {
    param(
        [Parameter(Mandatory = $true)]
        [string]$MonitorScript,
        [Parameter(Mandatory = $true)]
        [string]$ApiUrl,
        [int]$DeviceIndex = -1,
        [string]$VoiceName = 'en-US-AnaNeural'
    )

    if (-not (Test-Path $MonitorScript)) {
        throw "Voice monitor script missing: $MonitorScript"
    }

    $cmd = '& "' + $MonitorScript + '" -ApiUrl "' + $ApiUrl + '" -VoiceDevice ' + [string]$DeviceIndex + ' -VoiceHint "' + $VoiceName + '"'
    Start-Process powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $cmd) | Out-Null
}

function Invoke-ApiStart {
    param(
        [Parameter(Mandatory = $true)]
        [string]$StartScriptPath,
        [int]$ApiPort,
        [switch]$RunForeground,
        [switch]$EnableGuiSpeak,
        [string]$GuiVoiceHint
    )

    $prevAuto = $env:MK1_PROCESS_AUTO_SPEAK
    $prevVoice = $env:MK1_PROCESS_VOICE
    try {
        if ($EnableGuiSpeak) {
            $env:MK1_PROCESS_AUTO_SPEAK = '1'
            $env:MK1_PROCESS_VOICE = $GuiVoiceHint
            Write-Host "GUI Speak enabled for API process."
            Write-Host "GUI Speak voice:" $GuiVoiceHint
        }

        if ($RunForeground) {
            & $StartScriptPath -Port $ApiPort -Foreground
        }
        else {
            & $StartScriptPath -Port $ApiPort
        }
    }
    finally {
        if ($null -ne $prevAuto) {
            $env:MK1_PROCESS_AUTO_SPEAK = $prevAuto
        }
        else {
            Remove-Item Env:MK1_PROCESS_AUTO_SPEAK -ErrorAction SilentlyContinue
        }

        if ($null -ne $prevVoice) {
            $env:MK1_PROCESS_VOICE = $prevVoice
        }
        else {
            Remove-Item Env:MK1_PROCESS_VOICE -ErrorAction SilentlyContinue
        }
    }
}

switch ($Action) {
    'start' {
        if ($Foreground) {
            Invoke-ApiStart -StartScriptPath $startScript -ApiPort $Port -RunForeground -EnableGuiSpeak:$GuiSpeak -GuiVoiceHint $VoiceHint
        }
        else {
            Invoke-ApiStart -StartScriptPath $startScript -ApiPort $Port -EnableGuiSpeak:$GuiSpeak -GuiVoiceHint $VoiceHint

            if ($AutoVoice) {
                Start-VoiceMonitorDetached -MonitorScript $voiceMonitorScript -ApiUrl $baseUrl -DeviceIndex $VoiceDevice -VoiceName $VoiceHint
                Write-Host "AutoVoice: launched Mina voice monitor in a new window."
            }
        }
        exit (Get-SafeExitCode)
    }

    'stop' {
        & $stopScript -Port $Port
        exit (Get-SafeExitCode)
    }

    'status' {
        & $statusScript -Port $Port
        exit (Get-SafeExitCode)
    }

    'restart' {
        & $stopScript -Port $Port
        $stopCode = Get-SafeExitCode

        # stop can return non-zero if nothing is listening; continue with start in that case.
        if ($stopCode -ne 0) {
            Write-Host "Restart notice: stop returned code $stopCode, continuing with start..."
        }

        if ($Foreground) {
            Invoke-ApiStart -StartScriptPath $startScript -ApiPort $Port -RunForeground -EnableGuiSpeak:$GuiSpeak -GuiVoiceHint $VoiceHint
        }
        else {
            Invoke-ApiStart -StartScriptPath $startScript -ApiPort $Port -EnableGuiSpeak:$GuiSpeak -GuiVoiceHint $VoiceHint

            if ($AutoVoice) {
                Start-VoiceMonitorDetached -MonitorScript $voiceMonitorScript -ApiUrl $baseUrl -DeviceIndex $VoiceDevice -VoiceName $VoiceHint
                Write-Host "AutoVoice: launched Mina voice monitor in a new window."
            }
        }
        exit (Get-SafeExitCode)
    }

    'smoke' {
        & $statusScript -Port $Port
        $statusCode = Get-SafeExitCode
        if ($statusCode -ne 0) {
            Write-Error "Smoke failed: status check failed."
            exit $statusCode
        }

        try {
            $embedHealth = Invoke-RestMethod -Uri "$embedBaseUrl/health" -Method Get -TimeoutSec 5
            if (-not $embedHealth.ok) {
                Write-Error "Smoke failed: embed /health returned non-ok payload."
                exit 1
            }

            Write-Host "SMOKE embed /health: OK"

            $body = @{ input = 'what time is it?' } | ConvertTo-Json -Compress
            $resp = Invoke-RestMethod -Uri "$baseUrl/process" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 15
            $reply = $resp.reply
            if ([string]::IsNullOrWhiteSpace($reply)) {
                Write-Error "Smoke failed: /process returned empty reply."
                exit 1
            }

            Write-Host "SMOKE /process reply:" $reply
            Write-Host "Smoke check passed."
            exit 0
        }
        catch {
            Write-Error "Smoke failed: $($_.Exception.Message)"
            exit 1
        }
    }

    'setup' {
        if ($RunTests) {
            & $setupScript -RunTests
        }
        else {
            & $setupScript
        }
        exit (Get-SafeExitCode)
    }

    'gui' {
        if (-not (Test-Path $guiScript)) {
            throw "GUI script missing: $guiScript"
        }

        $py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path $py)) {
            throw "Python venv not found at $py. Run: .\mk1_api.ps1 setup"
        }

        & $py $guiScript
        exit (Get-SafeExitCode)
    }

    'voice' {
        if (-not (Test-Path $voiceScript)) {
            throw "Voice script missing: $voiceScript"
        }

        $py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path $py)) {
            throw "Python venv not found at $py. Run: .\mk1_api.ps1 setup"
        }

        if ($VoiceDevice -ge 0) {
            Write-Host "Using input device index: $VoiceDevice"
            & $py $voiceScript --api $baseUrl --speak-response --device $VoiceDevice --voice-hint $VoiceHint
        }
        else {
            & $py $voiceScript --api $baseUrl --speak-response --voice-hint $VoiceHint
        }
        exit (Get-SafeExitCode)
    }

    'voice-monitor' {
        if (-not (Test-Path $voiceMonitorScript)) {
            throw "Voice monitor script missing: $voiceMonitorScript"
        }

        & $voiceMonitorScript -ApiUrl $baseUrl -VoiceDevice $VoiceDevice -VoiceHint $VoiceHint
        exit (Get-SafeExitCode)
    }

    'voice-check' {
        $py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path $py)) {
            Write-Error "Python venv not found at $py. Run: .\mk1_api.ps1 setup"
            exit 1
        }

        Write-Host "VOICE CHECK: Python deps"
        $missing = @(Test-VoicePythonDeps -PythonExe $py)
        if ($missing.Count -eq 0) {
            Write-Host "  OK: requests, sounddevice, soundfile, multipart, edge_tts, pyttsx3, faster_whisper"
        }
        else {
            Write-Warning ("  Missing modules: " + ($missing -join ', '))
            Write-Host "  Install with: $py -m pip install" ($missing -join ' ')
        }

        Write-Host "VOICE CHECK: preferred voice"
        Write-Host "  VoiceHint:" $VoiceHint

        Write-Host "VOICE CHECK: ffplay"
        $ff = Get-Command ffplay -ErrorAction SilentlyContinue
        if ($null -ne $ff) {
            Write-Host "  OK: ffplay found at $($ff.Source)"
        }
        else {
            Write-Warning "  ffplay not found (fallback player will be used)."
        }

        Write-Host "VOICE CHECK: API status"
        try {
            $statusResp = Invoke-WebRequest -Uri "$baseUrl/status" -Method Get -TimeoutSec 5 -UseBasicParsing
            Write-Host "  OK: /status reachable (HTTP $($statusResp.StatusCode))"
        }
        catch {
            $resp = $_.Exception.Response
            if ($null -ne $resp) {
                Write-Warning "  /status responded with HTTP $($resp.StatusCode.value__)"
            }
            else {
                Write-Warning "  API not reachable at $baseUrl"
            }
        }

        Write-Host "VOICE CHECK: TTS endpoint"
        try {
            $body = @{ text = 'voice check ping'; voice_hint = $VoiceHint } | ConvertTo-Json -Compress
            $tts = Invoke-RestMethod -Uri "$baseUrl/voice/tts" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 20
            if ($tts.ok) {
                Write-Host "  OK: /voice/tts returned audio path" $tts.audio_path
                if ($tts.engine) {
                    Write-Host "  Engine:" $tts.engine
                }
                if ($tts.voice) {
                    Write-Host "  Voice:" $tts.voice
                }
            }
            else {
                Write-Warning ("  /voice/tts returned: " + ($tts.error | Out-String).Trim())
            }
        }
        catch {
            Write-Warning "  /voice/tts check failed: $($_.Exception.Message)"
        }

        if ($missing.Count -gt 0) {
            exit 1
        }
        exit 0
    }

    'voice-devices' {
        $py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path $py)) {
            Write-Error "Python venv not found at $py. Run: .\mk1_api.ps1 setup"
            exit 1
        }

        Write-Host "VOICE INPUT DEVICES:"
        try {
            Show-VoiceInputDevices -PythonExe $py
        }
        catch {
            Write-Error "Could not list voice input devices: $($_.Exception.Message)"
            exit 1
        }

        Write-Host "Tip: run .\mk1_api.ps1 voice -VoiceDevice <index>"
        exit 0
    }
}
