"""Smoke + regression tests for the web API, focused on the 2026-05-23 changes.

The Volksmond UI rebuild moved local AI summaries from Pro to Free (the design's
"Pro principle": Pro is only for things that need an online connection; anything
on-device stays free). It also added /api/app-info and split the frontend into
/assets. These tests pin that behaviour so it cannot silently regress.

No audio and no model load: the summarise check uses a bogus filename, which
returns 404 (transcript not found) BEFORE any model would load. That is exactly
what proves the Pro paywall is gone (a gated endpoint would 403 first).

Run:  python tests/test_web_api.py   (from the project root; exit 0 = pass)
"""
import os
import sys

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_transcribe import licensing
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

# The real UI always sends the CSRF token (app.js reads it from the page) and is
# served on loopback; mirror both so the existing tests exercise the endpoints,
# not the Host/CSRF guards (TestClient's default Host "testserver" would 400).
client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


def test_app_info():
    r = client.get("/api/app-info")
    assert r.status_code == 200, r.status_code
    j = r.json()
    for k in ("name", "version", "platform", "save_dir"):
        assert k in j and j[k], f"app-info missing {k}: {j}"
    print("  OK  /api/app-info returns name/version/platform/save_dir")


def test_summaries_are_free():
    # Local summaries must not be Pro-gated any more.
    assert "ai_summary" not in licensing.PRO_FEATURES, "ai_summary still in PRO_FEATURES"
    assert licensing.PRO_FEATURES == frozenset({"calendar"}), \
        f"PRO_FEATURES changed unexpectedly: {set(licensing.PRO_FEATURES)}"
    feats = client.get("/api/features").json()
    assert "ai_summary" not in feats["catalogue"]["pro"], feats
    print("  OK  summaries are free (ai_summary absent from PRO_FEATURES and /api/features)")


def test_summarise_not_pro_gated():
    # A free user (no licence) hitting summarise with a bogus file must get 404
    # (transcript not found), NOT 403 (Pro). That proves the paywall is gone and
    # nothing loads a model along the way.
    r = client.post("/api/summarise", json={"file": "does-not-exist-xyz.md"})
    assert r.status_code != 403, "summarise is still Pro-gated (403)"
    assert r.status_code == 404, f"expected 404 for missing transcript, got {r.status_code}: {r.text}"
    assert "Pro" not in r.json().get("detail", ""), r.json()
    print("  OK  /api/summarise is not Pro-gated (bogus file -> 404, not 403)")


def test_settings_never_leak_secret():
    j = client.get("/api/settings").json()
    assert "cloud_api_key" not in j, "secret cloud_api_key leaked in GET /api/settings"
    assert "has_cloud_api_key" in j, "presence flag missing from settings"
    print("  OK  GET /api/settings exposes the presence flag, never the secret")


def test_static_assets_served():
    assert client.get("/").status_code == 200, "index did not serve"
    assert client.get("/assets/app.js").status_code == 200, "app.js did not serve"
    assert client.get("/assets/styles.css").status_code == 200, "styles.css did not serve"
    print("  OK  index + /assets/app.js + /assets/styles.css all serve")


def test_csrf_blocks_unsafe_requests():
    # A page without the token (e.g. a random site firing a simple cross-origin
    # POST at localhost) must be rejected on any state-changing method.
    bare = TestClient(app, base_url="http://localhost")
    r = bare.post("/api/stop?what=all")
    assert r.status_code == 403, f"unsafe POST without CSRF token should be 403, got {r.status_code}"
    # GETs are safe and must still work without the token.
    assert bare.get("/api/app-info").status_code == 200, "GET should not require the CSRF token"
    # The token is handed to the page so the real UI can echo it.
    assert 'name="vm-csrf"' in client.get("/").text, "index does not embed the CSRF token"
    print("  OK  CSRF: unsafe POST blocked without token; GET open; token embedded in page")


def test_unique_transcript_filenames():
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    orig = webapp._sessions_dir
    webapp._sessions_dir = lambda: tmp
    try:
        p1 = webapp._build_output_path("Collision Test")
        p1.write_text("first meeting", encoding="utf-8")   # meeting A is on disk
        p2 = webapp._build_output_path("Collision Test")
        assert p2 != p1, "second transcript reused the first path (meetings would merge)"
        assert not p2.exists(), "second path already exists on disk"
    finally:
        webapp._sessions_dir = orig
    print("  OK  two same-topic sessions never share a transcript file")


def test_filename_allow_list():
    bad = ["../secret.md", "a/b.md", "a\\b.md", "CON.md", "CON.foo.md", "COM1.report.md",
           "report.txt", "stream.md:hidden", "", "..", "trans\x00.md"]
    for name in bad:
        try:
            webapp._validate_session_filename(name)
            assert False, f"accepted invalid filename: {name!r}"
        except HTTPException as e:
            assert e.status_code == 400, (name, e.status_code)
    webapp._validate_session_filename("2026-05-23-120000-weekly-standup.md")  # a normal name passes
    # The summarise endpoint rejects traversal with 400 (not 404/500).
    r = client.post("/api/summarise", json={"file": "../secret.md"})
    assert r.status_code == 400, f"summarise should 400 on a bad filename, got {r.status_code}"
    print("  OK  filename allow-list rejects traversal, ADS, reserved names, non-.md")


def test_license_pubkey_precedence():
    # A baked-in key must win, so a shipped build can't be pointed at another key
    # via SA_LIVE_LICENSE_PUBKEY to self-sign Pro.
    orig_baked = licensing._PUBLIC_KEY_HEX
    orig_env = os.environ.get("SA_LIVE_LICENSE_PUBKEY")
    try:
        licensing._PUBLIC_KEY_HEX = "baked"
        os.environ["SA_LIVE_LICENSE_PUBKEY"] = "attacker"
        assert licensing._pubkey_hex() == "baked", "env var overrode a baked-in key"
        licensing._PUBLIC_KEY_HEX = ""        # dev/test: no key shipped
        assert licensing._pubkey_hex() == "attacker", "env var ignored when nothing baked"
    finally:
        licensing._PUBLIC_KEY_HEX = orig_baked
        if orig_env is None:
            os.environ.pop("SA_LIVE_LICENSE_PUBKEY", None)
        else:
            os.environ["SA_LIVE_LICENSE_PUBKEY"] = orig_env
    print("  OK  licence pubkey: baked-in wins; env override only when none baked")


def test_host_rebinding_blocked():
    # DNS-rebinding defence: a non-loopback Host is rejected before anything is
    # served, including the page that carries the CSRF token.
    evil = TestClient(app, base_url="http://attacker.example:8765")
    assert evil.get("/api/app-info").status_code == 400, "non-loopback Host not rejected"
    assert evil.get("/").status_code == 400, "token page served to a non-loopback Host"
    # Loopback Host is served normally (the shared client uses http://localhost).
    assert client.get("/api/app-info").status_code == 200
    print("  OK  non-loopback Host rejected (DNS-rebinding defence); loopback served")


if __name__ == "__main__":
    failures = 0
    for fn in (test_app_info,
               test_summaries_are_free,
               test_summarise_not_pro_gated,
               test_settings_never_leak_secret,
               test_static_assets_served,
               test_csrf_blocks_unsafe_requests,
               test_host_rebinding_blocked,
               test_unique_transcript_filenames,
               test_filename_allow_list,
               test_license_pubkey_precedence):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll web-API tests passed.")
