"""t0-capture: /api/start must start capture the INSTANT Begin is clicked and load the transcription
model on a background thread, holding every chunk from t0 and replaying it (in order, no drops) once
the model is ready.

These pin the contract without any audio device or real model:
  (a) with a SLOW (blocked) engine build, POST /api/start returns PROMPTLY, capture is started, and
      /api/status reports preparing=True / model_ready=False;
  (b) while the engine is None+preparing, _feed BUFFERS chunks, and once the engine attaches ALL of
      them are replayed to the engine IN ORDER with block=True (zero drops);
  (c) an engine-build FAILURE sets prepare_error and leaves the session capturing (running True),
      without crashing;
plus a direct unit test of the bounded, drop-oldest pending-audio buffer.

Everything is stubbed: capture.AudioCapture is a device-free fake, transcribe.Engine is a per-test
fake (blocking / failing / recording), _sessions_dir is a temp folder and _silence_start is a no-op,
so no hardware, model weights or watcher threads are touched.

Run:  python tests/test_start_capture_first.py   (from the project root; exit 0 = pass)
"""
import contextlib
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from fastapi.testclient import TestClient

from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

# Loopback Host + CSRF token, exactly like test_web_api.py, so we exercise /api/start, not the guards.
client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})

START_BODY = {"transcribe": True, "record": False, "tier": "small", "device": "cpu", "language": "en"}


class FakeCapture:
    """Device-free stand-in for capture.AudioCapture: opens nothing, records that start() ran, and
    satisfies the ring-attach + aec_state calls start()/_build_engine_async make."""

    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15,
                 on_chunk=None, t0=None, aec=False, agc=True, record_raw_mic=False):
        self.on_chunk = on_chunk
        self.record_raw_mic = record_raw_mic
        self._t0 = t0 if t0 is not None else 0.0
        self.started = False

    def start(self):
        self.started = True

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


@contextlib.contextmanager
def stubs(engine_cls):
    tmp = Path(tempfile.mkdtemp())
    saved = (webapp._sessions_dir, webapp._silence_start,
             webapp.capture.AudioCapture, webapp.transcribe.Engine)
    webapp._sessions_dir = lambda: tmp
    webapp._silence_start = lambda cap: None            # no watcher threads in the test
    webapp.capture.AudioCapture = FakeCapture
    webapp.transcribe.Engine = engine_cls
    try:
        yield
    finally:
        (webapp._sessions_dir, webapp._silence_start,
         webapp.capture.AudioCapture, webapp.transcribe.Engine) = saved


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


def test_start_returns_before_model_loads():
    # (a) The model build is blocked; /api/start must still return promptly with capture started and
    # /api/status reporting preparing / not-yet-ready. Then releasing the build flips model_ready.
    reset_state()
    release = threading.Event()

    class BlockingEngine:
        model_name = "fake-model"
        family = "whisper"
        engine = "auto"

        def __init__(self, **kw):
            if not release.wait(timeout=20.0):
                raise RuntimeError("test never released the engine build")

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

    try:
        with stubs(BlockingEngine):
            t0 = time.monotonic()
            r = client.post("/api/start", json=START_BODY)
            elapsed = time.monotonic() - t0
            assert r.status_code == 200, r.text
            # PROMPT: the still-blocked model build must not hold up the response.
            assert elapsed < 5.0, f"/api/start blocked on the model build ({elapsed:.1f}s)"
            assert r.json()["model_ready"] is False, r.json()
            # Capture is up immediately; transcription is preparing.
            st = client.get("/api/status").json()
            assert st["running"] is True, st
            assert st["preparing"] is True and st["model_ready"] is False, st
            assert st["prepare_error"] is None, st
            assert webapp.STATE.capture is not None and webapp.STATE.capture.started, "capture not started at once"
            assert webapp.STATE.engine is None, "engine must not be attached while still building"
            # Release the build -> the engine attaches and readiness flips true.
            release.set()
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 10.0), \
                "model_ready never flipped true after the build completed"
            st2 = client.get("/api/status").json()
            assert st2["preparing"] is False and st2["model_ready"] is True, st2
    finally:
        release.set()
        reset_state()
    print("  OK  /api/start returns promptly with capture live; model_ready flips true once the build finishes")


def test_buffer_and_replay_in_order_no_drops():
    # (b) While the engine is None+preparing, _feed buffers chunks. Once the engine attaches, every
    # buffered chunk is replayed in order with block=True (the zero-drop guarantee). More than the
    # engine's maxsize=32 queue are fed, so the block=True replay's no-drop property is meaningful.
    reset_state()
    release = threading.Event()
    received = []
    rlock = threading.Lock()

    class RecordingEngine:
        model_name = "fake-model"
        family = "whisper"
        engine = "auto"

        def __init__(self, **kw):
            if not release.wait(timeout=20.0):
                raise RuntimeError("test never released the engine build")

        def subscribe(self, fn):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def pending(self):
            return 0

        def on_chunk(self, source, audio, t_start, block=False, timeout=None):
            with rlock:
                received.append((source, t_start, block))
            return True

    try:
        with stubs(RecordingEngine):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            assert r.json()["model_ready"] is False, r.json()
            st = client.get("/api/status").json()
            assert st["preparing"] is True and st["model_ready"] is False, st
            # Feed a burst while the engine is still building: it must all be held in the buffer.
            fed = []
            for i in range(40):
                src = "MIC" if i % 2 == 0 else "SYS"
                webapp._feed(src, np.full(160, i, dtype=np.float32), float(i))
                fed.append((src, float(i)))
            with rlock:
                assert received == [], f"nothing should reach the engine while it is still building: {received}"
            assert webapp.STATE.pending_audio is not None, "pending-audio buffer missing during preparing"
            # Release the build: the backlog replays into the engine, in order.
            release.set()
            assert wait_until(lambda: len(received) >= 40, 20.0), f"replay incomplete: got {len(received)}/40"
            with rlock:
                got = [(s, t) for (s, t, _b) in received]
                blocks = [b for (_s, _t, b) in received]
            assert got == fed, f"replay out of order/incomplete:\n got {got}\n exp {fed}"
            assert all(b is True for b in blocks), "replay must use block=True so it never drops on the maxsize=32 queue"
            # After the handoff, readiness flips and the buffer is released.
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 5.0), "not ready after replay"
            assert webapp.STATE.pending_audio is None, "pending-audio buffer not cleared after handoff"
    finally:
        release.set()
        reset_state()
    print("  OK  chunks buffered while preparing are replayed to the engine in order, block=True, zero drops (40 > 32)")


def test_build_failure_keeps_capturing():
    # (c) An engine-build failure must not crash the app or the session: prepare_error is surfaced,
    # preparing clears, and the session keeps running (capture stays live so nothing is lost).
    reset_state()

    class FailingEngine:
        def __init__(self, **kw):
            raise RuntimeError("boom: model file missing")

    try:
        with stubs(FailingEngine):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text   # start still returns OK; the failure is asynchronous
            assert wait_until(lambda: client.get("/api/status").json().get("prepare_error"), 10.0), \
                "prepare_error never set after the build failed"
            st = client.get("/api/status").json()
            assert st["running"] is True, st            # still capturing
            assert st["preparing"] is False, st
            assert st["model_ready"] is False, st
            assert isinstance(st["prepare_error"], str) and st["prepare_error"], st
            assert webapp.STATE.pending_audio is None, "buffer should be freed once the model can never load"
            # The app did not crash: status still serves.
            assert client.get("/api/status").status_code == 200
    finally:
        reset_state()
    print("  OK  engine-build failure sets prepare_error and leaves the session capturing (running), no crash")


def test_pending_buffer_bounds_and_drops_oldest():
    # The bounded pending-audio buffer: chunks are held in order; over the sample cap the OLDEST is
    # dropped (never the newest), so a never-loading model cannot OOM the app. close_and_drain returns
    # what is left, in order, and seals the buffer so a later append is rejected (fed live instead).
    pa = webapp._PendingAudio(max_samples=300)   # tiny cap: 300 float32 samples
    for i in range(5):
        assert pa.append("MIC", np.full(100, i, dtype=np.float32), float(i)) is True, i
    items = pa.close_and_drain()
    ts = [t for (_s, _a, t) in items]
    # 5 x 100 = 500 samples > 300 cap: the two oldest (t=0,1) were dropped; 2..4 remain, in order.
    assert ts == [2.0, 3.0, 4.0], ts
    assert sum(len(a) for (_s, a, _t) in items) <= 300
    # Sealed after the drain: a further append returns False so _feed feeds the live engine instead.
    assert pa.append("MIC", np.zeros(100, dtype=np.float32), 9.0) is False
    print("  OK  pending buffer keeps order, drops OLDEST over the cap, and seals on drain (no post-handoff loss)")


if __name__ == "__main__":
    failures = 0
    for fn in (test_start_returns_before_model_loads,
               test_buffer_and_replay_in_order_no_drops,
               test_build_failure_keeps_capturing,
               test_pending_buffer_bounds_and_drops_oldest):
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
    print("\nAll t0-capture start tests passed.")
