"""Build the deployable Volksmond marketing site (multi-file static, build-site
standard) from the Claude Design bundle. Run from anywhere:

    python landing/build/site_build.py

Reads the design source from ../design-bundle/project and writes the site to
../../site:
  site/index.html        productionised landing page (external CSS/JS, no scaffolding)
  site/assets/style.css  design tokens + landing layout + a few site additions
  site/assets/landing.js behaviour: EN/AF toggle, dark theme, early-access form fetch

It splits the single-file design into linked CSS/JS (build-site wants one style.css,
vanilla JS), inlines the REAL brand mark from brand/ (never the bundle's traced copy),
drops the Google Fonts call (fonts are self-hosted in fonts.css), removes the design
review scaffolding (React/Babel tweaks panel), converts inline on* handlers to
addEventListener (so the CSP needs no script unsafe-inline), wires the email form to
the /api/early-access Pages Function, adds canonical/OG/JSON-LD/skip-link, and strips
em and en dashes. Idempotent: rerun after any design-bundle refresh.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "design-bundle" / "project"
OUT = HERE.parent.parent / "site"
SITE = "https://volksmond.digiphyte.com"

# The official Volksmond mark (brand/, Chenelle 2026-06-04): five waveform bars over
# a smile, strokes on currentColor so it inherits --accent (brand blue on light,
# light-blue on dark). Identical geometry to the app's in-UI mark. Inlined so there is
# no empty-logo flash and it shows even if JS never runs.
NEW_MARK = (
    '<svg viewBox="0 0 1500 1500" fill="none" aria-hidden="true"><g transform="matrix(1,0,0,1,0,272)">'
    '<path stroke-linecap="round" transform="matrix(-1.186979,0,0,-1.186979,1416.494791,955.573618)" d="M 40.546292 283.673258 C 387.882847 -56.587826 735.222693 -56.558207 1082.562539 283.758821" stroke="currentColor" stroke-width="57"/>'
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,463.342337,489.209138)" d="M 28.501117 28.500532 L 383.301472 28.500532" stroke="currentColor" stroke-width="57"/>'
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,968.995486,489.209138)" d="M 28.501117 28.499854 L 383.301472 28.499854" stroke="currentColor" stroke-width="57"/>'
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,1221.818952,358.298284)" d="M 28.500529 28.500488 L 162.727165 28.500488" stroke="currentColor" stroke-width="57"/>'
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,716.16302,419.705936)" d="M 28.49842 28.500221 L 266.201362 28.500221" stroke="currentColor" stroke-width="57"/>'
    '<path stroke-linecap="round" transform="matrix(0,-1.186979,1.186979,0,210.51271,358.298284)" d="M 28.500529 28.498507 L 162.727165 28.498507" stroke="currentColor" stroke-width="57"/>'
    '</g></svg>'
)

HEAD_EXTRAS = f"""<link rel="canonical" href="{SITE}/" />
<meta property="og:url" content="{SITE}/" />
<meta property="og:image" content="{SITE}/assets/img/og.png" />
<meta name="twitter:image" content="{SITE}/assets/img/og.png" />
<meta name="theme-color" content="#36587b" media="(prefers-color-scheme: light)" />
<meta name="theme-color" content="#155" media="(prefers-color-scheme: dark)" />
<link rel="icon" href="/favicon.ico" sizes="any" />
<link rel="icon" href="/favicon.svg" type="image/svg+xml" />
<link rel="apple-touch-icon" href="/assets/img/apple-touch-icon.png" />
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@graph": [
    {{ "@type": "Organization", "@id": "https://digiphyte.com/#org", "name": "DigiPhyte", "url": "https://digiphyte.com" }},
    {{ "@type": "WebSite", "@id": "{SITE}/#website", "url": "{SITE}/", "name": "Volksmond",
       "publisher": {{ "@id": "https://digiphyte.com/#org" }}, "inLanguage": ["en-ZA", "af-ZA"] }}
  ]
}}
</script>
"""

HONEYPOT = ('<input type="text" name="website" tabindex="-1" autocomplete="off" '
            'class="hp" aria-hidden="true" />\n            <div class="form-fields">')

EXTRA_CSS = """

/* ===== Site additions (build-site standard) ===== */
.skip { position: absolute; left: -9999px; top: 0; z-index: 100; background: var(--accent); color: var(--accent-ink); padding: 10px 14px; border-radius: 0 0 8px 0; font: 600 13px/1 var(--font-sans); }
.skip:focus { left: 0; }
.hp { position: absolute !important; left: -9999px !important; width: 1px; height: 1px; overflow: hidden; }
.form-error { display: none; margin-top: 12px; font-size: 13px; color: var(--danger); font-weight: 500; }
a:focus-visible, button:focus-visible, input:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }

/* Privacy / legal page */
.legal { max-width: 760px; margin: 0 auto; padding: 28px 0 64px; }
.legal .back { display: inline-block; margin-bottom: 22px; font-size: 13px; color: var(--ink-3); }
.legal h1.display { margin-bottom: 6px; }
.legal .updated { color: var(--ink-3); font-size: 13px; margin-bottom: 28px; }
.legal h2 { font: 600 18px/1.3 var(--font-sans); color: var(--ink); margin: 30px 0 8px; letter-spacing: -0.01em; }
.legal p, .legal li { color: var(--ink-2); font-size: 15px; line-height: 1.62; margin: 0 0 12px; }
.legal ul { padding-left: 20px; margin: 0 0 12px; }
.legal a { color: var(--accent); }
"""

# Production behaviour. No logo machinery (marks are inlined), no entrance animation,
# no inline handlers; the form POSTs to the Pages Function which sends the link.
PROD_JS = r"""/* Volksmond landing behaviour. Vanilla, no framework, no external calls.
   EN/AF copy toggle, light/dark theme, and the early-access form, which POSTs to the
   same-origin Pages Function that stores the email and emails the download link.
   Brand marks are inlined in the HTML, so there is no logo code here. */
(function () {
  "use strict";

  function lsGet(k, d) { try { var v = localStorage.getItem(k); return v == null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  var body = document.body;

  function setDark(isDark) {
    body.setAttribute("data-dark", isDark ? "true" : "false");
    lsSet("vm_theme", isDark ? "dark" : "light");
    var btn = document.getElementById("themebtn");
    if (btn) btn.setAttribute("aria-pressed", isDark ? "true" : "false");
  }
  function toggleTheme() { setDark(body.getAttribute("data-dark") !== "true"); }

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

  function showError(form) {
    var el = form.querySelector(".form-error");
    if (!el) { el = document.createElement("div"); el.className = "form-error"; form.appendChild(el); }
    var af = document.documentElement.lang.indexOf("af") === 0;
    el.textContent = af ? "Iets het verkeerd geloop. Probeer asseblief weer."
                        : "Something went wrong. Please try again.";
    el.style.display = "block";
  }

  function wireForm(form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var input = form.querySelector('input[type=email]');
      var btn = form.querySelector('button[type=submit]');
      var hp = form.querySelector('input[name=website]');
      var consent = form.querySelector('input[type=checkbox]');
      var ts = form.querySelector('[name=cf-turnstile-response]');
      var fields = form.querySelector(".form-fields");
      var thanks = form.querySelector(".thanks");
      var email = input ? input.value.trim() : "";
      if (!email) return false;
      if (btn) btn.disabled = true;
      fetch("/api/early-access", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email, website: hp ? hp.value : "", consent: consent ? !!consent.checked : true, turnstile: ts ? ts.value : "" })
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) { return r.ok && d && d.ok; });
      }).then(function (ok) {
        if (ok) {
          if (fields) fields.style.display = "none";
          var err = form.querySelector(".form-error"); if (err) err.style.display = "none";
          if (thanks) thanks.style.display = "flex";
        } else {
          if (btn) btn.disabled = false;
          showError(form);
        }
      }).catch(function () { if (btn) btn.disabled = false; showError(form); });
      return false;
    });
  }

  function init() {
    setDark(lsGet("vm_theme", "light") === "dark");
    var stored = lsGet("vm_site_lang", null);
    var lang = stored || (((navigator.language || "").toLowerCase().indexOf("af") === 0) ? "af" : "en");
    setLang(lang);
    var en = document.getElementById("lang-en");
    var af = document.getElementById("lang-af");
    var tb = document.getElementById("themebtn");
    if (en) en.addEventListener("click", function () { setLang("en"); });
    if (af) af.addEventListener("click", function () { setLang("af"); });
    if (tb) tb.addEventListener("click", toggleTheme);
    document.querySelectorAll("form.form").forEach(wireForm);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
"""


def build():
    html = (SRC / "Volksmond - Landing Page.html").read_text(encoding="utf-8")
    tokens = (SRC / "vm-tokens.css").read_text(encoding="utf-8")

    # --- style.css: tokens (minus the Google Fonts @import) + the page's <style> block ---
    tokens = re.sub(r'@import url\("https://fonts\.googleapis\.com[^"]*"\);\n', "", tokens)
    assert "googleapis" not in tokens, "tokens still reference Google Fonts"
    m = re.search(r"<style>(.*?)</style>", html, flags=re.DOTALL)
    assert m, "inline <style> block not found"
    inline_css = m.group(1).strip()
    style_css = (
        "/* Volksmond site styles. Generated by landing/build/site_build.py, do not\n"
        "   hand-edit; edit the design bundle or the build script and rerun. Fonts are\n"
        "   in assets/fonts.css (self-hosted base64, no external calls). */\n\n"
        + tokens.strip() + "\n\n/* ===== Landing layout ===== */\n" + inline_css + EXTRA_CSS
    )

    # --- index.html transforms ---
    html = html.replace(
        '<html lang="en-ZA" data-hfont="serif" data-logo="speaker">',
        '<html lang="en-ZA" data-hfont="sans" data-logo="speaker">')
    assert 'data-hfont="sans"' in html, "heading font default not set to sans"

    html = html.replace(
        '<link rel="stylesheet" href="vm-tokens.css" />',
        '<link rel="stylesheet" href="/assets/fonts.css" />\n'
        '<link rel="stylesheet" href="/assets/style.css" />')

    html = re.sub(r"<style>.*?</style>\n?", "", html, count=1, flags=re.DOTALL)
    assert "<style>" not in html, "inline style not removed"

    html = html.replace("</head>", HEAD_EXTRAS + "</head>", 1)

    empty_mark = '<span class="mark" aria-hidden="true"></span>'
    n_marks = html.count(empty_mark)
    assert n_marks >= 3, f"expected >=3 marks, found {n_marks}"
    html = html.replace(empty_mark, f'<span class="mark" aria-hidden="true">{NEW_MARK}</span>')

    # Skip link + main landmark id
    html = html.replace(
        '<body class="vm" data-palette="clinical" data-dark="false">',
        '<body class="vm" data-palette="clinical" data-dark="false">\n'
        '<a class="skip" href="#main" data-en="Skip to content" data-af="Spring na inhoud">Skip to content</a>')
    html = html.replace("<main>", '<main id="main">', 1)

    # Form: name the email field + add a honeypot
    html = html.replace("<input type=\"email\" required",
                        "<input type=\"email\" name=\"email\" required")
    html = html.replace('<div class="form-fields">', HONEYPOT)

    # Strip inline event handlers (CSP: script-src 'self', no unsafe-inline)
    for h in (' onclick="VM.setLang(\'en\')"', ' onclick="VM.setLang(\'af\')"',
              ' onclick="VM.toggleTheme()"', ' onsubmit="return VM.submitForm(event)"'):
        html = html.replace(h, "")
    assert "VM.setLang" not in html and "VM.submitForm" not in html, "inline handler left behind"

    # a11y: give the icon-only theme button an explicit label, not just a title.
    html = html.replace(
        'id="themebtn" aria-pressed="false" title="Toggle dark theme"',
        'id="themebtn" aria-pressed="false" aria-label="Toggle dark theme" title="Toggle dark theme"')
    assert 'aria-label="Toggle dark theme"' in html, "theme button aria-label not added"

    # Footer: add a Privacy link
    html = html.replace(
        '<span class="ftag">Speak freely · Praat vrylik</span>\n      <span class="spacer"></span>',
        '<span class="ftag">Speak freely · Praat vrylik</span>\n'
        '      <a href="/privacy.html" data-en="Privacy" data-af="Privaatheid">Privacy</a>\n'
        '      <span class="spacer"></span>')

    # Replace the trailing scripts (landing.js + React/Babel/JSX) with one local script
    anchor = '<script src="landing.js"></script>'
    assert anchor in html, "landing.js script tag not found"
    head, _, tail = html.partition(anchor)
    after = tail[tail.index("</body>"):]
    html = head + '<script src="/assets/landing.js" defer></script>\n\n' + after

    # Remove the tweaks mount
    html = html.replace('<div id="tweaks-root"></div>\n', "")

    # House rule: no em or en dashes anywhere
    for a, b in ((" — ", ", "), (" – ", ", "), ("—", ", "), ("–", ", ")):
        html = html.replace(a, b)
        style_css = style_css.replace(a, b)
    assert "—" not in html and "–" not in html, "dash strip incomplete (html)"

    # Guards: nothing phones home, no scaffolding survived
    low = html.lower()
    for bad in ("googleapis", "gstatic", "unpkg", "text/babel", "tweaks-root",
                "tweaks-panel", "landing-tweaks", "react.development", "vm-tokens.css"):
        assert bad not in low, f"residual {bad!r} in index.html"

    (OUT / "index.html").write_text(html, encoding="utf-8")
    (OUT / "assets" / "style.css").write_text(style_css, encoding="utf-8")
    (OUT / "assets" / "landing.js").write_text(PROD_JS, encoding="utf-8")
    print("wrote site/index.html      ", len(html.encode()) // 1024, "KB")
    print("wrote site/assets/style.css", len(style_css.encode()) // 1024, "KB,",
          "marks inlined:", n_marks)
    print("wrote site/assets/landing.js", len(PROD_JS.encode()) // 1024, "KB")


if __name__ == "__main__":
    build()
