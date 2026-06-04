"""Validate the assembled landing page. Run from this folder: python validate.py
Checks: no external network calls (only digiphyte.com + schema.org), 7 embedded
fonts, required SEO/AI blocks, behaviour present, EN/AF copy parity, no em/en
dashes, clean structure, default logo inlined. Also writes readable.html (base64
elided) for human review of the markup/CSS/JS."""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
OUT = HERE.parent.parent / "Volksmond - Landing Page.html"
html = OUT.read_text(encoding="utf-8")
problems = []

# 1. External URLs: only digiphyte.com + schema.org allowed.
urls = re.findall(r"https?://[^\s\"')]+", html)
allowed = {"https://digiphyte.com", "https://schema.org"}
bad_urls = [u for u in urls if u not in allowed]
if bad_urls:
    problems.append(f"unexpected external URLs: {sorted(set(bad_urls))}")

# 2. Fonts embedded, no external font request.
n_face = html.count("@font-face")
n_data = html.count("data:font/woff2;base64")
if n_face != 7:
    problems.append(f"@font-face count = {n_face} (expected 7)")
if n_data != 7:
    problems.append(f"base64 font count = {n_data} (expected 7)")

# 3. Required SEO/AI blocks.
for needle in ('application/ld+json', '"SoftwareApplication"', 'property="og:title"',
               'name="twitter:card"', 'name="description"'):
    if needle not in html:
        problems.append(f"missing: {needle}")

# 4. Behaviour present.
for needle in ("window.VM", "setLang", "toggleTheme", "submitForm", "setPalette", "setLogo"):
    if needle not in html:
        problems.append(f"missing JS: {needle}")

# 5. EN/AF parity.
c_en, c_af = html.count("data-en="), html.count("data-af=")
c_enph, c_afph = html.count("data-en-ph="), html.count("data-af-ph=")
if c_en != c_af:
    problems.append(f"EN/AF copy parity off: data-en={c_en} data-af={c_af}")
if c_enph != c_afph:
    problems.append(f"EN/AF placeholder parity off: {c_enph} vs {c_afph}")

# 6. No em/en dashes in visible copy (strip base64 first).
stripped = re.sub(r"data:font/woff2;base64,[A-Za-z0-9+/=]+", "", html)
for dash, name in (("—", "em-dash"), ("–", "en-dash")):
    if dash in stripped:
        idx = stripped.index(dash)
        problems.append(f"{name} present: ...{stripped[idx-40:idx+40]!r}...")

# 7. Structural singletons (real-tag patterns; CSS comments can mention <html>).
for tag, n in (("<html lang", 1), ("</html>", 1), ("<body class", 1),
               ("</body>", 1), ("<main>", 1), ("</main>", 1)):
    if html.count(tag) != n:
        problems.append(f"tag {tag} count = {html.count(tag)} (expected {n})")

# 8. The Volksmond brand mark is inlined in the marks, and the old speaker mark is gone.
if html.count('class="mark"') < 3:
    problems.append("fewer than 3 marks")
if "1082.562539" not in html:
    problems.append("new Volksmond brand mark not inlined in marks")
if "M4 9.2h3.1L11.4 6v12" in html:
    problems.append("old speaker mark still present")

print("PROBLEMS:" if problems else "ALL CHECKS PASS")
for p in problems:
    print("  -", p)
print(f"\nstats: {len(html)//1024} KB, faces={n_face}, urls={sorted(set(urls))}, "
      f"data-en={c_en}, data-af={c_af}")

readable = re.sub(r"(data:font/woff2;base64,)[A-Za-z0-9+/=]+", r"\1<...elided...>", html)
(HERE / "readable.html").write_text(readable, encoding="utf-8")
print(f"readable copy: {HERE / 'readable.html'} ({len(readable)//1024} KB)")
