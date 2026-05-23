r"""Offline canonical re-transcribe with speaker diarisation.

The live tool (faster-whisper streaming) is the in-meeting preview. When you run a
meeting with --keep-audio it saves the raw per-source audio (`<stem>-MIC.wav` =
your mic, `<stem>-SYS.wav` = remote participants via loopback). This script turns
that saved audio into the *canonical* transcript: WhisperX large-v3 at high beam
(no real-time pressure) + pyannote speaker diarisation, with the two streams
merged by time. It is the second half of the two-pass workflow.

RUN IT WITH THE HEAVY-STACK PYTHON (torch / whisperx / pyannote), NOT the lean
live-transcribe venv, which deliberately has no torch:

    & "$env:LOCALAPPDATA\mms-env\Scripts\python.exe" retranscribe.py sessions\2026-05-22-1012-acme

Diarisation needs a Hugging Face token + accepting the gated model terms once at
https://huggingface.co/pyannote/speaker-diarization-3.1 . One-time login:

    & "$env:LOCALAPPDATA\mms-env\Scripts\hf.exe" auth login

Without a token it still runs, but emits one label per stream (no diarisation).

Input:  a session stem (finds `-MIC.wav` / `-SYS.wav`), or a path to any .wav.
Output: `<stem>-canonical.md` and `<stem>-canonical.srt` next to the audio.
"""
import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# Anti-Dutch anchor, kept in step with live_transcribe.transcribe.AF_ANCHOR_PROMPT.
# Whisper drifts to Dutch spellings on Afrikaans; this biases it back.
AF_ANCHOR_PROMPT = (
    "Dit is 'n gesprek in Afrikaans. Ons praat Suid-Afrikaanse Afrikaans, "
    "nie Nederlands nie. Algemene woorde: baie, nogal, lekker, kuier, sjoe, "
    "eish, vandag, môre, gister, dankie tog, asseblief, julle, hulle, ons, "
    "kinders, kollegas, vergadering, besigheid."
)


def load_audio_pyav(path, target_sr=16000):
    """Decode any audio/video file to 16k mono float32 via PyAV (no ffmpeg binary)."""
    import av
    import numpy as np
    container = av.open(str(path))
    stream = container.streams.audio[0]
    resampler = av.audio.resampler.AudioResampler(format="flt", layout="mono", rate=target_sr)
    chunks = []
    for frame in container.decode(stream):
        for resampled in resampler.resample(frame):
            chunks.append(resampled.to_ndarray().flatten())
    container.close()
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32)


def _hf_token():
    import os
    from huggingface_hub import get_token
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or get_token()


def _fmt_ts(t):
    t = max(0.0, t)
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


def _fmt_srt_time(t):
    h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def _collapse_repetition(text, max_run=3):
    """Collapse pathological consecutive-token loops (Whisper hallucination on
    silence/noise), e.g. 'Asseblief. Asseblief. ...' -> 'Asseblief.'. Mirrors the
    live engine's backstop; emphasis like 'baie baie baie' (run of 3) is kept."""
    words = text.split()
    if len(words) <= max_run:
        return text
    out, run = [], 1
    for i, w in enumerate(words):
        norm = w.lower().strip(".,!?;:")
        prev = words[i - 1].lower().strip(".,!?;:") if i > 0 else None
        run = run + 1 if (norm and norm == prev) else 1
        if run <= max_run:
            out.append(w)
    return " ".join(out)


def _assign_speakers(segments, annotation, tag, me_label):
    """Tag each segment with the diarisation speaker that dominates its span.

    A single-speaker stream collapses to `me_label` (that's you, on your mic);
    a multi-speaker stream gets `<tag> 1`, `<tag> 2`, ... in first-appearance order.
    """
    turns = list(annotation.itertracks(yield_label=True))

    def speaker_for(start, end):
        overlaps = {}
        for turn, _, spk in turns:
            ov = min(end, turn.end) - max(start, turn.start)
            if ov > 0:
                overlaps[spk] = overlaps.get(spk, 0.0) + ov
        return max(overlaps, key=overlaps.get) if overlaps else None

    for seg in segments:
        seg["_raw"] = speaker_for(seg["start"], seg["end"])
    distinct = sorted({seg["_raw"] for seg in segments if seg["_raw"] is not None})
    if me_label and len(distinct) <= 1:
        name_map = {spk: me_label for spk in distinct}
    else:
        name_map = {spk: f"{tag} {i + 1}" for i, spk in enumerate(distinct)}
    for seg in segments:
        seg["speaker"] = name_map.get(seg.pop("_raw"), me_label or f"{tag} 1")


def transcribe_stream(wav_path, tag, me_label, model, language, batch_size,
                      pipeline, min_speakers, max_speakers):
    import torch
    audio = load_audio_pyav(wav_path)
    dur = len(audio) / 16000.0
    if dur < 0.5:
        print(f"  [{wav_path.name}] empty/near-silent, skipping", flush=True)
        return []
    print(f"  [{wav_path.name}] {dur / 60:.1f} min, transcribing...", flush=True)
    result = model.transcribe(audio, batch_size=batch_size, language=language)
    segments = result.get("segments", [])
    for seg in segments:
        seg["source"] = tag
    if pipeline is not None and segments:
        print(f"  [{wav_path.name}] diarising...", flush=True)
        wav_t = torch.from_numpy(audio).unsqueeze(0)
        diar = pipeline({"waveform": wav_t, "sample_rate": 16000},
                        min_speakers=min_speakers, max_speakers=max_speakers)
        annotation = getattr(diar, "speaker_diarization", diar)
        _assign_speakers(segments, annotation, tag, me_label)
    else:
        for seg in segments:
            seg["speaker"] = me_label or tag
    return segments


def _write_outputs(out_md, out_srt, segments, stem, device, diarised):
    from datetime import datetime
    out_md.parent.mkdir(parents=True, exist_ok=True)
    speakers = sorted({s.get("speaker", "") for s in segments if s.get("speaker")})
    with open(out_md, "w", encoding="utf-8") as fmd, open(out_srt, "w", encoding="utf-8") as fsrt:
        fmd.write("# Canonical transcript (offline re-transcribe)\n\n")
        fmd.write(f"- Source audio: `{Path(stem).name}-*.wav`\n")
        fmd.write(f"- Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        fmd.write(f"- Engine: WhisperX large-v3 (beam 10) on {device}\n")
        fmd.write(f"- Diarisation: {'pyannote/speaker-diarization-3.1' if diarised else 'OFF (no Hugging Face token)'}\n")
        fmd.write(f"- Speakers: {', '.join(speakers) if speakers else 'n/a'}\n\n---\n\n")
        n = 0
        for seg in segments:
            text = _collapse_repetition((seg.get("text") or "").strip())
            if not text:
                continue
            n += 1
            spk = seg.get("speaker", "")
            prefix = f"**[{spk}]** " if spk else ""
            fmd.write(f"`[{_fmt_ts(seg['start'])}]` {prefix}{text}\n\n")
            srt_prefix = f"[{spk}] " if spk else ""
            fsrt.write(f"{n}\n{_fmt_srt_time(seg['start'])} --> {_fmt_srt_time(seg['end'])}\n{srt_prefix}{text}\n\n")


def main():
    ap = argparse.ArgumentParser(
        prog="retranscribe",
        description="Offline canonical re-transcribe + diarisation of a --keep-audio session.",
    )
    ap.add_argument("session", help="session stem (finds -MIC.wav/-SYS.wav) or a path to a .wav")
    ap.add_argument("--me-label", default="Me",
                    help="label for your own mic stream when it has a single speaker (default: Me)")
    ap.add_argument("--language", default="af", help="Whisper language code (default: af)")
    ap.add_argument("--prompt", default=None,
                    help="initial prompt, proper nouns, names, jargon. 'af' also gets an anti-Dutch anchor.")
    ap.add_argument("--min-speakers", type=int, default=1)
    ap.add_argument("--max-speakers", type=int, default=6)
    ap.add_argument("--no-diarise", action="store_true", help="skip diarisation even if a token is present")
    ap.add_argument("--output", default=None, help="output .md path (default: <stem>-canonical.md)")
    args = ap.parse_args()

    try:
        import torch
        import whisperx
    except Exception as e:
        print("[fatal] this script needs the heavy stack (torch / whisperx / pyannote).")
        print("        run it with the mms-env python, e.g.:")
        print(f'        & "$env:LOCALAPPDATA\\mms-env\\Scripts\\python.exe" retranscribe.py {args.session}')
        print(f"        (import error: {e})")
        return 2

    # Resolve input stream(s).
    p = Path(args.session)
    streams = []  # (path, tag, me_label_for_this_stream)
    if p.suffix.lower() == ".wav":
        streams.append((p, "Speaker", args.me_label))
        stem = Path(str(p.with_suffix("")).removesuffix("-MIC").removesuffix("-SYS"))
    else:
        mic, sysw = p.parent / f"{p.name}-MIC.wav", p.parent / f"{p.name}-SYS.wav"
        if mic.exists():
            streams.append((mic, "Mic", args.me_label))   # your voice
        if sysw.exists():
            streams.append((sysw, "Speaker", None))         # remote participants
        stem = p
    if not streams:
        print(f"[fatal] no audio found for {args.session!r} (looked for -MIC.wav / -SYS.wav).")
        return 2

    out_md = Path(args.output) if args.output else Path(f"{stem}-canonical.md")
    out_srt = out_md.with_suffix(".srt")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    batch_size = 16 if device == "cuda" else 4

    prompt = args.prompt
    if args.language == "af":
        prompt = f"{AF_ANCHOR_PROMPT} {prompt}".strip() if prompt else AF_ANCHOR_PROMPT

    diarise = not args.no_diarise
    token = _hf_token() if diarise else None
    if diarise and token is None:
        print("[warn] no Hugging Face token found, running WITHOUT diarisation.")
        print('       set it up once with: & "$env:LOCALAPPDATA\\mms-env\\Scripts\\hf.exe" auth login')
        diarise = False

    print(f"Device: {device} | model: large-v3 | streams: {[s[0].name for s in streams]}", flush=True)
    model = whisperx.load_model("large-v3", device, compute_type=compute_type, language=args.language,
                                asr_options={"initial_prompt": prompt, "beam_size": 10})

    pipeline = None
    if diarise:
        from pyannote.audio import Pipeline
        print("Loading pyannote/speaker-diarization-3.1 ...", flush=True)
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", token=token)
        pipeline.to(torch.device(device))

    all_segs = []
    for wav, tag, me in streams:
        all_segs += transcribe_stream(wav, tag, me, model, args.language, batch_size,
                                      pipeline, args.min_speakers, args.max_speakers)
    all_segs.sort(key=lambda s: s["start"])

    _write_outputs(out_md, out_srt, all_segs, stem, device, diarise)
    print(f"\nDone. {len(all_segs)} segments. Wrote:\n  {out_md}\n  {out_srt}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
