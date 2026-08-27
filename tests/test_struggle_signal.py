"""Tests for the "model struggling to keep up" nudge signal path (both causes).

One banner, two engine-side causes. A live CPU session auto-downgrades
(transcribe.Engine._maybe_downgrade, ladder medium->small->base->tiny) and fires
on_downgrade(old_size, new_size); a live GPU session has no ladder, so when it is sustained-slower
than real time AND its queue is backing up it fires on_struggle()
(transcribe.Engine._maybe_warn_gpu_struggle). Both run on the worker thread and both become the
same one-time banner + single Windows toast, told apart by the nudge's "reason". This covers,
cheapest first:

  1. The Engine downgrade callback (transcribe.py): captured old_size is the PRE-swap size, fires
     once per rung, None is a no-op, a raising callback never breaks the worker, and it stays inert
     on GPU / Swivuriso / non-adaptive / a full ladder. Driven by calling _maybe_downgrade directly
     on an Engine built with __new__ (no real model load) against stubbed load_model/resolve_model.
  1b. The Engine GPU warning: the RTF sample is now recorded on EVERY backend (it used to be
     CPU-only, which is what left a starved GPU with no signal), the warning needs slow AND backed
     up together, is inert on CPU and on a file import, and fires at most once per session. Driven
     both directly and end-to-end through Engine._run against a sleep-only model stub.
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
                 device=None, pending=0):
    """A minimal Engine with just the attributes _maybe_downgrade / _maybe_warn_gpu_struggle touch,
    built WITHOUT __init__ so no model is loaded. _rtf is filled to a full window whose average
    trips both thresholds. `device` defaults to the backend `is_cpu` implies ("cpu"/"cuda"); pass
    "mlx" for the Apple path. `pending` seeds a REAL queue so pending() is the engine's own method,
    which is the GPU warning's backlog co-condition."""
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
    eng._struggle_warned = False
    eng._busy = False
    eng._queue = queue.Queue(maxsize=32)
    for i in range(pending):
        eng._queue.put(("SYS", [], float(i)))
    _fill_rtf(eng, rtf)
    return eng


def _fill_rtf(eng, val=2.0):
    eng._rtf = deque([val] * transcribe.DOWNGRADE_WINDOW, maxlen=transcribe.DOWNGRADE_WINDOW)


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


# --- 1b. the GPU struggle warning (transcribe.py) --------------------------

def test_worker_records_rtf_on_a_non_cpu_engine_and_warns_end_to_end():
    """The regression this feature exists for: the RTF sample used to be appended only inside
    `if self._is_cpu`, so on cuda/mlx the window stayed permanently EMPTY and nothing could ever
    react. Run the real worker loop against a deliberately slow model stub and assert both that
    the window filled and that the warning came out once, as a notice, via _fanout not _route."""
    eng = _worker_engine(device="cuda")
    notices, routed, fired = [], [], []
    eng.subscribe(lambda seg: notices.append(seg))
    eng._route = lambda seg: routed.append(seg)      # nothing synthetic may take this path
    eng.on_struggle = lambda: fired.append(1)
    eng._run()
    assert len(eng._rtf) == eng._rtf.maxlen, f"a cuda session recorded no RTF at all: {eng._rtf}"
    assert sum(eng._rtf) / len(eng._rtf) > transcribe.GPU_STRUGGLE_RTF, list(eng._rtf)
    assert fired == [1], f"the GPU warning must fire exactly once from the worker, got {fired}"
    assert eng._struggle_warned is True
    assert len(notices) == 1 and "struggling to keep up" in notices[0].text, [s.text for s in notices]
    assert notices[0].source == "SYS", notices[0].source
    assert routed == [], "a synthetic line must never go through _route (loop guard / echo ring)"
    print("  OK  the worker records RTF on cuda and warns once, as a notice, never via _route")


def test_gpu_struggle_needs_slow_AND_a_backed_up_queue():
    # Slow and backing up: the real starvation. Both cuda and mlx are GPU sessions.
    for dev in ("cuda", "mlx"):
        eng = _stub_engine(is_cpu=False, device=dev, rtf=2.0, pending=8)
        fired = []
        eng.on_struggle = lambda: fired.append(1)
        eng._maybe_warn_gpu_struggle(30.0)
        assert fired == [1], f"{dev}: a slow, backed-up GPU session must warn"
    # Slow but the queue is short: a dense burst of speech the queue absorbs and clears. This is
    # the whole false-positive defence, so it must stay silent even with a fully slow window.
    eng = _stub_engine(is_cpu=False, device="cuda", rtf=2.0,
                       pending=transcribe.BACKPRESSURE_BEAM_THRESHOLD)
    fired = []
    eng.on_struggle = lambda: fired.append(1)
    eng._maybe_warn_gpu_struggle(30.0)
    assert fired == [] and eng._struggle_warned is False, "a burst the queue absorbs must not warn"
    # Backed up but comfortably faster than real time: nothing is wrong, do not warn.
    eng2 = _stub_engine(is_cpu=False, device="cuda", rtf=0.2, pending=20)
    fired2 = []
    eng2.on_struggle = lambda: fired2.append(1)
    eng2._maybe_warn_gpu_struggle(30.0)
    assert fired2 == [], "a fast GPU with a backlog (catch-up) must not warn"
    # A partial window is not evidence yet.
    eng3 = _stub_engine(is_cpu=False, device="cuda", rtf=2.0, pending=20)
    eng3._rtf = deque([2.0, 2.0], maxlen=transcribe.DOWNGRADE_WINDOW)
    fired3 = []
    eng3.on_struggle = lambda: fired3.append(1)
    eng3._maybe_warn_gpu_struggle(30.0)
    assert fired3 == [], "an unfilled RTF window must not warn"
    print("  OK  the GPU warning needs slow AND backed up: bursts, catch-up and short windows stay silent")


def test_gpu_struggle_inert_on_cpu_and_on_a_file_import():
    for label, kw in (("cpu", {"is_cpu": True, "device": "cpu"}),
                      ("file import", {"is_cpu": False, "device": "cuda", "adaptive": False})):
        eng = _stub_engine(rtf=2.0, pending=20, **kw)
        fired = []
        eng.on_struggle = lambda: fired.append(1)
        eng._maybe_warn_gpu_struggle(30.0)
        assert fired == [] and eng._struggle_warned is False, f"{label}: must not warn"
    print("  OK  the GPU warning stays inert on CPU (the ladder's job) and on a file import")


def test_gpu_struggle_fires_once_per_session_and_survives_a_raising_callback():
    eng = _stub_engine(is_cpu=False, device="cuda", rtf=2.0, pending=20)
    fired, notices = [], []
    eng.subscribe(lambda seg: notices.append(seg))
    eng.on_struggle = lambda: fired.append(1)
    eng._maybe_warn_gpu_struggle(30.0)
    _fill_rtf(eng)                       # still starved later in the same session
    eng._maybe_warn_gpu_struggle(90.0)
    assert fired == [1], f"the warning must fire once per session, got {fired}"
    assert len(notices) == 1, f"and put exactly one notice in the transcript, got {len(notices)}"
    # A raising callback must never take the transcription worker down with it.
    eng2 = _stub_engine(is_cpu=False, device="cuda", rtf=2.0, pending=20)

    def boom():
        raise RuntimeError("callback exploded")

    eng2.on_struggle = boom
    eng2._maybe_warn_gpu_struggle(30.0)   # must not raise
    assert eng2._struggle_warned is True
    # A None callback is a no-op (CLI, tests): the notice still lands, nothing raises.
    eng3 = _stub_engine(is_cpu=False, device="cuda", rtf=2.0, pending=20)
    eng3._maybe_warn_gpu_struggle(30.0)
    assert eng3._struggle_warned is True
    print("  OK  the GPU warning fires once per session; a raising or None callback is harmless")


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
             test_worker_records_rtf_on_a_non_cpu_engine_and_warns_end_to_end,
             test_gpu_struggle_needs_slow_AND_a_backed_up_queue,
             test_gpu_struggle_inert_on_cpu_and_on_a_file_import,
             test_gpu_struggle_fires_once_per_session_and_survives_a_raising_callback,
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
