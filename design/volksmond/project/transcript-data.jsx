// transcript-data.jsx — sample Afrikaans/English code-switched transcript
// used across all three live-transcript variants.

const TRANSCRIPT = [
  { t: "00:01:14", s: 2, lang: "af",
    text: "Goeiemôre almal. Dankie dat julle vandag kon inskakel." },
  { t: "00:01:21", s: 0, lang: "af",
    text: "Môre Thandi. Sorry ek is bietjie laat, my mic was nog op mute." },
  { t: "00:01:27", s: 2, lang: "mix",
    text: "No worries. Ek het net begin praat oor die Q3 numbers." },
  { t: "00:01:36", s: 3, lang: "mix",
    text: "Quick question before we move on. Het ons al die finale forecast gekry van die finance team?" },
  { t: "00:01:44", s: 2, lang: "af",
    text: "Nee, hulle is nog besig. Sou eers Donderdag finaliseer." },
  { t: "00:01:53", s: 0, lang: "af",
    text: "OK, dan kan ons miskien net die preliminary cycle deurloop, en dan return na die forecast as ons dit het." },
  { t: "00:02:02", s: 3, lang: "en",
    text: "Makes sense. So practically speaking, wat verander aan ons go-to-market?" },
  { t: "00:02:14", s: 2, lang: "af",
    text: "Niks groot nie. Ons hou by die enterprise segment, maar ons gaan SMB rolling laat oopgaan vanaf volgende kwartaal." },
  { t: "00:02:26", s: 3, lang: "mix",
    text: "Oraait. En het ons capacity op die delivery side, of moet ons eers nog mense aanstel?" },
  { t: "00:02:35", s: 2, lang: "af", inProgress: true,
    text: "Goeie vraag. Ons is op die oomblik 80% utilised, so daar's bietjie ruimte maar ek wil nie",
  },
];

// Mark phrases that switch language mid-line — used by the code-switch variant
// to render small inline language ribbons. (Token-level segmentation is faked
// here for the visual; the real app would get this from the recogniser.)
const CODESWITCH_TOKENS = {
  "00:01:21": [ // "Môre Thandi. Sorry ek is bietjie laat, my mic was nog op mute."
    { lang: "af", text: "Môre Thandi." },
    { lang: "en", text: "Sorry" },
    { lang: "af", text: "ek is bietjie laat," },
    { lang: "en", text: "my mic was" },
    { lang: "af", text: "nog op" },
    { lang: "en", text: "mute." },
  ],
  "00:01:27": [
    { lang: "en", text: "No worries." },
    { lang: "af", text: "Ek het net begin praat oor die" },
    { lang: "en", text: "Q3 numbers." },
  ],
  "00:01:36": [
    { lang: "en", text: "Quick question before we move on." },
    { lang: "af", text: "Het ons al die finale" },
    { lang: "en", text: "forecast" },
    { lang: "af", text: "gekry van die" },
    { lang: "en", text: "finance team?" },
  ],
  "00:01:53": [
    { lang: "en", text: "OK," },
    { lang: "af", text: "dan kan ons miskien net die" },
    { lang: "en", text: "preliminary cycle" },
    { lang: "af", text: "deurloop, en dan" },
    { lang: "en", text: "return" },
    { lang: "af", text: "na die" },
    { lang: "en", text: "forecast" },
    { lang: "af", text: "as ons dit het." },
  ],
  "00:02:02": [
    { lang: "en", text: "Makes sense. So practically speaking," },
    { lang: "af", text: "wat verander aan ons" },
    { lang: "en", text: "go-to-market?" },
  ],
  "00:02:26": [
    { lang: "af", text: "Oraait. En het ons" },
    { lang: "en", text: "capacity" },
    { lang: "af", text: "op die" },
    { lang: "en", text: "delivery" },
    { lang: "af", text: "side, of moet ons eers nog mense aanstel?" },
  ],
};

// Speaker presentation
const SPEAKERS = {
  0: { label: "You", short: "Y", tone: "accent" },
  2: { label: "Speaker 2", short: "2", tone: "warm" },
  3: { label: "Speaker 3", short: "3", tone: "cool" },
};

function speakerColor(n) {
  // Returns a CSS expression that picks a tonally-consistent hue per speaker.
  if (n === 0) return "var(--accent)";
  if (n === 2) return "color-mix(in oklch, var(--accent) 60%, oklch(0.55 0.12 70))";
  if (n === 3) return "color-mix(in oklch, var(--accent) 60%, oklch(0.55 0.10 200))";
  return "var(--ink-3)";
}

Object.assign(window, { TRANSCRIPT, CODESWITCH_TOKENS, SPEAKERS, speakerColor });
