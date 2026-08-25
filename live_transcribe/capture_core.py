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
RING_FRAME_SECONDS = 0.1     # energy-ring resolution (see _ring_frames)

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


def iter_silence_chunks(audio: np.ndarray, sample_rate: int = TARGET_RATE,
                        chunk_seconds: float = 15.0):
    """Silence-aware chunking for a whole in-memory array (file transcription).

    Yields (start_sample, chunk) pairs. Each chunk targets `chunk_seconds` but
    the boundary snaps to the latest silent window `_find_last_silence` can see,
    replaying the live path's behaviour: grow the candidate in BLOCK_SECONDS
    steps past the target (the 2s lookback slides with it), force-cut at
    `chunk_seconds * MAX_CHUNK_MULTIPLIER` when no silence exists, and never
    emit a chunk shorter than MIN_EMIT_SECONDS except the final tail.
    Chunks tile the input exactly: no overlap, no lost samples.
    """
    n = audio.shape[0]
    chunk_samples = int(sample_rate * chunk_seconds)
    max_samples = int(chunk_samples * MAX_CHUNK_MULTIPLIER)
    min_emit = int(sample_rate * MIN_EMIT_SECONDS)
    block = max(1, int(sample_rate * BLOCK_SECONDS))
    pos = 0
    while True:
        remaining = n - pos
        if remaining <= chunk_samples:
            if remaining > 0:
                yield pos, audio[pos:n]
            return
        limit = min(max_samples, remaining)
        cut = None
        buf_len = chunk_samples
        while True:
            c = _find_last_silence(audio[pos:pos + buf_len], sample_rate)
            if c is not None and c >= min_emit:
                cut = c
                break
            if buf_len >= limit:
                break
            buf_len = min(buf_len + block, limit)
        if cut is None:
            cut = limit  # no silence within 1.5x target: force-cut (or take the short remainder)
        yield pos, audio[pos:pos + cut]
        pos += cut


def _ring_frames(t_start, arr, rate, frame_s=RING_FRAME_SECONDS):
    """Sub-frame one raw block into ~frame_s energy samples: yields (t, dBFS) pairs.

    `t_start` is the session-clock time of the block's FIRST sample (block START, not arrival),
    so each yielded timestamp is the true time of the audio it describes. The RMS is taken over
    all channels, exactly like the block-level `_rms` in `_ingest_block`, and converted with the
    same `20*log10(x + 1e-9)`, so MIC and SYS ring samples stay directly comparable.

    100 ms rather than one sample per 0.5 s block: coverage tests (echo veto, speech evidence)
    need frame-level resolution - a 1 s segment used to give 2 samples, which cannot express a
    fraction. Costs 5 tiny RMS calls per block instead of 1 and ~6000 floats per ring.
    A block shorter than one frame yields a single sample; a trailing remainder shorter than
    half a frame is folded away (energy statistics, not audio - nothing is lost downstream).
    """
    fr = max(1, int(rate * frame_s))
    n = arr.shape[0]
    if n < fr:
        yield t_start, 20.0 * float(np.log10(float(np.sqrt(np.mean(arr * arr))) + 1e-9))
        return
    i = 0
    while i + fr <= n:
        w = arr[i:i + fr]
        yield t_start + i / rate, 20.0 * float(np.log10(float(np.sqrt(np.mean(w * w))) + 1e-9))
        i += fr
    if n - i >= fr // 2:
        w = arr[i:]
        yield t_start + i / rate, 20.0 * float(np.log10(float(np.sqrt(np.mean(w * w))) + 1e-9))


class CaptureBase:
    """Shared capture state + logic. Backends implement `_open_sources()`
    (open devices, register each via `_register_source`, raise if nothing
    opened) and `_close_sources()` (stop delivering blocks); an optional
    `_release_backend()` hook runs late in `stop()` for host-API teardown
    (PortAudio terminate on Windows)."""

    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15, on_chunk=None, t0=None, aec=False, agc=True, record_raw_mic=False):
        """on_chunk(source: str, audio_16k_mono: np.ndarray, t_start: float).

        t0: optional monotonic start time. When a live session switches its mic or
        loopback we tear this capture down and start a fresh one; passing the original
        t0 keeps transcript timestamps continuous across the switch instead of jumping
        back to 00:00.

        aec: when True AND both a mic and a system loopback open, route both through the
        WebRTC APM (live echo cancellation) before chunking. Needs the LiveKit binding;
        silently runs without it when absent.

        agc: live mic auto-gain (WebRTC AGC on the near end, the Meet/Teams mic behaviour),
        default ON. Independent of the AEC toggle: it applies whether echo cancellation is
        active or bypassed, and on a mic-only session it engages the APM worker on its own
        (AGC-only). The SYS loopback never gets AGC. Needs the LiveKit binding; degrades to
        the raw mic without it.

        record_raw_mic: when True AND the APM worker engages, ALSO chunk the RAW (pre-APM) mic
        on a side "MIC_RAW" source, so a recording made with live AEC/AGC on stays raw (the
        engine still transcribes the processed mic). No effect when the worker does not engage."""
        self.mic_device_spec = mic_device
        self.loopback_device_spec = loopback_device
        self.chunk_seconds = chunk_seconds
        self.on_chunk = on_chunk
        self.aec = aec
        self.agc = agc
        self.record_raw_mic = record_raw_mic
        self._live_aec = None     # set in start() when AEC engages; read by the audio callbacks

        self._stop_event = threading.Event()
        self._t0 = None
        self._t0_init = t0
        self._workers = []
        # Serialises start()'s worker-engagement decision + commit + chunker snapshot against a
        # backend that registers a source LATE from another thread (the macOS deferred system-
        # audio permission grant). Re-entrant; Windows registers everything before start() and
        # never contends it (so this is a no-op there).
        self._lifecycle_lock = threading.RLock()
        self._levels = {}         # source -> (peak, rms), latest input level for a live meter
        self._sys_ring = None     # optional transcribe.EnergyRing, fed raw SYS energy for the echo veto
        self._mic_ring = None     # optional transcribe.EnergyRing, fed RAW (pre-APM) MIC energy

        # Per-source state, keyed by "MIC" / "SYS"
        self._buffers = {}        # source -> list[np.ndarray]
        self._buffer_counts = {}  # source -> int (frames)
        self._buffer_locks = {}   # source -> threading.Lock
        self._rates = {}          # source -> int (native rate)
        self._native_rates = {}   # source -> int (the rate as OPENED; _rates["MIC"] is rewritten to
                                  # 16k when the APM engages, but the callback's blocks stay native)
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
        # The rate this device was OPENED at, never rewritten. start() sets _rates["MIC"] (and
        # SYS) to TARGET_RATE once the APM worker engages, because the CHUNK BUFFER then holds
        # 16k audio - but the blocks arriving at _ingest_block are still native, so the energy
        # ring's block-start timestamps must divide by this rate, not _rates.
        self._native_rates[source] = rate
        self._channels[source] = channels

    def _worker_takes(self, la, source):
        """Does the engaged APM worker `la` consume blocks from `source`?

        MIC always routes to a live worker. SYS routes to it ONLY when the worker was
        built with a far end (has_far): an AGC-only worker (mic-only start, far_rate=None)
        has no far pipeline (no main APM, no on_far sink), so pushing far blocks at it
        would silently DISCARD them; such SYS blocks take the native buffer path instead
        (registered at native rate by the deferred-permission handshake), exactly as a
        no-worker session would. This return logic is unchanged and shared by every backend.

        On macOS the system-audio TCC grant often lands AFTER start() has committed a
        mic-only AGC worker (the common Mac case). Rather than leave echo cancellation
        unavailable for the whole meeting, the macOS backend then attaches a far end to that
        SAME running worker IN PLACE (LiveAEC.attach_far, see
        `capture_mac._maybe_engage_aec_after_grant`): no worker swap, so the mic stream keeps
        flowing through one queue uninterrupted (no lost or reordered mic audio), and has_far
        flips true so this function routes SYS through the worker for the rest of the session.
        That in-place attach is a deliberate, accepted tradeoff and is safe because the helper's
        SYS rate is already 16 kHz, so no live buffer's rate changes. The base/Windows path
        never attaches late and relies purely on this routing; and if the macOS upgrade cannot
        run (no mic worker because AGC is off, or the LiveKit binding is missing), SYS simply
        stays on the native path (still captured + metered, AEC unavailable, as aec_state()
        reports)."""
        return la is not None and (source == "MIC" or getattr(la, "has_far", True))

    def _ingest_block(self, source, arr):
        """Take one float32 block, shaped (frames, channels), from a backend's
        audio thread: level calc, energy-ring feed (MIC + SYS), AEC routing, and
        the under-lock re-check before appending to the chunk buffer."""
        # Cheap per-block level for the live meter (peak + RMS, 0..1). A single
        # dict assignment, so the reader (levels()) never sees a torn value.
        # When the APM worker takes this source the meter is fed from _append_16k
        # instead, so it shows what the engine actually hears (AGC-boosted /
        # echo-cancelled), not the raw device level; the energy rings below ALWAYS read
        # the raw block (the guards key off true device levels, and the far end passes
        # through the worker unchanged anyway).
        if arr.size:
            _rms = float(np.sqrt(np.mean(arr * arr)))
            if not self._worker_takes(self._live_aec, source):
                self._levels[source] = (float(np.max(np.abs(arr))), _rms)
            # Feed this source's energy ring: 100 ms RMS samples from the RAW (pre-APM) block,
            # timestamped on the session clock (same _t0 as chunk t_start) at block START.
            # This is the tap that makes the MIC feed gain-invariant: the engine's silence gate
            # and echo veto used to read the AGC-boosted CHUNK for the mic while reading the raw
            # block for SYS, so a relative test compared a gain-controlled signal against a raw
            # one. Both sides now come from here, same seam, same RMS definition, same clock.
            _ring = self._sys_ring if source == "SYS" else (self._mic_ring if source == "MIC" else None)
            if _ring is not None and self._t0 is not None:
                # Native rate, NOT self._rates: start() rewrites _rates["MIC"] to 16k when the
                # APM engages while these blocks stay at the device rate.
                _nr = self._native_rates.get(source) or self._rates.get(source) or TARGET_RATE
                _tb = time.monotonic() - self._t0 - arr.shape[0] / _nr
                for _t, _db in _ring_frames(_tb, arr, _nr):
                    _ring.add(_t, _db)
        # Live echo cancellation (when engaged): hand the mono block to the APM worker
        # instead of the native chunk buffer; the worker resamples + cancels and feeds
        # the (now 16k) chunk buffer itself. A SYS block a mic-only (AGC-only) worker
        # cannot consume bypasses the worker (see _worker_takes) and lands in its own
        # native-rate buffer below.
        la = self._live_aec
        if self._worker_takes(la, source):
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
                if self._worker_takes(la, source):
                    mono = arr.mean(axis=1) if (arr.ndim > 1 and arr.shape[1] > 1) else arr[:, 0]
                    (la.push_near if source == "MIC" else la.push_far)(mono)
                else:
                    self._buffers[source].append(arr)
                    self._buffer_counts[source] = self._buffer_counts[source] + arr.shape[0]

    # ---- lifecycle -----------------------------------------------------

    def start(self):
        self._t0 = self._t0_init if self._t0_init is not None else time.monotonic()
        self._open_sources()
        # Worker-engagement decision + commit + chunker snapshot under the lifecycle lock: a
        # backend can register a source LATE from another thread (the macOS deferred system-audio
        # grant), so this serialises against it - no registration slips between the want_aec
        # decision and the worker commit, and self._buffers is never mutated while it is being
        # snapshotted for the chunker loop. Windows registers before start() and never contends it.
        with self._lifecycle_lock:
            self._engage_worker_and_start_chunkers()

    def _engage_worker_and_start_chunkers(self):
        # Live echo cancellation + mic auto-gain: the APM worker engages whenever both a mic and
        # a system loopback opened (AEC needs the loopback as the far-end reference), and ALSO on
        # a mic-only session when AGC is on (AGC-only worker, nothing to cancel against). When the
        # user has AEC off, the worker still runs but in BYPASS (raw or AGC-only mic emitted, APM
        # kept fed), so echo cancellation can be toggled on or off mid-meeting by flipping the
        # bypass; no capture rebuild, no rate change. The APM worker resamples to 16k and emits
        # the (processed or raw) mic + passthrough system into the chunk buffers, so the chunkers
        # then treat those sources as 16k (no second resample in _emit).
        want_aec = "MIC" in self._buffers and "SYS" in self._buffers
        want_agc_only = not want_aec and "MIC" in self._buffers and self.agc
        if want_aec or want_agc_only:
            la = None
            try:
                from . import aec as _aec
                if not _aec.available():
                    raise RuntimeError("LiveKit binding unavailable")
                from .aec_live import LiveAEC
                if want_aec:
                    la = LiveAEC(
                        self._rates["MIC"], self._rates["SYS"],
                        on_near=lambda x: self._append_16k("MIC", x),
                        on_far=lambda x: self._append_16k("SYS", x),
                        bypass=not self.aec,
                        agc=self.agc,
                    )
                else:
                    la = LiveAEC(
                        self._rates["MIC"], None,
                        on_near=lambda x: self._append_16k("MIC", x),
                        on_far=None,
                        bypass=True,   # no far end, nothing to cancel; AGC is the whole job
                        agc=True,
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
                if want_aec:
                    self._rates["SYS"] = TARGET_RATE
                for s in (("MIC", "SYS") if want_aec else ("MIC",)):
                    with self._buffer_locks[s]:
                        self._buffers[s].clear()
                        self._buffer_counts[s] = 0
                if want_aec:
                    print(f"[aec] live echo canceller engaged ({'active' if self.aec else 'bypassed, ready to toggle on'}) "
                          f"(mic + system -> WebRTC APM, mic auto-gain {'on' if la.agc else 'off'})", flush=True)
                else:
                    print("[agc] live mic auto-gain engaged (mic-only session -> WebRTC APM, no echo cancellation)", flush=True)
            except Exception as e:
                # A missing or broken audio processor must never stop the session: degrade to
                # normal capture (native rate, no AEC/AGC).
                if la is not None:
                    try:
                        la.stop()
                    except Exception:
                        pass
                self._live_aec = None
                print(f"[aec] live audio processing unavailable ({e}); running without it", flush=True)

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
        # Live meter from the PROCESSED audio (see _ingest_block): with the worker engaged,
        # the meter shows the post-APM mic (AGC boost visible) instead of the raw device level.
        if a.size:
            self._levels[source] = (float(np.max(np.abs(a))), float(np.sqrt(np.mean(a * a))))
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
        # An AGC-only worker (mic-only session) is not an echo canceller: for the AEC toggle
        # it counts as absent, and its bypass flag must never flip (bypass=True is what routes
        # the mic through the AGC-only path).
        if la is None or not getattr(la, "aec_capable", True):
            if enabled:
                return False
            self.aec = False
            return True
        la.bypass = not enabled
        self.aec = bool(enabled)
        return True

    def aec_state(self):
        """(available, active): available = an ECHO-CAPABLE APM worker is running this session
        (so the toggle can work; an AGC-only mic worker does not count), active = it is actually
        cancelling (not bypassed)."""
        la = self._live_aec
        capable = la is not None and getattr(la, "aec_capable", True)
        return (capable, capable and not la.bypass)

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
        """Attach a transcribe.EnergyRing for the far end. The audio callback then feeds it 100 ms
        RMS samples from every raw SYS block in real time, so the engine's echo veto has a current
        far-end reference even during a monologue (when SYS chunks emit late)."""
        self._sys_ring = ring

    def attach_mic_ring(self, ring):
        """Attach a transcribe.EnergyRing for the near end, fed from the RAW (pre-APM) mic blocks.

        The mic the engine transcribes is AGC-boosted, so chunk energy says nothing about how loud
        the room actually was: the silence gate and the echo veto's mic side need this raw feed to
        stay on their calibrated absolute basis (-45 dBFS floor, -28 dBFS ceiling, 10 dB margin).
        Separate method rather than a `source` argument on attach_sys_ring: several test stubs
        implement `attach_sys_ring` by name."""
        self._mic_ring = ring

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
