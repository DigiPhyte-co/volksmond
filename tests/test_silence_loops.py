"""Unit tests for the silence-hallucination guards (step 4): _is_silence (pre-transcription skip),
_is_phrase_loop (multi-word loop drop), and the anchor-leak regex fix. Companion to test_echo_veto.py.

Run:  python tests/test_silence_loops.py   (exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from live_transcribe import transcribe as T


def tone(db, secs, sr=16000):
    n = int(secs * sr)
    a = np.random.RandomState(0).randn(n).astype(np.float32)
    return a * ((10.0 ** (db / 20.0)) / (float(np.sqrt(np.mean(a * a))) + 1e-9))


# --- _is_silence ---
def test_silence_room_tone():
    assert T._is_silence(tone(-52, 3.0)) is True            # below the speech floor -> silence

def test_silence_real_speech_kept():
    assert T._is_silence(tone(-12, 3.0)) is False           # loud speech -> keep

def test_silence_quiet_word_amid_silence_kept():
    a = tone(-52, 3.0).copy()                               # room tone ...
    a[16000:16000 + int(0.4 * 16000)] = tone(-25, 0.4)      # ... with a 0.4s real word spliced in
    assert T._is_silence(a) is False                        # one loud frame keeps the whole chunk

def test_silence_too_short():
    assert T._is_silence(np.zeros(100, dtype=np.float32)) is False


# --- _is_phrase_loop ---
def test_loop_multiword():
    assert T._is_phrase_loop("ek het nie ek het nie ek het nie ek het nie") is True

def test_loop_real_sentence_kept():
    assert T._is_phrase_loop("so ek dink ons moet more die begroting afhandel en dan verder gaan") is False

def test_loop_short_backchannel_kept():
    assert T._is_phrase_loop("ja ja ja") is False           # too short to be a confident loop

def test_loop_incidental_repeat_kept():
    assert T._is_phrase_loop("ek dink ek dink ons moet nou regtig begin werk aan die ding") is False


# --- anchor-leak regex (the fix: match on a trailing period, not only : or ,) ---
def test_anchor_period_dropped():
    assert T._is_hallucination("Algemene woorde.") is True

def test_anchor_colon_dropped():
    assert T._is_hallucination("Algemene woorde: baie, nogal") is True

def test_normal_speech_not_flagged():
    assert T._is_hallucination("ons het vandag oor die begroting gepraat") is False


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"\nall {len(tests)} silence/loop tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
