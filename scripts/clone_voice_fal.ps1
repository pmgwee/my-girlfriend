<#
.SYNOPSIS
    Clones voices\reference.wav on fal.ai (MiniMax) and writes the voice id to .env.

.DESCRIPTION
    Unlike Alibaba's enrollment API, fal.ai accepts a direct file upload, so you
    do NOT need to host the clip on a public URL first.

    MiniMax clones from roughly 5 seconds, which is why this backend suits a
    short reference clip. ElevenLabs wants 1-5 minutes for comparable quality.

.EXAMPLE
    scripts\clone_voice_fal.ps1
    scripts\clone_voice_fal.ps1 -Audio voices\her.wav
#>
[CmdletBinding()]
param(
    [string]$Audio,
    [string]$PreviewText = "欸，你終於上線了喔，人家等你等好久了啦。",
    # Loop a short clip to clear fal's 10s cloning minimum. Prefer real audio.
    [switch]$Extend
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot

if (-not $Audio) { $Audio = Join-Path $Root "voices\reference.wav" }
if (-not (Test-Path $Audio)) {
    throw "Reference audio not found: $Audio`nCreate it with scripts\make_voice.ps1"
}

$envFile = Join-Path $Root ".env"
$key = (Select-String -Path $envFile -Pattern '^FAL_KEY=(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
if (-not $key) {
    throw "FAL_KEY is empty in .env. Get one at https://fal.ai/dashboard/keys"
}

$duration = [double](ffprobe -v error -show_entries format=duration -of csv=p=0 $Audio)
Write-Host ("reference: {0} ({1:N1}s)" -f (Split-Path $Audio -Leaf), $duration)

# fal rejects clips under 10s for cloning. If the source is close but short,
# concatenating it with itself clears the threshold: it is the same speaker and
# same timbre, so nothing is fabricated -- there is just no extra phonetic
# variety, which is why real extra audio is still better.
if ($duration -lt 10) {
    if (-not $Extend) {
        throw @"
Clip is $([math]::Round($duration,1))s but fal.ai requires at least 10s for voice cloning.

Options, best first:
  1. Cut a longer clip from your source:
       scripts\make_voice.ps1 -Source <original> -Start <sec> -Duration 12
  2. Re-run with -Extend to loop this clip up to 10s. Works, but adds no new
     phonetic material, so the clone will be slightly weaker than real audio.
"@
    }

    $extended = Join-Path $Root "voices\_extended.wav"
    $loops = [math]::Ceiling(11 / $duration)
    Write-Host "  extending to clear the 10s minimum ($loops loops)..." -ForegroundColor Yellow
    $listFile = Join-Path $env:TEMP "vg_concat_$PID.txt"
    (1..$loops | ForEach-Object { "file '$((Resolve-Path $Audio).Path -replace '\\','/')'" }) |
        Set-Content -Path $listFile -Encoding ascii
    ffmpeg -y -loglevel error -f concat -safe 0 -i $listFile -ac 1 -ar 24000 -c:a pcm_s16le $extended
    Remove-Item -Force $listFile -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed while extending the clip" }

    $Audio = $extended
    $duration = [double](ffprobe -v error -show_entries format=duration -of csv=p=0 $Audio)
    Write-Host ("  extended: {0:N1}s" -f $duration) -ForegroundColor Green
}

function Invoke-Fal {
    param([string]$Uri, [string]$Body, [int]$Timeout = 120)
    try {
        return Invoke-RestMethod -Uri $Uri -Method Post `
            -Headers @{ Authorization = "Key $key" } -ContentType "application/json" `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($Body)) -TimeoutSec $Timeout
    }
    catch {
        # PowerShell already drains the response stream into ErrorDetails, so
        # GetResponseStream() usually returns nothing. Check ErrorDetails first
        # or the actual reason (billing, bad key) is lost behind a bare 403.
        $detail = $_.ErrorDetails.Message
        if (-not $detail -and $_.Exception.Response) {
            try {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $detail = $reader.ReadToEnd()
            }
            catch { $detail = "" }
        }
        # These two are the common ones and deserve a plain answer rather than
        # a stack trace: the key is fine, the account just isn't usable yet.
        if ($detail -match "Exhausted balance|User is locked") {
            throw "fal.ai account has no balance. Add credit at https://fal.ai/dashboard/billing and re-run this script.`nThe key itself is valid."
        }
        if ($detail -match "Unauthorized|invalid.*key|403") {
            throw "fal.ai rejected the key. Check FAL_KEY in .env against https://fal.ai/dashboard/keys"
        }
        throw "fal.ai request failed: $($_.Exception.Message)`n$detail"
    }
}

# Step 1: upload the clip to fal's storage so the cloning call can reach it.
Write-Host "uploading..." -ForegroundColor Cyan
$upload = Invoke-Fal -Uri "https://rest.alpha.fal.ai/storage/upload/initiate" `
    -Body (@{ content_type = "audio/wav"; file_name = (Split-Path $Audio -Leaf) } | ConvertTo-Json) `
    -Timeout 60

Invoke-RestMethod -Uri $upload.upload_url -Method Put `
    -InFile $Audio -ContentType "audio/wav" -TimeoutSec 300 | Out-Null

Write-Host "  -> $($upload.file_url)" -ForegroundColor DarkGray

# Step 2: clone. Noise reduction and volume normalisation are on because a clip
# cut from a recording is rarely as clean as a studio take.
Write-Host "cloning..." -ForegroundColor Cyan
$body = @{
    audio_url                 = $upload.file_url
    text                      = $PreviewText
    model                     = "speech-02-hd"
    noise_reduction           = $true
    need_volume_normalization = $true
    accuracy                  = 0.9
} | ConvertTo-Json

$result = Invoke-Fal -Uri "https://fal.run/fal-ai/minimax/voice-clone" -Body $body -Timeout 300

$voice = $result.custom_voice_id
if (-not $voice) { throw "No custom_voice_id returned:`n$($result | ConvertTo-Json -Depth 6)" }

Write-Host "`nvoice id: $voice" -ForegroundColor Green

if ($result.audio.url) {
    $preview = Join-Path $Root "samples\clone_preview.mp3"
    New-Item -ItemType Directory -Force -Path (Split-Path $preview) | Out-Null
    curl.exe -L --silent -o $preview $result.audio.url
    Write-Host "preview:  $preview" -ForegroundColor Green
}

$text = [System.IO.File]::ReadAllText($envFile, [System.Text.Encoding]::UTF8)
if ($text -match 'MINIMAX_VOICE_ID=.*') {
    $text = $text -replace 'MINIMAX_VOICE_ID=.*', "MINIMAX_VOICE_ID=$voice"
}
else {
    $text += "`nMINIMAX_VOICE_ID=$voice`n"
}
[System.IO.File]::WriteAllText($envFile, $text, [System.Text.UTF8Encoding]::new($false))

Write-Host @"

written to .env

Next:
  1. Listen to samples\clone_preview.mp3 -- that is her cloned voice.
  2. Set TTS_BACKEND=minimax in .env
  3. scripts\run_server.ps1

"@
