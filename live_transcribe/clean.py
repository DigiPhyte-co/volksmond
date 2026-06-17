r"""Local, offline transcript clean-up: the "high quality" pass for recordings.

After a recording is transcribed, this optional pass sends the raw transcript through
the same on-device Gemma model (modeldl.py / summarise.py) to fix ONLY clear speech-
recognition errors: Afrikaans wrongly spelled as Dutch, punctuation, capitalisation,
obviously mis-heard words, and leftover repeated/hallucinated filler. It never
paraphrases, translates, summarises, reorders, or invents, and the RAW transcript is
always kept next to the cleaned one.

That guarantee is STRUCTURAL, not just a prompt instruction:
  - the model only ever sees the spoken TEXT, never the `[mm:ss] [SOURCE]` prefixes, so
    it cannot alter a timestamp or a speaker label;
  - text is sent one numbered line at a time and the model must return the SAME number
    of lines; on any mismatch (or unparseable output) we keep the ORIGINAL text for that
    chunk, so a line can never be dropped, merged, split, or reordered by the model;
  - system markers (e.g. "[... N chunks not transcribed ...]") and blank/non-segment
    lines are passed through untouched.

The transcript format is the one MarkdownSink writes: a header, a `---` separator, body
lines `[mm:ss] [SOURCE] text`, then a trailing `---` / end-of-session footer.
"""
import re

from .summarise import TRANSCRIPT_NOTE

# A body line: "[mm:ss] [SRC] text". Capture the "[mm:ss] [SRC] " prefix and the text.
_SEG_RE = re.compile(r"^(\[\d{1,2}:\d{2}\]\s+\[[A-Z]+\]\s+)(.*)$")
# A numbered line the model returns: "12. corrected text" (also tolerate "12) ").
_NUM_RE = re.compile(r"^\s*(\d+)[.)]\s?(.*)$")

CLEAN_INSTRUCTION = (
    "You are correcting raw speech-recognition output, line by line. For each numbered "
    "line, fix ONLY clear transcription errors: spelling (especially Afrikaans wrongly "
    "spelled as Dutch), punctuation, capitalisation, and words that were obviously mis-"
    "heard where the intended word is clear from context. Remove only repeated or "
    "hallucinated filler. Do NOT paraphrase, translate, summarise, reorder, merge, "
    "split, add, or remove lines, and do not change the meaning. Return EXACTLY the same "
    "numbered lines, the same count, in the same order, each containing only the "
    "corrected text. If a line is already correct, or you are unsure, return it unchanged."
)


def split_transcript(text):
    """Split a transcript into (header, body, footer).

    header includes the leading `---` separator; footer is the trailing `---` /
    end-of-session block. If no separators are found, the whole thing is treated as body
    so cleanup still degrades gracefully."""
    head, sep, rest = text.partition("\n---\n")
    if not sep:
        return "", text, ""
    header = head + sep
    body, sep2, foot = rest.rpartition("\n---\n")
    if sep2:
        return header, body, sep2 + foot
    return header, rest, ""


def _parse_body(body):
    """Turn the body into a list of items: each is either {'raw': line} (passed through
    verbatim) or {'prefix': p, 'text': t} (a segment whose text may be cleaned)."""
    items = []
    for line in body.splitlines(keepends=True):
        nl = "\n" if line.endswith("\n") else ""
        bare = line[:-1] if nl else line
        m = _SEG_RE.match(bare)
        # Clean real speech only: a segment whose text does not start with "[" (which
        # would be a system marker / notice like "[engine: ...]" or "[... N chunks ...]").
        if m and not m.group(2).lstrip().startswith("["):
            items.append({"prefix": m.group(1), "text": m.group(2), "nl": nl})
        else:
            items.append({"raw": line})
    return items


def _render(items):
    out = []
    for it in items:
        if "raw" in it:
            out.append(it["raw"])
        else:
            out.append(it["prefix"] + it["text"] + it["nl"])
    return "".join(out)


def _clean_chunk(generate, texts, language=None):
    """Ask the model to correct a chunk of spoken-text lines. Returns a list of the same
    length, or None if the output cannot be trusted (so the caller keeps the originals)."""
    lang = {"af": "Afrikaans", "en": "English"}.get(language or "")
    lang_note = (
        ("\n\nThe speech is mostly " + lang + " mixed with the other language; keep each "
         "line in the language it was spoken, do not translate.") if lang else ""
    )
    numbered = "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(texts))
    prompt = (CLEAN_INSTRUCTION + lang_note + "\n\n" + TRANSCRIPT_NOTE +
              "\n\nLINES:\n" + numbered)
    try:
        out = generate(prompt)
    except Exception:
        return None
    by_num = {}
    for line in (out or "").splitlines():
        m = _NUM_RE.match(line)
        if m:
            by_num[int(m.group(1))] = m.group(2).rstrip()
    # Trust the result only if every line came back, in range, exactly once.
    if len(by_num) != len(texts) or any((i + 1) not in by_num for i in range(len(texts))):
        return None
    return [by_num[i + 1] for i in range(len(texts))]


def clean_transcript(generate, transcript, language=None, chunk_lines=30):
    """Return a cleaned copy of `transcript`. `generate(prompt) -> str` is the LLM call
    (the caller bakes in the model + max_tokens). Structure is preserved exactly; only
    segment TEXT can change, and only when the model returns a trustworthy result."""
    if not transcript or not transcript.strip():
        return transcript
    header, body, footer = split_transcript(transcript)
    items = _parse_body(body)
    cleanable = [i for i, it in enumerate(items) if "text" in it]
    for start in range(0, len(cleanable), chunk_lines):
        idxs = cleanable[start:start + chunk_lines]
        originals = [items[i]["text"] for i in idxs]
        cleaned = _clean_chunk(generate, originals, language)
        if not cleaned or len(cleaned) != len(originals):
            continue  # safety: keep the originals for this chunk
        for i, txt in zip(idxs, cleaned):
            if txt.strip():            # never let a correction blank a line out
                items[i]["text"] = txt
    return header + _render(items) + footer
