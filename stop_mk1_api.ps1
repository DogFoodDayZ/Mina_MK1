param(
    [int]$Port = 8000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -Path $PSScriptRoot
$pidFile = Join-Path $PSScriptRoot '.mk1_api.pid'

$stoppedAny = $false

if (Test-Path $pidFile) {
    try {
        $pidFromFile = [int](Get-Content -Path $pidFile -Raw).Trim()
        $proc = Get-Process -Id $pidFromFile -ErrorAction SilentlyContinue
        if ($proc) {
            Stop-Process -Id $pidFromFile -Force -ErrorAction Stop
            Write-Host "Stopped PID from pidfile: $pidFromFile"
            $stoppedAny = $true
        }
    } catch {
        Write-Warning "Could not stop PID from pidfile: $($_.Exception.Message)"
    }
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}

$pids = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)

if ($pids.Count -eq 0) {
    if (-not $stoppedAny) {
        Write-Host "No process is listening on port $Port"
    }
    exit 0
}

foreach ($procId in $pids) {
    try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Write-Host "Stopped process on port ${Port}: $procId"
        $stoppedAny = $true
    } catch {
        Write-Error "Failed to stop process $procId on port ${Port}: $($_.Exception.Message)"
        exit 1
    }
}

if (Test-Path $pidFile) {
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}

exit 0
