<#
.SYNOPSIS
    Verifies every stage of the pipeline and reports what's broken.

.DESCRIPTION
    Run this before anything else if she isn't talking. It loads each model,
    times it, and does a TTS -> STT round trip that proves the audio path works.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) { throw "Run scripts\setup.ps1 first." }

Push-Location (Join-Path $Root "server")
try {
    & $VenvPython -m app.doctor
    exit $LASTEXITCODE
}
finally { Pop-Location }
