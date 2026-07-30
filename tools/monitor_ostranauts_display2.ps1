param(
    [int]$GraceSeconds = 120,
    [string]$LogPath = "E:\Mina_MK1\logs\ostranauts_display2_monitor.log"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$logDir = Split-Path -Parent $LogPath
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
}

function Wait-ForProcessEvent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Query
    )

    Register-WmiEvent -Query $Query -SourceIdentifier $Source | Out-Null
    try {
        Write-Log "Waiting for event: $Source"
        Wait-Event -SourceIdentifier $Source | Out-Null
        Write-Log "Event received: $Source"
    }
    finally {
        Unregister-Event -SourceIdentifier $Source -ErrorAction SilentlyContinue
        Remove-Event -SourceIdentifier $Source -ErrorAction SilentlyContinue
    }
}

function Move-VSCodeWindowsToDisplay2 {
    Add-Type -AssemblyName System.Windows.Forms

    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WinApi {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
}
"@

    $screen2 = [System.Windows.Forms.Screen]::AllScreens | Select-Object -Skip 1 -First 1
    if ($null -eq $screen2) {
        Write-Log "Display 2 not found. No window move performed."
        return
    }

    $left = $screen2.WorkingArea.Left + 40
    $top = $screen2.WorkingArea.Top + 40
    $flags = 0x0001 -bor 0x0004

    $moved = 0

    $procs = Get-Process -Name "Code" -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 }

    foreach ($p in $procs) {
        $title = [string]$p.MainWindowTitle

        [void][WinApi]::SetWindowPos($p.MainWindowHandle, [IntPtr]::Zero, $left, $top, 0, 0, $flags)
        $moved += 1
        Write-Log ("Moved VS Code window '{0}' (PID {1}) to display 2." -f $title, $p.Id)
    }

    if ($moved -eq 0) {
        Write-Log "No VS Code window handles found to move."
    }
}

try {
    Write-Log "Monitor started. GraceSeconds=$GraceSeconds"

    $isRunning = @(Get-Process -Name "Ostranauts" -ErrorAction SilentlyContinue).Count -gt 0

    if (-not $isRunning) {
        Wait-ForProcessEvent -Source "OstranautsStarted" -Query "SELECT * FROM __InstanceCreationEvent WITHIN 2 WHERE TargetInstance ISA 'Win32_Process' AND TargetInstance.Name='Ostranauts.exe'"
    }

    Wait-ForProcessEvent -Source "OstranautsStopped" -Query "SELECT * FROM __InstanceDeletionEvent WITHIN 2 WHERE TargetInstance ISA 'Win32_Process' AND TargetInstance.Name='Ostranauts.exe'"

    Write-Log "Ostranauts closed. Waiting $GraceSeconds seconds before display move."
    Start-Sleep -Seconds $GraceSeconds

    Move-VSCodeWindowsToDisplay2
    Write-Log "Monitor finished."
}
catch {
    Write-Log ("Monitor error: " + $_.Exception.Message)
}
