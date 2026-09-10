"""One-shot generator for the Volksmond app icons and the Fast Track favicon.

Composites the real Volksmond mark onto a rounded square and exports a multi-size .ico
(16/24/32/48/64/128/256), twice, in the two colourways the two Windows download channels use:

  volksmond.ico            white mark on a brand-blue tile. The original. Worn by the Store
                           edition, the offline edition and any source run.
  volksmond-fasttrack.ico  brand-blue mark on a white tile: the same geometry, the same
                           corner radius and the same padding, colours swapped. Worn by the
                           direct-download ("Volksmond Fast Track") edition, so it is
                           distinguishable from a Store install on the same machine.

It also exports the macOS app icon:

  volksmond.icns           the same white-on-blue mark as volksmond.ico, packed as a
                           multi-size .icns (16/32/64/128/256/512/1024, covering all 10 named
                           icon roles), so the app looks like the same product on both platforms.

Both .ico files use the real brand assets (brand/volksmond-mark-{white,blue}.png), so the icons
always match the logo exactly, and the two differ ONLY by colour. Also writes the matching Fast
Track browser-tab icon, live_transcribe/web/static/favicon-fasttrack.svg, by placing the untouched
favicon.svg artwork on the same white tile (see build_fasttrack_favicon below).

Windows: the .ico files and the .svg are committed to the repo. Rerun and commit when the brand
mark changes; PyInstaller picks the right .ico per edition via sa-live-transcribe.spec.

macOS: the .icns is NOT committed. It is generated at build time (mac/build-app-mac.sh locally,
the same script in CI via .github/workflows/mac-release.yml) so a binary artifact doesn't churn in
this OneDrive-synced git tree. volksmond-mac.spec picks it up by checking
os.path.exists("volksmond.icns") at build time.

Run:  python build-icon.py    (from the project root)
"""
import io
import os
import re
import struct

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

# macOS app icon. The .icns wears the primary colourway (white mark on brand blue), the same
# as volksmond.ico, so the Store, offline and Mac editions all look like the same product.
ICNS_OUT = "volksmond.icns"
ICNS_MARK = "brand/volksmond-mark-white.png"
ICNS_BG = BLUE
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]
# Apple's icns type codes, keyed by pixel size. Several sizes are shared by two named
# roles in a macOS .iconset (e.g. a 32px image is BOTH icon_16x16@2x and icon_32x32,
# byte-for-byte identical content under two type codes), so some sizes map to two codes.
# This table is the standard Apple Icon Services set (also what `iconutil -c icns` emits
# from a 10-file .iconset); Pillow's own icns reader (PIL/IcnsImagePlugin.py, IcnsFile.SIZES)
# recognises every code below.
ICNS_TYPES = {
    16: [b"icp4"],                # icon_16x16
    32: [b"icp5", b"ic11"],       # icon_32x32, icon_16x16@2x
    64: [b"ic12"],                # icon_32x32@2x
    128: [b"ic07"],               # icon_128x128
    256: [b"ic08", b"ic13"],      # icon_256x256, icon_128x128@2x
    512: [b"ic09", b"ic14"],      # icon_512x512, icon_256x256@2x
    1024: [b"ic10"],              # icon_512x512@2x
}


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


def write_icns(path, renders):
    """Pack per-size PNG renders into a .icns, written by hand rather than via
    Image.save(format="ICNS").

    Pillow can save ICNS on any platform (PIL/IcnsImagePlugin.py, "Allow saving on all
    operating systems", 2020-04-04), which is why this needed no Mac to develop or test.
    But its built-in writer only emits 8 of the 10 named icon roles: it skips icp4 and
    icp5 (the non-Retina icon_16x16 and icon_32x32 roles), so a plain
    im.save(path, format="ICNS") silently ships a mac icon with NO 16x16 image at all.
    This function packs the same container format (confirmed by reading it back with
    PIL/IcnsImagePlugin.IcnsFile below: magic, TOC block, length-prefixed data blocks)
    but with the full Apple type-code set from ICNS_TYPES, so every requested size is
    actually present.
    """
    HEADERSIZE = 8
    entries = []  # (type, declared_length, payload)
    png_cache = {}
    for size, types in ICNS_TYPES.items():
        if size not in png_cache:
            buf = io.BytesIO()
            renders[size].save(buf, format="PNG")
            png_cache[size] = buf.getvalue()
        payload = png_cache[size]
        for t in types:
            entries.append((t, HEADERSIZE + len(payload), payload))

    toc = bytearray(b"TOC ")
    toc += struct.pack(">I", HEADERSIZE + 8 * len(entries))
    for t, length, _ in entries:
        toc += t
        toc += struct.pack(">I", length)

    data = bytearray()
    for t, length, payload in entries:
        data += t
        data += struct.pack(">I", length)
        data += payload

    total_length = HEADERSIZE + len(toc) + len(data)
    with open(path, "wb") as fh:
        fh.write(b"icns")
        fh.write(struct.pack(">I", total_length))
        fh.write(toc)
        fh.write(data)


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

    # macOS app icon: white mark on brand blue, same colourway as volksmond.ico.
    if not os.path.exists(ICNS_MARK):
        raise SystemExit(f"brand asset missing: {ICNS_MARK} (run from the project root)")
    mac_renders = {s: render(s, ICNS_MARK, ICNS_BG) for s in ICNS_SIZES}
    write_icns(ICNS_OUT, mac_renders)
    print(f"Wrote {ICNS_OUT} with sizes: {ICNS_SIZES}")


if __name__ == "__main__":
    main()
