# Changelog, SA-Live-Transcribe

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
