# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Volksmond on Linux (x86_64, Debian family, CPU int8 faster-whisper).
# Build INSIDE the ubuntu:22.04 build container (glibc 2.35 = the support floor:
# Ubuntu 22.04 / Mint 21 / Debian 12 and newer). Never build this on a newer base
# image: glibc is forward-compatible only, so a bookworm/glibc-2.36 build would
# silently exclude Mint 21. Driven by linux/build-app-linux.sh via build-linux.ps1:
#   pyinstaller --noconfirm volksmond-linux.spec
#
# Produces a one-folder app under dist/volksmond/ (onedir: onefile's unpack-to-tmp
# cost is hostile to a 1 GB+ ML payload). linux/build-app-linux.sh stages it into
# Volksmond-<ver>.deb (installs to /opt/volksmond, /usr/bin/volksmond launcher,
# .desktop entry) plus a Volksmond-<ver>-linux-x64.tar.gz byproduct. This spec is
# SEPARATE from sa-live-transcribe.spec (Windows) and volksmond-mac.spec (macOS)
# on purpose: one spec per platform family, the collect lists and excludes differ
# too much to parametrise cleanly. The other specs are frozen; nothing here
# touches them.
#
# NOT bundled: the Whisper/Fluister model (multi-GB, downloads on first
# transcription) and the native GTK/WebKitGTK libraries. Freezing WebKitGTK is a
# known dead end (multi-process architecture, hardcoded helper paths), so the
# .deb DECLARES gir1.2-webkit2-4.1 / gir1.2-gtk-3.0 / libgtk-3-0 as Depends and
# apt supplies them on the target. PyGObject (the Python side) IS bundled, via
# PyInstaller's gi hooks. See docs/linux-port-plan.md sections 2.5-2.6.
#
# CPU int8 only in v1, but NOTE the deliberate difference from the Windows spec:
# ctranslate2's stock manylinux wheel keeps its CUDA capability (it dlopens
# libcublas/libcudnn if the system provides them), and we do NOT strip it. That
# is the documented manual GPU path (plan section 2.3, decision D2). There are
# no separate CUDA .so files in the Linux wheel to strip anyway; we simply
# bundle nothing extra.
import os
import re
from PyInstaller.utils.hooks import collect_all, collect_submodules

# --- App version: read from licensing.py (same source every other lane uses) ----------
# Parse the file rather than import the package: importing live_transcribe pulls the
# heavy ASR/server stack, and this spec only needs the one string.
_lic = os.path.join("live_transcribe", "licensing.py")
with open(_lic, encoding="utf-8") as _fh:
    _m = re.search(r'APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', _fh.read())
if not _m:
    raise SystemExit("volksmond-linux.spec: could not read APP_VERSION from licensing.py")
APP_VERSION = _m.group(1)

# Lowercase unix binary/folder name; the user-facing artifact names
# (Volksmond-<ver>.deb, Volksmond-<ver>-linux-x64.tar.gz) are applied by
# linux/build-app-linux.sh at packaging time.
APP_NAME = "volksmond"

datas, binaries, hiddenimports = [], [], []

# collect_all pulls each package's code, data and native libs. The Linux stack mirrors
# the Windows one MINUS the Windows-only backends (pyaudiowpatch WASAPI capture,
# clr_loader/pythonnet for the .NET pywebview backend) and MINUS sounddevice (the mac
# MIC path): Linux capture is native libpulse via pulsectl (enumeration) + pasimple
# (record streams), both pure-python ctypes over libpulse.so.0 (from the libpulse0
# Depends). faster_whisper/ctranslate2 = the CPU int8 ASR engine; uvicorn = the local
# server; llama_cpp = the CPU summary engine; webview = pywebview (GTK backend here).
for pkg in ("faster_whisper", "ctranslate2", "uvicorn", "llama_cpp", "webview",
            "pulsectl", "pasimple"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# livekit (the WebRTC APM echo canceller) is a SOFT dependency of the Linux build:
# its wheel is manylinux_2_28 (fine on the glibc 2.35 floor), but if the build venv
# has no livekit (wheel unavailable), the app degrades gracefully to AEC-off - the
# import is lazy inside aec.cancel_echo/aec_live. Warn, do not abort. Its APM import
# chain pulls protobuf (incl. the google._upb C extension) and aiofiles; the lazy
# import means static analysis never traces these, so collect them explicitly.
HAVE_LIVEKIT = True
try:
    for pkg in ("livekit", "google.protobuf", "aiofiles"):
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
except Exception as _e:  # pragma: no cover - build-machine diagnostic only
    HAVE_LIVEKIT = False
    print("volksmond-linux.spec: WARNING livekit (live AEC) not bundled (%s); "
          "the app will run with echo cancellation unavailable." % (_e,))

# soxr does the streaming resample for the live-AEC path; lazy import, cheap, and
# useful independently, so it is collected unconditionally (hard fail if missing).
for pkg in ("soxr",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("live_transcribe")
hiddenimports += ["scipy.signal", "anyio", "h11", "sniffio"]
if HAVE_LIVEKIT:
    hiddenimports += ["livekit.rtc.apm", "google._upb._message"]

# pywebview's GTK backend resolves its gi modules dynamically (gi.require_version
# then from gi.repository import ...), so PyInstaller's static analysis never sees
# them; name them explicitly, mirroring how the mac spec pins its pyobjc set. The
# gi hooks then gather the matching typelibs from the build container (which
# installs gir1.2-gtk-3.0 + gir1.2-webkit2-4.1). The native GTK/WebKit .so files
# the typelibs dlopen come from the .deb Depends on the target machine (see the
# a.binaries filter below).
hiddenimports += [
    "gi",
    "gi.repository.GLib",
    "gi.repository.GObject",
    "gi.repository.Gio",
    "gi.repository.Gtk",
    "gi.repository.Gdk",
    "gi.repository.GdkPixbuf",
    "gi.repository.Pango",
    "gi.repository.WebKit2",
    "gi.repository.Soup",
]

# The web UI's static asset(s).
datas += [(os.path.join("live_transcribe", "web", "static"),
           os.path.join("live_transcribe", "web", "static"))]

a = Analysis(
    ["app_main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # Heavy/irrelevant stacks (retranscribe runs in a separate env); tkinter isn't
    # needed (pywebview has its own file dialog). PLUS the platform backends that must
    # be physically absent from a Linux bundle: pyaudiowpatch/pythonnet/pywin32
    # (Windows), and sounddevice (mac MIC path; Linux capture is libpulse, by design -
    # released PortAudio builds cannot see Pulse/PipeWire monitor sources at all).
    excludes=["torch", "torchaudio", "torchvision", "whisperx", "pyannote",
              "pyannote.audio", "matplotlib", "tkinter", "IPython",
              "pyaudiowpatch", "clr", "clr_loader", "pythonnet",
              "win32com", "win32com.client", "win32timezone", "pythoncom",
              "pywintypes", "win32api", "win32gui", "win32con",
              "sounddevice", "_sounddevice"],
    # Trim the gi hooks' collection: no icon themes / GTK themes / translations
    # beyond English + Afrikaans (the app UI languages). Pin the module versions the
    # pywebview GTK backend actually uses so the hooks do not grab a GTK4 typelib on
    # some future base image.
    hooksconfig={
        "gi": {
            "icons": [],
            "themes": [],
            "languages": ["en", "af"],
            "module-versions": {"Gtk": "3.0", "WebKit2": "4.1"},
        },
    },
    runtime_hooks=[],
    noarchive=False,
)

# Do NOT bundle the WebKitGTK/JavaScriptCore giants even if the gi hooks dragged
# them in: a frozen WebKitGTK cannot spawn its helper processes (hardcoded paths),
# the classic blank-window dead end. The target machine's copies come from the
# .deb Depends (gir1.2-webkit2-4.1 pulls libwebkit2gtk-4.1-0), and the bundled
# typelib dlopens them by soname from the system paths.
_SYSTEM_ONLY = ("libwebkit2gtk", "libjavascriptcoregtk")
a.binaries = [b for b in a.binaries
              if not os.path.basename(b[0]).lower().startswith(_SYSTEM_ONLY)]

pyz = PYZ(a.pure)

# console=True is meaningless on Linux (no windowed subsystem); terminal-free launch
# comes from the .desktop entry (Terminal=false). When launched from a desktop entry
# stdout/stderr exist (journal/dev-null), so app_main.py's windowed-output redirect
# correctly no-ops and prints go wherever the session sends them.
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name=APP_NAME, console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME)

print("volksmond-linux.spec: building Volksmond %s (linux x86_64, CPU int8%s)"
      % (APP_VERSION, "" if HAVE_LIVEKIT else ", NO livekit"))
