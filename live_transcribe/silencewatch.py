"""Long-silence watch for a live session (WP-9b): the decision, with no I/O in it.

The failure this exists for is mundane and expensive: Volksmond sits there recording
happily while nothing at all is reaching it, because Windows switched the default mic to
a headset that is in a drawer, or the meeting app grabbed exclusive use of the device, or
somebody muted the mic in the OS mixer. The app looks busy, the timer climbs, and an hour
later there is a transcript of nothing. So: if EVERY channel we can measure has been below
a silence floor for long enough, say so once, out loud.

What is deliberately NOT here: any audio, any clock, any notification, any settings read.
This class takes numbers and returns a verdict, so the interesting rules (arming, resets,
snooze allowing exactly one more, the per-session cap) are testable in microseconds and
readable in one screen. web/app.py owns the 1 Hz thread, the session clock, the energy
rings, the toast and the banner.

The level numbers come from the WP-4 raw energy rings (transcribe.SysEnergyRing), which
are fed per 100 ms frame from the RAW pre-APM capture blocks. Raw matters: live AGC lifts
an empty room toward -25 dBFS, so a silence test on the post-AGC level (what /api/levels
shows) would never fire. `None` for a channel means "that ring has no frames in the window
at all", which is the dead-device case and counts AS silence once the session is armed.

Both channels must be silent, never just the mic. Sitting quietly while the far end talks
is ordinary meeting behaviour and must never be nudged; nothing arriving on either side is
what a broken capture looks like.
"""
import threading


class SilenceWatch:
    """Rolling verdict on "has everything been silent for too long?".

    threshold_s: how long every measured channel must stay below the floor before the
                 first nudge (default 5 minutes, the setting's default).
    floor_db:    dBFS at or below which a frame counts as silence. -50 sits below room
                 tone on a raw feed (-33..-57 measured) and well below any real speech.
    arm_s:       grace period after the first sample. A session that starts while the
                 room is quiet, or whose first blocks arrive late, must not begin
                 accumulating silence before the capture has actually settled.
    max_nudges:  hard cap per session, so a genuinely silent recording (someone left it
                 running) costs two interruptions, not sixty.

    Thread-safe: one internal lock guards every state transition. Two threads really do touch
    this - the 1 Hz watcher calls sample(), the request thread answering the banner calls
    snooze()/mute() - and none of the three is a single attribute write. sample() in particular
    reads _muted/_outstanding/_nudges and then writes all three, so a mute landing mid-decision
    could be overwritten and a nudge published for a banner the user had just closed. The lock is
    held only over arithmetic (no I/O, no callbacks), so it can never be the thing that blocks a
    request. Lock order where it matters: web/app.py always takes STATE.lock first, then this one.
    """

    def __init__(self, threshold_s=300.0, floor_db=-50.0, arm_s=30.0, max_nudges=2):
        self.threshold_s = float(threshold_s)
        self.floor_db = float(floor_db)
        self.arm_s = float(arm_s)
        self.max_nudges = int(max_nudges)
        self._first = None          # clock value of the first sample: the arming baseline
        self._last = None           # clock value of the last sample
        self._silent_since = None   # when the current run of silence began, or None
        self._nudges = 0            # nudges fired this session
        self._outstanding = False   # a nudge is on screen; do not fire another until it is answered
        self._muted = False         # the user closed it: silent for the rest of the session
        self._lock = threading.Lock()   # guards every transition above (see the class note)

    # -- the tick ----------------------------------------------------------

    def sample(self, now, levels):
        """Feed one tick. `now` is any monotonically rising clock (the session clock, in
        seconds); `levels` maps channel name -> loudest dBFS in the window just measured,
        or None when that channel produced no frames at all.

        Returns True EXACTLY on the tick a nudge should fire, so the caller can treat a
        True as "show it now" without tracking edges itself.

        An EMPTY `levels` means there is nothing to measure (no energy rings exist at all,
        e.g. a record-only session with no engine). That is absence of evidence, not
        evidence of silence: the watch stays unarmed and never trips on it.
        """
        if not levels:
            return False
        now = float(now)
        with self._lock:
            if self._first is None:
                self._first = now
            self._last = now
            if now - self._first < self.arm_s:
                # Not armed yet. Also keep the silence clock clear, so the grace period can
                # never be counted as part of a silent run.
                self._silent_since = None
                return False
            # A channel with no frames in the window counts as silent (dead device); a channel
            # above the floor on THIS tick clears the run for everyone.
            for db in levels.values():
                if db is not None and float(db) > self.floor_db:
                    self._silent_since = None
                    return False
            if self._silent_since is None:
                self._silent_since = now
                return False
            if self._muted or self._outstanding or self._nudges >= self.max_nudges:
                return False
            if now - self._silent_since < self.threshold_s:
                return False
            self._nudges += 1
            self._outstanding = True
            self._silent_since = None  # the next threshold is measured from here, not the start
            return True

    # -- the two user answers ---------------------------------------------

    def snooze(self, now=None):
        """"Keep recording": clear the nudge and restart the silence clock from now, so a
        still-silent session gets ONE more warning threshold_s later (up to max_nudges).

        `now` defaults to the last sampled clock value, so the request thread does not
        have to reconstruct the session clock to answer a banner.
        """
        with self._lock:
            if now is None:
                now = self._last
            self._outstanding = False
            self._silent_since = None if now is None else float(now)

    def mute(self):
        """"Not now, and stop asking": no further nudges for the rest of the session."""
        with self._lock:
            self._muted = True
            self._outstanding = False
            self._silent_since = None

    # -- introspection ----------------------------------------------------

    def state(self):
        """A plain dict for /api/status, the banner copy and the tests.

        Snapshotted under the lock, so a caller deciding on it (notably _silence_tick's
        re-check before publishing) never sees a half-applied mute."""
        with self._lock:
            silent_s = 0.0
            if self._silent_since is not None and self._last is not None:
                silent_s = max(0.0, self._last - self._silent_since)
            armed = (self._first is not None and self._last is not None
                     and (self._last - self._first) >= self.arm_s)
            return {
                "armed": armed,
                "silent_s": silent_s,
                "nudges": self._nudges,
                "muted": self._muted,
                "outstanding": self._outstanding,
                "threshold_s": self.threshold_s,
                "minutes": minutes_of(self.threshold_s),
                "exhausted": self._nudges >= self.max_nudges,
            }


def minutes_of(threshold_s):
    """The threshold in whole minutes, for the copy ("Nothing heard for 5 minutes").
    Never zero: a sub-minute threshold (a test, or a hand-edited settings file) still
    has to read as at least one minute."""
    return max(1, int(round(float(threshold_s) / 60.0)))
