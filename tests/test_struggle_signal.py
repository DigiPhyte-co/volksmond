"""Tests for the "model struggling to keep up" nudge signal path.

When a live CPU session auto-downgrades (transcribe.Engine._maybe_downgrade, ladder
medium->small->base->tiny) it now fires an optional on_downgrade(old_size, new_size) callback on
the worker thread, which the web layer turns into a one-time banner + a single Windows toast. This
covers, cheapest first:

  1. The Engine callback itself (transcribe.py): captured old_size is the PRE-swap size, fires once
     per rung, None is a no-op, a raising callback never breaks the worker, and it stays inert on
     GPU / Swivuriso / non-adaptive / a full ladder. Driven by calling _maybe_downgrade directly on
     an Engine built with __new__ (no real model load) against stubbed load_model/resolve_model.
  2. web/app.py's _on_downgrade with a hand-set STATE and a monkeypatched notify.show: publishes
     once per session, updates new_size in place on a later rung (keeping the original old_size),
     never re-fires the toast, guards STATE.stopping and a stale engine, does not re-nag after a
     dismiss, and honours the setting + env kill switch.
  3. The endpoints and gating: /api/status carries the nudge, POST /api/struggle-nudge dismisses
     (session-only) and mutes (persists struggle_nudge=false), 409 with no live session, CSRF, and
     the settings key exists and is patchable.

No audio, no pywin32, no real capture and no model load: the seams are load_model/resolve_model
(stubbed), notify.show (monkeypatched), config.load/update (monkeypatched) and STATE (hand-set and
restored).

Run:  python tests/test_struggle_signal.py   (from the project root; exit 0 = pass)
"""
import os
import sys
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

def _stub_engine(size="medium", family="whisper", adaptive=True, is_cpu=True, rtf=2.0):
    """A minimal Engine with just the attributes _maybe_downgrade touches, built WITHOUT __init__
    so no model is loaded. _rtf is filled to a full window whose average trips the downgrade."""
    eng = transcribe.Engine.__new__(transcribe.Engine)
    eng.family = family
    eng.adaptive = adaptive
    eng._is_cpu = is_cpu
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
    _fill_rtf(eng, rtf)
    return eng


def _fill_rtf(eng, val=2.0):
    eng._rtf = deque([val] * transcribe.DOWNGRADE_WINDOW, maxlen=transcribe.DOWNGRADE_WINDOW)


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
            assert webapp.STATE.struggle_nudge == {"old_size": "medium", "new_size": "small",
                                                   "recording": False}, webapp.STATE.struggle_nudge
            assert webapp.STATE.struggle_notified is True
            assert len(calls) == 1 and calls[0]["tag"] == "struggle", calls
            assert callable(calls[0]["on_click"]), "the toast must be clickable back to the app"
            # A later rung updates new_size IN PLACE (original old_size kept), no second toast.
            webapp._on_downgrade(eng, "small", "base")
            assert webapp.STATE.struggle_nudge == {"old_size": "medium", "new_size": "base",
                                                   "recording": False}, webapp.STATE.struggle_nudge
            assert len(calls) == 1, f"the toast must fire only once per session, got {len(calls)}"
            # `recording` is captured at emit time: once recording, the next update reflects it.
            webapp.STATE.recording = True
            webapp._on_downgrade(eng, "base", "tiny")
            assert webapp.STATE.struggle_nudge == {"old_size": "medium", "new_size": "tiny",
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


# --- 3. the endpoints, /api/status and the settings key --------------------

def test_status_carries_the_struggle_nudge():
    saved = _save_state()
    try:
        webapp.STATE.running = True
        webapp.STATE.stopping = False
        webapp.STATE.source_kind = "live"
        webapp.STATE.engine = None
        webapp.STATE.struggle_nudge = {"old_size": "medium", "new_size": "tiny", "recording": True}
        st = client.get("/api/status").json()
        assert st["running"] is True and st["struggle_nudge"] == webapp.STATE.struggle_nudge, st
    finally:
        _restore_state(saved)
    print("  OK  /api/status hands the outstanding struggle nudge to the UI")


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
             test_handler_publishes_once_and_updates_in_place,
             test_handler_guards_stopping_and_a_stale_engine,
             test_handler_does_not_renag_after_a_dismiss,
             test_handler_gated_by_setting_and_env,
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
