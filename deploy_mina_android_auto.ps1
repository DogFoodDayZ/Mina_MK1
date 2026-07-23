param(
    [string]$DeviceId,
    [switch]$SkipBuild,
    [switch]$NoLaunch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step([string]$message) {
    Write-Host "`n==> $message" -ForegroundColor Cyan
}

function Resolve-AdbPath {
    $fromPath = Get-Command adb -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }

    $candidates = @(
        'C:/Users/Admin/AppData/Local/Android/Sdk/platform-tools/adb.exe',
        'C:/Users/Admin/AppData/Local/Android/sdk/platform-tools/adb.exe',
        'C:/Android/platform-tools/adb.exe'
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw 'adb.exe not found. Install Android platform-tools or add adb to PATH.'
}

function Resolve-GradleWrapper([string]$repoRoot) {
    $wrapper = Join-Path $repoRoot 'android/mina-voice-app/gradlew.bat'
    if (-not (Test-Path $wrapper)) {
        throw "Gradle wrapper not found at $wrapper"
    }
    return $wrapper
}

function Resolve-ApkPath([string]$repoRoot) {
    return Join-Path $repoRoot 'android/mina-voice-app/app/build/outputs/apk/debug/app-debug.apk'
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$adb = Resolve-AdbPath
$gradlew = Resolve-GradleWrapper -repoRoot $repoRoot
$apkPath = Resolve-ApkPath -repoRoot $repoRoot
$projectDir = Join-Path $repoRoot 'android/mina-voice-app'

if (-not $env:JAVA_HOME) {
    $jbr = 'C:\Program Files\Android\Android Studio\jbr'
    if (Test-Path $jbr) {
        $env:JAVA_HOME = $jbr
        $env:Path = "$($env:JAVA_HOME)\bin;$($env:Path)"
    }
}

$adbTargetArgs = @()
if ($DeviceId) {
    $adbTargetArgs = @('-s', $DeviceId)
}

Write-Step 'Checking connected Android devices'
$devicesRaw = & $adb devices
$onlineDevices = @($devicesRaw | Where-Object { $_ -match "\tdevice$" })
if ($onlineDevices.Count -eq 0) {
    throw 'No online Android device found. Connect your phone and enable USB debugging.'
}

if (-not $DeviceId -and $onlineDevices.Count -gt 1) {
    throw "Multiple devices connected. Re-run with -DeviceId. Devices:`n$($onlineDevices -join "`n")"
}

if ($DeviceId) {
    Write-Host "Using device: $DeviceId" -ForegroundColor Yellow
}
else {
    $single = ($onlineDevices[0] -split "\s+")[0]
    Write-Host "Using device: $single" -ForegroundColor Yellow
}

if (-not $SkipBuild) {
    Write-Step 'Building Mina Android app (debug APK)'
    Push-Location $projectDir
    try {
        & $gradlew ':app:assembleDebug'
    }
    finally {
        Pop-Location
    }
}

if (-not (Test-Path $apkPath)) {
    throw "APK not found at $apkPath"
}

Write-Step 'Installing APK on phone'
& $adb @adbTargetArgs install -r $apkPath | Write-Host

Write-Step 'Refreshing Mina and Android Auto processes'
& $adb @adbTargetArgs shell am force-stop com.mina.voice | Out-Null
& $adb @adbTargetArgs shell am force-stop com.google.android.projection.gearhead | Out-Null
Start-Sleep -Milliseconds 700

if (-not $NoLaunch) {
    Write-Step 'Launching Mina app on phone'
    & $adb @adbTargetArgs shell am start -n com.mina.voice/com.mina.voice.MainActivity -a android.intent.action.MAIN -c android.intent.category.LAUNCHER | Write-Host
}

Write-Step 'Quick verification'
Start-Sleep -Milliseconds 1200
$minaPid = & $adb @adbTargetArgs shell pidof com.mina.voice
if ([string]::IsNullOrWhiteSpace($minaPid)) {
    Write-Host 'Mina process not detected immediately (some devices delay app process start).' -ForegroundColor Yellow
    Write-Host 'If needed, open Mina manually once on phone, then reconnect Android Auto.' -ForegroundColor Yellow
}
else {
    Write-Host "Mina running (pid: $minaPid)" -ForegroundColor Green
}

Write-Host "`nDone. Next in Android Auto:" -ForegroundColor Green
Write-Host '1) Open Android Auto settings on phone.'
Write-Host '2) Developer settings -> enable Unknown sources.'
Write-Host '3) Customize launcher -> enable Mina Voice.'
Write-Host '4) Reconnect to car and open Mina Voice from app grid.'
