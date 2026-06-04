"""Fetch IBM Plex (latin subset) woff2 from Google Fonts and emit self-hosted
@font-face rules with the binaries base64-embedded. This is a BUILD step: it runs
once here so the shipped landing page carries the fonts inline and never calls
Google at view time (the whole point of a privacy product's page)."""
import base64
import re
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Only the families+weights the landing page actually uses.
CSS_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=IBM+Plex+Sans:wght@400;500;600"
    "&family=IBM+Plex+Serif:wght@600"
    "&family=IBM+Plex+Mono:wght@400;500;600"
    "&display=swap"
)


def get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read() if binary else r.read().decode("utf-8")


def main():
    css = get(CSS_URL)
    # Split into @font-face blocks, each preceded by a /* subset */ comment.
    blocks = re.split(r"(/\*[^*]+\*/)\n", css)
    # blocks = ['', '/* cyrillic */', '@font-face{...}\n', '/* latin */', '@font-face{...}', ...]
    out = []
    total = 0
    pairs = list(zip(blocks[1::2], blocks[2::2]))
    for comment, body in pairs:
        if "latin" not in comment or "latin-ext" in comment:
            continue  # latin only (covers Afrikaans diacritics U+00C0-00FF)
        fam = re.search(r"font-family:\s*'([^']+)'", body).group(1)
        wght = re.search(r"font-weight:\s*(\d+)", body).group(1)
        style = re.search(r"font-style:\s*(\w+)", body)
        style = style.group(1) if style else "normal"
        url = re.search(r"src:\s*url\(([^)]+)\)", body).group(1)
        woff2 = get(url, binary=True)
        total += len(woff2)
        b64 = base64.b64encode(woff2).decode("ascii")
        out.append(
            f"@font-face{{font-family:'{fam}';font-style:{style};"
            f"font-weight:{wght};font-display:swap;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
        )
        print(f"  {fam} {wght} {style}: {len(woff2)//1024} KB woff2")
    with open("_fontface.css", "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"\n{len(out)} faces, {total//1024} KB raw woff2 "
          f"(~{int(total*1.34)//1024} KB base64) -> _fontface.css")


if __name__ == "__main__":
    main()
