"""Tests for AudioRecorder time-alignment (wall-clock chunk placement).

WASAPI loopback delivers NO callbacks while no application renders audio, so the
SYS stream can produce nothing for long stretches (before a call renders audio,
or between calls). The recorder must place every chunk at its wall-clock t_start,
zero-filling delivery gaps, so the folded stereo file stays time-aligned. These
tests drive AudioRecorder.on_chunk directly: no audio devices, no model load.

Also includes a LiveAEC pacing guard: synthetic producers feed the worker for a
few simulated seconds and the per-stream emitted 16 k sample counts must stay
within a bounded delta of the input, protecting against future worker pacing
regressions. Runs against a stub APM, so it needs no LiveKit binding either.

Run:  python tests/test_recorder_align.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import tempfile
import wave
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from live_transcribe.sinks import AudioRecorder

RATE = AudioRecorder.TARGET_RATE


def tone(secs, freq=440.0, amp=0.3):
    t = np.arange(int(secs * RATE), dtype=np.float32) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def read_stereo(path):
    with wave.open(str(path), "rb") as r:
        assert r.getnchannels() == 2, "expected the folded stereo file"
        assert r.getframerate() == RATE
        data = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2").reshape(-1, 2)
    return data[:, 0], data[:, 1]   # left = MIC, right = SYS


def first_activity_s(ch, thresh=100):
    nz = np.nonzero(np.abs(ch.astype(np.int32)) > thresh)[0]
    return None if len(nz) == 0 else nz[0] / RATE


def feed(rec, source, spans, freq=440.0):
    """spans: list of (t_start, secs) chunks, fed in order."""
    for t0, secs in spans:
        rec.on_chunk(source, tone(secs, freq), t0)


def test_start_gap_sys_lands_at_wall_clock():
    # MIC from t=0, SYS delivers nothing until t=40 (nothing rendered): the folded
    # stereo file must place the SYS content at 40 s, not at 0.
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "start-gap")
    feed(rec, "MIC", [(0.0, 15.0), (15.0, 15.0), (30.0, 15.0), (45.0, 15.0)], freq=220)
    feed(rec, "SYS", [(40.0, 15.0), (55.0, 5.0)], freq=660)
    rec.close()
    micc, sysc = read_stereo(d / "start-gap.wav")
    assert len(micc) == len(sysc), "channels must be equal length"
    onset = first_activity_s(sysc)
    assert onset is not None and abs(onset - 40.0) <= 0.5, f"SYS onset {onset}s, expected 40s +-0.5s"
    assert np.all(sysc[: int(39.5 * RATE)] == 0), "SYS must be silent before the gap ends"
    # Both channels cover the full 60 s session.
    assert abs(len(micc) / RATE - 60.0) <= 0.5, f"file length {len(micc)/RATE:.2f}s, expected ~60s"
    print("  OK  40s start gap: SYS content lands at 40s, channels equal length")


def test_mid_session_gap_zero_filled():
    # SYS delivers t=0..10, then nothing (call ended), then t=50..60: content must
    # land at those positions with zeros between.
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "mid-gap")
    feed(rec, "MIC", [(float(t), 10.0) for t in range(0, 60, 10)], freq=220)
    feed(rec, "SYS", [(0.0, 10.0), (50.0, 10.0)], freq=660)
    rec.close()
    micc, sysc = read_stereo(d / "mid-gap.wav")
    assert len(micc) == len(sysc)
    assert first_activity_s(sysc[: int(10 * RATE)]) is not None, "SYS content missing at t=0..10"
    mid = sysc[int(10.5 * RATE): int(49.5 * RATE)]
    assert np.all(mid == 0), "gap between 10s and 50s must be zero-filled"
    tail_onset = first_activity_s(sysc[int(49.0 * RATE):])
    assert tail_onset is not None and abs((49.0 + tail_onset) - 50.0) <= 0.5, \
        f"resumed SYS content at {49.0 + tail_onset:.2f}s, expected 50s +-0.5s"
    print("  OK  mid-session gap (10s..50s): zeros between, content at true positions")


def test_aligned_session_no_zero_fill():
    # Normal session with sub-second t_start jitter: output must be byte-identical
    # to the pre-fix append-only behaviour (no zero-fill ever triggers).
    rng = np.random.RandomState(7)
    spans = []
    t = 0.0
    for _ in range(6):
        secs = 10.0
        spans.append((max(0.0, t + rng.uniform(-0.9, 0.9)), secs))
        t += secs
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "aligned")
    for t0, secs in spans:
        rec.on_chunk("MIC", tone(secs, 220), t0)
        rec.on_chunk("SYS", tone(secs, 660), t0)
    rec.close()
    micc, sysc = read_stereo(d / "aligned.wav")
    # Pre-fix output for this input is the plain concatenation of the chunks.
    ref_mic = (np.clip(np.concatenate([tone(s, 220) for _, s in spans]), -1, 1) * 32767).astype("<i2")
    ref_sys = (np.clip(np.concatenate([tone(s, 660) for _, s in spans]), -1, 1) * 32767).astype("<i2")
    assert np.array_equal(micc, ref_mic), "aligned session must not be modified (MIC)"
    assert np.array_equal(sysc, ref_sys), "aligned session must not be modified (SYS)"
    print("  OK  aligned session with <1s jitter: byte-identical to plain append, no fill")


def test_overlap_appends_as_is_with_warning():
    # A chunk whose t_start is far behind what is already written (should not happen)
    # is appended as-is: no rewind, no fill, alignment of prior audio untouched.
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "overlap")
    rec.on_chunk("MIC", tone(10.0, 220), 0.0)
    rec.on_chunk("MIC", tone(5.0, 330), 2.0)    # 8s behind the 10s already written
    rec.close()
    with wave.open(str(d / "overlap.wav"), "rb") as r:
        assert r.getnchannels() == 1
        n = r.getnframes()
    assert n == int(15.0 * RATE), f"overlap chunk must append as-is (got {n / RATE:.2f}s, want 15s)"
    print("  OK  overlapping (backwards) chunk appended as-is, no corruption")


def test_mono_single_source_gap():
    # Record-only SYS session (single channel): gaps must still be filled so the
    # mono file keeps wall-clock positions too.
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "mono-gap")
    feed(rec, "SYS", [(30.0, 10.0)], freq=660)
    rec.close()
    with wave.open(str(d / "mono-gap.wav"), "rb") as r:
        assert r.getnchannels() == 1
        data = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2")
    onset = first_activity_s(data)
    assert onset is not None and abs(onset - 30.0) <= 0.5, f"mono onset {onset}s, expected 30s"
    print("  OK  mono single-source recording also placed on the session clock")


# ---------------------------------------------------------------------------
# LiveAEC pacing guard (stub APM, no LiveKit binding and no devices needed)
# ---------------------------------------------------------------------------

class _StubFrame:
    def __init__(self, data, rate, ch, samples):
        self.data = data


class _StubAPM:
    def process_reverse_stream(self, frame):
        pass

    def process_stream(self, frame):
        pass


def test_live_aec_pacing_sample_conservation():
    # Feed synthetic producers a few simulated seconds of native-rate audio and pump
    # the worker loop directly: the per-stream emitted 16 k sample counts must stay
    # within a bounded delta of the resampled input (nothing lost, nothing invented).
    # The APM is stubbed so no LiveKit binding is needed, but aec_live imports soxr
    # at module level, so skip where the resampler is not installed.
    try:
        from live_transcribe.aec_live import LiveAEC, TARGET_RATE
    except ImportError as e:
        print(f"  SKIP  LiveAEC pacing guard ({e})")
        return

    near_rate, far_rate = 44100, 48000
    near_out, far_out = [], []
    la = LiveAEC(near_rate, far_rate,
                 on_near=lambda a: near_out.append(a),
                 on_far=lambda a: far_out.append(a))
    la._apm = _StubAPM()
    la._AudioFrame = _StubFrame
    rng = np.random.RandomState(3)
    sim_secs = 4.0
    block = 0.5   # producers deliver ~0.5s native blocks, like the WASAPI callbacks
    n_blocks = int(sim_secs / block)
    for _ in range(n_blocks):
        la.push_near((rng.standard_normal(int(near_rate * block)) * 0.1).astype(np.float32))
        la.push_far((rng.standard_normal(int(far_rate * block)) * 0.1).astype(np.float32))
        la._pump()
    la._pump(flush=True)
    expect = int(sim_secs * TARGET_RATE)
    got_near = sum(len(a) for a in near_out)
    got_far = sum(len(a) for a in far_out)
    # Bound: one 10 ms frame of framing residue plus soxr's small latency (< 50 ms).
    tol = int(0.05 * TARGET_RATE)
    assert abs(got_near - expect) <= tol, f"near emitted {got_near} vs input {expect} (+-{tol})"
    assert abs(got_far - expect) <= tol, f"far emitted {got_far} vs input {expect} (+-{tol})"
    print(f"  OK  LiveAEC pacing: near {got_near}/{expect}, far {got_far}/{expect} samples (tol {tol})")


if __name__ == "__main__":
    failures = 0
    for fn in (test_start_gap_sys_lands_at_wall_clock,
               test_mid_session_gap_zero_filled,
               test_aligned_session_no_zero_fill,
               test_overlap_appends_as_is_with_warning,
               test_mono_single_source_gap,
               test_live_aec_pacing_sample_conservation):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll recorder-alignment tests passed.")
