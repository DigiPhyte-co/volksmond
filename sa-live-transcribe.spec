# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SA-Live-Transcribe (CPU-only desktop build).
# Build from the project root:  pyinstaller --noconfirm sa-live-transcribe.spec
#
# Produces a one-folder app under dist/SA-Live-Transcribe/. The Whisper model is
# NOT bundled (multi-GB), it downloads on first transcription. The native
# pywebview window is intentionally excluded here (browser mode), so pythonnet
# isn't bundled; window-mode packaging is a later step.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("faster_whisper", "ctranslate2", "uvicorn", "pyaudiowpatch"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("live_transcribe")
hiddenimports += ["scipy.signal", "anyio", "h11", "sniffio"]

# The web UI's static asset(s).
datas += [(os.path.join("live_transcribe", "web", "static"),
           os.path.join("live_transcribe", "web", "static"))]

# CPU-only: drop the CUDA GPU libraries ctranslate2 bundles (cuBLAS/cuDNN/cudart/
# cuFFT/nvrtc). Keeps libctranslate2 + the CPU oneDNN libs, roughly 1 GB smaller.
_CUDA = ("cublas", "cublaslt", "cudnn", "cudart", "cufft", "nvrtc", "cuda")
binaries = [b for b in binaries
            if not any(k in os.path.basename(b[0]).lower() for k in _CUDA)]

a = Analysis(
    ["app_main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # Heavy/irrelevant stacks (retranscribe runs in a separate env) and the
    # pywebview/pythonnet chain (browser mode doesn't use it).
    excludes=["torch", "torchaudio", "torchvision", "whisperx", "pyannote",
              "pyannote.audio", "matplotlib", "tkinter", "IPython",
              "webview", "pythonnet", "clr", "clr_loader", "bottle"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="SA-Live-Transcribe", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="SA-Live-Transcribe")
