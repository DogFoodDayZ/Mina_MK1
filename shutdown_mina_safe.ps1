param(
    [int]$Port = 8000,
    [int]$EmbedPort = 8084,
    [int]$FlushMaxItems = 400,
    [int]$WaitForBufferSeconds = 45,
    [int]$BufferPollSeconds = 2,
    [int]$PostFlushDelayMs = 1200,
    [switch]$ForceAfterTimeout,
    [string]$GuiDir = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -Path $PSScriptRoot

$baseUrl = "http://127.0.0.1:$Port"
$stopScript = Join-Path $PSScriptRoot 'stop_mk1_api.ps1'
$shutdownRefPath = Join-Path $PSScriptRoot '.mk1_last_shutdown.json'
$requestedAt = (Get-Date).ToString('s')

if (-not (Test-Path $stopScript)) {
    throw "Required script missing: $stopScript"
}

function Resolve-FancyGuiDir {
    param([string]$CliGuiDir)

    $override = ($CliGuiDir -as [string]).Trim()
    if (-not [string]::IsNullOrWhiteSpace($override)) {
        return $override
    }

    $envGui = ($env:MK1_FANCY_GUI_DIR -as [string]).Trim()
    if (-not [string]::IsNullOrWhiteSpace($envGui)) {
        return $envGui
    }

    $configPath = Join-Path $PSScriptRoot 'config\mk1_config.json'
    if (Test-Path $configPath) {
        try {
            $cfg = Get-Content -Path $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $cfgGui = ($cfg.gui.fancy_dir -as [string]).Trim()
            if (-not [string]::IsNullOrWhiteSpace($cfgGui)) {
                return $cfgGui
            }
        }
        catch {
        }
    }

    return 'C:\dev\mina-gui'
}

function Stop-GuiProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedGuiDir
    )

    $stopped = @()
    $needle = $ResolvedGuiDir.ToLowerInvariant()

    try {
        $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and (
                $_.CommandLine.ToLowerInvariant().Contains($needle) -or
                $_.CommandLine.ToLowerInvariant().Contains('agent\\gui\\mina_gui.py')
            )
        }

        foreach ($p in $procs) {
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                $stopped += [string]$p.ProcessId
            }
            catch {
                Write-Warning "Could not stop GUI process $($p.ProcessId): $($_.Exception.Message)"
            }
        }
    }
    catch {
        Write-Warning "Could not enumerate GUI processes: $($_.Exception.Message)"
    }

    return $stopped
}

function Stop-EmbedServer {
    param(
        [int]$TargetPort = 8084
    )

    $stoppedAny = $false
    $pids = @(Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)

    foreach ($procId in $pids) {
        try {
            Stop-Process -Id $procId -Force -ErrorAction Stop
            Write-Host "Stopped embed listener on port $TargetPort pid $procId"
            $stoppedAny = $true
        }
        catch {
        }
    }

    try {
        $embedProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match 'mk1_embed_server\.py|embed_server\.py' -and
            $_.Name -match 'python'
        }

        foreach ($p in $embedProcs) {
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                Write-Host "Stopped embed process: $($p.ProcessId)"
                $stoppedAny = $true
            }
            catch {
            }
        }
    }
    catch {
    }

    if (-not $stoppedAny) {
        Write-Host "No embed/database server process found to stop."
    }
}

$flushResult = $null
$flushError = $null
$gateResult = $null
$gateError = $null
$commitResult = $null
$commitError = $null
$guiStopped = @()

# 1) Stop GUI first.
$resolvedGuiDir = Resolve-FancyGuiDir -CliGuiDir $GuiDir
$guiStopped = @(Stop-GuiProcesses -ResolvedGuiDir $resolvedGuiDir)
if ($guiStopped.Count -gt 0) {
    Write-Host "Stopped GUI processes:" ($guiStopped -join ', ')
}
else {
    Write-Host "No GUI process found to stop."
}

try {
    $gateUri = "$baseUrl/system/shutdown_gate?force=$($ForceAfterTimeout.IsPresent)&max_wait_seconds=$WaitForBufferSeconds&poll_seconds=$BufferPollSeconds&flush_max_items=$FlushMaxItems"
    $gateResult = Invoke-RestMethod -Uri $gateUri -Method Post -TimeoutSec ([Math]::Max(15, $WaitForBufferSeconds + 15))
    if (-not $gateResult.ok) {
        throw "Shutdown gate failed: $($gateResult.error)"
    }
    Write-Host "Shutdown gate cleared. Buffer status ready for safe stop."
}
catch {
    $gateError = $_.Exception.Message
    Write-Warning "Shutdown gate failed: $gateError"

    if (-not $ForceAfterTimeout.IsPresent) {
        Write-Error "Aborting safe shutdown because buffer gate did not clear and force override was not set."
        exit 2
    }

    Write-Warning "Force override enabled; continuing shutdown sequence."
}

try {
    $flushUri = "$baseUrl/memory/flush_buffer?max_items=$FlushMaxItems"
    $flushResult = Invoke-RestMethod -Uri $flushUri -Method Post -TimeoutSec 10
    Write-Host "Final flush attempted before core stop."
}
catch {
    $flushError = $_.Exception.Message
    Write-Warning "Could not run final flush before core stop: $flushError"
}

$safeDelayMs = [Math]::Max(0, [Math]::Min(15000, $PostFlushDelayMs))
if ($safeDelayMs -gt 0) {
    Write-Host "Settling buffer writes for $safeDelayMs ms before core stop..."
    Start-Sleep -Milliseconds $safeDelayMs
}

$payload = [ordered]@{
    requested_at            = $requestedAt
    shutdown_port           = $Port
    embed_port              = $EmbedPort
    gui_dir                 = $resolvedGuiDir
    gui_pids_stopped        = $guiStopped
    wait_for_buffer_seconds = $WaitForBufferSeconds
    buffer_poll_seconds     = $BufferPollSeconds
    post_flush_delay_ms     = $safeDelayMs
    force_after_timeout     = [bool]$ForceAfterTimeout
    shutdown_gate_error     = $gateError
    shutdown_gate_result    = $gateResult
    flush_attempted         = $true
    flush_error             = $flushError
    flush_result            = $flushResult
    shutdown_commit_error   = $null
    shutdown_commit_result  = $null
}

try {
    $commitUri = "$baseUrl/system/shutdown_commit"
    $commitResult = Invoke-RestMethod -Uri $commitUri -Method Post -TimeoutSec ([Math]::Max(30, $WaitForBufferSeconds + 30))
    if (-not $commitResult.ok) {
        throw "Shutdown commit failed: $($commitResult.error)"
    }
    Write-Host "Memory close committed. Waiting for core API process to exit..."
}
catch {
    $commitError = $_.Exception.Message
    Write-Error "Aborting safe shutdown because the core did not confirm memory close: $commitError"
    exit 3
}

$payload.shutdown_commit_error = $commitError
$payload.shutdown_commit_result = $commitResult

try {
    $payload | ConvertTo-Json -Depth 8 | Set-Content -Path $shutdownRefPath -Encoding UTF8
}
catch {
    Write-Warning "Could not write shutdown reference file: $($_.Exception.Message)"
}

# 2) The commit endpoint exits only after memory close and final backup finish.
$coreExited = $false
for ($i = 0; $i -lt 60; $i++) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
    if (-not $listener) {
        $coreExited = $true
        break
    }
    Start-Sleep -Milliseconds 250
}

if (-not $coreExited) {
    Write-Warning "Core remained alive after confirmed memory close; stopping the closed process."
    & $stopScript -Port $Port
    $stopCode = $LASTEXITCODE
    if ($stopCode -ne 0) {
        Write-Error "Safe shutdown stop step failed with code $stopCode"
        exit $stopCode
    }
}
else {
    Remove-Item -Path (Join-Path $PSScriptRoot '.mk1_api.pid') -Force -ErrorAction SilentlyContinue
    Write-Host "Core API exited after graceful memory close."
}

# 3) Stop embed/database server last.
Stop-EmbedServer -TargetPort $EmbedPort

Write-Host "Mina safe shutdown complete (order: GUI -> core -> database/embed)."
exit 0
