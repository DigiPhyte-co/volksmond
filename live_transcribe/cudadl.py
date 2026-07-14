r"""Optional one-click download of the NVIDIA CUDA libraries, so the SAME CPU app can
use an NVIDIA GPU for transcription instead of shipping a separate GPU build.

NVIDIA ONLY. The transcription engine is ctranslate2, whose GPU backend is CUDA;
there is no AMD (ROCm) or Intel-GPU path in its shipped builds. AMD / Intel / no-GPU
machines simply use the CPU path (large-v3 on CPU is still available for uploads).

How it works. The shipped build is CPU-only (sa-live-transcribe.spec strips the CUDA
libs to keep it ~1 GB smaller). ctranslate2 4.7.x needs CUDA 12 + cuDNN 9. This module
fetches the matching NVIDIA pip wheels from PyPI (the same ones faster-whisper's docs
tell you to install for GPU), verifies each against PyPI's SHA-256, and extracts just
their Windows DLLs into <data>/cuda. transcribe.py adds that folder to the DLL search
path at startup, so ctranslate2 finds them and can run on the GPU. A restart is needed
after the download, because the search path is set once at launch.

Nothing about the user is sent; only public NVIDIA libraries are downloaded.
"""
import ctypes
import hashlib
import os
import re
import shutil
import sys
import threading
import zipfile
from pathlib import Path

from . import paths

# CUDA support here is Windows-only: the wheels fetched below are win_amd64, the DLL
# registration is a Windows API (os.add_dll_directory / ctypes.WinDLL), and macOS has
# no CUDA at all. On any other platform every probe reports False/None without touching
# ctranslate2 or shelling nvidia-smi, register_dll_dir()/self_test() no-op cleanly, and
# the download entry point refuses, so callers land on the CPU path with no special-casing.
# /api/cuda exposes this as "supported" so the UI can hide the GPU card entirely.
SUPPORTED = sys.platform == "win32"

# ctranslate2 4.7.x => CUDA 12 + cuDNN 9. Fetch the matching NVIDIA wheels (Windows),
# constrained to the right major version; the exact patch is resolved from PyPI.
_WHEELS = [
    {"pkg": "nvidia-cublas-cu12", "major": "12"},
    {"pkg": "nvidia-cuda-runtime-cu12", "major": "12"},
    {"pkg": "nvidia-cudnn-cu12", "major": "9"},
]
# DLLs that must be present for ctranslate2 to use the GPU; the sentinel for "installed".
_REQUIRED = ("cublas64_12.dll", "cudart64_12.dll", "cudnn64_9.dll")
# A rough display estimate before the real total is known (resolved at download time).
APPROX_BYTES = 1_500_000_000

# Set once at startup by register_dll_dir(): True only if the CUDA folder existed when
# the process launched and was put on the DLL search path. A download performed THIS
# session does not flip it (the search path is fixed at launch), so a restart is needed
# before the GPU can actually be used.
_REGISTERED = False
_DLL_DIR_HANDLE = None  # retain the os.add_dll_directory() handle for the process life
_LOAD_OK = False        # cached self_test result: True once the CUDA DLLs have actually loaded


def cuda_dir(create=False):
    d = paths.data_dir() / "cuda"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def installed():
    """True once the required CUDA DLLs are on disk in <data>/cuda."""
    if not SUPPORTED:
        return False
    d = cuda_dir()
    return all((d / name).is_file() for name in _REQUIRED)


# Cache the hardware probes: they cannot change during a run, and the UI polls
# /api/cuda once a second while a download runs (nvidia-smi is a subprocess).
_PROBE = {}


def gpu_present():
    """True if an NVIDIA CUDA device is visible. Uses ctranslate2's driver-level query,
    which works even on the CPU-only build (it does not need the runtime libs)."""
    if not SUPPORTED:
        return False
    if "gpu" not in _PROBE:
        try:
            import ctranslate2
            _PROBE["gpu"] = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            _PROBE["gpu"] = False
    return _PROBE["gpu"]


def cuda_ready():
    """Whether the GPU should actually be used for transcription. In the shipped (frozen)
    build that means the CUDA libs are downloaded AND verified loadable; from source we
    trust the developer's system CUDA toolkit."""
    if not SUPPORTED:
        return False
    if not gpu_present():
        return False
    if getattr(sys, "frozen", False):
        if not installed():
            return False
        # Report ready ONLY after the DLLs actually load, not merely because the files exist.
        # self_test() registers the folder on the DLL search path and load-tests the libs;
        # ctranslate2 loads CUDA lazily, so doing this on demand needs no restart and no
        # manual "Check GPU". Success is cached; a failure is retried on the next call (cheap
        # once the OS has the DLLs), so a transient AV lock right after a download recovers on
        # its own. A broken/incompatible install stays not-ready, so we fall back to CPU
        # instead of selecting a GPU tier that then fails to load.
        global _LOAD_OK
        if not _LOAD_OK:
            ok, _err = self_test()
            _LOAD_OK = bool(ok)
        return _LOAD_OK
    return True


def vram_mb():
    """Total VRAM of GPU 0 in MB via nvidia-smi, or None. Used to pick the 4 GB tier.
    Cached (the value never changes during a run and the UI polls it)."""
    if not SUPPORTED:
        return None
    if "vram" not in _PROBE:
        _PROBE["vram"] = _probe_vram()
    return _PROBE["vram"]


def _probe_vram():
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=(subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
        if out.returncode == 0:
            return int(out.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


def gpu_name():
    """Marketing name of GPU 0 (e.g. 'NVIDIA GeForce GTX 1650'), or None. Cached; used
    only for a friendly label in the UI."""
    if not SUPPORTED:
        return None
    if "name" not in _PROBE:
        _PROBE["name"] = _probe_name()
    return _PROBE["name"]


def _probe_name():
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
            creationflags=(subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
        if out.returncode == 0:
            lines = out.stdout.strip().splitlines()
            return (lines[0].strip() if lines else None) or None
    except Exception:
        pass
    return None


def register_dll_dir():
    """Add <data>/cuda to the DLL search path so ctranslate2 finds the downloaded CUDA
    libraries. No-op if the folder does not exist. Called from transcribe.py BEFORE
    faster_whisper / ctranslate2 are imported, so it must stay import-light. Idempotent:
    safe to call again after a download (it will not double-register or grow PATH)."""
    global _REGISTERED, _DLL_DIR_HANDLE
    if not SUPPORTED:
        return
    d = cuda_dir()
    if not d.is_dir():
        return
    ds = str(d)
    if _DLL_DIR_HANDLE is None:
        try:
            # Retain the handle for the process lifetime: if it is garbage-collected, the
            # directory is dropped from the search path again.
            _DLL_DIR_HANDLE = os.add_dll_directory(ds)
        except (OSError, AttributeError):
            pass
    if ds not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = ds + os.pathsep + os.environ.get("PATH", "")
    _REGISTERED = True


def self_test():
    """Try to load the required CUDA DLLs by bare name (after putting <data>/cuda on the
    search path). Returns (ok, error). A cheap, model-free proxy for "will the GPU
    libraries load this run": if ctypes can find and load them, so can ctranslate2."""
    if not SUPPORTED:
        return False, "GPU acceleration is only available on Windows."
    if not installed():
        return False, "The CUDA libraries are not downloaded yet."
    register_dll_dir()
    try:
        for name in _REQUIRED:
            ctypes.WinDLL(name)
        return True, None
    except OSError as e:
        return False, str(e)


def activate_after_download():
    """Make a just-downloaded install usable WITHOUT a restart when possible.

    ctranslate2 loads the CUDA libraries lazily on first GPU use, so adding the folder to
    the search path now is enough IF they actually load. We verify with self_test() and
    cache the result, so the next meeting uses the GPU with no restart. Returns True if
    ready now."""
    global _LOAD_OK
    ok, _err = self_test()       # self_test() calls register_dll_dir()
    _LOAD_OK = bool(ok)
    return bool(ok and installed())


_LOCK = threading.Lock()
_STATE = {"state": "idle", "downloaded": 0, "total": 0, "error": None}


def _set(**kw):
    with _LOCK:
        _STATE.update(kw)


def progress():
    with _LOCK:
        return dict(_STATE)


def start_download():
    """Begin a background download + extract of the CUDA libraries. Raises RuntimeError
    if one is already running, or on platforms without CUDA support."""
    if not SUPPORTED:
        raise RuntimeError("GPU acceleration is only available on Windows.")
    with _LOCK:
        if _STATE["state"] == "downloading":
            raise RuntimeError("CUDA libraries are already downloading.")
        _STATE.update({"state": "downloading", "downloaded": 0, "total": APPROX_BYTES, "error": None})
    threading.Thread(target=_run, daemon=True).start()


def remove():
    """Delete the downloaded CUDA libraries to free space. Refused while downloading."""
    with _LOCK:
        if _STATE["state"] == "downloading":
            raise RuntimeError("CUDA libraries are downloading.")
    d = cuda_dir()
    if d.exists():
        try:
            shutil.rmtree(d)
        except OSError as e:
            raise RuntimeError(f"Could not remove the CUDA libraries: {e}")


def _resolve(pkg, major):
    """Find the latest PyPI win_amd64 wheel for `pkg` within the given major version."""
    import requests
    j = requests.get("https://pypi.org/pypi/%s/json" % pkg, timeout=(15, 60)).json()
    best = None
    for ver, files in (j.get("releases") or {}).items():
        if not re.match(r"^%s\.[0-9.]+$" % re.escape(major), ver):  # right major, numeric only (no rc/beta)
            continue
        for f in files:
            if not str(f.get("filename", "")).endswith("win_amd64.whl") or f.get("yanked"):
                continue
            key = tuple(int(x) for x in ver.split("."))
            if best is None or key > best[0]:
                best = (key, f["url"], int(f.get("size") or 0), (f.get("digests") or {}).get("sha256"), f["filename"])
    if not best or not best[3]:
        raise RuntimeError("no verified %s %s.x win_amd64 wheel found on PyPI" % (pkg, major))
    return {"url": best[1], "size": best[2], "sha256": best[3], "filename": best[4]}


def _run():
    import requests
    try:
        d = cuda_dir(create=True)
        resolved = [_resolve(w["pkg"], w["major"]) for w in _WHEELS]
        total = sum(r["size"] for r in resolved) or APPROX_BYTES
        _set(total=total, downloaded=0)
        done = 0
        for r in resolved:
            # The wheel filename comes from PyPI metadata (remote input); keep it a bare
            # basename so it can never write the .part file outside <data>/cuda.
            fname = Path(r["filename"]).name
            if not fname or fname != r["filename"] or ":" in fname:
                raise RuntimeError("unexpected wheel filename from PyPI: %r" % (r["filename"],))
            part = d / (fname + ".part")
            digest = hashlib.sha256()
            with requests.get(r["url"], stream=True, timeout=(15, 120)) as resp:
                resp.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        _set(downloaded=done)
            if digest.hexdigest() != r["sha256"]:
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
                raise RuntimeError("checksum mismatch for %s" % r["filename"])
            # Extract just the DLLs, flattened into <data>/cuda. Path(m).name strips any
            # directory in the zip entry, so a crafted path cannot escape the folder.
            with zipfile.ZipFile(part) as z:
                for m in z.namelist():
                    if not m.lower().endswith(".dll"):
                        continue
                    name = Path(m).name   # strip any directory components
                    # Reject odd basenames (alternate data streams, stray separators)
                    # before writing, even though they would stay inside the folder.
                    if not name or ":" in name or "/" in name or "\\" in name:
                        continue
                    (d / name).write_bytes(z.read(m))
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
        if not installed():
            raise RuntimeError("downloaded libraries are missing required DLLs")
        try:
            activate_after_download()  # usable without a restart when the libs load now
        except Exception:
            pass
        _set(state="done", downloaded=total)
    except Exception as e:
        _set(state="error", error=str(e))
