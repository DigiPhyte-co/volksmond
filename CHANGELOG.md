# Changelog, SA-Live-Transcribe

## 2026-07-23, v1.11.2: a quiet microphone no longer starves the engine

`licensing.APP_VERSION` 1.11.1 -> 1.11.2.

- **Live mic auto-gain, the way Meet and Teams do it.** The microphone path now runs WebRTC automatic gain control, so a quiet mic (a real Samson C01U delivered speech at -34 dBFS even with Windows at 95%) reaches the engine at a healthy level instead of feeding it near-silence, which is exactly the audio Whisper hallucinates and false-vetoes on. AGC applies in BOTH echo-cancellation states (with AEC on it runs inside the same processor, Meet's configuration; with AEC off the mic routes through a dedicated AGC-only pass instead of raw), and mic-only sessions (no system audio captured) now engage the processor purely for AGC. System audio never gets AGC (it is a digital signal at a known level, and gain-riding music would audibly pump). New "Auto mic volume" toggle under the pre-meeting Advanced panel, ON by default, persisted as `agc_live`; the live level meter now shows the processed mic, so the boost is visible. Fail-open: if the gain controller cannot start or errors mid-stream, capture continues exactly as before with a log line. Validated on the real quiet-mic meeting (offline replay, control vs AGC through the live pipeline): active speech lifted ~6 dB into the healthy range, false echo-vetoes of real speech halved (16 -> 7), a real utterance recovered that the control lost entirely, silence handling and force-cut rate unchanged, and a real-bleed recording still vetoes echo correctly. (`aec_live.py`, `capture_core.py`, `capture_win.py`, `capture_mac.py`, `config.py`, `web/app.py`, `web/static/app.js`, `web/static/i18n.js`.)
- **Quiet uploaded recordings are boosted automatically before transcription.** When a channel of an uploaded or re-transcribed file carries very quiet speech (below -30 dBFS), Volksmond now normalises it toward -20 dBFS with gentle compression before the engine hears it, and tells you it did ("Quiet audio boosted for transcription"). Validated on a real recording where the quiet microphone side produced a fifteen-line hallucination loop unboosted and transcribes cleanly boosted. Healthy audio passes through untouched, byte for byte, and your source file on disk is never modified. (`audioboost.py` new, `web/app.py`, `web/static/app.js`, `web/static/i18n.js`.)
- **Robustness fixes from the independent review of the above**: an audio-processor error mid-meeting can no longer silently stop transcription while the session looks alive (the processing worker now survives any failure and falls back honestly, with the echo-cancel toggle reporting its real availability); the "Auto mic volume" choice rides the start request so a last-second toggle always applies; macOS sessions that gain system-audio permission mid-session no longer discard that audio (mac port groundwork); the boost's compressor attack is now truly instant at bursts; and boost logging reports the measured landing level rather than the target. Plus a regression test locking device-switch recording-clock continuity.

Verified: 177-test suite green (script-run and pytest), independent adversarial review of the combined release (2 P1 + 4 P2 findings, 5 fixed, 1 consciously deferred with its follow-up designed), real-device AGC smoke (+17.8 dB on the mic meter, AEC toggle clean both ways, mic-only session honest), offline replay of a real quiet-mic meeting through the AGC'd live pipeline (loop-free, false vetoes halved, force-cut rate unchanged), and the auto-boost end-to-end on the real recording through the actual transcribe endpoint.

## 2026-07-23, v1.11.1: recordings stay in sync, and uploads stop cutting words in half

`licensing.APP_VERSION` 1.11.0 -> 1.11.1.

- **Saved recordings no longer drift out of sync when the call is quiet at the start.** Windows delivers no system-audio frames while nothing is playing, and the recorder used to stitch the microphone and system channels together by raw position, so pressing Begin before a call's audio started shifted the whole system side earlier in the saved file (one real meeting ended up 20 seconds out, and the flaw goes back to at least v1.7.0). The recorder now places every piece of audio at its true wall-clock position and fills quiet periods with silence, so both channels always line up, whether the gap is at the start, in the middle, or at the end. Live transcription was never affected, only the saved audio file. (`sinks.py`.)
- **Uploaded recordings are chunked at natural pauses, not on a blind timer.** File uploads used to be sliced every 8 seconds regardless of who was mid-word, which garbled speech at every seam; live meetings already cut at silences. Uploads (including stereo interview mode and re-transcribed recordings) now use the same silence-aware boundaries as live, validated on a real 33-minute meeting recording: mid-word seam damage gone, no content lost, no speed cost. (`capture_core.py`, `web/app.py`.)

Verified: 155-test suite green (script-run and pytest), independent adversarial review of both fixes (all findings addressed, test assertions mutation-checked), the recorder fix proven on a real-device repro (audio played 35 s into a silent-start session lands at 35.2 s in the file, previously at 0), and the chunking fix proven on a real meeting upload (81% of chunk boundaries were locked to the 8 s grid before, 18% after, with word recovery at the seams and word count within 0.5%). notes beside the transcript, browse while recording, stereo interview uploads, a live echo-cancel toggle, and more languages

`licensing.APP_VERSION` 1.10.0 -> 1.11.0.

- **Notes now live beside the transcript, not under it.** On the live screen your notes are a full-height right-hand column with a draggable divider (drag to resize, double-click to reset); the width is remembered across restarts. The column collapses to a slim rail when you want the transcript full-width, and on narrow windows the layout falls back to the old stacked arrangement. Autosave behaviour is unchanged, and typing focus survives a resize. (`web/static/app.js`, `web/static/styles.css`, `config.py`, `web/app.py`.)
- **You can leave the live screen without stopping the meeting.** The sidebar (Meeting / History / Settings) is now available during a live session, so you can look up a past transcript mid-meeting; every other screen shows a pulsing "Return to meeting" pill with the elapsed time, and the Meeting tab takes you back to the running session instead of a new form. Recording never depended on the page (capture is server-side), and a mid-meeting reload now restores the full on-screen transcript AND your notes from disk instead of showing only new lines. (`web/static/app.js`, `web/static/i18n.js`.)
- **Stereo interview recordings transcribe as two speakers.** A new "Stereo interview mode" option on the upload screen (Advanced) is for phone recordings where the speakers sit in the left and right channels, like Samsung's Interview mode: each channel is transcribed separately and merged by time as Speaker L / Speaker R, with cross-channel gating that removes the directional bleed such recordings carry. Mono files fall back to the normal single-track path with a notice. (`web/app.py`, `sinks.py`, `web/static/app.js`, `web/static/i18n.js`.)
- **Echo cancellation is now toggleable DURING a live meeting.** The live audio strip gains the "Cancel echo live" switch; flipping it takes effect immediately (the canceller stays warmed in the background so switching on mid-meeting works instantly). The switch always shows the engine's real state, and a flip also becomes your new default, which fixes a subtle bug where the pre-meeting toggle could show a value that was no longer what was stored on disk. (`capture_core.py`, `aec_live.py`, `web/app.py`, `web/static/app.js`, `web/static/i18n.js`.)
- **Pick the exact meeting language, and set any mode as your default.** The pre-meeting Language control becomes Afrikaans / English / More languages / Auto-detect: More languages opens one grouped list with the South African languages (isiZulu, isiXhosa, Sesotho, Setswana, Xitsonga and more, via Swivuriso) followed by 18 major world languages on standard Whisper, and naming the language up front avoids mid-meeting language flapping. Every choice, including a specific language and Auto-detect, can now be the default in Settings, which previously only offered individual languages. Also fixed: choosing the South African languages mode with the engine forced to Whisper no longer misreads the group code as Sanskrit. (`transcribe.py`, `web/app.py`, `web/static/app.js`, `web/static/i18n.js`.)
- **Transcription-quality investigation concluded.** The "transcripts get more garbled late in long Afrikaans meetings" report was root-caused with A/B experiments on real meeting audio: the garbling was uncancelled speaker bleed reaching the microphone while live echo cancellation was off, not the chunking; the guard stack shipped in v1.8.x had already fixed the older runaway-repetition failure. Consequently this release changes no chunking parameters; the fix is echo cancellation on (the default) plus the new in-meeting toggle above. (No pipeline changes.)

Verified: 137-test suite green (script-run and pytest), py_compile + node --check clean, an independent adversarial code review over the whole wave (7 findings, all fixed), a live in-browser pass over the new layout, navigation, language controls and settings, a real-device AEC toggle session (Samson C01U + Realtek loopback, engage/bypass confirmed within 10 ms both ways), and the stereo mode validated end-to-end on a real 43-minute 2-channel phone interview recording (attributed two-speaker transcript, ghost lines gated out, mono fallback exercised). Afrikaans licence + honour system, editable notes everywhere, local-Outlook calendar seeding, honest business copy, provable offline-only build

`licensing.APP_VERSION` 1.9.0 -> 1.10.0.

- **The first-run licence screen is translated, and states the honour system out loud.** The whole "I agree and continue" gate was hardcoded English, the one screen the language toggle never reached; every line now translates to Afrikaans. A new card says plainly what the enforcement model actually is: Volksmond never phones home, there is no account, no activation server, and no way for us to see that you installed it or how you use it, so business use runs on trust, and that trust is what keeps the personal tier free and the Afrikaans models open. The locked posture (monetisation plan section 3) said to the user, not just to ourselves. (`web/static/app.js`, `web/static/i18n.js`.)
- **Your own meeting notes are first-class in the reader, and editable after the meeting.** The past-meeting reader is now a consistent three-way switch, Transcript / Summary / My notes. The My notes tab is a live editor (autosaves to the same `<stem>-notes.md` sidecar), so you can add or fix notes AFTER a call, when there was too much going on to write them during, then run or update the summary with those notes folded in, without leaving the page. The finish screen gains an "Open my notes" button when you typed any; the History list gains a "Notes" pill beside Recorded / Transcript / Summary. Notes translate to Afrikaans too (the v1.9.0 notes strings were English-only). (`web/static/app.js`, `web/static/i18n.js`.)
- **Calendar attendee seeding, from the LOCAL Outlook desktop, fully offline.** A "Pull from Outlook calendar" button on the live pre-meeting screen (Business licences only) reads the current or next meeting's subject and attendee names from the classic Outlook app on this machine over COM, and drops the names into Participants, with no network call. New `live_transcribe/outlook_local.py` (the offline twin of the Graph-based `outlook.py`), a Business-gated `POST /api/calendar-seed` (the server verifies the licence independently, so a spoofed UI still gets 402), and the UI. pywin32 added to requirements + the build spec. NOTE: the live COM read is now verified on the real machine (classic Outlook, a 997-item calendar): it reads the next meeting's subject, attendees and start correctly. It works only with the classic Outlook desktop app, not the new Outlook, and degrades cleanly elsewhere (a plain "not available" message). The real-machine test caught and fixed a timezone bug: pywin32 returns `AppointmentItem.Start` with the local wall-clock components but a bogus `+00:00` tzinfo, so the old `.timestamp()` path shifted every meeting by the UTC offset (09:00 SAST read as 11:00); the code now reads the calendar fields directly. The `Restrict` date query also now leads with unambiguous ISO (`%Y-%m-%d`), because the en-ZA `d/m/Y` format silently matched the wrong window. Still wants pywin32 bundled into the frozen build + an eyeball of the in-app button and reminder in WebView2. (`outlook_local.py`, `web/app.py`, `web/static/app.js`, `web/static/i18n.js`, `requirements.txt`, `sa-live-transcribe.spec`.)
- **A "start transcribing?" reminder when an Outlook meeting begins.** While Volksmond is open, it quietly polls the LOCAL Outlook calendar (the same offline COM read, once a minute) and, when a meeting is starting (from 2 minutes before to 15 minutes after its start time), floats a gentle banner offering to start, pre-seeded with the meeting's title and attendees. It NEVER auto-starts: accepting drops you on the ready pre-meeting screen and you press Begin, so the courtesy posture holds. Business-gated, on by default with a Settings toggle, nudges once per meeting, and is inert without a licence + Outlook + pywin32. New `GET /api/calendar-upcoming` and a `calendar_reminders` setting; it lives inside the same offline-build compile-out as the rest of the calendar feature. This replaces the audio/process-based call-detection idea, which was rejected because Sean runs Wispr Flow, a dictation app that holds the mic open all the time, so a "mic in use" signal would false-fire constantly; a calendar-only reminder is simpler and cannot misfire. (`web/app.py`, `config.py`, `outlook_local.py`, `web/static/app.js`, `web/static/i18n.js`.)
- **The upgrade screen no longer advertises features the app does not have.** Removed the "optional online transcription" and "optional online summary" bullets: neither was ever wired (the stored cloud key is an inert seam), and cloud ASR would break the local-only POPIA promise anyway. Record-and-transcribe-later covers a weak machine; a saved summary can go through your own local model. Added a clearly-labelled "coming soon" note for a future OPTIONAL online tier running our most accurate South African models on DigiPhyte's own hardware in South Africa (audio leaves the machine but stays in-country on hardware we control, POPIA-friendly, the Service Bear engine); the free local tier is never replaced. The calendar bullet now says "Outlook calendar" and is real. (`web/static/app.js`, `web/static/i18n.js`.)
- **Afrikaans now recommends High quality, not Best.** For the Fluister (Afrikaans) models the "Recommended" badge moved from Best (large-v3) to High quality (large-v3-turbo): our turbo tune is the Afrikaans sweet spot (it beats large-v3 on real Afrikaans, and is GPU-optional and faster). Display-only and scoped to the Afrikaans section; the hardware-based recommendation still drives the general Whisper section and the first-run auto-download. (`web/static/app.js`.)
- **The provable offline-only build is real, and the default build no longer phones home behind the privacy promise.** The locked two-build plan (distribution plan section 3) is built. `VOLKSMOND_OFFLINE=1` produces a second PyInstaller target, `Volksmond-Offline`, that compiles OUT every network path: the app update check (moved into its own `updatecheck.py`), the model-update check, and the Outlook calendar (`outlook.py` + the local `outlook_local.py`) with its cloud auth lib (`msal`). In that edition the routes are never registered and the modules are physically absent from the bundle, and a runtime hook (`pyi_rth_offline.py`) hard-forces the app onto the offline path (`buildflags.OFFLINE_ONLY`), so it cannot be talked back online by an env var. Interim and independently, the DEFAULT build now gates the "Check for updates" button AND the `/api/check-updates` route behind `connected()`, so a normal install cannot reach the internet for an app update either (only the future connected edition can); the model-update check and calendar stay in the default build. The quick-start privacy line (EN + AF) is softened from the false "the offline edition has no way to reach the internet" to an accurate "the only time it reaches the internet is when you ask it to, to download or update a speech model", until the offline exe actually ships. New `buildflags.py`, `updatecheck.py`, `pyi_rth_offline.py`; `build-app.ps1` now builds both editions. (`web/app.py`, `web/static/app.js`, `sa-live-transcribe.spec`, `build-app.ps1`, `docs/volksmond-quick-start.html`, `docs/volksmond-quick-start-af.html`, `docs/distribution-and-landing-plan.md`.)

Verified: node --check + py_compile clean; the full web-API test script passes (33 real sessions unaffected, the new `/api/calendar-seed` gate does not disturb the suite); and a browser pass on the running app confirmed the licence gate in both languages (heading "Dit werk op die eresisteem."), the reader's three tabs with an editable notes tab, the finish "Open my notes" button, the History "Notes" pill, the reworked upgrade view (both online bullets gone, coming-soon card present), and the calendar button gated behind a Business licence with the server independently returning 402 when the UI is spoofed. The calendar reminder was verified with a stubbed poll: it sets the nudge only inside the start window (a meeting 3 hours out does not fire), the banner floats over any screen, accepting pre-seeds the title + attendees and drops onto the pre-meeting screen without auto-starting, a handled meeting does not re-nudge, and both `/api/calendar-upcoming` and the settings toggle are Business-gated (the endpoint returns 402 even when the UI is spoofed pro). `outlook_local` degrades cleanly when pywin32 is absent. The offline gating was verified by importing the app under each build profile: the offline profile registers no `/api/check-updates`, `/api/model-updates` or `/api/calendar-seed` route (all three absent) and reports `offline: true`; the default build's `POST /api/check-updates` returns 404 before any network call, while the connected profile still reaches the manifest; the 28-check web-API suite still passes. Still wants: a real-machine test of the live Outlook COM read (pywin32 + classic Outlook), which remains classic-Outlook-only; and a full offline-exe build plus a real-machine socket capture confirming zero outbound sockets (the remaining packaging gate), with a separate installer identity for the offline edition.

## 2026-07-08, v1.9.0: first-run licence, business licensing, live meeting notes, and the recording pill fixed for good

`licensing.APP_VERSION` 1.8.2 -> 1.9.0.

- **First-run licence agreement.** A new user now sees a plain, un-skippable "free for personal use, paid licence for business use" screen before setup, with a prominent TLDR and three short bullets. The choice persists to `settings.json` (`licence_accepted`) and mirrors to localStorage; an existing user sees it once on the next launch. The rest of setup stays skippable, the licence gate does not. (`web/static/app.js`, `config.py`, `web/app.py`.)
- **Business licensing, replacing the R599 one-time Pro.** The upgrade view and the Settings licence card now read as a per-person Business licence for commercial and team use; personal use stays free and is never crippled. No price is baked into the app: it links to volksmond.digiphyte.com/business for current pricing, so a price change never forces a rebuild. Activation by pasted key and the Ed25519 backend are unchanged. (`web/static/app.js`.)
- **One-time business-use nudge.** After ten completed sessions on the free tier, a single dismissable card on the home screen mentions business licensing. Local only (a counter in `settings.json`), never blocks a meeting, never repeats, and is absent once a licence is active. (`config.py`, `web/app.py`, `web/static/app.js`.)
- **Optional "Made with Volksmond" summary footer.** A one-line credit at the end of the summary file only, on by default, one click off in Settings. Never added to the raw transcript, and never to any export shared onward. (`web/app.py`, `web/static/app.js`.)
- **Live meeting notes.** A collapsible notes panel on the live screen lets you type notes as the meeting runs; they autosave to `<stem>-notes.md` beside the transcript and never touch the transcript itself. At summary time you choose whether to fold them in, and when included they are handed to the local summariser as authoritative human input (preferred over the noisy transcript). Notes appear in the reader for a past session and are kept out of the session list. (`web/app.py`, `summarise.py`, `web/static/app.js`, `config.py`.)
- **The recording status pill no longer stacks the dot above its label.** Root cause, finally: the pill reused the class name `live`, which also matches the full-screen `.live` container rule (`flex-direction: column`), so under some rule orderings the container stacked the pill. v1.7.1 and v1.8.2 both fought this with specificity and it kept coming back. The pill modifier is now `rec`, so no chip can share a class with a layout container again. (`web/static/styles.css`, `web/static/app.js`.)

Verified: py_compile + node --check; isolated backend tests (the four licence/nudge/footer settings and the notes settings round-trip through /api/settings; notes write, read, session-list exclusion, path-traversal and CSRF rejection; the summariser folds notes in only when asked; the footer lands on the summary file only, transcript byte-identical); and a browser pass over the licence gate, Settings, the Business upgrade view, the nudge, the live notes panel and its autosave, the include-notes toggle, the reader notes card, and the pill rendering `flex-direction: row` with the dot measured beside the label. Still wants a real-machine eyeball on the frozen build (the pill symptom only showed there).

Note: `_PUBLIC_KEY_HEX` in `licensing.py` is still empty, so this build cannot activate a Business licence until the keygen is done. Everything else, including all local features, works.

## 2026-07-03, v1.8.2: fewer short mic ghosts, accurate live timing, snappier start + review fixes

`licensing.APP_VERSION` 1.8.1 -> 1.8.2.

- **Short one- and two-word mic ghosts ("Thank you", "dankie", "ja") are now caught.** v1.8.0's echo veto exempted every <=2-word segment to protect real backchannels, so short far-end bleed fragments slipped through on a silent mic. Short segments now face the same energy test as long ones: a genuinely-spoken short reply survives (its mic energy sits above the ceiling), while a quiet bleed fragment is dropped. Only a sub-0.5s blip stays auto-exempt. A/B across two real recordings (39 + 24 min) dropped 36 bleed fragments and kept every genuine backchannel. (`transcribe.py` `sys_echo_veto`.)
- **Live transcript timing is accurate again.** Each live chunk was timestamped from the emitted length only, ignoring the carried-over tail, so mic lines landed up to ~2s late and the echo veto's far-end lookup was misaligned against its real-time reference. The timestamp is now derived from the full buffer span, so mic and system lines interleave correctly and the veto reads the right window. (`capture.py` `_chunker` / `_emit`.)
- **Begin no longer freezes the window on a cold start.** The transcription model is pre-warmed outside the state lock, so `/api/status` and `/api/levels` keep responding while a first-time or uncached model loads. (`web/app.py` start.)
- **Cancelling a file transcription stops promptly** instead of grinding through the queued backlog first. (`web/app.py` transcribe-file.)
- **UI:** the status pill no longer stacks its label into two lines on a narrow window (pill labels never wrap), and starting a new meeting no longer pre-fills the previous meeting's name. (`web/static/styles.css`, `web/static/app.js`.)
- **Hardening (from a full-project review):** session filenames reject glob metacharacters (`* ? [ ]`); a frozen build ignores the `SA_LIVE_LICENSE_PUBKEY` env override, so a shipped binary cannot be pointed at another key to self-sign Pro; the update "Download" link is restricted to our own domains + GitHub; the Swivuriso chip icon renders (was an empty SVG). (`web/app.py`, `licensing.py`, `web/static/app.js`.)

Verified: py_compile + node --check, all eight test scripts green (echo-veto tests updated for the new short-segment behaviour), plus targeted checks (filename reject, frozen pubkey, update-link allowlist, `_emit` arity). The live capture changes (timestamp + pre-warm) still want a real-meeting eyeball on this build.

## 2026-07-02, v1.8.1: silence-hallucination guards (loops, room tone, prompt leaks)

`licensing.APP_VERSION` 1.8.0 -> 1.8.1.

- **Fewer invented lines when no one is talking.** Three deterministic backstops for the junk Whisper produces on quiet audio, layered under the echo veto. (1) A pre-transcription **silence gate** skips a chunk whose loudest ~100 ms frame is below the speech floor (room tone / true silence, where the model only hallucinates); it uses the loudest frame, so any real utterance keeps the chunk. (2) A **phrase-loop killer** drops a segment that is mostly one repeated multi-word unit ("as jy gaan as jy gaan as jy gaan ..."), which the single-token collapse missed; it only fires when the loop dominates the segment, so ordinary speech is left alone. (3) The **anchor-prompt-leak** match now catches the "Algemene woorde" list regardless of trailing punctuation (previously only with a colon or comma). Layers under the echo veto (which handles far-end bleed) and complements it for the no-far-end case. Toggle `SA_LIVE_SILENCE_GATE=0`. (`transcribe.py` `_is_silence` + `_is_phrase_loop` + the engine `_run`, `_HALLUCINATION_RE`.)

Verified: py_compile, all eight test scripts, and a new `tests/test_silence_loops.py` (11 cases). A/B on the vleis meeting mic: removed 2 phrase-loops + 1 anchor leak, zero real segments touched.

## 2026-07-02, v1.8.0: cross-channel echo suppression (the far end stops ghosting into your mic)

`licensing.APP_VERSION` 1.7.1 -> 1.8.0.

- **Echoes of the other person no longer show up on your side of the transcript.** Even on headphones a little of the far end leaks into your microphone (about 100 ms behind), and Whisper transcribed that leak as garbled "ghost" lines under your name that shadowed what the other person just said. The end-of-session text de-dup could not catch them, because the leak transcribes to different words. Two energy-based defences now remove it: a per-frame gate on the aligned stereo re-transcribe (silences mic frames sitting far below the concurrent far end, before transcription), and a segment veto in the live engine (drops a mic segment whose audio was well below the far end across the segment, using a real-time far-end energy reference fed from capture). The veto is conservative by design: it only drops segments that are overwhelmingly far end, always keeps short replies, and never fires without a far-end reference, so your own speech is left untouched. Live and re-transcribe both benefit. (`transcribe.py` `SysEnergyRing` + `sys_echo_veto` + `xchan_gate_mic` + the engine veto, `capture.py` per-block SYS energy feed, `web/app.py` live + file + device-switch wiring.)
- **Toggles:** `SA_LIVE_XCHAN_GATE=0` disables the re-transcribe frame gate, `SA_LIVE_XCHAN_VETO=0` disables the segment veto. Both on by default. The existing text de-dup (`strip_mic_echoes`) still runs as the speakers-in-a-room backstop.

Verified: py_compile (transcribe/capture/app), all six existing test scripts, and a new `tests/test_echo_veto.py` (7 cases). A/B on a real 39-minute call: 87 ghost segments dropped, genuine speech byte-identical, the one mixed line correctly kept. Still wants a real-meeting eyeball of the LIVE path on this build.

## 2026-06-30, v1.7.1: fix the GPU/CPU label + Quality readout on the new GPU tiers

`licensing.APP_VERSION` 1.7.0 -> 1.7.1.

- **The GPU/CPU badge and the Quality dropdown now read correctly on the new GPU size tiers.** v1.7.0 added `gpu-turbo`/`gpu-medium`/`gpu-small`, but the header badge only recognised `gpu`/`gpu-4gb`, so a session running on GPU turbo was mislabelled "CPU", and the live Quality dropdown showed "Auto" instead of the running size. Transcription was unaffected (it really was on the GPU); only the labels were wrong. Fixed the badge check (any `gpu*` tier) and the `LEGACY_QUALITY` map. (`web/static/app.js`.)
- **Listening / Saved status pills wrap their text in a span**, so the dot is unambiguously the first flex item (dot to the left, pill sized to content), consistent with the Finishing pill. (`web/static/app.js`.)

Verified: node --check; no Python logic changed, so test_web_api + test_engine_drain are unaffected.

## 2026-06-29, v1.7.0: one clean stereo recording + model selection that respects your pick

`licensing.APP_VERSION` 1.6.4 -> 1.7.0.

- **Recordings are now a single, echo-cancelled stereo file.** A recorded session used to leave three raw files (`-MIC.wav`, `-SYS.wav`, `-MIXED.wav`) with the speaker echo still in the mic, so the recordings were messy and a re-transcribe of them came out garbled. A session now saves one `<stem>.wav`: LEFT = your mic, RIGHT = everyone else, with echo cancellation already applied to the mic (live AEC is now ON by default). The per-source channels are still written during the meeting (crash-safe) and folded into the single stereo file on close, then removed. Tradeoff: baked-in AEC can blur your words during heavy crosstalk, the same trade Zoom and Teams make. (`config.py` aec_live default, `sinks.py` AudioRecorder.`_finalise_recording`, `web/app.py` start endpoint + sessions list.)
- **Re-transcribe splits the stereo recording; legacy recordings still work.** The file engine detects the single stereo recording and transcribes left as MIC and right as SYS, with no echo step (the mic is already clean). Old per-source `-MIC/-SYS` recordings keep the offline-AEC path unchanged. (`web/app.py` transcribe-file.)
- **Model selection now respects exactly what you pick.** Choosing a Quality (Fast / Balanced / High quality / Best) is honoured on the GPU too, not silently overridden to large-v3 as before. "Auto" picks the best model for the chosen language: Afrikaans goes to Fluister turbo (our v2 tune beats large-v3), English goes to Whisper large-v3. The CPU "too slow, stepping down" loop is unchanged. New GPU size tiers and a rewritten `resolve_tier(quality, device, language, engine)`. (`transcribe.py` TIER_CONFIG, `__main__.py`, `web/app.py`, `web/static/app.js`.)

Verified: py_compile, node --check, and test_web_api (with new stereo-fold + model-selection tests) + test_engine_drain all green. The recording format and the model picker still want an on-machine eyeball and a real-meeting validation (live transcript vs a re-transcribe of the recording).

## 2026-06-28, v1.6.4: consistent model cards (sizes, descriptions, Remove for every family)

`licensing.APP_VERSION` 1.6.3 -> 1.6.4.

- **Settings, Transcription model: all three families now look and behave the same.** The Afrikaans (Fluister), other South African languages (Swivuriso) and general Whisper models were three different card styles, and the Afrikaans and South African ones had no size, no description and no Remove. They now share one card (`voiceModelRow`): title, download size, a one-line description, and Installed/Download + Remove, under three clear headers ("Afrikaans model (Fluister)", "Other South African languages (Swivuriso)", "General Whisper models"). The Afrikaans section shows all four sizes (Download for any not installed) and keeps the opt-in "Check for updates"; the Swivuriso card states that only High quality is available, since it is one model. (`web/static/app.js`, `i18n.js`.)
- **You can now remove the Afrikaans and South African models.** `voicedl.delete` accepts a Fluister or Swivuriso repo id (not just a Whisper size) and clears the recorded version; Swivuriso removal also clears the local ct2 build. New `voicedl.start_fluister_download` + `POST /api/voice-model/fluister-download` give a first-install download for a Fluister size straight from the card. (`voicedl.py`, `web/app.py`.)

Verified: py_compile, node --check, test_web_api + test_engine_drain all green. The card layout still wants an on-machine eyeball on the next build.

## 2026-06-28, v1.6.3: South African languages reachable in one tap; "Bantu" removed

`licensing.APP_VERSION` 1.6.2 -> 1.6.3.

- **The South African languages are now in the pre-meeting Language picker.** 1.6.2 routed the seven Swivuriso languages correctly, but the "Start the meeting" screen only offered Afrikaans, English and Auto-detect, so there was no way to choose them there. The picker now reads Afrikaans, English, South African languages, Auto-detect: the seven are collapsed into one "South African languages" option (a "sa" group code that routes to Swivuriso, which auto-detects among the seven). Same on the file-import screen. (`transcribe.py` family_for_language now also accepts the "sa" group code, `web/static/app.js`, `i18n.js`.)
- **"Bantu" removed throughout.** The term carries apartheid-era baggage in South Africa, so every user-facing string, code comment, the manifest, the docs and the published Swivuriso model card now say "South African languages" instead. (`transcribe.py`, `voicedl.py`, `web/app.py`, `web/static/app.js`, `i18n.js`, `site/models.json`, `docs/`, and the HuggingFace card re-uploaded via `push_swivuriso.py`.)

## 2026-06-28, v1.6.2: Swivuriso is hosted, so every machine can get it

`licensing.APP_VERSION` 1.6.1 -> 1.6.2.

- **Swivuriso now downloads on any machine, with a progress bar.** v1.6.1 shipped Swivuriso (the seven South African languages) but only Sean's dev box had it, as a local ct2 build; every other machine silently fell back to standard Whisper for those languages. The converted model is now published at huggingface.co/digiphyte/swivuriso-turbo (public, MIT, credited to DSFSI / African Next Voices, with the OpenAI Whisper Apache-2.0 base preserved in NOTICE), and `transcribe.SWIVURISO_HOSTED` is flipped on, so a machine without the local build resolves to the hosted repo. A **Download** button in Settings -> Transcription model (the Swivuriso card) pulls it down up front with a progress bar instead of faster-whisper fetching it silently at first use; it also still downloads automatically the first time one of the seven languages is picked. New plain-repo download path (`voicedl.start_swivuriso_download`, recording the build baseline version) + `POST /api/voice-model/swivuriso-download` (session-gated) + the Settings card affordance and Afrikaans copy. Still beta. (`transcribe.py`, `voicedl.py`, `web/app.py`, `web/static/app.js`, `i18n.js`.)
- **Publishing script.** `SA-ASR-Model/finetune/push_swivuriso.py` (the Swivuriso twin of `push_fluister.py`) uploads the ct2 build with a credit-first model card, the model's MIT LICENSE, a NOTICE crediting DSFSI / African Next Voices and OpenAI Whisper, and the Apache-2.0 text.

## 2026-06-28, v1.6.1: Swivuriso, South African languages (beta)

`licensing.APP_VERSION` 1.6.0 -> 1.6.1.

- **Seven more South African languages, via Swivuriso (beta).** Selecting isiZulu, isiXhosa, Sesotho, Setswana, Xitsonga, isiNdebele or Tshivenda routes to Swivuriso, one model that covers all seven. It is a third-party model by DSFSI / African Next Voices (MIT), used under its own name and credited in the app rather than branded as ours; we convert it to ctranslate2 for faster-whisper. faster-whisper has no codes for these languages and DSFSI forces none, so Swivuriso runs on auto-detect (a deliberate departure from the force-the-language rule, for this family only). A live language switch into or out of these languages swaps the model automatically. New family routing (`transcribe.family_for_language` / `resolve_model` now return a family of fluister | whisper | swivuriso), a Swivuriso card in Settings -> Transcription model, and the seven languages added to "Languages you transcribe". Marked beta: transcription quality on real SA-language audio is still being verified. (`transcribe.py`, `voicedl.py`, `web/app.py`, `web/static/app.js`, `i18n.js`, `site/models.json`.)
- **Copy:** Fluister is now described as "best for Afrikaans and mixed Afrikaans and English meetings".

## 2026-06-26, v1.6.0: voice model management (manifest + opt-in updates)

`licensing.APP_VERSION` 1.5.0 -> 1.6.0.

- **Your Afrikaans model can update itself, on your say-so.** Fluister is ours, so it keeps improving (a v2 is coming). The app loads models offline (no silent revalidation against HuggingFace), so a better model pushed to the same place would never reach an existing install on its own. New: a published `models.json` manifest (the model twin of the app's `latest.json`) and an "Afrikaans model (Fluister)" panel in Settings -> Transcription model. A **Check for updates** button (manual, never automatic) compares what you have installed to what is published; when a newer version exists it shows "Update available -> v2" with an opt-in **Update** button that downloads it and records the version. Manual-only, never phones home: the check and the update run only when you click. A per-model `access` field leaves room to gate a premium model later without shipping a new app. (`transcribe.py`, `config.py`, `voicedl.py`, `web/app.py`, `web/static/app.js`, `i18n.js`, `site/models.json`.)

## 2026-06-25, v1.5.0: live language/model switching + re-transcribe controls

`licensing.APP_VERSION` 1.4.0 -> 1.5.0.

- **Change the language or model mid-meeting.** A live meeting's Advanced controls gained Language / Engine / Quality switchers via a new `/api/reconfigure`. Changing the language alone KEEPS the loaded model: the fix for the bilingual-garble case, where an Afrikaans meeting that an English speaker joined was forcing English through an Afrikaans-pinned decode. Changing the model swaps it on the worker thread, the same single-threaded discipline as the CPU auto-downgrade. (`transcribe.py`, `web/app.py`, `web/static/app.js`, `i18n.js`.)
- **Re-transcribe got language/engine/quality pickers, and echo cancellation.** The re-transcribe dialog now lets you choose language, engine and quality, exposes the echo-cancellation toggle, and a single-file upload auto-bundles its `-MIC`/`-SYS` siblings so both sides transcribe (and echo can cancel) from one pick. Also fixed: with live AEC on, the saved recording keeps the raw mic, not the cleaned one. (`web/app.py`, `web/static/app.js`, `capture.py`, `i18n.js`.)

## 2026-06-23, v1.4.0: live echo cancellation (opt-in, beta)

- **Cancel speaker echo DURING the meeting (opt-in, off by default).** New `live_transcribe/aec_live.py` runs the same WebRTC APM (Chrome AEC3) on the live capture: the two device streams (mic + system loopback, at different native rates) are streaming-resampled to 16k with `soxr`, fed to a persistent APM in 10ms frames on a dedicated worker thread (far-end via process_reverse_stream, near-end via process_stream), and the cleaned mic flows on to transcription + recording. The streams are decoupled, so if the system goes silent the mic still passes through. Toggle lives in the live meeting's Advanced controls ("Cancel echo live", beta). **Off by default**, same double-talk caveat as the re-transcribe canceller: great when you are mostly listening on speakers, can blur your own words during heavy crosstalk. When live AEC is on, the saved recording is the cleaned mic. Verified end-to-end on a simulated two-device feed (~28 dB cancellation, no dropped audio, exact length); real-hardware listening still wants a live test. `soxr` added to the bundle. (`live_transcribe/aec_live.py`, `capture.py`, `web/app.py`, `config.py`, `web/static/app.js`, `i18n.js`, `sa-live-transcribe.spec`.)

## 2026-06-23, v1.3.0: echo cancellation (opt-in)

- **Cancel speaker echo on a re-transcribe (opt-in, off by default).** New `live_transcribe/aec.py` uses LiveKit's WebRTC APM (Chrome's AEC3, Apache-2.0) to subtract the speaker echo your microphone re-heard from the MIC channel before a re-transcribe, so the other side is not transcribed twice. Measured ~28 dB cancellation on echo-only audio with your own voice preserved. **Off by default**: it cleans echo-only / you-listening audio (a video, a one-sided talk) well, but during sustained double-talk (you and the other side speaking over each other, especially overlapping words) it can blur your own words, so it is a Settings toggle rather than the default for back-and-forth meetings. The far end is the system loopback we already capture; the MIC-vs-SYS delay is auto-estimated (AEC3 also self-aligns). `livekit` + protobuf + aiofiles + the native FFI lib are bundled into the frozen build (verified running in the packaged app). The safe `dedup.strip_mic_echoes` text-level pass remains the default. (`live_transcribe/aec.py`, `web/app.py`, `config.py`, `web/static/app.js`, `i18n.js`, `sa-live-transcribe.spec`.)
  (Live echo cancellation - cleaning the echo during the meeting - followed in v1.4.0, below the next heading up.)

## 2026-06-23, v1.2.0: recording rework + engine override + async summaries

A round centred on giving you control over the recordings and the summary lifecycle. `licensing.APP_VERSION` 1.1.1 -> 1.2.0.

- **Auto-detect uses Fluister.** When you let Volksmond auto-detect the language, the Afrikaans-tuned Fluister model is used (large-v3 on GPU, medium on CPU), the tiers where Fluister keeps English clean. Explicit `en` or other languages still use stock Whisper. (`transcribe.py`.)
- **Engine override.** A new Advanced "Engine" selector (Auto / Fluister / Whisper) lets you force Fluister on an English meeting that has Afrikaans code-switched words, or force standard Whisper on an Afrikaans call. Default Auto follows the language. Falls back honestly to Whisper if Fluister is not installed. (`transcribe.py`, `config.py`, `web/app.py`, `web/static/app.js`, `i18n.js`.)
- **Mixed WAV alongside the channels.** When you record a session, Volksmond still keeps the two channels (`-MIC.wav` + `-SYS.wav`) separately, but on close it also writes `-MIXED.wav`, a single playable file of the whole conversation. The mixed file is for listening back; re-transcribe still feeds the separated channels so the diarisation (you vs the other side) is preserved. (`sinks.py`.)
- **Record-only sessions appear in History.** Previously a session that was recorded but not transcribed yet was invisible from the app (the file list only globbed transcripts). The session list now enumerates by stem, so a record-only session shows up immediately with a "Recorded" chip and a Transcribe button. (`web/app.py`.)
- **History status chips.** Each session row shows three indicators: recorded (mic icon), transcribed (note icon), summarised (sparkle icon), each with an in-progress form (a red pulse for the live recording, a spinner for transcribing or summarising) when that step is happening right now. (`web/static/app.js`, `web/static/styles.css`, `i18n.js`.)
- **Re-transcribe a saved recording, with speaker separation.** Sessions with kept audio get a Re-transcribe action (on the row for record-only sessions, in the reader toolbar otherwise). It feeds both channels through the file engine, time-merges them, tags MIC=you / SYS=the other side, and writes the new transcript at the recording's own stem so the History row gains its transcript rather than spawning a second row. Cleaner than the live pass (no real-time downgrade, full beam). (`web/app.py`, `web/static/app.js`, `i18n.js`.)
- **Summaries now run as background jobs.** `/api/summarise` no longer blocks; it spawns a worker thread and the UI polls `/api/summary-status`. So you can navigate away from the reader while a summary runs, the History list shows "Summarising" on the right row, and the reader shows "in progress" when you open a session whose summary is still being made. One summary at a time (a second request returns 409, same as before). (`web/app.py`, `web/static/app.js`.)
- **History updates while you watch.** While on the History screen, the session list refreshes every 2.5s so the indicators move from "Transcribing" to "Transcript + Summarising" to "+ Summary" without a manual refresh. The refresh is silent (no re-render unless something actually changed), so the search box does not lose focus. (`web/static/app.js`.)

## 2026-06-22, v1.1.1: first-run language step + testing-feedback polish

Round of fixes from testing the v1.1.0 build (`licensing.APP_VERSION` 1.1.0 -> 1.1.1).

- **First-run asks your languages.** A new setup step (after Welcome, before the model download)
  asks which languages you transcribe, so the download step is honest about what gets used:
  Afrikaans -> Fluister (downloaded on first Afrikaans use), English and the rest -> stock Whisper.
  (`web/static/app.js`, `i18n.js`.)
- **Live engine chip shows the size.** The Fluister/Whisper chip on the live and importing headers
  now reads e.g. "Fluister, Balanced" so you can see which model is running, not just the family.
  (`web/static/app.js`.)
- **No console-window flash.** The `nvidia-smi` hardware probes now run with `CREATE_NO_WINDOW` on
  Windows, so no black console box flashes when the GPU is probed. (`cudadl.py`, `__main__.py`.)
- **GPU panel: download size in the chip.** The chip beside "NVIDIA GPU acceleration" now shows the
  download size (like every other download bubble), and the detected card + VRAM moved to a
  "Detected:" line, so the two are no longer confused. (`web/static/app.js`, `i18n.js`.)
- **Afrikaans code-switching prompt.** The Afrikaans anchor now tells the model the conversation is
  primarily Afrikaans but may switch to English, and to write each as itself, so English in an
  Afrikaans call is transcribed as English. Anti-Dutch anchoring kept. (`transcribe.py`.)
- **Copy.** 12B summary model now has a name and description ("Gemma 4 (12 billion)"); summary card
  reads "Latest summary"; Afrikaans "Meld 'n gogga of idee", interface-language label
  "Volksmond-toepassingstaal", and the Volksmond pronunciation note now shows in English only.
  (`web/static/app.js`, `i18n.js`.)
- **Summary history.** Regenerating a summary no longer overwrites the previous one: the latest is
  always `<transcript>-summary.md` (the one the app shows) and the prior latest is archived next to
  it as `<transcript>-summary-N.md`. The history list hides all of them. (`web/app.py`.)
- **Real "Check for updates".** The About button now does a manual, user-initiated check: one
  outbound GET to the PUBLIC GitHub releases API (no user data sent), comparing the latest tag to
  this build and showing up-to-date / update-available + a download link. CSRF-protected; the only
  outbound call the app ever makes, and only on click. (`web/app.py` /api/check-updates,
  `web/static/app.js`, `i18n.js`.)
- **Afrikaans folder word.** Folder references now use "gids" (the correct Afrikaans for a
  directory); "lêer" stays only where it means an actual file. (`i18n.js`.)
- **History refresh.** The History list now re-fetches every time you open it, so a just-finished or
  just-imported transcript always shows up. Previously it refreshed only on app-start and at the
  instant a session finished, which could race the transcript's write to disk and leave the list
  stale (the transcript was always safe on disk; it just was not shown until restart).
  (`web/static/app.js`.)

## 2026-06-22, v1.1.0: Fluister Afrikaans-tuned models + language-first model selection

The Afrikaans-optimised models ship, and the model you transcribe with is now chosen by the
LANGUAGE you pick rather than a quality dropdown (`licensing.APP_VERSION` 1.0.15 -> 1.1.0).

- **Fluister models.** Afrikaans now transcribes on Fluister, our Afrikaans-tuned Whisper (LoRA
  fine-tunes merged to ctranslate2 int8), published at huggingface.co/digiphyte/fluister-* and
  downloaded on first use like the stock models. On real South African Afrikaans they produce clean
  Afrikaans where stock Whisper drifts to Dutch spellings ("gebou" not "gebouw"), while keeping
  English code-switching intact. English and other languages still use stock Whisper. On this dev
  machine the locally-built ct2 dirs are reused so there is no re-download. (`transcribe.py`.)
- **Language-first selection.** The spoken language picks the model family (Afrikaans -> Fluister,
  else -> Whisper); the hardware still picks the size automatically. The pre-meeting screen leads
  with Language and shows an honest "Engine: Fluister / Whisper" line; model size and GPU/CPU moved
  behind an Advanced disclosure. Settings gains a "Languages you transcribe" picker. EN + AF strings
  added. (`transcribe.py`, `web/app.py`, `config.py`, `web/static/app.js`, `i18n.js`.)
- **Lean engine label on the live screen.** The live and importing headers show a clean
  "Fluister"/"Whisper" chip instead of the raw model path. (`web/static/app.js`.)
- **Internal:** TIER_CONFIG now holds stock size names; the engine resolves the Fluister variant
  from the session language at load time (this also fixed a latent test break where a local
  Fluister path leaked into TIER_CONFIG). Added `test_family_resolution`; web-API tests green.

## 2026-06-19, v1.0.15: summary styles, GPU summaries, live device switch + meters, faster first start

Five user-facing features (`licensing.APP_VERSION` 1.0.14 -> 1.0.15).

- **Reader transcript/summary switch is now an obvious toggle.** In the reader, Transcript and
  Summary were two loose ghost buttons next to Copy and Folder, so while reading the transcript
  the way back to the summary looked like just another toolbar action (the sparkle icon even read
  as "make a new summary"). They are now a connected segmented control, visually distinct from the
  actions. The switching logic itself already worked both ways; this is the affordance fix.
  (`web/static/app.js`, readerView.)
- **Summary styles and custom instructions.** The summarise card and the regenerate controls now
  offer a style: Standard (meeting minutes), Action items only, Decisions and owners, Detailed
  notes, One-paragraph summary, or Custom (a free-text instruction box). The chosen instruction is
  sent to the existing `/api/summarise` (which already accepted `instruction`); the server still
  adds the transcript-cleanup guidance and the output-language directive. EN and AF strings added.
  (`web/static/app.js`, `i18n.js`.)
- **GPU summaries (app code; packaging pending).** Summaries can run on an NVIDIA GPU instead of
  the CPU when this build's llama.cpp has a CUDA backend and the model fits in VRAM, with automatic
  CPU fallback and a Settings GPU/CPU toggle (shown only when capable). Build-agnostic: the shipped
  CPU wheel keeps summaries on the CPU, unchanged. Enabling it ships with the separate GPU build
  (see `docs/cuda-build-plan.md` and `requirements.txt`). New `summary_device` setting.
  (`summarise.py`, `web/app.py`, `config.py`.)
- **Switch mic/speaker and see input levels during a live session.** The live and record-only
  screens now carry a compact audio strip: a dropdown per source to change the microphone or
  system-audio device mid-session, and a level meter for each (peak, with a clip tint). Switching
  restarts the capture on the new device while the engine, recorder, and transcript keep running,
  preserving the timeline (a brief ~1s capture gap during the switch) and reverting to the previous
  device if the new one fails to open. New `/api/levels` and `/api/switch-device`; `/api/status`
  now reports the current devices so a resumed session shows the right ones.
  (`capture.py`, `web/app.py`, `web/static/app.js`, `i18n.js`.)
- **Kill the multi-minute first-use stall.** Loading an already-downloaded model used to revalidate
  it against HuggingFace over the network on every launch (minutes on a slow or flaky connection),
  and the model only loaded when you pressed Begin. Now models load from the local cache only (no
  network), are cached and reused across sessions, and are warmed up in the background the moment you
  open a pre-meeting screen, with a "Preparing / ready" chip next to Begin. A tiny dummy inference in
  the warm-up also pre-initialises CUDA/cuDNN off the critical path. Measured on the 3090: a cached
  load drops to ~0.5s, and the full background warm-up reaches ready in ~5s. New `/api/warm-up`.
  (`transcribe.py`, `web/app.py`, `web/static/app.js`, `i18n.js`.)

Tests: `tests/test_web_api.py` extended (summary GPU capability and device, `fits_on_gpu`, custom
instruction accepted, `/api/levels` + `/api/switch-device` validation, capture keeps `t0`,
`/api/warm-up`) and two stale assertions fixed (the 12B model is in the catalogue now; the
quality-resolution test forces the CPU path so it is correct on GPU boxes too). All web-API tests
pass. Verified in the browser: the nav toggle, the style picker, the live audio strip (device
dropdowns + working meters with clip tint), and the warm-up chip; and measured the cached-load and
warm-up timings on the 3090. Still need an on-machine run for: GPU summary EXECUTION (with the CUDA
wheel on the 3090), real level movement, and a real mid-session device hot-swap.

Codex review (gpt-5.5), folded in: a failed device switch now stops the half-opened capture so it
cannot leak the audio device; and `AudioCapture.levels()` reads the fixed MIC/SYS keys instead of
iterating the live dict, so a startup race can no longer 500 `/api/levels`. Codex's third finding
(warm-up dummy vs session start) was a false positive: the dummy runs inside the build lock that
session start also takes, so they never overlap.

## 2026-06-18, v1.0.14: GPU ignored because of a stray SA_LIVE_TIER env var

The GPU was detected and ready (`cuda_ready=True`) but transcription still ran on CPU. The
`[tier]` log line caught it: `quality=None device='auto' ... cuda_ready=True -> cpu-strong`.
Cause: `resolve_tier` routed the GPU choice through `pick_tier("auto")`, which honours the
CLI-only `SA_LIVE_TIER` env override. A leftover `SA_LIVE_TIER=cpu-strong` on the test laptop
therefore forced a CPU tier even with a ready GPU.

- `__main__.resolve_tier`: when a GPU is ready, compute the GPU tier directly from VRAM rather
  than calling `pick_tier`, so the GUI app never honours `SA_LIVE_TIER` (it stays a CLI-only
  override). `licensing.APP_VERSION` 1.0.13 -> 1.0.14.

## 2026-06-18, v1.0.13: fix summary crash on AVX2-only CPUs (no more AVX-512)

The real fix for the summary `[WinError -1073741795]` (STATUS_ILLEGAL_INSTRUCTION) on Sean's
i7-9750H laptop. Root cause, proven by disassembly: abetlen's llama-cpp-python **0.3.23** "cpu"
wheel is compiled WITH AVX-512 (1203 AVX-512 instructions in `ggml-cpu.dll`), which the
i7-9750H does not have. The earlier "portable wheel" guard did not catch it because the
AVX-512 wheel is also tagged `py3-none`.

- Pinned llama-cpp-python to **0.3.22**, whose cpu wheel is AVX2-safe (0 AVX-512 instructions)
  and still loads Gemma 4. Verified: 0 AVX-512 by disassembly + a real Gemma 4 summary.
- Replaced the build guard with `tools/ensure_avx2_llama.py`, which disassembles `ggml-cpu.dll`
  and FAILS the build if any AVX-512 instruction is present (the tag check was insufficient).
- `licensing.APP_VERSION` 1.0.12 -> 1.0.13.

Note: this fixes ALL summary models on AVX2-only CPUs (they all share this llama.cpp), not just
the 12B. Summaries run CPU-only; on a 4 GB GPU the 12B cannot be offloaded anyway.

## 2026-06-18, v1.0.12: Gemma 4 12B summary model option

- Adds Gemma 4 12B (Q4_K_M, ~7.12 GB) to the summary-model catalogue (`modeldl.py`), pinned to
  the unsloth GGUF mirror with commit + SHA-256 exactly like the E2B/E4B entries. The biggest,
  highest-quality local summary option; CPU-only like the others, so it wants a capable machine
  with plenty of RAM. `licensing.APP_VERSION` 1.0.11 -> 1.0.12.

## 2026-06-18, v1.0.8 to v1.0.11: GPU that just works, summary fix, history split, turnkey build

Testing-driven fixes from Sean's GTX 1650 laptop, plus an independent Codex review
(`codex-review-2026-06-18-v111.md`).

### GPU / device
- The GPU now engages with NO restart and NO manual "Check GPU": `cudadl.cuda_ready()`
  registers the CUDA libs on demand and reports ready only after a cached load-test, so a
  ready GPU is used on the next meeting and a broken/incompatible install falls back to CPU
  instead of selecting a tier that then fails to load.
- A ready GPU is used for ANY quality (`__main__.resolve_tier`). Previously Fast/Balanced/High
  mapped to CPU even with a GPU; on the GPU the Best model is fast, so the GPU always wins.
- "Run on: GPU / CPU" toggle on the pre-meeting and import screens (NVIDIA machines only),
  remembered as a setting, default GPU. On the GPU the Best model runs; the Quality dropdown
  applies on CPU.
- Honest GPU/CPU badge on the live and import screens, a "Check GPU" self-test, and a `[tier]`
  log line recording the device decision.

### Summary
- The summary engine (llama.cpp) is now the portable prebuilt CPU wheel, fixing a
  STATUS_ILLEGAL_INSTRUCTION crash on CPUs without the build machine's AVX-512.
- `build-app.ps1` enforces the portable wheel on every build (WHEEL-tag check, auto-reinstall,
  then re-verify and fail the build if a native wheel slips in) and drops the zip in the synced
  Cowork folder.

### History / first run
- Derived `<stem>-summary.md` files are hidden from History; opening a meeting shows the
  transcript with a Transcript / Summary toggle and surfaces any saved summary.
- The first-run wizard is persisted to disk (`config.setup_complete`), so it stops reappearing
  on every launch (the WebView wipes localStorage); plus a "Skip setup" escape and an awaited
  save.

### Hardening (Codex review)
- CUDA `.part` write path uses a sanitised basename for the PyPI-supplied wheel filename.
- `licensing.APP_VERSION` 1.0.7 -> 1.0.11.
- Deferred and documented: gate `--seed-from-calendar` in the offline build; decide whether to
  block cloud-synced save locations.

## 2026-06-09, v1.0.7: one app, optional NVIDIA GPU (CUDA) download

The shipped build stays CPU-only (so it is one download for everyone), but now it can
fetch the NVIDIA CUDA libraries on demand when an NVIDIA GPU is present, and run the
Best model (large-v3) on the GPU. No separate GPU build.

NVIDIA ONLY. The transcription engine is ctranslate2, whose GPU backend is CUDA. There
is no AMD (ROCm) or Intel-GPU path in its shipped builds, so those machines use the CPU
path (large-v3 on CPU is available for uploads). A Vulkan/OpenVINO route would mean a
different ASR engine; out of scope. All the GPU copy says "NVIDIA" explicitly.

### Added

- **`live_transcribe/cudadl.py`** (new): downloads the matching NVIDIA pip wheels
  (`nvidia-cublas-cu12`, `nvidia-cuda-runtime-cu12`, `nvidia-cudnn-cu12` 9.x) from PyPI,
  verifies each against PyPI's SHA-256, and extracts only the Windows DLLs (basename
  only, no path traversal) into `%LOCALAPPDATA%\sa-live-transcribe\cuda`. Background
  thread + progress, idempotent `installed()`, `remove()`, cached hardware probes.
  `cuda_ready()` decides whether the GPU is actually usable.
- **`live_transcribe/transcribe.py`**: registers the CUDA folder on the DLL search path
  (`cudadl.register_dll_dir()`) BEFORE ctranslate2 imports, so the downloaded libraries
  are found. No-op when none are downloaded.
- **`live_transcribe/__main__.py`**: `pick_tier` routes to the GPU only when
  `cudadl.cuda_ready()` (frozen build: the libs are downloaded; source: a system CUDA
  toolkit is assumed). So the app runs on CPU until the user opts into the GPU download,
  then uses the GPU after a restart. GTX-1650-class 4 GB cards map to the int8 `gpu-4gb`
  tier.
- **`live_transcribe/web/app.py`**: `GET /api/cuda` (gpu_present / installed / ready /
  vram / progress), `POST /api/cuda/download`, `POST /api/cuda/remove` (refused while a
  session runs); `/api/open-folder?which=cuda`; `/api/app-info` gains `cuda_dir`.
- **`live_transcribe/web/static/app.js`**: a CUDA download panel + a new first-run
  "GPU acceleration" step (shown only when an NVIDIA GPU is detected) + a Settings card,
  with download / progress / installed / restart-to-use / remove and the storage path.
- Afrikaans for all the new copy; `tests/test_web_api.py` covers the new endpoints.

### Note

- After downloading the CUDA libraries, Volksmond must be restarted (the DLL search path
  is set once at launch). The UI says so.
- **`live_transcribe/licensing.py`**: `APP_VERSION` `1.0.6` -> `1.0.7`.
- Needs validation on a real NVIDIA machine (GTX 1650): the exact cuBLAS/cuDNN versions
  must load with the bundled ctranslate2 4.7.2. If a fetched version does not load, pin
  a known-good one in `cudadl._WHEELS`.

## 2026-06-09, v1.0.6: reconciled quality levels, large-v3 on CPU, first-run polish

A follow-up to v1.0.5 from Sean's testing notes.

### Changed

- **One quality taxonomy, reconciled.** The meeting screen and the download panel now
  show the SAME set: Auto (default) + four named models (Fast = small, Balanced =
  medium, High quality = large-v3-turbo, Best = large-v3). On the meeting screen, a
  model not downloaded yet is greyed out; clicking it starts that download and selects
  it (ready once it lands). Previously the two surfaces used different names/counts.
  - `live_transcribe/transcribe.py`: new `cpu-large` tier (large-v3 on CPU, int8).
  - `live_transcribe/__main__.py`: `resolve_tier()` maps the UI's model-keyed quality
    (or "auto", or a legacy tier key) to a concrete tier; "Best"/large-v3 picks the GPU
    when present, else CPU. `pick_tier` CPU floor is now `small` (was `base`); base/tiny
    remain internal live-downgrade rungs only.
  - `live_transcribe/voicedl.py`: offers the four models (dropped base from the list).
  - `live_transcribe/web/app.py`: `_resolve_tier_lang_prompt` uses `resolve_tier`.
  - `live_transcribe/web/static/app.js`: `qualitySelector()` (grey-out + click-to-
    download) replaces the old segmented control on both the meeting and import screens;
    `normalizeQuality()` maps legacy saved settings.
- **large-v3 runs on CPU.** Answer to "can the big model work on CPU?": yes. It is too
  slow to hold real-time live on most machines (the adaptive ladder downgrades it
  there), but it is the best-accuracy choice for an uploaded recording / post-meeting
  pass, where there is no real-time constraint. "Best" is now selectable on any machine.
- **First-run: language selector on the welcome screen** (EN/AF), and the **save-location
  page is now translated** (it was English-only despite the rest being Afrikaans).
- **Upgrade CTA is now "Coming soon"** (no pricing / buy flow for now).
- **Model storage locations shown in Settings.** Both the transcription-model card and
  the summaries card show the on-disk folder (voice = the HuggingFace cache; summary =
  the app's models folder) with an Open button, so models can be found and removed by
  hand as well as via the in-app Remove button. New `/api/app-info` fields
  `voice_models_dir` + `summary_models_dir`; `/api/open-folder?which=voice_models|summary_models|sessions`.
- **First-run, two follow-ups:** the welcome screen now shows a "Research Preview"
  badge (the "working name" caveat is gone, the name is settled); and selecting
  "Transcribe and summarise" always reveals the summary-model picker, even when a
  model is already installed (it was hidden in that case, so the picker never showed).
- **`live_transcribe/licensing.py`**: `APP_VERSION` `1.0.5` -> `1.0.6`.

### Deferred (next focused build)

- **One app with optional CUDA download.** Instead of a separate GPU build, ship the CPU
  app and offer to download NVIDIA's cuBLAS/cuDNN libraries during setup when a GPU is
  present, then run large-v3 on the GPU. Feasible; deferred because pinning the DLL
  versions that load with the bundled ctranslate2 needs a test loop on a GPU machine.

### Verification

- All test suites green via the project venv (`test_web_api` incl. new quality-resolution
  + reconciled-catalogue checks, `test_desktop_api`, `test_engine_drain`, `test_dedup`).
- `node --check` clean on `app.js` + `i18n.js`. Frozen-exe smoke after the build.

## 2026-06-08, v1.0.5: models download up front, and Begin no longer looks frozen

**Problem (Sean, testing v1.0.4):** on the pre-meeting screen, clicking Begin sat on
the same page for two to three minutes with no spinner or message before the live
screen appeared, worst on the "Beste" (Best) quality. Two root causes: (1) the Whisper
model is fetched by faster-whisper on first use (multi-GB, several minutes) with no
progress surfaced, and the first-run welcome screen wrongly claimed the model "is
installed with the app"; (2) the browser awaited `/api/start` (which loads the model
synchronously) before changing the view, so the UI showed nothing meanwhile.

### Fix, two parts

1. **Download the models up front, in first-run setup.** A new step (welcome ->
   transcription model -> save location -> summaries -> home) downloads the Whisper
   model to this machine with a real progress bar, into the same cache faster-whisper
   reads, so the first meeting starts without a hidden download. It recommends the
   model the machine will actually use and lets the user grab others.
2. **Immediate feedback on Begin.** Begin / Transcribe now switches straight to a
   "Starting" screen (spinner, honest "loading the model" copy, elapsed timer) while
   the model loads, then to the transcript when ready; a load error is shown there
   with a Back button instead of a vanishing toast.
3. **Manage downloaded models.** Settings now shows the real on-disk size of every
   installed model (voice and summary) and a Remove button to free space; a removed
   model can be downloaded again. Removing the model a running session is using is
   refused.

### Changed files

- **`live_transcribe/voicedl.py`** (new): voice-model downloader, the twin of
  `modeldl.py` (summary models). Background `snapshot_download` into the HuggingFace
  cache with live progress (on-disk size), an idempotent "already cached" path, and a
  hardware-aware recommendation (reuses `pick_tier`). Downloads only the public model
  weights faster-whisper would fetch anyway; HuggingFace verifies each file's hash.
- **`live_transcribe/web/app.py`**: new `GET /api/voice-models` (catalogue +
  recommended model + tier->model map + live progress) and `POST
  /api/voice-model/download` (CSRF-protected; bad model -> 400, already downloading ->
  409). Plus `POST /api/voice-model/delete` and `POST /api/summary-model/delete` to
  free space (re-downloadable later); voice-delete refuses the model a running session
  is using. `/api/start` is unchanged.
- **`live_transcribe/modeldl.py`**: `catalogue_public()` now reports `size_on_disk`,
  and a `delete(key)` removes a summary model file (clearing it as the active model if
  selected).
- **`live_transcribe/web/static/app.js`**:
  - First-run: new "voice" stage with a `voiceDownloadPanel` (mirrors the summary
    panel); welcome copy corrected (the model is downloaded, not bundled).
  - New "starting" route + `startingView`: immediate feedback on Begin and on file
    import, replacing the frozen pre-meeting screen.
  - Pre-meeting Quality hint: warns when "Best" is chosen on a machine with no GPU, or
    notes the download size when the chosen quality's model is not present yet.
  - Settings: a "Transcription model" card to download / switch models later.
  - `boot()` loads `/api/voice-models` and resumes a download poll if one is running.
- **`live_transcribe/web/static/i18n.js`**: Afrikaans for all new strings.
- **`build-app.ps1`**: the zip is now versioned, `volksmond_<version>.zip` (e.g.
  `volksmond_1_0_5.zip`). The Quick Start PDFs are bundled (unchanged).
- **`live_transcribe/licensing.py`**: `APP_VERSION` `1.0.4` -> `1.0.5`.
- **`tests/test_web_api.py`**: new test for the voice-model endpoints (catalogue,
  recommended pick, tier map, bad model 400, CSRF-protected, no stray download).

### Verification

- All 4 test suites green via the project venv (`test_web_api` 13/13 including the new
  voice-model test, `test_desktop_api`, `test_engine_drain`, `test_dedup`).
- `node --check` clean on `app.js` and `i18n.js`.
- Build + real-audio smoke test: pending the rebuild in this session.

### Notes / tradeoffs

- The Begin feedback is frontend-only; `/api/start` stays synchronous and runs on a
  FastAPI worker thread, so `/api/status` etc. stay responsive while the model loads.
  With the model now pre-downloaded in setup, the residual Begin wait is a short load,
  not a multi-GB download.
- `voicedl` does not pin a model commit revision yet (it fetches latest from the same
  Systran / mobiuslabsgmbh repos faster-whisper already uses; HuggingFace verifies file
  hashes). The structure accepts a pinned revision later.

## 2026-06-04, v1.0.4: no terminal window on launch (ready for wider testing)

The shipped exe is now a windowed app. Double-clicking `Volksmond.exe` opens the
native window only, with no console / terminal behind it. This is the build we hand
to wider testers, and it carries the official Volksmond brand mark (see the entry
below): the v1.0.3 zip predated the rebrand, this rebuild ships the new in-app mark
and icon.

### Changed

- **`sa-live-transcribe.spec`**: the EXE is built with `console=False` (was
  `console=True`, a debugging default). Windows now launches it under the GUI
  subsystem, so no console window appears.
- **`app_main.py`**: a windowed PyInstaller build has no console, so `sys.stdout`
  and `sys.stderr` are `None` and any `print()` or uncaught traceback would raise.
  `_redirect_windowed_output()` points both at a per-launch log file,
  `%LOCALAPPDATA%\sa-live-transcribe\volksmond.log` (truncated each launch so it
  stays small and always reflects the latest run). Source / console runs are
  untouched. A tester who hits a problem can send that one file.
- **`live_transcribe/licensing.py`**: `APP_VERSION` `1.0.3` -> `1.0.4`.

### Notes

- `--browser` and `--server-only` still work. With a single windowed exe they no
  longer show a console; `--browser` is a vestigial fallback now that the v1.0.0
  hang it worked around is fixed. To debug a build whose window will not appear,
  set `console=True` in the spec and rebuild.
- Deferred (not blocking wider testing): the cosmetic `IntelÂ®` mic-name mojibake
  seen only in the built exe on one laptop, and defaulting the Whisper tier to
  `medium` on slower CPUs so Stop drains faster.

### Verification

- All 4 test suites green via the project venv (`test_desktop_api`, `test_web_api`,
  `test_engine_drain`, `test_dedup`).
- Codex review of the diff: no blockers. The one acted-on nit moved the app import
  after the output redirect so import-time failures are logged too. Record in
  `codex-review-2026-06-04-v104.md`.
- Build clean (PyInstaller 6.20.0): the bootloader is `runw.exe` (the windowed
  loader), not `run.exe`; "Building because console changed". dist 378 MB,
  `Volksmond.zip` 141 MB, copied to the project root.
- PE header check on `Volksmond.exe`: Subsystem = 2 (Windows GUI), so Windows
  allocates no console on launch. Embedded icon extracts at 32x32.
- Headless boot (`Volksmond.exe --server-only`): server came up, `/api/app-info`
  reports version 1.0.4, and `/assets/app.js` serves the new brand mark (viewBox
  0 0 1500 1500). The startup line was written to `volksmond.log`, confirming the
  no-console stdout redirect works.
- Not re-verified here (unchanged from the confirmed-good v1.0.3 capture path; left
  to Sean's retest): the native window appearing with the icon in the title bar /
  taskbar, and a real-audio Begin -> transcript capture.

## 2026-06-04, brand: the official Volksmond logo everywhere

Chenelle delivered the final Volksmond mark (the "listening face": five waveform
bars over a smile). It replaces the interim speaker mark across every surface:

- In-app wordmark (`live_transcribe/web/static/app.js` `markSvg()`), as one inline
  `currentColor` SVG so it follows the palette (brand blue on light, light-blue on dark).
- App / taskbar icon: `volksmond.ico` regenerated by the rewritten `build-icon.py`,
  which now composites the real `brand/volksmond-mark-white.png` onto a brand-blue tile.
- App favicon: new `live_transcribe/web/static/favicon.svg` (brand blue), linked from `index.html`.
- Quick Start guides (EN + AF) header + footer marks; both PDFs regenerated.
- The early-access landing page (rebuilt via `landing/build/assemble.py`; the JS logo
  picker is collapsed to the single mark).
- Real brand assets committed under `brand/` (four colourways, SVG + PNG) with a README.

Colourways: black `#000000`, white `#ffffff`, brand blue `#36587b` (Clinical accent),
light blue `#7ac1f2`. The in-app mark + icon ship on the next exe rebuild (the v1.0.3
zip predates this); source-mode shows them immediately.

## 2026-06-04, v1.0.3: capture actually works on Sean's laptop + tidy device list + icon

v1.0.2 surfaced the underlying capture bug clearly enough that we could probe
it. Sean's laptop: every `Begin` returned `Could not start audio capture:
could not open system audio device #N '... [Loopback]': [Errno -9996] Invalid
device`, for EVERY loopback choice. Source-mode reproduced it 1:1, so it was
not PyInstaller -- it was the open-call itself.

### Root cause

The Realtek HD Audio driver on this laptop reports its WASAPI loopback devices
with `maxInputChannels = 8` (claiming 8-channel surround capability), but the
actual current shared-mode mix format is **2-channel stereo**. `Pa_OpenStream`
through pyaudiowpatch will only open WASAPI loopback at the device's real
mix-format channel count. The old `capture.py` did
`channels = max(1, info["maxInputChannels"])` -- so it tried `ch=8` and got
`-9996`. Mono (`ch=1`) also fails on WASAPI loopback. Only `ch=2` opens.

The home PC where v1.0.0 was built must have had `maxInputChannels = 2`
already, so the bug latently existed but never fired.

Probe `probe-loopback.py` (added this rev for future diagnosis) confirmed the
matrix:

    [FAIL] fmt=paFloat32 rate=48000 ch=1 -- OSError(-9996, 'Invalid device')
    [OK  ] fmt=paFloat32 rate=48000 ch=2
    [FAIL] fmt=paFloat32 rate=48000 ch=8 -- OSError(-9996, 'Invalid device')
    [FAIL] *           rate=44100 *      -- OSError(-9997, 'Invalid sample rate')

### Fix

`live_transcribe/capture.py` `_open_stream` now iterates a fallback list
`[maxInputChannels, 2, 1]` (deduped, in range) and accepts the first
combination that opens. The callback closure binds the winning channel count
via default arguments so a failed earlier attempt cannot leak channel state
into a later one. On standard stereo hardware the first attempt already
succeeds; on Sean's misreporting Realtek the second attempt (`ch=2`) wins;
on a real 7.1 setup the first attempt (`ch=8`) still wins.

### Other v1.0.3 changes

- **`live_transcribe/web/app.py`** `/api/devices`:
  - Mic list deduped to **WASAPI-only** (was 8 entries on Sean's laptop: MME
    + DirectSound + WASAPI versions of every device, plus "Microsoft Sound
    Mapper" / "Primary Sound Capture Driver" meta-devices). Now 2 entries,
    matching the API loopbacks already use.
  - `_fix_name()` helper undoes pyaudiowpatch's latin-1-as-UTF-8 mangling
    of device names (`IntelÂ®` becomes `Intel®` i.e. `Intel(R)`).
  - System-default mic now maps to its WASAPI twin by name when the system
    default is on MME / DirectSound, so the dropdown's default highlight
    still works.
- **`volksmond.ico`** (new): rounded-tile rendering of the inline SVG mark
  in app.js (open curve + three sound bars), Clinical-palette accent blue
  tile with near-white strokes. Multi-resolution (16 / 24 / 32 / 48 / 64 /
  128 / 256).
- **`build-icon.py`** (new): one-shot generator for the .ico. Rerun if the
  brand mark ever changes. Pillow-only; no external SVG renderer needed.
- **`probe-loopback.py`** (new): the diagnostic that pinned down the
  Realtek channel-count quirk. Kept in the tree so the next driver-quirk
  hunt has a known starting point.
- **`sa-live-transcribe.spec`**: `EXE(..., icon="volksmond.ico")` so the
  taskbar / file explorer pick up the icon.
- **`live_transcribe/licensing.py`**: `APP_VERSION` `1.0.2` -> `1.0.3`.

### Verification

- All 4 test suites green after the capture-fallback edit.
- Source-mode end-to-end test: Begin -> session active, "Listening" UI
  rendered, file saved as `2026-06-04-141636-test.md`. Confirmed by Sean.
- Built exe smoke test: server up cleanly, `version: 1.0.3`, recursion
  count 0, mic dropdown deduped (8 entries -> 2 WASAPI-only), icon
  embedded (32x32 extracted via `System.Drawing.Icon`), capture should
  succeed at Begin (proven from source-mode; same code path in the build).

### Known issue (cosmetic, non-blocking)

On the laptop the BUILT exe still returns `Microphone Array (IntelÂ®
Smart Sound Technology (IntelÂ® SST))` instead of the clean
`Intel®` form, even though the bundled `_fix_name` bytecode is correct
and DOES transform the problem string when invoked manually against an
extracted .pyc. Source-mode (`python -m live_transcribe.web`) returns
the clean form, so the bundled FastAPI runtime is doing something
different at request time that the function-level fix is not catching.
Function returns the input unchanged in the exe context for a reason
that has eluded a few rounds of bytecode inspection, manual
invocation, locale checks, and a clean-cache rebuild. The dropdown
still works, just renders one Intel mic with `Â®` instead of `®`.
Defer for v1.0.4 if a future user reports it; might be worth a
response-middleware fix that runs the same latin-1 -> UTF-8 transform
on the serialised JSON regardless.

## 2026-06-04, v1.0.2: clearer audio capture error + save-location in first-run

Two small follow-ups after Sean's first end-to-end test of v1.0.1 on the laptop.
v1.0.1 fixed the hang and the window worked, but two rough edges came out:

1. "Could not start audio capture: [Errno -9996] Invalid device" -- raw PyAudio
   message did not say WHICH device failed. The dropdown defaulted to "Headphones
   (Realtek(R) Audio) [Loopback]" which is enumerable but not openable when no
   headphones are plugged in. The user has to guess to switch to the Speakers
   loopback in the same dropdown.
2. The first-run setup never asked where to save transcripts. It went
   welcome -> summaries -> done, leaving the default
   `%LOCALAPPDATA%\sa-live-transcribe\sessions` in place. Most users would want
   it under Documents or a synced folder, and the current Settings location is
   easy to miss.

### Changed files

- **`live_transcribe/capture.py`**, `AudioCapture.start()`: each `_open_stream`
  call is now wrapped, so a failed open raises with the source label, the
  device index, the device name, and a concrete remediation. New message looks
  like: `could not open system audio device #16 'Headphones (Realtek(R) Audio)
  [Loopback]': [Errno -9996] Invalid device. Try a different option in the
  System audio dropdown (e.g. Speakers if Headphones fails).`
- **`live_transcribe/web/static/app.js`**, `setupView()`: added a new stage
  `save_location` between `welcome` and `summaries`. Shows the current default
  save folder, lets the user pick a different folder via the existing native
  `pickFile("folder")` flow, and continues. "Continue" with no override keeps
  the default. Picking a folder writes through `/api/settings` immediately so
  it sticks even if they bail before finishing setup.
- **`live_transcribe/licensing.py`**: `APP_VERSION` `1.0.1` -> `1.0.2`.

### Verification

- `python tests/test_*.py` all 4 suites green after the capture.py edit.
- `node --check app.js` clean after the setup-stage edit.
- Build (PyInstaller) clean; smoke-test of the rebuilt `Volksmond.exe` will
  appear once the rebuild completes in this session.

## 2026-06-04, v1.0.1: native window no longer hangs on launch

**Problem (Sean, testing the v1.0.0 zip on the laptop):** the Volksmond window opens
and renders the Get started screen, then the title bar flips to "Volksmond (Not
Responding)". The console spams thousands of:
`[pywebview] Error while processing window.native.AccessibilityObject.Bounds.Empty.Empty.Empty.<...> : maximum recursion depth exceeded`

**Root cause:** an own-goal in `live_transcribe/desktop.py`, NOT a pywebview platform
bug. pywebview's JS-API exposer (`webview/util.py:180`, `get_functions`) recursively
walks every PUBLIC attribute of the `js_api` object to expose callables as
`window.pywebview.api.*` in the page. We stored the pywebview `Window` reference as
`self.window = window` so `pick_path` could call `create_file_dialog` on it; that
made `window` a public attribute. The walker then recursed into the pywebview Window,
into its `.native` (the .NET WinForms `Form`), into `.AccessibilityObject.Bounds`
(a `Rectangle`), into `Rectangle.Empty` (a .NET static; pythonnet returns a fresh
Python wrapper each access, so the walker's `id()`-based cycle-guard never trips).
The walker descended `.Empty.Empty.Empty.<...>` until Python's recursion limit,
logged on every branch, and did it for every dir() entry of every parent (tens of
thousands of log lines per paint. The GUI thread choked.

**Fix:** rename to underscore-prefixed (`self._window` and `api._window = window`).
The walker skips `_`-prefixed names (`util.py:193`). One-character fix, no version
downgrade, no monkey-patches. Pywebview pin stays at `==6.2.1`.

### Changed files

- **`live_transcribe/desktop.py`**: `self.window` -> `self._window` (3 sites:
  `__init__`, `pick_path`, and the `api.window = window` assignment in `main()`).
  Added a docstring note on `DesktopApi` so the next person doesn't reintroduce a
  public attribute holding a pywebview/pythonnet object.
- **`live_transcribe/licensing.py`**: `APP_VERSION` `1.0.0` -> `1.0.1`.
- **`tests/test_desktop_api.py`** (new): regression. Asserts that every public
  attribute on a fresh `DesktopApi` is a callable, and that `api.window` does not
  exist (must be `api._window`). Guards against re-introducing the recursion.

### Verification (laptop, Windows 10 22H2, build 19045)

- Installed pywebview 6.2.1 in the laptop venv; ran
  `python -m live_transcribe.desktop` from source.
- Server came up on `:8765` on the first poll. `smoketest-desktop-source.log` is
  empty: **zero** `Error while processing ...` lines (was thousands per second
  before).
- Server-only mode (`--server-only`) had already proven the FastAPI/uvicorn/engine
  stack was fine; this confirms the JS-API exposer is no longer recursing.
- Diagnostic doc: `LAPTOP-FIX-2026-06-04.md` (in the project root).

### Workaround for anyone holding the v1.0.0 zip

Run `Volksmond.exe --browser` (or use `Volksmond - Browser Mode.bat` if it's been
dropped next to the exe). Opens the same UI in the default browser instead of a
native window; sidesteps the JS-API exposer entirely.

## 2026-05-23, Volksmond UI rebuild + summaries made free

**What:** Replaced the basic start/stop web page with the full Volksmond interface
(from the Claude Design handoff), rebuilt as a vanilla-JS single-page app wired to
every real endpoint. Default look is the "clinical" palette, light, document
transcript layout. Offline-first: no CDN, no web fonts, no framework (system font
stack, hand-drawn inline SVG icons), so it still works with no internet.

**Decision (please confirm):** local AI summaries moved from Pro to **Free**. The
design's "Pro principle" is that Pro covers only what needs an online connection
(calendar attendee seeding, optional cloud fallbacks); anything that runs on this
machine stays free. Practical bonus: summaries were previously unreachable by
anyone, since no licence public key ships yet, so this is also what makes them
usable at all. Revert if the pricing should differ.

### Changed files

- **`live_transcribe/licensing.py`**: `PRO_FEATURES` is now `{"calendar"}` (was
  diarise/calendar/exports/ai_summary/vocab_library/history_search). Everything
  local is free by design.
- **`live_transcribe/web/app.py`**:
  - `/api/summarise` no longer Pro-gated (local, so free). The 409 "finish the
    session first" guard and the path-traversal checks stay.
  - New `GET /api/app-info` (name, version, OS string, save dir) for the footer and
    the bug-report mailto.
  - New `POST /api/pick?kind=file|folder`: opens a native OS dialog (tkinter,
    imported lazily) and returns an absolute path. Right design for a local app
    handling multi-GB media: pick a path on disk rather than upload bytes through
    the browser. UI falls back to a paste-a-path field if no dialog is available.
  - Mounted `StaticFiles` at `/assets` so CSS/JS can be separate, reviewable files.
- **`live_transcribe/web/static/index.html`**: thin shell; loads `/assets/styles.css`
  + `/assets/app.js`; applies the saved theme before paint (no dark-mode flash).
- **`live_transcribe/web/static/styles.css`** (new): Volksmond design tokens (three
  palettes x light/dark) + component primitives + app layout.
- **`live_transcribe/web/static/app.js`** (new): the SPA. Router + screens: first-run
  setup, new-session hub (live / upload file / record-only), pre-meeting start (with
  microphone + system-audio device pickers wired to `/api/start`), live transcript
  (focus mode, SSE, three-way Stop), record-only + "transcribe this now" handoff,
  importing, finish + Summarise, history + reader, settings, upgrade/activation.
  Theme picker (system/light/dark), keyboard shortcuts (Esc, Cmd/Ctrl+Enter to
  begin, Cmd/Ctrl+K search), basic keyboard a11y on clickable cards, privacy-first
  "Report a bug or idea" mailto.
- **`tests/test_web_api.py`** (new): pins the changes. Proves summaries are not
  Pro-gated (bogus file returns 404, not 403), `PRO_FEATURES == {"calendar"}`,
  `/api/app-info` shape, settings never leak the secret key, and `/assets` serve.

### Deliberately NOT built (design showed them, no backend exists; kept honest)

Clean second pass / named-speaker diarisation on the finish screen (only exists
offline via `retranscribe.py`); in-app summary-model download with a progress bar
(no downloader; Settings points at a `.gguf` instead); calendar toggle in the start
flow (CLI + Graph only, shown as a Pro item in copy); true file drag-and-drop (a
browser cannot hand the server a path, so the dropzone is click-to-browse); a real
file-transcription progress percentage (backend reports none, so importing shows an
indeterminate bar plus the live transcript).

### Verification

- `python tests/test_web_api.py` green (5/5); `python tests/test_engine_drain.py`
  still green (3/3).
- `node --check` clean on `app.js`. Server runs; in a headless browser all 11
  screens render with zero console errors, real data flows (8 mics / 2 loopbacks,
  10 sessions, Gemma summary model shown installed), the device picker defaults to
  the real microphone, dark mode applies without flash, and the three-way stop menu
  shows exactly three options when recording.
- Not exercised here (needs real audio): a live transcription, recording, and the
  native `/api/pick` dialog.

## 2026-05-21, Stop now captures the tail (drain-on-stop)

**Problem:** Transcription runs a couple of minutes behind real time. Pressing Stop tore the engine down immediately, discarding every chunk still queued, so the last few minutes of a meeting were lost. (See `IMPROVEMENTS.md` item 1 for the root-cause analysis.)

**Fix:** Stop now keeps transcribing the already-captured backlog before finishing, so nothing up to the moment you press Stop is lost.

### Changed files

- **`live_transcribe/transcribe.py`**, `Engine` drains on stop.
  - `stop(drain=True, timeout=None)` (new signature, backwards-compatible): with `drain=True` (default) it finishes every queued chunk before exiting and blocks until done; `drain=False` is a fast abort that abandons the backlog.
  - `_run()` now loops until the sentinel (or, during shutdown, until the queue is empty) instead of breaking the instant `_stop` is set, that early break was what dropped the backlog.
  - Added `pending()` (queued + in-flight count, for progress) and a `_busy` flag.
  - `on_chunk()` ignores new audio once shutdown has begun (so the backlog is bounded while draining).
  - The 15 s join cap is gone (it could never finish a multi-minute backlog).

- **`live_transcribe/web/app.py`**, `/api/stop` is non-blocking.
  - Flips state to `stopping`, then runs `capture.stop()` → `engine.stop(drain=True)` → `md_sink.close()` on a **background thread**, without holding `STATE.lock` for the (possibly minutes-long) drain. Resets when done.
  - `capture.stop()` runs first so its trailing partial chunk is flushed into the engine queue and then drained (not lost).
  - `md_sink` is closed **after** the drain (was before).
  - `/api/status` now reports `stopping` (bool) and `pending` (chunks left) while draining.

- **`live_transcribe/web/static/index.html`**, Stop UX.
  - Keeps the SSE stream open after Stop so the final segments stream in live.
  - Polls `/api/status` and shows "Finishing transcription… N chunks of audio left" until the drain completes, then "Saved → …". Stop button stays disabled until done.

- **`tests/test_engine_drain.py`** (new), regression test, no model load (monkeypatches `WhisperModel`). Proves: `drain=True` processes the whole backlog; `drain=False` abandons it; `on_chunk` rejects audio during shutdown. Run: `python tests/test_engine_drain.py` from the project root.

### Verification
- Unit test green (3/3).
- Live start→stop smoke test: `/api/stop` returns `{stopping:true}`, `pending` counts down, state resets to `running:false`, and the transcript file gets its "End of session" footer.
- All changed `.py` files `py_compile` clean.

### Behaviour notes
- Stop now takes roughly as long as the current lag to finish, that's expected, it's transcribing the backlog. The UI shows the countdown so it doesn't look frozen.
- The drain join is intentionally unbounded (correctness over speed: never lose audio). In the unlikely event a single chunk's transcription hung, the session would stay in "Finishing…", acceptable trade-off given the goal; revisit only if it ever happens in practice.
- The CLI path (`__main__.py`, Ctrl+C) inherits the same drain-on-stop benefit; double-Ctrl+C still force-exits.
