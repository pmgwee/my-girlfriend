<#
.SYNOPSIS
    Starts the voice pipeline (VAD -> STT -> LLM -> TTS).

.DESCRIPTION
    First run downloads the Whisper weights from HuggingFace (~500MB for
    'small'), so expect a slower start. Subsequent runs load from cache in
    about 10 seconds.
#>
[CmdletBinding()]
param([switch]$Reload)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment missing.`nRun scripts\setup.ps1 first."
}
if (-not (Test-Path (Join-Path $Root "models\silero_vad.onnx"))) {
    throw "Models missing.`nRun scripts\download_models.ps1 first."
}

Push-Location (Join-Path $Root "server")
try {
    $arguments = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8765")
    if ($Reload) { $arguments += "--reload" }
    & $VenvPython @arguments
}
finally { Pop-Location }
