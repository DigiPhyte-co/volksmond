"""Regression test for the Stop-loses-the-tail bug.

When Stop is pressed, the engine has a backlog of already-captured chunks still
queued (transcription runs a couple of minutes behind real time). The old
Engine.stop() set a flag that made the worker loop exit immediately, discarding
that backlog, i.e. the last few minutes of the meeting were lost.

These tests prove the new behaviour:
  - stop(drain=True)  -> every queued chunk is transcribed before exit
  - stop(drain=False) -> the backlog is abandoned (fast abort)

No real Whisper model is loaded: we monkeypatch transcribe.WhisperModel with a
fake, so this runs in well under a second and needs no GPU/model download.

Run:  python tests/test_engine_drain.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import time

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import transcribe


class _FakeSeg:
    def __init__(self, text, start=0.0, end=1.0):
        self.text = text
        self.start = start
        self.end = end


class _FakeModel:
    """Stand-in for faster_whisper.WhisperModel. Echoes the chunk marker back as
    one segment, with a small delay so a real backlog exists when stop() fires."""
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, **kwargs):
        time.sleep(0.03)
        return ([_FakeSeg(f"seg-{audio}")], {})


def _make_engine():
    engine = transcribe.Engine(tier="cpu-strong")  # model is the fake (patched below)
    collected = []
    engine.subscribe(lambda seg: collected.append(seg.text))
    return engine, collected


def test_drain_processes_whole_backlog():
    engine, collected = _make_engine()
    engine.start()
    n = 8
    for i in range(n):
        engine.on_chunk("MIC", i, float(i))   # fill the queue with a backlog
    engine.stop(drain=True, timeout=30)        # must finish all of it
    assert len(collected) == n, f"drain lost chunks: got {len(collected)}/{n}"
    assert sorted(collected) == sorted(f"seg-{i}" for i in range(n)), \
        f"unexpected/duplicated output: {collected}"
    print(f"  OK  drain=True processed all {n} queued chunks")


def test_abort_drops_backlog():
    engine, collected = _make_engine()
    engine.start()
    m = 30
    for i in range(m):
        engine.on_chunk("MIC", i, float(i))
    engine.stop(drain=False, timeout=30)       # abandon the backlog
    assert len(collected) < m, f"abort should drop some, but got all {len(collected)}/{m}"
    print(f"  OK  drain=False abandoned the backlog ({len(collected)}/{m} processed before abort)")


def test_no_new_audio_accepted_during_shutdown():
    engine, collected = _make_engine()
    engine.start()
    engine._stop.set()                         # simulate shutdown in progress
    engine.on_chunk("MIC", 999, 0.0)           # should be ignored
    engine._stop.clear()
    engine.stop(drain=True, timeout=30)
    assert "seg-999" not in collected, "chunk accepted after shutdown began"
    print("  OK  on_chunk rejects new audio once shutdown has begun")


if __name__ == "__main__":
    transcribe.WhisperModel = _FakeModel       # patch before any Engine is built
    failures = 0
    for fn in (test_drain_processes_whole_backlog,
               test_abort_drops_backlog,
               test_no_new_audio_accepted_during_shutdown):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll engine-drain tests passed.")
