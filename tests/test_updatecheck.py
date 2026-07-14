"""Tests for the manifest parsing in updatecheck.py.

latest.json is ONE manifest with a platform key: the top level is the Windows entry
(shipped Windows clients parse only top-level version/url and ignore unknown keys), and
the mac build reads the "mac" object, falling back to the top level when the key is
absent. No network: urllib.request.urlopen is swapped for an in-memory response, and
sys.platform is patched per test.

Run:  python tests/test_updatecheck.py   (from the project root; exit 0 = pass)
"""
import json
import os
import sys
import urllib.request

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import updatecheck


class _FakeResp:
    """Minimal stand-in for the urlopen response context manager."""
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _check_with(platform, manifest, current="1.0.0"):
    """Run updatecheck.check(current) as if on `platform` against `manifest`."""
    orig_urlopen = urllib.request.urlopen
    orig_platform = sys.platform
    urllib.request.urlopen = lambda *a, **k: _FakeResp(manifest)
    sys.platform = platform
    try:
        return updatecheck.check(current)
    finally:
        urllib.request.urlopen = orig_urlopen
        sys.platform = orig_platform


_MANIFEST_BOTH = {
    "version": "1.10.0",
    "url": "https://volksmond.digiphyte.com/",
    "notes": "windows notes",
    "mac": {
        "version": "1.11.0",
        "url": "https://volksmond.digiphyte.com/mac",
        "notes": "mac notes",
    },
}

_MANIFEST_WIN_ONLY = {
    "version": "1.10.0",
    "url": "https://volksmond.digiphyte.com/",
    "notes": "windows notes",
}


def test_darwin_reads_mac_key():
    r = _check_with("darwin", _MANIFEST_BOTH)
    assert r["latest"] == "1.11.0", r
    assert r["url"] == "https://volksmond.digiphyte.com/mac", r
    assert r["update_available"] is True, r
    print("  OK  darwin reads the mac entry (version + url) when the key is present")


def test_darwin_falls_back_to_top_level():
    r = _check_with("darwin", _MANIFEST_WIN_ONLY)
    assert r["latest"] == "1.10.0", r
    assert r["url"] == "https://volksmond.digiphyte.com/", r
    assert r["update_available"] is True, r
    print("  OK  darwin falls back to the top-level entry when there is no mac key")


def test_win32_ignores_mac_key():
    r = _check_with("win32", _MANIFEST_BOTH)
    assert r["latest"] == "1.10.0", r
    assert r["url"] == "https://volksmond.digiphyte.com/", r
    assert r["update_available"] is True, r
    print("  OK  win32 parses only the top-level entry and ignores the mac key")


def test_darwin_non_dict_mac_key_falls_back():
    bad = dict(_MANIFEST_WIN_ONLY)
    bad["mac"] = "not-an-object"
    r = _check_with("darwin", bad)
    assert r["latest"] == "1.10.0", r
    assert r["url"] == "https://volksmond.digiphyte.com/", r
    print("  OK  darwin falls back to the top level when the mac key is not an object")


def test_up_to_date_no_update():
    r = _check_with("darwin", _MANIFEST_BOTH, current="1.11.0")
    assert r["update_available"] is False, r
    print("  OK  darwin reports no update when already on the mac manifest version")


if __name__ == "__main__":
    failures = 0
    for fn in (test_darwin_reads_mac_key,
               test_darwin_falls_back_to_top_level,
               test_win32_ignores_mac_key,
               test_darwin_non_dict_mac_key_falls_back,
               test_up_to_date_no_update):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll updatecheck tests passed.")
