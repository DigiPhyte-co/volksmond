"""WP-M2 tests: the MlxWhisperModel adapter + the transcribe.py mlx seam.

mlx-whisper publishes no Windows wheel, so nothing here imports the real thing:
a FAKE `mlx_whisper` module is injected into sys.modules and the tests exercise
the real adapter (kwarg mapping, local-first snapshot resolve, segment shape),
the real `_build_model` "mlx" branch, and a real Engine on the "mlx-turbo" tier
draining its backlog exactly like tests/test_engine_drain.py does on ct2.

Run:  python tests/test_mlx_backend.py   (from the project root; exit 0 = pass)
"""
import contextlib
import importlib
import os
import sys
import time
import types

import numpy as np

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import huggingface_hub

from live_transcribe import transcribe


# ── helpers ────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _fake_mlx(delay=0.0):
    """Install a fake `mlx_whisper` module; yields the list of recorded calls.

    The fake echoes a chunk marker back as one dict segment (audio sample 0 is
    the marker), mirroring test_engine_drain's _FakeModel, with an optional
    delay so a real backlog exists when stop() fires."""
    calls = []

    def _transcribe(audio, **kwargs):
        if delay:
            time.sleep(delay)
        calls.append({"audio": audio, "kwargs": kwargs})
        try:
            marker = int(float(audio.flat[0]))
        except Exception:
            marker = 0
        return {
            "segments": [{"text": f"seg-{marker}", "start": 0.25, "end": 1.5}],
            "language": "af",
        }

    mod = types.ModuleType("mlx_whisper")
    mod.transcribe = _transcribe
    prev = sys.modules.get("mlx_whisper")
    sys.modules["mlx_whisper"] = mod
    try:
        yield calls
    finally:
        if prev is None:
            sys.modules.pop("mlx_whisper", None)
        else:
            sys.modules["mlx_whisper"] = prev


@contextlib.contextmanager
def _patched(obj, name, value):
    orig = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, orig)


_FAKE_SNAPSHOT = os.path.join("fake", "hub", "snapshots", "abc123")


@contextlib.contextmanager
def _snapshot_local(path=_FAKE_SNAPSHOT):
    """huggingface_hub.snapshot_download resolves to a fake LOCAL snapshot path,
    the only way an adapter may be constructed (an uncached repo raises)."""
    with _patched(huggingface_hub, "snapshot_download", lambda *a, **k: path):
        yield


@contextlib.contextmanager
def _snapshot_raises():
    """huggingface_hub.snapshot_download always fails: the local cache is 'empty',
    so the adapter constructor must raise (never hand mlx-whisper the repo id,
    which would let its own downloader fetch multi-GB silently)."""
    def _raise(*a, **k):
        raise FileNotFoundError("not cached (test)")
    with _patched(huggingface_hub, "snapshot_download", _raise):
        yield


def _audio(marker=0.0, n=160):
    """A small ndarray chunk carrying `marker` in every sample. 160 samples is
    under one 100 ms silence-gate frame, so _is_silence never eats a chunk."""
    return np.full(n, float(marker), dtype=np.float32)


# ── adapter unit tests ─────────────────────────────────────────────────────

def test_local_snapshot_path_preferred():
    # A cached repo resolves to its LOCAL snapshot path (local_files_only=True),
    # and that path, not the repo id, is what mlx-whisper receives per chunk.
    from live_transcribe import mlxbackend
    seen = []

    def _fake_snapshot(repo, **kw):
        seen.append((repo, kw))
        return r"C:\fake\hub\snapshots\abc123"

    with _fake_mlx() as calls, _patched(huggingface_hub, "snapshot_download", _fake_snapshot):
        m = mlxbackend.MlxWhisperModel("mlx-community/whisper-large-v3-mlx")
        m.transcribe(_audio(), language="en")
    assert seen == [("mlx-community/whisper-large-v3-mlx", {"local_files_only": True})], seen
    assert calls[0]["kwargs"]["path_or_hf_repo"] == r"C:\fake\hub\snapshots\abc123"
    print("  OK  cached repo resolves to the local snapshot path (local_files_only=True)")


def test_uncached_repo_raises_never_downloads():
    # An uncached repo must RAISE at construction (codex H1): falling back to the raw
    # repo id would let warm-up's dummy inference trigger mlx-whisper's own multi-GB
    # download, before consent and outside voicedl's slot/progress machinery.
    from live_transcribe import mlxbackend
    with _fake_mlx() as calls, _snapshot_raises():
        try:
            mlxbackend.MlxWhisperModel("digiphyte/fluister-turbo-mlx")
            raise SystemExit("adapter construction succeeded on an uncached repo")
        except RuntimeError as e:
            assert "not downloaded" in str(e), e
    assert calls == [], "no transcribe call may happen for an uncached repo"
    print("  OK  uncached repo raises at construction; mlx-whisper can never self-download")


def test_guard_kwarg_mapping():
    # The full production call surface (transcribe.py's Engine._run call), with the
    # real GUARD dict, must arrive at mlx_whisper correctly translated.
    from live_transcribe import mlxbackend
    with _fake_mlx() as calls, _snapshot_local():
        m = mlxbackend.MlxWhisperModel("mlx-community/whisper-large-v3-mlx")
        m.transcribe(_audio(), language="af", initial_prompt="Volksmond, DigiPhyte",
                     vad_filter=True, beam_size=5, **transcribe.GUARD)
    kw = calls[0]["kwargs"]
    # Renamed: faster-whisper's log_prob_threshold is mlx-whisper's logprob_threshold.
    assert kw["logprob_threshold"] == transcribe.GUARD["log_prob_threshold"]
    assert "log_prob_threshold" not in kw
    # temperature list -> tuple (mlx-whisper's fallback ladder wants a tuple).
    assert kw["temperature"] == tuple(transcribe.GUARD["temperature"])
    assert isinstance(kw["temperature"], tuple)
    # Dropped: mlx-whisper has neither of these.
    assert "vad_filter" not in kw and "beam_size" not in kw
    # Pass-throughs and fixed kwargs.
    assert kw["condition_on_previous_text"] is False
    assert kw["no_speech_threshold"] == transcribe.GUARD["no_speech_threshold"]
    assert kw["compression_ratio_threshold"] == transcribe.GUARD["compression_ratio_threshold"]
    assert kw["word_timestamps"] is False
    assert kw["verbose"] is None
    assert kw["language"] == "af"
    assert kw["initial_prompt"] == "Volksmond, DigiPhyte"
    print("  OK  GUARD mapping: rename + tuple + drops + fixed word_timestamps/verbose")


def test_dropped_and_renamed_constants_pinned():
    # The adapter's translation tables are the contract WP-M3/M4 build on; pin them.
    from live_transcribe import mlxbackend
    assert mlxbackend.DROPPED_KWARGS == frozenset({"vad_filter", "beam_size"})
    assert mlxbackend.KWARG_RENAMES == {"log_prob_threshold": "logprob_threshold"}
    print("  OK  DROPPED_KWARGS / KWARG_RENAMES pinned")


def test_segment_contract():
    from live_transcribe import mlxbackend

    def _two_segs(audio, **kwargs):
        return {"segments": [{"text": " hallo", "start": 0.0, "end": 1.2},
                             {"text": "wereld ", "start": 1.2, "end": 2.0}],
                "language": "af"}

    with _fake_mlx(), _snapshot_local():
        m = mlxbackend.MlxWhisperModel("mlx-community/whisper-large-v3-mlx")
        sys.modules["mlx_whisper"].transcribe = _two_segs
        segs, info = m.transcribe(_audio(), language="af")
    seg_list = list(segs)   # the Engine forces the iterable exactly like this
    assert [s.text for s in seg_list] == [" hallo", "wereld "]
    assert [s.start for s in seg_list] == [0.0, 1.2]
    assert [s.end for s in seg_list] == [1.2, 2.0]
    assert isinstance(segs, list) and isinstance(info, dict)
    assert info["language"] == "af"   # info is the raw mlx dict; the Engine discards it
    print("  OK  segments expose .text/.start/.end; (list, info) shape survives list()")


def test_rejects_non_ndarray_audio():
    from live_transcribe import mlxbackend
    with _fake_mlx(), _snapshot_local():
        m = mlxbackend.MlxWhisperModel("mlx-community/whisper-large-v3-mlx")
        try:
            m.transcribe([0.0] * 160, language="af")
            raise SystemExit("adapter accepted non-ndarray audio")
        except TypeError:
            pass
    print("  OK  non-ndarray audio raises TypeError (ffmpeg path never reachable)")


def test_mlx_model_for_map():
    from live_transcribe import mlxbackend
    assert mlxbackend.mlx_model_for("digiphyte/fluister-turbo") == "digiphyte/fluister-turbo-mlx"
    assert mlxbackend.mlx_model_for("large-v3") == "mlx-community/whisper-large-v3-mlx"
    # No MLX form: unmapped sizes and local ct2 dirs miss the map (ct2 CPU fallback
    # happens at selection time, per D3).
    assert mlxbackend.mlx_model_for("medium") is None
    assert mlxbackend.mlx_model_for(r"C:\Users\seanf\.cache\af-lora-turbo-ct2-int8") is None
    print("  OK  mlx_model_for: mapped repos returned, local dirs and 'medium' -> None")


# ── seam tests ─────────────────────────────────────────────────────────────

def test_import_stays_lazy_and_tiers_present():
    # Importing transcribe/mlxbackend must never import mlx_whisper (no Windows
    # wheel exists), and TIER_CONFIG must carry the two mlx tiers.
    prev = sys.modules.pop("mlx_whisper", None)
    try:
        importlib.import_module("live_transcribe.mlxbackend")
        importlib.reload(sys.modules["live_transcribe.mlxbackend"])
        assert "mlx_whisper" not in sys.modules, "module import pulled in mlx_whisper"
    finally:
        if prev is not None:
            sys.modules["mlx_whisper"] = prev
    for tier, model in (("mlx", "large-v3"), ("mlx-turbo", "large-v3-turbo")):
        cfg = transcribe.TIER_CONFIG[tier]
        assert cfg["model"] == model and cfg["device"] == "mlx", cfg
    print("  OK  mlx_whisper import is lazy; TIER_CONFIG has mlx + mlx-turbo")


def test_tier_choices_unchanged():
    # The mlx tiers must NOT widen the CLI surface on Windows.
    from live_transcribe.__main__ import TIER_CHOICES
    assert "mlx" not in TIER_CHOICES and "mlx-turbo" not in TIER_CHOICES
    print("  OK  TIER_CHOICES untouched (mlx tiers unreachable from the CLI)")


def test_default_chunk_seconds_mlx_is_gpu_class():
    from live_transcribe.__main__ import default_chunk_seconds
    assert default_chunk_seconds("mlx") == 8
    assert default_chunk_seconds("mlx-turbo") == 8
    assert default_chunk_seconds("gpu") == 8          # unchanged
    assert default_chunk_seconds("cpu-strong") == 15  # unchanged
    print("  OK  default_chunk_seconds: mlx tiers 8 s (GPU-class), others unchanged")


def test_engine_drain_parity_on_mlx_tier():
    # An Engine on tier "mlx-turbo" (fake backend) must drain its backlog exactly
    # like test_engine_drain proves for ct2, and the CPU downgrade ladder must be
    # inert: _is_cpu False, _maybe_downgrade a no-op even under terrible RTF.
    from live_transcribe import mlxbackend
    cache_key = ("digiphyte/fluister-turbo", "mlx", "fp16")
    transcribe._MODEL_CACHE.pop(cache_key, None)
    with _fake_mlx(delay=0.03), _snapshot_local(), \
         _patched(transcribe, "_FLUISTER", dict(transcribe._FLUISTER)):
        # Force the af turbo resolve to the hosted repo id (a dev machine with a
        # local ct2 dir would otherwise resolve to a path outside the D3 map).
        transcribe._FLUISTER["large-v3-turbo"] = "digiphyte/fluister-turbo"
        engine = transcribe.Engine(tier="mlx-turbo")   # language "af" default
        try:
            assert isinstance(engine.model, mlxbackend.MlxWhisperModel)
            assert engine._is_cpu is False, "mlx must not be treated as CPU"
            assert engine._device == "mlx"
            assert engine.is_fluister is True
            collected = []
            engine.subscribe(lambda seg: collected.append(seg.text))
            engine.start()
            n = 8
            for i in range(n):
                engine.on_chunk("MIC", _audio(i), float(i))   # fill the queue with a backlog
        finally:
            engine.stop(drain=True, timeout=30)               # must finish all of it
        assert len(collected) == n, f"drain lost chunks: got {len(collected)}/{n}"
        assert sorted(collected) == sorted(f"seg-{i}" for i in range(n)), \
            f"unexpected/duplicated output: {collected}"
        # The CPU ladder must never fire on mlx: saturate the RTF window with a
        # hopeless value and prove _maybe_downgrade leaves the model alone.
        model_before, size_before = engine.model, engine.size
        for _ in range(engine._rtf.maxlen):
            engine._rtf.append(99.0)
        engine._maybe_downgrade(0.0)
        assert engine.model is model_before and engine.size == size_before, \
            "CPU downgrade ladder fired on an mlx engine"
    transcribe._MODEL_CACHE.pop(cache_key, None)
    print(f"  OK  mlx-turbo engine drained all {n} chunks; downgrade ladder inert")


if __name__ == "__main__":
    failures = 0
    for fn in (test_local_snapshot_path_preferred,
               test_uncached_repo_raises_never_downloads,
               test_guard_kwarg_mapping,
               test_dropped_and_renamed_constants_pinned,
               test_segment_contract,
               test_rejects_non_ndarray_audio,
               test_mlx_model_for_map,
               test_import_stays_lazy_and_tiers_present,
               test_tier_choices_unchanged,
               test_default_chunk_seconds_mlx_is_gpu_class,
               test_engine_drain_parity_on_mlx_tier):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll mlx-backend tests passed.")
