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

// Where "Report a bug or request a feature" sends. Privacy-first mailto: it
// only carries the app version and OS, never logs or transcripts.
var FEEDBACK_EMAIL = "volksmond@digiphyte.com";
// Where "Buy Pro" sends the user. The purchase/pricing page on the site (not sold during
// early access; this is the forward-looking link). Change when the page is live.
var PRO_URL = "https://volksmond.digiphyte.com/pro";

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
  };
}
var S = {
  route: "home", booted: false,
  settings: null, features: null, models: null, appInfo: null, license: null, devices: null,
  sessions: [], sessionsFolder: "", sessionsActive: null, sessionsSummarising: [],
  live: freshLive(),
  starting: { active: false, kind: null, title: "", error: null, startedAt: null },
  form: { title: "", language: "af", tier: "auto", device: "auto", engine: "auto", participants: [], terms: [], record: false, aec: false, mic: null, loopback: null, advancedOpen: false },
  setup: { stage: "welcome", choice: "transcribe" },
  finish: { outputPath: null, title: "", summary: null, savedAs: null, summarising: false, recordingStem: null, sinkError: null },
  reader: { name: "", title: "", text: "", summarising: false, summary: null },
  upgrade: { keyState: "empty", value: "", msg: "" },
  settingsDraft: null,
  theme: (function () { try { return localStorage.getItem("vm_theme") || "system"; } catch (e) { return "system"; } })(),
  stopMenuOpen: false,
  toast: null,
  warm: null,
};

// transient refs + timers (not part of render state)
var liveDocEl = null, liveBodyEl = null, elapsedEl = null, recTimerEl = null;
var pollTimer = null, elapsedTimer = null, toastTimer = null, levelTimer = null, warmTimer = null, histTimer = null;
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
  var cls = src === "MIC" ? "mic" : (src === "SYS" ? "sys" : "file");
  return el("div", { class: "row" }, [
    el("div", { class: "t", text: fmtTs(seg.t_start) }),
    el("div", {}, [el("span", { class: "src " + cls, text: "[" + src + "]" }), raw(seg.text || "")]),
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
  ]);
}
// Compact strip on the live screen to change the LANGUAGE and MODEL mid-meeting. Language alone
// keeps the loaded model; Engine/Quality reload it. Quality shows only on CPU, where the size is
// the user's to pick (on the GPU the best model always runs).
function liveTuneStrip() {
  if (!S.live.transcribing || S.live.stopping) return null;
  var isGpu = (S.live.tier === "gpu" || S.live.tier === "gpu-4gb");
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
  if (!isGpu) items.push(field("Quality", tuneSelect(QUALITY_OPTS, normalizeQuality(S.live.tier), function (v) { reconfigureLive({ tier: v }, "Model switched."); })));
  return el("div", { class: "row gap-16", style: { flexWrap: "wrap", padding: "8px 16px", borderBottom: "1px solid var(--line)", background: "var(--surface-2)", alignItems: "center" } }, items);
}

/* ── model warm-up (kill the first-use stall) ─────────────── */
// Loading the model the first time after launch can stall for minutes (network revalidation
// of an already-downloaded model, plus CUDA/AV cold start). We pre-load it in the background
// the moment the user reaches a pre-meeting screen, so Begin reuses a warm model.
function warmUp() {
  api.post("/api/warm-up", { tier: S.form.tier || "auto", device: S.form.device || "auto", language: S.form.language || "", engine: S.form.engine || "auto" })
    .then(function (st) {
      S.warm = st;
      if (st && st.state === "warming") pollWarm();
      if (S.route === "pre" || S.route === "importpre") render();
    }).catch(function () {});
}
function pollWarm() {
  if (warmTimer) return;
  warmTimer = setInterval(function () {
    api.get("/api/warm-up").then(function (st) {
      S.warm = st;
      if (!st || st.state !== "warming") { clearInterval(warmTimer); warmTimer = null; }
      if (S.route === "pre" || S.route === "importpre") render();
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
async function startLive() {
  var body = {
    topic: S.form.title || "",
    tier: S.form.tier, device: S.form.device, language: S.form.language, engine: S.form.engine,
    prompt: S.form.participants.concat(S.form.terms).join(", "),
    record: !!S.form.record, transcribe: true,
    mic_device: S.form.mic, loopback_device: S.form.loopback,
    aec_live: !!S.form.aecLive,
  };
  beginStarting("live", S.form.title || "Live meeting");
  try {
    var resp = await api.post("/api/start", body);
    endStarting();
    S.live = freshLive();
    S.live.running = true; S.live.transcribing = true; S.live.recording = !!resp.recording;
    S.live.sourceKind = "live"; S.live.startedAt = new Date().toISOString();
    S.live.outputPath = resp.output_path; S.live.audioStem = resp.audio_stem;
    S.live.tier = resp.tier; S.live.model = resp.model; S.live.family = resp.family; S.live.language = resp.language;
    S.live.engine = S.form.engine;
    S.live.title = S.form.title || "Live meeting";
    S.live.micDevice = S.form.mic; S.live.loopbackDevice = S.form.loopback;
    go("live"); openStream(); startElapsed(); startLevels();
  } catch (e) {
    // Surface the failure on the Starting screen (with Back), not just a toast that
    // vanishes; the model-load error ("Could not load model ...") needs to be readable.
    // Stop the elapsed interval first so it does not leak while the error is shown.
    if (startingTimer) { clearInterval(startingTimer); startingTimer = null; }
    if (S.route === "starting") { S.starting.error = e.message || "Could not start."; render(); }
    else { toast(e.message || "Could not start.", true); }
  }
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
    go("recordonly"); startElapsed(); startLevels();
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
  var body = { topic: arg.topic || "", tier: S.form.tier, device: S.form.device, language: S.form.language, engine: S.form.engine, aec: !!S.form.aec, prompt: (S.form.participants || []).concat(S.form.terms || []).join(", ") };
  if (arg.path) body.paths = [arg.path];
  if (arg.stem) body.stem = arg.stem;
  beginStarting("file", arg.topic || S.importName || "Recording");
  try {
    var resp = await api.post("/api/transcribe-file", body);
    endStarting();
    S.live = freshLive();
    S.live.running = true; S.live.transcribing = true; S.live.sourceKind = "file";
    S.live.startedAt = new Date().toISOString();
    S.live.outputPath = resp.output_path; S.live.tier = resp.tier; S.live.model = resp.model; S.live.family = resp.family;
    S.live.importName = baseName(arg.path) || (arg.topic || "recording");
    S.live.title = arg.topic || topicFromName(baseName(resp.output_path));
    go("importing"); openStream();
    pollStatus(function (st) { return !st.running; }, function () { gotoFinish(S.live.outputPath); });
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
  confirmModal({
    title: "Remove this model?",
    message: "Remove this transcription model from your computer? You can download it again later.",
    detail: meta.title + "   " + fmtGB(m.size_on_disk || m.approx_bytes),
    confirmLabel: "Remove", danger: true,
    onConfirm: function () {
      api.post("/api/voice-model/delete", { model: m.model })
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
  S.form.title = ""; S.form.participants = []; S.form.terms = [];
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
  teardownLive();
  S.finish.outputPath = outputPath || S.live.outputPath;
  S.finish.title = S.live.title || topicFromName(baseName(S.finish.outputPath));
  S.finish.recordingStem = S.live.recording ? S.live.audioStem : null;
  S.finish.summary = null; S.finish.savedAs = null; S.finish.summarising = false;
  S.finish.sinkError = sinkError || null;
  S.live.running = false;
  refreshSessions();
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
function openExternal(url) {
  // Native shell: hand it to the OS (system browser / mail client).
  if (inDesktop() && window.pywebview.api.open_external) { window.pywebview.api.open_external(url); return; }
  // Browser: mailto via location (page stays); web links in a new tab (never navigate
  // the app away).
  if (url.slice(0, 7) === "mailto:") { window.location.href = url; }
  else { window.open(url, "_blank", "noopener"); }
}
function reportBug() {
  // No phone-home: the app never sends anything. It either hands a prefilled draft to
  // the user's default mail app (mailto), or copies a report to the clipboard for them
  // to paste into webmail. The send always happens outside the app.
  var info = S.appInfo || {};
  var version = info.version || "?", plat = info.platform || "?";
  var af = LANG === "af";
  var subject = "Volksmond feedback (v" + version + ")";
  var body =
    (af ? "Beskryf die fout of die funksie wat jy graag wil hê:" : "Describe the bug or the feature you would like:") +
    "\n\n\n----------------------------------------\n" +
    "Volksmond version " + version + "\n" + plat + "\n" +
    (af ? "(Geen logs of transkripsies is aangeheg nie. Voeg self enigiets nuttig by.)"
        : "(No logs or transcripts are attached. Add anything helpful yourself.)") + "\n";
  var mailto = "mailto:" + FEEDBACK_EMAIL + "?subject=" + encodeURIComponent(subject) + "&body=" + encodeURIComponent(body);
  var reportText = "To: " + FEEDBACK_EMAIL + "\nSubject: " + subject + "\n\n" + body;

  var modal = el("div", { class: "modal-backdrop", onclick: function (e) { if (e.target === modal) modal.remove(); } }, [
    el("div", { class: "modal" }, [
      el("h2", { text: "Report a bug or idea" }),
      el("p", { class: "ink-3", style: { margin: "8px 0 14px", fontSize: "13px" }, text: "Nothing is sent automatically. The app never phones home, you send this yourself." }),
      el("div", { style: { display: "flex", alignItems: "center", gap: "10px", padding: "10px 12px", background: "var(--surface-2)", borderRadius: "8px", marginBottom: "16px" } }, [
        el("span", { style: { color: "var(--ink-3)", display: "inline-flex" } }, icon("bug", 16)),
        el("div", {}, [
          el("div", { style: { fontSize: "12px", color: "var(--ink-3)" }, text: "Send it to" }),
          el("div", { class: "mono", style: { fontSize: "13.5px" }, text: FEEDBACK_EMAIL }),
        ]),
      ]),
      el("div", { class: "row gap-8", style: { justifyContent: "flex-end", flexWrap: "wrap" } }, [
        el("button", { class: "btn ghost", onclick: function () { modal.remove(); } }, "Close"),
        el("button", { class: "btn ghost", onclick: function () { copyText(reportText); } }, [icon("copy", 14), "Copy report"]),
        el("button", { class: "btn primary", onclick: function () { openExternal(mailto); modal.remove(); } }, [icon("note", 14), "Open email"]),
      ]),
    ]),
  ]);
  APP.appendChild(modal);
}

/* ═══════════════════════════════════════════════════════════
 * RENDER
 * ═══════════════════════════════════════════════════════════ */
var _renderedRoute = null;
function render() {
  // Preserve scroll across a same-route re-render (e.g. the 1s download poll on
  // Settings) so the page does not snap to the top. A route change still resets.
  var keepScroll = (S.route === _renderedRoute);
  var prevScroller = keepScroll ? APP.querySelector(".screen, .solo, .live-body") : null;
  var prevScrollTop = prevScroller ? prevScroller.scrollTop : 0;
  liveDocEl = liveBodyEl = elapsedEl = recTimerEl = null;
  clear(APP);
  var view;
  switch (S.route) {
    case "setup": view = setupView(); break;
    case "starting": view = startingView(); break;
    case "home": view = shell("home", homeView()); break;
    case "pre": view = shell("home", preView()); break;
    case "importpre": view = shell("home", importPreView()); break;
    case "recordpre": view = shell("home", recordPreView()); break;
    case "live": view = liveView(); break;
    case "recordonly": view = recordOnlyView(); break;
    case "importing": view = importingView(); break;
    case "finish": view = shell("home", finishView()); break;
    case "history": view = shell("history", historyView()); break;
    case "reader": view = shell("history", readerView()); break;
    case "settings": view = shell("settings", settingsView()); break;
    case "upgrade": view = shell("settings", upgradeView()); break;
    default: view = shell("home", homeView());
  }
  APP.appendChild(view);
  if (S.stopMenuOpen) APP.appendChild(stopMenuLayer());
  if (S.toast) {
    APP.appendChild(el("div", { class: "toast-wrap" }, el("div", { class: "toast" + (S.toast.err ? " err" : ""), text: S.toast.msg })));
  }
  _renderedRoute = S.route;
  if (prevScrollTop) {
    var ns = APP.querySelector(".screen, .solo, .live-body");
    if (ns) ns.scrollTop = prevScrollTop;
  }
}

/* ── shell (sidebar + main) ───────────────────────────────── */
function shell(active, mainNode) {
  return el("div", { class: "shell" }, [sidebar(active), el("div", { class: "main" }, mainNode)]);
}
function sidebar(active) {
  function nav(id, label, ic, route) {
    return el("button", { class: "nav-item" + (active === id ? " active" : ""), onclick: function () { go(route); } },
      [icon(ic, 17), el("span", { text: label })]);
  }
  return el("aside", { class: "sidebar" }, [
    el("div", { class: "brand" }, [
      el("div", { class: "wordmark" }, [markSvg(20), el("span", { text: "Volksmond" })]),
      el("div", { class: "brand-sub", text: "by DigiPhyte" }),
    ]),
    el("nav", { class: "nav" }, [
      nav("home", "Meeting", "mic", "home"),
      nav("history", "History", "clock", "history"),
      nav("settings", "Settings", "gear", "settings"),
    ]),
    el("div", { class: "spacer" }),
    el("div", { class: "local-pill" }, [icon("lock", 14), el("span", { text: "Local only, no internet" })]),
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
function setupView() {
  var stage = S.setup.stage;
  var inner;
  if (stage === "welcome") {
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
      if (cur && cur !== "" && sel.indexOf(cur) < 0) patch.transcription_language = sel[0];
      if (S.form && S.form.language && S.form.language !== "" && sel.indexOf(S.form.language) < 0) S.form.language = sel[0];
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
    // The default location is per-user app data on this computer. Many users want
    // their transcripts in Documents or a synced folder instead, so we ask before
    // they start a session rather than hiding it in Settings. Picking nothing is
    // fine -- "Continue" just keeps the default.
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
    el("div", { class: "screen-head" }, el("div", {}, [
      el("div", { class: "eyebrow", text: "Ready when you are" }),
      el("h1", { text: "Start a session" }),
      el("p", { class: "sub", text: "Three ways in. Pick the one that fits the moment." }),
    ])),
    el("div", { class: "entry-grid" }, [
      entry({ primary: true, ic: "mic", title: "Start a live meeting", cta: "Begin",
        body: "Transcribe what you and others are saying right now, on this computer. Optionally record the audio too.",
        onclick: function () { go("pre"); } }),
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
      el("button", { class: "btn primary big", onclick: startLive }, [icon("dot", 15), "Begin"]),
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
  // Show the saved default context so it's visible on the setup screen, not hidden
  // behind "applied automatically". It's edited in Settings, read-only here.
  var dc = ((S.settings && S.settings.default_context) || "").trim();
  if (!dc) {
    return el("p", { class: "hint", style: { marginTop: "-4px", marginBottom: "16px" }, text: "Tip: save company names and jargon in Settings and they apply to every transcription automatically." });
  }
  return el("div", { class: "card", style: { padding: "10px 12px", marginBottom: "16px" } }, [
    el("div", { class: "section-label", style: { marginBottom: "4px" }, text: "Always applied (from Settings)" }),
    el("div", { class: "ink-2", style: { fontSize: "12px" } }, raw(dc)),
  ]);
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
    inner = el("div", { class: "rec-stage" }, [
      el("span", { class: "spinner" }),
      el("h1", { style: { fontSize: "22px", marginTop: "8px" }, text: "Starting" }),
      startingElapsedEl,
      el("p", { class: "ink-2", style: { maxWidth: "470px", textAlign: "center" }, text: "Loading the transcription model on your computer. The first time you use a quality level can take a moment, and if that model still needs downloading it can take a few minutes." }),
      el("p", { class: "ink-3", style: { fontSize: "12px" }, text: "You can keep this open. It switches to the transcript by itself." }),
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
  if (tier === "gpu" || tier === "gpu-4gb") {
    return el("span", { class: "chip ok", title: (S.cuda && S.cuda.gpu_name) || "GPU" }, [icon("check", 12), "GPU"]);
  }
  return el("span", { class: "chip" }, "CPU");
}

function liveView() {
  var statusChip;
  if (S.live.stopping) statusChip = el("span", { class: "chip warn" }, [el("span", { class: "dot" }), el("span", { id: "live-status-text", text: "Finishing" })]);
  else if (S.live.transcribing) statusChip = el("span", { class: "chip live" }, [el("span", { class: "dot" }), "Listening"]);
  else statusChip = el("span", { class: "chip ok" }, [el("span", { class: "dot" }), "Saved"]);

  elapsedEl = el("span", { class: "mono", text: fmtElapsed(S.live.startedAt) });
  var langLabel = S.live.language === "auto" || !S.live.language ? "Auto-detect" : (S.live.language === "af" ? "Afrikaans" : (S.live.language === "en" ? "English" : S.live.language));

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

  liveDocEl = el("div", { class: "doc" }, S.live.segments.length
    ? S.live.segments.map(segRow)
    : el("div", { class: "empty", text: "Listening. The transcript appears here as people talk." }));
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

  var footer = el("div", { class: "live-footer" }, [
    stopBtn,
    S.live.recording ? el("span", { class: "rec-ind" }, [el("i"), "Recording audio"]) : null,
    el("span", { class: "grow" }),
    S.live.outputPath ? el("span", { class: "saving" }, ["Saving to ", el("span", { class: "mono", text: baseName(S.live.outputPath) })]) : null,
  ]);

  return el("div", { class: "live" }, [header, liveAudioStrip(), liveTuneStrip(), liveBodyEl, footer]);
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
      el("div", { class: "right" }, el("span", { class: "chip live" }, [el("span", { class: "dot" }), "Recording"])),
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
  // stopped: handoff
  var stem = S.finish.recordingStem;
  return el("div", { class: "main" }, el("div", { class: "screen center" }, el("div", { class: "screen-inner col-narrow stack", style: { gap: "18px" } }, [
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
  ])));
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
    if (active.recording) chips.push(el("span", { class: "chip live" }, [el("span", { class: "dot" }), el("span", { text: tr("Recording") })]));
    if (active.transcribing) chips.push(busyChip(tr("Transcribing")));
  } else {
    if (f.recorded) chips.push(statChip(tr("Recorded"), "mic", "muted"));
    if (f.transcribed) chips.push(statChip(tr("Transcript"), "note", "ok"));
    if (summarising) chips.push(busyChip(tr("Summarising")));
    else if (f.has_summary) chips.push(statChip(tr("Summary"), "sparkle", "accent"));
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
    onConfirm: function () { startImport({ stem: stem, topic: topic }); },
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
async function openReader(name) {
  var row = (S.sessions || []).filter(function (s) { return s.name === name; })[0] || {};
  S.reader = { name: name, stem: row.stem || name.replace(/\.md$/, ""), recorded: !!row.recorded,
    title: topicFromName(name), text: "Loading...", tab: "transcript", summarising: false, summary: null, savedAs: null };
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
  var hasSummary = !!S.reader.summary;
  var tab = hasSummary ? (S.reader.tab || "transcript") : "transcript";
  // A connected segmented control, not two loose ghost buttons. Viewing the
  // transcript, a lone "Summary" ghost button is indistinguishable from the Copy
  // and Folder actions next to it, so the way back to the summary reads as just
  // another toolbar action. The segmented switch makes Transcript/Summary an
  // obvious two-way toggle, distinct from the actions.
  var toggle = hasSummary ? el("div", { class: "segmented", style: { width: "auto", flex: "0 0 auto" } }, [
    el("button", { class: tab === "transcript" ? "on" : "", onclick: function () { S.reader.tab = "transcript"; render(); } }, [icon("note", 13), el("span", { text: "Transcript" })]),
    el("button", { class: tab === "summary" ? "on" : "", onclick: function () { S.reader.tab = "summary"; render(); } }, [icon("sparkle", 13), el("span", { text: "Summary" })]),
  ]) : null;
  var body;
  if (hasSummary && tab === "summary") {
    body = summaryResult(S.reader.summary, S.reader.savedAs, S.reader.name, "reader");
  } else {
    body = el("div", { class: "stack", style: { gap: "16px" } }, [
      hasSummary ? null : summariseCard(S.reader.name, "reader"),
      el("div", { class: "card", style: { padding: "20px 22px" } },
        el("div", { class: "doc", style: { maxWidth: "none", fontSize: "15px", whiteSpace: "pre-wrap", fontFamily: "var(--font-transcript)" } }, raw(S.reader.text))),
    ]);
  }
  return el("div", { class: "screen" }, el("div", { class: "screen-inner col-mid stack", style: { gap: "16px" } }, [
    el("button", { class: "btn ghost sm", style: { alignSelf: "flex-start" }, onclick: function () { go("history"); } }, [icon("back", 14), "Back to history"]),
    el("div", { class: "row gap-12" }, [
      el("h2", {}, raw(S.reader.title)),
      el("span", { class: "grow" }),
      toggle,
      el("button", { class: "btn ghost sm", onclick: function () { copyText((hasSummary && tab === "summary") ? S.reader.summary : S.reader.text); } }, [icon("copy", 13), "Copy"]),
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
    (S.cuda && S.cuda.gpu_present) ? cudaCard() : null,
    summariesCard(),
    dataCard(st),
    connected() ? dangerCard(st) : null,
    aboutCard(),
  ]));
}
var updateState = { state: "idle", info: null };
// Manual, user-initiated update check. Posts to the localhost server, which makes ONE outbound
// GET to the public GitHub releases API. Never automatic; nothing leaves the machine until the
// user clicks Check for updates.
function checkUpdates() {
  updateState = { state: "checking", info: null }; render();
  api.post("/api/check-updates").then(function (d) {
    updateState = { state: "done", info: d }; render();
  }).catch(function () {
    updateState = { state: "error", info: null }; render();
  });
}
function aboutCard() {
  var version = (S.appInfo && S.appInfo.version) || "?";
  var u = updateState;
  var updateLine =
    u.state === "checking" ? el("div", { class: "s", style: { marginTop: "4px", display: "flex", gap: "6px", alignItems: "center" } }, [el("span", { class: "spinner" }), el("span", { text: "Checking for updates" })]) :
    u.state === "error" ? el("div", { class: "s", style: { marginTop: "4px", color: "var(--warn)" }, text: "Could not check for updates." }) :
    (u.state === "done" && u.info && u.info.update_available) ? el("div", { class: "s", style: { marginTop: "4px" } }, [el("span", { text: "Update available" }), raw(": v" + u.info.latest + "  "), el("span", { class: "link", onclick: function () { openExternal(u.info.url); } }, "Download")]) :
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
        el("button", { class: "btn ghost", disabled: u.state === "checking", onclick: function () { checkUpdates(); } }, "Check for updates"),
        el("button", { class: "btn ghost", onclick: function () { openExternal("https://digiphyte.com"); } }, "digiphyte.com"),
      ]),
    ]),
  ]);
}
function licenceCard() {
  var pro = isPro();
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "set-row" }, [
      el("div", { class: "tone-tile accent", style: { width: "36px", height: "36px", flex: "0 0 auto" } }, icon("crown", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t" }, [el("span", { text: pro ? "Pro, activated" : "Free" }), pro ? el("span", { class: "chip accent", text: "Perpetual" }) : null]),
        el("div", { class: "s", text: pro
          ? "Calendar attendee seeding and the optional online fallbacks are unlocked. Verified on this computer, never on a server."
          : "Unlimited local transcription and summaries, forever. Pro adds calendar attendees and optional online fallbacks for weak machines." }),
      ]),
      pro
        ? el("button", { class: "btn ghost", onclick: deactivateLicence }, "Deactivate")
        : el("span", { class: "chip", text: "Coming soon" }),
    ]),
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
function transcriptionCard(st) {
  var draft = S.settingsDraft || {};
  var ctxVal = draft.default_context != null ? draft.default_context : (st.default_context || "");
  var sel = (st.transcribe_languages || ["af", "en"]).slice();
  function toggleLang(code) {
    var i = sel.indexOf(code);
    if (i >= 0) { if (sel.length <= 1) return; sel.splice(i, 1); }   // keep at least one language
    else sel.push(code);
    var patch = { transcribe_languages: sel };
    // Keep the default language valid if we just removed it.
    if (st.transcription_language !== "" && sel.indexOf(st.transcription_language) < 0) patch.transcription_language = sel[0];
    saveSettings(patch);
  }
  var afOn = sel.indexOf("af") >= 0;
  var defOpts = sel.map(function (c) { return [c, langName(c)]; });
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
      el("div", { class: "s", style: { marginTop: "10px" }, text: "Afrikaans uses Fluister, our Afrikaans-tuned model; English and other languages use standard Whisper." }),
      (afOn && !fluisterReady()) ? el("div", { class: "s", style: { marginTop: "4px" }, text: "The Afrikaans-tuned Fluister model is not installed on this computer yet, so Afrikaans runs on standard Whisper for now." }) : null,
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("globe", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Default language" }), el("div", { class: "s", text: "Used unless you change it for a meeting." })]),
      el("div", { class: "ctl" }, selectEl(defOpts, st.transcription_language || "af", function (v) { saveSettings({ transcription_language: v }); })),
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
  "base":   { title: "Lite",         note: "Fastest and smallest, roughest accuracy. For very modest computers." },
  "small":  { title: "Light",        note: "Light and quick. Good on older or low-power machines." },
  "medium": { title: "Balanced",     note: "A solid balance of speed and accuracy on a typical computer." },
  "large-v3-turbo": { title: "High quality", note: "Near-best accuracy, lighter and faster than the largest model." },
  "large-v3": { title: "Best",       note: "Most accurate. Best on a computer with a graphics card (GPU)." },
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
      if (p.state === "done") { toast("Transcription model ready."); }
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
    var isThis = downloading && p.model === m.model;
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
function voiceModelCard() {
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "Transcription model, on this machine" }),
    el("div", { class: "set-row", style: { display: "block" } }, [
      el("div", { class: "t", style: { marginBottom: "4px" }, text: "Download or switch model" }),
      el("div", { class: "s", style: { marginBottom: "10px" }, text: "Volksmond transcribes on this computer. Download the model that suits your machine; the recommended one is marked. Bigger is more accurate, but slower and larger to download. Remove any you no longer need to free space." }),
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
// The CUDA download card. Returns null unless an NVIDIA GPU is present (NVIDIA only).
function cudaPanel(manage) {
  var c = S.cuda;
  if (!c || !c.gpu_present) return null;
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
        el("div", { class: "t", style: { marginBottom: "4px" } }, [el("span", { text: "Online API key for a future fallback" }), el("span", { class: "pro-badge", text: "Pro" })]),
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

  function freeCard() {
    return el("div", { class: "card", style: { padding: "20px" } }, [
      el("div", { class: "row", style: { justifyContent: "space-between", marginBottom: "8px" } }, [el("div", { style: { fontWeight: "600" }, text: "Free" }), el("span", { class: "mono", text: "R 0" })]),
      bulletList(["Unlimited local live transcription", "Local summaries, on this machine", "Afrikaans, English, and the mix", "Save and export, fully offline"]),
    ]);
  }
  function proCard() {
    return el("div", { class: "card disclosure accent", style: { padding: "20px" } }, [
      el("div", { class: "row", style: { justifyContent: "space-between", marginBottom: "8px" } }, [
        el("div", { class: "row gap-8" }, [el("span", { style: { fontWeight: "600" }, text: "Pro" }), el("span", { class: "chip accent" }, [icon("crown", 11), "Pro"])]),
        el("span", { class: "mono", text: "R 599, one-time" }),
      ]),
      bulletList(["Pull attendee names from your calendar", "Optional online transcription for weak machines", "Optional online summary for harder transcripts"]),
      el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "10px" }, text: "Pro covers only what needs an online connection. Everything that runs on this computer stays free. Perpetual: you own this version forever." }),
    ]);
  }
  return el("div", { class: "screen center" }, el("div", { class: "screen-inner col-mid stack", style: { gap: "16px" } }, [
    el("button", { class: "btn ghost sm", style: { alignSelf: "flex-start" }, onclick: function () { go("settings"); } }, [icon("back", 14), "Back to settings"]),
    el("div", {}, [el("div", { class: "eyebrow", text: "Upgrade" }), el("h1", { style: { marginTop: "6px" }, text: "Pro adds the polish, not the privacy." })]),
    el("p", { class: "ink-2", text: "Free is the real thing: unlimited live transcription and local summaries, on this machine, forever. Pro adds the few features that actually need to reach the internet." }),
    el("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" } }, [freeCard(), proCard()]),
    el("div", { class: "card", style: { padding: "20px" } }, [
      el("button", { class: "btn primary tall", onclick: function () { openExternal(PRO_URL); } }, "Buy Pro for R 599"),
      el("p", { class: "ink-3", style: { fontSize: "11.5px", marginTop: "8px" }, text: "Opens the Volksmond website in your browser. You get a licence key by email after purchase, and activation is fully offline." }),
      el("div", { class: "divider-label", text: "Already bought" }),
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
];
var LANG_NAMES = { "af": "Afrikaans", "en": "English", "": "Auto-detect" };
function langName(code) { return LANG_NAMES[code] != null ? LANG_NAMES[code] : code; }
function familyForLang(lang) { var l = (lang || "").toLowerCase(); return (l === "" || l === "auto" || /^af/.test(l)) ? "fluister" : "whisper"; }
// True once a Fluister model is actually installed; until then an Afrikaans session honestly
// runs (and is labelled) as stock Whisper.
function fluisterReady() { return !!(S.voiceModels && S.voiceModels.fluister_available); }
function familyLabelFor(lang) { return (familyForLang(lang) === "fluister" && fluisterReady()) ? "Fluister" : "Whisper"; }
// Friendly size label from the loaded model id/path (a stock name like "large-v3", a hosted
// Fluister repo like "digiphyte/fluister-medium", or a local ct2 dir). Mirrors the Quality
// vocabulary so the live chip can read "Fluister, Best".
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
// The lean engine chip on the live / importing header: the family plus which size is running,
// so the user can see it is e.g. Fluister at Balanced without opening anything.
function familyChip(family, model) {
  var size = sizeLabelFromModel(model);
  var name = (family === "fluister") ? "Fluister" : "Whisper";
  var label = size ? (name + ", " + tr(size)) : name;
  if (family === "fluister") return el("span", { class: "chip accent", title: tr("Afrikaans-optimised model") }, [icon("sparkle", 12), el("span", {}, raw(label))]);
  return el("span", { class: "chip" }, [el("span", {}, raw(label))]);
}
// The languages the user transcribes (Settings), plus Auto-detect, as picker options.
function transcribeLangOpts() {
  var ls = (S.settings && S.settings.transcribe_languages) || ["af", "en"];
  var opts = ls.map(function (c) { return [c, langName(c)]; });
  opts.push(["", "Auto-detect"]);
  return opts;
}
// Language is the hero control on the pre-meeting screens. Switching it re-warms the matching
// family so Begin stays instant.
function languageField() {
  return formField("Language", null, segmented(transcribeLangOpts(), S.form.language, function (v) {
    S.form.language = v; warmUp(); render();
  }), true);
}
// One honest line: which engine this session will use (the language decides, unless the Advanced
// Engine override forces a family), and that the size is automatic.
function engineLine() {
  var lang = S.form.language;
  var ov = S.form.engine || "auto";
  var famWanted = (ov === "fluister") ? "fluister" : (ov === "whisper") ? "whisper" : familyForLang(lang);
  var label = (famWanted === "fluister" && fluisterReady()) ? "Fluister" : "Whisper";
  var msg;
  if (famWanted === "fluister" && !fluisterReady())
    msg = "Fluister (our Afrikaans-tuned model) is not installed on this computer yet, so this runs on standard Whisper for now.";
  else if (ov === "fluister")
    msg = "Forced to Fluister for every language. Handy when an English meeting has Afrikaans words mixed in.";
  else if (ov === "whisper")
    msg = "Forced to standard Whisper for every language.";
  else if (famWanted === "fluister")
    msg = (lang === "")
      ? "Auto-detect uses Fluister, our Afrikaans-tuned model. The size is chosen automatically for your computer."
      : "Afrikaans uses Fluister, our Afrikaans-tuned model. The size is chosen automatically for your computer.";
  else msg = "English uses standard Whisper. The size is chosen automatically for your computer.";
  return el("div", { class: "card", style: { padding: "11px 13px", display: "flex", gap: "10px", alignItems: "center", marginBottom: "16px" } }, [
    el("div", { class: "tone-tile" + (label === "Fluister" ? " accent" : ""), style: { width: "30px", height: "30px", flex: "0 0 auto" } }, icon(label === "Fluister" ? "sparkle" : "globe", 15)),
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
          el("p", { class: "ink-3", style: { fontSize: "11px", margin: "6px 0 0" }, text: "Auto picks Fluister for Afrikaans and auto-detect, Whisper for English. Force one to override." })]), true),
      formField("Model size", el("span", { class: "label-muted", text: " (auto is recommended)" }),
        el("div", { style: { marginTop: "12px" } }, [qualitySelector(),
          el("p", { class: "ink-3", style: { fontSize: "11px", margin: "6px 0 0" }, text: "Auto picks the best model your computer can run. Bigger is more accurate but slower." })]), true),
      runOnField(),
      live ? aecLiveControl() : aecRetranscribeControl(),
    ]),
  ]);
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
var LEGACY_QUALITY = { "gpu": "large-v3", "gpu-4gb": "large-v3", "cpu-large": "large-v3", "cpu-strong": "large-v3-turbo", "cpu-mid": "medium", "cpu": "small", "cpu-min": "small" };
function normalizeQuality(q) {
  if (!q) return "auto";
  for (var i = 0; i < QUALITY_OPTS.length; i++) { if (QUALITY_OPTS[i][0] === q) return q; }
  return LEGACY_QUALITY[q] || "auto";
}
// The meeting / import Quality picker. A model not downloaded yet is greyed out;
// clicking it starts that model's download (and selects it, ready once it lands).
// "Auto" is always available and is the default.
function qualitySelector() {
  var vm = S.voiceModels || {};
  var present = {};
  (vm.models || []).forEach(function (m) { present[m.model] = !!m.present; });
  return el("div", { class: "segmented block" }, QUALITY_OPTS.map(function (o) {
    var key = o[0], label = o[1], model = (key === "auto") ? null : key;
    var ready = (model === null) || present[model];
    return el("button", {
      class: S.form.tier === key ? "on" : "",
      style: { opacity: ready ? "1" : "0.5" },
      title: ready ? null : tr("Not downloaded yet. Click to download."),
      onclick: function () {
        S.form.tier = key;
        if (model && !ready) {
          startVoiceDownload(model);
          toast("Downloading the model. You can begin once it is ready.");
        }
        render();
      },
    }, el("span", { text: label }));
  }));
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
// NVIDIA GPU is present. Remembered as a setting (default GPU). On the GPU the best model
// runs, so the Quality picker only matters on CPU.
function runOnField() {
  if (!(S.cuda && S.cuda.gpu_present)) return null;
  var v = (S.form.device === "cpu") ? "cpu" : "auto";
  var seg = segmented([["auto", "GPU"], ["cpu", "CPU"]], v, function (val) {
    S.form.device = val; saveSettings({ device: val }); render();
  });
  var note = (v === "cpu")
    ? el("p", { class: "ink-3", style: { fontSize: "11px", margin: "6px 0 0" }, text: "Running on the CPU. The Quality choice above applies." })
    : el("p", { class: "ink-3", style: { fontSize: "11px", margin: "6px 0 0" }, text: "On the GPU, Volksmond runs the Best model. The Quality choice applies on CPU." });
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
    var tl = S.settings.transcribe_languages || ["af", "en"];
    if (S.form.language !== "" && tl.indexOf(S.form.language) < 0) S.form.language = tl[0] || "af";
    S.form.tier = normalizeQuality(S.settings.tier);
    S.form.device = S.settings.device || "auto";
    S.form.engine = S.settings.engine || "auto";
    S.form.aecLive = !!S.settings.aec_live;
    S.form.aec = !!S.settings.aec;
  }
  if (S.devices) {
    if (S.devices.default_mic_index != null) S.form.mic = String(S.devices.default_mic_index);
    if (S.devices.default_loopback_index != null) S.form.loopback = String(S.devices.default_loopback_index);
  }
  refreshSessions();

  var resumed = false;
  try {
    var status = await api.get("/api/status");
    if (status.running) { adoptRunning(status); resumed = true; }
  } catch (e) {}

  if (!resumed) {
    var done = false;
    try { done = !!localStorage.getItem("vm_setup_done"); } catch (e) {}
    if (S.settings && S.settings.setup_complete) done = true;   // disk flag survives WebView storage resets
    S.route = done ? "home" : "setup";
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
  openStream(); startElapsed();
  if (status.source_kind !== "file") startLevels();
  if (status.source_kind === "file") {
    S.route = "importing";
    pollStatus(function (st) { return !st.running; }, function () { gotoFinish(S.live.outputPath); });
  } else if (status.transcribing) {
    S.route = "live";
  } else {
    S.route = "recordonly";
  }
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
