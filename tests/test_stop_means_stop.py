"""WP-2a: Stop means stop. The full-stop path must tell the truth about capture, immediately.

The incident: Stop was pressed 12 minutes into a 48 minute meeting. Capture stopped at once and
both per-source WAVs were frozen on disk from that second, but the ASR backlog took over twenty
minutes to drain, and for all of it the app kept saying "Recording audio", kept counting the clock
up, kept the meters moving, had not folded the stereo <stem>.wav, and refused to start the next
session. For a product sold on "local only, nothing leaves your machine", a recording indicator
that lies is the worst possible bug.

These pin the four halves of the fix, with no audio device and no model:
  (a) STATE.recording goes False when CAPTURE stops, not when the session finally resets;
  (b) /api/status publishes `capturing`, and it reads False while `stopping` and `running` are
      both still True (the 10 s status poll is what used to re-assert the lie);
  (c) the recorder is closed, and the stereo fold is on disk, BEFORE the engine drain finishes,
      because the recorder is tapped ahead of the engine queue and owes the backlog nothing;
  (d) a session with no recorder at all still stops cleanly.

Everything is stubbed: STATE is hand-set and restored, the engine is a fake whose stop(drain=True)
blocks on a gate so the "still draining" window can be inspected, and the one real object is an
AudioRecorder writing into a temp folder (so the fold under test is the actual fold).

Run:  python tests/test_stop_means_stop.py   (from the project root; exit 0 = pass)
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from fastapi.testclient import TestClient

from live_transcribe.sinks import AudioRecorder
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

# Loopback Host + CSRF token, exactly like test_web_api.py, so we exercise /api/stop, not the guards.
client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})

RATE = AudioRecorder.TARGET_RATE


def wait_until(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


class FakeCapture:
    """Device-free capture: records that stop() ran and when, opens nothing."""

    def __init__(self, log=None):
        self.stopped = False
        self._log = log

    def stop(self):
        self.stopped = True
        if self._log is not None:
            self._log.append("capture.stop")

    def aec_state(self):
        return False, False


class GatedEngine:
    """Engine stand-in whose stop(drain=True) BLOCKS until the test releases it, which is what a
    contended GPU looks like from the outside: minutes of drain after capture is already shut."""

    engine = "auto"     # /api/status publishes the family-override pref off the live engine

    def __init__(self, gate, log=None):
        self._gate = gate
        self._log = log
        self.stopped_drain = None

    def pending(self):
        return 3

    def stop(self, drain=False, timeout=None):
        if self._log is not None:
            self._log.append("engine.stop.enter")
        self._gate.wait(20.0)
        self.stopped_drain = drain
        if self._log is not None:
            self._log.append("engine.stop.exit")


class LoggingRecorder:
    """Recorder stand-in that only records WHEN it was closed, for the ordering assertions."""

    def __init__(self, log):
        self._log = log
        self.closed = 0
        self.last_error = None

    def close(self):
        self.closed += 1
        self._log.append("recorder.close")


def _save_state():
    st = webapp.STATE
    return (st.running, st.stopping, st.recording, st.recording_started, st.transcribing,
            st.source_kind, st.engine, st.capture, st.recorder, st.md_sink, st.output_path,
            st.session_counted, st.sink_error, st.started_at, st.silence_stop, st.silence_watch)


def _restore_state(saved):
    st = webapp.STATE
    (st.running, st.stopping, st.recording, st.recording_started, st.transcribing,
     st.source_kind, st.engine, st.capture, st.recorder, st.md_sink, st.output_path,
     st.session_counted, st.sink_error, st.started_at, st.silence_stop, st.silence_watch) = saved


def _arm_live_session(engine=None, capture=None, recorder=None, recording=True):
    """Hand-set STATE as a running live session. session_counted=True so the finalise path's
    _bump_session_count returns early and never touches the real settings file."""
    st = webapp.STATE
    st.running = True
    st.stopping = False
    st.transcribing = True
    st.recording = recording
    st.recording_started = recording
    st.source_kind = "live"
    st.engine = engine
    st.capture = capture
    st.recorder = recorder
    st.md_sink = None
    st.output_path = None
    st.session_counted = True      # never write the real settings.json from a test
    st.sink_error = None
    st.silence_stop = None
    st.silence_watch = None


def test_recording_goes_false_at_capture_stop_not_at_reset():
    # The bug in one assertion: while the engine is still draining, the session must already be
    # saying it is not recording. Before the fix STATE.recording was cleared only by STATE.reset(),
    # which runs after the (unbounded) drain, so the pill stayed lit for the whole backlog.
    saved = _save_state()
    gate = threading.Event()
    try:
        cap = FakeCapture()
        eng = GatedEngine(gate)
        rec = LoggingRecorder([])
        _arm_live_session(engine=eng, capture=cap, recorder=rec)
        r = client.post("/api/stop?what=all")
        assert r.status_code == 200, r.text
        assert wait_until(lambda: cap.stopped), "capture was never stopped"
        assert wait_until(lambda: webapp.STATE.recording is False), \
            "STATE.recording is still True after capture stopped (the pill would still be lit)"
        # Still mid-drain: the session has NOT reset, so this is genuinely early, not just the end.
        assert webapp.STATE.running is True, "the session ended before the drain was released"
        assert webapp.STATE.stopping is True, "stopping was cleared early"
        assert eng.stopped_drain is None, "the engine drain already finished; the window was not tested"
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "the stop never finalised"
        assert eng.stopped_drain is True, "the engine was not stop(drain=True)'d"
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  STATE.recording goes False when capture stops, not when the session resets")


def test_status_reports_capturing_false_while_still_stopping():
    # /api/status gained ONE new field, because none of running/stopping/recording answers "is the
    # microphone open?". It must read False during the drain, while running and stopping are both
    # still True, since that ten-second poll is exactly what used to re-assert the lie to the page.
    saved = _save_state()
    gate = threading.Event()
    try:
        cap = FakeCapture()
        eng = GatedEngine(gate)
        _arm_live_session(engine=eng, capture=cap, recorder=None, recording=True)
        # Positive control first: a live session says it IS capturing.
        live = client.get("/api/status").json()
        assert live["capturing"] is True, f"a live session must report capturing=True: {live}"
        assert client.post("/api/stop?what=all").status_code == 200
        assert wait_until(lambda: client.get("/api/status").json().get("capturing") is False), \
            "capturing never went False while the drain was still running"
        st = client.get("/api/status").json()
        assert st["running"] is True, f"expected the session to still exist mid-drain: {st}"
        assert st["stopping"] is True, f"expected stopping to still be True mid-drain: {st}"
        assert st["recording"] is False, f"status still asserts recording mid-drain: {st}"
        # The meters read the same capture handle, so they go to zero for free.
        lv = client.get("/api/levels").json()
        assert lv["running"] is False, f"/api/levels still reports a live capture: {lv}"
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "the stop never finalised"
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  /api/status reports capturing=False while running and stopping are both still True")


def test_recording_is_closed_and_folded_before_the_drain_finishes():
    # The recorder is tapped BEFORE the engine queue, so once capture has stopped it has zero data
    # dependency on the ASR backlog. Closing it after the drain only meant the two per-source WAVs
    # sat unfolded, with no single stereo <stem>.wav, for as long as transcription took. A REAL
    # AudioRecorder here, so the thing asserted on disk is the actual fold.
    saved = _save_state()
    gate = threading.Event()
    tmp = Path(tempfile.mkdtemp())
    try:
        log = []
        cap = FakeCapture(log)
        eng = GatedEngine(gate, log)
        stem = tmp / "2026-08-27-120000-stop-means-stop"
        rec = AudioRecorder(stem)
        tone = (0.3 * np.sin(2 * np.pi * 440.0 * np.arange(RATE, dtype=np.float32) / RATE)).astype(np.float32)
        rec.on_chunk("MIC", tone, 0.0)
        rec.on_chunk("SYS", tone, 0.0)
        assert not (tmp / (stem.name + ".wav")).exists(), "the fold cannot exist before the stop"
        _arm_live_session(engine=eng, capture=cap, recorder=rec)
        assert client.post("/api/stop?what=all").status_code == 200
        # The stereo file must be COMPLETE while the engine is still blocked in stop(drain=True).
        # "the file exists" is not the signal: wave.open("wb") creates it empty at the start of the
        # fold. The per-source channels are unlinked only after a successful write, so their
        # disappearance is the real completion edge.
        folded = tmp / (stem.name + ".wav")
        per_source = [tmp / (stem.name + "-MIC.wav"), tmp / (stem.name + "-SYS.wav")]
        assert wait_until(lambda: folded.is_file() and not any(p.exists() for p in per_source)), \
            "the stereo fold did not happen until the ASR drain finished"
        assert eng.stopped_drain is None, "the drain finished first; the ordering was not tested"
        with wave.open(str(folded), "rb") as r:
            assert r.getnchannels() == 2, "the folded file is not the stereo MIC/SYS interleave"
            assert r.getnframes() >= RATE, "the folded file is short of the audio that was captured"
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "the stop never finalised"
        assert log[0] == "capture.stop", f"capture must stop first: {log}"
        assert log.index("engine.stop.exit") > log.index("engine.stop.enter"), log
    finally:
        gate.set()
        _restore_state(saved)
        shutil.rmtree(tmp, ignore_errors=True)
    print("  OK  the recording is closed and folded to stereo BEFORE the ASR drain finishes")


def test_recorder_closed_once_before_the_drain():
    # The close() call MOVED ahead of the drain rather than being duplicated: one call site is one
    # place to reason about. (close() is idempotent, so a second call would be harmless, but a
    # second call is also a second thing to keep in step, and this pins that there is not one.)
    saved = _save_state()
    gate = threading.Event()
    try:
        log = []
        cap = FakeCapture(log)
        eng = GatedEngine(gate, log)
        rec = LoggingRecorder(log)
        _arm_live_session(engine=eng, capture=cap, recorder=rec)
        assert client.post("/api/stop?what=all").status_code == 200
        assert wait_until(lambda: rec.closed >= 1), "the recorder was never closed"
        assert eng.stopped_drain is None, "the drain finished first; the ordering was not tested"
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "the stop never finalised"
        assert rec.closed == 1, f"the recorder was closed {rec.closed} times, expected exactly one"
        assert log == ["capture.stop", "recorder.close", "engine.stop.enter", "engine.stop.exit"], log
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  the recorder is closed exactly once, between capture stop and the drain")


def test_stop_with_no_recorder_finalises_cleanly():
    # A transcribe-only session has no recorder at all. The reordered block must not assume one.
    saved = _save_state()
    try:
        cap = FakeCapture()
        eng = GatedEngine(threading.Event())   # pre-set below so the drain returns at once
        eng._gate.set()
        _arm_live_session(engine=eng, capture=cap, recorder=None, recording=False)
        r = client.post("/api/stop?what=all")
        assert r.status_code == 200, r.text
        assert wait_until(lambda: not webapp.STATE.running), "a recorder-less stop never finalised"
        assert cap.stopped is True, "capture was not stopped"
        assert eng.stopped_drain is True, "the engine was not drained"
        assert webapp.STATE.capture is None and webapp.STATE.recorder is None, "state survived reset"
        assert webapp.STATE.sink_error is None, f"a clean stop reported an error: {webapp.STATE.sink_error}"
    finally:
        _restore_state(saved)
    print("  OK  a session with no recorder still stops cleanly")


def test_repeated_stop_during_the_drain_is_still_a_no_op():
    # The idempotent second Stop is load-bearing: the page polls and the window-close handler can
    # both fire one. It must stay a plain acknowledgement, never a second teardown.
    saved = _save_state()
    gate = threading.Event()
    try:
        cap = FakeCapture()
        eng = GatedEngine(gate)
        rec = LoggingRecorder([])
        _arm_live_session(engine=eng, capture=cap, recorder=rec)
        assert client.post("/api/stop?what=all").status_code == 200
        assert wait_until(lambda: webapp.STATE.recording is False), "capture stop never landed"
        again = client.post("/api/stop?what=all")
        assert again.status_code == 200, again.text
        assert again.json()["stopping"] is True, again.text
        assert rec.closed == 1, f"the second stop re-closed the recorder ({rec.closed} closes)"
        assert cap.stopped is True and webapp.STATE.capture is None
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "the stop never finalised"
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  a second /api/stop?what=all during the drain is still an idempotent no-op")


if __name__ == "__main__":
    tests = [
        test_recording_goes_false_at_capture_stop_not_at_reset,
        test_status_reports_capturing_false_while_still_stopping,
        test_recording_is_closed_and_folded_before_the_drain_finishes,
        test_recorder_closed_once_before_the_drain,
        test_stop_with_no_recorder_finalises_cleanly,
        test_repeated_stop_during_the_drain_is_still_a_no_op,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: unexpected {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed} stop-means-stop test(s) failed.")
        sys.exit(1)
    print("\nAll stop-means-stop tests passed.")
