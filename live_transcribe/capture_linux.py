"""PulseAudio/PipeWire capture backend (Linux): mic + system audio over
native libpulse via pasimple.

This is the Linux counterpart of capture_win, and it is structurally modelled
on it (in-process, no subprocess helper): it implements only the device
lifecycle on top of the shared seam (`capture_core.CaptureBase`): open the
sources, register each via `_register_source`, feed raw float32 blocks shaped
(frames, channels) at the device's native rate into `_ingest_block`, and close
cleanly. Everything from the per-source buffer downwards (silence-aware
chunking, the 16 kHz emit, the live meter, the SYS energy ring, the live-AEC
engagement dance) is inherited unchanged.

Two sources, exactly as on Windows:

- MIC: the chosen non-monitor Pulse source.
- SYS: the default (or chosen) sink's `.monitor` source, the server-side
  equivalent of WASAPI loopback. No permission prompt, no helper binary.

Each source is one blocking `pasimple.PaSimple(PA_STREAM_RECORD, ...)` stream
(the libpulse *simple* API, ctypes over libpulse-simple.so.0), drained by a
small reader thread doing short blocking reads (~READ_SECONDS each, so stop()
stays responsive) that are aggregated to ~BLOCK_SECONDS before ingest, keeping
the level-meter and SYS-ring cadence identical to the Windows callbacks.
Streams are requested at PA_SAMPLE_FLOAT32LE and the source's native rate and
channel count; the server converts as needed, so there is no channel-count
fallback ladder here (unlike WASAPI, a Pulse open does not fail on format).
If the float constant is missing, or a FLOAT32LE open fails, the stream falls
back to PA_SAMPLE_S16LE (one retry, no ladder) and the reader scales by
1/32768 (capture_core ingests float32 either way).

Why not PortAudio/sounddevice: the released PortAudio builds (wheel-bundled
and distro alike) have no PulseAudio/PipeWire host API, so monitor sources
are invisible to them (linux-port plan, header note). libpulse is the one
client stack both PulseAudio (Mint 21.x) and pipewire-pulse (Mint 22, Debian
12+) serve.

Fallback ladder seam: tier 2 of the plan's ladder (a `parec` subprocess
piping raw PCM, section 2.2) is deliberately NOT implemented in v1; it ships
only if pasimple fails on real hardware. If that happens, add an
`_open_stream_parec(source, desc)` here alongside `_open_stream` and select
between them; enumeration stays on pulsectl either way.

Import discipline: numpy and the stdlib only at module import time. pasimple
(linux-only package) is imported lazily inside `_open_stream`, so this file
imports cleanly on Windows for the test suite, which drives the backend with
fake pulsectl/pasimple modules.
"""
import threading
import time

import numpy as np

from .capture_core import BLOCK_SECONDS, CaptureBase
from .devices_linux import resolve_loopback, resolve_mic

APP_NAME = "Volksmond"

# One blocking read's worth of audio. Small (20 ms) so the reader notices the
# stop flag quickly; reads are aggregated to ~BLOCK_SECONDS before ingest so
# the level meter and the SYS energy ring see the same ~0.5 s cadence as the
# Windows and macOS backends (the ring retains minutes of per-block samples,
# so flooding it at 50 Hz would be wasteful).
READ_SECONDS = 0.02

# One SHARED deadline across ALL reader joins at stop time (not per reader, so
# a stop can never stall for readers x timeout). Readers do ~READ_SECONDS
# blocking reads and check the stop flag between reads, so they normally exit
# almost instantly; the deadline only bites when a stalled server wedges a
# reader inside read().
_JOIN_DEADLINE_S = 4.0


class AudioCapture(CaptureBase):
    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15, on_chunk=None, t0=None, aec=False, record_raw_mic=False):
        super().__init__(mic_device=mic_device, loopback_device=loopback_device,
                         chunk_seconds=chunk_seconds, on_chunk=on_chunk, t0=t0,
                         aec=aec, record_raw_mic=record_raw_mic)
        self._streams = []                     # [{"source", "stream", "thread"}]
        self._stalled = []                     # readers that missed the stop deadline (see _close_sources)
        self._capture_stop = threading.Event()  # our own flag: _close_sources runs BEFORE CaptureBase sets _stop_event

    def _open_sources(self):
        # Transactional, like the macOS backend: on ANY failure, stop and close
        # everything opened so far (streams + reader threads) before
        # propagating, so we never leave a half-open backend.
        try:
            self._open_sources_inner()
        except Exception:
            self._close_sources()
            raise

    def _open_sources_inner(self):
        loop_desc = None
        try:
            loop_desc = resolve_loopback(None, self.loopback_device_spec)
        except Exception as e:
            print(f"[SYS] cannot resolve system-audio monitor source: {e}", flush=True)

        mic_desc = None
        try:
            mic_desc = resolve_mic(None, self.mic_device_spec)
        except Exception as e:
            print(f"[MIC] cannot resolve mic: {e}", flush=True)

        # Wrap each open so the failing source identifies itself in the error
        # the FastAPI layer surfaces, exactly as capture_win does: the raw
        # libpulse error code alone does not tell the user which dropdown to
        # change.
        if loop_desc is not None:
            try:
                self._open_stream("SYS", loop_desc)
            except Exception as e:
                raise RuntimeError(
                    f"could not open system audio source #{loop_desc['index']} "
                    f"'{loop_desc['name']}': {e}. Try a different option in "
                    "the System audio dropdown."
                ) from e
        if mic_desc is not None:
            try:
                self._open_stream("MIC", mic_desc)
            except Exception as e:
                raise RuntimeError(
                    f"could not open microphone #{mic_desc['index']} "
                    f"'{mic_desc['name']}': {e}. Try a different option in the "
                    "Your microphone dropdown."
                ) from e

        if not self._streams:
            raise RuntimeError(
                "no audio sources opened (both the system-audio monitor and the "
                "mic failed). Run --list-devices from the CLI to enumerate what "
                "is available."
            )

    def _open_stream(self, source, desc):
        import pasimple

        rate = int(desc["rate"])
        channels = max(1, int(desc["channels"]))

        # PA_SAMPLE_FLOAT32LE is the primary path (matches _ingest_block's
        # float32 contract with zero conversion). The S16 fallback (one
        # multiply in the reader covers it) engages when the float constant is
        # unavailable OR when the FLOAT32LE open itself fails: one retry with
        # S16LE for this source, no further ladder.
        float_fmt = getattr(pasimple, "PA_SAMPLE_FLOAT32LE", None)
        attempts = []  # (fmt, sample width in bytes, reader scale)
        if float_fmt is not None:
            attempts.append((float_fmt, 4, None))
        attempts.append((pasimple.PA_SAMPLE_S16LE, 2, 1.0 / 32768.0))

        self._register_source(source, rate, channels)
        try:
            stream = None
            read_bytes = None
            scale = None
            for i, (fmt, width, fmt_scale) in enumerate(attempts):
                # Sample-width-consistent read size: fragsize sized to one read
                # so the server delivers in low-latency fragments (the simple
                # API's default is about 2 s, far too coarse for a live meter).
                # TODO(linux-hw): validate fragsize behaviour and capture
                # latency under both PulseAudio (Mint 21) and pipewire-pulse
                # (Mint 22 / Debian 12) with the null-sink Docker fixture and
                # on the Mint box.
                frame_bytes = width * channels
                attempt_read_bytes = max(1, int(rate * READ_SECONDS)) * frame_bytes
                try:
                    stream = pasimple.PaSimple(
                        pasimple.PA_STREAM_RECORD,
                        fmt,
                        channels,
                        rate,
                        app_name=APP_NAME,
                        stream_name=f"{APP_NAME} {source}",
                        device_name=desc["source"],
                        fragsize=attempt_read_bytes,
                    )
                except Exception as e:
                    if i + 1 < len(attempts):
                        print(f"[{source}] FLOAT32LE open failed ({e}); "
                              "retrying once with S16LE", flush=True)
                        continue
                    raise
                read_bytes, scale = attempt_read_bytes, fmt_scale
                break
        except Exception:
            # Clear the partial per-source state so a failure doesn't leave
            # half-initialised buffers behind (mirrors capture_win).
            for d in (self._buffers, self._buffer_counts, self._buffer_locks,
                      self._rates, self._channels):
                d.pop(source, None)
            raise

        t = threading.Thread(
            target=self._reader,
            args=(source, stream, rate, channels, read_bytes, scale),
            daemon=True,
            name=f"pulse-reader-{source}",
        )
        entry = {"source": source, "stream": stream, "thread": t}
        self._streams.append(entry)
        print(f"[{source}] opened '{desc['name']}' @ {rate} Hz x{channels}ch "
              f"(pulse source '{desc['source']}')", flush=True)
        try:
            t.start()
        except Exception:
            # A thread that never started can neither drain nor close its
            # stream, and _close_sources skips not-alive threads, so the
            # transactional rollback would miss it: close the stream here (no
            # reader is inside read(), so this close is safe), drop its entry,
            # unregister its source, then re-raise into the existing
            # transactional path (_open_sources -> _close_sources).
            try:
                stream.close()
            except Exception:
                pass
            self._streams.remove(entry)
            for d in (self._buffers, self._buffer_counts, self._buffer_locks,
                      self._rates, self._channels):
                d.pop(source, None)
            raise

    def _reader(self, source, stream, rate, channels, read_bytes, scale):
        """Drain one record stream: short blocking reads, aggregated to
        ~BLOCK_SECONDS, converted to float32 (frames, channels) and handed to
        the shared core. Owns the stream's close on every exit path (libpulse
        simple streams are not thread-safe, so the thread that reads is the
        thread that closes)."""
        target_frames = int(rate * BLOCK_SECONDS)
        pending = []
        pending_frames = 0
        try:
            while not self._capture_stop.is_set():
                data = stream.read(read_bytes)
                if not data:
                    continue
                if scale is None:
                    arr = np.frombuffer(data, dtype="<f4").astype(np.float32, copy=True)
                else:
                    arr = np.frombuffer(data, dtype="<i2").astype(np.float32) * scale
                arr = arr.reshape(-1, channels)
                pending.append(arr)
                pending_frames += arr.shape[0]
                if pending_frames >= target_frames:
                    block = np.concatenate(pending, axis=0) if len(pending) > 1 else pending[0]
                    pending = []
                    pending_frames = 0
                    # Level calc, SYS-ring feed, AEC routing and the under-lock
                    # re-check all live in the shared core.
                    self._ingest_block(source, block)
        except Exception as e:
            # Mid-session stream failure (server restart, device vanished):
            # this source goes quiet, the session continues on the other one,
            # matching the macOS degrade posture. No auto-reconnect in v1.
            if not self._capture_stop.is_set():
                print(f"[{source}] capture stream stopped: {e}", flush=True)
        finally:
            # Flush the sub-block tail so the last words are not lost: stop()
            # calls _close_sources (which joins this thread) BEFORE it signals
            # the chunkers to flush, so this ingest still lands in the buffers
            # in time.
            if pending:
                try:
                    tail = np.concatenate(pending, axis=0) if len(pending) > 1 else pending[0]
                    self._ingest_block(source, tail)
                except Exception:
                    pass
            try:
                stream.close()
            except Exception:
                pass

    def _close_sources(self):
        self._capture_stop.set()
        deadline = time.monotonic() + _JOIN_DEADLINE_S
        for s in self._streams:
            t = s["thread"]
            if t.is_alive():
                t.join(timeout=max(0.0, deadline - time.monotonic()))
        # A reader that missed the shared deadline may still be INSIDE a
        # blocking read(); pa_simple makes no thread-safety promise, so
        # closing (freeing) its stream from this thread risks a use-after-free
        # in libpulse. Accepted trade-off: on a pathological hang we keep the
        # stream handle referenced (a leak) rather than free it under the
        # reader, and _release_backend attempts one final short rejoin.
        self._stalled = [s for s in self._streams if s["thread"].is_alive()]
        for s in self._stalled:
            print(f"[{s['source']}] reader did not exit within "
                  f"{_JOIN_DEADLINE_S:.0f}s; keeping its stream open rather "
                  "than freeing it under a blocked read", flush=True)
        self._streams = []

    def _release_backend(self):
        # Unlike PortAudio on Windows there is no host-API singleton to
        # terminate; each stream is closed by its reader. This late hook only
        # gives readers that missed the _close_sources deadline one final
        # short rejoin (the reader's finally block closes its own stream).
        # Anything still alive after that stays referenced in self._stalled
        # for the life of this capture object: a leaked stream on a wedged
        # daemon thread is freed at process exit and is the accepted
        # trade-off over a use-after-free.
        for s in self._stalled:
            t = s["thread"]
            if t.is_alive():
                t.join(timeout=0.5)
        self._stalled = [s for s in self._stalled if s["thread"].is_alive()]
        for s in self._stalled:
            print(f"[{s['source']}] reader still blocked after the final "
                  "rejoin; leaking its stream (daemon thread, reclaimed at "
                  "process exit)", flush=True)
