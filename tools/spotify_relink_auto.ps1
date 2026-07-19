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

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add('http://127.0.0.1:8888/')
$listener.Start()

Write-Host 'Spotify relink listener running on http://127.0.0.1:8888/'
Write-Host 'Opening Spotify consent page...'
Start-Process $authUrl

$ctx = $listener.GetContext()
$req = $ctx.Request
$code = [string]$req.QueryString['code']
$err = [string]$req.QueryString['error']

$html = ''
if (-not [string]::IsNullOrWhiteSpace($err)) {
    $html = '<html><body><h2>Spotify auth failed</h2><p>' + $err + '</p></body></html>'
}
elseif ([string]::IsNullOrWhiteSpace($code)) {
    $html = '<html><body><h2>No code found</h2><p>Close this tab and retry.</p></body></html>'
}
else {
    $html = '<html><body><h2>Spotify linked</h2><p>You can close this tab.</p></body></html>'
}

$buf = [System.Text.Encoding]::UTF8.GetBytes($html)
$ctx.Response.ContentType = 'text/html; charset=utf-8'
$ctx.Response.ContentLength64 = $buf.Length
$ctx.Response.OutputStream.Write($buf, 0, $buf.Length)
$ctx.Response.OutputStream.Close()
$listener.Stop()

if (-not [string]::IsNullOrWhiteSpace($err)) {
    throw "Spotify authorize returned error: $err"
}
if ([string]::IsNullOrWhiteSpace($code)) {
    throw 'Spotify callback missing code parameter'
}

$basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${clientId}:${clientSecret}"))
$body = 'grant_type=authorization_code&code=' + [uri]::EscapeDataString($code) + '&redirect_uri=' + [uri]::EscapeDataString($redirectUri)

$resp = Invoke-RestMethod -Method Post `
    -Uri 'https://accounts.spotify.com/api/token' `
    -Headers @{ Authorization = "Basic $basic"; 'Content-Type' = 'application/x-www-form-urlencoded' } `
    -Body $body

$refresh = [string]$resp.refresh_token
if ([string]::IsNullOrWhiteSpace($refresh)) {
    throw 'No refresh_token returned. Ensure approval completed and retry.'
}

$grantedScope = [string]$resp.scope
Write-Host "Granted scopes: $grantedScope"
if (-not ($grantedScope -split '\s+' | Where-Object { $_ -eq 'user-library-read' })) {
    throw 'Missing required scope user-library-read. Re-run and approve requested permissions.'
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

Write-Host 'Saved new SPOTIFY_REFRESH_TOKEN to .env.local'