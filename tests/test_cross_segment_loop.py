"""Unit tests for the cross-segment repetition guard (WP-2): RecentEmissions.

The two loops below are the real runs from the 2026-07-29 incident transcript: "[MIC] Bye."
22 times at one per second (27:32-27:53), and an "and" / "Danica Freimond" alternation of 22
pairs at two per second (33:42-34:11 SYS). Both are invisible to every existing guard because
those are all segment-scoped.

The "kept" cases are the backchannel safety contract: a real "ja" never loses anything unless
it is both short AND fast AND already past four cycles, and MIC/SYS never share a history.

No model load, no audio. Run:  python tests/test_cross_segment_loop.py   (exit 0 = pass)
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import transcribe as T


def drive(items, source="SYS", r=None):
    """Feed (text, t_start) pairs through one RecentEmissions; return the period per item."""
    r = r or T.RecentEmissions()
    return [r.observe(source, text, t) for text, t in items]


# --- the two real incident loops ---
def test_bye_x22_first_four_kept():
    periods = drive([("Bye.", i * 1.0) for i in range(22)])
    assert periods[:4] == [0, 0, 0, 0]              # four cycles of evidence survive
    assert all(p == 1 for p in periods[4:])         # the whole tail is suppressed
    assert periods.count(0) == 4 and len(periods) - 4 == 18

def test_alternating_pairs_x22_period_two():
    seq = []
    for i in range(22):
        seq.append(("and", i * 1.0))
        seq.append(("Danica Freimond", i * 1.0 + 0.5))
    periods = drive(seq)
    assert periods[:8] == [0] * 8                   # four complete pairs published
    assert all(p == 2 for p in periods[8:])         # 18 pairs suppressed, both halves
    assert periods.count(0) == 8

def test_period_three_cycle():
    seq = []
    for i in range(10):
        for w in ("ja", "nee", "ok"):
            seq.append((w, len(seq) * 0.5))
    periods = drive(seq)
    assert periods[:12] == [0] * 12                 # four cycles of three
    assert all(p == 3 for p in periods[12:])


# --- backchannel safety ---
def test_slow_backchannel_all_kept():
    # real backchannels are interleaved with the other party's turns: 8 s apart -> density fails
    assert drive([("ja", i * 8.0) for i in range(6)]) == [0] * 6

def test_fast_backchannel_keeps_first_four():
    # documents the accepted behaviour explicitly: 1 s apart is decoder-loop territory
    periods = drive([("ja", i * 1.0) for i in range(6)])
    assert periods == [0, 0, 0, 0, 1, 1]

def test_long_line_repeated_kept():
    # 5 content tokens > the 4-token ceiling: a repeating real sentence is not our business
    line = "we should finalise the budget tomorrow"
    assert drive([(line, i * 1.0) for i in range(5)]) == [0] * 5

def test_sources_never_share_history():
    r = T.RecentEmissions()
    seq = [("yeah", i * 0.5) for i in range(8)]
    interleaved = [r.observe("MIC" if i % 2 == 0 else "SYS", t, ts)
                   for i, (t, ts) in enumerate(seq)]
    assert interleaved == [0] * 8                   # 4 per channel -> nobody reaches cycle 5
    # the same 8 emissions on ONE source DO trip the guard, which is what makes the point
    assert drive(seq, source="MIC") == [0, 0, 0, 0, 1, 1, 1, 1]

def test_different_speech_breaks_the_cycle():
    r = T.RecentEmissions()
    drive([("Bye.", i * 1.0) for i in range(8)], r=r)          # guard armed
    assert r.observe("SYS", "so where does that leave us", 8.0) == 0
    assert r.observe("SYS", "Bye.", 9.0) == 0                  # cycle broken, guard disarmed

def test_normalisation_ignores_case_and_punctuation():
    periods = drive([("Bye.", 0.0), ("bye", 1.0), ("BYE!", 2.0), ("Bye,", 3.0), ("bye.", 4.0)])
    assert periods == [0, 0, 0, 0, 1]

def test_empty_text_never_suppressed():
    assert drive([("...", i * 1.0) for i in range(8)]) == [0] * 8


# --- reset points ---
def test_clear_resets_history():
    r = T.RecentEmissions()
    assert drive([("Bye.", i * 1.0) for i in range(5)], r=r)[-1] == 1
    r.clear()                                        # e.g. a live language/model change
    assert r.observe("SYS", "Bye.", 5.0) == 0

def test_engine_clears_on_pending_change():
    """R2: the history lives on the Engine and survives chunk boundaries, so a post-switch
    style change could false-trigger once if _apply_pending_change did not clear it."""
    assert "self._recent.clear()" in inspect.getsource(T.Engine._apply_pending_change)
    assert "self._recent.clear()" in inspect.getsource(T.Engine.start)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"\nall {len(tests)} cross-segment loop tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
