/* ──────────────────────────────────────────────────────────────────────────
 * Volksmond landing page — behaviour (vanilla, no framework).
 * Owns: EN/AF copy toggle, light/dark + palette theming, logo swapping,
 * the email form stub, and the hero transcript entrance animation.
 * All choices persist to localStorage. The Tweaks panel (review only)
 * calls the same window.VM setters, so there is a single source of truth.
 * ────────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  // ── Logo set ───────────────────────────────────────────────────────────────
  // Three marks, all stroke/fill on currentColor so they inherit --accent and
  // adapt to every palette + dark mode. viewBox 0 0 24 24.
  var LOGOS = {
    // 1 · Loudspeaker with sound waves (the lead idea).
    speaker:
      '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M4 9.2h3.1L11.4 6v12L7.1 14.8H4z" fill="currentColor"/>' +
      '<path d="M14.6 9.1a4.2 4.2 0 0 1 0 5.8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>' +
      '<path d="M17.3 6.6a8 8 0 0 1 0 10.8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>' +
      "</svg>",
    // 2 · Waveform — seven rounded bars, symmetric, reads as audio levels.
    wave:
      '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true" stroke="currentColor" stroke-width="2.1" stroke-linecap="round">' +
      '<path d="M3 11v2"/><path d="M6.5 8.5v7"/><path d="M10 5v14"/>' +
      '<path d="M13.5 8v8"/><path d="M17 4.5v15"/><path d="M20.5 9v6"/>' +
      "</svg>",
    // 3 · Speech bubble holding a small waveform — "spoken words, captured".
    bubble:
      '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M5 4.2h14a2.6 2.6 0 0 1 2.6 2.6v7.4a2.6 2.6 0 0 1-2.6 2.6h-8.7L6 19.6v-2.8H5A2.6 2.6 0 0 1 2.4 14.2V6.8A2.6 2.6 0 0 1 5 4.2z" stroke="currentColor" stroke-width="1.6"/>' +
      '<path d="M8 10v3.5M12 8v7.5M16 10v3.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>' +
      "</svg>",
  };
  var LOGO_ORDER = ["speaker", "wave", "bubble"];

  function lsGet(k, d) { try { var v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  // ── Logo ─────────────────────────────────────────────────────────────────
  function setLogo(name) {
    if (LOGOS[name] == null) name = "speaker";
    document.querySelectorAll(".mark").forEach(function (el) { el.innerHTML = LOGOS[name]; });
    lsSet("vm_logo", name);
    document.documentElement.setAttribute("data-logo", name);
  }

  // ── Theme: light/dark + palette ────────────────────────────────────────────
  var root = document.body; // carries class "vm" + data-palette + data-dark
  function setDark(isDark) {
    root.setAttribute("data-dark", isDark ? "true" : "false");
    lsSet("vm_theme", isDark ? "dark" : "light");
    var btn = document.getElementById("themebtn");
    if (btn) btn.setAttribute("aria-pressed", isDark ? "true" : "false");
  }
  function toggleTheme() { setDark(root.getAttribute("data-dark") !== "true"); }
  function setPalette(name) {
    var ok = { clinical: 1, paper: 1, veld: 1 };
    if (!ok[name]) name = "clinical";
    root.setAttribute("data-palette", name);
    lsSet("vm_palette", name);
  }

  // ── Heading font: serif (warmer) or sans (uniform) ──────────────────────────
  function setHeadingFont(which) {
    var serif = which !== "sans";
    document.documentElement.setAttribute("data-hfont", serif ? "serif" : "sans");
    lsSet("vm_hfont", serif ? "serif" : "sans");
  }

  // ── Language ───────────────────────────────────────────────────────────────
  function setLang(lang) {
    if (lang !== "af") lang = "en";
    document.documentElement.lang = lang === "af" ? "af-ZA" : "en-ZA";
    document.querySelectorAll("[data-en]").forEach(function (el) {
      var v = el.getAttribute("data-" + lang);
      if (v != null) el.textContent = v;
    });
    document.querySelectorAll("[data-en-ph]").forEach(function (el) {
      var v = el.getAttribute("data-" + lang + "-ph");
      if (v != null) el.setAttribute("placeholder", v);
    });
    var en = document.getElementById("lang-en"), af = document.getElementById("lang-af");
    if (en && af) {
      en.classList.toggle("on", lang === "en"); en.setAttribute("aria-pressed", lang === "en");
      af.classList.toggle("on", lang === "af"); af.setAttribute("aria-pressed", lang === "af");
    }
    lsSet("vm_site_lang", lang);
  }

  // ── Email form (not wired yet) ──────────────────────────────────────────────
  // TODO: wire form endpoint — point action at the Cloudflare Pages Function
  // that stores the email and sends the download link. For now: calm thank-you.
  function submitForm(e) {
    e.preventDefault();
    var form = e.target;
    var thanks = form.parentNode.querySelector(".thanks");
    if (thanks) { thanks.style.display = "flex"; }
    var fields = form.querySelector(".form-fields");
    if (fields) { fields.style.display = "none"; }
    return false;
  }

  // ── Hero transcript: sequential entrance + gentle live tick ─────────────────
  function animateTranscript() {
    var rows = document.querySelectorAll(".vt-row");
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    rows.forEach(function (r, i) {
      if (reduce) { r.classList.add("in"); return; }
      setTimeout(function () { r.classList.add("in"); }, 220 + i * 230);
    });
  }

  // ── Expose setters for header controls + Tweaks bridge ──────────────────────
  window.VM = {
    setLang: setLang, toggleTheme: toggleTheme, setDark: setDark,
    setPalette: setPalette, setLogo: setLogo, setHeadingFont: setHeadingFont,
    submitForm: submitForm, LOGO_ORDER: LOGO_ORDER,
    get state() {
      return {
        lang: lsGet("vm_site_lang", "en"),
        dark: lsGet("vm_theme", "light") === "dark",
        palette: lsGet("vm_palette", "clinical"),
        logo: lsGet("vm_logo", "speaker"),
        hfont: lsGet("vm_hfont", "serif"),
      };
    },
  };

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    var s = window.VM.state;
    setPalette(s.palette);
    setDark(s.dark);
    setLogo(s.logo);
    setHeadingFont(s.hfont);
    // Language: stored choice, else browser hint.
    var lang = s.lang;
    if (!lsGet("vm_site_lang", null)) {
      lang = ((navigator.language || "").toLowerCase().indexOf("af") === 0) ? "af" : "en";
    }
    setLang(lang);
    animateTranscript();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
