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
from live_transcribe.capture_core import iter_silence_chunks

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
    # Silence very early in the lookback must not produce a sub-1s chunk:
    # every chunk except the final tail is at least MIN_EMIT_SECONDS long.
    a = speech_with_pauses(30.0, [(0.2, 0.7), (6.2, 6.7), (13.0, 13.5)])
    chunks = chunks_of(a)
    assert_tiles(a, chunks)
    for _, c in chunks[:-1]:
        assert len(c) >= SR * 1.0, len(c)


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
