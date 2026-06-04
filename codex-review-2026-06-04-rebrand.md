# Codex review prompt, v1.0.3 reliability + brand-mark refresh (2026-06-04)

Run from the project root. The focused code diff is piped on stdin. Paste both this
prompt and Codex's verbatim findings into the session log.

---

You're doing an independent, cold code review of UNCOMMITTED changes to Volksmond
(a.k.a. SA-Live-Transcribe), a local-only Windows desktop app: Python + FastAPI +
pywebview + WebView2, plus a static early-access landing page. You have not seen the
conversation that produced these changes. The focused code diff is on stdin; you may
also read any file in the repo (read-only) for context.

Two threads are in this diff:

1. v1.0.3 desktop reliability (the pywebview hang fix was already reviewed; re-check the rest):
   - `live_transcribe/capture.py`: `_open_stream` now tries a fallback list of channel
     counts `[maxInputChannels, 2, 1]` (deduped, in range) and uses the first that opens;
     adds per-source error messages. Background: a Realtek WASAPI loopback reports
     maxInputChannels=8 but only opens at ch=2; mono also fails; only stereo opens.
   - `live_transcribe/web/app.py` `/api/devices`: mic list filtered to WASAPI-only and
     deduped; `_fix_name()` undoes latin-1-as-UTF-8 mangling of device names; the
     system-default mic is remapped to its WASAPI twin so the default highlight survives.
   - `live_transcribe/desktop.py`: `self.window` -> `self._window` (recursion fix, context only).
   - `live_transcribe/web/static/app.js`: a `save_location` first-run stage.
   - `tests/test_desktop_api.py`, `probe-loopback.py`, `sa-live-transcribe.spec` (icon=).

2. Brand-mark refresh (swap the old speaker mark for the new Volksmond mark everywhere):
   - `live_transcribe/web/static/app.js` `markSvg()`: new inline SVG, strokes on currentColor.
   - `build-icon.py`: rewritten to composite `brand/volksmond-mark-white.png` onto a
     brand-blue rounded tile and export multi-size `volksmond.ico`.
   - `live_transcribe/web/static/index.html`: added a favicon link to `/assets/favicon.svg`.
   - `landing/build/assemble.py` + `validate.py`: the landing page is assembled from a
     design bundle into one self-contained file; these now inject the new mark, and the
     build collapses a 3-logo JS picker to the single mark. (The generated
     `Volksmond - Landing Page.html` is not in this diff.)

Review for CORRECTNESS and BUGS, not style. Specifically:
- capture.py: is the channel-fallback loop correct? Any way it leaks the wrong channel
  count into the audio callback, opens a working-but-wrong device, or breaks normal stereo?
- app.py: can the WASAPI-only filter or default-remap drop the user's real mic, return an
  empty list, or mismatch the device index the frontend later sends to `/api/start`? Is
  `_fix_name` idempotent (no mojibake on already-correct UTF-8)?
- app.js `markSvg`: is the new SVG well-formed for innerHTML? Any broken caller?
- assemble.py: is the regex that collapses the LOGOS object safe (single, non-catastrophic
  replacement)? Does setLogo still resolve a stale localStorage value to the new mark? Any
  path where the build silently ships the OLD mark?
- Anything that breaks the offline / no-phone-home guarantee, or POPIA (no recorded content
  committed to git).

Answer in under 400 words. Separate BLOCKERS (must fix before commit) from NITS (optional).
If nothing is blocking, say so explicitly.
