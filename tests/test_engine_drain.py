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


_ORIG_MODEL = None


def setup_module(module=None):
    """Install the fake model for pytest runs (xunit hook, no pytest import needed).
    The plain-script path patches in __main__ below; pytest never executes that block,
    which used to leave the REAL WhisperModel in place and fail every engine test."""
    global _ORIG_MODEL
    _ORIG_MODEL = transcribe.WhisperModel
    transcribe.WhisperModel = _FakeModel


def teardown_module(module=None):
    transcribe.WhisperModel = _ORIG_MODEL


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


def test_blocking_feed_drops_nothing():
    # File import feeds the whole file as fast as possible into the small (32-slot)
    # queue. With block=True the producer waits for the transcriber instead of
    # dropping, so a long file is transcribed in full (the hour-file truncation fix).
    engine, collected = _make_engine()
    engine.start()
    n = 80  # well over the 32-slot queue
    for i in range(n):
        # Bounded wait (mirrors production's timeout): a drain regression should
        # fail this test fast, not hang it on a never-draining queue.
        assert engine.on_chunk("FILE", i, float(i), block=True, timeout=10), \
            "blocking feed timed out enqueueing (worker not draining?)"
    engine.stop(drain=True, timeout=60)
    assert engine._dropped == 0, f"blocking feed dropped {engine._dropped} chunks"
    assert len(collected) == n, f"blocking feed lost chunks: {len(collected)}/{n}"
    print(f"  OK  blocking feed transcribed all {n} chunks with a 32-slot queue (no drops)")


def test_hallucination_filter():
    # Drops memorised junk (Amara credit), the anchor-prompt leak, and whole-segment
    # video end-cards, but must NOT drop real speech that merely contains those words.
    h = transcribe._is_hallucination
    drop = [
        "Ondertitels ingediend door die Amara.org gemeenschap",
        "Algemene woorde, baie, nogal, lekker, sjoe, eish",
        "Ons praat Suid-Afrikaans, nie Nederlands nie.",
        "please subscribe",
        "Thanks for watching!",
    ]
    keep = [
        "We should subscribe to the new tool next quarter.",
        "Thanks for watching the demo, any questions?",
        "Ek het a passion as het kom by taal",
        "Lekker boys, soos baie van julle weet",
        "Dit is Afrikaans, nie Nederlands nie",   # genuine: only the full anchor phrase is junk
    ]
    for t in drop:
        assert h(t), f"should have dropped: {t!r}"
    for t in keep:
        assert not h(t), f"should have kept: {t!r}"
    print("  OK  hallucination filter drops junk and whole-segment end-cards, keeps real speech")


def test_mic_published_after_delay_no_live_dedup():
    # The engine no longer de-dups live; it only holds MIC briefly (for live ordering) then
    # publishes it. Echo removal is deferred to the saved-transcript cleanup, so a MIC echo of
    # a SYS line is still published here (both copies present) and nothing is lost before save.
    engine, collected = _make_engine()
    engine.start()
    engine.on_chunk("SYS", "alpha beta gamma delta", 1.0)
    engine.on_chunk("MIC", "alpha beta gamma delta", 1.4)    # would-be echo: still published live
    engine.on_chunk("MIC", "zulu yankee xray whiskey", 5.0)
    engine.stop(drain=True, timeout=30)
    assert collected.count("seg-alpha beta gamma delta") == 2, f"engine must not drop live: {collected}"
    assert "seg-zulu yankee xray whiskey" in collected, collected
    assert len(collected) == 3, f"unexpected segments: {collected}"
    print("  OK  engine holds + publishes MIC (no live de-dup); echo removal deferred to save")


def test_no_sys_means_all_mic_published():
    # Mic-only (no system audio): nothing to match against, so every MIC line must survive
    # the delay + flush. Guards against the hold ever silently swallowing real speech.
    engine, collected = _make_engine()
    engine.start()
    n = 5
    for i in range(n):
        engine.on_chunk("MIC", i, float(i))
    engine.stop(drain=True, timeout=30)
    assert len(collected) == n, f"mic-only lost lines: {collected}"
    assert sorted(collected) == sorted(f"seg-{i}" for i in range(n)), f"unexpected: {collected}"
    print("  OK  mic-only: all MIC lines published (delay never drops without a SYS twin)")


def test_live_language_change_keeps_model():
    # A language-only change (the bilingual-meeting fix) keeps the loaded model and only re-points
    # the decoder, dropping the Afrikaans anchor for English. Whitebox: apply the queued change
    # directly instead of running the worker.
    engine, collected = _make_engine()             # default language "af"
    model_before = engine.model
    assert engine.language == "af"
    assert engine.initial_prompt == transcribe.AF_ANCHOR_PROMPT
    engine.request_change(language="en", engine="auto")   # no model -> keep the current one
    engine._apply_pending_change(0.0)
    assert engine.language == "en", engine.language
    assert engine.model is model_before, "language-only change must NOT swap the model"
    assert engine.initial_prompt is None, f"en should drop the af anchor, got {engine.initial_prompt!r}"
    print("  OK  live language change keeps the model and recomposes the prompt")


def test_live_model_change_swaps_model():
    # A model change hands the engine a pre-built model + its labels; the worker swaps it in.
    engine, collected = _make_engine()
    model_before = engine.model
    new_model = _FakeModel()
    engine.request_change(language="af", engine="auto", model=new_model,
                          model_name="digiphyte/fluister-small", size="small", family="fluister")
    engine._apply_pending_change(0.0)
    assert engine.model is new_model and engine.model is not model_before, "model change must swap the model"
    assert engine.size == "small" and engine.model_name == "digiphyte/fluister-small", (engine.size, engine.model_name)
    assert engine.is_fluister is True and engine.family == "fluister"
    print("  OK  live model change swaps the model and updates the engine labels")


if __name__ == "__main__":
    transcribe.WhisperModel = _FakeModel       # patch before any Engine is built
    failures = 0
    for fn in (test_drain_processes_whole_backlog,
               test_abort_drops_backlog,
               test_no_new_audio_accepted_during_shutdown,
               test_blocking_feed_drops_nothing,
               test_hallucination_filter,
               test_mic_published_after_delay_no_live_dedup,
               test_no_sys_means_all_mic_published,
               test_live_language_change_keeps_model,
               test_live_model_change_swaps_model):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll engine-drain tests passed.")
