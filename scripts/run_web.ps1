<#
.SYNOPSIS
    Starts the Next.js UI on http://localhost:3000.

.DESCRIPTION
    Note the microphone only works on localhost or HTTPS -- browsers block
    getUserMedia on plain-HTTP origins. Reaching this from another device on
    your LAN means putting it behind HTTPS.
#>
[CmdletBinding()]
param([switch]$Production)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Push-Location (Join-Path $Root "web")
try {
    if (-not (Test-Path "node_modules")) {
        throw "Dependencies missing.`nRun scripts\setup.ps1 first."
    }
    if ($Production) {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "build failed" }
        npm run start
    }
    else {
        npm run dev
    }
}
finally { Pop-Location }
