"""Tests for the live processor switch (GPU<->CPU mid-meeting): POST /api/reconfigure {device}.

Why this exists: on 2026-09-11 an RTX 3090 session fell four minutes behind because the card was
starved by another program, and the engine could only warn. The owner wanted an escape hatch to the
CPU (and back) without ending the meeting. These tests pin the endpoint contract AND the six codex
findings from the 2026-09-15 review:

  * a device switch on a fake Windows + CUDA machine is accepted and returns "preparing" at once, and
    only reaches "ready" once the worker has APPLIED the swap (K3);
  * it is refused with 400 when there is no usable GPU;
  * one switch at a time (409), and a language/quality reconfigure is refused while preparing (K2);
  * an old session's helper thread never clobbers a new session's switch, on either its success or
    its failure path (K1);
  * the helper hands the engine the right model id for a stock English turbo and a Fluister Afrikaans
    session, on cpu/int8;
  * a switch back to the GPU restores the exact size the session had on the GPU, not a CPU-ladder
    downgrade rung (K5), and says so when the remembered model is gone;
  * a session that ended (or was superseded) while the model built discards the built model.

No audio and no real model: cudadl is stubbed to look like a ready NVIDIA machine, and
transcribe.load_model is intercepted so the "build" is a sentinel object under our control. The fake
engine simulates the worker applying a queued change (it echoes the change_id into
_last_applied_change_id), which is exactly what the helper waits on.

Run:  python tests/test_reconfigure_device.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import threading
import time

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _isolate_settings  # noqa: F401  redirect settings to a temp copy in script mode (codex F7)

from fastapi.testclient import TestClient

from live_transcribe import cudadl
from live_transcribe import transcribe as T
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


class _FakeEngine:
    """The attributes the switch reads off the live engine, plus a request_change that records what
    the helper hands over and simulates the worker applying it (device/model identity move, and the
    change_id is acknowledged) so the helper's apply-wait can complete. ack=False defers the apply to
    apply_now(), to prove "ready" is only published after the worker applies (K3)."""
    def __init__(self, device="cuda", compute="int8_float16", size="large-v3-turbo", engine="auto",
                 model_name="model-x", family="fluister", ack=True):
        self._device = device
        self._is_cpu = device == "cpu"
        self._is_mlx = device == "mlx"
        self._compute_type = compute
        self._cpu_threads = 4
        self.size = size
        self.engine = engine
        self.model_name = model_name
        self.family = family
        self._last_applied_change_id = None
        self._ack = ack
        self._pending = None
        self.changes = []

    def request_change(self, **kw):
        self.changes.append(kw)
        self._pending = kw
        if self._ack:
            self._apply(kw)

    def _apply(self, kw):
        if kw.get("model") is not None:
            self.model_name = kw["model_name"]
            self.family = kw["family"]
            self.size = kw["size"]
            if kw.get("device"):
                self._device = kw["device"]
                self._is_cpu = kw["device"] == "cpu"
        self._last_applied_change_id = kw.get("change_id")

    def apply_now(self):
        if self._pending is not None:
            self._apply(self._pending)


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


def _install_session(engine, language="af", tier="gpu-turbo", model="model-x",
                     family="fluister", gpu_size=None):
    """Put a fake live session into STATE and return the tuple to restore it."""
    st = webapp.STATE
    saved = (st.running, st.transcribing, st.stopping, st.source_kind, st.engine,
             st.language, st.tier, st.model, st.family, st.processor_switch, st.gpu_size_before_cpu)
    st.running, st.transcribing, st.stopping = True, True, False
    st.source_kind, st.engine = "live", engine
    st.language, st.tier, st.model, st.family = language, tier, model, family
    st.processor_switch = None
    st.gpu_size_before_cpu = gpu_size
    return saved


def _restore_session(saved):
    st = webapp.STATE
    (st.running, st.transcribing, st.stopping, st.source_kind, st.engine,
     st.language, st.tier, st.model, st.family, st.processor_switch, st.gpu_size_before_cpu) = saved


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


def test_device_switch_accepted_preparing_then_ready_afrikaans():
    # A Fluister Afrikaans session on the GPU switches to the CPU: 200 + "preparing" at once, then the
    # helper hands the engine the CPU model (device "cpu", compute "int8", the Fluister turbo id), the
    # worker applies it, and STATE reaches "ready" with the confirmed identity.
    fake = _FakeEngine()
    loads = []
    orig_load = T.load_model

    def _fake_load(name, device, compute, cpu_threads=8, local_only=False):
        loads.append((name, device, compute))
        return object()

    fl_model, fl_family = T.resolve_model("large-v3-turbo", "af", "auto")
    assert fl_family == "fluister", (fl_model, fl_family)
    saved = _install_session(fake, language="af", model=fl_model)
    try:
        T.load_model = _fake_load
        with _cuda_ready(gpu=True, ready=True):
            r = client.post("/api/reconfigure", json={"device": "cpu"})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j["state"] == "preparing" and j["processor"] == "cpu" and j["token"] is not None, j
            ps = _wait_state("ready")
            assert ps and ps["state"] == "ready" and ps["target"] == "cpu", ps
        assert loads and loads[-1] == (fl_model, "cpu", "int8"), loads
        ch = fake.changes[-1]
        assert ch["device"] == "cpu" and ch["compute_type"] == "int8", ch
        assert ch["model_name"] == fl_model and ch["family"] == "fluister" and ch["size"] == "large-v3-turbo", ch
        assert ch["change_id"] == ps["token"], ("change_id must be the switch token", ch, ps)
        assert webapp.STATE.tier == "cpu-strong" and webapp.STATE.model == fl_model
        assert webapp.STATE.gpu_size_before_cpu == "large-v3-turbo", "leaving the GPU must remember the size"
    finally:
        T.load_model = orig_load
        _restore_session(saved)
    print("  OK  af GPU->CPU: preparing then ready after apply, fluister-turbo on cpu/int8, GPU size remembered")


def test_device_switch_stock_english_uses_stock_turbo_id():
    fake = _FakeEngine(engine="auto", family="whisper")
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
    finally:
        T.load_model = orig_load
        _restore_session(saved)
    print("  OK  en GPU->CPU: helper builds the stock large-v3-turbo id on cpu/int8")


def test_ready_only_after_worker_applies():
    # K3: request_change only QUEUES the swap; "ready" must not be published (and a swap-back must not
    # read the new device) until the worker actually applies it. With ack deferred, the switch stays
    # "preparing"; a second switch is refused; only after apply does it go "ready".
    fake = _FakeEngine(ack=False)
    orig_load = T.load_model
    fl_model, _ = T.resolve_model("large-v3-turbo", "af", "auto")
    saved = _install_session(fake, language="af", model=fl_model)
    try:
        T.load_model = lambda *a, **k: object()
        with _cuda_ready(gpu=True, ready=True):
            r = client.post("/api/reconfigure", json={"device": "cpu"})
            assert r.status_code == 200 and r.json()["state"] == "preparing", r.text
            # The worker has not applied yet: the switch must still be preparing, not ready.
            time.sleep(0.2)
            ps = webapp.STATE.processor_switch
            assert ps and ps["state"] == "preparing", ps
            # A second switch while preparing is refused.
            assert client.post("/api/reconfigure", json={"device": "cpu"}).status_code == 409
            # The worker applies the queued change: now (and only now) it reaches ready.
            fake.apply_now()
            ps = _wait_state("ready")
            assert ps and ps["state"] == "ready", ps
        assert webapp.STATE.tier == "cpu-strong", webapp.STATE.tier
    finally:
        T.load_model = orig_load
        _restore_session(saved)
    print("  OK  ready is published only after the worker applies the swap; second switch 409s while preparing")


def test_language_reconfigure_refused_while_preparing():
    # K2: while a processor switch is preparing it owns the engine's single pending-change slot, so a
    # language/quality change must be refused with 409 rather than race for that slot.
    fake = _FakeEngine()
    saved = _install_session(fake, language="af")
    try:
        with webapp.STATE.lock:
            webapp.STATE.reconfigure_gen += 1
            tok = webapp.STATE.reconfigure_gen
            webapp.STATE.processor_switch = {"token": tok, "target": "cpu", "state": "preparing", "error": None, "note": None}
        assert client.post("/api/reconfigure", json={"language": "en"}).status_code == 409
        assert client.post("/api/reconfigure", json={"tier": "small"}).status_code == 409
    finally:
        _restore_session(saved)
    print("  OK  a language/quality reconfigure is refused (409) while a processor switch is preparing")


def test_old_helper_does_not_clobber_a_new_sessions_switch():
    # K1: an old session's helper (its build still in flight) must be a NO-OP once a new session has
    # started its own switch. Test both the success and the failure paths of the old helper.
    for old_fails in (False, True):
        old_engine = _FakeEngine()
        release = threading.Event()
        started = threading.Event()
        orig_load = T.load_model

        def _blocking_load(name, device, compute, cpu_threads=8, local_only=False, _fail=old_fails):
            started.set()
            release.wait(5)
            if _fail:
                raise RuntimeError("boom")
            return object()

        saved = _install_session(old_engine, language="af")
        try:
            T.load_model = _blocking_load
            with _cuda_ready(gpu=True, ready=True):
                r = client.post("/api/reconfigure", json={"device": "cpu"})
                assert r.status_code == 200, r.text
                old_token = r.json()["token"]
                assert started.wait(5), "old helper never started building"
                # A replacement session starts and reserves its OWN switch (new engine + new token).
                new_engine = _FakeEngine()
                with webapp.STATE.lock:
                    webapp.STATE.engine = new_engine
                    webapp.STATE.reconfigure_gen += 1
                    new_token = webapp.STATE.reconfigure_gen
                    webapp.STATE.processor_switch = {"token": new_token, "target": "cpu", "state": "preparing", "error": None, "note": None}
                assert new_token != old_token
                # Let the old helper finish (success or failure) and try to publish.
                release.set()
                time.sleep(0.3)
                ps = webapp.STATE.processor_switch
                assert ps and ps["token"] == new_token and ps["state"] == "preparing", (old_fails, ps)
                assert old_engine.changes == [], "old helper must never submit to its engine"
        finally:
            release.set()
            T.load_model = orig_load
            _restore_session(saved)
    print("  OK  an old session's helper (success OR failure) never clobbers a new session's switch")


def test_switch_to_cpu_remembers_gpu_size_and_back_restores_it_from_base():
    # K5: after a CPU downgrade to `base`, switching back to the GPU must restore the size the session
    # had on the GPU (large-v3-turbo -> gpu-turbo), NOT re-resolve `base` onto CUDA (which would give
    # gpu-small, a silent weight change).
    fake = _FakeEngine(device="cpu", compute="int8", size="base", family="fluister")
    loads = []
    orig_load, orig_present = T.load_model, T.model_present
    fl_turbo, _ = T.resolve_model("large-v3-turbo", "af", "auto")
    saved = _install_session(fake, language="af", tier="cpu-min", model="fl-base", family="fluister",
                             gpu_size="large-v3-turbo")
    try:
        T.load_model = lambda name, device, compute, cpu_threads=8, local_only=False: loads.append((name, device, compute)) or object()
        T.model_present = lambda mid: True          # the remembered turbo is still on disk
        with _cuda_ready(gpu=True, ready=True):
            r = client.post("/api/reconfigure", json={"device": "cuda"})
            assert r.status_code == 200, r.text
            ps = _wait_state("ready")
            assert ps and ps["state"] == "ready" and ps["target"] == "gpu", ps
            assert ps.get("note") is None, ps
        assert webapp.STATE.tier == "gpu-turbo", ("must restore the GPU size, not resolve base", webapp.STATE.tier)
        assert loads and loads[-1] == (fl_turbo, "cuda", "int8_float16"), loads
        assert webapp.STATE.gpu_size_before_cpu is None, "back on the GPU: the memory must clear"
    finally:
        T.load_model, T.model_present = orig_load, orig_present
        _restore_session(saved)
    print("  OK  CPU(base)->GPU restores the remembered large-v3-turbo (gpu-turbo), not gpu-small")


def test_switch_back_from_tiny_restores_remembered_size():
    # The tiny rung is the worst case: resolving `tiny` onto CUDA falls through to gpu (large-v3). The
    # remembered-size restore must sidestep that entirely.
    fake = _FakeEngine(device="cpu", compute="int8", size="tiny", family="fluister")
    loads = []
    orig_load, orig_present = T.load_model, T.model_present
    fl_turbo, _ = T.resolve_model("large-v3-turbo", "af", "auto")
    saved = _install_session(fake, language="af", tier="cpu-min", model="fl-tiny", family="fluister",
                             gpu_size="large-v3-turbo")
    try:
        T.load_model = lambda name, device, compute, cpu_threads=8, local_only=False: loads.append((name, device, compute)) or object()
        T.model_present = lambda mid: True
        with _cuda_ready(gpu=True, ready=True):
            client.post("/api/reconfigure", json={"device": "cuda"})
            _wait_state("ready")
        assert webapp.STATE.tier == "gpu-turbo", webapp.STATE.tier
        assert loads[-1] == (fl_turbo, "cuda", "int8_float16"), loads
    finally:
        T.load_model, T.model_present = orig_load, orig_present
        _restore_session(saved)
    print("  OK  CPU(tiny)->GPU restores large-v3-turbo, never the gpu large-v3 tiny would resolve to")


def test_gpu_restore_falls_back_and_flags_when_remembered_model_is_gone():
    # K5 fallback: if the remembered model is no longer on disk, fall back to the current size and flag
    # it so the toast can say the size changed.
    fake = _FakeEngine(device="cpu", compute="int8", size="small", family="fluister")
    orig_load, orig_present = T.load_model, T.model_present
    saved = _install_session(fake, language="af", tier="cpu", model="fl-small", family="fluister",
                             gpu_size="large-v3-turbo")
    try:
        T.load_model = lambda *a, **k: object()
        T.model_present = lambda mid: False         # the remembered turbo is gone
        with _cuda_ready(gpu=True, ready=True):
            client.post("/api/reconfigure", json={"device": "cuda"})
            ps = _wait_state("ready")
            assert ps and ps["state"] == "ready" and ps.get("note") == "size_fallback", ps
    finally:
        T.load_model, T.model_present = orig_load, orig_present
        _restore_session(saved)
    print("  OK  a GPU restore whose remembered model is gone falls back to the current size and flags it")


def test_device_switch_rejected_when_no_gpu():
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
    print("  OK  device switch with no GPU -> 400, no reservation left behind")


def test_second_switch_while_preparing_gets_409():
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
        assert len(fake.changes) == 1, fake.changes
    finally:
        release.set()
        T.load_model = orig_load
        _restore_session(saved)
    print("  OK  a second switch while one is preparing -> 409; only one build reaches the engine")


def test_superseded_session_discards_the_built_model():
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
            with webapp.STATE.lock:
                webapp.STATE.running = False
            release.set()
            _wait_state(None)
        assert fake.changes == [], "a superseded switch must not swap the model"
        assert webapp.STATE.processor_switch is None, webapp.STATE.processor_switch
    finally:
        release.set()
        T.load_model = orig_load
        _restore_session(saved)
    print("  OK  a session that ended mid-build discards the built model (no request_change, state cleared)")


def test_switch_only_during_a_live_session():
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


TESTS = (test_device_switch_accepted_preparing_then_ready_afrikaans,
         test_device_switch_stock_english_uses_stock_turbo_id,
         test_ready_only_after_worker_applies,
         test_language_reconfigure_refused_while_preparing,
         test_old_helper_does_not_clobber_a_new_sessions_switch,
         test_switch_to_cpu_remembers_gpu_size_and_back_restores_it_from_base,
         test_switch_back_from_tiny_restores_remembered_size,
         test_gpu_restore_falls_back_and_flags_when_remembered_model_is_gone,
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
