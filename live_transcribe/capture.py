"""WASAPI loopback + mic capture via pyaudiowpatch.

Each device is opened at its OWN native sample rate (the loopback is usually
48 kHz; mics vary, Samson C01U is 44.1 kHz). PortAudio invokes our callback
on a dedicated audio thread; we copy into a per-source buffer. Per-source
chunker threads slice into `chunk_seconds` windows, resample to 16 kHz mono,
and emit to the engine.

Mic and loopback are deliberately kept as separate streams, never mixed in
the audio domain. Each chunk emerges tagged `MIC` or `SYS`, giving us a free
pseudo-diarisation hint without running a diarisation model.
"""
import threading
import time
from math import gcd

import numpy as np
import pyaudiowpatch as pa
from scipy.signal import resample_poly

from .devices import resolve_loopback, resolve_mic

TARGET_RATE = 16000          # faster-whisper input rate
BLOCK_SECONDS = 0.5          # PortAudio callback granularity

# Silence-aware chunking: when the buffer reaches `chunk_seconds`, we try to
# cut at a natural silence boundary in the last ~2s so sentences end cleanly.
# If we can't find silence by `chunk_seconds * MAX_CHUNK_MULTIPLIER`, we
# force-cut. The trailing audio after the silence is carried into the next
# chunk so no audio is lost.
SILENCE_LOOKBACK_SECONDS = 2.0
SILENCE_WINDOW_MS = 300
SILENCE_RMS_THRESHOLD = 0.005
MAX_CHUNK_MULTIPLIER = 1.5
MIN_EMIT_SECONDS = 1.0       # never emit a chunk shorter than this


def _find_last_silence(audio: np.ndarray, sample_rate: int,
                       lookback_seconds: float = SILENCE_LOOKBACK_SECONDS,
                       window_ms: int = SILENCE_WINDOW_MS,
                       rms_threshold: float = SILENCE_RMS_THRESHOLD) -> int | None:
    """Search the tail of `audio` for the latest silent window.

    Returns the sample index to cut at (middle of the silent window), so that
    the emitted chunk ends in silence and the next chunk starts with silence.
    Returns None if no silence found within lookback.
    """
    # Collapse to mono for RMS calc (cheap, just for analysis)
    if audio.ndim > 1 and audio.shape[1] > 1:
        mono = audio.mean(axis=1)
    elif audio.ndim > 1:
        mono = audio[:, 0]
    else:
        mono = audio

    n = mono.shape[0]
    if n == 0:
        return None

    lookback_samples = int(lookback_seconds * sample_rate)
    window_samples = max(1, int(window_ms * sample_rate / 1000))
    if window_samples >= n:
        return None

    # Walk backwards from the end in half-window steps; first silent window wins
    step = max(1, window_samples // 2)
    search_floor = max(window_samples, n - lookback_samples)

    for end in range(n, search_floor, -step):
        win = mono[end - window_samples:end].astype(np.float32, copy=False)
        rms = float(np.sqrt(np.mean(win * win)))
        if rms < rms_threshold:
            # Cut in the middle of the silent window, gives each side a buffer
            return end - window_samples // 2

    return None


class AudioCapture:
    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15, on_chunk=None):
        """on_chunk(source: str, audio_16k_mono: np.ndarray, t_start: float)"""
        self.mic_device_spec = mic_device
        self.loopback_device_spec = loopback_device
        self.chunk_seconds = chunk_seconds
        self.on_chunk = on_chunk

        self._stop_event = threading.Event()
        self._t0 = None
        self._pa = None
        self._streams = []
        self._workers = []

        # Per-source state, keyed by "MIC" / "SYS"
        self._buffers = {}        # source -> list[np.ndarray]
        self._buffer_counts = {}  # source -> int (frames)
        self._buffer_locks = {}   # source -> threading.Lock
        self._rates = {}          # source -> int (native rate)
        self._channels = {}       # source -> int

    def start(self):
        self._t0 = time.monotonic()
        self._pa = pa.PyAudio()

        loopback_info = None
        try:
            loopback_info = resolve_loopback(self._pa, self.loopback_device_spec)
        except Exception as e:
            print(f"[SYS] cannot resolve loopback: {e}", flush=True)

        mic_info = None
        try:
            mic_info = resolve_mic(self._pa, self.mic_device_spec)
        except Exception as e:
            print(f"[MIC] cannot resolve mic: {e}", flush=True)

        # Wrap each open so the failing source identifies itself in the error
        # the FastAPI layer surfaces. The raw PyAudio message (e.g. `[Errno -9996]
        # Invalid device`) by itself does not tell the user whether their mic or
        # their loopback choice failed, so they cannot guess which dropdown to
        # change. WASAPI loopback in particular can enumerate a device whose
        # actual endpoint is inactive (laptop "Headphones" reported as default
        # when no headphones are plugged in is the common case): swapping to
        # the Speakers loopback usually fixes it.
        if loopback_info is not None:
            try:
                self._open_stream("SYS", loopback_info)
            except Exception as e:
                raise RuntimeError(
                    f"could not open system audio device #{loopback_info['index']} "
                    f"'{loopback_info['name']}': {e}. Try a different option in "
                    "the System audio dropdown (e.g. Speakers if Headphones fails)."
                ) from e
        if mic_info is not None:
            try:
                self._open_stream("MIC", mic_info)
            except Exception as e:
                raise RuntimeError(
                    f"could not open microphone #{mic_info['index']} "
                    f"'{mic_info['name']}': {e}. Try a different option in the "
                    "Your microphone dropdown."
                ) from e

        if not self._streams:
            raise RuntimeError(
                "no audio sources opened (both loopback and mic resolution failed). "
                "Run --list-devices from the CLI to enumerate what is available."
            )

        # Per-source chunker worker
        for source in list(self._buffers):
            t = threading.Thread(target=self._chunker, args=(source,), daemon=True, name=f"chunker-{source}")
            t.start()
            self._workers.append(t)

    def stop(self):
        self._stop_event.set()
        for s in self._streams:
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        for w in self._workers:
            w.join(timeout=BLOCK_SECONDS + 1.5)

    def _open_stream(self, source, info):
        rate = int(info["defaultSampleRate"])
        max_ch = max(1, int(info["maxInputChannels"]))
        block = int(rate * BLOCK_SECONDS)

        # Channel-count fallback list, highest-first. Some Realtek WASAPI
        # loopback drivers report maxInputChannels=8 (claiming surround
        # capability) but only accept opens at their actual mix format
        # (typically stereo); a `paInvalidDevice` is what the PortAudio
        # layer reports back from the driver in that case. Try the device's
        # reported max first, then fall through to common counts. The first
        # combination that opens wins; we keep its `channels` so the
        # callback reshapes correctly. For mono mics maxInputChannels=1 is
        # the only candidate and the loop short-circuits in one iteration.
        candidates = []
        for c in (max_ch, 2, 1):
            if 1 <= c <= max_ch and c not in candidates:
                candidates.append(c)

        last_err = None
        for channels in candidates:
            self._buffers[source] = []
            self._buffer_counts[source] = 0
            self._buffer_locks[source] = threading.Lock()
            self._rates[source] = rate
            self._channels[source] = channels

            buf = self._buffers[source]
            counts = self._buffer_counts
            lock = self._buffer_locks[source]
            ch = channels

            def callback(in_data, frame_count, time_info, status, _ch=ch, _src=source, _buf=buf, _lock=lock):
                try:
                    arr = np.frombuffer(in_data, dtype=np.float32)
                    if _ch > 1:
                        arr = arr.reshape(-1, _ch)
                    else:
                        arr = arr.reshape(-1, 1)
                    with _lock:
                        _buf.append(arr)
                        counts[_src] = counts[_src] + arr.shape[0]
                except Exception as e:
                    print(f"[{_src}] callback error: {e}", flush=True)
                return (None, pa.paContinue)

            try:
                stream = self._pa.open(
                    format=pa.paFloat32,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=info["index"],
                    frames_per_buffer=block,
                    stream_callback=callback,
                )
            except Exception as e:
                last_err = e
                # Clear partial state so the next candidate starts clean and
                # so a final failure doesn't leave half-initialised buffers.
                for d in (self._buffers, self._buffer_counts, self._buffer_locks,
                          self._rates, self._channels):
                    d.pop(source, None)
                continue

            print(f"[{source}] opened '{info['name']}' @ {rate} Hz x{channels}ch (device #{info['index']})", flush=True)
            stream.start_stream()
            self._streams.append(stream)
            return

        # Every candidate failed; re-raise the most recent error so the wrapper
        # in start() turns it into the user-facing "could not open ..." message.
        raise last_err if last_err is not None else RuntimeError(
            f"could not open {source} at any channel count (tried {candidates})"
        )

    def _chunker(self, source):
        rate = self._rates[source]
        min_chunk = int(rate * self.chunk_seconds)
        max_chunk = int(rate * self.chunk_seconds * MAX_CHUNK_MULTIPLIER)
        min_emit = int(rate * MIN_EMIT_SECONDS)
        lock = self._buffer_locks[source]

        while not self._stop_event.is_set():
            time.sleep(0.1)
            audio_to_emit = None

            with lock:
                count = self._buffer_counts[source]
                if count >= min_chunk:
                    full = np.concatenate(self._buffers[source], axis=0)

                    # Try to cut at a natural silence boundary in the last ~2s.
                    # Only fall through to force-cut once we've waited up to max_chunk.
                    cut_at = _find_last_silence(full, rate)
                    if cut_at is None and count >= max_chunk:
                        cut_at = max_chunk  # force cut

                    if cut_at is not None and cut_at >= min_emit:
                        audio_to_emit = full[:cut_at]
                        tail = full[cut_at:]
                        self._buffers[source].clear()  # in-place, callback closes over this list
                        if tail.shape[0] > 0:
                            self._buffers[source].append(tail)
                            self._buffer_counts[source] = tail.shape[0]
                        else:
                            self._buffer_counts[source] = 0
                    # else: not enough audio yet, or no silence found before max, keep waiting

            if audio_to_emit is not None:
                self._emit(source, audio_to_emit, rate)

        # Flush trailing audio on shutdown (only if at least 1s)
        audio = None
        with lock:
            if self._buffers[source]:
                audio = np.concatenate(self._buffers[source], axis=0)
                self._buffers[source].clear()
                self._buffer_counts[source] = 0
        if audio is not None and audio.shape[0] >= rate:
            self._emit(source, audio, rate)

    def _emit(self, source, audio, src_rate):
        # Channels -> mono
        if audio.ndim > 1:
            mono = audio.mean(axis=1) if audio.shape[1] > 1 else audio[:, 0]
        else:
            mono = audio
        # Resample to 16k via polyphase (alias-safe)
        if src_rate != TARGET_RATE:
            g = gcd(src_rate, TARGET_RATE)
            mono = resample_poly(mono, TARGET_RATE // g, src_rate // g)
        mono = np.asarray(mono, dtype=np.float32)
        duration = len(mono) / TARGET_RATE
        t_start = max(0.0, time.monotonic() - self._t0 - duration)
        if self.on_chunk:
            self.on_chunk(source, mono, t_start)
