"""Unit tests for the edition identity: live_transcribe/edition.py and the two app icons.

Two things are under test.

1. The edition module. It reads its inputs ONCE at import (buildflags does the same, because
   the frozen editions set their env flags from a PyInstaller runtime hook before any app code
   runs), so each case runs in a SUBPROCESS with a controlled environment, exactly like
   tests/test_buildflags.py. sys.frozen is set in that subprocess before the import to stand in
   for a frozen bundle, which is precisely how PyInstaller marks one.

2. The icons themselves. The Fast Track icon must be the SAME picture as the original with the
   colours swapped, so: identical size table, a white background and a brand-blue mark, and the
   original still a blue background with a white mark. Sampled from the pixels, not asserted
   from the generator's constants, so a broken regeneration is caught.

Run:  python tests/test_edition.py   (from the project root; exit 0 = pass)
"""
import os
import subprocess
import sys

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BRAND_BLUE = (54, 88, 123)      # #36587b, sampled from brand/volksmond-mark-blue.svg
WHITE = (255, 255, 255)
EXPECTED_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _edition(extra_env, frozen=False):
    """(EDITION, DISPLAY_NAME, ICON_FILE, FAVICON_FILE) as a fresh interpreter sees them."""
    env = {k: v for k, v in os.environ.items() if k not in ("SA_LIVE_OFFLINE", "SA_LIVE_STORE")}
    env.update(extra_env)
    code = "import sys\n"
    if frozen:
        # What PyInstaller sets on a frozen bundle, and the only thing edition.py looks at to
        # tell an installed edition from a source run.
        code += "sys.frozen = True\n"
    code += ("from live_transcribe import edition\n"
             "print(edition.EDITION)\nprint(edition.DISPLAY_NAME)\n"
             "print(edition.ICON_FILE)\nprint(edition.FAVICON_FILE)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, env=env, timeout=300)
    assert out.returncode == 0, f"edition failed to import:\n{out.stdout}\n{out.stderr}"
    lines = out.stdout.strip().splitlines()
    return tuple(lines[-4:])


def test_source_run_reports_dev():
    # Not frozen and no edition flag: a dev run must not claim to be an installed edition, or a
    # dev window would be indistinguishable from an installed Fast Track window.
    ed, name, ico, fav = _edition({})
    assert ed == "dev", ed
    assert name == "Volksmond", name
    assert (ico, fav) == ("volksmond.ico", "favicon.svg"), (ico, fav)
    print("  OK  source run -> dev, plain name, original icon")


def test_frozen_no_flag_is_fast_track():
    # The connected edition: the direct download, frozen, with neither edition flag set.
    ed, name, ico, fav = _edition({}, frozen=True)
    assert ed == "connected", ed
    assert name == "Volksmond Fast Track", name
    assert ico == "volksmond-fasttrack.ico", ico
    assert fav == "favicon-fasttrack.svg", fav
    print("  OK  frozen, no flag -> connected, 'Volksmond Fast Track', inverted icon")


def test_store_edition_keeps_the_plain_name():
    # The Store edition is unchanged by this work: same name, same icon as before.
    ed, name, ico, fav = _edition({"SA_LIVE_STORE": "1"}, frozen=True)
    assert ed == "store", ed
    assert name == "Volksmond", name
    assert (ico, fav) == ("volksmond.ico", "favicon.svg"), (ico, fav)
    print("  OK  SA_LIVE_STORE=1 -> store, still 'Volksmond', original icon")


def test_offline_edition_keeps_the_plain_name():
    ed, name, ico, fav = _edition({"SA_LIVE_OFFLINE": "1"}, frozen=True)
    assert ed == "offline", ed
    assert name == "Volksmond", name
    assert (ico, fav) == ("volksmond.ico", "favicon.svg"), (ico, fav)
    print("  OK  SA_LIVE_OFFLINE=1 -> offline, still 'Volksmond', original icon")


def _tooltip(extra_env, frozen=False):
    """notify.TOOLTIP, the tray icon's hover text, as a fresh interpreter sees it."""
    env = {k: v for k, v in os.environ.items() if k not in ("SA_LIVE_OFFLINE", "SA_LIVE_STORE")}
    env.update(extra_env)
    code = "import sys\n"
    if frozen:
        code += "sys.frozen = True\n"
    code += "from live_transcribe import notify\nprint(notify.TOOLTIP)\n"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, env=env, timeout=300)
    assert out.returncode == 0, f"notify failed to import:\n{out.stdout}\n{out.stderr}"
    return out.stdout.strip().splitlines()[-1]


def test_tray_tooltip_follows_the_edition_name():
    """The tray icon is one of the places a user with both installs looks to tell them apart,
    and its hover text was still hard-coded to "Volksmond" in every edition."""
    assert _tooltip({}, frozen=True) == "Volksmond Fast Track", "the connected edition's tooltip"
    assert _tooltip({"SA_LIVE_STORE": "1"}, frozen=True) == "Volksmond", "the Store is unchanged"
    assert _tooltip({"SA_LIVE_OFFLINE": "1"}, frozen=True) == "Volksmond", "offline is unchanged"
    assert _tooltip({}) == "Volksmond", "a source run is nobody's install"
    print("  OK  the tray tooltip says Fast Track only on the connected edition")


def _page_title(extra_env, frozen=False):
    """The <title> brand_page() produces for a minimal index page, per edition."""
    env = {k: v for k, v in os.environ.items() if k not in ("SA_LIVE_OFFLINE", "SA_LIVE_STORE")}
    env.update(extra_env)
    code = "import sys\n"
    if frozen:
        code += "sys.frozen = True\n"
    code += ("from live_transcribe import edition\n"
             "page = '<head><title>Volksmond</title>"
             "<link rel=\"icon\" href=\"/assets/favicon.svg\" /></head>'\n"
             "print(edition.brand_page(page))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, env=env, timeout=300)
    assert out.returncode == 0, f"brand_page failed:\n{out.stdout}\n{out.stderr}"
    return out.stdout.strip().splitlines()[-1]


def test_brand_page_only_touches_the_connected_edition():
    connected = _page_title({}, frozen=True)
    assert "<title>Volksmond Fast Track</title>" in connected, connected
    assert "/assets/favicon-fasttrack.svg" in connected, connected
    for label, env in (("store", {"SA_LIVE_STORE": "1"}), ("offline", {"SA_LIVE_OFFLINE": "1"})):
        page = _page_title(env, frozen=True)
        assert "<title>Volksmond</title>" in page, (label, page)
        assert "/assets/favicon.svg" in page, (label, page)
        assert "fasttrack" not in page, (label, page)
    print("  OK  brand_page rebrands only the connected page; store/offline untouched")


# --- the icons -------------------------------------------------------------

def _ico(name):
    from PIL import Image
    path = os.path.join(ROOT, name)
    assert os.path.isfile(path), f"missing {name}; run `python build-icon.py` from the root"
    sizes = sorted(Image.open(path).info.get("sizes"))
    im = Image.open(path)
    im.size = (256, 256)          # pick the largest frame out of the .ico
    return sizes, im.convert("RGBA")


def _dominant(im):
    """The two most common fully opaque colours, commonest first: the tile background (which
    fills most of the icon) and then the mark."""
    from collections import Counter
    counts = Counter()
    for count, colour in im.getcolors(maxcolors=1 << 24):
        if colour[3] > 200:
            counts[colour[:3]] += count
    return counts.most_common(2)


def test_fasttrack_ico_has_the_same_sizes_as_the_original():
    original, _ = _ico("volksmond.ico")
    fast, _ = _ico("volksmond-fasttrack.ico")
    assert original == EXPECTED_SIZES, original
    assert fast == original, (fast, original)
    print(f"  OK  both icons carry the same sizes: {fast}")


def test_fasttrack_ico_is_the_original_inverted():
    _, orig = _ico("volksmond.ico")
    _, fast = _ico("volksmond-fasttrack.ico")
    (obg, _oc), (omark, _om) = _dominant(orig)
    (fbg, _fc), (fmark, _fm) = _dominant(fast)
    # The original: white mark on the brand blue. The Fast Track icon: exactly the other way up.
    assert obg == BRAND_BLUE, obg
    assert omark == WHITE, omark
    assert fbg == WHITE, f"the Fast Track background must be white, got {fbg}"
    assert fmark == BRAND_BLUE, f"the Fast Track mark must be brand blue, got {fmark}"
    # Same tile geometry: the rounded corner is transparent in both, and a point well inside the
    # tile edge is opaque background in both.
    for im in (orig, fast):
        assert im.getpixel((1, 1))[3] == 0, "the tile corner should be rounded (transparent)"
        assert im.getpixel((20, 128))[3] == 255, "the tile edge should be opaque"
    print(f"  OK  Fast Track icon inverted: white tile, mark #{'%02x%02x%02x' % fmark}")


def test_fasttrack_favicon_exists_and_keeps_the_brand_blue_artwork():
    static = os.path.join(ROOT, "live_transcribe", "web", "static")
    path = os.path.join(static, "favicon-fasttrack.svg")
    assert os.path.isfile(path), "missing favicon-fasttrack.svg; run `python build-icon.py`"
    with open(path, encoding="utf-8") as fh:
        svg = fh.read()
    with open(os.path.join(static, "favicon.svg"), encoding="utf-8") as fh:
        original = fh.read()
    assert 'fill="#ffffff"' in svg, "the Fast Track tab icon needs its white tile"
    assert "#36587b" in svg, "the mark must stay the brand blue"
    # Every path from the original artwork is present and none was added: the mark is copied,
    # not redrawn.
    assert svg.count("<path") == original.count("<path"), \
        "the artwork must be copied from favicon.svg unedited"
    assert svg.count("stroke=\"#36587b\"") == original.count("stroke=\"#36587b\""), \
        "every stroke must keep the brand blue"
    print("  OK  favicon-fasttrack.svg is the favicon artwork on a white tile")


if __name__ == "__main__":
    failures = 0
    for fn in (test_source_run_reports_dev,
               test_frozen_no_flag_is_fast_track,
               test_store_edition_keeps_the_plain_name,
               test_offline_edition_keeps_the_plain_name,
               test_tray_tooltip_follows_the_edition_name,
               test_brand_page_only_touches_the_connected_edition,
               test_fasttrack_ico_has_the_same_sizes_as_the_original,
               test_fasttrack_ico_is_the_original_inverted,
               test_fasttrack_favicon_exists_and_keeps_the_brand_blue_artwork):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll edition tests passed.")
