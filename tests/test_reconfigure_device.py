"""Tests for the live processor switch (GPU<->CPU mid-meeting): POST /api/reconfigure {device}.

Why this exists: on 2026-09-11 an RTX 3090 session fell four minutes behind because the card was
starved by another program, and the engine could only warn. The owner wanted an escape hatch to the
CPU (and back) without ending the meeting. These tests pin the endpoint contract:

  * a device switch on a fake Windows + CUDA machine is accepted and returns "preparing" at once (the
    model build runs off the request thread, so the HTTP call never blocks on a cold CPU load);
  * it is refused with 400 when there is no usable GPU to switch between;
  * a second switch while one is in flight gets 409 (one at a time);
  * the helper thread hands the engine the right model: device "cpu", compute "int8", and the correct
    model id for BOTH a stock English turbo session (large-v3-turbo) and a Fluister Afrikaans session
    (digiphyte/fluister-turbo);
  * a session that ended (or was superseded) while the model built discards the built model and never
    installs it.

No audio and no real model: cudadl is stubbed to look like a ready NVIDIA machine, and
transcribe.load_model is intercepted so the "build" is a sentinel object under our control.

Run:  python tests/test_reconfigure_device.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import threading
import time

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe import cudadl
from live_transcribe import transcribe as T
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


class _FakeEngine:
    """The attributes _reconfigure_device / _run_processor_switch read off the live engine, plus a
    request_change that records what the helper thread eventually hands over."""
    def __init__(self, device="cuda", compute="int8_float16", size="large-v3-turbo", engine="auto"):
        self._device = device
        self._is_cpu = device == "cpu"
        self._compute_type = compute
        self._cpu_threads = 4
        self.size = size
        self.engine = engine
        self.changes = []

    def request_change(self, **kw):
        self.changes.append(kw)


class _cuda_ready:
    """Make cudadl look like a ready NVIDIA Windows machine (or, with gpu=False, a machine with no
    usable GPU). Restored on exit."""
    def __init__(self, gpu=True, ready=True):
        self.gpu, self.ready = gpu, ready

    def __enter__(self):
        self._saved = (cudadl.SUPPORTED, cudadl.gpu_present, cudadl.cuda_ready, cudadl.installed)
        cudadl.SUPPORTED = True
        cudadl.gpu_present = lambda: self.gpu
        cudadl.cuda_ready = lambda: self.ready
        cudadl.installed = lambda: self.ready
        return self

    def __exit__(self, *exc):
        cudadl.SUPPORTED, cudadl.gpu_present, cudadl.cuda_ready, cudadl.installed = self._saved


def _install_session(engine, language="af", tier="gpu-turbo", model="digiphyte/fluister-turbo",
                     family="fluister"):
    """Put a fake live session into STATE and return the tuple to restore it."""
    st = webapp.STATE
    saved = (st.running, st.transcribing, st.stopping, st.source_kind, st.engine,
             st.language, st.tier, st.model, st.family, st.processor_switch)
    st.running, st.transcribing, st.stopping = True, True, False
    st.source_kind, st.engine = "live", engine
    st.language, st.tier, st.model, st.family = language, tier, model, family
    st.processor_switch = None
    return saved


def _restore_session(saved):
    st = webapp.STATE
    (st.running, st.transcribing, st.stopping, st.source_kind, st.engine,
     st.language, st.tier, st.model, st.family, st.processor_switch) = saved


def _wait_state(want, timeout=5.0):
    """Wait for STATE.processor_switch to reach a state (or clear to None)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ps = webapp.STATE.processor_switch
        cur = ps.get("state") if ps else None
        if cur == want:
            return ps
        time.sleep(0.01)
    return webapp.STATE.processor_switch


def test_device_switch_accepted_returns_preparing_and_builds_off_thread_afrikaans():
    # A Fluister Afrikaans session on the GPU switches to the CPU: 200 + "preparing" at once, then
    # the helper thread hands the engine the CPU model (device "cpu", compute "int8", the Fluister
    # turbo id), and STATE reaches "ready".
    fake = _FakeEngine()
    loads = []
    orig_load = T.load_model

    def _fake_load(name, device, compute, cpu_threads=8, local_only=False):
        loads.append((name, device, compute))
        return object()

    # The concrete Fluister turbo id is machine-dependent (a hosted repo, or a local ct2 build), so
    # ask the resolver for the id this machine uses rather than hard-coding one.
    fl_model, fl_family = T.resolve_model("large-v3-turbo", "af", "auto")
    assert fl_family == "fluister", (fl_model, fl_family)
    saved = _install_session(fake, language="af", model=fl_model)
    try:
        T.load_model = _fake_load
        with _cuda_ready(gpu=True, ready=True):
            r = client.post("/api/reconfigure", json={"device": "cpu"})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j["state"] == "preparing" and j["processor"] == "cpu", j
            ps = _wait_state("ready")
            assert ps and ps["state"] == "ready" and ps["target"] == "cpu", ps
        # The helper thread built on the CPU with int8, and the model id stayed the Fluister turbo.
        assert loads and loads[-1] == (fl_model, "cpu", "int8"), loads
        ch = fake.changes[-1]
        assert ch["device"] == "cpu" and ch["compute_type"] == "int8", ch
        assert ch["model_name"] == fl_model and ch["family"] == "fluister", ch
        assert ch["size"] == "large-v3-turbo", ch
        assert webapp.STATE.tier == "cpu-strong" and webapp.STATE.model == fl_model
    finally:
        T.load_model = orig_load
        _restore_session(saved)
    print("  OK  af GPU->CPU switch: 200 preparing, helper builds fluister-turbo on cpu/int8, engine swapped, ready")


def test_device_switch_stock_english_uses_stock_turbo_id():
    # The same path for a stock English turbo session: the model id is the stock large-v3-turbo, not
    # a Fluister repo, and it still lands on the CPU at int8.
    fake = _FakeEngine(engine="auto")
    loads = []
    orig_load = T.load_model

    def _fake_load(name, device, compute, cpu_threads=8, local_only=False):
        loads.append((name, device, compute))
        return object()

    saved = _install_session(fake, language="en", tier="gpu-turbo", model="large-v3-turbo", family="whisper")
    try:
        T.load_model = _fake_load
        with _cuda_ready(gpu=True, ready=True):
            r = client.post("/api/reconfigure", json={"device": "cpu"})
            assert r.status_code == 200, r.text
            _wait_state("ready")
        assert loads and loads[-1] == ("large-v3-turbo", "cpu", "int8"), loads
        ch = fake.changes[-1]
        assert ch["model_name"] == "large-v3-turbo" and ch["family"] == "whisper", ch
        assert ch["device"] == "cpu" and ch["compute_type"] == "int8", ch
    finally:
        T.load_model = orig_load
        _restore_session(saved)
    print("  OK  en GPU->CPU switch: helper builds the stock large-v3-turbo id on cpu/int8")


def test_device_switch_rejected_when_no_gpu():
    # No usable GPU on the machine: there is nothing to switch between, so 400 (not a silent no-op).
    fake = _FakeEngine()
    saved = _install_session(fake, language="af")
    try:
        with _cuda_ready(gpu=False, ready=False):
            r = client.post("/api/reconfigure", json={"device": "cpu"})
            assert r.status_code == 400, r.text
            assert "NVIDIA GPU" in r.json().get("detail", ""), r.json()
        assert webapp.STATE.processor_switch is None, webapp.STATE.processor_switch
    finally:
        _restore_session(saved)
    print("  OK  device switch with no GPU -> 400, no switch state left behind")


def test_second_switch_while_preparing_gets_409():
    # One at a time: while the first build is in flight (load_model parked on an event), a second
    # switch is refused with 409.
    fake = _FakeEngine()
    release = threading.Event()
    started = threading.Event()
    orig_load = T.load_model

    def _blocking_load(name, device, compute, cpu_threads=8, local_only=False):
        started.set()
        release.wait(5)
        return object()

    saved = _install_session(fake, language="af")
    try:
        T.load_model = _blocking_load
        with _cuda_ready(gpu=True, ready=True):
            r1 = client.post("/api/reconfigure", json={"device": "cpu"})
            assert r1.status_code == 200 and r1.json()["state"] == "preparing", r1.text
            assert started.wait(5), "the helper thread never started building"
            r2 = client.post("/api/reconfigure", json={"device": "cpu"})
            assert r2.status_code == 409, r2.text
            release.set()
            _wait_state("ready")
        # Exactly one switch actually reached the engine.
        assert len(fake.changes) == 1, fake.changes
    finally:
        release.set()
        T.load_model = orig_load
        _restore_session(saved)
    print("  OK  a second switch while one is preparing -> 409; only one build reaches the engine")


def test_superseded_session_discards_the_built_model():
    # The session ends while the model builds: the helper thread must re-check and discard the built
    # model rather than swap it into a dead session.
    fake = _FakeEngine()
    release = threading.Event()
    started = threading.Event()
    orig_load = T.load_model

    def _blocking_load(name, device, compute, cpu_threads=8, local_only=False):
        started.set()
        release.wait(5)
        return object()

    saved = _install_session(fake, language="af")
    try:
        T.load_model = _blocking_load
        with _cuda_ready(gpu=True, ready=True):
            r = client.post("/api/reconfigure", json={"device": "cpu"})
            assert r.status_code == 200, r.text
            assert started.wait(5), "the helper thread never started building"
            # End the session out from under the in-flight build, then let the build finish.
            with webapp.STATE.lock:
                webapp.STATE.running = False
            release.set()
            _wait_state(None)      # the helper clears its own preparing state on discard
        assert fake.changes == [], "a superseded switch must not swap the model"
        assert webapp.STATE.processor_switch is None, webapp.STATE.processor_switch
    finally:
        release.set()
        T.load_model = orig_load
        _restore_session(saved)
    print("  OK  a session that ended mid-build discards the built model (no request_change, state cleared)")


def test_switch_only_during_a_live_session():
    # No live session: the endpoint refuses with 409 before any build, exactly like the language/model
    # reconfigure. (The gate is the same STATE predicate.)
    st = webapp.STATE
    saved = (st.running, st.transcribing, st.stopping, st.source_kind, st.engine, st.processor_switch)
    try:
        st.running, st.transcribing, st.stopping = False, False, False
        st.source_kind, st.engine, st.processor_switch = None, None, None
        with _cuda_ready(gpu=True, ready=True):
            r = client.post("/api/reconfigure", json={"device": "cpu"})
            assert r.status_code == 409, r.text
    finally:
        (st.running, st.transcribing, st.stopping, st.source_kind, st.engine, st.processor_switch) = saved
    print("  OK  a device switch with no live session -> 409")


TESTS = (test_device_switch_accepted_returns_preparing_and_builds_off_thread_afrikaans,
         test_device_switch_stock_english_uses_stock_turbo_id,
         test_device_switch_rejected_when_no_gpu,
         test_second_switch_while_preparing_gets_409,
         test_superseded_session_discards_the_built_model,
         test_switch_only_during_a_live_session)

if __name__ == "__main__":
    failures = 0
    for fn in TESTS:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll reconfigure-device tests passed.")
