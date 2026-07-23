"""Tests for the manifest parsing in updatecheck.py.

latest.json is ONE manifest with a platform key: the top level is the Windows entry
(shipped Windows clients parse only top-level version/url and ignore unknown keys), and
the mac build reads the "mac" object (the linux build reads "linux" the same way),
falling back to the top level when the key is absent. No network: urllib.request.urlopen
is swapped for an in-memory response, and sys.platform is patched per test.

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
    "linux": {
        "version": "1.12.0",
        "url": "https://volksmond.digiphyte.com/linux",
        "notes": "linux notes",
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


def test_linux_reads_linux_key():
    r = _check_with("linux", _MANIFEST_BOTH)
    assert r["latest"] == "1.12.0", r
    assert r["url"] == "https://volksmond.digiphyte.com/linux", r
    assert r["update_available"] is True, r
    print("  OK  linux reads the linux entry (version + url) when the key is present")


def test_linux_falls_back_to_top_level():
    r = _check_with("linux", _MANIFEST_WIN_ONLY)
    assert r["latest"] == "1.10.0", r
    assert r["url"] == "https://volksmond.digiphyte.com/", r
    assert r["update_available"] is True, r
    print("  OK  linux falls back to the top-level entry when there is no linux key")


def test_linux_non_dict_linux_key_falls_back():
    bad = dict(_MANIFEST_WIN_ONLY)
    bad["linux"] = "not-an-object"
    r = _check_with("linux", bad)
    assert r["latest"] == "1.10.0", r
    assert r["url"] == "https://volksmond.digiphyte.com/", r
    print("  OK  linux falls back to the top level when the linux key is not an object")


def test_win32_ignores_linux_key():
    r = _check_with("win32", _MANIFEST_BOTH)
    assert r["latest"] == "1.10.0", r
    assert r["url"] == "https://volksmond.digiphyte.com/", r
    print("  OK  win32 parses only the top-level entry and ignores the linux key too")


def test_darwin_ignores_linux_key():
    r = _check_with("darwin", _MANIFEST_BOTH)
    assert r["latest"] == "1.11.0", r
    assert r["url"] == "https://volksmond.digiphyte.com/mac", r
    print("  OK  darwin reads the mac entry and ignores the linux key")


def test_up_to_date_no_update():
    r = _check_with("darwin", _MANIFEST_BOTH, current="1.11.0")
    assert r["update_available"] is False, r
    print("  OK  darwin reports no update when already on the mac manifest version")


def test_up_to_date_no_update_linux():
    r = _check_with("linux", _MANIFEST_BOTH, current="1.12.0")
    assert r["update_available"] is False, r
    print("  OK  linux reports no update when already on the linux manifest version")


def _assert_malformed(platform, manifest, label):
    """The manifest must raise UpdateCheckError (the API's normal error shape), not blow up."""
    try:
        _check_with(platform, manifest)
    except updatecheck.UpdateCheckError as e:
        assert "malformed update manifest" in str(e), e
        return
    raise AssertionError(f"{label}: expected UpdateCheckError, got a result")


def test_malformed_non_string_version_raises():
    bad = dict(_MANIFEST_WIN_ONLY)
    bad["version"] = 123
    _assert_malformed("win32", bad, "non-string version")
    print("  OK  a non-string version raises UpdateCheckError (no 500)")


def test_malformed_manifest_not_a_dict_raises():
    for platform in ("win32", "darwin", "linux"):
        _assert_malformed(platform, ["not", "a", "dict"], f"non-dict manifest on {platform}")
    print("  OK  a non-dict manifest raises UpdateCheckError on win32, darwin and linux")


def test_darwin_mac_key_not_a_dict_with_bad_top_level_raises():
    # mac key not a dict -> fall back to the top level; the fallback entry is validated too,
    # so a top level missing its url still raises rather than returning junk.
    _assert_malformed("darwin", {"version": "1.10.0", "mac": "not-an-object"},
                      "mac not a dict, top level missing url")
    print("  OK  darwin: non-dict mac key with a malformed top level raises UpdateCheckError")


def test_linux_key_not_a_dict_with_bad_top_level_raises():
    # linux key not a dict -> fall back to the top level; the fallback entry is validated too,
    # so a top level missing its url still raises rather than returning junk.
    _assert_malformed("linux", {"version": "1.10.0", "linux": "not-an-object"},
                      "linux not a dict, top level missing url")
    print("  OK  linux: non-dict linux key with a malformed top level raises UpdateCheckError")


def test_malformed_linux_entry_non_string_version_raises():
    bad = dict(_MANIFEST_WIN_ONLY)
    bad["linux"] = {"version": 123, "url": "https://volksmond.digiphyte.com/linux"}
    _assert_malformed("linux", bad, "linux entry non-string version")
    print("  OK  a malformed linux entry (non-string version) raises UpdateCheckError")


def test_malformed_linux_entry_url_missing_raises():
    bad = dict(_MANIFEST_WIN_ONLY)
    bad["linux"] = {"version": "1.12.0"}
    _assert_malformed("linux", bad, "linux entry missing url")
    print("  OK  a malformed linux entry (missing url) raises UpdateCheckError")


def test_malformed_url_missing_raises():
    bad = dict(_MANIFEST_WIN_ONLY)
    del bad["url"]
    _assert_malformed("win32", bad, "url missing")
    print("  OK  a manifest without a url raises UpdateCheckError (no silent fallback)")


if __name__ == "__main__":
    failures = 0
    for fn in (test_darwin_reads_mac_key,
               test_darwin_falls_back_to_top_level,
               test_win32_ignores_mac_key,
               test_darwin_non_dict_mac_key_falls_back,
               test_linux_reads_linux_key,
               test_linux_falls_back_to_top_level,
               test_linux_non_dict_linux_key_falls_back,
               test_win32_ignores_linux_key,
               test_darwin_ignores_linux_key,
               test_up_to_date_no_update,
               test_up_to_date_no_update_linux,
               test_malformed_non_string_version_raises,
               test_malformed_manifest_not_a_dict_raises,
               test_darwin_mac_key_not_a_dict_with_bad_top_level_raises,
               test_linux_key_not_a_dict_with_bad_top_level_raises,
               test_malformed_linux_entry_non_string_version_raises,
               test_malformed_linux_entry_url_missing_raises,
               test_malformed_url_missing_raises):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll updatecheck tests passed.")
