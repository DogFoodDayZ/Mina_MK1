param(
    [string]$GameDir = 'G:\SteamLibrary\steamapps\common\Ostranauts',
    [string]$PackageVersion = '5.4.2305'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (Get-Process -Name Ostranauts -ErrorAction SilentlyContinue) {
    throw 'Close Ostranauts before installing the companion bridge.'
}
if (-not (Test-Path (Join-Path $GameDir 'Ostranauts.exe'))) {
    throw "Ostranauts installation not found: $GameDir"
}

$stage = Join-Path $env:TEMP "mina-ostranauts-bepinex-$PackageVersion"
$archive = "$stage.zip"
$download = "https://thunderstore.io/package/download/BepInEx/BepInExPack_Ostranauts/$PackageVersion/"

Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $archive -Force -ErrorAction SilentlyContinue
Invoke-WebRequest -Uri $download -OutFile $archive -UseBasicParsing
Expand-Archive -Path $archive -DestinationPath $stage -Force

$pack = Join-Path $stage 'BepInExPack'
if (-not (Test-Path (Join-Path $pack 'BepInEx\core\BepInEx.dll'))) {
    throw 'Downloaded package did not contain the expected BepInExPack layout.'
}

Copy-Item (Join-Path $pack '*') $GameDir -Recurse -Force
& (Join-Path $PSScriptRoot 'build_plugin.ps1') -GameDir $GameDir

$pluginSource = Join-Path $PSScriptRoot 'build\MinaOstranauts.dll'
$pluginDir = Join-Path $GameDir 'BepInEx\plugins'
New-Item -ItemType Directory -Path $pluginDir -Force | Out-Null
Copy-Item $pluginSource (Join-Path $pluginDir 'MinaOstranauts.dll') -Force

$installRecord = [ordered]@{
    installed_at = (Get-Date).ToString('o')
    game_dir = $GameDir
    package = "BepInEx-BepInExPack_Ostranauts-$PackageVersion"
    package_sha256 = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    plugin = 'MinaOstranauts.dll'
    plugin_sha256 = (Get-FileHash $pluginSource -Algorithm SHA256).Hash.ToLowerInvariant()
    read_only = $true
}
$installRecord | ConvertTo-Json | Set-Content (Join-Path $PSScriptRoot 'install_record.json') -Encoding UTF8

Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $archive -Force -ErrorAction SilentlyContinue

Write-Host 'Mina Ostranauts companion installed.'
Write-Host 'Launch Ostranauts once, then check http://127.0.0.1:8000/games/ostranauts/status'