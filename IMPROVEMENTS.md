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
