# start-meeting-ui.ps1, launches the browser UI for SA-Live-Transcribe
#
# Usage:
#   .\start-meeting-ui.ps1
#   .\start-meeting-ui.ps1 -Port 9000
#   .\start-meeting-ui.ps1 -NoBrowser     (don't open browser, e.g. on a server)
#
# The server binds to 127.0.0.1 (localhost only), never publicly reachable.
# Press Ctrl+C in this window to stop the server.

[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Bind = "127.0.0.1",   # -Bind not -Host: $Host is a PowerShell reserved variable
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$pyexe = Join-Path $env:LOCALAPPDATA "sa-live-transcribe\.venv\Scripts\python.exe"

if (-not (Test-Path $pyexe)) {
    Write-Host ""
    Write-Host "  [error] Python venv not found at:" -ForegroundColor Red
    Write-Host "    $pyexe"
    Write-Host ""
    Write-Host "  This is a fresh machine. Run 'First-time setup.bat' first" -ForegroundColor Yellow
    Write-Host "  (in this same folder)."
    exit 1
}

$cliArgs = @("-m", "live_transcribe.web", "--host", $Bind, "--port", $Port)
if ($NoBrowser) { $cliArgs += "--no-browser" }

Push-Location $here
try {
    & $pyexe @cliArgs
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $exitCode
