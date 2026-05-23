/* Volksmond (SA-Live-Transcribe) browser UI.
 *
 * Vanilla JS, no framework, no CDN: the product works offline, so nothing is
 * fetched from the network. The Volksmond design is rebuilt here with the .vm
 * design system in styles.css. Every screen is wired to the real FastAPI
 * endpoints; nothing is faked. Where the design showed a feature that is not
 * built yet (clean second pass, in-app model download, calendar in the start
 * flow), it is left out rather than mocked.
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
  span.innerHTML = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 22 22" fill="none" aria-hidden="true">' +
    '<path d="M3 12.5 C 6 16.5, 16 16.5, 19 12.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>' +
    '<path d="M7.5 8.5 L 7.5 6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>' +
    '<path d="M11 8.5 L 11 4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>' +
    '<path d="M14.5 8.5 L 14.5 6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';
  return span;
}

/* ── state ────────────────────────────────────────────────── */
function freshLive() {
  return {
    running: false, recording: false, transcribing: false, sourceKind: null,
    startedAt: null, outputPath: null, audioStem: null, tier: null, model: null,
    language: null, stopping: false, segments: [], es: null, title: "", importName: "",
  };
}
var S = {
  route: "home", booted: false,
  settings: null, features: null, models: null, appInfo: null, license: null, devices: null,
  sessions: [], sessionsFolder: "",
  live: freshLive(),
  form: { title: "", language: "af", tier: "auto", terms: [], record: false, mic: null, loopback: null },
  setup: { stage: "welcome", choice: "transcribe" },
  finish: { outputPath: null, title: "", summary: null, savedAs: null, summarising: false, recordingStem: null, sinkError: null },
  reader: { name: "", title: "", text: "", summarising: false, summary: null },
  upgrade: { keyState: "empty", value: "", msg: "" },
  settingsDraft: null,
  theme: (function () { try { return localStorage.getItem("vm_theme") || "system"; } catch (e) { return "system"; } })(),
  stopMenuOpen: false,
  toast: null,
};

// transient refs + timers (not part of render state)
var liveDocEl = null, liveBodyEl = null, elapsedEl = null, recTimerEl = null;
var pollTimer = null, elapsedTimer = null, toastTimer = null;

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
  S.stopMenuOpen = false;
  S.route = route;
  render();
}
function teardownLive() {
  closeStream();
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
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

/* ── session lifecycle ────────────────────────────────────── */
async function startLive() {
  var body = {
    topic: S.form.title || "",
    tier: S.form.tier, language: S.form.language,
    prompt: S.form.terms.join(", "),
    record: !!S.form.record, transcribe: true,
    mic_device: S.form.mic, loopback_device: S.form.loopback,
  };
  try {
    var resp = await api.post("/api/start", body);
    S.live = freshLive();
    S.live.running = true; S.live.transcribing = true; S.live.recording = !!resp.recording;
    S.live.sourceKind = "live"; S.live.startedAt = new Date().toISOString();
    S.live.outputPath = resp.output_path; S.live.audioStem = resp.audio_stem;
    S.live.tier = resp.tier; S.live.model = resp.model; S.live.language = resp.language;
    S.live.title = S.form.title || "Live meeting";
    go("live"); openStream(); startElapsed();
  } catch (e) { toast(e.message || "Could not start.", true); }
}
async function startRecordOnly() {
  try {
    var resp = await api.post("/api/start", { topic: S.form.title || "", transcribe: false, record: true, mic_device: S.form.mic, loopback_device: S.form.loopback });
    S.live = freshLive();
    S.live.running = true; S.live.recording = true; S.live.transcribing = false;
    S.live.sourceKind = "live"; S.live.startedAt = new Date().toISOString();
    S.live.outputPath = resp.output_path; S.live.audioStem = resp.audio_stem;
    S.live.title = S.form.title || "Recording";
    go("recordonly"); startElapsed();
  } catch (e) { toast(e.message || "Could not start recording.", true); }
}
async function startImport(arg) {
  var body = { topic: arg.topic || "", tier: S.form.tier, language: S.form.language, prompt: (S.form.terms || []).join(", ") };
  if (arg.path) body.paths = [arg.path];
  if (arg.stem) body.stem = arg.stem;
  try {
    var resp = await api.post("/api/transcribe-file", body);
    S.live = freshLive();
    S.live.running = true; S.live.transcribing = true; S.live.sourceKind = "file";
    S.live.startedAt = new Date().toISOString();
    S.live.outputPath = resp.output_path; S.live.tier = resp.tier; S.live.model = resp.model;
    S.live.importName = baseName(arg.path) || (arg.topic || "recording");
    S.live.title = arg.topic || topicFromName(baseName(resp.output_path));
    go("importing"); openStream();
    pollStatus(function (st) { return !st.running; }, function () { gotoFinish(S.live.outputPath); });
  } catch (e) { toast(e.message || "Could not start transcription.", true); }
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
async function importFromPicker() {
  var p = await pickFile("file");
  if (!p) return;
  // Go through the context screen first (title, language, names and jargon),
  // then transcribe - same as a live meeting gets its pre-meeting setup.
  S.importPath = p; S.importStem = null; S.importName = baseName(p);
  S.form.title = ""; S.form.terms = [];
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
async function doSummarise(fileName, scope) {
  var target = scope === "reader" ? S.reader : S.finish;
  target.summarising = true; render();
  try {
    var resp = await api.post("/api/summarise", { file: fileName });
    target.summary = resp.summary; target.savedAs = resp.saved; target.summarising = false;
    render();
  } catch (e) {
    target.summarising = false; render();
    toast(e.message || "Summarise failed.", true);
  }
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
async function refreshSessions() {
  try {
    var d = await api.get("/api/sessions");
    S.sessions = d.files || []; S.sessionsFolder = d.folder || "";
    if (S.route === "history") render();
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
function render() {
  liveDocEl = liveBodyEl = elapsedEl = recTimerEl = null;
  clear(APP);
  var view;
  switch (S.route) {
    case "setup": view = setupView(); break;
    case "home": view = shell("home", homeView()); break;
    case "pre": view = shell("home", preView()); break;
    case "importpre": view = shell("home", importPreView()); break;
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
function finishSetup() { try { localStorage.setItem("vm_setup_done", "1"); } catch (e) {} go("home"); }
function setupView() {
  var stage = S.setup.stage;
  var inner;
  if (stage === "welcome") {
    inner = el("div", { class: "col-narrow stack", style: { gap: "20px" } }, [
      el("div", { class: "wordmark", style: { marginBottom: "4px" } }, [markSvg(22), el("span", { text: "Volksmond" }), el("span", { class: "provisional", text: "working name" })]),
      el("h1", { text: "A calm, private transcript of any meeting on your computer." }),
      el("p", { class: "ink-2", style: { fontSize: "15px" }, text: "Volksmond listens to your microphone and the audio coming out of your computer, and writes it down as people talk. Built for Afrikaans, English, and the way people actually switch between them." }),
      el("div", { class: "card", style: { padding: "18px", display: "flex", gap: "14px" } }, [
        el("div", { class: "tone-tile accent", style: { width: "40px", height: "40px", flex: "0 0 auto" } }, icon("lock", 20)),
        el("div", {}, [
          el("div", { style: { fontWeight: "600", marginBottom: "4px" }, text: "Your audio never leaves this computer." }),
          el("p", { class: "ink-2", style: { fontSize: "12.5px" }, text: "No cloud, no third-party servers, no telemetry. Everything is transcribed locally, on your machine. You can use Volksmond completely offline." }),
        ]),
      ]),
      el("div", { class: "row gap-10" }, [
        el("button", { class: "btn primary tall grow", onclick: function () { S.setup.stage = "summaries"; render(); } }, "Get started"),
      ]),
      el("p", { class: "ink-3", style: { fontSize: "11.5px" }, text: "The language model for transcription is installed with the app. Summaries are an optional extra you can turn on next." }),
    ]);
  } else if (stage === "summaries") {
    var installed = summaryInstalled();
    inner = el("div", { class: "col-narrow stack", style: { gap: "18px" } }, [
      el("div", { class: "eyebrow", text: "Setup, summaries" }),
      el("h1", { text: "Do you want to just transcribe, or also summarise on your machine?" }),
      el("p", { class: "ink-2", text: "Summaries condense a finished transcript into the decisions, the to-dos, and what stayed unresolved. They run on a small model on your machine, separate from the one that does the transcribing. Off by default." }),
      choiceCard("transcribe", "mic", "Just transcribe", "The original promise. Live transcripts, history, all of it. No extra model, no extra RAM.", "Default. You can turn summaries on later in Settings."),
      choiceCard("summarise", "note", "Transcribe and summarise", "Adds a Summarise button at the end of every meeting, run entirely on this machine.",
        installed ? "A summary model is already installed on this machine." : "Needs a summary model file in your models folder. You can set this up in Settings."),
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
        onclick: startRecordOnly }),
    ]),
    el("div", { class: "dropzone" }, [
      el("div", { class: "tile" }, icon("upload", 18)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "500", fontSize: "13.5px" }, text: "Have a recording already?" }),
        el("div", { class: "ink-3", style: { fontSize: "12px", marginTop: "2px" }, text: "Up to several hours. The file stays on this computer. It is never uploaded." }),
      ]),
      el("button", { class: "btn", onclick: importFromPicker }, "Browse"),
    ]),
  ]));
}

/* ── pre-meeting (live start) ─────────────────────────────── */
function preView() {
  var langSeg = segmented([["af", "Afrikaans"], ["en", "English"], ["", "Auto-detect"]], S.form.language, function (v) { S.form.language = v; render(); });
  var tierSeg = segmented([["auto", "Auto"], ["cpu-mid", "Fast"], ["cpu-strong", "Balanced"], ["gpu", "Best"]], S.form.tier, function (v) { S.form.tier = v; render(); });

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
    el("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" } }, [
      formField("Language", null, langSeg, true),
      formField("Quality", null, tierSeg, true),
    ]),
    formField("Names and jargon", el("span", { class: "label-muted", text: " (optional, helps accuracy)" }), termsBox()),
    el("p", { class: "hint", style: { marginTop: "-8px", marginBottom: "16px" }, text: "Your saved default context is applied automatically. Add anything specific to this meeting here." }),
    recordCard,
  ]);

  var right = el("div", { class: "stack gap-12" }, [
    el("div", { class: "card", style: { padding: "16px" } }, [
      el("div", { class: "section-label", style: { marginBottom: "10px" }, text: "Audio sources" }),
      deviceField("Your microphone", dev.mics, S.form.mic, dev.default_mic_index, function (v) { S.form.mic = v; }),
      deviceField("System audio (everyone else)", dev.loopbacks, S.form.loopback, dev.default_loopback_index, function (v) { S.form.loopback = v; }),
      el("p", { class: "ink-3", style: { fontSize: "11.5px", margin: "0" }, text: "Your voice comes from the microphone. Everyone else comes from your computer's own audio." }),
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
    el("div", { class: "row gap-16", style: { marginTop: "24px" } }, [
      el("button", { class: "btn primary big", onclick: startLive }, [icon("dot", 15), "Begin"]),
      el("button", { class: "btn ghost", onclick: function () { go("home"); } }, "Back"),
      el("span", { class: "ink-3", style: { fontSize: "11.5px", marginLeft: "auto" }, text: "Audio stays on this machine unless you opt in." }),
    ]),
  ]));
}
function termsBox() {
  var box = el("div", { class: "chipbox" });
  S.form.terms.forEach(function (t, i) {
    box.appendChild(el("span", { class: "tag" }, [el("span", { text: t }),
      el("button", { onclick: function () { S.form.terms.splice(i, 1); render(); } }, icon("x", 12))]));
  });
  var inp = el("input", { placeholder: "Add a term", onkeydown: function (e) {
    if (e.key === "Enter") { e.preventDefault(); var v = e.target.value.trim(); if (v) { S.form.terms.push(v); render(); } }
  } });
  box.appendChild(inp);
  return box;
}

/* ── import setup (context before transcribing a file) ──────── */
function importPreView() {
  var langSeg = segmented([["af", "Afrikaans"], ["en", "English"], ["", "Auto-detect"]], S.form.language, function (v) { S.form.language = v; render(); });
  var tierSeg = segmented([["auto", "Auto"], ["cpu-mid", "Fast"], ["cpu-strong", "Balanced"], ["gpu", "Best"]], S.form.tier, function (v) { S.form.tier = v; render(); });
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
    el("div", { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" } }, [
      formField("Language", null, langSeg, true),
      formField("Quality", null, tierSeg, true),
    ]),
    formField("Names and jargon", el("span", { class: "label-muted", text: " (optional, helps accuracy)" }), termsBox()),
    el("p", { class: "hint", style: { marginTop: "-8px", marginBottom: "16px" }, text: "Your saved default context is applied automatically. Add anything specific to this meeting here." }),
    el("div", { class: "row gap-16", style: { marginTop: "8px" } }, [
      el("button", { class: "btn primary big", onclick: begin }, [icon("note", 15), "Transcribe"]),
      el("button", { class: "btn ghost", onclick: function () { go("home"); } }, "Back"),
    ]),
  ]));
}

/* ── live screen ──────────────────────────────────────────── */
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
      S.live.model ? el("span", { class: "chip" }, "On-device model") : null,
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

  return el("div", { class: "live" }, [header, liveBodyEl, footer]);
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
    return el("div", { class: "live" }, [header, body, footer]);
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
    el("div", { class: "right" }, el("button", { class: "btn ghost", onclick: function () { api.post("/api/stop?what=all").catch(function () {}); toast("Stopping."); go("home"); } }, "Cancel")),
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
  return el("div", { class: "card disclosure accent", style: { padding: "18px", display: "flex", gap: "14px", alignItems: "flex-start" } }, [
    el("div", { class: "tone-tile accent", style: { width: "30px", height: "30px", flex: "0 0 auto" } }, icon("sparkle", 16)),
    el("div", { class: "grow" }, [
      el("div", { style: { fontWeight: "600" }, text: "Summarise this transcript" }),
      el("p", { class: "ink-2", style: { fontSize: "12.5px", marginTop: "4px" }, text: "Runs on this computer using your installed model. Produces decisions, action items, and open questions." }),
    ]),
    el("button", { class: "btn primary", onclick: function () { doSummarise(fileName, scope); } }, [icon("sparkle", 14), "Summarise"]),
  ]);
}
function summaryResult(summary, savedAs, fileName, scope) {
  var card = el("div", { class: "card sum-card" }, [
    el("div", { class: "head" }, [
      el("div", { class: "tile" }, icon("sparkle", 15)),
      el("div", { class: "grow" }, [
        el("div", { style: { fontWeight: "600" }, text: "Summary" }),
        el("div", { class: "ink-3", style: { fontSize: "11.5px" }, text: "Ran on this computer, saved next to the transcript" }),
      ]),
      el("button", { class: "btn ghost sm", onclick: function () { copyText(summary); } }, [icon("copy", 13), "Copy"]),
      el("button", { class: "btn ghost sm", onclick: function () { (scope === "reader" ? S.reader : S.finish).summary = null; doSummarise(fileName, scope); } }, "Regenerate"),
    ]),
    el("div", { class: "sum-body" }, renderMarkdown(summary)),
    savedAs ? el("div", { class: "saved-strip" }, [icon("check", 14), el("span", {}, ["Saved as ", el("span", { class: "mono", text: baseName(savedAs) }), ", next to the transcript. Nothing was sent off this computer."])]) : null,
  ]);
  return card;
}

/* ── history ──────────────────────────────────────────────── */
var histQuery = "";
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
      var openIt = function () { openReader(f.name); };
      return el("div", { class: "hist-row", role: "button", tabindex: "0", onclick: openIt, onkeydown: keyActivate(openIt) }, [
        el("div", { class: "when", text: f.date ? f.date + " · " + f.time : "" }),
        el("div", { class: "topic" }, raw(f.topic || topicFromName(f.name))),
        el("div", { class: "right" }, [
          el("span", { class: "ink-3", style: { fontSize: "11.5px" }, text: fmtBytes(f.size) }),
          el("button", { class: "btn ghost sm", onclick: function (e) { e.stopPropagation(); openReader(f.name); } }, "Open"),
        ]),
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
  S.reader = { name: name, title: topicFromName(name), text: "Loading...", summarising: false, summary: null };
  go("reader");
  try { S.reader.text = await api.text("/sessions/" + encodeURIComponent(name)); }
  catch (e) { S.reader.text = "Could not load this transcript: " + e.message; }
  render();
}

/* ── reader (past transcript) ─────────────────────────────── */
function readerView() {
  return el("div", { class: "screen" }, el("div", { class: "screen-inner col-mid stack", style: { gap: "16px" } }, [
    el("button", { class: "btn ghost sm", style: { alignSelf: "flex-start" }, onclick: function () { go("history"); } }, [icon("back", 14), "Back to history"]),
    el("div", { class: "row gap-12" }, [
      el("h2", {}, raw(S.reader.title)),
      el("span", { class: "grow" }),
      el("button", { class: "btn ghost sm", onclick: function () { copyText(S.reader.text); } }, [icon("copy", 13), "Copy"]),
      el("button", { class: "btn ghost sm", onclick: function () { api.post("/api/open-folder").catch(function () {}); } }, [icon("folder", 13), "Folder"]),
    ]),
    summariseCard(S.reader.name, "reader"),
    el("div", { class: "card", style: { padding: "20px 22px" } },
      el("div", { class: "doc", style: { maxWidth: "none", fontSize: "15px", whiteSpace: "pre-wrap", fontFamily: "var(--font-transcript)" } }, raw(S.reader.text))),
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
    summariesCard(),
    dataCard(st),
    dangerCard(st),
    aboutCard(),
  ]));
}
function aboutCard() {
  var version = (S.appInfo && S.appInfo.version) || "?";
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "About" }),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, markSvg(20)),
      el("div", { class: "body" }, [
        el("div", { class: "t" }, [el("span", { text: "Volksmond" }), el("span", { class: "chip", text: "Version " + version })]),
        el("div", { class: "s", text: "Said FOLKS-mont. Afrikaans for the way people actually speak." }),
        el("div", { class: "s", text: "A DigiPhyte product, built in South Africa. All transcription happens on this machine unless you explicitly opt in." }),
      ]),
      el("div", { class: "ctl" }, el("button", { class: "btn ghost", onclick: function () { openExternal("https://digiphyte.com"); } }, "digiphyte.com")),
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
        : el("button", { class: "btn primary", onclick: function () { S.upgrade = { keyState: "empty", value: "", msg: "" }; go("upgrade"); } }, "Upgrade"),
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
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "Transcription" }),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("globe", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Default language" }), el("div", { class: "s", text: "Used unless you change it for a meeting." })]),
      el("div", { class: "ctl" }, selectEl([["af", "Afrikaans"], ["en", "English"], ["", "Auto-detect"]], st.transcription_language || "af", function (v) { saveSettings({ transcription_language: v }); })),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("cpu", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Quality" }), el("div", { class: "s", text: "Auto picks the best model your hardware can run." })]),
      el("div", { class: "ctl" }, selectEl([["auto", "Auto-detect"], ["gpu", "Best (GPU)"], ["cpu-strong", "Balanced"], ["cpu-mid", "Fast"]], st.tier || "auto", function (v) { saveSettings({ tier: v }); })),
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
function summariesCard() {
  var m = S.models || {};
  var installed = m.summary_installed;
  return el("div", { class: "card settings-card" }, [
    el("div", { class: "card-title section-label", text: "Summaries, run on this machine" }),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("cpu", 18)),
      el("div", { class: "body" }, [
        el("div", { class: "t", text: "Summary model" }),
        el("div", { class: "s", text: installed ? ("Installed: " + (m.summary_model || "a local model") + ". Summaries are free and stay on this computer.") : "None yet. Point Volksmond at a GGUF model file to turn summaries on." }),
      ]),
      el("div", { class: "ctl row gap-8" }, [
        installed ? el("span", { class: "chip ok" }, [icon("check", 12), "Installed"]) : null,
        el("button", { class: "btn ghost", onclick: pickSummaryModel }, installed ? "Change" : "Choose model"),
      ]),
    ]),
    el("div", { class: "set-row" }, [
      el("div", { class: "ic" }, icon("folder", 18)),
      el("div", { class: "body" }, [el("div", { class: "t", text: "Open data folder" }), el("div", { class: "s", text: "See the transcripts and any models stored on this computer." })]),
      el("div", { class: "ctl" }, el("button", { class: "btn ghost", onclick: function () { api.post("/api/open-folder").catch(function () {}); } }, "Open")),
    ]),
  ]);
}
async function pickSummaryModel() {
  var p = await pickFile("file");
  if (!p) return;
  if (!/\.gguf$/i.test(p)) { toast("Choose a .gguf model file.", true); return; }
  try { await api.post("/api/settings", { summary_model: p }); S.models = await api.get("/api/models"); toast("Summary model set."); render(); }
  catch (e) { toast(e.message || "Could not set model.", true); }
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

/* ── small shared builders ────────────────────────────────── */
function segmented(options, value, onChange) {
  return el("div", { class: "segmented block" }, options.map(function (o) {
    return el("button", { class: value === o[0] ? "on" : "", onclick: function () { onChange(o[0]); } }, o[1]);
  }));
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
  ]);
  S.settings = results[0]; S.features = results[1]; S.models = results[2];
  S.appInfo = results[3]; S.license = results[4]; S.devices = results[5];
  LANG = afLang(S.settings);
  if (S.settings) {
    S.form.language = S.settings.transcription_language != null ? S.settings.transcription_language : "af";
    S.form.tier = S.settings.tier || "auto";
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
  S.live.tier = status.tier; S.live.model = status.model; S.live.language = status.language;
  S.live.stopping = !!status.stopping;
  S.live.title = topicFromName(baseName(status.output_path));
  openStream(); startElapsed();
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
  if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey) && S.route === "history") {
    e.preventDefault();
    var s = document.querySelector('.screen input[placeholder^="Search"]');
    if (s) s.focus();
  }
});

boot();
