"""WER eval: Fluister MLX conversions vs the production CTranslate2 baseline.

Scores three engines on the same clips:
  ct2-int8   faster-whisper WhisperModel("digiphyte/fluister-turbo", cpu, int8)
             (the current Volksmond production configuration)
  mlx-fp16   mlx_whisper.transcribe with the converted fp16 model dir
  mlx-q8     mlx_whisper.transcribe with the converted 8-bit model dir

Datasets (public HF, streamed, first N usable rows of each test split, so the
selection is deterministic):
  af  andreoosthuizen/afrikaans-30s      column "transcript"
  en  danielshaps/nchlt_speech_eng       column "text"

Decode conditions are matched as closely as each API allows: temperature
fallback (0.0, 0.2, 0.4), condition_on_previous_text=False, no initial prompt.
faster-whisper uses beam_size=5 (its default, and what production uses);
mlx-whisper has no beam search, so it decodes greedily with the same
temperature fallback ladder.

Self-contained scoring, no jiwer: the normalize() function is copied verbatim
from SA-ASR-Model/train/sa_text.py (the training-side normaliser, so numbers
are comparable with training-era evals) and WER is plain word-level
Levenshtein as in SA-ASR-Model/eval/eval_wer.py.

RTF = total wall-clock transcribe time / total audio duration per engine.
CI-runner note: GitHub macOS arm64 VMs may not expose Metal to MLX; if the GPU
is unusable, MLX falls back to CPU and the RTF numbers are NOT representative
of real Apple Silicon. The actually-used MLX device is recorded in the output.
WER is the primary result either way.
"""
import argparse
import gc
import io
import re
import time
import unicodedata
from dataclasses import dataclass
from importlib.metadata import version as pkg_version
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

SAMPLE_RATE = 16000

# ---------------------------------------------------------------------------
# Text normalisation, copied verbatim from SA-ASR-Model/train/sa_text.py so
# WER here is measured on the same normalisation as training-side evals.
# ---------------------------------------------------------------------------
_WS = re.compile(r"\s+")
# keep intra-word apostrophe/hyphen (Afrikaans "'n", place names like "Ka-Nyamazane")
_PUNCT = re.compile(r"[^\w\s'\-]", flags=re.UNICODE)


def _basic(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower()
    text = _PUNCT.sub(" ", text)
    text = text.replace(" '", " ").replace("' ", " ")   # drop stray quote marks
    return _WS.sub(" ", text).strip()


def _af(text: str) -> str:
    return text


_HOOKS = {"af": _af}


def normalize(text: str, lang: str) -> str:
    text = _basic(text)
    hook = _HOOKS.get(lang)
    return hook(text) if hook else text


# Word-level Levenshtein, same as SA-ASR-Model/eval/eval_wer.py
def _lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------
# Clip loading
# ---------------------------------------------------------------------------
@dataclass
class Clip:
    lang: str
    ref: str
    audio: np.ndarray  # float32 mono 16 kHz
    duration: float


def load_clips(dataset_id, split, text_key, lang, n):
    from datasets import Audio, load_dataset

    print(f"loading {n} clips: {dataset_id} split={split} lang={lang}",
          flush=True)
    ds = load_dataset(dataset_id, split=split, streaming=True)
    ds = ds.cast_column("audio", Audio(decode=False))
    clips = []
    for row in ds:
        ref = normalize((row.get(text_key) or ""), lang)
        if not ref:
            continue
        raw = row["audio"]["bytes"]
        if raw is None:
            continue
        audio, sr = sf.read(io.BytesIO(raw), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != SAMPLE_RATE:
            from math import gcd
            g = gcd(SAMPLE_RATE, sr)
            audio = resample_poly(audio, SAMPLE_RATE // g, sr // g)
            audio = audio.astype(np.float32)
        clips.append(Clip(lang, ref, audio, len(audio) / SAMPLE_RATE))
        if len(clips) == n:
            break
    if len(clips) < n:
        raise SystemExit(f"only found {len(clips)}/{n} usable clips in "
                         f"{dataset_id}")
    total = sum(c.duration for c in clips)
    print(f"  got {len(clips)} clips, {total:.1f}s audio", flush=True)
    return clips


# ---------------------------------------------------------------------------
# Engines: each returns (hyp_text, elapsed_seconds) for one clip
# ---------------------------------------------------------------------------
TEMPS = [0.0, 0.2, 0.4]


def make_ct2(model_id):
    from faster_whisper import WhisperModel
    model = WhisperModel(model_id, device="cpu", compute_type="int8")

    def transcribe(clip):
        t0 = time.perf_counter()
        segs, _ = model.transcribe(
            clip.audio, language=clip.lang, beam_size=5,
            temperature=TEMPS, condition_on_previous_text=False)
        text = " ".join(s.text for s in segs)  # consumes the generator
        return text, time.perf_counter() - t0

    return transcribe


def make_mlx(model_dir):
    import mlx_whisper

    def transcribe(clip):
        t0 = time.perf_counter()
        result = mlx_whisper.transcribe(
            clip.audio, path_or_hf_repo=str(model_dir),
            language=clip.lang, temperature=tuple(TEMPS),
            condition_on_previous_text=False)
        return result["text"], time.perf_counter() - t0

    return transcribe


def mlx_device_note():
    """Report which device MLX actually uses (Metal may be absent in CI VMs)."""
    import mlx.core as mx
    try:
        metal = mx.metal.is_available()
    except Exception:
        metal = False
    try:
        mx.eval(mx.ones((16, 16)) @ mx.ones((16, 16)))
        return f"default_device={mx.default_device()}, metal_available={metal}"
    except Exception as e:
        mx.set_default_device(mx.cpu)
        return f"default_device=cpu (gpu failed: {e}), metal_available={metal}"


def run_engine(name, transcribe, clips):
    err = {"af": 0, "en": 0}
    nwords = {"af": 0, "en": 0}
    elapsed_total = 0.0
    audio_total = 0.0
    # Warm up on the first clip so model load / compile time is not billed to RTF
    transcribe(clips[0])
    for i, clip in enumerate(clips, 1):
        hyp_raw, elapsed = transcribe(clip)
        hyp = normalize(hyp_raw, clip.lang)
        err[clip.lang] += _lev(clip.ref.split(), hyp.split())
        nwords[clip.lang] += max(1, len(clip.ref.split()))
        elapsed_total += elapsed
        audio_total += clip.duration
        print(f"  [{name}] {i}/{len(clips)} {clip.lang} "
              f"{clip.duration:.1f}s in {elapsed:.1f}s", flush=True)
    wer = {L: err[L] / nwords[L] for L in err}
    rtf = elapsed_total / audio_total
    print(f"  [{name}] af WER {wer['af']:.3f}  en WER {wer['en']:.3f}  "
          f"RTF {rtf:.2f}", flush=True)
    return wer, rtf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default="digiphyte/fluister-turbo")
    ap.add_argument("--fp16-dir", required=True)
    ap.add_argument("--q8-dir", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", default="eval-results.md")
    args = ap.parse_args()

    pins = {p: pkg_version(p) for p in
            ("mlx", "mlx-whisper", "faster-whisper", "ctranslate2",
             "datasets", "soundfile", "scipy", "numpy")}
    print("versions:", pins, flush=True)
    device_note = mlx_device_note()
    print("mlx device:", device_note, flush=True)

    clips = (load_clips("andreoosthuizen/afrikaans-30s", "test",
                        "transcript", "af", args.n)
             + load_clips("danielshaps/nchlt_speech_eng", "test",
                          "text", "en", args.n))

    rows = []
    engines = [
        (f"ct2 int8 baseline ({args.baseline})", make_ct2, args.baseline),
        ("mlx fp16", make_mlx, args.fp16_dir),
        ("mlx q8", make_mlx, args.q8_dir),
    ]
    for name, factory, target in engines:
        print(f"\n=== {name} ===", flush=True)
        transcribe = factory(target)
        wer, rtf = run_engine(name, transcribe, clips)
        rows.append((name, wer["af"], wer["en"], rtf))
        del transcribe
        gc.collect()

    lines = [
        "# Fluister MLX conversion eval",
        "",
        f"- n = {args.n} clips per language, deterministic (first usable rows "
        "of each test split)",
        "- af: andreoosthuizen/afrikaans-30s (test), en: "
        "danielshaps/nchlt_speech_eng (test)",
        "- decode: temperature fallback (0.0, 0.2, 0.4), "
        "condition_on_previous_text=False, no initial prompt; ct2 beam_size=5 "
        "(production default), mlx greedy (no beam search in mlx-whisper)",
        "- normalisation: SA-ASR-Model train/sa_text.py normalize(); WER: "
        "plain word Levenshtein",
        f"- mlx device: {device_note}",
        "- RTF = total transcribe wall-clock / total audio seconds; "
        "CI-runner RTF is not representative of real Apple Silicon "
        "(virtualised, and Metal may be unavailable)",
        f"- versions: {', '.join(f'{k}={v}' for k, v in pins.items())}",
        "",
        "| model | af WER | en WER | mean RTF |",
        "|---|---|---|---|",
    ]
    for name, af, en, rtf in rows:
        lines.append(f"| {name} | {af:.3f} | {en:.3f} | {rtf:.2f} |")
    base_af = rows[0][1]
    lines += [
        "",
        f"- mlx fp16 af WER delta vs ct2 baseline (same run): "
        f"{rows[1][1] - base_af:+.3f} (gate: within +0.010)",
        f"- mlx q8 af WER delta vs ct2 baseline (same run): "
        f"{rows[2][1] - base_af:+.3f}",
    ]
    report = "\n".join(lines) + "\n"
    Path(args.out).write_text(report, encoding="utf-8")
    print("\n" + report, flush=True)


if __name__ == "__main__":
    main()
