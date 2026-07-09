param(
    [int]$Port = 8000,
    [string]$ApiHost = '127.0.0.1',
    [int]$EmbedPort = 8084,
    [string]$EmbedHost = '127.0.0.1'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -Path $PSScriptRoot
$pidFile = Join-Path $PSScriptRoot '.mk1_api.pid'

$baseUrl = "http://${ApiHost}:${Port}"
$embedHealthUrl = "http://${EmbedHost}:${EmbedPort}/health"

try {
    $embedHealth = Invoke-RestMethod -Uri $embedHealthUrl -Method Get -TimeoutSec 5
    if ($embedHealth.ok) {
        Write-Host "GET embed /health: OK"
        $embedHealth | ConvertTo-Json -Depth 3
    } else {
        Write-Error "GET embed /health returned non-ok payload"
        exit 1
    }
} catch {
    Write-Error "GET embed /health failed: $($_.Exception.Message)"
    exit 1
}

if (Test-Path $pidFile) {
    try {
        $pidFromFile = [int](Get-Content -Path $pidFile -Raw).Trim()
        $proc = Get-Process -Id $pidFromFile -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "PID file process: $pidFromFile"
        } else {
            Write-Warning "PID file is stale ($pidFromFile); removing."
            Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
        }
    } catch {
        Write-Warning "PID file unreadable; removing."
        Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
    }
}

$listener = $null
for ($i = 0; $i -lt 10; $i++) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1 OwningProcess, LocalAddress, LocalPort
    if ($listener) {
        break
    }
    Start-Sleep -Milliseconds 300
}

if (-not $listener) {
    Write-Host "MK1 API is not listening on ${ApiHost}:${Port}"
    exit 1
}

Write-Host "MK1 API listener PID: $($listener.OwningProcess)"

try {
    $status = Invoke-RestMethod -Uri "$baseUrl/status" -Method Get -TimeoutSec 5
    Write-Host "GET /status: OK"
    $status | ConvertTo-Json -Depth 4
} catch {
    Write-Error "GET /status failed: $($_.Exception.Message)"
    exit 1
}

try {
    $dbStatus = Invoke-RestMethod -Uri "$baseUrl/db/status" -Method Get -TimeoutSec 5
    Write-Host "GET /db/status: OK"
    $dbStatus | ConvertTo-Json -Depth 4
} catch {
    Write-Error "GET /db/status failed: $($_.Exception.Message)"
    exit 1
}

exit 0
