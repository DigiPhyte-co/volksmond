"""GPU / dependency diagnostics for SA-Live-Transcribe.

Run standalone to find out whether this machine can actually run a GPU tier,
and to pre-cache the CPU downgrade-ladder models so a mid-meeting downgrade
never stalls on a download:

    python -m live_transcribe.gpucheck probe     # is a CUDA device visible to ctranslate2?
    python -m live_transcribe.gpucheck gputest    # can we load + run large-v3 int8_float16 on it?
    python -m live_transcribe.gpucheck cache       # pre-download the CPU ladder models

`probe` deliberately imports only ctranslate2 (not faster_whisper or the engine)
so it still gives a useful answer in a partly-broken env. Exit codes are stable
so setup-laptop-gpu.ps1 can branch on them; they are documented per-command below.
"""
import sys


def probe():
    """Is a CUDA device visible? 0 = yes, 1 = none, 3 = ctranslate2 unusable."""
    try:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
    except Exception as e:
        print(f"PROBE_ERROR {e!r}")
        return 3
    print(f"CUDA_DEVICES {n}")
    return 0 if n > 0 else 1


def gputest():
    """Load + run the gpu-4gb config end-to-end. 0 = ok, 2 = load failed
    (often a missing cuBLAS/cuDNN DLL), 3 = import or inference failed."""
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        from .transcribe import TIER_CONFIG
    except Exception as e:
        print(f"IMPORT_FAIL {e!r}")
        return 3
    cfg = TIER_CONFIG["gpu-4gb"]  # large-v3 / cuda / int8_float16, the 4GB-card tier
    try:
        model = WhisperModel(cfg["model"], device=cfg["device"], compute_type=cfg["compute_type"])
    except Exception as e:
        print(f"LOAD_FAIL {e!r}")
        return 2
    try:
        # One second of silence: forces CUDA kernels to actually run without
        # needing an audio file. We only care that it doesn't raise.
        segs, _info = model.transcribe(np.zeros(16000, dtype=np.float32), language="af", beam_size=1)
        list(segs)
    except Exception as e:
        print(f"INFER_FAIL {e!r}")
        return 3
    print("GPU_OK")
    return 0


def cache():
    """Pre-download every CPU downgrade-ladder model. 0 = all cached, 1 = a download failed."""
    from faster_whisper import WhisperModel
    from .transcribe import CPU_LADDER  # single source of truth for the ladder
    for name in CPU_LADDER:
        try:
            WhisperModel(name, device="cpu", compute_type="int8")
            print(f"CACHED {name}")
        except Exception as e:
            print(f"CACHE_FAIL {name} {e!r}")
            return 1
    return 0


def main(argv):
    cmds = {"probe": probe, "gputest": gputest, "cache": cache}
    if len(argv) != 1 or argv[0] not in cmds:
        print(f"usage: python -m live_transcribe.gpucheck {{{'|'.join(cmds)}}}")
        return 64
    return cmds[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
