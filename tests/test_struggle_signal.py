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
import queue
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
    CPU downgrade. `device` defaults to the backend `is_cpu` implies ("cpu"/"cuda"); pass "mlx" for
    the Apple path. `pending` seeds a REAL queue, so pending() and on_chunk are the engine's own."""
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
    eng.on_struggle = None
    eng.struggle_armed = armed
    eng._struggle_warned = False
    eng._struggle_lock = threading.Lock()
    eng._struggle_notice_due = False
    eng._struggle_arm_depth = None
    eng._pending_hist = deque(maxlen=transcribe.DOWNGRADE_WINDOW)
    eng._dropped = 0
    eng._busy = False
    eng._stop = threading.Event()
    eng._queue = queue.Queue(maxsize=transcribe.QUEUE_MAXSIZE)
    for i in range(pending):
        eng._queue.put(("SYS", [], float(i)))
    _fill_rtf(eng, rtf)
    return eng


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


def _drive(eng, rtf, sources=2, chunk_secs=8.0, completions=80, backlog=0):
    """Deterministic model of a live session's queue: no real time, no audio, no model.

    Each completed transcription costs chunk_secs*rtf seconds of wall clock, during which each of
    `sources` capture streams produces one chunk every chunk_secs. Chunks go in through the real
    on_chunk (so the producer-side net is exercised) and every completion samples the depth and
    calls the worker-side check, exactly as _run does. `backlog` seeds a transient one first.

    This is the arithmetic the whole design turns on: with two sources, arrivals are 2 per
    chunk-length while the worker manages 1/rtf of them, so the queue grows whenever rtf > 0.5;
    with one source it grows only past 1.0.
    """
    fired = []
    eng.on_struggle = lambda: fired.append(1)
    for i in range(backlog):
        eng.on_chunk("SYS", [], float(i))
    arrivals, t = 0.0, 0.0
    for _ in range(completions):
        service = chunk_secs * rtf
        arrivals += sources * service / chunk_secs
        while arrivals >= 1.0:
            eng.on_chunk("SYS", [], t)
            arrivals -= 1.0
        try:
            eng._queue.get_nowait()          # this completion
        except queue.Empty:
            pass
        eng._busy = True                     # as _run samples it: the in-flight chunk counts
        eng._pending_hist.append(eng.pending())
        eng._maybe_warn_gpu_struggle(t)
        eng._busy = False
        t += service
    return fired


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
        transcribe.load_model = lambda *a, **k: object()
        transcribe.resolve_model = lambda size, language, engine: (f"model-{size}", "whisper")
        return self

    def __exit__(self, *exc):
        transcribe.load_model = self._load
        transcribe.resolve_model = self._resolve


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
        eng._maybe_downgrade(12.0)
    assert eng.size == "small", f"the downgrade must still happen; size is {eng.size}"
    assert calls == [("medium", "small")], f"callback must get the PRE-swap old size, got {calls}"
    print("  OK  on_downgrade fires with (pre-swap old_size, new_size) after a successful step")


def test_callback_none_is_a_noop_and_raising_never_breaks_the_worker():
    with _stub_models():
        # None callback: the swap still happens, nothing is called, nothing raised.
        eng = _stub_engine(size="medium")
        eng.on_downgrade = None
        eng._maybe_downgrade(1.0)
        assert eng.size == "small"
        # A raising callback must be swallowed: the swap still happens and _maybe_downgrade returns
        # normally (a crash here would take the transcription worker thread down).
        eng2 = _stub_engine(size="small")

        def boom(old, new):
            raise RuntimeError("callback exploded")

        eng2.on_downgrade = boom
        eng2._maybe_downgrade(2.0)     # must not raise
        assert eng2.size == "base", "a raising callback must not prevent the downgrade"
    print("  OK  a None callback is a no-op and a raising callback never breaks the downgrade")


def test_callback_steps_each_rung_and_stops_at_the_floor():
    with _stub_models():
        eng = _stub_engine(size="base")
        seen = []
        eng.on_downgrade = lambda old, new: seen.append((old, new))
        eng._maybe_downgrade(1.0)      # base -> tiny
        assert eng.size == "tiny" and seen == [("base", "tiny")], (eng.size, seen)
        # Already on the fastest rung: no further step, no callback.
        _fill_rtf(eng)
        eng._maybe_downgrade(2.0)
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
            eng._maybe_downgrade(1.0)
            assert eng.size == "medium", f"{label}: must not downgrade"
            assert fired == [], f"{label}: callback must not fire"
        # A partial (not-yet-full) RTF window must not downgrade either.
        eng = _stub_engine(size="medium")
        eng._rtf = deque([2.0, 2.0], maxlen=transcribe.DOWNGRADE_WINDOW)   # len 2 < maxlen 4
        fired = []
        eng.on_downgrade = lambda old, new: fired.append(1)
        eng._maybe_downgrade(1.0)
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


def test_producer_warns_on_the_high_water_mark_and_on_the_first_drop():
    """H3: under severe starvation the queue fills before four transcriptions complete, so the
    worker-side window can arrive after audio is already lost. The producer side does not wait for
    it: it trips at the high-water mark, and unconditionally on the first real drop."""
    # (a) high-water, with nothing completed at all: no RTF window, no depth samples.
    eng = _stub_engine(is_cpu=False, device="cuda")
    fired = []
    eng.on_struggle = lambda: fired.append(1)
    for i in range(transcribe.STRUGGLE_QUEUE_HIGH + 1):
        eng.on_chunk("SYS", [], float(i))
    assert eng._struggle_warned is True, "the high-water mark never tripped"
    assert list(eng._pending_hist) == [], "this must not need a completed chunk"
    assert eng._dropped == 0, "the high-water warning must land BEFORE any drop"
    assert _wait_fired(fired) == [1], fired
    # (b) a session handed a nearly full queue by the catch-up replay: the high-water mark is
    # already inside the inherited baseline, so it must NOT fire on that alone, only on real loss.
    eng2 = _stub_engine(is_cpu=False, device="cuda")
    eng2.on_chunk("SYS", [], 0.0)                       # first chunk takes the arming baseline
    eng2._struggle_arm_depth = transcribe.QUEUE_MAXSIZE  # ...as if the replay had left it full
    fired2 = []
    eng2.on_struggle = lambda: fired2.append(1)
    while eng2._queue.qsize() < transcribe.QUEUE_MAXSIZE:
        eng2.on_chunk("SYS", [], 1.0)
    assert eng2._struggle_warned is False, "an inherited catch-up backlog raised the high-water alarm"
    assert eng2.on_chunk("SYS", [], 2.0) is False, "the queue should be full now"
    assert eng2._dropped == 1 and eng2._struggle_warned is True, "the first real drop must warn"
    assert _wait_fired(fired2) == [1], fired2
    print("  OK  the producer net trips at the high-water mark and, regardless, on the first drop")


def test_catch_up_backlog_does_not_consume_the_warning():
    """H1: the callback is attached before the buffered-audio replay, during which the queue is deep
    BY DESIGN. If the warning tripped there it would burn its one-shot ratchet on a callback the web
    layer rejects (STATE.engine is not yet this engine), leaving the real starvation silent for the
    whole meeting. Nothing may fire before the owner arms it."""
    eng = _stub_engine(is_cpu=False, device="cuda", armed=False)
    fired = []
    eng.on_struggle = lambda: fired.append(1)
    for i in range(transcribe.QUEUE_MAXSIZE + 8):        # fill it, then drop 8: the worst catch-up
        eng.on_chunk("SYS", [], float(i))
    _drive(eng, rtf=4.0, sources=2, completions=12)      # and grind, still unarmed
    assert eng._dropped >= 8, eng._dropped
    assert eng._struggle_warned is False, "the catch-up replay spent the one-shot warning"
    assert fired == [], fired
    # Now the session goes live. The next drop must still warn: the ratchet was never spent. (The
    # first chunk after arming only takes the inherited-depth baseline, by design, so feed a few.)
    eng.struggle_armed = True
    eng.on_struggle = lambda: fired.append(1)
    dropped_before = eng._dropped
    for i in range(3):
        eng.on_chunk("SYS", [], 999.0 + i)
    assert eng._dropped > dropped_before, "the queue should still be losing chunks here"
    assert eng._struggle_warned is True, "the warning never fired after arming"
    assert _wait_fired(fired) == [1], fired
    print("  OK  an unarmed catch-up never warns and never spends the ratchet; arming restores it")


def test_the_warning_is_inert_on_cpu_and_on_a_file_import():
    # _trip_struggle is the single gate both detectors go through, so testing it directly is the
    # strongest form: if it refuses here, no path can warn.
    for label, kw in (("cpu", {"is_cpu": True, "device": "cpu"}),
                      ("file import", {"is_cpu": False, "device": "cuda", "adaptive": False}),
                      ("not yet armed", {"is_cpu": False, "device": "cuda", "armed": False})):
        eng = _stub_engine(**kw)
        fired = []
        eng.on_struggle = lambda: fired.append(1)
        eng._trip_struggle("test")
        assert eng._struggle_warned is False, f"{label}: must not warn"
        assert eng._struggle_notice_due is False and fired == [], f"{label}: must stay silent"
    # A CPU session grinding badly is the ladder's job (_maybe_downgrade), never this warning.
    cpu = _stub_engine(is_cpu=True, device="cpu")
    _drive(cpu, rtf=4.0, sources=2, completions=20)
    assert cpu._struggle_warned is False, "a CPU session warned instead of using its ladder"
    print("  OK  inert on CPU, on a file import and before arming, on every path")


def test_the_warning_fires_once_per_session_and_survives_a_raising_callback():
    eng = _stub_engine(is_cpu=False, device="cuda")
    fired = []
    eng.on_struggle = lambda: fired.append(1)
    eng._trip_struggle("first")
    eng._trip_struggle("second")          # a second cause (producer after worker, say)
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
    eng2._trip_struggle("boom")           # must not raise
    assert ran.wait(3.0), "the callback never ran"
    assert eng2._struggle_warned is True and eng2._struggle_notice_due is True
    # A None callback (CLI, tests) is a no-op: the notice is still owed, nothing raises.
    eng3 = _stub_engine(is_cpu=False, device="cuda")
    eng3._trip_struggle("no listener")
    assert eng3._struggle_warned is True and eng3._struggle_notice_due is True
    print("  OK  fires once per session; a raising or absent callback is harmless")


def test_the_struggle_callback_never_blocks_its_caller():
    """M1: the real callback reads settings from disk and calls notify.show(), whose first backend
    initialisation can block for seconds. It is raised from the sole transcription worker AND from
    the real-time capture thread, so neither may wait on it."""
    eng = _stub_engine(is_cpu=False, device="cuda")
    entered, release = threading.Event(), threading.Event()

    def slow_cb():
        entered.set()
        release.wait(timeout=10.0)

    eng.on_struggle = slow_cb
    try:
        t0 = time.monotonic()
        eng._trip_struggle("test")
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


def test_the_notice_is_emitted_by_the_worker_never_by_the_capture_thread():
    """_fanout is worker-thread-only: its subscribers write the transcript file and the SSE stream.
    A trip raised on the capture thread must therefore only leave the notice OWED, and the worker
    must emit it on its next chunk, through _emit_notice and never _route (a synthetic line must
    not enter RecentEmissions or SysTextRing)."""
    eng = _worker_engine(device="cuda", chunks=2)
    seen, routed = [], []
    eng.subscribe(lambda seg: seen.append(seg))
    eng._route = lambda seg: routed.append(seg)
    eng._trip_struggle("from the capture thread")
    assert eng._struggle_notice_due is True
    assert seen == [], "the trip fanned out a segment off the worker thread"
    eng._run()
    assert len(seen) == 1, [s.text for s in seen]
    assert seen[0].text == transcribe.STRUGGLE_NOTICE, seen[0].text
    assert seen[0].source == "SYS", seen[0].source
    assert eng._struggle_notice_due is False, "the notice was left owed after being emitted"
    assert routed == [], "a synthetic line must never go through _route"
    print("  OK  the notice is owed by the trip and emitted once by the worker, never via _route")


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
                                                   "new_size": "small",
                                                   "recording": False}, webapp.STATE.struggle_nudge
            assert webapp.STATE.struggle_notified is True
            assert len(calls) == 1 and calls[0]["tag"] == "struggle", calls
            assert callable(calls[0]["on_click"]), "the toast must be clickable back to the app"
            # A later rung updates new_size IN PLACE (original old_size kept), no second toast.
            webapp._on_downgrade(eng, "small", "base")
            assert webapp.STATE.struggle_nudge == {"reason": "cpu-downgrade", "old_size": "medium",
                                                   "new_size": "base",
                                                   "recording": False}, webapp.STATE.struggle_nudge
            assert len(calls) == 1, f"the toast must fire only once per session, got {len(calls)}"
            # `recording` is captured at emit time: once recording, the next update reflects it.
            webapp.STATE.recording = True
            webapp._on_downgrade(eng, "base", "tiny")
            assert webapp.STATE.struggle_nudge == {"reason": "cpu-downgrade", "old_size": "medium",
                                                   "new_size": "tiny",
                                                   "recording": True}, webapp.STATE.struggle_nudge
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
             test_producer_warns_on_the_high_water_mark_and_on_the_first_drop,
             test_catch_up_backlog_does_not_consume_the_warning,
             test_the_warning_is_inert_on_cpu_and_on_a_file_import,
             test_the_warning_fires_once_per_session_and_survives_a_raising_callback,
             test_the_struggle_callback_never_blocks_its_caller,
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
