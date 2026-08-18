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
import contextlib
import io
import os
import sys
import tempfile
import threading
import time
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


def pcm16(float_audio):
    """Match AudioRecorder.on_chunk's own float -> int16 conversion exactly, so
    reference arrays are byte-identical to what the recorder writes (same clip,
    same 32767 scale, same truncating astype)."""
    return (np.clip(float_audio, -1.0, 1.0) * 32767.0).astype("<i2")


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
    # stereo file must place the SYS content at 40 s, not at 0. Assert the exact
    # sample-for-sample PCM, not just an approximate onset, so a regression in the
    # int(t_start * RATE) placement (e.g. round() or an off-by-one chunk offset)
    # cannot slip through a +-0.5s tolerance window.
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "start-gap")
    feed(rec, "MIC", [(0.0, 15.0), (15.0, 15.0), (30.0, 15.0), (45.0, 15.0)], freq=220)
    feed(rec, "SYS", [(40.0, 15.0), (55.0, 5.0)], freq=660)
    rec.close()
    micc, sysc = read_stereo(d / "start-gap.wav")
    assert len(micc) == len(sysc), "channels must be equal length"
    assert len(micc) == int(60.0 * RATE), f"file length {len(micc) / RATE:.3f}s, expected exactly 60s"

    ref_mic = pcm16(np.concatenate([tone(15.0, 220) for _ in range(4)]))
    assert np.array_equal(micc, ref_mic), "MIC (no gap) must be the plain concatenation"

    # SYS: exactly int(40.0*RATE) zero samples, then the two chunks appended in place,
    # with no fill between them (their t_start abuts what was already written).
    ref_sys = pcm16(np.concatenate([
        np.zeros(int(40.0 * RATE), dtype=np.float32),
        tone(15.0, 660),
        tone(5.0, 660),
    ]))
    assert np.array_equal(sysc, ref_sys), "SYS must be zero exactly up to sample int(40*RATE), then exact content"
    print("  OK  40s start gap: SYS content lands sample-exact at int(40*RATE), channels equal length")


def test_mid_session_gap_zero_filled():
    # SYS delivers t=0..10, then nothing (call ended), then t=50..60: content must
    # land at those positions with zeros between. Assert the exact PCM: the first
    # span, the exact zero span (int(10*RATE)..int(50*RATE)), and the resumed span
    # landing at exactly int(50*RATE).
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "mid-gap")
    feed(rec, "MIC", [(float(t), 10.0) for t in range(0, 60, 10)], freq=220)
    feed(rec, "SYS", [(0.0, 10.0), (50.0, 10.0)], freq=660)
    rec.close()
    micc, sysc = read_stereo(d / "mid-gap.wav")
    assert len(micc) == len(sysc) == int(60.0 * RATE)

    ref_mic = pcm16(np.concatenate([tone(10.0, 220) for _ in range(6)]))
    assert np.array_equal(micc, ref_mic), "MIC (no gap) must be the plain concatenation"

    ref_sys = pcm16(np.concatenate([
        tone(10.0, 660),
        np.zeros(int(40.0 * RATE), dtype=np.float32),   # exact gap: int(50*RATE) - int(10*RATE)
        tone(10.0, 660),
    ]))
    assert np.array_equal(sysc, ref_sys), \
        "SYS must be exact content 0..10s, exact zeros 10..50s, exact content at int(50*RATE)"
    print("  OK  mid-session gap (10s..50s): exact zero span, content at exact wall-clock positions")


def test_repeated_subtolerance_gaps_snap_to_wall_clock():
    # Each individual idle gap (1.5s) is below GAP_TOLERANCE_S (2.0s), so on_chunk
    # does not fill it and simply appends: the source silently drifts behind its
    # true wall-clock position. Because `expected` is recomputed from t_start
    # absolutely (not accumulated relative to the previous chunk), that drift is
    # cumulative across chunks even though no single step trips the tolerance. Once
    # the *accumulated* shortfall exceeds 2.0s, the next chunk's gap does exceed
    # tolerance and on_chunk snaps back to the exact wall-clock position, filling
    # the whole accumulated shortfall in one go.
    #
    # chunk length 1.0s, idle 1.5s between chunks -> t_start = i * 2.5s.
    #   i=0: expected=0,      written=0      -> gap=0,      append (no fill)
    #   i=1: expected=40000,  written=16000  -> gap=24000 (1.5s) < 32000 tolerance -> append as-is (no fill)
    #   i=2: expected=80000,  written=32000  -> gap=48000 (3.0s) > 32000 tolerance -> FILL 48000, then append
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "subtol-drift")
    freq = 220
    for i in range(3):
        rec.on_chunk("MIC", tone(1.0, freq), i * 2.5)
    rec.close()
    with wave.open(str(d / "subtol-drift.wav"), "rb") as r:
        assert r.getnchannels() == 1
        data = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2")

    fill_start = 2 * int(1.0 * RATE)          # 32000: written position right before chunk 2
    fill_point = int(2 * 2.5 * RATE)          # 80000: exact wall-clock target for chunk 2 (i=2)
    assert fill_point - fill_start == 48000, "expected fill span must be exactly 48000 samples (3.0s)"

    ref = pcm16(np.concatenate([
        tone(1.0, freq),                                  # chunk 0 @ [0, 16000)
        tone(1.0, freq),                                  # chunk 1, sub-tolerance gap NOT filled @ [16000, 32000)
        np.zeros(fill_point - fill_start, dtype=np.float32),   # accumulated lag snapped @ [32000, 80000)
        tone(1.0, freq),                                  # chunk 2 lands exactly at int(2*2.5*RATE)=80000
    ]))
    assert len(data) == fill_point + int(1.0 * RATE) == 96000
    assert np.array_equal(data, ref), \
        "accumulated sub-tolerance drift must be corrected in one snap-fill at the exact wall-clock point"
    print("  OK  repeated 1.5s sub-tolerance gaps: drift accumulates, then snaps to exact wall-clock at 80000")


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
    # is appended as-is: no rewind, no fill, alignment of prior audio untouched. The
    # recorder also warns (print, once per source) on this condition; feed a second
    # overlapping chunk for the same source and assert the warning fires exactly
    # once, not once per offending chunk, and that the PCM is still pure append.
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "overlap")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rec.on_chunk("MIC", tone(10.0, 220), 0.0)
        rec.on_chunk("MIC", tone(5.0, 330), 2.0)    # 8s behind the 10s already written -> warns
        rec.on_chunk("MIC", tone(3.0, 440), 4.0)    # still behind -> already warned, no 2nd warning
    rec.close()
    out = buf.getvalue()
    warn_lines = [ln for ln in out.splitlines() if "warning:" in ln and "MIC" in ln]
    assert len(warn_lines) == 1, f"expected exactly one MIC warning, got {len(warn_lines)}: {warn_lines}"

    with wave.open(str(d / "overlap.wav"), "rb") as r:
        assert r.getnchannels() == 1
        data = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2")
    ref = pcm16(np.concatenate([tone(10.0, 220), tone(5.0, 330), tone(3.0, 440)]))
    assert np.array_equal(data, ref), "overlapping chunks must append as-is, byte for byte, no rewind or fill"
    print("  OK  overlapping (backwards) chunks appended as-is, warning fires exactly once")


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


def test_start_time_recorder_writes_from_t0():
    # t0-capture: the start-time recorder stays on anchor=None and simply receives chunks from t0 via
    # _feed, doing absolute wall-clock placement on t_start. A first chunk whose t_start is 0.0 lands
    # at sample 0 with no leading offset, so a record-on session captures from the instant Begin is
    # clicked. This pins the LOCKED decision that the recorder is anchor=None (no first-chunk anchor
    # slicing, which is what re-introduced the MIC/SYS channel skew before).
    d = Path(tempfile.mkdtemp())
    rec = AudioRecorder(d / "from-t0")   # anchor=None: a start-time recorder
    rec.on_chunk("MIC", tone(2.0, 220), 0.0)   # first mic chunk at t0
    rec.on_chunk("SYS", tone(2.0, 660), 0.0)   # first sys chunk at t0
    rec.close()
    micc, sysc = read_stereo(d / "from-t0.wav")
    assert len(micc) == len(sysc) == int(2.0 * RATE), (len(micc), len(sysc))
    # Byte-exact from sample 0: no leading zero-fill and no anchor offset -> the file starts at t0.
    assert np.array_equal(micc, pcm16(tone(2.0, 220))), "MIC must start at sample 0 (t0), no offset"
    assert np.array_equal(sysc, pcm16(tone(2.0, 660))), "SYS must start at sample 0 (t0), no offset"
    print("  OK  start-time recorder (anchor=None) writes both channels from t0 (sample 0), no offset")


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
    # Feed synthetic producers a few simulated seconds of native-rate audio on their
    # own real threads, paced in real time like the WASAPI callbacks, against a real
    # worker thread that loops _pump() on its own cadence (mirroring _run(), minus
    # the livekit.rtc import that start()/_run() would otherwise require). This
    # exercises the actual queue producer/consumer concurrency and cadence, not just
    # the single-threaded _pump() body: the per-stream emitted 16 k sample counts
    # must stay within a bounded delta of the resampled input (nothing lost, nothing
    # invented), and nothing may be dropped as queue-full under this pacing.
    # The APM is stubbed so no LiveKit binding is needed, but aec_live imports soxr
    # at module level, so skip where the resampler is not installed.
    try:
        from live_transcribe.aec_live import LiveAEC, TARGET_RATE
    except ImportError as e:
        print(f"  SKIP  LiveAEC pacing guard ({e})")
        return

    near_rate, far_rate = 44100, 48000
    near_out, far_out = [], []
    out_lock = threading.Lock()

    def on_near(a):
        with out_lock:
            near_out.append(a)

    def on_far(a):
        with out_lock:
            far_out.append(a)

    la = LiveAEC(near_rate, far_rate, on_near=on_near, on_far=on_far)
    la._apm = _StubAPM()
    la._AudioFrame = _StubFrame

    sim_secs = 4.0
    block = 0.5   # producers deliver ~0.5s native blocks, like the WASAPI callbacks
    n_blocks = int(sim_secs / block)

    stop_worker = threading.Event()

    def worker():
        # Same loop shape as LiveAEC._run(), without its livekit.rtc import: poll
        # _pump() on a 10ms cadence, then a final flush pump after stop is signalled.
        while not stop_worker.is_set():
            la._pump()
            stop_worker.wait(0.01)
        la._pump(flush=True)

    worker_thread = threading.Thread(target=worker, name="test-live-aec-worker", daemon=True)
    worker_thread.start()

    def produce(push_fn, rate, seed):
        rng = np.random.RandomState(seed)
        for _ in range(n_blocks):
            push_fn((rng.standard_normal(int(rate * block)) * 0.1).astype(np.float32))
            time.sleep(block)   # real-time pacing: exercises actual queue cadence, not a tight loop

    near_thread = threading.Thread(target=produce, args=(la.push_near, near_rate, 3),
                                    name="test-near-producer", daemon=True)
    far_thread = threading.Thread(target=produce, args=(la.push_far, far_rate, 4),
                                   name="test-far-producer", daemon=True)
    t0 = time.monotonic()
    near_thread.start()
    far_thread.start()
    near_thread.join(timeout=10.0)
    far_thread.join(timeout=10.0)
    stop_worker.set()
    worker_thread.join(timeout=2.0)
    wall = time.monotonic() - t0
    assert wall < 10.0, f"pacing test ran {wall:.1f}s wall, expected under ~10s"
    assert not worker_thread.is_alive(), "worker thread failed to stop"

    expect = int(sim_secs * TARGET_RATE)
    got_near = sum(len(a) for a in near_out)
    got_far = sum(len(a) for a in far_out)
    assert la._dropped == 0, f"worker dropped {la._dropped} block(s) under real threaded pacing"
    # Bound: one 10 ms frame of framing residue plus soxr's small latency (< 50 ms).
    tol = int(0.05 * TARGET_RATE)
    assert abs(got_near - expect) <= tol, f"near emitted {got_near} vs input {expect} (+-{tol})"
    assert abs(got_far - expect) <= tol, f"far emitted {got_far} vs input {expect} (+-{tol})"
    print(f"  OK  LiveAEC threaded pacing: near {got_near}/{expect}, far {got_far}/{expect} "
          f"samples (tol {tol}), dropped={la._dropped}, wall={wall:.1f}s")


if __name__ == "__main__":
    failures = 0
    for fn in (test_start_gap_sys_lands_at_wall_clock,
               test_mid_session_gap_zero_filled,
               test_repeated_subtolerance_gaps_snap_to_wall_clock,
               test_aligned_session_no_zero_fill,
               test_overlap_appends_as_is_with_warning,
               test_mono_single_source_gap,
               test_start_time_recorder_writes_from_t0,
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
