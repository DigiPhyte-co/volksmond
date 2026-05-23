# Volksmond landing page, spec

A simple, single-page early-access landing page. Captures an email in exchange for the
free download. Calm, privacy-first, trustworthy. Matches the app's "clinical" look.
No em or en dashes in any copy (house rule).

## Goal
One job: convince a privacy-conscious South African that this is a private, on-device
transcription tool that actually handles Afrikaans, and capture their email to send the
free early-access download.

## Brand and voice
- "Volksmond, by DigiPhyte" (name provisional).
- Voice: calm, plain, confident. No hype, no exclamation marks, no dashes. SA English.
- Bilingual: every piece of copy exists in English and Afrikaans (see toggle below).
- Tagline: "Speak freely · Praat vrylik" (shown in BOTH languages always, the brand
  signature; not toggled). Carries the privacy promise and demonstrates the bilingual
  product. Echoes Sean's tospeakfreely handle.

## Key messages (priority order)
1. Private by design. Runs entirely on your computer; your audio and transcripts never
   leave the machine; it never phones home. THE hero message.
2. It actually handles Afrikaans, and the real Afrikaans-English mix, where the big paid
   cloud tools give you gibberish. English is excellent too.
3. Free, in early access. Request access, get the download.
4. If it works for you, ditch the paid cloud tools entirely: all the benefit, fully
   offline, completely private.
5. Source-available: read the code and verify for yourself that it never phones home.

## Page structure (single page, simple)
1. Sticky header: Volksmond wordmark (left, reuse the app's sound-glyph mark), EN/AF
   language toggle (right), and a "Request early access" button.
2. Hero: H1 + subhead + email capture (one email field + button "Request early access")
   + a one-line privacy reassurance under the form. Calm visual, no stock photos: the
   sound-glyph mark, a clean abstract, or a tasteful shot of the transcript view.
3. Problem strip: short. You sit in meetings you do not host, often in Afrikaans or the
   mix; you cannot record them; cloud tools either butcher the Afrikaans or ship the
   conversation to someone else's servers.
4. What you get: four cards. Runs on your machine. Afrikaans, English, and the mix.
   Optional local summaries. Works fully offline.
5. "Ditch the cloud" section: the all-offline, all-private, source-available pitch from
   message 4 + 5.
6. Trust strip: three short items with icons: Never phones home. Source available. by
   DigiPhyte.
7. Repeat CTA: the email capture again.
8. Footer: by DigiPhyte, link to digiphyte.com, a short privacy note, POPIA consent on
   the form.

## EN / AF toggle (required)
- A toggle in the header (EN | AF) that switches ALL page copy between English and
  Afrikaans instantly, client-side. Default English; remember the choice.
- This serves the SA audience and demonstrates the product's bilingual nature.
- Provide both language strings for every piece of copy (mirror the app's i18n approach).

## Capture form
- Email only (name optional). No phone, no company.
- POPIA consent checkbox: "I agree to be contacted about Volksmond early access."
- On submit: a calm thank-you state; the download link is delivered by email (and shown
  on screen once the installer exists).

## Design direction
- Match the app: the "clinical" palette (calm near-monochrome, a cool muted blue accent),
  light with an optional dark mode, generous whitespace, hairline borders, rounded
  corners. IBM Plex Sans for UI; IBM Plex Serif is fine for the H1 if it reads well.
- Calm and trustworthy, not flashy. Fully mobile-responsive.

## Out of scope (for now)
Pricing, Pro, the future cloud product. This page is only the free early-access download.

## Build notes (for implementation, after the design)
- Static, on Cloudflare Pages, subdomain volksmond.digiphyte.com (CNAME from AfriHost).
- Form posts to a small Cloudflare Pages Function that stores the email and emails the
  download link. Download hosted on the public GitHub releases repo.
- Stitch produces the visual design; we wire the form, the language toggle strings, and
  the email handler at build.

---

## English hero copy (starting point)
- H1: "A private transcript of any meeting, on your own computer."
- Subhead: "Volksmond transcribes your meetings on your machine. Your audio never leaves
  it, nothing is sent to the cloud, and it is built for Afrikaans and the way we actually
  mix it with English."
- CTA button: "Request early access"
- Under-form line: "Free while in early access. Your email is only used to send you the
  download and the occasional update."

## What-you-get cards (English)
- "Runs on your machine. No cloud, no third-party servers, nothing to leak. Use it fully
  offline."
- "Afrikaans, English, and the mix. A usable transcript for the meetings other tools turn
  into nonsense."
- "Optional local summaries. Decisions, action items, and open questions, generated on
  your machine."
- "Yours to keep. Transcripts saved as plain files on your computer, in your folder."

## Ditch-the-cloud section (English)
- Heading: "Good enough to leave the cloud behind."
- Body: "If Volksmond works for your meetings, you can drop the paid cloud transcribers
  entirely. Same usable transcript, fully offline, your data completely private, and the
  source is available so you can confirm it never phones home."

## GitHub repo description (dovetails with the page)
- Short "About" (one line): "Volksmond, by DigiPhyte. Private, on-device meeting
  transcription for Afrikaans and English. Runs fully offline, never phones home."
- README opener:
  "# Volksmond
  Private, on-device meeting transcription for South African Afrikaans and English.
  Volksmond runs entirely on your own computer. Your audio and transcripts never leave the
  machine, nothing is sent to the cloud, and it is built to handle Afrikaans and the real
  Afrikaans-English mix where cloud tools give you gibberish. Source-available so you can
  verify exactly that.
  by DigiPhyte."
