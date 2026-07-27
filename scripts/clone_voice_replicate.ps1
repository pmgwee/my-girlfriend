<#
.SYNOPSIS
    Clones voices\reference.wav on Replicate (MiniMax) and writes the voice id
    into .env.

.DESCRIPTION
    Replicate accepts a direct file upload, so the clip does not need to be
    hosted on a public URL first.

    Cloning costs about $3 one-time. Synthesis afterwards is ~$50 per million
    characters -- roughly half fal.ai.

.EXAMPLE
    scripts\clone_voice_replicate.ps1
    scripts\clone_voice_replicate.ps1 -Extend
#>
[CmdletBinding()]
param(
    [string]$Audio,
    # Loop a short clip to clear the minimum duration. Prefer real audio.
    [switch]$Extend,
    [string]$PreviewText = "欸，你終於上線了喔，人家等你等好久了啦。"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $Root ".env"

if (-not $Audio) { $Audio = Join-Path $Root "voices\reference.wav" }
if (-not (Test-Path $Audio)) { throw "Reference audio not found: $Audio" }

$token = (Select-String -Path $envFile -Pattern '^REPLICATE_API_TOKEN=(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
if (-not $token) {
    throw "REPLICATE_API_TOKEN is empty in .env. Get one at https://replicate.com/account/api-tokens"
}
$headers = @{ Authorization = "Bearer $token" }

$duration = [double](ffprobe -v error -show_entries format=duration -of csv=p=0 $Audio)
Write-Host ("reference: {0} ({1:N1}s)" -f (Split-Path $Audio -Leaf), $duration)

# MiniMax trains from ~5s but every host sets its own floor, and a longer clip
# always clones better. 10s is the safe bar across providers.
if ($duration -lt 10) {
    if (-not $Extend) {
        throw @"
Clip is $([math]::Round($duration,1))s. MiniMax clones best from 10s or more.

  1. Cut a longer one:  scripts\make_voice.ps1 -Source <original> -Duration 12
  2. Or re-run with -Extend to loop this clip. Honest (same speaker, nothing
     invented) but adds no new phonetic material.
"@
    }
    $extended = Join-Path $Root "voices\_extended.wav"
    $loops = [math]::Ceiling(11 / $duration)
    Write-Host "  looping x$loops to clear the 10s bar..." -ForegroundColor Yellow
    $listFile = Join-Path $env:TEMP "vg_concat_$PID.txt"
    (1..$loops | ForEach-Object { "file '$((Resolve-Path $Audio).Path -replace '\\','/')'" }) |
        Set-Content -Path $listFile -Encoding ascii
    ffmpeg -y -loglevel error -f concat -safe 0 -i $listFile -ac 1 -ar 24000 -c:a pcm_s16le $extended
    Remove-Item -Force $listFile -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed while extending the clip" }
    $Audio = $extended
    Write-Host ("  extended: {0:N1}s" -f [double](ffprobe -v error -show_entries format=duration -of csv=p=0 $Audio)) -ForegroundColor Green
}

function Show-ReplicateError {
    param($ErrorRecord)
    $detail = $ErrorRecord.ErrorDetails.Message
    $code = if ($ErrorRecord.Exception.Response) { $ErrorRecord.Exception.Response.StatusCode.value__ } else { "?" }
    if ($code -eq 401) { throw "Replicate rejected the token. Check REPLICATE_API_TOKEN in .env." }
    if ($detail -match "insufficient credit|payment|billing") {
        throw "Replicate reports a billing problem. Check https://replicate.com/account/billing`n$detail"
    }
    throw "Replicate request failed (HTTP $code): $detail"
}

# Step 1: upload the clip to Replicate's file storage.
Write-Host "uploading..." -ForegroundColor Cyan
try {
    $boundary = [Guid]::NewGuid().ToString()
    $bytes = [System.IO.File]::ReadAllBytes($Audio)
    $enc = [System.Text.Encoding]::GetEncoding("ISO-8859-1")
    $body = "--$boundary`r`nContent-Disposition: form-data; name=`"content`"; filename=`"$(Split-Path $Audio -Leaf)`"`r`nContent-Type: audio/wav`r`n`r`n" +
            $enc.GetString($bytes) + "`r`n--$boundary--`r`n"
    $upload = Invoke-RestMethod -Uri "https://api.replicate.com/v1/files" -Method Post `
        -Headers $headers -ContentType "multipart/form-data; boundary=$boundary" `
        -Body $enc.GetBytes($body) -TimeoutSec 300
}
catch { Show-ReplicateError $_ }

$audioUrl = $upload.urls.get
if (-not $audioUrl) { throw "No file URL returned:`n$($upload | ConvertTo-Json -Depth 5)" }
Write-Host "  -> $audioUrl" -ForegroundColor DarkGray

# Step 2: clone.
Write-Host "cloning (~`$3 one-time)..." -ForegroundColor Cyan
$cloneBody = @{
    input = @{
        voice_file                = $audioUrl
        need_noise_reduction      = $true
        need_volume_normalization = $true
        accuracy                  = 0.9
    }
} | ConvertTo-Json -Depth 5

try {
    $result = Invoke-RestMethod -Uri "https://api.replicate.com/v1/models/minimax/voice-cloning/predictions" `
        -Method Post -Headers ($headers + @{ Prefer = "wait" }) -ContentType "application/json" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($cloneBody)) -TimeoutSec 600
}
catch { Show-ReplicateError $_ }

if ($result.status -eq "failed") { throw "Cloning failed: $($result.error)" }

$voice = $result.output
if ($voice -is [array]) { $voice = $voice[0] }
if (-not $voice) { throw "No voice id returned:`n$($result | ConvertTo-Json -Depth 6)" }

Write-Host "`nvoice id: $voice" -ForegroundColor Green

$text = [System.IO.File]::ReadAllText($envFile, [System.Text.Encoding]::UTF8)
if ($text -match 'REPLICATE_VOICE_ID=.*') {
    $text = $text -replace 'REPLICATE_VOICE_ID=.*', "REPLICATE_VOICE_ID=$voice"
}
else {
    $text += "`nREPLICATE_VOICE_ID=$voice`n"
}
[System.IO.File]::WriteAllText($envFile, $text, [System.Text.UTF8Encoding]::new($false))

Write-Host "written to .env" -ForegroundColor Green
Write-Host @"

Next:
  1. Set TTS_BACKEND=replicate in .env
  2. scripts\run_server.ps1

"@
