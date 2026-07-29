"""Tests for live_transcribe/notify.py (WP-9a: the Windows toast foundation).

These tests NEVER import pywin32. That is not squeamishness: notify.py is the module
that decides what happens on a machine WITHOUT pywin32, so a test that imported it would
be testing the wrong machine, and the repo venv is not guaranteed to have it. Everything
goes through the module's two seams instead:

  * `_import_win32()`   - the single place pywin32 is imported. Replaced here with either
                          a raiser (the "not installed" machine) or stand-in modules.
  * `_new_backend`      - a factory for the notification backend. Replaced here with a
                          recorder, so the queue, the coalescing and the never-raises
                          promise are all exercised without the Windows shell.

Every test restores the module's globals in a finally block, because the module memoises
its availability decision on purpose (a missing pywin32 must cost one failed import per
process, not one per toast).

Run:  python tests/test_notify.py   (from the project root; exit 0 = pass)
"""
import os
import sys

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import config, notify


class FakeBackend:
    """Stands in for _Notifier: records what show() hands down, raises on demand."""

    def __init__(self, boom=False):
        self.calls = []
        self.boom = boom

    def notify(self, title, body, *, tag=None, on_click=None):
        self.calls.append({"title": title, "body": body, "tag": tag, "on_click": on_click})
        if self.boom:
            raise RuntimeError("the shell said no")
        return True


class FakeGui:
    """The handful of win32gui calls focus_app() makes."""

    def __init__(self, foreground_fails=False):
        self.foreground = []
        self.flashed = []
        self.foreground_fails = foreground_fails

    def SetForegroundWindow(self, hwnd):
        if self.foreground_fails:
            raise RuntimeError("no foreground lock")
        self.foreground.append(hwnd)

    def FlashWindowEx(self, hwnd, flags, count, timeout):
        self.flashed.append(hwnd)


class FakeCon:
    FLASHW_ALL = 3
    FLASHW_TIMERNOFG = 12


def _install(*, importer=None, backend=None, setting=True):
    """Point notify.py at stand-ins and return a restore() callable.

    importer: replacement for _import_win32 (None = hand back harmless stand-ins).
    backend:  a backend instance to serve as the singleton factory's product.
    setting:  the value os_toasts should appear to have.
    """
    saved = {
        "_import_win32": notify._import_win32,
        "_new_backend": notify._new_backend,
        "config_load": config.load,
        "env": os.environ.get(notify.ENV_KILL),
    }
    notify._reset_for_tests()
    fake_modules = (object(), FakeCon(), FakeGui())
    notify._import_win32 = importer or (lambda: fake_modules)
    if backend is not None:
        notify._new_backend = lambda: backend
    config.load = lambda: {"os_toasts": setting}
    os.environ.pop(notify.ENV_KILL, None)

    def restore():
        notify._import_win32 = saved["_import_win32"]
        notify._new_backend = saved["_new_backend"]
        config.load = saved["config_load"]
        if saved["env"] is None:
            os.environ.pop(notify.ENV_KILL, None)
        else:
            os.environ[notify.ENV_KILL] = saved["env"]
        notify._reset_for_tests()

    return restore


def test_importer_missing_is_unavailable():
    # The machine (or build) without pywin32: available() is False, show() is False, and
    # the failure is memoised so the import is attempted exactly once per process.
    attempts = []

    def boom():
        attempts.append(1)
        raise ImportError("No module named 'win32gui'")

    restore = _install(importer=boom, backend=FakeBackend())
    try:
        assert notify.available() is False, "available() True without pywin32"
        assert notify.show("t", "b") is False, "show() True without pywin32"
        assert notify.available() is False
        assert len(attempts) == 1, f"import probe was not memoised: {len(attempts)} attempts"
    finally:
        restore()
    print("  OK  no pywin32: available() False, show() False, import probed once")


def test_env_kill_switch_short_circuits_before_import():
    # SA_LIVE_TOASTS=0 is the hard kill: it must be checked BEFORE pywin32 is touched, so
    # setting it also protects a machine where importing pywin32 would be slow or unhappy.
    attempts = []

    def tripwire():
        attempts.append(1)
        raise AssertionError("pywin32 was imported despite SA_LIVE_TOASTS=0")

    backend = FakeBackend()
    restore = _install(importer=tripwire, backend=backend)
    try:
        os.environ[notify.ENV_KILL] = "0"
        assert notify.available() is False, "SA_LIVE_TOASTS=0 did not disable notifications"
        assert notify.show("t", "b") is False
        assert attempts == [], "the env kill switch did not short-circuit the import"
        assert backend.calls == [], f"a toast got through the env kill switch: {backend.calls}"
    finally:
        restore()
    print("  OK  SA_LIVE_TOASTS=0: off, and off before pywin32 is imported")


def test_setting_off_short_circuits():
    # The os_toasts setting is read fresh on every toast (so the Settings toggle takes
    # effect without a restart) and no toast reaches the backend when it is off.
    backend = FakeBackend()
    restore = _install(backend=backend, setting=False)
    try:
        assert notify.show("t", "b") is False, "a toast was shown with os_toasts off"
        assert backend.calls == [], f"backend called with os_toasts off: {backend.calls}"
        config.load = lambda: {"os_toasts": True}      # the user flips it back on
        assert notify.show("t", "b") is True, "flipping os_toasts back on did not take effect"
        assert len(backend.calls) == 1, backend.calls
    finally:
        restore()
    print("  OK  os_toasts off: no toast, no backend call; back on: takes effect immediately")


def test_non_windows_is_unavailable():
    saved_platform = notify.sys.platform
    restore = _install(backend=FakeBackend())
    try:
        notify.sys.platform = "linux"
        assert notify.available() is False, "available() True on a non-Windows platform"
        assert notify.show("t", "b") is False
    finally:
        notify.sys.platform = saved_platform
        restore()
    print("  OK  non-Windows: available() False")


def test_backend_receives_title_body_and_tag():
    backend = FakeBackend()
    restore = _install(backend=backend)
    try:
        assert notify.available() is True, "available() False with a working (stand-in) pywin32"
        clicked = []
        assert notify.show("A meeting is starting", "Board pack review",
                           tag="meeting", on_click=lambda: clicked.append(1)) is True
        assert len(backend.calls) == 1, backend.calls
        call = backend.calls[0]
        assert call["title"] == "A meeting is starting", call
        assert call["body"] == "Board pack review", call
        assert call["tag"] == "meeting", call
        assert callable(call["on_click"]), call
    finally:
        restore()
    print("  OK  show() passes title, body, tag and on_click down to the backend")


def test_tag_coalescing():
    # Same tag + same text = swallowed (a 1 Hz watchdog must not stack sixty balloons).
    # Same tag + new text = shown (it replaces). Different tags = both shown.
    # No tag = never coalesced.
    backend = FakeBackend()
    restore = _install(backend=backend)
    try:
        assert notify.show("Nothing heard", "for 5 minutes", tag="silence") is True
        assert notify.show("Nothing heard", "for 5 minutes", tag="silence") is False, \
            "an identical same-tag toast was not coalesced"
        assert len(backend.calls) == 1, backend.calls
        assert notify.show("Nothing heard", "for 10 minutes", tag="silence") is True, \
            "a same-tag toast with new text was wrongly swallowed"
        assert notify.show("A meeting is starting", "Standup", tag="meeting") is True
        assert notify.show("Plain", "plain", tag=None) is True
        assert notify.show("Plain", "plain", tag=None) is True, "an untagged toast was coalesced"
        assert [c["tag"] for c in backend.calls] == ["silence", "silence", "meeting", None, None], \
            backend.calls
    finally:
        restore()
    print("  OK  tag coalescing: identical same-tag swallowed, new text replaces, untagged never")


def test_backend_failure_never_raises():
    backend = FakeBackend(boom=True)
    restore = _install(backend=backend)
    try:
        assert notify.show("t", "b", tag="x") is False, "a raising backend did not return False"
        assert len(backend.calls) == 1, backend.calls
    finally:
        restore()
    print("  OK  a backend that raises: show() returns False and never propagates")


def test_backend_build_failure_is_memoised():
    # If the window/pump cannot be created we must not try again on every toast, and
    # available() must start telling the truth afterwards (the "init failure" case).
    builds = []

    def wont_build():
        builds.append(1)
        raise RuntimeError("CreateWindow failed")

    restore = _install()
    try:
        notify._new_backend = wont_build
        assert notify.available() is True, "available() should be True before the first build"
        assert notify.show("t", "b") is False
        assert notify.show("t2", "b2") is False
        assert len(builds) == 1, f"backend build retried {len(builds)} times"
        assert notify.available() is False, "available() still True after an init failure"
    finally:
        restore()
    print("  OK  a backend that cannot be built: one attempt, then available() False")


def test_show_survives_junk_input():
    backend = FakeBackend()
    restore = _install(backend=backend)
    try:
        assert notify.show(None) is True, "show(None) did not survive"
        assert notify.show(object(), None) is True
        assert backend.calls[0]["title"] == "" and backend.calls[0]["body"] == "", backend.calls
    finally:
        restore()
    print("  OK  show() coerces junk input instead of raising")


def test_focus_app_is_a_no_op_without_a_hook():
    # desktop.py registers the window; the browser and server-only modes never do, and
    # there is no window to hunt for in those modes.
    restore = _install(backend=FakeBackend())
    try:
        assert notify._window_hook is None
        assert notify.focus_app() is False, "focus_app() did something without a window hook"
    finally:
        restore()
    print("  OK  focus_app() is a no-op (False) with no window hook registered")


def test_focus_app_uses_the_hook():
    gui = FakeGui()
    restore = _install(importer=lambda: (object(), FakeCon(), gui), backend=FakeBackend())
    try:
        notify.set_window_hook(lambda: 4242)         # an int HWND is accepted directly
        assert notify.focus_app() is True, "focus_app() failed with a hook returning an HWND"
        assert gui.foreground == [4242], gui.foreground

        notify.set_window_hook(lambda: None)          # a hook that has nothing yet
        assert notify.focus_app() is False, "focus_app() True with a hook returning None"

        def angry():
            raise RuntimeError("window is gone")
        notify.set_window_hook(angry)
        assert notify.focus_app() is False, "a raising hook was not contained"
    finally:
        restore()
    print("  OK  focus_app() foregrounds the hooked window, and tolerates None or a raising hook")


def test_focus_app_falls_back_to_flashing():
    # Windows refuses SetForegroundWindow to a process without the foreground lock, which
    # is the normal case for a background app. Flashing the taskbar button is the sanctioned
    # consolation prize, and must still count as "we did something".
    gui = FakeGui(foreground_fails=True)
    restore = _install(importer=lambda: (object(), FakeCon(), gui), backend=FakeBackend())
    try:
        notify.set_window_hook(lambda: 77)
        assert notify.focus_app() is True, "focus_app() gave up instead of flashing"
        assert gui.flashed == [77], gui.flashed
    finally:
        restore()
    print("  OK  focus_app() falls back to flashing the taskbar button")


def test_hwnd_reads_a_pywebview_style_window():
    # pywebview's Window keeps the WinForms Form on .native; .Handle is a .NET IntPtr,
    # which pythonnet exposes with ToInt64(). Every step of that is guarded.
    class FakeHandle:
        def ToInt64(self):
            return 909

    class FakeNative:
        Handle = FakeHandle()

    class FakeWindow:
        native = FakeNative()

    restore = _install(backend=FakeBackend())
    try:
        notify.set_window_hook(lambda: FakeWindow())
        assert notify._hwnd() == 909, notify._hwnd()
        notify.set_window_hook(lambda: object())      # a window shaped like nothing we know
        assert notify._hwnd() == 0, "an unrecognised window object did not degrade to 0"
    finally:
        restore()
    print("  OK  the HWND is read off a pywebview-style window, unknown shapes degrade to 0")


def test_the_module_imports_without_pywin32():
    # The whole posture rests on notify.py itself never importing pywin32 at module scope.
    for mod in ("win32gui", "win32api", "win32con", "pythoncom"):
        assert mod not in notify.__dict__, f"notify.py imported {mod} at module scope"
    print("  OK  notify.py holds no module-scope pywin32 import")


if __name__ == "__main__":
    tests = (test_importer_missing_is_unavailable,
             test_env_kill_switch_short_circuits_before_import,
             test_setting_off_short_circuits,
             test_non_windows_is_unavailable,
             test_backend_receives_title_body_and_tag,
             test_tag_coalescing,
             test_backend_failure_never_raises,
             test_backend_build_failure_is_memoised,
             test_show_survives_junk_input,
             test_focus_app_is_a_no_op_without_a_hook,
             test_focus_app_uses_the_hook,
             test_focus_app_falls_back_to_flashing,
             test_hwnd_reads_a_pywebview_style_window,
             test_the_module_imports_without_pywin32)
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    # Direct-run guard: this file must be provably runnable on a machine with no pywin32.
    leaked = [m for m in ("win32gui", "win32api", "win32con") if m in sys.modules]
    if leaked:
        failures += 1
        print(f"  FAIL  these tests imported pywin32: {leaked}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll notify tests passed.")
