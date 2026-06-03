"""Unit test for the end-of-session transcript cleanup (dedup.strip_mic_echoes).

On speakers the mic re-hears the far side, so the same words land on both MIC and SYS a
moment apart and in unpredictable processing order. The cleanup runs once at the end, when
both channels are fully present, so it is order-independent: it drops MIC lines that echo a
near-simultaneous SYS line and keeps genuine MIC speech (which has no SYS twin). It must
work whether the MIC echo arrived before or after its SYS original.

Run:  python tests/test_dedup.py   (from the project root; exit 0 = pass)
"""
import os
import sys
from dataclasses import dataclass

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import dedup


@dataclass
class _Seg:
    source: str
    t_start: float
    text: str


def test_strip_mic_echoes_both_orders():
    # Mirrors a real speaker transcript: MIC echoes that arrive BEFORE and AFTER their SYS
    # twin, plus one genuine MIC line that must survive.
    segs = [
        _Seg("SYS", 12.0, "My kak raak jy heel tyd weg"),
        _Seg("MIC", 8.0,  "my kak praat jy heel tyd"),               # echo, ahead of its SYS twin
        _Seg("MIC", 30.0, "Ek dink ons begin volgende kwartaal"),    # genuine speech, no SYS twin
        _Seg("SYS", 60.0, "jou sleutel jou phone jou remote"),
        _Seg("MIC", 61.0, "jou sleutel jou phone jou remote"),       # echo, behind its SYS twin
    ]
    kept = dedup.strip_mic_echoes(sorted(segs, key=lambda s: s.t_start))
    texts = [s.text for s in kept]

    assert "my kak praat jy heel tyd" not in texts, f"MIC echo (early) not removed: {texts}"
    assert texts.count("jou sleutel jou phone jou remote") == 1, f"MIC echo (late) not removed: {texts}"
    assert "Ek dink ons begin volgende kwartaal" in texts, f"genuine MIC speech dropped: {texts}"
    assert sum(1 for s in kept if s.source == "SYS") == 2, "a SYS line was wrongly removed"
    # Result is chronological (we sorted before stripping).
    assert [s.t_start for s in kept] == sorted(s.t_start for s in kept), "output not in time order"
    print("  OK  cleanup drops MIC echoes in either arrival order, keeps real speech, stays ordered")


def test_no_sys_means_no_change():
    # An imported single-source file (FILE only) or a mic-only session has nothing to echo
    # against, so every line must survive untouched.
    segs = [_Seg("FILE", 0.0, "hello world"), _Seg("FILE", 5.0, "hello world")]
    kept = dedup.strip_mic_echoes(segs)
    assert len(kept) == 2, f"single-source transcript was altered: {[s.text for s in kept]}"
    print("  OK  no SYS channel -> nothing removed (imports / mic-only untouched)")


def test_strips_garbled_speaker_echo():
    # Real speaker echoes are heavily mis-heard, so the MIC copy shares few EXACT words with
    # the SYS line ("dinsdag"->"dansdag", "mooi"->"moois"). Fuzzy (near-spelled) matching plus
    # the shared-word floor still removes them. Taken from a real speaker transcript.
    segs = [
        _Seg("SYS", 12.0, "Lekker boys hoop het loop mooi is dinsdag ek let jy weet vanaf"),
        _Seg("MIC", 14.0, "Lekker moois op een dood dansdag ek jy weet vanaf"),   # garbled echo
    ]
    texts = [s.text for s in dedup.strip_mic_echoes(segs)]
    assert "Lekker moois op een dood dansdag ek jy weet vanaf" not in texts, "garbled echo not removed"
    assert "Lekker boys hoop het loop mooi is dinsdag ek let jy weet vanaf" in texts, "SYS line lost"
    print("  OK  garbled speaker echo (low exact overlap) removed via fuzzy matching")


def test_fuzzy_share_count_is_one_to_one():
    # A single SYS word must not be reused for two MIC mis-spellings (no overcount).
    sys = dedup.tokens("alpha beta dinsdag omega")
    mic = dedup.tokens("alpha beta dansdag dinsdaag")   # dansdag + dinsdaag both ~ dinsdag
    n = dedup._shared_count(mic, sys)
    assert n == 3, f"expected 3 shared (alpha, beta, one of dinsdag), got {n}"
    print("  OK  fuzzy shared-count is one-to-one (a SYS word is not reused)")


def test_fuzzy_match_is_optimal_not_exact_first():
    # Max matching must beat naive exact-first: an exact token may be better "spent" on a fuzzy
    # pairing to free another word. MIC abcde/abxde vs SYS abcde/abcdf yields 4 (+ one + two).
    sys = dedup.tokens("abcde abcdf one two")
    mic = dedup.tokens("abcde abxde one two")
    n = dedup._shared_count(mic, sys)
    assert n == 4, f"expected optimal matching of 4, got {n}"
    print("  OK  shared-count uses a maximum matching (exact tokens reassigned when better)")


def test_keeps_short_genuine_reply_sharing_words():
    # A genuine short reply that reuses a couple of the far side's words must NOT be deleted
    # as echo (losing real speech is worse than leaving a duplicate). Conservative thresholds.
    segs = [
        _Seg("SYS", 10.0, "we should start next quarter"),
        _Seg("MIC", 11.0, "yes next quarter"),          # genuine agreement, shares 2 words
        _Seg("MIC", 12.0, "sounds good lets do it"),    # genuine, shares nothing
    ]
    kept = [s.text for s in dedup.strip_mic_echoes(segs)]
    assert "yes next quarter" in kept, "genuine short reply wrongly dropped as echo"
    assert "sounds good lets do it" in kept, "genuine reply wrongly dropped"
    print("  OK  genuine short replies that reuse a few far-side words are kept")


def test_markers_and_notices_are_kept():
    # Bracketed marker/notice lines must never be treated as speech for echo matching, and
    # must survive the cleanup, while a real MIC echo of a real SYS line is still removed.
    notice = "[engine: switched to 'small' model to keep up with the audio]"
    segs = [
        _Seg("SYS", 5.0, "alpha beta gamma delta epsilon"),
        _Seg("SYS", 6.0, notice),
        _Seg("MIC", 7.0, "alpha beta gamma delta epsilon"),   # echo of the real SYS line
    ]
    kept = [s.text for s in dedup.strip_mic_echoes(segs)]
    assert notice in kept, "engine notice was dropped"
    assert kept.count("alpha beta gamma delta epsilon") == 1, "MIC echo not removed / SYS lost"
    print("  OK  markers/notices kept and excluded from echo matching")


def test_markdown_sink_rewrites_clean_and_ordered():
    # End to end through the real sink: feed segments out of order with a MIC echo that
    # arrives BEFORE its SYS twin, then close. The saved file must come out chronological
    # with the echo gone and genuine speech kept.
    import tempfile
    from pathlib import Path
    from live_transcribe.sinks import MarkdownSink

    p = Path(tempfile.mkdtemp()) / "sess.md"
    sink = MarkdownSink(p)
    sink(_Seg("MIC", 8.0, "my kak praat jy heel tyd"))            # echo, arrives first
    sink(_Seg("SYS", 12.0, "My kak raak jy heel tyd weg"))        # its clean twin, later
    sink(_Seg("MIC", 30.0, "Ek dink ons begin volgende kwartaal"))  # genuine speech
    sink.close()

    out = p.read_text(encoding="utf-8")
    assert "my kak praat jy heel tyd" not in out, "MIC echo survived the rewrite"
    assert "My kak raak jy heel tyd weg" in out, "SYS line lost in the rewrite"
    assert "Ek dink ons begin volgende kwartaal" in out, "genuine MIC speech lost"
    assert out.index("My kak raak") < out.index("Ek dink ons"), "saved file not in time order"
    assert not p.with_name(p.name + ".tmp").exists(), ".tmp left behind"
    print("  OK  MarkdownSink saves the file clean (echo gone) and in chronological order")


def test_preexisting_file_is_not_clobbered():
    # If the path already has content, the sink is appending to data it does not own and must
    # NOT rewrite the whole file (which would delete the prefix). Data safety beats de-dup here.
    import tempfile
    from pathlib import Path
    from live_transcribe.sinks import MarkdownSink

    p = Path(tempfile.mkdtemp()) / "existing.md"
    p.write_text("PRE-EXISTING CONTENT\n", encoding="utf-8")
    sink = MarkdownSink(p)
    sink(_Seg("SYS", 1.0, "alpha beta gamma delta"))
    sink(_Seg("MIC", 2.0, "alpha beta gamma delta"))   # would echo, but no rewrite happens
    sink.close()
    out = p.read_text(encoding="utf-8")
    assert "PRE-EXISTING CONTENT" in out, "pre-existing transcript content was destroyed"
    print("  OK  a pre-existing file is appended to, never rewritten/clobbered")


if __name__ == "__main__":
    failures = 0
    for fn in (test_strip_mic_echoes_both_orders, test_no_sys_means_no_change,
               test_strips_garbled_speaker_echo, test_fuzzy_share_count_is_one_to_one,
               test_fuzzy_match_is_optimal_not_exact_first,
               test_keeps_short_genuine_reply_sharing_words, test_markers_and_notices_are_kept,
               test_markdown_sink_rewrites_clean_and_ordered, test_preexisting_file_is_not_clobbered):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll dedup tests passed.")
