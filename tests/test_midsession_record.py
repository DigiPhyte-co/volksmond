"""Tests for mid-session "Record from here": the AudioRecorder rebase and the endpoint.

Two layers, no audio devices, no model load, no threads that matter:

  1. AudioRecorder(rebase=True) (sinks.py): the mid-session recorder zeros its timeline on the
     FIRST chunk of ANY source with ONE shared offset, so the file starts at 0 instead of
     zero-filling the (up to hours) elapsed session lead with silence, while MIC/SYS stay aligned
     RELATIVE to each other. Default rebase=False is byte-for-byte today's wall-clock placement.
  2. POST /api/record-from-here (web/app.py): attaches a rebasing recorder to a running live
     transcription (recorder published BEFORE the flag), returns audio_stem for the finish-screen
     re-transcribe handoff, clears the struggle banner, and 409s when there is no live transcription
     session or when already recording (idempotent: no orphaned WAV handle on a double-click). It
     adds NO second WAV close path - the existing stop flow finalises the recorder.

Run:  python tests/test_midsession_record.py   (from the project root; exit 0 = pass)
"""
import inspect
import os
import sys
import tempfile
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


_STATE_FIELDS = ("running", "stopping", "source_kind", "engine", "recording", "recorder",
                 "transcribing", "output_path", "struggle_nudge", "struggle_notified")


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
    webapp.STATE.recording = over.get("recording", False)
    webapp.STATE.recorder = over.get("recorder", None)
    webapp.STATE.output_path = over.get("output_path", Path(tmp) / "sess.md")
    webapp.STATE.struggle_nudge = over.get("struggle_nudge", None)
    webapp.STATE.struggle_notified = over.get("struggle_notified", False)


# --- 1. AudioRecorder rebase (sinks.py) ------------------------------------

def test_rebase_zeroes_the_timeline_and_keeps_relative_alignment():
    # Recording started ~600 s into the session. rebase=True must land the first audio at ~0 (not a
    # 600 s silent lead) while keeping the MIC-ahead-of-SYS offset intact in the fold. SYS is 5 s
    # behind MIC on the session clock - comfortably above the 2 s jitter tolerance, so the fold shows
    # the preserved lead as real leading silence rather than being absorbed as jitter.
    d = Path(tempfile.mkdtemp())
    rec = sinks.AudioRecorder(d / "mid", rebase=True)
    rec.on_chunk("MIC", tone(10.0, 220), 600.0)     # first chunk anchors the shared zero
    assert rec._t_offset == 600.0, f"first chunk must set the shared offset, got {rec._t_offset}"
    rec.on_chunk("SYS", tone(10.0, 660), 605.0)     # 5 s behind MIC on the session clock
    rec.on_chunk("MIC", tone(10.0, 220), 610.0)
    assert rec._t_offset == 600.0, "the offset must be shared and set ONCE, not re-anchored per source"
    rec.close()

    micc, sysc = read_wav(d / "mid.wav")
    assert sysc is not None, "both channels present -> stereo fold expected"
    # MIC: 0..20 s. SYS: 5 s of lead silence, then 5..15 s. Tail-padded to the longer channel (20 s).
    assert len(micc) == len(sysc) == int(20.0 * RATE), \
        f"rebased length {len(micc) / RATE:.2f}s, expected 20s (NOT a 600s+ silent lead)"
    assert first_activity_s(micc) is not None and first_activity_s(micc) < 0.2, \
        "MIC must start at ~0 after the rebase, not at 600s"
    sys_onset = first_activity_s(sysc)
    assert sys_onset is not None and abs(sys_onset - 5.0) <= 0.5, \
        f"SYS must keep its 5s lead relative to MIC, onset was {sys_onset}s"
    print("  OK  rebase=True starts the file at 0 and preserves the MIC/SYS relative alignment")


def test_default_recorder_is_not_rebased():
    # Default rebase=False: a first chunk at t=30 still zero-fills 0..30 (today's wall-clock
    # placement, unchanged), so the elapsed lead is preserved for a start-time recording.
    d = Path(tempfile.mkdtemp())
    rec = sinks.AudioRecorder(d / "plain")
    assert rec._rebase is False and rec._t_offset is None, "default must not rebase"
    rec.on_chunk("SYS", tone(10.0, 660), 30.0)
    rec.close()
    mono, _ = read_wav(d / "plain.wav")
    assert len(mono) == int(40.0 * RATE), f"default must keep the 30s lead (len {len(mono) / RATE:.1f}s)"
    onset = first_activity_s(mono)
    assert onset is not None and abs(onset - 30.0) <= 0.5, f"default onset {onset}s, expected 30s"
    print("  OK  default rebase=False keeps today's wall-clock placement (30s lead preserved)")


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


def test_record_from_here_rejects_record_only_stopping_and_no_engine():
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
    finally:
        _restore_state(saved)
    print("  OK  record-from-here rejects record-only, stopping and engineless sessions (409)")


def test_record_from_here_attaches_a_rebasing_recorder_and_returns_the_stem():
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
        rec = webapp.STATE.recorder
        assert isinstance(rec, sinks.AudioRecorder), "a recorder must be attached"
        assert rec._rebase is True, "the mid-session recorder must rebase to zero"
        assert webapp.STATE.struggle_nudge is None, "taking the record action must clear the banner"
        rec.close()
    finally:
        _restore_state(saved)
    print("  OK  record-from-here attaches a rebasing recorder, returns the stem, clears the banner")


def test_record_from_here_is_idempotent_and_never_orphans_a_recorder():
    saved = _save_state()
    tmp = tempfile.mkdtemp()
    try:
        marker = object()               # stands in for an already-attached recorder
        _live_transcription(tmp, recording=True, recorder=marker)
        r = client.post("/api/record-from-here")
        assert r.status_code == 409, f"a second start must 409, got {r.status_code}"
        assert webapp.STATE.recorder is marker, \
            "the existing recorder must NOT be replaced (that would orphan its open WAV handle)"
    finally:
        _restore_state(saved)
    print("  OK  record-from-here is idempotent: a double-click 409s and never replaces the recorder")


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
    tests = (test_rebase_zeroes_the_timeline_and_keeps_relative_alignment,
             test_default_recorder_is_not_rebased,
             test_record_from_here_requires_a_live_transcription_session_and_csrf,
             test_record_from_here_rejects_record_only_stopping_and_no_engine,
             test_record_from_here_attaches_a_rebasing_recorder_and_returns_the_stem,
             test_record_from_here_is_idempotent_and_never_orphans_a_recorder,
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
