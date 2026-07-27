<#
.SYNOPSIS
    Downloads every model the pipeline needs, sized for a 6GB GPU.

.DESCRIPTION
    Total download is ~3.1GB. Everything is resumable-by-restart: existing files
    are skipped, so re-running after a dropped connection is safe.

    Whisper is NOT downloaded here -- faster-whisper pulls it from HuggingFace on
    first run and caches it under %USERPROFILE%\.cache\huggingface.
#>
[CmdletBinding()]
param(
    # Also fetch Piper as a fallback TTS.
    [switch]$IncludePiper,
    # Skip the 2.5GB language model if you already have a GGUF.
    [switch]$SkipLlm
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = Split-Path -Parent $PSScriptRoot
$Models = Join-Path $Root "models"

$Curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source

function Get-File {
    param([string]$Url, [string]$Path, [string]$Label)

    if (Test-Path $Path) {
        $mb = [math]::Round((Get-Item $Path).Length / 1MB, 1)
        Write-Host "  skip  $Label ($mb MB already present)" -ForegroundColor DarkGray
        return
    }

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    Write-Host "  get   $Label ..." -ForegroundColor Cyan

    # Download to a temp name so an interrupted transfer never leaves a
    # half-written file that the skip-check above would treat as complete.
    $temp = "$Path.partial"

    if ($Curl) {
        # curl.exe ships with Windows 10+ and is dramatically faster than
        # Invoke-WebRequest, which buffers the whole response in memory on
        # PowerShell 5.1 and crawls on multi-GB files. '-C -' resumes a partial
        # transfer, so re-running after a dropped connection continues rather
        # than restarting.
        & $Curl -L --fail --retry 3 --retry-delay 2 -C - -o $temp $Url
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to download $Label from $Url (curl exit $LASTEXITCODE)"
        }
    }
    else {
        try {
            Invoke-WebRequest -Uri $Url -OutFile $temp -UseBasicParsing -TimeoutSec 3600
        }
        catch {
            if (Test-Path $temp) { Remove-Item -Force $temp }
            throw "Failed to download $Label from $Url`n$($_.Exception.Message)"
        }
    }

    Move-Item -Force $temp $Path
    $mb = [math]::Round((Get-Item $Path).Length / 1MB, 1)
    Write-Host "        done ($mb MB)" -ForegroundColor Green
}

Write-Host "`n=== Silero VAD v5 ===" -ForegroundColor Yellow
Get-File `
    -Url "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx" `
    -Path (Join-Path $Models "silero_vad.onnx") `
    -Label "silero_vad.onnx (2 MB)"

Write-Host "`n=== Kokoro TTS (Chinese) ===" -ForegroundColor Yellow
# Release tag is 'model-files-v1.1' but the assets are named '-v1.1-zh'. The
# v1.0 assets do NOT contain Chinese voices -- don't substitute them.
Get-File `
    -Url "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.1-zh.onnx" `
    -Path (Join-Path $Models "kokoro\kokoro-v1.1-zh.onnx") `
    -Label "kokoro-v1.1-zh.onnx (328 MB)"
Get-File `
    -Url "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.1-zh.bin" `
    -Path (Join-Path $Models "kokoro\voices-v1.1-zh.bin") `
    -Label "voices-v1.1-zh.bin (51 MB)"
# The v1.1-zh phoneme vocabulary. Without it Mandarin maps to the wrong token
# ids and synthesis comes out as noise -- with no error to tell you why.
Get-File `
    -Url "https://huggingface.co/hexgrad/Kokoro-82M-v1.1-zh/resolve/main/config.json" `
    -Path (Join-Path $Models "kokoro\config.json") `
    -Label "config.json (vocab)"

if (-not $SkipLlm) {
    Write-Host "`n=== Language model ===" -ForegroundColor Yellow
    # Qwen3-4B at Q4_K_M is ~2.5GB resident. With Whisper-small int8 (~0.6GB)
    # that leaves roughly 2GB of a 6GB card free for context and the desktop.
    Get-File `
        -Url "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf" `
        -Path (Join-Path $Models "llm\Qwen3-4B-Instruct-2507-Q4_K_M.gguf") `
        -Label "Qwen3-4B-Instruct Q4_K_M (2.5 GB)"
}

if ($IncludePiper) {
    Write-Host "`n=== Piper (fallback TTS) ===" -ForegroundColor Yellow
    $piperZip = Join-Path $env:TEMP "piper_win.zip"
    Get-File `
        -Url "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip" `
        -Path $piperZip -Label "piper_windows_amd64.zip"
    Expand-Archive -Force -Path $piperZip -DestinationPath (Join-Path $Models "piper_tmp")
    Get-ChildItem -Recurse (Join-Path $Models "piper_tmp") |
        Where-Object { -not $_.PSIsContainer } |
        ForEach-Object { Move-Item -Force $_.FullName (Join-Path $Models "piper\$($_.Name)") -ErrorAction SilentlyContinue }
    Remove-Item -Recurse -Force (Join-Path $Models "piper_tmp") -ErrorAction SilentlyContinue

    Get-File `
        -Url "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx" `
        -Path (Join-Path $Models "piper\zh_CN-huayan-medium.onnx") -Label "zh_CN-huayan-medium.onnx"
    Get-File `
        -Url "https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json" `
        -Path (Join-Path $Models "piper\zh_CN-huayan-medium.onnx.json") -Label "zh_CN-huayan-medium.onnx.json"
}

Write-Host "`nAll models ready in $Models" -ForegroundColor Green
Write-Host "Next: scripts\setup.ps1`n"
