# build-app.ps1 - Build the standalone Windows app (CPU-only) with PyInstaller.
#
# Output: dist\SA-Live-Transcribe\ - a one-folder app. Double-click
# SA-Live-Transcribe.exe to run (starts the local server + opens the UI in the
# browser; close the console window to stop). The Whisper model is NOT bundled
# (multi-GB) - it downloads on first transcription.
#
# Run from the project root:  .\build-app.ps1
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$pyexe = Join-Path $env:LOCALAPPDATA "sa-live-transcribe\.venv\Scripts\python.exe"

if (-not (Test-Path $pyexe)) {
    Write-Host "  venv not found at $pyexe - run 'First-time setup.bat' first." -ForegroundColor Red
    exit 1
}

& $pyexe -c "import PyInstaller"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Installing PyInstaller..." -ForegroundColor Gray
    & $pyexe -m pip install pyinstaller
}

Push-Location $here
$rc = 0
try {
    & $pyexe -m PyInstaller --noconfirm sa-live-transcribe.spec
    $rc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($rc -ne 0) { Write-Host "  Build failed (rc=$rc)." -ForegroundColor Red; exit $rc }

$dist = Join-Path $here "dist\SA-Live-Transcribe"
$mb = [math]::Round((Get-ChildItem $dist -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 0)
Write-Host ""
Write-Host "  Built: $dist  ($mb MB)" -ForegroundColor Green
Write-Host "  Try it: double-click SA-Live-Transcribe.exe (opens the UI in your browser)." -ForegroundColor Green
