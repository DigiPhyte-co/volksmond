"""Desktop-app shell: runs the web UI in-process and shows it in a native window.

Same UI as `python -m live_transcribe.web`, but as a standalone application window
(via pywebview) instead of a browser tab. This is the basis for the packaged,
double-click installer (Phase 2). Run:

    python -m live_transcribe.desktop

The uvicorn server binds to 127.0.0.1 on a free port (localhost-only, never
public). Closing the window stops the server.
"""
import socket
import threading
import time

HOST = "127.0.0.1"
PREFERRED_PORT = 8765


def free_port(preferred=PREFERRED_PORT):
    """Return `preferred` if it's free, otherwise an OS-assigned free port."""
    s = socket.socket()
    try:
        s.bind((HOST, preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
    s2 = socket.socket()
    s2.bind((HOST, 0))
    port = s2.getsockname()[1]
    s2.close()
    return port


def wait_for_server(host, port, timeout=20.0):
    """Block until the server accepts a TCP connection, or timeout. True if up."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def start_server(port):
    """Launch the FastAPI app under uvicorn on a daemon thread. Returns the server
    so the caller can set `should_exit = True` to stop it."""
    import uvicorn
    from .web.app import app
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True, name="uvicorn").start()
    return server


def _keep_alive(server, url, open_browser):
    import time
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    print(f"SA-Live-Transcribe is running at {url}", flush=True)
    print("Leave this window open while you use it. Close it (or Ctrl+C) to stop.", flush=True)
    try:
        while not server.should_exit:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    server.should_exit = True


def main(argv=None):
    """Modes: window (default, pywebview) | --browser (open browser) | --server-only."""
    import sys
    args = argv if argv is not None else sys.argv[1:]
    mode = "server" if "--server-only" in args else ("browser" if "--browser" in args else "window")

    port = free_port()
    server = start_server(port)
    if not wait_for_server(HOST, port):
        print("[fatal] server did not come up", flush=True)
        return 2
    url = f"http://{HOST}:{port}"

    if mode == "window":
        import webview  # lazy: browser/server modes never need pythonnet
        webview.create_window("SA-Live-Transcribe", url, width=1100, height=820)
        webview.start()          # blocks until the window is closed
        server.should_exit = True
    else:
        _keep_alive(server, url, open_browser=(mode == "browser"))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
