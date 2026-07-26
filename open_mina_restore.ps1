param(
    [switch]$Wait
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$restoreScript = Join-Path $root 'restore\restore.py'
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
$python = if (Test-Path $venvPython) { $venvPython } else { (Get-Command python -ErrorAction Stop).Source }

if (-not (Test-Path $restoreScript)) {
    throw "Restore utility not found: $restoreScript"
}

$arguments = @($restoreScript)
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -PassThru -Wait:$Wait
if ($Wait -and $process.ExitCode -ne 0) {
    throw "Restore utility exited with code $($process.ExitCode)"
}

Write-Host "Mina restore utility launched (PID $($process.Id))."