param(
    [string]$DeviceId,
    [switch]$SkipBuild,
    [switch]$NoLaunch,
    [switch]$ResetAndroidAutoCache,
    [switch]$OpenAndroidAutoSettings,
    [switch]$CheckLauncherList
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

function Get-UiTexts([string]$xmlPath) {
    $raw = Get-Content -Path $xmlPath -Raw
    return @([regex]::Matches($raw, 'text="([^"]*)"') |
        ForEach-Object { $_.Groups[1].Value } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique)
}

function Get-BoundsCenterForText([string]$xmlPath, [string]$label) {
    $raw = Get-Content -Path $xmlPath -Raw
    $escaped = [regex]::Escape($label)
    $pattern = 'text="{0}"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"' -f $escaped
    $match = [regex]::Match($raw, $pattern)
    if (-not $match.Success) {
        return $null
    }

    $x = [int](($match.Groups[1].Value + $match.Groups[3].Value) / 2)
    $y = [int](($match.Groups[2].Value + $match.Groups[4].Value) / 2)
    return @($x, $y)
}

function Get-BoundsCenterForContentDesc([string]$xmlPath, [string]$contentDesc) {
    $raw = Get-Content -Path $xmlPath -Raw
    $escaped = [regex]::Escape($contentDesc)
    $pattern = 'content-desc="{0}"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"' -f $escaped
    $match = [regex]::Match($raw, $pattern)
    if (-not $match.Success) {
        return $null
    }

    $x = [int](($match.Groups[1].Value + $match.Groups[3].Value) / 2)
    $y = [int](($match.Groups[2].Value + $match.Groups[4].Value) / 2)
    return @($x, $y)
}

function Test-AndroidAutoDeveloperMenu {
    param(
        [string]$adbPath,
        [string[]]$targetArgs,
        [string]$repoRootPath
    )

    Write-Step 'Checking Android Auto developer menu availability'
    $tmpDir = Join-Path $repoRootPath 'tmp'
    if (-not (Test-Path $tmpDir)) {
        New-Item -ItemType Directory -Path $tmpDir | Out-Null
    }

    & $adbPath @targetArgs shell am start -a com.android.settings.action.SETTINGS -n com.google.android.projection.gearhead/.companion.settings.DefaultSettingsActivity | Out-Null
    Start-Sleep -Seconds 2

    $settingsUi = Join-Path $tmpDir 'aa_settings_for_dev_menu.xml'
    & $adbPath @targetArgs exec-out uiautomator dump /dev/tty > $settingsUi

    $menuCenter = Get-BoundsCenterForContentDesc -xmlPath $settingsUi -contentDesc 'More options'
    if ($null -eq $menuCenter) {
        Write-Host 'Could not locate Android Auto overflow menu button.' -ForegroundColor Yellow
        return $false
    }

    & $adbPath @targetArgs shell input tap $menuCenter[0] $menuCenter[1] | Out-Null
    Start-Sleep -Milliseconds 700

    $menuUi = Join-Path $tmpDir 'aa_overflow_menu.xml'
    & $adbPath @targetArgs exec-out uiautomator dump /dev/tty > $menuUi
    $texts = Get-UiTexts -xmlPath $menuUi
    if ($texts -contains 'Developer settings') {
        Write-Host 'Developer settings menu is available.' -ForegroundColor Green
        return $true
    }

    Write-Host 'Developer settings menu is NOT available (developer mode is still locked).' -ForegroundColor Yellow
    return $false
}

function Test-MinaInLauncherList {
    param(
        [string]$adbPath,
        [string[]]$targetArgs,
        [string]$repoRootPath
    )

    Write-Step 'Scanning Android Auto Customize launcher for Mina'
    $tmpDir = Join-Path $repoRootPath 'tmp'
    if (-not (Test-Path $tmpDir)) {
        New-Item -ItemType Directory -Path $tmpDir | Out-Null
    }

    & $adbPath @targetArgs shell am start -a com.android.settings.action.SETTINGS -n com.google.android.projection.gearhead/.companion.settings.DefaultSettingsActivity | Out-Null
    Start-Sleep -Seconds 2

    $customizeFound = $false
    $customizeXml = ''
    for ($i = 0; $i -lt 8; $i++) {
        $scanPath = Join-Path $tmpDir ("aa_settings_scan_{0}.xml" -f $i)
        & $adbPath @targetArgs exec-out uiautomator dump /dev/tty > $scanPath
        $texts = Get-UiTexts -xmlPath $scanPath
        if ($texts -contains 'Customize launcher') {
            $customizeFound = $true
            $customizeXml = $scanPath
            break
        }
        & $adbPath @targetArgs shell input swipe 360 1320 360 520 260 | Out-Null
        Start-Sleep -Milliseconds 700
    }

    if (-not $customizeFound) {
        Write-Host 'Could not find Customize launcher row via UI scan.' -ForegroundColor Yellow
        return $false
    }

    $center = Get-BoundsCenterForText -xmlPath $customizeXml -label 'Customize launcher'
    if ($null -eq $center) {
        Write-Host 'Customize launcher row found but tap bounds could not be resolved.' -ForegroundColor Yellow
        return $false
    }

    & $adbPath @targetArgs shell input tap $center[0] $center[1] | Out-Null
    Start-Sleep -Seconds 2

    for ($i = 0; $i -lt 10; $i++) {
        $scanPath = Join-Path $tmpDir ("aa_customize_scan_{0}.xml" -f $i)
        & $adbPath @targetArgs exec-out uiautomator dump /dev/tty > $scanPath
        $texts = Get-UiTexts -xmlPath $scanPath
        if (($texts -contains 'Mina Voice') -or ($texts -match '^Mina$') -or ($texts -match '^Mina Voice$')) {
            Write-Host 'Mina was found in Customize launcher list.' -ForegroundColor Green
            return $true
        }
        & $adbPath @targetArgs shell input swipe 360 1320 360 520 260 | Out-Null
        Start-Sleep -Milliseconds 700
    }

    Write-Host 'Mina was NOT found in first 10 Customize launcher viewports.' -ForegroundColor Yellow
    return $false
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

Write-Step 'Granting phone alarm notification permission'
& $adb @adbTargetArgs shell pm grant com.mina.voice android.permission.POST_NOTIFICATIONS 2>$null | Out-Null

Write-Step 'Refreshing Mina and Android Auto processes'
& $adb @adbTargetArgs shell am force-stop com.mina.voice | Out-Null
& $adb @adbTargetArgs shell am force-stop com.google.android.projection.gearhead | Out-Null
Start-Sleep -Milliseconds 700

if ($ResetAndroidAutoCache) {
    Write-Step 'Resetting Android Auto app data cache'
    & $adb @adbTargetArgs shell pm clear com.google.android.projection.gearhead | Write-Host
    & $adb @adbTargetArgs shell am force-stop com.google.android.projection.gearhead | Out-Null
    Start-Sleep -Milliseconds 500
}

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

Write-Step 'Checking Android Auto media service discovery'
$packageDump = & $adb @adbTargetArgs shell dumpsys package com.mina.voice 2>$null
$serviceLines = @($packageDump | Where-Object {
        $_ -match 'android\.media\.browse\.MediaBrowserService|MinaMediaBrowserService|com\.google\.android\.gms\.car\.application'
    })

$hasMediaServiceAction = ($serviceLines -join "`n") -match 'android\.media\.browse\.MediaBrowserService'
$hasMediaServiceName = ($serviceLines -join "`n") -match 'MinaMediaBrowserService'

if ($hasMediaServiceAction -and $hasMediaServiceName) {
    Write-Host 'Android Auto media service is registered in installed manifest.' -ForegroundColor Green
    $serviceLines | ForEach-Object { Write-Host $_ }
}
else {
    Write-Host 'Android Auto media service was not found in installed package dump.' -ForegroundColor Yellow
    Write-Host 'Run this manually for details:' -ForegroundColor Yellow
    Write-Host 'adb shell dumpsys package com.mina.voice | findstr /i "MediaBrowserService MinaMediaBrowserService car.application"'
}

Write-Step 'Triggering Android Auto endpoint refresh'
& $adb @adbTargetArgs shell am broadcast -a com.google.android.gms.car.FIRST_ACTIVITY | Out-Null
& $adb @adbTargetArgs shell am broadcast -a com.google.android.gms.car.GEARHEAD_ENDPOINTS | Out-Null
& $adb @adbTargetArgs shell am broadcast -a com.google.android.gms.car.SETTINGS_MIGRATION | Out-Null

if ($OpenAndroidAutoSettings -or $ResetAndroidAutoCache) {
    Write-Step 'Opening Android Auto settings on phone'
    & $adb @adbTargetArgs shell am start -a com.android.settings.action.SETTINGS -n com.google.android.projection.gearhead/.companion.settings.DefaultSettingsActivity | Write-Host
}

if ($CheckLauncherList) {
    [void](Test-MinaInLauncherList -adbPath $adb -targetArgs $adbTargetArgs -repoRootPath $repoRoot)
}

[void](Test-AndroidAutoDeveloperMenu -adbPath $adb -targetArgs $adbTargetArgs -repoRootPath $repoRoot)

Write-Host "`nDone. Next in Android Auto:" -ForegroundColor Green
Write-Host '1) Open Android Auto settings on phone.'
if ($ResetAndroidAutoCache) {
    Write-Host '2) Re-enable Developer settings and Unknown sources (cache reset clears this toggle).'
}
else {
    Write-Host '2) Developer settings -> enable Unknown sources.'
}
Write-Host '3) Customize launcher -> enable Mina Voice.'
Write-Host '4) Reconnect to car and open Mina Voice from app grid.'
