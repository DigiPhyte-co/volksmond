# Volksmond

**Private, on-device meeting transcription for South African English and Afrikaans.**

Volksmond turns any meeting playing on your Windows machine into a live transcript,
including meetings you join as a guest and cannot record. Everything runs locally: the
audio is processed on your own hardware and never leaves your machine. No account, no
cloud, no third-party processor.

Website and download: **[volksmond.com](https://volksmond.com/)**

## What it does

- **Live transcription** of system audio + microphone (WASAPI loopback), with a rolling
  transcript in a local browser UI.
- **South African speech first.** Ships with the Fluister family of Afrikaans-tuned
  Whisper models (handles real code-switched Afrikaans/English), standard Whisper for
  English, and Swivuriso (beta) for seven further South African languages.
- **Import and re-transcribe** existing recordings, or record now and transcribe later on
  a slower machine.
- **Local AI summaries** and editable meeting notes, saved as Markdown next to the
  transcript.
- **Optional Outlook calendar integration** (Business licences): pre-seed attendees from
  the classic Outlook desktop app, fully offline over COM, or, if you use new Outlook or
  Outlook on the web, via an optional Microsoft 365 sign-in (Microsoft Graph).
- **A provable offline edition** that compiles out every network code path.

## Privacy and POPIA

Audio and transcripts are processed and stored only on your machine. Because no third
party ever receives the audio, the POPIA third-party-processing concern that applies to
cloud transcription tools does not arise. The source is published precisely so you can
verify this yourself.

## Licensing

Volksmond is **free for personal use**. Commercial and team use needs a per-person
Business licence: see [volksmond.com/business](https://volksmond.com/business) for
current pricing. There is no phone-home enforcement; business use runs on the honour
system, stated plainly in the app.

The source is available under the **PolyForm Noncommercial License 1.0.0** with a
separate commercial licence via Business keys. See [LICENSE.md](LICENSE.md).

## Download and verify

- Installer: [volksmond.com](https://volksmond.com/)
- Verification (SHA-256, scan record, what the installer contains):
  [volksmond.com/trust](https://volksmond.com/trust)

The installer is currently unsigned; the trust page exists so cautious users can check
the hash and scan results against this repository before running it.

## Running from source

Windows 10/11, Python 3.11+, and (optionally) an NVIDIA GPU for the larger models.

```powershell
.\setup.ps1              # one-time: venv + dependencies (see SETUP.md for detail)
.\start-meeting-ui.ps1   # opens the local UI at http://127.0.0.1:8765
```

`requirements.txt` lists the Python dependencies; `build-app.ps1` builds the packaged
installer (PyInstaller). Models are downloaded on first use and cached locally.

## Support

Questions, bug reports, licence queries: **volksmond@digiphyte.com** (or open a GitHub
issue).

---

Volksmond is Afrikaans for "the vernacular", the way people actually speak. Built in
Pretoria by [DigiPhyte (Pty) Ltd](https://digiphyte.com/).
