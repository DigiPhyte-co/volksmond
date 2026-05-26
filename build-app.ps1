# build-app.ps1 - Build the standalone Windows app (CPU-only, native window) with
# PyInstaller, then zip it into one file to copy to another PC.
#
# Output goes OUTSIDE OneDrive (to %LOCALAPPDATA%\sa-live-transcribe\app-build): a
# one-folder app + Volksmond.zip. OneDrive locks files mid-build (it broke
# PyInstaller's clean step) and would needlessly sync the ~400 MB result, so we
# build there instead. Run the .exe -> native window. The Whisper model is NOT
# bundled (multi-GB) - it downloads on first transcription.
#
# Run from the project root:  .\build-app.ps1
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$pyexe = Join-Path $env:LOCALAPPDATA "sa-live-transcribe\.venv\Scripts\python.exe"
# Build outside OneDrive (see header): avoids sync locks + a pointless 400 MB sync.
$out = Join-Path $env:LOCALAPPDATA "sa-live-transcribe\app-build"
$distRoot = Join-Path $out "dist"
$workRoot = Join-Path $out "build"

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
    & $pyexe -m PyInstaller --noconfirm --distpath $distRoot --workpath $workRoot sa-live-transcribe.spec
    $rc = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($rc -ne 0) { Write-Host "  Build failed (rc=$rc)." -ForegroundColor Red; exit $rc }

$dist = Join-Path $distRoot "Volksmond"
$mb = [math]::Round((Get-ChildItem $dist -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 0)
Write-Host ""
Write-Host "  Built: $dist  ($mb MB)" -ForegroundColor Green

# Zip the one-folder app into a single file to copy to another PC.
$zip = Join-Path $out "Volksmond.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $dist "*") -DestinationPath $zip
$zmb = [math]::Round((Get-Item $zip).Length / 1MB, 0)
Write-Host "  Zipped: $zip  ($zmb MB)" -ForegroundColor Green
Write-Host "  Copy the zip to the other PC, unzip, run Volksmond.exe (opens the app window)." -ForegroundColor Green
