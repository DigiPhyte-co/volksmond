"""WP-2a: Stop means stop. The full-stop path must tell the truth about capture, immediately.

The incident: Stop was pressed 12 minutes into a 48 minute meeting. Capture stopped at once and
both per-source WAVs were frozen on disk from that second, but the ASR backlog took over twenty
minutes to drain, and for all of it the app kept saying "Recording audio", kept counting the clock
up, kept the meters moving, had not folded the stereo <stem>.wav, and refused to start the next
session. For a product sold on "local only, nothing leaves your machine", a recording indicator
that lies is the worst possible bug.

WP-2a fixes what the app SAYS, not when it writes. The recording is still finalised after the
drain, exactly where it always was, so nothing on disk depends on any of this; what depends on it
is whether the screen is telling the truth while the drain runs.

What is pinned here:
  (a) STATE.recording goes False when CAPTURE stops, not when the session finally resets;
  (b) /api/status publishes `capturing` and `capture_ended_at`, reading False and stamped while
      `stopping` and `running` are both still True (the 10 s status poll is what used to
      re-assert the lie within a tick of any client-side fix);
  (c) an UNCONFIRMED shutdown asserts NONE of that. Claiming a microphone is off while it may
      still be open is the original bug inverted and worse, so everything stays lit, the screen
      keeps saying "Stopping", and the reason is published at once;
  (d) that reason survives reset(), because the session ends either way and the app would
      otherwise go idle asserting exactly what it had just refused to assert;
  (e) both finalise paths (what="all" and the transcription drain) answer identically;
  (f) a session with no recorder at all still stops cleanly, and a second Stop mid-drain is
      still an idempotent no-op.

Plus the evidence the claim rests on: CaptureBase.stop() reports True only when its sources
closed, the backend released and every chunker joined. The platform backends used to swallow and
discard their own close failures, so a try/except around stop() confirmed precisely nothing.

Everything is stubbed: STATE is hand-set and restored, the engine is a fake whose stop(drain=True)
blocks on a gate so the "still draining" window can be inspected, and the capture contract runs
against a device-free CaptureBase subclass with scriptable hooks. No devices, no model, no audio.

Run:  python tests/test_stop_means_stop.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import threading
import time
from datetime import datetime

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe import capture_core
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

# Loopback Host + CSRF token, exactly like test_web_api.py, so we exercise /api/stop, not the guards.
client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


def wait_until(pred, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


class FakeCapture:
    """Device-free capture whose stop() CONFIRMS shutdown, like a healthy CaptureBase: sources
    closed without error and every chunker joined. Only a literal True counts as confirmed, so
    returning it here is load-bearing rather than decoration."""

    def __init__(self, log=None):
        self.stopped = False
        self._log = log

    def stop(self):
        self.stopped = True
        if self._log is not None:
            self._log.append("capture.stop")
        return True

    def aec_state(self):
        return False, False


class FailingCapture:
    """Shutdown that fails and SAYS so, which is the production contract: CaptureBase.stop() does
    not raise, it returns False. The microphone may well still be open, so nothing downstream is
    allowed to state that it is closed."""

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True
        return False

    def aec_state(self):
        return False, False


class RaisingCapture:
    """Shutdown that raises. CaptureBase.stop() is written never to do this, so the web layer must
    not DEPEND on that: a raise has to reach the same unconfirmed conclusion, not a 500 and a
    session stuck in `stopping` for ever. Belt and braces on a boundary, not a model of a backend."""

    def stop(self):
        raise OSError("the audio device would not release")

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

    def on_chunk(self, source, audio, t_start, block=False, timeout=None):
        return True     # a late chunker's tail still reaches the engine as well as the recorder

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
            st.session_counted, st.sink_error, st.started_at, st.silence_stop, st.silence_watch,
            st.capture_ended_at)


def _restore_state(saved):
    st = webapp.STATE
    (st.running, st.stopping, st.recording, st.recording_started, st.transcribing,
     st.source_kind, st.engine, st.capture, st.recorder, st.md_sink, st.output_path,
     st.session_counted, st.sink_error, st.started_at, st.silence_stop, st.silence_watch,
     st.capture_ended_at) = saved


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
    st.capture_ended_at = None
    st.started_at = datetime.now()


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


def test_stop_with_no_recorder_still_reports_capture_off():
    # A transcribe-only session has no recorder at all. It still has a microphone, so it still owes
    # the user the same answer: the confirmation and the capture-end stamp must not be wired
    # through the recording path, and a clean stop must not invent an error.
    saved = _save_state()
    gate = threading.Event()
    try:
        cap = FakeCapture()
        eng = GatedEngine(gate)
        _arm_live_session(engine=eng, capture=cap, recorder=None, recording=False)
        r = client.post("/api/stop?what=all")
        assert r.status_code == 200, r.text
        assert wait_until(lambda: client.get("/api/status").json().get("capturing") is False), \
            "a recorder-less session never reported capture off"
        st = client.get("/api/status").json()
        assert st["running"] is True and st["stopping"] is True, f"not mid-drain: {st}"
        assert st["capture_ended_at"], f"no capture-end stamp on a recorder-less session: {st}"
        assert st["sink_error"] is None, f"a clean stop reported an error: {st['sink_error']}"
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "a recorder-less stop never finalised"
        assert cap.stopped is True, "capture was not stopped"
        assert eng.stopped_drain is True, "the engine was not drained"
        assert webapp.STATE.capture is None and webapp.STATE.recorder is None, "state survived reset"
        assert webapp.STATE.sink_error is None, f"a clean stop reported an error: {webapp.STATE.sink_error}"
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  a session with no recorder still reports capture off, stamped, and stops cleanly")


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
        assert rec.closed == 0, f"the second stop finalised the recording early ({rec.closed} closes)"
        assert cap.stopped is True and webapp.STATE.capture is None
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "the stop never finalised"
        assert rec.closed == 1, f"the recording was closed {rec.closed} times, expected exactly one"
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  a second /api/stop?what=all during the drain is still an idempotent no-op")


def test_failed_capture_stop_never_claims_the_microphone_is_off():
    # The original trust bug inverted, and worse. If shutdown failed, the native streams may still
    # be open, so a screen stating "microphone off, nothing more is being recorded" would be lying
    # in the dangerous direction. An unconfirmed stop therefore asserts nothing it cannot defend:
    # everything stays lit, the screen keeps saying "Stopping", and the reason is surfaced.
    saved = _save_state()
    gate = threading.Event()
    try:
        cap = RaisingCapture()
        eng = GatedEngine(gate)
        rec = LoggingRecorder([])
        _arm_live_session(engine=eng, capture=cap, recorder=rec)
        assert client.post("/api/stop?what=all").status_code == 200
        assert wait_until(lambda: client.get("/api/status").json().get("stopping") is True)
        st = client.get("/api/status").json()
        assert st["capturing"] is True, f"a failed capture stop still claimed the mic was off: {st}"
        assert st["recording"] is True, f"a failed capture stop still cleared recording: {st}"
        assert st["capture_ended_at"] is None, f"a failed capture stop stamped a capture end: {st}"
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "the stop never finalised"
        assert webapp.STATE.sink_error and "could not stop audio capture cleanly" in webapp.STATE.sink_error.lower(), \
            f"the capture failure was not surfaced: {webapp.STATE.sink_error!r}"
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  a failed capture stop never claims the microphone is off, and is surfaced")


def test_an_unconfirmed_stop_is_still_reported_once_the_app_is_idle():
    # The session ends regardless: leaving it stuck open would be its own bug, and the retained
    # handle cannot be retried without a recovery state machine that is not worth building. So the
    # reset does happen, and after it /api/status reports the ordinary idle shape, capturing false
    # included. Without a notice riding through the reset the app would therefore end up asserting
    # exactly what it had just refused to assert, one level later. sink_error is that notice, and
    # it has to survive reset() and say something a user can act on.
    saved = _save_state()
    gate = threading.Event()
    try:
        cap = FailingCapture()
        eng = GatedEngine(gate)
        _arm_live_session(engine=eng, capture=cap, recorder=None)
        assert client.post("/api/stop?what=all").status_code == 200
        assert wait_until(lambda: webapp.STATE.sink_error is not None), \
            "the failure was not published during the drain, when the user can still act on it"
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "the stop never finalised"
        st = client.get("/api/status").json()
        assert st["running"] is False and st["capturing"] is False, st
        note = st["sink_error"]
        assert note, "the app went idle with no word that the stop was never confirmed"
        assert "could not confirm" in note.lower(), f"the notice does not say what happened: {note!r}"
        assert "close volksmond" in note.lower(), f"the notice gives the user nothing to do: {note!r}"
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  an unconfirmed stop still tells the user, plainly, once the app is idle")


def test_capture_ended_at_is_server_owned():
    # The clock has to freeze at the meeting's real length. A page reloaded fifteen minutes into a
    # drain cannot work that out for itself, so the server stamps the moment and publishes it.
    saved = _save_state()
    gate = threading.Event()
    try:
        cap = FakeCapture()
        eng = GatedEngine(gate)
        _arm_live_session(engine=eng, capture=cap, recorder=None, recording=False)
        assert client.get("/api/status").json()["capture_ended_at"] is None, \
            "a live session already carries a capture end"
        assert client.post("/api/stop?what=all").status_code == 200
        assert wait_until(lambda: client.get("/api/status").json().get("capture_ended_at")), \
            "capture_ended_at was never published"
        ended = datetime.fromisoformat(client.get("/api/status").json()["capture_ended_at"])
        assert ended >= webapp.STATE.started_at, "capture ended before the session started"
        assert abs((datetime.now() - ended).total_seconds()) < 30, f"capture_ended_at is not recent: {ended}"
        gate.set()
        assert wait_until(lambda: not webapp.STATE.running), "the stop never finalised"
        assert webapp.STATE.capture_ended_at is None, "capture_ended_at survived the session reset"
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  capture_ended_at is stamped by the server at the confirmed stop, and cleared on reset")


def test_idle_status_states_capturing_false():
    # `capturing` is presented as the authoritative answer to "is the microphone open". An idle app
    # answering that with an absent key would make every reader special-case the response shape.
    saved = _save_state()
    try:
        webapp.STATE.running = False
        st = client.get("/api/status").json()
        assert st["running"] is False, st
        assert st["capturing"] is False, f"the idle status omits or misreports capturing: {st}"
    finally:
        _restore_state(saved)
    print("  OK  the idle /api/status states capturing=False rather than omitting it")


def test_transcription_finalise_uses_the_same_confirmation():
    # "Stop transcription, then stop recording" ends the session through the OTHER finalise path,
    # which used to null STATE.capture BEFORE stopping and ignore the result. That published
    # capturing=false on a microphone nothing had confirmed closed, and threw away the only handle
    # that could have inspected or retried it: the same trust bug, through the back door.
    saved = _save_state()
    st = webapp.STATE
    try:
        cap = FailingCapture()
        st.running = True
        st.stopping = True          # transcription already stopped; its drain is what finalises
        st.transcribing = False
        st.recording = False        # recording stopped while we drained, so this IS the end
        st.recording_started = True
        st.source_kind = "live"
        st.engine = None
        st.capture = cap
        st.recorder = None
        st.md_sink = None
        st.output_path = None
        st.session_counted = True
        st.sink_error = None
        st.capture_ended_at = None
        st.started_at = datetime.now()
        cap_err = webapp._confirm_capture_stopped(st.capture)
        assert cap_err, "an unconfirmed stop reported success"
        assert st.capture is cap, "the capture handle was dropped despite an unconfirmed stop"
        assert client.get("/api/status").json()["capturing"] is True, \
            "status claimed the microphone was off after an unconfirmed stop"
        # M1: published straight away, not held back until the session ends. The drain that follows
        # can run for twenty minutes, and the user needs to know why the indicator is still lit
        # WHILE it is still lit.
        assert st.sink_error == cap_err, f"the failure was not published immediately: {st.sink_error!r}"
        assert client.get("/api/status").json()["sink_error"] == cap_err, \
            "the failure is not visible on /api/status during the actionable window"
        # And the confirmed case does drop the handle and stamp the clock.
        st.capture = FakeCapture()
        assert webapp._confirm_capture_stopped(st.capture) is None, "a healthy stop reported an error"
        assert st.capture is None and st.capture_ended_at is not None
    finally:
        _restore_state(saved)
    print("  OK  the transcription finalise path confirms capture the same way, and publishes at once")


def test_transcription_finalise_surfaces_a_failed_capture_stop_end_to_end():
    # The same defect driven through the real endpoint rather than the helper: stop transcription,
    # stop recording while it drains, and let the drain finalise the session. That path used to
    # call cap.stop() and discard the result entirely, so a shutdown that failed ended the session
    # silently, with the transcript reporting nothing wrong.
    saved = _save_state()
    gate = threading.Event()
    st = webapp.STATE
    try:
        cap = FailingCapture()
        eng = GatedEngine(gate)
        _arm_live_session(engine=eng, capture=cap, recorder=None, recording=True)
        r = client.post("/api/stop?what=transcription")
        assert r.status_code == 200, r.text
        assert wait_until(lambda: eng.stopped_drain is None and st.stopping), "the drain never started"
        st.recording = False        # the user stops recording too, so the drain must finalise
        gate.set()
        assert wait_until(lambda: not st.running), "the transcription drain never finalised"
        assert cap.stopped is True, "the capture was never stopped"
        assert st.sink_error and "confirm" in st.sink_error, \
            f"a failed capture stop ended the session silently: {st.sink_error!r}"
        assert st.capture_ended_at is None, "an unconfirmed stop stamped a capture end"
    finally:
        gate.set()
        _restore_state(saved)
    print("  OK  a failed capture stop is surfaced through the transcription finalise path too")


# --- the capture contract itself ------------------------------------------------------------
# Everything above tests the WEB layer's branch selection. These test the thing it branches on.
# CaptureBase.stop()'s True is the entire licence for finalising a recording before the ASR
# backlog has drained, so it has to be earned against real hooks and real threads, not asserted.


class _ProbeCapture(capture_core.CaptureBase):
    """A CaptureBase with no devices. The hooks are scriptable, because the failures that matter
    are exactly the ones the platform backends used to swallow and discard."""

    def __init__(self, close_ok=True, close_raises=False, release_ok=True, **kw):
        kw.setdefault("agc", False)     # MIC-only with agc off never engages the APM worker
        super().__init__(**kw)
        self.close_ok = close_ok
        self.close_raises = close_raises
        self.release_ok = release_ok

    def _open_sources(self):
        self._register_source("MIC", capture_core.TARGET_RATE, 1)

    def _close_sources(self):
        if self.close_raises:
            raise OSError("device busy")
        return self.close_ok

    def _release_backend(self):
        return self.release_ok


def _probe(**kw):
    cap = _ProbeCapture(chunk_seconds=1, on_chunk=lambda *a: None, **kw)
    cap.start()
    return cap


def test_capture_stop_confirms_only_when_every_part_succeeded():
    # The barrier's truth table. Before this, both platform _close_sources() caught and discarded
    # every close exception internally, so the try/except around the call never saw a failure and
    # stop() returned True whatever had happened: the confirmation was decorative.
    healthy = _probe()
    assert healthy.stop() is True, "a healthy shutdown must confirm"

    bad_close = _probe(close_ok=False)
    assert bad_close.stop() is False, "a reported close failure must NOT confirm"

    raising_close = _probe(close_raises=True)
    assert raising_close.stop() is False, "a raised close failure must NOT confirm"
    assert raising_close._stop_event.is_set(), (
        "a raised close must still set the stop event, or the chunkers never flush and late "
        "blocks are never rejected")

    bad_release = _probe(release_ok=False)
    assert bad_release.stop() is False, "a reported backend-release failure must NOT confirm"
    print("  OK  CaptureBase.stop() confirms only when close AND release both report success")


def test_capture_stop_does_not_confirm_while_a_chunker_is_still_alive():
    # The join has always been bounded (BLOCK_SECONDS + 1.5) and a worker outliving it used to pass
    # silently. That is exactly the case where the final chunk may not have reached on_chunk yet,
    # so finalising the recording there would lose the last seconds of the meeting.
    cap = _probe()
    release = threading.Event()
    lingering = threading.Thread(target=lambda: release.wait(30.0), daemon=True, name="chunker-LINGER")
    lingering.start()
    cap._workers.append(lingering)
    try:
        assert cap.stop() is False, "a chunker still alive after its join must NOT confirm"
    finally:
        release.set()
        lingering.join(5.0)
    print("  OK  CaptureBase.stop() does not confirm while a chunker outlives its join window")


if __name__ == "__main__":
    tests = [
        test_recording_goes_false_at_capture_stop_not_at_reset,
        test_status_reports_capturing_false_while_still_stopping,
        test_stop_with_no_recorder_still_reports_capture_off,
        test_repeated_stop_during_the_drain_is_still_a_no_op,
        test_failed_capture_stop_never_claims_the_microphone_is_off,
        test_an_unconfirmed_stop_is_still_reported_once_the_app_is_idle,
        test_capture_ended_at_is_server_owned,
        test_idle_status_states_capturing_false,
        test_transcription_finalise_uses_the_same_confirmation,
        test_transcription_finalise_surfaces_a_failed_capture_stop_end_to_end,
        test_capture_stop_confirms_only_when_every_part_succeeded,
        test_capture_stop_does_not_confirm_while_a_chunker_is_still_alive,
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
