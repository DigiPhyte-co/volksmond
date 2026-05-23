"""Launch the SA-Live-Transcribe web UI.

Usage:
    python -m live_transcribe.web                # opens browser automatically
    python -m live_transcribe.web --no-browser   # don't open browser
    python -m live_transcribe.web --port 9000    # custom port
"""
import argparse
import threading
import time
import webbrowser


def main():
    parser = argparse.ArgumentParser(prog="live_transcribe.web")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1, localhost-only)")
    parser.add_argument("--port", type=int, default=8765,
                        help="Port (default: 8765)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open the browser automatically")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"

    if not args.no_browser:
        def _open():
            time.sleep(1.2)  # wait for uvicorn to bind
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print(f"\n  SA-Live-Transcribe UI on {url}")
    print("  Press Ctrl+C to stop the server.\n")

    import uvicorn
    from .app import app
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
