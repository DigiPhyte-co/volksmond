"""Assemble the Claude Design bundle into ONE self-contained, privacy-respecting
landing page. Run from this folder:  python assemble.py   (then: python validate.py)

Reads the design source from ../design-bundle/project and _fontface.css beside this
script; writes ../../Volksmond - Landing Page.html. Inlines fonts + tokens + cleaned
JS, drops the Google Fonts call and the React/Babel Tweaks panel, applies the
locked-in defaults (Sans headings / clinical palette / speaker logo placeholder),
and strips em/en dashes. Paths are relative to this file, so it is laptop-portable.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUNDLE = HERE.parent / "design-bundle" / "project"
OUT = HERE.parent.parent / "Volksmond - Landing Page.html"
FONTFACE = HERE / "_fontface.css"

html = (BUNDLE / "Volksmond - Landing Page.html").read_text(encoding="utf-8")
tokens = (BUNDLE / "vm-tokens.css").read_text(encoding="utf-8")
landing_js = (BUNDLE / "landing.js").read_text(encoding="utf-8")
fontface = FONTFACE.read_text(encoding="utf-8")

# 1. Strip the Google Fonts @import (fonts are embedded instead).
tokens = re.sub(r'@import url\("https://fonts\.googleapis\.com[^"]*"\);\n', "", tokens)
assert "googleapis" not in tokens, "tokens still reference Google Fonts"

# 2. Replace the external stylesheet <link> with inlined fonts + tokens.
inline_css = (
    "<!-- Fonts self-hosted (base64). A privacy product's page must not call out. -->\n"
    '<style id="vm-fonts">\n' + fontface + "\n</style>\n"
    '<style id="vm-tokens">\n' + tokens.strip() + "\n</style>"
)
link = '<link rel="stylesheet" href="vm-tokens.css" />'
assert link in html, "stylesheet link not found"
html = html.replace(link, inline_css)

# 3. The Volksmond brand mark: five waveform bars over a smile. Real geometry from
#    brand/ (Chenelle, 2026-06-04), strokes on currentColor so it inherits --accent
#    (brand blue on light, light-blue on dark). One mark now, no picker. Inlined into
#    the empty marks so there is no empty-logo flash before JS runs.
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
empty_mark = '<span class="mark" aria-hidden="true"></span>'
n_marks = html.count(empty_mark)
assert n_marks >= 3, f"expected >=3 marks, found {n_marks}"
html = html.replace(empty_mark, f'<span class="mark" aria-hidden="true">{NEW_MARK}</span>')

# 4. Remove the Tweaks panel mount + its dead CSS rule.
html = html.replace('<div id="tweaks-root"></div>\n', "")
html = re.sub(r"\s*#tweaks-root \{[^}]*\}\n", "\n", html)

# 5. Clean landing.js: drop the entrance animation + tweaks mentions; keep all setters.
landing_js = landing_js.replace(
    "the email form stub, and the hero transcript entrance animation.",
    "the email form stub.",
)
landing_js = landing_js.replace(
    "All choices persist to localStorage. The Tweaks panel (review only)",
    "All choices persist to localStorage. window.VM exposes the",
)
landing_js = landing_js.replace(
    "calls the same window.VM setters, so there is a single source of truth.",
    "setters, so a default can be changed in one place or previewed from the console.",
)
landing_js = landing_js.replace(
    "Expose setters for header controls + Tweaks bridge",
    "Expose setters for the header controls (and console previews)",
)
landing_js = re.sub(r"  // ─+ Hero transcript:.*?\n  \}\n\n", "", landing_js, flags=re.DOTALL)
landing_js = landing_js.replace("    animateTranscript();\n", "")
assert "animateTranscript" not in landing_js, "animation leftover in JS"

# 6. Replace ALL trailing <script> tags (landing.js + React/Babel/JSX) with one inline script.
anchor = '<script src="landing.js"></script>'
assert anchor in html, "landing.js script tag not found"
head, _, tail = html.partition(anchor)
after = tail[tail.index("</body>"):]
inline_js = "<script>\n" + landing_js.strip() + "\n</script>\n\n"
html = head + inline_js + after

# 6b. Collapse the JS logo set to the single brand mark. The picker (speaker/wave/bubble)
#     existed only for the design review; now there is one logo. setLogo() falls back to
#     "speaker" for any unknown name, so a stale localStorage choice still yields NEW_MARK.
html, n_logos = re.subn(
    r'var LOGOS = \{.*?var LOGO_ORDER = \["speaker", "wave", "bubble"\];',
    lambda m: "var LOGOS = { speaker: '" + NEW_MARK + "' };\n  var LOGO_ORDER = [\"speaker\"];",
    html, flags=re.DOTALL,
)
assert n_logos == 1, f"LOGOS block not replaced ({n_logos})"
assert "M4 9.2h3.1L11.4 6v12" not in html, "old speaker mark still present"
assert "1082.562539" in html, "new brand mark missing"

# 7. Locked-in defaults (Sean, 2026-06-03): headline font = Sans. Flip ONLY the <html>
#    default attribute + JS fallback, NOT the html[data-hfont="serif"] CSS selector.
html = html.replace(
    '<html lang="en-ZA" data-hfont="serif" data-logo="speaker">',
    '<html lang="en-ZA" data-hfont="sans" data-logo="speaker">',
)
html = html.replace('lsGet("vm_hfont", "serif")', 'lsGet("vm_hfont", "sans")')
assert 'data-hfont="sans" data-logo' in html, "heading-font default not set to sans"
assert 'html[data-hfont="serif"] .display' in html, "serif CSS selector was damaged"

# 8. House rule: no em/en dashes anywhere, comments included. Base64 has none, so global is safe.
html = html.replace(" — ", ", ").replace(" – ", ", ")
html = html.replace("—", ", ").replace("–", ", ")
assert "—" not in html and "–" not in html, "dash strip incomplete"

# 9. Guards: nothing phones home, no design-time scaffolding left behind.
low = html.lower()
for bad in ("googleapis", "gstatic", "unpkg", "text/babel",
            "tweaks-root", "tweaks-panel", "landing-tweaks", "react.development"):
    if bad in low:
        i = low.index(bad)
        raise AssertionError(f"residual {bad!r}: ...{html[max(0,i-60):i+60]!r}...")

OUT.write_text(html, encoding="utf-8")
print(f"wrote {OUT}")
print(f"size: {len(html.encode('utf-8'))//1024} KB; marks inlined: {n_marks}")
