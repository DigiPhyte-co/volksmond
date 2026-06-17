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

# llama.cpp MUST be the PORTABLE prebuilt CPU wheel, not a local source build. A source
# build bakes in THIS PC's CPU instructions (the Ryzen 7700X's AVX-512) and then crashes
# with STATUS_ILLEGAL_INSTRUCTION on any machine without them (e.g. an Intel laptop). The
# portable wheel is tagged "py3-none"; a local build is tagged "cp3xx". Enforce portable so
# the app opens on any CPU; auto-installs the portable wheel of the same version if needed.
$llVer = (& $pyexe -c "import importlib.metadata as m; print(m.version('llama-cpp-python'))").Trim()
$site = Join-Path (Split-Path $pyexe) "..\Lib\site-packages"
$llDist = Get-ChildItem $site -Filter "llama_cpp_python-*.dist-info" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
$llPortable = $false
if ($llDist) {
    $llTag = Get-Content (Join-Path $llDist.FullName "WHEEL") -ErrorAction SilentlyContinue | Where-Object { $_ -match '^Tag:' }
    if ($llTag -match 'py3-none') { $llPortable = $true }
}
if (-not $llPortable) {
    Write-Host "  llama-cpp-python is a native build (would crash on CPUs without this PC's instructions); installing the portable CPU wheel..." -ForegroundColor Yellow
    & $pyexe -m pip install --force-reinstall --no-deps --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu "llama-cpp-python==$llVer"
    if ($LASTEXITCODE -ne 0) { Write-Host "  Could not install the portable llama-cpp-python wheel. Fix before shipping." -ForegroundColor Red; exit 1 }
    # Re-verify: --extra-index-url leaves PyPI in the candidate set, so a native wheel could
    # still have been picked. Re-read the WHEEL tag and FAIL unless it is portable (py3-none),
    # so a CPU-specific binary can never ship.
    $llDist2 = Get-ChildItem $site -Filter "llama_cpp_python-*.dist-info" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    $tag2 = ""
    if ($llDist2) { $tag2 = (Get-Content (Join-Path $llDist2.FullName "WHEEL") -ErrorAction SilentlyContinue | Where-Object { $_ -match '^Tag:' }) -join " " }
    if ($tag2 -notmatch 'py3-none') {
        Write-Host "  After reinstall, llama-cpp-python is STILL not the portable wheel (tag: '$tag2'). Aborting so a native binary cannot ship." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Portable llama-cpp-python wheel confirmed (tag: $tag2)." -ForegroundColor Green
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

# Bundle the Quick Start guides next to the exe so testers get them inside the zip.
$pdfs = @("Volksmond - Quick Start Guide.pdf", "Volksmond - Snelgids (Afrikaans).pdf")
foreach ($pdf in $pdfs) {
    $pdfSrc = Join-Path $here $pdf
    if (Test-Path $pdfSrc) {
        Copy-Item $pdfSrc (Join-Path $dist $pdf) -Force
    } else {
        Write-Host "  WARNING: '$pdf' not found at repo root; not bundled in the zip." -ForegroundColor Yellow
    }
}

$mb = [math]::Round((Get-ChildItem $dist -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 0)
Write-Host ""
Write-Host "  Built: $dist  ($mb MB, Quick Start PDFs bundled)" -ForegroundColor Green

# Zip the one-folder app into a single file to copy to another PC. Name it with the
# app version (e.g. volksmond_1_0_5.zip) so each build is self-identifying and a
# tester can see at a glance which one they have.
$ver = "dev"
$licPy = Join-Path $here "live_transcribe\licensing.py"
$verLine = Get-Content $licPy | Where-Object { $_ -match 'APP_VERSION\s*=' } | Select-Object -First 1
if ($verLine -match '"([0-9]+\.[0-9]+\.[0-9]+)"') { $ver = $Matches[1] }
$zipName = "volksmond_" + ($ver -replace '\.', '_') + ".zip"
$zip = Join-Path $out $zipName
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $dist "*") -DestinationPath $zip
$zmb = [math]::Round((Get-Item $zip).Length / 1MB, 0)
Write-Host "  Zipped: $zip  ($zmb MB)" -ForegroundColor Green

# Drop a copy in the project folder (synced via OneDrive) so it reaches the test laptop.
# The build itself stays OUTSIDE OneDrive (see header) to avoid sync locks; only this one
# final zip is synced. $here is the repo root (this script's folder), which is in OneDrive.
$synced = Join-Path $here $zipName
Copy-Item $zip $synced -Force
Write-Host "  Synced copy (OneDrive): $synced" -ForegroundColor Green
Write-Host "  On the test laptop, open it from the synced Cowork folder, unzip, run Volksmond.exe." -ForegroundColor Green
