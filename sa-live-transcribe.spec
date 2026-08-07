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

# Three build profiles from one spec (docs/distribution-and-landing-plan.md section 3). Set
# VOLKSMOND_OFFLINE=1 to build the OFFLINE-ONLY edition: the online-only modules (the app update
# check, the Outlook calendar and its Graph sibling, the cloud auth lib) are compiled OUT, and a
# runtime hook hard-forces SA_LIVE_OFFLINE=1 so the frozen app provably cannot phone home. Set
# VOLKSMOND_STORE=1 to build the STORE (Microsoft Store MSIX) edition: the connected build minus
# the in-app update check (the Store owns updates), so ONLY updatecheck is compiled out and a hook
# forces SA_LIVE_STORE=1. Unset (the default) builds the CONNECTED edition, which keeps everything.
OFFLINE = os.environ.get("VOLKSMOND_OFFLINE") == "1"
STORE = os.environ.get("VOLKSMOND_STORE") == "1"
if OFFLINE and STORE:
    raise SystemExit("VOLKSMOND_OFFLINE and VOLKSMOND_STORE are mutually exclusive; set one.")
# The store edition keeps the display name "Volksmond" (it IS the normal app, packaged for the
# Store); build-app.ps1 keeps its output apart by building it into a separate dist root
# (dist-store) and naming its zip volksmond-store_<ver>.zip, so the folder name here can stay
# what the exe, the shortcuts and the MSIX manifest all expect.
APP_NAME = "Volksmond-Offline" if OFFLINE else "Volksmond"
# Excluded from the offline bundle so these paths are not merely disabled but physically absent: a
# source-available verifier can confirm the update-manifest fetch (updatecheck), the calendar
# (outlook/outlook_local), and the Graph/cloud auth lib (msal) are gone.
ONLINE_ONLY_MODULES = ["live_transcribe.outlook", "live_transcribe.outlook_local",
                       "live_transcribe.updatecheck", "msal"]
# The store edition strips ONLY the update-manifest fetch: the calendar and its COM/auth support
# stay in, exactly as in the connected build. One list per edition, resolved once, so every
# exclusion site below treats the editions uniformly.
STORE_ONLY_EXCLUDES = ["live_transcribe.updatecheck"]
EDITION_EXCLUDES = ONLINE_ONLY_MODULES if OFFLINE else (STORE_ONLY_EXCLUDES if STORE else [])

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
# pywin32, imported lazily in two places. PyInstaller's built-in pywin32 hooks bundle the
# supporting DLLs; these names just ensure the modules are pulled in despite the lazy imports.
#
# The GUI half (win32gui/win32api/win32con/pywintypes) is needed by live_transcribe/notify.py
# for Shell_NotifyIcon desktop notifications, and is bundled in BOTH editions. That is a
# deliberate call about the offline edition's trust claim: the claim is "no network paths",
# verifiable by the absence of the online modules, and Shell_NotifyIcon is a purely local
# shell call that talks to explorer.exe on this machine. The COM half stays out (below), so
# nothing in the offline bundle can reach Outlook, Graph or any other remote-capable surface.
# If that trade is ever refused, notify.py's shell calls can be reimplemented in ctypes with
# no pywin32 at all (roughly 120 lines) and this line reverted.
hiddenimports += ["win32gui", "win32api", "win32con", "pywintypes"]
# The COM half, for live_transcribe/outlook_local.py's local Outlook calendar read. The
# offline edition drops the calendar feature entirely, so it needs none of this.
if not OFFLINE:
    hiddenimports += ["win32com", "win32com.client", "win32timezone", "pythoncom"]

# collect_submodules("live_transcribe") above pulls in EVERY submodule, including the online-only
# ones. For the offline and store editions, drop that edition's excluded modules from the hidden
# imports so the excludes below have nothing to fight, and the modules are genuinely absent from
# the bundle.
if EDITION_EXCLUDES:
    hiddenimports = [h for h in hiddenimports if h not in EDITION_EXCLUDES]

# The web UI's static asset(s).
datas += [(os.path.join("live_transcribe", "web", "static"),
           os.path.join("live_transcribe", "web", "static"))]
# The app icon as a DATA file as well as the EXE icon: live_transcribe/notify.py loads it at
# runtime (LoadImage from disk) for the notification tray icon, and an icon compiled into the
# EXE's resources is not reachable that way. Without this the notifications wear the generic
# Windows application icon instead of the Volksmond mark.
datas += [("volksmond.ico", ".")]

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
    # excludes the online-only modules, so its update-check and calendar code is not in the
    # bundle; the store edition excludes only updatecheck (the Store owns updates).
    excludes=["torch", "torchaudio", "torchvision", "whisperx", "pyannote",
              "pyannote.audio", "matplotlib", "tkinter", "IPython"]
             + EDITION_EXCLUDES,
    # The offline edition installs a runtime hook that hard-forces SA_LIVE_OFFLINE=1 before any app
    # code runs, so the frozen app takes the offline path (no update check, no calendar, no cloud).
    # The store edition's hook hard-forces SA_LIVE_STORE=1 the same way (no in-app update check).
    runtime_hooks=(["pyi_rth_offline.py"] if OFFLINE
                   else (["pyi_rth_store.py"] if STORE else [])),
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
