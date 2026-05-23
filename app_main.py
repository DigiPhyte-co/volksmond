"""Frozen-app entry point for SA-Live-Transcribe (PyInstaller).

Defaults to the native pywebview window (the shipped experience): starts the local
server and shows the UI in an app window. `--browser` opens a browser tab instead;
`--server-only` runs headless (build smoke-tests). pywebview + pythonnet are bundled
by sa-live-transcribe.spec, so the native window works in the frozen app.
"""
import sys

from live_transcribe.desktop import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
