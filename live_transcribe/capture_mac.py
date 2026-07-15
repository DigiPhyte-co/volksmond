"""macOS audio capture backend: mic via sounddevice, system audio via the
signed Swift `volksmond-audiotap` helper (a Core Audio process tap).

This is the darwin counterpart of capture_win. It implements only the device
lifecycle on top of the shared seam (`capture_core.CaptureBase`): open the
sources, register each via `_register_source`, feed raw float32 blocks into
`_ingest_block`, and close cleanly. Everything from the per-source buffer
downwards (silence-aware chunking, the 16 kHz emit, the live meter, the SYS
energy ring, the live-AEC engagement dance) is inherited unchanged.

Two sources, exactly as on Windows:

- MIC: a `sounddevice.InputStream` (PortAudio over Core Audio) at the device's
  native rate. Its callback hands each block to `_ingest_block("MIC", ...)`.
- SYS: the bundled `volksmond-audiotap` subprocess. It is invoked
  `--sample-rate 16000 --mono` and speaks the FROZEN stdout contract (mac-port
  plan section 2.2): newline-delimited JSON header lines (a format line plus
  `{"event":"started"}` / `{"event":"permission_denied"}`), then length-
  prefixed raw PCM frames (uint32 LE byte count + float32 LE samples). A reader
  thread parses the frames and feeds `_ingest_block("SYS", ...)`. At the fixed
  16 kHz the shared `_emit` skips resampling, exactly like the live-AEC path.

Import discipline: numpy and the stdlib only at module import time. sounddevice
(mac-only wheel) is imported lazily inside `_open_mic`, so this file imports
cleanly on Windows for the test suite, which drives the pure frame/header
parsers with synthetic bytes.
"""
import json
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from .capture_core import BLOCK_SECONDS, CaptureBase
from .devices_mac import resolve_loopback, resolve_mic

# ---- frozen helper contract (mac-port plan section 2.2) -------------------

HELPER_BINARY_NAME = "volksmond-audiotap"
HELPER_SAMPLE_RATE = 16000
HELPER_INVOCATION_ARGS = ["--sample-rate", str(HELPER_SAMPLE_RATE), "--mono"]

# Helper exit codes (contract): 0 clean stop (SIGTERM), 2 permission denied,
# 3 tap creation failed (pre-14.4 or API failure).
EXIT_CLEAN = 0
EXIT_PERMISSION_DENIED = 2
EXIT_TAP_FAILED = 3

# Sanity ceiling on a single PCM frame's byte count. The helper emits ~0.5 s
# blocks; 8 s of 16 kHz mono float32 is a generous ceiling that still catches a
# desynced/garbage length prefix ("bad magic") before we try to allocate it.
_MAX_FRAME_BYTES = HELPER_SAMPLE_RATE * 4 * 8

# How long to wait for the helper's initial handshake (the first control resolution:
# 'started', a terminal failure, OR the optional 'waiting_permission' pre-started
# event) before giving up. Kept short: a healthy helper resolves in well under a
# second when permission is already granted.
_HEADER_TIMEOUT_S = 8.0
# Once the helper emits 'waiting_permission' it is about to block on the first-run
# TCC dialog, which a human may take much longer than 8 s to answer. The deferred
# permission-wait path (see AudioCapture._await_system_tap) extends the wait for the
# eventual 'started' to this ceiling WITHOUT blocking mic/session startup.
_PERMISSION_WAIT_S = 120.0
# How long the reader waits for the consumer to register the SYS source and
# open the frame gate before it abandons streaming.
_GATE_TIMEOUT_S = 5.0

# _AudioTapHelper.start() outcomes.
START_STARTED = "started"   # handshake completed: SYS is live, frames may flow
START_FAILED = "failed"     # permission denied / protocol error / early exit / timeout
START_WAITING = "waiting"   # 'waiting_permission' seen; the 'started' handshake is still pending


class HelperProtocolError(RuntimeError):
    """The audiotap helper's stdout stream violated the frozen contract
    (torn frame, malformed header line, or an out-of-range length prefix)."""


# ---- pure stream parsers (unit-tested on Windows with synthetic bytes) -----

def _read_exactly(read, n):
    """Read exactly `n` bytes using the `read(k) -> bytes` callable.

    Returns the bytes, or None on a CLEAN EOF at a frame boundary (nothing read
    yet). Raises HelperProtocolError on a torn read (EOF partway through), which
    the reader treats as an unexpected helper exit.
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = read(n - len(buf))
        if not chunk:
            if not buf:
                return None
            raise HelperProtocolError(
                f"torn read: got {len(buf)} of {n} expected bytes before EOF"
            )
        buf.extend(chunk)
    return bytes(buf)


def _iter_pcm_frames(read, max_frame_bytes=_MAX_FRAME_BYTES):
    """Yield one float32 mono ndarray per length-prefixed PCM frame from `read`.

    Framing (frozen): uint32 LE byte count, then that many bytes of float32 LE
    samples. Stops cleanly at EOF on a frame boundary. Raises HelperProtocolError
    on a torn body, a length that is not a whole number of float32 samples, or a
    length past the sanity ceiling (all "stream desynced" conditions).
    """
    while True:
        header = _read_exactly(read, 4)
        if header is None:
            return
        (n,) = struct.unpack("<I", header)
        if n == 0:
            continue
        if n % 4 != 0:
            raise HelperProtocolError(
                f"frame length {n} is not a whole number of float32 samples"
            )
        if n > max_frame_bytes:
            raise HelperProtocolError(
                f"frame length {n} exceeds the {max_frame_bytes}-byte sanity "
                "ceiling (stream desync or bad length prefix)"
            )
        payload = _read_exactly(read, n)
        if payload is None:
            raise HelperProtocolError(
                f"frame body truncated: expected {n} bytes, stream ended at the header"
            )
        yield np.frombuffer(payload, dtype="<f4").astype(np.float32, copy=True)


def _parse_header_line(line):
    """Parse one stdout header line (bytes or str) as a JSON object.
    Raises HelperProtocolError if it is not valid JSON or not an object."""
    if isinstance(line, (bytes, bytearray)):
        try:
            line = bytes(line).decode("utf-8")
        except UnicodeDecodeError as e:
            raise HelperProtocolError(f"non-UTF-8 helper header line: {line!r}") from e
    line = line.strip()
    if not line:
        raise HelperProtocolError("empty helper header line")
    try:
        obj = json.loads(line)
    except (ValueError, TypeError) as e:
        raise HelperProtocolError(f"malformed helper header line: {line!r}") from e
    if not isinstance(obj, dict):
        raise HelperProtocolError(f"helper header line is not a JSON object: {line!r}")
    return obj


def _classify_header(obj):
    """Classify a parsed header dict into one of:
      ('format', {format?, rate?, channels?})  the pre-started format line
      ('started', None)                         the terminal go signal
      ('permission_denied', None)               terminal: TCC denied
      ('error', {code?, message?})              terminal: helper-reported error
      ('waiting_permission', None)              optional pre-started event: the helper
                                                is about to block on the first-run TCC
                                                dialog (CONTRACT.md 2.1)
      ('other', None)                           any UNKNOWN control event: tolerated and
                                                ignored for forward compatibility
    Split out so tests can cover the header state machine without a subprocess."""
    ev = obj.get("event")
    if ev == "started":
        return ("started", None)
    if ev == "permission_denied":
        return ("permission_denied", None)
    if ev == "waiting_permission":
        return ("waiting_permission", None)
    if ev == "error":
        return ("error", {"code": obj.get("code"), "message": obj.get("message")})
    if "format" in obj or "rate" in obj:
        meta = {}
        if obj.get("format") is not None:
            meta["format"] = obj["format"]
        if obj.get("rate") is not None:
            meta["rate"] = obj["rate"]
        if obj.get("channels") is not None:
            meta["channels"] = obj["channels"]
        return ("format", meta)
    return ("other", None)


def _validate_format(meta):
    """Validate a classified 'format' meta dict against the frozen contract
    (CONTRACT.md 2.1): format == 'f32le', rate a positive int, channels in {1, 2}.
    Returns (rate, channels). Raises HelperProtocolError on any violation. Split out
    so the handshake state machine's validation is unit-testable."""
    fmt = meta.get("format")
    if fmt is not None and fmt != "f32le":
        raise HelperProtocolError(f"unsupported helper format {fmt!r}, expected 'f32le'")
    rate = meta.get("rate")
    if not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0:
        raise HelperProtocolError(f"invalid helper sample rate {rate!r} (want a positive integer)")
    channels = meta.get("channels")
    if channels not in (1, 2):
        raise HelperProtocolError(f"invalid helper channel count {channels!r} (want 1 or 2)")
    return rate, channels


# ---- helper path resolution ------------------------------------------------

def _resolve_helper_path():
    """Locate the volksmond-audiotap binary, or return None if not found.

    Order: (1) the VOLKSMOND_AUDIOTAP env override (dev/CI, an explicit path);
    (2) bundled inside Volksmond.app at Contents/Resources/bin/ (plan 2.2);
    (3) a local SwiftPM release build next to the repo checkout.
    """
    override = os.environ.get("VOLKSMOND_AUDIOTAP")
    if override:
        p = Path(override)
        if p.is_file():
            return p
        print(f"[SYS] VOLKSMOND_AUDIOTAP={override!r} is not a file; ignoring it.", flush=True)

    candidates = []
    # TODO(mac-hw): confirm these resolve inside the notarised .app once WP-E
    # lands the BUNDLE datas/binaries entry; the exact bundled sub-path
    # (Contents/Resources/bin) is owned by WP-E and may need adjusting here.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "bin" / HELPER_BINARY_NAME)
        candidates.append(Path(meipass) / HELPER_BINARY_NAME)
    try:
        exe_dir = Path(sys.executable).resolve().parent
        # Inside a .app the executable lives in Contents/MacOS/; Resources is a
        # sibling of MacOS/.
        candidates.append(exe_dir.parent / "Resources" / "bin" / HELPER_BINARY_NAME)
    except Exception:
        pass
    repo_root = Path(__file__).resolve().parents[1]
    candidates.append(repo_root / "mac" / "volksmond-audiotap" / ".build" / "release" / HELPER_BINARY_NAME)

    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


# ---- the audiotap subprocess client ---------------------------------------

class _AudioTapHelper:
    """Owns the volksmond-audiotap subprocess and its stdout reader thread.

    `start()` spawns the process, drains the header synchronously, and returns
    one of START_STARTED / START_FAILED / START_WAITING within the initial
    handshake timeout. START_WAITING means the helper emitted the optional
    `{"event":"waiting_permission"}` line and is now blocked on the first-run TCC
    dialog; the caller must NOT block on this, and instead polls `wait_started`
    off the mic/session hot path (see AudioCapture._await_system_tap). The reader
    then BLOCKS on `begin()` so no SYS frame is delivered before the caller has
    registered the SYS source (avoids a KeyError race in `_ingest_block`).
    `on_frame(samples_1d)` is called per PCM frame.
    """

    def __init__(self, path, on_frame, log_prefix="[SYS]"):
        self._path = Path(path)
        self._on_frame = on_frame
        self._log_prefix = log_prefix
        self._proc = None
        self._reader_thread = None
        self._stderr_thread = None
        self._ready = threading.Event()   # header resolved to a terminal outcome (ok or fail)
        self._first = threading.Event()    # initial handshake settled: _ready OR waiting_permission
        self._waiting = threading.Event()  # 'waiting_permission' seen; 'started' still pending
        self._go = threading.Event()       # consumer registered SYS: frames may flow
        self._stop_event = threading.Event()
        self.started_ok = False
        self.rate = HELPER_SAMPLE_RATE
        self.channels = 1
        self.error = None

    def _signal_ready(self):
        """Mark the handshake terminally resolved (ok or fail) and settle the
        initial-handshake wait."""
        self._ready.set()
        self._first.set()

    def start(self, timeout=_HEADER_TIMEOUT_S):
        try:
            self._proc = subprocess.Popen(
                [str(self._path)] + HELPER_INVOCATION_ARGS,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception as e:
            self.error = f"could not launch {self._path.name}: {e}"
            return START_FAILED

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True, name="audiotap-stderr")
        self._stderr_thread.start()
        self._reader_thread = threading.Thread(
            target=self._run, daemon=True, name="audiotap-reader")
        self._reader_thread.start()

        # Wait for the initial handshake to settle: either a terminal outcome
        # (started/failure) or 'waiting_permission'. Whichever comes first.
        if not self._first.wait(timeout):
            self.error = self.error or f"helper sent no 'started' within {timeout:g}s"
            return START_FAILED
        if self._ready.is_set():
            return START_STARTED if self.started_ok else START_FAILED
        # waiting_permission arrived first: the 'started' handshake is still pending
        # behind the TCC dialog. The caller finishes it off the hot path.
        return START_WAITING

    def wait_started(self, timeout):
        """Block up to `timeout` for the handshake to reach 'started'. Returns True
        only if 'started' was reached and the helper has not been stopped. Used by
        the deferred permission-wait path so mic startup is never blocked on TCC."""
        self._ready.wait(timeout)
        return self.started_ok and not self._stop_event.is_set()

    def begin(self):
        """Open the frame gate: the SYS source is registered, frames may flow."""
        self._go.set()

    def _drain_stderr(self):
        # Human-readable helper logs. Draining also stops a full stderr pipe from
        # blocking the helper.
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                try:
                    line = raw.decode("utf-8", "replace").rstrip()
                except Exception:
                    line = repr(raw)
                if line:
                    print(f"{self._log_prefix} helper: {line}", flush=True)
        except Exception:
            pass

    def _run(self):
        proc = self._proc
        stdout = proc.stdout
        try:
            # Phase 1: newline-delimited JSON header state machine (CONTRACT.md 2.1).
            # A valid format line MUST precede 'started'; event:error and
            # permission_denied degrade immediately (no timeout wait); the optional
            # 'waiting_permission' extends the deadline; UNKNOWN events are tolerated
            # and ignored (forward compatibility).
            format_seen = False
            while True:
                line = stdout.readline()
                if not line:
                    self.error = self.error or (
                        "helper exited before sending 'started' "
                        f"(exit code {proc.poll()})"
                    )
                    self._signal_ready()
                    return
                try:
                    obj = _parse_header_line(line)
                except HelperProtocolError as e:
                    self.error = str(e)
                    self._signal_ready()
                    return
                kind, meta = _classify_header(obj)
                if kind == "format":
                    try:
                        self.rate, self.channels = _validate_format(meta)
                    except HelperProtocolError as e:
                        self.error = str(e)
                        self._signal_ready()
                        return
                    format_seen = True
                    continue
                if kind == "waiting_permission":
                    # The helper is about to block on the first-run TCC dialog. Signal
                    # the caller to stop waiting synchronously and extend the deadline;
                    # keep reading for the eventual terminal event.
                    self._waiting.set()
                    self._first.set()
                    continue
                if kind == "permission_denied":
                    self.error = (
                        "system-audio capture permission denied (grant Volksmond "
                        "audio capture in System Settings > Privacy & Security)"
                    )
                    self._signal_ready()
                    return
                if kind == "error":
                    code = meta.get("code") or "error"
                    msg = meta.get("message")
                    self.error = (f"system-audio helper reported {code}: {msg}"
                                  if msg else f"system-audio helper reported {code}")
                    self._signal_ready()
                    return
                if kind == "started":
                    if not format_seen:
                        self.error = "helper sent 'started' before the required format line"
                        self._signal_ready()
                        return
                    self.started_ok = True
                    self._signal_ready()
                    break
                # 'other' (UNKNOWN control event): tolerate and ignore for forward
                # compatibility (already logged on stderr).

            # Gate: wait for the consumer to register the SYS source.
            if not self._go.wait(_GATE_TIMEOUT_S):
                return

            # Phase 2: stream length-prefixed PCM frames.
            for arr in _iter_pcm_frames(stdout.read):
                if self._stop_event.is_set():
                    break
                self._on_frame(arr)
        except HelperProtocolError as e:
            # A mid-stream desync: SYS goes quiet, the session continues on MIC.
            # No auto-restart in v1 (the plan degrades rather than blocks).
            # TODO(mac-hw): observe real helper crash behaviour before deciding
            # whether a single restart attempt is worth the added complexity.
            if not self._ready.is_set():
                self.error = str(e)
                self._signal_ready()
            elif not self._stop_event.is_set():
                print(f"{self._log_prefix} audiotap stream desynced: {e}. "
                      "System audio stopped; the microphone continues.", flush=True)
        except Exception as e:
            if not self._ready.is_set():
                self.error = f"helper reader error: {e}"
                self._signal_ready()
            elif not self._stop_event.is_set():
                print(f"{self._log_prefix} audiotap reader stopped: {e}", flush=True)
        finally:
            # Every reader-thread exit path (clean EOF, protocol desync, callback
            # failure, gate timeout, header failure) fully reaps the child so it
            # never lingers as a zombie or blocks on a full stdout pipe.
            self._reap()

    def _reap(self):
        """Terminate and FULLY reap the child, closing its stdout/stderr pipes.
        Idempotent and safe to call from either the reader thread (on any exit) or
        stop(): terminate -> wait -> (kill -> wait) -> close handles. Centralises
        subprocess cleanup so no exit path leaves a zombie or a helper wedged on a
        full pipe."""
        proc = self._proc
        if proc is not None:
            # SIGTERM: the frozen contract's clean-stop signal (exit 0). Also unblocks
            # the reader's blocking read so it can exit.
            # TODO(mac-hw): confirm the helper handles SIGTERM as a clean stop
            # (exit 0) and releases the Core Audio tap promptly.
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3.0)
                except Exception:
                    pass
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

    def stop(self):
        self._stop_event.set()
        self._go.set()   # release the reader if it is still waiting at the gate
        # Terminate first so a reader blocked on read() unblocks and its own finally
        # reaps; then join, then a final idempotent reap in case the reader never ran.
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
        for t in (self._reader_thread, self._stderr_thread):
            if t is not None:
                try:
                    t.join(timeout=3.0)
                except Exception:
                    pass
        self._reap()


# ---- the capture backend ---------------------------------------------------

class AudioCapture(CaptureBase):
    def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15, on_chunk=None, t0=None, aec=False, record_raw_mic=False):
        super().__init__(mic_device=mic_device, loopback_device=loopback_device,
                         chunk_seconds=chunk_seconds, on_chunk=on_chunk, t0=t0,
                         aec=aec, record_raw_mic=record_raw_mic)
        self._mic_stream = None
        self._helper = None
        self._sys_await_thread = None   # deferred permission-wait completion thread

    def _open_sources(self):
        # Transactional: on ANY failure, stop/close everything opened so far (a
        # running helper subprocess, a background permission-wait thread, an open
        # mic stream) before propagating, so we never leave a half-open backend.
        try:
            self._open_sources_inner()
        except Exception:
            self._close_sources()
            raise

    def _open_sources_inner(self):
        # SYS first, then MIC (same order as the Windows backend). A SYS failure
        # degrades to mic-only and never aborts; a mic the user picked that will
        # not resolve OR open is surfaced as an error (matching capture_win exactly).
        loop_desc = None
        try:
            loop_desc = resolve_loopback(None, self.loopback_device_spec)
        except Exception as e:
            print(f"[SYS] system audio disabled: {e}", flush=True)

        if loop_desc is not None:
            try:
                self._open_system_tap()
            except Exception as e:
                print(f"[SYS] could not start system-audio tap: {e}. "
                      "Continuing with the microphone only.", flush=True)

        mic_desc = None
        try:
            mic_desc = resolve_mic(None, self.mic_device_spec)
        except Exception as e:
            if self.mic_device_spec is not None:
                # An EXPLICITLY chosen mic that will not resolve is fatal, exactly as
                # on Windows (capture_win): a user's explicit choice failing must
                # surface, not silently degrade to system-audio only. The outer
                # transactional wrapper cleans up anything already opened.
                raise RuntimeError(
                    f"could not select the requested microphone: {e}. Try a "
                    "different option in the Your microphone dropdown."
                ) from e
            print(f"[MIC] cannot resolve mic: {e}", flush=True)

        if mic_desc is not None:
            try:
                self._open_mic(mic_desc)
            except Exception as e:
                raise RuntimeError(
                    f"could not open microphone #{mic_desc['index']} "
                    f"'{mic_desc['name']}': {e}. Try a different option in the "
                    "Your microphone dropdown."
                ) from e

        # A deferred SYS handshake still in flight (first-run permission wait) counts
        # as a pending source, so we do NOT declare "nothing opened" while it runs.
        sys_pending = (self._sys_await_thread is not None
                       and self._sys_await_thread.is_alive())
        if not self._buffers and not sys_pending:
            raise RuntimeError(
                "no audio sources opened (both the system-audio tap and the "
                "microphone failed). Run --list-devices to see what is available."
            )

    def _open_system_tap(self):
        path = _resolve_helper_path()
        if path is None:
            raise RuntimeError(
                "system-audio helper binary not found. Set VOLKSMOND_AUDIOTAP to "
                "a built volksmond-audiotap, or ship it in the app bundle."
            )
        helper = _AudioTapHelper(
            path,
            on_frame=lambda samples: self._ingest_block("SYS", samples.reshape(-1, 1)),
        )
        status = helper.start()

        if status == START_STARTED:
            # Permission already granted: register SYS at the helper's declared rate
            # (fixed 16 kHz per contract) BEFORE opening the frame gate, so the first
            # frame has a buffer to land in. mono -> 1 channel.
            self._helper = helper
            self._register_source("SYS", helper.rate, helper.channels)
            helper.begin()
            print(f"[SYS] system-audio tap started via {path.name} "
                  f"@ {helper.rate} Hz x{helper.channels}ch", flush=True)
            return

        if status == START_WAITING:
            # First-run TCC dialog: the helper emitted 'waiting_permission' and is now
            # blocked awaiting the grant. Do NOT block session/mic startup for that
            # (possibly long) human wait. Keep the helper, finish the handshake on a
            # background thread, and let _open_sources continue straight to the mic.
            # The SYS-registered-before-gate invariant is preserved: the await thread
            # registers SYS (and its chunker) BEFORE calling begin().
            self._helper = helper
            self._sys_await_thread = threading.Thread(
                target=self._await_system_tap, args=(helper, path),
                daemon=True, name="audiotap-await")
            self._sys_await_thread.start()
            print("[SYS] waiting for system-audio capture permission; the microphone "
                  "starts now and system audio joins once permission is granted.",
                  flush=True)
            return

        # START_FAILED: degrade to mic-only.
        try:
            helper.stop()
        except Exception:
            pass
        raise RuntimeError(helper.error or "the system-audio tap did not start")

    def _await_system_tap(self, helper, path):
        # Runs off the mic/session hot path. Finish the deferred handshake within the
        # extended permission ceiling; on success register SYS + its chunker and open
        # the frame gate; on failure/timeout degrade to mic-only.
        if not helper.wait_started(_PERMISSION_WAIT_S):
            print(f"[SYS] system audio not started: "
                  f"{helper.error or 'timed out waiting for capture permission'}. "
                  "Continuing with the microphone only.", flush=True)
            try:
                helper.stop()
            except Exception:
                pass
            return
        # Register SYS and spawn its chunker BEFORE opening the gate so the first SYS
        # frame has both a buffer and a drainer. start() has already spawned the MIC
        # chunker; SYS registered late needs its own (start() only saw MIC).
        self._register_source("SYS", helper.rate, helper.channels)
        self._spawn_sys_chunker()
        helper.begin()
        print(f"[SYS] system-audio tap started via {path.name} "
              f"@ {helper.rate} Hz x{helper.channels}ch (after permission grant)",
              flush=True)

    def _spawn_sys_chunker(self):
        # Spawn the per-source chunker for a SYS source registered AFTER start() ran
        # (the deferred permission-wait path). Mirrors the chunker spawn in
        # CaptureBase.start(), which only sees sources registered before it runs.
        t = threading.Thread(target=self._chunker, args=("SYS",),
                             daemon=True, name="chunker-SYS")
        t.start()
        self._workers.append(t)

    def _open_mic(self, desc):
        import sounddevice as sd

        rate = desc["rate"]
        max_ch = desc["channels"]
        block = int(rate * BLOCK_SECONDS)

        # Channel-count fallback, highest-first, mirroring capture_win: some
        # aggregate/virtual Core Audio devices advertise more input channels
        # than they will actually open. The first count that opens wins and its
        # value is kept so the callback reshapes correctly.
        candidates = []
        for c in (max_ch, 2, 1):
            if 1 <= c <= max_ch and c not in candidates:
                candidates.append(c)

        last_err = None
        for channels in candidates:
            self._register_source("MIC", rate, channels)
            ch = channels

            def callback(indata, frames, time_info, status, _ch=ch, _self=self):
                try:
                    arr = np.asarray(indata, dtype=np.float32)
                    if arr.ndim == 1:
                        arr = arr.reshape(-1, 1)
                    # sounddevice reuses indata's buffer between callbacks, so
                    # copy: the shared core keeps the reference until the chunker
                    # concatenates it.
                    _self._ingest_block("MIC", arr.copy())
                except Exception as e:
                    print(f"[MIC] callback error: {e}", flush=True)

            stream = None
            try:
                stream = sd.InputStream(
                    device=desc["index"],
                    channels=channels,
                    samplerate=rate,
                    dtype="float32",
                    blocksize=block,
                    callback=callback,
                )
                stream.start()
            except Exception as e:
                last_err = e
                # A stream that constructed but whose start() failed must be closed
                # before trying the next channel count, or it leaks a PortAudio stream.
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                for d in (self._buffers, self._buffer_counts, self._buffer_locks,
                          self._rates, self._channels):
                    d.pop("MIC", None)
                continue

            self._mic_stream = stream
            print(f"[MIC] opened '{desc['name']}' @ {rate} Hz x{channels}ch "
                  f"(device #{desc['index']})", flush=True)
            return

        raise last_err if last_err is not None else RuntimeError(
            f"could not open MIC at any channel count (tried {candidates})"
        )

    def _close_sources(self):
        # Stop delivering blocks: close the mic stream and terminate the helper.
        # Ordering parity with capture_win, which stops its streams here and does
        # host-API teardown in _release_backend; the mac backend has no host-API
        # singleton to tear down, so both live here.
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None
        if self._helper is not None:
            try:
                self._helper.stop()
            except Exception:
                pass
            self._helper = None
        # Join the deferred permission-wait thread, if any: helper.stop() above set the
        # stop flag, so its wait_started returns False and it exits without registering.
        if self._sys_await_thread is not None:
            try:
                self._sys_await_thread.join(timeout=3.0)
            except Exception:
                pass
            self._sys_await_thread = None
