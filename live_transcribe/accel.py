"""Platform-neutral accelerator probes: which compute backend transcription should
use on this machine, and whether summaries can offload to a GPU.

Windows keeps its NVIDIA-only story (cudadl). Apple-silicon Macs get Metal: mlx
for ASR (mlx-whisper) and the Metal-built llama.cpp wheel for summaries, where
"VRAM" is the unified system memory. Everything here is a cheap probe: nothing
heavy (mlx, mlx_whisper, ctranslate2) is ever imported by these functions,
matching the cudadl gate discipline pinned by tests/test_paths.py.
"""
import importlib.util
import platform
import sys

from . import cudadl

# Cache the probes like cudadl._PROBE: the answers cannot change during a run.
_PROBE = {}


def mlx_supported():
    """True on an Apple-silicon Mac (the only platform mlx ships wheels for).
    Pure: reads sys.platform / platform.machine at call time, imports nothing."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def mlx_ready():
    """Supported AND the mlx_whisper package is installed. Uses find_spec, never
    an actual import, so the probe costs nothing on machines without it."""
    if not mlx_supported():
        return False
    if "mlx_whisper" not in _PROBE:
        _PROBE["mlx_whisper"] = importlib.util.find_spec("mlx_whisper") is not None
    return _PROBE["mlx_whisper"]


def asr_backend(device_pref="auto"):
    """The compute backend transcription should use: "cuda", "mlx" or "cpu".

    device_pref is the existing device setting (auto/gpu/cpu); "cpu" forces the
    CPU. Otherwise NVIDIA CUDA wins when it is actually usable, then Apple Metal
    via mlx, then the CPU. This never appears as a user-facing setting: it is the
    resolved meaning of "auto"/"gpu" on this machine.
    """
    if (device_pref or "auto").strip().lower() == "cpu":
        return "cpu"
    if cudadl.cuda_ready():
        return "cuda"
    if mlx_ready():
        return "mlx"
    return "cpu"


def summary_gpu_ready():
    """Whether the summariser may offload to a GPU on this machine. Windows: an
    NVIDIA device is present (cudadl). Apple silicon: always True (Metal plus
    unified memory); whether THIS build's llama.cpp can actually offload stays a
    separate check (summarise.gpu_offload_supported), same as on Windows."""
    if mlx_supported():
        return True
    return cudadl.gpu_present()


def summary_vram_mb():
    """Memory budget for summarise.fits_on_gpu, in MB. Windows: VRAM of GPU 0 via
    cudadl. darwin: total system RAM (unified memory; fits_on_gpu's 2 GB headroom
    protects the OS and the app). None when unknown."""
    if sys.platform == "darwin":
        import psutil
        return psutil.virtual_memory().total // 2**20
    return cudadl.vram_mb()
