# Set up SA-Live-Transcribe on this machine, runbook for Claude Code

**Sean:** open Claude Code inside this folder and say *"Read SETUP-WITH-CLAUDE-CODE.md and do it."* That's all.

---

## Claude Code: read this, then DO it

You are installing and launching a desktop app on **this Windows machine**. **Execute the
steps below yourself**, run the commands, check the output, fix problems, and don't stop
until the app is running (or you hit something you genuinely can't resolve and must ask Sean).
Do not just explain the steps. Use the PowerShell tool for everything.

This is a one-time setup. The project source is synced here via OneDrive, but the Python
environment and the speech-to-text models are **not** synced, they have to be built locally
on this machine. That's what you're doing.

### What the app is (context, so you can reason about failures)

A local Afrikaans/English live-transcription tool. It captures system audio (WASAPI loopback)
+ microphone, runs OpenAI Whisper locally via `faster-whisper`, and shows a live transcript in
a browser UI. **Everything runs on-device, no cloud, no API keys.** It's a Python package
(`live_transcribe`) plus a small FastAPI web UI (`live_transcribe.web`).

### Environment facts (don't re-derive these, they were learned the hard way)

- **OS:** Windows. Shell: PowerShell. Use PowerShell syntax.
- **Python:** must be **3.12**. Python 3.13/3.14 do **not** have `ctranslate2` wheels yet, using
  them will fail at `pip install`. Always create the venv with `py -3.12`.
- **venv location (NOT in OneDrive):** `%LOCALAPPDATA%\sa-live-transcribe\.venv`
  → expands to `C:\Users\<you>\AppData\Local\sa-live-transcribe\.venv`
- **Model cache (NOT in OneDrive):** `%USERPROFILE%\.cache\huggingface`
- **Dependencies are pinned** in `requirements.txt` in this folder. Just install from it -
  the pins already encode fixes for: cuDNN bundling (`ctranslate2==4.7.2`), the
  `large-v3-turbo` model name (`faster-whisper==1.2.1`), and WASAPI loopback (`PyAudioWPatch`).
- **GPU is optional.** `ctranslate2` 4.7+ bundles cuDNN, so a GPU box only needs the NVIDIA
  driver (no separate CUDA/cuDNN install). A CPU-only box works fine too.

### The fastest path: run the existing setup script

There is already a tested setup script in this folder: `setup.ps1`. It does the whole job
(venv + deps + model download, auto-detecting GPU vs CPU). **Try it first:**

```powershell
# 1. Make sure you're in the project folder (the one containing requirements.txt and setup.ps1).
#    If your working directory isn't already here, cd to it first. Adjust the path if the
#    username differs:
Set-Location "C:\Users\seanf\OneDrive - DigiPhyte\Cowork\SA-Live-Transcribe"

# 2. Confirm Python 3.12 is available
py -0
```

- If `py -0` lists a `3.12` entry → continue to step 3.
- If it does **not** list 3.12 → install it first (see "If Python 3.12 is missing" below), then continue.

```powershell
# 3. Run the setup script (creates venv, installs deps, downloads models)
powershell -ExecutionPolicy Bypass -NoProfile -File ".\setup.ps1"
```

This takes 5-15 minutes (mostly the ~1.5 GB model download on a CPU box, more if a GPU is
detected). Watch the output. It prints `=== Setup complete ===` on success.

### If Python 3.12 is missing

Install it yourself, non-interactively, then re-check:

```powershell
winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
```

After it finishes, **open a fresh PowerShell context** (the `py` launcher may not appear in the
current session's PATH until then) and re-run `py -0` to confirm `3.12` shows up. If `winget`
isn't available or fails, tell Sean to install Python 3.12 from
<https://www.python.org/downloads/release/python-3120/> (tick "Add to PATH"), then resume.

### If `setup.ps1` fails, do it manually

The script just wraps these commands. Run them yourself, in order, from the project folder:

```powershell
$venv = "$env:LOCALAPPDATA\sa-live-transcribe\.venv"
$py   = "$venv\Scripts\python.exe"

# Create the venv on Python 3.12
py -3.12 -m venv $venv

# Install pinned dependencies
& $py -m pip install --upgrade pip
& $py -m pip install -r ".\requirements.txt"

# Decide GPU vs CPU: does nvidia-smi succeed?
$gpu = $false
try { nvidia-smi -L *> $null; if ($LASTEXITCODE -eq 0) { $gpu = $true } } catch {}

# Pre-download the model(s) so the first real meeting doesn't stall.
if ($gpu) {
    & $py -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cuda', compute_type='float16')"
}
& $py -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8')"
```

If `pip install` fails with a message about no matching distribution for `ctranslate2`, you are
almost certainly on Python 3.13/3.14, recreate the venv with `py -3.12` explicitly.

### Verify it works

```powershell
$py = "$env:LOCALAPPDATA\sa-live-transcribe\.venv\Scripts\python.exe"
& $py -m live_transcribe --list-devices
```

You should see a table of audio devices including at least one `LOOP` (WASAPI loopback) and one
`MIC`. If you see that, the install is good.

### Launch it

```powershell
& "$env:LOCALAPPDATA\sa-live-transcribe\.venv\Scripts\python.exe" -m live_transcribe.web --port 8765
```

This starts the web server and opens the browser to <http://127.0.0.1:8765>. Leave it running.
(Equivalently, Sean can double-click `Launch SA-Live-Transcribe.bat` in this folder, it does
the same thing.)

### When you're done

Report back to Sean, briefly:
- Whether Python had to be installed
- Whether a GPU was detected (so he knows which tier to expect, GPU box defaults to the GPU
  model; CPU-only box uses `large-v3-turbo`)
- That the app is running at <http://127.0.0.1:8765>
- Remind him: in the UI, for English or mixed-language meetings he should set Language to
  "Auto-detect per chunk"; for pure Afrikaans, leave it on "Afrikaans".

---

## Known gotchas (reference)

| Symptom | Cause | Fix |
|---|---|---|
| `pip` can't find `ctranslate2` | venv is on Python 3.13/3.14 | Recreate venv with `py -3.12` |
| `Could not locate cudnn_*.dll` at transcribe time | `ctranslate2` < 4.5 (no bundled cuDNN) | Ensure `ctranslate2==4.7.2` installed (it's pinned) |
| `--list-devices` shows no `LOOP` entry | WASAPI loopback hidden by audio driver | Update audio driver; reboot |
| `WasapiSettings ... loopback` error | someone swapped in `sounddevice`/`soundcard` | Must use `PyAudioWPatch` (pinned in requirements.txt) |
| Port 8765 already in use | a server is already running | Find and stop it, or launch with `--port 8766` |
| Transcript drifts to Dutch | English audio forced to `af` | In the UI pick "Auto-detect per chunk" |

This file is safe to re-run against, every step is idempotent.
