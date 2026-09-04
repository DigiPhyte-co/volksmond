"""CPU model loading: an honest, survivable wait instead of a false failure (WP-1).

A CPU-only laptop pays the whole model load at Begin, and on CPU that load is dominated by the
first inference, not the constructor: minutes, not seconds. The flat 120 s budget declared a
healthy machine failed, and Retry then built a SECOND engine that could only queue behind the
first one on the model build lock, so the user paid the load twice in wall-clock time.

These pin the fix, with no audio device and no model weights:
  (1) the load budget is device-aware and lives in one testable function;
  (2) while the load thread is alive the prepare state stays "loading" with an elapsed counter and
      no error, well past the CUDA budget, and the CPU hint appears once the wait is a long one;
  (3) a Retry ATTACHES to the load already in flight instead of constructing a second Engine;
  (4) audio held while the model loads is never lost silently: a stop before the model ever loaded
      states the gap in the transcript, and an eviction at the buffer cap is recorded too;
  (5) pre-warm at app start is CPU-only, needs the model on disk, never downloads, and stands down
      while a session is running.

The timing cases scale the budget constants down (seconds instead of minutes) rather than sleeping
for real; the code path under test is identical, only the numbers differ.

Run:  python tests/test_cpu_load_budget.py   (from the project root; exit 0 = pass)
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

from live_transcribe import transcribe
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})

START_BODY = {"transcribe": True, "record": False, "tier": "small", "device": "cpu", "language": "en"}


class FakeCapture:
    """Device-free stand-in for capture.AudioCapture (same shape as test_start_capture_first)."""

    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15,
                 on_chunk=None, t0=None, aec=False, agc=True, record_raw_mic=False):
        self.on_chunk = on_chunk
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
def stubs(engine_cls, cpu_budget=None, gpu_budget=None, hint=None):
    tmp = Path(tempfile.mkdtemp())
    from live_transcribe import voicedl as _voicedl
    saved = (webapp._sessions_dir, webapp._silence_start,
             webapp.capture.AudioCapture, webapp.transcribe.Engine, _voicedl._present,
             webapp.PREPARE_LOAD_TIMEOUT_SECONDS, webapp.PREPARE_LOAD_TIMEOUT_SECONDS_CPU,
             webapp.PREPARE_LOAD_SLOW_HINT_SECONDS)
    webapp._sessions_dir = lambda: tmp
    webapp._silence_start = lambda cap: None
    webapp.capture.AudioCapture = FakeCapture
    webapp.transcribe.Engine = engine_cls
    _voicedl._present = lambda target: True     # never exercise the download phase here
    if cpu_budget is not None:
        webapp.PREPARE_LOAD_TIMEOUT_SECONDS_CPU = cpu_budget
    if gpu_budget is not None:
        webapp.PREPARE_LOAD_TIMEOUT_SECONDS = gpu_budget
    if hint is not None:
        webapp.PREPARE_LOAD_SLOW_HINT_SECONDS = hint
    try:
        yield tmp
    finally:
        (webapp._sessions_dir, webapp._silence_start,
         webapp.capture.AudioCapture, webapp.transcribe.Engine, _voicedl._present,
         webapp.PREPARE_LOAD_TIMEOUT_SECONDS, webapp.PREPARE_LOAD_TIMEOUT_SECONDS_CPU,
         webapp.PREPARE_LOAD_SLOW_HINT_SECONDS) = saved


def reset_state():
    with webapp.STATE.lock:
        webapp.STATE.reset()
        webapp.STATE.sink_error = None
        webapp.STATE.notice = None
    # One process runs every case, so an abandoned load from one must never be attached to by the
    # next (in the app itself that reuse is deliberate and correct; across tests it is contamination).
    webapp._reset_loads()


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


def _gated_engine(gate, builds, name="fake-model"):
    """An Engine stand-in whose CONSTRUCTOR blocks on `gate` and counts every construction."""

    class GatedEngine:
        model_name = name
        family = "whisper"
        engine = "auto"

        def __init__(self, **kw):
            builds.append(time.monotonic())
            if not gate.wait(timeout=30.0):
                raise RuntimeError("test never released the engine build")

        def subscribe(self, fn):
            pass

        def start(self):
            pass

        def stop(self, drain=True):
            pass

        def pending(self):
            return 0

        def is_alive(self):
            return True

        def on_chunk(self, source, audio, t_start, block=False, timeout=None):
            return True

    return GatedEngine


# --- (1) the budget itself ----------------------------------------------------------------------

def test_load_budget_is_device_aware():
    # CPU gets the generous budget because a healthy first load there is minutes; CUDA and Metal keep
    # 120 s, where a long wait really is a hang.
    assert webapp.PREPARE_LOAD_TIMEOUT_SECONDS == 120, webapp.PREPARE_LOAD_TIMEOUT_SECONDS
    assert webapp.PREPARE_LOAD_TIMEOUT_SECONDS_CPU == 600, webapp.PREPARE_LOAD_TIMEOUT_SECONDS_CPU
    for tier in ("cpu", "cpu-min", "cpu-mid", "cpu-strong", "cpu-large"):
        assert webapp.load_device_for(tier) == "cpu", tier
        assert webapp.load_budget_seconds(tier) == 600, tier
    for tier in ("gpu", "gpu-4gb", "gpu-turbo", "gpu-medium", "gpu-small"):
        assert webapp.load_device_for(tier) == "cuda", tier
        assert webapp.load_budget_seconds(tier) == 120, tier
    for tier in ("mlx", "mlx-turbo"):
        assert webapp.load_device_for(tier) == "mlx", tier
        assert webapp.load_budget_seconds(tier) == 120, tier
    # An unknown tier is treated as CPU: the conservative answer (wait longer, never cut a real load
    # short), and the device can also be asked for directly.
    assert webapp.load_budget_seconds("no-such-tier") == 600
    assert webapp.load_budget_seconds(device="cpu") == 600
    assert webapp.load_budget_seconds(device="cuda") == 120
    print("  OK  load budget is device-aware: 600 s on CPU, 120 s on CUDA/Metal, one function")


# --- (2) honest waiting -------------------------------------------------------------------------

def test_slow_cpu_load_stays_loading_not_error():
    # The GPU budget is scaled to 0.4 s and the CPU budget to 30 s, so "still loading well past the
    # CUDA budget" is reproduced in a couple of seconds. A CPU session must stay in phase "loading"
    # with a rising elapsed counter and prepare_error None, and must pick up the slow hint.
    reset_state()
    gate = threading.Event()
    builds = []
    try:
        with stubs(_gated_engine(gate, builds), cpu_budget=30.0, gpu_budget=0.4, hint=1.0):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            # Well past the CUDA budget (0.4 s), and past the hint threshold (1.0 s).
            time.sleep(2.0)
            st = client.get("/api/status").json()
            assert st["running"] is True, st
            assert st["prepare_error"] is None, f"a slow CPU load must NOT look like a failure: {st}"
            assert st["preparing"] is True and st["model_ready"] is False, st
            prep = st["prepare"] or {}
            assert prep.get("phase") == "loading", prep
            assert (prep.get("elapsed") or 0) >= 1.0, f"no elapsed counter while loading: {prep}"
            assert prep.get("budget") == 30.0, prep
            assert prep.get("slow") is True, f"the CPU 'this takes a few minutes' hint never armed: {prep}"
            # Releasing the build still ends in a normal ready session.
            gate.set()
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 10.0), \
                "model_ready never flipped after the slow load finished"
            assert len(builds) == 1, f"the slow load must be built exactly once: {len(builds)}"
    finally:
        gate.set()
        reset_state()
    print("  OK  a slow CPU load stays 'loading' with an elapsed counter and the hint, never the error screen")


def test_cpu_budget_exhaustion_is_still_an_error():
    # The budget is generous, not infinite: once it is gone the failure is surfaced, retryably, and
    # the session keeps capturing.
    reset_state()
    gate = threading.Event()
    builds = []
    try:
        with stubs(_gated_engine(gate, builds), cpu_budget=0.5, gpu_budget=0.5):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            assert wait_until(lambda: client.get("/api/status").json().get("prepare_error"), 10.0), \
                "an exhausted CPU budget never surfaced an error"
            st = client.get("/api/status").json()
            assert st["running"] is True, st            # still capturing; audio is not lost
            assert "did not finish loading" in st["prepare_error"], st["prepare_error"]
    finally:
        gate.set()
        reset_state()
    print("  OK  an exhausted CPU budget is still a clear, retryable error with capture still running")


# --- (3) retry attaches, never duplicates -------------------------------------------------------

def test_retry_attaches_to_the_live_load():
    # THE regression: after a load timeout, Retry used to construct a SECOND Engine, which could only
    # sit behind the first on the model build lock and then take the cache hit, so the user waited the
    # whole load again. Retry must attach to the load already running: exactly one construction.
    reset_state()
    gate = threading.Event()
    builds = []
    try:
        with stubs(_gated_engine(gate, builds), cpu_budget=0.5, gpu_budget=0.5) as _tmp:
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            assert wait_until(lambda: client.get("/api/status").json().get("prepare_error"), 10.0), \
                "the budget never expired"
            assert len(builds) == 1, f"expected one Engine construction before Retry, got {len(builds)}"
            assert webapp._load_in_flight(("cpu", "en", None, "auto")) is True, \
                "the load registry lost the still-running load"
            # Give the retry a real budget, then retry: it must join the SAME load.
            webapp.PREPARE_LOAD_TIMEOUT_SECONDS_CPU = 30.0
            rr = client.post("/api/prepare/retry")
            assert rr.status_code == 200, rr.text
            time.sleep(1.0)
            assert len(builds) == 1, \
                f"Retry constructed a SECOND engine instead of attaching: {len(builds)} builds"
            st = client.get("/api/status").json()
            assert st["prepare_error"] is None and st["preparing"] is True, st
            # The one load finishes: the attached retry claims that engine and goes ready.
            gate.set()
            assert wait_until(lambda: client.get("/api/status").json().get("model_ready") is True, 15.0), \
                "the attached retry never went ready"
            assert len(builds) == 1, f"a duplicate engine appeared after all: {len(builds)} builds"
            # The claimed load is forgotten, so a later session builds fresh rather than reusing it.
            assert webapp._load_in_flight(("cpu", "en", None, "auto")) is False, \
                "a claimed load stayed in the registry"
    finally:
        gate.set()
        reset_state()
    print("  OK  Retry attaches to the load already in flight: one Engine construction, not two")


def test_a_failed_load_is_never_attached_to():
    # A load that RAISED must not be reused: Retry has to genuinely try again.
    reset_state()
    attempts = []

    class FailingEngine:
        def __init__(self, **kw):
            attempts.append(1)
            raise RuntimeError("boom: model file missing")

    try:
        with stubs(FailingEngine, cpu_budget=30.0):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            assert wait_until(lambda: client.get("/api/status").json().get("prepare_error"), 10.0), \
                "the failing build never surfaced an error"
            assert len(attempts) == 1, attempts
            rr = client.post("/api/prepare/retry")
            assert rr.status_code == 200, rr.text
            assert wait_until(lambda: len(attempts) >= 2, 10.0), \
                "Retry after a FAILED load must build again, not reuse the failure"
    finally:
        reset_state()
    print("  OK  a failed load is retried for real, never attached to")


# --- (4) no silent loss -------------------------------------------------------------------------

def test_stop_before_the_model_loads_marks_the_gap():
    # Stopping while the model has never finished loading leaves held audio with nowhere to go: the
    # transcript must SAY so. Before this it simply ended short, with no marker at all.
    reset_state()
    gate = threading.Event()
    builds = []
    try:
        with stubs(_gated_engine(gate, builds), cpu_budget=30.0):
            r = client.post("/api/start", json=START_BODY)
            assert r.status_code == 200, r.text
            out = Path(r.json()["output_path"])
            # ~90 s of held audio: 6 chunks of 15 s per source, none of it ever transcribed.
            for i in range(6):
                for src in ("MIC", "SYS"):
                    webapp._feed(src, np.zeros(15 * 16000, dtype=np.float32), float(i * 15))
            assert webapp.STATE.engine is None and webapp.STATE.preparing_engine is None, "engine appeared"
            rs = client.post("/api/stop?what=all")
            assert rs.status_code == 200, rs.text
            assert wait_until(lambda: not webapp.STATE.running, 15.0), "stop never finalised"
            text = out.read_text(encoding="utf-8")
            assert "not transcribed live" in text, f"the lost audio was not marked:\n{text}"
            assert "1 min 30 s" in text, f"the marked gap does not match the held audio:\n{text}"
    finally:
        gate.set()
        reset_state()
    print("  OK  a stop before the model loads states the untranscribed gap in the transcript")


def test_pending_buffer_reports_what_it_evicted():
    # The cap drops audio when a load runs very long. That drop is now measurable, so it can be
    # admitted in the transcript instead of only in the console log.
    pb = webapp._PendingAudio(16000 * 10)        # 10 s of samples
    assert pb.dropped_span() == (None, 0.0)
    for i in range(6):                            # 6 x 4 s = 24 s into a 10 s buffer
        pb.append("SYS", np.zeros(4 * 16000, dtype=np.float32), float(i * 4))
    t0, secs = pb.dropped_span()
    assert t0 == 0.0, t0
    assert secs >= 8.0, f"evicted span looks wrong: {secs}"
    held_t0, held = pb.held_span()
    assert held_t0 is not None and held > 0, (held_t0, held)
    print("  OK  the pending buffer reports the span it evicted and the span it still holds")


def test_gap_wording():
    assert webapp._fmt_gap(47) == "47 s"
    assert webapp._fmt_gap(327) == "5 min 27 s"
    seen = []
    webapp._note_untranscribed(lambda seg: seen.append(seg), None, 2.0, 327, True)
    assert len(seen) == 1, seen
    assert seen[0].text == ("[engine: 5 min 27 s before the model loaded were not transcribed live, "
                            "the recording still has them]"), seen[0].text
    seen2 = []
    webapp._note_untranscribed(lambda seg: seen2.append(seg), None, 2.0, 327, False)
    assert "there is no recording of them" in seen2[0].text, seen2[0].text
    # Rounding noise is not a gap worth a line.
    seen3 = []
    webapp._note_untranscribed(lambda seg: seen3.append(seg), None, 0.0, 0.2, True)
    assert seen3 == [], seen3
    print("  OK  the untranscribed-audio notice reads honestly and only fires for a real gap")


# --- (5) pre-warm at app start ------------------------------------------------------------------

@contextlib.contextmanager
def prewarm_stubs(tier, present):
    calls = []
    saved = (webapp.resolve_tier_engine, webapp._resolve_download_plan,
             webapp.transcribe.warm_up_async, webapp.config.load, webapp._start_model_download)
    webapp.config.load = lambda: {"tier": "auto", "device": "auto", "language": "af", "engine": "auto"}
    webapp.resolve_tier_engine = lambda quality, device, language, engine: (tier, None)
    webapp._resolve_download_plan = lambda t, lang, eng: {
        "present": present, "model": "fake", "target": "fake", "family": "whisper",
        "size": "small", "label": "Fast", "approx_bytes": 0}

    def _warm(t, language=None, engine="auto"):
        calls.append((t, language, engine))
        return {"state": "warming", "tier": t}

    def _no_download(*a, **kw):
        raise AssertionError("pre-warm must never start a download")

    webapp.transcribe.warm_up_async = _warm
    webapp._start_model_download = _no_download
    try:
        yield calls
    finally:
        (webapp.resolve_tier_engine, webapp._resolve_download_plan,
         webapp.transcribe.warm_up_async, webapp.config.load, webapp._start_model_download) = saved


def test_prewarm_only_on_cpu_with_the_model_on_disk():
    reset_state()
    # CPU tier, model already downloaded: warm it.
    with prewarm_stubs("cpu-mid", True) as calls:
        res = webapp.prewarm_at_startup()
        assert calls == [("cpu-mid", "af", "auto")], calls
        assert res.get("state") == "warming", res
    # CUDA tier: nothing to hide there, so no warm at all.
    with prewarm_stubs("gpu", True) as calls:
        res = webapp.prewarm_at_startup()
        assert calls == [], calls
        assert res.get("why") == "not a CPU tier", res
    # Apple Metal: same.
    with prewarm_stubs("mlx", True) as calls:
        res = webapp.prewarm_at_startup()
        assert calls == [], calls
        assert res.get("why") == "not a CPU tier", res
    # CPU tier but the model is not on disk: never download at app start.
    with prewarm_stubs("cpu-mid", False) as calls:
        res = webapp.prewarm_at_startup()
        assert calls == [], calls
        assert res.get("why") == "model not downloaded", res
    print("  OK  pre-warm runs only for a CPU tier whose model is already downloaded, and never downloads")


def test_prewarm_stands_down_and_can_be_switched_off():
    reset_state()
    with prewarm_stubs("cpu-mid", True) as calls:
        with webapp.STATE.lock:
            webapp.STATE.running = True
        try:
            res = webapp.prewarm_at_startup()
        finally:
            reset_state()
        assert calls == [], calls
        assert res.get("why") == "session running", res
    with prewarm_stubs("cpu-mid", True) as calls:
        os.environ["SA_LIVE_PREWARM"] = "0"
        try:
            res = webapp.prewarm_at_startup()
        finally:
            os.environ.pop("SA_LIVE_PREWARM", None)
        assert calls == [], calls
        assert res.get("why") == "disabled", res
    # And it is actually wired to app start, off the startup path (a thread, so nothing blocks).
    assert webapp._prewarm_on_startup in app.router.on_startup, \
        "the pre-warm is not registered as an ASGI startup hook"
    print("  OK  pre-warm stands down during a session, has an off switch, and is wired to app start")


TESTS = [
    test_load_budget_is_device_aware,
    test_slow_cpu_load_stays_loading_not_error,
    test_cpu_budget_exhaustion_is_still_an_error,
    test_retry_attaches_to_the_live_load,
    test_a_failed_load_is_never_attached_to,
    test_stop_before_the_model_loads_marks_the_gap,
    test_pending_buffer_reports_what_it_evicted,
    test_gap_wording,
    test_prewarm_only_on_cpu_with_the_model_on_disk,
    test_prewarm_stands_down_and_can_be_switched_off,
]


if __name__ == "__main__":
    print("CPU model load: device-aware budget, honest waiting, attaching retry, no silent loss")
    for t in TESTS:
        t()
    print("ALL OK")
