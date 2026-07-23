"""Automatic quiet-channel boost for the FILE transcription path.

A channel captured well below normal level makes Whisper hallucinate: on the
2026-07-23 internal catch-up the MIC channel sat at -33.6 dBFS active-speech
median RMS and produced a 15-line repeated-word loop; normalising it to -20 dBFS
removed the loop entirely, while the healthy -28.3 dBFS SYS channel showed no
measurable change from the same treatment. So: boost only channels that are
genuinely quiet (active median below -30 dBFS) and pass everything else through
untouched, byte-identical.

The enhancement chain (validated A/B on that real recording):
  1. Static gain to bring the median ACTIVE-speech RMS (20 ms frames above
     -45 dBFS, the frame mask decided once on the PRE-gain signal so gain does
     not shift the frame population) toward -20 dBFS. Capped at +20 dB, so a
     channel below about -40 dBFS lands short of the target.
  2. Gentle downward compression: 10 ms RMS envelope, instant attack, 150 ms
     release, threshold -12 dBFS RMS, ratio 3:1, 6 dB soft knee.
  3. Small post-trim (clipped to +-3 dB) to pull the active median back toward
     -20 dBFS after the compressor (again: when a clamp engages, the landing is
     NOT exactly -20; the measured landing is reported, never assumed).
  4. tanh soft clipper above 0.85 FS so nothing hard-clips.
No noise reduction, no gating, no EQ. Deterministic, numpy only.

FILE path only, by decision: the live path's echo gates are calibrated in dBFS
on the raw capture, so real-time gain stays out of scope. The boosted audio
feeds the ENGINE only; the user's source file is never rewritten.
"""
import numpy as np

RATE = 16000
TARGET_DB = -20.0        # active-speech median to land on
TRIGGER_DB = -30.0       # boost only strictly below this (-33.6 looped, -28.3 was fine)
ACTIVE_FLOOR_DB = -45.0  # a 20 ms frame above this counts as active speech
MAX_STATIC_GAIN_DB = 20.0
FRAME_S = 0.020          # measurement frame (20 ms)


def _frame_rms(x, frame_s=FRAME_S, rate=RATE):
    """Per-frame RMS of a mono float track (tail shorter than a frame is dropped)."""
    w = max(1, int(frame_s * rate))
    m = len(x) // w
    if m == 0:
        return np.zeros(0, dtype=np.float64)
    return np.sqrt(np.mean(x[: m * w].reshape(m, w) ** 2, axis=1))


def _active_median_db(x, mask=None, rate=RATE):
    """(median_db, mask) of the active-speech frames, or (None, mask) when nothing
    reaches the activity floor. If mask is given, use exactly those frames (activity
    decided once, on the ORIGINAL signal, so gain does not shift the population)."""
    rms = _frame_rms(x, rate=rate)
    if mask is None:
        mask = rms > 10.0 ** (ACTIVE_FLOOR_DB / 20.0)
    act = rms[mask[: len(rms)]]
    if act.size == 0:
        return None, mask
    return float(20.0 * np.log10(np.median(act) + 1e-12)), mask


def measure_active_rms(audio, rate=RATE):
    """Median active-speech RMS of a mono float track, in dBFS.

    Active = 20 ms frames above -45 dBFS. Returns None for silence / audio too
    short to hold a single active frame (such a track must never be boosted)."""
    x = np.asarray(audio, dtype=np.float32)
    med_db, _ = _active_median_db(x, rate=rate)
    return med_db


def _compress(x, rate=RATE, thresh_db=-12.0, ratio=3.0, knee_db=6.0,
              frame_s=0.010, release_s=0.150):
    """Gentle downward compressor: 10 ms RMS envelope, instant attack, 150 ms
    release, soft knee. Attack gain applies from the start of its frame (truly
    instant); only release gains are ramped within their frame."""
    fw = max(1, int(frame_s * rate))
    m = len(x) // fw
    if m == 0:
        return x
    ev = np.sqrt(np.mean(x[: m * fw].reshape(m, fw) ** 2, axis=1))
    env_db = 20.0 * np.log10(ev + 1e-9)
    # Attack: instant (one frame). Release: exponential decay toward the current level.
    rel = np.exp(-frame_s / release_s)
    smoothed = np.empty(m)
    cur = env_db[0]
    for i in range(m):
        e = env_db[i]
        cur = e if e > cur else rel * cur + (1.0 - rel) * e
        smoothed[i] = cur
    # Soft-knee gain computer.
    over = smoothed - thresh_db
    gr_db = np.zeros(m)
    inknee = np.abs(over) <= knee_db / 2.0
    above = over > knee_db / 2.0
    gr_db[inknee] = (1.0 / ratio - 1.0) * (over[inknee] + knee_db / 2.0) ** 2 / (2.0 * knee_db)
    gr_db[above] = (1.0 / ratio - 1.0) * over[above]
    gain = 10.0 ** (gr_db / 20.0)
    # Per-sample gain. A gain REDUCTION (the signal's rising edge: the instant attack)
    # applies from the FIRST sample of its frame, flat across the frame, so the loud
    # onset is fully reduced immediately and the preceding quiet frame is never
    # pre-attenuated. Only a RISING gain (release) is ramped linearly across its frame
    # (the frame-level release is already the exponential decay above; the in-frame
    # ramp just avoids zipper steps). Centre-interpolating ALL transitions here used
    # to smear the attack: the first ~half frame of a burst got partial reduction and
    # the prior quiet frame's tail was pulled down with it.
    g = np.repeat(gain, fw)
    prev = np.concatenate(([gain[0]], gain[:-1]))
    rising = np.nonzero(gain > prev)[0]
    if rising.size:
        ramp = np.arange(fw, dtype=np.float64) / fw
        seg = prev[rising, None] + (gain[rising] - prev[rising])[:, None] * ramp
        g[(rising[:, None] * fw + np.arange(fw)).ravel()] = seg.ravel()
    if len(x) > g.size:   # tail shorter than a frame: hold the last frame's gain
        g = np.concatenate([g, np.full(len(x) - g.size, gain[-1])])
    return x * g


def _softclip(x, knee=0.85):
    """tanh soft clipper above `knee` FS; below it the signal is untouched."""
    y = x.copy()
    a = np.abs(x)
    hot = a > knee
    y[hot] = np.sign(x[hot]) * (knee + (1.0 - knee) * np.tanh((a[hot] - knee) / (1.0 - knee)))
    return y


def boost_if_quiet(audio, rate=RATE):
    """Boost a quiet mono float32 track for transcription; pass healthy audio through.

    Trigger: active-speech median RMS strictly below -30 dBFS. Below it, apply the
    validated chain (static gain toward -20 dBFS -> compressor -> trim -> soft clip);
    at or above it, return the INPUT OBJECT untouched, so healthy audio is
    byte-identical and costs nothing.

    Returns (audio_out, gain_db, landing_db):
      gain_db:    the net static level change applied to the active-speech median
                  (0.0 when passed through, always > 0 otherwise).
      landing_db: the active-speech median MEASURED on the output (fresh mask, like
                  a listener would), NOT the -20 dBFS target: when the +20 dB static
                  cap or the +-3 dB trim clamp engages (input below about -40 dBFS),
                  the landing falls short of the target, and callers must report
                  this measured value. Equals the input's median (or None for
                  silence) on pass-through.
    """
    x = np.asarray(audio, dtype=np.float32)
    med_db, mask = _active_median_db(x, rate=rate)
    if med_db is None or med_db >= TRIGGER_DB:
        return audio, 0.0, med_db
    g1_db = min(TARGET_DB - med_db, MAX_STATIC_GAIN_DB)
    y = x * 10.0 ** (g1_db / 20.0)
    y = _compress(y, rate=rate)
    med2_db, _ = _active_median_db(y, mask=mask, rate=rate)
    trim_db = 0.0
    if med2_db is not None:
        trim_db = float(np.clip(TARGET_DB - med2_db, -3.0, 3.0))
        y = y * 10.0 ** (trim_db / 20.0)
    y = np.ascontiguousarray(_softclip(y), dtype=np.float32)
    landing_db, _ = _active_median_db(y, rate=rate)
    return y, round(g1_db + trim_db, 1), landing_db
