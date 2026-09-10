"""Tests for the MLX (Apple Metal) auto-downgrade ladder: English-only, queue-depth-driven.

The Mac's Metal GPU cannot cut a beam and has no CPU-style per-stream RTF budget, so when a screen
share (or anything else) contends the GPU a session falls behind as a GROWING backlog, not a stable
real-time factor. So the MLX ladder is driven by the same queue-depth evidence as wp1's struggle
warning (a completion window that is deep AND still climbing), reuses the CPU ladder's hysteresis
wholesale (one build in flight, minimum spacing between steps, the window cleared after a step), and
steps an ENGLISH session down MLX_LADDER = medium -> small onto the stock mlx-community rungs.

The hard line, locked: an Afrikaans (Fluister) session is NEVER moved onto stock Whisper. There is
no stock MLX form for a Fluister rung, so _next_rung yields nothing and the session keeps only wp1's
warning. This file pins that, plus present-only (an uncached rung is skipped), the growth rule (a
draining backlog never steps), the single adaptive kill switch, and the honest rung-named notice.

No model is ever loaded and mlx-whisper is never imported: load_model and the MLX presence probe
(_mlx_rung_present) are stubbed; resolve_model, mlxbackend.mlx_model_for and is_stock_mlx_repo are
left REAL, because "does this rung have a stock MLX form in this family" is exactly what is tested.

Run:  python tests/test_mlx_ladder.py   (from the project root; exit 0 = pass)
"""
import contextlib
import os
import queue
import sys
import threading
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import transcribe as T


# --- helpers ---------------------------------------------------------------

class _Collect:
    def __init__(self):
        self.seen = []

    def __call__(self, seg):
        self.seen.append(seg)


def _mlx_engine(size="large-v3", family="whisper", language="en", adaptive=True, armed=True,
                aged=True):
    """An MLX Engine with only the attributes the ladder touches, built WITHOUT __init__ so no model
    is loaded. aged=True clears the minimum spacing; armed=True marks the session live (arm_struggle
    is what the owner calls once the catch-up replay is done)."""
    eng = T.Engine.__new__(T.Engine)
    eng.family = family
    eng.adaptive = adaptive
    eng._is_cpu = False
    eng._is_mlx = True
    eng._device = "mlx"
    eng.size = size
    eng.language = language
    eng.engine = "auto"
    eng._compute_type = "fp16"
    eng._cpu_threads = 8
    eng.model = object()
    eng.model_name = f"model-{size}"
    eng.is_fluister = family == "fluister"
    eng.subscribers = [_Collect()]
    eng.on_downgrade = None
    eng._swap = None
    eng._cold_decode = False
    eng._last_rung_change = 0.0 if aged else time.monotonic()
    eng._front = deque()
    eng._busy = False
    eng._queue = queue.Queue(maxsize=T.QUEUE_MAXSIZE)
    eng._recent = T.RecentEmissions()
    eng._rtf = deque(maxlen=T.DOWNGRADE_WINDOW)
    eng._mlx_depth = deque(maxlen=T.STRUGGLE_COMPLETION_WINDOW)
    eng.struggle_armed = armed
    return eng


@contextlib.contextmanager
def _stub_mlx_models(present=None, load=None):
    """Stub load_model (the helper-thread build) and _mlx_rung_present (the cache probe) so the
    ladder never loads or probes a real model. `present` is a predicate over the MLX repo id; the
    default says every rung is cached. resolve_model / mlxbackend are left REAL."""
    saved = (T.load_model, T._mlx_rung_present)
    T.load_model = load or (lambda *a, **k: object())
    T._mlx_rung_present = present or (lambda repo: True)
    try:
        yield
    finally:
        T.load_model, T._mlx_rung_present = saved


def _const(v):
    return lambda: v


def _growing():
    """A full completion window that is deep AND still climbing: 12 samples 8..19, every one over
    the depth mark (BACKPRESSURE_BEAM_THRESHOLD + 1 = 7) and the newest strictly above the oldest."""
    return list(range(8, 8 + T.STRUGGLE_COMPLETION_WINDOW))


def _drive_one_step(eng, depths):
    """Feed pending() = each value in `depths` through _maybe_downgrade_mlx, one per completed chunk,
    and install a build the moment one starts (the two-pass helper-thread swap). Returns True if the
    active model actually changed."""
    before = eng.size
    for d in depths:
        eng.pending = _const(d)
        eng._maybe_downgrade_mlx(1.0)
        if eng._swap is not None:
            assert eng._swap["done"].wait(10), "the helper-thread build never finished"
            eng._maybe_downgrade_mlx(1.0)   # second pass installs it
            break
    return eng.size != before


def _notices(eng):
    return [s.text for s in eng.subscribers[0].seen]


# --- 1. English steps medium then small ------------------------------------

def test_english_mlx_steps_medium_then_small_on_depth():
    with _stub_mlx_models():
        eng = _mlx_engine(size="large-v3")
        assert _drive_one_step(eng, _growing()) is True
        assert eng.size == "medium", eng.size
        # A step resets the spacing; wind it back so the very same evidence can take the next rung.
        eng._last_rung_change = 0.0
        assert _drive_one_step(eng, _growing()) is True
        assert eng.size == "small", eng.size
        # small is the floor: nothing lower has a stock MLX form, so no further step (the caller
        # then lets wp1's warning speak instead).
        eng._last_rung_change = 0.0
        assert _drive_one_step(eng, _growing()) is False
        assert eng.size == "small"
        assert eng._swap is None
    print("  OK  an English MLX session steps large-v3 -> medium -> small, then holds at the floor")


def test_maybe_downgrade_dispatches_mlx():
    # The public entry point _maybe_downgrade must route an MLX session to the queue-depth ladder
    # (the drain-parity test in test_mlx_backend calls it directly), never the RTF path.
    with _stub_mlx_models():
        eng = _mlx_engine(size="large-v3")
        before = eng.size
        for d in _growing():
            eng.pending = _const(d)
            eng._maybe_downgrade(1.0)
            if eng._swap is not None:
                assert eng._swap["done"].wait(10)
                eng._maybe_downgrade(1.0)
                break
        assert eng.size == "medium" and eng.size != before
    print("  OK  _maybe_downgrade dispatches an MLX session to the queue-depth ladder")


# --- 2. the hard line: an Afrikaans session is never crossed ----------------

def test_fluister_mlx_never_steps_onto_stock_whisper():
    with _stub_mlx_models():   # every rung "present", so only the family/stock gate can stop it
        eng = _mlx_engine(size="large-v3-turbo", family="fluister", language="af")
        assert eng._next_rung() is None, eng._next_rung()
        assert _drive_one_step(eng, _growing()) is False
        assert eng.size == "large-v3-turbo"
        assert eng._swap is None, "an Afrikaans MLX session must never start a stock rung build"
    print("  OK  an Afrikaans (Fluister) MLX session never steps onto stock Whisper")


# --- 3. present-only --------------------------------------------------------

def test_an_uncached_mlx_rung_is_skipped():
    # No mid-session downloads: medium is not on disk, small is, so the ladder skips medium and
    # steps straight to the cached small.
    def present(repo):
        return repo != "mlx-community/whisper-medium-mlx-8bit"

    with _stub_mlx_models(present=present):
        eng = _mlx_engine(size="large-v3")
        assert eng._next_rung() == ("small", "small", "whisper"), eng._next_rung()
        assert _drive_one_step(eng, _growing()) is True
        assert eng.size == "small", eng.size
    # Nothing cached below at all -> no rung, no build (the warning handles it from there).
    with _stub_mlx_models(present=lambda repo: False):
        eng = _mlx_engine(size="large-v3")
        assert eng._next_rung() is None
        assert _drive_one_step(eng, _growing()) is False
        assert eng.size == "large-v3" and eng._swap is None
    print("  OK  an uncached MLX rung is skipped; with none cached the session holds and never downloads")


# --- 4. trigger hygiene -----------------------------------------------------

def test_a_draining_backlog_never_steps():
    # The growth rule, same as the warning: a window that is deep but SHRINKING (an inherited
    # backlog being worked off) reads as recovering, not struggling, so it never steps.
    with _stub_mlx_models():
        eng = _mlx_engine(size="large-v3")
        draining = list(range(8 + T.STRUGGLE_COMPLETION_WINDOW - 1, 7, -1))  # 19..8, deep but falling
        assert _drive_one_step(eng, draining) is False
        assert eng.size == "large-v3" and eng._swap is None
    print("  OK  a deep but draining backlog never steps the MLX ladder")


def test_an_unarmed_session_does_not_step():
    # Before the session is armed (the catch-up replay at Begin leaves a deep queue BY DESIGN) the
    # ladder must not act, exactly as the warning must not fire.
    with _stub_mlx_models():
        eng = _mlx_engine(size="large-v3", armed=False)
        assert _drive_one_step(eng, _growing()) is False
        assert eng.size == "large-v3" and eng._swap is None
    print("  OK  an unarmed MLX session never steps (the catch-up backlog is not a fault)")


def test_minimum_spacing_between_mlx_steps():
    with _stub_mlx_models():
        eng = _mlx_engine(size="large-v3", aged=False)   # last rung change was just now
        assert _drive_one_step(eng, _growing()) is False, "a step fired inside the minimum spacing"
        assert eng.size == "large-v3"
        eng._last_rung_change = time.monotonic() - (T.DOWNGRADE_MIN_SECONDS + 1)
        assert _drive_one_step(eng, _growing()) is True and eng.size == "medium"
    assert T.DOWNGRADE_MIN_SECONDS >= 90.0, T.DOWNGRADE_MIN_SECONDS
    print("  OK  MLX rung changes are at least DOWNGRADE_MIN_SECONDS apart")


# --- 5. the single kill switch ----------------------------------------------

def test_the_adaptive_kill_switch_disables_the_mlx_ladder():
    # One switch, not two: a non-adaptive session (file import, or the struggle nudge turned off at
    # the engine) never steps, just like the CPU ladder.
    with _stub_mlx_models():
        eng = _mlx_engine(size="large-v3", adaptive=False)
        assert _drive_one_step(eng, _growing()) is False
        assert eng.size == "large-v3" and eng._swap is None
    print("  OK  a non-adaptive MLX session never steps (the single kill switch is honoured)")


# --- 6. the honest, rung-named notice + banner callback ---------------------

def test_an_mlx_step_reports_the_rung_honestly():
    downgrades = []
    with _stub_mlx_models():
        eng = _mlx_engine(size="large-v3")
        eng.on_downgrade = lambda old, new: downgrades.append((old, new))
        assert _drive_one_step(eng, _growing()) is True
    # The live transcript names the model and says English-only, in plain words.
    assert any("switched to Whisper medium (English only)" in t for t in _notices(eng)), _notices(eng)
    # And the on_downgrade callback (the banner + toast surface) fires with the honest sizes: the
    # full-quality model it left and the rung it is on now.
    assert downgrades == [("large-v3", "medium")], downgrades
    print("  OK  an MLX step names the rung honestly in the transcript and to the banner callback")


# --- runner ----------------------------------------------------------------

def main():
    tests = [
        test_english_mlx_steps_medium_then_small_on_depth,
        test_maybe_downgrade_dispatches_mlx,
        test_fluister_mlx_never_steps_onto_stock_whisper,
        test_an_uncached_mlx_rung_is_skipped,
        test_a_draining_backlog_never_steps,
        test_an_unarmed_session_does_not_step,
        test_minimum_spacing_between_mlx_steps,
        test_the_adaptive_kill_switch_disables_the_mlx_ladder,
        test_an_mlx_step_reports_the_rung_honestly,
    ]
    for t in tests:
        t()
    print(f"\n{len(tests)} MLX ladder tests passed")


if __name__ == "__main__":
    main()
