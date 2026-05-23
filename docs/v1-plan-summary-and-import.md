# V1 plan: local summaries, file import, record-later

Status: scope agreed 2026-05-22. Implementation pending the Claude Design third pass (UI)
and the Gemma conversion check. No feature code written yet; this is the build spec.

## Why these are in V1
All three reuse infrastructure already built, so the marginal cost is low:
- Summary reuses the bundled ctranslate2 engine (proven end to end 2026-05-22: ct2 4.7.2
  converts and runs a generative LLM on CPU). No new inference engine, no Ollama, no llama.cpp.
- Import reuses retranscribe.py's PyAV decode plus the existing live engine.
- Record-later reuses the existing `--keep-audio` recorder plus the import path.

## A. Local summary module (Pro)
Engine: **llama-cpp-python** running a GGUF model in-process (no daemon). ctranslate2 4.7.2
cannot convert the small Gemma 4 models (they use KV-shared layers; only the 31B converts,
which is too big), so summaries use llama.cpp while ASR stays on ctranslate2. Two engines,
both prebuilt pip wheels plus a model file. CPU by default (what the target machines have);
GPU offload optional later.

Models (optional download, user-selectable in Setup and in Settings). Source: the UNGATED
`unsloth/gemma-4-E2B-it-GGUF` repo (no licence click):

| Option | Model (GGUF) | Approx download | RAM guidance | Quality |
|---|---|---|---|---|
| Small (default) | Gemma 4 E2B, Q4_K_M | ~3GB | 8GB system RAM | Clean English, good Afrikaans (proven) |
| Larger | Gemma 4 E4B, Q4_K_M | ~5 to 6GB | 16GB system RAM | More polished |

- Not the 26B/31B (too heavy for the target machines).
- Download is optional and on demand: only fetched if the user enables summaries. Reuse the
  same model-download progress screen built for Whisper.
- No build-time conversion needed: the GGUF is downloaded and run directly. Validated
  2026-05-23 end-to-end on the app venv (~2s load, ~23s CPU summary, clean Afrikaans).

Gating: Pro feature `ai_summary` (entitlement seam already built). Free users see it locked.

Trigger: the Summarise button is enabled only once a full transcript exists (a live session
stopped, or an imported file finished). One click produces the summary, shown with the
transcript and saved alongside it.

Instruction and language: uses the active saved AI instruction (the custom-system-prompt
feature) to shape output (minutes, action items, counsellor themes). Output language
selectable; English default (cleanest), Afrikaans option.

## B. Chunking for long transcripts
- Context window: Gemma 4 small models have a large window (reported 32K to 128K depending on
  size; confirm via `config.json` max_position_embeddings at conversion). A 2-hour meeting is
  roughly 20,000 to 30,000 tokens, so it fits. Fitting is not the constraint.
- The real constraint is small-model coherence over long input: a 2B model loses the thread
  well before its context limit.
- Approach: map-reduce. Split into roughly 3,000 to 4,000-token segments (about 15 to 20
  minutes of speech) with light overlap, summarise each, then summarise the segment summaries
  into the final minutes.
- Threshold: single-pass under about 20 minutes (~4K tokens); map-reduce beyond.
- Build TODO: confirm the chosen model's real context, then quality-test on a full-length
  transcript and tune the chunk size.

## C. Import and transcribe an existing file
- Entry: an "Upload a recording to transcribe" button on the start / new-session screen, a
  first-class entry beside starting a live session (not a hidden mode).
- Accept common audio and video (mp3, m4a, mp4, wav, etc.).
- Pipeline: PyAV decode (retranscribe.py already does this) to 16k mono, chunk, feed the
  existing engine, reuse the live transcript view and save.
- On completion, the Summarise button enables (if a summary model is installed).

## D. Record now, transcribe later (for slow machines)
- Record-only mode: run capture plus the AudioRecorder (already exists via `--keep-audio`)
  with the live engine off. For machines that cannot keep up live.
- Then "Transcribe this recording" runs the recorded WAV(s) through the engine offline (the
  import path on the just-recorded file).
- Surfacing: when the "system struggling" banner appears (the engine already measures
  real-time factor), offer a calm "switch to record-only, transcribe after" path.

## E. Stop-button logic (live session)
- No recording: a single "Stop" (stops transcription, finalises the transcript).
- Recording enabled: Stop expands to three choices:
  - Stop transcription only (keep recording)
  - Stop recording only (keep live transcription)
  - Stop recording and transcription
- Struggling-banner integration: prominently offer "Stop transcription, keep recording" so
  the user can fall back to record-only and transcribe or summarise afterwards.

## F. Setup question
- First-run (and changeable in Settings): "Do you want to just transcribe, or also summarise
  on your machine?"
  - If summarise: show the model picker (small or larger) with download size and RAM
    recommendation; download now or later.
- Default: transcribe-only (smallest footprint); summary opt-in.

## G. Report a bug / request a feature
A small "Report a bug or request a feature" affordance (help/about area or footer).
Privacy-first: it must never silently send data. It opens a prefilled email (mailto:
carrying app version and OS plus an optional user note) or a simple web form the user
submits themselves. No automatic log or transcript attachment; if logs would help, the
user attaches them deliberately. Low effort; the mailto path needs no backend.

## Build order
1. Import-and-transcribe (foundation). ~1 to 2 days.
2. Record-later (rides on import). ~1 day.
3. Summary module (BUILT 2026-05-23): summarise.py via llama.cpp, /api/summarise + /api/models,
   map-reduce chunking, Gemma 4 e2b GGUF installed and working. Remaining: the model
   download/selection UI and PyInstaller bundling of llama-cpp-python.
4. UI per the Claude Design third pass below.

Rough total: under a week of focused build once the design lands.

## Claude Design third-pass instructions (paste to Claude Design)

```
SA-Live-Transcribe / "Volksmond" — design brief, third pass (V1 additions)

Building on the earlier briefs. Same voice rules (warm, calm, no em or en dashes).
Three capabilities are now in V1: optional local AI summaries, importing an existing
recording to transcribe, and a record-now-transcribe-later mode for slow machines.

1. Setup question (first run, also in Settings)
   - Ask plainly: "Do you want to just transcribe, or also summarise on your machine?"
   - If they choose summaries, show a model picker with two options, each with its download
     size and a recommended amount of system RAM, plus a clear note that the larger model is
     more accurate but needs more RAM. Let them download now or later.
   - Default is transcribe-only. Summaries are opt-in and the model is an optional download.

2. The Summarise action
   - Lives with a finished transcript (after a live session is stopped, or an imported file
     finishes). Disabled until a full transcript exists; show why ("available once the
     transcript is complete").
   - Pro feature: for Free users, show the calm locked affordance from pass two. For Pro users
     without the model downloaded, the button offers to download it first.
   - Result: the summary appears with the transcript, clearly labelled, and is saved alongside it.

3. Import an existing recording
   - On the start / new-session screen, a first-class "Upload a recording to transcribe" entry
     beside starting a live session. Accept audio and video files.
   - Show progress while it transcribes (reuse the live transcript view). When done, Summarise
     becomes available.

4. Record now, transcribe later
   - A start option: "Record only (transcribe later)", for a machine that cannot keep up live.
     Design its recording state (a calm "recording, not transcribing" indicator with elapsed
     time and a level meter), and the handoff: after "Stop recording", a clear "Transcribe this
     recording now" action.

5. Stop controls and the struggling banner
   - With no recording, a single "Stop".
   - With recording on, Stop offers three choices: "Stop transcription only" (keep recording),
     "Stop recording only" (keep transcribing), and "Stop recording and transcription".
   - When the honest "your machine is struggling to keep up" banner is showing, surface "Stop
     transcription, keep recording" prominently, so the user can fall back to recording and
     summarise or transcribe afterwards. Calm, never alarming.

6. Settings additions
   - A summaries section: on or off, which model is installed, a way to download or switch model
     (with size and RAM guidance), and the output language for summaries.

Microcopy to write: the setup question and the model-size/RAM note, the disabled and locked
states of Summarise, the download-the-model prompt, the three stop options, the record-only
suggestion under the struggling banner, and the summary-saved confirmation. Calm, plain, no
pressure, no dashes.
```
