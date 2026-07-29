"""Tests for the long-silence nudge (WP-9b): live_transcribe/silencewatch.py + its wiring.

Three layers, cheapest first:

  1. SilenceWatch on its own. It is pure arithmetic over a clock and a dict of dB values,
     so every rule it owns (arming, resets, the dead-device case, snooze allowing exactly
     one more, mute, the per-session cap) is table-driven here in microseconds.
  2. web/app.py's _silence_tick with a FAKE engine carrying fake energy rings and a
     monkeypatched notify.show. This is the layer that must query the rings on the SESSION
     clock and publish exactly one nudge and one toast per trip.
  3. The endpoints and the gating: /api/status carries the nudge, POST /api/silence-nudge
     answers it (409 with no session, CSRF-protected), and no watcher thread is started for
     file transcription, with the setting off, or with the env kill switch set.

No audio, no pywin32, no real capture and no model load: the seams are the rings (any
object with max_db), notify.show (monkeypatched) and STATE (hand-set and restored).

Run:  python tests/test_silence_nudge.py   (from the project root; exit 0 = pass)
"""
import inspect
import os
import sys
import threading
import time

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe import config, notify, silencewatch
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

SilenceWatch = silencewatch.SilenceWatch

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


# --- helpers ---------------------------------------------------------------

class FakeRing:
    """Stands in for transcribe.SysEnergyRing: answers max_db from a scripted list and
    records the windows it was asked about (so the session-clock query can be checked).

    The last scripted value repeats forever, so a test only has to script the interesting
    prefix. None means "no frames in that window", which is the dead-device signal."""

    def __init__(self, values):
        self.values = list(values)
        self.windows = []

    def max_db(self, t_lo, t_hi):
        self.windows.append((t_lo, t_hi))
        if not self.values:
            return None
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


class BoomRing:
    """A ring whose reader raises: the watcher must survive it as silence, not a crash."""

    def max_db(self, t_lo, t_hi):
        raise RuntimeError("ring exploded")


class FakeEngine:
    def __init__(self, mic=None, sys=None):
        self.mic_env = mic
        self.sys_env = sys


class RecordingWatch:
    """A watch that only records the clock values it is sampled with (for the loop test)."""

    threshold_s = 300.0

    def __init__(self):
        self.nows = []

    def sample(self, now, levels):
        self.nows.append(now)
        return False

    def state(self):
        return {"minutes": 5, "nudges": 0}

    def snooze(self, now=None):
        pass

    def mute(self):
        pass


class FakeCapture:
    def __init__(self, t0=None):
        self._t0 = t0


_STATE_FIELDS = ("running", "stopping", "source_kind", "engine", "capture",
                 "silence_nudge", "silence_watch", "silence_stop")


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


def _run(watch, ticks, levels_for, start=0.0, step=1.0):
    """Sample `watch` for `ticks` ticks, `step` apart, and return the clock values at which
    it tripped. levels_for(t) supplies the level dict for each tick."""
    trips = []
    for i in range(ticks):
        t = start + i * step
        if watch.sample(t, levels_for(t)):
            trips.append(t)
    return trips


def _silent(_t):
    return {"MIC": -70.0, "SYS": -65.0}


# --- 1. SilenceWatch ------------------------------------------------------

def test_trips_once_after_the_threshold():
    # 1 Hz of dead-quiet audio: nothing before arm_s + threshold_s, exactly one trip there,
    # and nothing more afterwards (the nudge is outstanding until the user answers it).
    w = SilenceWatch(threshold_s=300.0, floor_db=-50.0, arm_s=30.0)
    trips = _run(w, 700, _silent)
    assert trips == [330.0], f"expected a single trip at arm+threshold, got {trips}"
    st = w.state()
    assert st["nudges"] == 1 and st["outstanding"] is True and st["armed"] is True, st
    print("  OK  silent session trips once at arm + threshold, then waits for an answer")


def test_arming_delay_does_not_count_as_silence():
    # The grace period must not be counted as part of the silent run: a watch armed at 30 s
    # cannot trip at 300 s just because the session was quiet from the first sample.
    w = SilenceWatch(threshold_s=300.0, arm_s=30.0)
    assert _run(w, 301, _silent) == [], "the arming window leaked into the silence clock"
    assert w.state()["armed"] is True and w.state()["nudges"] == 0
    # And a watch that is only ever sampled inside the grace period is not armed at all.
    w2 = SilenceWatch(threshold_s=1.0, arm_s=30.0)
    assert _run(w2, 20, _silent) == [] and w2.state()["armed"] is False
    print("  OK  arming delay is honoured and never counted as silence")


def test_either_channel_above_the_floor_resets():
    # Sean listening quietly while the far end talks is normal meeting behaviour, so ANY
    # channel above the floor clears the run for everyone. Checked both ways round.
    for loud in ("MIC", "SYS"):
        w = SilenceWatch(threshold_s=300.0, arm_s=30.0)

        def levels(t, loud=loud):
            # One loud frame every 120 s on the channel under test, silence otherwise.
            base = {"MIC": -70.0, "SYS": -65.0}
            if t and t % 120 == 0:
                base[loud] = -20.0
            return base

        assert _run(w, 900, levels) == [], f"a talking {loud} channel still tripped the watch"
    # Exactly AT the floor is silence (the test is strictly above), one dB over is not.
    w = SilenceWatch(threshold_s=10.0, arm_s=0.0)
    assert _run(w, 30, lambda t: {"MIC": -50.0}) == [10.0], "at-the-floor must count as silence"
    w2 = SilenceWatch(threshold_s=10.0, arm_s=0.0)
    assert _run(w2, 30, lambda t: {"MIC": -49.9}) == [], "just above the floor must not be silence"
    print("  OK  either channel above the floor resets the clock; the floor itself is silence")


def test_missing_frames_count_as_silence_once_armed():
    # The dead-device case: the ring exists but has no frames in the window (None), which is
    # exactly what a mic Windows moved away looks like. Silent once armed, ignored before.
    w = SilenceWatch(threshold_s=300.0, arm_s=30.0)
    trips = _run(w, 700, lambda t: {"MIC": None, "SYS": None})
    assert trips == [330.0], f"a dead capture must trip like silence, got {trips}"
    # A mic-only session (only one ring exists) trips on that channel alone.
    w2 = SilenceWatch(threshold_s=300.0, arm_s=30.0)
    assert _run(w2, 700, lambda t: {"MIC": -70.0}) == [330.0], "a mic-only session did not trip"
    # No measurable channel at all is absence of evidence, not silence: never arms, never trips.
    w3 = SilenceWatch(threshold_s=1.0, arm_s=0.0)
    assert _run(w3, 100, lambda t: {}) == [] and w3.state()["armed"] is False, \
        "an empty level dict must not be treated as silence"
    print("  OK  missing frames count as silence once armed; no channels at all never trips")


def test_snooze_allows_exactly_one_more():
    # "Keep recording" restarts the clock, so a still-silent session is warned once more and
    # then never again: max_nudges is the session's hard budget.
    w = SilenceWatch(threshold_s=100.0, arm_s=0.0, max_nudges=2)
    assert _run(w, 101, _silent) == [100.0]
    w.snooze()                     # no clock argument: restarts from the last sample (t=100)
    trips = _run(w, 400, _silent, start=101.0)
    assert trips == [200.0], f"snooze should allow one more nudge at +threshold, got {trips}"
    w.snooze()
    assert _run(w, 400, _silent, start=401.0) == [], "the max_nudges cap was not enforced"
    st = w.state()
    assert st["nudges"] == 2 and st["exhausted"] is True, st
    print("  OK  snooze restarts the clock and allows exactly one more; the cap then holds")


def test_mute_is_permanent_for_the_session():
    w = SilenceWatch(threshold_s=50.0, arm_s=0.0, max_nudges=5)
    assert _run(w, 51, _silent) == [50.0]
    w.mute()
    assert _run(w, 2000, _silent, start=51.0) == [], "mute did not stop further nudges"
    assert w.state()["muted"] is True
    print("  OK  mute silences the watch for the rest of the session")


def test_state_and_minutes_shape():
    w = SilenceWatch(threshold_s=180.0, arm_s=0.0)
    _run(w, 60, _silent)
    st = w.state()
    assert st["minutes"] == 3 and st["threshold_s"] == 180.0, st
    assert 58.0 <= st["silent_s"] <= 60.0, st
    # A sub-minute threshold (a test, or a hand-edited file) still has to read as a minute.
    assert silencewatch.minutes_of(5.0) == 1 and silencewatch.minutes_of(900.0) == 15
    print("  OK  state() reports armed/silent_s/nudges/minutes; minutes_of never reads zero")


# --- 2. _silence_tick ------------------------------------------------------

def test_tick_reads_the_rings_on_the_session_clock():
    # The rings are timestamped on the SESSION clock (monotonic - capture._t0), so the tick
    # must query [now - lookback, now] on that same clock. A wall-clock "now" would query a
    # window the rings never had frames in, and every tick would read as silence.
    mic, sysr = FakeRing([-70.0]), FakeRing([-65.0])
    w = SilenceWatch(threshold_s=10.0, arm_s=0.0)
    assert webapp._silence_tick(FakeEngine(mic, sysr), w, 42.0) is None
    assert mic.windows == [(42.0 - webapp.SILENCE_LOOKBACK_S, 42.0)], mic.windows
    assert sysr.windows == [(42.0 - webapp.SILENCE_LOOKBACK_S, 42.0)], sysr.windows
    assert webapp.SILENCE_LOOKBACK_S > webapp.SILENCE_TICK_S, \
        "the ring window must be wider than the tick, or a late block reads as silence"
    # Nothing to read at all (no engine, no rings, a ring that raises) is never a trip.
    assert webapp._silence_tick(None, w, 43.0) is None
    assert webapp._silence_tick(FakeEngine(), w, 44.0) is None
    assert webapp._silence_tick(FakeEngine(BoomRing()), w, 45.0) is None
    print("  OK  _silence_tick queries both rings on the session clock and tolerates junk")


def test_tick_publishes_one_nudge_and_one_toast():
    saved = _save_state()
    calls, restore_notify = _catch_toasts()
    try:
        webapp.STATE.stopping = False
        webapp.STATE.silence_nudge = None
        eng = FakeEngine(FakeRing([-70.0]), FakeRing([-70.0]))
        w = SilenceWatch(threshold_s=5.0, arm_s=0.0)
        trips = [webapp._silence_tick(eng, w, float(t)) for t in range(0, 20)]
        fired = [t for t in trips if t is not None]
        assert len(fired) == 1, f"expected exactly one published nudge, got {len(fired)}"
        assert fired[0]["minutes"] == 1 and fired[0]["count"] == 1 and fired[0]["at"], fired[0]
        assert webapp.STATE.silence_nudge == fired[0], webapp.STATE.silence_nudge
        assert len(calls) == 1, f"expected one toast, got {len(calls)}"
        assert calls[0]["tag"] == "silence", calls[0]
        assert "Nothing heard for 1 minutes" == calls[0]["title"], calls[0]
        assert callable(calls[0]["on_click"]), "the toast must be clickable back to the app"
    finally:
        restore_notify()
        _restore_state(saved)
    print("  OK  a trip publishes STATE.silence_nudge once and sends one 'silence'-tagged toast")


def test_tick_never_fires_while_stopping():
    saved = _save_state()
    calls, restore_notify = _catch_toasts()
    try:
        webapp.STATE.stopping = True
        webapp.STATE.silence_nudge = None
        eng = FakeEngine(FakeRing([-70.0]), FakeRing([-70.0]))
        w = SilenceWatch(threshold_s=5.0, arm_s=0.0)
        for t in range(0, 20):
            assert webapp._silence_tick(eng, w, float(t)) is None, "nudged a stopping session"
        assert webapp.STATE.silence_nudge is None and calls == [], (webapp.STATE.silence_nudge, calls)
    finally:
        restore_notify()
        _restore_state(saved)
    print("  OK  a session that is finishing is never nudged (no banner, no toast)")


# --- 3. the loop, the endpoints and the gating ----------------------------

def test_loop_samples_on_the_session_clock_and_exits_on_signal():
    saved = _save_state()
    saved_tick = webapp.SILENCE_TICK_S
    try:
        webapp.SILENCE_TICK_S = 0.02          # keep the test to a few milliseconds
        watch = RecordingWatch()
        webapp.STATE.running = True
        webapp.STATE.stopping = False
        webapp.STATE.source_kind = "live"
        webapp.STATE.engine = FakeEngine(FakeRing([-70.0]))
        webapp.STATE.silence_watch = watch
        stop = threading.Event()
        t0 = time.monotonic() - 100.0         # a session that started 100 s ago
        th = threading.Thread(target=webapp._silence_loop, args=(stop, t0), daemon=True)
        th.start()
        deadline = time.time() + 3.0
        while not watch.nows and time.time() < deadline:
            time.sleep(0.01)
        assert watch.nows, "the watcher never sampled"
        assert 99.0 < watch.nows[0] < 103.0, \
            f"the loop must sample on the SESSION clock (~100 s), got {watch.nows[0]}"
        stop.set()
        th.join(2.0)
        assert not th.is_alive(), "the watcher did not exit when signalled"
        # It also gives up on its own the moment the session is no longer a running live one.
        watch2 = RecordingWatch()
        webapp.STATE.silence_watch = watch2
        webapp.STATE.running = False
        stop2 = threading.Event()
        th2 = threading.Thread(target=webapp._silence_loop, args=(stop2, t0), daemon=True)
        th2.start()
        th2.join(2.0)
        assert not th2.is_alive() and watch2.nows == [], "the watcher ran on a stopped session"
    finally:
        webapp.SILENCE_TICK_S = saved_tick
        _restore_state(saved)
    print("  OK  _silence_loop samples on the session clock, exits on signal and on a dead session")


def test_settings_and_env_gating():
    saved_load = config.load
    saved_env = os.environ.get(webapp.SILENCE_ENV)
    try:
        os.environ.pop(webapp.SILENCE_ENV, None)
        config.load = lambda: {"silence_nudge": True, "silence_nudge_minutes": 10}
        assert webapp._silence_settings() == (True, 600.0)
        config.load = lambda: {"silence_nudge": False, "silence_nudge_minutes": 5}
        assert webapp._silence_settings()[0] is False, "the setting does not switch it off"
        # A missing key defaults ON with the 5 minute default; junk and out-of-range clamp.
        config.load = lambda: {}
        assert webapp._silence_settings() == (True, 300.0)
        config.load = lambda: {"silence_nudge_minutes": "nonsense"}
        assert webapp._silence_settings() == (True, 300.0)
        # 0 is not a threshold anyone means, so it falls back to the default rather than to
        # the 1 minute clamp; a big number clamps to the 120 minute ceiling.
        config.load = lambda: {"silence_nudge_minutes": 0}
        assert webapp._silence_settings() == (True, 300.0), webapp._silence_settings()
        config.load = lambda: {"silence_nudge_minutes": 9999}
        assert webapp._silence_settings() == (True, 7200.0), webapp._silence_settings()
        # The env kill switch wins over the setting, and short-circuits before any read.
        config.load = lambda: {"silence_nudge": True}
        for val in ("0", "false", "no", "off"):
            os.environ[webapp.SILENCE_ENV] = val
            assert webapp._silence_settings() == (False, 0.0), f"{val} did not kill the feature"
        os.environ[webapp.SILENCE_ENV] = "1"
        assert webapp._silence_settings()[0] is True
    finally:
        config.load = saved_load
        if saved_env is None:
            os.environ.pop(webapp.SILENCE_ENV, None)
        else:
            os.environ[webapp.SILENCE_ENV] = saved_env
    print("  OK  gating: setting off, env kill switch, sane defaults and clamped minutes")


def test_no_watcher_when_off_or_for_file_transcription():
    saved = _save_state()
    saved_load = config.load
    saved_env = os.environ.get(webapp.SILENCE_ENV)
    try:
        os.environ.pop(webapp.SILENCE_ENV, None)
        # Setting off: no thread, no watch on STATE.
        config.load = lambda: {"silence_nudge": False}
        webapp.STATE.silence_watch = None
        assert webapp._silence_start(FakeCapture(t0=time.monotonic())) is None
        assert webapp.STATE.silence_watch is None, "a switched-off watcher still armed"
        # Env off: same.
        config.load = lambda: {"silence_nudge": True}
        os.environ[webapp.SILENCE_ENV] = "0"
        assert webapp._silence_start(FakeCapture(t0=time.monotonic())) is None
        assert webapp.STATE.silence_watch is None
        os.environ.pop(webapp.SILENCE_ENV, None)
        # No session clock (no capture._t0): nothing to query honestly, so no watcher.
        assert webapp._silence_start(FakeCapture(t0=None)) is None
        assert webapp.STATE.silence_watch is None
        # On: a daemon thread called "silence-watch" plus a watch on STATE, and _silence_signal
        # takes it down again (which is what /api/stop calls).
        webapp.STATE.running = True
        webapp.STATE.stopping = False
        webapp.STATE.source_kind = "live"
        th = webapp._silence_start(FakeCapture(t0=time.monotonic()))
        assert th is not None and th.name == "silence-watch" and th.daemon, th
        assert isinstance(webapp.STATE.silence_watch, SilenceWatch)
        assert webapp.STATE.silence_stop is not None
        webapp._silence_signal()
        th.join(2.5)
        assert not th.is_alive(), "the watcher survived _silence_signal"
        assert webapp.STATE.silence_watch is None and webapp.STATE.silence_stop is None
        # File transcription must never arm one: the wiring lives in start() and nowhere else.
        assert "_silence_start" not in inspect.getsource(webapp.transcribe_file), \
            "file transcription starts a silence watcher; a quiet file is just a quiet file"
        assert "_silence_start" in inspect.getsource(webapp.start), \
            "the live start path no longer arms the silence watcher"
        # reset() is the backstop every finalisation path goes through.
        src = inspect.getsource(webapp._State.reset)
        assert "silence_stop" in src and "silence_watch" in src and "silence_nudge" in src, src
    finally:
        config.load = saved_load
        if saved_env is None:
            os.environ.pop(webapp.SILENCE_ENV, None)
        else:
            os.environ[webapp.SILENCE_ENV] = saved_env
        _restore_state(saved)
    print("  OK  no watcher when switched off, killed by env, clockless, or transcribing a file")


def test_endpoint_requires_a_live_session_and_the_csrf_token():
    # Idle: 409, not a crash. Junk action: 422. No token: 403 (state-changing).
    assert webapp.STATE.running is False, "another test left a session running"
    r = client.post("/api/silence-nudge", json={"action": "snooze"})
    assert r.status_code == 409, (r.status_code, r.text)
    assert client.post("/api/silence-nudge", json={"action": "nope"}).status_code == 422
    bare = TestClient(app, base_url="http://localhost")
    assert bare.post("/api/silence-nudge", json={"action": "mute"}).status_code == 403
    print("  OK  /api/silence-nudge: 409 with no session, 422 on junk, CSRF-protected")


def test_endpoint_snoozes_and_mutes_and_status_carries_the_nudge():
    saved = _save_state()
    try:
        watch = SilenceWatch(threshold_s=100.0, arm_s=0.0)
        assert _run(watch, 101, _silent) == [100.0]
        webapp.STATE.running = True
        webapp.STATE.stopping = False
        webapp.STATE.source_kind = "live"
        webapp.STATE.engine = None
        webapp.STATE.capture = None
        webapp.STATE.silence_watch = watch
        webapp.STATE.silence_nudge = {"minutes": 5, "count": 1, "at": "2026-07-30T09:00:00"}
        # /api/status hands the outstanding nudge to the UI, which floats the banner.
        st = client.get("/api/status").json()
        assert st["running"] is True and st["silence_nudge"] == webapp.STATE.silence_nudge, st
        # Keep recording: the nudge clears, the watch is no longer outstanding, and the clock
        # restarts, so exactly one more warning is possible.
        r = client.post("/api/silence-nudge", json={"action": "snooze"})
        assert r.status_code == 200, (r.status_code, r.text)
        assert r.json()["silence_nudge"] is None and r.json()["muted"] is False, r.json()
        assert webapp.STATE.silence_nudge is None
        assert watch.state()["outstanding"] is False and watch.state()["nudges"] == 1
        assert _run(watch, 400, _silent, start=101.0) == [200.0], "snooze did not restart the clock"
        # The X mutes for the session.
        webapp.STATE.silence_nudge = {"minutes": 5, "count": 2, "at": "2026-07-30T09:10:00"}
        r2 = client.post("/api/silence-nudge", json={"action": "mute"})
        assert r2.status_code == 200 and r2.json()["muted"] is True, r2.text
        assert webapp.STATE.silence_nudge is None and watch.state()["muted"] is True
        assert _run(watch, 2000, _silent, start=401.0) == [], "mute did not stick"
        # A live device switch clears an outstanding nudge and restarts the clock: swapping the
        # mic IS the fix the nudge asks for, so the banner must not sit there contradicting it.
        watch2 = SilenceWatch(threshold_s=100.0, arm_s=0.0)
        assert _run(watch2, 101, _silent) == [100.0]
        webapp.STATE.silence_watch = watch2
        webapp.STATE.silence_nudge = {"minutes": 5, "count": 1, "at": "x"}
        webapp._silence_after_switch()
        assert webapp.STATE.silence_nudge is None and watch2.state()["outstanding"] is False
        assert _run(watch2, 400, _silent, start=101.0) == [200.0], \
            "a device switch must restart the silence clock, not resume mid-run"
    finally:
        _restore_state(saved)
    print("  OK  snooze/mute answer the banner, /api/status carries it, a device switch clears it")


def test_settings_keys_exist_and_are_patchable():
    from live_transcribe.web.app import SettingsPatch
    assert config.DEFAULTS["silence_nudge"] is True
    assert config.DEFAULTS["silence_nudge_minutes"] == 5
    fields = SettingsPatch.model_fields
    assert "silence_nudge" in fields and "silence_nudge_minutes" in fields, sorted(fields)
    # public_view() is DEFAULTS-driven, so the UI sees both keys without further plumbing.
    view = config.public_view()
    assert "silence_nudge" in view and "silence_nudge_minutes" in view, sorted(view)
    print("  OK  silence_nudge + silence_nudge_minutes are settings, patchable and published")


if __name__ == "__main__":
    tests = (test_trips_once_after_the_threshold,
             test_arming_delay_does_not_count_as_silence,
             test_either_channel_above_the_floor_resets,
             test_missing_frames_count_as_silence_once_armed,
             test_snooze_allows_exactly_one_more,
             test_mute_is_permanent_for_the_session,
             test_state_and_minutes_shape,
             test_tick_reads_the_rings_on_the_session_clock,
             test_tick_publishes_one_nudge_and_one_toast,
             test_tick_never_fires_while_stopping,
             test_loop_samples_on_the_session_clock_and_exits_on_signal,
             test_settings_and_env_gating,
             test_no_watcher_when_off_or_for_file_transcription,
             test_endpoint_requires_a_live_session_and_the_csrf_token,
             test_endpoint_snoozes_and_mutes_and_status_carries_the_nudge,
             test_settings_keys_exist_and_are_patchable)
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    # No watcher may outlive the tests: a leaked daemon would mean the exit paths are wrong.
    leaked = [t.name for t in threading.enumerate() if t.name == "silence-watch"]
    if leaked:
        failures += 1
        print(f"  FAIL  a silence watcher outlived its test: {leaked}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll silence-nudge tests passed.")
