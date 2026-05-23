# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SA-Live-Transcribe (CPU-only NATIVE desktop build).
# Build from the project root:  pyinstaller --noconfirm sa-live-transcribe.spec
#
# Produces a one-folder app under dist/SA-Live-Transcribe/. The native pywebview
# window is the shipped shell, so pywebview + pythonnet (clr) are bundled. Local
# AI summaries (llama-cpp-python) are bundled too. The Whisper model is NOT
# bundled (multi-GB); it downloads on first transcription.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
# collect_all pulls each package's code, data, and native libs. webview + clr_loader
# bring the pywebview backend and the pythonnet/.NET runtime for the native window;
# llama_cpp brings the local summary engine; the rest are the ASR + server stack.
for pkg in ("faster_whisper", "ctranslate2", "uvicorn", "pyaudiowpatch",
            "llama_cpp", "webview", "clr_loader"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("live_transcribe")
hiddenimports += ["scipy.signal", "anyio", "h11", "sniffio", "clr"]

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
    # Heavy/irrelevant stacks (retranscribe runs in a separate env). tkinter isn't
    # needed: the native window uses pywebview's own file dialog.
    excludes=["torch", "torchaudio", "torchvision", "whisperx", "pyannote",
              "pyannote.audio", "matplotlib", "tkinter", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
# console=True for this test build: if the native window fails to appear, the
# console shows why. A polished release would set console=False.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="SA-Live-Transcribe", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="SA-Live-Transcribe")
