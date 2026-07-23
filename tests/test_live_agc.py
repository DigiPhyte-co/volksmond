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
               test_capture_agc_only_worker_is_not_an_echo_canceller,
               test_capture_mic_only_degrades_without_binding):
        try:
            fn()
        except Exception as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    sys.exit(1 if failures else 0)
