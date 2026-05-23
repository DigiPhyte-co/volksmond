# Changelog, SA-Live-Transcribe

## 2026-05-23, Volksmond UI rebuild + summaries made free

**What:** Replaced the basic start/stop web page with the full Volksmond interface
(from the Claude Design handoff), rebuilt as a vanilla-JS single-page app wired to
every real endpoint. Default look is the "clinical" palette, light, document
transcript layout. Offline-first: no CDN, no web fonts, no framework (system font
stack, hand-drawn inline SVG icons), so it still works with no internet.

**Decision (please confirm):** local AI summaries moved from Pro to **Free**. The
design's "Pro principle" is that Pro covers only what needs an online connection
(calendar attendee seeding, optional cloud fallbacks); anything that runs on this
machine stays free. Practical bonus: summaries were previously unreachable by
anyone, since no licence public key ships yet, so this is also what makes them
usable at all. Revert if the pricing should differ.

### Changed files

- **`live_transcribe/licensing.py`**: `PRO_FEATURES` is now `{"calendar"}` (was
  diarise/calendar/exports/ai_summary/vocab_library/history_search). Everything
  local is free by design.
- **`live_transcribe/web/app.py`**:
  - `/api/summarise` no longer Pro-gated (local, so free). The 409 "finish the
    session first" guard and the path-traversal checks stay.
  - New `GET /api/app-info` (name, version, OS string, save dir) for the footer and
    the bug-report mailto.
  - New `POST /api/pick?kind=file|folder`: opens a native OS dialog (tkinter,
    imported lazily) and returns an absolute path. Right design for a local app
    handling multi-GB media: pick a path on disk rather than upload bytes through
    the browser. UI falls back to a paste-a-path field if no dialog is available.
  - Mounted `StaticFiles` at `/assets` so CSS/JS can be separate, reviewable files.
- **`live_transcribe/web/static/index.html`**: thin shell; loads `/assets/styles.css`
  + `/assets/app.js`; applies the saved theme before paint (no dark-mode flash).
- **`live_transcribe/web/static/styles.css`** (new): Volksmond design tokens (three
  palettes x light/dark) + component primitives + app layout.
- **`live_transcribe/web/static/app.js`** (new): the SPA. Router + screens: first-run
  setup, new-session hub (live / upload file / record-only), pre-meeting start (with
  microphone + system-audio device pickers wired to `/api/start`), live transcript
  (focus mode, SSE, three-way Stop), record-only + "transcribe this now" handoff,
  importing, finish + Summarise, history + reader, settings, upgrade/activation.
  Theme picker (system/light/dark), keyboard shortcuts (Esc, Cmd/Ctrl+Enter to
  begin, Cmd/Ctrl+K search), basic keyboard a11y on clickable cards, privacy-first
  "Report a bug or idea" mailto.
- **`tests/test_web_api.py`** (new): pins the changes. Proves summaries are not
  Pro-gated (bogus file returns 404, not 403), `PRO_FEATURES == {"calendar"}`,
  `/api/app-info` shape, settings never leak the secret key, and `/assets` serve.

### Deliberately NOT built (design showed them, no backend exists; kept honest)

Clean second pass / named-speaker diarisation on the finish screen (only exists
offline via `retranscribe.py`); in-app summary-model download with a progress bar
(no downloader; Settings points at a `.gguf` instead); calendar toggle in the start
flow (CLI + Graph only, shown as a Pro item in copy); true file drag-and-drop (a
browser cannot hand the server a path, so the dropzone is click-to-browse); a real
file-transcription progress percentage (backend reports none, so importing shows an
indeterminate bar plus the live transcript).

### Verification

- `python tests/test_web_api.py` green (5/5); `python tests/test_engine_drain.py`
  still green (3/3).
- `node --check` clean on `app.js`. Server runs; in a headless browser all 11
  screens render with zero console errors, real data flows (8 mics / 2 loopbacks,
  10 sessions, Gemma summary model shown installed), the device picker defaults to
  the real microphone, dark mode applies without flash, and the three-way stop menu
  shows exactly three options when recording.
- Not exercised here (needs real audio): a live transcription, recording, and the
  native `/api/pick` dialog.

## 2026-05-21, Stop now captures the tail (drain-on-stop)

**Problem:** Transcription runs a couple of minutes behind real time. Pressing Stop tore the engine down immediately, discarding every chunk still queued, so the last few minutes of a meeting were lost. (See `IMPROVEMENTS.md` item 1 for the root-cause analysis.)

**Fix:** Stop now keeps transcribing the already-captured backlog before finishing, so nothing up to the moment you press Stop is lost.

### Changed files

- **`live_transcribe/transcribe.py`**, `Engine` drains on stop.
  - `stop(drain=True, timeout=None)` (new signature, backwards-compatible): with `drain=True` (default) it finishes every queued chunk before exiting and blocks until done; `drain=False` is a fast abort that abandons the backlog.
  - `_run()` now loops until the sentinel (or, during shutdown, until the queue is empty) instead of breaking the instant `_stop` is set, that early break was what dropped the backlog.
  - Added `pending()` (queued + in-flight count, for progress) and a `_busy` flag.
  - `on_chunk()` ignores new audio once shutdown has begun (so the backlog is bounded while draining).
  - The 15 s join cap is gone (it could never finish a multi-minute backlog).

- **`live_transcribe/web/app.py`**, `/api/stop` is non-blocking.
  - Flips state to `stopping`, then runs `capture.stop()` → `engine.stop(drain=True)` → `md_sink.close()` on a **background thread**, without holding `STATE.lock` for the (possibly minutes-long) drain. Resets when done.
  - `capture.stop()` runs first so its trailing partial chunk is flushed into the engine queue and then drained (not lost).
  - `md_sink` is closed **after** the drain (was before).
  - `/api/status` now reports `stopping` (bool) and `pending` (chunks left) while draining.

- **`live_transcribe/web/static/index.html`**, Stop UX.
  - Keeps the SSE stream open after Stop so the final segments stream in live.
  - Polls `/api/status` and shows "Finishing transcription… N chunks of audio left" until the drain completes, then "Saved → …". Stop button stays disabled until done.

- **`tests/test_engine_drain.py`** (new), regression test, no model load (monkeypatches `WhisperModel`). Proves: `drain=True` processes the whole backlog; `drain=False` abandons it; `on_chunk` rejects audio during shutdown. Run: `python tests/test_engine_drain.py` from the project root.

### Verification
- Unit test green (3/3).
- Live start→stop smoke test: `/api/stop` returns `{stopping:true}`, `pending` counts down, state resets to `running:false`, and the transcript file gets its "End of session" footer.
- All changed `.py` files `py_compile` clean.

### Behaviour notes
- Stop now takes roughly as long as the current lag to finish, that's expected, it's transcribing the backlog. The UI shows the countdown so it doesn't look frozen.
- The drain join is intentionally unbounded (correctness over speed: never lose audio). In the unlikely event a single chunk's transcription hung, the session would stay in "Finishing…", acceptable trade-off given the goal; revisit only if it ever happens in practice.
- The CLI path (`__main__.py`, Ctrl+C) inherits the same drain-on-stop benefit; double-Ctrl+C still force-exits.
