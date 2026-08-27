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
    them to <data_dir>\\volksmond.log (live_transcribe.paths: the per-user app-data
    folder, %LOCALAPPDATA%\\sa-live-transcribe on Windows), truncated each launch
    so it stays small and always reflects the latest run. Falls back to os.devnull
    if the file cannot be opened. No-op when a console is present (source/dev runs).
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        # paths.py is stdlib-only and import-light by contract, so this is safe this
        # early. The except is belt-and-braces: the redirect must NEVER itself crash
        # a windowed build, or there would be no crash log at all.
        from live_transcribe.paths import data_dir
        base = data_dir()
    except Exception:
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


def _mlx_smoke():
    """CI-only frozen smoke (VOLKSMOND_SMOKE=mlx): prove THIS frozen bundle can import the MLX
    runtime (mlx.core + mlx_whisper) without torch, which the mac spec excludes. mac-release.yml
    runs the built .app binary with the env var set and asserts the OK line; a bundle where
    collect_all missed a piece fails here in seconds instead of at the first Mac transcription.
    Exit code: 0 with 'MLX SMOKE OK' on success; on a platform that ships no MLX (Windows,
    Intel Macs) a failed import prints SKIP and exits 0; on darwin-arm64 (the shipped Mac
    bundle) a failed import is a packaging bug and exits 1. Never runs unless the env var is
    set, so shipped behaviour on every platform is untouched."""
    import platform
    try:
        import mlx.core        # noqa: F401  (the Metal runtime; needs its .metallib packaged)
        import mlx_whisper     # noqa: F401  (the ASR backend; must import WITHOUT torch)
    except Exception as e:
        if sys.platform == "darwin" and platform.machine() == "arm64":
            print(f"MLX SMOKE FAIL: {e!r}")
            return 1
        print(f"MLX SMOKE SKIP (no MLX runtime on this platform): {e!r}")
        return 0
    print("MLX SMOKE OK")
    return 0


if __name__ == "__main__":
    # Redirect first so a windowed build has a valid stdout/stderr, then import:
    # any import-time failure in the app is then captured in the crash log too.
    _redirect_windowed_output()
    if os.environ.get("VOLKSMOND_SMOKE") == "mlx":
        sys.exit(_mlx_smoke())
    from live_transcribe.desktop import main
    sys.exit(main(sys.argv[1:]))
