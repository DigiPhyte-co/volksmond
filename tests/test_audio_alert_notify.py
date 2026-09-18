"""codex F10: the Windows notification de-dupe must be per (kind, seq), not just the last seq.

Two red alerts of different kinds can alternate priority tick to tick (mic-muted momentarily takes
priority over an armed wrong-sys-device, then hands it back), each keeping its own seq across the
flap. Comparing only against the LAST notified seq re-fires a notification on every such transition,
which is exactly the flap the seq machinery exists to suppress. The fix tracks a per-session set of
notified (kind, seq), so each distinct alert notifies once for the life of the session.

Hardware-free: notify.show is faked and STATE is driven directly (no capture, no watchdog thread).

Run:  python tests/test_audio_alert_notify.py   (from the project root; exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _isolate_settings  # noqa: F401  redirect settings to a temp copy in script mode (codex F7)

from live_transcribe import notify
from live_transcribe.web import app as webapp


def _drive(alerts):
    """Publish each alert against one live session with notify.show counted. Returns the fire count."""
    st = webapp.STATE
    saved = {f: getattr(st, f) for f in ("capture", "started_at", "audio_alert", "audio_alert_notified")}
    fired = []
    orig_show = notify.show
    session = object()
    try:
        notify.show = lambda title, body, **kw: fired.append((title, body))
        st.capture = object()
        st.started_at = session
        st.audio_alert_notified = set()
        for a in alerts:
            webapp._publish_audio_alert(a, session)
    finally:
        notify.show = orig_show
        for f, v in saved.items():
            setattr(st, f, v)
    return len(fired)


def _red(kind, seq):
    return {"kind": kind, "severity": "red", "chosen": "X", "other": None, "seq": seq}


def test_alternating_reds_notify_once_each_not_on_every_transition():
    # mic-muted (seq 1) and wrong-sys-device (seq 2) trade priority for eight ticks, each keeping its
    # seq across the flap. Old code fired on every transition; the fix fires exactly twice.
    seq = []
    for _ in range(4):
        seq.append(_red("mic-muted", 1))
        seq.append(_red("wrong-sys-device", 2))
    assert _drive(seq) == 2, "alternating reds with kept seqs must notify once per (kind, seq)"


def test_a_genuinely_new_arming_still_notifies():
    # A fresh arm of the same kind (a new seq after a real disarm) is a new event and must notify.
    seq = [_red("mic-muted", 1), _red("mic-muted", 1), _red("mic-muted", 5)]
    assert _drive(seq) == 2, "seq 1 fires once, the later seq 5 is a new event and fires again"


def test_amber_never_notifies():
    amber = {"kind": "mic-quiet", "severity": "amber", "chosen": "X", "other": None, "seq": 1}
    assert _drive([amber, amber]) == 0, "an amber hint never fires a Windows notification"


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
