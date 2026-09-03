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

from . import edition

HOST = "127.0.0.1"
PREFERRED_PORT = 8765
# Per edition: the direct-download build says "Volksmond Fast Track", so its window, taskbar
# button and Alt-Tab entry are distinguishable from a Store install of the same app on the same
# machine. Every other edition says "Volksmond" exactly as before (live_transcribe/edition.py).
WINDOW_TITLE = edition.DISPLAY_NAME

# How long the close is allowed to wait for a running session to finalise. Long enough for
# a normal stop (capture stop + a short ASR backlog + closing the files), short enough that
# a wedged drain never turns into an unclosable window. Past this we let the window go and
# rely on the sinks' atexit handlers to flush what is left.
CLOSE_FINALISE_TIMEOUT = 5.0


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


def finalise_open_session(timeout=CLOSE_FINALISE_TIMEOUT, poll=0.05, lock_timeout=0.5):
    """Finalise a running session the way the UI's "Stop and save" does, so closing the
    window is a real end-of-session and not a silent one.

    Before this existed, closing the window went straight to `server.should_exit = True`:
    the transcript and recording were only saved because MarkdownSink/AudioRecorder register
    atexit handlers, and the app's own stop path (which counts the session and lets the UI
    finish it) never ran. Result: session_count stuck at 1 across 50+ real meetings.

    Approach: call the `/api/stop?what=all` handler IN-PROCESS rather than doing an HTTP
    self-call. The window-close handler runs on the GUI thread; an HTTP call would make
    closing the window depend on the uvicorn thread being healthy and on a socket round
    trip, for no gain (the handler is a plain function - FastAPI's decorator returns it
    unchanged).

    Deadlock analysis (this runs on the GUI thread, which must always get to return):
      * The stop call is made on a throwaway daemon thread, so even a stop that blocks
        forever cannot hold the window open.
      * This function never holds STATE.lock while waiting, and every acquisition is
        bounded (`lock_timeout`). A busy lock is RETRIED until the overall close deadline
        rather than abandoning finalisation on the first miss: closing right after Begin
        finds /api/start holding STATE.lock through engine + capture construction, which
        is seconds on a model load. The retry is still bounded by `timeout`, so no lock
        held by a request thread can wedge the close.
      * The wait for the drain is a bounded poll (`timeout`), never a join on the drain
        thread, and `/api/stop` itself only holds STATE.lock briefly before handing off
        to its own daemon thread.
      * The bump happens synchronously inside the stop handler, so the session is counted
        even when the drain outlives the timeout.
    Worst case the GUI thread blocks for about `timeout` and the window then closes.

    Returns a short status string: "idle" (nothing was running), "finalised", "timeout"
    (still draining; atexit is the backstop) or "unavailable".
    """
    try:
        from .web import app as webapp
    except Exception as exc:                       # pragma: no cover - import can't realistically fail here
        print(f"[desktop] close: session state unavailable ({exc})", flush=True)
        return "unavailable"
    state = webapp.STATE
    deadline = time.monotonic() + timeout

    def _flags():
        """(running, stopping), or None once the close deadline passed with the lock busy.

        Each acquisition stays bounded by `lock_timeout` (never a deadlock, never an
        unbounded wait), but a single busy attempt no longer abandons the close: we keep
        retrying inside the OVERALL `timeout` budget."""
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            if not state.lock.acquire(timeout=min(lock_timeout, left)):
                continue
            try:
                return bool(state.running), bool(state.stopping)
            finally:
                state.lock.release()

    def _dispatch_stop():
        """Run /api/stop?what=all on a throwaway daemon thread (see the deadlock notes)."""
        def _stop():
            try:
                webapp.stop(what="all")
            except Exception as exc:   # 409 if the UI's own stop won the race - harmless
                print(f"[desktop] close: stop reported {exc!r}", flush=True)
        threading.Thread(target=_stop, daemon=True, name="close-stop").start()

    flags = _flags()
    if flags is None:
        print("[desktop] close: session lock busy, leaving finalisation to atexit", flush=True)
        return "unavailable"
    running, stopping = flags
    if not running:
        return "idle"          # the common case: nothing running, close immediately

    dispatched = False
    if not stopping:           # a stop already in flight finalises on its own; don't start a second one
        dispatched = True
        _dispatch_stop()

    while time.monotonic() < deadline:
        time.sleep(poll)
        flags = _flags()
        if flags is None:
            break              # the deadline passed while the lock stayed busy
        if not flags[0]:
            print("[desktop] close: session finalised", flush=True)
            return "finalised"
        if not flags[1] and not dispatched:
            # The stop that was in flight when the window closed was a PARTIAL one (the user
            # had stopped transcription only, recording carried on). It has now finished:
            # stopping is False again but the session is still running, and nothing will ever
            # finalise it - the close would time out and the session would go uncounted.
            # Upgrade to a full stop, once.
            dispatched = True
            print("[desktop] close: partial stop finished, upgrading to a full stop", flush=True)
            _dispatch_stop()
    print(f"[desktop] close: session still finalising after {timeout:.1f}s; closing anyway "
          "(the transcript and recording are flushed by their atexit handlers)", flush=True)
    return "timeout"


def _on_closing():
    """pywebview `closing` handler. It runs synchronously on the GUI thread and can VETO
    the close by returning False, so this must never return False and never raise."""
    try:
        finalise_open_session()
    except Exception as exc:
        print(f"[desktop] close: finalisation failed ({exc})", flush=True)
    return True


class DesktopApi:
    """Bridge exposed to the page as window.pywebview.api.*. Each method's return
    value reaches JS as a resolved promise. It gives the native-window UI the two
    things a browser does for free: open external links in the OS handler, and show
    a native file or folder picker.

    NOTE: every PUBLIC attribute (no leading underscore) gets walked recursively by
    pywebview's JS-API exposer (`webview.util.get_functions`, util.py:180). If we
    exposed `self.window` as a public attribute, that walker would recurse into the
    pywebview Window, then `.native` (the WinForms Form), then `.AccessibilityObject
    .Bounds`, then `Rectangle.Empty` (a .NET static that pythonnet returns as a NEW
    wrapper each access, so the visited-id-set never matches), recursing until the
    Python recursion limit; each failure is logged, on every paint, and the GUI
    thread chokes (the v1.0.0 "Not Responding" bug). Keep `_window` private so the
    walker skips it (it skips names starting with `_`)."""

    def __init__(self):
        self._window = None

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
        w = self._window
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
        api._window = window     # MUST stay underscored (see DesktopApi docstring)
        # Let notify.py bring this window forward when the user clicks a desktop notification.
        # It is handed over as a CLOSURE, not as an attribute on `api`, for the same reason
        # `_window` is underscored: any public attribute on the js_api object gets walked
        # recursively by pywebview's exposer and a Window leads it into .NET statics that
        # recurse to the limit on every paint (see the DesktopApi docstring). notify.py holds
        # the window in a module global, well outside that walker's reach.
        from . import notify
        notify.set_window_hook(lambda: window)
        # Closing the window must finalise a running session first (see
        # finalise_open_session): `closing` is the only event that still runs while the
        # server and the session threads are alive.
        window.events.closing += _on_closing
        webview.start()          # blocks until the window is closed
        server.should_exit = True
    else:
        _keep_alive(server, url, open_browser=(mode == "browser"))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
