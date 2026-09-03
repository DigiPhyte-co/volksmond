/* Volksmond (SA-Live-Transcribe) browser UI.
 *
 * Vanilla JS, no framework, no CDN. Audio and transcripts are never uploaded; the
 * only network use is downloading the local models. The Volksmond design is rebuilt
 * here with the .vm design system in styles.css. Every screen is wired to the real
 * FastAPI endpoints; nothing is faked. Where the design showed a feature that is not
 * built yet (clean second pass, calendar in the start flow), it is left out rather
 * than mocked.
 */
"use strict";

// Where "Report a bug or request a feature" sends. Privacy-first mailto: the body
// carries the app version, the OS, and one line describing the hardware. Logs go in
// the diagnostics zip, which the USER attaches; transcripts never go anywhere.
var FEEDBACK_EMAIL = "volksmond@digiphyte.com";
// The business / licensing page: current pricing and what a Business licence covers.
// Personal use is free, so this is only ever a "learn or buy" link, opened in the browser.
var BUSINESS_PAGE_URL = "https://volksmond.digiphyte.com/business";
// Public Microsoft Store identity for the stable channel. Opening this URI is a
// user-initiated hand-off to the Store app; Volksmond itself makes no update request.
var STORE_PRODUCT_URI = "ms-windows-store://pdp/?ProductId=9P7BD97WTZ3W";
// Completed sessions before the one-time, dismissable business-use nudge. Local only; the
// count lives in settings.json on this machine and is never sent anywhere.
var SESSION_NUDGE_THRESHOLD = 10;

var APP = document.getElementById("app");
// CSRF token handed to the page by the server (app.py). Echoed on every
// state-changing request so a third-party web page can't drive this localhost
// server; the same-origin policy stops other pages from reading it.
var CSRF = (document.querySelector('meta[name="vm-csrf"]') || {}).content || "";

/* ── tiny DOM helper ──────────────────────────────────────── */
function el(tag, attrs, children) {
  var n = document.createElement(tag);
  if (attrs) {
    for (var k in attrs) {
      var v = attrs[k];
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "html") n.innerHTML = v;          // trusted static markup only (icons)
      else if (k === "text") n.textContent = tr(v);    // safe text, auto-translated
      else if (k === "placeholder") n.setAttribute("placeholder", tr(v));
      else if (k === "value") n.value = v;
      else if (k === "checked") n.checked = !!v;
      else if (k === "style" && typeof v === "object") Object.assign(n.style, v);
      else if (k.slice(0, 2) === "on" && typeof v === "function") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
  }
  append(n, children);
  return n;
}
function append(n, c) {
  if (c == null || c === false || c === true) return;
  if (Array.isArray(c)) { for (var i = 0; i < c.length; i++) append(n, c[i]); return; }
  if (c instanceof Node) { n.appendChild(c); return; }
  n.appendChild(document.createTextNode(tr(String(c))));
}
function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
// Wrap dynamic/user content (transcript text, names, paths) so it bypasses tr().
// el({text}) and string children auto-translate UI labels; data must never be
// "translated", even when it happens to equal a UI label like "Stop" or "Open".
function raw(s) { return document.createTextNode(s == null ? "" : String(s)); }

/* ── i18n (interface language) ────────────────────────────── */
// Translations live in i18n.js (window.VM_I18N), keyed by the English string.
// tr() only changes strings present in the active map, so most dynamic content
// is unaffected; pass genuinely dynamic values through raw() (above) to be safe.
var VM_AF = (window.VM_I18N && window.VM_I18N.af) || {};
var LANG = "en";
function afLang(s) { return (s && /^af/i.test(s.interface_language || "")) ? "af" : "en"; }
function tr(s) { return (LANG === "af" && VM_AF[s] != null) ? VM_AF[s] : s; }
// Translate a template that carries {named} placeholders: translate the WHOLE template first
// (so a language can reorder the words), then substitute the dynamic values verbatim. Keeps the
// i18n key stable ("{done} of {total} ({pct}%)") while the numbers stay untranslated.
function trFmt(tmpl, subs) {
  var s = tr(tmpl);
  if (subs) for (var k in subs) s = s.split("{" + k + "}").join(String(subs[k]));
  return s;
}
// Server notices can carry a dynamic tail (e.g. "Quiet audio boosted for transcription
// (+13.6 dB)") and combine with " · ", which exact-key tr() cannot match. Translate the
// fixed phrase per part and keep the dynamic values verbatim.
function trNotice(s) {
  return String(s == null ? "" : s).split(" · ").map(function (p) {
    var m = /^Quiet audio boosted for transcription \((.+)\)$/.exec(p);
    return m ? tr("Quiet audio boosted for transcription") + " (" + m[1] + ")" : tr(p);
  }).join(" · ");
}
// Make a non-button clickable element keyboard-operable (Enter/Space).
function keyActivate(fn) {
  return function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(e); } };
}

/* ── icons (static inline SVG, currentColor) ──────────────── */
var IP = {
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
  speaker: '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M16 9a4 4 0 0 1 0 6"/><path d="M19 6.5a8 8 0 0 1 0 11"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  check: '<path d="M5 12.5 10 17.5 19 6.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>',
  gear: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3.2 1.9"/>',
  sparkle: '<path d="M12 3l1.8 5.6L19.5 10.5l-5.7 1.9L12 18l-1.8-5.6L4.5 10.5l5.7-1.9z" fill="currentColor" stroke="none"/>',
  alert: '<path d="M12 3.5 2.5 20.5h19z"/><path d="M12 10v4.5M12 17.5h.01"/>',
  upload: '<path d="M12 15.5V4M7.5 8.5 12 4l4.5 4.5"/><path d="M5 15.5V19a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19v-3.5"/>',
  download: '<path d="M12 4v11.5M7.5 11 12 15.5l4.5-4.5"/><path d="M5 15.5V19a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19v-3.5"/>',
  disk: '<path d="M5 4h11l3 3v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/><path d="M8 4v5h7V4"/><rect x="8" y="13" width="8" height="6.5"/>',
  note: '<path d="M7 3.5h6.5L18 8v12.5a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z"/><path d="M13 3.5V8h5"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.6 2.6 14.4 0 17M12 3.5c-2.6 2.6-2.6 14.4 0 17"/>',
  key: '<circle cx="8" cy="12" r="3.6"/><path d="M11.6 12H21l-2 2 2 2-3 2"/>',
  crown: '<path d="M4 8l3.5 3.2L12 5l4.5 6.2L20 8l-1.5 10.5h-13z" fill="currentColor" stroke="none"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  chevDown: '<path d="M6 9l6 6 6-6"/>',
  chevRight: '<path d="M9 6l6 6-6 6"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor" stroke="none"/>',
  dot: '<circle cx="12" cy="12" r="6" fill="currentColor" stroke="none"/>',
  back: '<path d="M19 12H5M11 6l-6 6 6 6"/>',
  sun: '<circle cx="12" cy="12" r="4.4"/><path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5 5l1.6 1.6M17.4 17.4 19 19M19 5l-1.6 1.6M6.6 17.4 5 19"/>',
  moon: '<path d="M20 13.5A8 8 0 1 1 10.5 4 6.5 6.5 0 0 0 20 13.5z"/>',
  auto: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17z" fill="currentColor" stroke="none"/>',
  wifiOff: '<path d="M3 8.5a16 16 0 0 1 4-2.4M21 8.5a16 16 0 0 0-7.5-3.8M6.5 12.5a10 10 0 0 1 3-1.8M17.5 12.5a10 10 0 0 0-3.7-2.1M9.5 16a5 5 0 0 1 5 0"/><path d="M12 19.5h.01M3.5 3.5l17 17"/>',
  bug: '<rect x="8" y="8.5" width="8" height="11" rx="4"/><path d="M8 12.5H4M16 12.5h4M8 16H4M16 16h4M9 8.5 7.2 5.5M15 8.5l1.8-3M12 19.5v-9"/>',
  cpu: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v2M14 3v2M10 19v2M14 19v2M3 10h2M3 14h2M19 10h2M19 14h2"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
  ext: '<path d="M14 5h5v5M19 5l-7 7M12 5H6a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-6"/>',
  heart: '<path d="M12 20s-7-4.6-7-9.6A3.9 3.9 0 0 1 12 7a3.9 3.9 0 0 1 7 2.8C19 15.4 12 20 12 20z"/>',
  pencil: '<path d="M4 20l1.2-4.2L16 5a2 2 0 0 1 3 3L8.2 18.8z"/><path d="M14 7l3 3"/>',
  calendar: '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M4 9.5h16M8 3v4M16 3v4"/>',
  bell: '<path d="M6.5 10.5a5.5 5.5 0 0 1 11 0c0 4 1.5 5.5 1.5 5.5H5s1.5-1.5 1.5-5.5z"/><path d="M10 19a2 2 0 0 0 4 0"/>',
};
function icon(name, size) {
  size = size || 16;
  var inner = IP[name] || "";
  var span = document.createElement("span");
  span.style.display = "inline-flex";
  span.innerHTML = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    inner + '</svg>';
  return span;
}
function markSvg(size) {
  size = size || 22;
  var span = document.createElement("span");
  span.className = "mark";
  // Volksmond mark: five waveform bars over a smile. Real brand geometry (brand/),
  // strokes on currentColor so it inherits --accent (brand blue on light, light-blue on dark).
  span.innerHTML = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 1500 1500" fill="none" aria-hidden="true">' +
    '<g transform="matrix(1,0,0,1,0,272)">' +
    '<path stroke-linecap="round" transform="matrix(-1.186979,0,0,-1.186979,1416.494791,955.573618)" d="M 40.546292 283.673258 C 387.882847 -56.587826 735.222693 -56.558207 1082.562539 283.758821" stroke="currentColor" stroke-width="57"/>' +
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,463.342337,489.209138)" d="M 28.501117 28.500532 L 383.301472 28.500532" stroke="currentColor" stroke-width="57"/>' +
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,968.995486,489.209138)" d="M 28.501117 28.499854 L 383.301472 28.499854" stroke="currentColor" stroke-width="57"/>' +
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,1221.818952,358.298284)" d="M 28.500529 28.500488 L 162.727165 28.500488" stroke="currentColor" stroke-width="57"/>' +
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,716.16302,419.705936)" d="M 28.49842 28.500221 L 266.201362 28.500221" stroke="currentColor" stroke-width="57"/>' +
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,210.51271,358.298284)" d="M 28.500529 28.498507 L 162.727165 28.498507" stroke="currentColor" stroke-width="57"/>' +
    '</g></svg>';
  return span;
}

/* ── state ────────────────────────────────────────────────── */
function freshLive() {
  return {
    running: false, recording: false, transcribing: false, sourceKind: null,
    startedAt: null, outputPath: null, audioStem: null, tier: null, model: null, family: null,
    language: null, engine: null, stopping: false, segments: [], es: null, title: "", importName: "",
    micDevice: null, loopbackDevice: null, switching: false, reconfiguring: false,
    notes: "", notesOpen: false, notesTouched: false,
    // Live AEC toggle: rendered from the ENGINE'S confirmed state (/api/status, /api/aec-live),
    // never from stored settings, so it can never show a value the engine does not have.
    aecAvailable: false, aecActive: false, aecBusy: false, noticeShown: "",
    // Live mic gate, on the same terms as the AEC toggle above: rendered from the ENGINE'S
    // confirmed state (/api/status, /api/mic-gate), never from stored settings. Null until the
    // engine exists, which is what hides the control. Shape once set:
    // {on, mode: "normal"|"gentle"|"off", skipped, decoded}. micGateHintSeq is the last
    // safety-valve hint this client has already shown, so a reload never re-fires an old one.
    micGate: null, micGateBusy: false, micGateHintSeq: 0,
    // Outstanding long-silence warning from the server ({minutes, count, at}), or null.
    // Server-owned: the watcher lives there, so this is only ever a copy of /api/status.
    silenceNudge: null,
    // Outstanding "model struggling to keep up" nudge from the server
    // ({old_size, new_size, recording}), or null. Server-owned, like silenceNudge: set when a
    // CPU session auto-downgrades to a lighter model to stay live.
    struggleNudge: null,
    // System-audio capture health, mirrored from /api/status ('active'|'disabled'|'pending'|
    // 'permission_denied'|'failed'), or null before the first poll. Unlike the nudges above this
    // is a continuous capture-health signal, not a one-shot server event, so dismissing its
    // banner is purely local: sysAudioDismissedFor holds the value already dismissed, and the
    // banner returns if sys_state later changes to a different bad value.
    sysState: null, sysAudioDismissedFor: null,
    // Server-owned latched flag: true once recording is, or has ever been, active this session.
    // Latched (never clears on stop) so the record affordances stay hidden after a stop, which
    // prevents a stop-then-restart that would clobber the session WAV.
    recordingStarted: false,
    // t0-capture: capture (and recording, if on) start the instant Begin is clicked, while the
    // transcription model loads in the background. modelReady is false until the engine attaches;
    // prepareError carries a model-load failure message (null while healthy). Both mirror
    // /api/status, adopted by the readiness poll and the silence reconcile.
    modelReady: false, prepareError: null,
    // t0-capture prepare progress, mirrored from /api/status (preparing + the prepare object):
    // preparing = still building; prepare = { phase: downloading|loading|ready|error, model,
    // family, size, label, downloaded, total, stalled }; prepareStalledClient is set by the
    // client-side watchdog when the byte count has not moved for a long time.
    preparing: false, prepare: null, prepareStalledClient: false,
  };
}
var S = {
  route: "home", booted: false,
  settings: null, features: null, models: null, appInfo: null, license: null, devices: null,
  sessions: [], sessionsFolder: "", sessionsActive: null, sessionsSummarising: [],
  live: freshLive(),
  starting: { active: false, kind: null, title: "", error: null, startedAt: null },
  form: { title: "", language: "af", moreLang: null, tier: "auto", device: "auto", engine: "auto", participants: [], terms: [], context: null, record: false, aec: false, agcLive: true, stereoSplit: false, mic: null, loopback: null, advancedOpen: false },
  setup: { stage: "welcome", choice: "transcribe" },
  finish: { outputPath: null, title: "", summary: null, savedAs: null, summarising: false, recordingStem: null, sinkError: null },
  reader: { name: "", title: "", text: "", summarising: false, summary: null },
  reminder: null,   // active calendar reminder: {subject, attendees, start, key}, or null
  upgrade: { keyState: "empty", value: "", msg: "" },
  settingsDraft: null,
  theme: (function () { try { return localStorage.getItem("vm_theme") || "system"; } catch (e) { return "system"; } })(),
  stopMenuOpen: false,
  toast: null,
  warm: null,
};

// transient refs + timers (not part of render state)
var liveDocEl = null, liveBodyEl = null, elapsedEl = null, recTimerEl = null, returnPillTimeEl = null;
var pollTimer = null, elapsedTimer = null, toastTimer = null, levelTimer = null, warmTimer = null, histTimer = null, reminderTimer = null;
var silenceTimer = null;
var readinessTimer = null;   // t0-capture: polls /api/status for model_ready while a session prepares
var startingTimer = null, startingElapsedEl = null;

/* ── api ──────────────────────────────────────────────────── */
async function errOf(r) {
  try { var j = await r.json(); return new Error(j.detail || ("HTTP " + r.status)); }
  catch (e) { return new Error("HTTP " + r.status); }
}
var api = {
  get: async function (p) { var r = await fetch(p); if (!r.ok) throw await errOf(r); return r.json(); },
  post: async function (p, body) {
    var r = await fetch(p, {
      method: "POST",
      headers: Object.assign({ "X-Volksmond-CSRF": CSRF }, body ? { "Content-Type": "application/json" } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw await errOf(r); return r.json();
  },
  del: async function (p) { var r = await fetch(p, { method: "DELETE", headers: { "X-Volksmond-CSRF": CSRF } }); if (!r.ok) throw await errOf(r); return r.json(); },
  text: async function (p) { var r = await fetch(p); if (!r.ok) throw await errOf(r); return r.text(); },
};

/* ── helpers ──────────────────────────────────────────────── */
function fmtTs(t) {
  t = Math.max(0, Math.floor(t || 0));
  var m = Math.floor(t / 60), s = t % 60;
  return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}
function fmtElapsed(sinceIso) {
  if (!sinceIso) return "00:00";
  var secs = Math.max(0, Math.floor((Date.now() - new Date(sinceIso).getTime()) / 1000));
  var h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
  if (h > 0) return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
}
// m:ss from a plain seconds count (the model-load elapsed counter). Not fmtElapsed: that one takes
// an ISO start time, and the server owns the load clock here, not the browser.
function fmtSecs(secs) {
  var t = Math.max(0, Math.floor(secs || 0));
  return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0");
}
function fmtBytes(n) {
  if (!n) return ""; if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}
function baseName(p) { return (p || "").split(/[\\/]/).pop() || ""; }
function topicFromName(name) {
  var stem = name.replace(/\.md$/, "");
  // Mirrors the backend YYYY-MM-DD-HHMMSS (or older HHMM) prefix.
  var m = /^\d{4}-\d{2}-\d{2}-\d{4}(?:\d{2})?-(.*)$/.exec(stem);
  if (m) return (m[1] || "").replace(/-/g, " ") || "session";
  return stem;
}
function toast(msg, isErr) {
  S.toast = { msg: msg, err: !!isErr };
  render();
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { S.toast = null; render(); }, isErr ? 4200 : 2600);
}
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); toast("Copied to clipboard."); }
  catch (e) { toast("Could not copy.", true); }
}

/* ── theme ────────────────────────────────────────────────── */
function effectiveDark() {
  if (S.theme === "dark") return true;
  if (S.theme === "light") return false;
  return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}
function applyTheme() { APP.setAttribute("data-dark", effectiveDark() ? "true" : "false"); }
function setTheme(t) {
  S.theme = t;
  try { localStorage.setItem("vm_theme", t); } catch (e) {}
  applyTheme(); render();
}
if (window.matchMedia) {
  try {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
      if (S.theme === "system") applyTheme();
    });
  } catch (e) {}
}

/* ── navigation ───────────────────────────────────────────── */
// The live-family route for the CURRENT running session, so the sidebar and the
// return pill can navigate back to it instead of a fresh pre-meeting form.
function liveRoute() {
  if (S.live.sourceKind === "file") return "importing";
  return S.live.transcribing ? "live" : "recordonly";
}
function go(route) {
  if ((S.route === "live" || S.route === "importing" || S.route === "recordonly") &&
      route !== S.route && !S.live.running) {
    teardownLive();
  }
  // Stop polling warm-up status once we leave the pre-meeting screens.
  if (warmTimer && route !== "pre" && route !== "importpre") { clearInterval(warmTimer); warmTimer = null; }
  S.stopMenuOpen = false;
  S.route = route;
  // Reaching a pre-meeting screen is the signal that a transcription is imminent: warm the model.
  if (route === "pre" || route === "importpre") warmUp();
  // Always re-fetch the session list when opening History, so a just-finished or just-imported
  // transcript appears even if the finish-time refresh raced the transcript's write to disk.
  // While on History, poll lightly so in-progress states (recording / transcribing / summarising)
  // update live; leaving History stops the poll.
  if (route === "history") { refreshSessions(); startHistoryPoll(); } else { stopHistoryPoll(); }
  render();
}
function startHistoryPoll() {
  if (histTimer) return;
  histTimer = setInterval(function () {
    if (S.route !== "history") { stopHistoryPoll(); return; }
    refreshSessions();
  }, 2500);
}
function stopHistoryPoll() { if (histTimer) { clearInterval(histTimer); histTimer = null; } }
function teardownLive() {
  closeStream();
  stopLevels();
  stopSilencePoll();
  stopReadinessPoll();
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
  if (startingTimer) { clearInterval(startingTimer); startingTimer = null; }
}
function closeStream() { if (S.live.es) { try { S.live.es.close(); } catch (e) {} S.live.es = null; } }

/* ── SSE ──────────────────────────────────────────────────── */
function openStream() {
  closeStream();
  var es = new EventSource("/api/stream");
  es.onmessage = function (e) {
    try { var seg = JSON.parse(e.data); if (seg && seg.text) onSegment(seg); } catch (x) {}
  };
  es.onerror = function () { /* heartbeats keep it alive; ignore */ };
  S.live.es = es;
}
function onSegment(seg) {
  S.live.segments.push(seg);
  if ((S.route === "live" || S.route === "importing") && liveDocEl && liveDocEl.isConnected) {
    var empty = liveDocEl.querySelector(".empty");
    if (empty) empty.remove();
    liveDocEl.appendChild(segRow(seg));
    if (liveBodyEl) {
      var near = liveBodyEl.scrollHeight - liveBodyEl.scrollTop - liveBodyEl.clientHeight < 140;
      if (near) liveBodyEl.scrollTop = liveBodyEl.scrollHeight;
    }
  }
}
function segRow(seg) {
  var src = (seg.source || "").toUpperCase();
  // Stereo interview mode streams "Speaker L"/"Speaker R"; keep their case (no shouting) and
  // reuse the mic/sys colouring so the two sides stay visually distinct.
  var cls = (src === "MIC" || src === "SPEAKER L") ? "mic" : ((src === "SYS" || src === "SPEAKER R") ? "sys" : "file");
  var disp = (src === "SPEAKER L" || src === "SPEAKER R") ? (seg.source || "") : src;
  return el("div", { class: "row" }, [
    el("div", { class: "t", text: fmtTs(seg.t_start) }),
    el("div", {}, [el("span", { class: "src " + cls, text: "[" + disp + "]" }), raw(seg.text || "")]),
  ]);
}

/* ── status polling ───────────────────────────────────────── */
function pollStatus(predicateDone, onDone, onTick) {
  function tick() {
    fetch("/api/status").then(function (r) { return r.json(); }).then(function (st) {
      if (onTick) onTick(st);
      if (predicateDone(st)) { onDone(st); return; }
      pollTimer = setTimeout(tick, 1100);
    }).catch(function () { pollTimer = setTimeout(tick, 1400); });
  }
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(tick, 900);
}

/* ── audio levels + live device switch ────────────────────── */
// Poll the mic/system levels a few times a second and paint the meter bars in place
// (by id), so a steady meeting never triggers a full re-render just to move a meter.
function startLevels() {
  if (levelTimer) return;
  levelTimer = setInterval(function () {
    api.get("/api/levels").then(function (d) {
      if (!d || !d.running) return;
      updateMeter("vm-meter-mic", d.mic);
      updateMeter("vm-meter-sys", d.sys);
    }).catch(function () {});
  }, 250);
}
function stopLevels() { if (levelTimer) { clearInterval(levelTimer); levelTimer = null; } }
function updateMeter(id, lv) {
  var bar = document.getElementById(id);
  if (!bar || !lv) return;
  // peak 0..1 -> bar width. A small gain + sqrt lifts ordinary speech off the floor so the
  // meter is legible; a near-full peak tints the bar to warn of clipping.
  var peak = lv.peak || 0;
  var w = Math.max(0, Math.min(1, Math.sqrt(Math.min(1, peak * 1.3))));
  bar.style.width = Math.round(w * 100) + "%";
  bar.style.background = peak > 0.97 ? "var(--record)" : "var(--accent)";
}
async function switchDevice(which, value) {
  var prev = which === "mic" ? S.live.micDevice : S.live.loopbackDevice;
  if (which === "mic") S.live.micDevice = value; else S.live.loopbackDevice = value;
  S.live.switching = true; render();
  try {
    await api.post("/api/switch-device", { which: which, device: value });
    toast(which === "mic" ? "Microphone switched." : "System audio switched.");
  } catch (e) {
    if (which === "mic") S.live.micDevice = prev; else S.live.loopbackDevice = prev;
    toast(e.message || "Could not switch device.", true);
  } finally {
    S.live.switching = false; render();
  }
}
// Change the transcription language and/or model mid-meeting. A language-only patch keeps the
// loaded model (instant; the fix for a room that switches Afrikaans <-> English on a both-capable
// model); a tier/engine patch reloads it. The server resolves and confirms the running model.
async function reconfigureLive(patch, okMsg) {
  var prev = { language: S.live.language, model: S.live.model, family: S.live.family, tier: S.live.tier, engine: S.live.engine };
  if (patch.language != null) S.live.language = patch.language === "" ? "auto" : patch.language;
  if (patch.engine != null) S.live.engine = patch.engine;
  S.live.reconfiguring = true; render();
  try {
    var resp = await api.post("/api/reconfigure", patch);
    S.live.language = resp.language;
    if (resp.tier) S.live.tier = resp.tier;
    if (resp.model) S.live.model = resp.model;
    if (resp.family) S.live.family = resp.family;
    if (resp.engine) S.live.engine = resp.engine;
    toast(okMsg || "Updated.");
  } catch (e) {
    S.live.language = prev.language; S.live.model = prev.model; S.live.family = prev.family;
    S.live.tier = prev.tier; S.live.engine = prev.engine;
    toast(e.message || "Could not change the settings.", true);
  } finally {
    S.live.reconfiguring = false; render();
  }
}
// Pull the engine's ACTUAL live-AEC state into S.live (available + active). The in-meeting
// toggle renders only from this server-confirmed state, never from S.form or stored settings,
// so a long-running app instance can never show a value the engine does not have.
function refreshLiveAec() {
  api.get("/api/status").then(function (st) {
    if (!st || !st.running) return;
    S.live.aecAvailable = !!st.aec_live_available;
    S.live.aecActive = !!st.aec_live_active;
    // Same poll, same posture: the mic gate is engine-confirmed too, and this is the first read
    // of it after Begin (a hint cannot have fired yet, so adopt the sequence silently).
    adoptMicGate(st, true);
    if (S.route === "live" || S.route === "recordonly") render();
  }).catch(function () {});
}
// Toggle live echo cancellation mid-meeting. The UI reflects the CONFIRMED new state from the
// server response, not an optimistic flip; the server also persists the choice as the new
// default, so the pre-meeting toggle and disk stay in sync.
async function toggleLiveAec() {
  if (S.live.aecBusy) return;
  S.live.aecBusy = true; render();
  try {
    var resp = await api.post("/api/aec-live", { enabled: !S.live.aecActive });
    S.live.aecAvailable = !!resp.aec_live_available;
    S.live.aecActive = !!resp.aec_live_active;
    S.form.aecLive = S.live.aecActive;
    if (S.settings) S.settings.aec_live = S.live.aecActive;
    // The toggle itself worked either way; if the server could not save it as the new default
    // (persisted false), warn softly so the user knows the next meeting starts from the old value.
    if (resp.persisted === false) toast("Echo cancellation changed for this meeting, but the choice could not be saved as your default.");
    else toast(S.live.aecActive ? "Echo cancellation on." : "Echo cancellation off.");
  } catch (e) {
    toast(e.message || "Could not change echo cancellation.", true);
  } finally {
    S.live.aecBusy = false; render();
  }
}
// Compact in-meeting AEC control for the live audio strip. Hidden when the canceller never
// engaged this session (mic-only capture, or the binding is missing): the toggle would lie.
function liveAecToggle() {
  if (S.live.sourceKind !== "live" || !S.live.aecAvailable) return null;
  return el("div", { class: "row gap-6", style: { alignItems: "center", flex: "0 0 auto" },
    title: tr("Remove the other side's voice that your speakers leak into your microphone, live as the meeting happens. Best on speakers when you are mostly listening; it can blur your words during heavy crosstalk, and does nothing on headphones.") }, [
    el("span", { class: "ink-3", style: { fontSize: "11.5px" }, text: "Cancel echo live" }),
    S.live.aecBusy ? el("span", { class: "spinner sm" }) : toggleEl(!!S.live.aecActive, toggleLiveAec),
  ]);
}
// The safety valve's one-shot messages, keyed by the mode it stepped down to.
var MIC_GATE_HINTS = {
  gentle: "Your microphone is quiet, mic gate set to gentle",
  off: "Mic gate switched off for this meeting: your microphone is very quiet",
};
// Adopt /api/status's mic_gate object into S.live and surface any new safety-valve hint.
// Returns true when something the UI draws has changed, so pollers can render() once for all of
// their fields. `silent` (a page reload adopting the current status) takes the hint sequence
// WITHOUT toasting, so a hint that fired before the reload is never shown twice.
function adoptMicGate(st, silent) {
  var g = st && st.mic_gate;
  if (!g) return false;
  var was = S.live.micGate;
  var now = { on: !!g.on, mode: g.mode || "normal", skipped: g.skipped || 0, decoded: g.decoded || 0 };
  var changed = !was || was.on !== now.on || was.mode !== now.mode || was.skipped !== now.skipped;
  S.live.micGate = now;
  var seq = g.hint_seq || 0;
  if (seq > (S.live.micGateHintSeq || 0)) {
    S.live.micGateHintSeq = seq;
    if (!silent && MIC_GATE_HINTS[g.hint]) toast(MIC_GATE_HINTS[g.hint]);
  }
  return changed;
}
// Toggle the mic gate mid-meeting. Like the AEC toggle: the UI reflects the CONFIRMED new state
// from the server, not an optimistic flip, and the server persists the choice as the new default.
async function toggleLiveMicGate() {
  if (S.live.micGateBusy || !S.live.micGate) return;
  S.live.micGateBusy = true; render();
  var want = !S.live.micGate.on;
  try {
    var resp = await api.post("/api/mic-gate", { enabled: want });
    adoptMicGate({ mic_gate: resp }, true);
    if (S.settings) S.settings.mic_gate = S.live.micGate.on;
    if (resp.persisted === false) toast("Mic gate changed for this meeting, but the choice could not be saved as your default.");
    else toast(S.live.micGate.on ? "Mic gate on." : "Mic gate off.");
  } catch (e) {
    toast(e.message || "Could not change the mic gate.", true);
  } finally {
    S.live.micGateBusy = false; render();
  }
}
// Compact in-meeting mic-gate control for the live audio strip, with the running count of chunks
// it has skipped so the user can see it working. Hidden until the engine reports its state.
function liveMicGateToggle() {
  var g = S.live.micGate;
  if (!g) return null;
  var n = g.skipped || 0;
  return el("div", { class: "row gap-6", style: { alignItems: "center", flex: "0 0 auto" },
    title: tr("Skips microphone audio with no speech in it so the far end gets the CPU. Switch it off if it ever cuts you off.") }, [
    el("span", { class: "ink-3", style: { fontSize: "11.5px" }, text: "Mic gate" }),
    g.on && g.mode === "gentle" ? el("span", { class: "ink-3", style: { fontSize: "11px", opacity: ".8" }, text: "gentle" }) : null,
    S.live.micGateBusy ? el("span", { class: "spinner sm" }) : toggleEl(!!g.on, toggleLiveMicGate),
    // The count and its label are separate spans so the label alone is an i18n key: an exact-key
    // translation table cannot hold "18 quiet chunks skipped".
    n > 0 ? el("span", { class: "ink-3", style: { fontSize: "11px", whiteSpace: "nowrap" } }, [
      el("span", { text: String(n) }),
      el("span", { style: { marginLeft: "3px" }, text: n === 1 ? "quiet chunk skipped" : "quiet chunks skipped" }),
    ]) : null,
  ]);
}
// Compact strip for the live + record-only screens: per source, a dropdown to switch the
// device on the fly and a level meter. An empty device list degrades to "not detected".
function liveAudioStrip() {
  var dev = S.devices || {};
  function channel(which, ic, list, sel, defIdx, meterId) {
    var control;
    if (!list || !list.length) {
      control = el("span", { class: "row gap-6", style: { color: "var(--warn)", fontSize: "11.5px" } }, [icon("alert", 13), el("span", { text: "not detected" })]);
    } else {
      var s = el("select", {
        class: "field", style: { width: "auto", maxWidth: "190px", fontSize: "12px", padding: "5px 8px" },
        disabled: S.live.switching,
        onchange: function (e) { switchDevice(which, e.target.value); },
      }, list.map(function (d) { return el("option", { value: String(d.index) }, raw(d.name)); }));
      s.value = sel != null ? String(sel) : (defIdx != null ? String(defIdx) : String(list[0].index));
      control = s;
    }
    return el("div", { class: "row gap-8", style: { alignItems: "center", minWidth: "0", flex: "1 1 260px" } }, [
      el("span", { style: { color: "var(--ink-3)", display: "inline-flex", flex: "0 0 auto" } }, icon(ic, 15)),
      control,
      el("div", { style: { flex: "1 1 50px", minWidth: "44px", height: "6px", background: "var(--line)", borderRadius: "999px", overflow: "hidden" } },
        el("div", { id: meterId, style: { height: "100%", width: "0%", background: "var(--accent)", borderRadius: "999px", transition: "width .2s ease-out" } })),
    ]);
  }
  return el("div", { class: "row gap-16", style: { flexWrap: "wrap", padding: "10px 16px", borderBottom: "1px solid var(--line)", background: "var(--surface-2)" } }, [
    channel("mic", "mic", dev.mics, S.live.micDevice, dev.default_mic_index, "vm-meter-mic"),
    channel("loopback", "speaker", dev.loopbacks, S.live.loopbackDevice, dev.default_loopback_index, "vm-meter-sys"),
    liveAecToggle(),
    liveMicGateToggle(),
  ]);
}
// Compact strip on the live screen to change the LANGUAGE and MODEL mid-meeting. Language alone
// keeps the loaded model; Engine/Quality reload it. Quality is the user's to pick on GPU or CPU
// (Auto runs the best model for the chosen language).
function liveTuneStrip() {
  if (!S.live.transcribing || S.live.stopping) return null;
  var langVal = (S.live.language === "auto" || !S.live.language) ? "" : S.live.language;
  function tuneSelect(opts, value, fn) {
    var s = el("select", {
      class: "field", style: { width: "auto", maxWidth: "150px", fontSize: "12px", padding: "5px 8px" },
      disabled: S.live.reconfiguring, onchange: function (e) { fn(e.target.value); },
    }, opts.map(function (o) { return el("option", { value: o[0], text: o[1] }); }));
    s.value = value;
    return s;
  }
  function field(label, control) {
    return el("div", { class: "row gap-6", style: { alignItems: "center", minWidth: "0" } }, [
      el("span", { class: "ink-3", style: { fontSize: "11.5px", flex: "0 0 auto" }, text: label }), control,
    ]);
  }
  var items = [
    field("Language", tuneSelect(transcribeLangOpts(), langVal, function (v) { reconfigureLive({ language: v }, "Language switched."); })),
    field("Engine", tuneSelect([["auto", "Auto"], ["fluister", "Fluister"], ["whisper", "Whisper"]], S.live.engine || "auto", function (v) { reconfigureLive({ engine: v }, "Model switched."); })),
  ];
  // Honest Quality options for the live family: a size whose build is not on disk is flagged
  // "downloads first" (pre-translated, since a composite string is not an i18n key). Auto is left
  // plain here (no cached live pre-flight). Same readiness helpers as the pre-meeting picker.
  var liveFam = familyFor(S.live.language, S.live.engine);
  var qOpts = QUALITY_OPTS.map(function (o) {
    if (o[0] === "auto" || sizePresentInFamily(liveFam, o[0])) return o;
    return [o[0], tr(o[1]) + " · " + tr("downloads first")];
  });
  items.push(field("Quality", tuneSelect(qOpts, normalizeQuality(S.live.tier), function (v) { reconfigureLive({ tier: v }, "Model switched."); })));
  return el("div", { class: "row gap-16", style: { flexWrap: "wrap", padding: "8px 16px", borderBottom: "1px solid var(--line)", background: "var(--surface-2)", alignItems: "center" } }, items);
}

/* ── model warm-up (kill the first-use stall) ─────────────── */
// Loading the model the first time after launch can stall for minutes (network revalidation
// of an already-downloaded model, plus CUDA/AV cold start). We pre-load it in the background
// the moment the user reaches a pre-meeting screen, so Begin reuses a warm model.
// The pre-meeting screen reflects warm status ONLY through warmChip(), which reads S.warm.state.
// So re-render that screen ONLY when the state actually changes, never on every 1.5s poll tick.
// An unconditional tick re-render tore the whole DOM down (clear(APP) in render) every 1.5s while
// warming, recreating the title / name / jargon inputs under the user's cursor and dropping them
// mid-word. Same "only render when something moved" guard the History list uses for its search box.
var _warmSig = null;
function warmRender(st) {
  var sig = st ? st.state : null;
  if (sig === _warmSig) return;
  _warmSig = sig;
  if (S.route === "pre" || S.route === "importpre") render();
}
function warmUp() {
  api.post("/api/warm-up", { tier: S.form.tier || "auto", device: S.form.device || "auto", language: S.form.language || "", engine: S.form.engine || "auto" })
    .then(function (st) {
      S.warm = st;
      if (st && st.state === "warming") pollWarm();
      warmRender(st);
    }).catch(function () {});
}
function pollWarm() {
  if (warmTimer) return;
  warmTimer = setInterval(function () {
    api.get("/api/warm-up").then(function (st) {
      S.warm = st;
      if (!st || st.state !== "warming") { clearInterval(warmTimer); warmTimer = null; }
      warmRender(st);
    }).catch(function () { clearInterval(warmTimer); warmTimer = null; });
  }, 1500);
}
function warmChip() {
  var w = S.warm;
  if (!w) return null;
  if (w.state === "warming") return el("span", { class: "chip" }, [el("span", { class: "dot" }), el("span", { text: "Preparing transcription model" })]);
  if (w.state === "ready") return el("span", { class: "chip ok" }, [icon("check", 12), el("span", { text: "Transcription model ready" })]);
  return null;  // idle / busy / error: stay quiet, Begin still loads on demand
}

/* ── session lifecycle ────────────────────────────────────── */
// True while Begin is in flight (pre-flight probe or /api/start). Drives the Begin button's
// inline spinner so the pre-meeting screen stays put (sidebar + form visible): the live path
// NEVER shows the old full-screen "Starting" takeover, which is what tripped cert 10.1.2.10.
var _liveStarting = false;
// Begin: pre-flight the chosen model first. If it is already downloaded, go straight to t0.
// If it is not, open an informed-consent modal (name, size, rough time, or switch to a model
// already on disk) BEFORE any download starts. Consent (Proceed) is the effective t0.
async function startLive() {
  if (_liveStarting) return;
  _liveStarting = true; render();
  var pf = null;
  try {
    pf = await api.post("/api/preflight-model", {
      tier: S.form.tier, device: S.form.device, language: S.form.language, engine: S.form.engine,
    });
  } catch (e) { pf = null; }   // pre-flight unavailable: do not block Begin, fall through to start
  if (pf && pf.present === false) {
    _liveStarting = false; render();
    preStartModal(pf,
      function () { beginLive(); },                                  // Proceed and download (t0)
      function (alt) { applyModelAlternative(alt); beginLive(); });  // Use a model already on disk
    return;
  }
  beginLive();
}
// Adopt a downloaded alternative from the pre-start modal into the form, so Begin uses a model
// already on disk (no download). The engine is pinned to the alternative's ACTUAL family, never
// "auto": leaving it Auto let Begin re-resolve by language and pick (and download) a different
// family, so a "use the SA model already on disk" click could still trigger a download (P1-1).
function applyModelAlternative(alt) {
  if (!alt) return;
  S.form.tier = alt.size;
  if (alt.family === "fluister") S.form.engine = "fluister";
  else if (alt.family === "whisper") S.form.engine = "whisper";
  else if (alt.family === "swivuriso") S.form.engine = "swivuriso";
}
async function beginLive() {
  _liveStarting = true; render();
  var body = {
    topic: S.form.title || "",
    tier: S.form.tier, device: S.form.device, language: S.form.language, engine: S.form.engine,
    prompt: S.form.participants.concat(S.form.terms).join(", "),
    context_override: S.form.context,   // null -> server uses the saved default; a string overrides it for this run
    record: !!S.form.record, transcribe: true,
    mic_device: S.form.mic, loopback_device: S.form.loopback,
    aec_live: !!S.form.aecLive,
    agc_live: !!S.form.agcLive,
  };
  try {
    var resp = await api.post("/api/start", body);
    _liveStarting = false;
    S.form.context = null;   // the override applied to this run; the next meeting starts from the saved default again
    S.live = freshLive();
    S.live.running = true; S.live.transcribing = true; S.live.recording = !!resp.recording;
    S.live.recordingStarted = !!resp.recording;   // latch: a session that starts already recording counts as having recorded
    S.live.sourceKind = "live"; S.live.startedAt = new Date().toISOString();
    S.live.outputPath = resp.output_path; S.live.audioStem = resp.audio_stem;
    S.live.tier = resp.tier; S.live.model = resp.model; S.live.family = resp.family; S.live.language = resp.language;
    S.live.engine = S.form.engine;
    S.live.title = S.form.title || "Live meeting";
    S.live.micDevice = S.form.mic; S.live.loopbackDevice = S.form.loopback;
    // t0-capture: /api/start now returns the instant capture is live; the model may still be loading.
    // Go straight to the live screen (progress bar + "preparing" chip until ready) instead of holding
    // the user on a blocking spinner, and poll readiness until the transcript starts filling in.
    S.live.modelReady = !!resp.model_ready;
    S.live.preparing = !resp.model_ready;
    go("live"); openStream(); startElapsed(); startLevels(); refreshLiveAec(); startSilencePoll();
    if (!S.live.modelReady) startReadinessPoll();
  } catch (e) {
    // The blocking model-load stall is gone (the model builds in the background now, with its own
    // on-screen progress + Retry), so an immediate /api/start failure is a quick device/capture
    // problem: a toast is enough, and the pre-meeting screen stays put for another try.
    _liveStarting = false;
    toast(e.message || "Could not start.", true);
    render();
  }
}
// Informed-consent modal shown ONLY when the chosen model is not yet on disk. It never downloads
// anything itself: Proceed hands off to the normal Begin (the download then runs in the background
// with visible progress), or the user picks a model already downloaded, or cancels back to the
// picker. Reuses the shared .modal-backdrop / .modal markup.
function preStartModal(pf, onProceed, onUseAlt) {
  var alts = pf.downloaded_alternatives || [];
  var altList = alts.length ? el("div", { style: { marginTop: "16px" } }, [
    el("div", { class: "section-label", style: { marginBottom: "8px" }, text: "Start instantly with a model you already have" }),
    el("div", { class: "stack", style: { gap: "8px" } }, alts.map(function (alt) {
      return el("div", { class: "card", style: { padding: "12px 14px", display: "flex", gap: "12px", alignItems: "center" } }, [
        el("div", { class: "grow", style: { minWidth: "0" } }, [
          el("div", { style: { fontWeight: "600", fontSize: "13px" } }, [
            raw(alt.label || alt.model || alt.size), el("span", { class: "chip", style: { marginLeft: "8px" } }, raw(familyDisplay(alt.family))),
            el("span", { class: "chip", style: { marginLeft: "6px" } }, raw(fmtGB(alt.approx_bytes))),
          ]),
          alt.quality_note ? el("div", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "3px" } }, raw(alt.quality_note)) : null,
        ]),
        el("button", { class: "btn sm", onclick: function () { modal.remove(); onUseAlt(alt); } }, "Use this"),
      ]);
    })),
  ]) : null;

  var modal = el("div", { class: "modal-backdrop", onclick: function (e) { if (e.target === modal) modal.remove(); } }, [
    el("div", { class: "modal" }, [
      el("h2", {}, raw(trFmt("Download the {label} model first?", { label: pf.label || pf.model || "" }))),
      el("p", { class: "ink-2", style: { margin: "10px 0 4px", fontSize: "13px" } }, [
        raw((pf.label || pf.model || "") + "  ·  " + familyDisplay(pf.family) + "  ·  "),
        el("span", { text: trFmt("About {size}, and usually a few minutes on a normal connection.", { size: fmtGB(pf.approx_bytes) }) }),
      ]),
      el("p", { class: "ink-3", style: { margin: "0", fontSize: "12px" }, text: "Capture and recording begin immediately. The transcript fills in from the start once the model is ready, and if you are recording, the audio is saved from the very beginning." }),
      altList,
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end", marginTop: "18px" } }, [
        el("button", { class: "btn ghost", onclick: function () { modal.remove(); } }, "Cancel"),
        el("button", { class: "btn primary", onclick: function () { modal.remove(); onProceed(); } }, [icon("download", 14), "Proceed and download"]),
      ]),
    ]),
  ]);
  APP.appendChild(modal);
}
async function startRecordOnly() {
  try {
    var resp = await api.post("/api/start", { topic: S.form.title || "", transcribe: false, record: true, mic_device: S.form.mic, loopback_device: S.form.loopback });
    S.live = freshLive();
    S.live.running = true; S.live.recording = true; S.live.transcribing = false;
    S.live.sourceKind = "live"; S.live.startedAt = new Date().toISOString();
    S.live.outputPath = resp.output_path; S.live.audioStem = resp.audio_stem;
    S.live.title = S.form.title || "Recording";
    S.live.micDevice = S.form.mic; S.live.loopbackDevice = S.form.loopback;
    go("recordonly"); startElapsed(); startLevels(); refreshLiveAec();
  } catch (e) { toast(e.message || "Could not start recording.", true); }
}

/* ── pre-record (name a record-only session before it starts) ─ */
function recordPreView() {
  var dev = S.devices || {};
  return el("div", { class: "screen" }, el("div", { class: "screen-inner col-mid" }, [
    el("div", { class: "screen-head" }, el("div", {}, [
      el("div", { class: "eyebrow", text: "Record only" }),
      el("h1", { text: "Name this recording" }),
      el("p", { class: "sub", text: "Volksmond records the audio cleanly on this computer. No transcript is made while recording. You can transcribe it later." }),
    ])),
    el("div", { class: "stack", style: { gap: "16px" } }, [
      formField("Recording name", el("span", { class: "label-muted", text: " (optional)" }),
        el("input", { class: "field tall", value: S.form.title, placeholder: "e.g. Toets met Jonathan", oninput: function (e) { S.form.title = e.target.value; } })),
      el("div", { class: "card", style: { padding: "16px" } }, [
        el("div", { class: "section-label", style: { marginBottom: "10px" }, text: "Audio sources" }),
        deviceField("Your microphone", dev.mics, S.form.mic, dev.default_mic_index, function (v) { S.form.mic = v; }),
        deviceField("System audio (everyone else)", dev.loopbacks, S.form.loopback, dev.default_loopback_index, function (v) { S.form.loopback = v; }),
      ]),
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end" } }, [
        el("button", { class: "btn ghost", onclick: function () { go("home"); } }, "Back"),
        el("button", { class: "btn primary tall", onclick: startRecordOnly }, [icon("dot", 14), "Start recording"]),
      ]),
    ]),
  ]));
}
async function startImport(arg) {
  var body = { topic: arg.topic || "", tier: S.form.tier, device: S.form.device, language: S.form.language, engine: S.form.engine, aec: !!S.form.aec, prompt: (S.form.participants || []).concat(S.form.terms || []).join(", "), context_override: S.form.context };
  if (arg.path) { body.paths = [arg.path]; body.stereo_split = !!S.form.stereoSplit; }
  if (arg.stem) body.stem = arg.stem;
  beginStarting("file", arg.topic || S.importName || "Recording");
  try {
    var resp = await api.post("/api/transcribe-file", body);
    endStarting();
    S.form.context = null;   // per-run override consumed; the next import starts from the saved default again
    S.live = freshLive();
    S.live.running = true; S.live.transcribing = true; S.live.sourceKind = "file";
    S.live.startedAt = new Date().toISOString();
    S.live.outputPath = resp.output_path; S.live.tier = resp.tier; S.live.model = resp.model; S.live.family = resp.family;
    S.live.importName = baseName(arg.path) || (arg.topic || "recording");
    S.live.title = arg.topic || topicFromName(baseName(resp.output_path));
    go("importing"); openStream(); startElapsed();
    // Surface a non-fatal server notice (e.g. "stereo requested but the file is mono") once,
    // whether it lands mid-run or only on the final poll.
    var showNotice = function (st) {
      if (st && st.notice && S.live.noticeShown !== st.notice) { S.live.noticeShown = st.notice; toast(trNotice(st.notice)); }
    };
    pollStatus(function (st) { return !st.running; },
      function (st) { showNotice(st); gotoFinish(S.live.outputPath); },
      showNotice);
  } catch (e) {
    if (startingTimer) { clearInterval(startingTimer); startingTimer = null; }
    if (S.route === "starting") { S.starting.error = e.message || "Could not start transcription."; render(); }
    else { toast(e.message || "Could not start transcription.", true); }
  }
}
function startElapsed() {
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = setInterval(function () {
    if (elapsedEl) elapsedEl.textContent = fmtElapsed(S.live.startedAt);
    if (recTimerEl) recTimerEl.textContent = fmtElapsed(S.live.startedAt);
    if (returnPillTimeEl) returnPillTimeEl.textContent = fmtElapsed(S.live.startedAt);
  }, 1000);
}
async function pickFile(kind) {
  // In the desktop shell use pywebview's native dialog; in the browser build hit
  // /api/pick (server-side tkinter); fall back to a paste-a-path modal.
  if (inDesktop() && window.pywebview.api.pick_path) {
    try { var pd = await window.pywebview.api.pick_path(kind || "file"); return pd || null; }
    catch (e) { /* fall through to the server picker */ }
  }
  try {
    var r = await api.post("/api/pick?kind=" + (kind || "file"));
    return r.path || null;
  } catch (e) {
    return await pastePathModal(kind);
  }
}
function pastePathModal(kind) {
  return new Promise(function (resolve) {
    var input = el("input", { class: "field", placeholder: kind === "folder" ? "C:\\path\\to\\folder" : "C:\\path\\to\\recording.mp4",
      onkeydown: function (e) { if (e.key === "Enter") { close(input.value.trim() || null); } else if (e.key === "Escape") { close(null); } } });
    var modal = el("div", { class: "modal-backdrop", onclick: function (e) { if (e.target === modal) { close(null); } } }, [
      el("div", { class: "modal" }, [
        el("h2", { text: kind === "folder" ? "Type a folder path" : "Type a file path" }),
        el("p", { class: "ink-3", style: { margin: "8px 0 14px", fontSize: "13px" }, text: "No native file dialog is available here. Paste the full path on this computer." }),
        input,
        el("div", { class: "row gap-8", style: { marginTop: "16px", justifyContent: "flex-end" } }, [
          el("button", { class: "btn ghost", onclick: function () { close(null); } }, "Cancel"),
          el("button", { class: "btn primary", onclick: function () { close(input.value.trim() || null); } }, "Use this path"),
        ]),
      ]),
    ]);
    function close(v) { modal.remove(); resolve(v); }
    APP.appendChild(modal); input.focus();
  });
}
/* ── confirm modal (used for destructive actions like removing a model) ─── */
function confirmModal(opts) {
  var modal = el("div", { class: "modal-backdrop", onclick: function (e) { if (e.target === modal) modal.remove(); } }, [
    el("div", { class: "modal" }, [
      el("h2", { text: opts.title || "Are you sure?" }),
      opts.message ? el("p", { class: "ink-2", style: { margin: "8px 0 4px", fontSize: "13px" }, text: opts.message }) : null,
      opts.detail ? el("p", { class: "ink-3 mono", style: { margin: "0 0 4px", fontSize: "12.5px" } }, raw(opts.detail)) : null,
      opts.body ? el("div", { style: { marginTop: "10px" } }, opts.body) : null,
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end", marginTop: "16px" } }, [
        el("button", { class: "btn ghost", onclick: function () { modal.remove(); } }, "Cancel"),
        el("button", { class: "btn " + (opts.danger ? "record" : "primary"), onclick: function () { modal.remove(); if (opts.onConfirm) opts.onConfirm(); } }, opts.confirmLabel || "Confirm"),
      ]),
    ]),
  ]);
  APP.appendChild(modal);
}
function confirmRemoveVoice(m) {
  var meta = VOICE_LABELS[m.model] || { title: m.model };
  confirmRemoveVoiceItem(meta.title, m.model, m.size_on_disk || m.approx_bytes);
}
// Remove any voice model by its delete id (a Whisper size, or a Fluister/Swivuriso repo id).
function confirmRemoveVoiceItem(label, id, bytes) {
  confirmModal({
    title: "Remove this model?",
    message: "Remove this transcription model from your computer? You can download it again later.",
    detail: label + "   " + fmtGB(bytes),
    confirmLabel: "Remove", danger: true,
    onConfirm: function () {
      api.post("/api/voice-model/delete", { model: id })
        .then(function () { toast("Model removed."); loadVoiceModels(); })
        .catch(function (e) { toast(e.message || "Could not remove.", true); });
    },
  });
}
function confirmRemoveSummary(m) {
  var meta = SUMMARY_LABELS[m.key] || { title: (m.params || "") + " model" };
  confirmModal({
    title: "Remove this model?",
    message: "Remove this summary model from your computer? You can download it again later.",
    detail: meta.title + "   " + fmtGB(m.size_on_disk || m.approx_bytes),
    confirmLabel: "Remove", danger: true,
    onConfirm: function () {
      api.post("/api/summary-model/delete", { key: m.key })
        .then(function () {
          toast("Model removed."); loadSummaryModels();
          api.get("/api/models").then(function (mm) { S.models = mm; render(); }).catch(function () {});
        })
        .catch(function (e) { toast(e.message || "Could not remove.", true); });
    },
  });
}
async function importFromPicker() {
  var p = await pickFile("file");
  if (!p) return;
  // Go through the context screen first (title, language, names and jargon),
  // then transcribe - same as a live meeting gets its pre-meeting setup.
  S.importPath = p; S.importStem = null; S.importName = baseName(p);
  S.form.title = ""; S.form.participants = []; S.form.terms = []; S.form.context = null;
  S.form.stereoSplit = false;   // per-file choice, never carried from a previous upload
  go("importpre");
}

async function doStop(what) {
  S.stopMenuOpen = false;
  try {
    var resp = await api.post("/api/stop?what=" + what);
    if (what === "recording") {
      S.live.recording = false; toast("Recording stopped. Transcript continues."); render(); return;
    }
    if (what === "transcription") {
      S.live.transcribing = false; S.live.stopping = true; render();
      pollStatus(
        function (st) { return !st.running || (!st.stopping && !st.transcribing); },
        function (st) {
          S.live.stopping = false;
          if (!st.running) { gotoFinish(resp.output_path, st && st.sink_error); }
          else { S.live.recording = true; go("recordonly"); }
        }
      );
      return;
    }
    // what === "all"
    S.live.stopping = true; render();
    pollStatus(
      function (st) { return !st.running; },
      function (st) { gotoFinish(resp.output_path, st && st.sink_error); },
      function (st) {
        if (st.running && st.stopping && elapsedEl) {
          var n = typeof st.pending === "number" ? st.pending : 0;
          var node = document.getElementById("live-status-text");
          if (node) node.textContent = n > 0 ? ("Finishing, " + n + " chunk" + (n === 1 ? "" : "s") + " left") : "Finishing";
        }
      }
    );
  } catch (e) { toast(e.message || "Could not stop.", true); }
}
function gotoFinish(outputPath, sinkError) {
  saveNotesNow();                                  // flush any pending notes for this session
  var notesText = (S.live.notes || "").trim();
  teardownLive();
  S.finish.outputPath = outputPath || S.live.outputPath;
  S.finish.title = S.live.title || topicFromName(baseName(S.finish.outputPath));
  S.finish.recordingStem = S.live.recordingStarted ? S.live.audioStem : null;   // latch, not the live flag: a recording that was stopped mid-session still has a file to surface
  S.finish.summary = null; S.finish.savedAs = null; S.finish.summarising = false;
  S.finish.sinkError = sinkError || null;
  S.finish.notes = notesText; S.finish.hasNotes = !!notesText; S.finish.includeNotes = true;
  S.live.running = false;
  refreshSessions();
  // The server bumped session_count as this session finalised; refresh settings so the one-time
  // business nudge can appear on the home screen once the threshold is reached.
  api.get("/api/settings").then(function (s) { if (s) S.settings = s; }).catch(function () {});
  if (S.finish.sinkError) toast(S.finish.sinkError, true);
  go("finish");
}
async function stopRecordOnly() {
  try {
    var resp = await api.post("/api/stop?what=all");
    var stem = S.live.audioStem;
    S.live.stopping = true; render();
    pollStatus(function (st) { return !st.running; }, function (st) {
      teardownLive();
      S.live.running = false; S.live.stopping = false;
      S.finish.recordingStem = stem;
      S.finish.outputPath = resp.output_path;
      S.finish.sinkError = (st && st.sink_error) || null;
      if (S.finish.sinkError) toast(S.finish.sinkError, true);
      go("recordonly"); // now renders the "stopped" handoff
    });
  } catch (e) { toast(e.message || "Could not stop recording.", true); }
}

/* ── summarise ────────────────────────────────────────────── */
// Summary styles. "Standard" sends no instruction, so the server uses its own
// meeting-minutes default. The rest send an explicit instruction; the server still
// adds the transcript-cleanup guidance and the output-language directive on top, so
// these only describe the SHAPE of the summary. "Custom" reveals a free-text box.
// The prompt text stays English regardless of output language (the server handles
// translation); only the names are translated for the UI.
var SUMMARY_STYLES = [
  { id: "standard", name: "Standard (meeting minutes)", prompt: "" },
  { id: "actions", name: "Action items only",
    prompt: "From this transcript, list only the action items: who needs to do what, with any due dates or deadlines mentioned. Use a short bulleted list, grouped by person where it is clear. If there are no clear action items, say so plainly." },
  { id: "decisions", name: "Decisions and owners",
    prompt: "Summarise only the decisions taken in this meeting and who owns each resulting follow-up. Use a concise bulleted list. Note anything that was explicitly left undecided." },
  { id: "detailed", name: "Detailed notes",
    prompt: "Write thorough, well-structured notes from this transcript: a short context line, the main topics discussed in the order they came up, the decisions, the action items, and any open questions. Use headings and bullet points." },
  { id: "tldr", name: "One-paragraph summary",
    prompt: "Summarise this meeting in a single short paragraph of three or four sentences, capturing the gist and the single most important outcome. No headings and no bullet points." },
  { id: "custom", name: "Custom instructions", prompt: null },
];
// Resolve the instruction to send for the target's chosen style. "" means "let the
// server use its default" (standard); a custom style with an empty box is treated the
// same, so an unfinished custom choice never produces a confusing empty-instruction run.
function summaryInstructionFor(target) {
  var id = target.summaryStyle || "standard";
  if (id === "custom") return (target.customInstruction || "").trim();
  var st = SUMMARY_STYLES.filter(function (s) { return s.id === id; })[0];
  return (st && st.prompt) || "";
}
async function doSummarise(fileName, scope) {
  var target = scope === "reader" ? S.reader : S.finish;
  target.summarising = true; render();
  try {
    var body = { file: fileName, language: target.sumLang || "en" };
    var instruction = summaryInstructionFor(target);
    if (instruction) body.instruction = instruction;
    if (target.hasNotes && target.includeNotes !== false) body.include_notes = true;
    // The summary now runs as a server-side job (so it survives navigating away and shows up
    // in History). POST starts it; poll /api/summary-status for the result.
    await api.post("/api/summarise", body);
    pollSummary(fileName, scope);
  } catch (e) {
    target.summarising = false; render();
    toast(e.message || "Summarise failed.", true);
  }
}
function pollSummary(fileName, scope) {
  function tick() {
    api.get("/api/summary-status?file=" + encodeURIComponent(fileName)).then(function (j) {
      var target = scope === "reader" ? S.reader : S.finish;
      // The reader may have moved to a different transcript while a summary ran; only apply the
      // result if this scope is still looking at the same file.
      if (scope === "reader" && S.reader.name !== fileName) return;
      if (j.state === "running") { setTimeout(tick, 1500); return; }
      if (j.state === "done") {
        target.summary = j.summary || ""; target.savedAs = j.saved; target.summarising = false;
        if (scope === "reader") S.reader.tab = "summary";
        render();
      } else if (j.state === "error") {
        target.summarising = false; render();
        toast(j.error || "Summarise failed.", true);
      } else {           // idle: the job is gone (e.g. the app restarted); stop the spinner
        target.summarising = false; render();
      }
    }).catch(function () { setTimeout(tick, 2000); });
  }
  setTimeout(tick, 1200);
}
// The style picker (a dropdown, plus a textarea when "Custom" is chosen). Shared by the
// pre-summarise card and the regenerate controls so the two never drift.
function summaryStyleControl(target) {
  var id = target.summaryStyle || "standard";
  var rows = [
    el("div", { class: "row gap-8", style: { alignItems: "center", flexWrap: "wrap" } }, [
      el("span", { class: "ink-3", style: { fontSize: "11.5px" }, text: "Summary style" }),
      selectEl(SUMMARY_STYLES.map(function (s) { return [s.id, s.name]; }), id, function (v) {
        target.summaryStyle = v; render();
      }),
    ]),
  ];
  if (id === "custom") {
    rows.push(el("textarea", {
      class: "field", style: { marginTop: "8px", minHeight: "72px" },
      value: target.customInstruction || "",
      placeholder: "Describe the summary you want. e.g. A bulleted list of risks raised, each with who raised it. Write in the second person to the team.",
      oninput: function (e) { target.customInstruction = e.target.value; },
    }));
  }
  return el("div", { class: "stack", style: { gap: "0" } }, rows);
}
function renderMarkdown(md) {
  var frag = document.createDocumentFragment();
  var lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
  var ul = null;
  function inline(text) {
    var parts = text.split(/(\*\*[^*]+\*\*)/g), out = [];
    for (var i = 0; i < parts.length; i++) {
      var m = /^\*\*([^*]+)\*\*$/.exec(parts[i]);
      if (m) out.push(el("strong", {}, raw(m[1])));
      else if (parts[i]) out.push(document.createTextNode(parts[i]));
    }
    return out;
  }
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].replace(/\s+$/, "");
    var h = /^(#{1,4})\s+(.*)$/.exec(line);
    var b = /^\s*[-*]\s+(.*)$/.exec(line);
    var num = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (b || num) {
      if (!ul) { ul = el("ul"); frag.appendChild(ul); }
      ul.appendChild(el("li", {}, inline((b || num)[1])));
      continue;
    }
    ul = null;
    if (h) { frag.appendChild(el("h" + h[1].length, {}, inline(h[2]))); continue; }
    if (line.trim() === "") continue;
    frag.appendChild(el("p", {}, inline(line)));
  }
  return frag;
}

/* ── data loads ───────────────────────────────────────────── */
var _sessionsSig = "";
async function refreshSessions() {
  try {
    var d = await api.get("/api/sessions");
    S.sessions = d.files || []; S.sessionsFolder = d.folder || "";
    S.sessionsActive = d.active || null; S.sessionsSummarising = d.summarising || [];
    // Only re-render when the list or its in-progress state actually changed, so the History
    // poll never steals focus from the search box mid-type when nothing has moved.
    var sig = JSON.stringify([S.sessions, S.sessionsActive, S.sessionsSummarising]);
    if (S.route === "history" && sig !== _sessionsSig) render();
    _sessionsSig = sig;
  } catch (e) { /* ignore */ }
}
async function refreshSideData() {
  try { S.models = await api.get("/api/models"); } catch (e) {}
  try { S.settings = await api.get("/api/settings"); } catch (e) {}
}

/* ── settings persistence ─────────────────────────────────── */
async function saveSettings(patch) {
  try {
    S.settings = await api.post("/api/settings", patch);
    LANG = afLang(S.settings);
    S.settingsDraft = null;
    toast("Saved.");
    render();
  } catch (e) { toast(e.message || "Could not save.", true); }
}

/* ── licence ──────────────────────────────────────────────── */
async function activateLicence() {
  var key = (S.upgrade.value || "").trim();
  if (!key) { return; }
  try {
    var st = await api.post("/api/license", { key: key });
    S.license = st;
    if (st.status === "ok") { S.upgrade.keyState = "success"; S.upgrade.msg = ""; toast("Activated. Thank you."); }
    else if (st.status === "version_exceeded") { S.upgrade.keyState = "error-version"; }
    else if (st.status === "expired") { S.upgrade.keyState = "error-bad"; S.upgrade.msg = "This licence has expired."; }
    else { S.upgrade.keyState = "error-bad"; S.upgrade.msg = ""; }
    render();
  } catch (e) {
    S.upgrade.keyState = "error-bad"; S.upgrade.msg = e.message || ""; render();
  }
}
async function deactivateLicence() {
  try { S.license = await api.del("/api/license"); toast("Deactivated on this computer."); render(); }
  catch (e) { toast(e.message || "Could not deactivate.", true); }
}
function isPro() { return S.license && S.license.status === "ok" && S.license.tier === "pro"; }
function summaryInstalled() { return S.models && S.models.summary_installed; }

/* ── desktop bridge + bug report ──────────────────────────── */
// In the native pywebview shell, window.pywebview.api is injected after load.
// Use it to open external links and native pickers; the browser build falls back.
function inDesktop() { return !!(window.pywebview && window.pywebview.api); }
function connected() { return !!(S.appInfo && S.appInfo.connected); }  // online (paid) edition; the offline build hides online-feature UI
function offlineBuild() { return !!(S.appInfo && S.appInfo.offline); }  // the airtight offline-only edition: even the model-update check and calendar are compiled out
function storeBuild() { return !!(S.appInfo && S.appInfo.store); }  // the Microsoft Store (MSIX) edition: ONLY the app-update check is compiled out (the Store owns updates); model updates and calendar stay
function openExternal(url) {
  // Native shell: hand it to the OS (system browser / mail client).
  if (inDesktop() && window.pywebview.api.open_external) { window.pywebview.api.open_external(url); return; }
  // Browser: mailto via location (page stays); web links in a new tab (never navigate
  // the app away).
  if (url.slice(0, 7) === "mailto:") { window.location.href = url; }
  else { window.open(url, "_blank", "noopener"); }
}
function openStoreListing() { openExternal(STORE_PRODUCT_URI); }
// Ask the server to write the diagnostics zip. Returns the response ({path, folder,
// name}) or null on failure; the caller decides what to say. Nothing is uploaded: the
// file is written to this machine's Downloads folder and only ever travels if the user
// attaches it to an email themselves.
async function saveDiagnostics(quiet) {
  try {
    var r = await api.post("/api/diagnostics");
    if (!quiet) toast("Diagnostics saved to " + r.path);
    return r;
  } catch (e) {
    if (!quiet) toast(e.message || "Could not save diagnostics.", true);
    return null;
  }
}
// The email body. mailto: cannot attach a file and Outlook truncates a long body, so
// this stays short: the version line, one machine line (CPU, cores, RAM, GPU, install
// kind) and where the user can find the zip to attach.
function feedbackBody(af, version, plat, machine, diagPath) {
  return (af ? "Beskryf die fout of die funksie wat jy graag wil hê:" : "Describe the bug or the feature you would like:") +
    "\n\n\n----------------------------------------\n" +
    "Volksmond version " + version + "\n" +
    (machine ? machine + "\n" : "") + plat + "\n" +
    (diagPath
      ? (af ? "Heg asseblief hierdie lêer aan: " : "Please attach this file: ") + diagPath
      : (af ? "Heg asseblief die diagnostiese lêer aan (Stoor diagnostiek in Volksmond, dan kyk in Aflaaie)."
            : "Please attach the diagnostics file (use Save diagnostics in Volksmond, then look in Downloads).")) + "\n";
}
function reportBug() {
  // No phone-home: the app never sends anything. It either hands a prefilled draft to
  // the user's default mail app (mailto), or copies a report to the clipboard for them
  // to paste into webmail. The send always happens outside the app, and the diagnostics
  // zip is only ever attached by the user.
  var info = S.appInfo || {};
  var version = info.version || "?", plat = info.platform || "?";
  var af = LANG === "af";
  var subject = "Volksmond feedback (v" + version + ")";
  var machine = "", diagPath = "";
  function body() { return feedbackBody(af, version, plat, machine, diagPath); }
  function mailtoUrl() {
    return "mailto:" + FEEDBACK_EMAIL + "?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(body());
  }
  function reportText() { return "To: " + FEEDBACK_EMAIL + "\nSubject: " + subject + "\n\n" + body(); }

  var pathLine = el("div", { class: "mono", style: { fontSize: "11.5px", wordBreak: "break-all", marginTop: "4px" } },
    raw(af ? "Nog nie gestoor nie." : "Not saved yet."));
  var openBtn = el("button", { class: "btn primary", onclick: async function () {
    openBtn.disabled = true;
    var r = await saveDiagnostics(true);
    openBtn.disabled = false;
    if (r) { diagPath = r.path; clear(pathLine); append(pathLine, raw(r.path)); }
    else { toast("Could not save diagnostics. Sending the report without it.", true); }
    openExternal(mailtoUrl());
    modal.remove();
  } }, [icon("note", 14), "Save diagnostics and open email"]);

  var modal = el("div", { class: "modal-backdrop", onclick: function (e) { if (e.target === modal) modal.remove(); } }, [
    el("div", { class: "modal" }, [
      el("h2", { text: "Report a bug or idea" }),
      el("p", { class: "ink-3", style: { margin: "8px 0 14px", fontSize: "13px" }, text: "Nothing is sent automatically. The app never phones home, you send this yourself." }),
      el("div", { style: { display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", background: "var(--surface-2)", borderRadius: "8px", marginBottom: "12px" } }, [
        el("span", { style: { color: "var(--ink-3)", display: "inline-flex" } }, icon("bug", 16)),
        el("div", {}, [
          el("div", { style: { fontSize: "12px", color: "var(--ink-3)" }, text: "Send it to" }),
          el("div", { class: "mono", style: { fontSize: "13.5px" }, text: FEEDBACK_EMAIL }),
        ]),
      ]),
      el("div", { style: { padding: "10px 12px", background: "var(--surface-2)", borderRadius: "8px", marginBottom: "16px" } }, [
        el("div", { style: { fontSize: "12.5px" }, text: "We save a small diagnostics file to your Downloads folder. Please attach it to the email: it is what lets us tell you what went wrong." }),
        el("div", { class: "s", style: { fontSize: "11.5px", marginTop: "6px" }, text: "It holds the app logs, your settings, and what this computer is. No transcripts, no notes, no audio, no licence key." }),
        pathLine,
      ]),
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end", flexWrap: "wrap" } }, [
        el("button", { class: "btn ghost", onclick: function () { modal.remove(); } }, "Close"),
        el("button", { class: "btn ghost", onclick: function () { copyText(reportText()); } }, [icon("copy", 14), "Copy report"]),
        openBtn,
      ]),
    ]),
  ]);
  APP.appendChild(modal);
  // The machine line needs a GPU probe (a subprocess), so it is fetched when the modal
  // opens rather than on every app load. If it never arrives the body simply omits it.
  api.get("/api/diagnostics").then(function (d) { machine = d.summary || ""; }).catch(function () {});
}

/* ═══════════════════════════════════════════════════════════
 * RENDER
 * ═══════════════════════════════════════════════════════════ */
var _renderedRoute = null;
// Keyboard focus + caret survive a re-render. render() rebuilds the entire DOM (clear(APP)
// below), so any re-render that lands while the user is typing — a background poll, a toast, a
// calendar reminder — would otherwise recreate the focused <input>/<textarea> as a new, unfocused
// element and drop the user mid-word. Record which field held focus (by its index path in the
// tree) and its caret range, then restore both onto the freshly built field of the same shape.
// A structural mismatch (different tag or placeholder at that path) just skips restoration, so we
// never focus the wrong box. Only same-route re-renders restore; a route change resets focus.
function captureFocus() {
  var a = document.activeElement;
  if (!a || (a.tagName !== "INPUT" && a.tagName !== "TEXTAREA") || !APP.contains(a)) return null;
  var path = [];
  for (var node = a; node && node !== APP; node = node.parentNode) {
    var p = node.parentNode;
    if (!p) return null;
    path.push(Array.prototype.indexOf.call(p.childNodes, node));
  }
  var range = null;
  try { range = { start: a.selectionStart, end: a.selectionEnd }; } catch (e) {}
  return { path: path, tag: a.tagName, ph: a.getAttribute("placeholder") || "", range: range };
}
function restoreFocus(f) {
  if (!f) return;
  var node = APP;
  for (var i = f.path.length - 1; i >= 0 && node; i--) node = node.childNodes[f.path[i]];
  if (!node || node.tagName !== f.tag || (node.getAttribute("placeholder") || "") !== f.ph) return;
  try {
    node.focus({ preventScroll: true });
    if (f.range && node.setSelectionRange) node.setSelectionRange(f.range.start, f.range.end);
  } catch (e) {}
}
function render() {
  // Preserve scroll across a same-route re-render (e.g. the 1s download poll on
  // Settings) so the page does not snap to the top. A route change still resets.
  var keepScroll = (S.route === _renderedRoute);
  // Same-route re-renders also preserve which text box had focus and the caret in it.
  var focusState = keepScroll ? captureFocus() : null;
  var prevScroller = keepScroll ? APP.querySelector(".screen, .solo, .live-body") : null;
  var prevScrollTop = prevScroller ? prevScroller.scrollTop : 0;
  liveDocEl = liveBodyEl = elapsedEl = recTimerEl = returnPillTimeEl = null;
  clear(APP);
  var view;
  switch (S.route) {
    case "setup": view = setupView(); break;
    case "starting": view = startingView(); break;
    case "home": view = shell("home", homeView()); break;
    case "pre": view = shell("home", preView()); break;
    case "importpre": view = shell("home", importPreView()); break;
    case "recordpre": view = shell("home", recordPreView()); break;
    // The live-family views render inside the shell so Meeting/History/Settings stay
    // reachable during a session; capture is server-side, so navigating away loses nothing.
    case "live": view = shell("home", liveView()); break;
    case "recordonly": view = shell("home", recordOnlyView()); break;
    case "importing": view = shell("home", importingView()); break;
    case "finish": view = shell("home", finishView()); break;
    case "history": view = shell("history", historyView()); break;
    case "reader": view = shell("history", readerView()); break;
    case "settings": view = shell("settings", settingsView()); break;
    case "upgrade": view = shell("settings", upgradeView()); break;
    default: view = shell("home", homeView());
  }
  APP.appendChild(view);
  if (S.stopMenuOpen) APP.appendChild(stopMenuLayer());
  // A calendar reminder floats above every screen except the ones where starting a meeting makes no
  // sense (already live/recording/importing, or the first-run gate).
  if (S.reminder && ["setup", "starting", "live", "recordonly", "importing"].indexOf(S.route) < 0) {
    APP.appendChild(reminderBanner());
  }
  // The long-silence warning belongs on the live screen: it is about THIS session's audio,
  // and its answers (stop and save / keep recording) only make sense there. Elsewhere the
  // Windows notification is what reaches the user, and the return pill leads back here.
  if (S.live.silenceNudge && S.route === "live") {
    APP.appendChild(silenceBanner());
  }
  // The "model struggling to keep up" nudge lives on the same live screen for the same reason:
  // its answers (record from here / keep going) only make sense there. Mirrors the silence inject.
  if (S.live.struggleNudge && S.route === "live") {
    APP.appendChild(struggleBanner());
  }
  // System audio not being captured (denied or failed): same live-screen-only reasoning, and
  // dismissible locally since there is nothing server-side to acknowledge (see sysState comment
  // in freshLive). codex H1.
  if (sysAudioWarn(S.live.sysState) && S.live.sysAudioDismissedFor !== S.live.sysState && S.route === "live") {
    APP.appendChild(sysAudioBanner());
  }
  if (S.toast) {
    APP.appendChild(el("div", { class: "toast-wrap" }, el("div", { class: "toast" + (S.toast.err ? " err" : ""), text: S.toast.msg })));
  }
  _renderedRoute = S.route;
  if (prevScrollTop) {
    var ns = APP.querySelector(".screen, .solo, .live-body");
    if (ns) ns.scrollTop = prevScrollTop;
  }
  restoreFocus(focusState);
}

/* ── shell (sidebar + main) ───────────────────────────────── */
function shell(active, mainNode) {
  return el("div", { class: "shell" }, [sidebar(active), el("div", { class: "main" }, mainNode)]);
}
function sidebar(active) {
  function nav(id, label, ic, route) {
    return el("button", { class: "nav-item" + (active === id ? " active" : ""), onclick: function () { go(typeof route === "function" ? route() : route); } },
      [icon(ic, 17), el("span", { text: label })]);
  }
  // While a session runs, a pulsing pill on every OTHER screen leads straight back to it.
  // The elapsed time updates in place (startElapsed), never via render().
  function returnPill() {
    if (!S.live.running) return null;
    if (S.route === "live" || S.route === "recordonly" || S.route === "importing") return null;
    returnPillTimeEl = el("span", { class: "mono", text: fmtElapsed(S.live.startedAt) });
    return el("button", { class: "return-pill", onclick: function () { go(liveRoute()); } }, [
      el("span", { class: "dot" }),
      el("span", { class: "rp-label", text: "Return to meeting" }),
      returnPillTimeEl,
    ]);
  }
  return el("aside", { class: "sidebar" }, [
    el("div", { class: "brand" }, [
      el("div", { class: "wordmark" }, [markSvg(20), el("span", { text: "Volksmond" })]),
      el("div", { class: "brand-sub", text: "by DigiPhyte" }),
    ]),
    el("nav", { class: "nav" }, [
      // Meeting returns to the RUNNING session when there is one, never a fresh form.
      nav("home", "Meeting", "mic", function () { return S.live.running ? liveRoute() : "home"; }),
      nav("history", "History", "clock", "history"),
      nav("settings", "Settings", "gear", "settings"),
    ]),
    returnPill(),
    el("div", { class: "spacer" }),
    el("div", { class: "local-pill" }, [icon("lock", 14), el("span", { text: "Local only, no internet" })]),
    // The direct edition checks DigiPhyte's manifest only when clicked. The Store edition instead
    // hands the user to its Store listing, where Microsoft owns the update decision. The airtight
    // offline edition has no update control at all.
    !offlineBuild() ? el("button", { class: "nav-item", style: { fontSize: "12px" }, disabled: !storeBuild() && updateState.state === "checking", onclick: function () { storeBuild() ? openStoreListing() : checkUpdates(); } },
      [icon("download", 16), el("span", { text: storeBuild() ? "Check for updates in Microsoft Store" : (updateState.state === "checking" ? "Checking for updates" : "Check for updates") })]) : null,
    !(offlineBuild() || storeBuild()) ? sideUpdateResult() : null,
    el("button", { class: "nav-item", style: { fontSize: "12px" }, onclick: reportBug },
      [icon("bug", 16), el("span", { text: "Report a bug or idea" })]),
  ]);
}

/* ── setup (first run) ────────────────────────────────────── */
async function finishSetup() {
  // Persist the durable flag to disk and AWAIT it: localStorage is wiped by the WebView
  // between launches (the reason the disk flag exists), so a silently-failed save would let
  // the wizard reappear. Surface a failure instead of relying on localStorage alone.
  try {
    S.settings = await api.post("/api/settings", { setup_complete: true });
  } catch (e) {
    if (S.settings) S.settings.setup_complete = true;
    toast(e.message || "Could not save setup state.", true);
  }
  try { localStorage.setItem("vm_setup_done", "1"); } catch (e) {}
  go("home");
}
// The first-run licence agreement. Un-skippable: accepting is the only way past it. Persist to
// disk AND localStorage (the same belt-and-braces as setup_complete, because the WebView can wipe
// localStorage between launches). A returning user who already finished setup goes straight home;
// a genuine first run continues into the welcome stage.
async function acceptLicence() {
  try {
    S.settings = await api.post("/api/settings", { licence_accepted: true });
  } catch (e) {
    if (S.settings) S.settings.licence_accepted = true;
    toast(e.message || "Could not save.", true);
  }
  try { localStorage.setItem("vm_licence_accepted", "1"); } catch (e) {}
  var done = false;
  try { done = !!localStorage.getItem("vm_setup_done"); } catch (e) {}
  if (S.settings && S.settings.setup_complete) done = true;
  if (done) { go("home"); }
  else { S.setup.stage = "welcome"; render(); }
}
function setupView() {
  var stage = S.setup.stage;
  var inner;
  if (stage === "licence") {
    inner = el("div", { class: "col-narrow stack", style: { gap: "20px" } }, [
      el("div", { class: "row", style: { justifyContent: "space-between", alignItems: "center" } }, [
        el("div", { class: "wordmark" }, [markSvg(22), el("span", { text: "Volksmond" }), el("span", { class: "provisional", text: "Research Preview" })]),
        langToggleSeg(),
      ]),
      // The TLDR, prominent and at the very top, so the deal is clear before anything else.
      el("div", { class: "card disclosure accent", style: { padding: "18px", display: "flex", gap: "14px" } }, [
        el("div", { class: "tone-tile accent", style: { width: "40px", height: "40px", flex: "0 0 auto" } }, icon("crown", 20)),
        el("div", {}, [
          el("div", { style: { fontWeight: "700", fontSize: "16px", marginBottom: "6px" }, text: "Free for personal use. Business use needs a licence." }),
          el("p", { class: "ink-2", style: { fontSize: "13.5px", margin: "0" }, text: "Use Volksmond for your own meetings, study, or personal projects and it is free, forever. If a business or practice uses it for work, that needs a paid licence. One or two people trying it at work is fine; rolling it out to a team or using it in paid client work is what a licence is for." }),
        ]),
      ]),
      bulletList([
        "Personal use: free, everything on this computer, no account.",
        "Business use: a paid licence per person, renewed yearly.",
        "Your audio never leaves this computer either way.",
      ]),
      // The honour system, stated plainly. This IS the whole enforcement model (no telemetry, no
      // phone-home, no activation check), so we say so out loud: it reads as a trust signal, not a
      // disclaimer, and it is exactly the posture the buyers we want (counselling, legal, medical)
      // respond to. Locked in the monetisation plan section 3.
      el("div", { class: "card", style: { padding: "16px 18px", display: "flex", gap: "14px" } }, [
        el("div", { class: "tone-tile accent", style: { width: "36px", height: "36px", flex: "0 0 auto" } }, icon("heart", 18)),
        el("div", {}, [
          el("div", { style: { fontWeight: "600", fontSize: "14px", marginBottom: "4px" }, text: "It runs on the honour system." }),
          el("p", { class: "ink-2", style: { fontSize: "12.5px", margin: "0" }, text: "Volksmond never phones home. There is no account, no activation server, and no way for us to see that you installed it or how you use it. We are trusting you: if a business or practice uses Volksmond for work, buy a licence. That trust is what keeps the personal version free and the Afrikaans models open for everyone." }),
        ]),
      ]),
      el("div", { class: "row gap-10" }, [
        el("button", { class: "btn primary tall grow", onclick: acceptLicence }, "I agree and continue"),
      ]),
      el("div", { class: "row", style: { justifyContent: "center" } },
        el("button", { class: "btn ghost sm", onclick: function () { openExternal(BUSINESS_PAGE_URL); } }, "Read the full licence")),
    ]);
  } else if (stage === "welcome") {
    inner = el("div", { class: "col-narrow stack", style: { gap: "20px" } }, [
      el("div", { class: "row", style: { justifyContent: "space-between", alignItems: "center" } }, [
        el("div", { class: "wordmark" }, [markSvg(22), el("span", { text: "Volksmond" }), el("span", { class: "provisional", text: "Research Preview" })]),
        langToggleSeg(),
      ]),
      el("h1", { text: "A calm, private transcript of any meeting on your computer." }),
      el("p", { class: "ink-2", style: { fontSize: "15px" }, text: "Volksmond listens to your microphone and the audio coming out of your computer, and writes it down as people talk. Built for Afrikaans, English, and the way people actually switch between them." }),
      el("div", { class: "card", style: { padding: "18px", display: "flex", gap: "14px" } }, [
        el("div", { class: "tone-tile accent", style: { width: "40px", height: "40px", flex: "0 0 auto" } }, icon("lock", 20)),
        el("div", {}, [
          el("div", { style: { fontWeight: "600", marginBottom: "4px" }, text: "Your audio never leaves this computer." }),
          el("p", { class: "ink-2", style: { fontSize: "12.5px" }, text: "No telemetry and no accounts. Your audio and transcripts are never uploaded; everything is transcribed and summarised on your own machine. Once the models are downloaded, you can use Volksmond completely offline." }),
        ]),
      ]),
      el("div", { class: "row gap-10" }, [
        el("button", { class: "btn primary tall grow", onclick: function () { S.setup.stage = "languages"; render(); } }, "Get started"),
      ]),
      el("div", { class: "row", style: { justifyContent: "center" } },
        el("button", { class: "btn ghost sm", onclick: function () { finishSetup(); } }, "Skip setup for now")),
      el("p", { class: "ink-3", style: { fontSize: "11.5px" }, text: "Next we download the transcription model to your computer, so your first meeting starts straight away. Summaries are an optional extra you can add after that." }),
    ]);
  } else if (stage === "languages") {
    // Ask which languages the user transcribes BEFORE the model download, so the engine copy on
    // the next step is honest about what gets used (Afrikaans -> Fluister, the rest -> Whisper).
    var setupLangs = (S.settings && S.settings.transcribe_languages) || ["af", "en"];
    var toggleSetupLang = function (code) {
      // Read the freshest saved list (not the render-time copy) so rapid toggles don't act on
      // stale state.
      var sel = ((S.settings && S.settings.transcribe_languages) || setupLangs).slice();
      var i = sel.indexOf(code);
      if (i >= 0) { if (sel.length <= 1) return; sel.splice(i, 1); }   // keep at least one language
      else sel.push(code);
      var patch = { transcribe_languages: sel };
      // If the removed language was the saved default, move the default to a kept one, so an
      // English-only first run does not still start in Afrikaans/Fluister (mirrors transcriptionCard).
      var cur = S.settings && S.settings.transcription_language;
      if (cur && langIsToggleable(cur) && sel.indexOf(cur) < 0) patch.transcription_language = sel[0];
      if (S.form && S.form.language && langIsToggleable(S.form.language) && sel.indexOf(S.form.language) < 0) S.form.language = sel[0];
      saveSettings(patch);
    };
    inner = el("div", { class: "col-narrow stack", style: { gap: "18px" } }, [
      el("div", { class: "eyebrow", text: "Setup, languages" }),
      el("h1", { text: "Which languages do you transcribe?" }),
      el("p", { class: "ink-2", text: "Pick the languages you record in. Afrikaans uses Fluister, our Afrikaans-tuned model; English and the rest use standard Whisper. The size is chosen automatically for your computer." }),
      el("div", { class: "card", style: { padding: "16px" } },
        el("div", { class: "row gap-8", style: { flexWrap: "wrap" } }, SUPPORTED_LANGS.map(function (l) {
          var on = setupLangs.indexOf(l.code) >= 0;
          return el("button", { class: "btn sm" + (on ? " primary" : " ghost"), onclick: function () { toggleSetupLang(l.code); } }, [on ? icon("check", 12) : null, el("span", { text: l.name })]);
        }))),
      el("p", { class: "ink-3", style: { fontSize: "11.5px" }, text: "You can change this any time in Settings." }),
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end", marginTop: "4px" } }, [
        el("button", { class: "btn ghost", onclick: function () { S.setup.stage = "welcome"; render(); } }, "Back"),
        el("button", { class: "btn primary tall", onclick: function () { S.setup.stage = "voice"; render(); } }, "Continue"),
      ]),
    ]);
  } else if (stage === "voice") {
    // Don't let Continue silently skip the whole point of this step. If nothing is
    // present and nothing is downloading yet, the primary button kicks off the
    // hardware-recommended model (in the background) and then advances, so a model
    // is always at least on its way before the user leaves setup.
    var vm = S.voiceModels;
    var voiceReady = !!(vm && (((vm.models || []).some(function (m) { return m.present; })) || (vm.progress && vm.progress.state === "downloading")));
    var recModel = vm && vm.recommended_model;
    // If an NVIDIA GPU is present, offer the optional CUDA step before save-location.
    var nextAfterVoice = (S.cuda && S.cuda.gpu_present) ? "cuda" : "save_location";
    var voiceContinue = (vm && !voiceReady && recModel)
      ? el("button", { class: "btn primary tall", onclick: function () { startVoiceDownload(recModel); S.setup.stage = nextAfterVoice; render(); } }, "Download recommended and continue")
      : el("button", { class: "btn primary tall", onclick: function () { S.setup.stage = nextAfterVoice; render(); } }, "Continue");
    inner = el("div", { class: "col-narrow stack", style: { gap: "18px" } }, [
      el("div", { class: "eyebrow", text: "Setup, transcription model" }),
      el("h1", { text: "Download the model that does the transcribing" }),
      el("p", { class: "ink-2", text: "Volksmond transcribes on your own computer using a language model. Download the one that suits your machine now, so your first meeting starts straight away instead of waiting on a download. It runs offline afterwards." }),
      ((S.settings && (S.settings.transcribe_languages || []).indexOf("af") >= 0)) ? el("p", { class: "ink-3", style: { fontSize: "12px", margin: "0" }, text: "Afrikaans uses Fluister, downloaded automatically the first time you transcribe Afrikaans. The model below is the standard Whisper model for English and other languages." }) : null,
      voiceDownloadPanel(),
      el("p", { class: "ink-3", style: { fontSize: "11.5px" }, text: "It downloads in the background. You can carry on with setup while it finishes; your first meeting waits for it to be ready." }),
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end", marginTop: "4px" } }, [
        el("button", { class: "btn ghost", onclick: function () { S.setup.stage = "languages"; render(); } }, "Back"),
        voiceContinue,
      ]),
    ]);
  } else if (stage === "cuda") {
    inner = el("div", { class: "col-narrow stack", style: { gap: "18px" } }, [
      el("div", { class: "eyebrow", text: "Setup, GPU acceleration" }),
      el("h1", { text: "Use your NVIDIA graphics card?" }),
      el("p", { class: "ink-2", text: "We found an NVIDIA graphics card. You can download the NVIDIA CUDA libraries so the Best model runs on your GPU, which is much faster than the CPU. This is optional and NVIDIA only; without it everything still works on the CPU. AMD and Intel graphics are not supported by the engine." }),
      cudaPanel(false),
      el("p", { class: "ink-3", style: { fontSize: "11.5px" }, text: "It is a large download (about 1.5 GB). You can skip this and set it up later in Settings. After it downloads, restart Volksmond to use your GPU." }),
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end", marginTop: "4px" } }, [
        el("button", { class: "btn ghost", onclick: function () { S.setup.stage = "voice"; render(); } }, "Back"),
        el("button", { class: "btn primary tall", onclick: function () { S.setup.stage = "save_location"; render(); } }, "Continue"),
      ]),
    ]);
  } else if (stage === "save_location") {
    // The default location is a per-user Volksmond folder on this computer (the
    // server supplies the exact path). Many users want their transcripts in
    // Documents or a synced folder instead, so we ask before they start a
    // session rather than hiding it in Settings. Picking nothing is fine --
    // "Continue" just keeps the default.
    var savedLoc = (S.settings && S.settings.save_location) || "";
    var defaultLoc = (S.appInfo && S.appInfo.save_dir) || "default folder";
    var currentLoc = savedLoc || defaultLoc;
    inner = el("div", { class: "col-narrow stack", style: { gap: "18px" } }, [
      el("div", { class: "eyebrow", text: "Setup, where to save" }),
      el("h1", { text: "Where should your transcripts go?" }),
      el("p", { class: "ink-2", text: "Every meeting is saved as a Markdown file. Pick a folder you can find later, or keep the default." }),
      el("div", { class: "card", style: { padding: "16px" } }, [
        el("div", { class: "section-label", style: { marginBottom: "6px" }, text: savedLoc ? "Your folder" : "Default folder (per user, on this computer)" }),
        el("div", { class: "mono ink-2", style: { fontSize: "12.5px", wordBreak: "break-all" }, text: currentLoc }),
        el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "10px" }, text: "For maximum privacy, choose a folder that a cloud provider does not sync (OneDrive, Google Drive, Dropbox, and the like)." }),
        el("div", { class: "row gap-8", style: { marginTop: "12px" } }, [
          el("button", { class: "btn ghost", onclick: async function () {
            var p = await pickFile("folder");
            if (!p) return;
            try {
              S.settings = await api.post("/api/settings", { save_location: p });
              S.appInfo = await api.get("/api/app-info");
              render();
            } catch (e) { toast(e.message || "Not a writable folder.", true); }
          } }, savedLoc ? "Choose a different folder" : "Choose another folder"),
        ]),
      ]),
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end", marginTop: "4px" } }, [
        el("button", { class: "btn ghost", onclick: function () { S.setup.stage = (S.cuda && S.cuda.gpu_present) ? "cuda" : "voice"; render(); } }, "Back"),
        el("button", { class: "btn primary tall", onclick: function () { S.setup.stage = "summaries"; render(); } }, "Continue"),
      ]),
    ]);
  } else if (stage === "summaries") {
    var installed = summaryInstalled();
    var wantsSummaries = S.setup.choice === "summarise";
    inner = el("div", { class: "col-narrow stack", style: { gap: "18px" } }, [
      el("div", { class: "eyebrow", text: "Setup, summaries" }),
      el("h1", { text: "Do you want to just transcribe, or also summarise on your machine?" }),
      el("p", { class: "ink-2", text: "Summaries condense a finished transcript into the decisions, the to-dos, and what stayed unresolved. They run on a small model on your machine, separate from the one that does the transcribing. Off by default." }),
      choiceCard("transcribe", "mic", "Just transcribe", "The original promise. Live transcripts, history, all of it. No extra model, no extra RAM.", "Default. You can turn summaries on later in Settings."),
      choiceCard("summarise", "note", "Transcribe and summarise", "Adds a Summarise button at the end of every meeting, run entirely on this machine.",
        installed ? "A summary model is already installed on this machine." : "Choose a model size below and we download it for you. One click, no file hunting."),
      wantsSummaries ? el("div", { class: "stack", style: { gap: "10px" } }, [
        el("div", { class: "section-label", text: installed ? "Your summary model (switch or add another)" : "Choose a summary model to download" }),
        summaryDownloadPanel(),
        el("p", { class: "ink-3", style: { fontSize: "11.5px" }, text: installed ? "Summaries are ready on this machine. You can switch model here, or add another." : "It downloads in the background. You can continue, it keeps going, and summaries switch on when it is ready." }),
      ]) : null,
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end", marginTop: "4px" } }, [
        el("button", { class: "btn ghost", onclick: finishSetup }, "Skip for now"),
        el("button", { class: "btn primary tall", onclick: finishSetup }, "Continue"),
      ]),
    ]);
  }
  return el("div", { class: "solo" }, el("div", { class: "solo-center" }, el("div", { class: "screen-inner" }, inner)));
}
function choiceCard(id, ic, title, body, note) {
  var on = S.setup.choice === id;
  var act = function () { S.setup.choice = id; render(); };
  return el("div", { class: "card choice" + (on ? " on" : ""), role: "button", tabindex: "0", onclick: act, onkeydown: keyActivate(act) }, [
    el("div", { class: "radio" }),
    el("div", { class: "grow" }, [
      el("div", { class: "ct" }, [icon(ic, 16), el("span", { text: title }), id === "transcribe" ? el("span", { class: "chip", text: "Default" }) : null]),
      el("div", { class: "cb", text: body }),
      el("div", { class: "cn", text: note }),
    ]),
  ]);
}

/* ── business-use nudge (one-time, local, dismissable) ────── */
// After SESSION_NUDGE_THRESHOLD completed sessions on a free licence, show one gentle,
// dismissable card on the home screen. It never blocks a meeting, never repeats (either button
// retires it for good), and never shows once a licence is active. Entirely local: the trigger is
// a count in settings.json and nothing is ever sent anywhere.
function shouldShowBusinessNudge() {
  var st = S.settings || {};
  return !isPro()
    && (st.session_count || 0) >= SESSION_NUDGE_THRESHOLD
    && !st.business_nudge_seen;
}
function retireBusinessNudge(openLink) {
  if (openLink) openExternal(BUSINESS_PAGE_URL);
  if (S.settings) S.settings.business_nudge_seen = true;  // hide at once, then persist
  render();
  api.post("/api/settings", { business_nudge_seen: true }).then(function (s) { S.settings = s; }).catch(function () {});
}
function businessNudgeCard() {
  return el("div", { class: "card", style: { padding: "16px", display: "flex", gap: "14px", alignItems: "flex-start", marginBottom: "16px" } }, [
    el("div", { class: "tone-tile accent", style: { width: "36px", height: "36px", flex: "0 0 auto" } }, icon("crown", 18)),
    el("div", { class: "grow" }, [
      el("div", { style: { fontWeight: "600", marginBottom: "4px" }, text: "Using Volksmond for work?" }),
      el("p", { class: "ink-2", style: { fontSize: "13px", margin: "0 0 12px" }, text: "Business use needs a licence. It keeps the personal version free for everyone and funds the open Afrikaans models." }),
      el("div", { class: "row gap-8" }, [
        el("button", { class: "btn sm primary", onclick: function () { retireBusinessNudge(true); } }, "Business licensing"),
        el("button", { class: "btn sm ghost", onclick: function () { retireBusinessNudge(false); } }, "Not now"),
      ]),
    ]),
  ]);
}

/* ── calendar reminder (local Outlook, Business) ──────────────── */
// While the app is open, poll the LOCAL Outlook calendar and nudge "start transcribing?" when a
// meeting begins. Fully offline (the server reads Outlook over COM, no network call). Business-gated
// and inert without a licence + Outlook + pywin32, so it costs nothing for personal users. The
// nudge NEVER auto-starts (consent posture): it drops you on the pre-meeting screen, pre-seeded.
var REMINDER_POLL_MS = 60000;
var reminderHandled = {};   // meeting key -> true, so a meeting is nudged at most once per session
function reminderKey(m) { return (m.subject || "") + "|" + (m.start || ""); }
function startReminderPoll() {
  if (reminderTimer) return;
  reminderTimer = setInterval(reminderTick, REMINDER_POLL_MS);
  reminderTick();
}
async function reminderTick() {
  // ONE poll, TWO independent outputs (WP-10): the in-app reminder card, gated on
  // calendar_reminders, and the Windows notification, gated on os_toasts. They are separately
  // switchable because they answer different situations: the card is for when you are looking at
  // Volksmond, the notification is for when you are not. So poll while EITHER is on, and gate each
  // output on its own switch. Guard cheaply BEFORE any request: Business only, and never while a
  // session is already running or being set up.
  if (!isPro()) return;
  var bannerOn = !(S.settings && S.settings.calendar_reminders === false);   // default on
  var toastOn = !(S.settings && S.settings.os_toasts === false);             // default on
  if (!bannerOn && !toastOn) return;
  if (S.reminder || (S.live && S.live.running)) return;
  if (["live", "recordonly", "importing", "starting", "setup"].indexOf(S.route) >= 0) return;
  var r;
  try { r = await api.get("/api/calendar-upcoming"); }
  catch (e) { return; }   // 402 or a transient error: skip this tick, try again next minute
  if (!r || !r.available || !r.found || typeof r.starts_in_min !== "number") return;
  // Nudge when the meeting is starting: from 2 minutes before to 15 minutes after its start time.
  if (r.starts_in_min > 2 || r.starts_in_min < -15) return;
  var key = reminderKey(r);
  if (reminderHandled[key]) return;
  // Marked before either output fires, so a meeting is nudged at most once per session whichever
  // output is on. The card used to rely on S.reminder blocking re-entry for this, which is no help
  // in toast-only mode: there is no card to block on, and the poll would fire a notification every
  // minute for a quarter of an hour.
  reminderHandled[key] = true;
  if (toastOn) notifyMeetingToast(r.subject || "", r.start || "");
  if (bannerOn) {
    S.reminder = { subject: r.subject, attendees: r.attendees || [], start: r.start, key: key };
    render();
  }
}
function notifyMeetingToast(subject, start) {
  // Fire and forget. The server hands the text to the Windows shell; a failure (no licence, no
  // pywin32, notifications switched off, offline edition with no such route) is not worth a word to
  // the user and must never disturb the reminder card. Clicking the notification only brings the
  // window forward, where the card is already waiting: there is deliberately no action channel
  // from a toast back into the app.
  // The start time goes with it because the server folds it into the notification's coalescing
  // tag: two occurrences of a recurring meeting share a subject, and without the start the second
  // one would be swallowed as a duplicate of the first.
  api.post("/api/notify-meeting", { subject: subject, start: start || "" }).catch(function () {});
}
function acceptReminder() {
  var r = S.reminder; if (!r) return;
  reminderHandled[r.key] = true;
  S.reminder = null;
  S.form.title = r.subject || "";
  S.form.participants = (r.attendees || []).slice();
  S.form.context = null;   // a calendar-started meeting begins from the saved default, never a stale per-meeting override
  go("pre");   // drop on the pre-meeting screen, pre-seeded; the user presses Begin (never auto-start)
}
function dismissReminder() {
  if (S.reminder) reminderHandled[S.reminder.key] = true;
  S.reminder = null;
  render();
}
// A floating banner injected in render() so it shows on any screen, not only the home hub.
function reminderBanner() {
  var r = S.reminder;
  return el("div", { style: { position: "fixed", top: "16px", left: "50%", transform: "translateX(-50%)", zIndex: "60", maxWidth: "440px", width: "calc(100% - 32px)" } },
    el("div", { class: "card", style: { padding: "14px 16px", display: "flex", gap: "12px", alignItems: "flex-start", borderColor: "var(--accent)", boxShadow: "0 10px 34px rgba(0,0,0,0.20)" } }, [
      el("div", { class: "tone-tile accent", style: { width: "34px", height: "34px", flex: "0 0 auto" } }, icon("calendar", 17)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600", fontSize: "13.5px" }, text: "A meeting is starting" }),
        r.subject ? el("div", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "1px" } }, raw(r.subject)) : null,
        el("p", { class: "ink-3", style: { fontSize: "11.5px", margin: "4px 0 0" }, text: "Start transcribing it? Names from the meeting are added automatically." }),
        el("div", { class: "row gap-8", style: { marginTop: "10px" } }, [
          el("button", { class: "btn sm primary", onclick: acceptReminder }, [icon("dot", 12), "Start transcribing"]),
          el("button", { class: "btn sm ghost", onclick: dismissReminder }, "Not now"),
        ]),
      ]),
      el("button", { class: "btn ghost sm", style: { flex: "0 0 auto", padding: "6px" }, onclick: dismissReminder, title: "Dismiss" }, icon("x", 14)),
    ]));
}

/* ── long silence during a live session (WP-9b) ───────────────── */
// The server watches the raw energy of both channels and, when NOTHING has been heard for
// the chosen number of minutes, publishes a nudge on /api/status (and sends a Windows
// notification if those are on). The page only has to notice it and offer the three honest
// answers: stop and save, keep recording, or stop asking.
//
// Why a poll of its own: during a live session the page runs the SSE stream, the elapsed
// timer and the level meter, but nothing that re-reads /api/status - deliberately, so a
// quiet meeting does not re-render. Ten seconds is plenty of resolution for a warning that
// is minutes old by the time it fires, and it costs one tiny GET.
var SILENCE_POLL_MS = 10000;
function startSilencePoll() {
  if (silenceTimer) return;
  silenceTimer = setInterval(refreshSilence, SILENCE_POLL_MS);
}
function stopSilencePoll() { if (silenceTimer) { clearInterval(silenceTimer); silenceTimer = null; } }
// t0-capture readiness poll: while a live session is still loading its model, poll /api/status ~1.5s
// (like pollWarm) and flip the "preparing" chip to live the instant model_ready turns true, then stop
// itself. The slower 10s silence reconcile also adopts these fields as a self-healing backstop.
var READINESS_POLL_MS = 1500;
// Client-side download-stall watchdog: if the downloaded byte count has not advanced for this
// long WHILE a download is in flight, surface the same bounded failure the server raises at 60s.
// Belt-and-braces in case a status poll is lost. Armed only during the "downloading" phase; a slow
// model LOAD moves no bytes, so the server's separate load timeout owns that case (never this one).
var PREPARE_STALL_MS = 90000;
var _prepWatchBytes = -1, _prepWatchAt = 0;
function resetPrepareWatch() { _prepWatchBytes = -1; _prepWatchAt = 0; }
function startReadinessPoll() {
  if (readinessTimer) return;
  readinessTimer = setInterval(function () {
    if (!S.live.running || S.live.sourceKind === "file" || S.live.modelReady) { stopReadinessPoll(); return; }
    api.get("/api/status").then(function (st) {
      if (!st || !st.running) return;
      if (adoptReadiness(st)) render();
      if (S.live.modelReady) stopReadinessPoll();
    }).catch(function () {});
  }, READINESS_POLL_MS);
}
function stopReadinessPoll() { if (readinessTimer) { clearInterval(readinessTimer); readinessTimer = null; } }
// Signature of the prepare object so a steady poll (same phase/bytes) never forces a re-render.
// The load's elapsed seconds are part of it, rounded to whole seconds, because a counter that does
// not tick is worse than none: that is one small re-render per poll, only while the model loads.
function prepareSig(p) {
  return p ? (String(p.phase) + "|" + String(p.model) + "|" + String(p.downloaded) + "|" + String(p.total)
    + "|" + (p.stalled ? "1" : "0") + "|" + String(Math.floor(p.elapsed || 0)) + "|" + (p.slow ? "1" : "0")) : "";
}
// Adopt the server's transcription-readiness + prepare progress onto S.live; returns true if
// anything changed (so the caller can re-render). Shared by the readiness poll and the silence
// reconcile, so a steady state never forces a re-render and the two stay in step.
function adoptReadiness(st) {
  var changed = false;
  var mr = !!st.model_ready;
  if (mr !== S.live.modelReady) { S.live.modelReady = mr; changed = true; }
  var preparing = !!st.preparing;
  if (preparing !== S.live.preparing) { S.live.preparing = preparing; changed = true; }
  var prep = st.prepare || null;
  if (prepareSig(prep) !== prepareSig(S.live.prepare)) { S.live.prepare = prep; changed = true; }
  // Client download-stall watchdog: track the byte count only while downloading; reset otherwise.
  var stalled = S.live.prepareStalledClient;
  if (mr || !preparing || !prep || prep.phase !== "downloading") {
    resetPrepareWatch();
    if (stalled) { S.live.prepareStalledClient = false; changed = true; }
  } else {
    var dl = prep.downloaded || 0, now = Date.now();
    if (dl !== _prepWatchBytes) {
      _prepWatchBytes = dl; _prepWatchAt = now;
      if (stalled) { S.live.prepareStalledClient = false; changed = true; }
    } else if (_prepWatchAt && (now - _prepWatchAt) > PREPARE_STALL_MS && !stalled) {
      S.live.prepareStalledClient = true; changed = true;
    }
  }
  var pe = st.prepare_error || null;
  if (pe !== S.live.prepareError) { S.live.prepareError = pe; changed = true; }
  return changed;
}
// The transcription-model failure message for the live screen, or null while healthy. Folds the
// server's actionable prepare_error, an explicit prepare.phase==="error", and the client stall
// watchdog into one signal, used by both the status chip and the empty-transcript panel.
function liveFailureMsg() {
  var L = S.live;
  if (L.prepareError) return L.prepareError;
  if (L.prepare && L.prepare.phase === "error") return tr("Could not load the transcription model on this computer.");
  if (L.prepareStalledClient) return tr("The download stalled. Check your connection and try again.");
  return null;
}
// Retry the model build after a bounded failure: clear the visible error, reset the watchdog and
// ask the server to re-attempt. Capture and recording keep running; only the model build restarts.
function retryPrepare() {
  S.live.prepareError = null; S.live.prepareStalledClient = false; resetPrepareWatch();
  if (S.live.prepare) S.live.prepare = null;
  S.live.preparing = true;
  render();
  api.post("/api/prepare/retry").then(function () {
    if (!S.live.modelReady) startReadinessPoll();
  }).catch(function (e) { toast(e.message || "Could not retry.", true); });
}
function silenceSig(n) { return n ? (String(n.at || "") + "|" + String(n.count || 0)) : ""; }
// The struggle nudge carries no timestamp; its identity is the model step plus whether
// recording has since started, so any of those changing re-renders (a second downgrade, or
// recording turning on so the banner switches to its "already recording" wording).
function struggleSig(n) { return n ? (String(n.old_size || "") + "|" + String(n.new_size || "") + "|" + (n.recording ? "1" : "0")) : ""; }
function refreshSilence() {
  if (!S.live.running || S.live.sourceKind === "file") return;
  api.get("/api/status").then(function (st) {
    if (!st || !st.running) return;
    // One poll, both server-owned live nudges PLUS the authoritative recording state: re-render
    // if ANY changed, and only then. The server is the source of truth for recording, so adopt
    // st.recording / st.recording_started every tick (not just on the record path); this self-heals
    // any desync within one poll if both the record POST and its recovery fetch were lost. The
    // optimistic flip in recordFromHere still gives an instant response; this only reconciles.
    var changed = false;
    var n = st.silence_nudge || null;
    if (silenceSig(n) !== silenceSig(S.live.silenceNudge)) { S.live.silenceNudge = n; changed = true; }
    var g = st.struggle_nudge || null;
    if (struggleSig(g) !== struggleSig(S.live.struggleNudge)) { S.live.struggleNudge = g; changed = true; }
    var rec = !!st.recording;
    if (rec !== S.live.recording) { S.live.recording = rec; changed = true; }
    var rs = !!st.recording_started;
    if (rs !== S.live.recordingStarted) { S.live.recordingStarted = rs; changed = true; }
    // H1: system-audio capture health. Absent (file/record-only sessions never set it server-side)
    // is treated the same as "active" so it never renders a stale warning.
    var ss = st.sys_state || null;
    if (ss !== S.live.sysState) { S.live.sysState = ss; changed = true; }
    // The mic-gate counter and mode, plus any hint the quiet-mic safety valve has just latched.
    // Not silent: this is the poll that is meant to surface it.
    if (adoptMicGate(st, false)) changed = true;
    // t0-capture: also adopt model readiness / load error here as a slower self-healing backstop to
    // the 1.5s readiness poll (which stops once ready), so a lost poll cannot leave the chip stuck.
    if (adoptReadiness(st)) changed = true;
    if (changed) render();
  }).catch(function () {});
}
// Clicking the Windows notification brings this window forward; the banner must already be
// there when it arrives, not up to ten seconds later. So a focus event forces the poll.
// Cheap and harmless when nothing is running (refreshSilence guards on that itself).
try { window.addEventListener("focus", function () { refreshSilence(); }); } catch (e) {}
async function answerSilence(action) {
  // Optimistic: the banner goes now. The server call only records the choice for the
  // watcher, so a failure means at worst one more warning later, never a stuck banner.
  S.live.silenceNudge = null;
  render();
  try { await api.post("/api/silence-nudge", { action: action }); } catch (e) {}
  toast(action === "mute" ? "No more silence warnings this session."
                          : "Still recording. We will tell you again if it stays silent.");
}
// A floating card, same shape as the calendar reminder, injected in render() so it sits
// above the live screen without disturbing its layout.
function silenceBanner() {
  var n = S.live.silenceNudge || {};
  var mins = n.minutes || 5;
  return el("div", { style: { position: "fixed", top: "16px", left: "50%", transform: "translateX(-50%)", zIndex: "60", maxWidth: "460px", width: "calc(100% - 32px)" } },
    el("div", { class: "card", style: { padding: "14px 16px", display: "flex", gap: "12px", alignItems: "flex-start", borderColor: "var(--warn)", boxShadow: "0 10px 34px rgba(0,0,0,0.20)" } }, [
      el("div", { class: "tone-tile warn", style: { width: "34px", height: "34px", flex: "0 0 auto" } }, icon("alert", 17)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600", fontSize: "13.5px" } },
          [raw(tr("Nothing heard for") + " " + mins + " " + tr("minutes"))]),
        el("p", { class: "ink-3", style: { fontSize: "11.5px", margin: "4px 0 0" }, text: "Volksmond is still recording, but both the microphone and the system audio have been silent. Check your device, or stop and save." }),
        el("div", { class: "row gap-8", style: { marginTop: "10px", flexWrap: "wrap" } }, [
          // Exactly what the Stop button does (doStop -> POST /api/stop?what=all), so there is
          // one stop path in the app and this one cannot drift from it. The server signals the
          // watcher as part of that stop, so no separate answer is needed here.
          el("button", { class: "btn sm primary", onclick: function () { S.live.silenceNudge = null; doStop("all"); } }, [icon("stop", 12), "Stop and save"]),
          el("button", { class: "btn sm ghost", onclick: function () { answerSilence("snooze"); } }, "Keep recording"),
        ]),
      ]),
      el("button", { class: "btn ghost sm", style: { flex: "0 0 auto", padding: "6px" }, onclick: function () { answerSilence("mute"); }, title: "Stop warning me this session" }, icon("x", 14)),
    ]));
}

/* ── model struggling to keep up during a live session ─────────── */
// Parallel to the silence nudge: the server steps a CPU session down to a lighter, faster model
// when it cannot hold real time, and publishes struggle_nudge on /api/status
// ({old_size, new_size, recording}). The page notices it on the SAME poll (refreshSilence) and
// offers the honest answers: start recording so the audio can be re-transcribed at full accuracy
// afterward, or keep going. Recording can also be started from the standalone live-footer button.
async function recordFromHere() {
  // Optimistic + double-click guard: flipping recording now hides BOTH triggers (this action's
  // banner button and the live-footer button), so a fast second click short-circuits here and
  // cannot open a second recorder. recordingStarted (latched) also blocks a restart after a stop.
  // Clearing the nudge drops the banner at once (the server clears it too on success).
  if (S.live.recording || S.live.recordingStarted) return;
  S.live.recording = true;
  S.live.struggleNudge = null;
  render();
  try {
    var resp = await api.post("/api/record-from-here");
    // The stem must reach the client or the finish screen's re-transcribe handoff has nothing to
    // point at (S.finish.recordingStem is derived from S.live.audioStem).
    if (resp && resp.audio_stem) S.live.audioStem = resp.audio_stem;
    // Re-assert on confirmed success: a status poll landing in the in-flight window could have
    // reconciled recording back to false before the server committed. The 2xx means it is on.
    S.live.recording = true;
    S.live.recordingStarted = true;
    toast("Recording from here. Earlier audio is not saved.");
  } catch (e) {
    // The optimistic flip may no longer match reality: the backend could have committed while the
    // response was lost, or 409'd because a recording had already started. Do NOT blindly force
    // recording off - ask the server and adopt the authoritative recording state.
    try {
      var st = await api.get("/api/status");
      if (st && st.running) {
        S.live.recording = !!st.recording;
        S.live.recordingStarted = !!st.recording_started;
        // If a recording really is running, recover its stem (status has no dedicated field; it
        // derives from the transcript path, output_path minus ".md") for the re-transcribe handoff.
        if (S.live.recording && !S.live.audioStem) {
          var stem = st.audio_stem || (st.output_path ? st.output_path.replace(/\.md$/, "") : null);
          if (stem) S.live.audioStem = stem;
        }
      } else {
        S.live.recording = false;
      }
    } catch (e2) {
      S.live.recording = false;   // status unreachable too: fall back to the safe "not recording"
    }
    render();
    // Recording ended up on (the call had committed) -> confirm it; otherwise surface the failure.
    if (S.live.recording) toast("Recording from here. Earlier audio is not saved.");
    else toast((e && e.message) || "Could not start recording.", true);
  }
}
async function dismissStruggle(action) {
  // Optimistic, exactly like answerSilence: the banner goes now; the POST only records the choice
  // server-side (dismiss clears it for this session; mute persists struggle_nudge=false so it is
  // off until re-enabled in Settings), so a failure means at worst one more nudge, never a stuck banner.
  S.live.struggleNudge = null;
  render();
  try { await api.post("/api/struggle-nudge", { action: action }); } catch (e) {}
  if (action === "mute") toast("Won't warn again");
}
// A floating card, same shape and inject point as silenceBanner().
function struggleBanner() {
  // Use the latched client state, not the nudge's snapshot: if a recording is running OR has run
  // this session, there is already audio to re-transcribe, so drop the record affordance and switch
  // the copy. (Same condition that hides the standalone footer button.)
  var hasRec = !!(S.live.recording || S.live.recordingStarted);
  var n = S.live.struggleNudge || {};
  var body;
  if (n.old_size && n.old_size === n.new_size)
    // A shed event, not a model change: the engine ran out of smaller models in this family and
    // is skipping audio to stay live rather than dropping to a model that would invent text.
    body = hasRec
      ? "Volksmond is skipping some audio to stay live. Your recording still has all of it and can be re-transcribed at full accuracy afterward."
      : "Volksmond is skipping some audio to stay live. Record now so nothing is lost, and re-transcribe at full accuracy afterward.";
  else if (n.indicative)
    body = "Live text is now rough (smaller model). The recording has everything, re-transcribe it afterwards.";
  else body = hasRec
    ? "Volksmond switched to a lighter, faster model to stay live, so this part may be less accurate. Your recording can be re-transcribed at full accuracy afterward."
    : "Volksmond switched to a lighter, faster model to stay live, so this part may be less accurate. Record now and re-transcribe at full accuracy afterward.";
  var actions = [];
  // Record button only when nothing has recorded yet; once it has, the audio is already kept for a
  // re-transcribe, so the primary action falls away (matches the body copy).
  if (!hasRec) actions.push(el("button", { class: "btn sm record", onclick: function () { recordFromHere(); } }, [icon("dot", 12), "Record from here"]));
  actions.push(el("button", { class: "btn sm ghost", onclick: function () { dismissStruggle("dismiss"); } }, "Keep going"));
  actions.push(el("button", { class: "btn ghost sm ink-3", onclick: function () { dismissStruggle("mute"); } }, "Don't warn again"));
  return el("div", { style: { position: "fixed", top: "16px", left: "50%", transform: "translateX(-50%)", zIndex: "60", maxWidth: "460px", width: "calc(100% - 32px)" } },
    el("div", { class: "card", style: { padding: "14px 16px", display: "flex", gap: "12px", alignItems: "flex-start", borderColor: "var(--warn)", boxShadow: "0 10px 34px rgba(0,0,0,0.20)" } }, [
      el("div", { class: "tone-tile warn", style: { width: "34px", height: "34px", flex: "0 0 auto" } }, icon("alert", 17)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600", fontSize: "13.5px" }, text: "Struggling to keep up" }),
        el("p", { class: "ink-3", style: { fontSize: "11.5px", margin: "4px 0 0" }, text: body }),
        el("div", { class: "row gap-8", style: { marginTop: "10px", flexWrap: "wrap" } }, actions),
      ]),
      // The corner X is the same as Keep going: dismiss this session's banner without muting.
      el("button", { class: "btn ghost sm", style: { flex: "0 0 auto", padding: "6px" }, onclick: function () { dismissStruggle("dismiss"); }, title: "Dismiss" }, icon("x", 14)),
    ]));
}

/* ── system audio not captured (codex H1) ─────────────────── */
// sys_state comes from the capture object via /api/status ('disabled'|'pending'|'active'|
// 'permission_denied'|'failed'); only the last two mean the meeting is missing the other side of
// the call, so only those two warn. 'disabled' (no loopback device chosen) and 'pending' (still
// opening) are normal, silent states, same as 'active'.
function sysAudioWarn(s) { return s === "permission_denied" || s === "failed"; }
// Local-only dismiss: unlike the silence/struggle nudges this is not a one-shot server event to
// acknowledge, it is a continuous health reading, so there is nothing to POST. Recording the
// dismissed VALUE (not just true/false) means a later change to a different bad state still warns.
function dismissSysAudioWarning() {
  S.live.sysAudioDismissedFor = S.live.sysState;
  render();
}
// Same floating-card shape and inject point as silenceBanner()/struggleBanner(), title static,
// body varies: permission_denied gets the extra "how to fix it" sentence, failed does not (nothing
// the user can do about it mid-meeting beyond knowing the other side is missing).
function sysAudioBanner() {
  var body = S.live.sysState === "permission_denied"
    ? "System audio isn't being captured, so only your microphone is being recorded. The other side of the call won't be in the transcript. You can allow it in System Settings > Privacy & Security, then restart the meeting."
    : "System audio isn't being captured, so only your microphone is being recorded. The other side of the call won't be in the transcript.";
  return el("div", { style: { position: "fixed", top: "16px", left: "50%", transform: "translateX(-50%)", zIndex: "60", maxWidth: "460px", width: "calc(100% - 32px)" } },
    el("div", { class: "card", style: { padding: "14px 16px", display: "flex", gap: "12px", alignItems: "flex-start", borderColor: "var(--warn)", boxShadow: "0 10px 34px rgba(0,0,0,0.20)" } }, [
      el("div", { class: "tone-tile warn", style: { width: "34px", height: "34px", flex: "0 0 auto" } }, icon("alert", 17)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600", fontSize: "13.5px" }, text: "System audio isn't being captured" }),
        el("p", { class: "ink-3", style: { fontSize: "11.5px", margin: "4px 0 0" }, text: body }),
      ]),
      el("button", { class: "btn ghost sm", style: { flex: "0 0 auto", padding: "6px" }, onclick: dismissSysAudioWarning, title: "Dismiss" }, icon("x", 14)),
    ]));
}

/* ── home (new session hub) ───────────────────────────────── */
function homeView() {
  function entry(opts) {
    return el("div", { class: "card entry-card" + (opts.primary ? " primary" : ""), role: "button", tabindex: "0", onclick: opts.onclick, onkeydown: keyActivate(opts.onclick) }, [
      el("div", { class: "row", style: { justifyContent: "space-between" } }, [
        el("div", { class: "tile" }, icon(opts.ic, 20)),
        opts.badge ? el("span", { class: "chip" + (opts.primary ? " accent" : "") }, opts.badge) : null,
      ]),
      el("div", { class: "et", text: opts.title }),
      el("div", { class: "eb", text: opts.body }),
      opts.formats ? el("div", { class: "fmt-chips" }, opts.formats.map(function (f) { return el("span", { class: "f", text: f }); })) : null,
      el("div", { class: "btn" + (opts.primary ? " primary" : "") + " block", style: { marginTop: "2px" } }, opts.cta),
    ]);
  }
  return el("div", { class: "screen center" }, el("div", { class: "screen-inner" }, [
    shouldShowBusinessNudge() ? businessNudgeCard() : null,
    el("div", { class: "screen-head" }, el("div", {}, [
      el("div", { class: "eyebrow", text: "Ready when you are" }),
      el("h1", { text: "Start a session" }),
      el("p", { class: "sub", text: "Three ways in. Pick the one that fits the moment." }),
    ])),
    el("div", { class: "entry-grid" }, [
      entry({ primary: true, ic: "mic", title: "Start a live meeting", cta: "Begin",
        body: "Transcribe what you and others are saying right now, on this computer. Optionally record the audio too.",
        onclick: function () { S.form.title = ""; S.form.context = null; go("pre"); } }),
      entry({ ic: "upload", title: "Upload a recording to transcribe", cta: "Choose a file",
        body: "Pick an audio or video file you already have. Volksmond transcribes it locally, just like a live meeting.",
        formats: [".mp3", ".m4a", ".wav", ".mp4", ".mov", ".ogg"],
        onclick: importFromPicker }),
      entry({ ic: "disk", title: "Record only, transcribe later", cta: "Start recording",
        body: "For machines that cannot keep up live. Volksmond records the audio cleanly, and you transcribe it when you are back at a desk.",
        onclick: function () { S.form.title = ""; go("recordpre"); } }),
    ]),
  ]));
}

/* ── pre-meeting (live start) ─────────────────────────────── */
function preView() {
  var dev = S.devices || {};

  var recordCard = el("div", { class: "card", style: { padding: "16px", background: S.form.record ? "var(--record-soft)" : "var(--surface)", borderColor: S.form.record ? "color-mix(in oklch, var(--record) 30%, var(--line))" : "var(--line)" } }, [
    el("div", { class: "row gap-12" }, [
      el("div", { class: "tone-tile", style: { width: "36px", height: "36px", flex: "0 0 auto", background: "var(--record-soft)", color: "var(--record)" } }, icon("dot", 16)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600", fontSize: "13.5px" }, text: "Record the audio" }),
        el("p", { class: "ink-2", style: { fontSize: "12px", marginTop: "3px" }, text: "Keeps the audio on this machine until you stop. Lets you transcribe or summarise it again later, more accurately." }),
      ]),
      toggleEl(S.form.record, function () { S.form.record = !S.form.record; render(); }, true),
    ]),
    S.form.record ? el("div", { style: { marginTop: "12px", paddingTop: "12px", borderTop: "1px solid color-mix(in oklch, var(--record) 20%, var(--line))" } }, [
      el("div", { class: "section-label", style: { color: "var(--record)" }, text: "Courtesy line you could say" }),
      el("p", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "6px", fontStyle: "italic" }, text: "Just a heads-up, I am running a tool on my machine that is taking a private transcript for my own notes. The audio does not leave my computer." }),
    ]) : null,
  ]);

  var left = el("div", {}, [
    formField("Meeting title", el("span", { class: "label-muted", text: " (optional)" }),
      el("input", { class: "field tall", value: S.form.title, placeholder: "e.g. Q3 strategy review", oninput: function (e) { S.form.title = e.target.value; } })),
    languageField(),
    engineLine(),
    advancedTranscribeControls(true),
    formField("Participants", el("span", { class: "label-muted", text: " (optional, helps accuracy)" }), termsBox(S.form.participants, "Add a name")),
    (isPro() && !offlineBuild()) ? calendarSeedRow() : null,
    formField("Jargon and terms", el("span", { class: "label-muted", text: " (optional)" }), termsBox(S.form.terms, "Add a term")),
    defaultContextNote(),
    recordCard,
  ]);

  var right = el("div", { class: "stack gap-12" }, [
    el("div", { class: "card", style: { padding: "16px" } }, [
      el("div", { class: "section-label", style: { marginBottom: "10px" }, text: "Audio sources" }),
      deviceField("Your microphone", dev.mics, S.form.mic, dev.default_mic_index, function (v) { S.form.mic = v; }),
      deviceField("System audio (everyone else)", dev.loopbacks, S.form.loopback, dev.default_loopback_index, function (v) { S.form.loopback = v; }),
      el("p", { class: "ink-3", style: { fontSize: "11.5px", margin: "0" }, text: "Your voice comes from the microphone. Everyone else comes from your computer's own audio." }),
      el("p", { class: "ink-3", style: { fontSize: "11.5px", margin: "8px 0 0" }, text: "Tip: use headphones. On speakers your microphone can re-hear the other people, and they get transcribed twice." }),
    ]),
    el("div", { class: "card", style: { padding: "16px", display: "flex", gap: "12px" } }, [
      el("div", { style: { color: "var(--ink-3)", flex: "0 0 auto" } }, icon("lock", 18)),
      el("div", {}, [
        el("div", { style: { fontWeight: "600", fontSize: "13px" }, text: "Stays on this computer" }),
        el("p", { class: "ink-3", style: { fontSize: "12px", marginTop: "2px" }, text: "No audio, transcript, or metadata is sent anywhere. Offline-safe." }),
      ]),
    ]),
  ]);

  return el("div", { class: "screen" }, el("div", { class: "screen-inner" }, [
    el("div", { class: "screen-head" }, [
      el("div", {}, [
        el("div", { class: "eyebrow", text: "Ready when you are" }),
        el("h1", { text: "Start a meeting" }),
        el("p", { class: "sub", text: "Press begin when the meeting starts. You can add names and jargon before you begin." }),
      ]),
      el("span", { class: "chip ok" }, [icon("check", 12), "On this machine"]),
    ]),
    el("div", { style: { display: "grid", gridTemplateColumns: "1fr 320px", gap: "24px", alignItems: "start" } }, [left, right]),
    el("div", { class: "row gap-16", style: { marginTop: "24px", alignItems: "center" } }, [
      _liveStarting
        ? el("button", { class: "btn primary big", disabled: true }, [el("span", { class: "spinner" }), "Starting"])
        : el("button", { class: "btn primary big", onclick: startLive }, [icon("dot", 15), "Begin"]),
      el("button", { class: "btn ghost", onclick: function () { go("home"); } }, "Back"),
      warmChip(),
      el("span", { class: "ink-3", style: { fontSize: "11.5px", marginLeft: "auto" }, text: "Audio stays on this machine unless you opt in." }),
    ]),
  ]));
}
function termsBox(list, placeholder) {
  var box = el("div", { class: "chipbox" });
  list.forEach(function (t, i) {
    box.appendChild(el("span", { class: "tag" }, [el("span", {}, raw(t)),
      el("button", { onclick: function () { list.splice(i, 1); render(); } }, icon("x", 12))]));
  });
  var inp = el("input", { placeholder: placeholder || "Add a term", onkeydown: function (e) {
    if (e.key === "Enter") { e.preventDefault(); var v = e.target.value.trim(); if (v) { list.push(v); render(); } }
  } });
  box.appendChild(inp);
  return box;
}
function defaultContextNote() {
  // The standing context saved in Settings, shown here EDITABLE so it can be tuned for just this
  // meeting. S.form.context stays null while the box merely mirrors the saved default; the moment
  // the user types it holds a per-meeting override that rides to the server as context_override and
  // is never written back to Settings. It resets to the saved default when the next meeting starts
  // (see startLive / startImport / the "Start a live meeting" entry).
  var saved = ((S.settings && S.settings.default_context) || "");
  var value = (S.form.context != null) ? S.form.context : saved;
  var hasSaved = !!saved.trim();
  return el("div", { class: "card", style: { padding: "10px 12px", marginBottom: "16px" } }, [
    el("div", { class: "row gap-6", style: { alignItems: "baseline", marginBottom: "6px" } }, [
      el("div", { class: "section-label", text: hasSaved ? "Always applied (from Settings)" : "Context for this meeting" }),
      el("span", { class: "label-muted", style: { fontSize: "11px" }, text: hasSaved ? " edit for this meeting" : " (optional)" }),
    ]),
    el("textarea", { class: "field", rows: "2", style: { fontSize: "12px", minHeight: "42px", resize: "vertical" },
      placeholder: "e.g. Thabo, Acme Corp, EBITDA. Or a sentence guiding the recogniser.",
      value: value,
      oninput: function (e) { S.form.context = e.target.value; } }),
    el("p", { class: "hint", style: { margin: "6px 0 0", fontSize: "11px" },
      text: hasSaved
        ? "Starts from your saved default. Edits here apply to this meeting only; your saved default in Settings is unchanged."
        : "Applies to this meeting only. To reuse it every time, save it in Settings." }),
  ]);
}

// Seed the Participants from the local Outlook calendar (Business feature, fully offline: the
// server reads classic Outlook over COM, no network call). Shown only to Business licences; the
// upgrade view advertises it to everyone else. Adds attendee names to the chips and fills an empty
// title from the meeting subject.
function calendarSeedRow() {
  return el("div", { class: "row gap-8", style: { marginTop: "-4px", marginBottom: "2px" } }, [
    el("button", { class: "btn ghost sm", onclick: pullFromCalendar },
      [icon("calendar", 13), el("span", { text: "Pull from Outlook calendar" })]),
    el("span", { class: "ink-3", style: { fontSize: "11px" }, text: "Reads your current meeting on this computer. Nothing is sent anywhere." }),
  ]);
}
async function pullFromCalendar() {
  try {
    var r = await api.post("/api/calendar-seed");
    if (!r.found) { toast(tr("No current or upcoming meeting found in Outlook.")); return; }
    var added = 0;
    (r.attendees || []).forEach(function (n) {
      if (n && S.form.participants.indexOf(n) < 0) { S.form.participants.push(n); added++; }
    });
    if (r.subject && !(S.form.title || "").trim()) S.form.title = r.subject;
    toast(added ? (tr("Added names from your calendar.")) : tr("Calendar meeting found; those names are already added."));
    render();
  } catch (e) {
    if (e && /business licence/i.test(e.message || "")) { toast(tr("Pulling from your calendar is a business feature.")); go("upgrade"); return; }
    toast(e.message || "Could not read the calendar.", true);
  }
}

/* ── import setup (context before transcribing a file) ──────── */
function importPreView() {
  var fileLabel = S.importName || "the recording";
  function begin() { startImport({ path: S.importPath, stem: S.importStem, topic: S.form.title }); }
  return el("div", { class: "screen" }, el("div", { class: "screen-inner col-mid" }, [
    el("div", { class: "screen-head" }, [
      el("div", {}, [
        el("div", { class: "eyebrow", text: "Before we transcribe" }),
        el("h1", { text: "Add context" }),
        el("p", { class: "sub", text: "Names and jargon help accuracy, especially for Afrikaans and the mix. All optional." }),
      ]),
      el("span", { class: "chip ok" }, [icon("check", 12), "On this machine"]),
    ]),
    el("div", { class: "card", style: { padding: "13px 16px", display: "flex", gap: "12px", alignItems: "center", marginBottom: "18px" } }, [
      el("div", { class: "tone-tile accent", style: { width: "34px", height: "34px", flex: "0 0 auto" } }, icon("upload", 17)),
      el("div", { class: "mono", style: { fontSize: "13px", fontWeight: "600", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: "0" } }, raw(fileLabel)),
    ]),
    formField("Title", el("span", { class: "label-muted", text: " (optional)" }),
      el("input", { class: "field tall", value: S.form.title, placeholder: "e.g. Physio discovery call", oninput: function (e) { S.form.title = e.target.value; } })),
    languageField(),
    engineLine(),
    advancedTranscribeControls(),
    formField("Participants", el("span", { class: "label-muted", text: " (optional, helps accuracy)" }), termsBox(S.form.participants, "Add a name")),
    formField("Jargon and terms", el("span", { class: "label-muted", text: " (optional)" }), termsBox(S.form.terms, "Add a term")),
    defaultContextNote(),
    el("div", { class: "row gap-16", style: { marginTop: "8px", alignItems: "center" } }, [
      el("button", { class: "btn primary big", onclick: begin }, [icon("note", 15), "Transcribe"]),
      el("button", { class: "btn ghost", onclick: function () { go("home"); } }, "Back"),
      warmChip(),
    ]),
  ]));
}

/* ── starting (immediate feedback while the model loads) ───── */
// Begin / Transcribe loads the Whisper model synchronously on the server (seconds
// once cached, minutes if it still has to download). Show this at once instead of
// leaving the user on a frozen pre-meeting screen with no sign anything is happening.
function beginStarting(kind, title) {
  S.starting = { active: true, kind: kind, title: title || "", error: null, startedAt: new Date().toISOString() };
  go("starting");
  if (startingTimer) clearInterval(startingTimer);
  startingTimer = setInterval(function () {
    if (startingElapsedEl) startingElapsedEl.textContent = fmtElapsed(S.starting.startedAt);
  }, 1000);
}
function endStarting() {
  if (startingTimer) { clearInterval(startingTimer); startingTimer = null; }
  S.starting.active = false;
}
function startingView() {
  startingElapsedEl = el("span", { class: "mono", text: fmtElapsed(S.starting.startedAt) });
  var err = S.starting.error;
  var inner;
  if (err) {
    inner = el("div", { class: "rec-stage" }, [
      el("div", { class: "tone-tile", style: { width: "44px", height: "44px", color: "var(--warn)" } }, icon("alert", 22)),
      el("h1", { style: { fontSize: "22px" }, text: "Could not start" }),
      el("p", { class: "ink-2", style: { maxWidth: "440px", textAlign: "center" } }, raw(err)),
      el("div", { class: "row gap-8" }, [
        el("button", { class: "btn primary", onclick: function () { endStarting(); go(S.starting.kind === "file" ? "importpre" : "pre"); } }, "Back"),
        el("button", { class: "btn ghost", onclick: function () { endStarting(); go("settings"); } }, "Set up models"),
      ]),
    ]);
  } else {
    // File import still loads the model on this screen (synchronous), so it keeps the original copy.
    // Live (t0-capture) returns the instant capture is up, so this screen is only a brief flash before
    // the live view, where the model loads behind a "preparing" chip; give live its own honest copy.
    var isFile = S.starting.kind === "file";
    var lead = isFile
      ? "Loading the transcription model on your computer. The first time you use a quality level can take a moment, and if that model still needs downloading it can take a few minutes."
      : "Opening your microphone and system audio and starting to capture. This is quick.";
    var sub = isFile
      ? "You can keep this open. It switches to the transcript by itself."
      : "The live screen opens by itself. The transcript fills in from the start once the model is ready.";
    inner = el("div", { class: "rec-stage" }, [
      el("span", { class: "spinner" }),
      el("h1", { style: { fontSize: "22px", marginTop: "8px" }, text: "Starting" }),
      startingElapsedEl,
      el("p", { class: "ink-2", style: { maxWidth: "470px", textAlign: "center" }, text: lead }),
      el("p", { class: "ink-3", style: { fontSize: "12px" }, text: sub }),
    ]);
  }
  var header = el("div", { class: "live-header" }, [
    el("div", { style: { minWidth: "0" } }, [
      el("div", { class: "ttl" }, [S.starting.title ? raw(S.starting.title) : "Starting"]),
      el("div", { class: "meta" }, [el("span", { text: err ? "Not started" : "Preparing" }), el("span", { text: "·" }), el("span", { text: "Local only" })]),
    ]),
    el("div", { class: "right" }, el("span", { class: "chip" + (err ? " warn" : "") }, [el("span", { class: "dot" }), err ? "Stopped" : "Preparing"])),
  ]);
  return el("div", { class: "live" }, [header, el("div", { class: "live-body" }, inner)]);
}

/* ── live screen ──────────────────────────────────────────── */
// Honest GPU/CPU badge for the active screens, so the user never has to open Task
// Manager to find out where transcription is actually running.
function deviceBadge(tier) {
  if (!tier) return null;
  if (tier && tier.indexOf("gpu") === 0) {   // gpu, gpu-4gb, gpu-turbo, gpu-medium, gpu-small
    return el("span", { class: "chip ok", title: (S.cuda && S.cuda.gpu_name) || "GPU" }, [icon("check", 12), "GPU"]);
  }
  if (tier.indexOf("mlx") === 0) {           // mlx, mlx-turbo: the Apple GPU via MLX (Mac only)
    return el("span", { class: "chip ok", title: "Apple GPU" }, [icon("check", 12), "Apple GPU"]);
  }
  return el("span", { class: "chip" }, "CPU");
}

/* ── meeting notes (typed live, saved beside the transcript) ── */
// Notes are the user's own words, saved to <stem>-notes.md as they type. They never touch the
// transcript, and only reach a summary if the user opts in (see summariseCard). The save is
// debounced; typing does not re-render, so the textarea keeps focus while transcription streams.
var notesSaveTimer = null;
function liveStem() { return baseName(S.live.outputPath || "").replace(/\.md$/, ""); }
function scheduleNotesSave() {
  if (notesSaveTimer) clearTimeout(notesSaveTimer);
  // Capture the stem AND the text NOW: if the route or session changes before the debounce
  // fires, the save still writes this text to the session it belongs to, never a later one's.
  var stem = liveStem(), text = S.live.notes || "";
  notesSaveTimer = setTimeout(function () {
    notesSaveTimer = null;
    if (stem) api.post("/api/notes", { stem: stem, text: text }).catch(function () {});
  }, 700);
}
function saveNotesNow() {
  if (notesSaveTimer) { clearTimeout(notesSaveTimer); notesSaveTimer = null; }
  var stem = liveStem();
  if (!stem) return;
  api.post("/api/notes", { stem: stem, text: S.live.notes || "" }).catch(function () {});
}
// The notes now live in a resizable right-hand column on the live screen. notesOpen keeps its
// old meaning: open = the full column, closed = a slim rail with a Notes button. The width is
// dragged via the splitter, which mutates a CSS variable directly, NEVER render(): a render
// mid-drag would rebuild the transcript DOM and steal the textarea's focus.
var NOTES_W_DEFAULT = 300, NOTES_W_MIN = 220;
var _notesW = 0;   // width picked up by the drag in progress, persisted on pointer-up
function clampNotesWidth(w) {
  var max = Math.max(NOTES_W_MIN, Math.floor(window.innerWidth * 0.6));
  return Math.max(NOTES_W_MIN, Math.min(max, Math.round(w)));
}
function notesWidth() {
  var w = 0;
  try { w = parseInt(localStorage.getItem("vm_live_split") || "0", 10) || 0; } catch (e) {}
  // localStorage first, settings.json as the durable mirror (the WebView can wipe localStorage).
  if (!w && S.settings && S.settings.live_notes_width) w = parseInt(S.settings.live_notes_width, 10) || 0;
  return w ? clampNotesWidth(w) : NOTES_W_DEFAULT;
}
function persistNotesWidth() {
  if (!_notesW) return;
  try { localStorage.setItem("vm_live_split", String(_notesW)); } catch (e) {}
  api.post("/api/settings", { live_notes_width: _notesW })
    .then(function (s) { if (s) S.settings = s; }).catch(function () {});
}
function resetNotesWidth(splitEl) {
  _notesW = 0;
  try { localStorage.removeItem("vm_live_split"); } catch (e) {}
  api.post("/api/settings", { live_notes_width: 0 })
    .then(function (s) { if (s) S.settings = s; }).catch(function () {});
  splitEl.style.setProperty("--vm-notes-w", NOTES_W_DEFAULT + "px");
}
function splitHandle(splitEl) {
  var h = el("div", { class: "split-handle", title: "Drag to resize. Double-click to reset." });
  h.addEventListener("pointerdown", function (e) {
    e.preventDefault();
    try { h.setPointerCapture(e.pointerId); } catch (x) {}
    h.classList.add("dragging");
    function move(ev) {
      var w = clampNotesWidth(splitEl.getBoundingClientRect().right - ev.clientX);
      _notesW = w;
      splitEl.style.setProperty("--vm-notes-w", w + "px");
    }
    function up() {
      h.classList.remove("dragging");
      h.removeEventListener("pointermove", move);
      h.removeEventListener("pointerup", up);
      h.removeEventListener("pointercancel", up);
      persistNotesWidth();
    }
    h.addEventListener("pointermove", move);
    h.addEventListener("pointerup", up);
    h.addEventListener("pointercancel", up);
  });
  h.addEventListener("dblclick", function () { resetNotesWidth(splitEl); });
  return h;
}
function notesCol() {
  var collapse = function () { S.live.notesOpen = false; render(); };
  var head = el("div", { class: "notes-head", role: "button", tabindex: "0", onclick: collapse, onkeydown: keyActivate(collapse), title: "Collapse notes" }, [
    icon("note", 14),
    el("span", { text: "Your notes" }),
    (S.live.notes && S.live.notes.trim()) ? el("span", { class: "chip muted", text: "saved on this computer" }) : null,
    el("span", { class: "grow" }),
    icon("chevRight", 14),
  ]);
  var ta = el("textarea", { class: "field notes-ta", value: S.live.notes || "",
    placeholder: "Jot notes as the meeting goes: decisions, names, to-dos. Saved with this meeting on your computer. When you summarise, you choose whether to fold them in.",
    oninput: function (e) { S.live.notes = e.target.value; S.live.notesTouched = true; scheduleNotesSave(); } });
  return el("div", { class: "notes-col" }, [head, ta]);
}
function notesRail() {
  var expand = function () { S.live.notesOpen = true; render(); };
  return el("div", { class: "notes-rail" },
    el("button", { class: "rail-btn", onclick: expand, title: "Open notes" }, [
      icon("note", 15),
      el("span", { class: "vert", text: "Notes" }),
    ]));
}

// The live-screen empty-transcript panel while the model is still preparing, or a bounded failure
// with Retry. Non-blocking: it sits inside the normal .doc area, chrome and audio strip intact.
function preparePanel(failMsg) {
  var L = S.live;
  if (failMsg) {
    return el("div", { class: "rec-stage", style: { maxWidth: "520px", margin: "0 auto" } }, [
      el("div", { class: "tone-tile warn", style: { width: "44px", height: "44px" } }, icon("alert", 22)),
      el("p", { class: "ink-2", style: { textAlign: "center", maxWidth: "440px" } }, raw(failMsg)),
      el("p", { class: "ink-3", style: { fontSize: "12px", textAlign: "center", maxWidth: "440px" },
        text: L.recording
          ? "Your audio is still recording safely on this computer. Stop when you are done and transcribe the recording later."
          : "Recording is off, so there is no live transcript. Set up the model in Settings, then start again." }),
      el("div", { class: "prep-actions" }, [
        el("button", { class: "btn primary", onclick: retryPrepare }, [icon("download", 14), "Retry"]),
        el("button", { class: "btn ghost", onclick: function () { go("settings"); } }, "Set up models"),
      ]),
    ]);
  }
  var p = L.prepare || {};
  var phase = p.phase || (L.preparing ? "loading" : "");
  var reassure = (L.recording ? tr("Capturing and recording now on this computer.") : tr("Capturing now on this computer."))
    + " " + tr("The transcription model is still loading. The transcript fills in from the start the moment it is ready, and if you are recording, the audio is saved from the very beginning.");
  var progressBlock;
  if (phase === "downloading") {
    var total = p.total || 0, dl = p.downloaded || 0;
    var pct = total ? Math.min(100, Math.round(dl * 100 / total)) : 0;
    progressBlock = el("div", { style: { width: "100%", maxWidth: "420px" } }, [
      el("div", { style: { fontWeight: "600", fontSize: "13px", textAlign: "center" } },
        [el("span", { text: "Downloading" }), raw(" " + (p.label || p.model || ""))]),
      total ? voiceProgressBar(pct) : el("div", { class: "track", style: { marginTop: "10px" } }, el("div", { class: "indeterminate" })),
      el("div", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "6px", textAlign: "center" },
        text: total ? trFmt("{done} of {total} ({pct}%)", { done: fmtGB(dl), total: fmtGB(total), pct: pct })
                    : (fmtGB(dl) + " " + tr("downloaded so far")) }),
    ]);
  } else {
    // Loading. The server owns the clock (p.elapsed), so the counter keeps counting across a page
    // reload and matches the budget the server is actually holding it to. The hint appears only
    // once the server says the wait is a long one (CPU past its hint threshold), so a fast machine
    // never sees it.
    progressBlock = el("div", { style: { width: "100%", maxWidth: "420px" } }, [
      el("div", { style: { fontWeight: "600", fontSize: "13px", textAlign: "center" } }, [
        el("span", { text: "Loading into memory" }),
        p.elapsed ? el("span", { class: "mono ink-3", style: { marginLeft: "8px", fontWeight: "500" } },
          raw(fmtSecs(p.elapsed))) : null,
      ]),
      el("div", { class: "track", style: { marginTop: "10px" } }, el("div", { class: "indeterminate" })),
      p.slow ? el("div", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "6px", textAlign: "center" },
        text: "First load on this computer can take a few minutes. It is faster next time." }) : null,
    ]);
  }
  return el("div", { class: "rec-stage", style: { maxWidth: "520px", margin: "0 auto" } }, [
    el("span", { class: "spinner" }),
    el("p", { class: "ink-2", style: { textAlign: "center", maxWidth: "460px" }, text: reassure }),
    progressBlock,
  ]);
}

function liveView() {
  // t0-capture: while the model loads the screen is fully live (Stop, audio strip, elapsed all key
  // off running/startedAt), only the status chip and the empty-transcript line say "preparing". A
  // model-load failure shows a clear but non-alarming chip; capture/recording carry on regardless.
  var fail = liveFailureMsg();
  var statusChip;
  if (S.live.stopping) statusChip = el("span", { class: "chip warn" }, [el("span", { class: "dot" }), el("span", { id: "live-status-text", text: "Finishing" })]);
  else if (S.live.transcribing && fail) statusChip = el("span", { class: "chip warn" }, [icon("alert", 12), el("span", { text: "Transcription unavailable" })]);
  else if (S.live.transcribing && !S.live.modelReady) statusChip = el("span", { class: "chip prep" }, [el("span", { class: "dot" }), el("span", { text: "Preparing transcription model" })]);
  else if (S.live.transcribing) statusChip = el("span", { class: "chip rec" }, [el("span", { class: "dot" }), el("span", { text: "Listening" })]);
  else statusChip = el("span", { class: "chip ok" }, [el("span", { class: "dot" }), el("span", { text: "Saved" })]);

  elapsedEl = el("span", { class: "mono", text: fmtElapsed(S.live.startedAt) });
  var langLabel = (S.live.language === "auto" || !S.live.language) ? "Auto-detect" : langName(S.live.language);

  var header = el("div", { class: "live-header" }, [
    el("div", { style: { minWidth: "0" } }, [
      el("div", { class: "ttl" }, [S.live.title ? raw(S.live.title) : "Live meeting"]),
      el("div", { class: "meta" }, [elapsedEl, el("span", { text: "·" }), el("span", { text: langLabel }), el("span", { text: "·" }), el("span", { text: "Local only" })]),
    ]),
    el("div", { class: "right" }, [
      deviceBadge(S.live.tier),
      S.live.family ? familyChip(S.live.family, S.live.model) : null,
      statusChip,
    ]),
  ]);

  // Empty-transcript area (before the first segment lands): a NON-BLOCKING live panel inside the
  // normal live shell, never a full-screen takeover. While the model is failing it shows the
  // bounded error + Retry; while it is still preparing it shows the reassurance line plus a real
  // determinate download bar (or a labelled "Loading into memory" once the bytes are in).
  var docContent;
  if (S.live.segments.length) docContent = S.live.segments.map(segRow);
  else if (S.live.transcribing && fail) docContent = preparePanel(fail);
  else if (S.live.transcribing && !S.live.modelReady) docContent = preparePanel(null);
  else docContent = el("div", { class: "empty", text: "Listening. The transcript appears here as people talk." });
  liveDocEl = el("div", { class: "doc" }, docContent);
  liveBodyEl = el("div", { class: "live-body" }, liveDocEl);
  setTimeout(function () { if (liveBodyEl) liveBodyEl.scrollTop = liveBodyEl.scrollHeight; }, 0);

  var stopBtn;
  if (S.live.stopping) {
    stopBtn = el("button", { class: "btn", disabled: true }, [el("span", { class: "spinner" }), "Finishing"]);
  } else if (S.live.recording) {
    stopBtn = el("button", { class: "btn primary", onclick: function () { S.stopMenuOpen = !S.stopMenuOpen; render(); }, id: "stop-anchor" }, [icon("stop", 14), "Stop", icon("chevDown", 14)]);
  } else {
    stopBtn = el("button", { class: "btn primary", onclick: function () { doStop("all"); } }, [icon("stop", 14), "Stop and save"]);
  }

  // Recording indicator once recording is on; before that, on a transcribing session that has
  // never recorded, a standalone "Record from here" button (for "I forgot to record") that starts
  // recording mid-session. recordingStarted is latched, so once a session has recorded the button
  // stays gone (a restart would clobber the WAV). Independent of the banner; both call recordFromHere().
  var recSlot;
  if (S.live.recording) recSlot = el("span", { class: "rec-ind" }, [el("i"), "Recording audio"]);
  else if (S.live.transcribing && !S.live.recordingStarted) recSlot = el("button", { class: "btn sm record", onclick: function () { recordFromHere(); } }, [icon("dot", 12), "Record from here"]);
  else recSlot = null;

  var footer = el("div", { class: "live-footer" }, [
    stopBtn,
    recSlot,
    el("span", { class: "grow" }),
    S.live.outputPath ? el("span", { class: "saving" }, ["Saving to ", el("span", { class: "mono", text: baseName(S.live.outputPath) })]) : null,
  ]);

  // Transcript left, notes right (or a slim rail when collapsed), splitter between.
  var split = el("div", { class: "live-split" }, [liveBodyEl]);
  split.style.setProperty("--vm-notes-w", notesWidth() + "px");
  if (S.live.notesOpen) { split.appendChild(splitHandle(split)); split.appendChild(notesCol()); }
  else { split.appendChild(notesRail()); }

  return el("div", { class: "live" }, [header, liveAudioStrip(), liveTuneStrip(), split, footer]);
}

/* ── record only ──────────────────────────────────────────── */
function recordOnlyView() {
  if (S.live.running) {
    recTimerEl = el("div", { class: "rec-timer", text: fmtElapsed(S.live.startedAt) });
    var header = el("div", { class: "live-header" }, [
      el("div", {}, [
        el("div", { class: "ttl" }, [S.live.title ? raw(S.live.title) : "Recording"]),
        el("div", { class: "meta" }, [el("span", { text: "Recording only, not transcribing yet" }), el("span", { text: "·" }), el("span", { text: "Local only" })]),
      ]),
      el("div", { class: "right" }, el("span", { class: "chip rec" }, [el("span", { class: "dot" }), "Recording"])),
    ]);
    var body = el("div", { class: "live-body" }, el("div", { class: "rec-stage" }, [
      el("div", { class: "rec-pulse" }, el("div", { class: "core" }, el("i"))),
      recTimerEl,
      el("p", { class: "ink-2", style: { maxWidth: "420px", textAlign: "center" }, text: "Recording cleanly. No transcript is being made right now. When you stop, you can transcribe it here." }),
      S.live.outputPath ? el("div", { class: "ink-3", style: { fontSize: "12px" } }, ["Saving to ", el("span", { class: "mono", text: baseName(S.live.audioStem || S.live.outputPath) + ".wav" })]) : null,
    ]));
    var footer = el("div", { class: "live-footer", style: { justifyContent: "center" } }, [
      S.live.stopping
        ? el("button", { class: "btn", disabled: true }, [el("span", { class: "spinner" }), "Saving"])
        : el("button", { class: "btn record", onclick: stopRecordOnly }, [icon("stop", 14), "Stop recording"]),
    ]);
    return el("div", { class: "live" }, [header, liveAudioStrip(), body, footer]);
  }
  // stopped: handoff (the shell supplies .main now that this route renders with the sidebar)
  var stem = S.finish.recordingStem;
  return el("div", { class: "screen center" }, el("div", { class: "screen-inner col-narrow stack", style: { gap: "18px" } }, [
    el("div", { class: "row gap-12" }, [
      el("div", { class: "tone-tile ok", style: { width: "40px", height: "40px" } }, icon("check", 20)),
      el("div", {}, [el("h1", { style: { fontSize: "24px" }, text: "Recording saved." }),
        S.finish.outputPath ? el("div", { class: "ink-3 mono", style: { fontSize: "12px", marginTop: "4px" }, text: baseName(stem || S.finish.outputPath) + ".wav" }) : null]),
    ]),
    el("div", { class: "card disclosure accent", style: { padding: "20px" } }, [
      el("div", { class: "row gap-12", style: { marginBottom: "10px" } }, [
        el("div", { class: "tone-tile accent", style: { width: "36px", height: "36px" } }, icon("mic", 18)),
        el("div", { style: { fontWeight: "600", fontSize: "15px" }, text: "Transcribe this recording now?" }),
      ]),
      el("p", { class: "ink-2", style: { fontSize: "13px" }, text: "Volksmond will read the file and write it out. Slower than live, but more accurate. You can keep working while it runs. Stays on this computer." }),
      el("div", { class: "row gap-8", style: { marginTop: "16px" } }, [
        el("button", { class: "btn primary", onclick: function () { if (stem) { S.importStem = stem; S.importPath = null; S.importName = baseName(stem) + ".wav"; S.form.title = S.live.title || ""; go("importpre"); } else { toast("Recording path missing.", true); } } }, "Transcribe this recording now"),
        el("button", { class: "btn ghost", onclick: function () { go("home"); } }, "Transcribe later"),
      ]),
    ]),
    el("p", { class: "ink-3", style: { fontSize: "12px" } }, ["You can transcribe a recording any time from ", el("span", { class: "link", onclick: function () { go("history"); } }, "History"), ". Recordings are kept until you delete them."]),
  ]));
}

/* ── importing ────────────────────────────────────────────── */
function importingView() {
  elapsedEl = el("span", { class: "mono", text: fmtElapsed(S.live.startedAt) });
  var header = el("div", { class: "live-header" }, [
    el("div", { class: "row gap-10", style: { minWidth: "0" } }, [
      el("div", { class: "tone-tile accent", style: { width: "32px", height: "32px", flex: "0 0 auto" } }, icon("upload", 16)),
      el("div", { style: { minWidth: "0" } }, [
        el("div", { class: "ttl", text: "Transcribing: " + (S.live.importName || "recording") }),
        el("div", { class: "meta" }, [elapsedEl, el("span", { text: "·" }), el("span", { text: "Local only" })]),
      ]),
    ]),
    el("div", { class: "right" }, [
      deviceBadge(S.live.tier),
      S.live.family ? familyChip(S.live.family, S.live.model) : null,
      el("button", { class: "btn ghost", onclick: function () { api.post("/api/stop?what=all").catch(function () {}); toast("Stopping."); go("home"); } }, "Cancel"),
    ]),
  ]);
  var strip = el("div", { class: "track thin", style: { borderRadius: "0" } }, el("div", { class: "indeterminate" }));
  liveDocEl = el("div", { class: "doc" }, S.live.segments.length
    ? S.live.segments.map(segRow)
    : el("div", { class: "empty", text: "Reading the file. The transcript appears here as it goes." }));
  liveBodyEl = el("div", { class: "live-body" }, liveDocEl);
  var footer = el("div", { class: "live-footer" }, [
    el("span", { class: "spinner" }),
    el("span", { class: "ink-2", style: { fontSize: "12.5px" }, text: "Reading the file. You can leave this open or come back later." }),
    el("span", { class: "grow" }),
    S.live.outputPath ? el("span", { class: "saving" }, ["Will save to ", el("span", { class: "mono", text: baseName(S.live.outputPath) })]) : null,
  ]);
  return el("div", { class: "live" }, [header, strip, liveBodyEl, footer]);
}

/* ── finish & save ────────────────────────────────────────── */
function finishView() {
  var name = baseName(S.finish.outputPath);
  return el("div", { class: "screen center" }, el("div", { class: "screen-inner col-mid stack", style: { gap: "16px" } }, [
    el("div", { class: "row gap-12" }, [
      el("div", { class: "tone-tile ok", style: { width: "40px", height: "40px" } }, icon("check", 20)),
      el("div", {}, [el("h1", { style: { fontSize: "24px" }, text: S.finish.sinkError ? "Finished, with a warning" : "Saved." }),
        S.finish.outputPath ? el("div", { class: "ink-3 mono", style: { fontSize: "12px", marginTop: "4px" } }, raw(S.finish.outputPath)) : null]),
    ]),
    S.finish.sinkError ? el("div", { class: "card", style: { padding: "14px 16px", borderColor: "var(--warn)", display: "flex", gap: "12px", alignItems: "flex-start" } }, [
      el("span", { style: { color: "var(--warn)", display: "inline-flex", flex: "0 0 auto", marginTop: "1px" } }, icon("alert", 18)),
      el("div", {}, [
        el("div", { style: { fontWeight: "600" }, text: "Saving may not have completed" }),
        el("div", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "2px" } }, raw(S.finish.sinkError)),
      ]),
    ]) : null,
    el("div", { class: "card", style: { padding: "18px" } }, [
      el("div", { style: { fontWeight: "600" } }, raw(S.finish.title || topicFromName(name))),
      el("div", { class: "row gap-8", style: { marginTop: "14px" } }, [
        el("button", { class: "btn", onclick: function () { api.post("/api/open-folder").catch(function (e) { toast(e.message, true); }); } }, [icon("folder", 15), "Open folder"]),
        el("button", { class: "btn", onclick: function () { openReader(name); } }, [icon("note", 15), "Open transcript"]),
        S.finish.hasNotes ? el("button", { class: "btn", onclick: function () { openReader(name, "notes"); } }, [icon("pencil", 15), "Open my notes"]) : null,
        el("button", { class: "btn ghost", onclick: async function () { try { var t = await api.text("/sessions/" + encodeURIComponent(name)); copyText(t); } catch (e) { toast(e.message, true); } } }, [icon("copy", 15), "Copy"]),
      ]),
    ]),
    summariseCard(name, "finish"),
    el("div", { class: "row", style: { marginTop: "4px" } }, [
      el("span", { class: "grow" }),
      el("button", { class: "btn ghost", onclick: function () { go("home"); } }, "Done"),
    ]),
  ]));
}
// The "include my notes" choice, shown on the summary card only when this session has notes.
// The notes are saved either way; this only decides whether the summary is told to use them.
function notesIncludeRow(target) {
  if (!target.hasNotes) return null;
  var on = target.includeNotes !== false;
  var flip = function () { target.includeNotes = !on; render(); };
  return el("div", { class: "row gap-10", style: { alignItems: "flex-start", marginTop: "12px", paddingTop: "12px", borderTop: "1px solid var(--line)" } }, [
    toggleEl(on, flip),
    el("div", { class: "grow", role: "button", tabindex: "0", onclick: flip, onkeydown: keyActivate(flip), style: { cursor: "pointer" } }, [
      el("div", { style: { fontWeight: "500", fontSize: "12.5px" }, text: "Include my notes in this summary" }),
      el("div", { class: "ink-3", style: { fontSize: "11px", marginTop: "2px" }, text: "Your notes stay saved with the meeting either way. This tells the summary to treat them as your own record." }),
    ]),
  ]);
}
/* ── reader notes (add or edit your own notes for a past session) ── */
// The reader's notes tab is fully editable, so notes can be added or fixed AFTER the meeting (during
// a live call there is often too much going on to write them). It autosaves to <stem>-notes.md, the
// same sidecar the live panel writes, so nothing new is needed on the backend.
var readerNotesSaveTimer = null;
function scheduleReaderNotesSave() {
  if (readerNotesSaveTimer) clearTimeout(readerNotesSaveTimer);
  readerNotesSaveTimer = setTimeout(saveReaderNotesNow, 700);
}
function saveReaderNotesNow() {
  if (readerNotesSaveTimer) { clearTimeout(readerNotesSaveTimer); readerNotesSaveTimer = null; }
  if (!S.reader.stem) return;
  api.post("/api/notes", { stem: S.reader.stem, text: S.reader.notes || "" }).catch(function () {});
}
// Edit-then-summarise, the workflow Sean asked for: flush the notes, mark them for inclusion, move
// to the Summary tab, and (re)run the summary. Regenerating is safe: the server archives the prior
// summary rather than overwriting it.
function readerSummariseWithNotes() {
  saveReaderNotesNow();
  S.reader.hasNotes = !!(S.reader.notes && S.reader.notes.trim());
  S.reader.includeNotes = true;
  S.reader.tab = "summary";
  if (summaryInstalled() && !S.reader.summarising) {
    S.reader.summary = null;
    doSummarise(S.reader.name, "reader");
  } else {
    render();   // Summary tab shows the "set up summaries" hint, or the run already in progress
  }
}
function readerNotesTab() {
  var has = !!(S.reader.notes && S.reader.notes.trim());
  var ta = el("textarea", { class: "field notes-ta", value: S.reader.notes || "",
    placeholder: "Add your own notes for this meeting: decisions, names, to-dos, anything you did not catch during the call. Saved with this meeting on your computer.",
    oninput: function (e) { S.reader.notes = e.target.value; S.reader.hasNotes = !!e.target.value.trim(); scheduleReaderNotesSave(); } });
  return el("div", { class: "stack", style: { gap: "16px" } }, [
    el("div", { class: "card", style: { padding: "16px 18px" } }, [
      el("div", { class: "row gap-8", style: { alignItems: "center", marginBottom: "8px" } }, [
        el("span", { style: { color: "var(--accent)", display: "inline-flex" } }, icon("pencil", 15)),
        el("div", { style: { fontWeight: "600" }, text: "My notes" }),
        has ? el("span", { class: "chip muted", text: "saved on this computer" }) : null,
        el("span", { class: "grow" }),
        has ? el("button", { class: "btn ghost sm", onclick: function () { copyText(S.reader.notes); } }, [icon("copy", 12), "Copy"]) : null,
      ]),
      ta,
      el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "8px" }, text: "Your notes are never mixed into the transcript. They stay on this computer, and you decide whether a summary uses them." }),
    ]),
    (has && summaryInstalled()) ? el("div", { class: "row gap-8", style: { justifyContent: "flex-end" } }, [
      el("button", { class: "btn", onclick: readerSummariseWithNotes }, [icon("sparkle", 14), S.reader.summary ? "Update summary with these notes" : "Summarise with these notes"]),
    ]) : null,
  ]);
}
function summariseCard(fileName, scope) {
  var target = scope === "reader" ? S.reader : S.finish;
  if (target.summary) {
    return summaryResult(target.summary, target.savedAs, fileName, scope);
  }
  if (!summaryInstalled()) {
    return el("div", { class: "card", style: { padding: "18px", display: "flex", gap: "14px", alignItems: "flex-start" } }, [
      el("div", { class: "tone-tile muted", style: { width: "30px", height: "30px", flex: "0 0 auto" } }, icon("sparkle", 16)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600" }, text: "Summarise this transcript" }),
        el("p", { class: "ink-3", style: { fontSize: "12.5px", marginTop: "4px" }, text: "No summary model is set up on this computer yet. Choose one in Settings, then summaries run here, fully on-device." }),
      ]),
      el("button", { class: "btn", onclick: function () { go("settings"); } }, "Set up summaries"),
    ]);
  }
  if (target.summarising) {
    return el("div", { class: "card disclosure accent", style: { padding: "18px" } }, [
      el("div", { class: "row gap-10" }, [el("span", { class: "spinner" }), el("div", { style: { fontWeight: "600" }, text: "Working on your summary" })]),
      el("p", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "8px" }, text: "Reading the full transcript on this machine. This takes a little while." }),
    ]);
  }
  return el("div", { class: "card disclosure accent", style: { padding: "18px" } }, [
    el("div", { class: "row gap-12", style: { alignItems: "flex-start" } }, [
      el("div", { class: "tone-tile accent", style: { width: "30px", height: "30px", flex: "0 0 auto" } }, icon("sparkle", 16)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600" }, text: "Summarise this transcript" }),
        el("p", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "4px" }, text: "Runs on this computer using your installed model. Pick a style, or write your own instructions." }),
      ]),
    ]),
    el("div", { style: { marginTop: "12px" } }, summaryStyleControl(target)),
    notesIncludeRow(target),
    el("div", { class: "row gap-8", style: { alignItems: "center", justifyContent: "flex-end", marginTop: "12px" } }, [
      el("span", { class: "ink-3", style: { fontSize: "11.5px" }, text: "Summary in" }),
      selectEl([["en", "English"], ["af", "Afrikaans"]], (target.sumLang || "en"), function (v) { target.sumLang = v; render(); }),
      el("button", { class: "btn primary", onclick: function () { doSummarise(fileName, scope); } }, [icon("sparkle", 14), "Summarise"]),
    ]),
  ]);
}
function summaryResult(summary, savedAs, fileName, scope) {
  var target = scope === "reader" ? S.reader : S.finish;
  var card = el("div", { class: "card sum-card" }, [
    el("div", { class: "head" }, [
      el("div", { class: "tile" }, icon("sparkle", 15)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600" }, text: "Latest summary" }),
        el("div", { class: "ink-3", style: { fontSize: "11.5px" }, text: "Ran on this computer, saved next to the transcript" }),
      ]),
      el("button", { class: "btn ghost sm", onclick: function () { copyText(summary); } }, [icon("copy", 13), "Copy"]),
    ]),
    el("div", { class: "sum-body" }, renderMarkdown(summary)),
    savedAs ? el("div", { class: "saved-strip" }, [icon("check", 14), el("span", {}, ["Saved as ", el("span", { class: "mono", text: baseName(savedAs) }), ", next to the transcript. Nothing was sent off this computer."])]) : null,
    // Regenerate in a different style or your own words. Reuses the same picker as the
    // pre-summarise card, so a fresh run overwrites the saved summary with the new shape.
    el("div", { style: { marginTop: "14px", paddingTop: "14px", borderTop: "1px solid var(--line)" } }, [
      el("div", { class: "section-label", style: { marginBottom: "8px" }, text: "Make another summary" }),
      summaryStyleControl(target),
      notesIncludeRow(target),
      el("div", { class: "row gap-8", style: { alignItems: "center", justifyContent: "flex-end", marginTop: "10px" } }, [
        el("span", { class: "ink-3", style: { fontSize: "11.5px" }, text: "Summary in" }),
        selectEl([["en", "English"], ["af", "Afrikaans"]], (target.sumLang || "en"), function (v) { target.sumLang = v; render(); }),
        el("button", { class: "btn", onclick: function () { target.summary = null; doSummarise(fileName, scope); } }, [icon("sparkle", 13), "Regenerate"]),
      ]),
    ]),
  ]);
  return card;
}

/* ── history ──────────────────────────────────────────────── */
var histQuery = "";
function statChip(label, ico, cls) {
  return el("span", { class: "chip " + (cls || "muted") }, [icon(ico, 11), el("span", { text: label })]);
}
function busyChip(label, cls) {
  return el("span", { class: "chip " + (cls || "accent") }, [el("span", { class: "spinner sm" }), el("span", { text: label })]);
}
// The three per-session indicators Sean asked for: recorded / transcribed / summarised, each with
// an in-progress form (a live dot or spinner) when that step is happening right now.
function sessionStatus(f, active, summarising) {
  var chips = [];
  if (active) {
    if (active.recording) chips.push(el("span", { class: "chip rec" }, [el("span", { class: "dot" }), el("span", { text: tr("Recording") })]));
    if (active.transcribing) chips.push(busyChip(tr("Transcribing")));
  } else {
    if (f.recorded) chips.push(statChip(tr("Recorded"), "mic", "muted"));
    if (f.transcribed) chips.push(statChip(tr("Transcript"), "note", "ok"));
    if (summarising) chips.push(busyChip(tr("Summarising")));
    else if (f.has_summary) chips.push(statChip(tr("Summary"), "sparkle", "accent"));
    if (f.has_notes) chips.push(statChip(tr("Notes"), "pencil", "muted"));
  }
  return chips.length ? el("div", { class: "stats" }, chips) : null;
}
function sessionActions(f, active) {
  var acts = [];
  if (f.size) acts.push(el("span", { class: "ink-3", style: { fontSize: "11.5px" }, text: fmtBytes(f.size) }));
  if (active) {
    // running right now: it lives on the live screen, nothing to do from History
  } else if (f.transcribed) {
    acts.push(el("button", { class: "btn ghost sm", onclick: function (e) { e.stopPropagation(); openReader(f.name); } }, tr("Open")));
  } else if (f.recorded) {
    // Record-only session (audio, no transcript yet): the primary action is to transcribe it.
    acts.push(el("button", { class: "btn sm", onclick: function (e) { e.stopPropagation(); reTranscribe(f.stem, f.topic || topicFromName(f.name), false); } }, [icon("note", 13), tr("Transcribe")]));
  }
  return acts;
}
// Re-transcribe a saved recording through the file engine: both channels, time-merged, with the
// MIC/SYS split kept as speaker separation (you vs the other side). Writes at the recording's own
// stem, so the History row gains its transcript instead of spawning a second row.
function reTranscribe(stem, topic, isRegen) {
  confirmModal({
    title: isRegen ? tr("Re-transcribe from the recording?") : tr("Transcribe this recording?"),
    message: isRegen
      ? tr("Re-transcribes both sides from the saved audio and replaces the current transcript. The audio is kept. Use it for a cleaner pass than the live one.")
      : tr("Transcribes both sides (you and the other person) as separate speakers. Pick the language and model for this pass below."),
    body: reTranscribeOptions(),
    confirmLabel: isRegen ? tr("Re-transcribe") : tr("Transcribe"),
    onConfirm: function () { S.form.context = null; startImport({ stem: stem, topic: topic }); },   // re-transcribe shows no context editor: use the saved default, never a stale override
  });
}
// Language + model pickers for the re-transcribe dialog. Native selects that mutate S.form
// silently (a render() here would clear the modal); startImport already sends S.form.language /
// tier / engine to /api/transcribe-file, so the choices take effect on the next pass.
function reTranscribeOptions() {
  return el("div", { class: "stack", style: { gap: "12px" } }, [
    formField("Language", null, selectEl(transcribeLangOpts(), S.form.language, function (v) { S.form.language = v; }), true),
    formField("Engine", el("span", { class: "label-muted", text: " (auto follows the language)" }),
      selectEl([["auto", "Auto"], ["fluister", "Fluister"], ["whisper", "Whisper"]], S.form.engine || "auto", function (v) { S.form.engine = v; }), true),
    formField("Model size", el("span", { class: "label-muted", text: " (auto is recommended)" }),
      selectEl(QUALITY_OPTS, normalizeQuality(S.form.tier), function (v) { S.form.tier = v; }), true),
    // Plain checkbox (NOT toggleEl/saveSettings): a render() in the modal would clear it. Sets
    // S.form.aec for this pass; startImport sends it. Only does anything when the recording has
    // both a MIC and a SYS channel (a saved Volksmond recording, or its siblings auto-bundled).
    el("label", { class: "row gap-8", style: { alignItems: "flex-start", cursor: "pointer", marginTop: "2px" } }, [
      el("input", { type: "checkbox", checked: !!S.form.aec, style: { marginTop: "3px", flex: "0 0 auto" }, onchange: function (e) { S.form.aec = e.target.checked; } }),
      el("div", {}, [
        el("div", { style: { fontWeight: "600", fontSize: "13px" }, text: "Cancel speaker echo" }),
        el("p", { class: "ink-3", style: { fontSize: "11px", margin: "2px 0 0" }, text: "Off by default. When you re-transcribe a recording, remove the other side's voice that your microphone re-heard through the speakers. Best when you are mostly listening (a video or a one-sided talk). It can blur your own words when you and the other side talk over each other, so leave it off for normal back-and-forth meetings. No effect on headphones." }),
      ]),
    ]),
  ]);
}
function historyView() {
  var rows = S.sessions.filter(function (f) {
    if (!histQuery) return true;
    return ((f.topic || "") + " " + (f.name || "")).toLowerCase().indexOf(histQuery.toLowerCase()) >= 0;
  });
  var list;
  if (!S.sessions.length) {
    list = el("div", { class: "card", style: { padding: "48px 16px", textAlign: "center" } }, [
      el("div", { class: "tone-tile muted", style: { width: "48px", height: "48px", margin: "0 auto 12px" } }, icon("clock", 22)),
      el("div", { style: { fontWeight: "600" }, text: "No meetings yet." }),
      el("p", { class: "ink-3", style: { fontSize: "13px", marginTop: "6px" }, text: "Once you transcribe a meeting, it shows up here. Nothing is uploaded; your meetings live in your data folder." }),
    ]);
  } else {
    list = el("div", { class: "card" }, rows.map(function (f) {
      var active = (S.sessionsActive && S.sessionsActive.stem === f.stem) ? S.sessionsActive : null;
      var summarising = (S.sessionsSummarising || []).indexOf(f.stem) >= 0;
      var canOpen = f.transcribed && !active;
      var openIt = function () { if (canOpen) openReader(f.name); };
      return el("div", { class: "hist-row", role: canOpen ? "button" : null, tabindex: canOpen ? "0" : null,
        onclick: canOpen ? openIt : null, onkeydown: canOpen ? keyActivate(openIt) : null }, [
        el("div", { class: "when", text: f.date ? f.date + " · " + f.time : "" }),
        el("div", { class: "topic" }, [
          el("div", {}, raw(f.topic || topicFromName(f.name))),
          sessionStatus(f, active, summarising),
        ]),
        el("div", { class: "right" }, sessionActions(f, active)),
      ]);
    }));
  }
  return el("div", { class: "screen" }, el("div", { class: "screen-inner" }, [
    el("div", { class: "row gap-12", style: { marginBottom: "20px" } }, [
      el("h2", { text: "Past meetings" }),
      el("span", { class: "grow" }),
      el("div", { class: "row", style: { position: "relative" } }, [
        el("span", { style: { position: "absolute", left: "10px", color: "var(--ink-3)", pointerEvents: "none", display: "inline-flex" } }, icon("search", 15)),
        el("input", { class: "field", style: { width: "240px", paddingLeft: "32px" }, placeholder: "Search transcripts", value: histQuery, oninput: function (e) { histQuery = e.target.value; render(); } }),
      ]),
      el("button", { class: "btn", onclick: function () { go("home"); } }, [icon("plus", 15), "New meeting"]),
    ]),
    list,
    S.sessionsFolder ? el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "12px" } }, ["Saved in ", el("span", { class: "mono", text: S.sessionsFolder })]) : null,
  ]));
}
async function openReader(name, initialTab) {
  var row = (S.sessions || []).filter(function (s) { return s.name === name; })[0] || {};
  S.reader = { name: name, stem: row.stem || name.replace(/\.md$/, ""), recorded: !!row.recorded,
    title: topicFromName(name), text: "Loading...", tab: initialTab || "transcript", summarising: false, summary: null, savedAs: null };
  go("reader");
  try { S.reader.text = await api.text("/sessions/" + encodeURIComponent(name)); }
  catch (e) { S.reader.text = "Could not load this transcript: " + e.message; }
  // Always try the sibling summary file (ignore 404). Don't gate on cached has_summary: a
  // summary made this session may not be in S.sessions yet, which would hide the Summary tab.
  // Header-verify it (matches the backend rule): a real transcript whose topic ends in
  // "summary" (e.g. budget-summary.md) is NOT a sibling summary — accepting it would show that
  // transcript as the current one's summary. Only treat it as a summary if it starts with
  // "# Summary:".
  var sumName = name.replace(/\.md$/, "") + "-summary.md";
  try {
    var raw = await api.text("/sessions/" + encodeURIComponent(sumName));
    if (/^#\s*Summary:/.test(raw)) {
      S.reader.summary = stripSummaryHeader(raw);
      S.reader.savedAs = sumName;
    }
  } catch (e) { /* no summary yet, or unreadable: the transcript still shows */ }
  // Load the user's own notes for this session (if any), so the reader can show them and the
  // summary card can offer to fold them in.
  S.reader.hasNotes = false; S.reader.notes = ""; S.reader.includeNotes = true;
  try {
    var nres = await api.get("/api/notes?stem=" + encodeURIComponent(S.reader.stem));
    if (nres && nres.text && nres.text.trim()) { S.reader.notes = nres.text; S.reader.hasNotes = true; }
  } catch (e) { /* no notes: fine */ }
  // If a summary is being generated right now (started before opening this reader), show the
  // in-progress state and poll for it, rather than showing nothing.
  if (!S.reader.summary && (S.sessionsSummarising || []).indexOf(S.reader.stem) >= 0) {
    S.reader.summarising = true;
    pollSummary(name, "reader");
  }
  render();
}
function stripSummaryHeader(s) {
  // Saved summaries start with "# Summary: <stem>"; drop that header line for display.
  return String(s || "").replace(/^#\s*Summary:[^\n]*\n+/, "");
}

/* ── reader (past transcript) ─────────────────────────────── */
function readerView() {
  var tab = S.reader.tab || "transcript";
  // A connected segmented control: Transcript, Summary and My notes are always present, so the
  // reader is one consistent three-way switch. The Summary tab hosts the summary if there is one,
  // else the summarise controls; the My notes tab is an editor, so notes can be added or fixed after
  // the meeting and folded into a fresh summary from right there.
  var mkTab = function (id, ico, label) {
    return el("button", { class: tab === id ? "on" : "", onclick: function () { S.reader.tab = id; render(); } }, [icon(ico, 13), el("span", { text: label })]);
  };
  var toggle = el("div", { class: "segmented", style: { width: "auto", flex: "0 0 auto" } }, [
    mkTab("transcript", "note", "Transcript"),
    mkTab("summary", "sparkle", "Summary"),
    mkTab("notes", "pencil", "My notes"),
  ]);
  var body;
  if (tab === "summary") {
    body = summariseCard(S.reader.name, "reader");
  } else if (tab === "notes") {
    body = readerNotesTab();
  } else {
    body = el("div", { class: "card", style: { padding: "20px 22px" } },
      el("div", { class: "doc", style: { maxWidth: "none", fontSize: "15px", whiteSpace: "pre-wrap", fontFamily: "var(--font-transcript)" } }, raw(S.reader.text)));
  }
  return el("div", { class: "screen" }, el("div", { class: "screen-inner col-mid stack", style: { gap: "16px" } }, [
    el("button", { class: "btn ghost sm", style: { alignSelf: "flex-start" }, onclick: function () { go("history"); } }, [icon("back", 14), "Back to history"]),
    el("div", { class: "row gap-12" }, [
      el("h2", {}, raw(S.reader.title)),
      el("span", { class: "grow" }),
      toggle,
      el("button", { class: "btn ghost sm", onclick: function () { copyText(tab === "summary" ? (S.reader.summary || "") : tab === "notes" ? (S.reader.notes || "") : S.reader.text); } }, [icon("copy", 13), "Copy"]),
      el("button", { class: "btn ghost sm", onclick: function () { api.post("/api/open-folder").catch(function () {}); } }, [icon("folder", 13), "Folder"]),
      S.reader.recorded ? el("button", { class: "btn ghost sm", onclick: function () { reTranscribe(S.reader.stem, S.reader.title, true); } }, [icon("mic", 13), tr("Re-transcribe")]) : null,
    ]),
    body,
  ]));
}

/* ── settings ─────────────────────────────────────────────── */
function settingsView() {
  var st = S.settings || {};
  return el("div", { class: "screen" }, el("div", { class: "screen-inner col-mid" }, [
    el("h2", { style: { marginBottom: "16px" }, text: "Settings" }),
    licenceCard(),
    appearanceCard(),
    transcriptionCard(st),
    voiceModelCard(),
    (S.cuda && S.cuda.supported !== false && S.cuda.gpu_present) ? cudaCard() : null,
    summariesCard(),
    dataCard(st),
    connected() ? dangerCard(st) : null,
    aboutCard(),
  ]));
}
var updateState = { state: "idle", info: null };
// Manual, user-initiated update check. Posts to the localhost server, which makes ONE outbound
// GET to the Volksmond update manifest on volksmond.digiphyte.com. Never automatic; nothing
// leaves the machine until the user clicks Check for updates.
function checkUpdates() {
  updateState = { state: "checking", info: null }; render();
  api.post("/api/check-updates").then(function (d) {
    updateState = { state: "done", info: d }; render();
  }).catch(function () {
    updateState = { state: "error", info: null }; render();
  });
}
// The update manifest is our own file, but treat its "url" as untrusted anyway: only follow an
// https link to our own domains (or GitHub, where release assets live), so a tampered manifest
// cannot turn the Download button into a redirect to an arbitrary site.
function openUpdateLink(u) {
  try {
    var url = new URL(u);
    var host = url.hostname.toLowerCase();
    var ok = url.protocol === "https:" && (
      host === "digiphyte.com" || host.endsWith(".digiphyte.com") ||
      host === "github.com" || host.endsWith(".github.com") || host.endsWith(".githubusercontent.com"));
    if (ok) { openExternal(u); return; }
  } catch (e) {}
  toast("That update link looked wrong, so it was not opened.", true);
}
// Compact result under the sidebar "Check for updates" item, so a check from the sidebar shows its
// outcome in place. Shares the same updateState as the Settings About card (they stay in sync).
function sideUpdateResult() {
  var u = updateState;
  if (u.state === "done" && u.info && u.info.update_available)
    return el("div", { class: "side-update" }, [el("span", { text: "v" + u.info.latest + " ready. " }), el("span", { class: "link", onclick: function () { openUpdateLink(u.info.url); } }, "Download")]);
  if (u.state === "done") return el("div", { class: "side-update", style: { color: "var(--ok)" } }, "You are up to date.");
  if (u.state === "error") return el("div", { class: "side-update", style: { color: "var(--warn)" } }, "Could not check.");
  return null;
}
function aboutCard() {
  var version = (S.appInfo && S.appInfo.version) || "?";
  var u = updateState;
  // Direct builds show the result of their manual manifest check. Store builds show static guidance
  // because Microsoft Store owns their automatic updates; the button only opens the Store listing.
  var updateLine = offlineBuild() ? null : storeBuild() ?
    el("div", { class: "s", style: { marginTop: "4px" }, text: "Microsoft Store normally updates Volksmond automatically. Use the button to check now." }) :
    u.state === "checking" ? el("div", { class: "s", style: { marginTop: "4px", display: "flex", gap: "6px", alignItems: "center" } }, [el("span", { class: "spinner" }), el("span", { text: "Checking for updates" })]) :
    u.state === "error" ? el("div", { class: "s", style: { marginTop: "4px", color: "var(--warn)" }, text: "Could not check for updates." }) :
    (u.state === "done" && u.info && u.info.update_available) ? el("div", { class: "s", style: { marginTop: "4px" } }, [el("span", { text: "Update available" }), raw(": v" + u.info.latest + "  "), el("span", { class: "link", onclick: function () { openUpdateLink(u.info.url); } }, "Download")]) :
    (u.state === "done") ? el("div", { class: "s ok-text", style: { marginTop: "4px" }, text: "You are up to date." }) : null;
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "About" }),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, markSvg(20)),
      el("div", { class: "body" }, [
        el("div", { class: "t" }, [el("span", { text: "Volksmond" }), el("span", { class: "chip", text: "Version " + version })]),
        // The pronunciation note only helps an English speaker; an Afrikaans speaker already
        // knows how to say it, so hide it when the interface is in Afrikaans.
        (LANG === "af") ? null : el("div", { class: "s", text: "Said Fawlks-mawnt. Afrikaans for the way people actually speak." }),
        el("div", { class: "s", text: "A DigiPhyte product, built in South Africa. All transcription happens on this machine unless you explicitly opt in." }),
        updateLine,
      ]),
      el("div", { class: "ctl", style: { display: "flex", flexDirection: "column", gap: "6px", alignItems: "stretch" } }, [
        !offlineBuild() ? el("button", { class: "btn ghost", disabled: !storeBuild() && u.state === "checking", onclick: function () { storeBuild() ? openStoreListing() : checkUpdates(); } }, storeBuild() ? "Check for updates in Microsoft Store" : "Check for updates") : null,
        el("button", { class: "btn ghost", onclick: function () { openExternal("https://digiphyte.com"); } }, "digiphyte.com"),
      ]),
    ]),
  ]);
}
function licenceCard() {
  var pro = isPro();
  var lic = S.license || {};
  var seats = lic.seats || 1;
  var until = lic.valid_until;  // ISO date, or null for an undated key
  var seatText = seats > 1 ? (seats + " seats") : "1 seat";
  var remindersOn = !(S.settings && S.settings.calendar_reminders === false);  // default on
  var toastsOn = !(S.settings && S.settings.os_toasts === false);              // default on
  var silenceOn = !(S.settings && S.settings.silence_nudge === false);         // default on
  var silenceMins = (S.settings && S.settings.silence_nudge_minutes) || 5;     // 3 / 5 / 10 / 15
  var struggleOn = !(S.settings && S.settings.struggle_nudge === false);       // default on
  // Shell_NotifyIcon balloons are a Windows mechanism, so the row is hidden elsewhere rather
  // than offering a switch that does nothing (platform is platform.platform(), e.g. "Windows-11-...").
  var winPlatform = /^windows/i.test((S.appInfo && S.appInfo.platform) || "");
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "set-row" }, [
      el("div", { class: "tone-tile accent", style: { width: "36px", height: "36px", flex: "0 0 auto" } }, icon("crown", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t" }, [
          el("span", { text: pro ? "Business licence, active" : "Personal use" }),
          pro ? el("span", { class: "chip accent", text: seatText }) : null,
        ]),
        el("div", { class: "s", text: pro
          ? ("This computer is activated on a business licence" + (until ? ", valid until " + until : "") + ". Calendar attendees are unlocked, verified on this computer, never on a server.")
          : "Licensed for personal, non-commercial use. Everything runs on this computer, free, with no account. A business or team using Volksmond for work needs a licence." }),
      ]),
      pro
        ? el("button", { class: "btn ghost", onclick: deactivateLicence }, "Deactivate")
        : el("button", { class: "btn ghost", onclick: function () { go("upgrade"); } }, "Business licensing"),
    ]),
    // Everyone: the shared switch for Windows desktop notifications. Not a Business feature,
    // because the things it is used for (a meeting starting, a long silence during a recording)
    // include plain data-integrity warnings that every user should get. Purely local: it hands a
    // short message to the Windows shell on this computer and makes no network call.
    winPlatform ? el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("bell", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t", text: "Windows notifications" }),
        el("div", { class: "s", text: "Let Volksmond send a Windows notification when it needs to tell you something while its window is hidden behind your meeting. Nothing is sent anywhere; the message appears on this computer only." }),
      ]),
      el("div", { class: "ctl" }, toggleEl(toastsOn, function () { saveSettings({ os_toasts: !toastsOn }); })),
    ]) : null,
    // Everyone: warn when a live session hears nothing at all for a long stretch. Not a Business
    // feature and not platform-gated: the in-app banner works everywhere, and the Windows
    // notification is a bonus governed by the row above. This is data integrity, not a nicety.
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("alert", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t", text: "Warn me about long silences" }),
        el("div", { class: "s", text: "If nothing at all reaches Volksmond during a meeting, neither your microphone nor the system audio, it warns you instead of quietly recording an hour of nothing. Useful when Windows moves your microphone to another device." }),
      ]),
      el("div", { class: "ctl row gap-8" }, [
        silenceOn ? selectEl([["3", "After 3 minutes"], ["5", "After 5 minutes"], ["10", "After 10 minutes"], ["15", "After 15 minutes"]],
          String(silenceMins), function (v) { saveSettings({ silence_nudge_minutes: parseInt(v, 10) || 5 }); }) : null,
        toggleEl(silenceOn, function () { saveSettings({ silence_nudge: !silenceOn }); }),
      ]),
    ]),
    // Everyone: warn when the computer cannot keep up and Volksmond drops to a lighter model.
    // Same class as the silence warning: data integrity, not a nicety. The in-app banner works
    // everywhere; the Windows notification is the bonus governed by the notifications row above.
    // Turning this off persists struggle_nudge=false, which is how "Don't warn again" mutes it too.
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("alert", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t", text: "Warn me when the model can't keep up" }),
        el("div", { class: "s", text: "On a slower computer, Volksmond drops to a lighter, faster model to stay live. When it does, it tells you so you can record and re-transcribe at full accuracy afterward." }),
      ]),
      el("div", { class: "ctl" }, toggleEl(struggleOn, function () { saveSettings({ struggle_nudge: !struggleOn }); })),
    ]),
    // Business only: the calendar reminder toggle. Reads the local Outlook calendar while the app is
    // open and offers to start transcribing when a meeting begins. Local only, never auto-starts.
    // The copy names the CARD specifically, because since WP-10 the same calendar poll also drives a
    // Windows notification, switched by the row above; this row governs only the in-app card.
    pro ? el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("calendar", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t", text: "Show a reminder card when a meeting starts" }),
        el("div", { class: "s", text: "While Volksmond is open, it checks your Outlook calendar on this computer and shows a reminder card, inside the app, offering to start transcribing when a meeting begins. Windows notifications are switched separately, above. Nothing is sent anywhere, and it never starts on its own." }),
      ]),
      el("div", { class: "ctl" }, toggleEl(remindersOn, function () { saveSettings({ calendar_reminders: !remindersOn }); })),
    ]) : null,
  ]);
}
function appearanceCard() {
  var seg = el("div", { class: "segmented" }, [["system", "System", "auto"], ["light", "Light", "sun"], ["dark", "Dark", "moon"]].map(function (o) {
    return el("button", { class: S.theme === o[0] ? "on" : "", onclick: function () { setTheme(o[0]); } }, [icon(o[2], 14), el("span", { text: o[1] })]);
  }));
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "Appearance" }),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("sun", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Theme" }), el("div", { class: "s", text: "System follows your operating system. Dark uses the same palette, inverted." })]),
      el("div", { class: "ctl" }, seg),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("globe", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Interface language" }), el("div", { class: "s", text: "The language Volksmond shows you. It does not change what gets transcribed." })]),
      el("div", { class: "ctl" }, selectEl([["en-ZA", "English (South Africa)"], ["af", "Afrikaans"]], (S.settings && S.settings.interface_language) || "en-ZA", function (v) { saveSettings({ interface_language: v }); })),
    ]),
  ]);
}
// A saved default language must reach the CURRENT app instance too (S.form seeds only at boot),
// so the next pre-meeting screen already shows the new default without a restart.
function saveDefaultLanguage(patch) {
  return saveSettings(patch).then(function () {
    var v = patch.transcription_language;
    if (S.settings && S.settings.transcription_language === v) {
      S.form.language = v;
      if (langMode(v) === "more") S.form.moreLang = v;
    }
  });
}
function transcriptionCard(st) {
  var draft = S.settingsDraft || {};
  var ctxVal = draft.default_context != null ? draft.default_context : (st.default_context || "");
  var sel = (st.transcribe_languages || ["af", "en"]).slice();
  function toggleLang(code) {
    var i = sel.indexOf(code);
    if (i >= 0) { if (sel.length <= 1) return; sel.splice(i, 1); }   // keep at least one language
    else sel.push(code);
    var patch = { transcribe_languages: sel };
    // Keep the default language valid if we just removed it. Only the individually
    // toggleable codes are clamped; "sa", world codes and auto-detect always stay valid.
    if (langIsToggleable(st.transcription_language) && sel.indexOf(st.transcription_language) < 0) {
      patch.transcription_language = sel[0];
      saveDefaultLanguage(patch);
      return;
    }
    saveSettings(patch);
  }
  var afOn = sel.indexOf("af") >= 0;
  // Every pre-meeting language mode is defaultable: the ticked languages above, the
  // South African group, each world language, and auto-detect.
  var defOpts = sel.map(function (c) { return [c, langName(c)]; });
  defOpts.push(["sa", "South African languages"]);
  WORLD_LANGS.forEach(function (w) { defOpts.push(w.slice()); });
  defOpts.push(["", "Auto-detect"]);
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "Transcription" }),
    el("div", { class: "set-row", style: { display: "block" } }, [
      el("div", { class: "t", style: { marginBottom: "4px" }, text: "Languages you transcribe" }),
      el("div", { class: "s", style: { marginBottom: "10px" }, text: "Pick the languages you record in. The language you choose for a meeting picks the model; the size is chosen automatically." }),
      el("div", { class: "row gap-8", style: { flexWrap: "wrap" } }, SUPPORTED_LANGS.map(function (l) {
        var on = sel.indexOf(l.code) >= 0;
        return el("button", { class: "btn sm" + (on ? " primary" : " ghost"), onclick: function () { toggleLang(l.code); } }, [on ? icon("check", 12) : null, el("span", { text: l.name })]);
      })),
      el("div", { class: "s", style: { marginTop: "10px" }, text: "Afrikaans uses Fluister, our Afrikaans-tuned model; the South African languages use Swivuriso (beta); English and other languages use standard Whisper." }),
      (afOn && !fluisterReady()) ? el("div", { class: "s", style: { marginTop: "4px" }, text: "The Afrikaans-tuned Fluister model is not installed on this computer yet, so Afrikaans runs on standard Whisper for now." }) : null,
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("globe", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Default language" }), el("div", { class: "s", text: "Used unless you change it for a meeting." })]),
      // "" is a real value (Auto-detect), so only null/undefined may fall back to "af".
      el("div", { class: "ctl" }, selectEl(defOpts, st.transcription_language != null ? st.transcription_language : "af", function (v) { saveDefaultLanguage({ transcription_language: v }); })),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("cpu", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Model size" }), el("div", { class: "s", text: "Advanced. Auto picks the best model your hardware can run; you rarely need to change this." })]),
      el("div", { class: "ctl" }, selectEl([["auto", "Auto"], ["small", "Fast"], ["medium", "Balanced"], ["large-v3-turbo", "High quality"], ["large-v3", "Best"]], normalizeQuality(st.tier), function (v) { saveSettings({ tier: v }); })),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("mic", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t", text: "Cancel speaker echo" }),
        el("div", { class: "s", text: "Off by default. When you re-transcribe a recording, remove the other side's voice that your microphone re-heard through the speakers. Best when you are mostly listening (a video or a one-sided talk). It can blur your own words when you and the other side talk over each other, so leave it off for normal back-and-forth meetings. No effect on headphones." }),
      ]),
      el("div", { class: "ctl" }, toggleEl(st.aec === true, function () { saveSettings({ aec: !(st.aec === true) }); })),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("mic", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t", text: "Skip quiet mic audio (mic gate)" }),
        el("div", { class: "s", text: "Skips microphone audio with no speech in it so the far end gets the CPU. Switch it off if it ever cuts you off." }),
      ]),
      // "" and 0 are not values here, so only an explicit false turns it off: the default is on.
      el("div", { class: "ctl" }, toggleEl(st.mic_gate !== false, function () { saveSettings({ mic_gate: st.mic_gate === false }); })),
    ]),
    el("div", { class: "set-row", style: { display: "block" } }, [
      el("div", { class: "t", style: { marginBottom: "4px" }, text: "Default context, names and jargon" }),
      el("div", { class: "s", style: { marginBottom: "8px" }, text: "Applied to every meeting to help accuracy. Stored on this computer only." }),
      el("textarea", { class: "field", value: ctxVal, placeholder: "e.g. Thabo, Acme Corp, EBITDA. Or a sentence guiding the recogniser.", oninput: function (e) { S.settingsDraft = S.settingsDraft || {}; S.settingsDraft.default_context = e.target.value; } }),
      el("div", { class: "row", style: { marginTop: "8px", justifyContent: "flex-end" } },
        el("button", { class: "btn sm", onclick: function () { saveSettings({ default_context: (S.settingsDraft && S.settingsDraft.default_context) || "" }); } }, "Save context")),
    ]),
  ]);
}
/* ── summary model download (one-click, shared by setup and settings) ─── */
var SUMMARY_LABELS = {
  "gemma-4-e2b": { title: "Gemma 4 (2 billion)", note: "Smaller and faster, light on memory. Works well on most machines." },
  "gemma-4-e4b": { title: "Gemma 4 (4 billion)", note: "Larger and more polished. Needs more memory and a little more time." },
  "gemma-4-12b": { title: "Gemma 4 (12 billion)", note: "The most capable local summary. Needs a strong machine with plenty of memory, and takes a little longer." },
};
function fmtGB(bytes) { var g = (bytes || 0) / 1e9; return (g < 1 ? g.toFixed(2) : g.toFixed(1)) + " GB"; }
var _dlTimer = null;
function startModelDownload(key) {
  api.post("/api/summary-model/download", { key: key })
    .then(function () { pollModelDownload(); render(); })
    .catch(function (e) { toast(e.message || "Could not start the download.", true); });
}
function pollModelDownload() {
  if (_dlTimer) return;
  _dlTimer = setInterval(function () {
    api.get("/api/summary-models").then(function (d) {
      S.summaryModels = d;
      var p = (d && d.progress) || {};
      if (p.state === "downloading") {
        // Update the bar in place. A full render() each second would rebuild the
        // DOM, closing any open dropdown and fighting the scroll position.
        updateDownloadProgress(p);
        return;
      }
      clearInterval(_dlTimer); _dlTimer = null;
      if (p.state === "done") { api.get("/api/models").then(function (mm) { S.models = mm; render(); }); toast("Summary model ready. Summaries are on."); }
      else if (p.state === "error") { toast("Download failed: " + (p.error || ""), true); }
      render();
    }).catch(function () {});
  }, 1000);
}
function updateDownloadProgress(p) {
  var pct = p.total ? Math.min(100, Math.round((p.downloaded || 0) * 100 / p.total)) : 0;
  var bar = document.getElementById("vm-dl-bar");
  if (bar) bar.style.width = pct + "%";
  var txt = document.getElementById("vm-dl-text");
  if (txt) txt.textContent = fmtGB(p.downloaded) + " of " + fmtGB(p.total) + "  (" + pct + "%)";
}
function progressBar(pct) {
  return el("div", { style: { height: "8px", borderRadius: "999px", background: "var(--line)", overflow: "hidden", marginTop: "10px" } },
    el("div", { id: "vm-dl-bar", style: { height: "100%", width: pct + "%", background: "var(--accent)", transition: "width .3s ease" } }));
}
function loadSummaryModels() {
  S._loadingSM = true; S._smError = false;
  api.get("/api/summary-models")
    .then(function (d) { S.summaryModels = d; S._loadingSM = false; render(); })
    .catch(function () { S._loadingSM = false; S._smError = true; render(); });
}
function summaryDownloadPanel(manage) {
  if (!S.summaryModels) {
    if (S._smError) {
      return el("div", { class: "stack", style: { gap: "6px" } }, [
        el("div", { class: "ink-3", style: { fontSize: "12px" }, text: "Could not load model options. Restart Volksmond and try again." }),
        el("span", { class: "link", style: { fontSize: "12px" }, onclick: function () { loadSummaryModels(); } }, "Try again"),
      ]);
    }
    if (!S._loadingSM) { loadSummaryModels(); }
    return el("div", { class: "ink-3", style: { fontSize: "12px" }, text: "Loading model options..." });
  }
  var d = S.summaryModels; var models = d.models || []; var p = d.progress || {};
  var downloading = p.state === "downloading";
  return el("div", { class: "stack", style: { gap: "10px" } }, models.map(function (m) {
    var meta = SUMMARY_LABELS[m.key] || { title: m.params + " model", note: "" };
    var isThis = downloading && p.key === m.key;
    var pct = (isThis && p.total) ? Math.min(100, Math.round((p.downloaded || 0) * 100 / p.total)) : 0;
    return el("div", { class: "card", style: { padding: "14px" } }, [
      el("div", { class: "row", style: { justifyContent: "space-between", alignItems: "flex-start", gap: "12px" } }, [
        el("div", { class: "grow" }, [
          el("div", { style: { fontWeight: "600" } }, [el("span", { text: meta.title }), el("span", { class: "chip", style: { marginLeft: "8px" } }, raw(fmtGB((m.present && m.size_on_disk) ? m.size_on_disk : m.approx_bytes)))]),
          el("div", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "2px" }, text: meta.note }),
        ]),
        (manage && m.present)
          ? el("div", { class: "row gap-8", style: { alignItems: "center", flex: "0 0 auto" } }, [
              m.active
                ? el("span", { class: "chip ok" }, [icon("check", 12), "Installed"])
                : el("button", { class: "btn", onclick: function () { if (downloading) return; startModelDownload(m.key); } }, isThis ? "Downloading" : "Use"),
              el("button", { class: "btn ghost sm", onclick: function () { confirmRemoveSummary(m); } }, "Remove"),
            ])
          : (m.active
              ? el("span", { class: "chip ok" }, [icon("check", 12), "Installed"])
              : el("button", { class: "btn", onclick: function () { if (downloading) return; startModelDownload(m.key); } }, isThis ? "Downloading" : (m.present ? "Use" : "Download"))),
      ]),
      isThis ? progressBar(pct) : null,
      isThis ? el("div", { id: "vm-dl-text", class: "ink-3", style: { fontSize: "11.5px", marginTop: "4px" } }, raw(fmtGB(p.downloaded) + " of " + fmtGB(p.total) + "  (" + pct + "%)")) : null,
    ]);
  }));
}
/* ── voice (transcription) model download, shared by setup and settings ─── */
var VOICE_LABELS = {
  "tiny":   { title: "Minimal",      note: "The fastest and the roughest. A live safety net for a slow computer, not a transcript to rely on." },
  "base":   { title: "Lite",         note: "Very fast and quite rough. For old or low-power computers, and the step above Minimal when the live view is falling behind." },
  "small":  { title: "Light",        note: "Light and quick, easy on memory. Good everyday accuracy on most laptops." },
  "medium": { title: "Balanced",     note: "A good balance of speed and accuracy on a typical computer. The usual sweet spot." },
  "large-v3-turbo": { title: "High quality", note: "Near the best accuracy, but lighter and faster. Great on a strong CPU or any GPU." },
  "large-v3": { title: "Best",       note: "The most accurate. Needs a graphics card (GPU) to be quick; slow on CPU alone." },
};
var _vdlTimer = null;
function startVoiceDownload(model) {
  api.post("/api/voice-model/download", { model: model })
    .then(function () { pollVoiceDownload(); render(); })
    .catch(function (e) { toast(e.message || "Could not start the download.", true); });
}
function pollVoiceDownload() {
  if (_vdlTimer) return;
  _vdlTimer = setInterval(function () {
    api.get("/api/voice-models").then(function (d) {
      S.voiceModels = d;
      var p = (d && d.progress) || {};
      if (p.state === "downloading") { updateVoiceProgress(p); return; }
      clearInterval(_vdlTimer); _vdlTimer = null;
      if (p.state === "done") { toast("Transcription model ready."); if (p.kind === "fluister") { S.modelUpdates = null; } }
      else if (p.state === "error") { toast("Download failed: " + (p.error || ""), true); }
      render();
    }).catch(function () {});
  }, 1000);
}
function updateVoiceProgress(p) {
  var pct = p.total ? Math.min(100, Math.round((p.downloaded || 0) * 100 / p.total)) : 0;
  var bar = document.getElementById("vm-vdl-bar");
  if (bar) bar.style.width = pct + "%";
  var txt = document.getElementById("vm-vdl-text");
  if (txt) txt.textContent = fmtGB(p.downloaded) + " of " + fmtGB(p.total) + "  (" + pct + "%)";
}
function loadVoiceModels() {
  S._loadingVM = true; S._vmError = false;
  api.get("/api/voice-models")
    .then(function (d) { S.voiceModels = d; S._loadingVM = false; render(); })
    .catch(function () { S._loadingVM = false; S._vmError = true; render(); });
}
function voiceProgressBar(pct) {
  return el("div", { style: { height: "8px", borderRadius: "999px", background: "var(--line)", overflow: "hidden", marginTop: "10px" } },
    el("div", { id: "vm-vdl-bar", style: { height: "100%", width: pct + "%", background: "var(--accent)", transition: "width .3s ease" } }));
}
function voiceDownloadPanel(manage) {
  if (!S.voiceModels) {
    if (S._vmError) {
      return el("div", { class: "stack", style: { gap: "6px" } }, [
        el("div", { class: "ink-3", style: { fontSize: "12px" }, text: "Could not load model options. Restart Volksmond and try again." }),
        el("span", { class: "link", style: { fontSize: "12px" }, onclick: function () { loadVoiceModels(); } }, "Try again"),
      ]);
    }
    if (!S._loadingVM) { loadVoiceModels(); }
    return el("div", { class: "ink-3", style: { fontSize: "12px" }, text: "Loading model options..." });
  }
  var d = S.voiceModels; var models = (d.models || []).slice(); var p = d.progress || {};
  var downloading = p.state === "downloading";
  models.sort(function (a, b) { return (b.recommended ? 1 : 0) - (a.recommended ? 1 : 0); }); // recommended first
  return el("div", { class: "stack", style: { gap: "10px" } }, models.map(function (m) {
    var meta = VOICE_LABELS[m.model] || { title: m.model, note: "" };
    var isThis = downloading && (p.kind === "whisper" || !p.kind) && p.model === m.model;
    var pct = (isThis && p.total) ? Math.min(100, Math.round((p.downloaded || 0) * 100 / p.total)) : 0;
    var usable = !(m.model === "large-v3" && d.is_gpu === false);
    return el("div", { class: "card", style: { padding: "14px", opacity: usable ? "1" : "0.6" } }, [
      el("div", { class: "row", style: { justifyContent: "space-between", alignItems: "flex-start", gap: "12px" } }, [
        el("div", { class: "grow" }, [
          el("div", { style: { fontWeight: "600" } }, [
            el("span", { text: meta.title }),
            m.recommended ? el("span", { class: "chip accent", style: { marginLeft: "8px" }, text: "Recommended" }) : null,
            el("span", { class: "chip", style: { marginLeft: "8px" } }, raw(fmtGB((m.present && m.size_on_disk) ? m.size_on_disk : m.approx_bytes))),
          ]),
          el("div", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "2px" }, text: meta.note }),
          (!usable) ? el("div", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "4px" }, text: "Needs a graphics card (GPU). Choose another for this computer." }) : null,
        ]),
        m.present
          ? el("div", { class: "row gap-8", style: { alignItems: "center", flex: "0 0 auto" } }, [
              el("span", { class: "chip ok" }, [icon("check", 12), "Installed"]),
              manage ? el("button", { class: "btn ghost sm", onclick: function () { confirmRemoveVoice(m); } }, "Remove") : null,
            ])
          : (usable ? el("button", { class: "btn", onclick: function () { if (downloading) return; startVoiceDownload(m.model); } }, isThis ? "Downloading" : "Download") : null),
      ]),
      isThis ? voiceProgressBar(pct) : null,
      isThis ? el("div", { id: "vm-vdl-text", class: "ink-3", style: { fontSize: "11.5px", marginTop: "4px" } }, raw(fmtGB(p.downloaded) + " of " + fmtGB(p.total) + "  (" + pct + "%)")) : null,
    ]);
  }));
}
// A short, honest "what am I getting" explanation of the model families, so a person who does not
// want to think about models understands what runs for their language. The SIZE they download below
// applies to whichever family their language uses (Afrikaans -> Fluister, else -> Whisper).
function modelFamiliesNote() {
  function li(text) { return el("li", { style: { marginBottom: "3px" }, text: text }); }
  return el("div", { class: "card", style: { padding: "12px 14px", marginBottom: "12px", background: "var(--surface-2)" } }, [
    el("div", { class: "row gap-8", style: { alignItems: "center", marginBottom: "6px" } }, [
      el("span", { class: "tone-tile accent", style: { width: "26px", height: "26px", flex: "0 0 auto" } }, icon("sparkle", 13)),
      el("div", { style: { fontWeight: "600", fontSize: "13px" }, text: "Three model families, chosen by language" }),
    ]),
    el("ul", { style: { margin: "0", paddingLeft: "18px", fontSize: "12px", lineHeight: "1.55", color: "var(--ink-2)" } }, [
      li("Fluister, our Afrikaans-tuned model: best for Afrikaans and mixed Afrikaans and English meetings. It downloads automatically the first time you transcribe Afrikaans."),
      li("Swivuriso, by African Next Voices (DSFSI): one model for seven South African languages (isiZulu, isiXhosa, Sesotho, Setswana, Xitsonga, isiNdebele, Tshivenda). Beta."),
      li("English and other languages use standard Whisper, the model you download below."),
      li("The size you pick (speed against accuracy) applies to whichever family your language needs."),
    ]),
  ]);
}
// ── Settings: one consistent model card per family ──────────────────────────
// All three families (Afrikaans/Fluister, the other South African languages/Swivuriso, general
// Whisper) render with the SAME card via voiceModelRow: title, download size, description, and
// Installed/Download + Remove. Built from the backend catalogues (d.fluister, d.swivuriso, d.models).
function vmChip(cls, text) { return el("span", { class: "chip " + cls, style: { marginLeft: "8px" }, text: text }); }
function vmPct(p) { return (p && p.total) ? Math.min(100, Math.round((p.downloaded || 0) * 100 / p.total)) : 0; }
function voiceModelSectionHeader(title, rightEl, sub) {
  return el("div", { style: { marginTop: "4px", marginBottom: "8px" } }, [
    el("div", { class: "row", style: { justifyContent: "space-between", alignItems: "center" } }, [
      el("div", { style: { fontWeight: "600", fontSize: "13px" }, text: title }),
      rightEl || null,
    ]),
    sub ? el("div", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "3px" }, text: sub }) : null,
  ]);
}
// o: title, badges[], sizeBytes, present, description, note, downloading, isThis, pct, p,
//    onDownload, onRemove, status (element shown when present, before Remove), disabled, disabledNote.
function voiceModelRow(o) {
  var right;
  if (o.present) {
    right = el("div", { class: "row gap-8", style: { alignItems: "center", flex: "0 0 auto" } }, [
      o.status || el("span", { class: "chip ok" }, [icon("check", 12), "Installed"]),
      o.onRemove ? el("button", { class: "btn ghost sm", onclick: o.onRemove }, "Remove") : null,
    ]);
  } else if (o.disabled) {
    right = null;
  } else {
    right = el("button", { class: "btn", onclick: function () { if (o.downloading) return; o.onDownload(); } }, o.isThis ? "Downloading" : "Download");
  }
  var title = [el("span", { text: o.title })].concat(o.badges || []).concat([
    el("span", { class: "chip", style: { marginLeft: "8px" } }, raw(fmtGB(o.sizeBytes))),
  ]);
  return el("div", { class: "card", style: { padding: "14px", opacity: o.disabled ? "0.6" : "1" } }, [
    el("div", { class: "row", style: { justifyContent: "space-between", alignItems: "flex-start", gap: "12px" } }, [
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600" } }, title),
        o.description ? el("div", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "2px" }, text: o.description }) : null,
        o.note ? el("div", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "4px" }, text: o.note }) : null,
        o.disabledNote ? el("div", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "4px" }, text: o.disabledNote }) : null,
      ]),
      right,
    ]),
    o.isThis ? voiceProgressBar(o.pct) : null,
    o.isThis ? el("div", { id: "vm-vdl-text", class: "ink-3", style: { fontSize: "11.5px", marginTop: "4px" } }, raw(fmtGB(o.p.downloaded) + " of " + fmtGB(o.p.total) + "  (" + o.pct + "%)")) : null,
  ]);
}
// Afrikaans (Fluister) is OURS, so unlike stock Whisper it improves over time: a manual "Check for
// updates" offers an opt-in newer version (the only time the app reaches our server about models, and
// only on click). Loads from the local cache (local_files_only), so a newer Fluister can ONLY arrive
// through this opt-in. Each of the four sizes is a consistent card with Download / Installed + Remove.
function afrikaansModelSection() {
  var d = S.voiceModels; if (!d) return null;
  var p = d.progress || {}; var downloading = p.state === "downloading";
  var fl = (d.fluister || []).slice();
  // For Afrikaans we recommend High quality (the large-v3-turbo Fluister tune), NOT Best (large-v3):
  // our turbo tune is the Afrikaans sweet spot (it beats large-v3 on real Afrikaans, is GPU-optional
  // and faster). This override is display-only and scoped to the Fluister section; the hardware
  // recommended_model still drives the general Whisper section and the first-run auto-download.
  var fluisterRec = "large-v3-turbo";
  var anyInstalled = fl.some(function (m) { return m.present; });
  var ups = {}; ((S.modelUpdates && S.modelUpdates.updates) || []).forEach(function (u) { ups[u.repo] = u; });
  var checking = S._checkingModelUpdates;
  // The offline-only edition compiles out the model-update route, so hide its button there.
  var checkBtn = (anyInstalled && !offlineBuild()) ? el("button", { class: "btn ghost sm", disabled: checking, onclick: function () { checkModelUpdates(); } }, checking ? "Checking" : "Check for updates") : null;
  fl.sort(function (a, b) { return (b.size === fluisterRec ? 1 : 0) - (a.size === fluisterRec ? 1 : 0); });
  return el("div", { style: { marginBottom: "14px" } }, [
    voiceModelSectionHeader("Afrikaans model (Fluister)", checkBtn, "Our Afrikaans-tuned model. Best for Afrikaans and mixed Afrikaans and English."),
    el("div", { class: "stack", style: { gap: "10px" } }, fl.map(function (m) {
      var meta = VOICE_LABELS[m.size] || { title: m.size, note: "" };
      var u = ups[m.repo];
      var isThis = downloading && p.kind === "fluister" && p.model === m.size;
      var badges = [];
      if (m.size === fluisterRec) badges.push(vmChip("accent", "Recommended"));
      var status = null;
      if (u && u.update_available) status = el("button", { class: "btn sm", disabled: downloading, onclick: function () { if (downloading) return; startFluisterUpdate(m.size); } }, isThis ? "Updating" : ("Update → v" + u.latest));
      else if (S.modelUpdates) status = el("span", { class: "chip ok" }, [icon("check", 12), "Up to date"]);
      return voiceModelRow({
        title: meta.title, badges: badges,
        sizeBytes: (m.present && m.size_on_disk) ? m.size_on_disk : m.approx_bytes,
        present: m.present, description: meta.note,
        downloading: downloading, isThis: isThis, pct: vmPct(p), p: p,
        onDownload: function () { startFluisterDownload(m.size); },
        onRemove: function () { confirmRemoveVoiceItem(meta.title + " (Fluister)", m.repo, m.size_on_disk || m.approx_bytes); },
        status: status,
      });
    })),
  ]);
}
function startFluisterDownload(size) {
  api.post("/api/voice-model/fluister-download", { size: size })
    .then(function () { pollVoiceDownload(); render(); })
    .catch(function (e) { toast(e.message || "Could not start the download.", true); });
}
function checkModelUpdates() {
  S._checkingModelUpdates = true; render();
  api.post("/api/model-updates").then(function (d) {
    S.modelUpdates = d; S._checkingModelUpdates = false; render();
    if (!d.any_update) { toast("Your Afrikaans model is up to date."); }
  }).catch(function () {
    S._checkingModelUpdates = false; render();
    toast("Could not check for model updates.", true);
  });
}
function startFluisterUpdate(size) {
  api.post("/api/voice-model/update", { size: size })
    .then(function () { pollVoiceDownload(); render(); })
    .catch(function (e) { toast(e.message || "Could not start the update.", true); });
}
// The other South African languages (Swivuriso, by DSFSI / African Next Voices). One model covers
// all seven; only High quality is available. We did not train it, so it carries its own name +
// credit (MIT), and is beta. Same consistent card as the rest, with Download / Installed + Remove.
function saLanguagesSection() {
  var d = S.voiceModels; if (!d || !d.swivuriso) return null;
  var sv = d.swivuriso; var p = d.progress || {}; var downloading = p.state === "downloading";
  var isThis = downloading && p.kind === "swivuriso";
  return el("div", { style: { marginBottom: "14px" } }, [
    voiceModelSectionHeader("Other South African languages (Swivuriso)", el("span", { class: "chip", text: "Beta" }), null),
    el("div", { style: { fontSize: "11.5px", color: "var(--ink-3)", marginBottom: "8px" } }, raw("isiZulu, isiXhosa, Sesotho, Setswana, Xitsonga, isiNdebele, Tshivenda")),
    voiceModelRow({
      title: "High quality",
      sizeBytes: (sv.present && sv.size_on_disk) ? sv.size_on_disk : sv.approx_bytes,
      present: sv.present,
      description: "One model covers all seven South African languages, on auto-detect. Only High quality is available.",
      note: "Model by DSFSI, African Next Voices. MIT licence.",
      downloading: downloading, isThis: isThis, pct: vmPct(p), p: p,
      onDownload: function () { startSwivurisoDownload(); },
      onRemove: function () { confirmRemoveVoiceItem("South African languages (Swivuriso)", sv.repo, sv.size_on_disk || sv.approx_bytes); },
    }),
  ]);
}
function startSwivurisoDownload() {
  api.post("/api/voice-model/swivuriso-download")
    .then(function () { pollVoiceDownload(); render(); })
    .catch(function (e) { toast(e.message || "Could not start the download.", true); });
}
function voiceModelCard() {
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "Transcription model, on this machine" }),
    el("div", { class: "set-row", style: { display: "block" } }, [
      el("div", { class: "t", style: { marginBottom: "4px" }, text: "Download or switch model" }),
      el("div", { class: "s", style: { marginBottom: "10px" }, text: "Volksmond transcribes on this computer. Download the model that suits your machine; the recommended one is marked. Bigger is more accurate, but slower and larger to download. Remove any you no longer need to free space." }),
      modelFamiliesNote(),
      afrikaansModelSection(),
      saLanguagesSection(),
      voiceModelSectionHeader("General Whisper models", null, "For English and other languages."),
      voiceDownloadPanel(true),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("folder", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Where models are stored" }),
        el("div", { class: "s mono", style: { fontSize: "11px", wordBreak: "break-all" } }, raw((S.appInfo && S.appInfo.voice_models_dir) || "")),
        el("div", { class: "s", style: { fontSize: "11px", marginTop: "4px" }, text: "You can delete these folders by hand to free space if you ever need to." })]),
      el("div", { class: "ctl" }, el("button", { class: "btn ghost", onclick: function () { api.post("/api/open-folder?which=voice_models").catch(function () {}); } }, "Open")),
    ]),
  ]);
}
/* ── NVIDIA CUDA (optional GPU acceleration) ─── shared by setup + settings ─── */
var _cudaTimer = null;
function loadCuda() {
  api.get("/api/cuda").then(function (d) { S.cuda = d; render(); }).catch(function () {});
}
function startCudaDownload() {
  api.post("/api/cuda/download").then(function () { pollCudaDownload(); render(); })
    .catch(function (e) { toast(e.message || "Could not start the download.", true); });
}
function pollCudaDownload() {
  if (_cudaTimer) return;
  _cudaTimer = setInterval(function () {
    api.get("/api/cuda").then(function (d) {
      S.cuda = d;
      var p = (d && d.progress) || {};
      if (p.state === "downloading") { updateCudaProgress(p); return; }
      clearInterval(_cudaTimer); _cudaTimer = null;
      if (p.state === "done") { toast(d.ready ? "GPU ready. No restart needed." : "CUDA libraries ready. Restart Volksmond to use your GPU."); }
      else if (p.state === "error") { toast("Download failed: " + (p.error || ""), true); }
      render();
    }).catch(function () {});
  }, 1000);
}
function updateCudaProgress(p) {
  var pct = p.total ? Math.min(100, Math.round((p.downloaded || 0) * 100 / p.total)) : 0;
  var bar = document.getElementById("vm-cuda-bar");
  if (bar) bar.style.width = pct + "%";
  var txt = document.getElementById("vm-cuda-text");
  if (txt) txt.textContent = fmtGB(p.downloaded) + " of " + fmtGB(p.total) + "  (" + pct + "%)";
}
function cudaProgressBar(pct) {
  return el("div", { style: { height: "8px", borderRadius: "999px", background: "var(--line)", overflow: "hidden", marginTop: "10px" } },
    el("div", { id: "vm-cuda-bar", style: { height: "100%", width: pct + "%", background: "var(--accent)", transition: "width .3s ease" } }));
}
// The CUDA download card. Returns null unless the platform supports CUDA at all
// (supported === false on e.g. macOS hides it entirely) and an NVIDIA GPU is present.
function cudaPanel(manage) {
  var c = S.cuda;
  if (!c || c.supported === false || !c.gpu_present) return null;
  var p = c.progress || {};
  var downloading = p.state === "downloading";
  var pct = (downloading && p.total) ? Math.min(100, Math.round((p.downloaded || 0) * 100 / p.total)) : 0;
  var vram = c.vram_mb ? (Math.round(c.vram_mb / 1024) + " GB") : "";
  var action;
  if (c.installed && c.ready) action = el("span", { class: "chip ok" }, [icon("check", 12), "Active"]);
  else if (c.installed) action = el("span", { class: "chip warn" }, [el("span", { class: "dot" }), "Restart to use"]);
  else if (downloading) action = el("button", { class: "btn", disabled: true }, "Downloading");
  else action = el("button", { class: "btn primary", onclick: function () { startCudaDownload(); } }, "Download");
  return el("div", { class: "card", style: { padding: "14px" } }, [
    el("div", { class: "row", style: { justifyContent: "space-between", alignItems: "flex-start", gap: "12px" } }, [
      el("div", { class: "grow" }, [
        // The chip carries the DOWNLOAD size (like every other download bubble in the app). The
        // detected card and its VRAM go in a separate line so the two are never confused.
        el("div", { style: { fontWeight: "600" } }, [el("span", { text: "NVIDIA GPU acceleration" }), c.approx_bytes ? el("span", { class: "chip", style: { marginLeft: "8px" } }, raw(fmtGB(c.approx_bytes))) : null]),
        (c.gpu_name || vram) ? el("div", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "2px" } }, [el("span", { text: "Detected" }), raw(": " + (c.gpu_name || "NVIDIA GPU") + (vram ? " (" + vram + ")" : ""))]) : null,
        el("div", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "4px" }, text: "Download the NVIDIA CUDA libraries to run the Best model on your GPU, much faster than the CPU. NVIDIA only." }),
      ]),
      action,
    ]),
    downloading ? cudaProgressBar(pct) : null,
    downloading ? el("div", { id: "vm-cuda-text", class: "ink-3", style: { fontSize: "11.5px", marginTop: "4px" } }, raw(fmtGB(p.downloaded) + " of " + fmtGB(p.total) + "  (" + pct + "%)")) : null,
    (c.installed && !c.ready) ? el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "6px" }, text: "Downloaded. Close and reopen Volksmond to start using your GPU." }) : null,
    (manage && c.installed) ? el("div", { class: "row", style: { justifyContent: "flex-end", marginTop: "8px", gap: "8px" } }, [
      el("button", { class: "btn ghost sm", onclick: function () { checkGpu(); } }, "Check GPU"),
      el("button", { class: "btn ghost sm", onclick: function () { confirmRemoveCuda(); } }, "Remove"),
    ]) : null,
  ]);
}
function confirmRemoveCuda() {
  confirmModal({
    title: "Remove CUDA libraries?",
    message: "Remove the NVIDIA CUDA libraries from your computer? Transcription falls back to the CPU. You can download them again later.",
    detail: "NVIDIA CUDA libraries  (~1.5 GB)",
    confirmLabel: "Remove", danger: true,
    onConfirm: function () {
      api.post("/api/cuda/remove").then(function () { toast("CUDA libraries removed."); loadCuda(); })
        .catch(function (e) { toast(e.message || "Could not remove.", true); });
    },
  });
}
function checkGpu() {
  api.post("/api/cuda/self-test").then(function (r) {
    if (r && r.ok) toast("GPU is working. It will be used for transcription.");
    else toast("GPU check failed: " + ((r && r.error) || ""), true);
    loadCuda();
  }).catch(function (e) { toast(e.message || "Could not check the GPU.", true); });
}
function cudaCard() {
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "GPU acceleration (NVIDIA only)" }),
    el("div", { class: "set-row", style: { display: "block" } }, [
      el("div", { class: "s", style: { marginBottom: "10px" }, text: "Run the Best model on your NVIDIA graphics card instead of the CPU. Optional, and NVIDIA only; AMD and Intel graphics use the CPU." }),
      cudaPanel(true),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("folder", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Where the CUDA libraries are stored" }),
        el("div", { class: "s mono", style: { fontSize: "11px", wordBreak: "break-all" } }, raw((S.appInfo && S.appInfo.cuda_dir) || ""))]),
      el("div", { class: "ctl" }, el("button", { class: "btn ghost", onclick: function () { api.post("/api/open-folder?which=cuda").catch(function () {}); } }, "Open")),
    ]),
  ]);
}
// "Run summaries on" GPU/CPU choice. Returns null unless this build can actually offload
// summaries to an NVIDIA GPU (summary_gpu_capable), so a CPU build never shows a dead toggle.
function summaryDeviceRow() {
  if (!(S.models && S.models.summary_gpu_capable)) return null;
  var v = (S.settings && S.settings.summary_device === "cpu") ? "cpu" : "auto";
  var seg = el("div", { class: "segmented", style: { width: "auto" } },
    [["auto", "GPU"], ["cpu", "CPU"]].map(function (o) {
      return el("button", { class: v === o[0] ? "on" : "", onclick: function () { saveSettings({ summary_device: o[0] }); } }, el("span", { text: o[1] }));
    }));
  return el("div", { class: "set-row" }, [
    el("div", { class: "ic" }, icon("cpu", 18)),
    el("div", { class: "body" }, [
      el("div", { class: "t", text: "Run summaries on" }),
      el("div", { class: "s", text: v === "cpu"
        ? "Summaries run on the CPU."
        : "Summaries run on your NVIDIA GPU when the model fits, which is much faster. Falls back to the CPU automatically if it will not fit in graphics memory." }),
    ]),
    el("div", { class: "ctl" }, seg),
  ]);
}
function summariesCard() {
  var d = S.summaryModels || {};
  var anyActive = (d.installed != null) ? d.installed : !!(S.models && S.models.summary_installed);
  var footerOn = !(S.settings && S.settings.summary_footer === false);  // default on
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "Summaries, run on this machine" }),
    el("div", { class: "set-row", style: { display: "block" } }, [
      el("div", { class: "t", style: { marginBottom: "4px" }, text: anyActive ? "Summary model" : "Turn on summaries" }),
      el("div", { class: "s", style: { marginBottom: "10px" }, text: anyActive
        ? "Summaries run on this computer and are free. You can switch model below any time."
        : "Download a small model and Volksmond can summarise a finished transcript on this computer. Pick a size, we download it for you." }),
      summaryDownloadPanel(true),
    ]),
    summaryDeviceRow(),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("note", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t", text: "Add a small 'Made with Volksmond' line to summaries" }),
        el("div", { class: "s", text: "A single credit line at the end of the summary file only. Never added to the transcript, and never to anything you export to share." }),
      ]),
      el("div", { class: "ctl" }, toggleEl(footerOn, function () { saveSettings({ summary_footer: !footerOn }); })),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("folder", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Where summary models are stored" }),
        el("div", { class: "s mono", style: { fontSize: "11px", wordBreak: "break-all" } }, raw((S.appInfo && S.appInfo.summary_models_dir) || "")),
        el("div", { class: "s", style: { fontSize: "11px", marginTop: "4px" }, text: "You can delete these files by hand to free space if you ever need to." })]),
      el("div", { class: "ctl" }, el("button", { class: "btn ghost", onclick: function () { api.post("/api/open-folder?which=summary_models").catch(function () {}); } }, "Open")),
    ]),
  ]);
}
function dataCard(st) {
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "Data and privacy" }),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("folder", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Save transcripts and recordings to" }),
        el("div", { class: "s mono", style: { fontSize: "11.5px" }, text: st.save_location || (S.appInfo && S.appInfo.save_dir) || "default folder" }),
        el("div", { class: "s", style: { fontSize: "11.5px", marginTop: "6px" }, text: "For maximum privacy, choose a folder that a cloud provider does not sync (OneDrive, Google Drive, Dropbox, and the like)." })]),
      el("div", { class: "ctl" }, el("button", { class: "btn ghost", onclick: pickSaveFolder }, "Change")),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("lock", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Audio is off by default" }),
        el("div", { class: "s", text: "Recording is only kept when you switch it on for a meeting. The privacy promise holds otherwise." })]),
      el("div", { class: "ctl" }, el("span", { class: "chip ok" }, [icon("check", 12), "On by you only"])),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("bug", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Save diagnostics" }),
        el("div", { class: "s", text: "Writes a small zip to your Downloads folder with the app logs, your settings and what this computer is. Attach it when you report a problem. No transcripts, no notes, no audio, no licence key, and nothing is sent anywhere." })]),
      el("div", { class: "ctl" }, el("button", { class: "btn ghost", onclick: function () { saveDiagnostics(false); } }, "Save")),
    ]),
  ]);
}
async function pickSaveFolder() {
  var p = await pickFile("folder");
  if (!p) return;
  try { S.settings = await api.post("/api/settings", { save_location: p }); S.appInfo = await api.get("/api/app-info"); toast("Save location updated."); render(); }
  catch (e) { toast(e.message || "Not a writable folder.", true); }
}
function dangerCard(st) {
  var has = st.has_cloud_api_key;
  var keyInput = el("input", { class: "field", type: "password", placeholder: has ? "A key is stored. Paste a new one to replace it." : "sk-... your provider key" });
  return el("div", { class: "card", style: { marginBottom: "16px", borderColor: "color-mix(in oklch, var(--record) 35%, var(--line))" } }, [
    el("div", { class: "row gap-8", style: { padding: "12px 18px", background: "var(--record-soft)", color: "var(--record)", borderRadius: "var(--radius-lg) var(--radius-lg) 0 0" } },
      [icon("alert", 16), el("span", { class: "section-label", style: { color: "var(--record)" }, text: "Danger zone, these settings can send data off your computer" })]),
    el("div", { style: { padding: "4px 18px 16px" } }, [
      el("div", { class: "set-row", style: { display: "block", borderTop: "0" } }, [
        el("div", { class: "t", style: { marginBottom: "4px" } }, [el("span", { text: "Online API key for a future fallback" }), el("span", { class: "pro-badge", text: "Business" })]),
        el("p", { class: "s", style: { marginBottom: "10px" }, text: "For weak machines that cannot keep up. When an online fallback is enabled in a later version, audio or transcript text would be sent to the provider you choose. Your data would leave this computer. Not recommended for counselling, legal, or any confidential context. The key is stored encrypted on this machine." }),
        keyInput,
        el("div", { class: "row gap-8", style: { marginTop: "10px", justifyContent: "flex-end" } }, [
          has ? el("button", { class: "btn ghost sm", onclick: function () { saveSettings({ cloud_api_key: "" }); } }, "Clear key") : null,
          el("button", { class: "btn sm", onclick: function () { var v = keyInput.value.trim(); if (v) saveSettings({ cloud_api_key: v }); } }, "Save key"),
        ]),
      ]),
    ]),
  ]);
}

/* ── upgrade / activation ─────────────────────────────────── */
function upgradeView() {
  var ks = S.upgrade.keyState;
  var ring = ks === "success" ? "var(--ok)" : ks === "error-bad" ? "var(--danger)" : ks === "error-version" ? "var(--warn)" : "var(--line)";
  var help = null;
  if (ks === "success") help = el("p", { style: { color: "var(--ok)", fontSize: "12px", marginTop: "8px" }, text: "Activated offline. Your licence is valid on this computer." });
  else if (ks === "error-version") help = el("p", { style: { color: "var(--warn)", fontSize: "12px", marginTop: "8px" }, text: "That key is for a different major version of Volksmond." });
  else if (ks === "error-bad") help = el("p", { style: { color: "var(--danger)", fontSize: "12px", marginTop: "8px" }, text: S.upgrade.msg || "That key did not match. Check for a typo, or paste it from your purchase email." });

  function personalCard() {
    return el("div", { class: "card", style: { padding: "20px" } }, [
      el("div", { class: "row", style: { justifyContent: "space-between", marginBottom: "8px" } }, [el("div", { style: { fontWeight: "600" }, text: "Personal" }), el("span", { class: "chip", text: "Free" })]),
      bulletList(["Unlimited local live transcription", "Local summaries, on this machine", "Afrikaans, English, and the mix", "Save and export, fully offline"]),
      el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "10px" }, text: "For your own meetings, study, or personal projects. No account, no licence, forever." }),
    ]);
  }
  function businessCard() {
    return el("div", { class: "card disclosure accent", style: { padding: "20px" } }, [
      el("div", { class: "row", style: { justifyContent: "space-between", marginBottom: "8px" } }, [
        el("div", { class: "row gap-8" }, [el("span", { style: { fontWeight: "600" }, text: "Business" }), el("span", { class: "chip accent" }, [icon("crown", 11), "Licence"])]),
        el("span", { class: "mono ink-3", text: "Per person" }),
      ]),
      bulletList(["Everything in Personal", "Licensed for business and professional use", "Pull attendee names from your Outlook calendar", "Priority email support"]),
      el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "10px" }, text: "For teams and paid client work. A licence per person, renewed yearly. Everything that runs on this computer stays free." }),
    ]);
  }
  // Coming soon: an OPTIONAL online tier that runs our most accurate SA models on DigiPhyte's own
  // hardware in South Africa. It is explicit that audio leaves the machine, and why it is still
  // POPIA-friendly (our hardware, in-country); the free local tier is never replaced.
  function comingSoonCard() {
    return el("div", { class: "card", style: { padding: "14px 18px", display: "flex", gap: "12px" } }, [
      el("div", { class: "tone-tile muted", style: { width: "32px", height: "32px", flex: "0 0 auto" } }, icon("clock", 16)),
      el("div", {}, [
        el("div", { class: "row gap-8", style: { alignItems: "center" } }, [
          el("span", { style: { fontWeight: "600", fontSize: "13.5px" }, text: "Premium South African transcription models" }),
          el("span", { class: "chip", text: "Coming soon" }),
        ]),
        el("p", { class: "ink-3", style: { fontSize: "11.5px", margin: "4px 0 0" }, text: "An optional online tier that runs our most accurate South African models on DigiPhyte's own hardware in South Africa. Your audio would leave this computer, but it stays in the country on hardware we control, so it remains POPIA-friendly. The local, offline transcription always stays free and is never replaced." }),
      ]),
    ]);
  }
  return el("div", { class: "screen center" }, el("div", { class: "screen-inner col-mid stack", style: { gap: "16px" } }, [
    el("button", { class: "btn ghost sm", style: { alignSelf: "flex-start" }, onclick: function () { go("settings"); } }, [icon("back", 14), "Back to settings"]),
    el("div", {}, [el("div", { class: "eyebrow", text: "Business licensing" }), el("h1", { style: { marginTop: "6px" }, text: "Free for personal use. A licence for business." })]),
    el("p", { class: "ink-2", text: "Personal use is the real thing: unlimited live transcription and local summaries, on this machine, forever. A business licence covers commercial and team use, and unlocks the extras for professional work." }),
    el("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" } }, [personalCard(), businessCard()]),
    comingSoonCard(),
    el("div", { class: "card", style: { padding: "20px" } }, [
      el("button", { class: "btn primary tall", onclick: function () { openExternal(BUSINESS_PAGE_URL); } }, "View business licensing"),
      el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "8px" }, text: "Opens volksmond.digiphyte.com/business for current pricing. You get a licence key by email, and activation is fully offline." }),
      el("div", { class: "divider-label", text: "Already have a key" }),
      el("div", { class: "row gap-8" }, [
        el("input", { class: "field mono", style: { outline: "2px solid " + ring, outlineOffset: "-1px" }, placeholder: "Paste your licence key, e.g. VM1-XXXX-XXXX-XXXX-XXXX", value: S.upgrade.value, oninput: function (e) { S.upgrade.value = e.target.value; } }),
        el("button", { class: "btn", onclick: activateLicence }, "Activate"),
      ]),
      help,
      el("p", { class: "ink-3", style: { fontSize: "11px", marginTop: "10px" }, text: "Your key is checked on this computer, never on a server. No account, no phone-home." }),
    ]),
  ]));
}
function bulletList(items) {
  return el("ul", { class: "bullet-list" }, items.map(function (t) { return el("li", {}, [el("span", { class: "ic" }, icon("check", 14)), el("span", { text: t })]); }));
}

/* ── stop menu popover ────────────────────────────────────── */
function stopMenuLayer() {
  var anchor = document.getElementById("stop-anchor");
  var rect = anchor ? anchor.getBoundingClientRect() : { left: 24, bottom: window.innerHeight - 80, top: window.innerHeight - 120 };
  var pop = el("div", { class: "popover", style: { left: rect.left + "px", bottom: (window.innerHeight - rect.top + 8) + "px", minWidth: "360px" } }, [
    el("div", { class: "section-label", style: { padding: "6px 10px 8px" }, text: "You have recording and transcription on" }),
    stopRow("transcription", true, "Stop transcription, keep recording", "Falls back to a quiet recording. Transcribe and summarise it after the meeting.", "T"),
    stopRow("recording", false, "Stop recording, keep transcribing", "The live transcript continues. Nothing more is saved as audio.", "R"),
    el("div", { style: { height: "1px", background: "var(--line)", margin: "6px 8px" } }),
    stopRow("all", false, "Stop recording and transcription", "End the session and save what you have.", null, true),
  ]);
  return el("div", {}, [
    el("div", { class: "popover-backdrop", onclick: function () { S.stopMenuOpen = false; render(); } }),
    pop,
  ]);
}
function stopRow(what, recommended, title, sub, kb, finish) {
  return el("button", { class: "stop-row" + (recommended ? " recommended" : ""), onclick: function () { doStop(what); } }, [
    el("span", { class: "mk" }, icon(finish ? "stop" : (recommended ? "check" : "dot"), 15)),
    el("div", { class: "grow" }, [
      el("div", { class: "t" }, [el("span", { text: title }), recommended ? el("span", { class: "chip accent", text: "Recommended" }) : null]),
      el("div", { class: "s", text: sub }),
    ]),
    kb ? el("div", { class: "kb" }, el("kbd", { text: kb })) : null,
  ]);
}

/* ── transcription model family (language-first) ──────────── */
// The spoken LANGUAGE picks the model family: Afrikaans -> Fluister (our Afrikaans-tuned
// Whisper), everything else -> stock Whisper. The hardware picks the SIZE, so the user mostly
// just picks a language. Mirrors transcribe.family_for_language on the server.
var SUPPORTED_LANGS = [
  { code: "af", name: "Afrikaans", family: "fluister" },
  { code: "en", name: "English", family: "whisper" },
  { code: "zu", name: "isiZulu", family: "swivuriso" },
  { code: "xh", name: "isiXhosa", family: "swivuriso" },
  { code: "st", name: "Sesotho", family: "swivuriso" },
  { code: "tn", name: "Setswana", family: "swivuriso" },
  { code: "ts", name: "Xitsonga", family: "swivuriso" },
  { code: "nr", name: "isiNdebele", family: "swivuriso" },
  { code: "ve", name: "Tshivenda", family: "swivuriso" },
];
var SWIVURISO_LANGS = ["zu", "xh", "st", "tn", "ts", "nr", "ve"];
// Major world languages standard Whisper handles well, offered under "More languages" so a
// known single-language meeting can FORCE its token instead of relying on auto-detect (which
// can flap between languages from chunk to chunk).
var WORLD_LANGS = [
  ["de", "German"], ["fr", "French"], ["es", "Spanish"], ["pt", "Portuguese"],
  ["it", "Italian"], ["nl", "Dutch"], ["zh", "Mandarin"], ["ar", "Arabic"],
  ["hi", "Hindi"], ["ru", "Russian"], ["ja", "Japanese"], ["ko", "Korean"],
  ["pl", "Polish"], ["tr", "Turkish"], ["sv", "Swedish"], ["no", "Norwegian"],
  ["da", "Danish"], ["el", "Greek"],
];
var LANG_NAMES = { "af": "Afrikaans", "en": "English", "": "Auto-detect", "sa": "South African languages",
  "zu": "isiZulu", "xh": "isiXhosa", "st": "Sesotho", "tn": "Setswana", "ts": "Xitsonga", "nr": "isiNdebele", "ve": "Tshivenda" };
WORLD_LANGS.forEach(function (w) { LANG_NAMES[w[0]] = w[1]; });
function langName(code) { return LANG_NAMES[code] != null ? LANG_NAMES[code] : code; }
// True for codes the Settings "Languages you transcribe" checkboxes cover; only those get
// clamped when a language is un-ticked. The "sa" group code and world codes are never clamped.
function langIsToggleable(code) { return SUPPORTED_LANGS.some(function (l) { return l.code === code; }); }
function familyForLang(lang) { var l = (lang || "").toLowerCase().split("-")[0]; if (l === "sa" || SWIVURISO_LANGS.indexOf(l) >= 0) return "swivuriso"; return (l === "" || l === "auto" || /^af/.test(l)) ? "fluister" : "whisper"; }
// True once the matching model is actually installed; until then a session honestly runs (and is
// labelled) as stock Whisper.
function fluisterReady() { return !!(S.voiceModels && S.voiceModels.fluister_available); }
function swivurisoReady() { return !!(S.voiceModels && S.voiceModels.swivuriso && S.voiceModels.swivuriso.present); }
function familyLabelFor(lang) { var f = familyForLang(lang); if (f === "swivuriso") return swivurisoReady() ? "Swivuriso" : "Whisper"; return (f === "fluister" && fluisterReady()) ? "Fluister" : "Whisper"; }
// Proper-noun family name shown to the user (never translated).
function familyDisplay(family) { return family === "fluister" ? "Fluister" : family === "swivuriso" ? "Swivuriso" : "Whisper"; }
// The family a run will ACTUALLY use, honouring an explicit engine override, else the language.
// Mirrors the backend's family_for_language + engine-override resolution.
function familyFor(language, engine) {
  var eng = (engine || "auto").toLowerCase();
  if (eng === "fluister") return "fluister";
  if (eng === "whisper") return "whisper";
  return familyForLang(language);
}
function familyForForm() { return familyFor(S.form.language, S.form.engine); }
// The catalogue entry for a (family, size) from S.voiceModels, or null.
function vmSizeEntry(family, size) {
  var d = S.voiceModels || {};
  if (family === "swivuriso") return d.swivuriso || null;   // one model serves every size
  var list = family === "fluister" ? (d.fluister || []) : (d.models || []);
  var keyName = family === "fluister" ? "size" : "model";
  for (var i = 0; i < list.length; i++) { if (list[i][keyName] === size) return list[i]; }
  return null;
}
// Is the (family, size) build already downloaded? Swivuriso is a single model that covers every
// size, so any size is "present" once it is installed (Begin will not download for that language).
function sizePresentInFamily(family, size) {
  if (family === "swivuriso") return swivurisoReady();
  var m = vmSizeEntry(family, size);
  return !!(m && m.present);
}
// Approx download size in bytes for a (family, size), for the "Downloads first time (~X GB)" hint.
function sizeApproxBytes(family, size) {
  var m = vmSizeEntry(family, size);
  return m ? (m.approx_bytes || 0) : 0;
}
// Cached pre-flight for the "Auto" tile, so it is honest instead of "always ready". Refetched only
// when (language, engine, device) change, reusing the same signature-guard idea as warmRender so a
// steady picker never re-hits the endpoint. _autoPf null = unknown/pending (tile stays neutral).
var _autoPfSig = null, _autoPf = null;
function autoPfSig() { return [S.form.language || "", S.form.engine || "auto", S.form.device || "auto"].join("|"); }
function refreshAutoPreflight() {
  var sig = autoPfSig();
  if (sig === _autoPfSig) return;   // inputs unchanged: keep the cached answer, no network call
  _autoPfSig = sig; _autoPf = null;
  api.post("/api/preflight-model", { tier: "auto", device: S.form.device || "auto", language: S.form.language || "", engine: S.form.engine || "auto" })
    .then(function (pf) {
      if (autoPfSig() !== _autoPfSig) return;   // inputs moved again while in flight: drop this answer
      _autoPf = pf;
      if (S.route === "pre" || S.route === "importpre") render();
    }).catch(function () {});
}
// Friendly size label from the loaded model id/path (a stock name like "large-v3", a hosted
// Fluister repo like "digiphyte/fluister-medium", or a local ct2 dir). Mirrors the Quality
// vocabulary so the live chip can read "Fluister, Best". The substring rules also cover the
// MLX repos (digiphyte/fluister-turbo-mlx, mlx-community/whisper-large-v3-mlx), which read
// the same as their ct2 twins ("High quality" / "Best").
function sizeLabelFromModel(model) {
  var m = (model || "").toLowerCase();
  if (!m) return "";
  if (m.indexOf("turbo") >= 0) return "High quality";
  if (m.indexOf("medium") >= 0) return "Balanced";
  if (m.indexOf("small") >= 0) return "Light";
  if (m.indexOf("base") >= 0 || m.indexOf("tiny") >= 0) return "Lite";
  if (m.indexOf("large") >= 0) return "Best";
  if (m.indexOf("af-lora") >= 0 || m.indexOf("fluister") >= 0) return "Best"; // bare Fluister == large-v3
  return "";
}
// True when the loaded model id is a local filesystem build (dev machine SA_LIVE_AF_MODEL / an
// af-lora-* ct2 dir), as opposed to a hosted repo id (digiphyte/..., Systran/...) or a bare size
// (small, large-v3). A hosted repo id is org/name (exactly one slash, no path structure); anything
// with af-lora, a backslash, or extra slashes is a real path, i.e. a local build.
function isLocalBuildModel(model) {
  var m = model || "";
  if (!m) return false;
  if (m.indexOf("af-lora") >= 0) return true;                 // our local ct2 Fluister builds
  if (m.indexOf("\\") >= 0) return true;                      // Windows path
  if (/^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/.test(m)) return false;   // hosted repo id org/name
  if (m.indexOf("/") >= 0) return true;                       // any other slashed path = local dir
  return false;                                               // bare size name
}
// The lean engine chip on the live / importing header: the family plus which size is running, so the
// user can see it is e.g. Fluister at Balanced without opening anything. The chip title always carries
// the exact model id (repo or local path) for inspection, and a subtle "local build" tag is appended
// when the running model loaded from a local ct2 dir rather than a downloaded model.
function familyChip(family, model) {
  var size = sizeLabelFromModel(model);
  var titleFor = function (desc) { return model ? (tr(desc) + " (" + model + ")") : tr(desc); };
  var localTag = isLocalBuildModel(model)
    ? el("span", { class: "chip-note", title: tr("Running from a local model build on this computer, not a downloaded model.") }, "local build")
    : null;
  if (family === "swivuriso") return el("span", { class: "chip accent", title: titleFor("South African languages model") }, [icon("globe", 12), el("span", {}, raw("Swivuriso")), localTag]);
  var name = (family === "fluister") ? "Fluister" : "Whisper";
  var label = size ? (name + ", " + tr(size)) : name;
  if (family === "fluister") return el("span", { class: "chip accent", title: titleFor("Afrikaans-optimised model") }, [icon("sparkle", 12), el("span", {}, raw(label)), localTag]);
  return el("span", { class: "chip" }, [el("span", {}, raw(label)), localTag]);
}
// The pre-meeting / import language picker: Afrikaans and English one tap away, everything else
// behind "More languages" (one dropdown: the seven Swivuriso South African languages first, then
// major world languages on standard Whisper), plus Auto-detect. "sa" stays the group code that
// routes to Swivuriso (see familyForLang and transcribe.family_for_language); a specific code
// forces that language token so the decoder cannot flap between languages mid-meeting. The
// Settings "languages you transcribe" list still drives which models first-run offers to download.
function langModeOpts() {
  return [["af", "Afrikaans"], ["en", "English"], ["more", "More languages"], ["", "Auto-detect"]];
}
// Which segment a language value belongs to: af / en / "" map to their own segments,
// everything else ("sa", zu..., de...) lives under More languages.
function langMode(lang) {
  var l = lang || "";
  return (l === "" || l === "af" || l === "en") ? l : "more";
}
// The grouped options for the More-languages dropdown: [groupLabel, [[code, name], ...]].
function moreLangOpts() {
  var sa = [["sa", "Any South African language"]];
  SWIVURISO_LANGS.forEach(function (c) { sa.push([c, langName(c)]); });
  return [["South African languages (Swivuriso)", sa],
          ["World languages (Whisper)", WORLD_LANGS.map(function (w) { return w.slice(); })]];
}
// Flat list of EVERY language mode, for the native selects that need one (the live tune strip
// and the re-transcribe dialog).
function transcribeLangOpts() {
  var flat = [["af", "Afrikaans"], ["en", "English"], ["sa", "South African languages"]];
  SWIVURISO_LANGS.forEach(function (c) { flat.push([c, langName(c)]); });
  WORLD_LANGS.forEach(function (w) { flat.push(w.slice()); });
  flat.push(["", "Auto-detect"]);
  return flat;
}
function moreLangSelect() {
  var sel = el("select", {
    class: "field", style: { width: "auto", minWidth: "220px", marginTop: "8px" },
    onchange: function (e) { S.form.language = S.form.moreLang = e.target.value; warmUp(); render(); },
  }, moreLangOpts().map(function (g) {
    return el("optgroup", { label: tr(g[0]) }, g[1].map(function (o) { return el("option", { value: o[0], text: o[1] }); }));
  }));
  sel.value = S.form.language;
  return sel;
}
// Language is the hero control on the pre-meeting screens. Switching it re-warms the matching
// family so Begin stays instant. Picking "More languages" reveals the grouped dropdown; the
// last specific pick is remembered (S.form.moreLang) so the segment toggles back to it.
function languageField() {
  var mode = langMode(S.form.language);
  var seg = segmented(langModeOpts(), mode, function (v) {
    S.form.language = (v === "more") ? (S.form.moreLang || "sa") : v;
    warmUp(); render();
  });
  return formField("Language", null, mode === "more" ? el("div", {}, [seg, moreLangSelect()]) : seg, true);
}
// One honest line: which engine this session will use (the language decides, unless the Advanced
// Engine override forces a family), and that the size is automatic.
function engineLine() {
  var lang = S.form.language;
  var ov = S.form.engine || "auto";
  var famWanted = (ov === "fluister") ? "fluister" : (ov === "whisper") ? "whisper" : familyForLang(lang);
  var label = (famWanted === "swivuriso") ? (swivurisoReady() ? "Swivuriso" : "Whisper")
            : (famWanted === "fluister" && fluisterReady()) ? "Fluister" : "Whisper";
  var msg;
  if (famWanted === "fluister" && !fluisterReady())
    msg = "Fluister (our Afrikaans-tuned model) is not installed on this computer yet, so this runs on standard Whisper for now.";
  else if (famWanted === "swivuriso" && !swivurisoReady())
    msg = "The Swivuriso model for South African languages is not installed on this computer yet, so this runs on standard Whisper for now.";
  else if (ov === "fluister")
    msg = "Forced to Fluister for every language. Handy when an English meeting has Afrikaans words mixed in.";
  else if (ov === "whisper")
    msg = "Forced to standard Whisper for every language.";
  else if (famWanted === "swivuriso")
    msg = "South African languages use Swivuriso, a model by African Next Voices (DSFSI). The size is chosen automatically for your computer.";
  else if (famWanted === "fluister")
    msg = (lang === "")
      ? "Auto-detect uses Fluister, our Afrikaans-tuned model. The size is chosen automatically for your computer."
      : "Best for Afrikaans and mixed Afrikaans and English meetings. The size is chosen automatically for your computer.";
  else msg = (lang === "en")
    ? "English uses standard Whisper. The size is chosen automatically for your computer."
    : tr(langName(lang)) + " " + tr("uses standard Whisper. The size is chosen automatically for your computer.");
  return el("div", { class: "card", style: { padding: "11px 13px", display: "flex", gap: "10px", alignItems: "center", marginBottom: "16px" } }, [
    el("div", { class: "tone-tile" + ((label === "Fluister" || label === "Swivuriso") ? " accent" : ""), style: { width: "30px", height: "30px", flex: "0 0 auto" } }, icon(label === "Fluister" ? "sparkle" : (label === "Swivuriso" ? "language" : "globe"), 15)),
    el("div", {}, [
      el("div", { style: { fontWeight: "600", fontSize: "12.5px" } }, [el("span", { text: "Engine: " }), el("span", { text: label })]),
      el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "1px" }, text: msg }),
    ]),
  ]);
}
// Size (Quality) and processor (GPU/CPU) are now advanced: the hardware picks the size by
// default. Tucked behind a disclosure so the pre-meeting screen stays about the language.
function advancedTranscribeControls(live) {
  var open = !!S.form.advancedOpen;
  var toggle = el("button", { class: "btn ghost sm", style: { padding: "5px 9px" }, onclick: function () { S.form.advancedOpen = !open; render(); } },
    [icon(open ? "chevDown" : "chevRight", 14), el("span", { text: "Advanced" })]);
  if (!open) return el("div", { style: { marginBottom: "16px" } }, toggle);
  return el("div", { style: { marginBottom: "16px" } }, [toggle,
    el("div", { class: "card", style: { padding: "14px", marginTop: "8px" } }, [
      formField("Engine", el("span", { class: "label-muted", text: " (auto follows the language)" }),
        el("div", {}, [segmented([["auto", "Auto"], ["fluister", "Fluister"], ["whisper", "Whisper"]], S.form.engine || "auto", function (v) { S.form.engine = v; saveSettings({ engine: v }); warmUp(); render(); }),
          el("p", { class: "ink-3", style: { fontSize: "11px", margin: "6px 0 0" }, text: "Auto picks the model for your language: Fluister for Afrikaans and auto-detect, Swivuriso for South African languages, Whisper for the rest. Force one to override." })]), true),
      formField("Model size", el("span", { class: "label-muted", text: " (auto is recommended)" }),
        el("div", { style: { marginTop: "12px" } }, [qualitySelector(),
          el("p", { class: "ink-3", style: { fontSize: "11px", margin: "6px 0 0" }, text: "Auto picks the best model your computer can run. Bigger is more accurate but slower." })]), true),
      runOnField(),
      live ? agcLiveControl() : null,
      live ? aecLiveControl() : aecRetranscribeControl(),
      (!live && S.route === "importpre" && S.importPath) ? stereoSplitControl() : null,
    ]),
  ]);
}
// Stereo interview mode for an UPLOADED file: transcribe the left and right channels as two
// separate speakers (Speaker L / Speaker R). Per-file choice, not persisted: it only makes
// sense for recordings whose channels really carry one speaker each (e.g. Samsung Interview
// mode). Not shown for saved Volksmond recordings, whose stereo channels mean you/everyone-else.
function stereoSplitControl() {
  return el("div", { style: { marginTop: "12px", paddingTop: "12px", borderTop: "1px solid var(--line)" } },
    el("div", { class: "row gap-10", style: { alignItems: "flex-start" } }, [
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600", fontSize: "13px" }, text: "Stereo interview mode" }),
        el("p", { class: "ink-3", style: { fontSize: "11px", margin: "4px 0 0" }, text: "For phone recordings where the two speakers sit in the left and right channels (e.g. Samsung Interview mode). Transcribes each side separately, labelled Speaker L and Speaker R. A mono file is transcribed as a single track." }),
      ]),
      toggleEl(!!S.form.stereoSplit, function () { S.form.stereoSplit = !S.form.stereoSplit; render(); }),
    ]));
}
// Echo cancellation for a re-transcribe / upload (the non-live Advanced panel). Mirrors
// aecLiveControl but persists the `aec` setting; only does anything when the file set has both a
// MIC and a SYS channel. Full screen, so saveSettings()'s render() is fine here.
function aecRetranscribeControl() {
  return el("div", { style: { marginTop: "12px", paddingTop: "12px", borderTop: "1px solid var(--line)" } },
    el("div", { class: "row gap-10", style: { alignItems: "flex-start" } }, [
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600", fontSize: "13px" }, text: "Cancel speaker echo" }),
        el("p", { class: "ink-3", style: { fontSize: "11px", margin: "4px 0 0" }, text: "Off by default. When you re-transcribe a recording, remove the other side's voice that your microphone re-heard through the speakers. Best when you are mostly listening (a video or a one-sided talk). It can blur your own words when you and the other side talk over each other, so leave it off for normal back-and-forth meetings. No effect on headphones." }),
      ]),
      toggleEl(!!S.form.aec, function () { S.form.aec = !S.form.aec; saveSettings({ aec: S.form.aec }); }),
    ]));
}
// Live mic auto-gain toggle (live pre-meeting only). Default ON; persisted as agc_live.
// Independent of the echo-cancellation toggle below: AGC applies in both AEC states.
function agcLiveControl() {
  return el("div", { style: { marginTop: "12px", paddingTop: "12px", borderTop: "1px solid var(--line)" } },
    el("div", { class: "row gap-10", style: { alignItems: "flex-start" } }, [
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600", fontSize: "13px" }, text: tr("Auto mic volume") }),
        el("p", { class: "ink-3", style: { fontSize: "11px", margin: "4px 0 0" }, text: tr("Automatically boosts a quiet microphone to a healthy level, the way Meet and Teams do. Leave it on unless your microphone levels are already set exactly how you want them.") }),
      ]),
      toggleEl(!!S.form.agcLive, function () { S.form.agcLive = !S.form.agcLive; saveSettings({ agc_live: S.form.agcLive }); }),
    ]));
}
// Live echo cancellation toggle (live pre-meeting only; re-transcribe AEC has its own setting).
function aecLiveControl() {
  return el("div", { style: { marginTop: "12px", paddingTop: "12px", borderTop: "1px solid var(--line)" } },
    el("div", { class: "row gap-10", style: { alignItems: "flex-start" } }, [
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600", fontSize: "13px" } }, [el("span", { text: tr("Cancel echo live") }), el("span", { class: "chip", style: { marginLeft: "6px" }, text: tr("beta") })]),
        el("p", { class: "ink-3", style: { fontSize: "11px", margin: "4px 0 0" }, text: tr("Remove the other side's voice that your speakers leak into your microphone, live as the meeting happens. Best on speakers when you are mostly listening; it can blur your words during heavy crosstalk, and does nothing on headphones.") }),
      ]),
      toggleEl(!!S.form.aecLive, function () { S.form.aecLive = !S.form.aecLive; saveSettings({ aec_live: S.form.aecLive }); }),
    ]));
}

/* ── small shared builders ────────────────────────────────── */
// Quality choices, keyed by the model each maps to (plus "auto"). The SAME set is
// shown here and in the download panel, so the two never disagree. Auto is default.
var QUALITY_OPTS = [["auto", "Auto"], ["small", "Fast"], ["medium", "Balanced"], ["large-v3-turbo", "High quality"], ["large-v3", "Best"]];
// Legacy saved tier keys -> the new model-keyed quality, so old settings still highlight.
// The mlx tiers (Apple GPU via MLX, Mac only) map like their gpu twins so the live Quality
// select shows the true size instead of falling back to Auto.
var LEGACY_QUALITY = { "gpu": "large-v3", "gpu-4gb": "large-v3", "gpu-turbo": "large-v3-turbo", "gpu-medium": "medium", "gpu-small": "small", "cpu-large": "large-v3", "cpu-strong": "large-v3-turbo", "cpu-mid": "medium", "cpu": "small", "cpu-min": "small", "mlx": "large-v3", "mlx-turbo": "large-v3-turbo" };
function normalizeQuality(q) {
  if (!q) return "auto";
  for (var i = 0; i < QUALITY_OPTS.length; i++) { if (QUALITY_OPTS[i][0] === q) return q; }
  return LEGACY_QUALITY[q] || "auto";
}
// The meeting / import Quality picker, honest about what is downloaded. Each tile's readiness is
// read from the LANGUAGE-IMPLIED family (Afrikaans -> Fluister, SA languages -> Swivuriso, else
// Whisper), so an Afrikaans meeting greys a size whose Fluister build is absent. "Auto" is driven
// by a cached pre-flight, not assumed ready. Selecting a size never downloads: Begin's pre-start
// modal handles consent, then the model downloads in the background with visible progress.
function qualityTileState(family, key) {
  if (key === "auto") {
    if (_autoPf) return { ready: !!_autoPf.present, bytes: _autoPf.approx_bytes || 0, known: true };
    return { ready: true, bytes: 0, known: false };   // pre-flight pending: stay neutral, do not grey
  }
  return { ready: sizePresentInFamily(family, key), bytes: sizeApproxBytes(family, key), known: true };
}
// "Downloads first time" hint, with the approx size when we know it (fall back to a sizeless
// phrase rather than a bogus "~0.00 GB" when the catalogue has no byte estimate for that size).
function downloadHint(bytes) {
  return bytes > 0 ? trFmt("Downloads first time (~{size})", { size: fmtGB(bytes) }) : tr("Downloads first time");
}
function qualitySelector() {
  var family = familyForForm();
  refreshAutoPreflight();   // guarded: only hits the endpoint when language/engine/device change
  var seg = el("div", { class: "segmented block" }, QUALITY_OPTS.map(function (o) {
    var key = o[0], label = o[1];
    var st = qualityTileState(family, key);
    return el("button", {
      class: S.form.tier === key ? "on" : "",
      style: { opacity: st.ready ? "1" : "0.5" },
      title: !st.known ? null : (st.ready ? tr("Starts instantly") : downloadHint(st.bytes)),
      onclick: function () { S.form.tier = key; render(); },
    }, el("span", { text: label }));
  }));
  // Honest one-line hint for the CURRENT choice: a "Starts instantly" pill, or the download size.
  var cst = qualityTileState(family, normalizeQuality(S.form.tier));
  var hint = !cst.known ? null
    : (cst.ready
        ? el("span", { class: "chip ok" }, [icon("check", 12), el("span", { text: "Starts instantly" })])
        : el("span", { class: "chip prep" }, [icon("download", 12), el("span", {}, raw(downloadHint(cst.bytes)))]));
  return el("div", {}, [seg, hint ? el("div", { style: { marginTop: "8px" } }, hint) : null]);
}
// EN/AF interface-language toggle (used on the first-run welcome screen).
function langToggleSeg() {
  var cur = afLang(S.settings);
  return el("div", { class: "segmented", style: { width: "auto" } }, [["en-ZA", "English", "en"], ["af", "Afrikaans", "af"]].map(function (o) {
    return el("button", { class: cur === o[2] ? "on" : "", onclick: function () { saveSettings({ interface_language: o[0] }); } }, el("span", { text: o[1] }));
  }));
}
function segmented(options, value, onChange) {
  return el("div", { class: "segmented block" }, options.map(function (o) {
    return el("button", { class: value === o[0] ? "on" : "", onclick: function () { onChange(o[0]); } }, o[1]);
  }));
}
// "Run on" GPU/CPU choice for the pre-meeting + import screens. Returns null unless an
// NVIDIA GPU is present. Remembered as a setting (default GPU). The Model size choice above is
// honoured on either processor; Auto picks the best model for the chosen language.
function runOnField() {
  if (!(S.cuda && S.cuda.gpu_present)) return null;
  var v = (S.form.device === "cpu") ? "cpu" : "auto";
  var seg = segmented([["auto", "GPU"], ["cpu", "CPU"]], v, function (val) {
    S.form.device = val; saveSettings({ device: val }); render();
  });
  var note = (v === "cpu")
    ? el("p", { class: "ink-3", style: { fontSize: "11px", margin: "6px 0 0" }, text: "Running on the CPU. Auto starts high and steps down if your computer cannot keep up." })
    : el("p", { class: "ink-3", style: { fontSize: "11px", margin: "6px 0 0" }, text: "Running on the GPU. Your Model size choice above is used as-is; Auto picks the best model for your language." });
  return formField("Run on", null, el("div", {}, [seg, note]), true);
}
function selectEl(options, value, onChange) {
  var sel = el("select", { class: "field", style: { width: "auto", minWidth: "150px" }, onchange: function (e) { onChange(e.target.value); } },
    options.map(function (o) { return el("option", { value: o[0], text: o[1] }); }));
  sel.value = value;
  return sel;
}
function toggleEl(on, onClick, danger) {
  return el("button", { class: "toggle" + (danger ? " danger" : "") + (on ? " on" : ""), onclick: onClick }, el("i"));
}
function formField(label, suffix, control, noMargin) {
  return el("div", { class: "formfield", style: noMargin ? { margin: "0" } : null }, [
    el("label", {}, [el("span", { text: label }), suffix]),
    control,
  ]);
}
function deviceField(label, list, value, defaultIdx, onChange) {
  var control;
  if (!list || !list.length) {
    control = el("div", { class: "row gap-6", style: { color: "var(--warn)", fontSize: "12px", padding: "7px 0" } }, [icon("alert", 14), "not detected"]);
  } else {
    var sel = el("select", { class: "field", onchange: function (e) { onChange(e.target.value); } },
      list.map(function (d) { return el("option", { value: String(d.index) }, raw(d.name)); }));
    sel.value = value != null ? value : (defaultIdx != null ? String(defaultIdx) : String(list[0].index));
    control = sel;
  }
  return el("div", { class: "formfield", style: { margin: "0 0 12px" } }, [el("label", {}, label), control]);
}

/* ── boot ─────────────────────────────────────────────────── */
async function boot() {
  applyTheme();
  var results = await Promise.all([
    api.get("/api/settings").catch(function () { return null; }),
    api.get("/api/features").catch(function () { return null; }),
    api.get("/api/models").catch(function () { return null; }),
    api.get("/api/app-info").catch(function () { return null; }),
    api.get("/api/license").catch(function () { return null; }),
    api.get("/api/devices").catch(function () { return null; }),
    api.get("/api/summary-models").catch(function () { return null; }),
    api.get("/api/voice-models").catch(function () { return null; }),
    api.get("/api/cuda").catch(function () { return null; }),
  ]);
  S.settings = results[0]; S.features = results[1]; S.models = results[2];
  S.appInfo = results[3]; S.license = results[4]; S.devices = results[5];
  S.summaryModels = results[6];
  S.voiceModels = results[7];
  S.cuda = results[8];
  if (S.summaryModels && S.summaryModels.progress && S.summaryModels.progress.state === "downloading") pollModelDownload();
  if (S.voiceModels && S.voiceModels.progress && S.voiceModels.progress.state === "downloading") pollVoiceDownload();
  if (S.cuda && S.cuda.progress && S.cuda.progress.state === "downloading") pollCudaDownload();
  LANG = afLang(S.settings);
  if (S.settings) {
    S.form.language = S.settings.transcription_language != null ? S.settings.transcription_language : "af";
    // Keep the default within the languages the user transcribes, so the picker highlights it.
    // Only individually toggleable codes are clamped; "sa", world codes and "" pass through.
    var tl = S.settings.transcribe_languages || ["af", "en"];
    if (langIsToggleable(S.form.language) && tl.indexOf(S.form.language) < 0) S.form.language = tl[0] || "af";
    // A default under "More languages" pre-selects itself in the dropdown.
    if (langMode(S.form.language) === "more") S.form.moreLang = S.form.language;
    S.form.tier = normalizeQuality(S.settings.tier);
    S.form.device = S.settings.device || "auto";
    S.form.engine = S.settings.engine || "auto";
    S.form.aecLive = !!S.settings.aec_live;
    S.form.agcLive = S.settings.agc_live !== false;   // default ON (an old settings file has no key)
    S.form.aec = !!S.settings.aec;
  }
  if (S.devices) {
    if (S.devices.default_mic_index != null) S.form.mic = String(S.devices.default_mic_index);
    if (S.devices.default_loopback_index != null) S.form.loopback = String(S.devices.default_loopback_index);
  }
  refreshSessions();
  startReminderPoll();   // Business + calendar-reminders-on + Outlook are all checked inside the tick



  var resumed = false;
  try {
    var status = await api.get("/api/status");
    if (status.running) { adoptRunning(status); resumed = true; }
  } catch (e) {}

  if (!resumed) {
    // The licence agreement gates everything and cannot be skipped. It is separate from the
    // setup wizard: a returning user who already finished setup still sees it once (the flag
    // defaults false until they accept), which is the intended one-time consent.
    var licenceOk = false;
    try { licenceOk = !!localStorage.getItem("vm_licence_accepted"); } catch (e) {}
    if (S.settings && S.settings.licence_accepted) licenceOk = true;
    if (!licenceOk) {
      S.setup.stage = "licence";
      S.route = "setup";
    } else {
      var done = false;
      try { done = !!localStorage.getItem("vm_setup_done"); } catch (e) {}
      if (S.settings && S.settings.setup_complete) done = true;   // disk flag survives WebView storage resets
      S.route = done ? "home" : "setup";
    }
  }
  S.booted = true;
  render();
}
function adoptRunning(status) {
  S.live = freshLive();
  S.live.running = true;
  S.live.recording = !!status.recording;
  S.live.transcribing = !!status.transcribing;
  S.live.sourceKind = status.source_kind;
  S.live.startedAt = status.started_at || new Date().toISOString();
  S.live.outputPath = status.output_path;
  S.live.tier = status.tier; S.live.model = status.model; S.live.family = status.family; S.live.language = status.language;
  S.live.engine = status.engine || "auto";
  S.live.stopping = !!status.stopping;
  S.live.title = topicFromName(baseName(status.output_path));
  S.live.micDevice = status.mic_device != null ? status.mic_device : null;
  S.live.loopbackDevice = status.loopback_device != null ? status.loopback_device : null;
  S.live.aecAvailable = !!status.aec_live_available;
  S.live.aecActive = !!status.aec_live_active;
  S.live.sysState = status.sys_state || null;   // system-audio capture health at reload time
  // /api/status does not carry the recording stem; the server derives it from the transcript
  // path (output_path minus ".md"), so reconstruct it for the record-only finish flow.
  S.live.audioStem = (status.recording_started && status.output_path) ? status.output_path.replace(/\.md$/, "") : null;
  openStream(); startElapsed();
  seedFromTranscript();
  seedNotes();
  S.live.silenceNudge = status.silence_nudge || null;   // a nudge that fired before this reload
  S.live.struggleNudge = status.struggle_nudge || null; // same, for a downgrade that fired before this reload
  S.live.recordingStarted = !!status.recording_started; // latched: recording is or was active this session
  adoptMicGate(status, true);                           // silent: a valve hint from before the reload is history
  if (status.source_kind !== "file") { startLevels(); startSilencePoll(); }
  if (status.source_kind === "file") {
    S.route = "importing";
    // Mirror startImport: surface the sticky server notice (e.g. "stereo requested but the file
    // is mono") once, whether it is already on the adopted status, lands mid-run, or only
    // arrives on the final poll.
    var showNotice = function (st) {
      if (st && st.notice && S.live.noticeShown !== st.notice) { S.live.noticeShown = st.notice; toast(trNotice(st.notice)); }
    };
    showNotice(status);
    pollStatus(function (st) { return !st.running; },
      function (st) { showNotice(st); gotoFinish(S.live.outputPath, st && st.sink_error); },
      showNotice);
  } else if (status.transcribing) {
    S.route = "live";
  } else {
    S.route = "recordonly";
  }
  // A reload can land while a live or record-only session is already stopping. The in-page
  // stop's completion polling died with the old page, so restart it here and route exactly as
  // the normal stop does when the drain finishes (a file import already polls to completion
  // above, so it never installs this second poll).
  if (status.stopping && status.source_kind !== "file") {
    pollStatus(
      function (st) { return !st.running || (!st.stopping && !st.transcribing); },
      function (st) {
        S.live.stopping = false;
        if (st.running) {
          // Transcription-only stop finished; the recording carries on (doStop "transcription").
          S.live.transcribing = false; S.live.recording = true; go("recordonly"); return;
        }
        if (S.live.transcribing) {
          // A transcribing session ended: same handoff as doStop ("all").
          gotoFinish(S.live.outputPath, st && st.sink_error); return;
        }
        // Record-only ended: mirror stopRecordOnly's completion.
        var stem = S.live.audioStem;
        teardownLive();
        S.live.running = false;
        S.finish.recordingStem = stem;
        S.finish.outputPath = S.live.outputPath;
        S.finish.sinkError = (st && st.sink_error) || null;
        if (S.finish.sinkError) toast(S.finish.sinkError, true);
        go("recordonly");
      }
    );
  }
}

// After a full reload mid-session, S.live.segments only holds lines that arrived AFTER the
// reload (the SSE stream has no replay). The saved transcript has everything so far, in the
// exact "[mm:ss] [SRC] text" format MarkdownSink writes: fetch it and seed the earlier lines.
// Any failure just leaves the post-reload view; the stream carries on regardless.
function seedFromTranscript() {
  var name = baseName(S.live.outputPath || "");
  if (!/\.md$/.test(name)) return;
  api.text("/sessions/" + encodeURIComponent(name)).then(function (txt) {
    var re = /^\[(\d+):(\d{2})\]\s+\[([A-Za-z]+(?: [A-Za-z]+)?)\]\s+(.*)$/;   // "MIC" or "Speaker L"
    var out = [];
    var lines = txt.split(/\r?\n/);
    for (var i = 0; i < lines.length; i++) {
      var m = re.exec(lines[i]);
      if (m) out.push({ t_start: parseInt(m[1], 10) * 60 + parseInt(m[2], 10), source: m[3], text: m[4] });
    }
    if (!out.length) return;
    // Dedupe against segments that already arrived over SSE while the file was in flight.
    var seen = {};
    function key(s) { return fmtTs(s.t_start) + "|" + (s.source || "") + "|" + (s.text || ""); }
    for (var j = 0; j < S.live.segments.length; j++) seen[key(S.live.segments[j])] = true;
    var earlier = out.filter(function (s) { return !seen[key(s)]; });
    if (!earlier.length) return;
    S.live.segments = earlier.concat(S.live.segments);
    if (S.route === "live" || S.route === "importing") render();
  }).catch(function () {});
}
// Same idea for the user's own notes: repopulate the live panel from the saved sidecar so
// typing after a reload extends the notes instead of starting from a blank textarea.
function seedNotes() {
  var stem = liveStem();
  if (!stem) return;
  api.get("/api/notes?stem=" + encodeURIComponent(stem)).then(function (n) {
    // Apply the disk text only if the user has not typed (or deleted) anything since the
    // adoption AND we are still on the same session: a late response must never resurrect
    // old notes over an edit, or seed another session's panel.
    if (n && n.text && !S.live.notes && !S.live.notesTouched && stem === liveStem()) {
      S.live.notes = n.text;
      if (S.route === "live") render();
    }
  }).catch(function () {});
}

// Global keyboard shortcuts: Escape closes the stop popover; Cmd/Ctrl+Enter
// begins a meeting from the pre-meeting screen; Cmd/Ctrl+K focuses History search.
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape" && S.stopMenuOpen) { S.stopMenuOpen = false; render(); return; }
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && S.route === "pre") { e.preventDefault(); startLive(); return; }
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && S.route === "importpre") { e.preventDefault(); startImport({ path: S.importPath, stem: S.importStem, topic: S.form.title }); return; }
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && S.route === "recordpre") { e.preventDefault(); startRecordOnly(); return; }
  if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey) && S.route === "history") {
    e.preventDefault();
    var s = document.querySelector('.screen input[placeholder^="Search"]');
    if (s) s.focus();
  }
});

boot();
