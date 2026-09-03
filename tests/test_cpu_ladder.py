"""Tests for the honest CPU ladder: in-family only, present-only, shed instead of degrade.

Background, so a future reader knows what these pin down. On a CPU-only laptop the live ladder
walked medium -> small -> base -> tiny inside half an hour. base and tiny had no Fluister entry, so
those two rungs were STOCK multilingual Whisper, which on Afrikaans answers in Dutch and loops
("dat, dat, dat"). Nearly half the far end's words never reached the transcript. The ladder had
bought speed by leaving the model family, and nobody was told.

What is covered, cheapest first:

  1. Registration: every rung of CPU_LADDER has a Fluister build, so an Afrikaans session can walk
     the whole ladder without ever resolving to a stock model.
  2. Rung selection (_next_rung): in-family only, present-only, an absent rung is SKIPPED to the
     next present one, and when nothing is left it returns None (the caller then sheds).
  3. Trigger hygiene: a full window is not enough on its own - DOWNGRADE_MIN_SECONDS must have
     passed, the first decode on a freshly built model is discarded, and burst-fed chunks are not
     evidence about holding real time.
  4. The helper-thread swap: _maybe_downgrade returns immediately while the next rung builds, the
     worker keeps decoding on the old model, at most one build is in flight, and installing it
     clears the loop history.
  5. The shed valve: drops the OLDEST audio first, bounds the backlog, says so in the transcript
     and reports the total.
  6. CPU decode defaults: 20 s encoder window + beam 1 on CPU, 30 s + beam 5 on CUDA, with the
     pad_or_trim override isolated per model AND per thread.
  7. Tier pick: CPU auto is small on every machine, whatever the core count.

No model is ever loaded: load_model / resolve_model / model_present / WhisperModel are stubbed.

Run:  python tests/test_cpu_ladder.py   (from the project root; exit 0 = pass)
"""
import os
import queue
import sys
import threading
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import faster_whisper.transcribe as fw_transcribe
from live_transcribe import __main__ as M
from live_transcribe import transcribe as T


# --- helpers ---------------------------------------------------------------

def _stub_engine(size="medium", family="fluister", language="af", adaptive=True,
                 is_cpu=True, rtf=2.0, aged=True):
    """An Engine with only the attributes the ladder touches, built WITHOUT __init__ so no model is
    loaded. aged=True puts the last rung change far enough in the past to clear the minimum spacing."""
    eng = T.Engine.__new__(T.Engine)
    eng.family = family
    eng.adaptive = adaptive
    eng._is_cpu = is_cpu
    eng.size = size
    eng.language = language
    eng.engine = "auto"
    eng._compute_type = "int8"
    eng._cpu_threads = 4
    eng.model = object()
    eng.model_name = f"model-{size}"
    eng.is_fluister = family == "fluister"
    eng.subscribers = []
    eng.on_downgrade = None
    eng._swap = None
    eng._cold_decode = False
    eng._last_rung_change = 0.0 if aged else time.monotonic()
    eng._front = deque()
    eng._queue = queue.Queue(maxsize=32)
    eng._stop = threading.Event()
    eng._recent = T.RecentEmissions()
    eng.shed_seconds = 0.0
    eng.shed_events = 0
    eng._rtf = deque([rtf] * T.DOWNGRADE_WINDOW, maxlen=T.DOWNGRADE_WINDOW)
    return eng


def _step(eng, t=1.0, timeout=10.0):
    """Drive one full ladder step. The build runs on a helper thread, so a step needs two passes:
    the first starts it, the second installs it. Returns True if the rung actually changed."""
    before = eng.size
    eng._maybe_downgrade(t)
    swap = eng._swap
    if swap is not None:
        assert swap["done"].wait(timeout), "the helper-thread build never finished"
    eng._maybe_downgrade(t)
    return eng.size != before


class _stub_models:
    """Stub load_model / model_present so the ladder never loads or probes a real model.
    `present` is a predicate over the model id; the default says every rung is on this machine.
    resolve_model is left REAL, because whether a rung is in-family is exactly what is under test."""

    def __init__(self, present=None, build=None):
        self._present_fn = present or (lambda model_id: True)
        self._build_fn = build or (lambda *a, **k: object())

    def __enter__(self):
        self._saved = (T.load_model, T.model_present)
        T.load_model = self._build_fn
        T.model_present = self._present_fn
        return self

    def __exit__(self, *exc):
        T.load_model, T.model_present = self._saved


def _chunk(seconds=15.0):
    return np.zeros(int(seconds * 16000), dtype=np.float32)


def _notices(eng):
    """Every notice the engine emitted into a collecting subscriber."""
    return [s.text for s in eng.subscribers[0].seen] if eng.subscribers else []


class _Collect:
    def __init__(self):
        self.seen = []

    def __call__(self, seg):
        self.seen.append(seg)


# --- 1. registration -------------------------------------------------------

def test_every_ladder_rung_has_a_fluister_build():
    # The rule the field incident broke: an Afrikaans session must never resolve to a stock model,
    # at ANY rung. base and tiny are the two that used to.
    for size in T.CPU_LADDER:
        assert size in T.FLUISTER_REPOS, f"{size} has no Fluister repo; the ladder would leave the family"
        assert T.FLUISTER_REPOS[size].startswith("digiphyte/fluister-"), T.FLUISTER_REPOS[size]
        model_id, family = T.resolve_model(size, "af")
        assert family == "fluister", f"af session resolves {size} to {family} ({model_id})"
    # And the repo ids for the two new rungs are exactly the published ones.
    assert T.FLUISTER_REPOS["base"] == "digiphyte/fluister-base"
    assert T.FLUISTER_REPOS["tiny"] == "digiphyte/fluister-tiny"
    # voicedl knows their download size, so "Set up models" can show and fetch them.
    from live_transcribe import voicedl
    for size in ("base", "tiny"):
        assert voicedl._FLUISTER_SIZES.get(size, 0) > 10_000_000, size
    print("  OK  every CPU_LADDER rung has a Fluister build; base/tiny registered and sized")


def test_a_stock_session_keeps_the_stock_ladder():
    # The other half of "in-family": an explicitly English (stock Whisper) session must keep
    # walking stock sizes, not get quietly moved onto the Afrikaans tune.
    with _stub_models():
        eng = _stub_engine(size="small", family="whisper", language="en")
        assert eng._next_rung() == ("base", "base", "whisper"), eng._next_rung()
    print("  OK  a stock-Whisper session keeps the stock ladder")


# --- 2. rung selection -----------------------------------------------------

def test_ladder_never_yields_a_stock_model_for_an_afrikaans_session():
    with _stub_models():
        eng = _stub_engine(size="medium", family="fluister", language="af")
        seen = []
        while True:
            nxt = eng._next_rung()
            if nxt is None:
                break
            size, model_id, family = nxt
            assert family == "fluister", (size, model_id, family)
            assert model_id != size, f"{size} resolved to the bare stock size name"
            seen.append(size)
            eng.size = size
        assert seen == ["small", "base", "tiny"], seen
    print("  OK  an af session walks medium -> small -> base -> tiny entirely inside Fluister")


def test_an_absent_rung_is_skipped_and_never_downloaded():
    # No mid-session network downloads: a rung is usable only if it is already on this machine.
    absent = T.FLUISTER_REPOS["base"]
    tuned_base = T._FLUISTER["base"]

    def present(model_id):
        return model_id not in (absent, tuned_base)

    with _stub_models(present=present):
        eng = _stub_engine(size="small", family="fluister", language="af")
        nxt = eng._next_rung()
        assert nxt is not None and nxt[0] == "tiny", f"base is absent, so tiny is next; got {nxt}"
    # Nothing present below the current model at all -> no rung, the caller sheds.
    with _stub_models(present=lambda model_id: False):
        eng = _stub_engine(size="medium", family="fluister", language="af")
        assert eng._next_rung() is None
        eng.on_downgrade = None
        eng._maybe_downgrade(1.0)
        assert eng.size == "medium", "with nothing present the engine must stay where it is"
        assert eng._swap is None, "an absent rung must never start a build (that would download)"
    print("  OK  an absent rung is skipped; with none present the engine holds and sheds instead")


def test_the_floor_holds_instead_of_falling_out_of_the_ladder():
    with _stub_models():
        eng = _stub_engine(size="tiny", family="fluister", language="af")
        assert eng._next_rung() is None, "there is nothing below tiny; shed, do not degrade"
        assert _step(eng) is False and eng.size == "tiny"
    print("  OK  below the last rung the engine holds the model and never leaves the family")


# --- 3. trigger hygiene ----------------------------------------------------

def test_minimum_spacing_between_rung_changes():
    with _stub_models():
        # A full window of hopeless RTF, but the last rung change was just now.
        eng = _stub_engine(size="medium", aged=False)
        assert _step(eng) is False, "a step fired inside the minimum spacing window"
        assert eng.size == "medium"
        # Wind the clock back past the minimum and the very same evidence steps.
        eng._last_rung_change = time.monotonic() - (T.DOWNGRADE_MIN_SECONDS + 1)
        assert _step(eng) is True and eng.size == "small"
    assert T.DOWNGRADE_MIN_SECONDS >= 90.0, T.DOWNGRADE_MIN_SECONDS
    print("  OK  rung changes are at least DOWNGRADE_MIN_SECONDS apart")


def test_burst_fed_chunks_are_not_evidence_about_real_time():
    eng = _stub_engine()
    eng._last_feed = {}
    audio = _chunk(15.0)
    # First chunk of a source: nothing to compare against, so never a burst.
    assert eng._is_burst("SYS", audio) is False
    # A second chunk immediately behind it: fed far faster than real time -> burst.
    assert eng._is_burst("SYS", audio) is True
    # A chunk that arrived a real chunk-length later is live evidence.
    eng._last_feed["SYS"] = time.monotonic() - 15.0
    assert eng._is_burst("SYS", audio) is False
    # MIC and SYS both feed this one engine, so a near-zero CROSS-source gap is normal and must
    # not be read as a burst. This is why the test is per source.
    eng._last_feed = {"SYS": time.monotonic()}
    assert eng._is_burst("MIC", audio) is False
    print("  OK  burst detection is per source and only fires on a faster-than-real-time feed")


def test_cold_and_burst_samples_are_excluded_from_the_downgrade_window():
    # Drives the REAL worker loop. The first decode on a freshly built model pays that model's
    # one-off load cost, and a burst-fed chunk was never a real-time obligation; counting either
    # is what let one slow moment cascade the whole ladder.
    with _fake_whisper():
        eng = _real_engine("cpu")
    eng._silence_gate = False          # the test audio is silence; we want it decoded anyway
    eng.adaptive = True
    eng.start()
    try:
        now = time.monotonic()
        eng._queue.put(("SYS", _chunk(), 0.0, now, False))     # cold: excluded
        eng._queue.put(("SYS", _chunk(), 15.0, now, True))     # burst: excluded
        eng._queue.put(("SYS", _chunk(), 30.0, now, False))    # live: counted
        eng._queue.put(("SYS", _chunk(), 45.0, now, False))    # live: counted
        deadline = time.time() + 10
        while eng.pending() and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)
        assert len(eng._rtf) == 2, f"expected 2 eligible samples, got {list(eng._rtf)}"
    finally:
        eng.stop(timeout=10)
    print("  OK  the first decode on a new model and every burst-fed chunk stay out of the window")


# --- 4. the helper-thread swap ---------------------------------------------

def test_the_next_rung_builds_off_the_worker_thread():
    started = threading.Event()
    release = threading.Event()

    def slow_build(*a, **k):
        started.set()
        assert release.wait(10), "the fake builder was never released"
        return object()

    with _stub_models(build=slow_build):
        eng = _stub_engine(size="medium")
        old_model = eng.model
        t0 = time.monotonic()
        eng._maybe_downgrade(1.0)            # must NOT wait for the build
        cost = time.monotonic() - t0
        assert cost < 0.5, f"_maybe_downgrade blocked the worker for {cost:.2f}s"
        assert started.wait(5), "the build never started on its helper thread"
        # Meanwhile the worker still has the OLD model and the old size: it keeps decoding.
        assert eng.size == "medium" and eng.model is old_model
        # At most one build in flight, however often the worker comes back round.
        swap = eng._swap
        eng._rtf.extend([9.0] * T.DOWNGRADE_WINDOW)
        eng._maybe_downgrade(2.0)
        assert eng._swap is swap, "a second build was started while one was already in flight"
        release.set()
        assert swap["done"].wait(5)
        eng._maybe_downgrade(3.0)            # installs it
        assert eng.size == "small" and eng.model is not old_model
        assert eng._swap is None, "the finished build must be released after the swap"
    print("  OK  the next rung builds on a helper thread; the worker keeps the old model meanwhile")


def test_a_rung_change_clears_the_loop_history_and_the_rtf_window():
    with _stub_models():
        eng = _stub_engine(size="medium")
        eng._recent.observe("SYS", "dit is 'n toets", 1.0)
        assert eng._recent._hist, "precondition: the loop history has something in it"
        assert _step(eng) is True
        assert not eng._recent._hist, "a model change must clear the cross-segment loop history"
        assert len(eng._rtf) == 0, "the new model must be judged on fresh evidence"
        assert eng._cold_decode is True, "the first decode on the new model must be discarded"
    print("  OK  a rung change clears _recent, the RTF window and arms the cold-decode discard")


def test_a_failed_build_leaves_the_engine_on_its_current_rung():
    def boom(*a, **k):
        raise RuntimeError("not on this machine")

    with _stub_models(build=boom):
        eng = _stub_engine(size="medium")
        assert _step(eng) is False
        assert eng.size == "medium" and eng._swap is None
    print("  OK  a failed rung build is swallowed and the engine keeps decoding where it was")


# --- 5. the shed valve -----------------------------------------------------

def test_shedding_drops_the_oldest_audio_first_and_bounds_the_backlog():
    eng = _stub_engine(size="tiny")            # nothing left below: shedding is the only move
    got = _Collect()
    eng.subscribers = [got]
    fired = []
    eng.on_downgrade = lambda old, new: fired.append((old, new))
    for i in range(8):                          # 8 x 15 s = 120 s of backlog
        eng._queue.put(("SYS", _chunk(15.0), float(i * 15), time.monotonic(), False))
    assert eng._backlog_seconds() == 120.0, eng._backlog_seconds()
    eng._maybe_shed(200.0)
    # Bounded.
    assert eng._backlog_seconds() <= T.SHED_BACKLOG_SECONDS, eng._backlog_seconds()
    # OLDEST first: what survives is the TAIL of the feed, not the head. queue.Queue on its own
    # drops the NEWEST (a full queue refuses the put), which is the wrong end.
    kept = [item[2] for item in eng._front]
    assert kept == [75.0, 90.0, 105.0], kept          # exactly the bound, and it is the TAIL
    assert eng.shed_seconds == 75.0 and eng.shed_events == 1, (eng.shed_seconds, eng.shed_events)
    # Said out loud, with how much and where.
    notices = [s.text for s in got.seen]
    assert len(notices) == 1, notices
    assert "skipped 75 s of audio" in notices[0], notices[0]
    assert "(0:00 to 1:15)" in notices[0], notices[0]
    assert notices[0].startswith("[engine: ") and notices[0].endswith("]"), notices[0]
    # And surfaced to the UI. old == new says "this was a shed, not a model change".
    assert fired == [("tiny", "tiny")], fired
    print("  OK  shedding drops the oldest first, bounds the backlog, and reports the span")


def test_shedding_is_live_only_and_quiet_when_the_backlog_is_fine():
    # A file import is not real time and must never lose a chunk.
    eng = _stub_engine(size="tiny", adaptive=False)
    got = _Collect()
    eng.subscribers = [got]
    for i in range(8):
        eng._queue.put(("FILE", _chunk(15.0), float(i * 15), time.monotonic(), False))
    eng._maybe_shed(10.0)
    assert eng._backlog_seconds() == 120.0 and eng.shed_events == 0, "a file import lost audio"
    assert got.seen == []
    # And a healthy live backlog is left completely alone.
    eng2 = _stub_engine(size="small")
    got2 = _Collect()
    eng2.subscribers = [got2]
    for i in range(2):
        eng2._queue.put(("SYS", _chunk(15.0), float(i * 15), time.monotonic(), False))
    eng2._maybe_shed(10.0)
    assert eng2.shed_events == 0 and got2.seen == [] and eng2._backlog_seconds() == 30.0
    # And a draining shutdown keeps every queued second: stop(drain=True) exists so the tail of
    # the session is not lost, and by then there is no real-time obligation left to defend.
    eng3 = _stub_engine(size="tiny")
    eng3.subscribers = [_Collect()]
    for i in range(8):
        eng3._queue.put(("SYS", _chunk(15.0), float(i * 15), time.monotonic(), False))
    eng3._stop.set()
    eng3._maybe_shed(10.0)
    assert eng3.shed_events == 0 and eng3._backlog_seconds() == 120.0, "a drain lost audio"
    print("  OK  shedding never touches a file import or a draining shutdown, and stays quiet inside the bound")


def test_a_shed_pass_never_loses_the_shutdown_sentinel():
    eng = _stub_engine(size="tiny")
    eng.subscribers = [_Collect()]
    for i in range(8):
        eng._queue.put(("SYS", _chunk(15.0), float(i * 15), time.monotonic(), False))
    eng._queue.put(None)                        # stop() already set _stop before putting this
    eng._maybe_shed(10.0)
    assert eng._stop.is_set(), "the sentinel was swallowed without recording the shutdown"
    print("  OK  a shed pass that swallows the sentinel still records the shutdown")


def test_indicative_flips_below_small():
    for size, want in (("medium", False), ("small", False), ("base", True), ("tiny", True),
                       ("large-v3", False)):
        eng = _stub_engine(size=size)
        assert eng.indicative is want, (size, eng.indicative)
    print("  OK  `indicative` is true exactly below `small`, so the UI can say the text is rough")


# --- 6. CPU decode defaults ------------------------------------------------

class _FakeFE:
    sampling_rate = 16000
    hop_length = 160

    def __init__(self):
        self.chunk_length = 30
        self.n_samples = 30 * 16000
        self.nb_max_frames = self.n_samples // 160


class _FakeModel:
    def __init__(self, *a, **k):
        self.feature_extractor = _FakeFE()
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return iter(()), object()


class _fake_whisper:
    """Stub WhisperModel so _build_model can be exercised without a real model on disk."""

    def __enter__(self):
        self._saved = T.WhisperModel
        T.WhisperModel = _FakeModel
        return self

    def __exit__(self, *exc):
        T.WhisperModel = self._saved


def _real_engine(tier, **kw):
    """A REAL Engine (so __init__ runs) with a fake model. Call inside _fake_whisper()."""
    saved = T.load_model
    T.load_model = lambda name, device, ct, cpu_threads=8, local_only=False: _build_fake(device)
    try:
        return T.Engine(tier=tier, language="af", **kw)
    finally:
        T.load_model = saved


def _build_fake(device):
    m = _FakeModel()
    if device == "cpu":
        T.set_encoder_window(m, T.CPU_ENCODER_WINDOW_S)
    return m


def test_cpu_models_get_the_measured_window_and_gpu_models_do_not():
    with _fake_whisper():
        cpu_m = T._build_model("small", "cpu", "int8", 4)
        cuda_m = T._build_model("large-v3", "cuda", "int8_float16", 4)
    assert cpu_m.feature_extractor.chunk_length == 20, cpu_m.feature_extractor.chunk_length
    assert cpu_m.feature_extractor.n_samples == 20 * 16000
    assert cpu_m.feature_extractor.nb_max_frames == 2000
    assert cpu_m._vm_encoder_frames == 2000
    # The GPU model is untouched: Whisper's native 30 s / 3000 frames, and no window attribute at
    # all, so encoder_window() is inert for it.
    assert cuda_m.feature_extractor.chunk_length == 30
    assert cuda_m.feature_extractor.nb_max_frames == 3000
    assert not hasattr(cuda_m, "_vm_encoder_frames")
    print("  OK  a CPU model is built at a 20 s window; a CUDA model keeps the full 30 s")


def test_the_pad_override_is_scoped_to_the_model_and_the_thread():
    # faster-whisper pads every mel back to 3000 frames unless the calling thread says otherwise.
    # That name is process-global, so the override has to be scoped twice over.
    with _fake_whisper():
        cpu_m = T._build_model("small", "cpu", "int8", 4)
        cuda_m = T._build_model("large-v3", "cuda", "int8_float16", 4)
    mel = np.zeros((80, 1500), dtype=np.float32)
    assert fw_transcribe.pad_or_trim(mel).shape[-1] == 3000, "the stock default must be unchanged"
    with T.encoder_window(cuda_m) as frames:
        assert frames is None
        assert fw_transcribe.pad_or_trim(mel).shape[-1] == 3000, "a GPU model must keep 30 s"
    seen = {}
    with T.encoder_window(cpu_m) as frames:
        assert frames == 2000
        assert fw_transcribe.pad_or_trim(mel).shape[-1] == 2000
        # Another thread decoding at the same time (a CUDA engine in the same process) must not
        # be able to observe the CPU engine's window.
        th = threading.Thread(target=lambda: seen.update(w=fw_transcribe.pad_or_trim(mel).shape[-1]))
        th.start()
        th.join(5)
    assert seen.get("w") == 3000, seen
    assert fw_transcribe.pad_or_trim(mel).shape[-1] == 3000, "the override must be restored"
    # An explicit length always wins.
    assert fw_transcribe.pad_or_trim(mel, 1000).shape[-1] == 1000
    print("  OK  the shorter pad length is scoped to the model AND to the decoding thread")


def test_beam_defaults_per_device_and_the_decode_kwargs():
    with _fake_whisper():
        cpu = _real_engine("cpu")
        gpu = _real_engine("gpu")
        explicit = _real_engine("cpu", beam_size=5)
    assert cpu.beam_size == T.CPU_BEAM_SIZE == 1, cpu.beam_size
    assert gpu.beam_size == T.DEFAULT_BEAM_SIZE == 5, gpu.beam_size
    assert explicit.beam_size == 5, "an explicit beam_size must still be honoured on CPU"
    # And the decode actually asks for the shorter window, so the VAD agrees with the encoder.
    cpu._silence_gate = False
    cpu.start()
    try:
        cpu._queue.put(("SYS", _chunk(), 0.0, time.monotonic(), False))
        deadline = time.time() + 10
        while cpu.pending() and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)
    finally:
        cpu.stop(timeout=10)
    kw = cpu.model.calls[0]
    assert kw["chunk_length"] == 20, kw
    assert kw["beam_size"] == 1, kw
    assert kw["condition_on_previous_text"] is False, kw   # the guards are still there
    print("  OK  CPU decodes at beam 1 with chunk_length 20; GPU keeps beam 5")


def test_chunk_audio_stays_at_15_seconds_on_cpu():
    # The encoder window changed; the AUDIO chunk did not. 15 s chunks with a 20 s window is the
    # measured pairing; matching the window to the chunk (15 s) is catastrophic.
    assert M.default_chunk_seconds("cpu") == 15
    assert M.default_chunk_seconds("cpu-mid") == 15
    assert T.CPU_ENCODER_WINDOW_S == 20
    print("  OK  audio chunks stay at 15 s while the encoder window is 20 s")


# --- 7. tier pick ----------------------------------------------------------

def test_cpu_auto_is_small_whatever_the_core_count():
    from live_transcribe import cudadl
    saved = (os.cpu_count, cudadl.cuda_ready, os.environ.get("SA_LIVE_TIER"))
    try:
        os.environ.pop("SA_LIVE_TIER", None)
        cudadl.cuda_ready = lambda: False
        for cores in (2, 4, 8, 16, 32):
            os.cpu_count = lambda c=cores: c
            assert M._cpu_auto_tier() == "cpu", cores
            assert M.pick_tier("auto") == "cpu", cores
        # Medium is still there, one explicit click away.
        assert M.pick_tier("cpu-mid") == "cpu-mid"
        assert M._QUALITY_TO_CPU_TIER["medium"] == "cpu-mid"
        assert T.TIER_CONFIG["cpu"]["model"] == "small"
        assert T.TIER_CONFIG["cpu-mid"]["model"] == "medium"
    finally:
        os.cpu_count, cudadl.cuda_ready = saved[0], saved[1]
        if saved[2] is not None:
            os.environ["SA_LIVE_TIER"] = saved[2]
    print("  OK  CPU auto starts at small on every core count; medium stays hand-selectable")


TESTS = (test_every_ladder_rung_has_a_fluister_build,
         test_a_stock_session_keeps_the_stock_ladder,
         test_ladder_never_yields_a_stock_model_for_an_afrikaans_session,
         test_an_absent_rung_is_skipped_and_never_downloaded,
         test_the_floor_holds_instead_of_falling_out_of_the_ladder,
         test_minimum_spacing_between_rung_changes,
         test_burst_fed_chunks_are_not_evidence_about_real_time,
         test_cold_and_burst_samples_are_excluded_from_the_downgrade_window,
         test_the_next_rung_builds_off_the_worker_thread,
         test_a_rung_change_clears_the_loop_history_and_the_rtf_window,
         test_a_failed_build_leaves_the_engine_on_its_current_rung,
         test_shedding_drops_the_oldest_audio_first_and_bounds_the_backlog,
         test_shedding_is_live_only_and_quiet_when_the_backlog_is_fine,
         test_a_shed_pass_never_loses_the_shutdown_sentinel,
         test_indicative_flips_below_small,
         test_cpu_models_get_the_measured_window_and_gpu_models_do_not,
         test_the_pad_override_is_scoped_to_the_model_and_the_thread,
         test_beam_defaults_per_device_and_the_decode_kwargs,
         test_chunk_audio_stays_at_15_seconds_on_cpu,
         test_cpu_auto_is_small_whatever_the_core_count)

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
    print("\nAll CPU-ladder tests passed.")
