# setup.ps1 - First-time setup for SA-Live-Transcribe on a new machine.
#
# Creates the Python venv OUTSIDE OneDrive (at %LOCALAPPDATA%\sa-live-transcribe\.venv)
# so OneDrive doesn't sync 1.5 GB of Python binaries between machines.
# Installs pinned deps and pre-downloads the Whisper model that matches the
# detected hardware (GPU box -> large-v3 + large-v3-turbo; CPU-only -> large-v3-turbo).
#
# Usage:
#   .\setup.ps1                # standard setup
#   .\setup.ps1 -Force         # delete existing venv and start fresh
#   .\setup.ps1 -CpuOnly       # skip the GPU model even if a GPU is detected
#
# Re-running is safe -- pip install is idempotent and models are cached.

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$CpuOnly
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$venvDir = Join-Path $env:LOCALAPPDATA "sa-live-transcribe"
$venv = Join-Path $venvDir ".venv"
$pyexe = Join-Path $venv "Scripts\python.exe"

function Write-Step {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

function Write-Ok   { param([string]$T) Write-Host "  OK  $T" -ForegroundColor Green }
function Write-Note { param([string]$T) Write-Host "  ..  $T" -ForegroundColor Gray }
function Write-Bad  { param([string]$T) Write-Host "  !!  $T" -ForegroundColor Red }

# --- 1. Python 3.12 -----------------------------------------------------------
Write-Step "Checking for Python 3.12"
$has312 = $false
try {
    $list = & py -0 2>$null
    if ($list -match "3\.12") { $has312 = $true }
} catch {}

if (-not $has312) {
    Write-Bad "Python 3.12 is not installed."
    Write-Host ""
    Write-Host "  Install it from:" -ForegroundColor Yellow
    Write-Host "    https://www.python.org/downloads/release/python-3120/"
    Write-Host "  Or run in a new PowerShell window:" -ForegroundColor Yellow
    Write-Host "    winget install Python.Python.3.12"
    Write-Host ""
    Write-Host "  Re-run this script after installing."
    exit 1
}
Write-Ok "Python 3.12 found via the 'py' launcher."

# --- 2. venv ------------------------------------------------------------------
Write-Step "Creating Python virtual environment"
if ($Force -and (Test-Path $venv)) {
    Write-Note "Removing existing venv at $venv"
    Remove-Item -Recurse -Force $venv
}
if (-not (Test-Path $venvDir)) {
    New-Item -ItemType Directory -Force -Path $venvDir | Out-Null
}
if (-not (Test-Path $pyexe)) {
    Write-Note "Creating venv at $venv"
    & py -3.12 -m venv $venv
} else {
    Write-Note "venv already exists at $venv (re-using)"
}
Write-Ok "venv: $venv"

# --- 3. pip install -----------------------------------------------------------
Write-Step "Installing pinned dependencies"
$req = Join-Path $here "requirements.txt"
if (-not (Test-Path $req)) {
    Write-Bad "requirements.txt not found at $req"
    Write-Host "  Run this script from inside the SA-Live-Transcribe folder."
    exit 1
}
& $pyexe -m pip install --upgrade pip
& $pyexe -m pip install -r $req
if ($LASTEXITCODE -ne 0) { Write-Bad "pip install failed."; exit 1 }
Write-Ok "Dependencies installed."

# --- 4. GPU detection ---------------------------------------------------------
Write-Step "Detecting hardware"
$hasGpu = $false
if (-not $CpuOnly) {
    try {
        $null = & nvidia-smi -L 2>$null
        if ($LASTEXITCODE -eq 0) { $hasGpu = $true }
    } catch {}
}
if ($hasGpu) {
    Write-Ok "NVIDIA GPU detected -- will pre-download both GPU and CPU models."
} else {
    if ($CpuOnly) {
        Write-Note "Forced CPU-only mode (-CpuOnly)."
    } else {
        Write-Note "No NVIDIA GPU detected -- CPU-only setup."
    }
}

# --- 5. Pre-download models ---------------------------------------------------
Write-Step "Pre-downloading Whisper model(s)"
Write-Host "  (First-time download is ~1.5-4.5 GB. Cached afterwards in"
Write-Host "   $env:USERPROFILE\.cache\huggingface)"
Write-Host ""

if ($hasGpu) {
    Write-Note "Downloading large-v3 (~3 GB) for GPU/fp16..."
    & $pyexe -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cuda', compute_type='float16'); print('  large-v3 ready')"
    if ($LASTEXITCODE -ne 0) { Write-Bad "large-v3 download/load failed."; exit 1 }
}
Write-Note "Downloading large-v3-turbo (~1.5 GB) for CPU/int8..."
& $pyexe -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8'); print('  large-v3-turbo ready')"
if ($LASTEXITCODE -ne 0) { Write-Bad "large-v3-turbo download/load failed."; exit 1 }

# --- 6. Done ------------------------------------------------------------------
Write-Step "Setup complete"
Write-Ok "venv: $venv"
Write-Ok "Models cached in: $env:USERPROFILE\.cache\huggingface"
Write-Host ""
Write-Host "  Next: double-click 'Launch SA-Live-Transcribe.bat' to start the UI." -ForegroundColor Green
Write-Host ""
