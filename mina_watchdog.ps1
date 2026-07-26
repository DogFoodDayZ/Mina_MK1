param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'start', 'stop', 'status', 'once', 'reset', 'register', 'unregister')]
    [string]$Action = 'status',
    [string]$WatchdogDir = 'E:\workspace\mina_watchdog',
    [string]$BackupDir = 'E:\workspace\mina_code_backups',
    [int]$Port = 8000,
    [int]$EmbedPort = 8084,
    [int]$IntervalSeconds = 15
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$sourceRuntime = Join-Path $projectRoot 'watchdog\mina_watchdog.py'
$runtimeDir = Join-Path $WatchdogDir 'runtime'
$externalRuntime = Join-Path $runtimeDir 'mina_watchdog.py'
$pidFile = Join-Path $WatchdogDir 'watchdog.pid'
$processLog = Join-Path $WatchdogDir 'watchdog_process.log'
$processErrorLog = Join-Path $WatchdogDir 'watchdog_process_error.log'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$startupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$startupLauncher = Join-Path $startupDir 'MinaMK1Watchdog.cmd'

function Install-WatchdogRuntime {
    if (-not (Test-Path $sourceRuntime)) { throw "Watchdog runtime missing: $sourceRuntime" }
    if (-not (Test-Path $pythonExe)) { throw "Python runtime missing: $pythonExe" }
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    Copy-Item -LiteralPath $sourceRuntime -Destination $externalRuntime -Force
    Write-Host "Installed independent watchdog runtime: $externalRuntime"
}

function Get-WatchdogArguments {
    param([string]$RuntimeAction)
    return @(
        $externalRuntime, $RuntimeAction,
        '--project-root', $projectRoot,
        '--watchdog-dir', $WatchdogDir,
        '--backup-dir', $BackupDir,
        '--port', [string]$Port,
        '--embed-port', [string]$EmbedPort,
        '--interval', [string]$IntervalSeconds
    )
}

function Get-WatchdogProcess {
    if (-not (Test-Path $pidFile)) { return $null }
    try {
        $watchdogPid = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
        $process = Get-Process -Id $watchdogPid -ErrorAction SilentlyContinue
        if ($null -eq $process) { return $null }
        $details = Get-CimInstance Win32_Process -Filter "ProcessId = $watchdogPid" -ErrorAction SilentlyContinue
        if ($null -eq $details -or $details.CommandLine -notlike '*mina_watchdog.py*monitor*') {
            Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
            return $null
        }
        return $process
    }
    catch { return $null }
}

switch ($Action) {
    'install' {
        Install-WatchdogRuntime
        exit 0
    }
    'start' {
        Install-WatchdogRuntime
        $existing = Get-WatchdogProcess
        if ($null -ne $existing) {
            Write-Host "Mina watchdog already running (PID $($existing.Id))."
            exit 0
        }
        New-Item -ItemType Directory -Path $WatchdogDir -Force | Out-Null
        $arguments = Get-WatchdogArguments -RuntimeAction 'monitor'
        $process = Start-Process -FilePath $pythonExe -ArgumentList $arguments -WorkingDirectory $WatchdogDir -RedirectStandardOutput $processLog -RedirectStandardError $processErrorLog -WindowStyle Hidden -PassThru
        Set-Content -LiteralPath $pidFile -Value $process.Id -NoNewline
        Write-Host "Mina watchdog started (PID $($process.Id))."
        exit 0
    }
    'stop' {
        $existing = Get-WatchdogProcess
        if ($null -ne $existing) {
            & taskkill.exe /PID $existing.Id /T /F | Out-Null
            Write-Host "Mina watchdog stopped (PID $($existing.Id))."
        }
        else { Write-Host 'Mina watchdog is not running.' }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        exit 0
    }
    'status' {
        $existing = Get-WatchdogProcess
        if ($null -ne $existing) { Write-Host "Mina watchdog process: RUNNING (PID $($existing.Id))" }
        else { Write-Host 'Mina watchdog process: STOPPED' }
        if (Test-Path $startupLauncher) { Write-Host "Windows logon startup: REGISTERED ($startupLauncher)" }
        else { Write-Host 'Windows logon startup: NOT REGISTERED' }
        if (Test-Path $externalRuntime) {
            & $pythonExe @(Get-WatchdogArguments -RuntimeAction 'status')
            exit $LASTEXITCODE
        }
        Write-Host 'Watchdog runtime is not installed.'
        exit 1
    }
    'once' {
        Install-WatchdogRuntime
        & $pythonExe @(Get-WatchdogArguments -RuntimeAction 'once')
        exit $LASTEXITCODE
    }
    'reset' {
        Install-WatchdogRuntime
        & $pythonExe @(Get-WatchdogArguments -RuntimeAction 'reset')
        exit $LASTEXITCODE
    }
    'register' {
        Install-WatchdogRuntime
        New-Item -ItemType Directory -Path $startupDir -Force | Out-Null
        $launcher = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" start -WatchdogDir `"$WatchdogDir`" -BackupDir `"$BackupDir`" -Port $Port -EmbedPort $EmbedPort -IntervalSeconds $IntervalSeconds`r`n"
        Set-Content -LiteralPath $startupLauncher -Value $launcher -Encoding ASCII -NoNewline
        Write-Host "Registered current-user Windows logon startup: $startupLauncher"
        exit 0
    }
    'unregister' {
        Remove-Item -LiteralPath $startupLauncher -Force -ErrorAction SilentlyContinue
        Write-Host "Removed Windows logon startup: $startupLauncher"
        exit 0
    }
}