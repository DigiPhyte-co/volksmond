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


if __name__ == "__main__":
    test_no_public_non_callable_attrs()
    test_window_holder_is_private()
    test_no_public_attribute_assignments_in_source()
    print("OK: DesktopApi exposes only methods to pywebview's JS-API walker.")
