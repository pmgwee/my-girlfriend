<#
.SYNOPSIS
    One-time setup: Python venv, backend deps, llama.cpp binaries, frontend deps.

.DESCRIPTION
    Safe to re-run. Each step detects existing state and skips.
#>
[CmdletBinding()]
param(
    # Your driver decides this. 561.09+ supports either; 12.4 is the safer default.
    [ValidateSet("12.4", "13.3")]
    [string]$CudaVersion = "12.4",
    [switch]$SkipLlamaCpp,
    # Install cuBLAS + cuDNN (~1.3GB) so Whisper can run on the GPU. Only worth
    # it if you have VRAM to spare after the LLM -- see README.
    [switch]$GpuStt,
    # Install torch + qwen-tts (~3GB) for voice cloning. See README 'Voice cloning'.
    [switch]$VoiceClone
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Tools = Join-Path $Root "tools"

Write-Host "`n=== Python environment ===" -ForegroundColor Yellow

if (-not (Test-Path $Venv)) {
    # faster-whisper and onnxruntime have no 3.13 wheels yet; 3.10-3.12 only.
    $python = $null
    foreach ($candidate in @("3.12", "3.11", "3.10")) {
        $found = & py "-$candidate" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $found; break }
    }
    if (-not $python) { throw "Need Python 3.10-3.12. Install from python.org and re-run." }

    Write-Host "  creating venv with $python" -ForegroundColor Cyan
    & $python -m venv $Venv
}
else {
    Write-Host "  venv exists" -ForegroundColor DarkGray
}

$VenvPython = Join-Path $Venv "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip --quiet
Write-Host "  installing backend requirements (a few minutes)..." -ForegroundColor Cyan
& $VenvPython -m pip install -r (Join-Path $Root "server\requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Kokoro's Mandarin frontend. NOT pulled in by kokoro-onnx, and without it every
# zh voice fails at runtime. The '-fork' build is the one kokoro-onnx targets;
# the upstream 'misaki' package conflicts with kokoro-onnx's pinned deps.
Write-Host "  installing Chinese G2P (misaki-fork[zh])..." -ForegroundColor Cyan
& $VenvPython -m pip install "misaki-fork[zh]" --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Warning "misaki-fork[zh] failed. Chinese voices won't work; set TTS_BACKEND=piper in .env."
}

if ($GpuStt) {
    # CTranslate2 links these dynamically but doesn't bundle them; installing the
    # full CUDA Toolkit for two DLLs is overkill. app/stt/whisper.py finds them.
    Write-Host "  installing CUDA runtime for GPU Whisper (~1.3GB)..." -ForegroundColor Cyan
    & $VenvPython -m pip install "nvidia-cublas-cu12>=12.3" "nvidia-cudnn-cu12>=9.0" --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "CUDA runtime install failed. Whisper will stay on CPU."
    }
}

if ($VoiceClone) {
    # Kokoro's voices are a fixed embedding bank -- cloning is impossible with it
    # at any quality. That is the only reason to pull in torch.
    # qwen-tts's speech tokenizer shells out to the SoX *binary* to normalise the
    # reference clip. The pip package `sox` is only a wrapper -- without the exe
    # on PATH, loading dies with "SoX could not be found!".
    if (-not (Get-Command sox -ErrorAction SilentlyContinue)) {
        Write-Host "  installing SoX (required by qwen-tts)..." -ForegroundColor Cyan
        winget install --id ChrisBagwell.SoX --accept-source-agreements --accept-package-agreements --silent --disable-interactivity
        # winget edits the persisted PATH, not this process's copy.
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not (Get-Command sox -ErrorAction SilentlyContinue)) {
            Write-Warning "SoX still not on PATH. Open a new terminal, or install it manually from http://sox.sourceforge.net/"
        }
    }

    Write-Host "  installing torch (CUDA 12.4) + qwen-tts for voice cloning (~3GB)..." -ForegroundColor Cyan
    & $VenvPython -m pip install torch --index-url https://download.pytorch.org/whl/cu124 --quiet
    if ($LASTEXITCODE -ne 0) { Write-Warning "torch install failed; voice cloning unavailable." }
    else {
        & $VenvPython -m pip install -U qwen-tts --quiet
        if ($LASTEXITCODE -ne 0) { Write-Warning "qwen-tts install failed." }
        else { Write-Host "  voice cloning ready -- see scripts\make_voice.ps1" -ForegroundColor Green }
    }
}

Write-Host "`n=== llama.cpp ===" -ForegroundColor Yellow

if ($SkipLlamaCpp) {
    Write-Host "  skipped" -ForegroundColor DarkGray
}
elseif (Test-Path (Join-Path $Tools "llama\llama-server.exe")) {
    Write-Host "  llama-server.exe already present" -ForegroundColor DarkGray
}
else {
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" `
        -UseBasicParsing -Headers @{ "User-Agent" = "vg-setup" }
    Write-Host "  release $($release.tag_name), CUDA $CudaVersion" -ForegroundColor Cyan

    New-Item -ItemType Directory -Force -Path $Tools | Out-Null

    # Two archives: the binaries, plus the CUDA runtime DLLs they link against.
    # Without cudart, llama-server.exe fails to start with no useful message.
    foreach ($pattern in @("llama-.*-bin-win-cuda-$CudaVersion-x64\.zip", "cudart-llama-bin-win-cuda-$CudaVersion-x64\.zip")) {
        $asset = $release.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
        if (-not $asset) { throw "No asset matching $pattern in release $($release.tag_name)" }

        $zip = Join-Path $env:TEMP $asset.name
        Write-Host "  downloading $($asset.name) ($([math]::Round($asset.size/1MB,1)) MB)..." -ForegroundColor Cyan
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing -TimeoutSec 1800
        Expand-Archive -Force -Path $zip -DestinationPath (Join-Path $Tools "llama")
        Remove-Item -Force $zip
    }

    if (-not (Test-Path (Join-Path $Tools "llama\llama-server.exe"))) {
        throw "llama-server.exe not found after extraction -- check $Tools\llama"
    }
    Write-Host "  llama.cpp ready" -ForegroundColor Green
}

Write-Host "`n=== Frontend ===" -ForegroundColor Yellow
Push-Location (Join-Path $Root "web")
try {
    if (Test-Path "node_modules") {
        Write-Host "  node_modules exists" -ForegroundColor DarkGray
    }
    else {
        Write-Host "  npm install..." -ForegroundColor Cyan
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    }
}
finally { Pop-Location }

$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $Root ".env.example") $envFile
    Write-Host "`n  created .env from .env.example" -ForegroundColor Green
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host @"

Start three terminals, in this order:

  1.  scripts\run_llm.ps1        (language model, wait for 'server listening')
  2.  scripts\run_server.ps1     (voice pipeline)
  3.  scripts\run_web.ps1        (UI, then open http://localhost:3000)

"@
