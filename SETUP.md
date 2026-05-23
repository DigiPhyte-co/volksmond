# Setup, SA-Live-Transcribe

The project folder lives in OneDrive (synced across all your machines). The Python venv and the cached Whisper models live **outside OneDrive** on each machine (so they don't sync 1.5+ GB of binaries between PC and laptop).

| What | Where | Synced? |
|---|---|---|
| Source code, scripts, UI | `C:\Users\seanf\OneDrive - DigiPhyte\Cowork\SA-Live-Transcribe\` | Yes (OneDrive) |
| Saved transcripts | `…\SA-Live-Transcribe\sessions\` | Yes, same on every machine |
| Python venv (per machine) | `%LOCALAPPDATA%\sa-live-transcribe\.venv\` | No |
| Whisper model cache (per machine) | `%USERPROFILE%\.cache\huggingface\` | No |

---

## First-time setup on a new machine

**Prerequisite:** Python 3.12 installed.
- Check: open a PowerShell window and run `py -0`. If you see `3.12` in the list, you're set.
- If not: install from [python.org/downloads/release/python-3120](https://www.python.org/downloads/release/python-3120/), or run `winget install Python.Python.3.12` in a new PowerShell.

**Then:**

1. Wait for OneDrive to finish syncing the SA-Live-Transcribe folder.
2. Open the folder in File Explorer.
3. **Double-click `First-time setup.bat`.**
4. Read the summary, press a key to continue.
5. Wait 5-15 minutes. The script will:
   - Create a venv at `%LOCALAPPDATA%\sa-live-transcribe\.venv`
   - Install all pinned dependencies
   - Detect whether you have an NVIDIA GPU
   - Pre-download the Whisper model(s) for your hardware:
     - GPU box → `large-v3` (~3 GB, fp16) AND `large-v3-turbo` (~1.5 GB, int8) for cross-tier testing
     - CPU-only → `large-v3-turbo` (~1.5 GB, int8)
6. When it says **Setup complete**, press a key to close.
7. **Double-click `Launch SA-Live-Transcribe.bat`**, the browser opens to `http://127.0.0.1:8765`. Done.

## Daily use

```text
Double-click   →   Launch SA-Live-Transcribe.bat
Browser opens  →   http://127.0.0.1:8765
Click          →   Start transcription
Click          →   Stop transcription
Close window   →   stops the server
```

Transcripts save to `sessions/` and appear in the sidebar (clickable). The folder icon next to the sidebar header opens File Explorer to that folder.

## Canonical re-transcribe (the two-pass workflow)

The live transcript is a real-time *preview*, fast, but it can drop chunks under load and has no speaker labels beyond the MIC/SYS split. For the accurate, speaker-labelled record, capture the audio during the meeting and re-transcribe afterwards (on the desktop / GPU box):

1. Run the meeting with `--keep-audio` (CLI) or the "Record audio" toggle (UI). **POPIA: only with everyone's consent.** This saves `<stem>-MIC.wav` (you) and `<stem>-SYS.wav` (remote participants) next to the transcript.
2. Afterwards, re-transcribe with the heavy-stack python (WhisperX large-v3 + pyannote diarisation, the same pipeline as the BiaD intake transcripts):

```powershell
& "$env:LOCALAPPDATA\mms-env\Scripts\python.exe" retranscribe.py sessions\2026-05-22-1012-acme
```

It writes `<stem>-canonical.md` and `.srt`: WhisperX large-v3 at beam 10, with per-speaker labels (a single-voice mic stream collapses to "Me"; remote voices become Speaker 1, 2, …), the two streams merged by time. Useful flags: `--me-label "Sean"`, `--prompt "Vleissentraal, Hennie, ..."`, `--max-speakers 4`, `--no-diarise`.

Diarisation needs a Hugging Face token once (after accepting the terms at [huggingface.co/pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)): `& "$env:LOCALAPPDATA\mms-env\Scripts\hf.exe" auth login`. Without a token it still produces a transcript, just without speaker labels.

## Re-running setup

Re-running `First-time setup.bat` is safe, pip install is idempotent, and the model cache is reused. Use it when you want to upgrade deps or recover from a partial install.

Flags (run from PowerShell with `.\setup.ps1 -Force` etc):

| Flag | What it does |
|---|---|
| `-Force` | Delete the venv and rebuild from scratch |
| `-CpuOnly` | Skip the GPU model even on a GPU machine (for CPU-only testing) |

## Enabling a laptop GPU (e.g. Dell G3 / GTX 1650, 4 GB)

A laptop with a small NVIDIA GPU can run the `gpu-4gb` tier (large-v3, int8_float16), far faster and more accurate than any CPU tier, and it fits in 4 GB. But if `ctranslate2` can't see CUDA, the app **silently falls back to CPU** (this is what degraded the C12 test). To enable and verify the GPU in one step, run this on the laptop:

```powershell
.\setup-laptop-gpu.ps1
```

It checks the NVIDIA driver, makes `ctranslate2` see the GPU (reinstalling the pinned deps if an old `ctranslate2` without bundled cuDNN is the blocker), proves the `gpu-4gb` tier actually loads and runs on the card, and pre-caches the CPU ladder models (medium/small/base/tiny) so a mid-meeting downgrade never stalls on a download. It ends with a plain verdict, GPU ready, or exactly why not. Re-running is safe and idempotent.

You can run the underlying diagnostics by hand any time:

```powershell
$py = "$env:LOCALAPPDATA\sa-live-transcribe\.venv\Scripts\python.exe"
& $py -m live_transcribe.gpucheck probe     # is a CUDA device visible to ctranslate2?
& $py -m live_transcribe.gpucheck gputest    # can it load + run large-v3 on the GPU?
```

Once the verdict is "GPU ready", just launch normally, auto-detect picks `gpu-4gb` on a 4 GB card.

## Cleaning up an old in-OneDrive venv

If you set this up on the original PC before the cross-machine refactor, you have an old `.venv\` folder *inside* OneDrive that's no longer used. Delete it:

```powershell
Remove-Item -Recurse -Force "C:\Users\seanf\OneDrive - DigiPhyte\Cowork\SA-Live-Transcribe\.venv"
```

OneDrive will propagate the delete to all your machines. Frees ~1.5 GB of cloud storage.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `py -3.12: command not found` in setup | Only Python 3.13/3.14 installed | Install Python 3.12 (see Prerequisite above) |
| Launch.bat says "Python venv not found" | First-time setup hasn't run on this machine | Double-click `First-time setup.bat` |
| `Could not locate cudnn_*.dll` | Old `ctranslate2` (<4.5) without bundled cuDNN | Run `.\setup-laptop-gpu.ps1` (reinstalls deps + verifies the GPU) |
| GPU laptop transcribes on CPU / very slow | `ctranslate2` can't see CUDA, so it fell back to CPU | Run `.\setup-laptop-gpu.ps1`, it diagnoses and fixes this, then confirms `gpu-4gb` works |
| `No WASAPI loopback device found` | Audio driver hiding the loopback | Update audio driver, reboot |
| Transcript blank | Mic muted in Windows or speaker output muted | Check Windows volume mixer |
| Latency > 60s on `cpu-strong` | Something else hogging CPU | Close Chrome tabs, or pick `cpu-mid` tier |
| Transcript drifts to Dutch spelling | Source audio is English but language forced to `af` | Switch Language to "Auto-detect per chunk" in the UI |
| `SYS` stream silent but `MIC` works | Audio playing through a different device than the chosen loopback | Pick the right speaker via the loopback override, or check Windows sound settings |
