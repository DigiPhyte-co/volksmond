# SA-Live-Transcribe

**A local desktop tool that produces a live transcript of any meeting on Sean's machine, including ones he joins as a guest and cannot record.**

Standalone project. Created 2026-05-20.

---

## 1. The problem this project solves

In Sean's consulting work, he frequently joins client meetings hosted on the client's own tenant (their Teams, Zoom, or Meet). In that scenario:

- He cannot start a recording (he isn't the host).
- He cannot request the host's recording afterwards (the client controls it).
- He still needs accurate notes, particularly for Afrikaans and mixed Afrikaans/English meetings where his manual note-taking misses nuance.

Existing tools that solve this in English (Otter, Fireflies, Granola) all fall over on SA Afrikaans and code-switched audio. Cloud-based meeting transcription tools are also POPIA-questionable: they ship the audio to a third-party cloud, often US-hosted, which means processing client conversational data outside SA without a clear legal basis.

The solution is a local desktop tool that captures system audio on Sean's machine, runs ASR entirely on his RTX 3090 (no cloud, no third-party processor), and produces a rolling transcript on screen plus an optional saved transcript at the end.

## 2. What this project will produce

A desktop application (Windows-first, possibly cross-platform later) with the following capabilities:

**V0 (minimum viable, ~1 weekend):**
- Headless CLI that captures Windows system audio (WASAPI loopback) and Sean's mic, mixes them, runs through a streaming Whisper backend, prints transcribed chunks to stdout in real time.
- Run via PowerShell, output to terminal. Press Ctrl-C to stop.

**V1 (next, ~1 week):**
- Simple GUI window showing a rolling transcript with ~2-3 second latency.
- "Pause" and "Stop" buttons.
- "Save transcript" button writes the transcript to a Markdown file in a configurable folder.
- Optional toggle: also save the audio (off by default for POPIA reasons; see §4).

**V2 (later):**
- Post-session re-transcribe option: if audio was saved, re-run the saved audio through the SA-ASR-Model (the sibling project's fine-tuned model) for a canonical high-accuracy transcript.
- Simple speaker labels via a "tag who's speaking now" hotkey (manual, not automatic, diarisation in live mode is much harder than offline).
- Configurable initial_prompt per session (seed with client name, industry terms, attendee names).

**Deferred:**
- Automatic speaker diarisation in live mode. Pyannote runs offline; live diarisation needs a different architecture.
- Calendar integration ("automatically start recording when a meeting begins").
- Cross-platform support (Mac, Linux). Windows-first because that's Sean's primary machine.

## 3. Technical approach (picked, not menu)

**Audio capture:** WASAPI loopback via `sounddevice` (Python) on Windows. Captures whatever's playing through Sean's speakers/headphones, including all remote meeting participants. Mix with mic input. Tradeoff: requires WASAPI to be configured to share audio (default on Windows 10/11).

**Streaming ASR engine:** `faster-whisper` (CTranslate2 backend) with manual chunking, OR `whisper.cpp` via `pywhispercpp` for lower latency. Default pick: `faster-whisper` because the venv is already set up and the model is already cached. Switch to `whisper.cpp` if latency feels too high.

**Model:** `large-v3` for accuracy. The 3090 can handle real-time large-v3 inference comfortably on 10-15 second chunks. If latency becomes an issue, distil to `medium` or `small` for V0; `large-v3` for V2 post-session.

**UI framework:** For V0, none (CLI). For V1, `tkinter` (stdlib, no install) or `customtkinter` (better-looking). For V2, possibly upgrade to Tauri or Electron if a richer UX is needed, but only if forced by feature scope.

**Persistence:** Markdown file per session. Default file location: `Cowork/SA-Live-Transcribe/sessions/<YYYY-MM-DD>-<topic>.md`. Audio not stored by default.

**Why local-only, never cloud:** POPIA defensibility (see §4), full control over data, no per-minute pricing, no internet dependency.

## 4. POPIA and consent

This is the legal pivot point of the project. Get it right.

**Sean's legal position when transcribing a meeting he attends as a guest:**

In SA, recording a conversation you are a party to is generally legal under common law one-party consent (the RICA exemption for participants). However:

- If the audio is processed by a third party (cloud Whisper, OpenAI's API, etc.), that's *sharing* the audio with that third party, which under POPIA likely requires consent from the other participants.
- If the model runs entirely locally and no third party ever sees the audio, the third-party-processing concern disappears. Sean is processing his own recording with his own tooling on his own hardware. POPIA's "personal/household activity" exemption may apply if used for personal note-taking only.
- Sharing the resulting transcript onward (with team members, in client deliverables, with the BiaD corpus) is a separate POPIA action requiring its own basis.

**Practical operating rules:**

1. **Local processing only.** Never run a client meeting's audio through a cloud API. The tool will refuse to use cloud-hosted ASR even if available.
2. **Default: no audio retention.** The tool transcribes in-memory chunks and discards the audio. Only the transcript persists. This minimises the data-protection footprint.
3. **Per-session decision to retain audio.** A checkbox or CLI flag in advance of the session if Sean knows he wants the audio for the SA-ASR-Model corpus. Retaining audio requires explicit consent from the client (using the verbal script from `Cowork/SA-ASR-Model/corpus-strategy.md` §3.2).
4. **Transcripts are private notes by default.** They are personal note-taking until Sean decides to share. If they get shared with the client, with a colleague, or fed into the BiaD corpus, that's a new POPIA decision with its own basis (legitimate interest, consent, contract, etc.).
5. **Disclosure (light) for client meetings.** Even though Sean's tool is local, it's good practice (and risk-reducing) to mention at the meeting start: "I'm using local AI tooling on my laptop to help me take notes. The audio doesn't leave my machine. Just letting you know." This isn't a consent request, it's a courtesy disclosure. Lowers the risk of a later "why didn't you tell me you were AI-transcribing the call" complaint, which has reputational impact even if legally fine.

## 5. Action items

**V0 build (target: this weekend or one focused afternoon):**
- [ ] Validate WASAPI loopback works on Sean's Windows 11 setup with `sounddevice`. Quick smoke test, record 10 seconds of system audio, confirm Teams audio is captured.
- [ ] Validate `faster-whisper` streaming pattern: read 10-second audio chunks from the mic+system mix, transcribe each, print to stdout, append to a running transcript string.
- [ ] CLI scaffolding: `live-transcribe.py --output session.md --language af --prompt "names, terms"`.
- [ ] Test on a self-meeting: open Teams, talk to self in Afrikaans + English for 5 minutes, check transcript quality and latency.

**V1 (next):**
- [ ] Add tkinter rolling-transcript window.
- [ ] Save-transcript button.
- [ ] Per-session prompt UI (text field for entity context).

**V2:**
- [ ] "Tag speaker" hotkey + UI.
- [ ] Post-session re-transcribe via the SA-ASR-Model pipeline.
- [ ] Calendar integration via Outlook (optional; only if friction in launching the tool becomes the bottleneck).

**Standing:**
- [ ] Add the courtesy disclosure (§4 rule 5) to Sean's pre-meeting checklist.

**V1 additions (promoted from backlog 2026-05-22, full spec in [docs/v1-plan-summary-and-import.md](docs/v1-plan-summary-and-import.md)):**
- [ ] **Import and transcribe an existing recording** (file upload, reuses PyAV decode + the live engine). ~1 to 2 days.
- [ ] **Record now, transcribe later** (record-only mode for slow machines, then transcribe the saved audio; reuses `--keep-audio` + the import path). ~1 day.
- [ ] **Optional local AI summary** (ctranslate2 + a downloadable Gemma text model, Pro feature, map-reduce chunking for long transcripts, small or larger model choice with RAM guidance). ~1.5 to 3 days.

## 6. Success criteria

Sean uses this tool in at least one real client meeting per week within 4 weeks of V0 shipping, and the resulting transcript captures enough of the substance that he no longer takes manual notes. That's the bar. Anything beyond that is bonus.

## 7. Setup and running V0

V0 is implemented. See [SETUP.md](./SETUP.md) for one-time install (CUDA/cuDNN for GPU, Python venv, model pre-download).

**Day-to-day use, pick CLI or browser UI:**

```powershell
# Browser UI (Recommended), opens http://127.0.0.1:8765 with controls
.\start-meeting-ui.ps1

# CLI, defaults to GPU auto-detect, output to sessions/
.\start-meeting.ps1 -Topic "Acme discovery"

# Force CPU on the PC to validate the laptop path
.\start-meeting.ps1 -Topic "smoketest" -Tier cpu-strong

# Seed entity context, names, jargon, client terms
.\start-meeting.ps1 -Topic "Acme Q2" -Prompt "Acme Corp, Thabo, EBITDA, logistics"
```

Ctrl+C stops the session and flushes the Markdown file to `sessions/`. The browser UI streams segments live and shows the saved file path on stop.

**Tiers:**

| Tier | Hardware | Model | Expected lag |
|---|---|---|---|
| `gpu` (default on PC) | RTX 3090 | `large-v3` fp16 | ≤ 9s |
| `cpu-strong` (default on laptop, 32 GB) | i7-9750H+, 32 GB | `large-v3-turbo` int8 | 20-30s |
| `cpu-mid` (16 GB floor) | recent i5/i7, 16 GB | `medium` int8 | 20-30s |

The plan that drove V0 is at [`~/.claude/plans/1-i-need-a-pure-planet.md`](../../../.claude/plans/1-i-need-a-pure-planet.md).

## 8. Out of scope

- **Meeting bot that joins Teams/Zoom directly.** This is a desktop-side tool that listens to whatever audio plays on the machine. It does not join meetings as a participant. That's a much harder architecture (cross-platform OAuth, bot management, scaling) and not needed for the primary use case.
- **Commercial product (Volksmond).** The downstream commercial product idea was discussed 2026-05-20. Leading name: **Volksmond** (Afrikaans for "vernacular / the common parlance", it transcribes how people actually speak, not textbook Afrikaans; chosen over the earlier "Notule" candidate on 2026-05-21). This project is the internal tool, not the commercial product. If V2 stabilises and Sean wants to commercialise, that's a separate engagement with its own product spec, pricing, deployment, and support.
- **Cross-platform.** Windows-first. Mac and Linux only if there's demand from a second user.

---

## Related files and projects

- `Cowork/SA-ASR-Model/`, sibling project. The fine-tuned model. SA-Live-Transcribe will switch to this model when it ships.
- `BiaD/Clients/physio-pretoria/transcribe-whisperx.py`, the offline reference implementation. Live-transcribe's V0 can borrow heavily from this.
- `BiaD/CLAUDE.md`, BiaD context, referenced for the workspace conventions.
