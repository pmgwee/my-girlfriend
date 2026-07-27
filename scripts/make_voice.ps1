<#
.SYNOPSIS
    Cuts a reference clip for voice cloning and checks it's actually usable.

.DESCRIPTION
    The reference audio decides whether she sounds like a person or a robot.
    Requirements Qwen3-TTS is strict about:

      * 5-10 seconds. Over ~15s makes cloning WORSE, not better.
      * One speaker, no music, no reverb, no background noise.
      * Mono, 24kHz or higher, WAV.
      * The delivery you want back. A flat reference clones to a flat voice.

    This script cuts the clip, converts it, then reports duration, peak level,
    silence ratio and clipping so you can tell before wasting a model load.

.EXAMPLE
    scripts\make_voice.ps1 -Source recording.mp3 -Start 12 -Duration 8
    scripts\make_voice.ps1 -Source clip.wav -Name wanwan
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Source,
    # Seconds into the source to start cutting.
    [double]$Start = 0,
    # 5-10 is the sweet spot.
    [double]$Duration = 8,
    [string]$Name = "reference"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VoicesDir = Join-Path $Root "voices"

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "ffmpeg not found on PATH. Install it from https://ffmpeg.org/download.html"
}
if (-not (Test-Path $Source)) { throw "Source audio not found: $Source" }

New-Item -ItemType Directory -Force -Path $VoicesDir | Out-Null
$out = Join-Path $VoicesDir "$Name.wav"

if ($Duration -gt 15) {
    Write-Warning "Duration $Duration s is over 15s. Longer references clone WORSE. Consider 8."
}

Write-Host "cutting ${Duration}s from ${Start}s..." -ForegroundColor Cyan
# -ac 1 mono, -ar 24000 matches the model's expected rate, and we strip video.
ffmpeg -y -loglevel error -i $Source -ss $Start -t $Duration -ac 1 -ar 24000 -vn -c:a pcm_s16le $out
if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed" }

Write-Host "`n--- quality check ---" -ForegroundColor Yellow

# volumedetect reports mean/max dB; silencedetect finds dead air. Both write to
# stderr, and PowerShell 5.1 turns a native exe's stderr into ErrorRecords that
# trip $ErrorActionPreference='Stop' -- so route it through a file instead of
# piping, which keeps the text as text.
$probeLog = Join-Path $env:TEMP "vg_probe_$PID.txt"
$null = Start-Process -FilePath "ffmpeg" -Wait -NoNewWindow -RedirectStandardError $probeLog `
    -ArgumentList @("-hide_banner", "-i", "`"$out`"", "-af", "volumedetect,silencedetect=n=-40dB:d=0.4", "-f", "null", "NUL")
$probe = Get-Content $probeLog -Raw
Remove-Item -Force $probeLog -ErrorAction SilentlyContinue

$duration = [double](ffprobe -v error -show_entries format=duration -of csv=p=0 $out)
Write-Host ("  duration    {0:N2}s" -f $duration) -NoNewline
if ($duration -ge 5 -and $duration -le 12) { Write-Host "   OK" -ForegroundColor Green }
else { Write-Host "   want 5-10s" -ForegroundColor Red }

if ($probe -match "max_volume:\s*(-?[\d.]+) dB") {
    $peak = [double]$Matches[1]
    Write-Host ("  peak        {0:N1} dB" -f $peak) -NoNewline
    if ($peak -gt -1) { Write-Host "   clipping -- lower the source volume" -ForegroundColor Red }
    elseif ($peak -lt -12) { Write-Host "   too quiet" -ForegroundColor Red }
    else { Write-Host "   OK" -ForegroundColor Green }
}
if ($probe -match "mean_volume:\s*(-?[\d.]+) dB") {
    Write-Host ("  mean        {0:N1} dB" -f [double]$Matches[1])
}

$silences = ([regex]::Matches($probe, "silence_duration:\s*([\d.]+)")) |
    ForEach-Object { [double]$_.Groups[1].Value }
$silent = ($silences | Measure-Object -Sum).Sum
if (-not $silent) { $silent = 0 }
$ratio = $silent / $duration
Write-Host ("  silence     {0:P0} of clip" -f $ratio) -NoNewline
if ($ratio -gt 0.3) { Write-Host "   too much dead air -- pick a denser section" -ForegroundColor Red }
else { Write-Host "   OK" -ForegroundColor Green }

Write-Host "`nsaved  $out" -ForegroundColor Green
Write-Host @"

Next:
  1. Listen to it. If it sounds bad to you, it will clone badly.
  2. Put the EXACT transcript in .env as QWEN3_REF_TEXT -- cloning quality
     drops noticeably when the transcript doesn't match.
  3. Set TTS_BACKEND=qwen3 and restart the server.

Only clone a voice you have permission to use: your own, one recorded with
consent, or a public dataset sample.
"@
