"""Tests for the stereo interview upload option and the in-meeting live-AEC toggle.

Server-side seams only, no model load and no audio devices:
  - stereo_split request handling: flag accepted, mono-upmix detection primitive,
    write-time Speaker L/R relabelling in the sinks, the sticky /api/status notice.
  - live AEC toggle: the capture-side flip logic, the /api/aec-live endpoint
    (session-gated, CSRF-protected, persists the choice, returns the CONFIRMED
    engine state), the /api/status truth fields, and the LiveAEC bypass
    passthrough (skipped when the LiveKit binding is not installed).

Run:  python tests/test_stereo_aec.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import tempfile
import types
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe import config, sinks
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


def test_stereo_split_request_flag():
    # The flag is accepted by the request model, defaults OFF, and a request with no
    # readable files still fails fast (400) before any engine loads, flag or not.
    from live_transcribe.web.app import TranscribeFileRequest
    assert TranscribeFileRequest(paths=["x"]).stereo_split is False
    assert TranscribeFileRequest(paths=["x"], stereo_split=True).stereo_split is True
    r = client.post("/api/transcribe-file", json={"paths": ["Z:\\nope\\missing.m4a"], "stereo_split": True})
    assert r.status_code == 400, (r.status_code, r.text)
    print("  OK  stereo_split: accepted by the request model (default off), no-files still 400s")


def test_stereo_split_mono_detection():
    # The mono fallback rests on one fact: decode_audio(split_stereo=True) upmixes a mono
    # source to two EXACTLY equal channels (same resampler filter on both), while a real
    # stereo file keeps distinct channels. Pin that, or the fallback breaks silently.
    import numpy as np
    import wave
    from faster_whisper.audio import decode_audio
    d = Path(tempfile.mkdtemp())
    t = np.arange(16000, dtype=np.float32) / 16000.0
    tone = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    mono = d / "mono.wav"
    with wave.open(str(mono), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(tone.tobytes())
    left, right = decode_audio(str(mono), sampling_rate=16000, split_stereo=True)
    assert np.array_equal(left, right), "mono upmix must give exactly equal channels"
    stereo = d / "stereo.wav"
    pcm = np.empty(len(tone) * 2, dtype="<i2")
    pcm[0::2] = tone
    pcm[1::2] = 0
    with wave.open(str(stereo), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    left, right = decode_audio(str(stereo), sampling_rate=16000, split_stereo=True)
    assert not np.array_equal(left, right), "a real stereo file must keep distinct channels"
    print("  OK  mono detection: upmixed mono decodes to equal channels, real stereo does not")


def test_sink_speaker_labels():
    # Interview mode relabels ONLY at the presentation seam: the Markdown file and the SSE
    # payload say Speaker L/R, while the retained segments keep MIC/SYS so the close-time
    # echo dedup still keys correctly.
    labels = {"MIC": "Speaker L", "SYS": "Speaker R"}
    d = Path(tempfile.mkdtemp())
    p = d / "t.md"
    sink = sinks.MarkdownSink(p, source_labels=labels)
    Seg = types.SimpleNamespace
    sink(Seg(source="MIC", t_start=1.0, t_end=2.0, text="linkerkant praat"))
    sink(Seg(source="SYS", t_start=3.0, t_end=4.0, text="regterkant antwoord"))
    assert [s.source for s in sink._segments] == ["MIC", "SYS"], "internal tags must survive"
    sink.close()
    txt = p.read_text(encoding="utf-8")
    assert "[Speaker L] linkerkant praat" in txt and "[Speaker R] regterkant antwoord" in txt, txt
    assert "[MIC]" not in txt and "[SYS]" not in txt, txt
    assert "your microphone" not in txt, "interview header must not claim MIC/SYS meanings"
    assert "left and right channels" in txt, txt
    # Default sink is unchanged: MIC/SYS written as before.
    p2 = d / "t2.md"
    sink2 = sinks.MarkdownSink(p2)
    sink2(Seg(source="MIC", t_start=1.0, t_end=2.0, text="hallo"))
    sink2.close()
    assert "[MIC] hallo" in p2.read_text(encoding="utf-8")
    # BrowserSink: the streamed payload carries the display label.
    bs = webapp.BrowserSink(source_labels=labels)
    q = bs.add_subscriber()
    bs(Seg(source="MIC", t_start=0.0, t_end=1.0, text="x"))
    assert q.get_nowait()["source"] == "Speaker L"
    print("  OK  sinks: Speaker L/R at write/stream time, MIC/SYS kept internally")


def test_status_notice_sticky():
    # The non-fatal notice ("file is mono...") rides /api/status, including after the
    # session ends (reset() keeps it, like sink_error), until the next session clears it.
    st = webapp.STATE
    saved = st.notice
    try:
        st.notice = "File is mono, transcribed as a single track"
        j = client.get("/api/status").json()
        assert j["running"] is False and j["notice"] == "File is mono, transcribed as a single track", j
        st.reset()
        assert st.notice == "File is mono, transcribed as a single track", "notice must survive reset()"
    finally:
        st.notice = saved
    print("  OK  /api/status carries the sticky notice across the session end")


def test_capture_aec_toggle_logic():
    # The capture-side flip: OFF is always honoured; ON needs the APM worker; the flip
    # only touches the worker's bypass flag and tracks the active state in cap.aec (so a
    # mid-meeting device switch rebuilds with the CURRENT choice).
    from live_transcribe.capture_core import CaptureBase

    class _LA:  # stands in for the LiveAEC worker; the toggle only touches .bypass
        def __init__(self, bypass):
            self.bypass = bypass

    cap = CaptureBase(aec=False)
    assert cap.aec_state() == (False, False)
    assert cap.set_aec(False) is True, "OFF with no APM is trivially satisfied"
    assert cap.set_aec(True) is False, "ON without the APM cannot be honoured"
    cap._live_aec = _LA(bypass=True)      # engaged in bypass (session started with AEC off)
    assert cap.aec_state() == (True, False)
    assert cap.set_aec(True) is True
    assert cap._live_aec.bypass is False and cap.aec is True
    assert cap.aec_state() == (True, True)
    assert cap.set_aec(False) is True
    assert cap._live_aec.bypass is True and cap.aec is False
    assert cap.aec_state() == (True, False)
    print("  OK  capture set_aec/aec_state: both directions flip the bypass, ON needs the APM")


def test_aec_live_endpoint():
    # /api/aec-live: idle -> 409, CSRF-protected; during a (faked) live session it flips
    # the capture, persists aec_live to settings, and /api/status reports the ENGINE'S
    # actual state (the fix for the stored-settings desync trap).
    assert client.post("/api/aec-live", json={"enabled": True}).status_code == 409
    bare = TestClient(app, base_url="http://localhost")
    assert bare.post("/api/aec-live", json={"enabled": True}).status_code == 403, "not CSRF-protected"

    from live_transcribe.capture_core import CaptureBase

    class _LA:
        bypass = True

    st = webapp.STATE
    cap = CaptureBase(aec=False)
    cap._live_aec = _LA()
    orig_aec = config.load().get("aec_live", True)
    saved = (st.running, st.stopping, st.source_kind, st.capture)
    try:
        st.running, st.stopping, st.source_kind, st.capture = True, False, "live", cap
        j = client.post("/api/aec-live", json={"enabled": True}).json()
        # persisted: True reports the settings write succeeded (review wave 1, F7).
        assert j == {"aec_live_available": True, "aec_live_active": True, "persisted": True}, j
        assert config.load().get("aec_live") is True, "choice not persisted as the new default"
        stj = client.get("/api/status").json()
        assert stj["aec_live_available"] is True and stj["aec_live_active"] is True, stj
        j = client.post("/api/aec-live", json={"enabled": False}).json()
        assert j["aec_live_active"] is False and j["aec_live_available"] is True, j
        assert config.load().get("aec_live") is False, "OFF not persisted"
        # A file session must not accept the live toggle.
        st.source_kind = "file"
        assert client.post("/api/aec-live", json={"enabled": True}).status_code == 409
        # A live session whose canceller never engaged cannot honour ON (honest 409).
        st.source_kind = "live"
        cap._live_aec = None
        assert client.post("/api/aec-live", json={"enabled": True}).status_code == 409
    finally:
        st.running, st.stopping, st.source_kind, st.capture = saved
        config.update({"aec_live": orig_aec})
    print("  OK  /api/aec-live: gated + CSRF, flips + persists, status reports engine truth")


def test_live_aec_bypass_passthrough():
    # The bypassed worker must emit the RAW near-end unchanged (AEC off costs nothing),
    # keep feeding the APM, and honour a mid-stream flip on the next frames.
    from live_transcribe import aec as _aec
    if not _aec.available():
        print("  SKIP  LiveAEC bypass passthrough (LiveKit binding not installed)")
        return
    import time
    import numpy as np
    from live_transcribe.aec_live import LiveAEC, FRAME
    near_out, far_out = [], []
    la = LiveAEC(16000, 16000, on_near=near_out.append, on_far=far_out.append, bypass=True)
    la.start()
    try:
        rng = np.random.default_rng(7)
        sig = (rng.standard_normal(FRAME * 5) * 0.1).astype(np.float32)
        la.push_far(sig.copy())
        la.push_near(sig.copy())
        deadline = time.monotonic() + 3.0
        while sum(len(a) for a in near_out) < len(sig) and time.monotonic() < deadline:
            time.sleep(0.02)
        got = np.concatenate(near_out)[:len(sig)]
        assert np.array_equal(got, sig), "bypass must emit the raw near-end bit-identically"
        # Flip ON mid-stream: from here the emitted frames go through the APM (int16 path),
        # so they can no longer be bit-identical to the input.
        la.bypass = False
        n_before = sum(len(a) for a in near_out)
        sig2 = (rng.standard_normal(FRAME * 5) * 0.1).astype(np.float32)
        la.push_far(sig2.copy())
        la.push_near(sig2.copy())
    finally:
        la.stop()   # drains + flushes everything still queued
    got2 = np.concatenate(near_out)[n_before:n_before + len(sig2)]
    assert got2.shape == sig2.shape, (got2.shape, sig2.shape)
    assert not np.array_equal(got2, sig2), "active AEC output should differ from the raw input"
    assert sum(len(a) for a in far_out) == len(sig) + len(sig2), "far-end passthrough lost samples"
    print("  OK  LiveAEC bypass: raw passthrough when off, mid-stream flip takes effect")


if __name__ == "__main__":
    failures = 0
    for fn in (test_stereo_split_request_flag,
               test_stereo_split_mono_detection,
               test_sink_speaker_labels,
               test_status_notice_sticky,
               test_capture_aec_toggle_logic,
               test_aec_live_endpoint,
               test_live_aec_bypass_passthrough):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll stereo/AEC tests passed.")
