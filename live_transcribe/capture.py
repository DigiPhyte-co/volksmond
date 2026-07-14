"""Platform selector for the audio capture backend.

The shared capture logic (silence-aware chunking, 16 kHz emit, levels, live
AEC) lives in `capture_core.CaptureBase`; each platform contributes only the
device lifecycle. Callers keep importing `capture.AudioCapture` unchanged.
"""
import sys

if sys.platform == "win32":
    from .capture_win import AudioCapture
elif sys.platform == "darwin":
    try:
        from .capture_mac import AudioCapture
    except ImportError as e:
        raise ImportError(
            "macOS audio capture backend (live_transcribe.capture_mac) is not "
            "available in this build yet; see docs/mac-port-plan.md."
        ) from e
else:
    raise ImportError(
        f"no audio capture backend for platform {sys.platform!r}: "
        "live_transcribe.capture_win covers Windows and "
        "live_transcribe.capture_mac (planned) covers macOS."
    )

__all__ = ["AudioCapture"]
