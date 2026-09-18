"""Tests for the audio source selection policy and the live source watchdog (WP3, 1.14.2):
live_transcribe/device_policy.py plus the four new config keys.

Everything here is pure: endpoint dicts in, a decision out, and for the watchdog an injected
clock. No audio, no COM, no pyaudiowpatch, no disk. The choosers are table-driven over the
matrix in the brief (headset vs speakers, HDMI monitor, Steam virtual, muted mics, remembered
devices, duplicate names); the watchdog rows drive its clock by hand (arm timing, instant
disarm, the auto-switch rate limit, the quiet-call and mic-only non-alerts, mute/flat, the
one-shot quiet hint, and seq flap suppression).

Run:  python tests/test_device_policy.py   (from the project root; exit 0 = pass)
"""
import os
import sys

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _isolate_settings  # noqa: F401  redirect settings to a temp copy in script mode (codex F7)

from live_transcribe import config, device_policy as dp


# --- helpers ---------------------------------------------------------------

def ep(name, flow="render", peak=None, roles=(), muted=None, ff=None, id=None):
    """One endpoint dict in the pinned WP contract shape."""
    return {"id": id if id is not None else name, "name": name, "flow": flow,
            "form_factor": ff, "muted": muted, "peak_db": peak, "roles": list(roles)}


def render(name, peak=None, roles=(), ff=None, id=None):
    return ep(name, "render", peak=peak, roles=roles, ff=ff, id=id)


def cap(name, peak=None, roles=(), muted=None, id=None):
    return ep(name, "capture", peak=peak, roles=roles, muted=muted, id=id)


def base_obs(**over):
    """A healthy live observation (SYS working, mic talking); tests override the interesting bits."""
    o = {
        "sys_in_use": True,
        "sys_mode": "auto",
        "sys_name": "SYS-A",
        "sys_frames": True,
        "sys_db": -20.0,
        "renders": [render("SYS-A", peak=-20.0)],
        "mic_name": "Mic",
        "mic_db": -30.0,
        "mic_muted": False,
        "captures": [],
    }
    o.update(over)
    return o


def feed(w, obs_list, start=0.0, step=1.0):
    return [w.observe(start + i * step, o) for i, o in enumerate(obs_list)]


# --- is_junk ---------------------------------------------------------------

def test_is_junk_denylist_and_monitor_but_not_nvidia_broadcast():
    assert dp.is_junk(render("Some Monitor", ff=9)) is True, "HDMI/DisplayPort monitor is junk"
    for name in ("Steam Streaming Speakers", "CABLE Input (VB-Audio Virtual Cable)",
                 "VoiceMeeter Aux Input", "Line 1 (Virtual Audio Cable)"):
        assert dp.is_junk(render(name)) is True, name
    # NVIDIA Broadcast is a wanted mic processor, never junk. Plain real devices are not junk.
    assert dp.is_junk(cap("Microphone (NVIDIA Broadcast)")) is False
    assert dp.is_junk(render("Speakers (Realtek(R) Audio)")) is False
    print("  OK  is_junk: monitor + virtual/streaming denylist; NVIDIA Broadcast and real devices pass")


# --- choose_sys: the matrix ------------------------------------------------

def test_sys_call_on_the_headset_comms_default_wins():
    eps = [render("Headset", peak=-18.0, roles=["communications"]),
           render("Speakers", peak=-70.0, roles=["multimedia", "console"])]
    got, rule = dp.choose_sys(eps)
    assert got["name"] == "Headset" and rule == "playing-single", (got, rule)
    print("  OK  a call playing on the comms-default headset is chosen (playing-single)")


def test_sys_youtube_on_the_speakers_only_wins():
    eps = [render("Headset", peak=-70.0, roles=["communications"]),
           render("Speakers", peak=-14.0, roles=["multimedia", "console"])]
    got, rule = dp.choose_sys(eps)
    assert got["name"] == "Speakers" and rule == "playing-single", (got, rule)
    print("  OK  YouTube on the speakers only is chosen even though it is not the comms default")


def test_sys_several_playing_prefers_comms_then_multimedia_then_loudest():
    both = [render("Headset", peak=-18.0, roles=["communications"]),
            render("Speakers", peak=-12.0, roles=["multimedia", "console"])]
    got, rule = dp.choose_sys(both)
    assert got["name"] == "Headset" and rule == "playing-comms", (got, rule)
    # No comms default among the players: multimedia default wins.
    mm = [render("A", peak=-18.0), render("Speakers", peak=-12.0, roles=["multimedia"])]
    got, rule = dp.choose_sys(mm)
    assert got["name"] == "Speakers" and rule == "playing-multimedia", (got, rule)
    # No role at all: the loudest player wins.
    loud = [render("A", peak=-18.0), render("B", peak=-6.0)]
    got, rule = dp.choose_sys(loud)
    assert got["name"] == "B" and rule == "playing-loudest", (got, rule)
    print("  OK  several playing: comms default, else multimedia default, else loudest")


def test_sys_hdmi_default_but_idle_is_avoided_when_a_real_device_exists():
    # HDMI monitor is the Windows default for every role, but nothing is playing: it must be
    # avoided in favour of the real (non-junk) speakers.
    eps = [render("HDMI Monitor", peak=-70.0, ff=9,
                  roles=["console", "multimedia", "communications"]),
           render("Speakers (Realtek)", peak=-72.0)]
    got, rule = dp.choose_sys(eps)
    assert got["name"] == "Speakers (Realtek)" and rule == "first-nonjunk", (got, rule)
    print("  OK  an idle HDMI monitor default is avoided when a real device exists (first-nonjunk)")


def test_sys_audio_really_playing_only_on_hdmi_is_picked():
    # If the only thing making sound is the HDMI monitor, junk or not, it is still the source.
    eps = [render("HDMI Monitor", peak=-15.0, ff=9, roles=["multimedia"]),
           render("Speakers (Realtek)", peak=-72.0)]
    got, rule = dp.choose_sys(eps)
    assert got["name"] == "HDMI Monitor" and rule == "playing-single", (got, rule)
    print("  OK  audio genuinely playing only on the HDMI monitor is picked (junk still counts alone)")


def test_sys_steam_virtual_is_never_auto_picked_while_a_real_device_exists():
    # Idle Steam sink present, nothing playing: the real multimedia default is chosen.
    idle = [render("Steam Streaming Speakers", peak=-70.0),
            render("Speakers", peak=-71.0, roles=["multimedia"])]
    got, rule = dp.choose_sys(idle)
    assert got["name"] == "Speakers", (got, rule)
    # Even if the Steam sink is "playing", a real device that is also playing wins outright.
    playing = [render("Steam Streaming Speakers", peak=-10.0),
               render("Speakers", peak=-18.0, roles=["multimedia"])]
    got, rule = dp.choose_sys(playing)
    assert got["name"] == "Speakers" and rule == "playing-single", (got, rule)
    print("  OK  a Steam virtual sink is never auto-picked while a real device is there")


def test_sys_samples_use_two_of_three_and_openable_filters_first():
    a = render("A", peak=-70.0, id="a")
    b = render("B", peak=-70.0, id="b")
    # A is above the floor in 2 of 3 samples -> playing; B in only 1 -> not playing.
    got, rule = dp.choose_sys([a, b], samples={"a": [-20.0, -20.0, -70.0],
                                               "b": [-20.0, -70.0, -70.0]})
    assert got["name"] == "A" and rule == "playing-single", (got, rule)
    # openable drops endpoints the opener cannot resolve before anything else: a loud but
    # unopenable device is not chosen.
    loud_hidden = [render("Ghost", peak=-8.0), render("Real", peak=-71.0, roles=["multimedia"])]
    got, rule = dp.choose_sys(loud_hidden, openable={"Real"})
    assert got["name"] == "Real", (got, rule)
    # Nothing render at all -> (None, "none").
    assert dp.choose_sys([cap("Mic")]) == (None, "none")
    print("  OK  choose_sys: 2-of-3 sample rule, openable filters first, empty render -> (None, none)")


# --- choose_mic ------------------------------------------------------------

def test_mic_prefers_multimedia_then_comms_then_first_nonjunk():
    # WP3 addendum, from a live probe of the owner's desktop: Windows makes the webcam mic the
    # COMMUNICATIONS default while the real Samson is the multimedia default. Multimedia-first (and
    # a camera being junk) means the real mic wins, not the webcam.
    eps = [cap("Microphone (FHD Camera Microphone)", roles=["communications"]),
           cap("Microphone (2- Samson C01U              )", roles=["multimedia", "console"])]
    got, rule, warn = dp.choose_mic(eps)
    assert got["name"].startswith("Microphone (2- Samson") and rule == "multimedia-default", (got, rule)
    # No multimedia default: fall back to the comms default (muted None counts as not muted).
    eps = [cap("Onboard", roles=["communications"], muted=None), cap("Plain")]
    got, rule, warn = dp.choose_mic(eps)
    assert got["name"] == "Onboard" and rule == "comms-default" and warn is None, (got, rule, warn)
    # Both role defaults muted -> the first live non-junk, reaching PAST a webcam and a cable.
    eps = [cap("Studio", roles=["multimedia"], muted=True),
           cap("Comms", roles=["communications"], muted=True),
           cap("HD Webcam Mic"), cap("CABLE Output (VB-Audio)"), cap("Real USB Mic")]
    got, rule, warn = dp.choose_mic(eps)
    assert got["name"] == "Real USB Mic" and rule == "first-nonjunk", (got, rule)
    print("  OK  choose_mic: multimedia default, then comms default, then first live non-junk past a webcam")


def test_mic_webcam_is_deprioritised_but_not_excluded():
    # A webcam mic is junk here, so it loses to a real mic; but when it is the only thing there is,
    # it is still chosen (deprioritised, never excluded).
    got, rule, warn = dp.choose_mic([cap("Integrated Webcam Microphone")])
    assert got["name"] == "Integrated Webcam Microphone" and rule == "any-unmuted", (got, rule)
    assert dp.is_junk(cap("Logitech Webcam")) is True and dp.is_junk(cap("USB Camera Mic")) is True
    print("  OK  a webcam/camera mic is deprioritised but still chosen when it is the only input")


def test_mic_all_muted_picks_multimedia_default_and_warns():
    eps = [cap("Studio", roles=["multimedia"], muted=True),
           cap("Comms", roles=["communications"], muted=True)]
    got, rule, warn = dp.choose_mic(eps)
    assert got["name"] == "Studio" and rule == "multimedia-default" and warn == "mic-muted", (got, rule, warn)
    # No capture endpoints at all -> (None, "none", None).
    assert dp.choose_mic([render("Speakers")]) == (None, "none", None)
    print("  OK  every mic muted -> multimedia default + warn 'mic-muted'; no mics -> (None, none, None)")


# --- resolve_remembered ----------------------------------------------------

def test_resolve_remembered_id_then_name_then_ambiguous_then_absent():
    eps = [cap("USB Mic", id="{id-A}"), cap("USB Mic", id="{id-B}"), cap("Onboard", id="{id-C}")]
    # id wins even against a shared name (duplicate names resolved by id).
    assert dp.resolve_remembered("USB Mic", "{id-B}", eps) == (eps[1], "id")
    # unique exact name.
    assert dp.resolve_remembered("Onboard", "", eps) == (eps[2], "name")
    # shared name, no id to break it -> the first, flagged ambiguous.
    assert dp.resolve_remembered("USB Mic", "", eps) == (eps[0], "name-ambiguous")
    # absent -> None + "absent" (caller falls back to Automatic).
    assert dp.resolve_remembered("Gone", "{id-Z}", eps) == (None, "absent")
    print("  OK  resolve_remembered: id first, exact name, ambiguous-by-name, then absent")


# --- norm_name: whitespace/case tolerant matching --------------------------

def test_norm_name_tolerates_padding_and_case_everywhere():
    # Real MMDevice names carry padding inside the brackets and differ in case from the UI form.
    assert dp.norm_name("Microphone (2- Samson C01U              )") == "microphone (2- samson c01u)"
    assert dp.norm_name("  Speakers   (Realtek) ") == "speakers (realtek)"
    assert dp.norm_name("HEADSET") == dp.norm_name("headset")
    # resolve_remembered matches across the padding difference (name, not id).
    eps = [cap("Microphone (2- Samson C01U              )", id="{id-S}")]
    got, how = dp.resolve_remembered("Microphone (2- Samson C01U)", "", eps)
    assert got is eps[0] and how == "name", (got, how)
    # The watchdog matches our SYS name to a padded render name, so it sees a fault (right device,
    # broken capture), not a false wrong-device. If the match failed, our_render would be None.
    w = dp.Watchdog()
    padded = base_obs(sys_name="Speakers (Realtek)", sys_frames=False,
                      renders=[render("Speakers (Realtek)      ", peak=-15.0)])
    for t in range(5):
        w.observe(float(t), padded)
    _, action = w.observe(5.0, padded)
    assert action == {"do": "rebuild", "target": "Speakers (Realtek)"}, action
    print("  OK  norm_name collapses padding and casefolds; resolve + watchdog match across it")


# --- config: the four new keys migrate to Automatic ------------------------

def test_config_defaults_and_old_files_migrate_to_automatic():
    for k, v in (("mic_device", "auto"), ("loopback_device", "auto"),
                 ("mic_device_id", ""), ("loopback_device_id", "")):
        assert config.DEFAULTS[k] == v, k
    saved = config._read_raw
    try:
        # An old settings.json with none of the new keys must come up Automatic (owner decision).
        config._read_raw = lambda: {"interface_language": "en-ZA", "tier": "auto"}
        m = config.load()
        assert m["mic_device"] == "auto" and m["loopback_device"] == "auto", m
        assert m["mic_device_id"] == "" and m["loopback_device_id"] == "", m
        # An explicit saved choice still survives the merge.
        config._read_raw = lambda: {"mic_device": "USB Mic", "mic_device_id": "{id-A}"}
        m2 = config.load()
        assert m2["mic_device"] == "USB Mic" and m2["mic_device_id"] == "{id-A}", m2
        assert m2["loopback_device"] == "auto", m2
    finally:
        config._read_raw = saved
    print("  OK  the four source-selection keys default to Automatic and old files migrate to it")


# --- Watchdog: wrong SYS device --------------------------------------------

def test_wd_wrong_device_arms_at_3s_and_disarms_instantly():
    w = dp.Watchdog()
    bad = base_obs(sys_mode="named", sys_name="Speakers", sys_db=-70.0,
                   renders=[render("Speakers", peak=-70.0), render("Headset", peak=-18.0)])
    out = feed(w, [bad] * 3)                       # t = 0, 1, 2
    assert all(a is None for a, _ in out), "wrong-device armed before 3 s"
    alert, action = w.observe(3.0, bad)            # t = 3: armed
    assert alert and alert["kind"] == "wrong-sys-device" and alert["severity"] == "red", alert
    assert alert["chosen"] == "Speakers" and alert["other"] == "Headset", alert
    assert action is None, "named mode must not auto-switch"
    # A single contradicting sample (our capture is live again) disarms at once.
    good = base_obs(sys_mode="named", sys_name="Speakers", sys_db=-20.0,
                    renders=[render("Speakers", peak=-70.0), render("Headset", peak=-18.0)])
    assert w.observe(4.0, good) == (None, None), "wrong-device did not disarm instantly"
    print("  OK  wrong-sys-device arms at 3 s (not 2 s), names the other device, disarms instantly")


def test_loudest_other_render_prefers_nonjunk_but_falls_back_to_a_playing_junk():
    # codex F4: prefer a playing non-junk endpoint over a louder junk one...
    r = [render("Speakers", peak=-30.0), render("HDMI Monitor", peak=-10.0, ff=9)]
    got = dp._loudest_other_render(r, "SYS-A")
    assert got is not None and got["name"] == "Speakers", got
    # ...but when nothing non-junk is playing, a playing junk endpoint (the HDMI the call moved onto)
    # is still returned, so a switch or warning can follow instead of silence.
    r2 = [render("Speakers", peak=-72.0), render("HDMI Monitor", peak=-12.0, ff=9)]
    got2 = dp._loudest_other_render(r2, "SYS-A")
    assert got2 is not None and got2["name"] == "HDMI Monitor", got2
    print("  OK  _loudest_other_render prefers non-junk, falls back to a playing junk endpoint (F4)")


def test_wd_mic_flat_when_a_live_mic_stops_delivering_frames():
    # codex F3: a mic that WAS delivering frames then stops (unplugged / driver dropped) raises
    # mic-flat, even though its energy reading is now None (unknown), not below the flat floor.
    w = dp.Watchdog()
    for t in range(3):
        w.observe(float(t), base_obs(mic_frames=True, mic_present=True, mic_db=-30.0))
    assert w._mic_ever_live is True, "frames advancing must mark the mic as having been live"
    dead = base_obs(mic_frames=False, mic_present=True, mic_db=None)   # frames stalled, energy unknown
    last = None
    for t in range(3, 9):                            # confirms over 5 s, arms at t = 8
        last = w.observe(float(t), dead)
    alert, _ = last
    assert alert and alert["kind"] == "mic-flat" and alert["severity"] == "red", alert
    print("  OK  a mic that was live then stopped delivering frames raises mic-flat (F3)")


def test_wd_mic_flat_when_the_endpoint_disappears():
    # codex F3: the mic endpoint vanishing from the capture listing (mic_present False), after it had
    # been live, is also mic-flat.
    w = dp.Watchdog()
    for t in range(3):
        w.observe(float(t), base_obs(mic_frames=True, mic_present=True, mic_db=-30.0))
    gone = base_obs(mic_frames=True, mic_present=False, mic_db=-30.0)
    last = None
    for t in range(3, 9):
        last = w.observe(float(t), gone)
    assert last[0] and last[0]["kind"] == "mic-flat", last[0]
    print("  OK  a mic endpoint disappearing (was live) raises mic-flat (F3)")


def test_wd_mic_that_never_delivered_a_frame_stays_unknown():
    # codex F3: None means unknown. A mic that has NEVER delivered a frame (never live) must not raise
    # mic-flat no matter how long it sits with no frames and no reading.
    w = dp.Watchdog()
    out = feed(w, [base_obs(mic_frames=False, mic_present=True, mic_db=None)] * 10)
    assert all(a is None or a["kind"] != "mic-flat" for a, _ in out), out
    assert w._mic_ever_live is False
    print("  OK  a mic that never delivered a frame stays unknown, never mic-flat (F3)")


def test_wd_healthy_live_mic_never_raises_mic_flat():
    # A mic delivering frames, present, at a normal level must never trip the new liveness path.
    w = dp.Watchdog()
    out = feed(w, [base_obs(mic_frames=True, mic_present=True, mic_db=-30.0)] * 10)
    assert all(a is None for a, _ in out), out
    print("  OK  a healthy live mic never raises mic-flat from the liveness path (F3)")


def test_wd_mic_quiet_hint_suppressed_when_not_allowed():
    # codex F9: a record-only session passes allow_quiet False, so the amber mic-quiet nudge never
    # fires even though the mic stays quiet through the whole opening window.
    w = dp.Watchdog()
    out = feed(w, [base_obs(mic_db=-70.0, allow_quiet=False)] * 60)   # quiet, past the 45 s window
    assert all(a is None or a["kind"] != "mic-quiet" for a, _ in out), out
    # Control: the same quiet mic WITH allow_quiet True does raise the hint (so the gate is real).
    w2 = dp.Watchdog()
    got = feed(w2, [base_obs(mic_db=-70.0, allow_quiet=True)] * 60)
    assert any(a is not None and a["kind"] == "mic-quiet" for a, _ in got), "control: quiet fires when allowed"
    print("  OK  the mic-quiet hint is suppressed on a record-only session (allow_quiet False) (F9)")


def test_wd_wrong_device_warns_when_the_call_moved_onto_hdmi():
    # codex F4: our chosen SYS is idle and the only thing playing is the HDMI monitor. In named mode
    # the watchdog must still raise wrong-sys-device naming the HDMI as `other` (no silent miss).
    w = dp.Watchdog()
    bad = base_obs(sys_mode="named", sys_name="Speakers", sys_db=-70.0,
                   renders=[render("Speakers", peak=-70.0), render("HDMI Monitor", peak=-14.0, ff=9)])
    feed(w, [bad] * 3)                               # t = 0, 1, 2: confirming
    alert, action = w.observe(3.0, bad)              # t = 3: armed
    assert alert and alert["kind"] == "wrong-sys-device", alert
    assert alert["other"] == "HDMI Monitor", alert
    assert action is None, "named mode never auto-switches"
    print("  OK  a call moved onto the HDMI monitor still warns (other=HDMI) mid-session (F4)")


def test_wd_auto_mode_switches_then_alerts_and_respects_the_rate_limit():
    w = dp.Watchdog()

    def bad(sys_db):
        return base_obs(sys_mode="auto", sys_name="Speakers", sys_db=sys_db,
                        renders=[render("Speakers", peak=-70.0), render("Headset", peak=-18.0)])

    switches, alerts = [], []
    # Three wrong episodes in one minute, each separated by a healthy (disarming) tick.
    t = 0.0
    for episode in range(3):
        for _ in range(4):                         # 4 ticks: arms on the last one
            alert, action = w.observe(t, bad(-70.0))
            if action and action["do"] == "switch":
                switches.append((t, action["target"]))
            if alert and alert["kind"] == "wrong-sys-device":
                alerts.append((t, alert))
            t += 1.0
        w.observe(t, bad(-20.0))                    # healthy: disarm, end the episode
        t += 1.0
    assert len(switches) == dp.AUTO_SWITCH_MAX, f"expected {dp.AUTO_SWITCH_MAX} switches, got {switches}"
    assert all(tg == "Headset" for _, tg in switches), switches
    assert alerts, "after the switch budget was spent it must fall back to alerting"
    print("  OK  auto mode switches up to the budget then alerts; switch targets the loud device")


# --- Watchdog: SYS capture fault -------------------------------------------

def test_wd_sys_capture_fault_rebuilds_once_then_alerts():
    w = dp.Watchdog()
    # Our own device's meter is playing but no frames arrive: a capture fault, not a wrong device.
    fault = base_obs(sys_name="SYS-A", sys_frames=False,
                     renders=[render("SYS-A", peak=-15.0)])
    for t in range(5):                             # t = 0..4: confirming, nothing yet
        assert w.observe(float(t), fault) == (None, None), f"fault surfaced early at t={t}"
    alert, action = w.observe(5.0, fault)          # armed: rebuild once
    assert alert is None and action == {"do": "rebuild", "target": "SYS-A"}, (alert, action)
    alert, action = w.observe(6.0, fault)          # still faulting: alert, no second rebuild
    assert action is None and alert and alert["kind"] == "sys-capture-fault", (alert, action)
    assert alert["severity"] == "red" and alert["chosen"] == "SYS-A", alert
    print("  OK  sys-capture-fault rebuilds once at 5 s, then alerts if the fault persists")


# --- Watchdog: the quiet cases never nag -----------------------------------

def test_wd_quiet_call_three_minutes_no_alert():
    # A call whose far end is quiet (every render meter low) while the user talks (mic active):
    # nothing is playing to be wrong about, so nothing is surfaced for three minutes.
    w = dp.Watchdog()
    quiet = base_obs(sys_name="Speakers", sys_db=-70.0, renders=[render("Speakers", peak=-70.0)])
    out = feed(w, [quiet] * 180)
    assert all(v == (None, None) for v in out), "a quiet call was nagged"
    print("  OK  a three-minute quiet call (far end silent, mic live) is never alerted")


def test_wd_mic_only_ten_minutes_no_alert():
    # No SYS in use at all, mic live: a legitimate mic-only session, silent for ten minutes.
    w = dp.Watchdog()
    mic_only = base_obs(sys_in_use=False, sys_name=None, renders=[])
    out = feed(w, [mic_only] * 600)
    assert all(v == (None, None) for v in out), "a mic-only session was nagged"
    print("  OK  a ten-minute mic-only session is never alerted")


# --- Watchdog: mic muted / flat --------------------------------------------

def test_wd_mic_muted_arms_at_2s_and_clears_on_unmute():
    w = dp.Watchdog()
    muted = base_obs(sys_in_use=False, renders=[], mic_muted=True)
    assert w.observe(0.0, muted)[0] is None and w.observe(1.0, muted)[0] is None, "muted armed before 2 s"
    alert, _ = w.observe(2.0, muted)
    assert alert and alert["kind"] == "mic-muted" and alert["severity"] == "red", alert
    live = base_obs(sys_in_use=False, renders=[], mic_muted=False)
    assert w.observe(3.0, live) == (None, None), "mic-muted did not clear on unmute"
    print("  OK  mic-muted arms at 2 s (red) and clears the instant the mic is unmuted")


def test_wd_mic_flat_arms_at_5s():
    w = dp.Watchdog()
    flat = base_obs(sys_in_use=False, renders=[], mic_db=-100.0)
    for t in range(5):
        assert w.observe(float(t), flat)[0] is None, f"mic-flat armed early at t={t}"
    alert, _ = w.observe(5.0, flat)
    assert alert and alert["kind"] == "mic-flat" and alert["severity"] == "red", alert
    print("  OK  mic-flat arms at 5 s (red) on a mic below -90 dBFS")


def test_wd_mic_side_other_populated_only_on_known_loud_capture():
    # Owner's note: a flat/muted mic offers a swap only when another capture endpoint has a
    # KNOWN peak above -50; an unknown (None) peak omits `other`.
    w = dp.Watchdog()
    obs = base_obs(sys_in_use=False, renders=[], mic_name="Onboard", mic_muted=True,
                   captures=[cap("USB Mic", peak=-20.0)])
    for t in range(3):
        alert, _ = w.observe(float(t), obs)
    assert alert and alert["other"] == "USB Mic", alert
    w2 = dp.Watchdog()
    obs2 = base_obs(sys_in_use=False, renders=[], mic_name="Onboard", mic_muted=True,
                    captures=[cap("USB Mic", peak=None)])
    for t in range(3):
        alert2, _ = w2.observe(float(t), obs2)
    assert alert2 and alert2["other"] is None, alert2
    print("  OK  a mic red offers 'other' only when another capture has a known loud peak")


# --- Watchdog: the one-shot quiet hint -------------------------------------

def test_wd_mic_quiet_hint_fires_once_then_can_be_dismissed():
    w = dp.Watchdog()
    quiet_mic = base_obs(sys_in_use=False, renders=[], mic_db=-70.0)  # never above -55, above -90
    w.observe(0.0, quiet_mic)                        # start the session clock at 0
    assert w.observe(44.0, quiet_mic) == (None, None), "the hint fired before the 45 s window"
    alert, _ = w.observe(45.0, quiet_mic)
    assert alert and alert["kind"] == "mic-quiet" and alert["severity"] == "amber", alert
    first_seq = alert["seq"]
    alert2, _ = w.observe(46.0, quiet_mic)
    assert alert2 and alert2["seq"] == first_seq, "the hint should keep its seq, not re-fire"
    w.dismiss()
    assert w.observe(47.0, quiet_mic) == (None, None), "a dismissed hint returned"
    assert w.observe(200.0, quiet_mic) == (None, None), "a dismissed hint came back later"
    print("  OK  mic-quiet fires once after 45 s (amber), keeps its seq, and never returns once dismissed")


# --- Watchdog: seq flap suppression ----------------------------------------

def test_wd_flap_within_10s_keeps_seq_but_a_later_rearm_is_new():
    w = dp.Watchdog()
    muted = base_obs(sys_in_use=False, renders=[], mic_muted=True)
    live = base_obs(sys_in_use=False, renders=[], mic_muted=False)
    feed(w, [muted] * 3)                            # arm at t = 2
    seq1 = w.observe(2.0, muted)[0]["seq"]
    w.observe(3.0, live)                            # disarm at t = 3
    # Re-arm within 10 s of the disarm: same seq (no re-toast for a flapping meter).
    w.observe(4.0, muted); w.observe(5.0, muted)
    rearm = w.observe(6.0, muted)[0]
    assert rearm["seq"] == seq1, f"a flap inside {dp.FLAP_WINDOW_S}s must keep the seq: {rearm}"
    # Disarm, then re-arm well after the flap window: a genuinely new alert.
    w.observe(7.0, live)
    w.observe(20.0, muted); w.observe(21.0, muted)
    later = w.observe(22.0, muted)[0]
    assert later["seq"] != seq1, f"a re-arm after the flap window must get a new seq: {later}"
    print("  OK  seq is kept across a <10 s flap and freshly incremented on a later re-arm")


TESTS = (
    test_is_junk_denylist_and_monitor_but_not_nvidia_broadcast,
    test_sys_call_on_the_headset_comms_default_wins,
    test_sys_youtube_on_the_speakers_only_wins,
    test_sys_several_playing_prefers_comms_then_multimedia_then_loudest,
    test_sys_hdmi_default_but_idle_is_avoided_when_a_real_device_exists,
    test_sys_audio_really_playing_only_on_hdmi_is_picked,
    test_sys_steam_virtual_is_never_auto_picked_while_a_real_device_exists,
    test_sys_samples_use_two_of_three_and_openable_filters_first,
    test_mic_prefers_multimedia_then_comms_then_first_nonjunk,
    test_mic_webcam_is_deprioritised_but_not_excluded,
    test_mic_all_muted_picks_multimedia_default_and_warns,
    test_resolve_remembered_id_then_name_then_ambiguous_then_absent,
    test_norm_name_tolerates_padding_and_case_everywhere,
    test_config_defaults_and_old_files_migrate_to_automatic,
    test_wd_wrong_device_arms_at_3s_and_disarms_instantly,
    test_loudest_other_render_prefers_nonjunk_but_falls_back_to_a_playing_junk,
    test_wd_wrong_device_warns_when_the_call_moved_onto_hdmi,
    test_wd_mic_flat_when_a_live_mic_stops_delivering_frames,
    test_wd_mic_flat_when_the_endpoint_disappears,
    test_wd_mic_that_never_delivered_a_frame_stays_unknown,
    test_wd_healthy_live_mic_never_raises_mic_flat,
    test_wd_mic_quiet_hint_suppressed_when_not_allowed,
    test_wd_auto_mode_switches_then_alerts_and_respects_the_rate_limit,
    test_wd_sys_capture_fault_rebuilds_once_then_alerts,
    test_wd_quiet_call_three_minutes_no_alert,
    test_wd_mic_only_ten_minutes_no_alert,
    test_wd_mic_muted_arms_at_2s_and_clears_on_unmute,
    test_wd_mic_flat_arms_at_5s,
    test_wd_mic_side_other_populated_only_on_known_loud_capture,
    test_wd_mic_quiet_hint_fires_once_then_can_be_dismissed,
    test_wd_flap_within_10s_keeps_seq_but_a_later_rearm_is_new,
)


if __name__ == "__main__":
    failures = 0
    for fn in TESTS:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll device-policy tests passed.")
