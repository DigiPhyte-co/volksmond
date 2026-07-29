"""Unit tests for the gain-invariant energy feed (WP-4): the raw MIC/SYS energy rings, their
window statistics, the ring-fed silence gate (incl. the dead-channel test) and the mic_ring arm
of the cross-channel echo veto.

The bug being pinned: live AGC boosts the mic the engine transcribes, so every level test that
read CHUNK samples (the silence gate, the veto's mic_p90) was comparing a gain-controlled signal
against a raw SYS reference. Feeding both sides from the raw pre-APM capture blocks restores the
calibrated basis of the -45 dBFS floor, the -28 dBFS ceiling and the 10 dB margin.

Companion to test_echo_veto.py (whose nine tests must stay green UNCHANGED - the mic_ring kwarg is
keyword-only and optional). Synthetic audio + hand-built rings, no model load, no audio device.

Run:  python tests/test_gain_invariant.py   (exit 0 = pass)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from live_transcribe import capture_core as C
from live_transcribe import transcribe as T

SR = 16000


def tone(db, secs, sr=SR):
    """White noise scaled to a target dBFS RMS (deterministic seed). Same helper as
    test_echo_veto.py, deliberately verbatim so the two suites share a calibration basis."""
    n = int(secs * sr)
    a = np.random.RandomState(0).randn(n).astype(np.float32)
    return a * ((10.0 ** (db / 20.0)) / (float(np.sqrt(np.mean(a * a))) + 1e-9))


def ring_with(db, t0, t1, step=0.1, raw=True):
    r = T.EnergyRing(retain_s=1e6, raw=raw)
    t = t0
    while t <= t1 + 1e-9:
        r.add(t, db)
        t += step
    return r


def block(db, secs, rate, channels=1):
    """A raw capture block shaped (frames, channels) at a target dBFS RMS."""
    n = int(secs * rate)
    a = np.random.RandomState(1).randn(n, channels).astype(np.float32)
    return a * ((10.0 ** (db / 20.0)) / (float(np.sqrt(np.mean(a * a))) + 1e-9))


class _Cap(C.CaptureBase):
    """Minimal CaptureBase: no devices, we drive _ingest_block by hand."""
    def _open_sources(self):
        pass

    def _close_sources(self):
        pass


def _fed_cap(rate, source="MIC", elapsed=100.0):
    c = _Cap()
    c._t0 = time.monotonic() - elapsed
    c._register_source(source, rate, 1)
    ring = T.EnergyRing(retain_s=1e6)
    (c.attach_mic_ring if source == "MIC" else c.attach_sys_ring)(ring)
    return c, ring


# --- ring feed: timestamps and sub-framing -------------------------------------------------

def test_ring_timestamp_is_block_start():
    # The old SYS feed timestamped a block at ARRIVAL, i.e. ~0.5 s AFTER the audio it describes,
    # while the file path built its ring positionally (block start). Live now matches file.
    c, ring = _fed_cap(48000)
    c._ingest_block("MIC", block(-20.0, 0.5, 48000))
    ts = [t for t, _ in zip(ring._t, ring._db)]
    assert ts, "the mic ring must be fed from _ingest_block"
    assert abs(ts[0] - 99.5) < 0.05, \
        f"first ring sample must be timestamped at block START (~99.5s), got {ts[0]:.3f}"
    assert ts[-1] < 100.0, "no sample may be timestamped at or after block arrival"


def test_ring_sub_frames_block_into_100ms():
    # 100 ms resolution is the whole point: a 1 s segment used to give 2 samples, which cannot
    # express a coverage fraction at all.
    c, ring = _fed_cap(48000)
    c._ingest_block("MIC", block(-20.0, 0.5, 48000))
    ts = list(ring._t)
    assert len(ts) == 5, f"a 0.5 s block must yield 5 ring samples, got {len(ts)}"
    diffs = [b - a for a, b in zip(ts, ts[1:])]
    assert all(abs(d - 0.1) < 1e-6 for d in diffs), f"samples must be 100 ms apart, got {diffs}"


def test_ring_sub_framing_uses_the_native_rate():
    # start() rewrites _rates["MIC"] to 16k once the APM engages (the chunk BUFFER is 16k then),
    # but the blocks arriving here are still native. Dividing by _rates would put the block start
    # 1.5 s back instead of 0.5 s and yield 15 samples instead of 5.
    c, ring = _fed_cap(48000)
    c._rates["MIC"] = C.TARGET_RATE          # simulate the post-APM flip in start()
    c._ingest_block("MIC", block(-20.0, 0.5, 48000))
    ts = list(ring._t)
    assert len(ts) == 5, f"sub-framing must use the NATIVE rate, got {len(ts)} samples"
    assert abs(ts[0] - 99.5) < 0.05, \
        f"block-start offset must use the NATIVE rate (~99.5s), got {ts[0]:.3f}"


def test_sys_ring_still_fed_and_mic_ring_is_separate():
    # attach_sys_ring keeps its name and its behaviour; the mic feed is additive.
    c = _Cap()
    c._t0 = time.monotonic() - 50.0
    c._register_source("SYS", 44100, 2)
    sring = T.EnergyRing(retain_s=1e6)
    c.attach_sys_ring(sring)
    c._ingest_block("SYS", block(-15.0, 0.5, 44100, channels=2))
    assert len(list(sring._t)) == 5, "the SYS ring must still be fed, at 100 ms resolution"
    assert c._mic_ring is None, "attaching a SYS ring must not touch the mic ring"


def test_ring_level_is_gain_independent_of_the_apm():
    # The ring reads the RAW block, so what the APM would do to the mic is invisible to it.
    c, ring = _fed_cap(16000)
    c._ingest_block("MIC", block(-40.0, 0.5, 16000))
    dbs = list(ring._db)
    assert all(abs(d - (-40.0)) < 0.5 for d in dbs), f"ring dB must track the raw block, got {dbs}"


# --- ring window statistics ----------------------------------------------------------------

def test_max_db_and_empty_window():
    r = T.EnergyRing(retain_s=1e6)
    for i, db in enumerate((-50.0, -47.0, -12.0, -44.0)):
        r.add(10.0 + i * 0.1, db)
    assert r.max_db(10.0, 10.4) == -12.0
    assert r.max_db(200.0, 201.0) is None, "an empty window must report None, not a number"


def test_speech_level_on_a_speech_silence_mix():
    # p90 over frames above -70: the channel's level WHEN SPEAKING, not its average.
    r = T.EnergyRing(retain_s=1e6)
    t = 0.0
    for _ in range(80):                     # mostly room tone
        r.add(t, -50.0); t += 0.1
    for _ in range(20):                     # a fifth of the window is speech
        r.add(t, -14.0); t += 0.1
    lvl = r.speech_level(t)
    assert lvl is not None and -15.5 <= lvl <= -13.0, f"speech_level should sit at the speech level, got {lvl}"
    assert T.EnergyRing().speech_level(10.0) is None, "no frames -> no estimate"


def test_noise_floor_is_p10_of_trailing_frames():
    r = T.EnergyRing(retain_s=1e6)
    t = 0.0
    for _ in range(90):
        r.add(t, -52.0); t += 0.1
    for _ in range(10):
        r.add(t, -10.0); t += 0.1
    nf = r.noise_floor(t)
    assert nf is not None and abs(nf - (-52.0)) < 0.6, f"noise_floor must be the room tone, got {nf}"
    assert T.EnergyRing().noise_floor(10.0) is None, "no frames -> no floor"


def test_noise_floor_tracks_a_raised_room_tone():
    r = T.EnergyRing(retain_s=1e6)
    t = 0.0
    for _ in range(100):                    # old, quiet room, outside the 10 s window read below
        r.add(t, -60.0); t += 0.1
    for _ in range(100):                    # the aircon came on
        r.add(t, -38.0); t += 0.1
    nf = r.noise_floor(t, window_s=10.0)
    assert nf is not None and abs(nf - (-38.0)) < 0.6, f"floor must follow the current room, got {nf}"


def test_coverage_sparse_vs_dense():
    # The WP-3/WP-7 contract: ghost/bleed windows measured ~0.39, real speech ~0.74.
    sparse = T.EnergyRing(retain_s=1e6)
    dense = T.EnergyRing(retain_s=1e6)
    for i in range(100):
        sparse.add(i * 0.1, -20.0 if i < 39 else -55.0)
        dense.add(i * 0.1, -20.0 if i < 74 else -55.0)
    cs = sparse.coverage(0.0, 9.9, -45.0)
    cd = dense.coverage(0.0, 9.9, -45.0)
    assert abs(cs - 0.39) < 0.02, f"sparse ghost window should cover ~0.39, got {cs}"
    assert abs(cd - 0.74) < 0.02, f"dense speech window should cover ~0.74, got {cd}"
    assert T.EnergyRing().coverage(0.0, 1.0, -45.0) == 0.0, "empty window -> 0.0, never a crash"


# --- the dynamic silence floor -------------------------------------------------------------

def test_silence_floor_static_for_a_raw_ring():
    # A raw feed IS the basis the -45 was calibrated on: never re-derive it.
    r = ring_with(-8.0, 0.0, 30.0)
    assert T._silence_floor_db(r, 30.0) == -45.0


def test_dynamic_floor_clamps_at_both_ends():
    loud = ring_with(-2.0, 0.0, 30.0, raw=False)      # speech_level -2 -> -32 -> clamped to -35
    quiet = ring_with(-40.0, 0.0, 30.0, raw=False)    # speech_level -40 -> -70 -> clamped to -55
    assert T._silence_floor_db(loud, 30.0) == -35.0, "floor must clamp at -35 for a hot channel"
    assert T._silence_floor_db(quiet, 30.0) == -55.0, "floor must clamp at -55 for a quiet channel"
    mid = ring_with(-15.0, 0.0, 30.0, raw=False)      # -15 - 30 = -45, inside the band
    assert abs(T._silence_floor_db(mid, 30.0) - (-45.0)) < 0.6
    assert T._silence_floor_db(T.EnergyRing(raw=False), 30.0) == -45.0, \
        "no speech estimate yet -> the static -45 floor"


# --- the ring-fed silence gate -------------------------------------------------------------

class _GateEngine:
    """Just the gate: Engine.__init__ loads a model, so borrow the two methods unbound."""
    _ring_for = T.Engine._ring_for
    _chunk_is_silence = T.Engine._chunk_is_silence

    def __init__(self, mic_ring=None, sys_ring=None, on=True):
        self.mic_env = mic_ring
        self.sys_env = sys_ring
        self._raw_mic_ring = on


def test_gate_skips_a_boosted_quiet_chunk_via_the_ring():
    # The headline case: AGC lifted the CHUNK to -20 dBFS (well over the floor) while the room
    # was really at -52. Chunk-fed gate: keeps it, Whisper fabricates. Ring-fed gate: skips it.
    e = _GateEngine(mic_ring=ring_with(-52.0, 9.0, 25.0))
    boosted = tone(-20.0, 15.0)
    assert T._is_silence(boosted) is False, "precondition: the boosted chunk looks like speech"
    assert e._chunk_is_silence("MIC", boosted, 10.0) is True, \
        "the ring-fed gate must see the RAW room level and skip the chunk"


def test_gate_keeps_real_speech_with_a_ring():
    e = _GateEngine(mic_ring=ring_with(-14.0, 9.0, 25.0))
    assert e._chunk_is_silence("MIC", tone(-14.0, 15.0), 10.0) is False


def test_gate_falls_back_without_a_ring():
    e = _GateEngine(mic_ring=None)
    assert e._chunk_is_silence("MIC", tone(-52.0, 3.0), 10.0) is T._is_silence(tone(-52.0, 3.0))
    assert e._chunk_is_silence("MIC", tone(-12.0, 3.0), 10.0) is False


def test_gate_kill_switch_ignores_the_mic_ring():
    # SA_LIVE_RAW_MIC_RING=0 -> _ring_for("MIC") is None -> exactly today's chunk behaviour.
    ring = ring_with(-52.0, 9.0, 25.0)
    boosted = tone(-20.0, 15.0)
    assert _GateEngine(mic_ring=ring, on=False)._chunk_is_silence("MIC", boosted, 10.0) is False
    assert _GateEngine(mic_ring=ring, on=True)._chunk_is_silence("MIC", boosted, 10.0) is True


def test_gate_falls_back_when_the_ring_has_no_frames_for_the_window():
    e = _GateEngine(mic_ring=ring_with(-52.0, 0.0, 5.0))      # nothing near t=600
    assert e._chunk_is_silence("MIC", tone(-20.0, 15.0), 600.0) is False, \
        "no ring evidence for the window -> fall back to the chunk, never gate blind"


def test_gate_dead_channel_window_skipped():
    # Nothing rose meaningfully above this channel's own room tone: max 5 dB over the p10.
    r = T.EnergyRing(retain_s=1e6)
    t = 0.0
    while t < 30.0:                          # 30 s of room tone at -50 sets the floor
        r.add(t, -50.0); t += 0.1
    while t < 45.0:                           # the judged window: 5 dB above that floor
        r.add(t, -45.0); t += 0.1
    e = _GateEngine(mic_ring=r)
    assert e._chunk_is_silence("MIC", tone(-20.0, 15.0), 30.0) is True, \
        "a window only 5 dB above its own room tone is a dead channel -> skip"


def test_gate_live_channel_not_skipped():
    r = T.EnergyRing(retain_s=1e6)
    t = 0.0
    while t < 30.0:
        r.add(t, -50.0); t += 0.1
    while t < 45.0:                           # 20 dB above the floor: someone spoke
        r.add(t, -30.0); t += 0.1
    e = _GateEngine(mic_ring=r)
    assert e._chunk_is_silence("MIC", tone(-20.0, 15.0), 30.0) is False, \
        "a window 20 dB above its own room tone must be kept"


def test_gate_loud_flat_channel_never_eaten():
    # R2 from the plan: a never-quiet channel (HVAC, music) poisons the p10 upward, so the
    # relative test alone would call every window dead. The -35 dBFS absolute cap forbids it.
    r = ring_with(-20.0, 0.0, 60.0)          # constant -20 dBFS, p10 == max == -20
    e = _GateEngine(mic_ring=r)
    assert e._chunk_is_silence("MIC", tone(-20.0, 15.0), 30.0) is False, \
        "a loud but flat channel must never be skipped by the dead-channel test"


def test_gate_sys_ring_also_gates():
    e = _GateEngine(sys_ring=ring_with(-52.0, 9.0, 25.0))
    assert e._chunk_is_silence("SYS", tone(-20.0, 15.0), 10.0) is True
    assert e._chunk_is_silence("SYS", tone(-20.0, 15.0), 600.0) is False   # no frames -> fallback


def test_gate_kill_switch_ignores_the_sys_ring_too():
    """SA_LIVE_RAW_MIC_RING=0 means "gate on chunk samples, like before WP-4" for BOTH sources.
    A switch that left SYS on its ring while MIC fell back restored neither behaviour."""
    r = ring_with(-52.0, 9.0, 25.0)
    boosted = tone(-20.0, 15.0)
    assert T._is_silence(boosted) is False, "precondition: the chunk itself looks like speech"
    assert _GateEngine(sys_ring=r, on=True)._chunk_is_silence("SYS", boosted, 10.0) is True
    assert _GateEngine(sys_ring=r, on=False)._chunk_is_silence("SYS", boosted, 10.0) is False, \
        "with the ring switched off the SYS gate must read the chunk, not the ring"
    # _ring_for itself is unchanged for SYS: the switch is applied by the gate, so the echo veto
    # keeps its far-end reference (see the note at the arm-1 call site).
    assert _GateEngine(sys_ring=r, on=False)._ring_for("SYS") is r


# --- the dead-channel baseline is measured BEFORE the window -------------------------------

def test_dead_channel_baseline_excludes_the_judged_window():
    """The bug: noise_floor() included the chunk under judgement, so the FIRST chunk of a
    sustained quiet talker (-40 dBFS throughout, p10 also -40) read as "0 dB above the room
    tone" and was eaten. The baseline now comes from frames strictly before t_start."""
    r = T.EnergyRing(retain_s=1e6)
    t = 0.0
    while t < 15.0:                          # nothing but this speaker, from the first frame
        r.add(t, -40.0); t += 0.1
    e = _GateEngine(mic_ring=r)
    assert -45.0 < -40.0, "precondition: -40 dBFS is above the absolute speech floor"
    assert e._chunk_is_silence("MIC", tone(-20.0, 15.0), 0.0) is False, \
        "sustained quiet speech must not be judged against itself and eaten"


def test_dead_channel_still_skips_against_an_established_room():
    # The case the arm exists for, unchanged: an established room tone, then a window only 6 dB
    # above it. The baseline is real history, so this is still a dead channel.
    r = T.EnergyRing(retain_s=1e6)
    t = 0.0
    while t < 40.0:                          # 40 s of room tone at -46 sets the baseline
        r.add(t, -46.0); t += 0.1
    while t < 55.0:                          # the judged window: 6 dB above that
        r.add(t, -40.0); t += 0.1
    e = _GateEngine(mic_ring=r)
    assert e._chunk_is_silence("MIC", tone(-20.0, 15.0), 40.0) is True, \
        "a window 6 dB above an ESTABLISHED room tone is still a dead channel"


def test_dead_channel_arm_inert_without_enough_history():
    """Under min_history_s of pre-window frames the relative arm is inert: a baseline measured
    over a second or two of a session that has only just begun is not a room tone. The absolute
    floor still does its job, so nothing is lost - only guessing is."""
    r = T.EnergyRing(retain_s=1e6)
    t = 0.0
    while t < 5.0:                           # only 5 s of history before the window
        r.add(t, -46.0); t += 0.1
    while t < 20.0:
        r.add(t, -40.0); t += 0.1
    e = _GateEngine(mic_ring=r)
    assert e._chunk_is_silence("MIC", tone(-20.0, 15.0), 5.0) is False, \
        "with under 10 s of history the relative arm must not fire"
    # 10 s of the same history and the same window: now it fires (the boundary is the history,
    # not the levels).
    r2 = T.EnergyRing(retain_s=1e6)
    t = 0.0
    while t < 10.0:
        r2.add(t, -46.0); t += 0.1
    while t < 25.0:
        r2.add(t, -40.0); t += 0.1
    assert _GateEngine(mic_ring=r2)._chunk_is_silence("MIC", tone(-20.0, 15.0), 10.0) is True, \
        "exactly min_history_s of history is enough (the bound is inclusive)"
    # and the absolute arm is untouched by any of this: a genuinely silent room with no history
    # at all is still skipped.
    assert _GateEngine(mic_ring=ring_with(-52.0, 0.0, 15.0))._chunk_is_silence(
        "MIC", tone(-20.0, 15.0), 0.0) is True


def test_noise_floor_before_is_strictly_before_and_needs_history():
    r = T.EnergyRing(retain_s=1e6)
    t = 0.0
    while t < 30.0:
        r.add(t, -50.0); t += 0.1
    while t < 45.0:
        r.add(t, -10.0); t += 0.1
    # the loud window after t=30 must not move the baseline at all
    nf = r.noise_floor_before(30.0)
    assert nf is not None and abs(nf - (-50.0)) < 0.6, f"baseline must exclude the window: {nf}"
    assert r.noise_floor_before(0.0) is None, "nothing before the start -> no baseline"
    assert r.noise_floor_before(5.0) is None, "under 10 s of history -> no baseline"
    assert r.noise_floor_before(10.0) is not None, "10 s of history is enough"
    # the window_s bound still applies: only recent history is the current room
    r2 = T.EnergyRing(retain_s=1e6)
    t = 0.0
    while t < 100.0:                          # an old, quiet room
        r2.add(t, -60.0); t += 0.1
    while t < 130.0:                          # the aircon came on 30 s ago
        r2.add(t, -38.0); t += 0.1
    nf2 = r2.noise_floor_before(130.0, window_s=20.0)
    assert nf2 is not None and abs(nf2 - (-38.0)) < 0.6, f"baseline must follow the room: {nf2}"


def test_gate_liveness_on_an_agc_lifted_quiet_set():
    """Instrumented acceptance number: on a synthetic set of AGC-lifted quiet chunks (raw room
    tone at -52 dBFS, boosted to about -20 in the chunk the engine sees), what fraction does each
    gate skip? Chunk-fed: ~0. Ring-fed: all of them."""
    raw_dbs = [-52.0, -50.0, -55.0, -48.0, -53.0, -51.0, -49.0, -54.0]
    chunk_skipped = ring_skipped = 0
    for i, raw_db in enumerate(raw_dbs):
        t0 = 10.0 + i * 20.0
        e = _GateEngine(mic_ring=ring_with(raw_db, t0 - 1.0, t0 + 16.0))
        boosted = tone(-20.0, 15.0)          # what AGC hands the engine
        if T._is_silence(boosted):
            chunk_skipped += 1
        if e._chunk_is_silence("MIC", boosted, t0):
            ring_skipped += 1
    assert chunk_skipped == 0, "precondition: the chunk-fed gate is blind to AGC-lifted silence"
    assert ring_skipped == len(raw_dbs), f"ring-fed gate skipped {ring_skipped}/{len(raw_dbs)}"
    print(f"    gate liveness: chunk-fed {chunk_skipped}/{len(raw_dbs)}, "
          f"ring-fed {ring_skipped}/{len(raw_dbs)} of AGC-lifted quiet chunks skipped")


# --- the echo veto's mic_ring arm ----------------------------------------------------------

def test_gain_invariance_ab():
    """THE core acceptance test. A quiet bleed mic (-33 dBFS) under a loud active far end is a
    ghost the veto must drop. Boost that same mic +12 dB (what live AGC does) and the chunk-fed
    veto flips to KEEP, because the boosted copy clears the -28 dBFS ceiling. Fed the raw ring,
    both give the same verdict."""
    plain = tone(-33.0, 3.0)
    gained = plain * 4.0                       # +12.04 dB, exactly what AGC does to a quiet mic
    mic_ring = ring_with(-33.0, 9.5, 13.5)     # the RAW mic: unchanged by the boost
    sysr = ring_with(-19.0, 9.5, 13.5)

    d_plain, w_plain = T.sys_echo_veto(plain, sysr, 10.0, 13.0, word_count=8, mic_ring=mic_ring)
    d_gained, w_gained = T.sys_echo_veto(gained, ring_with(-19.0, 9.5, 13.5), 10.0, 13.0,
                                         word_count=8, mic_ring=mic_ring)
    assert d_plain is d_gained is True, \
        f"with the raw ring both must be dropped: plain={d_plain} ({w_plain}) gained={d_gained} ({w_gained})"

    # ... and document the bug this fixes: without the ring the gain flips the decision.
    n_plain, _ = T.sys_echo_veto(plain, ring_with(-19.0, 9.5, 13.5), 10.0, 13.0, word_count=8)
    n_gained, why = T.sys_echo_veto(gained, ring_with(-19.0, 9.5, 13.5), 10.0, 13.0, word_count=8)
    assert n_plain is True and n_gained is False, \
        f"chunk-fed veto must flip under +12 dB (that is the bug): {n_plain} -> {n_gained} ({why})"


def test_mic_ring_none_is_byte_identical():
    # The kwarg is additive: omitting it, or passing None, must reproduce today's answers exactly.
    cases = [(tone(-33, 3.0), -19.0, 10.0, 13.0, 8),
             (tone(-8, 3.0), -19.0, 10.0, 13.0, 8),
             (tone(-24, 3.0), -19.0, 10.0, 13.0, 8),
             (tone(-33, 0.4), -19.0, 10.0, 10.4, 1),
             (tone(-33, 3.0), -60.0, 10.0, 13.0, 8)]
    for mic, sdb, a, b, wc in cases:
        base = T.sys_echo_veto(mic, ring_with(sdb, a - 0.5, b + 0.5), a, b, word_count=wc)
        with_none = T.sys_echo_veto(mic, ring_with(sdb, a - 0.5, b + 0.5), a, b, word_count=wc,
                                    mic_ring=None)
        assert base == with_none, f"mic_ring=None must be identical: {base} != {with_none}"


def test_veto_falls_back_when_the_mic_ring_is_empty_for_the_window():
    # A ring with no frames in the window must not silently answer "quiet" (that would be a
    # drop-everything bug); it falls back to the chunk samples.
    empty = T.EnergyRing()
    drop, why = T.sys_echo_veto(tone(-8, 3.0), ring_with(-19.0, 9.5, 13.5), 10.0, 13.0,
                               word_count=8, mic_ring=empty)
    assert not drop, f"loud real speech must survive an empty mic ring: {why}"
    assert "micsrc=ring" not in why, "an empty ring must not claim to have fed the decision"


def test_veto_reason_names_the_ring_source():
    _d, why = T.sys_echo_veto(tone(-33, 3.0), ring_with(-19.0, 9.5, 13.5), 10.0, 13.0,
                              word_count=8, mic_ring=ring_with(-33.0, 9.5, 13.5))
    assert "micsrc=ring" in why, f"the log line must say where mic_p90 came from: {why}"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nall {len(tests)} gain-invariance tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
