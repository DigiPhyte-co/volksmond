"""1.14.2 task-4: an Automatic wrong-sys-device switch keeps the session Automatic.

Once the watchdog's 2-per-60 s auto-switch budget is spent it stops switching by itself and raises
wrong-sys-device with `other` populated (device_policy.Watchdog.observe), so the big banner can offer
[Switch to {other}]. In Automatic mode the UI hands the session back to Automatic (device="auto")
rather than pinning `other`, so the live mode stays "auto" and the saved "auto" is never silently
converted to a named pick. This pins the backend half of that contract:

  * /api/switch-device with device="auto" leaves the live loopback mode "auto" and persists "auto";
  * a concrete device name still becomes a named pick and persists the name.

The device open itself is faked, so no audio device is touched and no session is started.

Run:  python tests/test_switch_device_mode.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe import config
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

# Plain-script runs do not load conftest, so redirect settings writes to a throwaway file here too;
# the owner's real settings.json must never be written by a test.
config._SETTINGS_PATH = Path(tempfile.mkdtemp(prefix="vm-switch-mode-")) / "settings.json"

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


def _fake_switch(which, device, **kw):
    """Stand in for the real capture rebuild: record the opener target and report a clean switch."""
    _fake_switch.calls.append((which, device))
    return {}


_fake_switch.calls = []


def _run(device, saved_before):
    """Drive one /api/switch-device on a loopback that starts in Automatic, with the device open and
    the (Windows) auto resolution both faked. Returns (response_json, saved_loopback_device_after)."""
    st = webapp.STATE
    saved_state = {f: getattr(st, f) for f in ("mic_mode", "loopback_mode", "device_notice")}
    prev = {
        "_switch_device": webapp._switch_device,
        "_resolve_one": webapp._resolve_one,
        "_probe_endpoints_detailed": webapp._probe_endpoints_detailed,
        "_sample_render_peaks": webapp._sample_render_peaks,
    }
    config.update({"loopback_device": saved_before, "loopback_device_id": ""})
    _fake_switch.calls = []
    try:
        st.mic_mode = "auto"
        st.loopback_mode = "auto"
        webapp._switch_device = _fake_switch
        # Deterministic, hardware-free resolution for the "auto" path (the Windows branch probes and
        # samples real endpoints otherwise); the policy would land on the playing render, `other`.
        webapp._resolve_one = lambda which, name, ident, eps, samples=None: ("Speakers [Loopback]", "id-1", "auto", False)
        webapp._probe_endpoints_detailed = lambda *a, **k: []
        webapp._sample_render_peaks = lambda *a, **k: {}
        r = client.post("/api/switch-device", json={"which": "loopback", "device": device})
        assert r.status_code == 200, r.text
        return r.json(), config.load().get("loopback_device")
    finally:
        for name, fn in prev.items():
            setattr(webapp, name, fn)
        for f, v in saved_state.items():
            setattr(st, f, v)


def test_auto_switch_keeps_mode_auto_and_saved_auto():
    resp, saved = _run("auto", saved_before="auto")
    assert resp["loopback_mode"] == "auto", resp
    assert saved == "auto", f"a hand-back to Automatic must not persist a named pick, got {saved!r}"


def test_named_switch_becomes_named_pick():
    resp, saved = _run("Speakers [Loopback]", saved_before="auto")
    assert resp["loopback_mode"] == "named", resp
    assert saved == "Speakers [Loopback]", f"a deliberate named switch must persist the name, got {saved!r}"


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
