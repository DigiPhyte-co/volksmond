# Volksmond v1.0.0 zip, hangs on laptop (pywebview recursion)

Written 2026-06-04 from the laptop (Sean was on the home PC for the v1.0.0 ship).
This file is in OneDrive and will sync to the PC, so the next session there sees it too.

## What Sean saw

Unzipped `Volksmond.zip` into `D:\OneDrive\OneDrive - Freimond Trust\Cowork Personal\Volksmond\`
on the laptop and ran `Volksmond.exe`. The window appears, renders the **Get started**
first-run screen, then the title bar flips to **"Volksmond (Not Responding)"** and the UI
freezes. The console window spams thousands of identical errors:

```
[pywebview] Error while processing window.native.AccessibilityObject.Bounds.Empty.Empty.Empty.Empty....: maximum recursion depth exceeded
[pywebview] Error while processing window.native.AccessibilityObject.Bounds.Empty.Empty.Empty.Empty....Location: maximum recursion depth exceeded
[pywebview] Error while processing window.native.AccessibilityObject.Bounds.Empty.Empty.Empty.Empty....Size: maximum recursion depth exceeded
...
```

Same on every paint/resize event. UI thread is stuck in the logging loop, so the
window paints but never responds.

## Root cause (high confidence, found by reading the bundled pywebview source)

**It's our `desktop.py`, not a pywebview Windows bug.** And it's not Win11-specific:
this laptop is **Windows 10 22H2 (build 19045)**, and the bug fires here too (the
home PC built the zip on Win11 26200 but that's incidental).

In `_internal/webview/util.py:180`, pywebview defines `get_functions`, a recursive
walker over the JS-API object. For each public attribute on the API instance it
calls `getattr`, and if the result is a non-callable object it recurses, appending
the attribute name to a dotted path. It has a cycle-guard (`id(obj) in
exposed_objects`) and skips names starting with `_`, but NO max depth.

`live_transcribe/desktop.py` set `api.window = window` (a pywebview `Window`
object). The walker recursed into Window -> `.native` (the WinForms Form) ->
`.AccessibilityObject` -> `.Bounds` (a .NET `Rectangle`) -> `.Empty`.
`Rectangle.Empty` is a .NET STATIC field; **pythonnet exposes statics as instance
attributes too** and returns a fresh Python wrapper each access, so
`id(Rectangle.Empty) != id(Rectangle.Empty)` and the cycle-guard never trips. The
walker descends `.Empty.Empty.Empty....` until Python's recursion limit, logs the
failure on every branch, and does this for every dir() entry of every parent
(`.Location`, `.Size`, `.Empty`, ...) -> tens of thousands of log lines, blocking
the GUI thread. Hence "Not Responding".

The WebView2 render is fine; the page loads (you saw "Get started"). Only the
JS-API exposer is in the recursion loop.

**The fix is a one-character change in our code.** The walker skips `_`-prefixed
attrs. Renaming `self.window` -> `self._window` (and the assignment) makes the
walker leave it alone. No pywebview downgrade needed.

## Immediate workaround, no rebuild required

`Volksmond.exe` already supports a `--browser` flag (per `live_transcribe/desktop.py`):
it starts the local server and opens the UI in the default browser instead of the
native pywebview window. This sidesteps the buggy WinForms wrapper entirely.

Two ways to use it on the laptop right now:

1. **`Volksmond - Browser Mode.bat`** dropped next to the extracted exe (this session
   creates it at `D:\OneDrive\OneDrive - Freimond Trust\Cowork Personal\Volksmond\`).
   Double-click it instead of `Volksmond.exe`. Opens the app in your default browser
   on `http://127.0.0.1:8765`; close the console window to stop it.
2. From a terminal in the same folder: `.\Volksmond.exe --browser`.

Functional-wise this is the same app, same offline guarantee, same data dir
(`%LOCALAPPDATA%\sa-live-transcribe\`). It's a browser tab instead of a native
window. Use it for the v1.0 video test until the rebuild lands.

## Real fix (v1.0.1, needs rebuild)

A one-character change in `live_transcribe/desktop.py`: rename the JS-API instance
attribute that holds the pywebview Window from `self.window` to `self._window`
(and the assignment in `main()` from `api.window = window` to
`api._window = window`). The walker at `webview/util.py:193` skips
underscore-prefixed names, so the recursion never starts.

`requirements.txt` stays unchanged (still `pywebview==6.2.1`). No backend swap, no
monkey-patches.

Roll-out:

- Patch `desktop.py` (done in this session; see git diff).
- Rerun `build-app.ps1` from the laptop. It writes to
  `%LOCALAPPDATA%\sa-live-transcribe\app-build\dist\Volksmond\` and
  `...\Volksmond.zip`. Copy the new zip into OneDrive (overwriting the project-root
  `Volksmond.zip`).
- Smoke-test the new exe end-to-end on the laptop (Win10 19045): native window
  should be immediately responsive, console should be silent.
- Bump `APP_VERSION` in `live_transcribe/licensing.py` to `1.0.1`.
- CHANGELOG entry.
- Commit on `volksmond-ui-rebuild`, tag `v1.0.1`, push.
- Re-attach the new zip + (unchanged) two PDFs to the GitHub Release once that
  step happens (per HANDOFF-2026-06-03).

## Status

- [x] Killed the two hung `Volksmond.exe` processes on the laptop.
- [x] Diagnosed root cause from the bundled `webview/util.py`: it's our
      `api.window = window` line, not a pywebview platform bug.
- [x] Dropped `Volksmond - Browser Mode.bat` next to the extracted exe (workaround
      while a rebuild is pending). Server-only smoke test confirmed the rest of
      the stack runs fine, only the WinForms-side JS-API exposer was looping.
- [x] Patched `live_transcribe/desktop.py` (`self.window` -> `self._window`,
      private under `_` so the walker skips it).
- [x] Installed `pywebview==6.2.1` in the laptop venv; ran
      `python -m live_transcribe.desktop` from source. Result: server up on
      `:8765`, `smoketest-desktop-source.log` **empty** (zero recursion log
      lines; before the patch this produced thousands per second). JS-API exposer
      is no longer descending into the .NET Form.
- [x] Bumped `APP_VERSION` to `1.0.1`. CHANGELOG entry added.
- [x] Installed `llama-cpp-python==0.3.23` (CPU wheel) and `pyinstaller==6.20.0`
      on the laptop venv (build deps; setup.ps1 doesn't install these by default,
      they were only on the home PC where v1.0.0 was built).
- [x] Added `tests/test_desktop_api.py` -- regression that asserts every public
      attribute on `DesktopApi()` is a callable, and that `api.window` doesn't
      exist. All 4 test suites green (`test_desktop_api`, `test_engine_drain`,
      `test_web_api`, `test_dedup`).
- [x] Rebuilt zip via `build-app.ps1`. Output:
      `%LOCALAPPDATA%\sa-live-transcribe\app-build\Volksmond.zip` (138 MB).
      Copied over `Volksmond.zip` in the OneDrive project root.
- [x] Smoke-tested the rebuilt exe: extracted to a temp folder, ran
      `Volksmond.exe` (full native window mode). Server up on `:8765` in ~5s,
      `/api/app-info` returned `"version":"1.0.1"`, output log **empty** (zero
      recursion lines). Killed + cleaned up.
- [ ] Sean: commit + tag `v1.0.1` + push (his workflow per HANDOFF-2026-06-03 is
      to commit manually; this session has not committed anything on his behalf).
- [ ] Sean (optional): re-extract the OneDrive zip into
      `D:\OneDrive\OneDrive - Freimond Trust\Cowork Personal\Volksmond\`,
      overwriting the v1.0.0 exe. The `Volksmond - Browser Mode.bat` workaround
      remains in place but is no longer needed.
- [ ] Update `HANDOFF-2026-06-03.md` if/when v1.0.1 ships publicly.
- [x] **Codex CLI review.** Codex isn't installed on the laptop; Sean ran it
      elsewhere with the prompt at `codex-review-prompt-2026-06-04.md`. Result:
      **"Findings: no blocking issues."** Codex confirmed (1) the diagnosis is
      sound, (2) the fix is complete for the current surface (only `_window` is
      stored on `api`, no other `js_api=` targets in the repo, regression test
      green), (3) breakage risk is low (`app.js` only references
      `pywebview.api.pick_path` and `.open_external`, no `api.window.*`
      dependency). Two non-blocking nits, both addressed below.
- [x] **Codex nit 1 (test gap).** The runtime test only inspected a fresh
      `DesktopApi()`; it would not catch a future `api.foo = SomeObject` added
      to `main()`. Added a third test in `tests/test_desktop_api.py`:
      `test_no_public_attribute_assignments_in_source` -- a static regex scan of
      `live_transcribe/desktop.py` that fails if any `api.X =` or `self.X =`
      target is not `_`-prefixed. All 3 cases pass.
- [x] **Codex nit 2 (mojibake).** Stripped the Unicode ellipses/em-dashes I had
      introduced into `CHANGELOG.md` (the v1.0.1 entry only -- older entries left
      untouched per the surgical-changes rule), `LAPTOP-FIX-2026-06-04.md`,
      `codex-review-prompt-2026-06-04.md`, and `tests/test_desktop_api.py`.
      Substitutions: `...` for ellipsis, `--` for em-dash. Verified with grep:
      zero remaining mojibake in files I authored.

## v1.0.2 (later in the same session)

Sean's first end-to-end test of v1.0.1 surfaced two non-hang issues. He approved a
follow-up rebuild.

- **Issue A: unclear audio capture error.** "Could not start audio capture:
  [Errno -9996] Invalid device" with no indication of which device failed. Root
  cause: laptop default loopback is `Headphones (Realtek(R) Audio) [Loopback]`
  which enumerates but is not openable when no headphones are physically the
  active endpoint. The fix is to pick `Speakers (Realtek(R) Audio) [Loopback]`
  from the same dropdown -- but the error gave no hint of that.
- **Issue B: first-run never asked where to save.** Setup flow was
  `welcome -> summaries -> done`. Default save folder
  (`%LOCALAPPDATA%\sa-live-transcribe\sessions`) was hidden in Settings.

### What I did

- `live_transcribe/capture.py`: each `_open_stream` call now wrapped; the raised
  `RuntimeError` carries the source label (`system audio` / `microphone`),
  device index, device name, the underlying PyAudio message, and a concrete
  remediation. FastAPI surfaces it via the existing
  `Could not start audio capture: <msg>` 500 detail at `web/app.py:398`.
- `live_transcribe/web/static/app.js`: added a `save_location` stage to
  `setupView()` between `welcome` and `summaries`. Shows the current default
  folder, "Choose another folder" runs the existing `pickFile("folder")`
  helper and persists via `/api/settings`. "Continue" with no override keeps
  the default. Same style/eyebrow pattern as the existing `summaries` stage.
- `live_transcribe/licensing.py`: `APP_VERSION` `1.0.1` -> `1.0.2`.
- `CHANGELOG.md`: v1.0.2 entry at the top.
- All 4 test suites still green; `node --check` clean on `app.js`.
- Rebuilt zip; smoke-test will appear here once the build finishes.

### Smoke test result

- [x] Replaced `Volksmond.zip` in OneDrive (138 MB).
- [x] Smoke-tested extracted v1.0.2 in `--server-only` mode: server bound
      cleanly, `/api/app-info` returns `"version":"1.0.2"`, output log empty
      (zero recursion lines). Killed and cleaned up.

## v1.0.3 (continued in the same session)

Sean's v1.0.2 retest landed the path-selection step (he liked it) and the new
error message worked. But every capture attempt still failed with `-9996
Invalid device` on the system audio, even after switching to Speakers
loopback. Source-mode reproduced 1:1, ruling out PyInstaller.

### Diagnosis

Wrote `probe-loopback.py` -- iterates every loopback device with a matrix of
`(format, rate, channels)` and reports which combinations open. Found:

- `ch=2` opens, `ch=1` and `ch=8` both fail with `-9996`.
- `rate=44100` fails with `-9997 Invalid sample rate`; only `rate=48000`
  (the device's native) works.
- Both formats (`paFloat32`, `paInt16`) behave identically per the channel
  test, so format isn't the variable.

Realtek's WASAPI driver on this laptop reports `maxInputChannels=8`
(surround-capable) but only accepts opens at the current mix format
(stereo). Old `capture.py` used `maxInputChannels` directly -> `ch=8` ->
fail. The home PC build worked because its loopback's `maxInputChannels`
was already `2`.

### Fix

`_open_stream` in `capture.py` now iterates a fallback list
`[max_ch, 2, 1]` (deduped, in range) and accepts the first combination
that opens. Callback binds the winning channel count via default args so
a failed earlier attempt cannot leak state.

### Other v1.0.3 changes batched in

- `web/app.py` `/api/devices`: WASAPI-only mic filter (8 entries -> 2);
  `_fix_name()` undoes latin-1-as-UTF-8 mangling of `Intel(R)`-style
  characters in PyAudio device names; system-default mic remapped to its
  WASAPI twin so the dropdown's default highlight survives the filter.
- `volksmond.ico` (new): rounded-tile rendering of the inline SVG mark
  from `app.js`, Clinical-palette accent blue, multi-resolution
  (16 / 24 / 32 / 48 / 64 / 128 / 256). Built by `build-icon.py` (new).
  Wired into `sa-live-transcribe.spec` via `EXE(..., icon=...)`.
- `probe-loopback.py` (new): kept in the tree as the diagnostic tool for
  future driver-quirk hunts.
- `licensing.py`: `APP_VERSION` `1.0.2` -> `1.0.3`.

### Verification

- All 4 test suites green after the capture-fallback edit.
- Sean ran source-mode after the capture.py change: Begin succeeded,
  "Listening" UI rendered, file saved as `2026-06-04-141636-test.md`.
  Reproduces the bug fix end-to-end without packaging in the loop.
- Smoke-test of the rebuilt exe pending build completion this session.

### Open

- [x] Replaced `Volksmond.zip` in OneDrive (clean-cache PyInstaller rebuild,
      145 MB).
- [x] Built exe verified: `version: 1.0.3`, icon embedded (32x32 extracted
      via `System.Drawing.Icon`), mic dropdown deduped to 2 entries, recursion
      count 0.
- [ ] Sean: re-extract to retest. Walk through first-run (now 3 stages incl.
      save location), confirm the icon shows in title bar / taskbar, confirm
      Begin actually transcribes audio with Speakers loopback.
- [ ] Sean: commit v1.0.3 + tag + push (manual workflow).

### One known cosmetic issue, deferred

One mic name (Intel SST) still renders as `IntelÂ®` in the built exe's
dropdown rather than `Intel®`. The `_fix_name` is in the bundled bytecode
and works correctly on the problem string when invoked manually against an
extracted .pyc, but the FastAPI route at runtime in the built exe is for
reasons unclear NOT transforming the names. Source-mode works fine. Did not
chase further this session because the dropdown is still usable and the
capture bug (the actual blocker) is fixed. A v1.0.4 response-middleware
fix would do the latin-1 -> UTF-8 transform at JSON serialisation regardless
of what `devices_list` returns.

## Files touched this session

Project source (will sync to home PC via OneDrive):

- `live_transcribe/desktop.py` -- the actual fix (`self.window` -> `self._window`,
  with a docstring note explaining why).
- `live_transcribe/licensing.py` -- `APP_VERSION` `1.0.0` -> `1.0.1`.
- `tests/test_desktop_api.py` (new) -- regression test, runs as
  `python tests/test_desktop_api.py` (exit 0 = pass).
- `CHANGELOG.md` -- v1.0.1 entry at the top.
- `Volksmond.zip` -- rebuilt, 138 MB; replaces the v1.0.0 zip in the OneDrive root.
- `LAPTOP-FIX-2026-06-04.md` -- this file.

NOT in OneDrive (laptop-local, won't sync):

- `Volksmond - Browser Mode.bat` -- at
  `D:\OneDrive\OneDrive - Freimond Trust\Cowork Personal\Volksmond\`
  (Sean's personal OneDrive, will sync to PC). Workaround for v1.0.0; harmless
  after the v1.0.1 zip is extracted.
- pywebview, llama-cpp-python, pyinstaller -- pip-installed in the laptop venv
  (`%LOCALAPPDATA%\sa-live-transcribe\.venv`) for the build. Not part of any
  shipped artifact.

(Updated below as each step lands.)
