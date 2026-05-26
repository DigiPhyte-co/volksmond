# Volksmond v1 scope (canonical)

Status: re-locked with Sean 2026-05-25 after a context-compaction loss. This is the
single source of truth for what v1 is. If anything here conflicts with an older note,
this wins. No em or en dashes anywhere in shipped copy.

## What ships now: the offline-only edition

There are TWO planned editions from one codebase:

1. **Offline-only edition (shipping now).** Never phones home under any circumstances.
   No cloud, no API, no telemetry, no update check. This is the flagship and what the
   privacy promise rests on (Sean's wife does counselling; her transcripts must never
   leave the machine). It carries a simple link to a download page for the connected
   edition.
2. **Connected / paid edition (later, not built).** Never phones home UNLESS the user
   connects their own API or enables an online feature. May do a periodic licence check
   (separate concern). This edition is gated/paid.

Because we ship the offline-only edition now, **everything that references talking to
the outside world is "coming soon", not live**: cloud ASR/summary fallback, bring-your-own
API key, Outlook calendar integration, any Pro-connect upgrade flow. In the offline build
these must read as TBC / coming soon, never as working features. (Cleanup pass pending,
batched with Sean's UI walkthrough.)

## The Pro principle

Anything that talks to the outside world is paid. That is the only line. So far that
means: Outlook integration, connecting an external API, and future online features not
yet thought of. Everything that runs on-device is free, including local AI summaries.

## Models (exact)

### Transcription (ASR)
`faster-whisper` on ctranslate2 4.7.2, running **OpenAI Whisper large-v3**, tier
auto-selected by hardware:

| Tier | Model | Compute |
|---|---|---|
| gpu | large-v3 | float16 |
| gpu-4gb | large-v3 | int8_float16 |
| cpu-strong | large-v3-turbo | int8 |
| cpu-mid | medium | int8 |
| cpu | small | int8 |
| cpu-min | base | int8 |

Plus an adaptive CPU ladder (medium to small to base to tiny) that steps down only if it
cannot hold real time. Whisper downloads on first transcription. The fine-tuned SA model
(SA-ASR-Model project) replaces large-v3 later, not v1.

### Summaries (LLM)
`llama-cpp-python` running a **Gemma 4** GGUF in-process. Gemma 4 is the locked, only
choice: smaller models are too weak for SA Afrikaans, which is the whole reason llama.cpp
is in the app (ct2 cannot convert Gemma 4's KV-shared layers). Source is the UNGATED
unsloth GGUF mirrors:

| UI option | Model (GGUF) | Repo | Download | RAM |
|---|---|---|---|---|
| Gemma 4 (2 billion) | gemma-4-E2B-it-Q4_K_M | unsloth/gemma-4-E2B-it-GGUF | 3.11 GB | 8 GB |
| Gemma 4 (4 billion) | gemma-4-E4B-it-Q4_K_M | unsloth/gemma-4-E4B-it-GGUF | 4.98 GB | 16 GB |

Not the 26B/31B (too heavy). Map-reduce chunking for long transcripts.

**Summary model picker UX:** two buttons (the 2B or the 4B), pick one, it downloads with a
progress bar, then summaries switch on. No file picker. Optional and opt-in.

## v1 feature set

**Core (free, always on, the privacy spine)**
- Live transcription: WASAPI system-audio loopback + mic, streaming Whisper, rolling transcript.
- Local-only processing, permanent, never crippled.
- Save to Markdown; save location is the user's choice with a static privacy tip, never judged.
- Optional audio recording, off by default; three-way stop (transcription / recording / both).
- Mic + system-audio device pickers.
- History and reader.
- Default context (names/jargon every meeting) plus separate per-meeting Participants and Jargon.

**The three v1 additions**
- Import an existing recording (audio/video) and transcribe locally.
- Record now, transcribe later (record-only mode for slow machines).
- Local AI summaries (free): Gemma 4 via llama.cpp, Summarise on a finished transcript,
  shaped by the active custom AI instruction, output language EN default / AF.

**Shell, UI, language**
- Vanilla-JS SPA, clinical light theme + dark mode, in a native pywebview window
  (browser mode = dev fallback).
- Full interface i18n EN/AF, separate from transcription language.
- First-run setup including "just transcribe or also summarise?" with the two-button model picker.
- Custom, named, switchable AI instruction governing summaries.
- Privacy-first "Report a bug / request a feature" (mailto or clipboard, never a silent send),
  volksmond@digiphyte.com.

**Privacy and security (hard requirements)**
- Never phones home (offline edition: provably, online code compiled out).
- CSRF token, Host-rebinding defence, CSP on the localhost server.
- Offline Ed25519 licensing seam, fails closed to Free.

**Packaging and go-to-market**
- Windows-first. CPU-only native one-folder build, zipped. Separate GPU build planned
  (see cuda-build-plan.md).
- Source-available, not full open-source.
- "Early access" positioning (no "beta" language). Landing page volksmond.digiphyte.com,
  email-only capture. Downloads via a public GitHub releases repo.
- Tagline "Speak freely / Praat vrylik", shown bilingually always.

## Coming soon in the offline edition (must NOT look live)
Cloud ASR (Groq) and cloud LLM (OpenRouter) fallbacks; bring-your-own API key; Outlook
attendee-seeding; any Pro-connect upgrade flow. Show a link to the connected-edition
download page instead.

## Not in v1 (deferred)
macOS then Linux; in-app diarisation / clean second pass / named speakers (offline CLI
only via retranscribe.py); the meeting-join bot (separate commercial product); live
per-speaker diarisation; Inno Setup installer (zip for now); first-run download progress
for the Whisper model; the fine-tuned SA model; local remembered-participants autocomplete.

## Still open (Sean to decide)
- Pricing model for the connected edition: monthly subscription vs perpetual-per-major.
- The name "Volksmond" for the international market (tagline is locked).
