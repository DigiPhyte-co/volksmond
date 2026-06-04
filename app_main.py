"""Frozen-app entry point for SA-Live-Transcribe (PyInstaller).

Defaults to the native pywebview window (the shipped experience): starts the local
server and shows the UI in an app window. `--browser` opens a browser tab instead;
`--server-only` runs headless (build smoke-tests). pywebview + pythonnet are bundled
by sa-live-transcribe.spec, so the native window works in the frozen app.

The shipped build is windowed (console=False in the spec), so there is no terminal
on launch. A windowed build has no console, which means sys.stdout and sys.stderr
are None and any print() (or an uncaught traceback) would raise. The redirect below
points both at a per-launch log file so the app runs cleanly and testers still have
a crash log to send. Console / source runs keep their normal stdout untouched.
"""
import os
import sys
from pathlib import Path


def _redirect_windowed_output():
    """In a windowed (no-console) build, point sys.stdout/stderr at a log file.

    Without a console both are None, so print() and tracebacks would raise. Write
    them to %LOCALAPPDATA%\\sa-live-transcribe\\volksmond.log, truncated each launch
    so it stays small and always reflects the latest run. Falls back to os.devnull
    if the file cannot be opened. No-op when a console is present (source/dev runs).
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "sa-live-transcribe"
    try:
        base.mkdir(parents=True, exist_ok=True)
        sink = open(base / "volksmond.log", "w", encoding="utf-8", buffering=1)
    except OSError:
        sink = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = sink
    if sys.stderr is None:
        sys.stderr = sink


if __name__ == "__main__":
    # Redirect first so a windowed build has a valid stdout/stderr, then import:
    # any import-time failure in the app is then captured in the crash log too.
    _redirect_windowed_output()
    from live_transcribe.desktop import main
    sys.exit(main(sys.argv[1:]))
