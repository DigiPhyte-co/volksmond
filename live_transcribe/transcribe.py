"""faster-whisper engine with tier-based config and subscriber fan-out.

Single transcription worker thread, single model, single chunk queue.
Chunks from both mic and system loopback go through serially, keeps GPU
memory at one model's footprint and simplifies the data flow. If GPU under-
utilisation becomes a problem in V1 with a snappier chunk size, we can run
two model instances; not worth it for V0.
"""
import contextlib
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
# Imported as a MODULE (not just the class) because the CPU encoder window below has to rebind
# one name inside it; see _pad_or_trim.
import faster_whisper.transcribe as _fw_transcribe

# The fuzzy word matcher the end-of-session echo strip already uses; the live fuzzy echo veto
# (fuzzy_echo_veto below) reuses it so both places call the same two words "the same word".
from . import dedup


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
#
# base and tiny are here for ONE reason: they are the bottom two rungs of the live CPU ladder
# (CPU_LADDER), and stock base/tiny are unusable on Afrikaans. Measured on a five minute Afrikaans
# slice against a large-v3 reference: stock base scores WER 0.91 and answers in Dutch, stock tiny
# 1.47 with a dozen loop lines; the Fluister forms of the same sizes score 0.58 and 0.78. A ladder
# that steps out of the family to "keep up" buys speed by inventing text, so it must not exist.
FLUISTER_REPOS = {
    "large-v3":       "digiphyte/fluister-large-v3",
    "large-v3-turbo": "digiphyte/fluister-turbo",
    "medium":         "digiphyte/fluister-medium",
    "small":          "digiphyte/fluister-small",
    "base":           "digiphyte/fluister-base",
    "tiny":           "digiphyte/fluister-tiny",
}

# size -> Fluister model id: the hosted HF repo (downloaded on first use), or the local ct2 build
# when present on this machine. resolve_model treats anything != the stock size name as Fluister.
_FLUISTER = {
    "large-v3":       _fluister(FLUISTER_REPOS["large-v3"], r"C:\Users\seanf\.cache\af-lora-ct2-int8", "large-v3"),
    "large-v3-turbo": _fluister(FLUISTER_REPOS["large-v3-turbo"], r"C:\Users\seanf\.cache\af-lora-turbo-ct2-int8", "large-v3-turbo"),
    "medium":         _fluister(FLUISTER_REPOS["medium"], r"C:\Users\seanf\.cache\af-lora-medium-ct2-int8", "medium"),
    "small":          _fluister(FLUISTER_REPOS["small"], r"C:\Users\seanf\.cache\af-lora-small-ct2-int8", "small"),
    "base":           _fluister(FLUISTER_REPOS["base"], r"C:\Users\seanf\.cache\af-lora-base-ct2-int8", "base"),
    "tiny":           _fluister(FLUISTER_REPOS["tiny"], r"C:\Users\seanf\.cache\af-lora-tiny-ct2-int8", "tiny"),
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


def decode_language(family, language):
    """The language token actually passed to faster-whisper's transcribe(). Swivuriso always
    decodes on auto-detect (faster-whisper has no codes for its languages and DSFSI forces
    none). The same South African codes, and the "sa" group code, have no stock-Whisper token
    either, so any OTHER family also decodes them on auto-detect instead of erroring (or, for
    "sa", silently decoding as Sanskrit). Every other explicit code (af/en/de/fr/...) is forced
    as-is, which stops the decoder flapping between languages per chunk; ""/None/"auto" stay
    auto-detect."""
    lang = (language or "").lower()
    base = lang.split("-")[0]
    if family == "swivuriso" or base == "sa" or base in SWIVURISO_LANGS:
        return None
    if lang in ("", "auto"):
        return None
    return language


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
    # MLX tiers: the Apple GPU (Metal) via mlx-whisper, macOS arm64 only. Deliberately
    # NOT in __main__.TIER_CHOICES, so the Windows CLI/env surface is untouched; only
    # resolve_tier_engine emits them, and only on darwin-arm64. compute_type is nominal
    # here (the MLX repo holds its own precision) but keeps the cache key disambiguated.
    "mlx":        {"model": "large-v3",       "device": "mlx",  "compute_type": "fp16"},
    "mlx-turbo":  {"model": "large-v3-turbo", "device": "mlx",  "compute_type": "fp16"},
}


# ── CPU decode defaults: a 20 s encoder window and beam 1 ──────────────────
# Measured 2026-09-03 on one five minute Afrikaans slice, CPU int8, 8 threads, WER against a
# large-v3 GPU reference, 15 s audio chunks throughout:
#
#   model  window beam   RTF     WER
#   small   30 s    5   0.159   0.345    <- the old default
#   small   20 s    1   0.072   0.373
#   medium  30 s    5   0.442   0.289    <- the old default
#   medium  20 s    1   0.191   0.307
#
# A 20 s window costs nothing in WER (medium is fractionally BETTER at 0.282 vs 0.289 at beam 5)
# and saves 16-27 %; beam 1 costs about +0.02 WER and saves another 41-46 %. Together they roughly
# halve the CPU cost, which is what lets a laptop hold real time on two sources at once. 15 s was
# also measured and is catastrophic (WER 1.4-1.9, doubled word counts, the model emitting
# timestamps past the end of its own window): never go below 20 s.
#
# GPU work (CUDA and Metal) is untouched at Whisper's native 30 s / beam 5. The window is applied
# per MODEL, not per process: only a ct2 model built for device="cpu" carries _vm_encoder_frames,
# and only a decode wrapped in encoder_window() sees the shortened pad length.
CPU_ENCODER_WINDOW_S = 20
CPU_BEAM_SIZE = 1
DEFAULT_BEAM_SIZE = 5              # GPU / MLX, and any caller that asks for a beam explicitly

# faster-whisper pads every mel window back to 3000 frames (30 s) before handing it to the encoder:
# generate_segments() calls the module-level pad_or_trim() with no length, so shrinking the feature
# extractor alone does NOT shrink what the encoder actually sees. The only seam is that name. It is
# rebound ONCE, process-wide, to a wrapper that keeps the stock 3000 unless the CALLING THREAD has
# asked for a shorter window (encoder_window() sets a thread-local from the model's own attribute).
# Thread-local rather than a global flag on purpose: a CUDA or Metal engine decoding in the same
# process, on its own worker thread, must keep the full 30 s window and never be able to observe
# the CPU one. An explicit length (faster-whisper passes none today) always wins.
_ENCODER_WINDOW = threading.local()
_FW_PAD_OR_TRIM = _fw_transcribe.pad_or_trim


def _pad_or_trim(array, length=None, *, axis=-1):
    if length is None:
        length = getattr(_ENCODER_WINDOW, "frames", None) or 3000
    return _FW_PAD_OR_TRIM(array, length, axis=axis)


_pad_or_trim._vm_patched = True
if not getattr(_fw_transcribe.pad_or_trim, "_vm_patched", False):
    _fw_transcribe.pad_or_trim = _pad_or_trim


def set_encoder_window(model, seconds):
    """Point a ct2 model's feature extractor at a `seconds`-long encoder window and record the
    resulting mel width on the model as _vm_encoder_frames (what encoder_window() then honours).

    Returns the mel frame count, or None for a model with no ct2 feature extractor (the MLX
    adapter), which is left exactly as it is."""
    fe = getattr(model, "feature_extractor", None)
    if fe is None:
        return None
    fe.chunk_length = seconds
    fe.n_samples = seconds * fe.sampling_rate
    fe.nb_max_frames = fe.n_samples // fe.hop_length
    model._vm_encoder_frames = fe.nb_max_frames
    return fe.nb_max_frames


@contextlib.contextmanager
def encoder_window(model):
    """Run a decode with this model's shortened encoder window (a no-op for any model without
    one, i.e. every CUDA/Metal model). Yields the mel frame count, or None."""
    frames = getattr(model, "_vm_encoder_frames", None)
    prev = getattr(_ENCODER_WINDOW, "frames", None)
    _ENCODER_WINDOW.frames = frames
    try:
        yield frames
    finally:
        _ENCODER_WINDOW.frames = prev


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


def _build_model(model_name, device, compute_type, cpu_threads, local_only=False):
    if device == "mlx":
        # Apple Metal via the mlx-whisper adapter. Imported lazily so Windows (where the
        # mlx packages have no wheels and are never installed) pays nothing for this
        # branch. compute_type/cpu_threads have no MLX equivalent; the repo's own
        # precision applies. The adapter resolves the snapshot LOCAL-ONLY and raises
        # when the repo is not cached (stricter than the ct2 fallback below): an MLX
        # download must only ever happen through voicedl, never inside mlx-whisper.
        from . import mlxbackend
        repo = mlxbackend.mlx_model_for(model_name)
        if repo is None:
            raise ValueError(f"{model_name!r} has no MLX form (see mlxbackend.MLX_REPOS); "
                             "use a ct2 CPU tier for this model")
        return mlxbackend.MlxWhisperModel(repo)
    kw = dict(device=device, compute_type=compute_type)
    if device == "cpu":
        kw["cpu_threads"] = cpu_threads
        kw["num_workers"] = 1
    # Local cache only: never touch the network for an already-downloaded model. Fall back
    # to a normal (network-allowed) load only if it genuinely is not on disk yet - and never
    # when the caller said local_only, which is how the live ladder guarantees it will not stop
    # to download a model in the middle of a meeting.
    try:
        m = WhisperModel(model_name, local_files_only=True, **kw)
    except Exception as e:
        if local_only:
            raise
        print(f"[engine] {model_name} not in local cache ({e}); allowing a download", flush=True)
        m = WhisperModel(model_name, local_files_only=False, **kw)
    if device == "cpu":
        # Every CPU model, everywhere (live, file import, warm-up), gets the measured CPU
        # window. See CPU_ENCODER_WINDOW_S.
        set_encoder_window(m, CPU_ENCODER_WINDOW_S)
    return m


def model_present(model_id):
    """True when `model_id` can be loaded WITHOUT touching the network.

    The live ladder's usability test: a rung that is not already on this machine is skipped, never
    downloaded mid-meeting. A bare directory is judged directly; anything else is resolved through
    the HuggingFace cache with local_files_only. In both cases a real model.bin has to be there,
    because hf_hub reports a snapshot as present as soon as refs/main survives, even when an
    interrupted download left the weights missing (the same trap voicedl._present guards). Any
    error means "not present": never claim a model on missing evidence."""
    if not model_id:
        return False
    if os.path.isdir(model_id):
        return _has_ct2_weights(model_id)
    try:
        from huggingface_hub import snapshot_download
        path = snapshot_download(model_id, local_files_only=True)
    except Exception:
        return False
    return _has_ct2_weights(path)


def _has_ct2_weights(path, min_bytes=1_000_000):
    try:
        binp = os.path.join(path, "model.bin")
        return os.path.isfile(binp) and os.path.getsize(binp) > min_bytes
    except Exception:
        return False


def load_model(model_name, device, compute_type, cpu_threads=8, local_only=False):
    """Return a cached WhisperModel for these settings, building it (from the local cache,
    no network) if needed. Safe from both the warm-up thread and session start; the build
    lock makes a Begin during warm-up wait for the warm model instead of building a second.
    local_only=True refuses the network fallback and raises instead (the live ladder)."""
    key = (model_name, device, compute_type)
    with _BUILD_LOCK:
        m = _MODEL_CACHE.get(key)
        if m is None:
            m = _build_model(model_name, device, compute_type, cpu_threads, local_only=local_only)
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
            warm_lang = decode_language(fam, language)
            if warm_lang is None and fam != "swivuriso":
                warm_lang = "af"   # historic warm token for auto-detect; a fixed token skips detection on zeros
            try:
                # Inside encoder_window so a CPU model warms at the SAME mel width it will decode
                # at (20 s), rather than autotuning ctranslate2 for a shape it never sees again.
                with encoder_window(m):
                    list(m.transcribe(np.zeros(16000, dtype=np.float32), language=warm_lang,
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
# ladder: too slow to be a sane CPU live floor. GPU tiers never downgrade (they keep up).
#
# Three rules the ladder obeys, all of them learned the hard way on a CPU-only laptop that walked
# medium -> small -> base -> tiny inside half an hour and produced Dutch-flavoured loops:
#
#  1. IN-FAMILY ONLY. A rung is only taken when it resolves to the SAME family the session started
#     in. For an Afrikaans session that means Fluister the whole way down (which is why
#     FLUISTER_REPOS now carries base and tiny); a stock-Whisper session keeps the stock ladder.
#     Speed bought by leaving the family is not speed, it is fabrication.
#  2. PRESENT ONLY. A rung is usable only if it is already on this machine (model_present). No
#     model is ever downloaded in the middle of a meeting; an absent rung is skipped.
#  3. SHED, DO NOT DEGRADE. Below the last usable rung there is no smaller model to take, so the
#     engine drops the OLDEST queued audio instead (see _maybe_shed) and says so in the transcript.
#     Missing audio you can see beats invented text you cannot.
CPU_LADDER = ["medium", "small", "base", "tiny"]
DOWNGRADE_RTF = 0.95     # rolling real-time factor above this = not keeping up
DOWNGRADE_WINDOW = 4     # eligible chunks of evidence required before a step down
# Minimum wall clock between rung changes. The field cascade was self-feeding: a freshly built
# ct2 model pays its whole load cost on its FIRST inference (measured 35 s for medium, 16 s for
# small on a desktop), that single sample poisons a 4-sample window, and the ladder immediately
# stepped again. The first sample on a new model is now discarded outright (_cold_decode) AND a
# step has to earn a full window of fresh evidence over at least this long.
DOWNGRADE_MIN_SECONDS = 90.0
# Backlog bound, in seconds of audio waiting to be transcribed. Past this the engine is no longer
# live in any useful sense, so the shed valve drops the oldest queued audio back down to the bound.
# Three 15 s chunks per source plus the one in flight, i.e. about 45 s of lag, is the point where
# the "live" text stops being live.
SHED_BACKLOG_SECONDS = 45.0
# Past this rung the live text is indicative only: still the right family, but small enough that it
# should be read as a rough guide and re-transcribed from the recording afterwards. Surfaced to the
# UI as `indicative` on the downgrade payload.
INDICATIVE_BELOW = "small"

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


def raw_mic_ring_on():
    """Kill switch for the raw-mic energy ring (WP-4): SA_LIVE_RAW_MIC_RING=0 leaves Engine.mic_env
    unset, so the echo veto and the silence gate fall back to chunk samples - exactly the pre-WP-4
    behaviour. Read at call time so a test (or a support session) can flip it without a reimport."""
    return os.environ.get("SA_LIVE_RAW_MIC_RING", "1") != "0"


class SysEnergyRing:
    """Rolling per-frame RMS (dBFS) of one channel, keyed by session-relative time.

    Named for its first consumer (the SYS reference for the MIC echo veto) and kept under that name
    because it is public API; `EnergyRing` is the neutral alias new call sites use. There is now one
    of these per channel: the MIC ring is fed from the RAW pre-APM mic so every level test stays on
    its calibrated absolute basis under AGC (see capture_core.attach_mic_ring).

    `raw` records whether the feed is pre-processing device audio (live capture) or already-processed
    audio (an uploaded/auto-boosted recording, which has no absolute basis). Only the silence floor
    reads it; see _silence_floor_db.

    Written in real time by the capture callback (live) or filled in one pass from the aligned SYS
    channel (re-transcribe); read by the transcription worker to veto MIC echo segments. The live
    write MUST come from the capture callback (every ~0.5 s block), NOT from SYS chunk arrival:
    chunks only emit at a silence or a force-cut, so during a far-end monologue - precisely the echo
    case - the SYS chunk lands many seconds late and the reference would be missing when the MIC
    ghost is judged. Retained for minutes because a MIC chunk can be transcribed well after capture
    under CPU backlog. Thread-safe: the audio thread writes, the transcribe worker reads.
    """
    def __init__(self, retain_s=600.0, raw=True):
        self._t = deque()
        self._db = deque()
        self._retain = retain_s
        self.raw = raw
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
        """The frame dB values whose timestamps fall in [t_lo, t_hi]. Copies under the lock;
        the caller does the heavier percentile maths outside it."""
        with self._lock:
            return [d for t, d in zip(self._t, self._db) if t_lo <= t <= t_hi]

    def max_db(self, t_lo, t_hi):
        """The LOUDEST frame in the window, or None when the window holds no frames.

        The silence gate's statistic: any real utterance, however quiet, leaves one loud frame,
        so gating on the max keeps it (the same principle as _is_silence, on raw energy)."""
        f = self.frames_in(t_lo, t_hi)
        return max(f) if f else None

    def coverage(self, t_lo, t_hi, floor_db):
        """Fraction of frames in the window above `floor_db` (0.0 when the window is empty).

        Continuity, not loudness: measured on the incident recording, ghost/bleed lines cover
        ~39% of their window while real speech covers ~74%."""
        f = self.frames_in(t_lo, t_hi)
        if not f:
            return 0.0
        return sum(1 for d in f if d > floor_db) / len(f)

    def speech_level(self, t_hi, window_s=120.0):
        """p90 of the trailing window's frames above -70 dBFS: this channel's level WHEN SPEAKING.

        Returns None until there is anything to measure. Used to derive a relative floor for feeds
        with no absolute basis (an uploaded, possibly auto-boosted recording)."""
        import numpy as np
        f = [d for d in self.frames_in(t_hi - window_s, t_hi) if d > -70.0]
        if not f:
            return None
        return float(np.percentile(f, 90))

    def noise_floor(self, t_hi, window_s=120.0):
        """p10 of the trailing window's frames: this room's tone. None when there are no frames.

        The hardware-independent reference a continuity test needs (a whisper sits above room tone
        even when it sits far below any absolute speech floor)."""
        import numpy as np
        f = self.frames_in(t_hi - window_s, t_hi)
        if not f:
            return None
        return float(np.percentile(f, 10))

    def noise_floor_before(self, t_start, window_s=120.0, min_history_s=10.0):
        """p10 of the frames STRICTLY BEFORE `t_start`: the room tone this channel had going IN.

        The baseline a relative test needs. noise_floor() includes the window under judgement, so
        the first chunk of a sustained quiet talker is compared against ITSELF: a whole chunk of
        low-gain speech at -40 dBFS gives p10 ~ -40 and peak -40, "0 dB above the floor", and the
        speech is eaten. Frames before the window cannot be contaminated that way.

        None (so the caller skips the relative test entirely) when there is nothing before
        `t_start`, or when the oldest frame in the pre-window is younger than `min_history_s`: a
        baseline measured over a second or two of a session that has only just begun is not a room
        tone, and guessing one is how real speech gets deleted. 10 s is comfortably longer than any
        single chunk (0.5-15 s) yet short enough that the test is live within the first half minute.
        """
        import numpy as np
        with self._lock:
            f = [(t, d) for t, d in zip(self._t, self._db) if t_start - window_s <= t < t_start]
        if not f or (t_start - f[0][0]) < min_history_s:
            return None
        return float(np.percentile([d for _, d in f], 10))


# Neutral alias: there is one ring per channel now, not just a SYS one. The old name stays
# because app.py, capture_core's docstrings and two test files use it.
EnergyRing = SysEnergyRing


def _silence_floor_db(ring, t_hi, static_db=-45.0):
    """The dBFS floor a ring-fed silence gate should use at time `t_hi`.

    A RAW ring is device audio, which is what the static -45 dBFS floor was calibrated on, so it
    is used unchanged. A PROCESSED ring (the file/upload path: already AGC'd, possibly
    auto-boosted) has no absolute basis, so the floor is derived from the channel's own speech
    level, 30 dB down, clamped to [-55, -35]. Measured basis for the 30: real speech lands
    -8..-20 dBFS while bleed/room tone lands -33..-57, the same gap the absolute -45 encodes for
    a -15 dBFS mic. Falls back to the static floor until there is a speech estimate at all."""
    if getattr(ring, "raw", True):
        return static_db
    lvl = ring.speech_level(t_hi)
    if lvl is None:
        return static_db
    return min(-35.0, max(-55.0, lvl - 30.0))


# --- MIC speech-evidence gate (WP-3) --------------------------------------------------------
# Arms 1 and 2 of _chunk_is_silence both judge a chunk on its LOUDEST raw frame, so one door
# bang, one keyboard click or one cough keeps a whole 15 s chunk of room tone. Measured on a
# 67 min CPU capture: only 8 to 12 min of the MIC channel was near-end speech, yet in a 10 min
# window the peak tests kept 36 of 40 MIC chunks; decoding them cost MORE than the far end
# (RTF 0.89 on MIC against 0.42 on SYS) and produced the loops and the prompt echo that made up
# that session's entire junk budget.
#
# Arm 3 swaps the peak for CONTINUITY: speech occupies frames, a transient does not. A MIC chunk
# is decoded only when at least MIC_EVIDENCE_SECONDS of its raw 100 ms ring frames clear an
# evidence threshold.
#
# The threshold is the room's own p10 floor (measured strictly BEFORE the chunk, so a chunk is
# never judged against itself) plus MIC_EVIDENCE_MARGIN_DB, but never above
# MIC_EVIDENCE_CEILING_DB. The cap is what makes the arm safe in both directions:
#   quiet room - the floor sits well below the cap, so the threshold follows the room down and a
#                quiet talker still clears it (measured floors on the incident capture: -53 to
#                -59 dBFS, thresholds -35 to -39, near-end speech -22.8 dBFS mean);
#   noisy room - the floor rises to within 20 dB of the cap, room frames clear the threshold on
#                their own, and the arm goes inert rather than eating a quiet talker.
# 20 dB: near-end speech on that capture sat about 30 dB above its own p10 room tone, so this is
# the midpoint, leaving 13 to 16 dB of headroom below real speech.
#
# MIC only, by design. The far end is a digital signal at a known level with no microphone, no
# room and no AGC; there is no measured junk to gate there and no basis for these constants.
MIC_EVIDENCE_MARGIN_DB = 20.0     # above this channel's own p10 room tone ...
MIC_EVIDENCE_CEILING_DB = -35.0   # ... but never above this (the noisy-room escape)
MIC_EVIDENCE_SECONDS = 0.5        # of frames that must clear it before the chunk is decoded

# --- the quiet-mic safety valve (WP-7) -------------------------------------------------------
# The gate above is relative, so a quiet mic in a QUIET room is already safe: the threshold rides
# the room down with it. The case it cannot ride down for is a quiet mic in a room loud enough
# that MIC_EVIDENCE_CEILING_DB caps the threshold. Then the bar stops following the room and a
# soft talker can sit just under it, chunk after chunk. That is the one way this arm can cut
# someone off, so it watches for exactly that signature and stands itself down.
#
# The signature, per chunk, is two things TOGETHER: the chunk was skipped, AND it held sustained
# activity in the band just under the threshold (the same "enough frames" test the evidence arm
# uses, applied to [thr - MIC_GATE_NEAR_BAND_DB, thr)). A dead-quiet room fails the second half,
# its frames sit at the floor far below the band, so an empty room never trips the valve. Pairing
# the two is what separates "someone is talking under the bar" from "nobody is talking".
#
# Two steps, one way only. It never escalates back automatically: a user who has been cut off
# once must not be cut off again by the same arm re-arming itself.
#   normal -> gentle : margin 12 dB, evidence 0.3 s. Still a gate, with the bar about 8 dB lower.
#   gentle -> off    : the arm goes inert for the rest of the session.
# Each step clears the window, so the next step is judged on its own fresh MIC_GATE_WINDOW chunks.
MIC_GATE_GENTLE_MARGIN_DB = 12.0  # gentle mode's margin over the room floor (normal: 20)
MIC_GATE_GENTLE_SECONDS = 0.3     # gentle mode's evidence requirement (normal: 0.5)
MIC_GATE_WINDOW = 8               # the valve looks at the last N MIC chunks the arm judged ...
MIC_GATE_TRIP = 6                 # ... and trips when this many were skipped AND near the line
MIC_GATE_NEAR_BAND_DB = 12.0      # "just under the line" = within this far below the threshold

# Arm 1's relative qualifier (MIC only). The absolute -45 dBFS floor was calibrated on a mic at a
# healthy level; a low-gain one puts its speech AND its room under that line together, so the
# absolute test alone cut the talker before arm 3 or the valve could see them at all (measured:
# -18 dB of attenuation, 33 of 40 chunks skipped as "absolute", arm 3 never reached). The floor is
# now a CEILING on a relative test - skip only when the chunk also failed to rise this far above
# the room tone it arrived into - so the cut needs both "quiet in absolute terms" and "nothing
# happened here". 6 dB, not arm 2's 8: this arm is the coarser of the two and should defer.
ABS_FLOOR_MARGIN_DB = 6.0         # above the room floor, and arm 1 keeps the chunk ...
ABS_FLOOR_GENTLE_MARGIN_DB = 3.0  # ... halved once the valve has stepped down to gentle

# Per-source silero VAD options (faster-whisper 1.2.1 VadOptions). The library defaults -
# threshold 0.5, min_speech_duration_ms 0, min_silence_duration_ms 2000, speech_pad_ms 400 -
# only split a chunk on a silence LONGER THAN TWO SECONDS and keep speech regions of any length,
# so a near-silent 15 s mic chunk arrives at the decoder as one merged "speech" region: measured
# on the incident capture, 86% of the MIC channel passed the VAD as speech in 39 such regions.
# MIC gets a tighter set; SYS keeps the library defaults (vad_parameters=None), which is what it
# has always run and what every existing SYS measurement was taken on.
#
# `threshold` is deliberately NOT raised. Raising it is where quiet real speech dies, and the
# problem measured here is region MERGING, not the per-frame speech probability.
MIC_VAD = dict(
    threshold=0.5,                # unchanged, on purpose (see above)
    min_speech_duration_ms=250,   # a sub-quarter-second region is a click or a bump, not a word
    min_silence_duration_ms=500,  # split on a half-second gap instead of waiting for two seconds
    speech_pad_ms=200,            # half the default padding, so a split is not merged back
)


def vad_options_for(source):
    """The VAD options this source decodes with: the tightened set for MIC, None (the
    faster-whisper defaults) for SYS and for anything else."""
    return MIC_VAD if source == "MIC" else None


def _logtxt(text, n=40):
    """How a rejected segment's TEXT appears in the log: a length, not the words.

    The guards below (prompt leak, echo veto, cross-channel echo, loop guard) each logged the
    first 40 characters of what they dropped. That is meeting content, and the diagnostics
    bundle ships the raw log files, so it left the machine in a file the user emails us while
    the UI promises "No transcripts, no notes". What actually diagnoses these guards is the
    count, the source, the timestamp and the reason; the words are a development convenience.

    Set SA_LIVE_LOG_TEXT=1 for a support session (read per call, so it can be flipped without a
    rebuild) and the text comes back. Off by default, and the bundle sanitises these lines
    anyway (diagnostics._sanitise_log) so an old log or a flagged run cannot leak through it.
    """
    text = text or ""
    if os.environ.get("SA_LIVE_LOG_TEXT") == "1":
        return repr(text[:n])
    return f"<{len(text)} chars>"


def _gate_log(engine, source, t_start, skipped, why, stats=None):
    """Return `skipped` after counting it and logging it under SA_LIVE_MIC_GATE_DEBUG.

    Every ring-fed gate decision funnels through here, which is why the MIC session counters and
    the quiet-mic safety valve live here too rather than at each of the arms' return statements.
    `stats` is arm 3's measurement dict (see mic_speech_evidence) and is passed ONLY by arm 3, so
    it doubles as the "this decision is the valve's business" marker.

    Numbers only: never any audio and never any transcribed text. Module-level, and every
    attribute read through getattr, so the half-Engine stubs the gate tests build (which borrow
    _chunk_is_silence unbound) keep working untouched.
    """
    if source == "MIC":
        _mic_gate_count(engine, skipped, stats)
    if getattr(engine, "_mic_gate_debug", False):
        print(f"[gate] {source} @ {t_start:.1f}s {'skip' if skipped else 'keep'} [{why}]", flush=True)
    return skipped


def _mic_gate_count(engine, skipped, stats):
    """Tally one MIC decode decision and, for an arm-3 decision, feed the safety valve.

    Counts EVERY ring-fed MIC decision, whichever arm made it, because "quiet chunks skipped" is
    what the user is shown and a chunk skipped by the absolute arm is just as skipped. The valve,
    by contrast, only ever sees arm 3's own decisions (stats is not None), because arm 3 is the
    only thing it can stand down.

    Module-level and defensive throughout: a stub engine without these attributes gets them
    created, and any surprise leaves the gate itself untouched (a counter must never be able to
    break a transcription).
    """
    try:
        engine.mic_gate_skipped = getattr(engine, "mic_gate_skipped", 0) + (1 if skipped else 0)
        engine.mic_gate_decoded = getattr(engine, "mic_gate_decoded", 0) + (0 if skipped else 1)
        if stats is None:
            return
        hist = getattr(engine, "_mic_gate_recent", None)
        if hist is None:
            hist = deque(maxlen=MIC_GATE_WINDOW)
            engine._mic_gate_recent = hist
        hist.append(bool(skipped) and bool(stats.get("near")))
        _mic_gate_valve(engine, hist)
    except Exception:
        return


def _mic_gate_valve(engine, hist):
    """Step the gate down one level when the last MIC_GATE_WINDOW chunks show the quiet-mic
    signature: MIC_GATE_TRIP of them skipped WITH sustained activity just under the threshold.

    One-way, one step per full window (the window is cleared on every step), and it latches a
    one-shot hint for the UI to toast rather than writing anything into the transcript.
    """
    if len(hist) < MIC_GATE_WINDOW or sum(1 for near_skip in hist if near_skip) < MIC_GATE_TRIP:
        return
    hist.clear()
    if getattr(engine, "_mic_gate_level", "normal") == "normal":
        engine._mic_gate_level = "gentle"
        hint = "gentle"
    else:
        engine._mic_speech_gate = False
        hint = "off"
    engine.mic_gate_hint = hint
    engine.mic_gate_hint_seq = getattr(engine, "mic_gate_hint_seq", 0) + 1
    print(f"[gate] quiet-mic safety valve: mic gate -> {hint}", flush=True)


def mic_speech_evidence(ring, t_start, t_hi,
                        margin_db=MIC_EVIDENCE_MARGIN_DB,
                        ceiling_db=MIC_EVIDENCE_CEILING_DB,
                        need_s=MIC_EVIDENCE_SECONDS,
                        stats=None):
    """(verdict, detail) for arm 3. verdict True = speech evidence, False = none, None = inert.

    Inert whenever the evidence would have to be guessed: no room-tone baseline before the chunk
    (see noise_floor_before - nothing earlier, or under 10 s of history), or no ring frames
    covering the window at all. Never gate on missing evidence.

    `detail` is a short numbers-only string for the debug log: no audio, no text.

    `stats`, when a dict is passed in, is filled with this chunk's measurement for the quiet-mic
    safety valve: floor, thr, n_evid, n_near, need and `near` (True when the frames sitting in
    [thr - MIC_GATE_NEAR_BAND_DB, thr) are sustained enough to have cleared the evidence bar had
    the bar been that much lower). An out-parameter rather than a third return value on purpose:
    the two-tuple is pinned API. Costs one extra pass over frames already in memory, no audio
    maths and no decoding.
    """
    floor = ring.noise_floor_before(t_start)
    if floor is None:
        return None, "no baseline"
    frames = ring.frames_in(t_start, t_hi)
    if not frames:
        return None, "no frames"
    thr = min(floor + margin_db, ceiling_db)
    n_evid = sum(1 for d in frames if d >= thr)
    # need_s worth of ring frames, derived from the ring's own resolution rather than a constant
    # shared with capture_core (10 frames/s today), and never more than a third of a short chunk:
    # a 1 s tail must not have to spend half its frames proving itself.
    dur = max(t_hi - t_start, 1e-6)
    need = min(max(1, int(round(need_s * len(frames) / dur))), max(1, len(frames) // 3))
    if stats is not None:
        n_near = sum(1 for d in frames if thr - MIC_GATE_NEAR_BAND_DB <= d < thr)
        stats.update(floor=floor, thr=thr, n_evid=n_evid, n_near=n_near, need=need,
                     frames=len(frames), near=n_near >= need)
    return (n_evid >= need,
            f"floor={floor:.0f} thr={thr:.0f} evid={n_evid}/{len(frames)} need={need}")


def sys_echo_veto(mic_audio, sys_ring, abs_start, abs_end, word_count, sr=16000,
                  frame_ms=100, tol=0.3, active_floor=-50.0, min_coverage=0.60,
                  margin_db=10.0, mic_ceiling=-28.0, *, mic_ring=None):
    """Decide whether a MIC segment is far-end bleed (echo) that should be dropped.

    Conservative by construction so it (almost) never eats real speech: it fires only when the far
    end was active across most of the segment AND the mic's LOUDEST frames still sit well below the
    far end AND below an absolute ceiling. Only a sub-0.5s blip is auto-exempt now (a genuine short reply survives via its above-ceiling mic energy), and a
    missing SYS reference fails safe (keep). Post-ASR, so it cannot un-blend a mixed segment - hence
    the coverage floor: it only drops segments that are overwhelmingly far-end. Returns
    (drop: bool, reason: str). Thresholds are the tuned starting points from the design review.

    mic_ring (keyword-only): a raw-mic EnergyRing. When supplied, the mic's loud-frame level comes
    from its RAW pre-APM frames instead of the chunk samples, which is what makes this relative
    test honest under live AGC - the chunk is gain-boosted while the SYS reference never is, so a
    boosted-but-silent mic used to clear the -28 dBFS ceiling and dodge the veto. Omitted (or with
    no frames for the window) it behaves exactly as before.
    """
    import numpy as np
    dur = abs_end - abs_start
    # Only a sub-0.5s blip is auto-exempt. The old `word_count <= 2` exemption also kept short
    # segments, but that let quiet far-end bleed fragments ("Thank you", "ja") survive on a silent
    # mic; short segments now face the energy test too, and a genuinely-spoken short reply still
    # stays because its mic energy is above the ceiling (A/B-verified on real recordings, v1.8.2).
    if dur < 0.5:
        return False, "short"
    mic_p90 = None                                # the mic's loud frames; low => never really spoke
    micsrc = ""
    if mic_ring is not None:
        _mf = mic_ring.frames_in(abs_start - tol, abs_end + tol)
        if _mf:
            mic_p90 = float(np.percentile(_mf, 90))
            micsrc = " micsrc=ring"
    if mic_p90 is None:
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
        mic_p90 = float(np.percentile(mdb, 90))
    sys = sys_ring.frames_in(abs_start - tol, abs_end + tol)
    if not sys:
        return False, "nosys"                     # no reference -> keep (fail safe)
    active = [d for d in sys if d > active_floor]
    coverage = len(active) / len(sys)
    if coverage < min_coverage:
        return False, f"cov={coverage:.2f}"
    sys_p70 = float(np.percentile(active, 70))
    drop = (sys_p70 - mic_p90) >= margin_db and mic_p90 < mic_ceiling
    return drop, f"cov={coverage:.2f} sysP70={sys_p70:.0f} micP90={mic_p90:.0f}{micsrc}"


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

    A run of the same token longer than max_run is truncated to its first max_run
    tokens (not to one): "Hekkaan." x20 -> "Hekkaan. Hekkaan. Hekkaan." Legitimate
    emphasis like "baie baie baie" (a run of 3) is left alone.
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


# ---------------------------------------------------------------------------
# User-prompt-leak filter (WP-1)
#
# initial_prompt is decoder conditioning, and on silence/noise Whisper emits it
# back verbatim. _HALLUCINATION_RE above only hand-codes the AF anchor's leaks;
# nothing guarded the USER prompt (names, jargon), which on a 36-minute English
# call produced 90 junk lines (14% of the transcript) built from the two names in
# the pre-meeting context field.
#
# Two deliberately different matching modes:
#   Mode A (user prompt) - unit/coverage matching. The prompt is a SHORT list of
#     proper nouns, so a segment that is (almost) nothing but prompt units is junk.
#   Mode B (AF anchor, and any over-long user prompt) - contiguous n-gram only.
#     The anchor is ordinary Afrikaans ("ons", "kinders", "baie", "vergadering");
#     token-membership matching against it would delete real speech. This is the
#     single biggest correctness trap here: NEVER token-match the anchor.
_LEAK_COVERAGE = 0.80          # >=80% of a segment's content tokens must be prompt units
_LEAK_REPEAT = 2               # same unit twice in one segment...
_LEAK_REPEAT_COVERAGE = 0.60   # ...AND that unit must still be most of the segment (see A2)
_LEAK_MIN_CONTENT = 2          # A1 needs 2+ content tokens unless the matched unit is multi-token
_LEAK_NGRAM = 5                # anchor/long-prompt match length; below 5 common Afrikaans collides
_LEAK_NGRAM_COVERAGE = 0.75    # matched n-gram spans must cover this much of the content tokens
_LEAK_UNIT_MAX = 4             # a "unit" is a name/jargon term; longer -> prose, n-gram it instead
_LEAK_LONG_PROMPT = 60         # a prompt longer than this is prose -> n-gram-only (safety valve)

# Mode C (WP-3), the short anchor-echo drop. Modes A and B both need the leak to be MOST of the
# segment: A wants 0.80 coverage by whole prompt units, B wants 0.75 coverage by 5-grams. The
# residue they miss is the short scatter - three or four of the anchor's own distinctive words
# in a row, in no order the anchor ever used ("Afrikaans, kodewissel, dankie"), which covers no
# n-gram and matches no unit. Whisper emits these on non-speech, never mid-conversation.
#
# The terms are DERIVED from AF_ANCHOR_PROMPT, never listed a second time: its tokens of at least
# _LEAK_ANCHOR_MINLEN characters. Six is where the anchor stops being ordinary conversation: it
# keeps kodewisseling / vergadering / besigheid / kollegas / afrikaans / engels / sprekers /
# dankie and leaves out baie, nogal, ons, hulle, julle, sjoe, more, tog. A token also matches by
# prefix in either direction, so the truncation Whisper actually emits ("kodewissel") counts.
#
# The anchor labels its own two halves, and the split matters. Everything after "Algemene woorde"
# ("common words") is, by the constant's own declaration, ordinary Afrikaans: "ons kinders is
# baie lekker vandag" is a real sentence built entirely from it. So at least one hit must come
# from the INSTRUCTION half - the half that talks ABOUT the transcription (afrikaans, engels,
# kodewisseling, sprekers, nederlands, gesprek) and that a speaker has no reason to recite.
#
# Safety, measured 2026-09-03 on the incident capture: 0 of 53 segments of genuine Afrikaans
# (the GPU large-v3 far-end reference, 918 words, plus both far-end CPU decodes) trip this, while
# it catches the prompt-echo lines in the near-end junk. Four bounds buy that: only short
# segments, at least two DISTINCT anchor terms, at least one of them from the instruction half,
# and an escape for any segment still carrying _LEAK_ANCHOR_MIN_OWN words of the speaker's own.
_LEAK_ANCHOR_SPLIT = "Algemene woorde"   # the anchor's own label for its ordinary-Afrikaans half
_LEAK_ANCHOR_MINLEN = 6        # anchor tokens shorter than this are ordinary speech, never terms
_LEAK_ANCHOR_MAX_TOKENS = 12   # only short segments; real sentences are longer than the scatter
_LEAK_ANCHOR_MIN_HITS = 2      # distinct spoken terms; one is a speaker legitimately saying it
_LEAK_ANCHOR_MIN_OWN = 5       # words NOT in the prompt that keep any segment, however many hits

# Short, language-agnostic (EN+AF) filler list, derived from the observed leaks: these are
# the words that pad a leak ("and Danica Freimond.", "... , yeah.") and must not count
# against coverage. Written with the same normalisation as segments ("'n" -> "n").
_LEAK_FILLERS = frozenset(
    "and en the die a n of van is dit it i ek yeah ja yes no nee ok okay um uh uhm mm mmm hmm so".split()
)


def _norm_tokens(text):
    """Lowercase, punctuation-stripped, whitespace-collapsed token list.

    Shared by the prompt-leak filter and the cross-segment loop guard so both compare
    text the same way ("Bye." == "bye", "Suid-Afrikaanse" -> "suid afrikaanse")."""
    return re.sub(r"[^\w\s]", " ", (text or "").lower()).split()


def _content_tokens(toks):
    """Positions in `toks` that are not fillers (the tokens that carry meaning)."""
    return [i for i, t in enumerate(toks) if t not in _LEAK_FILLERS]


def _coverage(toks, covered):
    """Fraction of `toks`'s CONTENT tokens that `covered` marks as prompt-derived.

    The one measure both matching modes decide on: "how much of what this speaker
    actually said came out of the prompt?". Fillers are excluded so a leak padded with
    "and"/"yeah" still scores 1.0. 0.0 when there is nothing to measure."""
    content = _content_tokens(toks)
    if not content:
        return 0.0
    return sum(1 for i in content if covered[i]) / len(content)


# --- Cross-channel echo veto, arm 2: energy-armed fuzzy echo ---------------------------------
#
# Arm 1 (sys_echo_veto, above) fires only on a CONTINUOUSLY loud far end (coverage >= 0.60), and
# it should: energy is its only evidence, so it cannot afford less. The bleed it therefore refuses
# is the GAPPY far end - a remote speaker who pauses mid-sentence leaves coverage below the floor
# while the mic is still re-hearing them, and the fabricated MIC line survives.
#
# This arm supplies the missing evidence from the TEXT. It keeps arm 1's asymmetry test (a mic that
# never really spoke, a far end much louder) and adds: the line substantially echoes what the far
# end actually said in the surrounding seconds. Both halves must agree before anything is dropped.
#
# Offline-validated on 2026-07-29 against two real meetings (spprac + ashley wavs): 4/4 and 3/3
# junk precision, zero adjudicated real-speech loss. Every threshold below comes from that run.
# Lives here rather than beside arm 1 because it needs the token helpers defined just above.
_XCHAN2_MIN_DUR = 0.5         # seconds; same sub-blip exemption as arm 1
_XCHAN2_MIC_CEILING = -27.0   # dBFS; p90 of the RAW mic frames must sit below this ("never spoke")
_XCHAN2_MARGIN_DB = 10.0      # dB the far end's p90 must sit ABOVE the mic's p90
_XCHAN2_MIN_TOKENS = 4        # content words; a short line is never judged on text overlap
_XCHAN2_MIN_OVERLAP = 0.30    # fraction of the MIC line's content words the far end already said
_XCHAN2_TEXT_WINDOW = 6.0     # +/- seconds around the MIC span: the ring SCAN bound, nothing more
_XCHAN2_SIMUL_PAD = 1.0       # seconds the MIC span is grown by before demanding the SYS span
                              # INTERSECT it. Bleed is simultaneous audio: the far end has to have
                              # been talking WHILE the mic allegedly did, or the mic was not
                              # re-hearing it. The pad absorbs honest segmentation slop (ASR
                              # boundaries, the 100 ms ring cadence, a chunk cut) without admitting
                              # sequential dialogue - a real reply to what the far end just finished
                              # saying shares most of its words by nature ("Please send the updated
                              # budget tomorrow" / "I will send the updated budget tomorrow") and
                              # would be dropped by a proximity-only rule, however quiet the mic.
_XCHAN2_TOL = 0.3             # window padding for the energy lookups (arm 1's `tol`)


def _content_words(text):
    """The meaning-carrying WORDS of `text`, normalised: what _content_tokens indexes.

    _content_tokens returns positions (its callers score coverage per position); the echo arm
    needs the words themselves to match against the far end."""
    toks = _norm_tokens(text)
    return [toks[i] for i in _content_tokens(toks)]


class SysTextRing:
    """Content words of recently PUBLISHED SYS segments: the echo reference for arm 2.

    Deliberately NOT RecentEmissions, which looks similar and is wrong for this job three ways:
    it is cleared on a model change AND on a live device switch (either would silently disarm this
    veto for the following minutes), it only fills while the loop guard is enabled, and it records
    SUPPRESSED candidates - text that was never real far-end speech and must never serve as proof
    that the far end said something. This ring holds published SYS only, and is cleared only on
    engine start.

    Bounded twice: `maxlen` entries and a retention window. Measured SYS cadence is ~11 s per
    segment, so 8 entries / 20 s comfortably span the +/- 6 s the veto looks at. Locked like
    SysEnergyRing: both ends are the transcription worker today, but the ring is reachable from
    the Engine, so a future reader (a debug endpoint) cannot tear the deque.
    """

    def __init__(self, maxlen=8, retain_s=20.0):
        self._items = deque(maxlen=maxlen)   # (t_start, t_end, [content words])
        self._retain = retain_s
        self._lock = threading.Lock()

    def clear(self):
        """Forget everything. Engine start only - see the class note."""
        with self._lock:
            self._items.clear()

    def add(self, t_start, text, t_end=None):
        """Record a published SYS segment. A line with no content words is not a reference.

        `t_end` is kept because WHEN the far end was talking is half the evidence: the veto only
        counts a SYS line that overlaps the MIC segment in time (see near()). It defaults to
        t_start, i.e. a zero-length span, which the overlap test then almost always rejects - a
        caller that does not know the end contributes no echo evidence, which is the safe way to
        be ignorant.
        """
        words = _content_words(text)
        if not words:
            return
        t = float(t_start)
        with self._lock:
            self._items.append((t, t if t_end is None else float(t_end), words))
            cut = t - self._retain
            while self._items and self._items[0][0] < cut:
                self._items.popleft()

    def near(self, t_lo, t_hi, span=None):
        """Content-word lists of the SYS segments whose START falls in [t_lo, t_hi].

        `span` as (lo, hi) additionally requires the SYS segment's OWN span to INTERSECT [lo, hi]:
        the simultaneity test that separates bleed (the far end audible while the mic recorded)
        from ordinary turn-taking (the far end finished, then somebody replied). [t_lo, t_hi] stays
        the cheap ring-scan bound; `span` is the actual rule.
        """
        with self._lock:
            items = list(self._items)
        out = []
        for t0, t1, w in items:
            if not (t_lo <= t0 <= t_hi):
                continue
            if span is not None and (t0 > span[1] or t1 < span[0]):
                continue
            out.append(w)
        return out


def fuzzy_echo_veto(text, abs_start, abs_end, mic_ring, sys_ring, sys_text, *,
                    min_dur=_XCHAN2_MIN_DUR, mic_ceiling=_XCHAN2_MIC_CEILING,
                    margin_db=_XCHAN2_MARGIN_DB, min_tokens=_XCHAN2_MIN_TOKENS,
                    min_overlap=_XCHAN2_MIN_OVERLAP, window_s=_XCHAN2_TEXT_WINDOW,
                    tol=_XCHAN2_TOL, simul_pad=_XCHAN2_SIMUL_PAD):
    """Drop a MIC segment as bleed-echo fabrication iff ALL of:

      1. it lasts at least `min_dur`;
      2. the mic's own loud frames (p90 of the RAW mic ring over the padded span) sit below
         `mic_ceiling` - on its calibrated absolute basis, this speaker never actually spoke here;
      3. the far end was at least `margin_db` louder, measured as p90 of ALL SYS frames in the same
         window. Plain p90, NOT arm 1's p70-of-the-active-subset: a gappy far end is precisely the
         case this arm exists for, so its quiet frames must not be filtered out of the reference
         first (p90 already ignores the gaps by construction);
      4. the line has at least `min_tokens` content words. This is the decisive protection: it is
         what keeps "Yeah, it's a pleasure." (2 content words) however quiet the mic was;
      5. at least `min_overlap` of those content words were said by the far end in a published SYS
         segment whose span OVERLAPS this one (the MIC span grown by `simul_pad` on each side),
         counted with dedup's fuzzy one-to-one matcher so the mic's mis-hearings still count as the
         same word. The denominator is the MIC line: "how much of what this speaker allegedly said
         came from the far end?". The overlap requirement is physical, not stylistic: bleed is the
         mic hearing audio that is playing AT THE SAME TIME. `window_s` only bounds the ring scan;
         on its own it would make sequential dialogue self-incriminating, because a real reply
         repeats the words of the thing it is replying to.

    Every missing input fails safe (keep): no mic ring, no mic or SYS frames for the window, no SYS
    ring, no published SYS text. Returns (drop, own_p90, margin, overlap, why); the three numbers
    are None until measured and all three are set whenever `drop` is True.
    """
    import numpy as np
    if abs_end - abs_start < min_dur:
        return False, None, None, None, "short"
    if mic_ring is None:
        return False, None, None, None, "nomicring"
    own = mic_ring.frames_in(abs_start - tol, abs_end + tol)
    if not own:
        return False, None, None, None, "nomic"
    own_p90 = float(np.percentile(own, 90))
    if own_p90 >= mic_ceiling:
        return False, own_p90, None, None, "loud"
    if sys_ring is None:
        return False, own_p90, None, None, "nosysring"
    far = sys_ring.frames_in(abs_start - tol, abs_end + tol)
    if not far:
        return False, own_p90, None, None, "nosys"
    margin = float(np.percentile(far, 90)) - own_p90
    if margin < margin_db:
        return False, own_p90, margin, None, f"marg={margin:.1f}"
    mic_words = _content_words(text)
    if len(mic_words) < min_tokens:
        return False, own_p90, margin, None, f"tok={len(mic_words)}"
    if sys_text is None:
        return False, own_p90, margin, None, "nosystext"
    overlap = 0.0
    for words in sys_text.near(abs_start - window_s, abs_end + window_s,
                               span=(abs_start - simul_pad, abs_end + simul_pad)):
        # dedup._shared_count: maximum one-to-one pairing, exact or near-spelled (ratio >= 0.78).
        # Reused rather than reimplemented so "same word" means the same thing live and at the
        # end-of-session strip; private only because dedup has had no outside caller until now.
        overlap = max(overlap, dedup._shared_count(mic_words, words) / len(mic_words))
        if overlap >= min_overlap:
            break                       # already decided; skip the remaining matchings
    if overlap < min_overlap:
        return False, own_p90, margin, overlap, f"ov={overlap:.2f}"
    return True, own_p90, margin, overlap, f"ov={overlap:.2f}"


class PromptLeakMatcher:
    """Detects a segment that is regurgitated initial_prompt rather than speech.

    Built once per prompt/language combination (see Engine._rebuild_prompt_leak) so the
    per-segment cost is a few tuple comparisons. Inert when there is no prompt.
    """

    def __init__(self, user_prompt, anchor=None):
        self._units = []      # Mode A: normalised token tuples, matched as contiguous n-grams
        self._vocab = set()   # Mode A: every token of those units (see the F7 note in is_leak)
        self._ngrams = set()  # Mode B: every _LEAK_NGRAM-length n-gram of the anchor / long prompt
        # Mode C: the anchor's terms, the subset of them from its instruction half, and every
        # token of the whole prompt (anchor plus user prompt) so "words of the speaker's own"
        # means own, not merely non-anchor.
        head = (anchor or "").split(_LEAK_ANCHOR_SPLIT)[0]
        self._anchor_terms = sorted({t for t in _norm_tokens(anchor) if len(t) >= _LEAK_ANCHOR_MINLEN})
        self._anchor_head = {t for t in self._anchor_terms if t in set(_norm_tokens(head))}
        self._prompt_vocab = set(_norm_tokens(anchor)) | set(_norm_tokens(user_prompt))
        toks = _norm_tokens(user_prompt)
        if len(toks) > _LEAK_LONG_PROMPT:
            # Safety valve: a pasted agenda, not a name list. Unit/coverage matching over a
            # large vocabulary is where false positives come from; prose leaks as verbatim
            # runs anyway, so n-gram matching is both safer and sufficient.
            self._add_ngrams(toks)
        elif toks:
            seen = set()
            for part in re.split(r"[,;\n]", user_prompt):
                self._add_unit(tuple(_norm_tokens(part)), seen)
            # R2: names written without commas ("Danica and Sean Freimond") still match.
            self._add_unit(tuple(toks), seen)
        self._add_ngrams(_norm_tokens(anchor))

    def _add_unit(self, unit, seen):
        """File one comma-separated prompt part as a Mode A unit, or as Mode B n-grams.

        A unit is meant to be a NAME or a jargon term. /api/start concatenates the user's
        saved default_context into the same prompt string (web/app.py:635), so a free-form
        instruction sentence ("Please transcribe the meeting exactly as spoken") arrives here
        as one "unit" alongside the names. Unit/coverage matching a sentence deletes real
        speech that merely re-uses its words, which is the exact trap Mode B exists to avoid,
        so anything longer than _LEAK_UNIT_MAX tokens goes through the n-gram path instead.
        Short units keep full Mode A treatment."""
        if not unit or unit in seen:
            return
        seen.add(unit)
        if len(unit) > _LEAK_UNIT_MAX:
            self._add_ngrams(unit)
        else:
            self._units.append(unit)
            self._vocab.update(unit)

    def _add_ngrams(self, toks):
        for i in range(len(toks) - _LEAK_NGRAM + 1):
            self._ngrams.add(tuple(toks[i:i + _LEAK_NGRAM]))

    def is_leak(self, text):
        toks = _norm_tokens(text)
        if not toks:
            return False
        if self._ngram_leak(toks):
            return True
        if self._anchor_echo(toks):
            return True
        if not self._units:
            return False
        # Mode A: mark every token covered by a contiguous occurrence of a prompt unit.
        covered = [False] * len(toks)
        matched_len = 0
        repeated = False
        for unit in self._units:
            n = len(unit)
            hits = 0
            for i in range(len(toks) - n + 1):
                if tuple(toks[i:i + n]) == unit:
                    hits += 1
                    for j in range(i, i + n):
                        covered[j] = True
            if hits >= _LEAK_REPEAT:
                repeated = True
            if hits:
                matched_len = max(matched_len, n)
        if not matched_len:
            return False
        # F7 (real-audio validation gap): Whisper regurgitates the prompt from wherever the
        # decoder happens to enter it, so a leak often starts MID-unit - "Freimond, Sean
        # Freimond" for the prompt "Danica Freimond, Sean Freimond". Whole-unit matching covers
        # only the intact "Sean Freimond", scoring 0.667 and keeping the line (0 of 20 such
        # lines caught in the replay harness). Once at least one WHOLE unit has matched (that
        # is the gate - never on vocabulary alone), any other token from the unit vocabulary
        # counts as covered too, which reads the dangling half correctly. Deliberately Mode A
        # only: the anchor / long-prompt vocabulary is ordinary speech, and token-membership
        # matching against THAT is the trap Mode B exists to avoid.
        for i, t in enumerate(toks):
            if t in self._vocab:
                covered[i] = True
        content = _content_tokens(toks)
        if not content:
            return False
        cov = _coverage(toks, covered)
        # A2: nobody says the same name twice in one 3-second breath - PROVIDED the repeat is
        # most of the segment. A correction ("I said Sean Freimond, not Shawn Freemont, Sean
        # Freimond") repeats the name deliberately and is real speech, so the repeat shortcut
        # needs its own (lower than A1) coverage floor rather than none at all.
        if repeated and cov >= _LEAK_REPEAT_COVERAGE:
            return True
        # A1: the segment is (almost) nothing but prompt units. Real speech that merely
        # contains a name sits below 0.30, so the 0.80 floor leaves a wide dead band.
        if cov < _LEAK_COVERAGE:
            return False
        return len(content) >= _LEAK_MIN_CONTENT or matched_len > 1

    def _anchor_echo(self, toks):
        """Mode C: a SHORT segment that is a scatter of the anchor's own distinctive terms.

        Additive to modes A and B and deliberately narrow (see the _LEAK_ANCHOR_* notes): a
        segment of at most _LEAK_ANCHOR_MAX_TOKENS tokens carrying at least
        _LEAK_ANCHOR_MIN_HITS DISTINCT spoken words that are anchor terms, at least one of them
        matching the anchor's instruction half, and fewer than _LEAK_ANCHOR_MIN_OWN tokens
        that are not in the prompt at all.
        Inert on a non-af session, where there is no anchor.
        """
        if not self._anchor_terms or len(toks) > _LEAK_ANCHOR_MAX_TOKENS:
            return False
        hit, head = set(), False
        for t in set(toks):
            for term in self._anchor_terms:
                # Either direction, because Whisper truncates the anchor's long words as often
                # as it extends them ("kodewissel" for "kodewisseling", "afrikaanse" for
                # "afrikaans"). The MINLEN floor on both sides keeps this off short words.
                if t == term or (len(t) >= _LEAK_ANCHOR_MINLEN
                                 and (t.startswith(term) or term.startswith(t))):
                    # Count the SPOKEN token, never the terms it matched: prefix matching lets a
                    # single "Afrikaans" hit both "afrikaans" and "afrikaanse", and counting terms
                    # would turn one word into the two the drop requires.
                    hit.add(t)
                    head = head or term in self._anchor_head
        if len(hit) < _LEAK_ANCHOR_MIN_HITS or not head:
            return False
        own = sum(1 for t in toks if t not in self._prompt_vocab)
        return own < _LEAK_ANCHOR_MIN_OWN

    def _ngram_leak(self, toks):
        """Mode B: the anchor / long prompt / long unit, matched as contiguous n-grams.

        A single >=_LEAK_NGRAM run is NOT enough on its own. The anchor is ordinary Afrikaans,
        so a genuine sentence can legitimately contain one 5-gram of it ("...net soos dit
        gepraat word...") and still be mostly the speaker's own words. A real regurgitation is
        the prompt end to end, so require the union of the matched spans to cover
        _LEAK_NGRAM_COVERAGE of the segment's content tokens."""
        if not self._ngrams:
            return False
        covered = [False] * len(toks)
        hit = False
        for i in range(len(toks) - _LEAK_NGRAM + 1):
            if tuple(toks[i:i + _LEAK_NGRAM]) in self._ngrams:
                hit = True
                for j in range(i, i + _LEAK_NGRAM):
                    covered[j] = True
        return hit and _coverage(toks, covered) >= _LEAK_NGRAM_COVERAGE


# ---------------------------------------------------------------------------
# Cross-segment repetition guard (WP-2)
#
# Every existing loop guard (_collapse_repetition, _is_phrase_loop) is SEGMENT
# scoped: it only sees one seg.text. The incident's worst runs are spread ACROSS
# segments and chunks and are therefore invisible to all of them - "Bye." x22 one
# per second, and an "and" / "Danica Freimond" alternation running 22 pairs at two
# per second. This is the missing cross-segment view.
#
# Shape is driven entirely by backchannel safety: a genuine "ja. ja. ja." must
# never lose anything, and a reader must still see evidence of whatever got
# suppressed. Hence: the first 4 cycles ALWAYS publish, only short lines qualify,
# the run must be dense, and MIC/SYS histories are never shared (a listener's "ja"
# must not be cancelled by a speaker's "ja").
_LOOP_MAX_PERIOD = 3          # covers the observed p=1 and p=2 with headroom
_LOOP_MIN_CYCLES = 4          # cycles that always publish before suppression starts
_LOOP_MAX_TOKENS = 4          # per element; a 5+ word line repeating is likelier a real refrain
_LOOP_MAX_S_PER_CYCLE = 3.0   # observed loops 0.5-1.0 s; backchannel spacing is 5-20 s
_LOOP_HISTORY = 16            # 4 cycles at p=3 plus slack; bounded memory


class RecentEmissions:
    """Per-source rolling history of recent segments, used to spot a repeating cycle.

    Suppression is SILENT (stdout log only, no transcript marker): a suppressed loop lost
    nothing real, and the first _LOOP_MIN_CYCLES cycles stay in the transcript as evidence.

    Notices and backpressure markers go straight to _fanout and never reach _route, so they
    can never enter this history.
    """

    def __init__(self, maxlen=_LOOP_HISTORY):
        self._maxlen = maxlen
        self._hist = {}   # source -> deque[(norm_text, n_content_tokens, t_start)]

    def clear(self):
        """Forget everything. Called on engine start and on a live model/language change,
        which legitimately changes output style and must not false-trigger the guard."""
        self._hist.clear()

    def observe(self, source, text, t_start):
        """Record a candidate and return the cycle period that suppresses it (0 = publish).

        Suppressed candidates are recorded too. They are part of the run, and dropping them
        from the history would break the phase of an alternating (p>=2) loop and would stall
        the density measurement on the tail of a run, so only the first few cycles past the
        threshold would be caught. Genuinely different speech still enters the history and
        breaks the cycle, which is what disarms the guard.
        """
        toks = _norm_tokens(text)
        cand = (" ".join(toks), len(_content_tokens(toks)), float(t_start))
        hist = self._hist.setdefault(source, deque(maxlen=self._maxlen))
        p = self._period(hist, cand)
        hist.append(cand)
        return p

    def _period(self, hist, cand):
        if not cand[0] or cand[1] > _LOOP_MAX_TOKENS:
            return 0
        for p in range(1, _LOOP_MAX_PERIOD + 1):
            need = p * _LOOP_MIN_CYCLES
            if len(hist) < need:
                continue
            run = list(hist)[-need:] + [cand]          # _LOOP_MIN_CYCLES complete cycles
            if any(e[1] > _LOOP_MAX_TOKENS for e in run):
                continue
            if any(run[i][0] != run[i + p][0] for i in range(len(run) - p)):
                continue
            if (cand[2] - run[0][2]) / _LOOP_MIN_CYCLES > _LOOP_MAX_S_PER_CYCLE:
                continue                               # too slow to be a decoder loop
            return p
        return 0


@dataclass
class Segment:
    source: str       # "MIC" or "SYS"
    t_start: float    # session-relative seconds
    t_end: float
    text: str


def _chunk_seconds(item, sr=16000):
    """Audio seconds in a queued chunk. 0.0 for anything that is not real audio (test stubs feed
    ints and strings), so the backlog accounting can never raise on the worker thread."""
    try:
        return len(item[1]) / float(sr)
    except Exception:
        return 0.0


def _mmss(seconds):
    """Session-relative seconds as m:ss, for notices a reader has to line up with the transcript."""
    try:
        s = max(0, int(round(seconds)))
    except Exception:
        return "?"
    return f"{s // 60}:{s % 60:02d}"


class Engine:
    def __init__(self, tier, language="af", initial_prompt=None, cpu_threads=8, beam_size=None, adaptive=True, engine="auto"):
        self.engine = (engine or "auto").lower()
        if tier not in TIER_CONFIG:
            raise ValueError(f"Unknown tier {tier!r}; choose from {list(TIER_CONFIG)}")
        self.tier = tier
        self.language = language
        # beam_size=None means "this device's measured default": 1 on CPU (see CPU_BEAM_SIZE,
        # roughly halves the cost for about +0.02 WER), 5 everywhere else. An explicit value from
        # the caller is always honoured, on either device.
        self.beam_size = beam_size if beam_size is not None else (
            CPU_BEAM_SIZE if TIER_CONFIG[tier]["device"] == "cpu" else DEFAULT_BEAM_SIZE)
        # adaptive=True (live): cut beam + downgrade the model under backlog to keep
        # up with real time. adaptive=False (file import): not real time, so never
        # trade quality for speed - keep the chosen model and full beam size.
        self.adaptive = adaptive

        # The Afrikaans anchor (af only) plus any user prompt. Keep the raw user prompt too, so a
        # live language change (request_change) can recompose the anchor for the new language.
        self._user_prompt = initial_prompt
        self.initial_prompt = _compose_prompt(language, initial_prompt)
        # Matcher for prompt content leaking back out as "speech". Built from the same
        # (prompt, language) pair _compose_prompt just used, and rebuilt wherever that is.
        self._rebuild_prompt_leak(initial_prompt, language)
        cfg = TIER_CONFIG[tier]
        # size = the stock Whisper size for this tier (drives the CPU downgrade ladder); model_name
        # = the concrete model loaded, which is the Fluister tune of that size for an Afrikaans
        # session and stock Whisper otherwise. family/is_fluister label the active engine honestly.
        self.size = cfg["model"]
        self.model_name, self.family = resolve_model(self.size, language, engine)
        self.is_fluister = self.family == "fluister"
        # The decode token: Swivuriso (and any South African code on any family) runs on
        # auto-detect; every other explicit code is forced as-is (see decode_language).
        self.language = decode_language(self.family, language)

        # Cached, local-only load (no network revalidation) and reused across sessions, so a
        # warmed model makes Begin instant. See load_model / warm_up_async above.
        self.model = load_model(self.model_name, cfg["device"], cfg["compute_type"], cpu_threads=cpu_threads)
        self._is_cpu = cfg["device"] == "cpu"
        # The actual compute backend ("cuda"/"cpu"/"mlx"), kept alongside _is_cpu so the
        # web layer can report the device honestly instead of reconstructing it. _is_cpu
        # stays the ladder gate: mlx is not CPU, so the RTF downgrade never fires on it.
        self._device = cfg["device"]
        self._compute_type = cfg["compute_type"]
        self._cpu_threads = cpu_threads
        self._rtf = deque(maxlen=DOWNGRADE_WINDOW)  # recent real-time factors (CPU downgrade)
        # ── live ladder + shed valve state (CPU only) ──
        # Chunks the shed valve pulled back out of the queue so it could drop the OLDEST first.
        # Only the worker touches it, producers only ever put on the queue, and it is always
        # drained before the queue, so it holds strictly older chunks and FIFO order is preserved.
        self._front = deque()
        self._last_rung_change = time.monotonic()   # DOWNGRADE_MIN_SECONDS is measured from here
        self._cold_decode = True      # first decode on a freshly built model: its RTF is load cost
        self._last_feed = {}          # source -> monotonic of that source's previous chunk
        self._swap = None             # at most ONE next-rung model being built off the worker
        self.shed_seconds = 0.0       # total audio the shed valve dropped this session
        self.shed_events = 0
        self.subscribers = []
        self.on_downgrade = None               # optional callback(old_size, new_size), fired on the
                                               # worker thread after a successful CPU auto-downgrade.
                                               # Decoupled like subscribe(): the web layer sets it to
                                               # surface the downgrade (banner + one-time toast); the
                                               # engine stays ignorant of app.py/notify/STATE.
        self._pending_mic = []                # [(release_monotonic, Segment)] held by MIC_PUBLISH_DELAY
        self.sys_env = None                   # optional EnergyRing (far end) -> enables the MIC echo veto
        self.mic_env = None                   # optional EnergyRing (RAW near end) -> gain-invariant
                                              # mic levels for the silence gate + the echo veto
        self._raw_mic_ring = raw_mic_ring_on()
        self._xchan_veto = os.environ.get("SA_LIVE_XCHAN_VETO", "1") != "0"
        # Arm 2 of the echo veto (energy-armed fuzzy echo) has its own switch: SA_LIVE_XCHAN_VETO
        # continues to govern arm 1 alone, so either can be turned off in a support session without
        # losing the other. "0" makes arm 2 fully inert, ring included.
        self._xchan_veto2 = os.environ.get("SA_LIVE_XCHAN_VETO2", "1") != "0"
        self._silence_gate = os.environ.get("SA_LIVE_SILENCE_GATE", "1") != "0"
        # Arm 3 of the silence gate (MIC speech evidence) and its per-chunk decision log. The
        # arm has its own switch so a support session can restore the peak-only gate without
        # losing arms 1 and 2; the log is off by default because it is one line per MIC chunk.
        self._mic_speech_gate = os.environ.get("SA_LIVE_MIC_SPEECH_GATE", "1") != "0"
        self._mic_gate_debug = os.environ.get("SA_LIVE_MIC_GATE_DEBUG", "0") != "0"
        # The gate is live-switchable now (set_mic_gate / mic_gate_state), so the env var above is
        # only the STARTING value; the web layer overrides it from the saved setting and the user
        # can flip it mid-meeting. _mic_speech_gate stays the on/off flag under its own name so the
        # worker keeps reading one attribute per chunk (no lock: a bool assignment is atomic and
        # the next chunk picks it up, which is exactly the contract).
        self._mic_gate_level = "normal"   # "normal" | "gentle", stepped down by the safety valve
        self._mic_gate_recent = deque(maxlen=MIC_GATE_WINDOW)  # the valve's window (near-miss skips)
        self.mic_gate_skipped = 0         # MIC chunks not decoded this session
        self.mic_gate_decoded = 0         # MIC chunks decoded this session
        # One-shot hint for the UI, latched by the valve and pulled (never pushed) by /api/status:
        # a sequence number the client compares against the last one it showed. Pull, so the engine
        # stays ignorant of the web layer and nothing has to be cleared.
        self.mic_gate_hint = None         # None | "gentle" | "off"
        self.mic_gate_hint_seq = 0
        self._prompt_leak_on = os.environ.get("SA_LIVE_PROMPT_LEAK_GUARD", "1") != "0"
        self._loop_guard_on = os.environ.get("SA_LIVE_LOOP_GUARD", "1") != "0"
        self._recent = RecentEmissions()   # cross-segment loop history, per source
        self._sys_text = SysTextRing()     # published SYS text: the echo reference for arm 2
        self._queue = queue.Queue(maxsize=32)
        self._stop = threading.Event()    # shutting down: stop accepting new audio
        self._abort = threading.Event()   # hard abort: discard the backlog instead of draining
        self._busy = False                # True while a chunk is mid-transcription (for pending())
        self._dropped = 0                 # chunks dropped to backpressure since last reported
        self._change_lock = threading.Lock()  # guards _pending_change (API thread queues, worker applies)
        self._pending_change = None           # a live language/model change to apply between chunks
        self._pending_recent_reset = False    # a live device switch: forget the loop history
        self._worker = threading.Thread(target=self._run, daemon=True, name="transcribe")

    def _rebuild_prompt_leak(self, user_prompt, language):
        """(Re)build the prompt-leak matcher. Must follow every _compose_prompt call: the AF
        anchor is only in the prompt for an af session, and a live af->en switch recomposes
        the prompt from anchor+names down to names alone (exactly the incident scenario)."""
        self._prompt_leak = PromptLeakMatcher(
            user_prompt, AF_ANCHOR_PROMPT if language == "af" else None)

    @property
    def indicative(self):
        """True once the active model sits BELOW `small` on the ladder, i.e. the live text should
        be read as a rough guide and the recording re-transcribed afterwards. Still the session's
        own family (the ladder never leaves it), just a model small enough to say so."""
        try:
            return CPU_LADDER.index(self.size) > CPU_LADDER.index(INDICATIVE_BELOW)
        except ValueError:
            return False

    def _is_burst(self, source, audio):
        """True when this chunk arrived FASTER than real time for its source.

        The evidence that separates a replay/catch-up burst from a machine that genuinely cannot
        keep up. Live capture hands over one chunk per chunk-duration per source, so the gap
        between two chunks of the SAME source is about the chunk length; a burst feed (a replay
        harness, a buffered flush after a device glitch) closes that gap to nothing. Measured per
        source on purpose: MIC and SYS both feed this one engine, so a cross-source gap is
        near zero even in a perfectly healthy live session.

        The RTF of a burst-fed chunk is a real measurement of the model, but it is not evidence
        about holding REAL TIME, so it is excluded from the downgrade window."""
        now = time.monotonic()
        prev = self._last_feed.get(source)
        self._last_feed[source] = now
        if prev is None:
            return False
        try:
            dur = len(audio) / 16000.0
        except Exception:
            return False
        return dur > 0 and (now - prev) < 0.5 * dur

    def _ring_for(self, source):
        """This source's energy ring, or None when it has none (or the kill switch is off).

        MIC honours SA_LIVE_RAW_MIC_RING here, because the mic ring IS what that switch was added
        for. The SYS ring predates it, so this accessor hands it back regardless; the silence gate
        applies the switch to SYS itself (see _chunk_is_silence), which is the one place the switch
        is meant to restore pre-WP-4 behaviour for both channels."""
        if source == "MIC":
            return self.mic_env if self._raw_mic_ring else None
        return self.sys_env if source == "SYS" else None

    def _chunk_is_silence(self, source, audio, t_start, dead_margin_db=8.0, dead_ceiling_db=-35.0):
        """Is this whole chunk room tone / near-silence (so Whisper must not see it)?

        With an energy ring for the source, decide on the loudest RAW 100 ms frame in the chunk's
        window, on two tests:

        1. Absolute: the loudest frame never reached the speech floor (see _silence_floor_db).
           That is the point of WP-4 - live AGC lifts a silent room's chunk energy over the
           -45 dBFS floor, so the chunk-fed gate was effectively dead on a quiet mic, the exact
           condition Whisper fabricates on. On MIC that floor is a CEILING on a relative test
           rather than a cut of its own (see ABS_FLOOR_MARGIN_DB): a low-gain microphone puts
           its speech and its room under -45 dBFS together, so the chunk must ALSO have failed
           to rise above the room tone before it is skipped. The quiet-mic safety valve owns
           this arm as well: gentle halves the margin and off stands it down.
        2. Dead channel (relative): the loudest frame sits <= `dead_margin_db` above this
           channel's room tone as measured STRICTLY BEFORE the chunk (ring p10 of the frames
           preceding t_start), i.e. nothing here rose meaningfully above the background it
           arrived into. Measured on the incident recording, fabrication-bearing windows sit at
           <= 6 dB above floor while the nearest real line sits 25.8 dB above it, so 8 dB leaves
           ~18 dB of headroom. Guarded three ways: an absolute cap, so a window louder than
           `dead_ceiling_db` is never skipped on the relative test alone (a never-quiet channel -
           HVAC, music, a constant-tone feed - whose p10 is poisoned upward cannot be eaten); a
           baseline that EXCLUDES the judged window, so a chunk cannot be measured against
           itself; and a minimum of history before the window (see noise_floor_before), without
           which this arm stays inert. The last two are what keep the first chunk of a sustained
           quiet talker: at -40 dBFS throughout, p10 and peak are both -40, and a
           self-referential baseline would call that a dead channel and eat real speech.
        3. MIC ONLY - speech evidence (WP-3): both tests above key off the loudest frame, which
           one door bang, one keyboard click or one cough is enough to satisfy for a whole 15 s
           chunk of room tone. This one asks instead how MANY frames cleared an evidence
           threshold, because speech occupies frames and a transient does not. See
           mic_speech_evidence for the threshold and the two ways it stays inert. Its own switch,
           SA_LIVE_MIC_SPEECH_GATE=0, restores the peak-only behaviour of arms 1 and 2.

        Without a ring (uploads, ring-less paths, SA_LIVE_RAW_MIC_RING=0 for EITHER source) it
        falls back to `_is_silence(audio)` verbatim, which keeps its four pinned tests untouched.
        A ring with no frames covering the window also falls back: never gate on missing evidence.
        All three tests share SA_LIVE_SILENCE_GATE=0.

        Skipping is a DECODE decision only. The recorder is fed from the capture callback, ahead
        of the engine queue (web/app.py _feed, __main__.py feed), so a chunk the gate skips is
        already on disk in full and the saved audio is unchanged by any of this.
        """
        # The gate is the one consumer that honours SA_LIVE_RAW_MIC_RING for BOTH sources: the
        # switch exists to restore pre-WP-4 gate behaviour wholesale, and a switch that left SYS
        # chunks judged on their ring while MIC fell back to chunk samples restored neither.
        ring = self._ring_for(source) if self._raw_mic_ring else None
        if ring is None:
            return _is_silence(audio)
        try:
            dur = len(audio) / 16000.0
        except TypeError:
            return _is_silence(audio)          # not real audio (a test stub): same posture as _is_silence
        t_hi = t_start + dur
        peak = ring.max_db(t_start, t_hi)
        if peak is None:
            return _is_silence(audio)
        floor = ring.noise_floor_before(t_start)
        abs_floor = _silence_floor_db(ring, t_hi)
        if peak < abs_floor:
            if source != "MIC":
                return _gate_log(self, source, t_start, True, "absolute")
            # MIC: the absolute floor is a CEILING on a relative test, not a cut of its own.
            # Measured at -18 dB of mic attenuation on a real capture: 33 of 40 chunks were cut
            # here, arm 3 never got to run on any of them and the safety valve therefore never
            # saw the quiet mic it exists for. A low-gain microphone puts EVERYTHING - speech
            # and room alike - under -45 dBFS, so an absolute floor alone cannot tell a quiet
            # talker from an empty room. What can is the room's own tone: skip only when nothing
            # in the chunk rose meaningfully above the floor it arrived into.
            # The valve owns this arm too. Gentle halves the margin, and off (whether the valve
            # stepped there or the user did) stands it down entirely, or the escalation would
            # hand a cut-off user from one arm to another. Arm 1 feeds the valve no near-miss
            # evidence, deliberately: post-fix it only fires ON the room floor, which is the
            # dead-room signature, never a talker under the bar. That evidence is arm 3's.
            armed = bool(getattr(self, "_mic_speech_gate", True))
            gentle = getattr(self, "_mic_gate_level", "normal") == "gentle"
            margin = ABS_FLOOR_GENTLE_MARGIN_DB if gentle else ABS_FLOOR_MARGIN_DB
            if armed and (floor is None or (peak - floor) <= margin):
                # floor is None (session start, under 10 s of history) keeps the old absolute cut:
                # with no room tone to compare against there is nothing better to do, and this is
                # what the pinned first-chunk cases expect.
                why = "absolute" if floor is None else f"absolute peak={peak:.0f} floor={floor:.0f}"
                return _gate_log(self, source, t_start, True, why)
        if (floor is not None and peak <= dead_ceiling_db
                and (peak - floor) <= dead_margin_db):
            return _gate_log(self, source, t_start, True, f"dead peak={peak:.0f} floor={floor:.0f}")
        # Arm 3 (MIC only): continuity, not peak. Additive - it can only skip a chunk the two
        # peak arms already decided to keep, never rescue one they skipped.
        if source == "MIC" and getattr(self, "_mic_speech_gate", True):
            # Read the level per chunk, not once per session: the safety valve steps it down from
            # under this very call, and a live toggle can flip the flag between two chunks.
            gentle = getattr(self, "_mic_gate_level", "normal") == "gentle"
            stats = {}
            verdict, why = mic_speech_evidence(
                ring, t_start, t_hi,
                margin_db=MIC_GATE_GENTLE_MARGIN_DB if gentle else MIC_EVIDENCE_MARGIN_DB,
                need_s=MIC_GATE_GENTLE_SECONDS if gentle else MIC_EVIDENCE_SECONDS,
                stats=stats)
            if verdict is not None:
                return _gate_log(self, source, t_start, not verdict, f"evidence {why}", stats)
            return _gate_log(self, source, t_start, False, f"evidence inert ({why})")
        return _gate_log(self, source, t_start, False, f"peak={peak:.0f}")

    def mic_gate_state(self):
        """The mic gate as the UI must render it: the ENGINE'S own state, never a stored setting.

        mode is "normal" | "gentle" | "off"; gentle is the quiet-mic safety valve's first step.
        skipped/decoded are this session's MIC chunk counts. hint/hint_seq carry the valve's
        one-shot message: the client toasts when hint_seq moves past the one it last showed, so
        nothing needs clearing and a page reload cannot re-fire an old hint.
        """
        on = bool(getattr(self, "_mic_speech_gate", True))
        return {
            "on": on,
            "mode": (getattr(self, "_mic_gate_level", "normal") if on else "off"),
            "skipped": int(getattr(self, "mic_gate_skipped", 0)),
            "decoded": int(getattr(self, "mic_gate_decoded", 0)),
            "hint": getattr(self, "mic_gate_hint", None),
            "hint_seq": int(getattr(self, "mic_gate_hint_seq", 0)),
        }

    def set_mic_gate(self, on):
        """Turn the MIC speech gate on or off, effective on the NEXT chunk. Returns mic_gate_state().

        Decoding only: the recorder is fed ahead of this queue, so neither value changes a single
        sample of what is saved. Turning it back on restores the level the valve last chose (gentle
        stays gentle) rather than jumping back to normal, because "never escalate automatically"
        would be hollow if an off/on flick undid the valve's finding. The window is cleared either
        way, so the valve judges what happens next, not what happened before the switch."""
        self._mic_speech_gate = bool(on)
        try:
            self._mic_gate_recent.clear()
        except AttributeError:
            self._mic_gate_recent = deque(maxlen=MIC_GATE_WINDOW)
        return self.mic_gate_state()


    def subscribe(self, fn):
        self.subscribers.append(fn)

    def start(self):
        self._recent.clear()
        self._sys_text.clear()   # the ONLY place this is cleared: see SysTextRing
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
        """Approximate chunks still to transcribe (shed buffer + queued + the one in flight)."""
        n = self._queue.qsize() + len(self._front)
        if self._busy:
            n += 1
        return n

    def _backlog_seconds(self):
        """Seconds of audio still waiting to be transcribed (shed buffer + queue). The real-time
        contract, and what the shed valve bounds. Chunks that are not real audio count as 0."""
        total = sum(_chunk_seconds(i) for i in self._front if i is not None)
        try:
            with self._queue.mutex:          # read-only snapshot; producers only ever append
                queued = list(self._queue.queue)
        except Exception:
            queued = []
        return total + sum(_chunk_seconds(i) for i in queued if i is not None)

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
        # Stamped with its arrival time and whether it arrived faster than real time, so the
        # downgrade window can judge live evidence only (see _is_burst).
        item = (source, audio, t_start, time.monotonic(), self._is_burst(source, audio))
        try:
            self._queue.put(item, block=block, timeout=timeout)
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
        self._rebuild_prompt_leak(prompt, self.language)

    def request_change(self, *, language, engine, model=None, model_name=None, size=None, family=None,
                       device=None, compute_type=None):
        """Queue a live language and/or model change, applied by the worker between chunks.

        Pass `model` (a WhisperModel the CALLER already built via load_model, off the worker, so
        the swap never stalls transcription) together with model_name + size + family to swap
        the model; omit them to keep the current model and change only the decode language - instant,
        no reload, and the right move for a bilingual meeting on a both-capable model. `language` is
        the next decode language (None == auto-detect, "af"/"en" otherwise); the prompt is recomposed
        for it. `device`/`compute_type` (with `model`) record the backend the new model was BUILT on
        ("cpu"/"cuda"/"mlx"), so a cross-backend swap (mlx <-> cpu on a Mac) updates the engine's
        device identity together with the model; None keeps the current values (every same-device
        swap, which is all Windows ever does). A second request before the worker applies the first
        simply replaces it.

        Known limitation (pre-existing, all platforms including Windows): rapid overlapping
        reconfigure requests can transiently desync STATE.* from the engine, because each API
        thread publishes its own view while the worker applies only the LAST queued change
        between chunks. Accepted as-is; no request/worker generation tags here."""
        with self._change_lock:
            self._pending_change = {
                "language": language, "engine": engine,
                "model": model, "model_name": model_name, "size": size, "family": family,
                "device": device, "compute_type": compute_type,
            }

    def request_loop_history_reset(self):
        """Ask the worker to forget the cross-segment loop history (RecentEmissions).

        Called from a REQUEST thread (a live device switch, web/app.py:switch_device). The
        history is owned by the transcription worker, so this thread must not clear it
        directly; it queues a flag the worker consumes between chunks, exactly like
        request_change / _pending_change. A different microphone changes what the model
        hears, so an already-armed loop must not swallow the first genuine identical line
        from the new device."""
        with self._change_lock:
            self._pending_recent_reset = True

    def _apply_pending_recent_reset(self):
        """Consume a queued loop-history reset. Worker-loop only (single writer)."""
        with self._change_lock:
            pending = self._pending_recent_reset
            self._pending_recent_reset = False
        if pending:
            self._recent.clear()

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
            # A cross-backend swap (mlx <-> cpu on a Mac) must move the engine's device
            # identity WITH the model: _is_cpu gates the CPU adaptive downgrade ladder,
            # and _device/_compute_type drive the next reconfigure's resolution and any
            # downgrade reload. Applied here on the worker thread together with the model
            # (single writer), so model and device identity can never be torn. None (the
            # only value Windows callers ever pass) keeps the current backend untouched.
            if ch.get("device"):
                self._device = ch["device"]
                self._is_cpu = ch["device"] == "cpu"
            if ch.get("compute_type"):
                self._compute_type = ch["compute_type"]
        self.initial_prompt = _compose_prompt(self.language, self._user_prompt)
        self._rebuild_prompt_leak(self._user_prompt, self.language)
        self._recent.clear()  # a model/language flip legitimately changes output style
        self._rtf.clear()   # judge the (possibly new) model fresh; never downgrade on the old RTF
        if ch["model"] is not None:
            self._cold_decode = True            # the new model pays its load cost on its first decode
            self._last_rung_change = time.monotonic()
            self._swap = None                   # a ladder build in flight is stale now: drop it
        lang_name = {"af": "Afrikaans", "en": "English"}.get(self.language, self.language or "auto-detect")
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
            # Published SYS is the echo reference for veto arm 2. Recorded here because this is
            # the single point SYS reaches the transcript: notices and backpressure markers go
            # straight to _fanout, so no synthetic line can ever become an echo reference.
            if seg.source == "SYS" and self._xchan_veto2:
                # Both ends: the arm only counts a far-end line that was SOUNDING while the mic
                # segment ran, so the span is the evidence, not just the arrival time.
                self._sys_text.add(seg.t_start, seg.text, seg.t_end)
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

    def _notify_downgrade(self, old_size, new_size):
        """Fire on_downgrade best-effort. Fully decoupled, exactly like the segment subscribers:
        this runs on the worker thread, so the callback body does its own thread-safe hand-off. A
        None callback (CLI, file import, tests) is a no-op; a raising callback must never take the
        transcription worker down with it. The UI reads self.indicative / self.shed_seconds off
        the engine when it builds its payload, so this signature stays as it has always been."""
        cb = self.on_downgrade
        if cb is None:
            return
        try:
            cb(old_size, new_size)
        except Exception as e:
            print(f"[engine] on_downgrade callback error: {e}", flush=True)

    def _next_rung(self):
        """The next USABLE rung below the current model, or None when there is none left.

        Usable means both of the ladder's hard rules at once: it resolves to the family this
        session is already running (never a stock model under an Afrikaans session), and it is
        already on this machine (never a mid-meeting download). A rung that fails either test is
        skipped and the search carries on down; when nothing is left the caller sheds instead.
        Returns (size, model_id, family)."""
        try:
            idx = CPU_LADDER.index(self.size)
        except ValueError:
            idx = -1  # start size above the ladder (turbo/large-v3) -> first rung is next
        for size in CPU_LADDER[idx + 1:]:
            # Keep the family AND the user's engine choice: a forced-Fluister or forced-Whisper
            # session must NOT silently flip to language-based auto when the size drops a rung.
            model_id, fam = resolve_model(size, self.language, self.engine)
            if fam != self.family:
                print(f"[engine] ladder: skipping {size} ({fam}, not {self.family})", flush=True)
                continue
            if not model_present(model_id):
                print(f"[engine] ladder: skipping {size} ({model_id} is not on this machine)", flush=True)
                continue
            return size, model_id, fam
        return None

    def _begin_swap(self, size, model_id, family):
        """Start building the next rung on a HELPER thread and return at once, so the worker keeps
        decoding on the current model while the new one loads. A ct2 build plus its first inference
        costs tens of seconds (measured 35 s for medium, 16 s for small), and doing that on the
        worker used to stall transcription outright at the exact moment the session was already
        behind. At most one build is ever in flight, so memory is bounded at one extra model."""
        if self._swap is not None:
            return
        swap = {"size": size, "model_id": model_id, "family": family,
                "model": None, "error": None, "done": threading.Event()}
        self._swap = swap
        threading.Thread(target=self._swap_run, args=(swap,), daemon=True, name="rung-swap").start()

    def _swap_run(self, swap):
        try:
            # local_only: the ladder never downloads. _next_rung already checked, this is the
            # belt-and-braces half of the same rule.
            swap["model"] = load_model(swap["model_id"], "cpu", self._compute_type,
                                       cpu_threads=self._cpu_threads, local_only=True)
        except Exception as e:
            swap["error"] = e
        finally:
            swap["done"].set()

    def _install_swap(self, t_start):
        """Install a finished helper-thread build. Worker thread only, so self.model stays
        single-writer. Returns True when the rung actually changed."""
        swap = self._swap
        if swap is None or not swap["done"].is_set():
            return False
        self._swap = None                       # drop the reference either way: one build in flight
        self._last_rung_change = time.monotonic()
        if swap["model"] is None:
            print(f"[engine] downgrade load failed ({swap['size']}): {swap['error']}", flush=True)
            return False
        old_size = self.size   # capture BEFORE the swap: the callback reports the size we left
        self.model = swap["model"]
        self.size = swap["size"]
        self.model_name = swap["model_id"]
        self.family = swap["family"]
        self.is_fluister = swap["family"] == "fluister"
        self._rtf.clear()
        # A different model legitimately changes output style, so the cross-segment loop history
        # from the old one is not evidence about the new one (_apply_pending_change has always
        # done this for a language/model change; the ladder used to forget to).
        self._recent.clear()
        self._cold_decode = True                # the first decode pays this model's load cost
        self._emit_notice(t_start, f"[engine: switched to '{self.size}' model to keep up with the audio]")
        self._notify_downgrade(old_size, self.size)
        return True

    def _maybe_downgrade(self, t_start):
        """Step down CPU_LADDER when sustained, honest evidence says we can't hold real-time.

        Only fires on CPU. Ratchets down only, never back up, to avoid oscillation. The new
        (smaller) model also chews through the queued backlog faster, which is how the session
        catches back up. See CPU_LADDER for the three rules the step itself obeys.
        """
        # Swivuriso is a single fixed model (size-independent), so there is no smaller rung to drop
        # to; never downgrade it.
        if self.family == "swivuriso":
            return
        if not self.adaptive or not self._is_cpu:
            return
        if self._install_swap(t_start):
            return                              # just changed rung; judge the new one fresh
        if self._swap is not None:
            return                              # a rung is already being built off-thread
        if time.monotonic() - self._last_rung_change < DOWNGRADE_MIN_SECONDS:
            return                              # a step has to hold for a while before the next
        if len(self._rtf) < self._rtf.maxlen:
            return
        avg = sum(self._rtf) / len(self._rtf)
        if avg <= DOWNGRADE_RTF:
            return
        self._start_step(f"CPU RTF ~{avg:.2f} (> {DOWNGRADE_RTF})")

    def _start_step(self, reason):
        """Begin a step to the next usable rung, if the ladder is allowed to move and has one.

        The two callers hold different evidence and both are honest: a full window of over-budget
        RTF, and a shed event (the backlog blew past its bound on a live feed, which is the real
        time contract failing in the most direct way there is). Everything else - swivuriso, the
        minimum spacing, one build in flight, in-family, present-only - is checked here or in
        _next_rung, so neither caller can bypass a rule."""
        if self.family == "swivuriso" or not self.adaptive or not self._is_cpu:
            return False
        if self._swap is not None:
            return False
        if time.monotonic() - self._last_rung_change < DOWNGRADE_MIN_SECONDS:
            return False
        nxt = self._next_rung()
        if nxt is None:
            # Nothing usable left below this model. Do NOT reach outside the family or the local
            # store for one: the shed valve handles it from here. Clear the window so the next
            # decision is made on fresh evidence rather than a standing trigger.
            self._rtf.clear()
            return False
        new_size, new_model, new_family = nxt
        print(f"[engine] {reason}; downgrading {self.size} -> {new_size}", flush=True)
        self._begin_swap(new_size, new_model, new_family)
        self._rtf.clear()                       # don't re-trigger while the build runs
        return True

    def _drain_to_front(self):
        """Move everything queued into the worker's own front buffer so the shed valve can drop the
        OLDEST audio. queue.Queue only drops the NEWEST (a full queue refuses the put), which is
        the wrong end: the newest chunk is the one the listener is waiting to see."""
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is None:
                self._stop.set()   # never lose the shutdown sentinel; the drained+stop check exits
                continue
            self._front.append(item)

    def _maybe_shed(self, t_start):
        """Drop the OLDEST queued audio when the backlog is past SHED_BACKLOG_SECONDS.

        The last honest move. When the machine cannot keep up and there is no usable rung left
        below the current model, the choice is between falling further and further behind (the live
        view slides minutes into the past) and losing some audio. Losing audio wins, provided it is
        SAID: the notice names how much and where, and the recording, if one is running, still has
        every second of it. Live only (self.adaptive): a file import is not real time and must
        never lose a chunk."""
        if not self.adaptive or not self._is_cpu:
            return
        if self._stop.is_set():
            # Shutting down and draining the tail. There is no real-time obligation left, and
            # stop(drain=True) exists precisely so the last minutes of the session are not lost.
            return
        if self._backlog_seconds() <= SHED_BACKLOG_SECONDS:
            return
        self._drain_to_front()
        dropped = 0.0
        first = last = None
        while self._front and self._backlog_seconds() > SHED_BACKLOG_SECONDS:
            item = self._front.popleft()
            if item is None:
                continue
            dur = _chunk_seconds(item)
            if first is None:
                first = item[2]
            last = item[2] + dur
            dropped += dur
        if dropped <= 0:
            return
        self.shed_seconds += dropped
        self.shed_events += 1
        span = f" ({_mmss(first)} to {_mmss(last)})" if first is not None else ""
        print(f"[engine] shed {dropped:.0f}s of backlog{span}; "
              f"total shed {self.shed_seconds:.0f}s", flush=True)
        self._emit_notice(t_start, f"[engine: skipped {dropped:.0f} s of audio{span} to catch up]")
        # Surface it the same way a rung change is surfaced: same callback, same banner. Passing
        # the current size for both sides says "nothing changed model-side, this is the shed
        # valve", which is what the banner copy keys off.
        self._notify_downgrade(self.size, self.size)
        # A shed IS the real-time contract failing, and it is far more direct evidence than a
        # rolling RTF average: the backlog went past its bound on a live feed. Measured on a ten
        # minute two-source replay starting at medium on four threads, waiting for the RTF window
        # alone left the session on medium for six minutes and 126 s of audio shed before it
        # stepped; stepping on the shed itself cuts that to one shed event. Every ladder rule
        # still applies (_start_step), so this can only ever take a rung that is in-family,
        # present, and past the minimum spacing.
        self._start_step(f"shed {dropped:.0f}s of backlog")

    def _run(self):
        # Loop until the sentinel, or (during shutdown) until the queue empties.
        # We deliberately do NOT break the instant _stop is set, that would drop
        # the queued backlog (the last minutes of the session). Instead we drain.
        while True:
            self._flush_pending_mic()   # release held MIC whose delay elapsed (ticks each ~0.5s)
            if self._front:
                item = self._front.popleft()   # chunks the shed valve pulled back: strictly older
            else:
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
            # Tolerant unpack: on_chunk stamps arrival time + the burst flag, but a bare
            # (source, audio, t_start) put straight on the queue still works.
            source, audio, t_start = item[0], item[1], item[2]
            burst = bool(item[4]) if len(item) > 4 else False

            # Apply a queued live language/model change before this chunk (worker-thread only, so
            # self.model stays single-writer, like the adaptive downgrade further down).
            self._apply_pending_change(t_start)
            # A live device switch asked us to forget the loop history (worker-owned).
            self._apply_pending_recent_reset()

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
                # Reads the source's raw energy ring when there is one, so an AGC boost cannot
                # hide a silent room from the gate (SA_LIVE_RAW_MIC_RING=0 restores chunk energy).
                if self._silence_gate and self._chunk_is_silence(source, audio, t_start):
                    continue
                t0 = time.monotonic()
                # encoder_window(): a CPU model decodes at its measured 20 s window (the mel is
                # otherwise padded back to 30 s inside faster-whisper). Inert for CUDA/Metal.
                with encoder_window(self.model) as _win_frames:
                    _kw = {"chunk_length": _win_frames // 100} if _win_frames else {}
                    segs, _info = self.model.transcribe(
                        audio,
                        language=self.language,
                        initial_prompt=self.initial_prompt,
                        vad_filter=True,
                        # Per-source VAD: the tightened MIC set, the library defaults for SYS.
                        vad_parameters=vad_options_for(source),
                        beam_size=beam,
                        **_kw,
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
                    # initial_prompt leaking back out as "speech" (names, jargon, the AF
                    # anchor). Toggle SA_LIVE_PROMPT_LEAK_GUARD=0.
                    if self._prompt_leak_on and self._prompt_leak.is_leak(text):
                        print(f"[engine] prompt-leak dropped {source} @ "
                              f"{t_start + float(seg.start):.1f}s {_logtxt(text)}", flush=True)
                        continue
                    # Phrase-loop artifact: a segment that is mostly one repeated multi-word unit
                    # ("ek het nie ek het nie ...") is a quiet-mic hallucination, not speech.
                    # (_collapse_repetition handles single-token runs; this catches word groups.)
                    if _is_phrase_loop(text):
                        continue
                    # (There used to be a `no_speech_prob > 0.85` per-segment drop here. Measured
                    # across 640 real segments on this stack - faster-whisper 1.2.1 / ctranslate2
                    # 4.7.2, vad_filter either way - seg.no_speech_prob is identically 0.000, so the
                    # guard had never once fired. Removed as dead code, not as a loosening. The
                    # field exists on the segment; any future guard that wants it must first verify
                    # it actually VARIES on the pinned versions before depending on it.)
                    out = Segment(
                        source=source,
                        t_start=t_start + float(seg.start),
                        t_end=t_start + float(seg.end),
                        text=text,
                    )
                    # Cross-channel echo veto: drop a MIC segment that is far-end bleed (quiet copy
                    # of a concurrently-active SYS). Post-ASR + conservative, so it never touches the
                    # audio (no word-nudging / fragmentation) and keeps real speech. Live and file
                    # both feed self.sys_env; when it is absent the veto is inert. The mic side of
                    # the comparison comes from self.mic_env (RAW, pre-AGC) when that ring exists,
                    # so the calibrated 10 dB margin and -28 dBFS ceiling mean what they were
                    # measured to mean; without it the chunk samples are used, as before.
                    # Honest note on the SYS reference both arms read: the ring is fed per 100 ms
                    # frame from the RAW capture blocks, stamped at BLOCK START
                    # (capture_core._ring_frames). SA_LIVE_RAW_MIC_RING does NOT gate that feed and
                    # is not meant to: the finer granularity and the corrected timestamps replaced a
                    # 0.5 s arrival-stamped feed that mis-timed the reference, and putting a
                    # known-wrong reference back behind a switch would be a worse veto, not an older
                    # one. The switch's job is the MIC ring (and, in the silence gate, the ring-fed
                    # gate itself), not the far-end reference.
                    if source == "MIC" and self._xchan_veto and self.sys_env is not None:
                        _mic = audio[int(float(seg.start) * 16000):int(float(seg.end) * 16000)]
                        _drop, _why = sys_echo_veto(_mic, self.sys_env, out.t_start, out.t_end, len(text.split()),
                                                    mic_ring=self._ring_for("MIC"))
                        if _drop:
                            print(f"[engine] echo-veto dropped MIC @ {out.t_start:.1f}s [{_why}] {_logtxt(text)}", flush=True)
                            continue
                    # Arm 2: energy-armed fuzzy echo. Catches the quiet-but-GAPPY far end that arm
                    # 1 correctly refuses, by demanding the text echo what the far end just said on
                    # top of the same energy asymmetry. Runs after arm 1 (the cheaper, purely
                    # absolute test) and before the loop guard, so a vetoed line never seeds the
                    # loop history. Toggle SA_LIVE_XCHAN_VETO2=0.
                    if source == "MIC" and self._xchan_veto2:
                        # ACCEPTED recall limitation, stated here so it is not rediscovered as a
                        # bug: the MIC segment is judged the moment its chunk finishes
                        # transcribing, which can be BEFORE the SYS segment it echoes has been
                        # published into _sys_text. The far-end original then simply is not in the
                        # ring, the arm scores no overlap, and the line is kept. That is the right
                        # way to lose: a missing reference must never become evidence. The known
                        # future improvement is to defer this judgement to the MIC publish-delay
                        # flush (_flush_pending_mic, MIC_PUBLISH_DELAY later, by which time the
                        # concurrent SYS text has landed) rather than to widen the text window,
                        # which would only re-admit sequential dialogue as false evidence.
                        # (the reason string is only interesting on a keep, and the numbers below
                        # already say why on a drop)
                        _d2, _own, _marg, _ov, _ = fuzzy_echo_veto(
                            text, out.t_start, out.t_end,
                            self._ring_for("MIC"), self.sys_env, self._sys_text)
                        if _d2:
                            print(f"[engine] xchan-echo dropped MIC @ {out.t_start:.1f}s "
                                  f"[own={_own:.1f} marg={_marg:.1f} ov={_ov:.2f}] {_logtxt(text)}",
                                  flush=True)
                            continue
                    # Cross-segment loop: this line is the 5th+ cycle of a short, fast
                    # repeat on this source. Silent drop, stdout log only - the first four
                    # cycles are already in the transcript as evidence. Toggle
                    # SA_LIVE_LOOP_GUARD=0. Recorded only for segments that reach publish,
                    # so a vetoed or dropped segment never seeds the history.
                    if self._loop_guard_on:
                        _p = self._recent.observe(source, text, out.t_start)
                        if _p:
                            print(f"[engine] loop-guard suppressed {source} @ {out.t_start:.1f}s "
                                  f"p={_p} {_logtxt(text)}", flush=True)
                            continue
                    self._route(out)

                # Adaptive model downgrade (CPU only): track real-time factor and
                # step down to a faster model if we're sustained-slower than real-time.
                if self._is_cpu:
                    audio_dur = len(audio) / 16000.0
                    # Only LIVE, WARM samples are evidence about holding real time. A burst-fed
                    # chunk was never a real-time obligation (see _is_burst), and the first decode
                    # on a freshly built model is dominated by that model's one-off load cost -
                    # counting either is how a single slow moment used to cascade the whole ladder.
                    if audio_dur > 0 and not burst and not self._cold_decode:
                        self._rtf.append(elapsed / audio_dur)
                    self._cold_decode = False
                    self._maybe_downgrade(t_start)
                    self._maybe_shed(t_start)
            except Exception as e:
                print(f"[engine] transcribe error on {source} chunk: {e}", flush=True)
            finally:
                self._busy = False

        # Worker exiting (drained or aborted): release anything still held by the MIC delay.
        self._flush_pending_mic(force=True)
