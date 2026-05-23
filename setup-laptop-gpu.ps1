# setup-laptop-gpu.ps1 - Make a laptop GPU (e.g. Dell G3 / GTX 1650 Mobile, 4GB)
# actually run the GPU tier, and pre-cache the CPU downgrade-ladder models.
#
# Why this exists: in the C12 test the laptop silently fell back to CPU and
# degraded badly. The cause was ctranslate2 not seeing CUDA in the venv. This is
# NOT a CUDA-toolkit installer -- ctranslate2 4.7.2 bundles cuDNN 9 in the wheel,
# so a current NVIDIA driver is the only external requirement. This script:
#   1. confirms an NVIDIA GPU + driver are present (nvidia-smi),
#   2. checks whether ctranslate2 in the venv can SEE the GPU,
#   3. if not, reinstalls the pinned deps (the usual fix: an old ctranslate2
#      without the bundled cuDNN) and re-checks,
#   4. proves the gpu-4gb tier (large-v3 int8_float16) loads + runs on this card,
#   5. pre-caches the CPU ladder (medium/small/base/tiny) so a mid-meeting
#      downgrade never stalls on a download,
#   6. prints a plain verdict: GPU ready, or exactly why not.
#
# Run it from inside the SA-Live-Transcribe folder:  .\setup-laptop-gpu.ps1
# Re-running is safe and idempotent. Requires First-time setup.bat to have run.

[CmdletBinding()]
param(
    [switch]$ReinstallDeps   # force `pip install -r requirements.txt` even if CUDA already works
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$pyexe = Join-Path $env:LOCALAPPDATA "sa-live-transcribe\.venv\Scripts\python.exe"

function Write-Step { param([string]$Text) Write-Host ""; Write-Host "=== $Text ===" -ForegroundColor Cyan }
function Write-Ok   { param([string]$T) Write-Host "  OK  $T" -ForegroundColor Green }
function Write-Note { param([string]$T) Write-Host "  ..  $T" -ForegroundColor Gray }
function Write-Bad  { param([string]$T) Write-Host "  !!  $T" -ForegroundColor Red }

function Invoke-GpuCheck {
    # Runs a gpucheck subcommand; returns its exit code + captured stdout.
    # stderr (download progress, library noise) streams to the console.
    param([string]$Cmd)
    $out = & $pyexe -m live_transcribe.gpucheck $Cmd
    return [pscustomobject]@{ Code = $LASTEXITCODE; Out = ($out -join " ").Trim() }
}

# --- 0. venv must exist -------------------------------------------------------
if (-not (Test-Path $pyexe)) {
    Write-Bad "Python venv not found at: $pyexe"
    Write-Host "  Run 'First-time setup.bat' first (in this same folder), then re-run this script." -ForegroundColor Yellow
    exit 1
}

Push-Location $here
try {
    # --- 1. NVIDIA GPU + driver present? --------------------------------------
    Write-Step "Checking for an NVIDIA GPU"
    $gpuName = $null
    try {
        $smi = & nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
        if ($LASTEXITCODE -eq 0 -and $smi) { $gpuName = ($smi | Select-Object -First 1) }
    } catch {}
    if ($gpuName) {
        Write-Ok "GPU: $gpuName"
    } else {
        Write-Bad "No NVIDIA GPU/driver detected (nvidia-smi unavailable)."
        Write-Note "This machine will run CPU tiers only; caching the CPU ladder so they work offline."
    }

    # --- 2. Can ctranslate2 see the GPU? --------------------------------------
    $cudaOk = $false
    if ($gpuName) {
        Write-Step "Checking CUDA visibility in the venv"
        $p = Invoke-GpuCheck "probe"
        if ($p.Code -eq 0) {
            Write-Ok "ctranslate2 sees the GPU ($($p.Out))."
            $cudaOk = $true
        } else {
            Write-Note "ctranslate2 cannot see the GPU yet ($($p.Out))."
        }

        # --- 3. Repair: refresh pinned deps (old ctranslate2 is the usual cause)
        if (-not $cudaOk -or $ReinstallDeps) {
            Write-Step "Refreshing GPU dependencies"
            Write-Note "Reinstalling pinned deps -- ctranslate2 4.7.2 bundles cuDNN 9..."
            & $pyexe -m pip install --upgrade -r (Join-Path $here "requirements.txt")
            if ($LASTEXITCODE -ne 0) { Write-Bad "pip install reported an error (continuing to re-check)." }
            $p = Invoke-GpuCheck "probe"
            if ($p.Code -eq 0) {
                Write-Ok "GPU now visible ($($p.Out))."
                $cudaOk = $true
            } else {
                Write-Bad "Still no CUDA device ($($p.Out))."
                Write-Note "Most likely an outdated NVIDIA driver -- CUDA 12 needs driver R525+. Update it, then re-run."
            }
        }
    }

    # --- 4. Prove the gpu-4gb tier actually loads + runs ----------------------
    $gpuVerified = $false
    if ($cudaOk) {
        Write-Step "Testing the gpu-4gb tier (large-v3 int8_float16)"
        Write-Note "Downloads large-v3 (~3 GB) if it isn't cached yet; otherwise instant."
        $g = Invoke-GpuCheck "gputest"
        if ($g.Code -eq 0) {
            Write-Ok "gpu-4gb works on this GPU."
            $gpuVerified = $true
        } elseif ($g.Code -eq 2) {
            Write-Note "Model load failed ($($g.Out)). Installing CUDA runtime libs and retrying once..."
            & $pyexe -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
            $g = Invoke-GpuCheck "gputest"
            if ($g.Code -eq 0) {
                Write-Ok "gpu-4gb works after installing the CUDA libs."
                $gpuVerified = $true
            } else {
                Write-Bad "gpu-4gb still failing ($($g.Out))."
            }
        } else {
            Write-Bad "GPU test failed ($($g.Out))."
        }
    }

    # --- 5. Pre-cache the CPU downgrade ladder (safety net, GPU or not) --------
    Write-Step "Pre-caching CPU ladder models (medium, small, base, tiny)"
    Write-Note "~2.2 GB total, one-time. Lets a mid-meeting CPU downgrade swap instantly + offline."
    $c = Invoke-GpuCheck "cache"
    if ($c.Code -eq 0) {
        Write-Ok "CPU ladder cached ($($c.Out))."
    } else {
        Write-Bad "Some ladder models failed to cache ($($c.Out))."
    }

    # --- 6. Verdict -----------------------------------------------------------
    Write-Step "Result"
    if ($gpuVerified) {
        Write-Ok "GPU transcription is READY. Auto-detect will pick the 'gpu-4gb' tier."
        Write-Host "  Next meeting: double-click 'Launch SA-Live-Transcribe.bat' (or run .\start-meeting-ui.ps1)." -ForegroundColor Green
        Write-Host "  It will run on the GPU -- no more silent CPU fallback." -ForegroundColor Green
    } else {
        Write-Bad "GPU transcription is NOT available on this machine."
        Write-Host "  Meetings will run on a CPU tier and adaptively downgrade (medium->small->base->tiny)." -ForegroundColor Yellow
        Write-Host "  The CPU ladder is now cached, so that path is as fast as it can be." -ForegroundColor Yellow
        if ($gpuName) {
            Write-Host "  To enable the GPU: update your NVIDIA driver, then re-run this script." -ForegroundColor Yellow
        }
    }

    # Informational: the stale in-OneDrive venv (if any) wastes cloud storage.
    $odVenv = Join-Path $here ".venv"
    if (Test-Path $odVenv) {
        Write-Host ""
        Write-Note "Heads-up: an unused old '.venv' exists inside the OneDrive folder."
        Write-Note "Reclaim ~1.5 GB cloud storage -- see SETUP.md, 'Cleaning up an old in-OneDrive venv'."
    }
}
finally {
    Pop-Location
}
