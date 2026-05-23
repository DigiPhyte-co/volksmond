# start-meeting-native.ps1, launches Volksmond as a native desktop window (pywebview).
#
# Usage:
#   .\start-meeting-native.ps1
#
# Same UI as the browser launcher, but in its own application window instead of a
# browser tab. The server binds to 127.0.0.1 (localhost only), never publicly
# reachable. Close the window to stop it.

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

# Prefer the canonical runtime venv; fall back to a project-local .venv.
$pyexe = Join-Path $env:LOCALAPPDATA "sa-live-transcribe\.venv\Scripts\python.exe"
if (-not (Test-Path $pyexe)) { $pyexe = Join-Path $here ".venv\Scripts\python.exe" }

if (-not (Test-Path $pyexe)) {
    Write-Host ""
    Write-Host "  [error] Python venv not found." -ForegroundColor Red
    Write-Host "  Run 'First-time setup.bat' in this folder first."
    Write-Host ""
    exit 1
}

# Native window mode is the default for python -m live_transcribe.desktop.
Push-Location $here
try {
    & $pyexe -m live_transcribe.desktop
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $exitCode
