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

# NOT "Stop": PyInstaller writes its INFO lines to stderr, and under "Stop" the first one
# trips PowerShell's NativeCommandError trap and aborts the script before $LASTEXITCODE is
# read. The script already gates every step on $LASTEXITCODE explicitly, so "Continue" gives
# the same safety without the brittleness.
$ErrorActionPreference = "Continue"
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

# llama.cpp must contain NO AVX-512, or the summary engine crashes with
# STATUS_ILLEGAL_INSTRUCTION on AVX2-only CPUs (e.g. an Intel i7-9750H). abetlen's 0.3.23
# "cpu" wheel is compiled WITH AVX-512; the 0.3.22 "cpu" wheel is AVX2-safe and still supports
# Gemma 4. This guard pins 0.3.22 and VERIFIES by disassembling ggml-cpu.dll (a WHEEL-tag check
# is NOT enough: both wheels are tagged py3-none). It fails the build if any AVX-512 remains.
& $pyexe (Join-Path $here "tools\ensure_avx2_llama.py")
if ($LASTEXITCODE -ne 0) { Write-Host "  llama.cpp AVX-512 guard failed; aborting build." -ForegroundColor Red; exit 1 }

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

# Build a single-file installer (Inno Setup) from the one-folder app: a proper installed app
# (Start menu + Add/Remove Programs + uninstaller, per-user, no admin) and one .exe to hand out.
# Skipped with a note if Inno Setup is not installed (winget install JRSoftware.InnoSetup).
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
$iss = Join-Path $here "volksmond.iss"
if ($iscc -and (Test-Path $iss)) {
    & $iscc "/DMyAppVersion=$ver" "/DMySourceDir=$dist" "/DMyOutputDir=$out" $iss
    if ($LASTEXITCODE -eq 0) {
        $setup = Join-Path $out ("Volksmond-Setup-" + $ver + ".exe")
        if (Test-Path $setup) {
            $smb = [math]::Round((Get-Item $setup).Length / 1MB, 0)
            Write-Host "  Installer: $setup  ($smb MB)" -ForegroundColor Green
            $setupSynced = Join-Path $here ("Volksmond-Setup-" + $ver + ".exe")
            Copy-Item $setup $setupSynced -Force
            Write-Host "  Synced installer (OneDrive): $setupSynced" -ForegroundColor Green
        }
    } else {
        Write-Host "  Inno Setup compile failed (rc=$LASTEXITCODE); the zip is still available." -ForegroundColor Yellow
    }
} else {
    Write-Host "  Inno Setup not found; skipped the installer (zip still built). Add it with: winget install JRSoftware.InnoSetup" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  On the test laptop: run Volksmond-Setup-$ver.exe to install, or unzip $zipName and run Volksmond.exe." -ForegroundColor Green
