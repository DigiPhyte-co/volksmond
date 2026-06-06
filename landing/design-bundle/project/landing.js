/* ──────────────────────────────────────────────────────────────────────────
 * Volksmond landing page — behaviour (vanilla, no framework).
 * Owns: EN/AF copy toggle, light/dark + palette theming, logo swapping,
 * the email form stub, and the hero transcript entrance animation.
 * All choices persist to localStorage. The Tweaks panel (review only)
 * calls the same window.VM setters, so there is a single source of truth.
 * ────────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  // ── Logo ─────────────────────────────────────────────────────────────────
  // The chosen mark: a five-bar waveform over a smile (DigiPhyte supplied art,
  // reconstructed as a single stroke on currentColor so it inherits --accent —
  // navy in light mode, lighter blue in dark, and adapts to every palette).
  // Geometry traced from the source SVG; viewBox framed to centre the artwork.
  var MARK =
    '<svg viewBox="80 130 1340 1340" fill="none" stroke="currentColor" ' +
    'stroke-width="68" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M211 437V596"/>' +
    '<path d="M463 306V727"/>' +
    '<path d="M716 376V658"/>' +
    '<path d="M969 306V727"/>' +
    '<path d="M1222 437V596"/>' +
    '<path d="M131 891C544 1295 956 1295 1368 891"/>' +
    "</svg>";

  function lsGet(k, d) { try { var v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  // Paint the mark into every .mark slot (header, footer, transcript titlebar).
  function setLogo() {
    document.querySelectorAll(".mark").forEach(function (el) { el.innerHTML = MARK; });
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
    submitForm: submitForm,
    get state() {
      return {
        lang: lsGet("vm_site_lang", "en"),
        dark: lsGet("vm_theme", "light") === "dark",
        palette: lsGet("vm_palette", "clinical"),
        hfont: lsGet("vm_hfont", "serif"),
      };
    },
  };

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    var s = window.VM.state;
    setPalette(s.palette);
    setDark(s.dark);
    setLogo();
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
