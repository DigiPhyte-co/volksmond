"""codex F5 (backend half): an absent remembered device must SURVIVE a start, not be overwritten.

The UI half sends null (not "auto") for a remembered pick that is currently unplugged; the backend
then migrates null to the saved setting, finds it absent, falls back to Automatic for this run, and
must NOT persist "auto" over the saved name, so the device comes back when it is reconnected. The
locked rule: an auto FALLBACK never overwrites a saved named device; only an explicit user choice of
Automatic persists "auto".

Hardware-free: the MMDevice probe is faked and no capture is started. sys.platform is pinned to
"win32" for the absent-resolution path (the role policy is Windows-only; off Windows a named pick
passes straight through, which is codex F6's separate concern).

Run:  python tests/test_remembered_device_persist.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _isolate_settings  # noqa: F401  redirect settings to a temp copy in script mode (codex F7)

from live_transcribe import config
from live_transcribe.web import app as webapp

# Endpoints that DO NOT include the saved mic: a different capture and a playing render, so the mic
# policy has something to fall back to and the saved "Samson C01U" resolves as absent.
_EPS_WITHOUT_SAMSON = [
    {"id": "cap-other", "name": "Other Mic", "flow": "capture", "form_factor": None,
     "muted": False, "peak_db": -20.0, "roles": ["multimedia", "communications"]},
    {"id": "ren-spk", "name": "Speakers", "flow": "render", "form_factor": None,
     "muted": False, "peak_db": -20.0, "roles": ["multimedia"]},
]


def _with_win32_and_fake_probe(fn):
    prev_platform = sys.platform
    prev_probe = webapp._probe_endpoints_detailed
    prev_sample = webapp._sample_render_peaks
    try:
        sys.platform = "win32"
        webapp._probe_endpoints_detailed = lambda *a, **k: list(_EPS_WITHOUT_SAMSON)
        webapp._sample_render_peaks = lambda *a, **k: {}
        return fn()
    finally:
        sys.platform = prev_platform
        webapp._probe_endpoints_detailed = prev_probe
        webapp._sample_render_peaks = prev_sample


def test_absent_remembered_mic_is_not_overwritten_by_a_null_request():
    # A saved named mic that is currently absent, and a UI that sends null (its remembered intent).
    config.update({"mic_device": "Samson C01U", "mic_device_id": "id-samson"})

    def body():
        sel = webapp._resolve_selection(None, "auto", None, "")  # mic null -> migrate to saved
        assert sel["mic_mode"] == "auto", sel
        assert sel["mic_absent"] is True, "an unplugged saved mic must resolve as absent"
        assert sel["notice"] and sel["notice"]["which"] == "mic", sel["notice"]
        webapp._persist_selection(sel["mic_mode"], sel["mic_name"], sel["mic_id"], sel["mic_absent"],
                                  sel["loop_mode"], sel["loop_name"], sel["loop_id"], sel["loop_absent"])

    _with_win32_and_fake_probe(body)
    assert config.load().get("mic_device") == "Samson C01U", \
        f"the saved mic must survive an absent-fallback start, got {config.load().get('mic_device')!r}"


def test_a_vanished_unsaved_pick_does_not_overwrite_a_different_saved_device():
    # codex G4: saved mic A; the user picked B, then B was unplugged before Begin. The UI now submits
    # B's NAME (not null), so the backend resolves B's absence (Automatic + a notice naming B) WITHOUT
    # migrating to, or overwriting, the different saved A. The session runs Automatic, and A survives.
    config.update({"mic_device": "Samson C01U", "mic_device_id": "id-samson"})   # saved A

    def body():
        sel = webapp._resolve_selection("Yeti X", "auto", None, "")   # the vanished, unsaved pick B
        assert sel["mic_mode"] == "auto" and sel["mic_absent"] is True, sel
        assert sel["notice"] == {"kind": "remembered-absent", "which": "mic", "wanted": "Yeti X"}, sel["notice"]
        webapp._persist_selection(sel["mic_mode"], sel["mic_name"], sel["mic_id"], sel["mic_absent"],
                                  sel["loop_mode"], sel["loop_name"], sel["loop_id"], sel["loop_absent"])

    _with_win32_and_fake_probe(body)
    assert config.load().get("mic_device") == "Samson C01U", \
        f"the different saved mic must NOT be overwritten by a vanished unsaved pick, got {config.load().get('mic_device')!r}"


def test_explicit_automatic_choice_does_persist_auto():
    # The user deliberately picks Automatic (an explicit "auto", not a null fallback): that DOES
    # overwrite the saved name, which is the intended distinction.
    config.update({"mic_device": "Samson C01U", "mic_device_id": "id-samson"})

    def body():
        sel = webapp._resolve_selection("auto", "auto", "", "")  # explicit auto for the mic
        assert sel["mic_mode"] == "auto" and sel["mic_absent"] is False, sel
        webapp._persist_selection(sel["mic_mode"], sel["mic_name"], sel["mic_id"], sel["mic_absent"],
                                  sel["loop_mode"], sel["loop_name"], sel["loop_id"], sel["loop_absent"])

    _with_win32_and_fake_probe(body)
    assert config.load().get("mic_device") == "auto", \
        f"an explicit Automatic choice must persist 'auto', got {config.load().get('mic_device')!r}"


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
