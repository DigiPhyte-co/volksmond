"""One-shot generator for the Volksmond app icons and the Fast Track favicon.

Composites the real Volksmond mark onto a rounded square and exports a multi-size .ico
(16/24/32/48/64/128/256), twice, in the two colourways the two Windows download channels use:

  volksmond.ico            white mark on a brand-blue tile. The original. Worn by the Store
                           edition, the offline edition and any source run.
  volksmond-fasttrack.ico  brand-blue mark on a white tile: the same geometry, the same
                           corner radius and the same padding, colours swapped. Worn by the
                           direct-download ("Volksmond Fast Track") edition, so it is
                           distinguishable from a Store install on the same machine.

Both use the real brand assets (brand/volksmond-mark-{white,blue}.png), so the icons always
match the logo exactly, and the two differ ONLY by colour. Also writes the matching Fast Track
browser-tab icon, live_transcribe/web/static/favicon-fasttrack.svg, by placing the untouched
favicon.svg artwork on the same white tile (see build_fasttrack_favicon below).

Rerun when the brand mark changes; commit the .ico files and the .svg. PyInstaller picks the
right .ico per edition via sa-live-transcribe.spec.

Run:  python build-icon.py    (from the project root)
"""
import os
import re

from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]

BLUE = (54, 88, 123, 255)   # #36587b, the Volksmond brand blue (Clinical accent)
WHITE = (255, 255, 255, 255)
TILE_RADIUS = 0.22        # corner radius as a fraction of the tile
MARK_FRACTION = 0.64      # the mark's larger dimension as a fraction of the tile

# (output, mark asset, tile colour). Identical geometry, inverted colours.
ICONS = [
    ("volksmond.ico", "brand/volksmond-mark-white.png", BLUE),
    ("volksmond-fasttrack.ico", "brand/volksmond-mark-blue.png", WHITE),
]

FAVICON_SRC = os.path.join("live_transcribe", "web", "static", "favicon.svg")
FAVICON_OUT = os.path.join("live_transcribe", "web", "static", "favicon-fasttrack.svg")


def render(size, mark_path, bg):
    # Supersample 4x then downsample for crisp edges at small sizes.
    ss = size * 4
    tile = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle(
        (0, 0, ss - 1, ss - 1), radius=int(ss * TILE_RADIUS), fill=bg)

    mark = Image.open(mark_path).convert("RGBA")
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


def build_icon(out, mark_path, bg):
    frames = [render(s, mark_path, bg) for s in SIZES]
    frames[-1].save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"Wrote {out} with sizes: {SIZES}")


def build_fasttrack_favicon(src=FAVICON_SRC, out=FAVICON_OUT):
    """Write the Fast Track browser-tab icon from favicon.svg, artwork untouched.

    favicon.svg is the brand-blue mark on a transparent background, so a plain colour swap
    would give a white mark on transparent: invisible on a light browser tab, and no use as a
    "which edition is this" cue. Instead the SAME artwork, with its brand-blue strokes exactly
    as they are, goes on the same white rounded tile the Fast Track .ico uses, so the tab icon
    and the taskbar icon are the same picture.

    The inner viewport reproduces the .ico geometry in SVG: a nested <svg> whose viewBox is the
    mark's ink bounding box, scaled into a centred MARK_FRACTION-sized box, over a rounded rect
    of TILE_RADIUS. No path data is touched, so the mark cannot drift from the brand asset.
    """
    with open(src, encoding="utf-8") as fh:
        svg = fh.read()
    # Strip the artwork's own outer <svg ...> wrapper; everything inside is copied verbatim.
    inner = re.sub(r"^\s*<svg\b[^>]*>", "", svg, count=1)
    inner = re.sub(r"</svg>\s*$", "", inner, count=1)

    tile = 1500                       # the artwork's own user-unit square
    radius = round(tile * TILE_RADIUS)
    box = round(tile * MARK_FRACTION)
    off = round((tile - box) / 2)
    # Ink bounding box of the mark, in artwork user units. Measured with Pillow from
    # brand/volksmond-mark-blue.png (a 6250 px render of the same 1500-unit artwork):
    # getbbox() -> (407, 1135, 5843, 5115), times 1500/6250.
    vb = "97.68 272.4 1304.64 955.2"
    out_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
        f' width="2000" height="2000" viewBox="0 0 {tile} {tile}" version="1.0">'
        "<!-- Volksmond Fast Track tab icon: the favicon.svg artwork, unedited, on the same"
        " white rounded tile as volksmond-fasttrack.ico. Generated by build-icon.py. -->"
        f'<rect width="{tile}" height="{tile}" rx="{radius}" ry="{radius}" fill="#ffffff"/>'
        f'<svg x="{off}" y="{off}" width="{box}" height="{box}" viewBox="{vb}"'
        ' preserveAspectRatio="xMidYMid meet" overflow="visible">'
        f"{inner}</svg></svg>\n"
    )
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out_svg)
    print(f"Wrote {out}")


def main():
    for out, mark_path, bg in ICONS:
        if not os.path.exists(mark_path):
            raise SystemExit(f"brand asset missing: {mark_path} (run from the project root)")
        build_icon(out, mark_path, bg)
    build_fasttrack_favicon()


if __name__ == "__main__":
    main()
