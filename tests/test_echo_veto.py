"""Unit tests for the cross-channel echo veto (transcribe.SysEnergyRing + sys_echo_veto).

The energy-domain counterpart to test_dedup.py (which covers the text-overlap dedup). Pins the
calibrated thresholds and the fail-safe behaviour so a future tweak cannot silently start eating
real speech (or stop catching bleed). Synthetic audio + a hand-built ring, no model load.

Run:  python tests/test_echo_veto.py   (exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from live_transcribe import transcribe as T


def tone(db, secs, sr=16000):
    """White noise scaled to a target dBFS RMS (deterministic seed)."""
    n = int(secs * sr)
    a = np.random.RandomState(0).randn(n).astype(np.float32)
    return a * ((10.0 ** (db / 20.0)) / (float(np.sqrt(np.mean(a * a))) + 1e-9))


def ring_with(db, t0, t1):
    r = T.SysEnergyRing(retain_s=1e6)
    t = t0
    while t <= t1:
        r.add(t, db)
        t += 0.5
    return r


def test_ghost_dropped():
    # quiet mic (bleed) under a loud, active far end across the whole segment -> drop
    drop, why = T.sys_echo_veto(tone(-33, 3.0), ring_with(-19, 9.7, 13.3), 10.0, 13.0, word_count=8)
    assert drop, f"ghost should be dropped: {why}"


def test_real_speech_kept():
    # loud mic (near speaker), far end also active (double-talk) -> keep (margin is negative)
    drop, why = T.sys_echo_veto(tone(-8, 3.0), ring_with(-19, 9.7, 13.3), 10.0, 13.0, word_count=8)
    assert not drop, f"real speech must be kept: {why}"


def test_quiet_but_below_ceiling_kept():
    # mic quiet-ish (-24) but ABOVE the -28 ceiling: not confident bleed -> keep
    drop, why = T.sys_echo_veto(tone(-24, 3.0), ring_with(-19, 9.7, 13.3), 10.0, 13.0, word_count=8)
    assert not drop, f"segment above the mic ceiling must be kept: {why}"


def test_short_reply_kept():
    # a 2-word backchannel is never dropped even if it looks like bleed
    drop, why = T.sys_echo_veto(tone(-33, 3.0), ring_with(-19, 9.7, 13.3), 10.0, 13.0, word_count=2)
    assert not drop and why == "short", f"short reply must be kept: {why}"


def test_no_reference_fails_safe():
    # no SYS energy in the window -> keep (never drop without evidence)
    drop, why = T.sys_echo_veto(tone(-33, 3.0), T.SysEnergyRing(), 10.0, 13.0, word_count=8)
    assert not drop and why == "nosys", f"missing reference must fail safe: {why}"


def test_far_end_silent_kept():
    # far end below the active floor across the segment -> coverage 0 -> keep
    drop, why = T.sys_echo_veto(tone(-33, 3.0), ring_with(-60, 9.7, 13.3), 10.0, 13.0, word_count=8)
    assert not drop, f"no active far end must be kept: {why}"


def test_ring_retention_evicts():
    r = T.SysEnergyRing(retain_s=600.0)
    for i in range(0, 1400):      # 0 .. 700s at 0.5s steps
        r.add(i * 0.5, -20.0)
    assert r.frames_in(0, 50) == [], "frames older than retention must be evicted"
    assert r.frames_in(690, 700), "recent frames must be retained"


def main():
    tests = [test_ghost_dropped, test_real_speech_kept, test_quiet_but_below_ceiling_kept,
             test_short_reply_kept, test_no_reference_fails_safe, test_far_end_silent_kept,
             test_ring_retention_evicts]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nall {len(tests)} echo-veto tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
