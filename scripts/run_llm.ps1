<#
.SYNOPSIS
    Starts llama.cpp's server with the girlfriend model.

.DESCRIPTION
    VRAM budget on a 6GB card, with Whisper-small int8 also resident (~0.6GB):

        Qwen3-4B Q4_K_M weights   ~2.5 GB
        4096-token KV cache       ~0.4 GB
        compute buffers           ~0.4 GB
        ----------------------------------
        total                     ~3.3 GB

    That leaves headroom for Windows' own desktop compositor, which can take
    500MB-1GB on its own. If you hit an out-of-memory error, lower -GpuLayers.
#>
[CmdletBinding()]
param(
    [string]$Model,
    # High port, deliberately. 8080 collides with Apache/Jenkins/Tomcat and 8090
    # with Windows' notification helper; both answer the socket, so the failure
    # shows up as a confusing 400 rather than a connection refused.
    [int]$Port = 18080,
    # 99 = offload everything. Drop to ~20 to keep half the model on CPU if the
    # GPU is also driving a game or a second app.
    [int]$GpuLayers = 99,
    # 4096 is ~12 turns of conversation. Raising it costs VRAM linearly.
    [int]$Context = 4096
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not $Model) {
    $Model = Join-Path $Root "models\llm\Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
}
if (-not (Test-Path $Model)) {
    throw "Model not found: $Model`nRun scripts\download_models.ps1 first."
}

$Server = Join-Path $Root "tools\llama\llama-server.exe"
if (-not (Test-Path $Server)) {
    throw "llama-server.exe not found.`nRun scripts\setup.ps1 first."
}

$occupied = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($occupied) {
    $owner = (Get-Process -Id $occupied[0].OwningProcess -ErrorAction SilentlyContinue).ProcessName
    throw "Port $Port is already in use by '$owner'.`nPick another: scripts\run_llm.ps1 -Port 8091 (then update LLM_BASE_URL in .env)."
}

Write-Host "Starting llama.cpp on port $Port" -ForegroundColor Green
Write-Host "  model   $(Split-Path -Leaf $Model)"
Write-Host "  layers  $GpuLayers on GPU"
Write-Host "  context $Context tokens`n"

& $Server `
    --model $Model `
    --port $Port `
    --host 127.0.0.1 `
    --n-gpu-layers $GpuLayers `
    --ctx-size $Context `
    --batch-size 512 `
    --threads 6 `
    --flash-attn on `
    --no-webui
