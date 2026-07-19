Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -Path (Join-Path $PSScriptRoot '..')

$envPath = Join-Path (Get-Location) '.env.local'
if (-not (Test-Path $envPath)) {
    throw ".env.local not found at $envPath"
}

$lines = Get-Content $envPath
$map = @{}
foreach ($line in $lines) {
    $trim = ($line -as [string]).Trim()
    if ([string]::IsNullOrWhiteSpace($trim)) { continue }
    if ($trim.StartsWith('#')) { continue }
    if ($trim -notmatch '=') { continue }

    $pair = $trim -split '=', 2
    if ($pair.Count -ne 2) { continue }
    $map[$pair[0].Trim()] = $pair[1].Trim()
}

$clientId = [string]($map['SPOTIFY_CLIENT_ID'])
$clientSecret = [string]($map['SPOTIFY_CLIENT_SECRET'])
if ([string]::IsNullOrWhiteSpace($clientId) -or [string]::IsNullOrWhiteSpace($clientSecret)) {
    throw 'SPOTIFY_CLIENT_ID or SPOTIFY_CLIENT_SECRET is missing in .env.local'
}

$redirectUri = 'http://127.0.0.1:8888/callback'
$scope = 'playlist-modify-private playlist-modify-public playlist-read-private user-read-playback-state user-modify-playback-state user-read-currently-playing user-library-read'
$authUrl = 'https://accounts.spotify.com/authorize?client_id=' +
[uri]::EscapeDataString($clientId) +
'&response_type=code&redirect_uri=' +
[uri]::EscapeDataString($redirectUri) +
'&scope=' +
[uri]::EscapeDataString($scope) +
'&show_dialog=true'

Write-Host ''
Write-Host '1) Open this URL in your browser and approve access:'
Write-Host $authUrl
Write-Host ''
Write-Host '2) After redirect fails/loads callback, copy the code value from URL:'
Write-Host '   http://127.0.0.1:8888/callback?code=...'
Write-Host '   (You can paste either the full callback URL or only the code.)'
Write-Host ''

$codeInput = Read-Host 'Paste Spotify code or callback URL'
if ([string]::IsNullOrWhiteSpace($codeInput)) {
    throw 'No code provided.'
}

$code = $codeInput.Trim()
if ($code -match '^https?://') {
    try {
        $uriObj = [System.Uri]$code
        $parsed = [System.Web.HttpUtility]::ParseQueryString($uriObj.Query)
        $code = [string]$parsed['code']
    }
    catch {
        throw 'Could not parse callback URL. Paste only the code value after code=' 
    }
}

# Accept raw query fragments like: code=ABC123&state=... or code=ABC123&ubi=...
if ($code -match '(?:^|[?&])code=') {
    try {
        $query = $code
        if ($query.StartsWith('?')) {
            $query = $query.Substring(1)
        }
        $parsed = [System.Web.HttpUtility]::ParseQueryString($query)
        $fromQuery = [string]$parsed['code']
        if (-not [string]::IsNullOrWhiteSpace($fromQuery)) {
            $code = $fromQuery
        }
    }
    catch {
        # Keep original $code; downstream request will fail loudly if malformed.
    }
}

if ([string]::IsNullOrWhiteSpace($code)) {
    throw 'No valid code value found.'
}

$basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${clientId}:${clientSecret}"))
$body = 'grant_type=authorization_code&code=' + [uri]::EscapeDataString($code) + '&redirect_uri=' + [uri]::EscapeDataString($redirectUri)

$resp = Invoke-RestMethod -Method Post `
    -Uri 'https://accounts.spotify.com/api/token' `
    -Headers @{ Authorization = "Basic $basic"; 'Content-Type' = 'application/x-www-form-urlencoded' } `
    -Body $body

$grantedScope = [string]$resp.scope
if (-not [string]::IsNullOrWhiteSpace($grantedScope)) {
    Write-Host "Granted scopes: $grantedScope"
}

$refresh = [string]$resp.refresh_token
if ([string]::IsNullOrWhiteSpace($refresh)) {
    throw 'No refresh_token returned. Run again with show_dialog=true and re-approve app.'
}

if (-not ($grantedScope -split '\\s+' | Where-Object { $_ -eq 'user-library-read' })) {
    throw 'Missing required scope user-library-read. Re-run and ensure you approve requested permissions.'
}

$updated = @()
$replaced = $false
foreach ($line in (Get-Content $envPath)) {
    if ($line -match '^SPOTIFY_REFRESH_TOKEN=') {
        $updated += ('SPOTIFY_REFRESH_TOKEN=' + $refresh)
        $replaced = $true
    }
    else {
        $updated += $line
    }
}

if (-not $replaced) {
    $updated += ('SPOTIFY_REFRESH_TOKEN=' + $refresh)
}

$updated | Set-Content $envPath

Write-Host ''
Write-Host 'Saved SPOTIFY_REFRESH_TOKEN to .env.local'
Write-Host 'Restart MK1 API to load updated environment.'
