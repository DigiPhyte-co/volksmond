"""Tests for the automatic quiet-channel boost on the FILE transcription path.

audioboost.boost_if_quiet: a channel whose active-speech median RMS sits below
-30 dBFS is normalised to -20 dBFS (static gain -> gentle compressor -> trim ->
tanh soft clip); anything at or above the trigger passes through byte-identical.
Evidence base: a -33.6 dBFS mic channel produced a 15-line repeated-word
hallucination loop that vanished after this exact treatment, while a -28.3 dBFS
channel showed no measurable change from it.

Covers: the trigger boundary (-31 boosts, -29 does not), the -20 dBFS landing,
compressor engagement on loud bursts, the soft-clip bound, the one-quiet-side
stereo case, byte-identity pass-through, and an end-to-end run through the web
FILE branch proving the BOOSTED audio is what feeds the chunker/engine.

Run:  python tests/test_audioboost.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from live_transcribe import audioboost

SR = 16000


def speech_at(db, secs=10.0, seed=0, burst_s=0.5, gap_s=0.5):
    """Noise bursts separated by silence, scaled so the ACTIVE median RMS is
    exactly `db` dBFS. Burst frames sit far above the -45 dBFS activity floor and
    gaps far below it, so a uniform gain never shifts the frame population."""
    rng = np.random.RandomState(seed)
    a = rng.randn(int(secs * SR)).astype(np.float32)
    period = burst_s + gap_s
    t = (np.arange(len(a)) / SR) % period
    a[t >= burst_s] = 0.0
    med = audioboost.measure_active_rms(a)
    return (a * 10.0 ** ((db - med) / 20.0)).astype(np.float32)


def test_measure_active_rms():
    for db in (-20.0, -31.0, -40.0):
        got = audioboost.measure_active_rms(speech_at(db))
        assert abs(got - db) < 0.05, (db, got)
    # Silence and too-short audio measure as None, and must never boost.
    assert audioboost.measure_active_rms(np.zeros(SR, dtype=np.float32)) is None
    assert audioboost.measure_active_rms(np.zeros(8, dtype=np.float32)) is None
    out, g, landing = audioboost.boost_if_quiet(np.zeros(SR, dtype=np.float32))
    assert g == 0.0 and float(np.abs(out).max()) == 0.0 and landing is None
    print("  OK  measure_active_rms: exact on synthetic speech, None on silence (never boosted)")


def test_trigger_boundary():
    # Locked rule: boost only strictly below -30 dBFS active median.
    quiet = speech_at(-31.0)
    out, g, _ = audioboost.boost_if_quiet(quiet)
    assert g > 0.0, "a -31 dBFS channel must boost"
    assert out is not quiet
    healthy = speech_at(-29.0)
    out2, g2, _ = audioboost.boost_if_quiet(healthy)
    assert g2 == 0.0, "a -29 dBFS channel must pass through"
    assert out2 is healthy, "pass-through must return the input object"
    print(f"  OK  trigger boundary: -31 dBFS boosts (+{g:.1f} dB), -29 dBFS passes through")


def test_landing_minus20():
    # The boosted channel lands on the -20 dBFS active median within +-0.5 dB,
    # measured post-hoc on the OUTPUT (fresh mask), like a listener would.
    for db in (-31.0, -33.6, -38.0):
        out, g, landing = audioboost.boost_if_quiet(speech_at(db, seed=int(-db)))
        got = audioboost.measure_active_rms(out)
        assert abs(got - audioboost.TARGET_DB) <= 0.5, (db, got, g)
        assert landing is not None and abs(landing - got) <= 0.05, (landing, got)
    print("  OK  landing: -31/-33.6/-38 dBFS inputs all land on -20 dBFS +-0.5 dB")


def test_compression_engages_on_loud_bursts():
    # A quiet base (triggers the boost) with a few loud shouts: after the static
    # gain the shouts sit far above the -12 dBFS compressor threshold, so their
    # level must come out well below what the static gain alone would give.
    a = speech_at(-33.0, secs=10.0, seed=3)
    shout = np.where(np.abs(a) > 0, np.sign(a), 0.0).astype(np.float32)
    span = slice(int(2.0 * SR), int(2.4 * SR))
    a[span] = (shout[span] * 10.0 ** (-6.0 / 20.0) *
               np.abs(np.random.RandomState(4).randn(span.stop - span.start))
               .clip(0.2, 1.0).astype(np.float32))
    in_burst_db = 20.0 * np.log10(float(np.sqrt(np.mean(a[span] ** 2))) + 1e-12)
    out, g, _ = audioboost.boost_if_quiet(a)
    assert g > 0.0
    out_burst_db = 20.0 * np.log10(float(np.sqrt(np.mean(out[span] ** 2))) + 1e-12)
    static_only_db = in_burst_db + g
    reduction = static_only_db - out_burst_db
    assert reduction >= 3.0, (in_burst_db, out_burst_db, g, reduction)
    print(f"  OK  compressor: loud burst held {reduction:.1f} dB below the static-gain-only level")


def test_compressor_instant_attack_frame_aligned():
    # A frame-aligned quiet->loud step: the FIRST loud sample must get the full
    # frame gain reduction (truly instant attack), the preceding quiet frame must
    # be untouched (no pre-attenuation), and the release must recover smoothly.
    fw = 160  # 10 ms at 16 k, the compressor's envelope frame
    quiet_frames, loud_frames, tail_frames = 20, 10, 60
    x = np.concatenate([
        np.full(quiet_frames * fw, 0.01, dtype=np.float32),   # far below threshold
        np.full(loud_frames * fw, 0.90, dtype=np.float32),    # far above -12 dBFS
        np.full(tail_frames * fw, 0.01, dtype=np.float32),
    ])
    out = audioboost._compress(x)
    g = out / x  # per-sample applied gain (input never zero)
    first_loud = quiet_frames * fw
    # No pre-attenuation: every quiet sample before the step keeps unity gain.
    assert np.allclose(g[:first_loud], 1.0, atol=1e-9), \
        f"quiet frame pre-attenuated (min gain {g[:first_loud].min():.6f})"
    # Instant attack: the first loud sample already carries the frame's full
    # reduction (identical to mid-frame), and it is a real reduction.
    assert g[first_loud] < 0.7, f"first loud sample barely reduced (gain {g[first_loud]:.3f})"
    assert abs(g[first_loud] - g[first_loud + fw // 2]) < 1e-9, \
        "attack gain must be flat from the frame START, not ramped in"
    # Smooth release: after the loud region the gain recovers monotonically toward
    # 1.0 with no zipper steps (per-sample increments stay tiny).
    rel = g[(quiet_frames + loud_frames + 1) * fw:]
    d = np.diff(rel)
    assert np.all(d >= -1e-9), "release gain must be monotone non-decreasing"
    assert float(d.max()) < 0.01, f"release steps too coarse (max step {d.max():.4f})"
    assert rel[-1] > 0.99, f"gain did not recover to ~1.0 (got {rel[-1]:.3f})"
    print(f"  OK  instant attack: first loud sample gain {g[first_loud]:.3f} (flat over the frame), "
          f"quiet frame untouched, release smooth (max step {d.max():.5f})")


def test_capped_boost_reports_measured_landing():
    # Near-floor channel: the +20 dB static cap (and the +-3 dB trim clamp) means
    # the landing is NOT the -20 dBFS target. The reported landing_db must be the
    # MEASURED post-chain median, matching an independent measurement of the
    # output, not the target constant.
    a = speech_at(-44.9, secs=10.0, seed=11)
    out, g, landing = audioboost.boost_if_quiet(a)
    assert g > 0.0, "a -44.9 dBFS channel must boost"
    measured = audioboost.measure_active_rms(out)
    assert landing is not None and measured is not None
    assert abs(landing - measured) <= 0.05, \
        f"reported landing {landing:.2f} != measured {measured:.2f} dBFS"
    assert landing - audioboost.TARGET_DB < -1.0, \
        f"capped boost cannot land on target; reported {landing:.2f} dBFS looks like the -20 claim"
    print(f"  OK  capped boost: -44.9 dBFS in, +{g:.1f} dB applied, reported landing "
          f"{landing:.1f} dBFS == measured {measured:.1f} (not the -20 target)")


def test_softclip_bounded():
    # Even a near-full-scale spike inside a quiet channel must stay below 1.0 FS
    # after the boost (the tanh clipper is asymptotically bounded at 1.0). The spike
    # is a SHORT transient (8 samples): it barely lifts its 10 ms frame's RMS, so the
    # compressor's (now truly instant) attack only partially tames it and the sample
    # itself still overshoots into the clipper region. A sustained full-frame spike
    # is the compressor's job, not the clipper's, since the attack fix.
    a = speech_at(-34.0, secs=6.0, seed=5)
    a[SR:SR + 8] = 0.95  # the +14 dB static gain pushes the spike sample to ~4.7 FS
    out, g, _ = audioboost.boost_if_quiet(a)
    assert g > 0.0
    peak = float(np.abs(out).max())
    # tanh is asymptotically bounded at 1.0 and saturates TO 1.0 in float for an
    # extreme overdrive, so the bound is inclusive; nothing ever exceeds full scale.
    assert peak <= 1.0, peak
    assert peak > 0.85, "the spike should reach the clipper region, not vanish"
    print(f"  OK  soft clip: peak {peak:.3f} FS <= 1.0 after boosting a 0.95 FS spike")


def test_stereo_quiet_channel_only():
    # A stereo interview file with one quiet side: only that side is boosted; the
    # healthy side is returned byte-identical (the measured real-recording case:
    # MIC -33.6 boosted, SYS -28.3 untouched).
    quiet = speech_at(-33.6, seed=6)
    healthy = speech_at(-28.3, seed=7)
    out_q, g_q, _ = audioboost.boost_if_quiet(quiet)
    out_h, g_h, _ = audioboost.boost_if_quiet(healthy)
    assert g_q > 0.0 and abs(audioboost.measure_active_rms(out_q) + 20.0) <= 0.5
    assert g_h == 0.0 and out_h is healthy
    assert out_h.tobytes() == healthy.tobytes()
    print(f"  OK  stereo: quiet side boosted (+{g_q:.1f} dB), healthy side byte-identical")


def test_passthrough_byte_identity():
    healthy = speech_at(-20.0, seed=8)
    before = healthy.tobytes()
    out, g, _ = audioboost.boost_if_quiet(healthy)
    assert g == 0.0 and out is healthy and out.tobytes() == before
    print("  OK  pass-through: healthy audio returned as the same object, byte-identical")


def test_e2e_file_branch_feeds_boosted_chunks():
    # End-to-end through the real web FILE branch: POST /api/transcribe-file on a
    # quiet wav with the Engine stubbed. The chunks the engine receives must be
    # the BOOSTED audio (active median ~ -20 dBFS, not the file's -33), and the
    # sticky status notice must carry the boost. No model load, no real sessions
    # dir write, no session-count bump.
    from fastapi.testclient import TestClient
    from live_transcribe import transcribe as T
    from live_transcribe.web import app as webapp
    from live_transcribe.web.app import CSRF_TOKEN, app

    client = TestClient(app, base_url="http://localhost")
    client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})

    d = Path(tempfile.mkdtemp())
    quiet = speech_at(-33.0, secs=12.0, seed=9)
    src = d / "quiet-meeting.wav"
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((quiet * 32768.0).clip(-32768, 32767).astype("<i2").tobytes())

    class _FakeEngine:
        model_name = "fake-model"
        family = "fluister"

        def __init__(self, *a, **k):
            self.chunks = []
            self.sys_env = None

        def subscribe(self, sink): pass
        def start(self): pass

        def on_chunk(self, src_, window, t_start, block=False, timeout=None):
            self.chunks.append((src_, np.array(window, copy=True), t_start))
            return True

        def is_alive(self): return True
        def stop(self, drain=True): pass

    st = webapp.STATE
    holder = {}

    def _mk_engine(*a, **k):
        holder["engine"] = _FakeEngine(*a, **k)
        return holder["engine"]

    orig = (T.Engine, webapp._build_output_path, webapp._bump_session_count, st.notice)
    try:
        T.Engine = _mk_engine
        webapp._build_output_path = lambda topic: d / "out.md"
        webapp._bump_session_count = lambda: None
        st.notice = None
        r = client.post("/api/transcribe-file",
                        json={"paths": [str(src)], "topic": "boost-e2e", "tier": "medium"})
        assert r.status_code == 200, r.text
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            with st.lock:
                if not st.running:
                    break
            time.sleep(0.05)
        with st.lock:
            assert not st.running, "file transcription did not finish in time"
            notice = st.notice
        eng = holder["engine"]
        assert eng.chunks, "the chunker fed the engine nothing"
        assert all(s == "FILE" for s, _, _ in eng.chunks), [s for s, _, _ in eng.chunks]
        fed = np.concatenate([c for _, c, _ in eng.chunks])
        med = audioboost.measure_active_rms(fed)
        assert med is not None and abs(med - audioboost.TARGET_DB) <= 1.0, \
            f"engine was fed {med} dBFS audio, expected ~{audioboost.TARGET_DB} (boost missing?)"
        assert notice and notice.startswith("Quiet audio boosted for transcription (+"), notice
        # The source file on disk is untouched (boost feeds the engine only).
        with wave.open(str(src), "rb") as w:
            back = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        assert np.array_equal(back, (quiet * 32768.0).clip(-32768, 32767).astype("<i2"))
    finally:
        T.Engine, webapp._build_output_path, webapp._bump_session_count, st.notice = orig
    print(f"  OK  e2e FILE branch: engine fed boosted chunks ({med:.1f} dBFS), notice set, source file untouched")


if __name__ == "__main__":
    failures = 0
    for fn in (test_measure_active_rms,
               test_trigger_boundary,
               test_landing_minus20,
               test_compression_engages_on_loud_bursts,
               test_compressor_instant_attack_frame_aligned,
               test_capped_boost_reports_measured_landing,
               test_softclip_bounded,
               test_stereo_quiet_channel_only,
               test_passthrough_byte_identity,
               test_e2e_file_branch_feeds_boosted_chunks):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll audioboost tests passed.")
