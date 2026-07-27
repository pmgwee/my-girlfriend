<#
.SYNOPSIS
    Frees the ports 雨桐 uses (backend 8765, frontend 3000).

.DESCRIPTION
    Python (uvicorn) and Node (next dev) do NOT exit when you close their
    terminal -- they keep holding their port with the OLD code. That is why a
    fresh `run_server.ps1` / `run_web.ps1` fails with Errno 10048 /
    EADDRINUSE. Run this first to kill the stale processes. Safe when nothing's
    there; it just reports "already free".

    The clean alternative is Ctrl+C in the SAME terminal before restarting --
    but once that terminal is gone, this is the only way back.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "SilentlyContinue"
$any = $false
foreach ($port in 8765, 3000) {
    foreach ($conn in Get-NetTCPConnection -LocalPort $port -State Listen) {
        $name = (Get-Process -Id $conn.OwningProcess).ProcessName
        Stop-Process -Id $conn.OwningProcess -Force
        Write-Host "killed $name (PID $($conn.OwningProcess)) on port $port"
        $any = $true
    }
}
if (-not $any) { Write-Host "ports 8765 and 3000 are already free" }
