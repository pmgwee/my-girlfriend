<#
.SYNOPSIS
    Renders a sample line in several Kokoro voices so you can pick one.

.DESCRIPTION
    Writes WAV files to samples\. Kokoro v1.1-zh has 100 Chinese voices with
    numeric names, so listening is the only practical way to choose.

.EXAMPLE
    scripts\list_voices.ps1 -Count 12
    scripts\list_voices.ps1 -Voices zf_001,zf_017,zm_010 -Text "今天过得怎么样呀"
#>
[CmdletBinding()]
param(
    [string]$Text = "诶，你终于来了呀，我等你好久了。",
    [string[]]$Voices,
    [int]$Count = 8
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) { throw "Run scripts\setup.ps1 first." }

$outDir = Join-Path $Root "samples"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# "__all__", not "": PS 5.1 drops an empty string when invoking a native exe,
# which shifts every later argument and breaks the Python positional parse below.
$voiceArg = if ($Voices) { $Voices -join "," } else { "__all__" }

$script = @'
import sys, numpy as np, soundfile as sf
from pathlib import Path
from kokoro_onnx import Kokoro

root, text, requested, count, out_dir = Path(sys.argv[1]), sys.argv[2], sys.argv[3], int(sys.argv[4]), Path(sys.argv[5])
kokoro = Kokoro(str(root / "models/kokoro/kokoro-v1.1-zh.onnx"), str(root / "models/kokoro/voices-v1.1-zh.bin"))

if requested and requested != "__all__":
    names = [v for v in requested.split(",") if v]
else:
    names = [n for n in sorted(np.load(root / "models/kokoro/voices-v1.1-zh.bin").files) if n.startswith("zf_")][:count]

for name in names:
    samples, rate = kokoro.create(text, voice=name, speed=1.0, lang="cmn")
    sf.write(out_dir / f"{name}.wav", samples, rate)
    print(f"  {name}.wav")
'@

$scriptPath = Join-Path $env:TEMP "vg_list_voices.py"
Set-Content -Path $scriptPath -Value $script -Encoding utf8

Write-Host "Rendering samples to $outDir" -ForegroundColor Green
& $VenvPython $scriptPath $Root $Text $voiceArg $Count $outDir
Remove-Item -Force $scriptPath

Write-Host "`nSet the one you like as TTS_VOICE in .env" -ForegroundColor Cyan
