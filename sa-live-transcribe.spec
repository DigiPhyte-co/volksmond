# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SA-Live-Transcribe (CPU-only NATIVE desktop build).
# Build from the project root:  pyinstaller --noconfirm sa-live-transcribe.spec
#
# Produces a one-folder app under dist/Volksmond/. The native pywebview
# window is the shipped shell, so pywebview + pythonnet (clr) are bundled. Local
# AI summaries (llama-cpp-python) are bundled too. The Whisper model is NOT
# bundled (multi-GB); it downloads on first transcription.
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Two build profiles from one spec (docs/distribution-and-landing-plan.md section 3). Set
# VOLKSMOND_OFFLINE=1 to build the OFFLINE-ONLY edition: the online-only modules (the app update
# check, the Outlook calendar and its Graph sibling, the cloud auth lib) are compiled OUT, and a
# runtime hook hard-forces SA_LIVE_OFFLINE=1 so the frozen app provably cannot phone home. Unset
# (the default) builds the CONNECTED edition, which keeps them.
OFFLINE = os.environ.get("VOLKSMOND_OFFLINE") == "1"
APP_NAME = "Volksmond-Offline" if OFFLINE else "Volksmond"
# Excluded from the offline bundle so these paths are not merely disabled but physically absent: a
# source-available verifier can confirm the update-manifest fetch (updatecheck), the calendar
# (outlook/outlook_local), and the Graph/cloud auth lib (msal) are gone.
ONLINE_ONLY_MODULES = ["live_transcribe.outlook", "live_transcribe.outlook_local",
                       "live_transcribe.updatecheck", "msal"]

datas, binaries, hiddenimports = [], [], []
# collect_all pulls each package's code, data, and native libs. webview + clr_loader
# bring the pywebview backend and the pythonnet/.NET runtime for the native window;
# llama_cpp brings the local summary engine; livekit brings the WebRTC APM (echo
# cancellation) native FFI lib; the rest are the ASR + server stack.
for pkg in ("faster_whisper", "ctranslate2", "uvicorn", "pyaudiowpatch",
            "llama_cpp", "webview", "clr_loader", "livekit"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# livekit's APM import chain pulls protobuf (incl. the google._upb C extension) and aiofiles.
# Our `import livekit` is lazy (inside aec.cancel_echo), so PyInstaller's static analysis never
# traces these; collect them explicitly or the echo canceller fails to import in the frozen app.
for pkg in ("google.protobuf", "aiofiles", "soxr"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("live_transcribe")
hiddenimports += ["scipy.signal", "anyio", "h11", "sniffio", "clr",
                  "livekit.rtc.apm", "google._upb._message"]
# pywin32, imported lazily by live_transcribe/outlook_local.py for the local Outlook calendar
# read. PyInstaller's built-in pywin32 hooks bundle the pythoncom/pywintypes DLLs; these names
# just ensure the modules are pulled in despite the lazy import. The offline edition drops the
# calendar feature, so it needs no pywin32 at all.
if not OFFLINE:
    hiddenimports += ["win32com", "win32com.client", "win32timezone", "pythoncom", "pywintypes"]

# collect_submodules("live_transcribe") above pulls in EVERY submodule, including the online-only
# ones. For the offline edition, drop them from the hidden imports so the excludes below have
# nothing to fight, and the modules are genuinely absent from the bundle.
if OFFLINE:
    hiddenimports = [h for h in hiddenimports if h not in ONLINE_ONLY_MODULES]

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
    # needed: the native window uses pywebview's own file dialog. The offline edition also
    # excludes the online-only modules, so its update-check and calendar code is not in the bundle.
    excludes=["torch", "torchaudio", "torchvision", "whisperx", "pyannote",
              "pyannote.audio", "matplotlib", "tkinter", "IPython"]
             + (ONLINE_ONLY_MODULES if OFFLINE else []),
    # The offline edition installs a runtime hook that hard-forces SA_LIVE_OFFLINE=1 before any app
    # code runs, so the frozen app takes the offline path (no update check, no calendar, no cloud).
    runtime_hooks=(["pyi_rth_offline.py"] if OFFLINE else []),
    noarchive=False,
)
pyz = PYZ(a.pure)
# console=False: windowed app, no terminal on launch (the shipped experience).
# A windowed build has no console, so sys.stdout/stderr are None; app_main.py
# redirects them to a per-launch log file (volksmond.log in the data dir) so
# print() and tracebacks do not crash and testers still get a log to send. Flip
# to console=True only to debug a build whose window will not appear.
# icon: rounded-tile rendering of the brand mark (see build-icon.py); regenerate
# by running `python build-icon.py` if the brand mark changes.
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name=APP_NAME, console=False, icon="volksmond.ico")
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)
