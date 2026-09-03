"""WP-3: stop feeding a near-silent microphone to the model.

Three changes, pinned here:
  1. `_chunk_is_silence` arm 3 - a MIC chunk is decoded only when enough of its RAW energy-ring
     frames clear an evidence threshold. Arms 1 and 2 judge the loudest frame, so one bang keeps
     a whole 15 s chunk of room tone; this one asks how many frames, not how loud the loudest is.
  2. per-source VAD options: a tightened set for MIC, the faster-whisper defaults for SYS.
  3. PromptLeakMatcher mode C - the short anchor-echo scatter that modes A and B are both too
     coverage-hungry to see.

The locked constraint the last group of tests defends: the gate is a DECODE filter. The recorder
is fed from the capture callback ahead of the engine queue, so a skipped chunk is still recorded
in full, and the SYS path is untouched end to end.

Synthetic rings and arrays only: no model load, no audio device, no recording.

Run:  python tests/test_mic_gate.py   (exit 0 = pass)
"""
import io
import os
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from live_transcribe import transcribe as T

SR = 16000
ROOM_DB = -52.0        # the incident capture's near-end room tone sat -53 to -59 dBFS
SPEECH_DB = -25.0      # its genuine near-end speech sat -22.8 dBFS mean
BANG_DB = -10.0        # one door bang / keyboard click: what the peak arms cannot tell from speech
HISTORY_S = 60.0       # room tone before the judged window (over noise_floor_before's 10 s minimum)
CHUNK_S = 15.0


def audio(db=-20.0, secs=CHUNK_S):
    """A chunk that LOOKS like speech to a sample-level test, whatever the ring says. Every gate
    test feeds this, so a pass can only come from the ring, never from the samples."""
    a = np.random.RandomState(0).randn(int(secs * SR)).astype(np.float32)
    return a * ((10.0 ** (db / 20.0)) / (float(np.sqrt(np.mean(a * a))) + 1e-9))


def ring_of(window, room_db=ROOM_DB, history_s=HISTORY_S, step=0.1, raw=True, lift_db=0.0):
    """A raw MIC ring: `history_s` of room tone, then `window` (a list of frame dB) as the chunk.

    lift_db simulates an AGC applied to the RING FEED itself (the thing WP-4 exists to prevent):
    every frame, floor and content alike, moves up together.
    """
    r = T.EnergyRing(retain_s=1e6, raw=raw)
    t = 0.0
    while t < history_s - 1e-9:
        r.add(round(t, 3), room_db + lift_db)
        t += step
    for db in window:
        r.add(round(t, 3), db + lift_db)
        t += step
    return r


def frames(n_loud, loud_db, room_db=ROOM_DB, n=int(CHUNK_S / 0.1)):
    """One chunk's worth of frames: `n_loud` of them at loud_db, the rest at room tone."""
    return [loud_db] * n_loud + [room_db] * (n - n_loud)


class _GateEngine:
    """Just the gate. Engine.__init__ loads a model, so borrow the methods unbound - the same
    shape tests/test_gain_invariant.py uses, deliberately."""
    _ring_for = T.Engine._ring_for
    _chunk_is_silence = T.Engine._chunk_is_silence

    def __init__(self, mic_ring=None, sys_ring=None, arm3=True, debug=False):
        self.mic_env = mic_ring
        self.sys_env = sys_ring
        self._raw_mic_ring = True
        self._mic_speech_gate = arm3
        self._mic_gate_debug = debug


# --- arm 3: continuity, not peak -------------------------------------------------------------

def test_gate_blocks_room_tone_with_one_transient():
    """The whole reason arm 3 exists. 15 s of room tone with two loud frames in it: the absolute
    arm sees a -10 dBFS peak (way over the speech floor), the dead-channel arm sees 42 dB over
    the room tone, and both keep the chunk. Only the frame COUNT says there is no speech here."""
    ring = ring_of(frames(2, BANG_DB))
    peaked = _GateEngine(mic_ring=ring, arm3=False)
    assert peaked._chunk_is_silence("MIC", audio(), HISTORY_S) is False, \
        "precondition: the two peak arms keep this chunk (that is the bug)"
    assert _GateEngine(mic_ring=ring)._chunk_is_silence("MIC", audio(), HISTORY_S) is True, \
        "two loud frames in 15 s is a transient, not speech: arm 3 must skip the chunk"


def test_gate_blocks_a_pure_noise_floor_chunk():
    ring = ring_of(frames(0, BANG_DB))
    assert _GateEngine(mic_ring=ring)._chunk_is_silence("MIC", audio(), HISTORY_S) is True


def test_gate_passes_a_speech_level_chunk():
    # 6 s of near-end speech in a 15 s chunk: kept, and the samples are irrelevant to that.
    ring = ring_of(frames(60, SPEECH_DB))
    assert _GateEngine(mic_ring=ring)._chunk_is_silence("MIC", audio(), HISTORY_S) is False


def test_gate_passes_exactly_half_a_second_of_speech():
    """The documented bound: MIC_EVIDENCE_SECONDS of frames is enough, one frame less is not.
    Pinned because it is the recall floor of the whole package."""
    keep = ring_of(frames(5, SPEECH_DB))
    skip = ring_of(frames(4, SPEECH_DB))
    assert _GateEngine(mic_ring=keep)._chunk_is_silence("MIC", audio(), HISTORY_S) is False, \
        "0.5 s of speech-level frames must survive"
    assert _GateEngine(mic_ring=skip)._chunk_is_silence("MIC", audio(), HISTORY_S) is True


def test_gate_passes_a_quiet_talker_in_a_quiet_room():
    """A talker 18 dB below the -25 dBFS reference, in a room 15 dB quieter than the incident's.
    The threshold rides the room down, so quiet speech is not the price of this arm."""
    ring = ring_of(frames(60, -43.0), room_db=-67.0)
    assert _GateEngine(mic_ring=ring)._chunk_is_silence("MIC", audio(), HISTORY_S) is False


# --- the AGC, which is the reason the ring must stay raw --------------------------------------

def test_gate_holds_when_agc_lifts_the_chunk_but_not_the_ring():
    """The live case: AGC boosted the audio the engine sees to -20 dBFS while the room really was
    at -52. The chunk samples say speech; the RAW ring says room tone; the ring wins."""
    ring = ring_of(frames(2, BANG_DB))
    assert T._is_silence(audio(-20.0)) is False, "precondition: the boosted chunk looks like speech"
    assert _GateEngine(mic_ring=ring)._chunk_is_silence("MIC", audio(-20.0), HISTORY_S) is True


def test_gate_survives_a_moderate_lift_of_the_ring_itself():
    """Relative by construction: lift the whole ring 10 dB (floor and content together) and both
    verdicts are unchanged, because the threshold is measured from the room, not from zero."""
    noise = ring_of(frames(2, BANG_DB), lift_db=10.0)
    speech = ring_of(frames(60, SPEECH_DB), lift_db=10.0)
    assert _GateEngine(mic_ring=noise)._chunk_is_silence("MIC", audio(), HISTORY_S) is True
    assert _GateEngine(mic_ring=speech)._chunk_is_silence("MIC", audio(), HISTORY_S) is False


def test_gate_goes_inert_rather_than_greedy_under_a_heavy_lift():
    """A lift big enough to push the room itself past MIC_EVIDENCE_CEILING_DB (a very noisy room,
    or a ring wrongly fed post-AGC) makes the arm keep everything. That is the designed failure
    direction: the ceiling exists so a loud floor disables the arm instead of eating a talker."""
    noise = ring_of(frames(2, BANG_DB), lift_db=25.0)
    assert _GateEngine(mic_ring=noise)._chunk_is_silence("MIC", audio(), HISTORY_S) is False


# --- what the arm must never touch ------------------------------------------------------------

def test_sys_chunks_are_never_gated_by_arm_3():
    """SYS is a digital feed at a known level with no room and no AGC. The identical ring that
    skips on MIC must keep on SYS, or the far end starts losing lines to constants measured on
    a microphone."""
    window = frames(2, BANG_DB)
    assert _GateEngine(mic_ring=ring_of(window))._chunk_is_silence("MIC", audio(), HISTORY_S) is True
    assert _GateEngine(sys_ring=ring_of(window))._chunk_is_silence("SYS", audio(), HISTORY_S) is False


def test_arm_3_switch_restores_the_peak_only_gate():
    ring = ring_of(frames(2, BANG_DB))
    assert _GateEngine(mic_ring=ring, arm3=False)._chunk_is_silence("MIC", audio(), HISTORY_S) is False
    assert _GateEngine(mic_ring=ring, arm3=True)._chunk_is_silence("MIC", audio(), HISTORY_S) is True


def test_arm_3_is_inert_without_a_room_baseline():
    """No history before the chunk (session start), or under noise_floor_before's 10 s minimum:
    the arm has nothing to measure a threshold from and must not guess one."""
    ring = ring_of(frames(2, BANG_DB), history_s=0.0)
    assert _GateEngine(mic_ring=ring)._chunk_is_silence("MIC", audio(), 0.0) is False
    short = ring_of(frames(2, BANG_DB), history_s=5.0)
    assert _GateEngine(mic_ring=short)._chunk_is_silence("MIC", audio(), 5.0) is False


def test_arm_3_never_rescues_a_chunk_the_peak_arms_skipped():
    """Additive, in one direction only: arm 3 can turn a keep into a skip, never a skip into a
    keep. A room-tone chunk with plenty of frames just above its own floor is still skipped by
    the absolute arm."""
    ring = ring_of(frames(150, -60.0), room_db=-70.0)
    assert _GateEngine(mic_ring=ring)._chunk_is_silence("MIC", audio(), HISTORY_S) is True


def test_evidence_helper_reports_inert_rather_than_false():
    r = ring_of(frames(2, BANG_DB), history_s=0.0)
    verdict, why = T.mic_speech_evidence(r, 0.0, CHUNK_S)
    assert verdict is None and "baseline" in why, (verdict, why)
    # a real baseline, but the window sits past the end of the ring
    verdict, why = T.mic_speech_evidence(ring_of(frames(2, BANG_DB)), 80.0, 95.0)
    assert verdict is None and "frames" in why, (verdict, why)


def test_debug_log_is_numbers_only():
    """The per-chunk decision log must never carry audio or transcribed text."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        _GateEngine(mic_ring=ring_of(frames(2, BANG_DB)), debug=True)._chunk_is_silence(
            "MIC", audio(), HISTORY_S)
        _GateEngine(mic_ring=ring_of(frames(60, SPEECH_DB)), debug=True)._chunk_is_silence(
            "MIC", audio(), HISTORY_S)
    out = buf.getvalue()
    assert "[gate] MIC @ 60.0s skip" in out and "[gate] MIC @ 60.0s keep" in out, out
    assert "evid=" in out and "thr=" in out, out
    quiet = io.StringIO()
    with redirect_stdout(quiet):
        _GateEngine(mic_ring=ring_of(frames(2, BANG_DB)))._chunk_is_silence("MIC", audio(), HISTORY_S)
    assert quiet.getvalue() == "", "the log must stay off unless SA_LIVE_MIC_GATE_DEBUG is set"


# --- per-source VAD ---------------------------------------------------------------------------

def test_vad_options_differ_per_source():
    mic = T.vad_options_for("MIC")
    assert mic is T.MIC_VAD and T.vad_options_for("SYS") is None, (mic, T.vad_options_for("SYS"))
    assert T.vad_options_for("MIC_RAW") is None, "only the engine-bound MIC source is tightened"


def test_mic_vad_is_tighter_than_the_library_defaults():
    from faster_whisper.vad import VadOptions
    d = VadOptions()
    assert T.MIC_VAD["min_silence_duration_ms"] < d.min_silence_duration_ms
    assert T.MIC_VAD["speech_pad_ms"] < d.speech_pad_ms
    assert T.MIC_VAD["min_speech_duration_ms"] > d.min_speech_duration_ms
    assert T.MIC_VAD["threshold"] == d.threshold, \
        "the speech threshold is deliberately unchanged: raising it is where quiet speech dies"
    VadOptions(**T.MIC_VAD)   # every key must be a real VadOptions field on the pinned version


def test_engine_passes_the_per_source_options_to_the_decoder():
    import inspect
    src = inspect.getsource(T.Engine._run)
    assert "vad_parameters=vad_options_for(source)" in src, \
        "the decode call must select VAD options by source, not pass one set for both"


def test_mlx_backend_drops_vad_parameters():
    """mlx-whisper has no vad_parameters kwarg; the adapter must drop it, not raise."""
    from live_transcribe import mlxbackend
    assert "vad_parameters" in mlxbackend.DROPPED_KWARGS


# --- prompt-leak mode C -----------------------------------------------------------------------

def _matcher():
    return T.PromptLeakMatcher("Danica Freimond, Sean Freimond", T.AF_ANCHOR_PROMPT)


def test_anchor_echo_lines_dropped():
    m = _matcher()
    for text in ("Afrikaans, kodewissel, dankie",
                 "Kodewisseling, vergadering, besigheid.",
                 "Afrikaans en Engels.",
                 "Sprekers, Afrikaans, kodewisseling."):
        assert m.is_leak(text) is True, f"prompt echo kept: {text!r}"


def test_genuine_short_afrikaans_kept():
    m = _matcher()
    for text in ("Ja, baie dankie",
                 "ons kinders is baie lekker vandag",
                 "Dankie, kollegas.",
                 "Nee wag, ek dink nie so nie",
                 "Ons het gister die vergadering gehad oor die besigheid se planne"):
        assert m.is_leak(text) is False, f"genuine speech dropped: {text!r}"


def test_anchor_terms_are_derived_from_the_constant():
    """No second copy of the word list: change AF_ANCHOR_PROMPT and the terms change with it."""
    m = _matcher()
    anchor_toks = set(T._norm_tokens(T.AF_ANCHOR_PROMPT))
    assert m._anchor_terms and set(m._anchor_terms) <= anchor_toks
    for term in ("kodewisseling", "dankie", "besigheid", "vergadering",
                 "kollegas", "afrikaans", "engels", "sprekers"):
        assert term in m._anchor_terms, term
    assert m._anchor_head <= set(m._anchor_terms)
    assert "dankie" not in m._anchor_head, \
        "the anchor's own 'Algemene woorde' half is ordinary speech and cannot arm the drop"


def test_mode_c_needs_two_distinct_terms_and_one_from_the_instruction_half():
    m = _matcher()
    assert m.is_leak("Afrikaans.") is False, "one term is a speaker legitimately saying it"
    assert m.is_leak("Vergadering, kollegas, besigheid.") is False, \
        "common-word terms alone must never arm the drop"


def test_mode_c_escapes_on_the_speakers_own_words():
    m = _matcher()
    assert m.is_leak("Ek dink ons moet Afrikaans praat in die gesprek more oggend") is False


def test_mode_c_is_inert_without_an_anchor():
    en = T.PromptLeakMatcher("Danica Freimond", None)
    assert en._anchor_terms == [] and en.is_leak("Afrikaans, kodewissel, dankie") is False


# --- the locked constraint: recording is never gated ------------------------------------------

def test_the_recorder_is_fed_ahead_of_the_engine():
    """Structural, and the reason a skipped chunk is still on disk: _feed hands the chunk to the
    recorder BEFORE the engine, and the gate lives inside the engine worker, behind its queue."""
    import inspect
    from live_transcribe.web import app as webapp
    feed = inspect.getsource(webapp._feed)
    assert feed.index("rec.on_chunk(source, audio, t_start)") < feed.index("eng.on_chunk(source, audio, t_start)"), \
        "the recorder must be fed before the engine"
    assert "_chunk_is_silence" not in feed, "the gate must not sit on the recording path"
    assert "_chunk_is_silence" in inspect.getsource(T.Engine._run), \
        "the gate belongs in the engine worker, downstream of the recorder tap"
    assert "_chunk_is_silence" not in inspect.getsource(T.Engine.on_chunk), \
        "the gate must not run at enqueue time either"


def test_a_gated_chunk_still_reaches_the_recorder():
    """Behavioural companion: an engine that skips EVERY chunk changes nothing about what the
    recorder is handed."""
    from live_transcribe.web import app as webapp

    seen = {"rec": [], "eng": []}

    class Rec:
        def on_chunk(self, source, a, t):
            seen["rec"].append((source, t))

    class Eng:
        def on_chunk(self, source, a, t, block=False, timeout=None):
            seen["eng"].append((source, t))   # the queue always accepts; the gate acts later
            return True

    st = webapp.STATE
    saved = (st.recorder, st.engine, st.recording, st.transcribing, st.record_raw_mic)
    try:
        st.recorder, st.engine = Rec(), Eng()
        st.recording = st.transcribing = True
        st.record_raw_mic = False
        for i in range(4):
            webapp._feed("MIC", audio(), float(i) * CHUNK_S)
        webapp._feed("SYS", audio(), 0.0)
        assert seen["rec"] == [("MIC", 0.0), ("MIC", 15.0), ("MIC", 30.0), ("MIC", 45.0), ("SYS", 0.0)], \
            seen["rec"]
    finally:
        (st.recorder, st.engine, st.recording, st.transcribing, st.record_raw_mic) = saved


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nall {len(tests)} MIC-gate tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
