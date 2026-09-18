"""Audio source selection policy and the live source watchdog (WP3, 1.14.2): the decisions,
with no I/O, no COM and no audio in them.

Two failures this exists for, both of them the owner's words:

  * "Open it and it just works." Windows hands us a pile of render (playback) and capture
    (recording) endpoints and a set of "default" flags that are wrong as often as they are
    right: the system default output is the HDMI monitor nobody listens through, a virtual
    cable left over from some other app, a headset in a drawer. So when the user has not
    pinned a device, we pick the SYS (system-audio) source and the mic the way a person
    would: prefer the endpoint that is actually making sound, prefer the communications
    device on a call, and never reach for an obviously virtual or monitor endpoint while a
    real one is there.

  * "If a channel is quiet, Volksmond must check if there is another channel that is not
    quiet" and warn "as seamless as Microsoft Teams" would, but a legitimately quiet call
    or a mic-only session must NOT nag. So the Watchdog watches the live meters once a
    second and, only after several consecutive confirmations, says the SYS source is the
    wrong one (and in Automatic mode just switches, rate-limited), or that the mic is muted
    or dead. Nothing playing anywhere is the quiet call: it is silence, not a fault, so it
    is never surfaced.

What is deliberately NOT here: any audio, any pyaudiowpatch/COM call, any settings read,
any clock. choose_* and the resolvers are pure functions over endpoint dicts; the Watchdog
takes numbers and an injected clock and returns a verdict, so every rule is testable in
microseconds. devices_win.py / mmdevice_win.py own the enumeration and the peaks, capture
owns the frames, web/app.py owns the 1 Hz thread, the switch and the banner.

The endpoint dict this module reasons over (the pinned WP contract, produced elsewhere):

    {"id": str, "name": str, "flow": "render"|"capture",
     "form_factor": int|None, "muted": bool|None, "peak_db": float|None,
     "roles": list[str]}

where roles is a subset of ("console", "multimedia", "communications"), peak_db is dBFS
(floor DB_FLOOR, None when it cannot be read), and form_factor 9 means HDMI/DisplayPort
monitor audio. A capture endpoint's peak_db is often None or the floor when nothing is
recording from it: None means "unknown", never "silent".
"""
import re
import threading

# --- level thresholds, all dBFS -------------------------------------------
DB_FLOOR = -120.0          # the contract's peak floor; a None peak is treated as this loud
PLAY_DB = -50.0            # at or below this a render endpoint is not "playing"; above it is
SYS_IDLE_DB = -60.0        # our SYS capture is "idle" when its raw level sits below this
OTHER_LOUD_DB = -50.0      # another render endpoint has to beat this to count as "the real one"
MIC_FLAT_DB = -90.0        # a mic below this is flat-lined (dead / unplugged), not merely quiet
MIC_QUIET_DB = -55.0       # a mic that never beats this in the opening window earns the hint
NOTHING_DB = -60.0         # nothing is playing anywhere when every render meter is below this

# --- watchdog confirmations, all seconds ----------------------------------
WRONG_SYS_CONFIRM_S = 3.0  # SYS looks wrong for this long before we act
SYS_FAULT_CONFIRM_S = 5.0  # our device is playing but no frames arrive, for this long
MIC_MUTED_CONFIRM_S = 2.0  # mic muted for this long
MIC_FLAT_CONFIRM_S = 5.0   # mic flat for this long
MIC_QUIET_WINDOW_S = 45.0  # the opening window a mic gets before the quiet hint can fire
FLAP_WINDOW_S = 10.0       # a disarm+re-arm inside this window keeps the same seq (no re-toast)

# --- auto-switch rate limit -----------------------------------------------
AUTO_SWITCH_MAX = 2        # most SYS auto-switches in the window before we fall back to alerting
AUTO_SWITCH_WINDOW_S = 60.0
SYS_REBUILD_MAX = 1        # rebuild a faulting SYS capture this many times per fault, then alert

# --- alert severities (kind -> "red"|"amber") -----------------------------
SEVERITY = {
    "wrong-sys-device": "red",
    "sys-capture-fault": "red",
    "mic-muted": "red",
    "mic-flat": "red",
    "mic-quiet": "amber",
}

# Which alert wins when several are armed at once (highest priority first). The user's own
# voice going missing is the worst, so the mic reds lead; the quiet hint is an amber last.
_PRIORITY = ("mic-muted", "mic-flat", "wrong-sys-device", "sys-capture-fault", "mic-quiet")

# Substrings (compared against a normalised name) that mark a render/capture endpoint as junk:
# virtual cables and streaming sinks that Windows will happily hand us as a "default". NVIDIA
# Broadcast is left off deliberately: it is a real, wanted microphone processor, not a device
# to avoid.
_JUNK_NAMES = ("steam streaming", "vb-audio", "voicemeeter", "cable", "virtual")

# Capture-only: a webcam's built-in mic is a real endpoint Windows often makes the communications
# default, but it is rarely the mic the user means. Deprioritised (never excluded) so a real mic
# is preferred when there is one.
_CAM_NAMES = ("camera", "webcam")

_MONITOR_FORM_FACTOR = 9   # HDMI/DisplayPort monitor audio; almost never what a meeting wants

_WHITESPACE = re.compile(r"\s+")


# --- small pure helpers ---------------------------------------------------

def norm_name(s):
    """Normalise a device name for comparison, tolerant of the ways MMDevice reports the same
    endpoint. Real names carry padding inside the brackets, e.g.
    "Microphone (2- Samson C01U              )", and case can differ between the UI-cleaned form
    and the raw form. So: collapse every run of whitespace to one space, trim the ends, drop the
    padding before a closing bracket, and casefold. Comparisons use this; the ORIGINAL name is
    always what gets returned to the caller (the opener resolves against the real string)."""
    s = _WHITESPACE.sub(" ", (s or "")).strip()
    s = s.replace(" )", ")")
    return s.casefold()


def _same_name(a, b):
    return norm_name(a) == norm_name(b)


def _roles(ep):
    r = ep.get("roles")
    return r if isinstance(r, (list, tuple)) else ()


def _peak(ep):
    """The endpoint's single peak in dBFS, with None read as the floor (never as loud)."""
    v = ep.get("peak_db")
    return float(v) if v is not None else DB_FLOOR


def _muted(ep):
    """Muted only when explicitly True; None ('unknown') counts as not muted, as pinned."""
    return ep.get("muted") is True


def is_junk(ep):
    """A render/capture endpoint we should never reach for on our own while a real one exists.

    Junk is HDMI/DisplayPort monitor audio (form_factor 9) or a name on the small virtual/
    streaming denylist. It is only ever DEPRIORITISED, never excluded: if the audio is truly
    playing only on a junk endpoint, or a junk endpoint is the only one there is, it is still
    picked. That is why this is a hint the choosers weigh, not a filter they apply.
    """
    if ep.get("form_factor") == _MONITOR_FORM_FACTOR:
        return True
    name = norm_name(ep.get("name"))
    if any(bad in name for bad in _JUNK_NAMES):
        return True
    if ep.get("flow") == "capture" and any(cam in name for cam in _CAM_NAMES):
        return True
    return False


def is_playing(peaks, floor=PLAY_DB):
    """Is this endpoint making sound? `peaks` is either one peak_db (float or None) or a short
    list of samples. A single sample plays when it is above the floor; three or more samples
    use "above the floor in 2 of 3" so one stray frame neither arms nor clears a decision.
    """
    vals = list(peaks) if isinstance(peaks, (list, tuple)) else [peaks]
    hot = sum(1 for v in vals if v is not None and float(v) > floor)
    need = 2 if len(vals) >= 3 else 1
    return hot >= need


def _openable_only(endpoints, openable):
    """Drop endpoints the opener cannot resolve (by name), when a set of names is supplied.
    The match is whitespace/case tolerant (norm_name), like every name comparison here."""
    if openable is None:
        return list(endpoints)
    allowed = {norm_name(n) for n in openable}
    return [e for e in endpoints if norm_name(e.get("name")) in allowed]


def _default(endpoints, role):
    """The first endpoint carrying `role` (the Windows default for that role), or None."""
    for e in endpoints:
        if role in _roles(e):
            return e
    return None


# --- SYS (system audio / loopback) selection ------------------------------

def choose_sys(endpoints, openable=None, samples=None):
    """Pick the SYS source (a render endpoint we loop back) and name the rule that chose it.

    endpoints: the full endpoint list (any flow); only render endpoints are considered.
    openable:  optional set of endpoint NAMES the opener can actually resolve. When given,
               endpoints outside it are dropped before anything else.
    samples:   optional {endpoint_id: [peak_db, ...]} of recent samples per endpoint, for the
               "playing" test. An endpoint with no entry falls back to its own peak_db.

    Returns (endpoint | None, rule). The locked order:
      1) exactly one endpoint playing            -> "playing-single"
      2) several playing: comms default,
         then multimedia default, then loudest   -> "playing-comms"/"playing-multimedia"/
                                                     "playing-loudest"
      3) nothing playing: comms default if real  -> "comms-default"
      4) else multimedia default if real         -> "multimedia-default"
      5) else first non-junk, else anything      -> "first-nonjunk"/"any"
    A playing junk endpoint counts as playing only when nothing non-junk is playing, so a real
    device that is making sound always wins over a virtual one, but a junk endpoint that is
    genuinely the only thing playing (a call on the HDMI monitor speakers) is still chosen.
    """
    render = [e for e in _openable_only(endpoints, openable) if e.get("flow") == "render"]
    if not render:
        return None, "none"

    def plays(ep):
        if samples and ep.get("id") in samples:
            return is_playing(samples[ep["id"]])
        return is_playing(ep.get("peak_db"))

    def loud(ep):
        if samples and ep.get("id") in samples:
            vals = [v for v in samples[ep["id"]] if v is not None]
            return max(vals) if vals else DB_FLOOR
        return _peak(ep)

    playing_real = [e for e in render if plays(e) and not is_junk(e)]
    playing = playing_real or [e for e in render if plays(e)]

    if len(playing) == 1:
        return playing[0], "playing-single"
    if len(playing) > 1:
        comms = _default(playing, "communications")
        if comms is not None:
            return comms, "playing-comms"
        mm = _default(playing, "multimedia")
        if mm is not None:
            return mm, "playing-multimedia"
        return max(playing, key=loud), "playing-loudest"

    # Nothing playing: fall back to the OS defaults, but never to a junk default while a real
    # endpoint exists (rule 5 mops that up).
    comms = _default(render, "communications")
    if comms is not None and not is_junk(comms):
        return comms, "comms-default"
    mm = _default(render, "multimedia")
    if mm is not None and not is_junk(mm):
        return mm, "multimedia-default"
    for e in render:
        if not is_junk(e):
            return e, "first-nonjunk"
    return render[0], "any"


# --- microphone selection -------------------------------------------------

def choose_mic(endpoints, openable=None):
    """Pick the mic (a capture endpoint) and name the rule, plus a warning when every mic is muted.

    Returns (endpoint | None, rule, warn). warn is None normally, or "mic-muted" when the only
    thing we could pick is muted. A muted or junk default is skipped in favour of a live real
    device; muted None counts as not muted (unknown state is trusted, not warned about).

    The order is MULTIMEDIA first, deliberately (WP3 addendum, from a live probe of the owner's
    machine): Windows makes a webcam's mic the COMMUNICATIONS default when the webcam is plugged
    in, while the real studio mic is the console+multimedia default. So:
      1) multimedia default if live and not junk  -> "multimedia-default"
      2) else communications default, same terms  -> "comms-default"
      3) else first live non-junk                 -> "first-nonjunk"
      4) else any live endpoint                    -> "any-unmuted"
      5) every candidate muted: the multimedia default (or first mic) + warn "mic-muted"
    A webcam/camera capture counts as junk here, so step 3 reaches past it to a real mic; it is
    only ever deprioritised, so a webcam mic is still chosen when it is the only thing there is.
    """
    caps = [e for e in _openable_only(endpoints, openable) if e.get("flow") == "capture"]
    if not caps:
        return None, "none", None

    mm = _default(caps, "multimedia")
    if mm is not None and not _muted(mm) and not is_junk(mm):
        return mm, "multimedia-default", None
    comms = _default(caps, "communications")
    if comms is not None and not _muted(comms) and not is_junk(comms):
        return comms, "comms-default", None
    for e in caps:
        if not _muted(e) and not is_junk(e):
            return e, "first-nonjunk", None
    for e in caps:
        if not _muted(e):
            return e, "any-unmuted", None
    # Every candidate is muted: pick the multimedia default so a click unmutes the right one.
    chosen = mm if mm is not None else caps[0]
    return chosen, ("multimedia-default" if mm is not None else "any"), "mic-muted"


# --- remembered-device resolution -----------------------------------------

def resolve_remembered(saved_name, saved_id, endpoints):
    """Find the endpoint a saved (name, id) points at, and say how it was found.

    Returns (endpoint | None, how). The id is the stable key, so it is tried first ("id"). A
    unique exact name match is "name". When several endpoints share the saved name, the id
    breaks the tie ("id"); with no id to break it, the first is taken and flagged
    "name-ambiguous". Nothing matching returns (None, "absent"), and the caller falls back to
    Automatic with a notice.
    """
    if saved_id:
        for e in endpoints:
            if e.get("id") == saved_id:
                return e, "id"
    named = [e for e in endpoints if _same_name(e.get("name"), saved_name)] if saved_name else []
    if not named:
        return None, "absent"
    if len(named) == 1:
        return named[0], "name"
    if saved_id:
        for e in named:
            if e.get("id") == saved_id:
                return e, "id"
    return named[0], "name-ambiguous"


# --- the live source watchdog ---------------------------------------------

class _Confirm:
    """A rising-edge confirmation timer: a condition must hold for threshold_s of wall time
    before it is armed, and it disarms the instant a contradicting sample arrives. now is the
    injected clock (any monotonically rising seconds), so it needs no threads of its own."""

    def __init__(self, threshold_s):
        self.threshold_s = float(threshold_s)
        self.since = None
        self.armed = False

    def update(self, now, active):
        if not active:
            self.since = None
            self.armed = False
            return False
        if self.since is None:
            self.since = now
        self.armed = (now - self.since) >= self.threshold_s
        return self.armed


class Watchdog:
    """The once-a-second verdict on "are we capturing the right, live sources?".

    Fed an observation dict each tick (see observe()), it returns (alert | None, action | None):

      alert  = {"kind": str, "severity": "red"|"amber", "chosen": str, "other": str|None,
                "seq": int}  -- the pinned shape the backend publishes verbatim. seq rises each
               time a fresh alert arms; an alert that disarms and re-arms within FLAP_WINDOW_S
               keeps its seq, so a flapping meter does not re-toast.
      action = {"do": "switch"|"rebuild", "target": str|None} -- what the app should DO before
               (or instead of) bothering the user: switch the SYS source to `target`, or rebuild
               the SYS capture in place. None when there is nothing to do.

    Only ONE alert is returned per tick (the highest-priority armed one); the action is computed
    independently, so a SYS auto-switch can ride alongside a mic alert. Nothing playing anywhere
    is silence, not a fault, and is never surfaced. The mic is never auto-switched: a live mic on
    another endpoint only ever populates `other` so the UI can offer the swap.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._start = None
        self._wrong = _Confirm(WRONG_SYS_CONFIRM_S)
        self._fault = _Confirm(SYS_FAULT_CONFIRM_S)
        self._muted = _Confirm(MIC_MUTED_CONFIRM_S)
        self._flat = _Confirm(MIC_FLAT_CONFIRM_S)
        self._fault_rebuilt = False          # the one rebuild for the current fault episode is spent
        self._switch_target_episode = None   # the target we already auto-switched to this wrong episode
        self._switch_times = []              # clock values of recent auto-switches (rolling window)
        # mic-quiet hint, one-shot per session: "pending" -> "fired" -> "done"
        self._quiet_state = "pending"
        self._mic_seen_any = False           # any real (non-None) mic reading yet
        self._mic_seen_loud = False          # any mic reading above MIC_QUIET_DB yet
        self._mic_ever_live = False          # MIC frames have advanced at least once (codex F3): only
                                             # then can "was live, now dead/gone" raise mic-flat; a mic
                                             # that has never delivered a frame stays unknown, not flat
        # seq bookkeeping
        self._seq_counter = 0
        self._seq = {}                       # kind -> its current seq
        self._disarm_at = {}                 # kind -> clock it last stopped alerting
        self._armed_prev = {}                # kind -> was it alerting on the previous tick

    # -- the tick ----------------------------------------------------------

    def observe(self, now, obs):
        """Feed one 1 Hz observation. `now` is the session clock (seconds). `obs` keys:

            sys_name    str|None    the render endpoint we currently loop back as SYS
            sys_frames  bool        did SYS frames advance this tick
            sys_db      float|None  our SYS raw dBFS (None == unreadable)
            sys_mode    "auto"|"named"
            sys_in_use  bool        is any SYS source in use at all
            renders     [ep, ...]   every render endpoint, each with its peak_db
            mic_name    str|None    the capture endpoint we currently record as the mic
            mic_db      float|None  our mic raw dBFS (None == unreadable)
            mic_frames  bool        did MIC frames advance this tick (device liveness, codex F3)
            mic_present bool|None   is the mic endpoint still in the capture listing (None == unknown)
            mic_muted   bool        is the mic muted in the OS mixer
            mic_mode    "auto"|"named"
            captures    [ep, ...]   every capture endpoint, each with its peak_db (for `other`)

        Returns (alert | None, action | None).
        """
        now = float(now)
        with self._lock:
            if self._start is None:
                self._start = now

            sys_mode = obs.get("sys_mode", "auto")
            sys_in_use = bool(obs.get("sys_in_use"))
            sys_name = obs.get("sys_name")
            sys_frames = bool(obs.get("sys_frames"))
            sys_db = obs.get("sys_db")
            renders = obs.get("renders") or []
            mic_name = obs.get("mic_name")
            mic_db = obs.get("mic_db")
            mic_frames = bool(obs.get("mic_frames"))
            mic_present = obs.get("mic_present")   # True / False / None(unknown)
            mic_muted = bool(obs.get("mic_muted"))
            captures = obs.get("captures") or []

            action = None

            # ---- SYS: is it the wrong device, or a broken capture? -----------------
            our_render = _find_by_name(renders, sys_name)
            our_meter_playing = our_render is not None and _peak(our_render) > PLAY_DB
            sys_idle = (not sys_frames) or (sys_db is not None and float(sys_db) < SYS_IDLE_DB)
            other = _loudest_other_render(renders, sys_name)

            fault_active = sys_in_use and our_meter_playing and (not sys_frames)
            wrong_active = (sys_in_use and not fault_active and sys_idle and other is not None)

            self._fault.update(now, fault_active)
            self._wrong.update(now, wrong_active)

            # fault: rebuild once per episode, then alert if it is still faulting
            fault_alerting = False
            if self._fault.armed:
                if not self._fault_rebuilt:
                    self._fault_rebuilt = True
                    action = {"do": "rebuild", "target": sys_name}
                else:
                    fault_alerting = True
            else:
                self._fault_rebuilt = False

            # wrong device: in auto mode switch (rate-limited, once per target per episode),
            # otherwise (named mode, or budget/target spent) alert with `other` for a click.
            wrong_alerting = False
            wrong_other = other["name"] if other is not None else None
            if self._wrong.armed:
                if sys_mode == "auto" and self._switch_ok(now, wrong_other):
                    self._record_switch(now, wrong_other)
                    action = action or {"do": "switch", "target": wrong_other}
                else:
                    wrong_alerting = True
            else:
                self._switch_target_episode = None

            # ---- mic: muted, flat, or quiet ---------------------------------------
            # mic-flat now covers three ways a mic delivers nothing (codex F3):
            #   * a real energy reading below the flat-line floor (the original case);
            #   * a mic that WAS delivering frames and has stopped (unplugged / driver dropped): a live
            #     mic delivers blocks whatever the room volume, so a stalled frame counter is the DEVICE
            #     going away, not silence;
            #   * the mic endpoint disappearing from the capture listing once it had been live.
            # A mic that has never delivered a frame stays unknown (never flat): None means unknown.
            if mic_frames:
                self._mic_ever_live = True
            mic_flat_energy = mic_db is not None and float(mic_db) < MIC_FLAT_DB
            mic_gone = (mic_present is False)
            mic_dead = self._mic_ever_live and ((not mic_frames) or mic_gone)
            mic_flat_active = mic_flat_energy or mic_dead
            self._muted.update(now, mic_muted)
            self._flat.update(now, mic_flat_active)

            if mic_db is not None:
                self._mic_seen_any = True
                if float(mic_db) > MIC_QUIET_DB:
                    self._mic_seen_loud = True

            # The mic-quiet hint is a transcription-quality nudge; a record-only session (no engine)
            # passes allow_quiet False so it never nags, while the mic-flat / wrong-SYS reds still run
            # off the capture-owned levels (codex F9). Default True keeps every existing caller intact.
            quiet_alerting = self._update_quiet(now) if obs.get("allow_quiet", True) else False

            mic_other = _loudest_capture(captures, mic_name)  # a live mic elsewhere, or None
            mic_other_name = mic_other["name"] if mic_other is not None else None

            # ---- assemble the armed alerts, resolve seq, pick the winner ----------
            armed = {
                "mic-muted": (self._muted.armed, mic_name, mic_other_name),
                "mic-flat": (self._flat.armed, mic_name, mic_other_name),
                "wrong-sys-device": (wrong_alerting, sys_name, wrong_other),
                "sys-capture-fault": (fault_alerting, sys_name, None),
                "mic-quiet": (quiet_alerting, mic_name, mic_other_name),
            }
            alerts = {}
            for kind, (is_on, chosen, other_name) in armed.items():
                seq = self._seq_for(kind, is_on, now)
                if is_on:
                    alerts[kind] = {
                        "kind": kind,
                        "severity": SEVERITY[kind],
                        "chosen": chosen,
                        "other": other_name,
                        "seq": seq,
                    }
            for kind in _PRIORITY:
                if kind in alerts:
                    return alerts[kind], action
            return None, action

    # -- the one user answer ----------------------------------------------

    def dismiss(self):
        """Close the amber mic-quiet hint for good: it never returns this session."""
        with self._lock:
            self._quiet_state = "done"

    # -- internals ---------------------------------------------------------

    def _update_quiet(self, now):
        """Fire the one-shot mic-quiet hint at the end of the opening window when the mic was
        never heard above MIC_QUIET_DB, but only if we ever got a real reading (an all-None mic
        is unknown, not quiet). Once fired or resolved it never returns."""
        if self._quiet_state == "done":
            return False
        if self._quiet_state == "fired":
            # A mic that comes alive resolves the hint; otherwise it keeps showing until dismissed.
            if self._mic_seen_loud:
                self._quiet_state = "done"
                return False
            return True
        # pending
        if self._mic_seen_loud:
            self._quiet_state = "done"   # the mic is fine; the hint can never fire this session
            return False
        if (now - self._start) >= MIC_QUIET_WINDOW_S and self._mic_seen_any:
            self._quiet_state = "fired"
            return True
        return False

    def _switch_ok(self, now, target):
        """May we auto-switch SYS now? Not to a target we already switched to this episode, and
        not more than AUTO_SWITCH_MAX times in the rolling window."""
        if target is None or target == self._switch_target_episode:
            return False
        recent = [t for t in self._switch_times if (now - t) < AUTO_SWITCH_WINDOW_S]
        return len(recent) < AUTO_SWITCH_MAX

    def _record_switch(self, now, target):
        self._switch_times = [t for t in self._switch_times if (now - t) < AUTO_SWITCH_WINDOW_S]
        self._switch_times.append(now)
        self._switch_target_episode = target

    def _seq_for(self, kind, alerting, now):
        """Assign/keep the seq for an alert kind across arm/disarm edges. A rising edge inside
        FLAP_WINDOW_S of the last disarm keeps the old seq (no re-toast); otherwise it is new."""
        prev = self._armed_prev.get(kind, False)
        if alerting and not prev:
            last = self._disarm_at.get(kind)
            if not (last is not None and (now - last) <= FLAP_WINDOW_S and kind in self._seq):
                self._seq_counter += 1
                self._seq[kind] = self._seq_counter
        elif prev and not alerting:
            self._disarm_at[kind] = now
        self._armed_prev[kind] = alerting
        return self._seq.get(kind)


def _find_by_name(endpoints, name):
    if not name:
        return None
    for e in endpoints:
        if _same_name(e.get("name"), name):
            return e
    return None


def _loudest_other_render(renders, sys_name):
    """The loudest render endpoint that is not our own SYS device and is above OTHER_LOUD_DB, or
    None. This is "the real one that is actually playing" the watchdog would switch to (auto) or warn
    about (named). Non-junk is preferred, but a PLAYING junk endpoint is still returned when nothing
    non-junk is playing, so a call moved onto the HDMI monitor speakers mid-session still earns a
    switch or warning (codex F4). Mirrors choose_sys, which likewise picks a junk endpoint when it is
    the only thing playing."""
    def loudest(include_junk):
        best = None
        for e in renders:
            if _same_name(e.get("name"), sys_name):
                continue
            if not include_junk and is_junk(e):
                continue
            if _peak(e) > OTHER_LOUD_DB and (best is None or _peak(e) > _peak(best)):
                best = e
        return best
    return loudest(False) or loudest(True)


def _loudest_capture(captures, mic_name):
    """The loudest OTHER capture endpoint with a KNOWN peak above OTHER_LOUD_DB, or None. A None
    (unknown) peak never qualifies, so a mic problem only offers a swap when there is real signal
    on another input."""
    best = None
    for e in captures:
        if _same_name(e.get("name"), mic_name):
            continue
        p = e.get("peak_db")
        if p is not None and float(p) > OTHER_LOUD_DB and (best is None or float(p) > _peak(best)):
            best = e
    return best
