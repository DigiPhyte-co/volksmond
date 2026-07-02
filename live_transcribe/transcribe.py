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

# size -> canonical Fluister HuggingFace repo. The single source of truth for the repo ids, so the
# voice-model catalogue and the update manifest (voicedl) resolve exactly the repos this engine loads.
FLUISTER_REPOS = {
    "large-v3":       "digiphyte/fluister-large-v3",
    "large-v3-turbo": "digiphyte/fluister-turbo",
    "medium":         "digiphyte/fluister-medium",
    "small":          "digiphyte/fluister-small",
}

# size -> Fluister model id: the hosted HF repo (downloaded on first use), or the local ct2 build
# when present on this machine. resolve_model treats anything != the stock size name as Fluister.
_FLUISTER = {
    "large-v3":       _fluister(FLUISTER_REPOS["large-v3"], r"C:\Users\seanf\.cache\af-lora-ct2-int8", "large-v3"),
    "large-v3-turbo": _fluister(FLUISTER_REPOS["large-v3-turbo"], r"C:\Users\seanf\.cache\af-lora-turbo-ct2-int8", "large-v3-turbo"),
    "medium":         _fluister(FLUISTER_REPOS["medium"], r"C:\Users\seanf\.cache\af-lora-medium-ct2-int8", "medium"),
    "small":          _fluister(FLUISTER_REPOS["small"], r"C:\Users\seanf\.cache\af-lora-small-ct2-int8", "small"),
}


# Swivuriso: the South African Next Voices (DSFSI) multilingual model, used UNDER ITS OWN NAME (we
# did not train it; MIT). One model covers seven South African languages. faster-whisper has no codes for
# them and DSFSI forces none, so the engine runs it on auto-detect (language=None). ct2-converted
# from dsfsi-anv/za-anv-multilingual-whisper-v3-turbo; a local ct2 build is reused if present, just
# like Fluister. Credit DSFSI / African Next Voices (model card + NOTICE on the hosted repo).
SWIVURISO_LANGS = ("zu", "xh", "st", "tn", "ts", "nr", "ve")  # isiZulu isiXhosa Sesotho Setswana Xitsonga isiNdebele Tshivenda
SWIVURISO_REPO = "digiphyte/swivuriso-turbo"          # our hosted ct2 (published, public, MIT)
SWIVURISO_LOCAL = r"C:\Users\seanf\.cache\swivuriso-turbo-ct2-int8"
SWIVURISO_HOSTED = True                                # SWIVURISO_REPO is published, so non-local machines resolve to it


def swivuriso_model():
    """The Swivuriso model id to load: the local ct2 build if present, else the hosted repo once
    published, else None (not available on this machine yet)."""
    if os.path.isdir(SWIVURISO_LOCAL):
        return SWIVURISO_LOCAL
    return SWIVURISO_REPO if SWIVURISO_HOSTED else None


def swivuriso_available():
    """True if a Swivuriso model can actually load here (local build present, or hosted)."""
    return swivuriso_model() is not None


def family_for_language(language):
    """The model family that transcribes this language. The seven South African languages -> Swivuriso
    (the DSFSI model), whether named individually (zu/xh/...) or via the "sa" group code the UI sends
    when the user picks "South African languages"; Afrikaans AND auto-detect ("") -> our Fluister tune;
    explicit English/other -> stock Whisper. A manual engine override (resolve_model) can still force
    any family."""
    lang = (language or "").lower()
    base = lang.split("-")[0]
    if base == "sa" or base in SWIVURISO_LANGS:
        return "swivuriso"
    return "fluister" if (lang == "" or lang == "auto" or lang.startswith("af")) else "whisper"


def resolve_model(size, language, engine="auto"):
    """Map a Whisper size + the spoken language to the concrete model id to load, and the FAMILY it
    belongs to. `engine` overrides the family: "auto" follows the language (family_for_language);
    "fluister"/"whisper"/"swivuriso" force that family. A forced family falls back honestly to stock
    Whisper when its model is not available (a size with no Fluister build, or Swivuriso not yet
    installed). Returns (model_id, family) where family is "fluister" | "whisper" | "swivuriso"."""
    eng = (engine or "auto").lower()
    fam = eng if eng in ("fluister", "whisper", "swivuriso") else family_for_language(language)
    if fam == "swivuriso":
        sv = swivuriso_model()
        if sv:
            return sv, "swivuriso"          # one model, size-independent
        return size, "whisper"              # not installed/hosted yet -> honest stock fallback
    if fam == "fluister":
        tuned = _FLUISTER.get(size)
        if tuned and tuned != size:         # a real Fluister path/repo, not the stock fallback
            return tuned, "fluister"
        return size, "whisper"
    return size, "whisper"


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
    # GPU size variants, so an explicit Quality pick is honoured on the GPU instead of always
    # running large-v3. Same compute_type; only the model size loaded differs.
    "gpu-turbo":  {"model": "large-v3-turbo", "device": "cuda", "compute_type": "int8_float16"},
    "gpu-medium": {"model": "medium",         "device": "cuda", "compute_type": "int8_float16"},
    "gpu-small":  {"model": "small",          "device": "cuda", "compute_type": "int8_float16"},
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


def warm_up_async(tier, language=None, engine="auto"):
    """Pre-load and lightly exercise the model for `tier` + `language` in the background, so the
    first Begin is instant. The language matters: an Afrikaans session loads the Fluister model,
    so warming the stock model would miss. Idempotent: a no-op while already warming, or once the
    resolved model is cached."""
    cfg = TIER_CONFIG.get(tier)
    if not cfg:
        return {"state": "idle", "tier": None}
    model_id, fam = resolve_model(cfg["model"], language, engine)
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
    threading.Thread(target=_warm_run, args=(tier, cfg, model_id, language, fam), daemon=True, name="warmup").start()
    return {"state": "warming", "tier": tier}


def _warm_run(tier, cfg, model_id, language, fam):
    import numpy as np
    try:
        with _BUILD_LOCK:   # hold across build + dummy so a concurrent Begin waits for a fully warm model
            m = load_model(model_id, cfg["device"], cfg["compute_type"])
            # A tiny dummy inference triggers CUDA/cuDNN init (and any first-call autotune) now,
            # off the user's critical path. vad_filter=False so the encoder actually runs on the
            # silence rather than the VAD discarding it.
            try:
                list(m.transcribe(np.zeros(16000, dtype=np.float32), language=(None if fam == "swivuriso" else (language or "af")),
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


def _compose_prompt(language, user_prompt):
    """The initial_prompt for a session: the anti-Dutch anchor (Afrikaans only) with any user
    prompt (names, jargon) appended so client terms still bias the model, else the user prompt
    alone. A standalone function so a live language change can recompose it exactly as the
    constructor first did."""
    if language == "af":
        user = (user_prompt or "").strip()
        return f"{AF_ANCHOR_PROMPT} {user}".strip() if user else AF_ANCHOR_PROMPT
    return user_prompt


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


def xchan_gate_mic(mic, sysc, sr=16000, frame_ms=300, gate_db=10.0, sys_floor_db=-50.0):
    """Silence far-end bleed in the MIC channel using the time-aligned SYS as the reference.

    Even on headphones the far side leaks into the microphone at low level (measured ~100ms
    behind SYS), and Whisper transcribes that leak as garbled ghost lines that shadow the real
    SYS line. dedup.strip_mic_echoes cannot catch them because the leak transcribes to DIFFERENT
    words, so we remove it in the audio, before transcription. A MIC frame is treated as bleed
    (zeroed) when the SYS is active there AND the MIC sits more than gate_db below it - i.e. the
    only thing in the mic is a quiet copy of the far end. Near-end speech (MIC at or above the
    SYS) is left untouched, so on a clean recording this is a no-op. Only meaningful for the single
    stereo recording, where MIC (left) and SYS (right) are sample-aligned.

    Returns (cleaned_mic, silenced_frames, total_frames).
    """
    import numpy as np
    mic = np.asarray(mic, dtype=np.float32)
    sysc = np.asarray(sysc, dtype=np.float32)
    n = min(len(mic), len(sysc))
    out = mic.copy()
    fr = max(1, int(sr * frame_ms / 1000.0))
    silenced = total = 0
    for i in range(0, n, fr):
        m = mic[i:i + fr]
        s = sysc[i:i + fr]
        total += 1
        m_db = 20.0 * np.log10(float(np.sqrt(np.mean(m * m))) + 1e-9)
        s_db = 20.0 * np.log10(float(np.sqrt(np.mean(s * s))) + 1e-9)
        if s_db > sys_floor_db and m_db < s_db - gate_db:
            out[i:i + fr] = 0.0
            silenced += 1
    return out, silenced, total


class SysEnergyRing:
    """Rolling per-frame RMS (dBFS) of the SYS (far-end) channel, keyed by session-relative time.

    Written in real time by the capture callback (live) or filled in one pass from the aligned SYS
    channel (re-transcribe); read by the transcription worker to veto MIC echo segments. The live
    write MUST come from the capture callback (every ~0.5 s block), NOT from SYS chunk arrival:
    chunks only emit at a silence or a force-cut, so during a far-end monologue - precisely the echo
    case - the SYS chunk lands many seconds late and the reference would be missing when the MIC
    ghost is judged. Retained for minutes because a MIC chunk can be transcribed well after capture
    under CPU backlog. Thread-safe: the audio thread writes, the transcribe worker reads.
    """
    def __init__(self, retain_s=600.0):
        self._t = deque()
        self._db = deque()
        self._retain = retain_s
        self._lock = threading.Lock()

    def add(self, t, db):
        with self._lock:
            self._t.append(t)
            self._db.append(db)
            cut = t - self._retain
            while self._t and self._t[0] < cut:
                self._t.popleft()
                self._db.popleft()

    def add_block(self, t, samples):
        import numpy as np
        s = np.asarray(samples, dtype=np.float32)
        rms = float(np.sqrt(np.mean(s * s))) if s.size else 0.0
        self.add(t, 20.0 * np.log10(rms + 1e-9))

    def frames_in(self, t_lo, t_hi):
        """The SYS frame dB values whose timestamps fall in [t_lo, t_hi]. Copies under the lock;
        the caller does the heavier percentile maths outside it."""
        with self._lock:
            return [d for t, d in zip(self._t, self._db) if t_lo <= t <= t_hi]


def sys_echo_veto(mic_audio, sys_ring, abs_start, abs_end, word_count, sr=16000,
                  frame_ms=100, tol=0.3, active_floor=-50.0, min_coverage=0.60,
                  margin_db=10.0, mic_ceiling=-28.0):
    """Decide whether a MIC segment is far-end bleed (echo) that should be dropped.

    Conservative by construction so it (almost) never eats real speech: it fires only when the far
    end was active across most of the segment AND the mic's LOUDEST frames still sit well below the
    far end AND below an absolute ceiling. Short replies / backchannels are always kept, and a
    missing SYS reference fails safe (keep). Post-ASR, so it cannot un-blend a mixed segment - hence
    the coverage floor: it only drops segments that are overwhelmingly far-end. Returns
    (drop: bool, reason: str). Thresholds are the tuned starting points from the design review.
    """
    import numpy as np
    dur = abs_end - abs_start
    if word_count <= 2 or dur < 0.5:
        return False, "short"
    mic = np.asarray(mic_audio, dtype=np.float32)
    if mic.size < sr * frame_ms / 1000.0:
        return False, "tiny"
    fr = max(1, int(sr * frame_ms / 1000.0))
    mdb = []
    for i in range(0, len(mic), fr):
        w = mic[i:i + fr]
        if len(w) < fr // 2:
            break
        mdb.append(20.0 * np.log10(float(np.sqrt(np.mean(w * w))) + 1e-9))
    if not mdb:
        return False, "nomic"
    mic_p90 = float(np.percentile(mdb, 90))       # the mic's loud frames; low => never really spoke
    sys = sys_ring.frames_in(abs_start - tol, abs_end + tol)
    if not sys:
        return False, "nosys"                     # no reference -> keep (fail safe)
    active = [d for d in sys if d > active_floor]
    coverage = len(active) / len(sys)
    if coverage < min_coverage:
        return False, f"cov={coverage:.2f}"
    sys_p70 = float(np.percentile(active, 70))
    drop = (sys_p70 - mic_p90) >= margin_db and mic_p90 < mic_ceiling
    return drop, f"cov={coverage:.2f} sysP70={sys_p70:.0f} micP90={mic_p90:.0f}"


def _is_silence(audio, sr=16000, frame_ms=100, floor_db=-45.0):
    """True if a chunk is room tone / near-silence: no ~100ms frame reaches the speech floor.

    Whisper invents phrases and loops on such audio, so the caller skips it. Uses the LOUDEST
    frame, so any real utterance - even a quiet one - keeps the chunk; only chunks with no frame
    above the floor are dropped. Complements sys_echo_veto, which needs a loud far end as its
    reference; this is the case where nothing is playing at all (the pure-silence hallucination)."""
    import numpy as np
    try:
        a = np.asarray(audio, dtype=np.float32)
        fr = max(1, int(sr * frame_ms / 1000.0))
        if a.ndim != 1 or a.size < fr:
            return False
        floor_lin = 10.0 ** (floor_db / 20.0)
        for i in range(0, len(a) - fr + 1, fr):
            w = a[i:i + fr]
            if float(np.sqrt(np.mean(w * w))) >= floor_lin:
                return False   # a frame reached the floor -> real speech somewhere -> keep the chunk
        return True            # nothing reached the floor -> silence
    except Exception:
        return False           # not real audio (e.g. a test stub) -> never gate on bad input


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


def _is_phrase_loop(text, max_unit=5, min_repeats=3, min_cov=0.6):
    """True if the text is mostly ONE multi-word unit repeated back to back ("ek het nie ek het nie
    ek het nie ..."), a quiet-mic loop artifact rather than speech, so the caller drops the segment.

    _collapse_repetition handles single-token runs; this catches word-group loops that slip past it
    and past the compression-ratio guard. Conservative: needs several words, at least min_repeats
    consecutive repeats of the unit, and the loop to cover most of the segment, so an ordinary
    sentence with an incidental repeat is left alone."""
    words = [w for w in (t.lower().strip('.,!?;:"\'') for t in text.split()) if w]
    n = len(words)
    if n < 6:
        return False
    for u in range(1, max_unit + 1):
        if u * min_repeats > n:
            break
        for start in range(0, n - u * min_repeats + 1):
            unit = words[start:start + u]
            reps, j = 1, start + u
            while j + u <= n and words[j:j + u] == unit:
                reps += 1
                j += u
            if reps >= min_repeats and (reps * u) / n >= min_cov:
                return True
    return False


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
    r"|\balgemene woorde\b"             # AF_ANCHOR_PROMPT list header leaking (any trailing punctuation)
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
    def __init__(self, tier, language="af", initial_prompt=None, cpu_threads=8, beam_size=5, adaptive=True, engine="auto"):
        self.engine = (engine or "auto").lower()
        if tier not in TIER_CONFIG:
            raise ValueError(f"Unknown tier {tier!r}; choose from {list(TIER_CONFIG)}")
        self.tier = tier
        self.language = language
        self.beam_size = beam_size
        # adaptive=True (live): cut beam + downgrade the model under backlog to keep
        # up with real time. adaptive=False (file import): not real time, so never
        # trade quality for speed - keep the chosen model and full beam size.
        self.adaptive = adaptive

        # The Afrikaans anchor (af only) plus any user prompt. Keep the raw user prompt too, so a
        # live language change (request_change) can recompose the anchor for the new language.
        self._user_prompt = initial_prompt
        self.initial_prompt = _compose_prompt(language, initial_prompt)
        cfg = TIER_CONFIG[tier]
        # size = the stock Whisper size for this tier (drives the CPU downgrade ladder); model_name
        # = the concrete model loaded, which is the Fluister tune of that size for an Afrikaans
        # session and stock Whisper otherwise. family/is_fluister label the active engine honestly.
        self.size = cfg["model"]
        self.model_name, self.family = resolve_model(self.size, language, engine)
        self.is_fluister = self.family == "fluister"
        # Swivuriso has no faster-whisper codes for these South African languages and DSFSI forces none, so
        # run it on auto-detect; every other family keeps the chosen decode language.
        self.language = None if self.family == "swivuriso" else language

        # Cached, local-only load (no network revalidation) and reused across sessions, so a
        # warmed model makes Begin instant. See load_model / warm_up_async above.
        self.model = load_model(self.model_name, cfg["device"], cfg["compute_type"], cpu_threads=cpu_threads)
        self._is_cpu = cfg["device"] == "cpu"
        self._compute_type = cfg["compute_type"]
        self._cpu_threads = cpu_threads
        self._rtf = deque(maxlen=DOWNGRADE_WINDOW)  # recent real-time factors (CPU downgrade)
        self.subscribers = []
        self._pending_mic = []                # [(release_monotonic, Segment)] held by MIC_PUBLISH_DELAY
        self.sys_env = None                   # optional SysEnergyRing -> enables the MIC echo veto
        self._xchan_veto = os.environ.get("SA_LIVE_XCHAN_VETO", "1") != "0"
        self._silence_gate = os.environ.get("SA_LIVE_SILENCE_GATE", "1") != "0"
        self._queue = queue.Queue(maxsize=32)
        self._stop = threading.Event()    # shutting down: stop accepting new audio
        self._abort = threading.Event()   # hard abort: discard the backlog instead of draining
        self._busy = False                # True while a chunk is mid-transcription (for pending())
        self._dropped = 0                 # chunks dropped to backpressure since last reported
        self._change_lock = threading.Lock()  # guards _pending_change (API thread queues, worker applies)
        self._pending_change = None           # a live language/model change to apply between chunks
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

    def request_change(self, *, language, engine, model=None, model_name=None, size=None, family=None):
        """Queue a live language and/or model change, applied by the worker between chunks.

        Pass `model` (a WhisperModel the CALLER already built via load_model, off the worker, so
        the swap never stalls transcription) together with model_name + size + family to swap
        the model; omit them to keep the current model and change only the decode language - instant,
        no reload, and the right move for a bilingual meeting on a both-capable model. `language` is
        the next decode language (None == auto-detect, "af"/"en" otherwise); the prompt is recomposed
        for it. A second request before the worker applies the first simply replaces it."""
        with self._change_lock:
            self._pending_change = {
                "language": language, "engine": engine,
                "model": model, "model_name": model_name, "size": size, "family": family,
            }

    def _apply_pending_change(self, t_start):
        """Apply a queued request_change. Called only from the worker loop, so self.model is read
        and written on a single thread, the same discipline _maybe_downgrade relies on."""
        with self._change_lock:
            ch = self._pending_change
            self._pending_change = None
        if not ch:
            return
        self.language = ch["language"]
        self.engine = ch["engine"]
        if ch["model"] is not None:
            self.model = ch["model"]
            self.model_name = ch["model_name"]
            self.size = ch["size"]
            self.family = ch["family"]
            self.is_fluister = ch["family"] == "fluister"
        self.initial_prompt = _compose_prompt(self.language, self._user_prompt)
        self._rtf.clear()   # judge the (possibly new) model fresh; never downgrade on the old RTF
        lang_name = {"af": "Afrikaans", "en": "English"}.get(self.language, "auto-detect")
        self._emit_notice(t_start, f"[engine: now {self.family} {self.size}, language {lang_name}]")

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
        # Swivuriso is a single fixed model (size-independent), so there is no smaller rung to drop
        # to; never downgrade it.
        if self.family == "swivuriso":
            return
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
        # Keep the family AND the user's engine choice: a forced-Fluister or forced-Whisper session
        # must NOT silently flip to language-based auto when the model size drops a rung.
        new_model, new_family = resolve_model(new_size, self.language, self.engine)
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
        self.family = new_family
        self.is_fluister = new_family == "fluister"
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

            # Apply a queued live language/model change before this chunk (worker-thread only, so
            # self.model stays single-writer, like the adaptive downgrade further down).
            self._apply_pending_change(t_start)

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
                # Silence gate: room tone / near-silence makes Whisper invent phrases and loops.
                # If no frame in the chunk reaches the speech floor there is nothing to transcribe,
                # so skip it. Complements the echo veto (which needs a loud far end as reference);
                # this is the no-far-end pure-silence case. Toggle SA_LIVE_SILENCE_GATE=0.
                if self._silence_gate and _is_silence(audio):
                    continue
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
                    # Phrase-loop artifact: a segment that is mostly one repeated multi-word unit
                    # ("ek het nie ek het nie ...") is a quiet-mic hallucination, not speech.
                    # (_collapse_repetition handles single-token runs; this catches word groups.)
                    if _is_phrase_loop(text):
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
                    # Cross-channel echo veto: drop a MIC segment that is far-end bleed (quiet copy
                    # of a concurrently-active SYS). Post-ASR + conservative, so it never touches the
                    # audio (no word-nudging / fragmentation) and keeps real speech. Live and file
                    # both feed self.sys_env; when it is absent the veto is inert.
                    if source == "MIC" and self._xchan_veto and self.sys_env is not None:
                        _mic = audio[int(float(seg.start) * 16000):int(float(seg.end) * 16000)]
                        _drop, _why = sys_echo_veto(_mic, self.sys_env, out.t_start, out.t_end, len(text.split()))
                        if _drop:
                            print(f"[engine] echo-veto dropped MIC @ {out.t_start:.1f}s [{_why}] {text[:40]!r}", flush=True)
                            continue
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
