# CUDA-enabled standalone build, plan

Status: PLANNED, not built. Gated behind full end-to-end testing of the CPU build.
The CPU-only build (`sa-live-transcribe.spec` -> `SA-Live-Transcribe.zip`) stays the
primary public download. This adds a second, optional "for NVIDIA GPUs" build for
the author and GPU power users.

## Why a separate build, not one artifact
The CPU build deliberately strips the CUDA libraries (roughly 1 GB). A GPU build
keeps them so the `gpu` tier (large-v3, float16) can run. They cannot be one
artifact: shipping CUDA to everyone bloats the download for the majority who have
no NVIDIA GPU.

## Prerequisite bug, fix it in the SAME change (it affects both builds)
`pick_tier()` auto-detects CUDA via `ctranslate2.get_cuda_device_count()`, which
reads the NVIDIA *driver*, not the bundled libs. So the CPU-only frozen build, on
any NVIDIA PC, currently picks the `gpu` tier and then fails to load the model
(libs were stripped) -> 500 on first transcribe. Fix by gating GPU tiers on a
build flag:

- The GPU spec adds a PyInstaller runtime hook that sets
  `os.environ["SA_LIVE_GPU_BUILD"] = "1"` before app code runs.
- `pick_tier()`:
  `gpu_allowed = (not getattr(sys, "frozen", False)) or os.environ.get("SA_LIVE_GPU_BUILD") == "1"`.
  Only probe for and return GPU tiers when `gpu_allowed`.
- Result: dev (run from source) = GPU as today; CPU build = never GPU (bug fixed);
  GPU build = GPU.

## Spec, parametrise rather than duplicate
`sa-live-transcribe.spec` reads `os.environ.get("SA_LIVE_GPU_BUILD")`:

- GPU build: SKIP the `_CUDA` strip filter (keep `cudnn64_9.dll`; `cudart`/`cuBLAS`
  are already statically linked into `ctranslate2.dll`). Add the runtime hook.
  Name the output `SA-Live-Transcribe-GPU`.
- Default (env unset): unchanged CPU-only build.
- `console=True` for the first GPU test build (debug), flip to `False` for release.

## Build script
`build-app.ps1` gains a `-Gpu` switch: sets `SA_LIVE_GPU_BUILD=1`, builds the GPU
variant outside OneDrive (same `%LOCALAPPDATA%` workpath/distpath as today to
avoid OneDrive file locks), zips to `SA-Live-Transcribe-GPU.zip`.

## What ships in the GPU bundle
`ctranslate2.dll` (static cudart + cuBLAS) + `cudnn64_9.dll` (the only separate
CUDA DLL ctranslate2 4.7.2 needs; proven sufficient in-venv, where the tiny model
transcribed on cuda/float16). PyInstaller's binary-dependency analysis pulls any
transitive DLLs. No CUDA toolkit needed on the target, only a current NVIDIA
driver (CUDA 12-capable).

## Size and UX notes
- GPU zip will be much larger than the 141 MB CPU zip (cuDNN is hundreds of MB);
  expect roughly 600 MB to 1 GB. Label the download clearly "for NVIDIA GPUs".
- First run on the `gpu` tier downloads large-v3 (about 3 GB), vs the CPU build's
  smaller starting model.

## Acceptance test (the real gate)
On a machine with an NVIDIA GPU + driver but NO Python and NO CUDA toolkit:
1. Unzip, run the exe.
2. Settings / `/api/status` shows the tier resolves to `gpu` (not `cpu-*`).
3. Start a short meeting -> large-v3 loads on GPU, transcription runs, no
   missing-DLL error.
4. Stop -> transcript saved to `%LOCALAPPDATA%\sa-live-transcribe\sessions`.

## Open decisions
- Public second download vs author-only? Recommend: secondary, clearly labelled.
- Minimum NVIDIA driver version to state on the download page.
