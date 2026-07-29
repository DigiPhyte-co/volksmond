"""Unit tests for the user-prompt-leak filter (WP-1): PromptLeakMatcher.

The leak shapes below are VERBATIM lines from the 2026-07-29 incident transcript (36 min
English call, large-v3, initial_prompt="Danica Freimond, Sean Freimond"), which produced 90
prompt-regurgitation lines. The "kept" cases are the false-positive discipline: a name spoken
in real speech, and - the critical one - ordinary Afrikaans made of AF anchor words.

No model load, no audio. Run:  python tests/test_prompt_leak.py   (exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import transcribe as T

PROMPT = "Danica Freimond, Sean Freimond"


def m(user_prompt=PROMPT, anchor=None):
    return T.PromptLeakMatcher(user_prompt, anchor)


# --- Mode A: the five real leak shapes ---
def test_leak_pure_repeat():
    # coverage 1.00, and "Sean Freimond" twice -> A1 and A2 both fire
    assert m().is_leak("Danica Freimond, Sean Freimond, Sean Freimond,") is True

def test_leak_fragment_with_filler_and():
    assert m().is_leak("and Danica Freimond.") is True

def test_leak_fragment_with_filler_yeah():
    assert m().is_leak("Danica Freimond, Sean Freimond, yeah.") is True

def test_leak_bare_unit():
    assert m().is_leak("Sean Freimond") is True

def test_leak_repeat_beats_coverage():
    # A2 alone: below A1's 0.80 coverage floor, but the same unit appears twice AND still
    # accounts for most of the segment (0.67 >= _LEAK_REPEAT_COVERAGE).
    assert m().is_leak("Sean Freimond, Sean Freimond, okay thanks everyone") is True


# --- F2a: the repeat shortcut needs coverage too ---
def test_repeat_shortcut_needs_coverage():
    """A2 used to fire on ANY unit repeated twice, whatever else the segment contained, so a
    speaker CORRECTING a mis-heard name (which necessarily says it twice) was deleted. The
    repeat now has to be most of the segment as well."""
    # the observed leak: nothing but prompt units, coverage 1.00 -> still dropped
    assert m().is_leak("Danica Freimond, Sean Freimond, Sean Freimond,") is True
    # a genuine correction: the unit twice, but only ~0.50 coverage -> kept
    assert m().is_leak("I said Sean Freimond, not Shawn Freemont, Sean Freimond") is False
    # the old A2-only case: two mentions inside a sentence (coverage 0.44) is no longer a leak
    assert m().is_leak("So I was saying to Sean Freimond and Sean Freimond about the thing") is False


# --- F7: leaks that start MID-unit (real-audio validation gap) ---
# Whisper enters the prompt wherever the decoder lands, so the leading unit arrives as a
# dangling half. Whole-unit matching alone covers only the intact unit; once ANY whole unit
# has matched, the prompt's unit vocabulary counts as covered too. Arithmetic per case below
# (this prompt's unit vocabulary = {danica, freimond, sean}).
def test_leak_starting_mid_unit():
    # "freimond sean freimond": the unit match covers 2/3, the vocabulary covers the dangling
    # leading "Freimond" -> 3/3 = 1.00 >= 0.80. This fixture fails without F7 (0.667).
    assert m().is_leak("Freimond, Sean Freimond") is True

def test_leak_starting_mid_unit_with_filler():
    # "yeah" is a filler, so the content tokens are the same three -> 3/3 = 1.00
    assert m().is_leak("Freimond, Sean Freimond, yeah.") is True

def test_mid_unit_vocabulary_does_not_widen_a_correction():
    # 8 content tokens, 4 of them (sean/freimond twice) in the vocabulary -> 0.50 < 0.60, so
    # the A2 repeat floor still keeps a spoken correction. F7 adds no coverage here at all.
    assert m().is_leak("I said Sean Freimond, not Shawn Freemont, Sean Freimond") is False

def test_exclamation_around_a_name_kept():
    # "oh sean freimond": "oh" is neither a filler nor vocabulary -> 2/3 = 0.667 < 0.80
    assert m().is_leak("Oh, Sean Freimond!") is False

def test_bare_single_vocabulary_token_kept():
    # No WHOLE unit matches, so the vocabulary expansion never arms and a bare surname stays.
    # Deliberate: one word is not enough evidence to delete a line, and the real answer for it
    # is the audio-evidence gate in a later work package, not a wider text rule.
    assert m().is_leak("Freimond") is False
    assert m().is_leak("Freimond.") is False


# --- Mode A: what must survive ---
def test_embedded_prose_kept():
    # The fabricated "keynote speaker" line: prompt tokens embedded in prose. Coverage ~0.13.
    # Correctly kept at this layer - separating it from real speech needs audio evidence (A3).
    assert m().is_leak(
        "I am here with Sean Freimond, who is going to be our keynote speaker for this session."
    ) is False

def test_genuine_name_mention_kept():
    assert m().is_leak("Sean, can you send that through?") is False

def test_one_word_prompt_term_does_not_eat_one_word_line():
    # min-content-tokens rule: a single-token unit may not drop a single-token real line
    assert T.PromptLeakMatcher("Budget").is_leak("Budget.") is False

def test_normal_speech_kept():
    assert m().is_leak("we should finalise the numbers before Friday") is False


# --- Mode B: the AF anchor, n-gram matched only ---
def test_anchor_wordlist_leak_dropped():
    assert m(user_prompt=None, anchor=T.AF_ANCHOR_PROMPT).is_leak(
        "Algemene woorde: baie, nogal, lekker, kuier") is True

def test_anchor_opening_leak_dropped():
    assert m(user_prompt=None, anchor=T.AF_ANCHOR_PROMPT).is_leak(
        "Ons praat Suid-Afrikaanse Afrikaans, nie Nederlands nie.") is True

def test_real_afrikaans_made_of_anchor_words_kept():
    # THE anti-regression: every one of these words is in the anchor. Token-membership
    # matching would delete it; contiguous n-gram matching must not.
    mm = m(user_prompt=None, anchor=T.AF_ANCHOR_PROMPT)
    assert mm.is_leak("ons kinders is baie lekker vandag") is False
    assert mm.is_leak("die kollegas kuier lekker by die vergadering") is False
    assert mm.is_leak("baie dankie julle, ons praat more weer") is False

def test_anchor_ngram_needs_coverage():
    """F1: a single contiguous 5-gram used to be enough to delete a segment. The anchor is
    ordinary Afrikaans, so a real sentence can legitimately contain one of its 5-grams and
    still be mostly the speaker's own words. The matched spans must now cover most of the
    segment's content tokens."""
    mm = m(user_prompt=None, anchor=T.AF_ANCHOR_PROMPT)
    # verbatim anchor regurgitation: the matched spans are the whole line (coverage ~1.0)
    assert mm.is_leak("net soos dit gepraat word. Ons praat Suid-Afrikaanse Afrikaans") is True
    assert mm.is_leak("Algemene woorde: baie, nogal, lekker, kuier") is True
    # genuine speech that happens to contain the anchor's "net soos dit gepraat word"
    # 5-gram but is mostly its own words (coverage ~0.57) -> kept
    assert mm.is_leak("Dit moet net soos dit gepraat word neergeskryf word") is False


# --- F2b: a free-form context sentence is n-gram matched, never unit matched ---
# /api/start concatenates the saved default_context into the SAME prompt string as the
# names (web/app.py:635), so an instruction sentence arrives here as one "unit".
CONTEXT = "Please transcribe the meeting exactly as spoken"

def test_long_unit_does_not_eat_speech_sharing_its_words():
    # 5 of the sentence's words, contiguous, but only ~0.45 of the segment -> kept
    assert T.PromptLeakMatcher(CONTEXT).is_leak(
        "we should transcribe the meeting exactly as spoken by the client on Friday") is False

def test_long_unit_still_eats_its_own_regurgitation():
    assert T.PromptLeakMatcher(CONTEXT).is_leak("Please transcribe the meeting exactly as spoken.") is True

def test_long_unit_repeated_is_not_an_automatic_leak():
    # the A2 repeat shortcut must not apply to a long unit at all: this is one speaker
    # restating an instruction, and the prompt words are ~0.57 of the segment
    assert T.PromptLeakMatcher(CONTEXT).is_leak(
        "Please transcribe the meeting exactly as spoken. That is what the client asked for "
        "on the call this morning, so please transcribe the meeting exactly as spoken."
    ) is False

def test_context_sentence_and_names_in_one_prompt():
    # the real shape: default_context + the user's names, comma-joined by /api/start.
    mm = T.PromptLeakMatcher(f"{CONTEXT}, Danica Freimond, Sean Freimond")
    assert mm.is_leak("Sean Freimond") is True                 # short unit: Mode A, unchanged
    assert mm.is_leak("Danica Freimond, Sean Freimond, yeah.") is True
    assert mm.is_leak("Please transcribe the meeting exactly as spoken.") is True
    assert mm.is_leak(
        "we should transcribe the meeting exactly as spoken by the client on Friday") is False


def test_anchor_and_user_prompt_together():
    mm = m(user_prompt=PROMPT, anchor=T.AF_ANCHOR_PROMPT)
    assert mm.is_leak("Sean Freimond") is True
    assert mm.is_leak("Algemene woorde: baie, nogal, lekker, kuier") is True
    assert mm.is_leak("ons kinders is baie lekker vandag") is False


# --- long-prompt safety valve: n-gram only above 60 tokens ---
LONG = (
    "Agenda for the quarterly review meeting with the operations team covering the budget "
    "position, the hiring plan for the next two quarters, the status of the migration project, "
    "outstanding client escalations, the renewal pipeline, our supplier contracts, staff "
    "training commitments, the compliance audit findings, and any other business raised by "
    "the attendees before we close the session and circulate the notes to everyone involved"
)

def test_long_prompt_verbatim_run_dropped():
    assert len(T._norm_tokens(LONG)) > T._LEAK_LONG_PROMPT
    assert T.PromptLeakMatcher(LONG).is_leak(
        "the status of the migration project, outstanding client escalations") is True

def test_long_prompt_does_not_unit_match():
    # Same vocabulary, not a verbatim run -> kept. Under Mode A this would score high coverage.
    assert T.PromptLeakMatcher(LONG).is_leak("the budget position") is False
    assert T.PromptLeakMatcher(LONG).is_leak("any other business") is False


# --- inert matcher ---
def test_empty_prompt_inert():
    for p in (None, "", "   ", ",,"):
        mm = T.PromptLeakMatcher(p)
        assert mm.is_leak("Danica Freimond, Sean Freimond,") is False
        assert mm.is_leak("anything at all") is False

def test_empty_text_never_leaks():
    assert m().is_leak("") is False
    assert m().is_leak("...") is False


# --- engine seam: the matcher is rebuilt when the prompt/language changes ---
class _FakeEngine:
    """Exercises Engine._rebuild_prompt_leak without loading a model."""
    _rebuild_prompt_leak = T.Engine._rebuild_prompt_leak

def test_rebuild_drops_anchor_on_language_switch():
    e = _FakeEngine()
    e._rebuild_prompt_leak(PROMPT, "af")
    assert e._prompt_leak.is_leak("Algemene woorde: baie, nogal, lekker, kuier") is True
    # the 13:21 af -> en switch recomposes the prompt down to names alone
    e._rebuild_prompt_leak(PROMPT, "en")
    assert e._prompt_leak.is_leak("Algemene woorde: baie, nogal, lekker, kuier") is False
    assert e._prompt_leak.is_leak("Danica Freimond, Sean Freimond, yeah.") is True

def test_rebuild_on_new_user_prompt():
    e = _FakeEngine()
    e._rebuild_prompt_leak(PROMPT, "en")
    assert e._prompt_leak.is_leak("Sean Freimond") is True
    e._rebuild_prompt_leak("Ashley Muller", "en")
    assert e._prompt_leak.is_leak("Sean Freimond") is False
    assert e._prompt_leak.is_leak("Ashley Muller.") is True


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"\nall {len(tests)} prompt-leak tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
