"""Tests for the transcript clean-up pass (live_transcribe/clean.py).

The clean-up sends only spoken TEXT to the model and must never alter timestamps,
speaker labels, or the number/order of lines. These tests stub the model (no GGUF load)
and prove the structural guarantees: identity is a no-op, a real correction is applied
while prefixes stay byte-identical, system markers are never sent, and an untrustworthy
model response (wrong count / garbage) falls back to the original text.

Run:  python tests/test_clean.py   (from the project root; exit 0 = pass)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import clean

TRANSCRIPT = (
    "# Volksmond session\n\n"
    "- Started: 2026-06-09T10:00:00\n"
    "- File: `x.md`\n"
    "- Format: `[mm:ss] [SOURCE] text`, where `MIC` is your microphone and `SYS` is everyone else\n\n"
    "---\n\n"
    "[00:00] [MIC] hallo daar almal\n"
    "[00:03] [SYS] [… ~2 chunk(s) not transcribed, transcriber fell behind …]\n"
    "[00:05] [MIC] ek dink ons moet begin\n"
    "[00:09] [SYS] ja laat ons\n"
    "\n---\n\n"
    "_End of session._\n"
)


def _input_lines(prompt):
    """Pull the numbered lines the cleaner sent to the model out of its prompt."""
    body = prompt.split("LINES:\n", 1)[1]
    pairs = []
    for line in body.splitlines():
        m = re.match(r"^(\d+)\.\s?(.*)$", line)
        if m:
            pairs.append((int(m.group(1)), m.group(2)))
    return pairs


def _gen_identity(prompt):
    return "\n".join("%d. %s" % (n, t) for n, t in _input_lines(prompt))


def _gen_upper(prompt):
    return "\n".join("%d. %s" % (n, t.upper()) for n, t in _input_lines(prompt))


def _gen_wrong_count(prompt):
    pairs = _input_lines(prompt)
    return "\n".join("%d. %s" % (n, t) for n, t in pairs[:-1])  # drop the last line


def _gen_garbage(prompt):
    return "Sure! Here is the cleaned transcript you asked for."


def test_identity_is_noop():
    out = clean.clean_transcript(_gen_identity, TRANSCRIPT)
    assert out == TRANSCRIPT, "identity clean changed the transcript"
    print("  OK  identity clean is a byte-for-byte no-op")


def test_correction_applied_prefixes_untouched():
    out = clean.clean_transcript(_gen_upper, TRANSCRIPT)
    # The spoken text is uppercased...
    assert "[00:00] [MIC] HALLO DAAR ALMAL\n" in out, out
    assert "[00:09] [SYS] JA LAAT ONS\n" in out, out
    # ...but every timestamp + speaker prefix is byte-identical, and the system marker
    # line (text starting with "[") was never sent, so it is unchanged.
    for prefix in ("[00:00] [MIC] ", "[00:05] [MIC] ", "[00:09] [SYS] "):
        assert prefix in out, prefix
    assert "[00:03] [SYS] [… ~2 chunk(s) not transcribed, transcriber fell behind …]\n" in out, \
        "a system marker line was altered or sent to the model"
    # Header + footer preserved.
    assert out.startswith("# Volksmond session\n"), "header changed"
    assert out.rstrip().endswith("_End of session._"), "footer changed"
    print("  OK  correction applied to text only; timestamps/speakers/markers untouched")


def test_wrong_count_falls_back():
    out = clean.clean_transcript(_gen_wrong_count, TRANSCRIPT)
    # A short/garbled model response is not trusted: the chunk keeps its original text.
    assert out == TRANSCRIPT, "wrong-count model response was trusted instead of falling back"
    print("  OK  wrong line count -> falls back to the original text")


def test_garbage_falls_back():
    out = clean.clean_transcript(_gen_garbage, TRANSCRIPT)
    assert out == TRANSCRIPT, "unparseable model response was trusted instead of falling back"
    print("  OK  unparseable response -> falls back to the original text")


def test_split_roundtrips():
    header, body, footer = clean.split_transcript(TRANSCRIPT)
    assert header.endswith("---\n"), header[-20:]
    assert "[00:00] [MIC] hallo daar almal" in body, "body missing speech"
    assert "_End of session._" in footer, footer
    assert header + body + footer == TRANSCRIPT, "split did not round-trip"
    print("  OK  split_transcript round-trips (header + body + footer == original)")


if __name__ == "__main__":
    failures = 0
    for fn in (test_split_roundtrips, test_identity_is_noop,
               test_correction_applied_prefixes_untouched,
               test_wrong_count_falls_back, test_garbage_falls_back):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll clean tests passed.")
