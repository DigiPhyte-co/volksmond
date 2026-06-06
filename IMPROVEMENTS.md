# SA-Live-Transcribe, Improvements & Known Issues

> **Parked notes, not yet implemented.** Captured 2026-05-21. Implement when greenlit.

## 1. [HIGH / data loss] Stop must drain the backlog, not discard it, ✅ DONE (2026-05-21)

**✅ Resolved 2026-05-21.** The engine now drains the queued backlog on stop instead of discarding it; `/api/stop` runs the drain on a background thread with a "Finishing transcription… N left" progress UI; added a regression test. Files: `transcribe.py`, `web/app.py`, `web/static/index.html`, `tests/test_engine_drain.py`. Full detail in `CHANGELOG.md`. Design notes below kept for reference.

**Symptom (Sean):** Transcription runs a couple of minutes behind real-time. Pressing Stop loses the last few minutes, the tail of the meeting never reaches the transcript.

**Confirmed root cause**, `live_transcribe/transcribe.py`, `Engine.stop()` + `_run()`:
- `_run()` guards its loop with `while not self._stop.is_set():`. `stop()` calls `self._stop.set()`, so the worker exits on its next check and **abandons every chunk still in `self._queue`**, the entire transcription backlog (the minutes of lag).
- `self._worker.join(timeout=15.0)` is also far too short to finish a multi-minute backlog even if it did drain.
- **Capture is not the culprit:** `capture.py` `_chunker` already flushes its trailing partial chunk into the engine queue on shutdown (lines 227-235). The engine then discards it along with the rest of the queue.

**Fix (design, implement later):**
- Change Engine stop semantics from "abandon queue" to **"stop accepting new input, drain the remaining queue, then exit."** Keep the `None` sentinel; have `_run` keep processing until it dequeues the sentinel, and stop using `_stop.is_set()` as the loop break (or split "accepting new" vs "processing" into two flags).
- Remove / greatly lengthen the 15 s join, draining can legitimately take ~as long as the current lag (minutes). Join until the drain actually completes.
- **Don't block the HTTP request:** `/api/stop` currently runs the whole teardown synchronously under `STATE.lock` (`web/app.py` lines 238-257). Draining for minutes there would freeze the request *and* `/api/status`. Make stop async: mark state `stopping`, run capture-stop → engine-drain → `md_sink.close()` on a background thread, flip `running=False` when done. Move `md_sink.close()` to *after* the drain (today it's closed immediately after `engine.stop()`).
- **UX:** `/api/status` should report a `stopping` state + remaining backlog (queue depth → ~seconds of audio left) so the UI shows "Finishing transcription… ~N s left" rather than looking frozen.

## 2. [related, lower priority] Reduce the real-time lag itself

The backlog exists because the CPU tier (`large-v3-turbo`, int8) can't keep pace with real-time on this hardware, so chunks queue up (`_queue` maxsize=32 ≈ up to ~8 min before it starts dropping new chunks). Fixing item 1 makes us *keep* the tail, but Stop will then take ~the current lag to finish. If we also want to shrink the lag: a quantized GPU path on capable boxes, a smaller/faster model, shorter chunks, or parallel workers. Separate goal from item 1, noted only.

## 3. [feature, later] Selectable transcription quality modes (Fast / Balanced / Quality)

**Parked 2026-06-06 (Sean). Not now, roadmap.** Let the user pick the transcription
approach BEFORE a run starts, trading speed for accuracy:

- **Fast:** one small/fast model. Lowest latency and load, lowest accuracy. Good for
  quick notes or weak hardware. Likely the same tier the dynamic downgrade targets today
  (e.g. `medium` or a turbo tier).
- **Balanced:** one large model (e.g. `large-v3` / `large-v3-turbo`). The current default.
- **Quality:** two models run in series or in parallel that correct each other, so
  disagreements and hallucinations get detected and resolved (a reconcile / consensus pass,
  not merely a bigger single model). Highest accuracy, slowest, heaviest.

Design notes for when greenlit:
- This is a pre-run control (new-session / pre-meeting screen), with a persisted default in
  settings. It selects the engine tier(s) at start, not a mid-run toggle.
- Quality mode needs a reconcile step: align the two transcripts (timestamps + text), flag
  divergences, and pick or merge (longer agreement wins, or a small local model adjudicates
  the conflicting spans only). Decide series vs parallel by available RAM/CPU. Bound the
  extra work so Stop-drain stays sane (ties into item 1's drain and item 2's lag).
- Live (streaming) Quality may be infeasible in real time; it may only make sense for the
  file-upload / record-then-transcribe path. Decide whether Quality is offered for live
  capture at all, or uploads only.
- Local-only constraint holds: any adjudication model must run on-device, no cloud.
