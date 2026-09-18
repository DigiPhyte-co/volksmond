"""1.14.2 task-3: the owner-approved "Open sound settings" route.

POST /api/open-sound-settings launches the Windows Sound settings page and nothing else. The target
is the fixed system URI ms-settings:sound (there is no user-supplied string, so it cannot be
steered), it is Windows only, and it is CSRF-protected like every other POST. These tests patch the
launcher so the real settings window is never opened.

Run:  python tests/test_open_sound_settings.py   (from the project root; exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


class _patch:
    """Temporarily set an attribute on an object, restoring the previous value (or removing it) after."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.had = hasattr(self.obj, self.name)
        self.prev = getattr(self.obj, self.name, None)
        setattr(self.obj, self.name, self.value)
        return self

    def __exit__(self, *exc):
        if self.had:
            setattr(self.obj, self.name, self.prev)
        else:
            try:
                delattr(self.obj, self.name)
            except Exception:
                pass


def test_opens_ms_settings_sound_on_windows():
    opened = []
    with _patch(webapp.sys, "platform", "win32"), _patch(webapp.os, "startfile", lambda t: opened.append(t)):
        r = client.post("/api/open-sound-settings")
    assert r.status_code == 200, r.text
    assert r.json() == {"opened": "ms-settings:sound"}
    assert opened == ["ms-settings:sound"], f"the launcher must be handed exactly the fixed URI, got {opened!r}"


def test_404_off_windows():
    with _patch(webapp.sys, "platform", "linux"):
        r = client.post("/api/open-sound-settings")
    assert r.status_code == 404, r.text


def test_requires_csrf_token():
    # No per-process token: the middleware refuses the POST before the route runs (defence in depth).
    bare = TestClient(app, base_url="http://localhost")
    r = bare.post("/api/open-sound-settings")
    assert r.status_code == 403, r.text


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)
