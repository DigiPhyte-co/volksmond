"""Tests for the "model struggling to keep up" nudge signal path (both causes).

One banner, two engine-side causes. A live CPU session auto-downgrades
(transcribe.Engine._maybe_downgrade, ladder medium->small->base->tiny) and fires
on_downgrade(old_size, new_size); a live GPU session has no ladder, so when its chunk queue is deep
and still growing it fires on_struggle() instead (transcribe.Engine._trip_struggle, raised either by
_maybe_warn_gpu_struggle on the worker or by the producer-side net in on_chunk). Both become the
same one-time banner + single Windows toast, told apart by the nudge's "reason". This covers,
cheapest first:

  1. The Engine downgrade callback (transcribe.py): captured old_size is the PRE-swap size, fires
     once per rung, None is a no-op, a raising callback never breaks the worker, and it stays inert
     on GPU / Swivuriso / non-adaptive / a full ladder. Driven by calling _maybe_downgrade directly
     on an Engine built with __new__ (no real model load) against stubbed load_model/resolve_model.
  1b. The Engine GPU warning: the RTF sample is now recorded on EVERY backend (it used to be
     CPU-only, which is what left a starved GPU with no signal); the trip is a queue that is deep
     AND still growing, so two channels losing ground at RTF 0.6 warn while a mic-only session
     draining a transient backlog does not; a producer-side net trips at the high-water mark and on
     the first real drop without waiting for any window; nothing fires before the owner arms it (the
     catch-up replay must not spend the one-shot ratchet); the transcript notice is emitted only by
     the worker; and the callback is delivered off-thread, so a blocked notification backend stalls
     neither the worker nor the audio thread. Driven directly, through a deterministic queue
     simulation, and end-to-end through Engine._run against a sleep-only model stub.
  2. web/app.py's _on_downgrade and _on_gpu_struggle with a hand-set STATE and a monkeypatched
     notify.show: publish once per session with the right reason, the CPU one updates new_size in
     place on a later rung (keeping the original old_size), neither re-fires the toast, both guard
     STATE.stopping and a stale engine, neither re-nags after a dismiss, and both honour the
     setting + env kill switch.
  3. The endpoints and gating: /api/status carries the nudge (reason included), POST
     /api/struggle-nudge dismisses (session-only) and mutes (persists struggle_nudge=false), 409
     with no live session, CSRF, and the settings key exists and is patchable.

No audio, no pywin32, no real capture and no model load: the seams are load_model/resolve_model
(stubbed), the transcribing model itself (a sleep-only stub), notify.show (monkeypatched),
config.load/update (monkeypatched) and STATE (hand-set and restored).

Run:  python tests/test_struggle_signal.py   (from the project root; exit 0 = pass)
"""
import os
import heapq
import queue
import random
import sys
import threading
import time
from collections import deque

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe import config, notify, transcribe
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


# --- helpers ---------------------------------------------------------------

def _stub_engine(size="medium", family="whisper", adaptive=True, is_cpu=True, rtf=2.0,
                 device=None, pending=0, armed=True):
    """A minimal Engine with just the attributes the downgrade and struggle paths touch, built
    WITHOUT __init__ so no model is loaded. _rtf is filled to a full window whose average trips the
    CPU downgrade, and _last_rung_change is put far enough in the past to clear DOWNGRADE_MIN_SECONDS.
    `device` defaults to the backend `is_cpu` implies ("cpu"/"cuda"); pass "mlx" for the Apple path.
    `pending` seeds a REAL queue, so pending() and on_chunk are the engine's own."""
    eng = transcribe.Engine.__new__(transcribe.Engine)
    eng.family = family
    eng.adaptive = adaptive
    eng._is_cpu = is_cpu
    eng._device = device if device is not None else ("cpu" if is_cpu else "cuda")
    eng.size = size
    eng.language = "en"
    eng.engine = "auto"
    eng._compute_type = "int8"
    eng._cpu_threads = 4
    eng.model = object()
    eng.model_name = f"whisper-{size}"
    eng.is_fluister = False
    eng.subscribers = []
    eng.on_downgrade = None
    # CPU adaptive ladder attributes (head): _maybe_downgrade / _maybe_shed touch these.
    eng._swap = None
    eng._cold_decode = False
    eng._last_rung_change = 0.0            # monotonic 0 is always > DOWNGRADE_MIN_SECONDS ago
    eng._last_feed = {}                    # on_chunk -> _is_burst reads/writes this per source
    eng._front = deque()
    eng._recent = transcribe.RecentEmissions()
    eng.shed_seconds = 0.0
    eng.shed_events = 0
    # GPU/MLX queue-depth warning attributes (wp1): the struggle path touches these.
    eng.on_struggle = None
    eng.struggle_armed = False
    eng._struggle_warned = False
    eng._struggle_lock = threading.Lock()
    eng._struggle_notice_due = False
    eng._pending_hist = deque(maxlen=transcribe.DOWNGRADE_WINDOW)
    eng._arrival_hist = deque(maxlen=transcribe.STRUGGLE_ARRIVAL_WINDOW)
    eng._dropped = 0
    eng._busy = False
    eng._stop = threading.Event()
    eng._queue = queue.Queue(maxsize=transcribe.QUEUE_MAXSIZE)
    for i in range(pending):
        eng._queue.put(("SYS", [], float(i)))
    if armed:
        eng.arm_struggle()   # the real method, so its locked window reset is what tests run against
    _fill_rtf(eng, rtf)
    return eng


def _step(eng, t=1.0, timeout=5.0):
    """Drive one full ladder step. The next rung is now built on a HELPER thread so the worker keeps
    decoding meanwhile, so a step takes two passes through _maybe_downgrade: the first starts the
    build, the second installs it once it is ready. Returns True if a rung was installed."""
    before = eng.size
    eng._maybe_downgrade(t)
    swap = eng._swap
    if swap is not None:
        assert swap["done"].wait(timeout), "the helper-thread build never finished"
    eng._maybe_downgrade(t)
    return eng.size != before


def _fill_rtf(eng, val=2.0):
    eng._rtf = deque([val] * transcribe.DOWNGRADE_WINDOW, maxlen=transcribe.DOWNGRADE_WINDOW)


def _wait_fired(fired, want=1, timeout=3.0):
    """The struggle callback is delivered on its own thread (it must never block the worker or the
    capture thread), so a POSITIVE delivery assertion has to wait for it. The trip itself is
    synchronous, so negative assertions use eng._struggle_warned and never need this."""
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(fired) < want:
        time.sleep(0.005)
    return list(fired)


def _drive(eng, rtf, sources=2, chunk_secs=8.0, completions=80, backlog=0, arm=False, fired=None):
    """Deterministic model of a live session's queue: no real time, no audio, no model.

    Each completed transcription costs chunk_secs*rtf seconds of wall clock, during which each of
    `sources` capture streams produces one chunk every chunk_secs. Chunks go in through the real
    on_chunk (so the producer-side net is exercised) and every completion samples the depth and
    calls the worker-side check, exactly as _run does. `backlog` seeds an INHERITED queue first,
    the way the catch-up replay leaves one behind; pass arm=True to arm after seeding it, which is
    the real order of events. Every sample goes through the engine's own locked evaluation.

    This is the arithmetic the whole design turns on: with two sources, arrivals are 2 per
    chunk-length while the worker manages 1/rtf of them, so the queue grows whenever rtf > 0.5;
    with one source it grows only past 1.0.
    """
    fired = [] if fired is None else fired
    eng.on_struggle = lambda: fired.append(1)
    for i in range(backlog):
        eng._queue.put_nowait(("SYS", [], float(i)))     # inherited, not arriving
    if arm:
        eng.arm_struggle()
    arrivals, t = 0.0, 0.0
    for _ in range(completions):
        service = chunk_secs * rtf
        arrivals += sources * service / chunk_secs
        while arrivals >= 1.0:
            eng.on_chunk("SYS", [], t)
            arrivals -= 1.0
            if eng._struggle_warned:
                return fired     # stop AT the warning, so _dropped tells us what it cost
        try:
            eng._queue.get_nowait()          # this completion
        except queue.Empty:
            pass
        eng._busy = True                     # as _run samples it: the in-flight chunk counts
        eng._maybe_warn_gpu_struggle()
        eng._busy = False
        if eng._struggle_warned:
            return fired
        t += service
    return fired


def _simulate_chunkers(eng, rtf, backlog=0, seed=0, horizon=900.0, p_silence=0.8, sources=("MIC", "SYS")):
    """Event-driven simulation of the REAL chunker behaviour, on virtual time.

    _drive above feeds a fixed number of 8 s arrivals per completion, which is smooth enough to
    hide how the windows behave at the margin. capture_core is not smooth: one thread per source
    (:408), each waiting for 8 s of audio, cutting at the last silence in the final 2 s and
    CARRYING the tail forward (:531), or force-cutting at 1.5x (12 s) when it finds no silence. So
    emissions run 6 to 12 s, the two sources drift in and out of phase, and service time is
    proportional to each chunk's own duration.

    Returns a dict: warned, at (virtual seconds), dropped, end/start depth, and the mean span of an
    8-arrival window, which is what STRUGGLE_ARRIVAL_WINDOW is sized against.
    """
    rng = random.Random(seed)
    for i in range(backlog):
        eng._queue.put_nowait(("SYS", [], float(i)))
    eng.arm_struggle()
    fired = []
    eng.on_struggle = lambda: fired.append(1)
    tails = {s: 0.0 for s in sources}
    events = [(rng.uniform(0.0, 6.0), s, "arrive") for s in sources]   # independent phases
    heapq.heapify(events)
    busy, warned_at, arrivals = False, None, []
    while events:
        t, who, kind = heapq.heappop(events)
        if t > horizon:
            break
        if kind == "arrive":
            need = 8.0 - tails[who]
            if rng.random() < p_silence:
                cut = rng.uniform(6.0, 8.0)          # silence boundary inside the last 2 s
                dur, tails[who] = cut, 8.0 - cut
            else:
                dur, tails[who] = 12.0, 0.0          # force cut at 1.5x
                need += 4.0
            eng.on_chunk(who, [0.0] * int(dur * 16000), t)
            arrivals.append(t)
            heapq.heappush(events, (t + need, who, "arrive"))
        else:
            eng._busy = False
            eng._maybe_warn_gpu_struggle()
            busy = False
        if not busy and not eng._queue.empty():
            try:
                item = eng._queue.get_nowait()
                # on_chunk stamps a 5-tuple (source, audio, t_start, arrival, burst); a seeded
                # backlog item is a bare 3-tuple. audio is index 1 in both, tolerant like _run.
                audio = item[1]
            except queue.Empty:
                audio = None
            if audio is not None:
                busy = True
                eng._busy = True
                heapq.heappush(events, (t + (len(audio) / 16000.0) * rtf, who, "done"))
        if fired and warned_at is None:
            warned_at = t
    spans = [arrivals[i + 7] - arrivals[i] for i in range(max(0, len(arrivals) - 7))]
    return dict(warned=bool(fired), at=warned_at, dropped=eng._dropped, start=backlog,
                end=eng._queue.qsize(), span=(sum(spans) / len(spans)) if spans else 0.0,
                worst_span=min(spans) if spans else 0.0)


class _SlowModel:
    """A transcribing model that only burns time and returns nothing. The worker computes RTF as
    (elapsed / audio seconds), so a fixed sleep against a very short chunk is a controllable,
    sustained-slower-than-real-time session with no model, no audio and no GPU anywhere."""

    def __init__(self, work_secs):
        self.work_secs = work_secs

    def transcribe(self, audio, **kw):
        time.sleep(self.work_secs)
        return ([], None)


def _worker_engine(device="cuda", chunks=12, chunk_secs=0.005, work_secs=0.02):
    """A stub Engine wired well enough to run Engine._run() ON THE CALLING THREAD: _stop is already
    set, so the loop drains the pre-queued chunks and exits. Everything the loop touches is hand-set
    (no capture, no rings, no real model); _rtf starts EMPTY so the worker itself has to fill it."""
    eng = _stub_engine(is_cpu=(device == "cpu"), device=device)
    eng._rtf = deque(maxlen=transcribe.DOWNGRADE_WINDOW)
    eng.model = _SlowModel(work_secs)
    eng.initial_prompt = None
    eng.beam_size = 5
    eng._silence_gate = False          # no rings here; the gate is not what is under test
    eng._loop_guard_on = False
    eng._dropped = 0
    eng._pending_mic = []
    eng._pending_change = None
    eng._pending_recent_reset = False
    eng._change_lock = threading.Lock()
    eng._stop = threading.Event()
    eng._stop.set()                    # drain the queue, then exit
    eng._abort = threading.Event()
    eng._queue = queue.Queue(maxsize=64)
    for i in range(chunks):
        eng._queue.put(("SYS", [0.0] * int(16000 * chunk_secs), float(i)))
    return eng


class _stub_models:
    """Context manager: stub transcribe.load_model / resolve_model so _maybe_downgrade never loads a
    real model. resolve_model keeps the family it was given so a whisper stub stays whisper."""

    def __enter__(self):
        self._load = transcribe.load_model
        self._resolve = transcribe.resolve_model
        self._present = transcribe.model_present
        transcribe.load_model = lambda *a, **k: object()
        transcribe.resolve_model = lambda size, language, engine: (f"model-{size}", "whisper")
        transcribe.model_present = lambda model_id: True   # every rung is on this machine
        return self

    def __exit__(self, *exc):
        transcribe.load_model = self._load
        transcribe.resolve_model = self._resolve
        transcribe.model_present = self._present


_STATE_FIELDS = ("running", "stopping", "source_kind", "engine", "recording",
                 "struggle_nudge", "struggle_notified")


def _save_state():
    return {k: getattr(webapp.STATE, k) for k in _STATE_FIELDS}


def _restore_state(saved):
    for k, v in saved.items():
        setattr(webapp.STATE, k, v)


def _catch_toasts():
    """Replace notify.show with a recorder. Returns (calls, restore)."""
    calls = []
    saved = notify.show

    def fake_show(title, body="", *, tag=None, on_click=None):
        calls.append({"title": title, "body": body, "tag": tag, "on_click": on_click})
        return True

    notify.show = fake_show
    return calls, (lambda: setattr(notify, "show", saved))


class _on_setting:
    """Context manager: force _struggle_nudge_on() to a known answer by stubbing config.load and
    clearing the env kill switch, restoring both after."""

    def __init__(self, on=True):
        self.on = on

    def __enter__(self):
        self._load = config.load
        self._env = os.environ.get(webapp.STRUGGLE_ENV)
        os.environ.pop(webapp.STRUGGLE_ENV, None)
        config.load = lambda: {"struggle_nudge": bool(self.on)}
        return self

    def __exit__(self, *exc):
        config.load = self._load
        if self._env is None:
            os.environ.pop(webapp.STRUGGLE_ENV, None)
        else:
            os.environ[webapp.STRUGGLE_ENV] = self._env


# --- 1. the Engine callback (transcribe.py) --------------------------------

def test_callback_gets_preswap_old_size_and_new_size():
    with _stub_models():
        eng = _stub_engine(size="medium")
        calls = []
        eng.on_downgrade = lambda old, new: calls.append((old, new))
        _step(eng, 12.0)
    assert eng.size == "small", f"the downgrade must still happen; size is {eng.size}"
    assert calls == [("medium", "small")], f"callback must get the PRE-swap old size, got {calls}"
    print("  OK  on_downgrade fires with (pre-swap old_size, new_size) after a successful step")


def test_callback_none_is_a_noop_and_raising_never_breaks_the_worker():
    with _stub_models():
        # None callback: the swap still happens, nothing is called, nothing raised.
        eng = _stub_engine(size="medium")
        eng.on_downgrade = None
        _step(eng, 1.0)
        assert eng.size == "small"
        # A raising callback must be swallowed: the swap still happens and _maybe_downgrade returns
        # normally (a crash here would take the transcription worker thread down).
        eng2 = _stub_engine(size="small")

        def boom(old, new):
            raise RuntimeError("callback exploded")

        eng2.on_downgrade = boom
        _step(eng2, 2.0)               # must not raise
        assert eng2.size == "base", "a raising callback must not prevent the downgrade"
    print("  OK  a None callback is a no-op and a raising callback never breaks the downgrade")


def test_callback_steps_each_rung_and_stops_at_the_floor():
    with _stub_models():
        eng = _stub_engine(size="base")
        seen = []
        eng.on_downgrade = lambda old, new: seen.append((old, new))
        _step(eng, 1.0)                # base -> tiny
        assert eng.size == "tiny" and seen == [("base", "tiny")], (eng.size, seen)
        # Already on the fastest rung: no further step, no callback.
        _fill_rtf(eng)
        eng._last_rung_change = 0.0
        _step(eng, 2.0)
        assert eng.size == "tiny" and seen == [("base", "tiny")], "must not step past the floor"
    print("  OK  callback fires per rung and never fires once on the fastest rung")


def test_callback_inert_off_the_cpu_adaptive_path():
    with _stub_models():
        for label, kw in (("GPU", {"is_cpu": False}),
                          ("non-adaptive", {"adaptive": False}),
                          ("swivuriso", {"family": "swivuriso"}),
                          ("rtf below threshold", {"rtf": 0.1})):
            eng = _stub_engine(size="medium", **kw)
            fired = []
            eng.on_downgrade = lambda old, new: fired.append((old, new))
            _step(eng, 1.0)
            assert eng.size == "medium", f"{label}: must not downgrade"
            assert fired == [], f"{label}: callback must not fire"
        # A partial (not-yet-full) RTF window must not downgrade either.
        eng = _stub_engine(size="medium")
        eng._rtf = deque([2.0, 2.0], maxlen=transcribe.DOWNGRADE_WINDOW)   # len 2 < maxlen 4
        fired = []
        eng.on_downgrade = lambda old, new: fired.append(1)
        _step(eng, 1.0)
        assert eng.size == "medium" and fired == [], "an unfilled RTF window must not downgrade"
    print("  OK  callback stays inert on GPU / non-adaptive / Swivuriso / low-RTF / unfilled window")

# --- 1b. the "cannot hold real time" warning (transcribe.py) ---------------

def test_worker_records_rtf_on_a_non_cpu_engine_and_a_draining_backlog_never_warns():
    """Two properties in one real run of Engine._run().

    First the regression this feature exists for: the RTF sample used to be appended only inside
    `if self._is_cpu`, so on cuda/mlx the window stayed permanently EMPTY and nothing could react.
    Second, and just as important, a backlog that is DRAINING must never warn: this is the shape
    of every expected catch-up, and the queue-growth test is what makes it silent (the old
    RTF-threshold version would have warned here, on a session that was recovering fine)."""
    eng = _worker_engine(device="cuda")
    notices, fired = [], []
    eng.subscribe(lambda seg: notices.append(seg))
    eng.on_struggle = lambda: fired.append(1)
    eng._run()
    assert len(eng._rtf) == eng._rtf.maxlen, f"a cuda session recorded no RTF at all: {eng._rtf}"
    assert sum(eng._rtf) / len(eng._rtf) > 1.0, list(eng._rtf)   # genuinely slower than real time
    assert len(eng._pending_hist) == eng._pending_hist.maxlen, list(eng._pending_hist)
    assert eng._struggle_warned is False, "a draining backlog was reported as a fault"
    assert fired == [] and notices == [], (fired, [s.text for s in notices])
    print("  OK  a cuda worker records RTF + queue depth, and a draining backlog never warns")


def test_two_channel_session_losing_ground_at_rtf_0_6_warns():
    """The case a fixed RTF threshold got wrong. Two sources feed ONE worker, so break-even is
    RTF 0.5: at 0.6 the queue grows without bound and audio will be dropped, yet the old
    GPU_STRUGGLE_RTF of 0.7 could never fire. Queue growth catches it."""
    eng = _stub_engine(is_cpu=False, device="cuda")
    fired = _drive(eng, rtf=0.6, sources=2)
    assert eng._struggle_warned is True, "a two-channel session losing ground at RTF 0.6 never warned"
    assert _wait_fired(fired) == [1], fired
    assert eng._struggle_notice_due is True, "the worker was never told to emit the notice"
    # It warned from the WORKER side, before the queue ever got near dropping.
    assert eng._queue.qsize() < transcribe.STRUGGLE_QUEUE_HIGH, eng._queue.qsize()
    assert eng._dropped == 0, "the warning must arrive before any audio is lost"
    print("  OK  two channels at RTF 0.6 (break-even 0.5) warn, well before the first drop")


def test_mic_only_session_with_a_transient_backlog_does_not_warn():
    """The mirror case. One source means break-even is RTF 1.0, so 0.6 is comfortably fine and the
    seeded backlog just drains. A threshold tuned for two channels would have over-fired here."""
    eng = _stub_engine(is_cpu=False, device="cuda")
    fired = _drive(eng, rtf=0.6, sources=1, backlog=12)
    assert eng._struggle_warned is False, "a mic-only session that was catching up fine warned"
    assert fired == [] and eng._dropped == 0
    assert eng._queue.qsize() == 0, f"the backlog should have drained: {eng._queue.qsize()}"
    print("  OK  a mic-only session at RTF 0.6 drains its transient backlog in silence")


def test_producer_warns_from_any_inherited_depth_and_at_the_ceiling_only_on_the_drop():
    """H2, plus the one shape that is deliberately allowed to warn late.

    An earlier "the floor must first reach a healthy depth" gate disabled this net for every
    session armed above that depth, which is every session handed a backlog by a slow model load.
    Worked through at RTF 4 from depth 16, the queue exhausts in ~70 s while four worker
    completions take ~128 s, so neither path spoke and the first DROP became the warning. The
    trend window has no precondition: growth is growth, whatever depth it starts from. Seeds 7, 16
    and 23 therefore all warn with nothing dropped.

    Seeds 30 and 32 assert the opposite, and that is ACCEPTED, chosen rather than missed: armed
    that close to the 32-slot ceiling only one or two samples fit before the queue overflows, so
    the window cannot fill and the unconditional drop path is what claims the warning. At 30 of 32
    loss is one chunk away whatever we do, so the user learns within a chunk instead of never, and
    an arm-time special case for a near-full queue is the kind of exception that cost two earlier
    revisions of this design. See the note beside STRUGGLE_ARRIVAL_WINDOW.
    """
    for seed in (7, 16, 23, 30, 32):
        eng = _stub_engine(is_cpu=False, device="cuda", armed=False)
        fired = _drive(eng, rtf=4.0, sources=2, completions=40, backlog=seed, arm=True)
        assert eng._struggle_warned is True, f"inherited {seed}: starved and never warned"
        assert _wait_fired(fired) == [1], (seed, fired)
        if seed <= 23:
            # There was still room to warn inside, so it must have landed before any loss.
            assert eng._dropped == 0, f"inherited {seed}: warned only after losing audio"
        else:
            # Armed with the queue all but full: the trend window cannot fill before the queue
            # does, so the unconditional first-drop path is what speaks. Documented, not ideal.
            assert eng._dropped >= 1, f"inherited {seed}: expected the drop path to be the warning"
    print("  OK  the producer net warns from any inherited depth (7/16/23 before a single drop)")


def test_producer_warns_when_a_partial_drain_reverses():
    """The other half of H2's shape: an inherited backlog that drains PARTWAY and then reverses.
    A one-shot baseline (or a floor that had not reached a healthy depth) left the net asleep for
    the rest of the session; a trend window simply sees the reversal."""
    for seed in (16, 23, 30, 32):
        eng = _stub_engine(is_cpu=False, device="cuda", armed=False)
        fired = []
        _drive(eng, rtf=0.1, sources=2, completions=12, backlog=seed, arm=True, fired=fired)
        assert eng._struggle_warned is False, f"inherited {seed}: the healthy drain warned"
        drained = eng._queue.qsize()
        assert drained < seed, f"inherited {seed}: the queue did not drain ({drained})"
        _drive(eng, rtf=4.0, sources=2, completions=40, fired=fired)
        assert eng._struggle_warned is True, f"inherited {seed}: the reversal was never caught"
        assert _wait_fired(fired) == [1], (seed, fired)
        assert eng._dropped == 0, f"inherited {seed}: the reversal warning came after loss"
    print("  OK  a partial drain that reverses warns, from every inherited depth, before any loss")


def test_a_healthy_drain_from_a_deep_backlog_stays_silent():
    """The false positive the trend window has to avoid: an inherited backlog on a healthy card.
    It descends THROUGH the high-water mark, and the per-source chunker threads pick their own
    silence boundaries so depths jitter by a chunk or two on the way down. Newest below oldest is
    what makes that safe, with no gate and no baseline."""
    eng = _stub_engine(is_cpu=False, device="cuda", armed=False)
    fired = _drive(eng, rtf=0.1, sources=2, completions=60, backlog=30, arm=True)
    assert eng._struggle_warned is False, "a draining inherited backlog raised the alarm"
    assert fired == [] and eng._dropped == 0
    assert eng._queue.qsize() == 0, f"the backlog should have drained: {eng._queue.qsize()}"
    print("  OK  a deep inherited backlog draining on a healthy card stays silent")


def test_producer_warns_on_the_first_drop_without_any_window():
    """Loss needs no trend. Neither window may be a precondition for reporting audio that is
    already gone."""
    eng = _stub_engine(is_cpu=False, device="cuda", armed=False)
    for i in range(transcribe.QUEUE_MAXSIZE):
        eng._queue.put_nowait(("SYS", [], float(i)))
    eng.arm_struggle()                       # armed with the queue already full
    fired = []
    eng.on_struggle = lambda: fired.append(1)
    assert eng.on_chunk("SYS", [], 1.0) is False, "the queue should be full"
    assert eng._dropped == 1 and eng._struggle_warned is True, "the first real drop must warn"
    assert list(eng._arrival_hist) == [] and list(eng._pending_hist) == [], "no window was needed"
    assert _wait_fired(fired) == [1], fired
    print("  OK  the first dropped chunk warns outright, with neither window filled")


def test_pre_arm_evidence_can_never_spend_the_warning():
    """H1. The worker used to evaluate its window OUTSIDE the lock and only take it to claim the
    ratchet, so this interleaving spent the one-shot warning on wholly pre-arm evidence: worker
    passes its checks on the catch-up's history, arm_struggle() clears and arms, worker resumes and
    claims. (It could also index a deque the clear had just emptied.) Sampling, verdict and claim
    now happen in ONE acquisition, so the interleaving does not exist."""
    # (a) the semantics: a full pre-arm window is discarded, so the next sample cannot claim on it.
    eng = _stub_engine(is_cpu=False, device="cuda", armed=False)
    for d in (20, 22, 24, 26):
        eng._pending_hist.append(d)
    for d in range(24, 32):
        eng._arrival_hist.append(d)
    eng.arm_struggle()
    assert list(eng._pending_hist) == [] and list(eng._arrival_hist) == [], "arming kept stale evidence"
    eng._maybe_warn_gpu_struggle()
    assert eng._struggle_warned is False, "one post-arm sample claimed the warning"
    # (b) the race itself, hammered: arming against evaluation, with the pre-arm window primed to
    # trip. Whichever wins the lock, the answer is the same, and nothing may raise.
    errors = []
    for _ in range(200):
        e = _stub_engine(is_cpu=False, device="cuda", armed=False)
        for d in (20, 22, 24, 26):
            e._pending_hist.append(d)
        ready = threading.Barrier(2)

        def arm():
            try:
                ready.wait(timeout=5.0)
                e.arm_struggle()
            except Exception as ex:
                errors.append(repr(ex))

        def evaluate():
            try:
                ready.wait(timeout=5.0)
                e._maybe_warn_gpu_struggle()
            except Exception as ex:
                errors.append(repr(ex))

        ts = [threading.Thread(target=arm), threading.Thread(target=evaluate)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(5.0)
        assert e._struggle_warned is False, "pre-arm evidence claimed the one-shot warning"
    assert errors == [], errors[:3]
    print("  OK  pre-arm evidence can never claim the warning, however arming interleaves")


def test_under_real_chunker_jitter_a_recovering_session_is_never_warned():
    """M2: the smooth simulation above justifies the window lengths with an argument it does not
    test. This one drives the engine from independently phased per-source chunkers across the real
    6 to 12 s boundary behaviour (see _simulate_chunkers) and pins the two properties that matter.

    MEASURED, and recorded here so the next reader does not have to re-derive it:

      * an 8-arrival window spans ~28 s on average and ~20 s at its tightest (all-6 s cuts on two
        sources), not the ~32 s the earlier comment claimed. Corrected there.
      * the recovery case is CLEAN: a card fast enough to be recovering (RTF <= 0.25) never warns
        while draining an inherited backlog, at any starting depth up to 30. That is the case this
        design must not get wrong, because a slow first-run model load routinely hands the engine
        20+ chunks.
      * there IS a marginal band, RTF ~0.30 to ~0.50 with a backlog already past the completion
        threshold, where a session that ends up draining can still warn. The claim comes from the
        COMPLETION window (4 samples, shared with the CPU ladder as DOWNGRADE_WINDOW), not the
        arrival window: at 80 to 100% of capacity the depth wobbles up within any 4 samples even
        while the long-run trend is down. Deliberately NOT chased. Two channels break even at
        RTF 0.5, so those sessions are inside 20% of losing audio with a minute or more already
        queued, the banner they get offers exactly the right remedy, and the alternative is a
        condition that would re-open a blind spot. A spurious banner is dismissible; a missed
        warning is lost audio.
    """
    # The protection that must hold: recovering, at every depth a slow model load could hand us.
    for backlog in (10, 20, 26, 30):
        for rtf in (0.05, 0.15, 0.25):
            for seed in range(4):
                eng = _stub_engine(is_cpu=False, device="cuda", armed=False)
                r = _simulate_chunkers(eng, rtf=rtf, backlog=backlog, seed=seed)
                assert not r["warned"], (
                    f"backlog {backlog} at RTF {rtf} (seed {seed}) warned while recovering: {r}")
                assert r["end"] == 0 and r["dropped"] == 0, r
    # And the harm that must be caught, from an empty queue, with the jitter running.
    for rtf in (0.55, 0.7, 1.0):
        for seed in range(4):
            eng = _stub_engine(is_cpu=False, device="cuda", armed=False)
            r = _simulate_chunkers(eng, rtf=rtf, backlog=0, seed=seed)
            assert r["warned"], f"a session losing ground at RTF {rtf} (seed {seed}) never warned: {r}"
            assert r["at"] is not None and r["at"] < 400.0, r
    # The window length claim, measured rather than asserted from theory.
    eng = _stub_engine(is_cpu=False, device="cuda", armed=False)
    r = _simulate_chunkers(eng, rtf=0.1, backlog=0, seed=7)
    assert 18.0 <= r["worst_span"] <= 32.0, f"8 arrivals spanned {r['worst_span']:.1f}s at worst"
    assert 22.0 <= r["span"] <= 34.0, f"8 arrivals spanned {r['span']:.1f}s on average"
    print(f"  OK  real chunker jitter: recovery never warns, real loss always does "
          f"(8 arrivals span ~{r['span']:.0f}s, ~{r['worst_span']:.0f}s at worst)")


def test_the_depth_sample_is_taken_inside_the_lock():
    """H of round 5: the depth used to be measured by the CALLER and passed in, so a thread could
    read a depth, be descheduled, and have its now-stale value appended as the first sample of a
    window that arm_struggle (or a model swap) had cleared in the meantime. A pre-arm 24 landing
    ahead of a healthy 29, 28, 27 reads as growth and spends the one-shot warning on a session that
    is recovering. The sample is taken inside the lock now, so a sample and a clear are mutually
    exclusive by construction: this test pauses INSIDE the sampling call and proves the clear
    cannot interleave.
    """
    eng = _stub_engine(is_cpu=False, device="cuda")
    eng._arrival_hist.append(24)                 # a leftover the clear is about to remove
    sampling, release = threading.Event(), threading.Event()

    class _PausingQueue:
        """Stands in for the chunk queue and stops the world inside qsize(), which is exactly where
        the depth is read. Everything else passes through to the real queue."""

        def __init__(self, inner):
            self._inner = inner

        def qsize(self):
            sampling.set()
            release.wait(timeout=5.0)
            return 27

        def __getattr__(self, name):
            return getattr(self._inner, name)

    eng._queue = _PausingQueue(eng._queue)
    sampler = threading.Thread(target=lambda: eng._struggle_evaluate(arrival=True))
    sampler.start()
    assert sampling.wait(3.0), "the evaluation never reached the sampling point"
    clearer = threading.Thread(target=eng.arm_struggle)   # one of the two clear sites
    clearer.start()
    clearer.join(0.3)
    assert clearer.is_alive(),         "a window clear ran while a depth sample was in flight: the sample is outside the lock"
    release.set()
    sampler.join(5.0)
    clearer.join(5.0)
    assert not sampler.is_alive() and not clearer.is_alive(), "a thread never finished"
    assert list(eng._arrival_hist) == [],         f"a sample taken before the clear survived it: {list(eng._arrival_hist)}"
    assert eng._struggle_warned is False, "a stale sample claimed the warning"
    print("  OK  the depth sample happens inside the lock: no clear can interleave with it")


def test_concurrent_producers_cannot_corrupt_the_window_or_double_spend():
    """M1: MIC and SYS reach on_chunk from independent chunker threads (capture_core starts one per
    source), so the depth window and the ratchet are touched concurrently. A read-then-write pair
    outside the lock could lose an update; everything is inside one lock now. The warning must be
    delivered exactly once no matter how the two threads interleave."""
    eng = _stub_engine(is_cpu=False, device="cuda")
    fired, errors = [], []
    eng.on_struggle = lambda: fired.append(1)
    ready = threading.Barrier(2)

    def producer(src):
        try:
            ready.wait(timeout=5.0)
            for i in range(200):
                eng.on_chunk(src, [], float(i))
        except Exception as e:
            errors.append(repr(e))

    ts = [threading.Thread(target=producer, args=(s,)) for s in ("MIC", "SYS")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10.0)
    assert errors == [], errors
    assert len(eng._arrival_hist) <= eng._arrival_hist.maxlen, list(eng._arrival_hist)
    assert eng._struggle_warned is True, "400 chunks into a 32-slot queue and nothing warned"
    assert _wait_fired(fired) == [1], f"the one-shot warning was delivered {len(fired)} times"
    print("  OK  two concurrent producers: window intact, ratchet spent exactly once, no errors")


def test_the_warning_is_inert_on_cpu_and_on_a_file_import():
    # _struggle_evaluate is the single gate both sampling points go through, so testing it directly
    # is the strongest form: if it refuses here, no path can warn.
    for label, kw in (("cpu", {"is_cpu": True, "device": "cpu"}),
                      ("file import", {"is_cpu": False, "device": "cuda", "adaptive": False}),
                      ("not yet armed", {"is_cpu": False, "device": "cuda", "armed": False})):
        eng = _stub_engine(**kw)
        assert eng._struggle_evaluate(forced="test") is None, f"{label}: claimed the warning"
        assert eng._struggle_warned is False and eng._struggle_notice_due is False, label
    # A CPU session grinding badly is the ladder's job (_maybe_downgrade), never this warning.
    cpu = _stub_engine(is_cpu=True, device="cpu")
    _drive(cpu, rtf=4.0, sources=2, completions=20)
    assert cpu._struggle_warned is False, "a CPU session warned instead of using its ladder"
    print("  OK  inert on CPU, on a file import and before arming, on every path")


def test_the_warning_fires_once_per_session_and_survives_a_raising_callback():
    eng = _stub_engine(is_cpu=False, device="cuda")
    fired = []
    eng.on_struggle = lambda: fired.append(1)
    eng._deliver_struggle(eng._struggle_evaluate(forced="first"))
    eng._deliver_struggle(eng._struggle_evaluate(forced="second"))   # a second cause, later
    assert _wait_fired(fired, want=1) == [1], f"fired more than once: {fired}"
    assert eng._struggle_warned is True
    # A raising callback runs on its own thread: it cannot reach the caller, and must not stop the
    # transcript notice from being owed.
    eng2 = _stub_engine(is_cpu=False, device="cuda")
    ran = threading.Event()

    def boom():
        ran.set()
        raise RuntimeError("callback exploded")

    eng2.on_struggle = boom
    eng2._deliver_struggle(eng2._struggle_evaluate(forced="boom"))   # must not raise
    assert ran.wait(3.0), "the callback never ran"
    assert eng2._struggle_warned is True and eng2._struggle_notice_due is True
    # A None callback (CLI, tests) is a no-op: the notice is still owed, nothing raises.
    eng3 = _stub_engine(is_cpu=False, device="cuda")
    eng3._deliver_struggle(eng3._struggle_evaluate(forced="no listener"))
    assert eng3._struggle_warned is True and eng3._struggle_notice_due is True
    print("  OK  fires once per session; a raising or absent callback is harmless")


def test_the_struggle_callback_never_blocks_its_caller():
    """M1 of round 2: the real callback reads settings from disk and calls notify.show(), whose
    first backend initialisation can block for seconds. It is raised from the sole transcription
    worker AND from the real-time capture thread, so neither may wait on it."""
    eng = _stub_engine(is_cpu=False, device="cuda")
    entered, release = threading.Event(), threading.Event()

    def slow_cb():
        entered.set()
        release.wait(timeout=10.0)

    eng.on_struggle = slow_cb
    try:
        t0 = time.monotonic()
        eng._deliver_struggle(eng._struggle_evaluate(forced="test"))
        trip_elapsed = time.monotonic() - t0
        assert entered.wait(3.0), "the callback never ran on its own thread"
        # ...and with the listener still stuck, the audio thread's next chunk is unaffected.
        t1 = time.monotonic()
        eng.on_chunk("SYS", [], 1.0)
        chunk_elapsed = time.monotonic() - t1
    finally:
        release.set()
    assert trip_elapsed < 0.5, f"the trip waited {trip_elapsed:.2f}s on a blocked listener"
    assert chunk_elapsed < 0.5, f"the next chunk waited {chunk_elapsed:.2f}s on a blocked listener"
    print("  OK  a blocked notification backend delays neither the trip nor the next chunk")


def test_the_callback_is_delivered_even_when_stdout_is_broken():
    """M2: the diagnostic print used to run BEFORE the callback and outside its guard, so a stdout
    that raises (a windowed build's log sink failing) killed the delivery thread and the user never
    got the banner or the toast. Delivery comes first now, and the print is guarded."""
    eng = _stub_engine(is_cpu=False, device="cuda")
    fired = []
    eng.on_struggle = lambda: fired.append(1)

    class _BrokenStdout:
        def write(self, *a, **k):
            raise OSError("stdout is gone")

        def flush(self, *a, **k):
            raise OSError("stdout is gone")

    saved = sys.stdout
    try:
        sys.stdout = _BrokenStdout()
        eng._deliver_struggle(eng._struggle_evaluate(forced="test"))
        got = _wait_fired(fired)
    finally:
        sys.stdout = saved
    assert got == [1], "a broken stdout swallowed the warning delivery"
    print("  OK  the listener is still called when stdout raises on every write")


def test_the_notice_is_emitted_by_the_worker_never_by_the_capture_thread():
    """_fanout is worker-thread-only: its subscribers write the transcript file and the SSE stream.
    A claim made on the capture thread must therefore only leave the notice OWED, and the worker
    must emit it on its next chunk, through _emit_notice and never _route (a synthetic line must
    not enter RecentEmissions or SysTextRing)."""
    eng = _worker_engine(device="cuda", chunks=2)
    seen, routed = [], []
    eng.subscribe(lambda seg: seen.append(seg))
    eng._route = lambda seg: routed.append(seg)
    eng._deliver_struggle(eng._struggle_evaluate(forced="from the capture thread"))
    assert eng._struggle_notice_due is True
    assert seen == [], "the claim fanned out a segment off the worker thread"
    eng._run()
    assert len(seen) == 1, [s.text for s in seen]
    assert seen[0].text == transcribe.STRUGGLE_NOTICE, seen[0].text
    assert seen[0].source == "SYS", seen[0].source
    assert eng._struggle_notice_due is False, "the notice was left owed after being emitted"
    assert routed == [], "a synthetic line must never go through _route"
    print("  OK  the notice is owed by the claim and emitted once by the worker, never via _route")


# --- 2. the _on_downgrade handler (web/app.py) -----------------------------

def test_handler_publishes_once_and_updates_in_place():
    saved = _save_state()
    calls, restore_notify = _catch_toasts()
    eng = object()
    try:
        with _on_setting(on=True):
            webapp.STATE.running = True
            webapp.STATE.source_kind = "live"
            webapp.STATE.stopping = False
            webapp.STATE.recording = False
            webapp.STATE.engine = eng
            webapp.STATE.struggle_nudge = None
            webapp.STATE.struggle_notified = False
            # First downgrade: banner appears, toast fires once.
            webapp._on_downgrade(eng, "medium", "small")
            assert webapp.STATE.struggle_nudge == {"reason": "cpu-downgrade", "old_size": "medium",
                                                   "new_size": "small", "recording": False,
                                                   "indicative": False,
                                                   "shed_seconds": 0}, webapp.STATE.struggle_nudge
            assert webapp.STATE.struggle_notified is True
            assert len(calls) == 1 and calls[0]["tag"] == "struggle", calls
            assert callable(calls[0]["on_click"]), "the toast must be clickable back to the app"
            # A later rung updates new_size IN PLACE (original old_size kept), no second toast.
            webapp._on_downgrade(eng, "small", "base")
            assert webapp.STATE.struggle_nudge == {"reason": "cpu-downgrade", "old_size": "medium",
                                                   "new_size": "base", "recording": False,
                                                   "indicative": False,
                                                   "shed_seconds": 0}, webapp.STATE.struggle_nudge
            assert len(calls) == 1, f"the toast must fire only once per session, got {len(calls)}"
            # `recording` is captured at emit time: once recording, the next update reflects it.
            webapp.STATE.recording = True
            webapp._on_downgrade(eng, "base", "tiny")
            assert webapp.STATE.struggle_nudge == {"reason": "cpu-downgrade", "old_size": "medium",
                                                   "new_size": "tiny", "recording": True,
                                                   "indicative": False,
                                                   "shed_seconds": 0}, webapp.STATE.struggle_nudge
            assert len(calls) == 1
    finally:
        restore_notify()
        _restore_state(saved)
    print("  OK  first downgrade publishes + toasts once; later rungs update new_size in place")


def test_handler_guards_stopping_and_a_stale_engine():
    saved = _save_state()
    calls, restore_notify = _catch_toasts()
    eng = object()
    try:
        with _on_setting(on=True):
            # Stopping: never nudge a session that is finishing.
            webapp.STATE.running = True
            webapp.STATE.source_kind = "live"
            webapp.STATE.stopping = True
            webapp.STATE.engine = eng
            webapp.STATE.struggle_nudge = None
            webapp.STATE.struggle_notified = False
            assert webapp._on_downgrade(eng, "medium", "small") is None
            assert webapp.STATE.struggle_nudge is None and calls == [], "nudged a stopping session"
            # Stale engine: a callback from an engine that is no longer the session's must not
            # publish onto the current one.
            webapp.STATE.stopping = False
            webapp.STATE.engine = object()       # somebody else is current now
            assert webapp._on_downgrade(eng, "medium", "small") is None
            assert webapp.STATE.struggle_nudge is None and calls == [], "published for a stale engine"
    finally:
        restore_notify()
        _restore_state(saved)
    print("  OK  a stopping session and a stale engine are both suppressed (no banner, no toast)")


def test_handler_does_not_renag_after_a_dismiss():
    saved = _save_state()
    calls, restore_notify = _catch_toasts()
    eng = object()
    try:
        with _on_setting(on=True):
            webapp.STATE.running = True
            webapp.STATE.source_kind = "live"
            webapp.STATE.stopping = False
            webapp.STATE.recording = False
            webapp.STATE.engine = eng
            webapp.STATE.struggle_nudge = None
            webapp.STATE.struggle_notified = False
            webapp._on_downgrade(eng, "medium", "small")     # surfaces once
            assert webapp.STATE.struggle_nudge is not None and len(calls) == 1
            # User dismisses (banner cleared, notified latch stays set).
            webapp.STATE.struggle_nudge = None
            # A later rung must NOT re-raise a dismissed banner and must NOT toast again.
            assert webapp._on_downgrade(eng, "small", "base") is None
            assert webapp.STATE.struggle_nudge is None, "a dismissed banner was re-raised"
            assert len(calls) == 1, "the toast fired again after a dismiss"
    finally:
        restore_notify()
        _restore_state(saved)
    print("  OK  a dismissed banner is not re-raised by a later rung (fires once per session)")


def test_handler_gated_by_setting_and_env():
    # The truth table of the gate helpers, plus the handler short-circuiting when off.
    saved_load = config.load
    saved_env = os.environ.get(webapp.STRUGGLE_ENV)
    saved = _save_state()
    calls, restore_notify = _catch_toasts()
    eng = object()
    try:
        os.environ.pop(webapp.STRUGGLE_ENV, None)
        config.load = lambda: {"struggle_nudge": True}
        assert webapp._struggle_nudge_on() is True
        config.load = lambda: {"struggle_nudge": False}
        assert webapp._struggle_nudge_on() is False, "the setting does not switch it off"
        config.load = lambda: {}
        assert webapp._struggle_nudge_on() is True, "a missing key defaults ON"
        # Env kill switch wins over the setting and short-circuits before any read.
        config.load = lambda: {"struggle_nudge": True}
        for val in ("0", "false", "no", "off"):
            os.environ[webapp.STRUGGLE_ENV] = val
            assert webapp._struggle_nudge_on() is False, f"{val} did not kill the surfacing"
        os.environ[webapp.STRUGGLE_ENV] = "1"
        assert webapp._struggle_nudge_on() is True
        # And the handler itself publishes nothing when the surfacing is off.
        os.environ.pop(webapp.STRUGGLE_ENV, None)
        config.load = lambda: {"struggle_nudge": False}
        webapp.STATE.running = True
        webapp.STATE.source_kind = "live"
        webapp.STATE.stopping = False
        webapp.STATE.engine = eng
        webapp.STATE.struggle_nudge = None
        webapp.STATE.struggle_notified = False
        assert webapp._on_downgrade(eng, "medium", "small") is None
        assert webapp.STATE.struggle_nudge is None and calls == [], "surfaced while switched off"
        assert webapp.STATE.struggle_notified is False, "a gated-off downgrade must not latch"
    finally:
        restore_notify()
        config.load = saved_load
        if saved_env is None:
            os.environ.pop(webapp.STRUGGLE_ENV, None)
        else:
            os.environ[webapp.STRUGGLE_ENV] = saved_env
        _restore_state(saved)
    print("  OK  gate: setting off, env kill switch, sane default ON, handler short-circuits")


def test_gpu_handler_publishes_the_gpu_busy_reason_once():
    saved = _save_state()
    calls, restore_notify = _catch_toasts()
    eng = object()
    try:
        with _on_setting(on=True):
            webapp.STATE.running = True
            webapp.STATE.source_kind = "live"
            webapp.STATE.stopping = False
            webapp.STATE.recording = False
            webapp.STATE.engine = eng
            webapp.STATE.struggle_nudge = None
            webapp.STATE.struggle_notified = False
            published = webapp._on_gpu_struggle(eng)
            # No old_size/new_size: nothing was switched, so there is nothing honest to put there.
            assert webapp.STATE.struggle_nudge == {"reason": "gpu-busy",
                                                   "recording": False}, webapp.STATE.struggle_nudge
            assert published == webapp.STATE.struggle_nudge
            assert webapp.STATE.struggle_notified is True
            assert len(calls) == 1 and calls[0]["tag"] == "struggle", calls
            assert callable(calls[0]["on_click"]), "the toast must be clickable back to the app"
            # The engine only fires once, but a second call must still be harmless: no second toast.
            webapp._on_gpu_struggle(eng)
            assert len(calls) == 1, f"the toast must fire only once per session, got {len(calls)}"
    finally:
        restore_notify()
        _restore_state(saved)
    print("  OK  the GPU handler publishes reason=gpu-busy (no sizes) and toasts once")


def test_gpu_handler_guards_stopping_a_stale_engine_a_dismiss_and_the_setting():
    saved = _save_state()
    calls, restore_notify = _catch_toasts()
    eng = object()
    try:
        with _on_setting(on=True):
            webapp.STATE.running = True
            webapp.STATE.source_kind = "live"
            webapp.STATE.recording = False
            webapp.STATE.struggle_nudge = None
            webapp.STATE.struggle_notified = False
            # Stopping: never nudge a session that is finishing.
            webapp.STATE.stopping = True
            webapp.STATE.engine = eng
            assert webapp._on_gpu_struggle(eng) is None
            assert webapp.STATE.struggle_nudge is None and calls == [], "nudged a stopping session"
            # Stale engine: a callback from an engine that is no longer the session's (the
            # catch-up drain, a switch) must not publish onto the current one.
            webapp.STATE.stopping = False
            webapp.STATE.engine = object()
            assert webapp._on_gpu_struggle(eng) is None
            assert webapp.STATE.struggle_nudge is None and calls == [], "published for a stale engine"
            # Surfaced then dismissed: do not nag again.
            webapp.STATE.engine = eng
            webapp._on_gpu_struggle(eng)
            assert webapp.STATE.struggle_nudge is not None and len(calls) == 1
            webapp.STATE.struggle_nudge = None            # the user dismissed it
            assert webapp._on_gpu_struggle(eng) is None
            assert webapp.STATE.struggle_nudge is None, "a dismissed banner was re-raised"
            assert len(calls) == 1
        # Setting off (and the env kill switch, shared with the CPU path): publish nothing at all.
        with _on_setting(on=False):
            webapp.STATE.stopping = False
            webapp.STATE.engine = eng
            webapp.STATE.struggle_nudge = None
            webapp.STATE.struggle_notified = False
            assert webapp._on_gpu_struggle(eng) is None
            assert webapp.STATE.struggle_nudge is None and len(calls) == 1, "surfaced while switched off"
            assert webapp.STATE.struggle_notified is False, "a gated-off warning must not latch"
    finally:
        restore_notify()
        _restore_state(saved)
    print("  OK  the GPU handler honours stopping, a stale engine, a dismiss and the setting")


# --- 3. the endpoints, /api/status and the settings key --------------------

def test_status_carries_the_struggle_nudge():
    saved = _save_state()
    try:
        webapp.STATE.running = True
        webapp.STATE.stopping = False
        webapp.STATE.source_kind = "live"
        webapp.STATE.engine = None
        webapp.STATE.struggle_nudge = {"reason": "cpu-downgrade", "old_size": "medium",
                                       "new_size": "tiny", "recording": True}
        st = client.get("/api/status").json()
        assert st["running"] is True and st["struggle_nudge"] == webapp.STATE.struggle_nudge, st
        assert st["struggle_nudge"]["reason"] == "cpu-downgrade", st["struggle_nudge"]
        # The reason is what the banner branches its copy on, so it must survive the poll for
        # BOTH causes (the GPU one carries no sizes at all).
        webapp.STATE.struggle_nudge = {"reason": "gpu-busy", "recording": False}
        st2 = client.get("/api/status").json()
        assert st2["struggle_nudge"] == {"reason": "gpu-busy", "recording": False}, st2["struggle_nudge"]
    finally:
        _restore_state(saved)
    print("  OK  /api/status hands the outstanding struggle nudge, reason included, to the UI")


def test_endpoint_requires_a_live_session_and_the_csrf_token():
    assert webapp.STATE.running is False, "another test left a session running"
    r = client.post("/api/struggle-nudge", json={"action": "dismiss"})
    assert r.status_code == 409, (r.status_code, r.text)
    assert client.post("/api/struggle-nudge", json={"action": "nope"}).status_code == 422
    bare = TestClient(app, base_url="http://localhost")
    assert bare.post("/api/struggle-nudge", json={"action": "mute"}).status_code == 403
    print("  OK  /api/struggle-nudge: 409 with no session, 422 on junk, CSRF-protected")


def test_endpoint_dismisses_for_the_session_and_mutes_by_persisting():
    saved = _save_state()
    saved_update = config.update
    updates = []
    try:
        config.update = lambda patch: updates.append(dict(patch)) or {}
        webapp.STATE.running = True
        webapp.STATE.stopping = False
        webapp.STATE.source_kind = "live"
        webapp.STATE.struggle_nudge = {"old_size": "medium", "new_size": "small", "recording": False}
        # Dismiss: clears for the session, does NOT persist anything.
        r = client.post("/api/struggle-nudge", json={"action": "dismiss"})
        assert r.status_code == 200 and r.json() == {"struggle_nudge": None}, r.text
        assert webapp.STATE.struggle_nudge is None
        assert updates == [], f"dismiss must not persist a setting, wrote {updates}"
        # Mute: clears AND persists struggle_nudge=false so the machine stops surfacing it.
        webapp.STATE.struggle_nudge = {"old_size": "medium", "new_size": "base", "recording": False}
        r2 = client.post("/api/struggle-nudge", json={"action": "mute"})
        assert r2.status_code == 200 and r2.json() == {"struggle_nudge": None}, r2.text
        assert webapp.STATE.struggle_nudge is None
        assert updates == [{"struggle_nudge": False}], f"mute must persist the off setting, got {updates}"
    finally:
        config.update = saved_update
        _restore_state(saved)
    print("  OK  dismiss clears for the session; mute clears and persists struggle_nudge=false")


def test_settings_key_exists_and_is_patchable():
    from live_transcribe.web.app import SettingsPatch
    assert config.DEFAULTS["struggle_nudge"] is True
    assert "struggle_nudge" in SettingsPatch.model_fields, sorted(SettingsPatch.model_fields)
    # public_view() is DEFAULTS-driven, so the UI sees the key without further plumbing.
    assert "struggle_nudge" in config.public_view()
    print("  OK  struggle_nudge is a setting, default True, patchable and published")


if __name__ == "__main__":
    tests = (test_callback_gets_preswap_old_size_and_new_size,
             test_callback_none_is_a_noop_and_raising_never_breaks_the_worker,
             test_callback_steps_each_rung_and_stops_at_the_floor,
             test_callback_inert_off_the_cpu_adaptive_path,
             test_worker_records_rtf_on_a_non_cpu_engine_and_a_draining_backlog_never_warns,
             test_two_channel_session_losing_ground_at_rtf_0_6_warns,
             test_mic_only_session_with_a_transient_backlog_does_not_warn,
             test_producer_warns_from_any_inherited_depth_and_at_the_ceiling_only_on_the_drop,
             test_producer_warns_when_a_partial_drain_reverses,
             test_a_healthy_drain_from_a_deep_backlog_stays_silent,
             test_producer_warns_on_the_first_drop_without_any_window,
             test_pre_arm_evidence_can_never_spend_the_warning,
             test_under_real_chunker_jitter_a_recovering_session_is_never_warned,
             test_the_depth_sample_is_taken_inside_the_lock,
             test_concurrent_producers_cannot_corrupt_the_window_or_double_spend,
             test_the_warning_is_inert_on_cpu_and_on_a_file_import,
             test_the_warning_fires_once_per_session_and_survives_a_raising_callback,
             test_the_struggle_callback_never_blocks_its_caller,
             test_the_callback_is_delivered_even_when_stdout_is_broken,
             test_the_notice_is_emitted_by_the_worker_never_by_the_capture_thread,
             test_handler_publishes_once_and_updates_in_place,
             test_handler_guards_stopping_and_a_stale_engine,
             test_handler_does_not_renag_after_a_dismiss,
             test_handler_gated_by_setting_and_env,
             test_gpu_handler_publishes_the_gpu_busy_reason_once,
             test_gpu_handler_guards_stopping_a_stale_engine_a_dismiss_and_the_setting,
             test_status_carries_the_struggle_nudge,
             test_endpoint_requires_a_live_session_and_the_csrf_token,
             test_endpoint_dismisses_for_the_session_and_mutes_by_persisting,
             test_settings_key_exists_and_is_patchable)
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll struggle-signal tests passed.")
