"""Regression test for the v1.0.0 "Volksmond (Not Responding)" bug.

The bug: pywebview exposes our JS-API object to the page via `window.pywebview.api.*`.
To know which attributes to expose, `webview.util.get_functions` recursively walks
every PUBLIC attribute of the api object. Methods get exposed; non-callable
attributes are treated as nested namespaces and recursed into.

If we store a pywebview Window (or any pythonnet-wrapped .NET object) as a public
attribute on the api, the walker descends Window -> .native (the WinForms Form) ->
.AccessibilityObject.Bounds.Empty.Empty.Empty.... because pythonnet returns a fresh
Python wrapper for every access of the static `Rectangle.Empty` field, so the
walker's id()-based cycle-guard never trips. The window hangs at launch.

The fix: keep the Window reference private (underscore-prefixed). The walker skips
underscore names. Three tests guard that:

  1. test_no_public_non_callable_attrs -- a fresh `DesktopApi()` has no public
     non-callable attribute. Catches a re-introduced `self.foo = something` in
     `__init__`.
  2. test_window_holder_is_private -- explicit: `api.window` must not exist;
     `api._window` must. Catches the literal v1.0.0 mistake by name.
  3. test_no_public_attribute_assignments_in_source -- static scan of
     `live_transcribe/desktop.py`: every `api.X =` and `self.X =` assignment
     must use a `_`-prefixed name. Catches the "future main() adds a public
     attribute after construction" scenario the runtime tests miss (codex
     review, 2026-06-04 nit 1).

Run:  python tests/test_desktop_api.py   (from the project root; exit 0 = pass)
"""
import inspect
import os
import re
import sys

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe.desktop import DesktopApi
import live_transcribe.desktop as desktop_mod


def test_no_public_non_callable_attrs():
    api = DesktopApi()
    public = [n for n in dir(api) if not n.startswith("_")]
    bad = []
    for name in public:
        attr = getattr(api, name)
        if not (inspect.ismethod(attr) or inspect.isfunction(attr)):
            bad.append((name, type(attr).__name__))
    assert not bad, (
        "DesktopApi has public non-callable attribute(s): "
        f"{bad}. pywebview's JS-API exposer would recurse into them. "
        "Make them underscore-prefixed (e.g. self._window) so the walker skips them. "
        "See CHANGELOG 2026-06-04 + LAPTOP-FIX-2026-06-04.md for context."
    )


def test_window_holder_is_private():
    """Explicit: the pywebview Window reference must live on `_window`, not `window`."""
    api = DesktopApi()
    assert not hasattr(api, "window"), (
        "DesktopApi.window is public; pywebview's JS-API exposer will recurse into "
        "it and hit the AccessibilityObject.Bounds.Empty.... recursion (v1.0.0 hang). "
        "Rename to `_window`."
    )
    assert hasattr(api, "_window"), "DesktopApi._window is missing"


def test_no_public_attribute_assignments_in_source():
    """Static-scan desktop.py for `api.X =` / `self.X =` assignments and assert
    every target is `_`-prefixed. The runtime tests above only inspect a fresh
    DesktopApi(); they miss assignments performed AFTER construction (e.g. in
    main()). This catches that path. Codex review nit, 2026-06-04."""
    src = inspect.getsource(desktop_mod)
    bad = []
    # Match `api.NAME =` or `self.NAME =` (but not `==`). Skip `_`-prefixed names.
    pat = re.compile(r"(?:^|[\s(\[])(api|self)\.([A-Za-z]\w*)\s*=(?!=)", re.MULTILINE)
    for m in pat.finditer(src):
        target = m.group(2)
        if target.startswith("_"):
            continue
        line_no = src.count("\n", 0, m.start()) + 1
        bad.append((m.group(1), target, line_no))
    assert not bad, (
        f"Found public attribute assignment(s) in live_transcribe/desktop.py: "
        f"{bad}. pywebview's JS-API exposer recurses into any non-callable public "
        "attribute on the js_api object (v1.0.0 hang). Underscore-prefix the "
        "target (e.g. `api._foo = ...`)."
    )


def _finalise_case(running, stopping, stop_impl, timeout=0.4):
    """Drive desktop.finalise_open_session against a simulated session state.

    Returns (status, stop_calls). `stop_impl` replaces the /api/stop handler, so no real
    session, audio, model or HTTP request is involved - and pywebview is never imported
    (the close logic is deliberately factored out of the event handler for exactly that
    reason). The real STATE is saved and restored."""
    from live_transcribe.web import app as webapp
    st = webapp.STATE
    calls = []
    saved = (st.running, st.stopping, st.source_kind, st.session_counted)
    orig_stop = webapp.stop
    try:
        def _stop(what="all"):
            calls.append(what)
            return stop_impl(st)
        webapp.stop = _stop
        st.running, st.stopping, st.source_kind = running, stopping, "live"
        status = desktop_mod.finalise_open_session(timeout=timeout, poll=0.02)
        return status, calls
    finally:
        webapp.stop = orig_stop
        (st.running, st.stopping, st.source_kind, st.session_counted) = saved


def test_close_no_session_is_a_fast_no_op():
    """Nothing running: the close must not call stop and must return immediately."""
    import time
    from live_transcribe.web import app as _warm   # noqa: F401  (import cost is not close cost)
    t0 = time.monotonic()
    status, calls = _finalise_case(running=False, stopping=False, stop_impl=lambda st: None)
    elapsed = time.monotonic() - t0
    assert status == "idle", status
    assert calls == [], f"stop called with no session running: {calls}"
    assert elapsed < 0.3, f"the no-session close path waited {elapsed:.2f}s"


def test_close_finalises_a_running_session():
    """A running session is stopped through the app's own stop path, and the close waits
    for the session to actually finish (that is what makes the files and the session count
    land the same way "Stop and save" does)."""
    def _stop(st):
        st.running = False        # what the real drain thread does via STATE.reset()
    status, calls = _finalise_case(running=True, stopping=False, stop_impl=_stop)
    assert calls == ["all"], f"close must finalise the whole session, got {calls}"
    assert status == "finalised", status


def test_close_does_not_restart_a_stop_already_in_flight():
    """A UI stop already draining (STATE.stopping) finalises on its own: the close must not
    fire a second stop at it, just wait (bounded) for it to finish."""
    status, calls = _finalise_case(running=True, stopping=True, stop_impl=lambda st: None)
    assert calls == [], f"close fired a second stop at an in-flight one: {calls}"
    assert status == "timeout", status     # still 'running' in this simulation, so it times out


def test_close_is_bounded_when_the_stop_hangs():
    """The regression that matters most: a wedged stop/drain must never make the window
    unclosable. The stop here blocks forever; the close must still return, in about the
    timeout, reporting that atexit will finish the job."""
    import threading
    import time
    release = threading.Event()
    t0 = time.monotonic()
    try:
        status, calls = _finalise_case(running=True, stopping=False,
                                       stop_impl=lambda st: release.wait(30), timeout=0.4)
    finally:
        release.set()
    elapsed = time.monotonic() - t0
    assert calls == ["all"], calls
    assert status == "timeout", status
    assert elapsed < 3.0, f"a hung stop blocked the close for {elapsed:.2f}s"


def test_closing_handler_never_vetoes_the_close():
    """pywebview cancels the close if a `closing` handler returns False, so the handler must
    return a non-False value even when finalisation blows up, and must take no arguments
    (pywebview inspects the signature)."""
    import inspect as _inspect
    orig = desktop_mod.finalise_open_session
    try:
        def _boom(**kw):
            raise RuntimeError("finalisation exploded")
        desktop_mod.finalise_open_session = _boom
        assert desktop_mod._on_closing() is not False, "the close handler vetoed the close"
    finally:
        desktop_mod.finalise_open_session = orig
    assert list(_inspect.signature(desktop_mod._on_closing).parameters) == [], \
        "pywebview calls a 0-parameter closing handler with no arguments"


if __name__ == "__main__":
    test_no_public_non_callable_attrs()
    test_window_holder_is_private()
    test_no_public_attribute_assignments_in_source()
    test_close_no_session_is_a_fast_no_op()
    test_close_finalises_a_running_session()
    test_close_does_not_restart_a_stop_already_in_flight()
    test_close_is_bounded_when_the_stop_hangs()
    test_closing_handler_never_vetoes_the_close()
    print("OK: DesktopApi exposes only methods to pywebview's JS-API walker; "
          "window close finalises the session (bounded, idempotent).")
