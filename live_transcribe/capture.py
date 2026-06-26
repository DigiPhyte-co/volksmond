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
    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15, on_chunk=None, t0=None, aec=False, record_raw_mic=False):
        """on_chunk(source: str, audio_16k_mono: np.ndarray, t_start: float).

        t0: optional monotonic start time. When a live session switches its mic or
        loopback we tear this capture down and start a fresh one; passing the original
        t0 keeps transcript timestamps continuous across the switch instead of jumping
        back to 00:00.

        aec: when True AND both a mic and a system loopback open, route both through the
        WebRTC APM (live echo cancellation) before chunking. Needs the LiveKit binding;
        silently runs without it when absent.

        record_raw_mic: when True AND live AEC engages, ALSO chunk the RAW (pre-AEC) mic on a
        side "MIC_RAW" source, so a recording made with live AEC on stays raw (the engine still
        transcribes the cleaned mic). No effect when AEC does not engage."""
        self.mic_device_spec = mic_device
        self.loopback_device_spec = loopback_device
        self.chunk_seconds = chunk_seconds
        self.on_chunk = on_chunk
        self.aec = aec
        self.record_raw_mic = record_raw_mic
        self._live_aec = None     # set in start() when AEC engages; read by the audio callbacks

        self._stop_event = threading.Event()
        self._t0 = None
        self._t0_init = t0
        self._pa = None
        self._streams = []
        self._workers = []
        self._levels = {}         # source -> (peak, rms), latest input level for a live meter

        # Per-source state, keyed by "MIC" / "SYS"
        self._buffers = {}        # source -> list[np.ndarray]
        self._buffer_counts = {}  # source -> int (frames)
        self._buffer_locks = {}   # source -> threading.Lock
        self._rates = {}          # source -> int (native rate)
        self._channels = {}       # source -> int

    def start(self):
        self._t0 = self._t0_init if self._t0_init is not None else time.monotonic()
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

        # Live echo cancellation: only when requested AND both a mic and a system loopback
        # opened (we need the loopback as the far-end reference). The APM worker resamples both
        # to 16k and emits the cleaned mic + passthrough system into the chunk buffers, so the
        # chunkers then treat both sources as 16k (no second resample in _emit).
        if self.aec and "MIC" in self._buffers and "SYS" in self._buffers:
            la = None
            try:
                from . import aec as _aec
                if not _aec.available():
                    raise RuntimeError("LiveKit binding unavailable")
                from .aec_live import LiveAEC
                la = LiveAEC(
                    self._rates["MIC"], self._rates["SYS"],
                    on_near=lambda x: self._append_16k("MIC", x),
                    on_far=lambda x: self._append_16k("SYS", x),
                )
                la.start()   # builds the APM + worker thread; raises here if the lib is broken
                # Commit to the AEC path ONLY now the worker is live (so a failed start never
                # half-engages it). Set `_live_aec` BEFORE clearing/flipping so the callback's
                # under-lock re-check sees it and routes to the worker; the clear (under each
                # buffer lock) then drops the few native-rate samples captured before the switch,
                # so a chunk buffer never mixes sample rates.
                # Recording with live AEC: also chunk the RAW mic on a side "MIC_RAW" source so the
                # SAVED recording stays raw (the engine still gets the cleaned MIC). Set up BEFORE
                # MIC's rate is flipped to 16k below, so MIC_RAW keeps the mic's native rate and the
                # generic chunker/_emit resample it like any other source.
                if self.record_raw_mic:
                    self._buffers["MIC_RAW"] = []
                    self._buffer_counts["MIC_RAW"] = 0
                    self._buffer_locks["MIC_RAW"] = threading.Lock()
                    self._rates["MIC_RAW"] = self._rates["MIC"]        # native rate (pre-flip)
                    self._channels["MIC_RAW"] = self._channels["MIC"]
                self._live_aec = la
                self._rates["MIC"] = TARGET_RATE
                self._rates["SYS"] = TARGET_RATE
                for s in ("MIC", "SYS"):
                    with self._buffer_locks[s]:
                        self._buffers[s].clear()
                        self._buffer_counts[s] = 0
                print("[aec] live echo cancellation engaged (mic + system -> WebRTC APM)", flush=True)
            except Exception as e:
                # A missing or broken echo canceller must never stop the session: degrade to
                # normal capture (native rate, no AEC).
                if la is not None:
                    try:
                        la.stop()
                    except Exception:
                        pass
                self._live_aec = None
                print(f"[aec] live echo cancellation unavailable ({e}); running without it", flush=True)

        # Per-source chunker worker
        for source in list(self._buffers):
            t = threading.Thread(target=self._chunker, args=(source,), daemon=True, name=f"chunker-{source}")
            t.start()
            self._workers.append(t)

    def stop(self):
        # Stop the input streams first (no more native blocks arrive), then drain the AEC worker
        # into the chunk buffers, and only then signal the chunkers to flush - otherwise the
        # AEC could emit its tail after the chunkers had already flushed, losing the last words.
        for s in self._streams:
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass
        if self._live_aec is not None:
            try:
                self._live_aec.stop()
            except Exception:
                pass
        self._stop_event.set()
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None
        for w in self._workers:
            w.join(timeout=BLOCK_SECONDS + 1.5)

    def _append_16k(self, source, mono):
        """Append 16k mono float32 (from the live AEC worker) into the chunk buffer for `source`.
        Mirrors what the callback does for the non-AEC path, but the audio is already 16k mono."""
        # Once stop() has signalled shutdown, the chunkers are flushing/finishing; a late emit
        # from a slow-to-exit worker must not append behind them (it would be lost or grow a
        # buffer nobody reads). Drop it.
        if self._stop_event.is_set():
            return
        a = np.asarray(mono, dtype=np.float32).reshape(-1, 1)
        lock = self._buffer_locks.get(source)
        if lock is None:
            return
        with lock:
            self._buffers[source].append(a)
            self._buffer_counts[source] += a.shape[0]

    def has_raw_mic(self):
        """True once the raw-mic side channel is live (record_raw_mic AND live AEC engaged), so the
        caller records MIC_RAW as the raw mic and routes the cleaned MIC only to the engine."""
        return "MIC_RAW" in self._buffers

    def levels(self):
        """Latest per-source input level as {source: {"peak": x, "rms": y}}, 0..1, for a
        live meter. Refreshed every PortAudio callback (about every BLOCK_SECONDS). A source
        with no audio yet is simply absent.

        Read the two known keys directly rather than iterating the dict: the audio callback
        adds the first MIC/SYS key concurrently, and iterating a dict being grown raises
        'changed size during iteration'. A per-key .get() is a safe atomic read."""
        lv = self._levels
        out = {}
        for src in ("MIC", "SYS"):
            v = lv.get(src)
            if v is not None:
                out[src] = {"peak": v[0], "rms": v[1]}
        return out

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

            def callback(in_data, frame_count, time_info, status, _ch=ch, _src=source, _buf=buf, _lock=lock, _levels=self._levels, _self=self):
                try:
                    arr = np.frombuffer(in_data, dtype=np.float32)
                    if _ch > 1:
                        arr = arr.reshape(-1, _ch)
                    else:
                        arr = arr.reshape(-1, 1)
                    # Cheap per-block level for the live meter (peak + RMS, 0..1). A single
                    # dict assignment, so the reader (levels()) never sees a torn value.
                    if arr.size:
                        _levels[_src] = (float(np.max(np.abs(arr))), float(np.sqrt(np.mean(arr * arr))))
                    # Live echo cancellation (when engaged): hand the mono block to the APM worker
                    # instead of the native chunk buffer; the worker resamples + cancels and feeds
                    # the (now 16k) chunk buffer itself.
                    la = _self._live_aec
                    if la is not None:
                        mono = arr.mean(axis=1) if (arr.ndim > 1 and arr.shape[1] > 1) else arr[:, 0]
                        (la.push_near if _src == "MIC" else la.push_far)(mono)
                        # Tap the RAW mic (pre-AEC) into MIC_RAW for the recorder, so a recording
                        # made with live AEC on stays raw. The engine still gets the cleaned MIC.
                        if _src == "MIC":
                            rawbuf = _self._buffers.get("MIC_RAW")
                            if rawbuf is not None:
                                with _self._buffer_locks["MIC_RAW"]:
                                    rawbuf.append(arr)
                                    _self._buffer_counts["MIC_RAW"] += arr.shape[0]
                    else:
                        # Re-check under the buffer lock: AEC may engage between the read above and
                        # here, after which the buffer rate is 16k. Appending a native-rate block
                        # then would mix sample rates in one buffer, so route to the worker instead.
                        with _lock:
                            la = _self._live_aec
                            if la is not None:
                                mono = arr.mean(axis=1) if (arr.ndim > 1 and arr.shape[1] > 1) else arr[:, 0]
                                (la.push_near if _src == "MIC" else la.push_far)(mono)
                            else:
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
