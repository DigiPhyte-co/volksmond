"""Desktop-app shell: runs the web UI in-process and shows it in a native window.

Same UI as `python -m live_transcribe.web`, but as a standalone application window
(via pywebview) instead of a browser tab. This is the shell we ship to end users:
it feels like an app, keeps the offline and private promise, and has no browser tab
to lose. A small JS API (window.pywebview.api) lets the in-window UI open external
links in the OS handler and show native file pickers; the browser build falls back
to /api/pick and window.location. Run:

    python -m live_transcribe.desktop

The uvicorn server binds to 127.0.0.1 on a free port (localhost-only, never
public). Closing the window stops the server.
"""
import socket
import threading
import time

HOST = "127.0.0.1"
PREFERRED_PORT = 8765
WINDOW_TITLE = "Volksmond"


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
    print(f"Volksmond is running at {url}", flush=True)
    print("Leave this window open while you use it. Close it (or Ctrl+C) to stop.", flush=True)
    try:
        while not server.should_exit:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    server.should_exit = True


class DesktopApi:
    """Bridge exposed to the page as window.pywebview.api.*. Each method's return
    value reaches JS as a resolved promise. It gives the native-window UI the two
    things a browser does for free: open external links in the OS handler, and show
    a native file or folder picker."""

    def __init__(self):
        self.window = None

    def open_external(self, url):
        """Open a URL (e.g. a mailto: bug report) in the OS default handler rather
        than navigating the app window to it."""
        import webbrowser
        try:
            webbrowser.open(url)
            return True
        except Exception:
            return False

    def pick_path(self, kind="file"):
        """Show a native open dialog and return the chosen absolute path, or None.

        kind 'file' picks one audio or video file to import; 'folder' picks a save
        location. Uses pywebview's own dialog (not tkinter) so it works inside the
        native window without a second GUI toolkit."""
        import webview
        w = self.window
        if w is None:
            return None
        file_dialog = getattr(webview, "FileDialog", None)  # 6.x enum; fall back to the old ints
        try:
            if kind == "folder":
                dtype = file_dialog.FOLDER if file_dialog is not None else webview.FOLDER_DIALOG
                result = w.create_file_dialog(dtype)
            else:
                dtype = file_dialog.OPEN if file_dialog is not None else webview.OPEN_DIALOG
                result = w.create_file_dialog(
                    dtype,
                    allow_multiple=False,
                    file_types=(
                        "Audio and video (*.mp3;*.m4a;*.wav;*.mp4;*.mov;*.ogg;*.flac;*.aac;*.webm;*.mkv;*.avi)",
                        "All files (*.*)",
                    ),
                )
        except Exception:
            return None
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result


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
        api = DesktopApi()
        window = webview.create_window(
            WINDOW_TITLE, url,
            width=1180, height=860, min_size=(940, 640),
            js_api=api,
        )
        api.window = window
        webview.start()          # blocks until the window is closed
        server.should_exit = True
    else:
        _keep_alive(server, url, open_browser=(mode == "browser"))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
