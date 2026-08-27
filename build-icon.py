"""One-shot generator for the Volksmond app icons (volksmond.ico and volksmond.icns).

Composites the real Volksmond mark (white colourway) onto a rounded square in the
brand blue, and exports:
  - a multi-size Windows .ico (16/24/32/48/64/128/256), and
  - a multi-size macOS .icns (16/32/64/128/256/512/1024, covering all 10 named
    icon roles: icon_16x16 through icon_512x512@2x).
Both use the real brand asset (brand/volksmond-mark-white.png), so the icons always
match the logo exactly and look like the same product on both platforms.

Windows: the .ico is committed to the repo (rerun and commit when the brand mark
changes). PyInstaller picks it up via sa-live-transcribe.spec.

macOS: the .icns is NOT committed. It is generated at build time (mac/build-app-mac.sh
locally, the same script in CI via .github/workflows/mac-release.yml) so a binary
artifact doesn't churn in this OneDrive-synced git tree. volksmond-mac.spec picks it up
by checking os.path.exists("volksmond.icns") at build time.

Run:  python build-icon.py    (from the project root; writes both files)
"""
import io
import struct

from PIL import Image, ImageDraw

OUT = "volksmond.ico"
MARK = "brand/volksmond-mark-white.png"   # white mark, transparent background
SIZES = [16, 24, 32, 48, 64, 128, 256]

ICNS_OUT = "volksmond.icns"
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


def main():
    frames = [render(s) for s in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"Wrote {OUT} with sizes: {SIZES}")

    mac_renders = {s: render(s) for s in ICNS_SIZES}
    write_icns(ICNS_OUT, mac_renders)
    print(f"Wrote {ICNS_OUT} with sizes: {ICNS_SIZES}")


if __name__ == "__main__":
    main()
