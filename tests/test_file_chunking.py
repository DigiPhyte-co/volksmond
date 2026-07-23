"""Unit tests for iter_silence_chunks: silence-aware chunk boundaries for the
file-upload transcription path (replaces the blind fixed 8s/15s grid that cut
words mid-syllable at every seam). Mirrors the live path's cutter: cuts snap to
detected silences, force-cut at 1.5x the target when speech never pauses, and
the chunks tile the input exactly (no overlap, no lost samples).

Run:  python tests/test_file_chunking.py   (exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from live_transcribe.capture_core import iter_silence_chunks, _find_last_silence

SR = 16000


def noise(secs, db=-12.0, seed=0):
    n = int(secs * SR)
    a = np.random.RandomState(seed).randn(n).astype(np.float32)
    return a * ((10.0 ** (db / 20.0)) / (float(np.sqrt(np.mean(a * a))) + 1e-9))


def speech_with_pauses(total_secs, pauses):
    """Loud noise with hard-silence gaps at the given (start_s, end_s) spans."""
    a = noise(total_secs)
    for s, e in pauses:
        a[int(s * SR):int(e * SR)] = 0.0
    return a


def chunks_of(audio, chunk_seconds=8.0):
    return list(iter_silence_chunks(audio, SR, chunk_seconds))


def assert_tiles(audio, chunks):
    """Chunks reconstruct the input exactly: contiguous, no overlap, no loss."""
    pos = 0
    for start, chunk in chunks:
        assert start == pos, (start, pos)
        pos += len(chunk)
    assert pos == len(audio), (pos, len(audio))
    assert np.array_equal(np.concatenate([c for _, c in chunks]), audio)


def test_cuts_land_in_silences():
    pauses = [(7.0, 7.5), (14.5, 15.0), (21.0, 21.5)]
    a = speech_with_pauses(30.0, pauses)
    chunks = chunks_of(a)
    assert_tiles(a, chunks)
    # The first three boundaries must each land inside one of the silent spans.
    bounds = [(start + len(chunk)) / SR for start, chunk in chunks[:-1]]
    assert len(bounds) >= 3, bounds
    for b, (s, e) in zip(bounds, pauses):
        assert s <= b <= e, (b, s, e)


def test_continuous_noise_force_cuts():
    a = noise(30.0)  # no silence anywhere
    chunks = chunks_of(a)
    assert_tiles(a, chunks)
    # Force-cuts at exactly 1.5x the 8s target: 12.0s, 24.0s, then the 6s tail.
    lens = [len(c) / SR for _, c in chunks]
    assert lens == [12.0, 12.0, 6.0], lens


def test_short_tail_emitted():
    a = noise(12.3)  # force-cut at 12.0 leaves a 0.3s tail, shorter than MIN_EMIT
    chunks = chunks_of(a)
    assert_tiles(a, chunks)
    lens = [len(c) / SR for _, c in chunks]
    assert lens[0] == 12.0 and abs(lens[1] - 0.3) < 0.01, lens


def test_min_emit_respected():
    # Genuinely exercises the `c >= min_emit` guard rather than just asserting
    # the outcome. With chunk_seconds=1.0, MIN_EMIT_SECONDS (1.0s) means
    # min_emit == chunk_samples == 16000 samples, so ANY silence found on the
    # first search (buf_len == chunk_samples) is necessarily below min_emit
    # and must be rejected.
    #
    # Silence sits at 0.1-0.4s (samples 1600:6400). The backward scan over the
    # first buf_len=16000 window (window=4800 samples, step=2400, lookback
    # 2.0s > buf_len so search_floor=4800) hits it at end=6400, giving
    # cut = 6400 - 2400 = 4000 samples -- far below min_emit (16000). The
    # guard must refuse this cut. No later candidate window (as buf_len grows
    # by BLOCK_SECONDS steps to the 24000-sample limit, i.e.
    # int(16000 * MAX_CHUNK_MULTIPLIER)) touches that same silent span again,
    # so growth continues to the force-cut boundary and the first chunk is
    # exactly 24000 samples (1.5s), not 4000.
    #
    # Mutation check performed: with the `c >= min_emit` condition in
    # iter_silence_chunks temporarily changed to just `c is not None` (guard
    # removed) in a scratch copy, this test fails -- the guard-intact
    # `lens == [24000, 8000]` assertion breaks because the mutated run
    # produces `lens == [4000, 24000, 4000]` (three chunks: the wrongly
    # accepted early cut at 4000 samples, then the search resumes from the
    # new position and finds further cuts instead of one clean force-cut).
    # The scratch copy was not committed; only this test file changed.
    a = speech_with_pauses(2.0, [(0.1, 0.4)])
    chunks = chunks_of(a, chunk_seconds=1.0)
    assert_tiles(a, chunks)
    lens = [len(c) for _, c in chunks]
    assert lens == [24000, 8000], lens
    for _, c in chunks[:-1]:
        assert len(c) >= SR * 1.0, len(c)


def test_find_last_silence_midpoint_cut():
    # Exact sample-index check on _find_last_silence directly, using a small
    # sample rate so every index is hand-computable. sr=100: window_samples =
    # int(300 * 100 / 1000) = 30, step = 15, lookback_samples = 200.
    # n=250 -> search_floor = max(30, 250-200) = 50, so candidate `end`
    # values scanned back from 250 are 250,235,220,205,190,175,160,145,...
    # (stopping once <= 50). A silent span of exactly one window's width is
    # placed at samples [130:160), which matches the candidate window for
    # end=160 exactly (mono[160-30:160] == mono[130:160]); every later
    # (larger) candidate window overlaps only part of the silent span and
    # so still contains noise and fails the RMS threshold. Expected cut is
    # the midpoint of that window: 160 - 30//2 = 145.
    sr = 100
    n = 250
    rng = np.random.RandomState(1)
    a = (rng.randn(n).astype(np.float32)) * 0.5
    a[130:160] = 0.0
    cut = _find_last_silence(a, sr)
    assert cut == 145, cut


def test_find_last_silence_lookback_floor():
    # Silence that exists in the audio but falls entirely outside the
    # lookback window must not be found: it demonstrates search_floor =
    # max(window_samples, n - lookback_samples) actually bounds the scan.
    # sr=100, n=300: lookback_samples=200 -> search_floor = max(30, 100) =
    # 100. The smallest candidate window scanned is end=105 (window
    # [75:105)); a silent span at [0:30), far below that floor, is never
    # examined, so the function must return None despite real silence
    # being present in the array.
    sr = 100
    n = 300
    rng = np.random.RandomState(1)
    a = (rng.randn(n).astype(np.float32)) * 0.5
    a[0:30] = 0.0
    cut = _find_last_silence(a, sr)
    assert cut is None, cut


def test_block_extension_stepping():
    # Exact proof that iter_silence_chunks grows the search window by
    # exactly one BLOCK_SECONDS step (0.5s -> 50 samples at sr=100) when the
    # first search comes up empty. sr=100, chunk_seconds=2.0: chunk_samples
    # = 200. A silent span at samples [220:250) sits entirely past the first
    # buf_len=200 window (which only covers [0:200)), so that first search
    # must find nothing and buf_len must grow to chunk_samples + block =
    # 200 + 50 = 250 before the span is even visible. At buf_len=250 the
    # span exactly fills the end=250 candidate window ([220:250)), the very
    # first one checked, giving cut = 250 - 15 = 235.
    sr = 100
    n = 400
    rng = np.random.RandomState(2)
    a = (rng.randn(n).astype(np.float32)) * 0.5
    a[220:250] = 0.0
    # Confirm the two searches directly: nothing at buf_len=200, hit at 250.
    assert _find_last_silence(a[0:200], sr) is None
    assert _find_last_silence(a[0:250], sr) == 235
    chunks = list(iter_silence_chunks(a, sr, 2.0))
    starts = [s for s, _ in chunks]
    lens = [len(c) for _, c in chunks]
    assert starts == [0, 235], starts
    assert lens == [235, 165], lens


def test_force_cut_boundary_exact():
    # Exact sample indices for the force-cut path with no silence anywhere.
    # sr=100, chunk_seconds=2.0: chunk_samples=200,
    # limit = int(200 * MAX_CHUNK_MULTIPLIER) = 300. Pure noise never
    # satisfies the RMS threshold, so the first chunk force-cuts at exactly
    # `limit` and the 50-sample remainder is emitted as the final tail.
    sr = 100
    n = 350
    rng = np.random.RandomState(2)
    a = (rng.randn(n).astype(np.float32)) * 0.5
    chunks = list(iter_silence_chunks(a, sr, 2.0))
    starts = [s for s, _ in chunks]
    lens = [len(c) for _, c in chunks]
    assert starts == [0, 300], starts
    assert lens == [300, 50], lens


def test_whole_file_shorter_than_target():
    a = noise(5.0)
    chunks = chunks_of(a)
    assert_tiles(a, chunks)
    assert len(chunks) == 1


def test_empty_audio():
    assert chunks_of(np.zeros(0, dtype=np.float32)) == []


def test_file_branch_shape_not_grid_locked():
    # Integration shape of the single-track FILE branch in web/app.py: t_start is
    # start_sample / 16000.0, must be strictly increasing and NOT locked to the
    # old fixed 8.0s grid on speech that actually pauses.
    pauses = [(i * 9.0 + 6.3, i * 9.0 + 6.8) for i in range(12)]
    a = speech_with_pauses(110.0, pauses)
    items = [(i / 16000.0, "FILE", chunk) for i, chunk in iter_silence_chunks(a, SR, 8.0)]
    t_starts = [t for t, _, _ in items]
    assert all(b > a_ for a_, b in zip(t_starts, t_starts[1:])), t_starts
    gaps = [round(b - a_, 3) for a_, b in zip(t_starts, t_starts[1:])]
    on_grid = sum(1 for g in gaps if abs(g / 8.0 - round(g / 8.0)) < 1e-6)
    assert on_grid <= len(gaps) // 4, (on_grid, gaps)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"\nall {len(tests)} file-chunking tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
