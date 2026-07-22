"""Platform-neutral audio capture core.

Everything from the per-source buffer downwards lives here and is pure
numpy/scipy: silence-aware chunking, the 16 kHz resample + emit, the live
level meter, the SYS energy ring feed, and the live-AEC engagement dance.
Platform backends (capture_win, capture_mac) own only the device lifecycle:
they open their native audio sources, register each one via
`_register_source`, and feed raw float32 blocks into `_ingest_block` from
their audio threads.

Mic and loopback are deliberately kept as separate streams, never mixed in
the audio domain. Each chunk emerges tagged `MIC` or `SYS`, giving us a free
pseudo-diarisation hint without running a diarisation model.
"""
import threading
import time
from math import gcd

import numpy as np
from scipy.signal import resample_poly

TARGET_RATE = 16000          # faster-whisper input rate
BLOCK_SECONDS = 0.5          # audio callback granularity

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


class CaptureBase:
    """Shared capture state + logic. Backends implement `_open_sources()`
    (open devices, register each via `_register_source`, raise if nothing
    opened) and `_close_sources()` (stop delivering blocks); an optional
    `_release_backend()` hook runs late in `stop()` for host-API teardown
    (PortAudio terminate on Windows)."""

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
        self._workers = []
        self._levels = {}         # source -> (peak, rms), latest input level for a live meter
        self._sys_ring = None     # optional transcribe.SysEnergyRing, fed SYS block RMS for the echo veto

        # Per-source state, keyed by "MIC" / "SYS"
        self._buffers = {}        # source -> list[np.ndarray]
        self._buffer_counts = {}  # source -> int (frames)
        self._buffer_locks = {}   # source -> threading.Lock
        self._rates = {}          # source -> int (native rate)
        self._channels = {}       # source -> int

    # ---- backend seam -------------------------------------------------

    def _open_sources(self):
        """Open the platform's audio sources, registering each via
        `_register_source`. Must raise if no source opened at all."""
        raise NotImplementedError

    def _close_sources(self):
        """Stop the platform's audio sources so no more blocks arrive.
        Called first in stop(), before the AEC drain and chunker flush."""
        raise NotImplementedError

    def _release_backend(self):
        """Optional late teardown of the host audio API, called in stop()
        after the stop event is set and before the chunkers are joined."""

    def _register_source(self, source, rate, channels):
        """Create the per-source buffer state for a newly opened device."""
        self._buffers[source] = []
        self._buffer_counts[source] = 0
        self._buffer_locks[source] = threading.Lock()
        self._rates[source] = rate
        self._channels[source] = channels

    def _ingest_block(self, source, arr):
        """Take one float32 block, shaped (frames, channels), from a backend's
        audio thread: level calc, SYS-ring feed, AEC routing, and the
        under-lock re-check before appending to the chunk buffer."""
        # Cheap per-block level for the live meter (peak + RMS, 0..1). A single
        # dict assignment, so the reader (levels()) never sees a torn value.
        if arr.size:
            _rms = float(np.sqrt(np.mean(arr * arr)))
            self._levels[source] = (float(np.max(np.abs(arr))), _rms)
            # Feed the SYS energy ring (engine echo veto): one RMS sample per block,
            # timestamped on the session clock (same _t0 as chunk t_start). SYS only.
            _ring = self._sys_ring
            if _ring is not None and source == "SYS" and self._t0 is not None:
                _ring.add(time.monotonic() - self._t0, 20.0 * np.log10(_rms + 1e-9))
        # Live echo cancellation (when engaged): hand the mono block to the APM worker
        # instead of the native chunk buffer; the worker resamples + cancels and feeds
        # the (now 16k) chunk buffer itself.
        la = self._live_aec
        if la is not None:
            mono = arr.mean(axis=1) if (arr.ndim > 1 and arr.shape[1] > 1) else arr[:, 0]
            (la.push_near if source == "MIC" else la.push_far)(mono)
            # Tap the RAW mic (pre-AEC) into MIC_RAW for the recorder, so a recording
            # made with live AEC on stays raw. The engine still gets the cleaned MIC.
            if source == "MIC":
                rawbuf = self._buffers.get("MIC_RAW")
                if rawbuf is not None:
                    with self._buffer_locks["MIC_RAW"]:
                        rawbuf.append(arr)
                        self._buffer_counts["MIC_RAW"] += arr.shape[0]
        else:
            # Re-check under the buffer lock: AEC may engage between the read above and
            # here, after which the buffer rate is 16k. Appending a native-rate block
            # then would mix sample rates in one buffer, so route to the worker instead.
            with self._buffer_locks[source]:
                la = self._live_aec
                if la is not None:
                    mono = arr.mean(axis=1) if (arr.ndim > 1 and arr.shape[1] > 1) else arr[:, 0]
                    (la.push_near if source == "MIC" else la.push_far)(mono)
                else:
                    self._buffers[source].append(arr)
                    self._buffer_counts[source] = self._buffer_counts[source] + arr.shape[0]

    # ---- lifecycle -----------------------------------------------------

    def start(self):
        self._t0 = self._t0_init if self._t0_init is not None else time.monotonic()
        self._open_sources()

        # Live echo cancellation: engaged whenever both a mic and a system loopback opened (we
        # need the loopback as the far-end reference). When the user has AEC off, the worker still
        # runs but in BYPASS (raw mic emitted, APM kept fed), so echo cancellation can be toggled
        # on or off mid-meeting by flipping the bypass; no capture rebuild, no rate change. The APM
        # worker resamples both to 16k and emits the (cleaned or raw) mic + passthrough system into
        # the chunk buffers, so the chunkers then treat both sources as 16k (no second resample in
        # _emit).
        if "MIC" in self._buffers and "SYS" in self._buffers:
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
                    bypass=not self.aec,
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
                print(f"[aec] live echo canceller engaged ({'active' if self.aec else 'bypassed, ready to toggle on'}) (mic + system -> WebRTC APM)", flush=True)
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
        self._close_sources()
        if self._live_aec is not None:
            try:
                self._live_aec.stop()
            except Exception:
                pass
        self._stop_event.set()
        self._release_backend()
        for w in self._workers:
            w.join(timeout=BLOCK_SECONDS + 1.5)

    # ---- shared plumbing ------------------------------------------------

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

    def set_aec(self, enabled):
        """Toggle live echo cancellation mid-session. Instant and safe in both directions when
        the APM worker is running (it flips the worker's bypass; the frame flow is identical
        either way, so nothing replays or desyncs). Returns False only when the worker never
        engaged (mic-only session, or the LiveKit binding is missing) and the caller asked for
        ON, which cannot be honoured this session. `self.aec` tracks the ACTIVE state so a
        mid-meeting device switch rebuilds the capture with the current choice, not the one
        the session started with."""
        la = self._live_aec
        if la is None:
            if enabled:
                return False
            self.aec = False
            return True
        la.bypass = not enabled
        self.aec = bool(enabled)
        return True

    def aec_state(self):
        """(available, active): available = the APM worker is running this session (so the
        toggle can work), active = it is actually cancelling (not bypassed)."""
        la = self._live_aec
        return (la is not None, la is not None and not la.bypass)

    def has_raw_mic(self):
        """True once the raw-mic side channel is live (record_raw_mic AND live AEC engaged), so the
        caller records MIC_RAW as the raw mic and routes the cleaned MIC only to the engine."""
        return "MIC_RAW" in self._buffers

    def levels(self):
        """Latest per-source input level as {source: {"peak": x, "rms": y}}, 0..1, for a
        live meter. Refreshed every audio callback (about every BLOCK_SECONDS). A source
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

    def attach_sys_ring(self, ring):
        """Attach a transcribe.SysEnergyRing. The audio callback then feeds it one SYS RMS sample
        per ~0.5 s block in real time, so the engine's echo veto has a current far-end reference
        even during a monologue (when SYS chunks emit late)."""
        self._sys_ring = ring

    def _chunker(self, source):
        rate = self._rates[source]
        min_chunk = int(rate * self.chunk_seconds)
        max_chunk = int(rate * self.chunk_seconds * MAX_CHUNK_MULTIPLIER)
        min_emit = int(rate * MIN_EMIT_SECONDS)
        lock = self._buffer_locks[source]

        while not self._stop_event.is_set():
            time.sleep(0.1)
            audio_to_emit = None
            emit_t_start = None

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
                        # The emitted slice is the OLDEST audio in the buffer: it starts one FULL
                        # buffer span before the newest sample (~now), not one emitted-length before.
                        # count/rate is that span (rate matches the buffer contents in both the native
                        # and live-AEC 16k paths). Deriving t_start from the emitted length alone left
                        # every chunk late by the carried-over tail (up to ~2s on a silence cut), which
                        # misaligned the SYS-ring echo veto.
                        emit_t_start = max(0.0, time.monotonic() - self._t0 - count / rate)
                        tail = full[cut_at:]
                        self._buffers[source].clear()  # in-place, callback closes over this list
                        if tail.shape[0] > 0:
                            self._buffers[source].append(tail)
                            self._buffer_counts[source] = tail.shape[0]
                        else:
                            self._buffer_counts[source] = 0
                    # else: not enough audio yet, or no silence found before max, keep waiting

            if audio_to_emit is not None:
                self._emit(source, audio_to_emit, rate, emit_t_start)

        # Flush trailing audio on shutdown (only if at least 1s)
        audio = None
        flush_t_start = None
        with lock:
            if self._buffers[source]:
                audio = np.concatenate(self._buffers[source], axis=0)
                # Whole remaining buffer is emitted, so its first sample is one full span back.
                flush_t_start = max(0.0, time.monotonic() - self._t0 - audio.shape[0] / rate)
                self._buffers[source].clear()
                self._buffer_counts[source] = 0
        if audio is not None and audio.shape[0] >= rate:
            self._emit(source, audio, rate, flush_t_start)

    def _emit(self, source, audio, src_rate, t_start):
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
        # t_start is computed by the caller from the FULL buffer span (see _chunker); it is the
        # session-clock time of the first emitted sample. Do NOT derive it from the emitted length
        # here: that ignores the carried-over tail and left every chunk late by up to ~2s, which
        # skewed the SYS-ring echo veto.
        if self.on_chunk:
            self.on_chunk(source, mono, t_start)
