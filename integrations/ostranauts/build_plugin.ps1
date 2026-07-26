param(
    [string]$GameDir = 'G:\SteamLibrary\steamapps\common\Ostranauts',
    [string]$OutputDir = "$PSScriptRoot\build"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$compiler = 'C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\MSBuild\Current\Bin\Roslyn\csc.exe'
$sources = Get-ChildItem (Join-Path $PSScriptRoot 'MinaOstranauts') -Filter '*.cs' |
Select-Object -ExpandProperty FullName
$managed = Join-Path $GameDir 'Ostranauts_Data\Managed'
$bepInEx = Join-Path $GameDir 'BepInEx\core\BepInEx.dll'
$output = Join-Path $OutputDir 'MinaOstranauts.dll'

foreach ($required in @($compiler, $managed, $bepInEx) + $sources) {
    if (-not (Test-Path $required)) {
        throw "Required build input not found: $required"
    }
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$references = @(
    $bepInEx,
    (Join-Path $managed 'netstandard.dll'),
    (Join-Path $managed 'UnityEngine.dll'),
    (Join-Path $managed 'UnityEngine.CoreModule.dll'),
    (Join-Path $managed 'Newtonsoft.Json.dll')
)

$arguments = @(
    '/nologo',
    '/target:library',
    '/optimize+',
    '/langversion:latest',
    "/out:$output"
)
$arguments += $references | ForEach-Object { "/reference:$_" }
$arguments += $sources

& $compiler @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Plugin compilation failed with exit code $LASTEXITCODE"
}

Write-Host "Built Mina Ostranauts plugin: $output"