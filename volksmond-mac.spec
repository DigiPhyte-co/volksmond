# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Volksmond on macOS (Apple Silicon, arm64, CPU int8 faster-whisper).
# Build from the project root on a Mac:  pyinstaller --noconfirm volksmond-mac.spec
# (or via the local mirror  mac/build-app-mac.sh, which also compiles the Swift helper
# and sets the env vars this spec reads).
#
# Produces a one-folder .app under dist/Volksmond.app: a signed, notarisable macOS
# bundle wrapping the same browser UI in a pywebview (WKWebView) window as the Windows
# build. See docs/mac-port-plan.md section 2.6 for the packaging design. This spec is
# SEPARATE from sa-live-transcribe.spec (Windows) on purpose: the collect list and the
# platform excludes differ too much to parametrise one spec cleanly. The Windows spec is
# frozen; nothing here touches it.
#
# NOT bundled: the Whisper/Fluister model (multi-GB, downloads on first transcription).
# CUDA is Windows-only here; the CUDA-binary strip below is a harmless no-op on arm64.
#
# Build-time env vars this spec reads (set by mac/build-app-mac.sh or CI, WP-F):
#   VOLKSMOND_AUDIOTAP_BIN  absolute path to the compiled Swift SYS-capture helper
#                           (WP-B, `mac/volksmond-audiotap`, `swift build -c release`).
#                           Bundled to Contents/Resources/bin/volksmond-audiotap.
#   CODESIGN_IDENTITY       Developer ID Application identity for inside-out signing.
#                           Unset -> PyInstaller does no signing (ad-hoc / local dev);
#                           WP-F sets it to the real cert for the notarised release.
import os
import re
from PyInstaller.utils.hooks import collect_all, collect_submodules

# --- App version: read from licensing.py (same source the Windows lanes use) ----------
# Parse the file rather than import the package: importing live_transcribe pulls the heavy
# ASR/server stack, and this spec only needs the one string. Matches build-app.ps1's regex.
_lic = os.path.join("live_transcribe", "licensing.py")
with open(_lic, encoding="utf-8") as _fh:
    _m = re.search(r'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', _fh.read())
if not _m:
    raise SystemExit("volksmond-mac.spec: could not read APP_VERSION from licensing.py")
APP_VERSION = _m.group(1)

APP_NAME = "Volksmond"  # dist/Volksmond.app; the DMG is Volksmond-<ver>.dmg (release.ps1 -MacDmg, WP-G)

datas, binaries, hiddenimports = [], [], []

# collect_all pulls each package's code, data and native libs. The mac stack mirrors the
# Windows one MINUS the Windows-only backends (pyaudiowpatch WASAPI capture, clr_loader/
# pythonnet for the .NET pywebview backend) and PLUS sounddevice (Core Audio MIC capture,
# bundles its own PortAudio dylib). faster_whisper/ctranslate2 = the CPU int8 ASR engine;
# uvicorn = the local server; llama_cpp = the Metal-enabled summary engine; webview =
# pywebview (Cocoa/WebKit backend on mac); livekit = the WebRTC APM echo canceller;
# mlx + mlx_whisper = the Metal ASR backend (WP-M6; the requirements.txt marker makes
# them darwin-arm64 only). collect_all MUST capture mlx's Metal shader library
# (mlx/lib/*.metallib; MLX fails to initialise without it) and mlx_whisper's tokenizer
# asset files; mac-release.yml asserts both in the built bundle. If the metallib assert
# ever fails on the runner, add an explicit datas entry for mlx/lib/*.metallib here.
for pkg in ("faster_whisper", "ctranslate2", "uvicorn", "sounddevice",
            "llama_cpp", "webview", "livekit", "mlx", "mlx_whisper"):
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

# pywebview's macOS backend loads pyobjc lazily (Cocoa window + WKWebView), so PyInstaller's
# static analysis never sees these; collect them explicitly. Wrapped because the exact set of
# pyobjc framework packages pywebview needs is CI-tunable: a missing optional one should warn,
# not abort, while the critical collect_all calls above stay unwrapped and fail loud.
# TODO(ci-verify): confirm this pyobjc set is sufficient for the WKWebView window to launch;
# add WebKit/Cocoa submodules here if the notarised app opens blank or crashes on start.
for pkg in ("objc", "Foundation", "AppKit", "WebKit", "Quartz"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as _e:  # pragma: no cover - build-machine diagnostic only
        print("volksmond-mac.spec: WARNING skipping pyobjc package %r (%s)" % (pkg, _e))

hiddenimports += collect_submodules("live_transcribe")
hiddenimports += ["scipy.signal", "anyio", "h11", "sniffio",
                  "livekit.rtc.apm", "google._upb._message",
                  # sounddevice is a CFFI binding; its backend + cffi runtime are lazy imports.
                  "_sounddevice", "cffi", "_cffi_backend"]

# The web UI's static asset(s).
datas += [(os.path.join("live_transcribe", "web", "static"),
           os.path.join("live_transcribe", "web", "static"))]

# --- Swift SYS-capture helper (WP-B), bundled at Contents/Resources/bin/volksmond-audiotap ---
# Contract: docs/mac-port-plan.md section 2.2. The binary is compiled by mac/build-app-mac.sh
# (or CI, WP-F) and its path handed in via VOLKSMOND_AUDIOTAP_BIN. capture_mac.py (WP-C) locates
# it at this exact bundle path, so the dest MUST stay Contents/Resources/bin/volksmond-audiotap.
# A DATA-type entry lands it under Contents/Resources/ (a `binaries` entry would be relocated to
# Contents/Frameworks/ and break that runtime path); the tradeoff is that PyInstaller's binary
# signing pass does NOT sign a data file. mac/build-app-mac.sh signs the helper IN PLACE BEFORE this
# bundling step (signing the nested copy afterwards would invalidate the sealed outer signature), so
# this spec copies an already-signed binary; WP-F supplies the Developer ID + hardened runtime.
# TODO(ci-verify): confirm the helper is present, executable AND signed with hardened runtime in
# the notarised .app (spctl/codesign --verify over Contents/Resources/bin/volksmond-audiotap).
_helper = os.environ.get("VOLKSMOND_AUDIOTAP_BIN")
if _helper and os.path.isfile(_helper):
    datas += [(_helper, "bin")]  # -> Contents/Resources/bin/volksmond-audiotap
    print("volksmond-mac.spec: bundling audiotap helper from %s" % _helper)
else:
    # Not fatal: a Python-only iteration build still launches and does MIC capture; only SYS
    # (system-audio) capture needs the helper. WP-C's backend degrades to MIC-only if absent.
    print("volksmond-mac.spec: WARNING VOLKSMOND_AUDIOTAP_BIN unset or missing; "
          "the SYS-capture helper will NOT be bundled (MIC-only build).")

# CPU-only: drop the CUDA GPU libraries ctranslate2 may bundle. On arm64 nothing matches, so this
# is a harmless no-op kept for parity with the Windows spec (docs/mac-port-plan.md section 2.6).
_CUDA = ("cublas", "cublaslt", "cudnn", "cudart", "cufft", "nvrtc", "cuda")
binaries = [b for b in binaries
            if not any(k in os.path.basename(b[0]).lower() for k in _CUDA)]

a = Analysis(
    ["app_main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # Heavy/irrelevant stacks (retranscribe runs in a separate env); tkinter isn't needed (the
    # native window uses pywebview's own file dialog). torch stays excluded even though
    # mlx-whisper declares it as a dependency: mlx_whisper imports torch only in its checkpoint
    # convert path, never at transcribe runtime, and bundling torch would balloon the app.
    # PLUS the Windows-only backends, which must
    # be physically absent from the mac bundle: pyaudiowpatch (WASAPI), pythonnet/clr_loader (the
    # .NET pywebview backend; mac uses pyobjc), and pywin32 (the Outlook COM calendar, which
    # outlook_local.py imports lazily and already feature-gates off on non-Windows).
    excludes=["torch", "torchaudio", "torchvision", "whisperx", "pyannote",
              "pyannote.audio", "matplotlib", "tkinter", "IPython",
              "pyaudiowpatch", "clr", "clr_loader", "pythonnet",
              "win32com", "win32com.client", "win32timezone", "pythoncom",
              "pywintypes", "win32api", "win32gui", "win32con"],
    runtime_hooks=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

# console=False: windowed app, no terminal on launch. A macOS .app has no console either, so
# app_main.py redirects sys.stdout/stderr to <data_dir>/volksmond.log exactly as on Windows.
# target_arch='arm64': Apple Silicon only for v1 (universal2 is impossible; several wheels are
# thin arm64 - docs/mac-port-plan.md section 1).
# codesign_identity + entitlements_file: PyInstaller 6.x signs inside-out with the Developer ID
# and applies the hardened-runtime entitlements (mac/entitlements.plist). Identity comes from the
# env so a local/pre-F build (no cert) produces an ad-hoc app that still launches; WP-F sets the
# real cert. TODO(mac-hw): an .icns app icon is not yet generated; the Windows .ico does not apply
# to a .app, so the bundle ships with the default icon until build-icon.py grows an .icns target.
_icns = "volksmond.icns" if os.path.exists("volksmond.icns") else None
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name=APP_NAME, console=False,
    target_arch="arm64",
    codesign_identity=os.environ.get("CODESIGN_IDENTITY"),
    entitlements_file=os.path.join("mac", "entitlements.plist"),
    icon=_icns,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

# The .app bundle. bundle_identifier + version + the Info.plist keys per plan section 2.6:
# the two TCC usage strings (mic + system-audio capture), the 14.4 floor, and the version from
# licensing.py. NSHighResolutionCapable keeps the WebKit UI crisp on Retina displays.
app = BUNDLE(
    coll,
    name=APP_NAME + ".app",
    icon=_icns,
    bundle_identifier="com.digiphyte.volksmond",
    version=APP_VERSION,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": APP_VERSION,
        "CFBundleVersion": APP_VERSION,
        "LSMinimumSystemVersion": "14.4",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        # TCC prompts. The user sees these exact strings; keep them POPIA-honest and specific.
        "NSMicrophoneUsageDescription":
            "Volksmond transcribes your microphone locally on this Mac. Audio never leaves the device.",
        "NSAudioCaptureUsageDescription":
            "Volksmond captures the meeting audio this Mac is playing, to transcribe it locally. "
            "Audio never leaves the device.",
    },
)
