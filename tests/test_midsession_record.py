"""Tests for mid-session "Record from here": the AudioRecorder anchor and the endpoint.

Two layers, no audio devices, no model load, no threads that matter:

  1. AudioRecorder(anchor=...) (sinks.py): the mid-session recorder is given the session-clock time
     of the click as ONE shared anchor. Audio before it is never written (a chunk ending at/before
     the anchor is dropped whole; the chunk straddling it is sliced), and the SAME anchor is
     subtracted from every source, so the file starts at 0 with MIC/SYS aligned to that one moment.
     Default anchor=None is byte-for-byte today's wall-clock placement. Plus a _closed TOCTOU guard:
     on_chunk re-checks _closed UNDER the lock, so a close() racing the call creates no orphan handle.
  2. POST /api/record-from-here (web/app.py): attaches an anchored recorder to a running live
     transcription (recorder published BEFORE the flag), returns audio_stem for the finish-screen
     re-transcribe handoff, clears the struggle banner, and 409s when there is no live transcription
     session, no live capture, or the session has already recorded (a session records ONCE - a
     re-record would reuse the stem and truncate the first WAV). recording_started is the latch.

Run:  python tests/test_midsession_record.py   (from the project root; exit 0 = pass)
"""
import inspect
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from fastapi.testclient import TestClient

from live_transcribe import sinks
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})

RATE = sinks.AudioRecorder.TARGET_RATE


# --- helpers ---------------------------------------------------------------

def tone(secs, freq=440.0, amp=0.3):
    t = np.arange(int(secs * RATE), dtype=np.float32) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def pcm16(float_audio):
    """Match AudioRecorder.on_chunk's own float -> int16 conversion exactly."""
    return (np.clip(float_audio, -1.0, 1.0) * 32767.0).astype("<i2")


def read_wav(path):
    with wave.open(str(path), "rb") as r:
        nch = r.getnchannels()
        data = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2")
    if nch == 2:
        data = data.reshape(-1, 2)
        return data[:, 0], data[:, 1]      # left = MIC, right = SYS
    return data, None


def first_activity_s(ch, thresh=100):
    nz = np.nonzero(np.abs(ch.astype(np.int32)) > thresh)[0]
    return None if len(nz) == 0 else nz[0] / RATE


class _FakeCapture:
    """Stands in for capture.AudioCapture: carries only the session-clock origin _t0 the endpoint
    needs to compute the anchor. (t0=0.0 is fine; no chunks are fed through it in these tests.)"""

    def __init__(self, t0=0.0):
        self._t0 = t0


_STATE_FIELDS = ("running", "stopping", "source_kind", "engine", "capture", "recording",
                 "recording_started", "recorder", "transcribing", "output_path",
                 "struggle_nudge", "struggle_notified")


def _save_state():
    return {k: getattr(webapp.STATE, k) for k in _STATE_FIELDS}


def _restore_state(saved):
    for k, v in saved.items():
        setattr(webapp.STATE, k, v)


def _live_transcription(tmp, **over):
    """Hand-set STATE to a running live transcription session rooted at tmp/sess.md."""
    webapp.STATE.running = over.get("running", True)
    webapp.STATE.stopping = over.get("stopping", False)
    webapp.STATE.source_kind = over.get("source_kind", "live")
    webapp.STATE.transcribing = over.get("transcribing", True)
    webapp.STATE.engine = over.get("engine", object())
    webapp.STATE.capture = over.get("capture", _FakeCapture(t0=time.monotonic()))
    webapp.STATE.recording = over.get("recording", False)
    webapp.STATE.recording_started = over.get("recording_started", False)
    webapp.STATE.recorder = over.get("recorder", None)
    webapp.STATE.output_path = over.get("output_path", Path(tmp) / "sess.md")
    webapp.STATE.struggle_nudge = over.get("struggle_nudge", None)
    webapp.STATE.struggle_notified = over.get("struggle_notified", False)


# --- 1. AudioRecorder anchor (sinks.py) ------------------------------------

def test_anchor_drops_and_slices_preclick_audio():
    # Click at session-clock 100 s. A chunk ending before it (80..90) is dropped whole; the chunk
    # straddling it (95..105) is sliced AT the anchor so only 100..105 survives; then everything is
    # rebased to 0. Asserted byte-for-byte: nothing captured before the click reaches disk.
    d = Path(tempfile.mkdtemp())
    rec = sinks.AudioRecorder(d / "anchor1", anchor=100.0)
    freq = 330
    straddle = tone(10.0, freq)      # session-clock 95..105, straddles the click at 100
    after = tone(10.0, freq)         # 105..115
    rec.on_chunk("SYS", tone(10.0, freq), 80.0)   # 80..90: ends before the click -> dropped whole
    rec.on_chunk("SYS", straddle, 95.0)           # sliced: only 100..105 kept
    rec.on_chunk("SYS", after, 105.0)
    rec.close()

    data, _ = read_wav(d / "anchor1.wav")
    skip = int((100.0 - 95.0) * RATE)
    ref = pcm16(np.concatenate([straddle[skip:], after]))
    assert len(data) == int(15.0 * RATE), \
        f"expected 15s (5s sliced straddle + 10s), got {len(data) / RATE:.2f}s (pre-click leaked?)"
    assert np.array_equal(data, ref), "pre-anchor audio must be dropped/sliced exactly, then start at 0"
    print("  OK  anchor drops whole pre-click chunks and slices the straddling one, byte-exact from 0")


def test_anchor_keeps_channels_aligned_to_one_shared_moment():
    # MIC and SYS have DIFFERENT straddle start times (97 vs 95), but the SAME shared anchor (100).
    # Both must land at rebased 0, so the click lines up on both channels. A per-source first-chunk
    # offset (the old behaviour) would zero them at 97 and 95 and misalign L/R.
    d = Path(tempfile.mkdtemp())
    rec = sinks.AudioRecorder(d / "anchor2", anchor=100.0)
    rec.on_chunk("MIC", tone(10.0, 220), 97.0)    # 97..107 -> keep 100..107 (7s)
    rec.on_chunk("SYS", tone(10.0, 660), 95.0)    # 95..105 -> keep 100..105 (5s)
    rec.on_chunk("MIC", tone(10.0, 220), 107.0)   # -> 7..17
    rec.on_chunk("SYS", tone(10.0, 660), 105.0)   # -> 5..15
    rec.close()

    micc, sysc = read_wav(d / "anchor2.wav")
    assert sysc is not None, "both channels present -> stereo fold expected"
    assert len(micc) == len(sysc) == int(17.0 * RATE), \
        f"expected 17s (MIC 7+10, tail-padded), got {len(micc) / RATE:.2f}s"
    assert first_activity_s(micc) is not None and first_activity_s(micc) < 0.2, \
        "MIC must start at the shared click (rebased 0)"
    assert first_activity_s(sysc) is not None and first_activity_s(sysc) < 0.2, \
        "SYS must start at the SAME shared click (rebased 0), i.e. aligned with MIC"
    print("  OK  one shared anchor aligns MIC/SYS to the click; no per-source zero, no pre-click")


def test_default_recorder_uses_wall_clock_placement():
    # Default anchor=None: a first chunk at t=30 still zero-fills 0..30 (today's wall-clock placement,
    # unchanged), so the elapsed lead is preserved for a start-time recording.
    d = Path(tempfile.mkdtemp())
    rec = sinks.AudioRecorder(d / "plain")
    assert rec._anchor is None, "default must not anchor"
    rec.on_chunk("SYS", tone(10.0, 660), 30.0)
    rec.close()
    mono, _ = read_wav(d / "plain.wav")
    assert len(mono) == int(40.0 * RATE), f"default must keep the 30s lead (len {len(mono) / RATE:.1f}s)"
    onset = first_activity_s(mono)
    assert onset is not None and abs(onset - 30.0) <= 0.5, f"default onset {onset}s, expected 30s"
    print("  OK  default anchor=None keeps today's wall-clock placement (30s lead preserved)")


def test_closed_recheck_under_lock_prevents_orphan_handle():
    # Force the exact interleaving the pre-lock _closed check leaves open: close() lands AFTER
    # on_chunk's pre-lock check passed but BEFORE it holds the lock. A lock whose acquire flips
    # _closed True (as close() would, under this same lock) stands in for that race. Without the
    # re-check under the lock, on_chunk would create a WAV writer AFTER finalisation -> an orphaned,
    # never-finalised handle.
    d = Path(tempfile.mkdtemp())
    rec = sinks.AudioRecorder(d / "toctou")
    real_lock = rec._lock
    fired = {"n": 0}

    class _CloseOnAcquire:
        def __enter__(self):
            real_lock.acquire()
            if fired["n"] == 0:            # simulate close() winning the race exactly once
                fired["n"] = 1
                rec._closed = True
            return self

        def __exit__(self, *a):
            real_lock.release()

    rec._lock = _CloseOnAcquire()
    rec.on_chunk("MIC", tone(1.0, 220), 0.0)
    assert rec._writers == {}, "on_chunk created a writer after close: the _closed re-check is missing"
    assert not (d / "toctou-MIC.wav").exists(), "an orphaned per-source WAV was created after close()"
    print("  OK  on_chunk re-checks _closed under the lock: no writer/handle created after close")


# --- 2. POST /api/record-from-here (web/app.py) ----------------------------

def test_record_from_here_requires_a_live_transcription_session_and_csrf():
    saved = _save_state()
    try:
        webapp.STATE.running = False        # idle
        r = client.post("/api/record-from-here")
        assert r.status_code == 409, (r.status_code, r.text)
        bare = TestClient(app, base_url="http://localhost")
        assert bare.post("/api/record-from-here").status_code == 403, "must be CSRF-protected"
    finally:
        _restore_state(saved)
    print("  OK  /api/record-from-here: 409 with no live session, CSRF-protected")


def test_record_from_here_rejects_record_only_stopping_no_engine_and_no_capture():
    saved = _save_state()
    tmp = tempfile.mkdtemp()
    try:
        # Not transcribing (a record-only session already records): 409.
        _live_transcription(tmp, transcribing=False)
        assert client.post("/api/record-from-here").status_code == 409
        # Stopping (draining): 409.
        _live_transcription(tmp, stopping=True)
        assert client.post("/api/record-from-here").status_code == 409
        # No engine: 409.
        _live_transcription(tmp, engine=None)
        assert client.post("/api/record-from-here").status_code == 409
        # No live capture (a failed device switch can leave a running session with capture=None): 409.
        _live_transcription(tmp, capture=None)
        assert client.post("/api/record-from-here").status_code == 409
        assert webapp.STATE.recorder is None, "a rejected call must not attach a recorder"
    finally:
        _restore_state(saved)
    print("  OK  record-from-here rejects record-only, stopping, engineless and capture-less sessions")


def test_record_from_here_attaches_an_anchored_recorder_and_returns_the_stem():
    saved = _save_state()
    tmp = tempfile.mkdtemp()
    try:
        _live_transcription(tmp, struggle_nudge={"old_size": "medium", "new_size": "small",
                                                 "recording": False})
        r = client.post("/api/record-from-here")
        assert r.status_code == 200, (r.status_code, r.text)
        expected_stem = str(Path(tmp) / "sess")
        assert r.json() == {"recording": True, "audio_stem": expected_stem}, r.json()
        assert webapp.STATE.recording is True, "the recording flag must be set"
        assert webapp.STATE.recording_started is True, "the latch must be set"
        rec = webapp.STATE.recorder
        assert isinstance(rec, sinks.AudioRecorder), "a recorder must be attached"
        assert rec._anchor is not None, "the mid-session recorder must carry the click anchor"
        assert webapp.STATE.struggle_nudge is None, "taking the record action must clear the banner"
        rec.close()
    finally:
        _restore_state(saved)
    print("  OK  record-from-here attaches an anchored recorder, returns the stem, clears the banner")


def test_record_from_here_is_idempotent_and_never_orphans_a_recorder():
    saved = _save_state()
    tmp = tempfile.mkdtemp()
    try:
        marker = object()               # stands in for an already-attached recorder
        _live_transcription(tmp, recording=True, recording_started=True, recorder=marker)
        r = client.post("/api/record-from-here")
        assert r.status_code == 409, f"a second start must 409, got {r.status_code}"
        assert webapp.STATE.recorder is marker, \
            "the existing recorder must NOT be replaced (that would orphan its open WAV handle)"
    finally:
        _restore_state(saved)
    print("  OK  record-from-here is idempotent: a double-click 409s and never replaces the recorder")


def test_record_from_here_latches_and_refuses_a_second_recording():
    # The truncation bug: record, stop recording (transcription continues), record again -> the new
    # recorder reuses the stem and truncates the first <stem>.wav on close. The recording_started
    # latch makes the second attempt 409 instead.
    saved = _save_state()
    tmp = tempfile.mkdtemp()
    try:
        _live_transcription(tmp)
        r = client.post("/api/record-from-here")
        assert r.status_code == 200, r.text
        assert webapp.STATE.recording_started is True, "a successful record must set the latch"
        rec = webapp.STATE.recorder
        # Simulate POST /api/stop?what=recording: recording stops, but the latch persists.
        webapp.STATE.recording = False
        webapp.STATE.recorder = None
        r2 = client.post("/api/record-from-here")
        assert r2.status_code == 409, f"a second recording must be refused, got {r2.status_code}: {r2.text}"
        assert webapp.STATE.recorder is None, "the refused call must not attach a new recorder"
        if isinstance(rec, sinks.AudioRecorder):
            rec.close()
    finally:
        _restore_state(saved)
    print("  OK  recording_started latches; a second record-from-here after a stop is refused")


def test_status_carries_recording_started():
    saved = _save_state()
    try:
        webapp.STATE.running = True
        webapp.STATE.stopping = False
        webapp.STATE.source_kind = "live"
        webapp.STATE.engine = None
        webapp.STATE.recording_started = True
        assert client.get("/api/status").json()["recording_started"] is True
        webapp.STATE.recording_started = False
        assert client.get("/api/status").json()["recording_started"] is False
    finally:
        _restore_state(saved)
    print("  OK  /api/status carries recording_started (true iff recording is or ever was active)")


def test_record_from_here_attach_order_and_no_second_close_path():
    # Static guards for two landmines: the recorder must be published BEFORE the recording flag
    # (_feed reads both lock-free every chunk), and the endpoint must add no second WAV close path
    # (the existing stop flow finalises STATE.recorder).
    src = inspect.getsource(webapp.record_from_here)
    assert src.index("STATE.recorder = rec") < src.index("STATE.recording = True"), \
        "the recorder must be published before the recording flag"
    assert ".close(" not in src, "record-from-here must not add a second WAV close path"
    print("  OK  recorder is attached before the flag, and no second close path is introduced")


if __name__ == "__main__":
    tests = (test_anchor_drops_and_slices_preclick_audio,
             test_anchor_keeps_channels_aligned_to_one_shared_moment,
             test_default_recorder_uses_wall_clock_placement,
             test_closed_recheck_under_lock_prevents_orphan_handle,
             test_record_from_here_requires_a_live_transcription_session_and_csrf,
             test_record_from_here_rejects_record_only_stopping_no_engine_and_no_capture,
             test_record_from_here_attaches_an_anchored_recorder_and_returns_the_stem,
             test_record_from_here_is_idempotent_and_never_orphans_a_recorder,
             test_record_from_here_latches_and_refuses_a_second_recording,
             test_status_carries_recording_started,
             test_record_from_here_attach_order_and_no_second_close_path)
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll mid-session recording tests passed.")
