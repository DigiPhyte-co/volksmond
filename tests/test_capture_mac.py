"""macOS capture backend tests, runnable on Windows with fakes.

Covers the parts of the mac backend that are pure logic and therefore
verifiable without a Mac, real audio, or the Swift helper:

- the frozen-contract stdout parsers (frame framing incl. torn/partial reads
  and desynced "bad magic" length prefixes; the JSON header state machine),
- the _AudioTapHelper header handshake + gated frame delivery, driven by a
  fake subprocess whose stdout is synthetic bytes,
- devices_mac's mic list / default resolution and the synthetic system-audio
  loopback entry (sounddevice mocked out),
- the platform selectors routing darwin -> capture_mac / devices_mac.

Anything needing real Core Audio, a live mic, the actual helper subprocess or
TCC permission is out of scope here and marked needs-mac in the WP-C report.

Run:  python tests/test_capture_mac.py   (from the project root; exit 0 = pass)
"""
import importlib
import io
import os
import struct
import sys
import threading
import time

import numpy as np

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import capture_mac, devices_mac
from live_transcribe.capture_mac import (
    HelperProtocolError,
    _classify_header,
    _iter_pcm_frames,
    _parse_header_line,
    _parse_stats_line,
    _read_exactly,
)


# ---- helpers ---------------------------------------------------------------

def _encode_frame(samples):
    payload = np.asarray(samples, dtype="<f4").tobytes()
    return struct.pack("<I", len(payload)) + payload


def _reader_of(data):
    """A read(n) callable over a byte string, EOF -> b'' (like a pipe)."""
    return io.BytesIO(data).read


class _FakeProc:
    """Minimal subprocess stand-in: stdout/stderr are BytesIO, the control
    methods are no-ops. Lets us drive _AudioTapHelper._run without spawning."""

    def __init__(self, stdout_bytes=b"", stderr_bytes=b"", returncode=0):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(stdout_bytes)
        self.stderr = io.BytesIO(stderr_bytes)
        self._rc = returncode

    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        return self._rc

    def terminate(self):
        pass

    def kill(self):
        pass


class _FakeSD:
    """Fake sounddevice module: query_devices() + default.device."""

    def __init__(self, devices, default_in=-1):
        self._devices = devices

        class _Default:
            pass

        self.default = _Default()
        self.default.device = (default_in, -1)

    def query_devices(self):
        return self._devices


_MICS = [
    {"name": "MacBook Pro Microphone", "max_input_channels": 1, "max_output_channels": 0, "default_samplerate": 48000.0},
    {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0},
    {"name": "USB Audio CODEC", "max_input_channels": 2, "max_output_channels": 0, "default_samplerate": 44100.0},
]


def _with_fake_sd(devices, default_in=-1):
    """Install a fake sounddevice in sys.modules; return a restore() callable."""
    prev = sys.modules.get("sounddevice")
    sys.modules["sounddevice"] = _FakeSD(devices, default_in)

    def restore():
        if prev is not None:
            sys.modules["sounddevice"] = prev
        else:
            sys.modules.pop("sounddevice", None)

    return restore


# ---- frame framing ---------------------------------------------------------

def test_frames_roundtrip():
    data = _encode_frame([0.1, 0.2, 0.3]) + _encode_frame([0.4, 0.5])
    out = list(_iter_pcm_frames(_reader_of(data)))
    assert len(out) == 2, out
    assert np.allclose(out[0], [0.1, 0.2, 0.3], atol=1e-6)
    assert np.allclose(out[1], [0.4, 0.5], atol=1e-6)
    print("  OK  well-formed PCM frames decode to the right float32 samples")


def test_frames_clean_eof_at_boundary():
    # Two frames then EOF exactly on a boundary: no error, just stops.
    data = _encode_frame([1.0]) + _encode_frame([2.0])
    out = list(_iter_pcm_frames(_reader_of(data)))
    assert len(out) == 2
    print("  OK  clean EOF on a frame boundary ends the stream without error")


def test_frames_empty_frame_skipped():
    data = struct.pack("<I", 0) + _encode_frame([7.0])
    out = list(_iter_pcm_frames(_reader_of(data)))
    assert len(out) == 1 and np.allclose(out[0], [7.0], atol=1e-6)
    print("  OK  a zero-length frame is skipped, streaming continues")


def test_frames_torn_body_raises():
    # Header claims 16 bytes (4 samples) but only 8 bytes of body follow.
    data = struct.pack("<I", 16) + struct.pack("<ff", 1.0, 2.0)
    try:
        list(_iter_pcm_frames(_reader_of(data)))
    except HelperProtocolError as e:
        assert "torn" in str(e) or "truncated" in str(e), e
        print("  OK  a torn frame body raises HelperProtocolError")
        return
    raise AssertionError("torn frame body did not raise")


def test_frames_partial_header_raises():
    # Only 2 of the 4 length-prefix bytes present, then EOF: a torn read.
    data = b"\x10\x00"
    try:
        list(_iter_pcm_frames(_reader_of(data)))
    except HelperProtocolError as e:
        assert "torn" in str(e), e
        print("  OK  a partial length prefix raises HelperProtocolError")
        return
    raise AssertionError("partial header did not raise")


def test_frames_bad_magic_not_multiple_of_four():
    data = struct.pack("<I", 7) + b"\x00" * 7
    try:
        list(_iter_pcm_frames(_reader_of(data)))
    except HelperProtocolError as e:
        assert "float32" in str(e), e
        print("  OK  a non-multiple-of-4 length (bad magic) raises")
        return
    raise AssertionError("bad length did not raise")


def test_frames_bad_magic_over_ceiling():
    # A garbage/desynced length prefix far past the sanity ceiling must be
    # rejected BEFORE we try to read (and allocate) it.
    huge = capture_mac._MAX_FRAME_BYTES + 4
    data = struct.pack("<I", huge)  # deliberately no body: must reject on length
    try:
        list(_iter_pcm_frames(_reader_of(data)))
    except HelperProtocolError as e:
        assert "ceiling" in str(e), e
        print("  OK  an over-ceiling length prefix (bad magic) raises before read")
        return
    raise AssertionError("over-ceiling length did not raise")


def test_read_exactly_partial_then_full():
    # A read() that dribbles bytes out (partial reads) must still assemble.
    chunks = [b"ab", b"cd", b"ef"]

    def dribble(n):
        return chunks.pop(0) if chunks else b""

    assert _read_exactly(dribble, 6) == b"abcdef"
    print("  OK  _read_exactly reassembles across partial reads")


def test_read_exactly_clean_eof_returns_none():
    assert _read_exactly(lambda n: b"", 4) is None
    print("  OK  _read_exactly returns None on a clean boundary EOF")


# ---- header state machine --------------------------------------------------

def test_parse_header_line_ok():
    obj = _parse_header_line(b'{"format":"f32le","rate":16000,"channels":1}\n')
    assert obj["rate"] == 16000 and obj["channels"] == 1
    print("  OK  a valid header line parses to a dict")


def test_parse_header_line_malformed():
    for bad in (b"not json\n", b"[1,2,3]\n", b"\n"):
        try:
            _parse_header_line(bad)
        except HelperProtocolError:
            continue
        raise AssertionError(f"malformed header line did not raise: {bad!r}")
    print("  OK  malformed / non-object / empty header lines raise")


def test_classify_header():
    assert _classify_header({"format": "f32le", "rate": 16000, "channels": 1}) == (
        "format", {"format": "f32le", "rate": 16000, "channels": 1})
    assert _classify_header({"event": "started"}) == ("started", None)
    assert _classify_header({"event": "permission_denied"}) == ("permission_denied", None)
    assert _classify_header({"event": "waiting_permission"}) == ("waiting_permission", None)
    assert _classify_header({"event": "error", "code": "tap_failed", "message": "boom"}) == (
        "error", {"code": "tap_failed", "message": "boom"})
    # An unknown control event is 'other' (tolerated + ignored for forward compat).
    assert _classify_header({"event": "level", "db": -30}) == ("other", None)
    print("  OK  header classification covers format/started/denied/waiting/error/other")


def test_validate_format():
    from live_transcribe.capture_mac import _validate_format
    assert _validate_format({"format": "f32le", "rate": 16000, "channels": 1}) == (16000, 1)
    assert _validate_format({"format": "f32le", "rate": 48000, "channels": 1}) == (48000, 1)
    for bad in ({"format": "s16le", "rate": 16000, "channels": 1},   # wrong format
                {"rate": 0, "channels": 1},                          # non-positive rate
                {"rate": 16000, "channels": 2},                      # stereo: rejected (M3, --mono only)
                {"rate": 16000, "channels": 3},                      # bad channel count
                {"rate": 16000},                                     # missing channels
                {"channels": 1}):                                    # missing rate
        try:
            _validate_format(bad)
        except HelperProtocolError:
            continue
        raise AssertionError(f"_validate_format accepted bad meta: {bad!r}")
    print("  OK  _validate_format enforces f32le / positive rate / channels == 1 (mono only)")


def test_validate_format_rejects_stereo():
    # M3: the PCM path reshapes flat samples to (-1, 1) mono, so a stereo (interleaved)
    # stream would be misread as double-rate mono. The tap is always invoked --mono, so a
    # channels != 1 format line is a contract violation we reject (degrading SYS to mic-only)
    # rather than silently mangle.
    from live_transcribe.capture_mac import _validate_format
    for ch in (2, 4, 0):
        try:
            _validate_format({"format": "f32le", "rate": 16000, "channels": ch})
        except HelperProtocolError as e:
            assert "channel" in str(e).lower(), e
            continue
        raise AssertionError(f"_validate_format accepted a non-mono channel count {ch}")
    print("  OK  _validate_format rejects any non-mono channel count (M3)")


# ---- STATS diagnostics (M5) ------------------------------------------------

def test_parse_stats_line():
    assert _parse_stats_line("STATS seq=1 dropped=0 host_ms=10") == {
        "seq": 1, "dropped": 0, "host_ms": 10}
    # Extra key=int tokens tolerated (forward compat); key order irrelevant.
    assert _parse_stats_line("STATS dropped=7 seq=42 host_ms=3 extra=9")["dropped"] == 7
    for bad in ("STATS",                       # no fields
                "STATS seq=1 dropped=0",       # missing host_ms
                "STATS seq=1 dropped=x host_ms=3",  # non-integer value
                "STATS seq=1 dropped host_ms=3",    # token without '='
                "not a stats line",            # wrong prefix
                "STATSseq=1 dropped=0 host_ms=3"):  # prefix not followed by a space
        assert _parse_stats_line(bad) is None, bad
    print("  OK  _parse_stats_line parses well-formed STATS lines, rejects malformed ones")


def test_stderr_stats_warns_on_dropped_increase():
    import contextlib
    stderr = (b"STATS seq=1 dropped=0 host_ms=10\n"
              b"helper: starting up\n"                 # non-STATS: echoed as a helper log
              b"STATS seq=2 dropped=0 host_ms=20\n"    # no increase: no warning
              b"STATS seq=3 dropped=5 host_ms=30\n"    # increase 0 -> 5: warn
              b"STATS seq=4 dropped=5 host_ms=40\n"    # no increase: no warning
              b"STATS totally malformed line\n"        # malformed STATS: ignored, no warning
              b"STATS seq=5 dropped=9 host_ms=50\n")   # increase 5 -> 9: warn
    h = capture_mac._AudioTapHelper("audiotap", on_frame=lambda a: None)
    h._proc = _FakeProc(stderr_bytes=stderr)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        h._drain_stderr()
    out = buf.getvalue()
    assert out.count("system audio may glitch") == 2, out   # exactly the two increases
    assert "helper: starting up" in out, out                # non-STATS handling preserved
    print("  OK  _drain_stderr warns only when the helper's dropped count increases (M5)")


# ---- _AudioTapHelper handshake + gated frames ------------------------------

def _run_helper(proc):
    h = capture_mac._AudioTapHelper("audiotap", on_frame=lambda a: None)
    h._proc = proc
    h._stderr_thread = threading.Thread(target=h._drain_stderr, daemon=True)
    h._stderr_thread.start()
    h._reader_thread = threading.Thread(target=h._run, daemon=True)
    h._reader_thread.start()
    return h


def test_helper_started_then_frames():
    header = (b'{"format":"f32le","rate":16000,"channels":1}\n'
              b'{"event":"started"}\n')
    body = _encode_frame([0.1, 0.2]) + _encode_frame([0.3])
    got = []
    h = capture_mac._AudioTapHelper("audiotap", on_frame=lambda a: got.append(a))
    h._proc = _FakeProc(header + body)
    h._stderr_thread = threading.Thread(target=h._drain_stderr, daemon=True)
    h._stderr_thread.start()
    h._reader_thread = threading.Thread(target=h._run, daemon=True)
    h._reader_thread.start()

    assert h._ready.wait(2.0), "helper never signalled ready"
    assert h.started_ok is True
    assert h.rate == 16000 and h.channels == 1
    # Frames must NOT be delivered until the gate opens.
    assert got == [], "frames leaked before begin()"
    h.begin()
    h._reader_thread.join(2.0)
    samples = np.concatenate([a.reshape(-1) for a in got]) if got else np.array([])
    assert samples.shape[0] == 3, samples
    assert np.allclose(samples, [0.1, 0.2, 0.3], atol=1e-6)
    print("  OK  helper: header handshake, gate holds frames, then all frames flow")


def test_helper_permission_denied():
    h = _run_helper(_FakeProc(b'{"event":"permission_denied"}\n'))
    assert h._ready.wait(2.0)
    assert h.started_ok is False
    assert h.error and "denied" in h.error.lower()
    print("  OK  helper: permission_denied sets started_ok False with a clear error")


def test_helper_early_exit_before_started():
    h = _run_helper(_FakeProc(b""))  # immediate EOF, no header
    assert h._ready.wait(2.0)
    assert h.started_ok is False
    assert h.error
    print("  OK  helper: EOF before 'started' fails cleanly with an error")


def test_helper_unexpected_eof_after_started_fires_on_failed():
    # H1: the helper handshakes, delivers a frame, then its stdout hits EOF with no stop
    # requested -> an unexpected post-'started' end must fire on_failed (the mic keeps going).
    header = (b'{"format":"f32le","rate":16000,"channels":1}\n'
              b'{"event":"started"}\n')
    body = _encode_frame([0.1])
    failed = threading.Event()
    h = capture_mac._AudioTapHelper("audiotap", on_frame=lambda a: None,
                                    on_failed=failed.set)
    h._proc = _FakeProc(header + body)   # stdout EOFs right after the single frame
    h._reader_thread = threading.Thread(target=h._run, daemon=True)
    h._reader_thread.start()
    assert h._ready.wait(2.0)
    h.begin()   # open the gate: the frame flows, then EOF is seen as an unexpected exit
    h._reader_thread.join(2.0)
    assert failed.wait(2.0), "on_failed did not fire on an unexpected post-started EOF"
    print("  OK  helper: an unexpected EOF after 'started' fires on_failed (H1)")


def test_helper_clean_stop_does_not_fire_on_failed():
    # H1: a deliberate stop() (reader blocked on read, stop flag set, stdout closed like a
    # SIGTERM would) must NOT look like a failure - on_failed stays unfired.
    r_fd, w_fd = os.pipe()
    rf = os.fdopen(r_fd, "rb", buffering=0)
    wf = os.fdopen(w_fd, "wb", buffering=0)
    proc = _FakeProc()
    proc.stdout = rf
    failed = threading.Event()
    h = capture_mac._AudioTapHelper("audiotap", on_frame=lambda a: None, on_failed=failed.set)
    h._proc = proc
    h._reader_thread = threading.Thread(target=h._run, daemon=True)
    h._reader_thread.start()
    wf.write(b'{"format":"f32le","rate":16000,"channels":1}\n')
    wf.write(b'{"event":"started"}\n')
    assert h._ready.wait(2.0)
    h.begin()
    time.sleep(0.1)        # let the reader reach its blocking read in Phase 2
    h._stop_event.set()    # simulate stop(): request shutdown...
    wf.close()             # ...and close the helper's stdout (EOF), as terminate would
    h._reader_thread.join(2.0)
    assert not failed.is_set(), "on_failed fired on a clean stop"
    print("  OK  helper: a clean stop does not fire on_failed (H1)")


def test_stop_closes_helper_stdin():
    # M4: stop() closes the helper's stdin (clean-shutdown EOF signal, additive to SIGTERM).
    h = capture_mac._AudioTapHelper("audiotap", on_frame=lambda a: None)
    proc = _FakeProc()
    h._proc = proc
    h.stop()
    assert proc.stdin.closed, "stop() should close the helper's stdin"
    print("  OK  stop() closes the helper's stdin as a clean-shutdown signal (M4)")


# ---- negative handshake state machine (fix 10) -----------------------------

def test_helper_started_before_format_rejected():
    # 'started' with no preceding format line violates the contract.
    h = _run_helper(_FakeProc(b'{"event":"started"}\n'))
    assert h._ready.wait(2.0)
    assert h.started_ok is False
    assert h.error and "format" in h.error.lower()
    print("  OK  helper: 'started' before the format line is rejected")


def test_helper_error_event_degrades_immediately():
    header = (b'{"format":"f32le","rate":16000,"channels":1}\n'
              b'{"event":"error","code":"tap_failed","message":"boom"}\n')
    h = _run_helper(_FakeProc(header))
    assert h._ready.wait(2.0)
    assert h.started_ok is False
    assert h.error and "tap_failed" in h.error
    print("  OK  helper: event:error degrades immediately with the reported code")


def test_helper_bad_channels_in_format_rejected():
    header = (b'{"format":"f32le","rate":16000,"channels":3}\n'
              b'{"event":"started"}\n')
    h = _run_helper(_FakeProc(header))
    assert h._ready.wait(2.0)
    assert h.started_ok is False
    assert h.error and "channel" in h.error.lower()
    print("  OK  helper: an out-of-range channel count in the format line is rejected")


def test_helper_unknown_event_tolerated_then_started():
    # An unknown pre-started control event must be ignored (forward compat), and a
    # valid format+started still resolves to a live stream.
    header = (b'{"format":"f32le","rate":16000,"channels":1}\n'
              b'{"event":"level","db":-30}\n'
              b'{"event":"started"}\n')
    body = _encode_frame([0.5])
    got = []
    h = capture_mac._AudioTapHelper("audiotap", on_frame=lambda a: got.append(a))
    h._proc = _FakeProc(header + body)
    h._stderr_thread = threading.Thread(target=h._drain_stderr, daemon=True)
    h._stderr_thread.start()
    h._reader_thread = threading.Thread(target=h._run, daemon=True)
    h._reader_thread.start()
    assert h._ready.wait(2.0)
    assert h.started_ok is True
    h.begin()
    h._reader_thread.join(2.0)
    assert got and np.allclose(got[0].reshape(-1), [0.5], atol=1e-6)
    print("  OK  helper: an unknown control event is ignored, started still resolves")


# ---- waiting_permission extended deadline (fix 3) --------------------------

def test_helper_waiting_permission_extends_deadline():
    # Drive the reader over a real OS pipe so 'started' can arrive LATER than
    # 'waiting_permission', proving the started deadline is extended (not resolved
    # by waiting_permission) and that wait_started only returns True after started.
    r_fd, w_fd = os.pipe()
    rf = os.fdopen(r_fd, "rb", buffering=0)
    wf = os.fdopen(w_fd, "wb", buffering=0)
    proc = _FakeProc()
    proc.stdout = rf   # replace the empty BytesIO with the pipe read end
    h = capture_mac._AudioTapHelper("audiotap", on_frame=lambda a: None)
    h._proc = proc
    h._stderr_thread = threading.Thread(target=h._drain_stderr, daemon=True)
    h._stderr_thread.start()
    h._reader_thread = threading.Thread(target=h._run, daemon=True)
    h._reader_thread.start()

    wf.write(b'{"format":"f32le","rate":16000,"channels":1}\n')
    wf.write(b'{"event":"waiting_permission"}\n')
    assert h._waiting.wait(2.0), "waiting_permission was not observed"
    assert h._first.is_set(), "the initial handshake did not settle on waiting_permission"
    assert not h._ready.is_set(), "the started deadline resolved before 'started' arrived"
    assert not h.wait_started(0.2), "wait_started true before 'started'"

    wf.write(b'{"event":"started"}\n')
    assert h._ready.wait(2.0), "'started' after the wait was not observed"
    assert h.started_ok is True
    assert h.wait_started(2.0) is True
    h.begin()
    wf.close()   # EOF -> reader exits its stream loop and reaps
    h._reader_thread.join(2.0)
    print("  OK  helper: waiting_permission extends the deadline, then 'started' resolves")


class _FakeHelper:
    """Stand-in for _AudioTapHelper that lets a test drive the deferred permission
    path: start() reports a fixed outcome; wait_started blocks until signalled."""

    def __init__(self, status, rate=16000, channels=1):
        self._status = status
        self.rate = rate
        self.channels = channels
        self.error = None
        self.permission_denied = False
        self.began = False
        self.stopped = False
        self._started = threading.Event()

    def start(self):
        return self._status

    def wait_started(self, timeout):
        self._started.wait(timeout)
        return self._started.is_set() and not self.stopped

    def signal_started(self):
        self._started.set()

    def begin(self):
        self.began = True
        ev = getattr(self, "_events", None)
        if ev is not None:
            ev.append("begin")

    def stop(self):
        self.stopped = True
        self._started.set()


class _FakeWorker:
    """Stand-in for a running LiveAEC worker, to drive the late-grant in-place attach path
    without LiveKit. Records attach_far so a test can prove the worker is upgraded IN PLACE
    (same object, no swap)."""

    def __init__(self, has_far=False):
        self.has_far = has_far
        self.bypass = True
        self.attached = None   # (far_rate, on_far) once attach_far is called
        self._events = None

    def attach_far(self, far_rate, on_far):
        self.attached = (far_rate, on_far)
        self.has_far = True
        if self._events is not None:
            self._events.append("attach_far")


def test_open_system_tap_waiting_defers_registration():
    # WAITING must NOT block: _open_system_tap returns with SYS unregistered and a
    # background thread pending; once the grant lands, that thread registers SYS,
    # spawns its chunker, and opens the gate (SYS-registered-before-gate preserved).
    cap = capture_mac.AudioCapture()
    cap._t0 = 0.0
    fake = _FakeHelper(capture_mac.START_WAITING)
    orig_resolve = capture_mac._resolve_helper_path
    orig_cls = capture_mac._AudioTapHelper
    capture_mac._resolve_helper_path = lambda: capture_mac.Path("fake/volksmond-audiotap")
    capture_mac._AudioTapHelper = lambda path, on_frame, **kw: fake
    try:
        cap._open_system_tap()   # must return promptly, not block
        assert "SYS" not in cap._buffers, "SYS registered before the grant (should defer)"
        assert cap._helper is fake
        assert not fake.began, "gate opened before the grant"
        fake.signal_started()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if "SYS" in cap._buffers and fake.began:
                break
            time.sleep(0.02)
        assert "SYS" in cap._buffers, "await thread never registered SYS after the grant"
        assert fake.began, "await thread never opened the frame gate after the grant"
        # A SYS chunker worker must have been spawned (start() only saw MIC).
        assert any(w.name == "chunker-SYS" for w in cap._workers), "no SYS chunker spawned"
    finally:
        capture_mac._resolve_helper_path = orig_resolve
        capture_mac._AudioTapHelper = orig_cls
        cap._stop_event.set()   # let the spawned chunker flush (empty) and exit
    print("  OK  _open_system_tap: WAITING defers SYS registration + chunker to the grant")


def _patch_helper(fake):
    """Install fake _resolve_helper_path + _AudioTapHelper; return a restore() callable."""
    orig_resolve = capture_mac._resolve_helper_path
    orig_cls = capture_mac._AudioTapHelper
    capture_mac._resolve_helper_path = lambda: capture_mac.Path("fake/volksmond-audiotap")
    capture_mac._AudioTapHelper = lambda path, on_frame, **kw: fake

    def restore():
        capture_mac._resolve_helper_path = orig_resolve
        capture_mac._AudioTapHelper = orig_cls

    return restore


def test_open_system_tap_started_sets_active():
    # H1: START_STARTED registers SYS, opens the gate, and reports sys_state='active'.
    cap = capture_mac.AudioCapture()
    cap._t0 = 0.0
    assert cap.sys_state == "disabled"   # initial value before any SYS attempt
    fake = _FakeHelper(capture_mac.START_STARTED)
    restore = _patch_helper(fake)
    try:
        cap._open_system_tap()
        assert cap.sys_state == "active", cap.sys_state
        assert "SYS" in cap._buffers
        assert fake.began
    finally:
        restore()
    print("  OK  _open_system_tap: START_STARTED sets sys_state='active' (H1)")


def test_open_system_tap_failed_denied_sets_state_and_raises():
    # H1: START_FAILED from a TCC denial sets sys_state='permission_denied' and raises so the
    # caller degrades to mic-only; SYS is left unregistered.
    cap = capture_mac.AudioCapture()
    cap._t0 = 0.0
    fake = _FakeHelper(capture_mac.START_FAILED)
    fake.permission_denied = True
    fake.error = "system-audio capture permission denied"
    restore = _patch_helper(fake)
    try:
        raised = False
        try:
            cap._open_system_tap()
        except RuntimeError:
            raised = True
        assert raised, "START_FAILED should raise to the caller"
        assert cap.sys_state == "permission_denied", cap.sys_state
        assert "SYS" not in cap._buffers
        assert fake.stopped
    finally:
        restore()
    print("  OK  _open_system_tap: START_FAILED (denied) sets sys_state='permission_denied', raises (H1)")


def test_await_system_tap_denial_after_wait_sets_state():
    # H1: on the deferred path, a denial/timeout after the extended wait sets sys_state and
    # stops the helper (mic continues). Shorten the ceiling so the test does not block.
    cap = capture_mac.AudioCapture()
    cap._t0 = 0.0
    fake = _FakeHelper(capture_mac.START_WAITING)
    fake.permission_denied = True   # denial arrives during the wait
    orig_wait = capture_mac._PERMISSION_WAIT_S
    capture_mac._PERMISSION_WAIT_S = 0.05
    try:
        cap._await_system_tap(fake, capture_mac.Path("fake/volksmond-audiotap"))
    finally:
        capture_mac._PERMISSION_WAIT_S = orig_wait
    assert cap.sys_state == "permission_denied", cap.sys_state
    assert fake.stopped
    assert "SYS" not in cap._buffers
    print("  OK  _await_system_tap: a denial after the wait sets sys_state='permission_denied' (H1)")


def test_await_system_tap_teardown_is_not_a_failure():
    # H1: if stop() woke the wait during teardown, the deferred path must NOT mark it failed.
    cap = capture_mac.AudioCapture()
    cap._t0 = 0.0
    fake = _FakeHelper(capture_mac.START_WAITING)
    fake.stop()           # stop() already ran: wait_started returns promptly, False (teardown)
    cap.sys_state = "pending"
    cap._await_system_tap(fake, capture_mac.Path("fake/volksmond-audiotap"))
    assert cap.sys_state == "pending", cap.sys_state   # unchanged, not 'failed'
    assert "SYS" not in cap._buffers
    print("  OK  _await_system_tap: a teardown-driven wake is not treated as a SYS failure (H1)")


# ---- late-grant AEC engage: ordering, in-place attach, gate timeout --------

def test_await_opens_gate_before_upgrade():
    # Fix 1: begin() (open the frame gate) MUST happen before the AEC engage, so a slow engage
    # can never delay begin() past the reader's ~5s gate deadline (which would get the helper
    # reaped while the UI still reports 'active').
    events = []
    cap = capture_mac.AudioCapture()
    cap._t0 = 0.0
    worker = _FakeWorker(has_far=False)
    worker._events = events
    cap._live_aec = worker
    fake = _FakeHelper(capture_mac.START_WAITING)   # rate = 16000 = TARGET_RATE
    fake._events = events
    fake.signal_started()
    try:
        cap._await_system_tap(fake, capture_mac.Path("fake/volksmond-audiotap"))
        assert events == ["begin", "attach_far"], events   # gate opened BEFORE the engage
        assert cap._live_aec is worker                      # attached in place, not swapped
    finally:
        cap._stop_event.set()
    print("  OK  _await_system_tap: opens the gate BEFORE the AEC engage (Fix 1)")


def test_gate_timeout_fires_failure():
    # Fix 1: if begin() is never called, the reader's post-'started' gate wait times out and must
    # surface a SYS failure (fire on_failed), never silently die while the UI still says 'active'.
    header = (b'{"format":"f32le","rate":16000,"channels":1}\n'
              b'{"event":"started"}\n')
    failed = threading.Event()
    orig = capture_mac._GATE_TIMEOUT_S
    capture_mac._GATE_TIMEOUT_S = 0.2
    try:
        h = capture_mac._AudioTapHelper("audiotap", on_frame=lambda a: None, on_failed=failed.set)
        h._proc = _FakeProc(header)   # handshake only; we deliberately never call begin()
        h._reader_thread = threading.Thread(target=h._run, daemon=True)
        h._reader_thread.start()
        assert h._ready.wait(2.0)
        assert failed.wait(2.0), "on_failed did not fire on a post-started gate timeout"
    finally:
        capture_mac._GATE_TIMEOUT_S = orig
    print("  OK  helper: a post-'started' gate timeout fires on_failed (Fix 1)")


def test_maybe_engage_attaches_in_place_no_swap():
    # Fix 2: the late-grant engage attaches a far end to the SAME running worker (attach_far) - no
    # swap - so the mic stream is never handed off and no mic block can be lost or reordered.
    cap = capture_mac.AudioCapture()
    cap._t0 = 0.0
    cap._register_source("SYS", 16000, 1)   # as the grant path registers it before engaging
    worker = _FakeWorker(has_far=False)
    cap._live_aec = worker
    fake = _FakeHelper(capture_mac.START_STARTED)   # rate = 16000
    cap._maybe_engage_aec_after_grant(fake)
    assert cap._live_aec is worker, "worker was swapped; must attach in place (Fix 2)"
    assert worker.attached is not None and worker.attached[0] == 16000
    assert worker.has_far is True
    assert worker.bypass is (not cap.aec)   # bypass reflects the session's AEC choice
    print("  OK  _maybe_engage: attaches the far end in place, no worker swap (Fix 2)")


def test_maybe_engage_noop_when_worker_already_has_far():
    # Fix 3: if start() already committed a MIC+SYS worker (SYS registered before its decision),
    # the attach is a no-op (already echo-capable).
    cap = capture_mac.AudioCapture()
    worker = _FakeWorker(has_far=True)
    cap._live_aec = worker
    cap._maybe_engage_aec_after_grant(_FakeHelper(capture_mac.START_STARTED))
    assert worker.attached is None   # not attached again
    print("  OK  _maybe_engage: no-op when the worker already has a far end (Fix 3)")


def test_maybe_engage_noop_when_no_worker():
    # Fix 3: no mic worker (AGC off / binding missing) -> nothing to attach to (documented
    # native-SYS fallback); must not raise.
    cap = capture_mac.AudioCapture()
    cap._live_aec = None
    cap._maybe_engage_aec_after_grant(_FakeHelper(capture_mac.START_STARTED))
    assert cap._live_aec is None
    print("  OK  _maybe_engage: no-op (no crash) when there is no worker to attach to (Fix 3)")


def test_liveaec_attach_far_state():
    # Fix 2 (aec_live): attach_far on an AGC-only worker sets up far routing with NO swap; the
    # APM build is deferred to the worker thread (not exercised here - LiveKit is absent). This
    # only touches LiveAEC.__init__ + attach_far, neither of which imports livekit.
    from live_transcribe.aec_live import LiveAEC
    la = LiveAEC(16000, None, on_near=lambda x: None, on_far=None, bypass=True, agc=True)
    assert la.has_far is False and la.aec_capable is False and la._far_pending is None
    sink = lambda x: None
    la.attach_far(16000, sink)
    assert la.has_far is True
    assert la._far_pending == 16000
    assert la._on_far is sink
    la.attach_far(48000, lambda x: None)   # idempotent: a second attach is a no-op
    assert la._far_pending == 16000
    print("  OK  LiveAEC.attach_far: sets far routing in place, idempotent (Fix 2)")


def test_start_snapshot_under_lifecycle_lock():
    # Fix 4: base start() takes _lifecycle_lock around the worker-decision + chunker snapshot -
    # the SAME lock the macOS grant thread uses for _register_source - so a late registration can
    # never mutate self._buffers mid-snapshot ('dict changed size during iteration'). Deterministic
    # proof: while start() is inside its locked section, a probe that also takes the lock cannot.
    from live_transcribe.capture_core import CaptureBase

    class _Cap(CaptureBase):
        def _open_sources(self):
            self._register_source("MIC", 16000, 1)

        def _close_sources(self):
            pass

        def _chunker(self, source):
            pass   # no-op: spawned chunker threads just exit

    cap = _Cap(agc=False)   # agc off -> no worker; start() goes straight to snapshot + spawn
    real = cap._lifecycle_lock
    inside = threading.Event()
    proceed = threading.Event()
    probe = {"blocked": None}

    class _Probe:
        def __enter__(self):
            real.acquire()
            inside.set()
            proceed.wait(2.0)
            return self

        def __exit__(self, *a):
            real.release()

    cap._lifecycle_lock = _Probe()

    def probing():
        assert inside.wait(2.0)
        got = real.acquire(blocking=False)   # start() holds it via _Probe -> must fail
        probe["blocked"] = not got
        if got:
            real.release()
        proceed.set()

    t = threading.Thread(target=probing, daemon=True)
    t.start()
    try:
        cap.start()
        t.join(2.0)
    finally:
        cap._stop_event.set()
    assert probe["blocked"] is True, "start() did not hold _lifecycle_lock during its snapshot"
    print("  OK  start(): worker-decision + chunker snapshot run under _lifecycle_lock (Fix 4)")


# ---- devices_mac -----------------------------------------------------------

def test_list_ui_devices_shape():
    restore = _with_fake_sd(_MICS, default_in=0)
    try:
        d = devices_mac.list_ui_devices()
    finally:
        restore()
    assert set(d) == {"loopbacks", "mics", "default_loopback_index", "default_mic_index"}
    assert d["default_loopback_index"] == devices_mac.SYS_LOOPBACK_INDEX == -1
    assert len(d["loopbacks"]) == 1
    assert d["loopbacks"][0]["index"] == -1
    assert d["loopbacks"][0]["name"] == devices_mac.SYS_LOOPBACK_NAME
    names = [m["name"] for m in d["mics"]]
    assert names == ["MacBook Pro Microphone", "USB Audio CODEC"], names  # output-only skipped
    assert d["default_mic_index"] == 0
    print("  OK  list_ui_devices: one synthetic loopback, input-only mics, correct default")


def test_merge_default_absent_multiple_mics():
    mics = [{"index": 0, "name": "a", "rate": 48000}, {"index": 2, "name": "b", "rate": 44100}]
    d = devices_mac._merge_ui_devices(mics, default_mic_index=9)
    assert d["default_mic_index"] is None  # phantom default, >1 candidate -> unset
    print("  OK  merge: a phantom default with several mics highlights none")


def test_merge_default_absent_single_mic():
    mics = [{"index": 5, "name": "only", "rate": 48000}]
    d = devices_mac._merge_ui_devices(mics, default_mic_index=9)
    assert d["default_mic_index"] == 5  # single mic -> pick it
    print("  OK  merge: a phantom default with one mic picks that mic")


def test_resolve_loopback_on_off():
    for spec in (None, -1, "-1", "system audio"):
        info = devices_mac.resolve_loopback(None, spec)
        assert info["index"] == -1, (spec, info)
    for bad in ("5", "headphones", 3):
        try:
            devices_mac.resolve_loopback(None, bad)
        except ValueError:
            continue
        raise AssertionError(f"resolve_loopback should reject {bad!r}")
    print("  OK  resolve_loopback: None/-1/name -> tap; other -> ValueError")


def test_resolve_mic_default_index_name():
    restore = _with_fake_sd(_MICS, default_in=2)
    try:
        assert devices_mac.resolve_mic(None, None)["index"] == 2      # system default
        assert devices_mac.resolve_mic(None, 0)["index"] == 0         # by index
        got = devices_mac.resolve_mic(None, "usb")                    # by name substring
        assert got["index"] == 2 and got["channels"] == 2 and got["rate"] == 44100
        # output-only device (#1) has no input channels
        try:
            devices_mac.resolve_mic(None, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("resolve_mic accepted an output-only device")
        # out-of-range index
        try:
            devices_mac.resolve_mic(None, 99)
        except ValueError:
            pass
        else:
            raise AssertionError("resolve_mic accepted an out-of-range index")
    finally:
        restore()
    print("  OK  resolve_mic: default / index / name substring, rejects non-inputs")


def test_resolve_mic_default_falls_back_when_no_system_default():
    restore = _with_fake_sd(_MICS, default_in=-1)  # no default reported
    try:
        assert devices_mac.resolve_mic(None, None)["index"] == 0  # first input device
    finally:
        restore()
    print("  OK  resolve_mic: no system default -> first input device")


# ---- selector routing ------------------------------------------------------

def test_selectors_route_darwin():
    import live_transcribe.capture as capture
    import live_transcribe.devices as devices
    orig = sys.platform
    try:
        sys.platform = "darwin"
        importlib.reload(capture)
        importlib.reload(devices)
        from live_transcribe import capture_mac as cm
        from live_transcribe import devices_mac as dm
        assert capture.AudioCapture is cm.AudioCapture
        assert devices.list_ui_devices is dm.list_ui_devices
        assert devices.resolve_loopback is dm.resolve_loopback
        assert devices.resolve_mic is dm.resolve_mic
    finally:
        sys.platform = orig
        importlib.reload(capture)
        importlib.reload(devices)
    print("  OK  selectors route darwin -> capture_mac / devices_mac")


def test_selectors_route_native():
    # After the reload dance restores the real platform, the live selectors must
    # point at this host's backend (win32 here).
    import live_transcribe.capture as capture
    if sys.platform == "win32":
        from live_transcribe import capture_win as cw
        assert capture.AudioCapture is cw.AudioCapture
        print("  OK  selectors route win32 -> capture_win on this host")
    else:
        print("  SKIP native-routing assertion (host is not win32)")


TESTS = [
    test_frames_roundtrip,
    test_frames_clean_eof_at_boundary,
    test_frames_empty_frame_skipped,
    test_frames_torn_body_raises,
    test_frames_partial_header_raises,
    test_frames_bad_magic_not_multiple_of_four,
    test_frames_bad_magic_over_ceiling,
    test_read_exactly_partial_then_full,
    test_read_exactly_clean_eof_returns_none,
    test_parse_header_line_ok,
    test_parse_header_line_malformed,
    test_classify_header,
    test_validate_format,
    test_validate_format_rejects_stereo,
    test_parse_stats_line,
    test_stderr_stats_warns_on_dropped_increase,
    test_helper_started_then_frames,
    test_helper_permission_denied,
    test_helper_early_exit_before_started,
    test_helper_unexpected_eof_after_started_fires_on_failed,
    test_helper_clean_stop_does_not_fire_on_failed,
    test_stop_closes_helper_stdin,
    test_helper_started_before_format_rejected,
    test_helper_error_event_degrades_immediately,
    test_helper_bad_channels_in_format_rejected,
    test_helper_unknown_event_tolerated_then_started,
    test_helper_waiting_permission_extends_deadline,
    test_open_system_tap_waiting_defers_registration,
    test_open_system_tap_started_sets_active,
    test_open_system_tap_failed_denied_sets_state_and_raises,
    test_await_system_tap_denial_after_wait_sets_state,
    test_await_system_tap_teardown_is_not_a_failure,
    test_await_opens_gate_before_upgrade,
    test_gate_timeout_fires_failure,
    test_maybe_engage_attaches_in_place_no_swap,
    test_maybe_engage_noop_when_worker_already_has_far,
    test_maybe_engage_noop_when_no_worker,
    test_liveaec_attach_far_state,
    test_start_snapshot_under_lifecycle_lock,
    test_list_ui_devices_shape,
    test_merge_default_absent_multiple_mics,
    test_merge_default_absent_single_mic,
    test_resolve_loopback_on_off,
    test_resolve_mic_default_index_name,
    test_resolve_mic_default_falls_back_when_no_system_default,
    test_selectors_route_darwin,
    test_selectors_route_native,
]


if __name__ == "__main__":
    failures = 0
    for fn in TESTS:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:  # a fake/wiring bug should surface loudly too
            failures += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll mac capture-backend tests passed.")
