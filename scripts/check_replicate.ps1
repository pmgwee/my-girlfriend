<#
.SYNOPSIS
    Lists which MiniMax speech models your Replicate account can actually reach,
    and prints their real input schema.

.DESCRIPTION
    Replicate does not host every MiniMax revision that other platforms do, and
    the model id is not guessable. This asks the account rather than assuming,
    then writes the newest available one into .env.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $Root ".env"

$token = (Select-String -Path $envFile -Pattern '^REPLICATE_API_TOKEN=(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
if (-not $token) {
    throw "REPLICATE_API_TOKEN is empty in .env. Get one at https://replicate.com/account/api-tokens"
}

$headers = @{ Authorization = "Bearer $token" }

# Newest first -- the first hit wins.
$candidates = @(
    "minimax/speech-2.8-hd", "minimax/speech-2.8-turbo",
    "minimax/speech-2.6-hd", "minimax/speech-2.6-turbo",
    "minimax/speech-2.5-hd", "minimax/speech-2.5-turbo",
    "minimax/speech-02-hd", "minimax/speech-02-turbo"
)

Write-Host "checking which MiniMax speech models this account can reach...`n" -ForegroundColor Cyan

$available = @()
foreach ($m in $candidates) {
    try {
        $null = Invoke-RestMethod -Uri "https://api.replicate.com/v1/models/$m" -Headers $headers -TimeoutSec 25
        Write-Host ("  [+] {0}" -f $m) -ForegroundColor Green
        $available += $m
    }
    catch {
        $code = if ($_.Exception.Response) { $_.Exception.Response.StatusCode.value__ } else { "?" }
        if ($code -eq 401) { throw "Replicate rejected the token. Check REPLICATE_API_TOKEN in .env." }
        Write-Host ("  [ ] {0}  ({1})" -f $m, $code) -ForegroundColor DarkGray
    }
}

Write-Host "`nvoice cloning:" -ForegroundColor Cyan
try {
    $clone = Invoke-RestMethod -Uri "https://api.replicate.com/v1/models/minimax/voice-cloning" -Headers $headers -TimeoutSec 25
    Write-Host "  [+] minimax/voice-cloning" -ForegroundColor Green
    $props = $clone.latest_version.openapi_schema.components.schemas.Input.properties
    if ($props) {
        Write-Host "      inputs:" -ForegroundColor DarkGray
        foreach ($p in $props.PSObject.Properties.Name) {
            Write-Host ("        {0}" -f $p) -ForegroundColor DarkGray
        }
    }
}
catch {
    Write-Host "  [ ] minimax/voice-cloning not reachable" -ForegroundColor Red
}

if (-not $available) { throw "No MiniMax speech model is reachable on this account." }

$best = $available[0]
Write-Host "`nnewest available: $best" -ForegroundColor Green

# Show the real schema so the adapter's parameters can be checked against it
# rather than against documentation for a different host.
try {
    $info = Invoke-RestMethod -Uri "https://api.replicate.com/v1/models/$best" -Headers $headers -TimeoutSec 25
    $props = $info.latest_version.openapi_schema.components.schemas.Input.properties
    if ($props) {
        Write-Host "`n$best inputs:" -ForegroundColor Cyan
        foreach ($p in $props.PSObject.Properties.Name) {
            $d = $props.$p
            $extra = if ($d.enum) { " enum: " + ($d.enum -join ", ") } elseif ($d.default -ne $null) { " default: $($d.default)" } else { "" }
            Write-Host ("  {0,-24}{1}" -f $p, $extra)
        }
    }
}
catch { }

$text = [System.IO.File]::ReadAllText($envFile, [System.Text.Encoding]::UTF8)
if ($text -match 'REPLICATE_MODEL=.*') {
    $text = $text -replace 'REPLICATE_MODEL=.*', "REPLICATE_MODEL=$best"
}
else {
    $text += "`nREPLICATE_MODEL=$best`n"
}
[System.IO.File]::WriteAllText($envFile, $text, [System.Text.UTF8Encoding]::new($false))
Write-Host "`nwritten to .env as REPLICATE_MODEL" -ForegroundColor Green
