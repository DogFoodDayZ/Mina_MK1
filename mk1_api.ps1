param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'restart', 'smoke', 'setup')]
    [string]$Action = 'status',

    [int]$Port = 8000,
    [int]$EmbedPort = 8084,
    [string]$EmbedHost = '127.0.0.1',
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

switch ($Action) {
    'start' {
        if ($Foreground) {
            & $startScript -Port $Port -Foreground
        }
        else {
            & $startScript -Port $Port
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
            & $startScript -Port $Port -Foreground
        }
        else {
            & $startScript -Port $Port
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
}
