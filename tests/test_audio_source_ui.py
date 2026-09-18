"""Structural guards for the WP5 (1.14.2) audio-source UI: the Automatic-first pickers, the
remembered-pick handling, and the big audio warnings.

These are static checks over the browser bundle (app.js, i18n.js, styles.css), in the same spirit as
test_recording_default.py: no browser, no DOM. They pin the load-bearing pieces of behaviour so a
later refactor cannot quietly drop them, and they enforce the two house rules that a static bundle
can carry: every new user-visible English string has an Afrikaans translation, and no em or en dash
appears in either language.

Run:  python tests/test_audio_source_ui.py   (from the project root; exit 0 = pass)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "live_transcribe" / "web" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
I18N_JS = (STATIC / "i18n.js").read_text(encoding="utf-8")
STYLES = (STATIC / "styles.css").read_text(encoding="utf-8")

# The new English source strings this work package introduced. Each must key an Afrikaans value in
# i18n.js so the whole feature renders in Afrikaans, and none may carry a dash the house style bans.
NEW_STRINGS = [
    "Automatic (recommended): {d}",
    "Automatic (recommended)",
    "More microphones",
    "More outputs",
    "{p} is not connected. Using Automatic for now.",
    "{p} is no longer available. Using Automatic.",
    "Using {p} again.",
    "Switch to {d}",
    "Keep {d}",
    "System audio moved to {d} (that is where sound is playing)",
    "Volksmond may be on the wrong output",
    "We can't hear the other side. Sound is playing on {other}, but Volksmond is listening to {chosen}.",
    "System audio stopped",
    "System audio stopped arriving from {chosen}. We restarted it; if this stays, pick another output.",
    "Your microphone is muted",
    "Your microphone is muted in Windows. Nothing you say is being captured.",
    "No sound from your microphone",
    "Your microphone is sending no sound at all. Check it is plugged in, or pick another one.",
    "Your microphone is very quiet",
    "Your microphone is very quiet. Move closer or raise its level in Windows.",
    "Open sound settings",
]


def test_pickers_offer_automatic_first_and_group_junk():
    checks = [
        ('function sourceOptions(dev, which)',
         "one shared builder puts Automatic first and groups junk under a separator"),
        ('trFmt("Automatic (recommended): {d}"',
         "the Automatic option must show the resolved device it would pick"),
        ('which === "mic" ? "More microphones" : "More outputs"',
         "junk devices must sit under a 'More microphones' / 'More outputs' separator"),
        ('dev.auto_mic_name', "the mic picker must read the backend's live auto pick"),
        ('dev.auto_loopback_name', "the loopback picker must read the backend's live auto pick"),
        ('function seedFormSource(dev, which)',
         "boot must seed from the saved mode + remembered name, not the Windows default"),
    ]
    for needle, why in checks:
        assert needle in APP_JS, f"app.js: {why} (missing {needle!r})"
    # The default-index seeding must be gone: the picker follows mode + name now.
    assert "deviceDefaultName" not in APP_JS, \
        "app.js must not seed or reconcile from the Windows default index any more"


def test_remembered_pick_survives_and_falls_back_to_auto():
    checks = [
        ('function formDeviceValue(which)',
         "an absent remembered pick must submit null so the backend keeps it, not overwrite with auto"),
        ('return null;',
         "formDeviceValue returns null for an absent remembered pick"),
        ('{p} is not connected. Using Automatic for now.',
         "the start form must show the remembered-absent note beside the field"),
        ('S.form[pickKey] = DEVICE_AUTO;',
         "a vanished present pick must fall back to Automatic, not to a default device name"),
    ]
    for needle, why in checks:
        assert needle in APP_JS, f"app.js: {why} (missing {needle!r})"


def test_vanished_pick_preserves_the_wanted_name_so_the_saved_device_survives():
    # codex F5: when a present named pick vanishes (unplugged before Begin), reconcileSource must
    # record it in *Wanted before falling back to Automatic, so formDeviceValue sends null (keeping
    # the saved device) rather than "auto" (which the backend would persist over the saved name).
    import re
    m = re.search(r"function reconcileSource\(dev, which, doToast\)\s*\{(.*?)\n\}", APP_JS, re.S)
    assert m, "reconcileSource not found"
    body = m.group(1)
    vanish = body[body.index("is no longer available"):]
    assert "S.form[wantedKey] = cur;" in vanish, \
        "the vanish branch must preserve the pick in *Wanted (codex F5), else the saved device is lost"
    assert vanish.index("S.form[wantedKey] = cur;") < vanish.index("S.form[pickKey] = DEVICE_AUTO;"), \
        "the wanted name must be recorded before the pick is reset to Automatic"


def test_switch_device_can_hand_back_to_automatic():
    # A live switch to "auto" hands the source back to the policy; the response modes are adopted.
    assert 'var isAuto = (value === DEVICE_AUTO);' in APP_JS, \
        "switchDevice must recognise the Automatic value"
    assert 'if (resp.mic_mode) S.live.micMode = resp.mic_mode;' in APP_JS, \
        "switchDevice must adopt the authoritative mode from the switch response"


def test_big_red_alert_and_amber_hint_render_from_audio_alert():
    checks = [
        ('function bigAudioAlert(a)', "the red alerts render a dedicated big banner"),
        ('a.kind === "wrong-sys-device"', "wrong-sys-device is handled"),
        ('a.kind === "sys-capture-fault"', "sys-capture-fault is handled"),
        ('a.kind === "mic-muted"', "mic-muted is handled"),
        ('a.kind === "mic-flat"', "mic-flat is handled"),
        ('function audioQuietBanner(a)', "the amber mic-quiet hint has its own hint-sized banner"),
        ('api.post("/api/audio-alert/dismiss")', "the amber hint is dismissed through the backend"),
        ('switchDevice("loopback", a.other + LOOPBACK_SUFFIX)',
         "a named wrong-sys-device switch must re-add the loopback suffix the devices list uses"),
        ('S.live.loopbackMode === "auto"',
         "an Automatic wrong-sys-device switch must hand back to Automatic, not pin a named pick"),
        ('switchDevice("loopback", "auto")',
         "the Automatic branch re-resolves through the policy so the live mode stays Automatic"),
        ('api.post("/api/open-sound-settings")',
         "the muted-mic banner opens the Windows sound settings through the backend route"),
        ('function keepAudioAlert(a)', "the wrong-sys-device 'Keep' answer suppresses this seq locally"),
        ('function audioAlertSig(a)', "re-renders are gated on the alert signature (kind + seq + chosen + other)"),
        ('audio-alert-wrap', "the big alert uses its own full-width top bar"),
    ]
    for needle, why in checks:
        assert needle in APP_JS, f"app.js: {why} (missing {needle!r})"
    # The ms-settings:sound URI is a backend concern only: the browser bundle names the API route,
    # never the raw Windows URI, so an untrusted page can never be handed a launchable target.
    assert "ms-settings" not in APP_JS, \
        "app.js must call /api/open-sound-settings, never the raw ms-settings:sound URI"


def test_moved_toast_and_remembered_absent_notice_are_consumed():
    checks = [
        ('System audio moved to {d} (that is where sound is playing)',
         "the Automatic sys-moved toast is shown"),
        ('st.device_notice', "the remembered-absent device_notice is consumed from status"),
        ('st.mic_mode', "the live mic mode is adopted from status"),
        ('st.loopback_mode', "the live loopback mode is adopted from status"),
    ]
    for needle, why in checks:
        assert needle in APP_JS, f"app.js: {why} (missing {needle!r})"
    # The 1.14.1 sys_switch_notice toast must not double up with the new audio_toast.
    assert "Windows default output changed" not in APP_JS, \
        "the legacy sys_switch_notice toast must be replaced by audio_toast, not shown alongside it"


def test_big_red_banner_has_high_contrast_styling():
    for needle in (".vm .audio-alert-wrap", ".vm .audio-alert {", ".aa-primary", ".aa-ghost"):
        assert needle in STYLES, f"styles.css missing {needle!r}"
    assert "position: fixed; top: 0" in STYLES, "the red bar must pin to the top of the view"


def test_new_strings_have_afrikaans_and_no_dashes():
    for s in NEW_STRINGS:
        # The English string must be used in app.js and must key an Afrikaans value in i18n.js.
        assert s in APP_JS, f"app.js no longer uses the string {s!r}"
        assert ('"' + s + '"') in I18N_JS, f"i18n.js is missing an Afrikaans translation for {s!r}"
    # i18n.js is copy the user reads in both languages; it must be free of em and en dashes.
    # The dash characters are built with chr() so this guard file itself stays dash-free.
    en_dash, em_dash = chr(0x2013), chr(0x2014)
    assert en_dash not in I18N_JS and em_dash not in I18N_JS, \
        "i18n.js must not contain an em or en dash"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("all passed")
