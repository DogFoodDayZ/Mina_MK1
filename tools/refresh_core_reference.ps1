Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Maintainer helper:
# Regenerates a raw function index from agent/core.py.
# This does not modify runtime code. It only refreshes reference input data.

$root = Split-Path -Parent $PSScriptRoot
$corePath = Join-Path $root 'agent\core.py'
$outPath = Join-Path $root 'tmp\core_functions.txt'

if (-not (Test-Path $corePath)) {
    throw "core.py not found at $corePath"
}

$outDir = Split-Path -Parent $outPath
if (-not (Test-Path $outDir)) {
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
}

$defs = Select-String -Path $corePath -Pattern '^\s*def\s+'
$defs |
ForEach-Object { "{0}:{1}" -f $_.LineNumber, ($_.Line.Trim()) } |
Set-Content -Path $outPath -Encoding UTF8

Write-Host "Wrote: $outPath"
Write-Host "Function count: $($defs.Count)"
Write-Host 'Next: update agent/core_reference.md and agent/core_index.json if major section responsibilities changed.'
