"""Acoustic echo cancellation for a recorded MIC + SYS pair.

When you record with speakers (not headphones), your microphone re-hears the other side coming
out of the speakers, and that echo gets transcribed as if you said it. We already capture the
system audio (the loopback) separately, so we have the far-end reference an echo canceller needs:
near-end = MIC, far-end = SYS, and the cleaned MIC is MIC minus the speaker echo.

Engine: LiveKit's WebRTC APM (the same AEC3 Chrome/Meet use), Apache-2.0, via `livekit.rtc.apm`.
When that binding is missing the module degrades to a no-op (returns the mic unchanged), so the
app never hard-depends on it.

Offline use (re-transcribe a saved recording): both channels are fully available, so we estimate
the MIC-vs-SYS delay by cross-correlation and align before cancelling. The MIC usually LEADS the
SYS by a few tens of ms (WASAPI adds a capture-path buffer delay while the speaker->air->mic path
is shorter), which a causal canceller must be told about.
"""
import numpy as np

RATE = 16000
FRAME = 160          # 10 ms at 16 kHz, the APM frame size
MAX_DELAY_MS = 250   # cap the delay search/shift; real speaker-mic offsets are well under this


def available():
    """True iff the LiveKit APM binding can be imported on this machine."""
    try:
        import livekit.rtc.apm  # noqa: F401
        return True
    except Exception:
        return False


def estimate_mic_delay(mic, sys_, rate=RATE, max_ms=MAX_DELAY_MS):
    """Samples to delay MIC by so the SYS reference leads it causally (>= 0).

    Cross-correlates MIC against SYS on the loudest ~10 s of SYS (so the estimate keys off real
    echo, not silence). Returns 0 when there is too little to measure or the mic already lags."""
    n = min(len(mic), len(sys_))
    if n < rate:
        return 0
    m = np.asarray(mic[:n], dtype=np.float32)
    s = np.asarray(sys_[:n], dtype=np.float32)
    # Centre a window on the loudest SYS second (most echo to lock onto).
    win = min(n, 10 * rate)
    secs = n // rate
    if secs > 1:
        energy = np.array([np.dot(s[i * rate:(i + 1) * rate], s[i * rate:(i + 1) * rate]) for i in range(secs)])
        centre = int(np.argmax(energy)) * rate
    else:
        centre = 0
    a = max(0, min(centre - win // 2, n - win))
    mw = m[a:a + win] - m[a:a + win].mean()
    sw = s[a:a + win] - s[a:a + win].mean()
    max_lag = int(max_ms * rate / 1000)
    fftlen = 1
    while fftlen < win + 2 * max_lag:
        fftlen *= 2
    corr = np.fft.irfft(np.fft.rfft(mw, fftlen) * np.conj(np.fft.rfft(sw, fftlen)), fftlen)
    # corr[k] = sum mic[i] * sys[i-k]; reorganise to lags -max_lag..+max_lag.
    vals = np.concatenate([corr[-max_lag:], corr[:max_lag + 1]])
    lags = np.arange(-max_lag, max_lag + 1)
    peak_lag = int(lags[int(np.argmax(np.abs(vals)))])
    # peak_lag < 0 means MIC leads SYS by |peak_lag| samples -> delay MIC by that to make it causal.
    return int(max(0, -peak_lag))


def cancel_echo(mic, sys_, rate=RATE, mic_delay=None, noise_suppression=False):
    """Remove the SYS echo from MIC. mic/sys_: float32 mono in [-1, 1] at `rate`.

    Returns a float32 cleaned MIC of the same length, on MIC's original timeline. No-op (returns
    MIC unchanged) when the APM binding is unavailable or there is no usable reference."""
    if not available():
        return np.asarray(mic, dtype=np.float32)
    if rate != RATE:
        raise ValueError(f"AEC expects {RATE} Hz, got {rate}")
    from livekit.rtc import AudioFrame
    from livekit.rtc.apm import AudioProcessingModule

    mic = np.asarray(mic, dtype=np.float32)
    sys_ = np.asarray(sys_, dtype=np.float32)
    if len(mic) < FRAME or len(sys_) < FRAME:
        return mic

    if mic_delay is None:
        mic_delay = estimate_mic_delay(mic, sys_, rate)

    # float [-1,1] -> int16 for the APM
    mic_i16 = np.clip(mic * 32768.0, -32768, 32767).astype(np.int16)
    sys_i16 = np.clip(sys_ * 32768.0, -32768, 32767).astype(np.int16)
    # Delay the mic so the reference leads it causally; trim the delay back off at the end.
    if mic_delay > 0:
        mic_aligned = np.concatenate([np.zeros(mic_delay, dtype=np.int16), mic_i16])[:len(mic_i16)]
    else:
        mic_aligned = mic_i16

    apm = AudioProcessingModule(echo_cancellation=True, noise_suppression=noise_suppression,
                                high_pass_filter=False, auto_gain_control=False)
    n = min(len(mic_aligned), len(sys_i16))
    n_frames = n // FRAME
    cleaned = np.empty(n_frames * FRAME, dtype=np.int16)
    for f in range(n_frames):
        s, e = f * FRAME, f * FRAME + FRAME
        ref = AudioFrame(sys_i16[s:e].tobytes(), RATE, 1, FRAME)
        near = AudioFrame(bytearray(mic_aligned[s:e].tobytes()), RATE, 1, FRAME)
        apm.process_reverse_stream(ref)   # far-end first, then near-end (the APM contract)
        apm.process_stream(near)
        cleaned[s:e] = np.frombuffer(near.data, dtype=np.int16)

    # Undo the alignment delay so the cleaned mic lines up with the original timeline, and
    # restore any samples past the last whole frame (left untouched) so length is preserved.
    out = np.zeros(len(mic), dtype=np.float32)
    body = cleaned.astype(np.float32) / 32768.0
    if mic_delay > 0:
        usable = body[mic_delay:]
        out[:len(usable)] = usable
    else:
        out[:len(body)] = body
    # Tail beyond the processed region: fall back to the original mic so nothing is dropped.
    processed = (len(body) - mic_delay) if mic_delay > 0 else len(body)
    if processed < len(mic):
        out[processed:] = mic[processed:]
    return out
