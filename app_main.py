"""Frozen-app entry point for SA-Live-Transcribe (PyInstaller).

Defaults to browser mode: starts the local server, opens the browser, and keeps a
console window the user can close to stop. `--server-only` runs headless (used for
build smoke-tests). The native pywebview window is a later packaging step; this
frozen build deliberately avoids it so it doesn't have to bundle pythonnet.
"""
import sys

from live_transcribe.desktop import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["--browser"]))
