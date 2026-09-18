"""codex F6: off Windows a remembered named device must be validated, not passed through unchecked.

On macOS (and any non-Windows backend) a saved named mic that is currently absent used to be handed
straight to capture, which raised and made Begin fail. _resolve_one must instead check the saved name
against the platform's live /api/devices listing and, when it is gone, fall back to Automatic (None)
with the remembered-absent notice so the session still starts. When the listing cannot be read the
name passes through unchanged (no false "absent").

Hardware-free: sys.platform is pinned and devices.list_ui_devices is faked.

Run:  python tests/test_remembered_device_nonwin.py   (from the project root; exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _isolate_settings  # noqa: F401  redirect settings to a temp copy in script mode (codex F7)

from live_transcribe import config, devices
from live_transcribe.web import app as webapp

_MAC_LISTING = {
    "loopbacks": [{"index": -1, "name": "System audio (everything this Mac plays)", "rate": 16000}],
    "mics": [{"index": 0, "name": "MacBook Pro Microphone", "rate": 48000}],
}


def _on_platform(platform, listing, fn):
    prev_platform = sys.platform
    prev_list = devices.list_ui_devices
    try:
        sys.platform = platform
        devices.list_ui_devices = (lambda: listing) if listing is not None else _raise
        return fn()
    finally:
        sys.platform = prev_platform
        devices.list_ui_devices = prev_list


def _raise():
    raise RuntimeError("no enumeration backend on this platform")


def test_absent_mac_mic_falls_back_to_automatic_with_a_notice():
    def body():
        name, dev_id, mode, absent = webapp._resolve_one("mic", "Samson C01U", "id-x", [])
        assert (name, mode, absent) == (None, "auto", True), (name, mode, absent)
    _on_platform("darwin", _MAC_LISTING, body)


def test_present_mac_mic_stays_named():
    def body():
        name, dev_id, mode, absent = webapp._resolve_one("mic", "MacBook Pro Microphone", "", [])
        assert (name, mode, absent) == ("MacBook Pro Microphone", "named", False), (name, mode, absent)
    _on_platform("darwin", _MAC_LISTING, body)


def test_present_mac_synthetic_loopback_stays_named():
    def body():
        name, dev_id, mode, absent = webapp._resolve_one(
            "loopback", "System audio (everything this Mac plays)", "", [])
        assert mode == "named" and absent is False, (name, mode, absent)
    _on_platform("darwin", _MAC_LISTING, body)


def test_unreadable_listing_passes_the_name_through():
    # No backend / enumeration failure (e.g. linux without the port wired): assume present, do not
    # wrongly declare the saved device absent.
    def body():
        name, dev_id, mode, absent = webapp._resolve_one("mic", "Some USB Mic", "id-y", [])
        assert (name, mode, absent) == ("Some USB Mic", "named", False), (name, mode, absent)
    _on_platform("linux", None, body)


def test_resolve_selection_marks_the_notice_and_persist_keeps_the_saved_mic():
    config.update({"mic_device": "Samson C01U", "mic_device_id": "id-x"})

    def body():
        sel = webapp._resolve_selection(None, "auto", None, "")   # mic null -> migrate to saved name
        assert sel["mic_absent"] is True and sel["notice"]["which"] == "mic", sel
        webapp._persist_selection(sel["mic_mode"], sel["mic_name"], sel["mic_id"], sel["mic_absent"],
                                  sel["loop_mode"], sel["loop_name"], sel["loop_id"], sel["loop_absent"])
    _on_platform("darwin", _MAC_LISTING, body)
    assert config.load().get("mic_device") == "Samson C01U", config.load().get("mic_device")


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
