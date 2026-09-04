"""WP-7: the mic gate as a LIVE control, with a counter and a quiet-mic safety valve.

WP-3 built the gate; tests/test_mic_gate.py pins what it decides and is untouched by this file.
This one pins what the user can see and change about it:

  1. the runtime flag - set_mic_gate takes effect on the NEXT chunk, no engine restart, and the
     env var is only the starting value;
  2. the counters - every ring-fed MIC decision is tallied, SYS is never counted;
  3. the quiet-mic safety valve - normal -> gentle -> off on the "skipped WITH sustained activity
     just under the line" signature, and NOT on a dead-quiet room, which is the whole reason the
     signature is a pair rather than a skip count;
  4. the seams the UI renders from - Engine.mic_gate_state(), /api/status's mic_gate field and
     POST /api/mic-gate, plus the mic_gate setting's default and round-trip.

Synthetic rings and hand-set STATE only: no model load, no audio device, no recording.

Run:  python tests/test_mic_gate_ui.py   (from the project root; exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from fastapi.testclient import TestClient

from live_transcribe import config
from live_transcribe import transcribe as T
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})

SR = 16000
CHUNK_S = 15.0
STEP = 0.1
FRAMES = int(CHUNK_S / STEP)          # 150 ring frames per chunk
ROOM_DB = -52.0                       # a room loud enough that the ceiling caps the threshold,
                                      # which is the only condition the valve exists for
SPEECH_DB = -25.0
HISTORY_S = 60.0


def audio(db=-20.0, secs=CHUNK_S):
    """A chunk that looks like speech to a sample-level test, so a verdict can only come from
    the ring. Same helper shape as tests/test_mic_gate.py, deliberately."""
    a = np.random.RandomState(0).randn(int(secs * SR)).astype(np.float32)
    return a * ((10.0 ** (db / 20.0)) / (float(np.sqrt(np.mean(a * a))) + 1e-9))


class _GateEngine:
    """The gate plus the WP-7 surface, borrowed unbound (Engine.__init__ loads a model)."""
    _ring_for = T.Engine._ring_for
    _chunk_is_silence = T.Engine._chunk_is_silence
    mic_gate_state = T.Engine.mic_gate_state
    set_mic_gate = T.Engine.set_mic_gate

    engine = None                 # the family-override field /api/status reads off STATE.engine

    def __init__(self, mic_ring=None, sys_ring=None, on=True):
        self.mic_env = mic_ring
        self.sys_env = sys_ring
        self._raw_mic_ring = True
        self._mic_speech_gate = on
        self._mic_gate_debug = False
        self._mic_gate_level = "normal"
        self._mic_gate_recent = T.deque(maxlen=T.MIC_GATE_WINDOW)
        self.mic_gate_skipped = 0
        self.mic_gate_decoded = 0
        self.mic_gate_hint = None
        self.mic_gate_hint_seq = 0


class Feeder:
    """A ring plus a clock, so a test can push chunk after chunk through the real gate the way a
    live session does: room tone first (the baseline arm 3 needs), then one window per call."""

    def __init__(self, room_db=ROOM_DB, history_s=HISTORY_S):
        self.ring = T.EnergyRing(retain_s=1e6, raw=True)
        self.t = 0.0
        while self.t < history_s - 1e-9:
            self.ring.add(round(self.t, 3), room_db)
            self.t += STEP
        self.room_db = room_db
        self.eng = _GateEngine(mic_ring=self.ring)

    def chunk(self, level_db, n_loud=60, source="MIC"):
        """One 15 s chunk: `n_loud` frames at level_db, the rest at room tone. Returns the gate's
        verdict (True = skipped).

        60 of 150 frames by default, i.e. 6 s of talking in a 15 s chunk, because that is what a
        person sounds like AND because a chunk that is loud end to end poisons its own p10 room
        baseline within two chunks (noise_floor_before looks back 120 s), which would make every
        level test here measure something other than the gate."""
        t0 = self.t
        for i in range(FRAMES):
            self.ring.add(round(self.t, 3), level_db if i < n_loud else self.room_db)
            self.t += STEP
        return self.eng._chunk_is_silence(source, audio(), t0)


# The threshold in a ROOM_DB room is min(-52 + 20, -35) = -35 dBFS: capped, so it cannot ride
# down to meet a quiet talker. -42 dBFS sits 7 dB under it, inside the 12 dB near band, and is
# what a quiet mic in this room looks like. -70 is the dead-quiet room: nowhere near the band.
JUST_UNDER_DB = -42.0
DEAD_QUIET_DB = -70.0


# --- 1. the runtime flag ----------------------------------------------------------------------

def test_flag_flips_between_chunks_without_a_restart():
    """The locked contract: a live toggle is effective on the NEXT chunk. Same engine, same ring,
    same room - only the flag changes, and the verdict changes with it."""
    f = Feeder()
    assert f.chunk(JUST_UNDER_DB) is True, "precondition: this chunk is gated while the gate is on"
    f.eng.set_mic_gate(False)
    assert f.chunk(JUST_UNDER_DB) is False, "the very next chunk must be decoded once the gate is off"
    f.eng.set_mic_gate(True)
    assert f.chunk(JUST_UNDER_DB) is True, "and gated again the chunk after it is switched back on"
    print("  OK  the mic gate flag takes effect on the next chunk, no restart")


def test_set_mic_gate_reports_the_confirmed_state():
    f = Feeder()
    st = f.eng.set_mic_gate(False)
    assert st["on"] is False and st["mode"] == "off", st
    st = f.eng.set_mic_gate(True)
    assert st["on"] is True and st["mode"] == "normal", st
    print("  OK  set_mic_gate returns the engine's confirmed state")


def test_env_var_is_only_the_starting_value():
    """SA_LIVE_MIC_SPEECH_GATE seeds _mic_speech_gate; it must not be re-read per chunk, or a
    live toggle could not work at all."""
    assert "SA_LIVE_MIC_SPEECH_GATE" in T.Engine.__init__.__code__.co_consts, \
        "the env var is still read in Engine.__init__ (the starting value)"
    assert "environ" not in T.Engine._chunk_is_silence.__code__.co_names, \
        "the per-chunk path must read the attribute, never the environment"
    print("  OK  the env var seeds the flag; the chunk path reads the attribute")


# --- 2. the counters --------------------------------------------------------------------------

def test_counters_count_skipped_and_decoded():
    f = Feeder()
    f.chunk(SPEECH_DB)          # decoded
    f.chunk(JUST_UNDER_DB)      # skipped
    f.chunk(SPEECH_DB)          # decoded
    st = f.eng.mic_gate_state()
    assert st["decoded"] == 2 and st["skipped"] == 1, st
    print("  OK  the counters tally decoded and skipped MIC chunks")


def test_counters_ignore_the_far_end():
    """SYS is never gated and must never be counted either: the counter is labelled as the mic's."""
    f = Feeder()
    f.eng.sys_env = f.ring
    assert f.chunk(JUST_UNDER_DB, source="SYS") is False, "SYS must never be gated"
    st = f.eng.mic_gate_state()
    assert st["skipped"] == 0 and st["decoded"] == 0, st
    print("  OK  SYS chunks are neither gated nor counted")


def test_counters_keep_counting_while_the_gate_is_off():
    """With arm 3 off the two peak arms still skip genuinely dead audio, and that is still a
    quiet chunk skipped. The counter reports what happened, not what the toggle says."""
    f = Feeder()
    f.eng.set_mic_gate(False)
    f.chunk(DEAD_QUIET_DB)
    st = f.eng.mic_gate_state()
    assert st["mode"] == "off" and st["skipped"] == 1, st
    print("  OK  the counters stay honest with the gate switched off")


# --- 3. the quiet-mic safety valve ------------------------------------------------------------

def test_valve_steps_to_gentle_on_a_quiet_mic():
    """Six of eight chunks skipped WITH sustained activity just under the threshold: a quiet mic,
    not an empty room. The gate drops to gentle and latches one hint."""
    f = Feeder()
    for _ in range(T.MIC_GATE_WINDOW):
        f.chunk(JUST_UNDER_DB)
    st = f.eng.mic_gate_state()
    assert st["mode"] == "gentle" and st["on"] is True, st
    assert st["hint"] == "gentle" and st["hint_seq"] == 1, st
    print(f"  OK  the valve stepped to gentle after {T.MIC_GATE_WINDOW} quiet-mic chunks")


def test_gentle_mode_then_decodes_the_quiet_talker():
    """The point of the step. In a -52 dBFS room the normal threshold is capped at -35 dBFS, so a
    talker at -38 is cut off; gentle's 12 dB margin puts the threshold at -40 and the same talker
    is heard. Normal gates it, eight of those trip the valve, gentle decodes it."""
    f = Feeder()
    assert f.chunk(-38.0) is True, "precondition: normal mode gates a talker at -38 dBFS here"
    for _ in range(T.MIC_GATE_WINDOW - 1):
        f.chunk(-38.0)
    assert f.eng.mic_gate_state()["mode"] == "gentle", f.eng.mic_gate_state()
    assert f.chunk(-38.0) is False, "gentle mode must decode the talker it was stepped down for"
    print("  OK  gentle mode decodes the quiet talker normal mode was gating")


def test_valve_does_not_fire_on_a_dead_quiet_room():
    """The pair test, and the reason a bare skip count would be wrong. An empty room skips every
    chunk too, but its frames sit at the floor, nowhere near the band under the threshold. The
    gate must stay in normal mode: there is nobody to stop cutting off.

    n_loud=0 is what makes it an empty room: every frame IS the room tone. (It used to pass
    DEAD_QUIET_DB for 60 of the 150 frames, which put those frames 18 dB BELOW the established
    room, dragged the p10 baseline down with them and left the room's own tone looking like
    sustained activity under the bar. A room cannot be quieter than its own noise floor.)"""
    f = Feeder()
    for _ in range(T.MIC_GATE_WINDOW * 3):
        f.chunk(DEAD_QUIET_DB, n_loud=0)
    st = f.eng.mic_gate_state()
    assert st["skipped"] == T.MIC_GATE_WINDOW * 3, st
    assert st["mode"] == "normal" and st["hint"] is None, st
    print("  OK  a dead-quiet room skips everything and never trips the valve")


def test_valve_does_not_fire_on_a_talker_who_is_being_heard():
    f = Feeder()
    for _ in range(T.MIC_GATE_WINDOW * 2):
        f.chunk(SPEECH_DB)
    assert f.eng.mic_gate_state()["mode"] == "normal", f.eng.mic_gate_state()
    print("  OK  a mic being decoded never trips the valve")


def test_valve_needs_the_trip_count_not_just_any_skips():
    """Five near-miss skips in eight is under MIC_GATE_TRIP: the odd quiet stretch in a normal
    meeting must not stand the gate down."""
    f = Feeder()
    for i in range(T.MIC_GATE_WINDOW):
        f.chunk(JUST_UNDER_DB if i < T.MIC_GATE_TRIP - 1 else SPEECH_DB)
    assert f.eng.mic_gate_state()["mode"] == "normal", f.eng.mic_gate_state()
    print(f"  OK  {T.MIC_GATE_TRIP - 1} of {T.MIC_GATE_WINDOW} near-miss skips is not enough")


def test_valve_escalates_to_off_when_gentle_is_still_cutting_someone_off():
    """A very quiet mic: gentle lowers the bar but the same signature returns against the new
    threshold, so the arm stands itself down entirely for the session."""
    f = Feeder()
    for _ in range(T.MIC_GATE_WINDOW):
        f.chunk(JUST_UNDER_DB)
    assert f.eng.mic_gate_state()["mode"] == "gentle"
    # -42 dBFS is under gentle's own -40 dBFS threshold and inside its 12 dB band too, so the
    # signature simply returns against the lower bar: exactly the "still cutting me off" case.
    for _ in range(T.MIC_GATE_WINDOW):
        f.chunk(JUST_UNDER_DB)
    st = f.eng.mic_gate_state()
    assert st["on"] is False and st["mode"] == "off", st
    assert st["hint"] == "off" and st["hint_seq"] == 2, st
    # and it is genuinely inert now: the next quiet chunk is decoded
    assert f.chunk(JUST_UNDER_DB) is False, "an off gate must decode"
    print("  OK  gentle escalates to off, and off decodes")


def test_valve_never_escalates_back_to_normal():
    """Locked: the valve is one-way. A quiet spell then a loud stretch leaves it in gentle."""
    f = Feeder()
    for _ in range(T.MIC_GATE_WINDOW):
        f.chunk(JUST_UNDER_DB)
    assert f.eng.mic_gate_state()["mode"] == "gentle"
    for _ in range(T.MIC_GATE_WINDOW * 3):
        f.chunk(SPEECH_DB)
    assert f.eng.mic_gate_state()["mode"] == "gentle", "the valve must never re-arm itself"
    # a manual off/on does not undo the valve's finding either
    f.eng.set_mic_gate(False)
    f.eng.set_mic_gate(True)
    assert f.eng.mic_gate_state()["mode"] == "gentle", "an off/on flick must not restore normal"
    print("  OK  the valve is one-way, and a manual toggle does not undo it")


def test_valve_ignores_the_far_end():
    """SYS chunks are not arm-3 decisions, so they can never step the mic's gate down."""
    f = Feeder()
    f.eng.sys_env = f.ring
    for _ in range(T.MIC_GATE_WINDOW * 2):
        f.chunk(JUST_UNDER_DB, source="SYS")
    assert f.eng.mic_gate_state()["mode"] == "normal", f.eng.mic_gate_state()
    print("  OK  far-end chunks never trip the mic's safety valve")


def test_near_miss_stats_are_numbers_only():
    """The valve's measurement is levels and counts: no audio, no text, nothing to leak."""
    f = Feeder()
    f.chunk(JUST_UNDER_DB)
    stats = {}
    T.mic_speech_evidence(f.ring, HISTORY_S, HISTORY_S + CHUNK_S, stats=stats)
    assert set(stats) == {"floor", "thr", "n_evid", "n_near", "need", "frames", "near"}, stats
    assert all(isinstance(v, (int, float, bool)) for v in stats.values()), stats
    print("  OK  the valve's per-chunk measurement is numbers only")


# --- 4. the seams the UI renders from ---------------------------------------------------------

def test_mic_gate_state_shape():
    f = Feeder()
    st = f.eng.mic_gate_state()
    assert set(st) == {"on", "mode", "skipped", "decoded", "hint", "hint_seq"}, st
    assert st == {"on": True, "mode": "normal", "skipped": 0, "decoded": 0,
                  "hint": None, "hint_seq": 0}, st
    print("  OK  mic_gate_state has the shape the UI renders")


_STATE_FIELDS = ("running", "stopping", "source_kind", "engine", "preparing_engine")


def _live_session(eng):
    """Hand-set a running live session around `eng`. Returns the saved state to restore."""
    saved = {k: getattr(webapp.STATE, k) for k in _STATE_FIELDS}
    webapp.STATE.running = True
    webapp.STATE.stopping = False
    webapp.STATE.source_kind = "live"
    webapp.STATE.engine = eng
    webapp.STATE.preparing_engine = None
    return saved


def _restore(saved):
    for k, v in saved.items():
        setattr(webapp.STATE, k, v)


def test_status_reports_the_mic_gate():
    f = Feeder()
    f.chunk(SPEECH_DB)
    f.chunk(JUST_UNDER_DB)
    saved = _live_session(f.eng)
    try:
        j = client.get("/api/status").json()
        g = j.get("mic_gate")
        assert g is not None, j
        assert g["on"] is True and g["mode"] == "normal", g
        assert g["decoded"] == 1 and g["skipped"] == 1, g
    finally:
        _restore(saved)
    print("  OK  /api/status carries the mic gate's live state and counters")


def test_status_omits_the_mic_gate_with_no_engine():
    saved = _live_session(None)
    try:
        assert client.get("/api/status").json().get("mic_gate") is None
    finally:
        _restore(saved)
    print("  OK  /api/status omits the mic gate when there is no engine")


def test_status_uses_the_preparing_engine_during_catch_up():
    """STATE.engine is only published after the backlog drains; the toggle must work from the
    moment transcription is live, so the endpoint and /api/status both fall back to it."""
    f = Feeder()
    saved = _live_session(None)
    try:
        webapp.STATE.preparing_engine = f.eng
        assert client.get("/api/status").json().get("mic_gate") is not None
    finally:
        _restore(saved)
    print("  OK  the status field falls back to the still-catching-up engine")


def test_mic_gate_endpoint_toggles_and_persists():
    f = Feeder()
    saved = _live_session(f.eng)
    orig = config.load().get("mic_gate", True)
    try:
        r = client.post("/api/mic-gate", json={"enabled": False})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["on"] is False and j["mode"] == "off" and j["persisted"] is True, j
        assert f.eng._mic_speech_gate is False, "the engine flag did not move"
        assert config.load().get("mic_gate") is False, "the choice was not persisted"
        # and the very next chunk is decoded: live, no restart
        assert f.chunk(JUST_UNDER_DB) is False
        j = client.post("/api/mic-gate", json={"enabled": True}).json()
        assert j["on"] is True and j["mode"] == "normal", j
        assert f.chunk(JUST_UNDER_DB) is True
    finally:
        _restore(saved)
        config.update({"mic_gate": orig})
    print("  OK  POST /api/mic-gate flips the engine live and persists the default")


def test_mic_gate_endpoint_409s_without_a_session():
    saved = {k: getattr(webapp.STATE, k) for k in _STATE_FIELDS}
    webapp.STATE.running = False
    webapp.STATE.engine = None
    webapp.STATE.preparing_engine = None
    try:
        assert client.post("/api/mic-gate", json={"enabled": False}).status_code == 409
    finally:
        _restore(saved)
    print("  OK  the endpoint 409s when nothing is being transcribed")


def test_mic_gate_setting_default_and_roundtrip():
    assert config.DEFAULTS.get("mic_gate") is True, "mic_gate must default ON"
    orig = config.load().get("mic_gate", True)
    try:
        config.update({"mic_gate": False})
        assert config.load().get("mic_gate") is False, "OFF did not persist"
        j = client.post("/api/settings", json={"mic_gate": True}).json()
        assert j.get("mic_gate") is True, j
        assert config.load().get("mic_gate") is True, "endpoint write did not persist"
    finally:
        config.update({"mic_gate": orig})
    print("  OK  mic_gate: default ON, config + /api/settings round-trip")


def test_saved_setting_is_applied_to_a_fresh_engine():
    """_apply_mic_gate_setting is what makes the settings row mean anything, and the env var
    still wins over it (the support kill switch)."""
    f = Feeder()
    orig = config.load().get("mic_gate", True)
    env = os.environ.pop("SA_LIVE_MIC_SPEECH_GATE", None)
    try:
        config.update({"mic_gate": False})
        webapp._apply_mic_gate_setting(f.eng)
        assert f.eng._mic_speech_gate is False, "the saved setting was not applied"
        os.environ["SA_LIVE_MIC_SPEECH_GATE"] = "1"
        f.eng.set_mic_gate(True)
        webapp._apply_mic_gate_setting(f.eng)
        assert f.eng._mic_speech_gate is True, "the env var must win over the stored setting"
    finally:
        os.environ.pop("SA_LIVE_MIC_SPEECH_GATE", None)
        if env is not None:
            os.environ["SA_LIVE_MIC_SPEECH_GATE"] = env
        config.update({"mic_gate": orig})
    print("  OK  a fresh engine starts from the saved setting; the env var overrides it")


# --- what none of this may touch --------------------------------------------------------------

def test_the_toggle_is_a_decode_decision_only():
    """Locked: neither value changes the recording. Pinned the way WP-3 pins it - by the shape of
    the code, since the recorder is fed from the capture callback ahead of the engine queue."""
    import inspect
    src = inspect.getsource(T.Engine.set_mic_gate) + inspect.getsource(T._mic_gate_valve)
    for word in ("record", "wav", "sink", "write"):
        assert word not in src.lower().replace("recorder is fed", ""), \
            f"the live toggle must not reach anywhere near the recording ({word})"
    print("  OK  the live toggle touches decoding only, never the recording")


if __name__ == "__main__":
    failures = 0
    for fn in (test_flag_flips_between_chunks_without_a_restart,
               test_set_mic_gate_reports_the_confirmed_state,
               test_env_var_is_only_the_starting_value,
               test_counters_count_skipped_and_decoded,
               test_counters_ignore_the_far_end,
               test_counters_keep_counting_while_the_gate_is_off,
               test_valve_steps_to_gentle_on_a_quiet_mic,
               test_gentle_mode_then_decodes_the_quiet_talker,
               test_valve_does_not_fire_on_a_dead_quiet_room,
               test_valve_does_not_fire_on_a_talker_who_is_being_heard,
               test_valve_needs_the_trip_count_not_just_any_skips,
               test_valve_escalates_to_off_when_gentle_is_still_cutting_someone_off,
               test_valve_never_escalates_back_to_normal,
               test_valve_ignores_the_far_end,
               test_near_miss_stats_are_numbers_only,
               test_mic_gate_state_shape,
               test_status_reports_the_mic_gate,
               test_status_omits_the_mic_gate_with_no_engine,
               test_status_uses_the_preparing_engine_during_catch_up,
               test_mic_gate_endpoint_toggles_and_persists,
               test_mic_gate_endpoint_409s_without_a_session,
               test_mic_gate_setting_default_and_roundtrip,
               test_saved_setting_is_applied_to_a_fresh_engine,
               test_the_toggle_is_a_decode_decision_only):
        try:
            fn()
        except Exception as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    sys.exit(1 if failures else 0)
