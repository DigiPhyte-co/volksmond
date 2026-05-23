# Volksmond, distribution and go-to-market plan (living)

Status: planning only, nothing here is built. Drawn up 2026-05-23 during a strategy
discussion. Each decision is tagged LOCKED / LEANING / OPEN. This is the running list
of go-to-market threads; update it as they firm up.

## 1. Landing page (lead magnet)
LEANING:
- Host at a SUBDOMAIN, e.g. volksmond.digiphyte.com, not a bought .com. Zero cost,
  instant, and the name is still provisional. Redirect to a real domain later if the
  name is committed. Borrows DigiPhyte credibility (which the app now credits anyway).
- Capture EMAIL ONLY (name optional). No phone, no company: it is a privacy tool, so
  minimal data is on-brand, converts better, and is less POPIA surface. Enrich later.
- Static page on Cloudflare Pages (same pattern as the BiaD client sites), CNAME the
  subdomain from AfriHost. Form posts to a small CF Pages Function that stores the
  email and returns/emails the download link. Download hosted on R2 or a public GitHub
  release.
- Content: one-line value prop, the privacy promise as the hero, 4 feature bullets
  (live transcription; Afrikaans/English/code-switch; optional local summaries; fully
  offline), who it is for, email form + POPIA consent, "by DigiPhyte" credit.
DEPENDENCY: the page cannot deliver a download until the installer exists, so its real
launch is gated on the "easy install" packaging phase.
STRATEGIC FIT: a free Volksmond download is another DigiPhyte lead magnet alongside the
Founder's Checklist and the free Website Review; the email list feeds the pipeline.

## 2. Source visibility
LEANING: SOURCE-AVAILABLE, not full open source.
- The goal is the TRUST benefit (a privacy tool; let people read the code and confirm
  it never phones home), not giving away the right to fork and sell.
- Source-available licence = read and build allowed, redistribution and commercial use
  reserved. Keeps the freemium model legally protected.
- Publish the WHOLE app (partial publishing looks like hiding the network code).
- Piracy risk is low: everything local is already free; Pro is only the online
  features, which are hard to pirate.
- Tradeoff: no open-source community contributions, but trust plus protection win for a
  niche commercial privacy tool.

## 3. Two build profiles (one codebase)
LEANING: ship two builds from ONE codebase via a build flag, decided at packaging time.
- OFFLINE-ONLY build: the online modules (outlook.py / calendar, any cloud paths, any
  update check) are compiled OUT. It provably cannot phone home. This is the
  high-assurance flagship for counselling/legal/paranoid users, and what Sean's wife
  runs. A calm CTA links to the site for the connected build.
- CONNECTED build: the full app (local-first, optional online Pro features available).
- Cheap because the online code is already isolated (lazy imports + entitlement seam).
- GOTCHA: the offline-only build must also strip the "check for updates on launch" idea
  from the settings design (that is a phone-home). No update mechanism is built yet, so
  this is free to get right.
- Pairs with source-available: "read the code, or run the build with no network code at
  all."

## 4. Beta positioning
LEANING: launch as a free BETA, graduate to V1 after gathering real-world issues.
- It is probably good enough for V1, but shipping as beta surfaces bugs first and sets
  expectations (users report instead of silently churning). Matches the calm,
  no-overpromising voice; on a privacy tool, "beta" reads as honest and careful.
- Free during beta; the value exchange is "free tool for your feedback", which also
  justifies the email capture. Do not sell Pro during beta (or only an early-bird).
- In-app: a subtle "Beta" tag by the version (reuse the existing "working name"
  treatment). Landing page: "Free beta / early access".
- Feedback channel: the bug-report mailto already built; add "this is a beta, tell us
  what breaks" microcopy.
- Version: display 0.9.x (beta); keep APP_MAJOR = 1 for forward licence compatibility.
  (licensing.APP_VERSION is currently "1.0.0".)
- GRADUATION CRITERIA (define to avoid perpetual beta): e.g. several weeks of real use
  with no data-loss bugs, top accuracy/crash issues fixed, installer solid, wife plus a
  handful of beta users happy.
- Tradeoff: "beta" slightly lowers perceived polish, but on a free privacy tool it is
  net positive.

## 5. macOS (later phase, not a port)
OPEN: defer; it is a proper phase, not a quick port.
- Ports for free: the pywebview UI (WKWebView on Mac) and the transcription/summary
  engine are platform-agnostic and already done.
- The hard part is SYSTEM-AUDIO capture. macOS has no built-in loopback. Options:
  1. Mic-only on Mac (cheapest): works for in-person meetings, not for capturing the
     other side of a Teams/Zoom call. The app already supports mic-only.
  2. ScreenCaptureKit (macOS 13+): Apple's modern, permission-based, no-driver system-
     audio capture (what current competitors use). Needs a small native Swift helper
     feeding the Python engine. The RIGHT path, real native work.
  3. BlackHole/Loopback virtual driver: avoid for non-technical users (same trap as
     Windows "Stereo Mix", worse).
  RECOMMENDATION: when Mac happens, do ScreenCaptureKit, not BlackHole.
- Packaging differs: a Mac .app needs Apple codesigning + notarization (paid Apple
  Developer account, ~$99/yr) or Gatekeeper blocks it. Unavoidable for distribution.
- No CUDA on Mac; CPU or Core ML/Metal. CPU is fine for the smaller models.
- User: Chanel (works for Sean) is on Mac, so there is a real internal user eventually.
  The wife (primary, counselling) is Windows, so Windows-first is correct.

## Day-two feature backlog (post-beta, not launch-critical)
Ideas parked for after the beta; add to this list as they come up.
- **Remembered participants, local autocomplete (FREE, privacy-first).** Every term/name
  used in a meeting is saved locally (count + last-used), and the "Names and jargon"
  field autocompletes from that history, so recurring collaborators are one tap away.
  This is the local, offline alternative to the Outlook calendar pull (which stays the
  Pro/online feature), and is a better default for most users and for the wife's
  counselling use (recurring client names, no calendar/Outlook). Design notes:
  - SUGGEST, do not auto-apply. Whisper's initial_prompt is short (~224 tokens), so
    stuffing every name ever used would HURT accuracy. Only the names selected for THIS
    meeting go into the prompt; history is for suggestions only. (Distinct from
    `default_context`, which is the always-applied standing context.)
  - Rank suggestions by frequency then recency; optional "add usual participants" one-tap.
  - Privacy: stored locally only, never synced or sent; include a "clear remembered
    names" control (names are personal info; on-device only, same class as transcripts).
  - Small build: a persisted list (config.py key or a tiny endpoint) + record-on-use +
    autocomplete on the existing chipbox.

## Decisions snapshot
- LOCKED: native pywebview shell as the product (built); DigiPhyte credit in-app (built);
  local AI summaries are free, not Pro (built).
- LEANING (cheap, reversible): subdomain over .com; email-only capture; source-available;
  free-beta launch; two build profiles.
- OPEN (decide at packaging / later): exact source-available licence; offline-only vs
  connected split mechanics; macOS approach and timing; graduation-to-V1 criteria.

## Build and launch order (current best guess)
1. Codex review of PR #1 + Sean tests the native window.
2. Fix review and test findings.
3. Easy install: bundle pywebview/pythonnet in PyInstaller, flip the frozen default to
   native, Inno Setup installer, first-run model-download UX.
4. Decide the source-available licence + the offline-only build flag.
5. Landing page (subdomain, email capture) goes live with the installer download.
6. Free beta to a small group (including the wife); gather issues.
7. Graduate to V1; introduce Pro.
