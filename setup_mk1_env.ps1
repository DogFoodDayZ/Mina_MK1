param(
    [string]$VenvPath = '.venv',
    [string]$RequirementsFile = 'requirements.txt',
    [switch]$RunTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -Path $PSScriptRoot

$venvFullPath = Join-Path $PSScriptRoot $VenvPath
$venvPython = Join-Path $venvFullPath 'Scripts\python.exe'
$requirementsPath = Join-Path $PSScriptRoot $RequirementsFile

if (-not (Test-Path $requirementsPath)) {
    throw "Requirements file not found: $requirementsPath"
}

if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment at $venvFullPath"

    $pyCmd = Get-Command py -ErrorAction SilentlyContinue
    if ($pyCmd) {
        & $pyCmd.Source -3 -m venv $venvFullPath
    }
    else {
        $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCmd) {
            throw 'No Python launcher found. Install Python 3.11+ and ensure py or python is on PATH.'
        }
        & $pythonCmd.Source -m venv $venvFullPath
    }
}

if (-not (Test-Path $venvPython)) {
    throw "Virtual environment Python was not created: $venvPython"
}

Write-Host 'Upgrading pip tooling...'
& $venvPython -m pip install --upgrade pip setuptools wheel

Write-Host "Installing dependencies from $RequirementsFile"
& $venvPython -m pip install -r $requirementsPath

Write-Host 'Validating core imports...'
& $venvPython -c "import fastapi, uvicorn, faiss; print('Dependency check OK')"

if ($RunTests) {
    Write-Host 'Running tests...'
    & $venvPython -m pytest -q
}

Write-Host ''
Write-Host 'Setup complete.'
Write-Host "Python: $venvPython"
Write-Host 'Activate with: .\\.venv\\Scripts\\Activate.ps1'
