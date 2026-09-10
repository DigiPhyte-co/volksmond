"""Tests for the honest-Stop wave: Stop always terminates, and a struggling machine says so.

The field failure this covers (a Mac, 2026-08): a session transcribed nothing at all for a whole
meeting, Stop showed a spinner and the bare word "Finishing" with NO number, and force-quitting the
window was the only way out. A later session fell far behind, wrote "[... ~12 chunk(s) not
transcribed ...]" into the transcript, and only recovered when the far end stopped screen sharing.

Covered here, cheapest first:

  1. transcribe.Engine: per-chunk ASR failures are COUNTED and handed out (asr_errors +
     on_asr_error). This is what stops a backend failing on every chunk looking like a quiet room.
  2. web/app.py's bounded Stop: _drain_engine_bounded returns when the user asks to discard and
     when the worker is provably wedged, always through the engine's EXISTING stop(drain=False)
     abort; _join_builder is bounded and hard-aborts instead of hanging; a discarded stop still
     closes the transcript and finalises the recording, and keeps every line already transcribed.
  3. /api/status while stopping: a real number when there is one (engine queue PLUS the still-held
     pending-audio backlog, so the catch-up case counts too) and an honest null + phase when there
     is not - never a fabricated 0, which is what rendered the countless spinner.
  4. The ASR-error nudge: _on_asr_error raises ONE banner past the threshold and updates it in
     place; /api/asr-error-nudge dismisses it for the session.

NOTE (1.14 assembly): the draft's own backend-agnostic "falling behind" signal was DROPPED here in
favour of wp1's queue-depth warning, which ships on this branch. Its behaviour is covered by
tests/test_struggle_signal.py (the GPU/MLX struggle warning), so this file no longer tests
_check_falling_behind / on_behind / _on_behind. The pending-audio lost-audio marker was likewise
dropped: head already reports evicted / abandoned pre-engine audio in the transcript
(web/app.py:_mark_dropped_backlog / _mark_abandoned_backlog via _PendingAudio.dropped_span /
held_span), so the draft's on_evict marker would have been a second mechanism for the same job.

No audio, no model load and no real capture: the seams are transcribe.WhisperModel (faked),
notify.show (monkeypatched), config.load (monkeypatched) and STATE (hand-set and restored). The
stop windows are shrunk to fractions of a second for the wedged-worker tests, so the whole file
runs in a couple of seconds.

Run:  python tests/test_stop_honesty.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import threading
import time

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe import config, notify, transcribe
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


# --- helpers ---------------------------------------------------------------

class _FakeSeg:
    def __init__(self, text, start=0.0, end=1.0):
        self.text = text
        self.start = start
        self.end = end


class _FakeModel:
    """Stand-in for faster_whisper.WhisperModel: echoes the chunk marker back as one segment,
    with a small delay so a real backlog exists when stop() fires."""
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, **kwargs):
        time.sleep(0.02)
        return ([_FakeSeg(f"seg-{audio}")], {})


class _RaisingModel:
    """A backend that throws on every chunk: the broken-install / out-of-memory shape."""
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, **kwargs):
        raise RuntimeError("Metal out of memory")


_ORIG_MODEL = None


def setup_module(module=None):
    """Install the fake model for pytest runs (xunit hook, no pytest import needed); the
    plain-script path patches in __main__ below."""
    global _ORIG_MODEL
    _ORIG_MODEL = transcribe.WhisperModel
    transcribe.WhisperModel = _FakeModel


def teardown_module(module=None):
    transcribe.WhisperModel = _ORIG_MODEL


def _make_engine(model_cls=None):
    """A started Engine on a tiny CPU tier with the fake model, plus the list its subscriber
    collects into. The silence gate is inert here: the chunks are plain strings, not audio, so
    _chunk_is_silence falls through to _is_silence, which keeps them."""
    if model_cls is not None:
        prev, transcribe.WhisperModel = transcribe.WhisperModel, model_cls
    try:
        eng = transcribe.Engine(tier="cpu-min", language="af", cpu_threads=1, beam_size=1)
    finally:
        if model_cls is not None:
            transcribe.WhisperModel = prev
    collected = []
    eng.subscribe(collected.append)
    eng.start()
    return eng, collected


class _NeverDrains:
    """An engine whose drain never returns: the wedged worker. stop(drain=False) is the escape,
    and it releases the blocked drain exactly as the real Engine's abort does."""
    def __init__(self, depth=7):
        self.released = threading.Event()
        self.aborted = False
        self.depth = depth

    def pending(self):
        return self.depth

    def is_alive(self):
        return not self.released.is_set()

    def stop(self, drain=True, timeout=None):
        if drain:
            self.released.wait(10)
            return
        self.aborted = True
        self.released.set()


class _StubEngine:
    """The minimum /api/status reads off a live engine (its family-override preference and its
    backlog), for tests that hand-set STATE.engine and then GET the status."""
    engine = "auto"

    def pending(self):
        return 0


class _FakeSink:
    def __init__(self):
        self.segments = []
        self.closed = False
        self.last_error = None

    def __call__(self, seg):
        if not self.closed:
            self.segments.append(seg)

    def close(self):
        self.closed = True


class _FakeRecorder(_FakeSink):
    """Same shape as the transcript sink, plus the on_chunk the recorder path uses."""
    def on_chunk(self, source, audio, t_start):
        pass


_STATE_FIELDS = ("running", "stopping", "source_kind", "engine", "recording", "recording_started",
                 "transcribing", "capture", "md_sink", "browser_sink", "recorder", "output_path",
                 "session_counted", "sink_error", "struggle_nudge", "struggle_notified",
                 "asr_errors", "asr_error_nudge", "asr_error_dismissed", "model_ready",
                 "preparing", "preparing_engine", "pending_audio", "build_thread", "started_at",
                 "stop_started_at", "stop_phase", "stop_pending", "stop_slow", "stop_discard")


def _save_state():
    return {k: getattr(webapp.STATE, k) for k in _STATE_FIELDS}


def _restore_state(saved):
    for k, v in saved.items():
        setattr(webapp.STATE, k, v)


def _catch_toasts():
    """Replace notify.show with a recorder. Returns (calls, restore)."""
    calls = []
    saved = notify.show

    def fake_show(title, body="", *, tag=None, on_click=None):
        calls.append({"title": title, "body": body, "tag": tag, "on_click": on_click})
        return True

    notify.show = fake_show
    return calls, (lambda: setattr(notify, "show", saved))


class _on_setting:
    """Force _struggle_nudge_on() to a known answer (stub config.load, clear the kill switch)."""

    def __init__(self, on=True):
        self.on = on

    def __enter__(self):
        self._load = config.load
        self._env = os.environ.get(webapp.STRUGGLE_ENV)
        os.environ.pop(webapp.STRUGGLE_ENV, None)
        config.load = lambda: {"struggle_nudge": bool(self.on)}
        return self

    def __exit__(self, *exc):
        config.load = self._load
        if self._env is None:
            os.environ.pop(webapp.STRUGGLE_ENV, None)
        else:
            os.environ[webapp.STRUGGLE_ENV] = self._env


class _fast_stop_windows:
    """Shrink the bounded-Stop windows so the wedged-worker paths are testable in milliseconds
    instead of minutes. Restores every constant afterwards."""

    def __init__(self, grace=0.05, stall=0.3, join=0.3, build_join=0.3, poll=0.02):
        self._vals = {"STOP_GRACE_SECONDS": grace, "STOP_STALL_SECONDS": stall,
                      "STOP_JOIN_SECONDS": join, "STOP_BUILD_JOIN_SECONDS": build_join,
                      "_STOP_POLL_SECONDS": poll}

    def __enter__(self):
        self._saved = {k: getattr(webapp, k) for k in self._vals}
        for k, v in self._vals.items():
            setattr(webapp, k, v)
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            setattr(webapp, k, v)


def _wait_idle(timeout=10.0):
    """Wait for the background drain thread to finish the session."""
    deadline = time.time() + timeout
    while webapp.STATE.running and time.time() < deadline:
        time.sleep(0.02)
    return not webapp.STATE.running


# --- 1. the Engine signals (transcribe.py) ---------------------------------

def test_per_chunk_asr_failures_are_counted_and_handed_out():
    # A backend that throws on every chunk used to leave only a log line, which is why an empty
    # transcript and a broken model looked identical. Now the count is the evidence.
    eng, collected = _make_engine(_RaisingModel)
    seen = []
    eng.on_asr_error = lambda n, msg: seen.append((n, msg))
    try:
        for i in range(3):
            assert eng.on_chunk("MIC", f"c{i}", float(i), block=True, timeout=2)
        eng.stop(drain=True, timeout=10)
    finally:
        eng.stop(drain=False, timeout=5)
    assert eng.asr_errors == 3, eng.asr_errors
    assert "Metal out of memory" in eng.last_asr_error, eng.last_asr_error
    assert [n for n, _ in seen] == [1, 2, 3], seen
    assert collected == [], f"a failing backend must publish no text, got {collected}"
    print("  OK  every failing chunk is counted and reported (asr_errors + on_asr_error)")


def test_a_raising_asr_error_callback_never_kills_the_worker():
    # Same contract as on_downgrade: the engine must survive a bad listener.
    eng, _collected = _make_engine(_RaisingModel)
    eng.on_asr_error = lambda n, msg: (_ for _ in ()).throw(ValueError("boom"))
    try:
        assert eng.on_chunk("MIC", "c0", 0.0, block=True, timeout=2)
        time.sleep(0.2)
        assert eng.is_alive(), "a raising on_asr_error took the transcription worker down"
        assert eng.asr_errors == 1, eng.asr_errors
    finally:
        eng.stop(drain=False, timeout=5)
    print("  OK  a raising on_asr_error callback never breaks the transcription worker")


# --- 2. the bounded Stop (web/app.py) --------------------------------------

def test_bounded_drain_gives_up_on_a_wedged_worker_instead_of_hanging():
    # The field failure: engine.stop(drain=True) joined the worker with no timeout, so a wedged
    # model call hung Stop forever. The backlog never shrinks here, so the stall window trips.
    eng = _NeverDrains()
    with _fast_stop_windows():
        t0 = time.monotonic()
        drained = webapp._drain_engine_bounded(eng, None)
        elapsed = time.monotonic() - t0
    assert drained is False, "a wedged worker must report an abandoned backlog, not a clean drain"
    assert eng.aborted, "the abandon path must go through the engine's existing stop(drain=False)"
    assert elapsed < 5.0, f"the bounded drain took {elapsed:.1f}s; it must not hang"
    print("  OK  a wedged worker is abandoned through stop(drain=False), and Stop returns")


def test_bounded_drain_returns_at_once_when_the_backlog_finishes():
    # The normal path must be untouched: nothing abandoned, no abort, no waiting around.
    class _DrainsCleanly(_NeverDrains):
        def stop(self, drain=True, timeout=None):
            if drain:
                self.released.set()
                return
            self.aborted = True

    eng = _DrainsCleanly()
    with _fast_stop_windows(grace=5.0, stall=5.0):
        drained = webapp._drain_engine_bounded(eng, None)
    assert drained is True, "a backlog that finishes must NOT be reported as abandoned"
    assert eng.aborted is False, "a clean drain must never reach the abort path"
    print("  OK  a backlog that finishes drains normally: nothing abandoned, no abort")


def test_the_user_can_discard_the_backlog_and_stop_now():
    eng = _NeverDrains()
    discard = threading.Event()
    threading.Timer(0.05, discard.set).start()
    with _fast_stop_windows(grace=60.0, stall=60.0):   # neither timer can be what ends this
        t0 = time.monotonic()
        drained = webapp._drain_engine_bounded(eng, discard)
        elapsed = time.monotonic() - t0
    assert drained is False and eng.aborted, "'stop now' must abandon the backlog"
    assert elapsed < 5.0, f"'stop now' took {elapsed:.1f}s; it must take effect within a poll"
    print("  OK  'stop now' discards the backlog through the abort path, within a poll")


def test_the_builder_join_is_bounded_and_hard_aborts_rather_than_hanging():
    # build_thread.join() had no timeout, so a builder that never returned hung Stop with no way
    # out. Bounded now, and the timeout is HANDLED: the engine is hard-aborted, which is what makes
    # the builder's next enqueue fail so it can never become a second feeder.
    never = threading.Event()
    t = threading.Thread(target=lambda: never.wait(30), daemon=True)
    t.start()
    eng = _NeverDrains()
    try:
        with _fast_stop_windows():
            t0 = time.monotonic()
            released = webapp._join_builder(t, eng)
            elapsed = time.monotonic() - t0
    finally:
        never.set()
    assert released is False, "a builder that never returns must be reported as not released"
    assert eng.aborted, "an unreleased builder must leave the engine hard-aborted, not draining"
    assert elapsed < 5.0, f"the builder join took {elapsed:.1f}s; it must be bounded"
    print("  OK  the model-builder join is bounded and hard-aborts instead of hanging Stop")


def test_a_discarded_stop_keeps_the_transcript_and_finalises_the_recording():
    # The whole safety claim of the discard path, end to end through POST /api/stop?what=all:
    # what is already transcribed stays, the transcript is closed, and the WAV is finalised.
    st = webapp.STATE
    saved = _save_state()
    md, rec = _FakeSink(), _FakeRecorder()
    md.segments.append("line already transcribed")
    eng = _NeverDrains()
    try:
        with _fast_stop_windows(grace=0.02, stall=0.15):
            st.running, st.stopping, st.source_kind = True, False, "live"
            st.transcribing, st.recording, st.model_ready = True, True, True
            st.engine, st.capture, st.md_sink, st.recorder = eng, None, md, rec
            st.preparing_engine = st.pending_audio = st.build_thread = None
            st.session_counted, st.sink_error = False, None
            r = client.post("/api/stop?what=all")
            assert r.status_code == 200, r.text
            assert r.json()["stop_phase"] == "draining", r.json()
            assert _wait_idle(), "the bounded stop never finished: STATE.running stuck True"
        assert eng.aborted, "a wedged drain must end through the abort path"
        assert md.closed and rec.closed, (md.closed, rec.closed)
        assert md.segments == ["line already transcribed"], \
            f"the discard path must not lose already-transcribed lines: {md.segments}"
    finally:
        eng.released.set()
        _restore_state(saved)
    print("  OK  a discarded Stop keeps the transcript so far and still finalises the recording")


def test_stop_now_requires_a_stop_in_progress():
    st = webapp.STATE
    saved = _save_state()
    try:
        st.running, st.stopping, st.source_kind = True, False, "live"
        assert client.post("/api/stop-now").status_code == 409, "no stop in progress must 409"
        st.stopping = True
        st.stop_discard = threading.Event()
        r = client.post("/api/stop-now")
        assert r.status_code == 200 and r.json()["stop_phase"] == "discarding", r.text
        assert st.stop_discard.is_set(), "the endpoint must signal the drain thread's event"
        # CSRF is enforced like every other unsafe endpoint.
        bare = TestClient(app, base_url="http://localhost")
        assert bare.post("/api/stop-now").status_code == 403
    finally:
        _restore_state(saved)
    print("  OK  /api/stop-now needs a stop in progress, signals the drain, and is CSRF-protected")


# --- 3. /api/status while stopping -----------------------------------------

def test_status_counts_the_backlog_even_with_no_published_engine():
    # THE countless-"Finishing" bug: `pending` was reported only when STATE.engine was set, so a
    # Stop during catch-up - the exact field failure - sent no number at all.
    st = webapp.STATE
    saved = _save_state()

    class _Prep:
        def pending(self):
            return 4

    pb = webapp._PendingAudio(webapp._PENDING_MAX_SAMPLES)
    for i in range(3):
        pb.append("MIC", "x" * 10, float(i))
    try:
        st.running, st.stopping, st.source_kind = True, True, "live"
        st.transcribing, st.recording, st.model_ready = False, False, True
        st.engine, st.md_sink, st.capture = None, None, None
        st.preparing_engine, st.pending_audio = _Prep(), pb
        st.stop_phase, st.stop_pending, st.stop_slow = "draining", None, False
        body = client.get("/api/status").json()
        assert body["pending"] == 7, f"engine queue + held backlog, got {body.get('pending')}"
        assert body["stop_phase"] == "draining" and body["stop_slow"] is False, body
    finally:
        _restore_state(saved)
    print("  OK  /api/status counts the unpublished engine's queue PLUS the held backlog")


def test_status_says_starting_rather_than_inventing_a_zero():
    # With no engine and no buffer there is honestly no number. It must be null, not 0: 0 is what
    # the page turned into a bare, countless "Finishing".
    st = webapp.STATE
    saved = _save_state()
    try:
        st.running, st.stopping, st.source_kind = True, True, "live"
        st.transcribing, st.recording, st.model_ready = True, False, False
        st.engine = st.preparing_engine = st.pending_audio = st.md_sink = st.capture = None
        st.stop_phase, st.stop_pending, st.stop_slow = "starting", None, False
        body = client.get("/api/status").json()
        assert body["pending"] is None, f"an unknown backlog must be null, got {body['pending']!r}"
        assert body["stop_phase"] == "starting", body
    finally:
        _restore_state(saved)
    print("  OK  /api/status reports a null backlog with an honest phase, never a fabricated 0")


def test_arm_stop_picks_the_phase_from_what_the_session_actually_has():
    st = webapp.STATE
    saved = _save_state()
    try:
        st.transcribing, st.model_ready, st.preparing = False, False, False
        webapp._arm_stop()
        assert st.stop_phase == "closing", st.stop_phase       # record-only: nothing to transcribe
        st.transcribing, st.model_ready = True, True
        webapp._arm_stop()
        assert st.stop_phase == "draining", st.stop_phase      # a live model with a backlog
        st.model_ready = False
        webapp._arm_stop()
        assert st.stop_phase == "starting", st.stop_phase      # still catching up
        assert isinstance(st.stop_discard, threading.Event) and not st.stop_discard.is_set()
        webapp._disarm_stop()
        assert st.stop_phase == "" and st.stop_discard is None, (st.stop_phase, st.stop_discard)
    finally:
        _restore_state(saved)
    print("  OK  the stop phase is closing / draining / starting, from what the session has")


def test_asr_error_nudge_raises_one_banner_and_updates_it_in_place():
    st = webapp.STATE
    saved = _save_state()
    eng = object()
    try:
        st.running, st.source_kind, st.stopping = True, "live", False
        st.engine, st.preparing_engine = eng, None
        st.asr_errors, st.asr_error_nudge, st.asr_error_dismissed = 0, None, False
        # Below the threshold the count is recorded but nothing is shown: one bad chunk is normal.
        for i in range(1, webapp.ASR_ERROR_THRESHOLD):
            assert webapp._on_asr_error(eng, i, "Metal out of memory") is None
            assert st.asr_errors == i and st.asr_error_nudge is None
        n = webapp.ASR_ERROR_THRESHOLD
        pub = webapp._on_asr_error(eng, n, "Metal out of memory")
        assert pub and pub["count"] == n, pub
        assert "Metal out of memory" in pub["message"], pub
        assert pub["log_path"].endswith("volksmond.log"), pub["log_path"]
        # Every later failure updates the ONE banner rather than stacking a new one.
        webapp._on_asr_error(eng, n + 7, "Metal out of memory")
        assert st.asr_error_nudge["count"] == n + 7, st.asr_error_nudge
        assert st.asr_errors == n + 7
    finally:
        _restore_state(saved)
    print("  OK  ASR failures raise ONE banner past the threshold and update it in place")


def test_asr_error_nudge_surfaces_on_status_and_dismisses_for_the_session():
    st = webapp.STATE
    saved = _save_state()
    eng = _StubEngine()
    try:
        st.running, st.stopping, st.source_kind = True, False, "live"
        st.transcribing, st.recording, st.model_ready = True, False, True
        st.engine, st.preparing_engine, st.md_sink, st.capture = eng, None, None, None
        st.asr_errors, st.asr_error_dismissed = 9, False
        st.asr_error_nudge = {"count": 9, "message": "boom", "log_path": "/tmp/volksmond.log"}
        body = client.get("/api/status").json()
        assert body["asr_errors"] == 9 and body["asr_error_nudge"]["count"] == 9, body
        r = client.post("/api/asr-error-nudge")
        assert r.status_code == 200 and r.json() == {"asr_error_nudge": None}, r.text
        assert st.asr_error_nudge is None and st.asr_error_dismissed is True
        # A dismissed banner is not re-raised by later failures; the count still climbs.
        assert webapp._on_asr_error(eng, 40, "boom") is None
        assert st.asr_error_nudge is None and st.asr_errors == 40
        bare = TestClient(app, base_url="http://localhost")
        assert bare.post("/api/asr-error-nudge").status_code == 403, "CSRF must be enforced"
    finally:
        _restore_state(saved)
    print("  OK  the ASR-error banner reaches /api/status and dismisses for the session only")


if __name__ == "__main__":
    setup_module()
    tests = (test_per_chunk_asr_failures_are_counted_and_handed_out,
             test_a_raising_asr_error_callback_never_kills_the_worker,
             test_bounded_drain_gives_up_on_a_wedged_worker_instead_of_hanging,
             test_bounded_drain_returns_at_once_when_the_backlog_finishes,
             test_the_user_can_discard_the_backlog_and_stop_now,
             test_the_builder_join_is_bounded_and_hard_aborts_rather_than_hanging,
             test_a_discarded_stop_keeps_the_transcript_and_finalises_the_recording,
             test_stop_now_requires_a_stop_in_progress,
             test_status_counts_the_backlog_even_with_no_published_engine,
             test_status_says_starting_rather_than_inventing_a_zero,
             test_arm_stop_picks_the_phase_from_what_the_session_actually_has,
             test_asr_error_nudge_raises_one_banner_and_updates_it_in_place,
             test_asr_error_nudge_surfaces_on_status_and_dismisses_for_the_session)
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    teardown_module()
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll stop-honesty tests passed.")
