"""faster-whisper engine with tier-based config and subscriber fan-out.

Single transcription worker thread, single model, single chunk queue.
Chunks from both mic and system loopback go through serially, keeps GPU
memory at one model's footprint and simplifies the data flow. If GPU under-
utilisation becomes a problem in V1 with a snappier chunk size, we can run
two model instances; not worth it for V0.
"""
import os
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

# Make any downloaded NVIDIA CUDA libraries (optional GPU support, see cudadl.py)
# findable BEFORE ctranslate2 loads. No-op when none have been downloaded.
from . import cudadl
cudadl.register_dll_dir()

from faster_whisper import WhisperModel


# Fluister: our Afrikaans-optimised Whisper models (LoRA fine-tunes merged to ctranslate2 int8).
# Much better on Afrikaans, equal-or-better on English, no Afrikaans leakage on pure-English
# audio (see SA-ASR-Model/corpus-strategy.md). The LANGUAGE chosen for a session decides the
# model FAMILY (Afrikaans -> Fluister, everything else -> stock Whisper); the hardware tier
# decides the SIZE. resolve_model() pairs the two.
#
# Published publicly at huggingface.co/digiphyte/fluister-*. faster-whisper downloads and caches a
# repo id on first use, exactly like the stock models. On this dev machine the locally-built ct2
# dirs are reused as-is (no re-download). Set SA_LIVE_AF_MODEL=stock to force the stock Whisper of
# the same size (A/B), or to any path/name to override every Fluister size.
def _fluister(repo, local, stock):
    ov = os.environ.get("SA_LIVE_AF_MODEL")
    if ov:
        return stock if ov.lower() == "stock" else ov
    return local if os.path.isdir(local) else repo

# size -> Fluister model id: the hosted HF repo (downloaded on first use), or the local ct2 build
# when present on this machine. resolve_model treats anything != the stock size name as Fluister.
_FLUISTER = {
    "large-v3":       _fluister("digiphyte/fluister-large-v3", r"C:\Users\seanf\.cache\af-lora-ct2-int8", "large-v3"),
    "large-v3-turbo": _fluister("digiphyte/fluister-turbo", r"C:\Users\seanf\.cache\af-lora-turbo-ct2-int8", "large-v3-turbo"),
    "medium":         _fluister("digiphyte/fluister-medium", r"C:\Users\seanf\.cache\af-lora-medium-ct2-int8", "medium"),
    "small":          _fluister("digiphyte/fluister-small", r"C:\Users\seanf\.cache\af-lora-small-ct2-int8", "small"),
}


def family_for_language(language):
    """The model family that transcribes this language. Afrikaans -> our Fluister tune; everything
    else (English, others, and auto-detect, which we cannot assume is Afrikaans) -> stock Whisper.
    Bantu languages will map to a future za-anv family."""
    return "fluister" if (language or "").lower().startswith("af") else "whisper"


def resolve_model(size, language):
    """Map a Whisper size + the spoken language to the concrete model id to load, and say whether
    the result is actually a Fluister model. Afrikaans uses the Fluister tune of that size when it
    is installed; any other language, or a size with no Fluister build, uses stock Whisper.
    Returns (model_id, is_fluister)."""
    if family_for_language(language) == "fluister":
        tuned = _FLUISTER.get(size)
        if tuned and tuned != size:        # a real Fluister path/repo, not the stock fallback
            return tuned, True
    return size, False


def fluister_available():
    """True if at least one Fluister model is installed (any size resolves to a tuned model rather
    than its stock fallback), so the UI can tell the truth about whether an Afrikaans session will
    actually run on Fluister yet."""
    return any(v != k for k, v in _FLUISTER.items())


# Tiers map hardware -> SIZE + device + compute_type. The model field is a stock Whisper size;
# the Engine swaps it for the Fluister tune of that size when the session language is Afrikaans
# (see resolve_model). compute_type matters as much as the size:
#  - "gpu"     large-v3, int8_float16, needs ~3GB+ VRAM (RTX 3090 etc.)
#  - "gpu-4gb" large-v3, int8_float16, fits a 4GB card (GTX 1650 Mobile);
#                                       near-float16 quality, int8 tensor cores
#  - cpu tiers, fallback only. CPU ASR is memory-bandwidth-bound, so it can run
#                slower than real-time while CPU usage looks moderate. Prefer a
#                GPU tier whenever a CUDA device exists.
TIER_CONFIG = {
    "gpu":        {"model": "large-v3",       "device": "cuda", "compute_type": "int8_float16"},
    "gpu-4gb":    {"model": "large-v3",       "device": "cuda", "compute_type": "int8_float16"},
    # CPU tiers set the STARTING size. On CPU the engine measures its real-time
    # factor each chunk and auto-downgrades along CPU_LADDER if it can't keep up
    # (see _maybe_downgrade), so a fast CPU keeps the bigger model and a slow one
    # ratchets down on its own. Start ambitious; let it self-correct. Pair with
    # --keep-audio + a post-meeting large-v3 pass for the canonical transcript.
    "cpu":        {"model": "small",          "device": "cpu",  "compute_type": "int8"},
    "cpu-min":    {"model": "base",           "device": "cpu",  "compute_type": "int8"},
    "cpu-strong": {"model": "large-v3-turbo", "device": "cpu",  "compute_type": "int8"},
    "cpu-mid":    {"model": "medium",         "device": "cpu",  "compute_type": "int8"},
    # large-v3 on CPU: too slow to hold real-time for LIVE on most machines (the
    # adaptive ladder downgrades it there), but the best-accuracy choice for an
    # uploaded recording / post-meeting pass, where there is no real-time constraint.
    "cpu-large":  {"model": "large-v3",       "device": "cpu",  "compute_type": "int8"},
}


# ── model cache + warm-up ──────────────────────────────────────────────────
# Building a WhisperModel is the slow part of starting a session. Two costs hide here:
#  1. With no local_files_only, faster-whisper revalidates the model against HuggingFace
#     Hub over the NETWORK on every load, even when it is fully downloaded. On a slow or
#     flaky connection those per-file checks retry for minutes: the "initialising forever"
#     first-use stall, fast next time only because the caches are warm.
#  2. On the GPU the first inference also initialises CUDA/cuDNN.
# So load_model() loads from the local cache only (never the network) and reuses the
# instance, and warm_up_async() pre-builds + lightly exercises it in the background BEFORE
# the user hits Begin, so the first real chunk is instant.
_MODEL_CACHE = {}                 # (model, device, compute_type) -> WhisperModel
_CACHE_MAX = 3                    # bound memory; evicted entries stay alive while a session still references them
_BUILD_LOCK = threading.RLock()   # serialise builds + warm-up, so a Begin during warm-up reuses the warm model
_WARM_LOCK = threading.Lock()     # guards _WARM only (kept separate so status reads never wait on a long build)
_WARM = {"state": "idle", "tier": None, "model": None}   # state: idle|warming|ready|error; model = resolved id being warmed


def _build_model(model_name, device, compute_type, cpu_threads):
    kw = dict(device=device, compute_type=compute_type)
    if device == "cpu":
        kw["cpu_threads"] = cpu_threads
        kw["num_workers"] = 1
    # Local cache only: never touch the network for an already-downloaded model. Fall back
    # to a normal (network-allowed) load only if it genuinely is not on disk yet.
    try:
        return WhisperModel(model_name, local_files_only=True, **kw)
    except Exception as e:
        print(f"[engine] {model_name} not in local cache ({e}); allowing a download", flush=True)
        return WhisperModel(model_name, local_files_only=False, **kw)


def load_model(model_name, device, compute_type, cpu_threads=8):
    """Return a cached WhisperModel for these settings, building it (from the local cache,
    no network) if needed. Safe from both the warm-up thread and session start; the build
    lock makes a Begin during warm-up wait for the warm model instead of building a second."""
    key = (model_name, device, compute_type)
    with _BUILD_LOCK:
        m = _MODEL_CACHE.get(key)
        if m is None:
            m = _build_model(model_name, device, compute_type, cpu_threads)
            # Bound memory: drop the oldest cache slot. The just-built model, and any model a
            # live session still holds, stay alive via their own references; only the slot goes.
            while len(_MODEL_CACHE) >= _CACHE_MAX:
                _MODEL_CACHE.pop(next(iter(_MODEL_CACHE)), None)
            _MODEL_CACHE[key] = m
        return m


def warm_status():
    with _WARM_LOCK:
        return dict(_WARM)


def warm_up_async(tier, language=None):
    """Pre-load and lightly exercise the model for `tier` + `language` in the background, so the
    first Begin is instant. The language matters: an Afrikaans session loads the Fluister model,
    so warming the stock model would miss. Idempotent: a no-op while already warming, or once the
    resolved model is cached."""
    cfg = TIER_CONFIG.get(tier)
    if not cfg:
        return {"state": "idle", "tier": None}
    model_id, _ = resolve_model(cfg["model"], language)
    key = (model_id, cfg["device"], cfg["compute_type"])
    with _WARM_LOCK:
        if key in _MODEL_CACHE:        # already built (GIL-safe membership read) -> ready
            _WARM.update(state="ready", tier=tier, model=model_id)
            return dict(_WARM)
        # Only treat an in-flight warm as a no-op when it is warming THIS model. A language switch
        # selects a different family, so its warm must still be kicked off (it queues on _BUILD_LOCK).
        if _WARM["state"] == "warming" and _WARM.get("model") == model_id:
            return dict(_WARM)
        _WARM.update(state="warming", tier=tier, model=model_id)
    threading.Thread(target=_warm_run, args=(tier, cfg, model_id, language), daemon=True, name="warmup").start()
    return {"state": "warming", "tier": tier}


def _warm_run(tier, cfg, model_id, language):
    import numpy as np
    try:
        with _BUILD_LOCK:   # hold across build + dummy so a concurrent Begin waits for a fully warm model
            m = load_model(model_id, cfg["device"], cfg["compute_type"])
            # A tiny dummy inference triggers CUDA/cuDNN init (and any first-call autotune) now,
            # off the user's critical path. vad_filter=False so the encoder actually runs on the
            # silence rather than the VAD discarding it.
            try:
                list(m.transcribe(np.zeros(16000, dtype=np.float32), language=(language or "af"),
                                  vad_filter=False, beam_size=1)[0])
            except Exception:
                pass
        with _WARM_LOCK:
            if _WARM.get("model") == model_id:   # don't clobber a newer warm (late language switch)
                _WARM.update(state="ready", tier=tier)
    except Exception as e:
        print(f"[warmup] failed for {tier}: {e}", flush=True)
        with _WARM_LOCK:
            if _WARM.get("model") == model_id:
                _WARM.update(state="error", tier=tier)

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
    "Dit is 'n gesprek hoofsaaklik in Afrikaans, maar die sprekers wissel soms "
    "na Engels (kodewisseling). Skryf Afrikaans as Afrikaans en Engels as Engels, "
    "net soos dit gepraat word. Ons praat Suid-Afrikaanse Afrikaans, "
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

# Hold each MIC segment this long before showing it in the LIVE view, so a speaker echo lands
# just after its cleaner SYS original instead of jumbled in front of it (the system channel
# transcribes a touch behind the mic). Nothing is dropped live; ALL echo removal happens once
# at the end, on the saved transcript (dedup.strip_mic_echoes in sinks.py), where the full
# time-ordered context makes matching accurate and nothing is lost before it. Live ordering only.
MIC_PUBLISH_DELAY = 1.0


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
    r"|\bbaie,\s*nogal,\s*lekker"       # AF_ANCHOR_PROMPT word-list leaking
    r"|ons praat suid-?afrikaans.{0,40}nie nederlands nie",  # AF_ANCHOR_PROMPT opening leaking
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
        # size = the stock Whisper size for this tier (drives the CPU downgrade ladder); model_name
        # = the concrete model loaded, which is the Fluister tune of that size for an Afrikaans
        # session and stock Whisper otherwise. family/is_fluister label the active engine honestly.
        self.size = cfg["model"]
        self.model_name, self.is_fluister = resolve_model(self.size, language)
        self.family = "fluister" if self.is_fluister else "whisper"

        # Cached, local-only load (no network revalidation) and reused across sessions, so a
        # warmed model makes Begin instant. See load_model / warm_up_async above.
        self.model = load_model(self.model_name, cfg["device"], cfg["compute_type"], cpu_threads=cpu_threads)
        self._is_cpu = cfg["device"] == "cpu"
        self._compute_type = cfg["compute_type"]
        self._cpu_threads = cpu_threads
        self._rtf = deque(maxlen=DOWNGRADE_WINDOW)  # recent real-time factors (CPU downgrade)
        self.subscribers = []
        self._pending_mic = []                # [(release_monotonic, Segment)] held by MIC_PUBLISH_DELAY
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

    def _fanout(self, seg):
        """Deliver a finished segment to every subscriber. Single point of delivery."""
        for sub in self.subscribers:
            try:
                sub(seg)
            except Exception as e:
                print(f"[engine] subscriber error: {e}", flush=True)

    def _route(self, seg):
        """Publish a finished segment. MIC is held briefly (MIC_PUBLISH_DELAY) so that in the
        LIVE view a speaker echo lands just after its cleaner SYS original rather than jumbled
        in front of it; nothing is dropped here. SYS and file-import segments publish at once.
        All echo removal happens once at the end, on the saved transcript (sinks.py uses
        dedup.strip_mic_echoes), where the full time-ordered context makes it safe."""
        if seg.source == "MIC":
            self._pending_mic.append((time.monotonic() + MIC_PUBLISH_DELAY, seg))
        else:
            self._fanout(seg)

    def _flush_pending_mic(self, force=False):
        """Publish held MIC segments whose delay has elapsed (or all of them when force=True,
        at a clean shutdown). Nothing is dropped here: the live view shows everything, and echo
        removal is deferred to the end-of-session rewrite of the saved transcript. On a hard
        abort (drain=False) held MIC is discarded with the rest of the backlog, so the abort
        stays prompt."""
        if self._abort.is_set():
            self._pending_mic = []
            return
        if not self._pending_mic:
            return
        now = time.monotonic()
        keep = []
        for release_at, seg in self._pending_mic:
            if force or now >= release_at:
                self._fanout(seg)
            else:
                keep.append((release_at, seg))
        self._pending_mic = keep

    def _emit_marker(self, source, t_start, n_dropped):
        self._fanout(Segment(
            source=source,
            t_start=t_start,
            t_end=t_start,
            text=f"[… ~{n_dropped} chunk(s) not transcribed, transcriber fell behind …]",
        ))

    def _emit_notice(self, t_start, text):
        """Emit a system notice into the transcript (e.g. a model change)."""
        self._fanout(Segment(source="SYS", t_start=t_start, t_end=t_start, text=text))

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
            idx = CPU_LADDER.index(self.size)
        except ValueError:
            idx = -1  # start size above the ladder (turbo/large-v3) -> first rung is next
        if idx + 1 >= len(CPU_LADDER):
            return  # already on the fastest rung; nothing more to give
        new_size = CPU_LADDER[idx + 1]
        # Keep the family: an Afrikaans session downgrades to the Fluister tune of the smaller size
        # where one exists (medium, small), and to stock Whisper for the rungs that have none.
        new_model, new_is_fluister = resolve_model(new_size, self.language)
        print(f"[engine] CPU RTF ~{avg:.2f} (> {DOWNGRADE_RTF}); "
              f"downgrading {self.size} -> {new_size}", flush=True)
        try:
            new = load_model(new_model, "cpu", self._compute_type, cpu_threads=self._cpu_threads)
        except Exception as e:
            print(f"[engine] downgrade load failed ({new_size}): {e}", flush=True)
            return
        self.model = new
        self.size = new_size
        self.model_name = new_model
        self.is_fluister = new_is_fluister
        self.family = "fluister" if new_is_fluister else "whisper"
        self._rtf.clear()
        self._emit_notice(t_start, f"[engine: switched to '{new_size}' model to keep up with the audio]")

    def _run(self):
        # Loop until the sentinel, or (during shutdown) until the queue empties.
        # We deliberately do NOT break the instant _stop is set, that would drop
        # the queued backlog (the last minutes of the session). Instead we drain.
        while True:
            self._flush_pending_mic()   # release held MIC whose delay elapsed (ticks each ~0.5s)
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
                    self._route(out)

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

        # Worker exiting (drained or aborted): release anything still held by the MIC delay.
        self._flush_pending_mic(force=True)
