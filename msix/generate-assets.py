"""Generate the MSIX tile PNGs from the brand mark, for msix\\build-msix.ps1.

The Store's tile assets are conventionally FULL-BLEED and SQUARE-CORNERED (the shell
applies its own corner treatment), so unlike build-icon.py's rounded desktop icon these
tiles are a plain brand-blue square with the white mark centred on it: same mark, same
colours, same supersample-then-LANCZOS rendering, radius 0.

Run:  python msix/generate-assets.py <out_dir>    (from the project root)
Writes Square150x150Logo.png, Square44x44Logo.png and StoreLogo.png into <out_dir>.
"""
import os
import sys

from PIL import Image

MARK = os.path.join("brand", "volksmond-mark-white.png")  # white mark, transparent background
BG = (54, 88, 123, 255)   # #36587b, the Volksmond brand blue (matches build-icon.py)
MARK_FRACTION = 0.64      # the mark's larger dimension as a fraction of the tile
ASSETS = {
    "Square150x150Logo.png": 150,
    "Square44x44Logo.png": 44,
    "StoreLogo.png": 50,
}


def render(size):
    # Supersample 4x then downsample for crisp edges at small sizes (as build-icon.py does),
    # but on a full-bleed square: tile corners belong to the Store shell, not the asset.
    ss = size * 4
    tile = Image.new("RGBA", (ss, ss), BG)

    mark = Image.open(MARK).convert("RGBA")
    bbox = mark.getbbox()          # trim the transparent margin to size consistently
    if bbox:
        mark = mark.crop(bbox)
    mw, mh = mark.size
    scale = (ss * MARK_FRACTION) / max(mw, mh)
    mark = mark.resize((max(1, int(mw * scale)), max(1, int(mh * scale))), Image.LANCZOS)
    tile.alpha_composite(mark, ((ss - mark.width) // 2, (ss - mark.height) // 2))

    return tile.resize((size, size), Image.LANCZOS)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python msix/generate-assets.py <out_dir>")
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    for name, size in ASSETS.items():
        render(size).save(os.path.join(out_dir, name))
    print(f"Wrote {', '.join(ASSETS)} to {out_dir}")


if __name__ == "__main__":
    main()
