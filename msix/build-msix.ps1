# build-msix.ps1 - Pack the STORE edition's PyInstaller onedir build into an unsigned
# .msix for Microsoft Store submission. Usually invoked by ..\build-app.ps1 -Editions store -Msix;
# runs standalone too once a store dist exists.
#
# What it does: reads the Partner Center identity from msix\identity.json (gitignored; copy
# identity.sample.json and fill in the real Product identity values), scrapes the app version
# from live_transcribe\licensing.py, renders the tile PNGs from the brand mark
# (msix\generate-assets.py), stamps msix\AppxManifest.template.xml, assembles a package layout
# and runs the Windows SDK's makeappx pack. The output goes OUTSIDE OneDrive, next to the other
# build artefacts: %LOCALAPPDATA%\sa-live-transcribe\app-build\Volksmond-Store-<ver>.msix.
#
# Deliberately NOT signed: the Store re-signs every package on ingestion, so a local signature
# would be discarded anyway, and an unsigned .msix is exactly what Partner Center expects.
#
# Run from anywhere:  .\msix\build-msix.ps1   (optionally -Dist <path to the store onedir app>)
[CmdletBinding()]
param(
    # The STORE edition's onedir app (dist-store\Volksmond, as built by build-app.ps1
    # -Editions store). The store edition, not connected: the two differ (the store build
    # compiles the update check out), and packing the wrong one would ship a Store app that
    # still phones our manifest.
    [string]$Dist = (Join-Path $env:LOCALAPPDATA "sa-live-transcribe\app-build\dist-store\Volksmond")
)

# NOT "Stop", for the same reason as build-app.ps1: native tools write progress to stderr, and
# under "Stop" the first such line trips PowerShell's NativeCommandError trap. Every step below
# gates on $LASTEXITCODE explicitly instead.
$ErrorActionPreference = "Continue"
$here = $PSScriptRoot                       # msix\
$root = Split-Path $here -Parent            # repo root
$out = Join-Path $env:LOCALAPPDATA "sa-live-transcribe\app-build"
$pyexe = Join-Path $env:LOCALAPPDATA "sa-live-transcribe\.venv\Scripts\python.exe"

if (-not (Test-Path (Join-Path $Dist "Volksmond.exe"))) {
    Write-Host "  Store dist not found at $Dist" -ForegroundColor Red
    Write-Host "  Build it first:  .\build-app.ps1 -Editions store" -ForegroundColor Red
    exit 1
}

# Only a verified STORE dist may be packed. build-app.ps1's store pass writes this marker
# (containing the app version) at the dist-store root, a SIBLING of the app folder, so it never
# enters the layout below. Its absence means the dist is a connected/offline build or a stray
# copy, and a connected onedir packed here would ship a Store app that still phones our update
# manifest; the version check catches a stale store dist left over from an older release.
$marker = Join-Path (Split-Path $Dist -Parent) "store-edition.marker"
if (-not (Test-Path $marker)) {
    Write-Host "  $Dist is not a verified STORE build (no store-edition.marker beside it)." -ForegroundColor Red
    Write-Host "  Build the store edition first:  .\build-app.ps1 -Editions store" -ForegroundColor Red
    exit 1
}
$markerVer = (Get-Content $marker -TotalCount 1).Trim()

if (-not (Test-Path $pyexe)) {
    Write-Host "  venv not found at $pyexe - run 'First-time setup.bat' first." -ForegroundColor Red
    exit 1
}

# Pillow renders the tile assets (generate-assets.py). It is a build-time tool, not an app
# dependency, so the app venv does not otherwise carry it; install on first use, like
# build-app.ps1 does for PyInstaller.
& $pyexe -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Installing Pillow (renders the tile assets)..." -ForegroundColor Gray
    & $pyexe -m pip install pillow
    if ($LASTEXITCODE -ne 0) { Write-Host "  Pillow install failed." -ForegroundColor Red; exit 1 }
}

# Partner Center identity. identity.json is gitignored (public repo); the sample documents the
# shape and where the real values live in Partner Center.
$idFile = Join-Path $here "identity.json"
if (-not (Test-Path $idFile)) {
    Write-Host "  msix\identity.json not found." -ForegroundColor Red
    Write-Host "  Copy msix\identity.sample.json to msix\identity.json and fill in the real values" -ForegroundColor Red
    Write-Host "  from Partner Center (your app > Product management > Product identity)." -ForegroundColor Red
    exit 1
}
$identity = Get-Content $idFile -Raw | ConvertFrom-Json
foreach ($k in @("IdentityName", "Publisher", "PublisherDisplayName")) {
    if (-not $identity.$k) {
        Write-Host "  msix\identity.json is missing '$k' (see identity.sample.json)." -ForegroundColor Red
        exit 1
    }
    # Sample values must never reach a package: a .msix stamped with them sails through
    # makeappx and only bounces at Partner Center, or worse, gets handed around looking real.
    # (-match is case-insensitive in PowerShell.)
    if ($identity.$k -match "PLACEHOLDER") {
        Write-Host "  msix\identity.json '$k' still carries a PLACEHOLDER value ('$($identity.$k)')." -ForegroundColor Red
        Write-Host "  Fill in the real values from Partner Center (your app > Product management > Product identity)." -ForegroundColor Red
        exit 1
    }
}
if ($identity.Publisher -eq "CN=00000000-0000-0000-0000-000000000000") {
    Write-Host "  msix\identity.json 'Publisher' is the sample's all-zero GUID, not a real identity." -ForegroundColor Red
    Write-Host "  Use the exact 'Package/Identity/Publisher' value from Partner Center (Product identity)." -ForegroundColor Red
    exit 1
}

# App version from licensing.py (the single version source), as the Store's required 4-part
# form with revision 0.
$ver = $null
$licPy = Join-Path $root "live_transcribe\licensing.py"
$verLine = Get-Content $licPy | Where-Object { $_ -match 'APP_VERSION\s*=' } | Select-Object -First 1
if ($verLine -match '"([0-9]+\.[0-9]+\.[0-9]+)"') { $ver = $Matches[1] }
if (-not $ver) {
    Write-Host "  Could not read APP_VERSION from $licPy" -ForegroundColor Red
    exit 1
}
if ($markerVer -ne $ver) {
    Write-Host "  The store dist is v$markerVer but the source is v$ver (a stale dist-store)." -ForegroundColor Red
    Write-Host "  Rebuild it:  .\build-app.ps1 -Editions store" -ForegroundColor Red
    exit 1
}
$msixVer = "$ver.0"

# makeappx from the Windows SDK, newest installed version (a real [version] sort, because a
# lexicographic one would rank 10.0.9xxxx above 10.0.26xxx). Not vendored and not assumed on PATH.
$makeappx = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\makeappx.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.Directory.Parent.Name -match '^[0-9]+(\.[0-9]+)+$' } |
    Sort-Object { [version]$_.Directory.Parent.Name } | Select-Object -Last 1
if (-not $makeappx) {
    Write-Host "  makeappx.exe not found under ${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\" -ForegroundColor Red
    Write-Host "  Install the Windows SDK:  winget install Microsoft.WindowsSDK.10.0.26100" -ForegroundColor Red
    exit 1
}
Write-Host "  makeappx: $($makeappx.FullName)" -ForegroundColor Gray

# Assemble the package layout: the onedir app under Volksmond\, the generated tiles under
# Assets\, and the stamped manifest at the root. Rebuilt from scratch every time so a stale
# layout can never leak old files into the package.
$layout = Join-Path $out "msix-layout"
if (Test-Path $layout) { Remove-Item $layout -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $layout "Volksmond") | Out-Null
Write-Host "  Laying out the package (copying the app)..." -ForegroundColor Gray
Copy-Item (Join-Path $Dist "*") (Join-Path $layout "Volksmond") -Recurse -Force

Write-Host "  Rendering the tile assets from the brand mark..." -ForegroundColor Gray
Push-Location $root
try {
    & $pyexe (Join-Path $here "generate-assets.py") (Join-Path $layout "Assets")
    if ($LASTEXITCODE -ne 0) { Write-Host "  Tile asset generation failed (rc=$LASTEXITCODE)." -ForegroundColor Red; exit 1 }
} finally {
    Pop-Location
}

$manifest = Get-Content (Join-Path $here "AppxManifest.template.xml") -Raw
$manifest = $manifest.Replace("{{IDENTITY_NAME}}", $identity.IdentityName)
$manifest = $manifest.Replace("{{PUBLISHER}}", $identity.Publisher)
$manifest = $manifest.Replace("{{PUBLISHER_DISPLAY_NAME}}", $identity.PublisherDisplayName)
$manifest = $manifest.Replace("{{VERSION}}", $msixVer)
$manifest = $manifest.Replace("{{EXECUTABLE}}", "Volksmond\Volksmond.exe")
# UTF-8 without BOM: makeappx accepts a BOM, but Partner Center tooling has historically been
# strict about manifest encoding, so write the plain form.
[System.IO.File]::WriteAllText((Join-Path $layout "AppxManifest.xml"), $manifest,
    (New-Object System.Text.UTF8Encoding($false)))

$msix = Join-Path $out ("Volksmond-Store-" + $ver + ".msix")
if (Test-Path $msix) { Remove-Item $msix -Force }
& $makeappx.FullName pack /d $layout /p $msix /o
if ($LASTEXITCODE -ne 0) {
    Write-Host "  makeappx pack failed (rc=$LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}
$mb = [math]::Round((Get-Item $msix).Length / 1MB, 0)
Write-Host "  MSIX: $msix  ($mb MB, unsigned; the Store signs on ingestion)" -ForegroundColor Green
