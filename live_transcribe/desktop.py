"""Desktop-app shell: runs the web UI in-process and shows it in a native window.

Same UI as `python -m live_transcribe.web`, but as a standalone application window
(via pywebview) instead of a browser tab. This is the shell we ship to end users:
it feels like an app, keeps the offline and private promise, and has no browser tab
to lose. A small JS API (window.pywebview.api) lets the in-window UI open external
links in the OS handler and show native file pickers; the browser build falls back
to /api/pick and window.location. Run:

    python -m live_transcribe.desktop

The uvicorn server binds to 127.0.0.1 on the fixed port (localhost-only, never
public). Closing the window stops the server. Only ONE copy of the app ever runs:
that bind is also the single-instance guard, see claim_port().
"""
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

HOST = "127.0.0.1"
PREFERRED_PORT = 8765
WINDOW_TITLE = "Volksmond"

# The .app bundle identifier (see volksmond-mac.spec). A second launch uses it to ask
# LaunchServices to activate the copy that is already running.
BUNDLE_ID = "com.digiphyte.volksmond"

# The endpoint a second launch probes, and the marker it looks for in the reply.
# /api/app-info is chosen because it already exists, is a GET (so the CSRF middleware
# lets it through), touches no session state, and carries a name we can match on.
PROBE_PATH = "/api/app-info"
APP_MARKER = "Volksmond"

# How long the probe may keep asking "are you Volksmond?" before giving up. It has to
# cover a cold start of the OTHER instance: two launches milliseconds apart leave the
# loser talking to a socket that is bound and listening but whose uvicorn has not
# attached yet, so the connection is accepted (from the kernel backlog) and the request
# simply waits. 20s matches wait_for_server's own "did the server come up" budget.
# The cost of the budget is paid only in the exotic case where a non-Volksmond, non-HTTP
# program squats on 8765: that launch is delayed by this much before falling back to a
# spare port. A foreign HTTP service answers at once and is classified immediately.
PROBE_TIMEOUT = 20.0
PROBE_ATTEMPT_TIMEOUT = 5.0

# How long the close is allowed to wait for a running session to finalise. Long enough for
# a normal stop (capture stop + a short ASR backlog + closing the files), short enough that
# a wedged drain never turns into an unclosable window. Past this we let the window go and
# rely on the sinks' atexit handlers to flush what is left.
CLOSE_FINALISE_TIMEOUT = 5.0


def claim_port(preferred=PREFERRED_PORT):
    """Bind and listen on `preferred`, KEEPING the socket. The socket, or None if taken.

    This is the single-instance guard, and the returned socket is the claim: it is handed
    straight to uvicorn (start_server) and lives for the process, so nothing else can take
    the port while this instance runs.

    Before this, free_port() bound the port, CLOSED it, and returned the number, and
    uvicorn bound it again moments later. Two things fell out of that. First, a second
    launch that found the port taken silently bound port 0 instead and started a whole
    second app: second window, second server, two processes fighting over the same audio
    devices and writing sessions into the same folder (the field report that produced this
    guard). Second, the gap between our bind and uvicorn's was a check-then-act race: two
    launches milliseconds apart could both see the port free.

    Now the kernel arbitrates. Exactly one process can hold a bound listening socket for
    127.0.0.1:8765, and the loser learns it lost from the OSError, with no window in
    between. Deliberately NO SO_REUSEADDR: on Windows that option lets a second process
    steal a port another process is already listening on, which would hand both launches
    a "win". Everywhere else it would be harmless, but there is nothing to gain from it
    on a loopback server that restarts with the app.
    """
    s = socket.socket()
    try:
        s.bind((HOST, preferred))
        s.listen(128)
    except OSError:
        s.close()
        return None
    return s


def spare_socket():
    """A bound, listening socket on an OS-assigned port.

    The fallback for when 8765 is held by a program that is NOT Volksmond: the app still
    starts (an unrelated service on our port must never brick it), just somewhere else.
    """
    s = socket.socket()
    s.bind((HOST, 0))
    s.listen(128)
    return s


def _probe_once(host, port, timeout):
    """One GET of PROBE_PATH. ("answered", body) | ("http-error", None) | ("gone", None)
    | ("no-answer", None).

    The caller needs the distinction: an answer of any kind is decisive, "gone" means the
    listener disappeared, and only "no-answer" (accepted but silent, or a transient
    failure) is worth retrying.
    """
    url = f"http://{host}:{port}{PROBE_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return "answered", resp.read(4096)
    except urllib.error.HTTPError:
        return "http-error", None            # it speaks HTTP and it is not us
    except Exception as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ConnectionRefusedError):
            return "gone", None              # nothing is listening there any more
        return "no-answer", None


def is_running_instance(host=HOST, port=PREFERRED_PORT, timeout=PROBE_TIMEOUT,
                        attempt=PROBE_ATTEMPT_TIMEOUT):
    """True when the program already holding host:port is a running Volksmond.

    Used only after claim_port() lost the bind, to decide between handing over to our own
    already-running window and stepping aside for an unrelated service. Retries while the
    answer is still ambiguous (see PROBE_TIMEOUT); every decisive answer returns at once.
    """
    deadline = time.monotonic() + timeout
    while True:
        outcome, body = _probe_once(host, port, attempt)
        if outcome == "answered":
            try:
                return json.loads(body.decode("utf-8", "replace")).get("name") == APP_MARKER
            except Exception:
                return False                 # it answered, but that is not our JSON
        if outcome in ("http-error", "gone"):
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _front_window_win32(title=WINDOW_TITLE):
    """Bring the other instance's window forward on Windows via pywin32. True if done.

    pywin32 is already a pinned, frozen-proven dependency (notify.py, outlook_local.py),
    and this is the same SetForegroundWindow-then-flash dance notify.focus_app does, only
    aimed at a window owned by ANOTHER process, found by its exact title. Never raises:
    a failure here just means the caller falls back to opening the URL.
    """
    try:
        import win32con
        import win32gui
    except Exception:
        return False
    try:
        hwnd = win32gui.FindWindow(None, title)
    except Exception:
        return False
    if not hwnd:
        return False
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)   # no-op unless it is minimised
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        # Windows refuses SetForegroundWindow from a process without the foreground lock.
        # Flashing the taskbar button is the sanctioned consolation (same as notify.py).
        try:
            win32gui.FlashWindowEx(hwnd, win32con.FLASHW_ALL | win32con.FLASHW_TIMERNOFG, 3, 0)
            return True
        except Exception:
            return False


def _front_window_mac(bundle_id=BUNDLE_ID):
    """Bring the other instance's window forward on macOS. True if done.

    `open -b <bundle id>` asks LaunchServices to activate the app: without -n it does not
    start a second copy, it raises the one that is running. osascript is the second try,
    for a bundle LaunchServices cannot resolve by id. Both are stdlib subprocess calls,
    no new dependency. A source run (no bundle registered at all) fails both and the
    caller opens the URL instead.
    """
    import subprocess
    for cmd in (["open", "-b", bundle_id],
                ["osascript", "-e", f'tell application id "{bundle_id}" to activate']):
        try:
            if subprocess.run(cmd, capture_output=True, timeout=10).returncode == 0:
                return True
        except Exception:
            continue
    return False


def front_running_instance(url, mode="window"):
    """Bring the Volksmond that is already running to the user's attention.

    Returns a short label of the route taken, for the log. Never raises: this runs on the
    way out of a duplicate launch, and the important half (not starting a second server)
    has already happened.
    """
    try:
        if mode == "server":
            return "no window (server-only)"      # headless: nothing to raise
        if mode == "window":
            if sys.platform == "darwin" and _front_window_mac():
                return "LaunchServices activate"
            if sys.platform == "win32" and _front_window_win32():
                return "foreground window"
        # --browser mode, and the degraded path for a window we could not raise: point the
        # default browser at the instance that IS running. One server, one set of devices.
        import webbrowser
        webbrowser.open(url)
        return "browser"
    except Exception as exc:
        return f"could not raise it ({exc!r})"


def wait_for_server(host, port, timeout=20.0):
    """Block until the server ANSWERS an HTTP request, or timeout. True if it is serving.

    This used to be a bare TCP connect. That is no longer proof of anything: main() binds
    the listening socket itself (claim_port, the single-instance guard) and hands it to
    uvicorn, so a connect succeeds out of the kernel backlog before uvicorn has attached.
    Any complete HTTP reply counts, error statuses included: this only has to prove the
    app is serving, not that one endpoint is healthy.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        outcome, _ = _probe_once(host, port, min(2.0, timeout))
        if outcome in ("answered", "http-error"):
            return True
        time.sleep(0.1)
    return False


def start_server(sock):
    """Launch the FastAPI app under uvicorn on a daemon thread, serving the ALREADY-BOUND
    socket `sock` (see claim_port). Returns the server so the caller can set
    `should_exit = True` to stop it."""
    import uvicorn
    from .web.app import app
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="warning"))
    threading.Thread(target=lambda: server.run(sockets=[sock]),
                     daemon=True, name="uvicorn").start()
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
    """Modes: window (default, pywebview) | --browser (open browser) | --server-only.

    Single instance, in every mode: whoever binds 127.0.0.1:8765 owns the app (claim_port).
    A launch that loses the bind to a Volksmond that is already running raises that window
    and exits 0 rather than starting a second server, a second window and a second set of
    audio and GPU consumers. A launch that loses it to some OTHER program on 8765 starts
    normally on a spare port.
    """
    args = argv if argv is not None else sys.argv[1:]
    mode = "server" if "--server-only" in args else ("browser" if "--browser" in args else "window")

    # Every port here is read from the module global at call time, never defaulted, so the
    # guard can be exercised end to end against a stub port in the tests.
    sock = claim_port(PREFERRED_PORT)
    if sock is None:
        if is_running_instance(HOST, PREFERRED_PORT):
            route = front_running_instance(f"http://{HOST}:{PREFERRED_PORT}", mode)
            print(f"[desktop] Volksmond is already running on port {PREFERRED_PORT}; "
                  f"raised the running copy ({route}) and left it to it", flush=True)
            return 0
        # Not us on that port. Either an unrelated program holds it, or the instance that
        # held it died while we were probing, so try the fixed port once more before
        # settling for a spare one. (While a stranger squats 8765 there is no shared
        # arbitration point left, so two launches could both end up on spare ports. That
        # is the deliberate trade: an unrelated service on our port must never leave the
        # user unable to start their own app.)
        sock = claim_port(PREFERRED_PORT)
        if sock is None:
            sock = spare_socket()
            print(f"[desktop] port {PREFERRED_PORT} is held by another program; starting on "
                  f"port {sock.getsockname()[1]} instead", flush=True)
    port = sock.getsockname()[1]
    server = start_server(sock)
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
    sys.exit(main())
