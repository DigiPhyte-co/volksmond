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
    # A2 alone: coverage is low but the same unit appears twice in one short segment
    assert m().is_leak("So I was saying to Sean Freimond and Sean Freimond about the thing") is True


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
