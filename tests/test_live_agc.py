"""Tests for live mic auto-gain (WebRTC AGC on the mic path, v1.11.2).

Server-side and worker seams only, no model load, no audio devices, no LiveKit
binding needed (the APM is stubbed or faked):
  - agc_live setting: default ON, round-trips through config and /api/settings.
  - LiveAEC frame routing: with AGC on, the BYPASSED near end is emitted through
    the AGC-only APM (not raw); with AEC active the main APM output is emitted;
    a mic-only worker (far_rate=None) runs AGC-only.
  - Fail-open: a broken AGC APM (at construction or mid-stream) never blocks
    capture; the mic falls back to raw/AEC-only with the worker still running.
  - Capture-level state: an AGC-only worker does not count as an available echo
    canceller (set_aec/aec_state honesty), and a missing binding degrades a
    mic-only session to plain native-rate capture.

Run:  python tests/test_live_agc.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import threading
import types

import numpy as np

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe import config
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


# ---------------------------------------------------------------------------
# Stub APM plumbing (mirrors test_recorder_align's pacing-guard stubs)
# ---------------------------------------------------------------------------

class _StubFrame:
    def __init__(self, data, rate, ch, samples):
        self.data = data


class _PassthroughAPM:
    """Stands in for the main (AEC) APM: output == input."""
    def process_reverse_stream(self, frame):
        pass

    def process_stream(self, frame):
        pass


class _GainAPM:
    """Stands in for the AGC-only APM: applies a fixed x4 gain in the int16 domain."""
    def __init__(self, gain=4.0):
        self.gain = gain
        self.frames = 0

    def process_stream(self, frame):
        self.frames += 1
        arr = np.frombuffer(bytes(frame.data), dtype=np.int16).astype(np.float32)
        frame.data = np.clip(arr * self.gain, -32768, 32767).astype("<i2").tobytes()


class _BrokenAPM:
    def process_stream(self, frame):
        raise RuntimeError("synthetic APM failure")


def _mk_worker(near_rate=16000, far_rate=16000, bypass=True, agc=True):
    from live_transcribe.aec_live import LiveAEC
    near_out, far_out = [], []
    la = LiveAEC(near_rate, far_rate,
                 on_near=near_out.append,
                 on_far=(far_out.append if far_rate is not None else None),
                 bypass=bypass, agc=agc)
    la._AudioFrame = _StubFrame   # what start() would set via livekit.rtc
    return la, near_out, far_out


def test_agc_setting_default_and_roundtrip():
    # agc_live defaults ON (the Meet/Teams behaviour) and round-trips through config
    # and the /api/settings endpoint. Restores the user's real value afterwards.
    assert config.DEFAULTS.get("agc_live") is True, "agc_live must default ON"
    orig = config.load().get("agc_live", True)
    try:
        config.update({"agc_live": False})
        assert config.load().get("agc_live") is False, "OFF did not persist"
        j = client.post("/api/settings", json={"agc_live": True}).json()
        assert j.get("agc_live") is True, j
        assert config.load().get("agc_live") is True, "endpoint write did not persist"
    finally:
        config.update({"agc_live": orig})
    print("  OK  agc_live: default ON, config + /api/settings round-trip")


def test_agc_bypass_emits_gained_not_raw():
    # AEC bypassed + AGC on: the near end must be emitted through the AGC-only APM
    # (boosted), NOT raw - the AEC toggle means echo cancellation, never gain.
    from live_transcribe.aec_live import FRAME
    la, near_out, _far = _mk_worker(bypass=True, agc=True)
    la._apm = _PassthroughAPM()
    gain = _GainAPM(4.0)
    la._apm_agc = gain
    sig = np.full(FRAME * 3, 0.05, dtype=np.float32)
    la.push_near(sig.copy())
    la._pump()
    got = np.concatenate(near_out)
    assert got.shape == sig.shape
    assert np.allclose(got, 0.20, atol=1e-3), f"bypassed mic not AGC-boosted (mean {got.mean():.3f})"
    assert gain.frames == 3, "AGC APM must be fed every frame"
    # Flip AEC ON: the main (passthrough stub) APM output is emitted instead, so the
    # x4 stub gain disappears; the AGC APM keeps being fed so its state stays warm.
    la.bypass = False
    la.push_near(sig.copy())
    la._pump()
    got2 = np.concatenate(near_out)[len(sig):]
    assert np.allclose(got2, 0.05, atol=1e-3), "active-AEC path must emit the main APM output"
    assert gain.frames == 6, "AGC APM must stay fed while AEC is active"
    print("  OK  LiveAEC: bypass emits AGC-boosted mic, active emits main APM, both APMs stay fed")


def test_agc_mic_only_worker():
    # far_rate=None: an AGC-only worker for a mic-only session. No far end, no AEC
    # (aec_capable False), the near end still comes out gain-controlled.
    from live_transcribe.aec_live import FRAME, LiveAEC
    la, near_out, _far = _mk_worker(far_rate=None, bypass=True, agc=True)
    assert la.aec_capable is False
    la._apm = None
    la._apm_agc = _GainAPM(2.0)
    sig = np.full(FRAME * 2, 0.1, dtype=np.float32)
    la.push_near(sig.copy())
    la._pump()
    got = np.concatenate(near_out)
    assert np.allclose(got, 0.2, atol=1e-3), "mic-only worker must emit the AGC output"
    # A mic-only worker without AGC has no reason to exist: constructor refuses it.
    try:
        LiveAEC(16000, None, on_near=lambda x: None, on_far=None, agc=False)
        raise AssertionError("mic-only LiveAEC without AGC must be rejected")
    except ValueError:
        pass
    print("  OK  LiveAEC mic-only: AGC-only worker, aec_capable False, agc=False rejected")


def test_agc_fail_open_midstream():
    # A mid-stream AGC failure must never block capture: the worker drops AGC for the
    # session (falls back to raw on the bypass path) and keeps emitting.
    from live_transcribe.aec_live import FRAME
    la, near_out, _far = _mk_worker(bypass=True, agc=True)
    la._apm = _PassthroughAPM()
    la._apm_agc = _BrokenAPM()
    sig = np.full(FRAME * 2, 0.05, dtype=np.float32)
    la.push_near(sig.copy())
    la._pump()
    got = np.concatenate(near_out)
    assert np.array_equal(got, sig), "after an AGC failure the bypassed mic must fall back to raw"
    assert la._apm_agc is None, "a failed AGC APM must be dropped for the session"
    la.push_near(sig.copy())
    la._pump()
    assert sum(len(a) for a in near_out) == 2 * len(sig), "worker must keep running after the failure"
    print("  OK  LiveAEC: mid-stream AGC failure fails open to raw, worker keeps running")


def test_agc_constructor_fail_open_with_fake_binding():
    # start() against a livekit binding whose APM rejects auto_gain_control: with a far
    # end present, echo cancellation must survive (AEC-only retry) instead of the whole
    # worker dying; the AGC-only APM is dropped with a log line.
    from live_transcribe.aec_live import LiveAEC

    class _FakeAPM:
        def __init__(self, *, echo_cancellation=False, noise_suppression=False,
                     high_pass_filter=False, auto_gain_control=False):
            if auto_gain_control:
                raise RuntimeError("no AGC in this fake binding")

        def process_reverse_stream(self, frame):
            pass

        def process_stream(self, frame):
            pass

    fake_apm_mod = types.ModuleType("livekit.rtc.apm")
    fake_apm_mod.AudioProcessingModule = _FakeAPM
    fake_rtc_mod = types.ModuleType("livekit.rtc")
    fake_rtc_mod.apm = fake_apm_mod
    fake_rtc_mod.AudioFrame = _StubFrame
    fake_root = types.ModuleType("livekit")
    fake_root.rtc = fake_rtc_mod
    saved = {k: sys.modules.get(k) for k in ("livekit", "livekit.rtc", "livekit.rtc.apm")}
    sys.modules["livekit"] = fake_root
    sys.modules["livekit.rtc"] = fake_rtc_mod
    sys.modules["livekit.rtc.apm"] = fake_apm_mod
    try:
        la = LiveAEC(16000, 16000, on_near=lambda x: None, on_far=lambda x: None,
                     bypass=False, agc=True)
        la.start()
        try:
            assert isinstance(la._apm, _FakeAPM), "main APM must be rebuilt AEC-only"
            assert la.agc is False and la._apm_agc is None, "AGC must be dropped, not fatal"
        finally:
            la.stop()
        # Mic-only worker: AGC is the whole job, so the same failure must RAISE and let
        # the capture-level catch degrade to plain capture.
        la2 = LiveAEC(16000, None, on_near=lambda x: None, on_far=None, bypass=True, agc=True)
        try:
            la2.start()
            raise AssertionError("mic-only start() must raise when AGC cannot be built")
        except RuntimeError:
            pass
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    print("  OK  LiveAEC.start(): AGC-less binding keeps AEC alive; mic-only raises for the caller")


class _FailsOnNthForward:
    """Main-APM stub whose process_stream raises on the Nth near frame."""
    def __init__(self, n):
        self.n = n
        self.calls = 0

    def process_reverse_stream(self, frame):
        pass

    def process_stream(self, frame):
        self.calls += 1
        if self.calls == self.n:
            raise RuntimeError("synthetic main-APM forward failure")


class _FailsOnReverse:
    """Main-APM stub whose process_reverse_stream raises on the Nth far frame."""
    def __init__(self, n=1):
        self.n = n
        self.calls = 0

    def process_reverse_stream(self, frame):
        self.calls += 1
        if self.calls >= self.n:
            raise RuntimeError("synthetic main-APM reverse failure")

    def process_stream(self, frame):
        pass


def _install_fake_livekit(apm_cls):
    """Install a fake livekit module tree so _retire_main's AEC-only rebuild can
    succeed; returns the saved modules for restoration."""
    fake_apm_mod = types.ModuleType("livekit.rtc.apm")
    fake_apm_mod.AudioProcessingModule = apm_cls
    fake_rtc_mod = types.ModuleType("livekit.rtc")
    fake_rtc_mod.apm = fake_apm_mod
    fake_rtc_mod.AudioFrame = _StubFrame
    fake_root = types.ModuleType("livekit")
    fake_root.rtc = fake_rtc_mod
    saved = {k: sys.modules.get(k) for k in ("livekit", "livekit.rtc", "livekit.rtc.apm")}
    sys.modules["livekit"] = fake_root
    sys.modules["livekit.rtc"] = fake_rtc_mod
    sys.modules["livekit.rtc.apm"] = fake_apm_mod
    return saved


def _restore_modules(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def test_main_apm_forward_failure_retires_and_flows():
    # F1: the main (AEC+AGC) APM raising in process_stream must NOT kill the worker.
    # With AGC compiled into the failed main, it is retried ONCE as AEC-only (fake
    # binding installed so the rebuild succeeds); the failing frame is emitted via
    # the AGC-only fallback and every later frame flows through the rebuilt main.
    from live_transcribe.aec_live import FRAME

    class _RebuiltAPM:
        """What the retire path rebuilds: a passthrough AEC-only APM."""
        def __init__(self, *, echo_cancellation=False, noise_suppression=False,
                     high_pass_filter=False, auto_gain_control=False):
            assert echo_cancellation and not auto_gain_control, \
                "retire must rebuild AEC-only (no AGC)"

        def process_reverse_stream(self, frame):
            pass

        def process_stream(self, frame):
            pass

    saved = _install_fake_livekit(_RebuiltAPM)
    try:
        la, near_out, _far = _mk_worker(bypass=False, agc=True)
        la._apm = _FailsOnNthForward(2)   # frame 1 OK, frame 2 raises
        la._apm_agc = _GainAPM(4.0)
        sig = np.full(FRAME * 4, 0.05, dtype=np.float32)
        la.push_near(sig.copy())
        la._pump()   # must not raise
        got = np.concatenate(near_out)
        assert len(got) == len(sig), "frames must keep flowing through the failure"
        f1, f2, f3, f4 = (got[i * FRAME:(i + 1) * FRAME] for i in range(4))
        assert np.allclose(f1, 0.05, atol=1e-3), "frame before the failure: main APM output"
        assert np.allclose(f2, 0.20, atol=1e-3), \
            "the failing frame must be emitted via the AGC-only fallback, not dropped"
        assert np.allclose(f3, 0.05, atol=1e-3) and np.allclose(f4, 0.05, atol=1e-3), \
            "frames after the retry must flow through the rebuilt AEC-only main"
        assert isinstance(la._apm, _RebuiltAPM), "main APM must be rebuilt AEC-only"
        assert la.aec_capable is True, "a successful AEC-only retry keeps AEC available"
        assert la.agc is False, "the rebuilt main carries no AGC"
        # A SECOND failure (retry already spent) retires AEC for good: frames still
        # flow (AGC-only fallback) and the worker reports honestly unavailable.
        la._apm = _FailsOnNthForward(1)
        la.push_near(sig.copy())
        la._pump()
        got2 = np.concatenate(near_out)[len(sig):]
        assert len(got2) == len(sig), "frames must keep flowing after the final retire"
        assert np.allclose(got2, 0.20, atol=1e-3), "post-retire mic takes the AGC-only path"
        assert la._apm is None and la.aec_capable is False
    finally:
        _restore_modules(saved)
    print("  OK  LiveAEC: forward main-APM failure -> AEC-only retry, then honest retire; frames never stop")


def test_main_apm_reverse_failure_retires_and_flows():
    # F1: process_reverse_stream raising must not kill the worker either. Main built
    # WITHOUT AGC (no retry path, no livekit needed): one failure retires AEC, far
    # frames keep flowing to the transcript, the near end falls back to raw, and
    # aec_state() reports unavailable so /api/status and the UI toggle go honest.
    from live_transcribe.aec_live import FRAME
    from live_transcribe.capture_core import CaptureBase

    la, near_out, far_out = _mk_worker(bypass=False, agc=False)
    la._apm = _FailsOnReverse(1)
    sig = np.full(FRAME * 3, 0.05, dtype=np.float32)
    la.push_far(sig.copy())
    la.push_near(sig.copy())
    la._pump()   # must not raise
    far_got = np.concatenate(far_out)
    assert len(far_got) == len(sig), "far frames must keep flowing after the reverse failure"
    assert np.allclose(far_got, 0.05, atol=1e-6), "far passthrough must be emitted unchanged"
    near_got = np.concatenate(near_out)
    assert len(near_got) == len(sig), "near frames must keep flowing after the reverse failure"
    assert np.array_equal(near_got, sig), "with no AGC the fallback near output is the raw mic"
    assert la._apm is None and la.aec_capable is False, "failed main must be retired for the session"
    cap = CaptureBase(aec=True)
    cap._live_aec = la
    assert cap.aec_state() == (False, False), "aec_state must report AEC unavailable after the retire"
    assert cap.set_aec(True) is False, "the toggle must refuse ON after the retire"
    # The worker keeps pumping on later blocks too.
    la.push_far(sig.copy())
    la.push_near(sig.copy())
    la._pump()
    assert sum(len(a) for a in near_out) == 2 * len(sig)
    assert sum(len(a) for a in far_out) == 2 * len(sig)
    print("  OK  LiveAEC: reverse-stream failure -> retire, far + near keep flowing, aec_state honest")


def test_late_far_blocks_bypass_agc_only_worker():
    # F2: a worker built far_rate=None (mic-only start; the macOS deferred TCC-grant
    # path) must NOT be handed late SYS blocks - it has no main APM and no on_far
    # sink, so they would be drained and discarded. They must land in SYS's own
    # native-rate buffer (the same path a no-worker session uses), with the SYS
    # meter fed from the raw block, while the mic keeps routing through the worker.
    from live_transcribe.capture_core import CaptureBase

    class _AgcOnlyWorker:
        aec_capable = False
        has_far = False        # far_rate=None: no far pipeline at all

        def __init__(self):
            self.near, self.far = [], []
            self.push_near = self.near.append
            self.push_far = self.far.append

    cap = CaptureBase(agc=True)
    cap._register_source("MIC", 16000, 1)
    w = _AgcOnlyWorker()
    cap._live_aec = w
    # The macOS deferred-permission handshake registers SYS late, at native rate.
    cap._register_source("SYS", 16000, 1)
    sys_block = np.full((800, 1), 0.25, dtype=np.float32)
    cap._ingest_block("SYS", sys_block)
    assert not w.far, "late SYS blocks must never reach an AGC-only worker's far queue"
    assert cap._buffer_counts["SYS"] == 800, "late SYS blocks must land in the native SYS buffer"
    assert cap._buffers["SYS"] and cap._buffers["SYS"][0] is sys_block, \
        "the SYS block must reach the downstream sink, not the void"
    assert cap.levels().get("SYS", {}).get("peak") == 0.25, "SYS meter must show the raw block"
    # Mic AGC unaffected: MIC still routes through the worker (meter fed post-APM).
    mic_block = np.full((800, 1), 0.10, dtype=np.float32)
    cap._ingest_block("MIC", mic_block)
    assert len(w.near) == 1 and cap._buffer_counts["MIC"] == 0, \
        "MIC must keep routing through the AGC worker"
    # Regression guard: a REAL two-ended worker still takes SYS blocks.
    class _FullWorker(_AgcOnlyWorker):
        aec_capable = True
        has_far = True
    cap2 = CaptureBase(aec=True)
    cap2._register_source("MIC", 16000, 1)
    cap2._register_source("SYS", 16000, 1)
    w2 = _FullWorker()
    cap2._live_aec = w2
    cap2._ingest_block("SYS", sys_block)
    assert len(w2.far) == 1 and cap2._buffer_counts["SYS"] == 0, \
        "a far-capable worker must keep taking SYS blocks"
    print("  OK  capture: late SYS blocks bypass an AGC-only worker into the native buffer (mac deferred-TCC path)")


def test_start_request_agc_live_overrides_settings():
    # F3: agc_live in the /api/start body must override the on-disk setting (the
    # Advanced toggle's save is async and unawaited, so toggle-then-immediately-Begin
    # must not start with the stale value); omitted -> settings default.
    import time as _time
    from live_transcribe import capture as capture_mod
    from live_transcribe.web import app as webapp

    calls = []

    class _FakeCap:
        def __init__(self, **kw):
            calls.append(kw)

        def start(self):
            pass

        def stop(self):
            pass

        def has_raw_mic(self):
            return False

        def attach_sys_ring(self, ring):
            pass

        def aec_state(self):
            return (False, False)

        def levels(self):
            return {}

    import tempfile
    from pathlib import Path
    d = Path(tempfile.mkdtemp())
    st = webapp.STATE
    orig_cap = capture_mod.AudioCapture
    orig_bop = webapp._build_output_path
    orig_bump = webapp._bump_session_count
    orig_setting = config.load().get("agc_live", True)

    def _start_and_stop(body, expect_agc):
        r = client.post("/api/start", json=body)
        assert r.status_code == 200, r.text
        assert calls[-1]["agc"] is expect_agc, (body, calls[-1]["agc"])
        r2 = client.post("/api/stop")
        assert r2.status_code == 200, r2.text
        deadline = _time.monotonic() + 10.0
        while _time.monotonic() < deadline:
            with st.lock:
                if not st.running:
                    return
            _time.sleep(0.02)
        raise AssertionError("session did not stop in time")

    try:
        capture_mod.AudioCapture = _FakeCap
        webapp._build_output_path = lambda topic: d / "agc-start.md"
        webapp._bump_session_count = lambda: None
        base = {"record": True, "transcribe": False}
        config.update({"agc_live": True})
        _start_and_stop({**base, "agc_live": False}, False)   # explicit False beats True setting
        config.update({"agc_live": False})
        _start_and_stop({**base, "agc_live": True}, True)     # explicit True beats False setting
        _start_and_stop(dict(base), False)                    # omitted -> settings default
    finally:
        capture_mod.AudioCapture = orig_cap
        webapp._build_output_path = orig_bop
        webapp._bump_session_count = orig_bump
        config.update({"agc_live": orig_setting})
    print("  OK  /api/start: explicit agc_live overrides the setting; omitted falls back to it")


def test_capture_agc_only_worker_is_not_an_echo_canceller():
    # aec_state()/set_aec() honesty: an AGC-only worker must not present as an available
    # echo canceller, and set_aec must never flip its bypass (bypass=True IS the AGC route).
    from live_transcribe.capture_core import CaptureBase

    class _LA:
        bypass = True
        aec_capable = False

    cap = CaptureBase(aec=False)
    cap._live_aec = _LA()
    assert cap.aec_state() == (False, False), "AGC-only worker must not report AEC available"
    assert cap.set_aec(True) is False, "ON must be refused with no far end"
    assert cap.set_aec(False) is True
    assert cap._live_aec.bypass is True, "set_aec must not touch an AGC-only worker's bypass"
    # And a worker WITHOUT the attribute (older stubs) still counts as capable.
    class _Old:
        bypass = True
    cap._live_aec = _Old()
    assert cap.aec_state() == (True, False)
    print("  OK  capture: AGC-only worker hidden from the AEC toggle, legacy stubs still capable")


def test_capture_mic_only_degrades_without_binding():
    # A mic-only session with agc on but no LiveKit binding must degrade to plain
    # native-rate capture (no worker), never fail the session.
    from live_transcribe import aec as _aec
    from live_transcribe.capture_core import CaptureBase

    class _MicOnly(CaptureBase):
        def _open_sources(self):
            self._register_source("MIC", 44100, 1)

        def _close_sources(self):
            pass

    real_available = _aec.available
    _aec.available = lambda: False
    try:
        cap = _MicOnly(agc=True)
        cap.start()
        try:
            assert cap._live_aec is None, "no binding -> no worker"
            assert cap._rates["MIC"] == 44100, "rates must stay native when degraded"
            assert cap.aec_state() == (False, False)
        finally:
            cap.stop()
    finally:
        _aec.available = real_available
    print("  OK  capture: mic-only + no binding degrades to plain capture")


if __name__ == "__main__":
    failures = 0
    for fn in (test_agc_setting_default_and_roundtrip,
               test_agc_bypass_emits_gained_not_raw,
               test_agc_mic_only_worker,
               test_agc_fail_open_midstream,
               test_agc_constructor_fail_open_with_fake_binding,
               test_main_apm_forward_failure_retires_and_flows,
               test_main_apm_reverse_failure_retires_and_flows,
               test_late_far_blocks_bypass_agc_only_worker,
               test_start_request_agc_live_overrides_settings,
               test_capture_agc_only_worker_is_not_an_echo_canceller,
               test_capture_mic_only_degrades_without_binding):
        try:
            fn()
        except Exception as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    sys.exit(1 if failures else 0)
