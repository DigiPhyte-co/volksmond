"""faster-whisper engine with tier-based config and subscriber fan-out.

Single transcription worker thread, single model, single chunk queue.
Chunks from both mic and system loopback go through serially, keeps GPU
memory at one model's footprint and simplifies the data flow. If GPU under-
utilisation becomes a problem in V1 with a snappier chunk size, we can run
two model instances; not worth it for V0.
"""
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

from faster_whisper import WhisperModel


# Tiers. compute_type matters as much as the model:
#  - "gpu"     large-v3 float16       , needs ~3GB+ VRAM (RTX 3090 etc.)
#  - "gpu-4gb" large-v3 int8_float16  , fits a 4GB card (GTX 1650 Mobile);
#                                        near-float16 quality, int8 tensor cores
#  - cpu tiers, fallback only. CPU ASR is memory-bandwidth-bound, so it can run
#                slower than real-time while CPU usage looks moderate. Prefer a
#                GPU tier whenever a CUDA device exists.
TIER_CONFIG = {
    "gpu":        {"model": "large-v3",       "device": "cuda", "compute_type": "float16"},
    "gpu-4gb":    {"model": "large-v3",       "device": "cuda", "compute_type": "int8_float16"},
    # CPU tiers set the STARTING model. On CPU the engine measures its real-time
    # factor each chunk and auto-downgrades along CPU_LADDER if it can't keep up
    # (see _maybe_downgrade), so a fast CPU keeps the bigger model and a slow one
    # ratchets down on its own. Start ambitious; let it self-correct. Pair with
    # --keep-audio + a post-meeting large-v3 pass for the canonical transcript.
    "cpu":        {"model": "small",          "device": "cpu",  "compute_type": "int8"},
    "cpu-min":    {"model": "base",           "device": "cpu",  "compute_type": "int8"},
    "cpu-strong": {"model": "large-v3-turbo", "device": "cpu",  "compute_type": "int8"},
    "cpu-mid":    {"model": "medium",         "device": "cpu",  "compute_type": "int8"},
}

# Anti-Dutch anchor for Afrikaans transcription.
#
# Whisper's training data has Dutch heavily represented and Afrikaans sparsely.
# The model often outputs Dutch-flavoured spellings even when language="af" is
# forced. The initial_prompt is conditioning text, Whisper biases toward the
# vocabulary and style it contains. Stuffing it with distinctly-Afrikaans
# words and grammar pulls the output away from Dutch.
#
# Picked for maximum signal-per-token: pronouns and conjunctions that ARE
# different between the two languages (julle/hulle, nie...nie double-negative,
# baie/nogal/lekker), plus everyday SA business vocabulary.
AF_ANCHOR_PROMPT = (
    "Dit is 'n gesprek in Afrikaans. Ons praat Suid-Afrikaanse Afrikaans, "
    "nie Nederlands nie. Algemene woorde: baie, nogal, lekker, kuier, sjoe, "
    "eish, vandag, môre, gister, dankie tog, asseblief, julle, hulle, ons, "
    "kinders, kollegas, vergadering, besigheid."
)

# Hallucination guards. Whisper invents text on silence, noise, and low-
# confidence audio. CPU testing showed two failure modes: (a) a single token
# repeated on near-silence ("Hekkaan." x20), and (b) looping phrases
# ("s'apere s'apere s'apere"). These decoder thresholds let Whisper detect and
# suppress its own low-confidence output; _collapse_repetition below is a
# backstop for loops that slip past the thresholds.
#
# condition_on_previous_text=False is the most important one: it stops the model
# conditioning each window on its own previous (possibly hallucinated) output,
# which is the engine that drives runaway loops.
GUARD = dict(
    condition_on_previous_text=False,
    no_speech_threshold=0.6,
    compression_ratio_threshold=2.4,   # repetitive text compresses well -> reject
    log_prob_threshold=-1.0,           # low average logprob = guessing -> reject
    temperature=[0.0, 0.2, 0.4],       # fallback decoding when a chunk trips a threshold
)

# When the backlog exceeds this many chunks, drop beam_size to 1 for the next
# chunk so transcription speeds up and catches back up to real-time. Trades a
# little per-chunk accuracy for not falling behind (and eventually dropping).
# This is the FAST, within-model response; model downgrade (below) is the
# heavier lever if beam-cutting isn't enough.
BACKPRESSURE_BEAM_THRESHOLD = 6

# CPU adaptive model ladder (highest-quality -> fastest). When a CPU start model
# can't hold real-time, the engine steps DOWN this ladder until it keeps up -
# never back up (avoids oscillation). large-v3/turbo are deliberately NOT in the
# ladder: too slow to be a sane CPU live floor. `tiny` is the last-resort rung -
# rough, but guarantees real-time on almost anything, which still beats dropping
# audio. GPU tiers never downgrade (they keep up).
CPU_LADDER = ["medium", "small", "base", "tiny"]
DOWNGRADE_RTF = 0.95     # rolling real-time factor above this = not keeping up
DOWNGRADE_WINDOW = 4     # consecutive chunks of evidence required before a step down


def _collapse_repetition(text, max_run=3):
    """Collapse pathological consecutive-token loops on bad audio.

    "Hekkaan. Hekkaan. Hekkaan. ..." -> "Hekkaan." Legitimate emphasis like
    "baie baie baie" (a run of 3) is left alone; only runs LONGER than max_run
    identical consecutive tokens are collapsed to a single token.
    """
    words = text.split()
    if len(words) <= max_run:
        return text
    out = []
    run = 1
    for i, w in enumerate(words):
        norm = w.lower().strip(".,!?;:")
        prev = words[i - 1].lower().strip(".,!?;:") if i > 0 else None
        if norm and norm == prev:
            run += 1
        else:
            run = 1
        if run <= max_run:
            out.append(w)
    return " ".join(out)


# Whisper's training data is saturated with YouTube subtitle credits and video
# end-cards. On silence or noise it reproduces these verbatim and CONFIDENTLY, so
# the decoder confidence thresholds (which only catch low-confidence guessing) miss
# them entirely. This is a precise blocklist of phrases that are never real meeting
# speech. The big offender in SA testing is the Amara.org subtitle credit (often in
# Dutch-flavoured spelling, e.g. "Ondertitels ingediend door die Amara.org gemeenskap").
#
# It also catches the AF_ANCHOR_PROMPT regurgitating itself: on silence Whisper can
# emit the initial_prompt verbatim, which showed up on the silent mic channel as the
# "Algemene woorde: baie, nogal, lekker, ..." word list. If you change the anchor's
# word list, keep the two anchor-leak patterns below in sync with it.
_HALLUCINATION_RE = re.compile(
    r"amara\.org"
    r"|\bondertitel\w*\b.*\b(gemeenskap|gemeenschap|amara)"
    r"|\buntertitel\w*\b.*amara"
    r"|\balgemene woorde[:,]"           # AF_ANCHOR_PROMPT list header leaking
    r"|\bbaie,\s*nogal,\s*lekker",      # AF_ANCHOR_PROMPT word-list leaking
    re.IGNORECASE,
)

# Video end-cards. Their words DO appear in real speech ("our subscribe page",
# "thanks for watching the demo"), so they are junk only when they ARE the whole
# segment (compared after stripping punctuation). Bare "subscribe" in a sentence is
# left alone.
_ENDCARD_PHRASES = frozenset({
    "thanks for watching", "thank you for watching",
    "please subscribe", "like and subscribe", "dont forget to subscribe",
    "subscribe to my channel", "subscribe to the channel",
    "dankie vir die kyk", "bedankt voor het kijken",
})


def _is_hallucination(text):
    """True for known Whisper junk (subtitle credits, the anchor-prompt leak, and
    video end-cards). These slip past the confidence thresholds because the model emits
    them confidently, so they need explicit phrase matching rather than a threshold."""
    if _HALLUCINATION_RE.search(text):
        return True
    bare = re.sub(r"[^\w\s]", "", text).strip().lower()
    return bare in _ENDCARD_PHRASES


@dataclass
class Segment:
    source: str       # "MIC" or "SYS"
    t_start: float    # session-relative seconds
    t_end: float
    text: str


class Engine:
    def __init__(self, tier, language="af", initial_prompt=None, cpu_threads=8, beam_size=5, adaptive=True):
        if tier not in TIER_CONFIG:
            raise ValueError(f"Unknown tier {tier!r}; choose from {list(TIER_CONFIG)}")
        self.tier = tier
        self.language = language
        self.beam_size = beam_size
        # adaptive=True (live): cut beam + downgrade the model under backlog to keep
        # up with real time. adaptive=False (file import): not real time, so never
        # trade quality for speed - keep the chosen model and full beam size.
        self.adaptive = adaptive

        # Apply the Afrikaans anchor when transcribing in af. User prompt (if any)
        # follows the anchor so client-specific terms still bias the model.
        if language == "af":
            user = (initial_prompt or "").strip()
            self.initial_prompt = f"{AF_ANCHOR_PROMPT} {user}".strip() if user else AF_ANCHOR_PROMPT
        else:
            self.initial_prompt = initial_prompt
        cfg = TIER_CONFIG[tier]
        self.model_name = cfg["model"]

        kw = dict(device=cfg["device"], compute_type=cfg["compute_type"])
        if cfg["device"] == "cpu":
            kw["cpu_threads"] = cpu_threads
            kw["num_workers"] = 1

        self.model = WhisperModel(cfg["model"], **kw)
        self._is_cpu = cfg["device"] == "cpu"
        self._compute_type = cfg["compute_type"]
        self._cpu_threads = cpu_threads
        self._rtf = deque(maxlen=DOWNGRADE_WINDOW)  # recent real-time factors (CPU downgrade)
        self.subscribers = []
        self._queue = queue.Queue(maxsize=32)
        self._stop = threading.Event()    # shutting down: stop accepting new audio
        self._abort = threading.Event()   # hard abort: discard the backlog instead of draining
        self._busy = False                # True while a chunk is mid-transcription (for pending())
        self._dropped = 0                 # chunks dropped to backpressure since last reported
        self._worker = threading.Thread(target=self._run, daemon=True, name="transcribe")

    def subscribe(self, fn):
        self.subscribers.append(fn)

    def start(self):
        self._worker.start()

    def stop(self, drain=True, timeout=None):
        """Stop the worker.

        drain=True (default): finish transcribing everything already queued
        before exiting, so the tail of the session is not lost. Blocks until the
        backlog is drained, which can take a while, call it off the request
        thread. drain=False: abandon the queued backlog and stop promptly.
        """
        self._stop.set()
        if not drain:
            self._abort.set()
        try:
            self._queue.put_nowait(None)  # wake the worker; it exits on the sentinel or empty+stop
        except queue.Full:
            pass  # full queue -> worker drains it and exits via the empty+stop check below
        self._worker.join(timeout=timeout)  # timeout=None -> wait until fully drained

    def pending(self):
        """Approximate chunks still to transcribe (queued + the one in flight)."""
        n = self._queue.qsize()
        if self._busy:
            n += 1
        return n

    def is_alive(self):
        """True while the transcription worker thread is running."""
        return self._worker.is_alive()

    def on_chunk(self, source, audio, t_start, block=False, timeout=None):
        """Enqueue a chunk for transcription. Returns True if it was enqueued.

        block=False (live capture): drop if the backlog is full rather than stall
        the real-time audio thread; returns False on a drop. block=True with a
        timeout (file import): wait up to `timeout` seconds for space and return
        False if it could not enqueue, so the caller can re-check abort + worker
        health and retry. That way a stalled or dead worker never wedges the import.
        """
        if self._stop.is_set():
            return False  # shutting down, don't accept new audio while we drain
        try:
            self._queue.put((source, audio, t_start), block=block, timeout=timeout)
            return True
        except queue.Full:
            if not block:
                # Live backpressure: drop, but make it VISIBLE - a real gap in the
                # transcript beats a silent lie. The marker is emitted from the
                # worker once it next runs (see _run).
                self._dropped += 1
                print(f"[engine] queue full, dropping {source} chunk @ t={t_start:.1f}s "
                      f"(total dropped: {self._dropped})", flush=True)
            return False

    def set_initial_prompt(self, prompt):
        """Replace the live prompt mid-session (growing glossary support).

        The caller is responsible for keeping the anchor in place if desired -
        pass the full string. Each subsequent chunk picks this up immediately.
        """
        self.initial_prompt = prompt

    def _emit_marker(self, source, t_start, n_dropped):
        out = Segment(
            source=source,
            t_start=t_start,
            t_end=t_start,
            text=f"[… ~{n_dropped} chunk(s) not transcribed, transcriber fell behind …]",
        )
        for sub in self.subscribers:
            try:
                sub(out)
            except Exception as e:
                print(f"[engine] subscriber error: {e}", flush=True)

    def _emit_notice(self, t_start, text):
        """Emit a system notice into the transcript (e.g. a model change)."""
        out = Segment(source="SYS", t_start=t_start, t_end=t_start, text=text)
        for sub in self.subscribers:
            try:
                sub(out)
            except Exception as e:
                print(f"[engine] subscriber error: {e}", flush=True)

    def _maybe_downgrade(self, t_start):
        """Step down CPU_LADDER when sustained RTF shows we can't hold real-time.

        Only fires on CPU. Ratchets down only, never back up, to avoid
        oscillation. The new (smaller) model also chews through the queued
        backlog faster, which is how the session catches back up.
        """
        if not self.adaptive or not self._is_cpu or len(self._rtf) < self._rtf.maxlen:
            return
        avg = sum(self._rtf) / len(self._rtf)
        if avg <= DOWNGRADE_RTF:
            return
        try:
            idx = CPU_LADDER.index(self.model_name)
        except ValueError:
            idx = -1  # start model above the ladder (turbo/large-v3) -> first rung is next
        if idx + 1 >= len(CPU_LADDER):
            return  # already on the fastest rung; nothing more to give
        new_model = CPU_LADDER[idx + 1]
        print(f"[engine] CPU RTF ~{avg:.2f} (> {DOWNGRADE_RTF}); "
              f"downgrading {self.model_name} -> {new_model}", flush=True)
        try:
            new = WhisperModel(new_model, device="cpu",
                               compute_type=self._compute_type,
                               cpu_threads=self._cpu_threads, num_workers=1)
        except Exception as e:
            print(f"[engine] downgrade load failed ({new_model}): {e}", flush=True)
            return
        self.model = new
        self.model_name = new_model
        self._rtf.clear()
        self._emit_notice(t_start, f"[engine: switched to '{new_model}' model to keep up with the audio]")

    def _run(self):
        # Loop until the sentinel, or (during shutdown) until the queue empties.
        # We deliberately do NOT break the instant _stop is set, that would drop
        # the queued backlog (the last minutes of the session). Instead we drain.
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stop.is_set():
                    break  # shutdown requested and the backlog is fully drained
                continue
            if item is None:
                break  # sentinel
            if self._abort.is_set():
                continue  # hard abort: discard remaining items without transcribing
            source, audio, t_start = item

            # If chunks were dropped to backpressure before this one, record the
            # gap in the transcript so the reader knows audio is missing here.
            if self._dropped:
                self._emit_marker(source, t_start, self._dropped)
                self._dropped = 0

            # Adaptive quality: under backlog pressure, drop to beam_size=1 to
            # transcribe faster and catch up. Best-effort quality when keeping up.
            beam = 1 if (self.adaptive and self.pending() > BACKPRESSURE_BEAM_THRESHOLD) else self.beam_size

            self._busy = True
            try:
                t0 = time.monotonic()
                segs, _info = self.model.transcribe(
                    audio,
                    language=self.language,
                    initial_prompt=self.initial_prompt,
                    vad_filter=True,
                    beam_size=beam,
                    **GUARD,
                )
                seg_list = list(segs)  # faster-whisper is lazy, this forces the actual compute
                elapsed = time.monotonic() - t0

                for seg in seg_list:
                    text = _collapse_repetition((seg.text or "").strip())
                    if not text:
                        continue
                    if _is_hallucination(text):
                        continue
                    # Residual silence hallucination the window-level no_speech guard
                    # let through: drop a segment the model is very sure is non-speech.
                    if getattr(seg, "no_speech_prob", 0.0) > 0.85:
                        continue
                    out = Segment(
                        source=source,
                        t_start=t_start + float(seg.start),
                        t_end=t_start + float(seg.end),
                        text=text,
                    )
                    for sub in self.subscribers:
                        try:
                            sub(out)
                        except Exception as e:
                            print(f"[engine] subscriber error: {e}", flush=True)

                # Adaptive model downgrade (CPU only): track real-time factor and
                # step down to a faster model if we're sustained-slower than real-time.
                if self._is_cpu:
                    audio_dur = len(audio) / 16000.0
                    if audio_dur > 0:
                        self._rtf.append(elapsed / audio_dur)
                    self._maybe_downgrade(t_start)
            except Exception as e:
                print(f"[engine] transcribe error on {source} chunk: {e}", flush=True)
            finally:
                self._busy = False
