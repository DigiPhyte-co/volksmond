# UI/UX design brief: SA-Live-Transcribe (working name "Volksmond")

Paste the brief below into a design-focused Claude session to work out the full UI and UX. It is self-contained. Update it as the product evolves.

---

You are a senior product designer. Design the complete UI and UX for a desktop application. Deliver information architecture, screen-by-screen layouts at wireframe fidelity (every state), a component inventory, a visual direction (colour, type, spacing, iconography), and microcopy for the key moments. Work in South African English.

## What the product is
A desktop app that produces a live, on-screen transcript of any meeting happening on the user's computer, including meetings they join as a guest and cannot record through the meeting platform. It captures the computer's own audio (the other participants) plus the user's microphone and transcribes on the device in real time. It is built for South African Afrikaans and mixed Afrikaans/English (code-switched) speech, which mainstream tools handle poorly.

The defining feature is privacy. Everything runs on the user's machine. No audio and no transcript ever leaves the device. No cloud, no third-party servers. This is the core promise and the main reason to choose it over Otter, Fireflies, or Read AI.

Working name: "Volksmond" (Afrikaans for the everyday way people actually speak). Treat the name as provisional.

## Who uses it (two audiences, one app)
1. A consulting professional who joins client meetings as a guest and needs accurate, private notes. Comfortable with software. Wants speed and control.
2. A non-technical professional who needs a simple, trustworthy, private tool. Example: a counsellor recording sessions, where confidentiality is paramount and a cloud tool would be unacceptable. Needs a calm, reassuring, near-zero-learning-curve experience.

Design so the simple user is never overwhelmed and the power user is never blocked. Prefer a clean default with optional advanced controls over two separate modes, unless you can argue a simple/advanced toggle is genuinely better.

## Platform and technical constraints (design within these)
- Windows first, macOS later. Desktop only, not mobile. Account for window resizing.
- Delivered as a local web UI: a small bundled server renders a single-page app shown either in the user's browser or in a native app window. Your design must be implementable in plain HTML, CSS, and JavaScript. You may recommend a lightweight approach, but do not assume a heavy framework.
- Fully offline. No external fonts, analytics, or runtime assets that need the internet.
- One model download on first run (about 1.5 GB) and one model load each session (several seconds). These waits must feel intentional, never broken.

## Screens and states to design (cover all of them)
1. First run and setup: welcome, a one-line explanation of what it does and the privacy promise, choose the microphone and the system-audio source, and the one-time model download with clear progress and an estimate.
2. Pre-meeting start: optional meeting title, language (Afrikaans, English, auto-detect), optional context terms to improve accuracy (names, jargon), a "Record audio" toggle that is off by default with a short consent reminder when switched on, a hardware and quality indicator (fast or modest machine, expected delay), an optional "pull attendee names from my calendar", and one obvious primary action to begin.
3. Live transcript (the main screen): a rolling, auto-scrolling transcript with timestamps and speaker labels (the user versus other participants, named speakers later); a way to add a term mid-meeting without stopping, because users often start recording the instant a meeting begins and cannot prepare context first; clear live status (listening, which quality level is active, and an honest, calm signal if the machine is falling behind, since the app may automatically switch to a faster, slightly less accurate setting to keep up and may mark a gap where it could not); microphone and system-audio level meters; elapsed time; pause and stop.
4. Finish and save: where the transcript was saved with a clear path and an "open folder" action, and an option to produce a slower, higher-accuracy clean version with named speakers (a second pass on recorded audio, only if audio was recorded).
5. History: a searchable list of past sessions showing the live version and any clean version, opening to read.
6. Settings: devices, default language, quality level, save location, and data retention (audio is not kept by default, state this plainly). A clearly separated, off-by-default, plainly labelled option to use an online model on weak machines, with an explicit warning that it sends audio off the device. Keep it visually and conceptually separate from the private default so nobody enables it by accident.
7. Every state: empty, first-run, model downloading, model loading, listening, paused, falling-behind, recording-on, error (no microphone, no system audio, model failed), offline, and stopped/saved.

## Specific UX problems to solve well
- The first-run download and the per-session model load must show progress and reassure, never look hung.
- Privacy must be felt, not just stated. The non-technical user should immediately understand their audio stays on their machine.
- Starting must be near-instant with good defaults, because users hit record as the meeting begins. Context terms can be added during the meeting.
- Be honest about quality. The live transcript is a fast preview that can miss parts under load. The clean second pass is the accurate record. Set expectations without undermining confidence.
- Communicate automatic quality adjustments and any gaps in a low-anxiety way that does not alarm a non-technical user.
- Recording consent: when recording is on, offer a short, friendly, copy-ready line the user can say to the room as a courtesy disclosure that the meeting is being transcribed locally for notes.
- Provide a clear, obvious way to stop and quit the app.

## Tone, voice, and copy
- South African English throughout (colour, organise, programme).
- Warm, calm, plain, trustworthy. Not flashy, not techy, not salesy. Privacy-forward without fear-mongering.
- Do not use em dashes anywhere. Use commas, full stops, parentheses, or colons. Avoid en dashes too. Avoid generic AI phrasing and exclamation-heavy openers.
- Write real microcopy for: the privacy promise, the first-run download wait, the consent reminder and the spoken disclosure line, the "falling behind, switched to keep up" notice, the empty and error states, and the save confirmation.

## What to deliver
1. A short product and audience summary in your own words, to confirm understanding.
2. Information architecture and a primary user flow (first run, then a typical meeting start to finish).
3. Screen-by-screen layouts at wireframe fidelity, including every state above, described clearly enough to build from.
4. A component inventory (buttons, toggles, status chips, meters, transcript line, term input, progress, dialogs).
5. A visual design direction: colour palette, typography, spacing scale, iconography, and overall feel, suited to a calm, private utility that also works as a future commercial product.
6. The microcopy listed above.
7. Accessibility notes: readable transcript by default, adjustable text size, strong contrast, full keyboard control, clear focus states.
8. Notes on how the design scales from a personal tool to a sellable product.

Ask me up to five clarifying questions before you begin if anything material is unclear. Otherwise proceed and state your assumptions.
