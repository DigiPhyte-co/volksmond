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
    from live_transcribe import voicedl as _voicedl
    saved = (webapp._sessions_dir, webapp._silence_start,
             webapp.capture.AudioCapture, webapp.transcribe.Engine, _voicedl._present)
    webapp._sessions_dir = lambda: tmp
    webapp._silence_start = lambda cap: None            # no watcher threads in the test
    webapp.capture.AudioCapture = FakeCapture
    webapp.transcribe.Engine = engine_cls
    # Model is always "present" so the async builder skips its download phase and goes straight to the
    # (stubbed) load: these tests pin the capture/replay seam, not model fetching, and must not depend
    # on which models happen to be cached on the machine running them.
    _voicedl._present = lambda target: True
    try:
        yield
    finally:
        (webapp._sessions_dir, webapp._silence_start,
         webapp.capture.AudioCapture, webapp.transcribe.Engine, _voicedl._present) = saved


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
            # P1-2: a retryable build failure KEEPS the buffer (retry replays from t0); it is not freed
            # here. Only stop / final abandonment / a successful publication clears it.
            assert webapp.STATE.pending_audio is not None, "buffer must be retained for a retry after a build failure (P1-2)"
            # The app did not crash: status still serves.
            assert client.get("/api/status").status_code == 200
    finally:
        reset_state()
    print("  OK  engine-build failure sets prepare_error and leaves the session capturing (running), no crash")


def test_pending_buffer_bounds_and_drop_take_finalise():
    # The bounded pending-audio buffer's primitives:
    #  - append holds chunks in order and, over the sample cap, drops the OLDEST (never the newest),
    #    so a never-loading model cannot OOM the app;
    #  - take_all returns what is held, in order, and LEAVES THE BUFFER OPEN (append still works), so
    #    the drain loop can keep collecting live chunks behind the backlog;
    #  - finalise_if_empty returns stragglers (and stays open) when NON-empty, and only when EMPTY
    #    closes the buffer + runs on_close (publish) atomically, after which append returns False.
    pa = webapp._PendingAudio(max_samples=300)   # tiny cap: 300 float32 samples
    for i in range(5):
        assert pa.append("MIC", np.full(100, i, dtype=np.float32), float(i)) is True, i
    items = pa.take_all()
    ts = [t for (_s, _a, t) in items]
    # 5 x 100 = 500 samples > 300 cap: the two oldest (t=0,1) were dropped; 2..4 remain, in order.
    assert ts == [2.0, 3.0, 4.0], ts
    assert sum(len(a) for (_s, a, _t) in items) <= 300
    # take_all left the buffer OPEN: append still works (this is how live-during-drain chunks queue).
    assert pa.append("MIC", np.zeros(100, dtype=np.float32), 9.0) is True
    # finalise_if_empty on a NON-empty buffer returns the stragglers and does NOT publish or close.
    published = []
    res = pa.finalise_if_empty(lambda: published.append("published"))
    assert [t for (_s, _a, t) in res] == [9.0], res
    assert published == [], "must not publish while stragglers remain"
    assert pa.append("MIC", np.zeros(10, dtype=np.float32), 10.0) is True, "buffer must still be open"
    # Drain it, then finalise on an EMPTY buffer: closes + runs on_close atomically, returns None.
    pa.take_all()
    res2 = pa.finalise_if_empty(lambda: published.append("published"))
    assert res2 is None and published == ["published"], (res2, published)
    # Sealed: any later append returns False so _feed feeds the published engine directly.
    assert pa.append("MIC", np.zeros(100, dtype=np.float32), 11.0) is False
    print("  OK  pending buffer: drop-oldest, take_all leaves it OPEN, finalise_if_empty publishes+seals only when empty")


def test_backlog_stays_ahead_of_live_during_replay():
    # THE seam-ordering property: chunks that arrive live DURING the backlog replay must land AFTER the
    # whole backlog and never be dropped. We buffer a backlog, then pause the engine inside the replay
    # of the FIRST backlog chunk, inject live chunks while it is paused, release, and assert the engine
    # received the entire backlog strictly BEFORE any live chunk, in order, with zero drops.
    reset_state()
    build_release = threading.Event()
    replay_release = threading.Event()
    first_chunk_seen = threading.Event()
    received = []
    rlock = threading.Lock()

    class GatedEngine:
        model_name = "fake-model"
        family = "whisper"
        engine = "auto"

        def __init__(self, **kw):
            if not build_release.wait(timeout=20.0):
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
                n = len(received)
            if n == 1:
                # Pause the drain INSIDE the replay of the first backlog chunk, so the test can inject
                # live chunks while the engine is still unpublished and the backlog is mid-replay.
                first_chunk_seen.set()
                replay_release.wait(timeout=20.0)
            return True

    try:
        with stubs(GatedEngine):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            # Buffer a backlog while the engine build is blocked.
            backlog = []
            for i in range(10):
                src = "MIC" if i % 2 == 0 else "SYS"
                webapp._feed(src, np.full(160, i, dtype=np.float32), float(i))
                backlog.append((src, float(i)))
            # Release the build; the drain begins and pauses inside the first backlog chunk's on_chunk.
            build_release.set()
            assert first_chunk_seen.wait(10.0), "drain never reached the first backlog chunk"
            # Engine is not published yet: inject LIVE chunks WHILE the backlog replay is paused. They
            # must be buffered behind the backlog (STATE.engine is still None during the drain).
            assert webapp.STATE.engine is None, "engine must not be published mid-replay"
            live = []
            for i in range(5):
                src = "MIC" if i % 2 == 0 else "SYS"
                webapp._feed(src, np.full(160, 100 + i, dtype=np.float32), float(100 + i))
                live.append((src, float(100 + i)))
            # Let the drain finish: backlog tail, then the live chunks, then finalise.
            replay_release.set()
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 15.0), \
                "session never finalised after the replay"
            with rlock:
                got = [(s, t) for (s, t, _b) in received]
                blocks = [b for (_s, _t, b) in received]
            # The whole backlog, in order, STRICTLY before every live chunk, in order. Zero drops.
            assert got == backlog + live, f"seam order/drops wrong:\n got {got}\n exp {backlog + live}"
            assert len(got) == 15, f"expected 15 chunks (no drops), got {len(got)}"
            assert all(b is True for b in blocks), "replay must use block=True (never drops on a full queue)"
            last_backlog_idx = max(got.index(c) for c in backlog)
            first_live_idx = min(got.index(c) for c in live)
            assert last_backlog_idx < first_live_idx, "a live chunk landed before the backlog finished"
    finally:
        build_release.set()
        replay_release.set()
        reset_state()
    print("  OK  live chunks arriving DURING replay land strictly after the whole backlog, in order, zero drops")


def test_asr_slower_than_realtime_forever_stays_ready():
    # WP-1 ready-state hardening: if transcription can NEVER catch up (a stub engine whose queue never
    # drains), model_ready must STILL flip True at phase-1 end and STAY True, preparing must clear, the
    # engine must never be published (STATE.engine stays None while the backlog never drains), and
    # _feed must drop ZERO chunks - it just keeps buffering. This is the exact case that used to leave
    # the UI stuck on "preparing"; readiness is now decoupled from the drain completing.
    reset_state()
    release = threading.Event()
    seen = []
    slock = threading.Lock()

    class NeverDrainsEngine:
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
            return 999

        def on_chunk(self, source, audio, t_start, block=False, timeout=None):
            # The queue is perpetually full: record the attempt, wait like the real block=True path so
            # the drain is not a busy-spin, and report failure so the backlog never drains and the
            # engine is never published.
            with slock:
                seen.append(t_start)
            time.sleep(min(timeout or 0.05, 0.05))
            return False

    try:
        with stubs(NeverDrainsEngine):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            # Buffer a backlog WHILE the build is blocked, so once it releases the drain has something
            # to (fail to) replay and can never reach the empty-buffer finalise.
            for i in range(20):
                src = "MIC" if i % 2 == 0 else "SYS"
                webapp._feed(src, np.full(160, i, dtype=np.float32), float(i))
            assert webapp.STATE.pending_audio is not None, "no pending buffer while preparing"
            # Release the build: model_ready must flip true even though the drain will never complete.
            release.set()
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 10.0), \
                "model_ready never flipped true for a never-draining engine"
            st = client.get("/api/status").json()
            assert st["preparing"] is False and st["prepare_error"] is None, st
            assert webapp.STATE.engine is None, "engine must NOT be published while the backlog never drains"
            # Feed MORE after readiness: still buffered behind the stuck backlog, still zero drops.
            for i in range(20, 40):
                src = "MIC" if i % 2 == 0 else "SYS"
                webapp._feed(src, np.full(160, i, dtype=np.float32), float(i))
            time.sleep(0.5)   # let the drain spin a few cycles on the stuck first chunk
            st2 = client.get("/api/status").json()
            assert st2["model_ready"] is True and st2["preparing"] is False, st2
            assert webapp.STATE.engine is None, "engine published despite a never-draining backlog"
            assert webapp.STATE.pending_audio is not None, "buffer closed though the drain never finished"
            assert webapp.STATE.pending_audio._warned is False, "the pending buffer dropped a chunk (overflow); it must not"
            # The stub is stuck retrying the FIRST backlog chunk (t=0.0): the drain never advances while
            # readiness is already live, which is exactly the decoupling being asserted.
            with slock:
                distinct = sorted(set(seen))
            assert distinct == [0.0], f"drain advanced past / away from the stuck first chunk: {distinct}"
    finally:
        release.set()
        reset_state()
    print("  OK  never-draining ASR: model_ready flips true and STAYS true, preparing clears, engine unpublished, zero drops")


def test_stop_during_catchup_drains_backlog():
    # P1-3: Stop clicked AFTER model_ready but BEFORE the engine is published (the slow-CPU catch-up
    # window) must drain the held backlog into the transcript, not discard it. The engine exists only in
    # STATE.preparing_engine here (STATE.engine is still None), so the stop path has to reach it, drain
    # every buffered chunk into it (in order), and stop(drain=True) it. Before the fix the whole
    # transcript was lost.
    reset_state()
    build_gate = threading.Event()
    drain_gate = threading.Event()
    first_seen = threading.Event()
    received = []
    rlock = threading.Lock()

    class DrainGateEngine:
        model_name = "fake-model"
        family = "whisper"
        engine = "auto"

        def __init__(self, **kw):
            self.stopped_drain = None
            if not build_gate.wait(20.0):
                raise RuntimeError("test never released the engine build")

        def subscribe(self, fn):
            pass

        def start(self):
            pass

        def stop(self, drain=False, timeout=None):
            self.stopped_drain = drain

        def pending(self):
            return 0

        def is_alive(self):
            return True

        def on_chunk(self, source, audio, t_start, block=False, timeout=None):
            with rlock:
                received.append((source, t_start))
                n = len(received)
            if n == 1:
                # Park inside the FIRST backlog chunk so the test can catch the session mid-catch-up
                # (model_ready true, engine unpublished) and Stop it there.
                first_seen.set()
                drain_gate.wait(20.0)
            return True

    try:
        with stubs(DrainGateEngine):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            # Buffer a backlog while the build is gated.
            backlog = []
            for i in range(20):
                src = "MIC" if i % 2 == 0 else "SYS"
                webapp._feed(src, np.full(160, i, dtype=np.float32), float(i))
                backlog.append((src, float(i)))
            # Release the build: model_ready flips true, the drain begins and parks in the first chunk.
            build_gate.set()
            assert first_seen.wait(10.0), "the drain never reached the first backlog chunk"
            assert client.get("/api/status").json()["model_ready"] is True, "not ready at catch-up"
            assert webapp.STATE.engine is None, "engine must not be published during catch-up"
            assert webapp.STATE.preparing_engine is not None, "the private engine handle is missing (P1-3)"
            eng = webapp.STATE.preparing_engine
            # Stop everything mid-catch-up.
            rs = client.post("/api/stop?what=all")
            assert rs.status_code == 200, rs.text
            # Let the parked drain finish so the builder hands the engine off to the stop path.
            drain_gate.set()
            assert wait_until(lambda: not webapp.STATE.running, 15.0), "stop never finalised the session"
            with rlock:
                got = list(received)
            assert got == backlog, f"backlog not fully drained into the engine on Stop:\n got {got}\n exp {backlog}"
            assert eng.stopped_drain is True, "the private engine was not stop(drain=True)'d on Stop"
            assert webapp.STATE.preparing_engine is None, "the private engine handle survived the stop"
            assert webapp.STATE.pending_audio is None, "the pending buffer survived the stop"
    finally:
        build_gate.set()
        drain_gate.set()
        reset_state()
    print("  OK  Stop during model catch-up drains the whole backlog into the transcript, no loss (P1-3)")


def test_partial_transcription_stop_makes_builder_bail():
    # P1-4: stopping ONLY transcription (recording continues) while the model is still loading must make
    # the in-flight builder bail out - it must NOT finish building, start an engine, and flip model_ready
    # true after the user stopped transcribing. _still_ours() now checks STATE.transcribing, and the
    # partial-stop clears preparation state and takes ownership, so a late build finds it is no longer ours.
    reset_state()
    build_gate = threading.Event()
    started = {"v": False}

    class BailEngine:
        model_name = "fake-model"
        family = "whisper"
        engine = "auto"

        def __init__(self, **kw):
            if not build_gate.wait(20.0):
                raise RuntimeError("test never released the engine build")

        def subscribe(self, fn):
            pass

        def start(self):
            started["v"] = True

        def stop(self, drain=False, timeout=None):
            pass

        def pending(self):
            return 0

        def is_alive(self):
            return True

        def on_chunk(self, source, audio, t_start, block=False, timeout=None):
            return True

    try:
        with stubs(BailEngine):
            # transcribe + record, so "stop transcription" leaves the session running (recording).
            r = client.post("/api/start", json={"transcribe": True, "record": True, "tier": "small",
                                                "device": "cpu", "language": "en"})
            assert r.status_code == 200, r.text
            st = client.get("/api/status").json()
            assert st["preparing"] is True and st["model_ready"] is False, st
            # Stop ONLY transcription while the model is still (gated) loading.
            rs = client.post("/api/stop?what=transcription")
            assert rs.status_code == 200, rs.text
            st2 = client.get("/api/status").json()
            assert st2["transcribing"] is False and st2["running"] is True and st2["recording"] is True, st2
            # Release the late build. It must NOT resurrect transcription.
            build_gate.set()
            time.sleep(0.6)   # give a would-be resurrection time to happen
            st3 = client.get("/api/status").json()
            assert st3["model_ready"] is False, "builder resurrected transcription after a partial stop (P1-4)"
            assert webapp.STATE.engine is None, "an engine was published after transcription was stopped"
            assert started["v"] is False, "the builder started an engine after transcription was stopped"
            assert st3["prepare_error"] is None, st3
    finally:
        build_gate.set()
        reset_state()
    print("  OK  stopping only transcription mid-load makes the in-flight builder bail, no resurrection (P1-4)")


if __name__ == "__main__":
    failures = 0
    for fn in (test_start_returns_before_model_loads,
               test_buffer_and_replay_in_order_no_drops,
               test_backlog_stays_ahead_of_live_during_replay,
               test_asr_slower_than_realtime_forever_stays_ready,
               test_build_failure_keeps_capturing,
               test_stop_during_catchup_drains_backlog,
               test_partial_transcription_stop_makes_builder_bail,
               test_pending_buffer_bounds_and_drop_take_finalise):
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
