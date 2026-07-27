<#
.SYNOPSIS
    Registers your reference clip as a cloned voice on Alibaba Model Studio.

.DESCRIPTION
    One-time step. Returns a voice id which goes in .env as DASHSCOPE_VOICE_ID.
    Nothing is uploaded during conversations afterwards -- only reply text.

    IMPORTANT: the enrollment API takes a publicly reachable URL, not a local
    file. Host voices\reference.wav somewhere Alibaba can fetch it (OSS, S3, or
    any static host) and pass that URL.

    Audio requirements differ from the local model -- DashScope wants LONGER:
      * 10-20s recommended (60s max), vs 5-10s locally
      * WAV 16-bit / MP3 / M4A, mono, >= 24kHz
      * at least 3s of continuous clear speech

.EXAMPLE
    scripts\register_voice.ps1 -AudioUrl https://example.com/reference.wav
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AudioUrl,
    [string]$Name = "xiaoya",
    # Must match DASHSCOPE_MODEL in .env exactly, or synthesis fails later.
    # cosyvoice-v3-flash is the Singapore model that supports BOTH cloning and
    # emotion instructions; v2 clones but silently ignores instructions.
    [string]$TargetModel = "cosyvoice-v3-flash",
    [string]$BaseUrl = "https://dashscope-intl.aliyuncs.com/api/v1"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

# Read the key from .env rather than taking it as an argument, so it never
# lands in shell history.
$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) { throw ".env not found. Copy .env.example first." }

$key = (Select-String -Path $envFile -Pattern '^DASHSCOPE_API_KEY=(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
if (-not $key) {
    throw "DASHSCOPE_API_KEY is empty in .env. Get a key at https://modelstudio.console.alibabacloud.com/"
}

# CosyVoice and Qwen3-TTS use different enrollment payloads.
if ($TargetModel -like "cosyvoice*") {
    $body = @{
        model = "voice-enrollment"
        input = @{
            action       = "create_voice"
            target_model = $TargetModel
            prefix       = $Name
            url          = $AudioUrl
        }
    } | ConvertTo-Json -Depth 6
}
else {
    $body = @{
        model = "qwen-voice-enrollment"
        input = @{
            action         = "create"
            target_model   = $TargetModel
            preferred_name = $Name
            audio          = @{ data = $AudioUrl }
        }
    } | ConvertTo-Json -Depth 6
}

Write-Host "registering voice '$Name' against $TargetModel..." -ForegroundColor Cyan

try {
    $response = Invoke-RestMethod -Uri "$BaseUrl/services/audio/tts/customization" `
        -Method Post -UseBasicParsing -TimeoutSec 120 `
        -Headers @{ Authorization = "Bearer $key" } `
        -ContentType "application/json" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
}
catch {
    $detail = ""
    if ($_.Exception.Response) {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $detail = $reader.ReadToEnd()
    }
    throw "Enrollment failed: $($_.Exception.Message)`n$detail"
}

# CosyVoice returns output.voice_id; Qwen3-TTS returns output.voice.
$voice = if ($response.output.voice_id) { $response.output.voice_id } else { $response.output.voice }
if (-not $voice) { throw "No voice id in response:`n$($response | ConvertTo-Json -Depth 6)" }

Write-Host "`nvoice id: $voice" -ForegroundColor Green

# Write it straight into .env so the exact string can't be mistyped.
$text = [System.IO.File]::ReadAllText($envFile, [System.Text.Encoding]::UTF8)
if ($text -match 'DASHSCOPE_VOICE_ID=.*') {
    $text = $text -replace 'DASHSCOPE_VOICE_ID=.*', "DASHSCOPE_VOICE_ID=$voice"
}
else {
    $text += "`nDASHSCOPE_VOICE_ID=$voice`n"
}
[System.IO.File]::WriteAllText($envFile, $text, [System.Text.UTF8Encoding]::new($false))

Write-Host "written to .env" -ForegroundColor Green
Write-Host @"

Next:
  1. Set TTS_BACKEND=dashscope in .env
  2. scripts\run_server.ps1
  3. scripts\doctor.ps1 to confirm synthesis works

"@
