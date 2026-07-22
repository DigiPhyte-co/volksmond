"""Streaming acoustic echo cancellation for a LIVE capture (mic + system loopback).

Offline AEC (aec.py) has both full channels in hand. Live is harder: the mic (near-end) and the
system loopback (far-end) arrive as native-rate blocks from two PortAudio callbacks on separate
threads, at different device clocks (mic 44.1k, loopback 48k). WebRTC's APM needs both as
continuous 10 ms frames at one rate, far-end fed via process_reverse_stream and near-end via
process_stream (which returns the echo-cancelled near-end); it aligns them internally.

Design:
  - The two callbacks only monoise + streaming-resample to 16 k (soxr, stateful per stream) and
    enqueue. They never touch the APM, so the real-time audio thread stays fast and lock-free.
  - One worker thread owns the APM (so it is never called concurrently - no lock needed) and
    emits cleaned mic frames + passthrough system frames back into the capture buffers.
  - The streams are decoupled: if the system goes silent and the loopback stops delivering, the
    mic still flows through (the APM simply has no new reference, so nothing is cancelled). The
    mic is never blocked waiting on the far-end.

Degrades to nothing useful without the LiveKit binding, so the caller checks aec.available()
before constructing this.
"""
import queue
import threading

import numpy as np
import soxr

TARGET_RATE = 16000
FRAME = 160          # 10 ms at 16 k
_MAXQ = 400          # ~ blocks queued before we drop (worker runs ~100x real-time, so never hit)


class LiveAEC:
    def __init__(self, near_rate, far_rate, on_near, on_far, bypass=False):
        """on_near(cleaned_mono_16k_float32), on_far(system_mono_16k_float32): sinks that append
        the processed audio into the capture pipeline (already at 16 k).

        bypass: when True the RAW mic frames are emitted instead of the cleaned ones. The APM is
        still fed every frame (both directions), so its echo estimate stays converged and flipping
        bypass mid-meeting takes effect on the next 10 ms frame with no replay, drop, or clock
        skew: the frame flow is identical either way, only which copy is emitted changes. Written
        by the toggle endpoint's thread, read per-frame by the worker (a plain bool is atomic)."""
        self._on_near = on_near
        self._on_far = on_far
        self.bypass = bypass
        self._near_rs = None if near_rate == TARGET_RATE else soxr.ResampleStream(near_rate, TARGET_RATE, 1, dtype="float32")
        self._far_rs = None if far_rate == TARGET_RATE else soxr.ResampleStream(far_rate, TARGET_RATE, 1, dtype="float32")
        self._near_q = queue.Queue(maxsize=_MAXQ)
        self._far_q = queue.Queue(maxsize=_MAXQ)
        self._near_buf = np.zeros(0, dtype=np.float32)   # 16k samples awaiting framing (worker-only)
        self._far_buf = np.zeros(0, dtype=np.float32)
        self._stop = threading.Event()
        self._thread = None
        self._apm = None
        self._dropped = 0

    # -- producer side (PortAudio callback threads) --------------------------------------------
    def push_near(self, mono_native):
        self._offer(self._near_q, mono_native)

    def push_far(self, mono_native):
        self._offer(self._far_q, mono_native)

    def _offer(self, q, block):
        try:
            q.put_nowait(np.asarray(block, dtype=np.float32))
        except queue.Full:
            self._dropped += 1   # worker fell behind (should never happen at ~100x real-time)

    # -- worker ---------------------------------------------------------------------------------
    def start(self):
        from livekit.rtc.apm import AudioProcessingModule
        self._apm = AudioProcessingModule(echo_cancellation=True, noise_suppression=False,
                                          high_pass_filter=False, auto_gain_control=False)
        self._thread = threading.Thread(target=self._run, daemon=True, name="live-aec")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _drain(self, q, rs):
        """Pull every queued native block, streaming-resample to 16 k, return the concatenation."""
        out = []
        while True:
            try:
                block = q.get_nowait()
            except queue.Empty:
                break
            out.append(rs.resample_chunk(block) if rs is not None else block)
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

    def _run(self):
        from livekit.rtc import AudioFrame
        self._AudioFrame = AudioFrame
        while not self._stop.is_set():
            self._pump()
            self._stop.wait(0.01)
        self._pump(flush=True)   # drain queues + resampler tails + the final partial frame

    def _pump(self, flush=False):
        far_new = self._drain(self._far_q, self._far_rs)
        near_new = self._drain(self._near_q, self._near_rs)
        if flush:   # flush soxr's internal latency so the last few ms are not lost
            if self._far_rs is not None:
                far_new = np.concatenate([far_new, self._far_rs.resample_chunk(np.zeros(0, np.float32), last=True)])
            if self._near_rs is not None:
                near_new = np.concatenate([near_new, self._near_rs.resample_chunk(np.zeros(0, np.float32), last=True)])
        self._far_buf = np.concatenate([self._far_buf, far_new])
        self._near_buf = np.concatenate([self._near_buf, near_new])

        # Far-end (reference) first, in 10 ms frames, so the APM has it before the matching
        # near-end. The SYS transcript wants the clean loopback, so emit the pre-APM far audio;
        # process_reverse_stream only informs the canceller.
        far_emit = []
        while len(self._far_buf) >= FRAME:
            frame = self._far_buf[:FRAME]
            self._far_buf = self._far_buf[FRAME:]
            self._apm.process_reverse_stream(self._pcm_frame(frame))
            far_emit.append(frame)
        if flush and len(self._far_buf):
            far_emit.append(self._far_buf)
            self._far_buf = np.zeros(0, dtype=np.float32)
        if far_emit:
            self._on_far(np.concatenate(far_emit))

        # Near-end: process each 10 ms frame -> cleaned mic. Runs even when the far-end is
        # starved (system silent): the APM cancels nothing and the mic passes through.
        near_emit = []
        while len(self._near_buf) >= FRAME:
            frame = self._near_buf[:FRAME]
            self._near_buf = self._near_buf[FRAME:]
            af = self._pcm_frame(frame)
            self._apm.process_stream(af)
            # Bypassed: emit the raw frame; the APM was still fed above so a re-enable is instant.
            near_emit.append(frame if self.bypass
                             else np.frombuffer(af.data, dtype=np.int16).astype(np.float32) / 32768.0)
        if flush and len(self._near_buf):
            rem = len(self._near_buf)
            padded = np.zeros(FRAME, dtype=np.float32)
            padded[:rem] = self._near_buf
            af = self._pcm_frame(padded)
            self._apm.process_stream(af)
            near_emit.append(padded[:rem] if self.bypass
                             else (np.frombuffer(af.data, dtype=np.int16).astype(np.float32) / 32768.0)[:rem])
            self._near_buf = np.zeros(0, dtype=np.float32)
        if near_emit:
            self._on_near(np.concatenate(near_emit))

    def _pcm_frame(self, mono_f32):
        pcm = np.clip(mono_f32 * 32768.0, -32768, 32767).astype("<i2")
        return self._AudioFrame(bytearray(pcm.tobytes()), TARGET_RATE, 1, FRAME)
