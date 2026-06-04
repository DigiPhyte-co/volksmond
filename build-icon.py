"""One-shot generator for the Volksmond app icon (volksmond.ico).

Composites the real Volksmond mark (white colourway) onto a rounded square in the
brand blue, and exports a multi-size .ico (16/24/32/48/64/128/256). Uses the real
brand asset (brand/volksmond-mark-white.png), so the icon always matches the logo
exactly. Rerun when the brand mark changes; commit the .ico. PyInstaller picks it
up via sa-live-transcribe.spec.

Run:  python build-icon.py    (from the project root; writes volksmond.ico)
"""
from PIL import Image, ImageDraw

OUT = "volksmond.ico"
MARK = "brand/volksmond-mark-white.png"   # white mark, transparent background
SIZES = [16, 24, 32, 48, 64, 128, 256]

BG = (54, 88, 123, 255)   # #36587b, the Volksmond brand blue (Clinical accent)
TILE_RADIUS = 0.22        # corner radius as a fraction of the tile
MARK_FRACTION = 0.64      # the mark's larger dimension as a fraction of the tile


def render(size):
    # Supersample 4x then downsample for crisp edges at small sizes.
    ss = size * 4
    tile = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle(
        (0, 0, ss - 1, ss - 1), radius=int(ss * TILE_RADIUS), fill=BG)

    mark = Image.open(MARK).convert("RGBA")
    bbox = mark.getbbox()          # trim the transparent margin to size consistently
    if bbox:
        mark = mark.crop(bbox)
    mw, mh = mark.size
    scale = (ss * MARK_FRACTION) / max(mw, mh)
    mark = mark.resize((max(1, int(mw * scale)), max(1, int(mh * scale))), Image.LANCZOS)
    ox = (ss - mark.width) // 2
    oy = (ss - mark.height) // 2
    tile.alpha_composite(mark, (ox, oy))

    return tile.resize((size, size), Image.LANCZOS)


def main():
    frames = [render(s) for s in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"Wrote {OUT} with sizes: {SIZES}")


if __name__ == "__main__":
    main()
