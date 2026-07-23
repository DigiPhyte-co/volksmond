# build-linux.ps1 - Build the Volksmond Linux release artifacts (.deb + tarball) in
# Docker on this Windows machine, then run the container smoke gates.
#
# The Linux binary cannot be cross-compiled (PyInstaller freezes for the host it runs
# on), so the build runs inside an ubuntu:22.04 container (glibc 2.35 = the support
# floor: Ubuntu 22.04 / Mint 21 / Debian 12; docs/linux-port-plan.md section 2.6).
# Stages:
#   1. docker build  linux/Dockerfile      -> the pinned build image (layer-cached;
#                                             repeat runs skip the multi-GB pip pull)
#   2. docker run    build-app-linux.sh    -> dist-linux\Volksmond-<ver>.deb
#                                             + Volksmond-<ver>-linux-x64.tar.gz
#   3. docker run    smoke.sh              -> CLEAN ubuntu:22.04 AND debian:12:
#                                             apt-get install ./<deb> (Depends must
#                                             resolve), volksmond --server-only,
#                                             HTTP 200 on the web UI root; ubuntu
#                                             additionally runs the xvfb window
#                                             stage (default pywebview/GTK window
#                                             boot; catches missing gi typelibs)
#   4. docker run    pulse-fixture.sh      -> null-sink capture fixture (real backend,
#                                             MIC + SYS non-silent; optional stage)
#
# Output lands in dist-linux\ next to this script (gitignored). Run this from a repo
# checkout on C:\dev, NOT from the OneDrive cockpit: the artifacts are ~1 GB and
# OneDrive locks files mid-build. If the build container is OOM-killed (WSL2 memory
# ceiling), raise it in %USERPROFILE%\.wslconfig ([wsl2] memory=12GB) and
# `wsl --shutdown` (plan risk R6).
#
# Run from the project root:  .\build-linux.ps1
[CmdletBinding()]
param(
    [switch]$SkipSmoke,    # skip stage 3 (fast iteration on the build itself)
    [switch]$SkipFixture,  # skip stage 4 (the pulse null-sink capture fixture)
    [switch]$NoCache       # rebuild the Docker image from scratch
)

$ErrorActionPreference = "Continue"
$here = $PSScriptRoot
$img = "volksmond-linux-build"

& docker version --format "{{.Server.Version}}" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Docker is not running. Start Docker Desktop and retry." -ForegroundColor Red
    exit 1
}

# App version, read once from licensing.py (same regex as build-app.ps1) so the
# artifact names are checked against the pinned release-lane interface.
$ver = ""
$licPy = Join-Path $here "live_transcribe\licensing.py"
$verLine = Get-Content $licPy | Where-Object { $_ -match 'APP_VERSION\s*=' } | Select-Object -First 1
if ($verLine -match '"([0-9]+\.[0-9]+\.[0-9]+)"') { $ver = $Matches[1] }
if (-not $ver) {
    Write-Host "  Could not read APP_VERSION from live_transcribe\licensing.py." -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "  === Volksmond $ver Linux lane (Docker, ubuntu:22.04 base) ===" -ForegroundColor Cyan

$out = Join-Path $here "dist-linux"
New-Item -ItemType Directory -Force -Path $out | Out-Null

# Every in-container script is CR-stripped before bash runs it: this repo is checked
# out on Windows (core.autocrlf), and bash treats a stray \r as part of the command.
# The function deliberately returns NOTHING: docker's live output already flows down
# the success stream, and a `return $LASTEXITCODE` would mix into it, handing callers
# an array instead of a number (found the hard way: `$rc -ne 0` on an array is truthy
# even for a passing build). Callers read $LASTEXITCODE directly after the call - it
# is global and survives the function boundary - the same gating style as build-app.ps1.
function Invoke-ContainerScript([string[]]$DockerArgs, [string]$Script, [string]$ScriptArgs) {
    $inner = "sed 's/\r$//' $Script > /tmp/run.sh && bash /tmp/run.sh $ScriptArgs"
    & docker @DockerArgs bash -lc $inner
}

# --- 1) Build image (layer-cached: apt + the full pip env) ------------------------------
Write-Host ""
Write-Host "  [1/4] docker build ($img)" -ForegroundColor Cyan
$buildArgs = @("build", "-t", $img, "-f", (Join-Path $here "linux\Dockerfile"))
if ($NoCache) { $buildArgs += "--no-cache" }
$buildArgs += $here
& docker @buildArgs
if ($LASTEXITCODE -ne 0) { Write-Host "  docker build failed." -ForegroundColor Red; exit 1 }

# --- 2) PyInstaller + dpkg-deb + tarball -------------------------------------------------
Write-Host ""
Write-Host "  [2/4] build-app-linux.sh (PyInstaller -> .deb + tarball)" -ForegroundColor Cyan
Invoke-ContainerScript @("run", "--rm", "-v", "${here}:/src", "-v", "${out}:/out", $img) `
    "/src/linux/build-app-linux.sh" ""
if ($LASTEXITCODE -ne 0) { Write-Host "  Linux build failed (rc=$LASTEXITCODE)." -ForegroundColor Red; exit 1 }

$deb = Join-Path $out "Volksmond-$ver.deb"
$tarball = Join-Path $out "Volksmond-$ver-linux-x64.tar.gz"
foreach ($f in @($deb, $tarball)) {
    if (-not (Test-Path $f)) { Write-Host "  Expected artifact missing: $f" -ForegroundColor Red; exit 1 }
}

# --- 3) Smoke gate: clean-container install + headless boot on BOTH floor distros -------
if ($SkipSmoke) {
    Write-Host "  [3/4] SKIPPED (-SkipSmoke): clean-container smoke on ubuntu:22.04 + debian:12" -ForegroundColor Yellow
} else {
    foreach ($image in @("ubuntu:22.04", "debian:12")) {
        # The xvfb window stage (default pywebview/GTK window boot) runs on the
        # ubuntu smoke only; debian stays server-only for wall time.
        $stage2 = if ($image -eq "ubuntu:22.04") { " window" } else { "" }
        Write-Host ""
        Write-Host "  [3/4] smoke.sh on $image$(if ($stage2) { ' (+ window stage)' })" -ForegroundColor Cyan
        Invoke-ContainerScript @("run", "--rm", "-v", "${here}:/src:ro", "-v", "${out}:/out:ro", $image) `
            "/src/linux/smoke.sh" "/out/Volksmond-$ver.deb$stage2"
        if ($LASTEXITCODE -ne 0) { Write-Host "  Smoke FAILED on $image (rc=$LASTEXITCODE)." -ForegroundColor Red; exit 1 }
        Write-Host "  Smoke passed on $image." -ForegroundColor Green
    }
}

# --- 4) Pulse null-sink capture fixture (real backend, headless, no audio hardware) -----
if ($SkipFixture) {
    Write-Host "  [4/4] SKIPPED (-SkipFixture): pulse null-sink capture fixture" -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "  [4/4] pulse-fixture.sh (null-sink MIC + SYS capture)" -ForegroundColor Cyan
    Invoke-ContainerScript @("run", "--rm", "-v", "${here}:/src:ro", $img) `
        "/src/linux/pulse-fixture.sh" ""
    if ($LASTEXITCODE -ne 0) { Write-Host "  Pulse fixture FAILED (rc=$LASTEXITCODE)." -ForegroundColor Red; exit 1 }
    Write-Host "  Pulse fixture passed." -ForegroundColor Green
}

Write-Host ""
foreach ($f in @($deb, $tarball)) {
    $mb = [math]::Round((Get-Item $f).Length / 1MB, 0)
    $sha = (Get-FileHash $f -Algorithm SHA256).Hash
    Write-Host "  $(Split-Path $f -Leaf)  ($mb MB)" -ForegroundColor Green
    Write-Host "    SHA256 $sha" -ForegroundColor Gray
}
Write-Host ""
Write-Host "  Done ($ver). Publish with release.ps1's Linux lane; hardware validation (WP-LH) installs $((Split-Path $deb -Leaf)) on the Mint box." -ForegroundColor Green
