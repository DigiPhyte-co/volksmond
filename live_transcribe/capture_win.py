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
from .devices_win import (
    _fix_name,
    default_loopback_name,
    loopback_candidate_names,
    mic_candidate_names,
    pa_acquire,
    pa_instances,
    pa_release,
    pa_table_age_s,
    resolve_loopback,
    resolve_mic,
)
from .licensing import APP_VERSION


class AudioCapture(CaptureBase):
    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15, on_chunk=None, t0=None, aec=False, agc=True, record_raw_mic=False, positional=True):
        super().__init__(mic_device=mic_device, loopback_device=loopback_device,
                         chunk_seconds=chunk_seconds, on_chunk=on_chunk, t0=t0,
                         aec=aec, agc=agc, record_raw_mic=record_raw_mic, positional=positional)
        self._pa = None
        self._streams = []
        # System-audio (loopback) health, mirrored to /api/status the same way the Mac backend
        # exposes sys_state (H1). Windows uses only two of the shared values: "active" once the
        # loopback stream opened, "failed" when the loopback could not resolve or open while the mic
        # DID open (a mic-only session, which is better than no session). sys_error is a short,
        # human-readable reason (device name + what went wrong, no stack trace) the banner shows.
        self.sys_state = "active"
        self.sys_error = None
        # Structured form of the same failure for the UI (codex F7): sys_error_reason is a code
        # ("not_found" | "open_failed") and sys_error_device the device name, so the banner text is
        # built by a translated trFmt template rather than passing this English string through
        # exact-key tr(). sys_error stays a plain English string for the log and diagnostics.
        self.sys_error_reason = None
        self.sys_error_device = None
        # Follow-the-default bookkeeping for the device-follow watcher (WP-4). sys_loopback_name is
        # the CLEANED name of the loopback we actually opened; sys_following_default is True when that
        # equals the Windows default output's loopback name at open time, which is the signal the
        # watcher uses to decide between auto-following a default change and warning about an idle
        # explicit pick. sys_frames counts SYS blocks delivered, so the watcher can tell a loopback
        # that is producing nothing (an endpoint nothing is rendering to) from one that is live.
        self.sys_loopback_name = None
        self.sys_following_default = False
        self.sys_frames = 0

    def _open_sources(self):
        # Acquire through the lifecycle guard (role="capture"): it waits briefly for any live
        # enumeration helper to release so PortAudio rebuilds a FRESH device table (the init-count
        # trap), and never deadlocks. The device view every source below resolves against is only as
        # current as this table, so log its age at each session open for the field diagnostics.
        self._pa = pa_acquire("capture")
        print(f"[devices] table age={pa_table_age_s()}s pa_instances={pa_instances()} "
              f"build={APP_VERSION}", flush=True)
        self.sys_state = "active"   # optimistic; flipped to "failed" below if the loopback cannot open
        self.sys_error = None
        self.sys_error_reason = None
        self.sys_error_device = None

        loopback_info = None
        try:
            loopback_info = resolve_loopback(self._pa, self.loopback_device_spec, positional=self.positional)
        except Exception as e:
            # Resolution failed (a stale index, or a chosen device that was unplugged/renumbered). Do
            # NOT abort: the mic below may still open, and a mic-only session is better than no
            # session. Record it so /api/status can raise the banner (H1) and the user knows the far
            # side of the call is missing from the transcript. Log the candidate names the resolver
            # searched, so a "wrong source" report can be read straight off the log.
            self.sys_state = "failed"
            self._set_sys_error(self.loopback_device_spec, reason="not_found")
            print(f"[SYS] cannot resolve loopback: {e}", flush=True)
            print(f"[SYS] resolve FAILED want={self.loopback_device_spec!r} "
                  f"candidates={self._candidate_names(loopback_candidate_names)}", flush=True)

        mic_info = None
        try:
            mic_info = resolve_mic(self._pa, self.mic_device_spec, positional=self.positional)
        except Exception as e:
            print(f"[MIC] cannot resolve mic: {e}", flush=True)
            print(f"[MIC] resolve FAILED want={self.mic_device_spec!r} "
                  f"candidates={self._candidate_names(mic_candidate_names)}", flush=True)

        # Wrap each open so the failing source identifies itself in the error the FastAPI layer
        # surfaces. The raw PyAudio message (e.g. `[Errno -9996] Invalid device`) by itself does not
        # tell the user whether their mic or their loopback choice failed, so they cannot guess which
        # dropdown to change. WASAPI loopback in particular can enumerate a device whose actual
        # endpoint is inactive (a loopback on the speakers while Windows is playing through the
        # headphones is the common case): swapping to the endpoint that is actually playing fixes it.
        #
        # A loopback open failure is now RECORDED and swallowed rather than raised: the mic still
        # opens below, so the session runs mic-only with sys_state="failed" and the banner shows
        # immediately (locked: mic-only is better than nothing). Only a mic that will not open, or
        # both sources failing, aborts the start.
        if loopback_info is not None:
            try:
                self._open_stream("SYS", loopback_info)
            except Exception as e:
                self.sys_state = "failed"
                self._set_sys_error(loopback_info.get("name"), reason="open_failed")
                print(f"[SYS] could not open system audio device #{loopback_info['index']} "
                      f"'{loopback_info['name']}': {e}", flush=True)
        if mic_info is not None:
            try:
                self._open_stream("MIC", mic_info)
            except Exception as e:
                # A SYS (loopback) stream may already be open and running on its own audio thread.
                # Close it before re-raising so a mic that will not open can never strand the
                # loopback stream (and its thread) for the life of the process. Idempotent: the
                # streams are removed from the list so a caller's later stop() will not double-close.
                self._close_sources()
                self._streams = []
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

        # Follow-the-default bookkeeping: record the cleaned name of the loopback we actually opened
        # and whether it matches the Windows default output at open time. The watcher (WP-4) reads
        # both to decide between auto-following a later default change and warning that an explicitly
        # chosen loopback is idle. Only meaningful when a loopback stream opened; on a failed loopback
        # the name stays None and following stays False so the watcher leaves it to the banner.
        if "SYS" in self._buffers and loopback_info is not None:
            self.sys_loopback_name = _fix_name(loopback_info["name"]).strip()
            default_name = default_loopback_name(self._pa)
            self.sys_following_default = (
                default_name is not None and self.sys_loopback_name == default_name
            )

    def _candidate_names(self, lister):
        """The PortAudio candidate names `lister` would search, for a resolve-failure log line. Never
        raises: a diagnostic must not mask the resolution error it is annotating, so an enumeration
        that itself fails just logs an empty list."""
        try:
            return lister(self._pa)
        except Exception:
            return []

    def _set_sys_error(self, name, reason):
        """Record a system-audio failure in both forms: the structured (reason code + device name)
        the UI renders through a translated template (codex F7), and the plain English sys_error
        string for the log and diagnostics. reason is "not_found" (could not resolve) or "open_failed"
        (resolved but the endpoint would not open, usually nothing rendering to it). No stack traces.

        When there is no real device name (a None spec that still failed), sys_error_device is None
        (codex G7): the UI then picks a translated UNNAMED-device template rather than inserting an
        English placeholder verbatim into the Afrikaans string. The log string keeps a readable
        placeholder for diagnostics."""
        who = _fix_name(str(name)).strip() if name else None
        self.sys_error_reason = reason
        self.sys_error_device = who
        log_who = who or "the chosen system-audio device"
        if reason == "open_failed":
            self.sys_error = (f"System audio device '{log_who}' would not open, usually because nothing is "
                              "playing to it. Pick the output you are actually using in the System audio dropdown.")
        else:
            self.sys_error = (f"System audio device '{log_who}' could not be found (it may have been unplugged "
                              "or renumbered). Pick another entry in the System audio dropdown.")

    def _close_sources(self):
        """Stop and close every stream; True only if they all closed. The per-stream exception is
        still swallowed, because one stuck device must never skip the teardown of the others, but
        it is no longer DISCARDED: stop() turns this answer into whether the app may tell the user
        the microphone is off, and a close that silently failed made that claim unearned."""
        ok = True
        for s in self._streams:
            try:
                s.stop_stream()
                s.close()
            except Exception as e:
                ok = False
                print(f"[capture] could not close an input stream: {e}", flush=True)
        return ok

    def _release_backend(self):
        """Terminate PortAudio through the lifecycle guard (which keeps the process-wide instance
        count honest); True on success, or when there is nothing left to release."""
        if self._pa is None:
            return True
        ok = True
        try:
            pa_release(self._pa)
        except Exception as e:
            ok = False
            print(f"[capture] could not terminate the audio backend: {e}", flush=True)
        self._pa = None
        return ok

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
                    # Count SYS blocks delivered so the device-follow watcher can tell a loopback that
                    # is producing nothing (an endpoint nothing is rendering to) from a live one. A
                    # plain int increment from the audio thread is a GIL-atomic write the watcher reads
                    # without a lock; it only ever needs to see whether it moved between ticks.
                    if _src == "SYS":
                        _self.sys_frames += arr.shape[0]
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
