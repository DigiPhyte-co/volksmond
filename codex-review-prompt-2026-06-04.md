# Codex review prompt -- Volksmond v1.0.1 hang fix

Paste this into codex CLI (e.g. `codex exec` or interactive) from the project root
`D:\OneDrive - DigiPhyte\Cowork\SA-Live-Transcribe\`. Independent second opinion
on the diagnosis and the fix.

---

You're being asked for an independent code review. You haven't seen the
conversation that produced this change -- read the files cold and judge them.

**Background.** This is SA-Live-Transcribe / Volksmond, a Windows desktop app
(Python + FastAPI + pywebview + WebView2) that transcribes meetings locally on
the user's machine. v1.0.0 shipped as a PyInstaller one-folder zip a day ago.

**The bug Sean saw on first launch of v1.0.0.** The Volksmond window opens, renders
the "Get started" screen, then the title bar flips to "Volksmond (Not Responding)".
The console spams thousands of identical lines per second:

```
[pywebview] Error while processing window.native.AccessibilityObject.Bounds.Empty.Empty.Empty.... : maximum recursion depth exceeded
```

...with the `.Empty.Empty.Empty....` chain growing into the thousands of segments,
and variants ending in `.Location`, `.Size`, `.Location.Empty`, etc.

**Claimed root cause (verify or refute).** pywebview's JS-API exposer at
`_internal/webview/util.py:180` (function `get_functions` inside `inject_pywebview`)
recursively walks every public attribute of the js_api object to surface its
methods as `window.pywebview.api.*` in the page. Skip list: only names starting
with `_`. Cycle-guard: `id(obj) in exposed_objects` (a visited set).

`live_transcribe/desktop.py` had `self.window = window` on `DesktopApi`, where
`window` is the pywebview Window. The walker descends:

- `DesktopApi.window` -> pywebview Window
- `Window.native` -> a .NET `System.Windows.Forms.Form` (via pythonnet)
- `Form.AccessibilityObject` -> a `Control.ControlAccessibleObject`
- `AccessibilityObject.Bounds` -> a `System.Drawing.Rectangle`
- `Rectangle.Empty` -> a .NET STATIC field; pythonnet exposes statics as
  instance attributes AND returns a fresh Python wrapper each access, so
  `id(Rectangle.Empty) != id(Rectangle.Empty)` and the cycle-guard never trips.
- `.Empty.Empty.Empty....` recurses until Python's recursion limit.

Each parent's dir() iteration then walks the next sibling (`.Location`, `.Size`,
...) and recurses again. The GUI thread spends all its time in this loop -> "Not
Responding".

**Fix applied.** Renamed the single offending attribute in `live_transcribe/desktop.py`
from `self.window` to `self._window` (and the assignment from `api.window = window`
to `api._window = window`). The walker skips `_`-prefixed names at `util.py:193`,
so the recursion never starts. `requirements.txt` is unchanged; pywebview pin
stays at `==6.2.1`. Added a regression test at `tests/test_desktop_api.py`. Bumped
`APP_VERSION` in `live_transcribe/licensing.py` to `1.0.1`. CHANGELOG entry at the
top of `CHANGELOG.md` has the full write-up.

**Verification done.** Ran `python -m live_transcribe.desktop` from source after
the patch -- server up cleanly, stdout/stderr empty (zero "Error while processing"
lines vs. thousands per second before). Rebuilt the PyInstaller zip; extracted,
ran the new `Volksmond.exe` -- same result, `/api/app-info` reports `version:
1.0.1`, log empty.

**Please answer in under 400 words:**

1. **Diagnosis sound?** Read `_internal/webview/util.py` lines 168-220 (function
   `inject_pywebview` / `get_functions`) -- does the recursion explanation hold?
   If pywebview is *unzipped* under `_internal/`, look there; otherwise check the
   pip-installed copy in `%LOCALAPPDATA%\sa-live-transcribe\.venv\Lib\site-packages\webview\util.py`.
2. **Fix complete?** Read `live_transcribe/desktop.py`. Does any OTHER public
   attribute on `DesktopApi` (or any other class passed as `js_api=` to
   `webview.create_window`) hold a non-callable, non-private object? Is the
   regression test sufficient?
3. **Breakage risk?** Does any JavaScript in `live_transcribe/web/static/app.js`
   (or anywhere else) expect to reach `window.pywebview.api.window.*`? If yes,
   the rename breaks it.
4. **Nits vs. real concerns** -- keep them separate. Mark anything you'd block on
   vs. anything you'd just mention.

Key files:
- `live_transcribe/desktop.py`
- `tests/test_desktop_api.py`
- `live_transcribe/web/static/app.js` (search for `pywebview` references)
- `CHANGELOG.md` (top entry)
- `_internal/webview/util.py` if pywebview's bundled source is present under the
  extracted zip; otherwise the venv-installed copy
