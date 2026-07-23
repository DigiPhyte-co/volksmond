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
    out, g = audioboost.boost_if_quiet(np.zeros(SR, dtype=np.float32))
    assert g == 0.0 and float(np.abs(out).max()) == 0.0
    print("  OK  measure_active_rms: exact on synthetic speech, None on silence (never boosted)")


def test_trigger_boundary():
    # Locked rule: boost only strictly below -30 dBFS active median.
    quiet = speech_at(-31.0)
    out, g = audioboost.boost_if_quiet(quiet)
    assert g > 0.0, "a -31 dBFS channel must boost"
    assert out is not quiet
    healthy = speech_at(-29.0)
    out2, g2 = audioboost.boost_if_quiet(healthy)
    assert g2 == 0.0, "a -29 dBFS channel must pass through"
    assert out2 is healthy, "pass-through must return the input object"
    print(f"  OK  trigger boundary: -31 dBFS boosts (+{g:.1f} dB), -29 dBFS passes through")


def test_landing_minus20():
    # The boosted channel lands on the -20 dBFS active median within +-0.5 dB,
    # measured post-hoc on the OUTPUT (fresh mask), like a listener would.
    for db in (-31.0, -33.6, -38.0):
        out, g = audioboost.boost_if_quiet(speech_at(db, seed=int(-db)))
        got = audioboost.measure_active_rms(out)
        assert abs(got - audioboost.TARGET_DB) <= 0.5, (db, got, g)
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
    out, g = audioboost.boost_if_quiet(a)
    assert g > 0.0
    out_burst_db = 20.0 * np.log10(float(np.sqrt(np.mean(out[span] ** 2))) + 1e-12)
    static_only_db = in_burst_db + g
    reduction = static_only_db - out_burst_db
    assert reduction >= 3.0, (in_burst_db, out_burst_db, g, reduction)
    print(f"  OK  compressor: loud burst held {reduction:.1f} dB below the static-gain-only level")


def test_softclip_bounded():
    # Even a near-full-scale spike inside a quiet channel must stay below 1.0 FS
    # after the boost (the tanh clipper is asymptotically bounded at 1.0).
    a = speech_at(-34.0, secs=6.0, seed=5)
    a[SR:SR + 320] = 0.95  # a spike that the +14 dB static gain would push to ~4.7 FS
    out, g = audioboost.boost_if_quiet(a)
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
    out_q, g_q = audioboost.boost_if_quiet(quiet)
    out_h, g_h = audioboost.boost_if_quiet(healthy)
    assert g_q > 0.0 and abs(audioboost.measure_active_rms(out_q) + 20.0) <= 0.5
    assert g_h == 0.0 and out_h is healthy
    assert out_h.tobytes() == healthy.tobytes()
    print(f"  OK  stereo: quiet side boosted (+{g_q:.1f} dB), healthy side byte-identical")


def test_passthrough_byte_identity():
    healthy = speech_at(-20.0, seed=8)
    before = healthy.tobytes()
    out, g = audioboost.boost_if_quiet(healthy)
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
