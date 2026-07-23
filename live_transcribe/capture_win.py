"""WASAPI loopback + mic capture via pyaudiowpatch (Windows backend).

Each device is opened at its OWN native sample rate (the loopback is usually
48 kHz; mics vary, Samson C01U is 44.1 kHz). PortAudio invokes our callback
on a dedicated audio thread; we hand each block to the shared core
(`capture_core.CaptureBase._ingest_block`), which owns the per-source
buffers, the silence-aware chunkers and the 16 kHz emit.
"""
import numpy as np
import pyaudiowpatch as pa

from .capture_core import BLOCK_SECONDS, CaptureBase
from .devices_win import resolve_loopback, resolve_mic


class AudioCapture(CaptureBase):
    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15, on_chunk=None, t0=None, aec=False, agc=True, record_raw_mic=False):
        super().__init__(mic_device=mic_device, loopback_device=loopback_device,
                         chunk_seconds=chunk_seconds, on_chunk=on_chunk, t0=t0,
                         aec=aec, agc=agc, record_raw_mic=record_raw_mic)
        self._pa = None
        self._streams = []

    def _open_sources(self):
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

    def _close_sources(self):
        for s in self._streams:
            try:
                s.stop_stream()
                s.close()
            except Exception:
                pass

    def _release_backend(self):
        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:
                pass
            self._pa = None

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
            self._register_source(source, rate, channels)
            ch = channels

            def callback(in_data, frame_count, time_info, status, _ch=ch, _src=source, _self=self):
                try:
                    arr = np.frombuffer(in_data, dtype=np.float32)
                    if _ch > 1:
                        arr = arr.reshape(-1, _ch)
                    else:
                        arr = arr.reshape(-1, 1)
                    # Level calc, SYS-ring feed, AEC routing and the under-lock
                    # re-check all live in the shared core.
                    _self._ingest_block(_src, arr)
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
        # in _open_sources turns it into the user-facing "could not open ..." message.
        raise last_err if last_err is not None else RuntimeError(
            f"could not open {source} at any channel count (tried {candidates})"
        )
