"""CLI entry: arg parsing, tier picking, banner, wiring, signal handling.

Invoke via `python -m live_transcribe ...`. Day-to-day use is via the
PowerShell wrapper `start-meeting.ps1` which assembles sensible defaults.
"""
import argparse
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path


TIER_CHOICES = ["auto", "gpu", "gpu-4gb", "cpu", "cpu-min", "cpu-strong", "cpu-mid", "cpu-large"]


def parse_args():
    p = argparse.ArgumentParser(
        prog="live_transcribe",
        description="Local Afrikaans live transcription (WASAPI loopback + mic, on-device Whisper).",
    )
    p.add_argument("--output", type=Path, default=None,
                   help="Markdown output path. Default: sessions/<YYYY-MM-DD-HHMM>-session.md")
    p.add_argument("--language", default="af",
                   help="Whisper language code (default: af). Use 'en' for English-only meetings.")
    p.add_argument("--prompt", default=None,
                   help="Initial prompt, entity names, jargon, client terms. Seeds the model.")
    p.add_argument("--tier", default=None, choices=TIER_CHOICES,
                   help="Hardware tier. Default: auto-detect. Override (e.g. --tier cpu-strong) to force CPU even on a GPU box.")
    p.add_argument("--chunk-seconds", type=int, default=None,
                   help="Capture chunk size in seconds. Default: 8 for gpu, 15 for cpu tiers.")
    p.add_argument("--mic-device", default=None,
                   help="Mic device index or name substring. Default: WASAPI default input.")
    p.add_argument("--loopback-device", default=None,
                   help="Loopback (system audio) device index or name substring. Default: WASAPI default output.")
    p.add_argument("--offline", action="store_true",
                   help="Set HF_HUB_OFFLINE=1; refuse network calls. Model must already be cached.")
    p.add_argument("--keep-audio", action="store_true",
                   help="Save raw 16k audio alongside transcript. POPIA: requires client consent.")
    p.add_argument("--seed-from-calendar", action="store_true",
                   help="Add the current/next Outlook meeting's attendees to the prompt (Microsoft Graph; see live_transcribe/outlook.py).")
    p.add_argument("--list-devices", action="store_true",
                   help="List audio devices and exit.")
    return p.parse_args()


def _gpu_vram_mb():
    """Total VRAM of GPU 0 in MB via nvidia-smi, or None if unavailable.

    Used to split a detected CUDA device into the float16 tier (>=6GB) vs the
    int8_float16 4GB tier (e.g. GTX 1650 Mobile). nvidia-smi ships with the
    driver, so this needs no extra dependency.
    """
    from . import cudadl
    if not cudadl.SUPPORTED:
        return None   # non-Windows: no CUDA here, never shell nvidia-smi
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=(subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
        if out.returncode == 0:
            return int(out.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


def pick_tier(explicit):
    """Resolution order: explicit flag > SA_LIVE_TIER env > auto-detect."""
    chosen = explicit if (explicit and explicit != "auto") else None
    if chosen is None:
        env = os.environ.get("SA_LIVE_TIER", "").strip()
        if env in TIER_CHOICES and env != "auto":
            chosen = env
    # An explicit / env GPU tier is honoured only if CUDA is actually usable; otherwise
    # fall back to large-v3 on CPU so a forced "gpu" can never fail to load.
    if chosen in ("gpu", "gpu-4gb"):
        try:
            from . import cudadl
            return chosen if cudadl.cuda_ready() else "cpu-large"
        except Exception:
            return "cpu-large"
    if chosen:
        return chosen
    # No explicit choice: auto-detect. Use the GPU only when CUDA is actually usable
    # (frozen: the libs are downloaded; source: a system CUDA toolkit is assumed).
    try:
        from . import cudadl
        if cudadl.cuda_ready():
            # large-v3 float16 needs ~6GB+; small cards (e.g. GTX 1650, 4GB) use
            # int8_float16 to fit. Probe VRAM and pick the safe tier.
            vram = _gpu_vram_mb()
            if vram is not None and vram < 6000:
                return "gpu-4gb"
            return "gpu"
    except Exception:
        pass
    # No GPU. Start AMBITIOUS based on core count, the engine measures its
    # real-time factor and auto-downgrades along CPU_LADDER (medium->small->base
    # ->tiny) if it can't keep up, so we don't have to guess the CPU's speed
    # exactly. A capable CPU keeps the bigger model; a slow one ratchets down.
    cores = os.cpu_count() or 0
    if cores >= 8:
        return "cpu-mid"    # medium start; downgrades to small/base if it lags
    return "cpu"            # small start for modest CPUs (downgrades live if it lags)


# UI quality keys are model SIZES (plus "auto"); map them to a concrete tier per device.
_QUALITY_TO_CPU_TIER = {
    "small": "cpu", "medium": "cpu-mid", "large-v3-turbo": "cpu-strong",
    "large-v3": "cpu-large", "base": "cpu-min",
}
_QUALITY_TO_GPU_TIER = {
    "small": "gpu-small", "base": "gpu-small", "medium": "gpu-medium",
    "large-v3-turbo": "gpu-turbo", "large-v3": "gpu",
}


def _cpu_auto_tier():
    """CPU 'auto' quality: ambitious by core count; the engine downgrades live if it lags."""
    cores = os.cpu_count() or 0
    return "cpu-mid" if cores >= 8 else "cpu"


def _best_size_for(language, engine="auto"):
    """Highest-quality model SIZE for the family this language/engine will use: Fluister
    (Afrikaans) is best at large-v3-turbo (our v2 tune beats large-v3); stock Whisper
    (English/other) is best at large-v3; Swivuriso is one model, so the size is nominal."""
    from . import transcribe
    eng = (engine or "auto").lower()
    fam = eng if eng in ("fluister", "whisper", "swivuriso") else transcribe.family_for_language(language)
    return "large-v3-turbo" if fam == "fluister" else "large-v3"


# Per-family size preference, WORST -> BEST accuracy. "Auto" walks this best-first and takes the
# largest size actually DOWNLOADED for the family, so a meeting starts on a model already on disk
# instead of triggering a surprise multi-minute download at Begin. The BEST (last) entry equals
# _best_size_for's answer for the family: Fluister's tuned large-v3-turbo beats its large-v3 (our v2
# tune), while stock Whisper peaks at large-v3. Entries are the downloadable sizes (voicedl _OFFER /
# FLUISTER_REPOS); base/tiny are internal live-downgrade rungs only, never an auto START size.
_FAMILY_SIZE_ORDER = {
    "fluister": ["small", "medium", "large-v3", "large-v3-turbo"],
    "whisper":  ["small", "medium", "large-v3-turbo", "large-v3"],
}
# Sizes CPU 'auto' may START at (cheapest -> costliest). The live ceiling is _cpu_auto_tier()'s size
# (medium on >=8 cores, else small); turbo/large-v3 are deliberately absent (too slow to hold real-
# time on CPU), so the downloaded-size pick can never exceed the ceiling and stall a live meeting.
_CPU_AUTO_ORDER = ["small", "medium"]


def _downloaded_sizes(family):
    """The set of model SIZES actually cached on disk for `family`, via voicedl's REAL per-size
    on-disk check (voicedl._present), never the always-true fluister_available()/swivuriso_available()
    flags. Whisper checks the stock size name; Fluister checks its HuggingFace repo id.

    Defensive by contract: any failure (voicedl import, a raising probe, an unknown family) yields an
    EMPTY set, so resolve_tier falls back to today's biggest-size behaviour and never crashes on the
    model-picking path."""
    order = _FAMILY_SIZE_ORDER.get(family)
    if not order:
        return set()
    try:
        from . import voicedl, transcribe
        out = set()
        for size in order:
            target = transcribe.FLUISTER_REPOS.get(size) if family == "fluister" else size
            if target and voicedl._present(target):
                out.add(size)
        return out
    except Exception:
        return set()


def _best_downloaded_size(family):
    """Highest-accuracy size actually downloaded for `family` (per-family order above), or None when
    none are downloaded so the caller uses today's biggest-size fallback."""
    order = _FAMILY_SIZE_ORDER.get(family)
    if not order:
        return None
    downloaded = _downloaded_sizes(family)
    for size in reversed(order):          # best first
        if size in downloaded:
            return size
    return None


def _cpu_auto_downloaded_tier(family):
    """CPU 'auto': the largest DOWNLOADED size within today's CPU-auto ceiling (_cpu_auto_tier()'s
    size - medium on >=8 cores, else small), mapped to its CPU tier. Rewards an existing download so a
    live meeting starts on a cached model instead of triggering one; falls back to today's ambitious
    _cpu_auto_tier() when nothing suitable is downloaded (which then downloads on demand and self-
    downgrades). NEVER exceeds the ceiling: turbo/large-v3 are too slow to hold real-time on CPU."""
    ceiling_tier = _cpu_auto_tier()
    from . import transcribe
    ceiling_size = transcribe.TIER_CONFIG.get(ceiling_tier, {}).get("model", "medium")
    order = _CPU_AUTO_ORDER
    candidates = order[:order.index(ceiling_size) + 1] if ceiling_size in order else list(order)
    downloaded = _downloaded_sizes(family)
    for size in reversed(candidates):     # largest size at or below the ceiling first
        if size in downloaded:
            return _QUALITY_TO_CPU_TIER.get(size, ceiling_tier)
    return ceiling_tier


def resolve_tier(quality, device="auto", language=None, engine="auto"):
    """Resolve a UI quality choice + device + language to a concrete TIER_CONFIG tier.

    device: "cpu" forces the CPU even when a GPU is present; "auto"/"gpu" use the GPU when ready.
    An EXPLICIT quality (a size like "medium"/"large-v3") is honoured as-is, on GPU or CPU, so the
    user always gets the model they asked for. "auto" prefers the LARGEST size already DOWNLOADED for
    the chosen language's family, within the hardware ceiling (GPU: up to the family best; CPU: up to
    _cpu_auto_tier()'s medium/small live ceiling) - rewarding an existing download and avoiding a
    surprise multi-minute download at Begin. When nothing suitable is downloaded it falls back to
    today's biggest-size pick (Afrikaans -> turbo, English -> large-v3), which then downloads on
    demand. Swivuriso is one model at a nominal size, so it is unaffected. On the CPU the engine still
    downgrades along CPU_LADDER if the chosen model cannot keep up live."""
    explicit = bool(quality) and quality != "auto"
    # For "auto", resolve the model FAMILY once so the size pick can prefer what is already on disk.
    # Swivuriso never participates (one model, nominal size); any failure leaves fam None, i.e.
    # today's biggest-size behaviour.
    fam = None
    if not explicit:
        try:
            from . import transcribe
            eng = (engine or "auto").lower()
            fam = eng if eng in ("fluister", "whisper", "swivuriso") else transcribe.family_for_language(language)
        except Exception:
            fam = None
    if device != "cpu":
        try:
            from . import cudadl
            if cudadl.cuda_ready():
                # Do NOT route through pick_tier() here: it honours the SA_LIVE_TIER env override
                # (a CLI-only feature), which could force a CPU tier even though the GPU is ready.
                if explicit:
                    size = quality
                elif fam in ("fluister", "whisper"):
                    # Prefer the largest DOWNLOADED size; only when none is on disk fall back to
                    # today's family best (which downloads on demand).
                    size = _best_downloaded_size(fam) or _best_size_for(language, engine)
                else:
                    size = _best_size_for(language, engine)
                return _QUALITY_TO_GPU_TIER.get(size, "gpu")
        except Exception:
            pass
    # CPU path (forced, or no usable GPU): honour the picked quality, never a GPU tier.
    if not explicit:
        # Prefer the largest downloaded size within the CPU live ceiling; swivuriso and any
        # family-detection failure keep today's ambitious core-count pick.
        if fam in ("fluister", "whisper"):
            return _cpu_auto_downloaded_tier(fam)
        return _cpu_auto_tier()
    if quality in TIER_CHOICES:
        t = pick_tier(quality)
        return "cpu-large" if t in ("gpu", "gpu-4gb") else t
    if quality == "large-v3":
        return "cpu-large"
    return _QUALITY_TO_CPU_TIER.get(quality, _cpu_auto_tier())


def default_chunk_seconds(tier):
    return 8 if tier.startswith("gpu") else 15


def default_output_path():
    sessions = Path(__file__).resolve().parent.parent / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    return sessions / f"{datetime.now().strftime('%Y-%m-%d-%H%M')}-session.md"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = parse_args()

    # Import lazily so --list-devices doesn't pay the import cost of faster-whisper
    from . import devices

    if args.list_devices:
        devices.print_devices()
        return 0

    tier = pick_tier(args.tier)
    output_path = Path(args.output) if args.output else default_output_path()
    chunk_seconds = args.chunk_seconds or default_chunk_seconds(tier)

    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    # Optional: seed the prompt with the meeting's attendees from Outlook. Wrapped
    # so a calendar/Graph hiccup can never block starting the transcription.
    if args.seed_from_calendar:
        try:
            from . import outlook
            meeting = outlook.current_or_next_meeting(outlook.get_token())
        except Exception as e:
            print(f"[calendar] skipped, {e}", flush=True)
            meeting = None
        if meeting:
            if meeting["subject"]:
                print(f"[calendar] meeting: {meeting['subject']}", flush=True)
            if meeting["attendees"]:
                names = ", ".join(meeting["attendees"])
                args.prompt = f"{args.prompt}, {names}" if args.prompt else names
                print(f"[calendar] seeded prompt with attendees: {names}", flush=True)

    if args.prompt and len(args.prompt) > 224 * 4:
        print("[warn] --prompt likely exceeds Whisper's ~224-token initial_prompt limit; "
              "the model will truncate.", flush=True)

    if args.keep_audio:
        print("[keep-audio] ON, raw audio will be saved next to the transcript. "
              "POPIA: ensure everyone recorded has consented.", flush=True)

    # Banner
    bar = "=" * 64
    print(bar)
    print("  SA-Live-Transcribe V0")
    print(f"  Tier:       {tier}")
    print(f"  Language:   {args.language}")
    print(f"  Chunk:      {chunk_seconds}s")
    print(f"  Output:     {output_path}")
    if args.prompt:
        preview = args.prompt if len(args.prompt) <= 60 else args.prompt[:57] + "..."
        print(f"  Prompt:     {preview}")
    if args.offline:
        print("  Offline:    yes (model must already be cached)")
    print(bar)
    print("  Loading model (first run may take a minute)...", flush=True)

    from . import capture, transcribe, sinks

    try:
        engine = transcribe.Engine(
            tier=tier,
            language=args.language,
            initial_prompt=args.prompt,
        )
    except Exception as e:
        print(f"\n[fatal] could not load model: {e}", flush=True)
        print("If this is your first GPU run, confirm CUDA 12 + cuDNN 9 are installed.", flush=True)
        print("If this is your first CPU run, the model is downloading, be patient or pre-warm via SETUP.md.", flush=True)
        return 2

    stdout_sink = sinks.StdoutSink()
    md_sink = sinks.MarkdownSink(output_path)
    engine.subscribe(stdout_sink)
    engine.subscribe(md_sink)
    engine.start()

    # Optional raw-audio recorder. Tapped BEFORE the engine queue so the
    # recording stays complete even if transcription drops chunks under load -
    # that's what lets a later high-accuracy re-transcribe recover everything.
    recorder = None
    if args.keep_audio:
        recorder = sinks.AudioRecorder(output_path.with_suffix(""))

    def feed(source, audio, t_start):
        if recorder is not None:
            recorder.on_chunk(source, audio, t_start)
        engine.on_chunk(source, audio, t_start)

    cap = capture.AudioCapture(
        mic_device=args.mic_device,
        loopback_device=args.loopback_device,
        chunk_seconds=chunk_seconds,
        on_chunk=feed,
    )

    print(f"  Model loaded: {engine.model_name}", flush=True)
    print("  Ready. Speak now. Ctrl+C to stop.", flush=True)
    print("-" * 64, flush=True)

    stop_requested = {"v": False}

    def handle_sigint(_sig, _frame):
        if stop_requested["v"]:
            print("\n[double Ctrl+C, forcing exit]", flush=True)
            sys.exit(1)
        stop_requested["v"] = True
        print("\n[stopping, flushing transcript...]", flush=True)

    signal.signal(signal.SIGINT, handle_sigint)

    cap.start()
    try:
        while not stop_requested["v"]:
            time.sleep(0.2)
    finally:
        cap.stop()
        engine.stop()
        md_sink.close()
        if recorder is not None:
            recorder.close()

    print(f"\nDone. Transcript: {output_path}", flush=True)
    if recorder is not None:
        print(f"Audio saved: {output_path.with_suffix('').name}-MIC.wav / -SYS.wav", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
