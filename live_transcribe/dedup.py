"""Echo de-dup helpers, shared by the live engine and the end-of-session cleanup.

When a meeting plays over SPEAKERS (not headphones), the microphone re-hears the far side,
so Whisper transcribes the same words twice: once on SYS (the clean loopback) and again,
garbled, on MIC. We drop the MIC copy. This is safe because the user's own voice never
reaches the system-audio loopback, so a MIC line that matches a near-simultaneous SYS line
can only be echo, never something the user actually said. Headphones avoid the echo
entirely; this is the speaker-case backstop.

Two callers use these helpers:
  - the live engine (transcribe.py) drops a MIC segment as it is about to be published, by
    matching it against the SYS segments seen so far (it delays MIC slightly so the SYS copy
    is almost always seen first);
  - the file sink (sinks.py) runs strip_mic_echoes once at the end, over the full set, which
    is order-independent and catches anything the live pass missed.
"""
import re
from difflib import SequenceMatcher

# Echo matching is tuned to catch the GARBLED echoes Whisper produces: the mic mishears the
# speaker, so the MIC and SYS copies of the same sentence share only ~40-60% of their EXACT
# words ("dinsdag"->"dansdag", "pensel"->"pencil", "phone"->"voorn"). Plain exact-word overlap
# is too weak, so we also count NEAR-SPELLED words as shared (fuzzy). To avoid deleting real
# speech we only act on MIC lines of several words, require several shared words, and require
# those to be a real fraction of the shorter line. A short reply, or one sharing only a word or
# two, is always kept.
#
# RESIDUAL RISK (accepted, by design): a long genuine reply that repeats 4+ of the far side's
# words verbatim ("...start next month" right after "...start next quarter") can still be
# dropped. That is the trade for catching the pervasive speaker echo. Headphones avoid echo
# entirely and carry zero risk; this is only the speaker-case backstop. Tune the constants here.
FUZZY_RATIO = 0.78          # difflib ratio at/above which two words count as the same word
ECHO_WINDOW_SECONDS = 6.0   # max start-time gap to treat a MIC line as an echo of a SYS line
ECHO_MIN_TOKENS = 4         # ignore MIC lines of 3 words or fewer (short replies stay safe)
ECHO_MIN_SHARED = 4         # require at least this many shared / near-spelled words
ECHO_OVERLAP = 0.5          # and that they are at least this fraction of the shorter line


def tokens(text):
    """Word set for comparison: lowercased, punctuation stripped."""
    return set(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _synthetic(text):
    """Markers and engine notices are bracketed lines ("[… …]"); never treat them as speech
    for echo matching, in either direction."""
    return text.lstrip().startswith("[")


def _shared_count(mic_tokens, sys_tokens):
    """Maximum number of MIC words that pair one-to-one with a SYS word, where a pair is an
    EXACT match or a near-spelling (the mic's mis-hearings). Computed as a maximum bipartite
    matching (augmenting paths) over sorted word lists: deterministic (independent of set hash
    order), one-to-one (a SYS word is never reused), and optimal (no greedy/exact-first
    under- or over-count). `==` short-circuits before the fuzzy ratio, so exact pairs stay cheap."""
    mic = sorted(mic_tokens)
    sys = sorted(sys_tokens)
    adj = [[j for j, sw in enumerate(sys)
            if mw == sw or SequenceMatcher(None, mw, sw).ratio() >= FUZZY_RATIO]
           for mw in mic]
    match_sys = [-1] * len(sys)   # sys index -> matched mic index, or -1

    def _augment(u, seen):
        for j in adj[u]:
            if not seen[j]:
                seen[j] = True
                if match_sys[j] == -1 or _augment(match_sys[j], seen):
                    match_sys[j] = u
                    return True
        return False

    count = 0
    for u in range(len(mic)):
        if _augment(u, [False] * len(sys)):
            count += 1
    return count


def is_echo(mic_tokens, t_start, sys_index):
    """True if a MIC segment (`mic_tokens` at `t_start`) is a near-copy of a near-simultaneous
    SYS segment. `sys_index` is any iterable of (t_start, token_set) for SYS segments. Counts
    near-spelled words as shared (see the threshold notes above) so garbled echoes match, while
    the multi-word and shared-word floors keep brief genuine replies."""
    if len(mic_tokens) < ECHO_MIN_TOKENS:
        return False
    for s_start, s_tokens in sys_index:
        if abs(t_start - s_start) > ECHO_WINDOW_SECONDS or len(s_tokens) < ECHO_MIN_TOKENS:
            continue
        shared = _shared_count(mic_tokens, s_tokens)
        if shared >= ECHO_MIN_SHARED and shared / min(len(mic_tokens), len(s_tokens)) >= ECHO_OVERLAP:
            return True
    return False


def strip_mic_echoes(segments):
    """Return `segments` with MIC lines that echo a near-simultaneous SYS line removed.

    `segments` is any iterable of objects with `.source`, `.t_start`, and `.text`. Input order
    is preserved (pass it pre-sorted by t_start for a clean chronological result). SYS lines,
    genuine MIC speech, and synthetic marker/notice lines are always kept. Order-independent:
    every MIC line is compared against ALL SYS lines in the time window, so it does not matter
    which channel finished first.
    """
    segments = list(segments)
    sys_index = [(s.t_start, tokens(s.text)) for s in segments
                 if s.source == "SYS" and not _synthetic(s.text)]
    if not sys_index:
        return segments  # nothing to echo against (e.g. an imported single-source file)
    kept = []
    for seg in segments:
        if (seg.source == "MIC" and not _synthetic(seg.text)
                and is_echo(tokens(seg.text), seg.t_start, sys_index)):
            continue  # mic re-heard the speakers; SYS already has this line
        kept.append(seg)
    return kept
