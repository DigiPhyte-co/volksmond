r"""Windows desktop notifications (toasts). Best-effort, never fatal.

Volksmond sometimes needs to say something while its window is behind a Teams call:
"a meeting is starting", "nothing has been heard for five minutes". A banner inside a
hidden window is useless for that, so we hand the message to Windows itself and let it
appear in the corner and land in the notification centre.

Mechanism: a Shell_NotifyIcon BALLOON on a tray icon we own. That is deliberately the
oldest, dullest option available. It needs zero new dependencies (pywin32 is already
pinned and frozen-proven for the Outlook calendar read), it needs no AppUserModelID or
Start-menu shortcut the way the WinRT toast API does, it spawns no powershell.exe, and
on Windows 10/11 the shell renders these balloons as real toasts that persist in the
notification centre. The cost is one tray icon while the app is running, which Windows
requires: the notification is attached to the icon, and deleting the icon evicts the
notification. Hence the icon is created on the FIRST toast and then kept for the life
of the process.

Posture, copied from outlook_local.py because it earned it:
  - pywin32 is imported LAZILY, in exactly one place (_import_win32), so the module
    imports fine on a machine or a build without it, and so tests can replace it.
  - nothing here ever raises. Every public function returns a bool and swallows its
    own failures: a notification is a nicety, and no code path may break because the
    shell was unhappy.
  - availability is memoised, so a missing pywin32 costs one failed import per process.

Threading: every Shell_NotifyIcon call must be made by the thread that owns the window
the icon is attached to. Callers here are request threads and daemon watchdogs, so the
module owns a single message-only window on its own "toast-pump" daemon thread; the
public show() only appends to a queue and PostMessages WM_APP+1 at that window. The
pump thread makes the shell calls.

Kill switches: SA_LIVE_TOASTS=0 in the environment turns this off hard (before any
import), and the os_toasts setting (default on) turns it off per user.

Clicking a toast focuses the app window. That needs the pywebview Window object, which
only desktop.py has, and which MUST NOT be stored as a public attribute on DesktopApi
(pywebview's JS-API exposer walks public attributes and recurses into the .NET window
until the recursion limit; see the DesktopApi docstring). So desktop.py passes it here
through set_window_hook() instead, and this module holds it well away from that walker.
"""
import os
import sys
import threading
from collections import deque

# WM_APP is 0x8000. WM_APP+1 is our "drain the queue" message, posted by show() and
# handled on the pump thread; WM_APP+2 is the tray icon's uCallbackMessage, through
# which the shell reports clicks on the icon and on the balloon.
WM_TOAST = 0x8000 + 1
WM_TRAY = 0x8000 + 2

# WM_USER + 5. The pinned pywin32 does not define NIN_BALLOONUSERCLICK in win32con
# (verified on the build venv), so it is spelled out here rather than depending on a
# constant that may or may not exist. It arrives as the lParam of WM_TRAY when the user
# clicks the balloon body (as opposed to dismissing it with the X, which is
# NIN_BALLOONTIMEOUT).
NIN_BALLOONUSERCLICK = 1029

HWND_MESSAGE = -3          # parent for a message-only window: no paint, no taskbar, no focus
TOOLTIP = "Volksmond"      # tray icon hover text
_ICON_ID = 1
_INIT_TIMEOUT = 5.0        # seconds to wait for the pump thread to create its window

ENV_KILL = "SA_LIVE_TOASTS"

_lock = threading.RLock()
_import_ok = None          # None = not probed yet, True/False = memoised probe result
_init_failed = False       # a backend build failed once; do not keep retrying it
_notifier = None           # the _Notifier singleton (or a test fake)
_window_hook = None        # callable returning the pywebview Window, set by desktop.py
_shown = {}                # tag -> (title, body) of the toast currently outstanding for that tag
_warned = False            # so an unavailable backend logs once, not once per toast

# Test seam. When set to a zero-argument callable, _make_backend() uses it instead of
# building the real Shell_NotifyIcon backend, so the queue/coalescing/never-raises
# behaviour can be tested on a machine (or a CI venv) without pywin32.
_new_backend = None


def _import_win32():
    """The ONE place pywin32 is imported. Raises if it is not installed.

    Kept as a named function so tests can replace it (either to make the import fail or
    to hand back stand-ins) without touching sys.modules.
    """
    import win32api
    import win32con
    import win32gui
    return win32api, win32con, win32gui


def _env_on() -> bool:
    """False when SA_LIVE_TOASTS is set to 0/false/no: the hard, pre-import kill switch."""
    return (os.environ.get(ENV_KILL, "") or "").strip().lower() not in ("0", "false", "no", "off")


def _setting_on() -> bool:
    """The os_toasts user setting, read fresh each toast so the Settings toggle takes
    effect immediately rather than at the next launch. Defaults ON, and defaults ON again
    if settings cannot be read at all."""
    try:
        from . import config
        return config.load().get("os_toasts", True) is not False
    except Exception:
        return True


def available() -> bool:
    """True when a toast could actually be shown on this machine, right now.

    Deliberately cheap and side-effect-free beyond one memoised import probe: it does not
    create the window, the pump thread or the tray icon. Those happen on the first toast.
    """
    global _import_ok
    if sys.platform != "win32":
        return False
    if not _env_on():
        return False
    if _init_failed:
        return False
    if _import_ok is None:
        try:
            _import_win32()
            _import_ok = True
        except Exception as exc:
            _import_ok = False
            print(f"[notify] desktop notifications unavailable (pywin32: {exc})", flush=True)
    return bool(_import_ok)


def _make_backend():
    """Build the notification backend: the test fake when one is installed, else the real
    Shell_NotifyIcon notifier."""
    if _new_backend is not None:
        return _new_backend()
    win32api, win32con, win32gui = _import_win32()
    return _Notifier(win32api, win32con, win32gui)


def _backend():
    """The singleton backend, built on first use. None (once, loudly) if it cannot be built."""
    global _notifier, _init_failed
    with _lock:
        if _notifier is not None:
            return _notifier
        if _init_failed:
            return None
        try:
            _notifier = _make_backend()
        except Exception as exc:
            _init_failed = True
            print(f"[notify] could not start the notification backend: {exc!r}", flush=True)
            return None
        return _notifier


def show(title, body="", *, tag=None, on_click=None) -> bool:
    """Show a desktop notification. Returns True if one was handed to the shell.

    `tag` groups related notifications: a repeat of the SAME tag with the same text is
    swallowed rather than stacked, so a watchdog that ticks every second cannot fill the
    notification centre with sixty copies of itself. A same-tag toast with NEW text
    replaces the old one (one tray icon means the shell shows one balloon at a time
    anyway). tag=None never coalesces.

    `on_click` is called (guarded) if the user clicks the balloon, before the app window
    is brought forward. Never raises, whatever happens.
    """
    global _warned
    try:
        title = str(title or "")
        body = str(body or "")
        if not _setting_on():
            return False
        if not available():
            if not _warned:
                _warned = True
                print("[notify] desktop notifications are off or unavailable", flush=True)
            return False
        if tag is not None:
            with _lock:
                if _shown.get(tag) == (title, body):
                    return False           # identical toast already outstanding for this tag
                _shown[tag] = (title, body)
        backend = _backend()
        if backend is None:
            return False
        return bool(backend.notify(title, body, tag=tag, on_click=on_click))
    except Exception as exc:               # a notification must never break its caller
        print(f"[notify] show failed: {exc!r}", flush=True)
        return False


# --- focusing the app window ----------------------------------------------

def set_window_hook(fn) -> None:
    """Register a zero-argument callable returning the app's window, so a clicked toast
    can bring it forward. Called once from desktop.py.

    Why a hook and not an attribute on DesktopApi: pywebview's JS-API exposer walks every
    PUBLIC attribute of the api object and recurses into it, and a pywebview Window leads
    it into the WinForms Form and then into .NET statics that hand back a fresh wrapper on
    every access, so its visited-set never matches and it recurses to the limit, logging on
    every paint (the v1.0.0 "Not Responding" bug). The window must therefore live outside
    DesktopApi's public surface entirely. Pass an int HWND instead of a Window if you have
    one; both are accepted.
    """
    global _window_hook
    _window_hook = fn if callable(fn) else None


def _hwnd() -> int:
    """The app window's HWND via the registered hook, or 0. Never raises."""
    hook = _window_hook
    if hook is None:
        return 0
    try:
        win = hook()
    except Exception:
        return 0
    if win is None:
        return 0
    if isinstance(win, int):
        return win
    # pywebview's Window keeps the WinForms Form on .native; its .Handle is a .NET IntPtr,
    # which pythonnet exposes with ToInt64(). Every step is optional and guarded because
    # this is backend-specific and must degrade to "cannot focus", never to a crash.
    for get in (lambda: getattr(win, "hwnd"),
                lambda: getattr(getattr(win, "native"), "Handle").ToInt64(),
                lambda: getattr(getattr(win, "native"), "Handle")):
        try:
            h = get()
            if h:
                return int(h)
        except Exception:
            continue
    return 0


def focus_app() -> bool:
    """Bring the app window to the front. True if something was done.

    A no-op (False) when desktop.py never registered a window: the browser and
    server-only modes have no window to focus, and we do not go hunting for one.
    """
    try:
        if _window_hook is None:
            return False
        if not available():
            return False
        hwnd = _hwnd()
        if not hwnd:
            return False
        _win32api, win32con, win32gui = _import_win32()
        try:
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception:
            # Windows refuses SetForegroundWindow from a process that does not have the
            # foreground lock. Flashing the taskbar button is the sanctioned consolation.
            try:
                win32gui.FlashWindowEx(hwnd, win32con.FLASHW_ALL | win32con.FLASHW_TIMERNOFG, 3, 0)
                return True
            except Exception:
                return False
    except Exception:
        return False


# --- the real backend ------------------------------------------------------

def _icon_path():
    """Absolute path to volksmond.ico, or None. Frozen builds carry it next to the app
    (sys._MEIPASS); a source run has it in the project root beside live_transcribe/."""
    from pathlib import Path
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "volksmond.ico")
    candidates.append(Path(__file__).resolve().parent.parent / "volksmond.ico")
    for p in candidates:
        try:
            if p.is_file():
                return str(p)
        except Exception:
            continue
    return None


class _Notifier:
    """One message-only window plus one tray icon, driven from one daemon thread.

    Construction blocks (briefly) until the pump thread has its window, so a caller that
    gets a _Notifier back knows PostMessage will land somewhere. If the window cannot be
    created, construction raises and show() degrades to False for the rest of the process.
    """

    def __init__(self, win32api, win32con, win32gui):
        self._api, self._con, self._gui = win32api, win32con, win32gui
        self._hwnd = 0
        self._hicon = None
        self._icon_added = False
        self._pending = deque()
        self._clicks = {}          # tag -> on_click, replaced as newer toasts arrive
        self._last_click = None    # on_click for an untagged toast
        self._error = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._pump, name="toast-pump", daemon=True)
        self._thread.start()
        self._ready.wait(_INIT_TIMEOUT)
        if not self._hwnd:
            raise RuntimeError(self._error or "the toast window did not come up")

    # -- pump thread --

    def _pump(self):
        try:
            self._create_window()
        except BaseException as exc:            # noqa: BLE001 - reported to the constructor
            self._error = repr(exc)
            self._ready.set()
            return
        self._ready.set()
        try:
            self._gui.PumpMessages()            # returns when the window is destroyed
        except Exception:
            pass

    def _create_window(self):
        gui, api = self._gui, self._api
        cls = gui.WNDCLASS()
        cls.hInstance = api.GetModuleHandle(None)
        cls.lpszClassName = "VolksmondToast"
        cls.lpfnWndProc = {
            WM_TOAST: self._on_toast,
            WM_TRAY: self._on_tray,
            self._con.WM_DESTROY: self._on_destroy,
        }
        try:
            gui.RegisterClass(cls)
        except Exception:
            pass                                # already registered in this process: reuse it
        self._hwnd = gui.CreateWindow(cls.lpszClassName, TOOLTIP, 0, 0, 0, 0, 0,
                                     HWND_MESSAGE, 0, cls.hInstance, None)

    def _load_icon(self):
        gui, con = self._gui, self._con
        path = _icon_path()
        if path:
            try:
                return gui.LoadImage(0, path, con.IMAGE_ICON, 0, 0,
                                     con.LR_LOADFROMFILE | con.LR_DEFAULTSIZE)
            except Exception:
                pass
        try:
            return gui.LoadIcon(0, con.IDI_APPLICATION)
        except Exception:
            return 0

    def _ensure_icon(self):
        """Add the tray icon, once. Windows attaches the balloon to an icon, and removing
        the icon removes the notification from the notification centre with it, so once
        added it stays for the life of the process."""
        if self._icon_added:
            return
        gui = self._gui
        self._hicon = self._load_icon()
        gui.Shell_NotifyIcon(gui.NIM_ADD, (
            self._hwnd, _ICON_ID,
            gui.NIF_ICON | gui.NIF_MESSAGE | gui.NIF_TIP,
            WM_TRAY, self._hicon, TOOLTIP,
        ))
        self._icon_added = True

    def _on_toast(self, hwnd, msg, wparam, lparam):
        """WM_APP+1: drain the queue on the thread that owns the window."""
        while True:
            try:
                title, body, tag, on_click = self._pending.popleft()
            except IndexError:
                return 0
            try:
                self._ensure_icon()
                gui = self._gui
                gui.Shell_NotifyIcon(gui.NIM_MODIFY, (
                    self._hwnd, _ICON_ID,
                    gui.NIF_INFO | gui.NIF_ICON | gui.NIF_MESSAGE | gui.NIF_TIP,
                    WM_TRAY, self._hicon, TOOLTIP,
                    body, 0, title, gui.NIIF_INFO,
                ))
                if tag is None:
                    self._last_click = on_click
                else:
                    self._clicks[tag] = on_click
            except Exception as exc:
                print(f"[notify] the shell refused a notification: {exc!r}", flush=True)

    def _on_tray(self, hwnd, msg, wparam, lparam):
        """The tray icon's callback message. lParam says what happened to it."""
        if lparam in (NIN_BALLOONUSERCLICK, self._con.WM_LBUTTONUP):
            callbacks = list(self._clicks.values()) + [self._last_click]
            self._clicks.clear()
            self._last_click = None
            with _lock:
                _shown.clear()      # the outstanding toasts are gone; let them be shown again
            for cb in callbacks:
                if cb is None:
                    continue
                try:
                    cb()
                except Exception as exc:
                    print(f"[notify] toast click handler failed: {exc!r}", flush=True)
            focus_app()
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        try:
            self._gui.Shell_NotifyIcon(self._gui.NIM_DELETE, (self._hwnd, _ICON_ID))
        except Exception:
            pass
        try:
            self._gui.PostQuitMessage(0)
        except Exception:
            pass
        return 0

    # -- any thread --

    def notify(self, title, body, *, tag=None, on_click=None) -> bool:
        """Queue a balloon and wake the pump thread. Called from request/watchdog threads."""
        self._pending.append((title, body, tag, on_click))
        try:
            self._gui.PostMessage(self._hwnd, WM_TOAST, 0, 0)
        except Exception as exc:
            print(f"[notify] could not reach the toast pump: {exc!r}", flush=True)
            return False
        return True


def _reset_for_tests() -> None:
    """Forget every memoised decision and the singleton. Tests only."""
    global _import_ok, _init_failed, _notifier, _window_hook, _new_backend, _warned
    with _lock:
        _import_ok = None
        _init_failed = False
        _notifier = None
        _window_hook = None
        _new_backend = None
        _warned = False
        _shown.clear()
