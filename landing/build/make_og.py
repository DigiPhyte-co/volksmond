"""Generate the Volksmond site social image and apple-touch icon from the real brand
mark. Run:  python landing/build/make_og.py  (needs Pillow). Writes:
  site/assets/img/og.png              1200x630 social card
  site/assets/img/apple-touch-icon.png 180x180 home-screen icon
Rerun if the brand mark or the headline changes.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
IMG = ROOT / "site" / "assets" / "img"
MARK = ROOT / "brand" / "volksmond-mark-white.png"
BLUE = (54, 88, 123)            # #36587b, the brand accent
WHITE = (255, 255, 255)
SUBTLE = (214, 223, 233)        # light tint for the subline
SEGOE_B = r"C:\Windows\Fonts\segoeuib.ttf"
SEGOE = r"C:\Windows\Fonts\segoeui.ttf"


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def make_og():
    W, H, pad = 1200, 630, 92
    im = Image.new("RGB", (W, H), BLUE)
    d = ImageDraw.Draw(im)
    mark = Image.open(MARK).convert("RGBA")

    msize = 72
    mk = mark.resize((msize, msize))
    top = 150
    im.paste(mk, (pad, top), mk)
    d.text((pad + msize + 18, top + 14), "Volksmond", font=ImageFont.truetype(SEGOE_B, 40), fill=WHITE)

    h_font = ImageFont.truetype(SEGOE_B, 56)
    lines = wrap(d, "A private transcript of any meeting, on your own computer.", h_font, W - pad * 2 - 60)
    ty = top + msize + 46
    for ln in lines:
        d.text((pad, ty), ln, font=h_font, fill=WHITE)
        ty += 68

    d.text((pad, ty + 20),
           "Private, on-device transcription for Afrikaans, English, and the mix. By DigiPhyte.",
           font=ImageFont.truetype(SEGOE, 23), fill=SUBTLE)

    IMG.mkdir(parents=True, exist_ok=True)
    im.save(IMG / "og.png")
    print("wrote", IMG / "og.png")


def make_icon():
    S = 180
    im = Image.new("RGB", (S, S), BLUE)
    mark = Image.open(MARK).convert("RGBA")
    m = 116
    mk = mark.resize((m, m))
    im.paste(mk, ((S - m) // 2, (S - m) // 2), mk)
    im.save(IMG / "apple-touch-icon.png")
    print("wrote", IMG / "apple-touch-icon.png")


if __name__ == "__main__":
    make_og()
    make_icon()
