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
    import subprocess
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
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


# UI quality keys are model names (plus "auto"); map them to concrete tiers. large-v3
# uses the GPU when one is present, else CPU (slow live, but fine for uploaded files).
_QUALITY_TO_CPU_TIER = {
    "small": "cpu", "medium": "cpu-mid", "large-v3-turbo": "cpu-strong",
    "large-v3": "cpu-large", "base": "cpu-min",
}


def _cpu_auto_tier():
    """CPU 'auto' quality: ambitious by core count; the engine downgrades live if it lags."""
    cores = os.cpu_count() or 0
    return "cpu-mid" if cores >= 8 else "cpu"


def resolve_tier(quality, device="auto"):
    """Resolve a UI quality choice + device preference to a concrete TIER_CONFIG tier.

    device: "cpu" forces the CPU even when a GPU is present; "auto"/"gpu" use the GPU when
    it is ready. The UI exposes this as a GPU/CPU toggle (default GPU on GPU machines). On
    the GPU large-v3 is fast, so the Quality dropdown only applies on the CPU; on the GPU we
    run the best model the card can hold."""
    if device != "cpu":
        try:
            from . import cudadl
            if cudadl.cuda_ready():
                # Pick the GPU tier directly from VRAM. Do NOT route through pick_tier() here:
                # it honours the SA_LIVE_TIER env override (a CLI-only feature), so a stray
                # leftover like SA_LIVE_TIER=cpu-strong would silently force a CPU tier even
                # though the user chose GPU and the GPU is ready (the cuda_ready=True ->
                # cpu-strong bug seen on the test laptop). The GUI app never honours it.
                vram = _gpu_vram_mb()
                return "gpu-4gb" if (vram is not None and vram < 6000) else "gpu"
        except Exception:
            pass
    # CPU path (forced, or no usable GPU): honour the picked quality, never a GPU tier.
    if not quality or quality == "auto":
        return _cpu_auto_tier()
    if quality in TIER_CHOICES:
        t = pick_tier(quality)
        return "cpu-large" if t in ("gpu", "gpu-4gb") else t
    if quality == "large-v3":
        return "cpu-large"
    return _QUALITY_TO_CPU_TIER.get(quality, _cpu_auto_tier())


def default_chunk_seconds(tier):
    return 8 if tier in ("gpu", "gpu-4gb") else 15


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
