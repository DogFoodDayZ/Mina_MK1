param(
    [string]$RepoUrl = 'https://github.com/DogFoodDayZ/AI-friendly-Spotify-build-for-android.git',
    [string]$Branch = 'main',
    [string]$SourceDir = 'android/mina-voice-app',
    [string]$WorkDir = '.tmp_publish_android_repo',
    [switch]$Force,
    [switch]$Push,
    [switch]$SkipSecretScan
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-Command([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "Required command not found: $Name"
    }
}

Assert-Command -Name 'git'
Assert-Command -Name 'robocopy'

function Remove-SensitiveFiles([string]$Path) {
    $sensitiveNames = @(
        '.env',
        '.env.local',
        '.env.*',
        'google-services.json',
        '*.keystore',
        '*.jks',
        '*.p12',
        '*.pem',
        '*.key'
    )

    foreach ($pattern in $sensitiveNames) {
        Get-ChildItem -Path $Path -Recurse -Force -File -Filter $pattern -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
        }
    }
}

function Find-PotentialSecrets([string]$Path) {
    $findings = @()
    $patterns = @(
        '(?i)SPOTIFY_CLIENT_SECRET\s*[=:]\s*.+',
        '(?i)SPOTIFY_REFRESH_TOKEN\s*[=:]\s*.+',
        '(?i)API[_-]?KEY\s*[=:]\s*.+',
        '(?i)SECRET[_-]?KEY\s*[=:]\s*.+',
        '(?i)ACCESS[_-]?TOKEN\s*[=:]\s*.+',
        '(?i)REFRESH[_-]?TOKEN\s*[=:]\s*.+',
        '(?i)PRIVATE[_-]?KEY\s*[=:]\s*.+',
        '(?i)BEGIN\s+PRIVATE\s+KEY',
        '(?i)Authorization\s*:\s*Bearer\s+[A-Za-z0-9._-]+'
    )

    $excludeDirs = @('.git', '.gradle', 'build', 'app\\build', '.idea')

    $textFiles = Get-ChildItem -Path $Path -Recurse -Force -File -ErrorAction SilentlyContinue |
    Where-Object {
        $full = $_.FullName
        foreach ($d in $excludeDirs) {
            if ($full -match [Regex]::Escape("\\$d\\")) { return $false }
        }
        if ($_.Length -gt 2MB) { return $false }
        return $true
    }

    foreach ($file in $textFiles) {
        $lines = @()
        try {
            $lines = @(Get-Content -Path $file.FullName -ErrorAction Stop)
        }
        catch {
            continue
        }

        for ($i = 0; $i -lt $lines.Count; $i++) {
            $line = [string]$lines[$i]
            foreach ($pat in $patterns) {
                if ($line -match $pat) {
                    $findings += [PSCustomObject]@{
                        File    = $file.FullName
                        Line    = $i + 1
                        Pattern = $pat
                    }
                    break
                }
            }
        }
    }

    return @($findings)
}

$root = $PSScriptRoot | Split-Path -Parent
Set-Location -Path $root

$src = Join-Path $root $SourceDir
if (-not (Test-Path $src)) {
    throw "Source directory not found: $src"
}

$dst = Join-Path $root $WorkDir
if (Test-Path $dst) {
    if (-not $Force) {
        throw "Work directory already exists: $dst. Re-run with -Force to replace it."
    }
    Remove-Item -Path $dst -Recurse -Force
}

New-Item -Path $dst -ItemType Directory | Out-Null

# Copy Android app project only. Exclude common local/build folders.
$null = robocopy $src $dst /E /NFL /NDL /NJH /NJS /NP /XD .git .gradle build app\build .idea /XF local.properties *.keystore *.jks *.p12 *.pem *.key .env .env.local google-services.json
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

Remove-SensitiveFiles -Path $dst

# Add a focused gitignore for Android builds.
$gitignore = @(
    '.gradle/'
    'build/'
    'app/build/'
    'local.properties'
    '.env'
    '.env.local'
    '.env.*'
    'google-services.json'
    '*.keystore'
    '*.jks'
    '*.p12'
    '*.pem'
    '*.key'
    '*.iml'
    '.idea/'
    '.DS_Store'
)
Set-Content -Path (Join-Path $dst '.gitignore') -Value ($gitignore -join [Environment]::NewLine) -Encoding UTF8

if (-not $SkipSecretScan) {
    $findings = @(Find-PotentialSecrets -Path $dst)
    if ($findings.Count -gt 0) {
        Write-Host 'Potential secrets detected. Publish aborted.' -ForegroundColor Red
        $findings | Select-Object -First 30 | ForEach-Object {
            Write-Host ("- {0}:{1}" -f $_.File, $_.Line)
        }
        throw 'Secret scan failed. Remove secrets or rerun with -SkipSecretScan only if findings are false positives.'
    }
}

Set-Location -Path $dst

git init | Out-Null
git checkout -b $Branch | Out-Null
git add .

$status = git status --porcelain
if (-not [string]::IsNullOrWhiteSpace($status)) {
    git commit -m 'Initial Mina Android app import from Mina_MK1' | Out-Null
}

# Ensure remote points to target repo.
$existingRemote = git remote
if ($existingRemote -contains 'origin') {
    git remote remove origin
}
git remote add origin $RepoUrl

Write-Host "Prepared Android repo in: $dst"
Write-Host "Remote: $RepoUrl"
Write-Host "Branch: $Branch"

if ($Push) {
    Write-Host 'Pushing to remote...'
    git push -u origin $Branch
    Write-Host 'Push complete.'
}
else {
    Write-Host 'Dry run complete. No push performed.'
    Write-Host 'Run again with -Push to publish.'
}
