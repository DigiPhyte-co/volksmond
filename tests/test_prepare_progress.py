"""WP-2: real download/load progress + bounded failure + retry for the background model prepare.

The async builder (_build_engine_async) resolves the concrete model, and when it is NOT already on disk
runs a "downloading" phase that polls voicedl.progress() into STATE.prepare, giving up with a retryable
error if no bytes arrive for PREPARE_DOWNLOAD_STALL_SECONDS or voicedl reports an error, then a
"loading" phase. These tests drive that with voicedl fully stubbed (no real download, no real model):

  (a) byte growth -> STATE.prepare tracks downloaded/total, then a "done" -> the model loads and
      model_ready flips true;
  (b) NO byte growth -> a retryable "stall" prepare_error within the (shrunk) threshold, capture still
      running, buffer freed;
  (c) voicedl state "error" -> a retryable prepare_error, capture still running;
  (d) /api/prepare/retry from an error state re-spawns the build and recovers WITHOUT restarting
      capture (same capture object, never re-started), and 409s when there is no error to retry.

Run:  python tests/test_prepare_progress.py   (from the project root; exit 0 = pass)
"""
import contextlib
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from fastapi.testclient import TestClient

from live_transcribe import voicedl
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})

# af so the resolved family is Fluister (its repo target exercises the fluister download branch);
# device cpu keeps the tier deterministic.
START_BODY = {"transcribe": True, "record": False, "tier": "small", "device": "cpu", "language": "af"}


class FakeCapture:
    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15,
                 on_chunk=None, t0=None, aec=False, agc=True, record_raw_mic=False):
        self.on_chunk = on_chunk
        self.record_raw_mic = record_raw_mic
        self._t0 = t0 if t0 is not None else 0.0
        self.started = False
        self.start_count = 0

    def start(self):
        self.started = True
        self.start_count += 1

    def stop(self):
        pass

    def has_raw_mic(self):
        return False

    def attach_sys_ring(self, ring):
        pass

    def attach_mic_ring(self, ring):
        pass

    def aec_state(self):
        return (False, False)


class FakeEngineOK:
    model_name = "fake-model"
    family = "fluister"
    engine = "auto"

    def __init__(self, **kw):
        pass

    def subscribe(self, fn):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def pending(self):
        return 0

    def on_chunk(self, source, audio, t_start, block=False, timeout=None):
        return True


@contextlib.contextmanager
def stubs(progress_fn, present_fn, engine_cls=FakeEngineOK, stall_s=0.4, poll_s=0.02):
    tmp = Path(tempfile.mkdtemp())
    saved = {
        "_sessions_dir": webapp._sessions_dir, "_silence_start": webapp._silence_start,
        "AudioCapture": webapp.capture.AudioCapture, "Engine": webapp.transcribe.Engine,
        "resolve_model": webapp.transcribe.resolve_model,
        "_start_model_download": webapp._start_model_download,
        "progress": voicedl.progress, "_present": voicedl._present,
        "STALL": webapp.PREPARE_DOWNLOAD_STALL_SECONDS, "POLL": webapp._PREPARE_POLL_SECONDS,
    }
    webapp._sessions_dir = lambda: tmp
    webapp._silence_start = lambda cap: None
    webapp.capture.AudioCapture = FakeCapture
    webapp.transcribe.Engine = engine_cls
    # Force the resolved model to a NON-local Fluister repo id so the download-target present-check is
    # driven purely by the present_fn stub (this dev machine has a local ct2 Fluister build that would
    # otherwise short-circuit present=True and skip the download phase entirely).
    webapp.transcribe.resolve_model = lambda size, language, engine="auto": ("digiphyte/fluister-" + size, "fluister")
    # Never kick a real voicedl download; the test drives progress() directly.
    webapp._start_model_download = lambda family, size: True
    voicedl.progress = progress_fn
    voicedl._present = present_fn
    webapp.PREPARE_DOWNLOAD_STALL_SECONDS = stall_s
    webapp._PREPARE_POLL_SECONDS = poll_s
    try:
        yield
    finally:
        webapp._sessions_dir = saved["_sessions_dir"]
        webapp._silence_start = saved["_silence_start"]
        webapp.capture.AudioCapture = saved["AudioCapture"]
        webapp.transcribe.Engine = saved["Engine"]
        webapp.transcribe.resolve_model = saved["resolve_model"]
        webapp._start_model_download = saved["_start_model_download"]
        voicedl.progress = saved["progress"]
        voicedl._present = saved["_present"]
        webapp.PREPARE_DOWNLOAD_STALL_SECONDS = saved["STALL"]
        webapp._PREPARE_POLL_SECONDS = saved["POLL"]


def reset_state():
    with webapp.STATE.lock:
        webapp.STATE.reset()
        webapp.STATE.sink_error = None
        webapp.STATE.notice = None


def wait_until(pred, timeout, interval=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(interval)
    try:
        return bool(pred())
    except Exception:
        return False


def test_download_progress_then_ready():
    # (a) Progress grows, STATE.prepare tracks it with the full contract schema, then a "done" hands
    # off to the (stubbed) load and model_ready flips true.
    reset_state()
    state = {"downloaded": 0, "done": False}

    def progress():
        if state["done"]:
            return {"state": "done", "downloaded": state["downloaded"], "total": 1000}
        state["downloaded"] += 100
        return {"state": "downloading", "downloaded": state["downloaded"], "total": 1000}

    try:
        with stubs(progress, present_fn=lambda t: False):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            assert r.json()["model_ready"] is False, r.json()
            # The prepare block appears with the pinned schema while downloading, and bytes flow into it.
            assert wait_until(lambda: (client.get("/api/status").json().get("prepare") or {}).get("downloaded", 0) > 0, 5.0), \
                "download progress never reached STATE.prepare"
            p = client.get("/api/status").json()["prepare"]
            for k in ("phase", "model", "family", "size", "label", "downloaded", "total", "stalled"):
                assert k in p, (k, p)
            assert p["phase"] == "downloading" and p["family"] == "fluister" and p["total"] == 1000, p
            assert p["size"] == "small" and p["model"], p
            # Flip to done -> the model loads and readiness flips true; the prepare block clears.
            state["done"] = True
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 5.0), \
                "model_ready never flipped true after the download completed"
            st = client.get("/api/status").json()
            assert st["preparing"] is False and st["prepare_error"] is None and st["prepare"] is None, st
    finally:
        reset_state()
    print("  OK  download progress tracked with the contract schema; done -> load -> model_ready true")


def test_stall_becomes_a_retryable_error():
    # (b) No byte growth for the whole (shrunk) stall window -> a retryable prepare_error flagged
    # stalled, capture still running, the held backlog RETAINED for a retry (P1-2). model_ready never flips.
    reset_state()

    def progress():
        return {"state": "downloading", "downloaded": 0, "total": 1000}   # never grows

    try:
        with stubs(progress, present_fn=lambda t: False, stall_s=0.4, poll_s=0.02):
            t0 = time.monotonic()
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            assert wait_until(lambda: client.get("/api/status").json().get("prepare_error"), 5.0), \
                "a stalled download never became a prepare_error"
            elapsed = time.monotonic() - t0
            assert elapsed < 4.0, f"stall detection took too long ({elapsed:.1f}s) vs a 0.4s threshold"
            st = client.get("/api/status").json()
            assert st["running"] is True, st                 # capture kept running
            assert st["preparing"] is False and st["model_ready"] is False, st
            assert "stall" in st["prepare_error"].lower(), st
            assert (st["prepare"] or {}).get("stalled") is True and st["prepare"]["phase"] == "error", st
            # P1-2: the buffer is KEPT across a retryable failure (it goes on holding chunks, and a retry
            # replays from t0), not freed. It is cleared only on stop / final abandonment / publication.
            assert webapp.STATE.pending_audio is not None, "the held backlog must be retained for a retry (P1-2)"
    finally:
        reset_state()
    print("  OK  a stalled download -> retryable prepare_error (stalled) within the threshold; capture kept running")


def test_hard_error_surfaces():
    # (c) voicedl reports state "error" -> a retryable prepare_error, capture still running.
    reset_state()

    def progress():
        return {"state": "error", "downloaded": 0, "total": 1000, "error": "boom"}

    try:
        with stubs(progress, present_fn=lambda t: False):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            assert wait_until(lambda: client.get("/api/status").json().get("prepare_error"), 5.0), \
                "a download error never became a prepare_error"
            st = client.get("/api/status").json()
            assert st["running"] is True and st["preparing"] is False and st["model_ready"] is False, st
            assert isinstance(st["prepare_error"], str) and st["prepare_error"], st
    finally:
        reset_state()
    print("  OK  a download error surfaces as a retryable prepare_error; capture kept running")


def test_retry_recovers_without_restarting_capture():
    # (d) From an error state, /api/prepare/retry re-spawns the build and recovers, and capture is NOT
    # restarted (same object, started exactly once). Also: retry 409s when there is no error.
    reset_state()
    present = {"v": False}   # first attempt: not present -> download error; retry: present -> loads

    def progress():
        return {"state": "error", "downloaded": 0, "total": 1000}

    try:
        with stubs(progress, present_fn=lambda t: present["v"]):
            # No session yet: retry is a 409, not a crash.
            assert client.post("/api/prepare/retry").status_code == 409

            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            assert wait_until(lambda: client.get("/api/status").json().get("prepare_error"), 5.0), st_err()
            cap = webapp.STATE.capture
            assert cap is not None and cap.started and cap.start_count == 1, "capture not started once"

            # Make the retry succeed: the model is now "present" (skip download) and loads instantly.
            present["v"] = True
            rr = client.post("/api/prepare/retry")
            assert rr.status_code == 200 and rr.json() == {"ok": True}, rr.text
            # The endpoint clears the error synchronously under the lock (the re-spawned build may then
            # reach ready almost instantly, so preparing/model_ready are not asserted here - only the
            # deterministic error clear is).
            assert client.get("/api/status").json()["prepare_error"] is None, client.get("/api/status").json()
            # It recovers to ready, and the SAME capture is used, never restarted.
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 5.0), \
                "retry never reached model_ready"
            assert webapp.STATE.capture is cap, "retry replaced the capture object"
            assert cap.start_count == 1, f"retry restarted capture ({cap.start_count} starts)"

            # Now that it is ready (no error), retry is a 409 again.
            assert client.post("/api/prepare/retry").status_code == 409
    finally:
        reset_state()
    print("  OK  retry re-spawns the build and recovers without restarting capture; 409 when there is no error")


def test_retry_replays_the_buffer_from_t0():
    # P1-2: a retryable failure must KEEP the pending-audio buffer (it goes on holding chunks through the
    # error state) and /api/prepare/retry must REUSE it, so a successful retry replays from Begin, not
    # from the Retry click. Here the first build fails; chunks captured DURING the error state are then
    # replayed to the engine on a successful retry, in order, with none dropped.
    reset_state()
    attempt = {"n": 0}
    received = []
    rlock = threading.Lock()

    class RetryReplayEngine:
        model_name = "fake-model"
        family = "fluister"
        engine = "auto"

        def __init__(self, **kw):
            attempt["n"] += 1
            if attempt["n"] == 1:
                raise RuntimeError("first build fails (retryable)")

        def subscribe(self, fn):
            pass

        def start(self):
            pass

        def stop(self, drain=False, timeout=None):
            pass

        def pending(self):
            return 0

        def is_alive(self):
            return True

        def on_chunk(self, source, audio, t_start, block=False, timeout=None):
            with rlock:
                received.append((source, t_start))
            return True

    def progress():   # unused: present=True skips the download phase and goes straight to the load
        return {"state": "done", "downloaded": 0, "total": 0}

    try:
        with stubs(progress, present_fn=lambda t: True, engine_cls=RetryReplayEngine):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            # First build fails -> a retryable error, and the buffer is RETAINED (P1-2).
            assert wait_until(lambda: client.get("/api/status").json().get("prepare_error"), 5.0), st_err()
            assert webapp.STATE.pending_audio is not None, "the buffer was freed on failure (P1-2 regression)"
            # Capture chunks DURING the error state: they must be held (not dropped) for the retry.
            backlog = []
            for i in range(12):
                src = "MIC" if i % 2 == 0 else "SYS"
                webapp._feed(src, np.full(160, i, dtype=np.float32), float(i))
                backlog.append((src, float(i)))
            # Retry: reuse the SAME buffer, build succeeds, and everything held is replayed from t0.
            rr = client.post("/api/prepare/retry")
            assert rr.status_code == 200, rr.text
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 5.0), \
                "retry never reached model_ready"
            assert wait_until(lambda: len(received) >= 12, 5.0), f"retry replayed only {len(received)}/12"
            with rlock:
                got = list(received)
            assert got == backlog, f"retry did not replay the held buffer from t0:\n got {got}\n exp {backlog}"
    finally:
        reset_state()
    print("  OK  a retryable failure keeps the buffer; retry replays it from t0, in order, zero drops (P1-2)")


def test_foreign_download_in_slot_is_not_misread():
    # P1-5: the single global download slot may be busy with a DIFFERENT model (a Settings download). Its
    # bytes / 'done' / 'error' must NOT be read as ours: a foreign 'done' must not complete our prepare,
    # and we must wait for the slot then rely on OUR model actually being on disk.
    reset_state()
    present = {"v": False}
    foreign_done = {"v": False}

    def progress():
        st = "done" if foreign_done["v"] else "downloading"
        return {"state": st, "repo": "someone/else-model", "downloaded": 500, "total": 500}

    try:
        with stubs(progress, present_fn=lambda t: present["v"], stall_s=0.3, poll_s=0.02):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            time.sleep(0.3)   # several poll cycles while the FOREIGN download holds the slot
            # The foreign download completes: its 'done' must NOT complete our prepare, and we must not
            # have failed ourselves as stalled while waiting for the slot.
            foreign_done["v"] = True
            time.sleep(0.3)
            st = client.get("/api/status").json()
            assert st["model_ready"] is False, "a foreign download's 'done' was misread as ours (P1-5)"
            assert st["prepare_error"] is None, f"waiting for the slot was wrongly failed: {st}"
            # Our model actually lands on disk -> prepare completes on the on-disk truth.
            present["v"] = True
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 5.0), \
                "prepare never completed once our model was actually present"
    finally:
        reset_state()
    print("  OK  a foreign model in the download slot is not misread; completion needs OUR model present (P1-5)")


def test_verification_tail_is_not_a_false_stall():
    # P2-2: bytes flat AT (or near) the total is the no-write verification tail (checksum / extraction /
    # AV scan), which must get a longer grace than a genuine mid-download stall so a healthy download is
    # not failed. Here bytes sit at the total: it must NOT trip the (short) download-stall window, and it
    # completes when the files finish landing.
    reset_state()
    present = {"v": False}

    def progress():   # flat AT total, no repo -> treated as ours; this is the verification tail
        return {"state": "downloading", "downloaded": 1000, "total": 1000}

    saved_grace = webapp.PREPARE_VERIFY_GRACE_SECONDS
    try:
        with stubs(progress, present_fn=lambda t: present["v"], stall_s=0.3, poll_s=0.02):
            webapp.PREPARE_VERIFY_GRACE_SECONDS = 30   # far beyond the test window, unlike the 0.3s stall
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            time.sleep(0.7)   # well past the 0.3s download-stall window
            st = client.get("/api/status").json()
            assert st["prepare_error"] is None, f"the verification tail was wrongly failed as stalled (P2-2): {st}"
            assert st["model_ready"] is False, st
            # Files finish landing -> present -> completes (no false stall along the way).
            present["v"] = True
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 5.0), \
                "prepare never completed after the verification tail"
    finally:
        webapp.PREPARE_VERIFY_GRACE_SECONDS = saved_grace
        reset_state()
    print("  OK  a flat-at-total verification tail is not failed as a stall within the download window (P2-2)")


def st_err():
    return f"prepare_error never set: {client.get('/api/status').json()}"


if __name__ == "__main__":
    tests = (test_download_progress_then_ready,
             test_stall_becomes_a_retryable_error,
             test_hard_error_surfaces,
             test_retry_recovers_without_restarting_capture,
             test_retry_replays_the_buffer_from_t0,
             test_foreign_download_in_slot_is_not_misread,
             test_verification_tail_is_not_a_false_stall)
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"  ERROR {fn.__name__}: {e!r}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll prepare-progress tests passed.")
