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
    assert _validate_format({"format": "f32le", "rate": 48000, "channels": 2}) == (48000, 2)
    for bad in ({"format": "s16le", "rate": 16000, "channels": 1},   # wrong format
                {"rate": 0, "channels": 1},                          # non-positive rate
                {"rate": 16000, "channels": 3},                      # bad channel count
                {"rate": 16000},                                     # missing channels
                {"channels": 1}):                                    # missing rate
        try:
            _validate_format(bad)
        except HelperProtocolError:
            continue
        raise AssertionError(f"_validate_format accepted bad meta: {bad!r}")
    print("  OK  _validate_format enforces f32le / positive rate / channels in {1,2}")


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

    def stop(self):
        self.stopped = True
        self._started.set()


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
    test_helper_started_then_frames,
    test_helper_permission_denied,
    test_helper_early_exit_before_started,
    test_helper_started_before_format_rejected,
    test_helper_error_event_degrades_immediately,
    test_helper_bad_channels_in_format_rejected,
    test_helper_unknown_event_tolerated_then_started,
    test_helper_waiting_permission_extends_deadline,
    test_open_system_tap_waiting_defers_registration,
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
