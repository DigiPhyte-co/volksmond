"""Linux capture backend tests, runnable on Windows with fakes.

Covers the parts of the Linux backend that are pure logic and therefore
verifiable without a Linux box, a Pulse/PipeWire server, or real audio:

- devices_linux enumeration shaping: mic vs monitor classification, the
  "System audio: <sink>" labels, positional indices, default resolution
  (default sink -> ".monitor" name; default source -> mic position),
- resolve_mic / resolve_loopback by default, index and name substring, plus
  their failure modes,
- capture_linux._open_sources registering MIC + SYS with the sources' native
  rates/channels, opening pasimple record streams against the right Pulse
  source names, the reader threads ingesting float32 (frames, channels)
  blocks, and the S16 fallback scaling,
- the no-sources failure path (RuntimeError) and per-source open-failure
  messages,
- lazy-import cleanliness (both modules import with pulsectl/pasimple absent),
- the platform selectors routing linux -> capture_linux / devices_linux.

pulsectl and pasimple are faked via sys.modules injection (the same trick
test_capture_mac.py uses for sounddevice); anything needing a real libpulse
is marked TODO(linux-hw) in the source and lands with the Docker null-sink
fixture (WP-L3) and the Mint hardware pass (WP-LH).

Run:  python tests/test_capture_linux.py   (from the project root; exit 0 = pass)
"""
import importlib
import os
import sys
import time

import numpy as np

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import capture_linux, devices_linux


# ---- fakes -------------------------------------------------------------------

class _FakeSpec:
    def __init__(self, rate, channels):
        self.format = 5
        self.rate = rate
        self.channels = channels


class _FakeSource:
    def __init__(self, index, name, description, rate, channels,
                 monitor_of_sink=devices_linux.PA_INVALID_INDEX,
                 monitor_of_sink_name=None):
        self.index = index
        self.name = name
        self.description = description
        self.sample_spec = _FakeSpec(rate, channels)
        self.channel_count = channels
        self.monitor_of_sink = monitor_of_sink
        self.monitor_of_sink_name = monitor_of_sink_name


class _FakeServer:
    def __init__(self, default_sink_name, default_source_name):
        self.default_sink_name = default_sink_name
        self.default_source_name = default_source_name


class _FakePulseConn:
    def __init__(self, sources, server):
        self._sources = sources
        self._server = server
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.closed = True

    def source_list(self):
        return list(self._sources)

    def server_info(self):
        return self._server


class _FakePulseModule:
    """Injected as sys.modules['pulsectl']; Pulse(name) yields a fake conn."""

    def __init__(self, sources, server):
        self._sources = sources
        self._server = server
        self.opened = []

    def Pulse(self, name=None):
        conn = _FakePulseConn(self._sources, self._server)
        self.opened.append(conn)
        return conn


class _FakePaSimpleError(Exception):
    pass


def _make_fake_pasimple(float_value=0.25, with_float32=True, fail_devices=(),
                        s16_value=16384, float_fail_devices=()):
    """Build a fake pasimple module. Streams synthesise a constant signal:
    float32 `float_value` on the float path, int16 `s16_value` on the S16
    path. `fail_devices` lists device_name values whose open always raises;
    `float_fail_devices` lists device_name values whose open raises only for
    PA_SAMPLE_FLOAT32LE (exercising the S16 open-retry fallback)."""

    class _FakePaSimple:
        instances = []

        def __init__(self, direction, format, channels, rate, app_name="python",
                     stream_name=None, server_name=None, device_name=None,
                     maxlength=-1, tlength=-1, prebuf=-1, minreq=-1, fragsize=-1):
            if device_name in fail_devices:
                raise _FakePaSimpleError(f"open failed for {device_name!r}")
            if (device_name in float_fail_devices
                    and format == getattr(mod, "PA_SAMPLE_FLOAT32LE", None)):
                raise _FakePaSimpleError(f"FLOAT32LE open failed for {device_name!r}")
            self.direction = direction
            self.format = format
            self.channels = channels
            self.rate = rate
            self.app_name = app_name
            self.stream_name = stream_name
            self.device_name = device_name
            self.fragsize = fragsize
            self.closed = False
            _FakePaSimple.instances.append(self)

        def read(self, num_bytes):
            if self.closed:
                raise _FakePaSimpleError("stream closed")
            time.sleep(0.001)
            if self.format == mod.PA_SAMPLE_S16LE:
                n = num_bytes // 2
                return np.full(n, s16_value, dtype="<i2").tobytes()
            n = num_bytes // 4
            return np.full(n, float_value, dtype="<f4").tobytes()

        def close(self):
            self.closed = True

    class _Module:
        pass

    mod = _Module()
    mod.PA_STREAM_PLAYBACK = 1
    mod.PA_STREAM_RECORD = 2
    mod.PA_SAMPLE_S16LE = 3
    if with_float32:
        mod.PA_SAMPLE_FLOAT32LE = 5
    mod.PaSimple = _FakePaSimple
    mod.PaSimpleError = _FakePaSimpleError
    return mod


# Fixture: two mics, two monitors (server order interleaved to prove the
# split), defaults pointing at the Samson mic and the built-in sink.
_SINK = "alsa_output.pci-0000_00_1f.3.analog-stereo"
_MIC0 = "alsa_input.usb-Samson_C01U-00.mono-fallback"
_MIC1 = "alsa_input.pci-0000_00_1f.3.analog-stereo"
_MON1 = "bluez_output.headset.a2dp.monitor"


def _fixture_sources():
    return [
        _FakeSource(3, _MIC0, "Samson C01U Mono", 44100, 1),
        _FakeSource(5, f"{_SINK}.monitor", "Monitor of Built-in Audio Analog Stereo",
                    48000, 2, monitor_of_sink=0, monitor_of_sink_name=_SINK),
        _FakeSource(7, _MIC1, "Built-in Audio Analog Stereo", 48000, 2),
        _FakeSource(9, _MON1, "Monitor of Headset", 48000, 2,
                    monitor_of_sink=2, monitor_of_sink_name="bluez_output.headset.a2dp"),
    ]


def _install_fakes(sources=None, server=None, pasimple_mod=None):
    """Install fake pulsectl (+ optionally pasimple) in sys.modules; return a
    (pulse_module, restore) pair."""
    if sources is None:
        sources = _fixture_sources()
    if server is None:
        server = _FakeServer(_SINK, _MIC0)
    fake_pulse = _FakePulseModule(sources, server)
    prev_pulse = sys.modules.get("pulsectl")
    prev_pas = sys.modules.get("pasimple")
    sys.modules["pulsectl"] = fake_pulse
    if pasimple_mod is not None:
        sys.modules["pasimple"] = pasimple_mod

    def restore():
        if prev_pulse is not None:
            sys.modules["pulsectl"] = prev_pulse
        else:
            sys.modules.pop("pulsectl", None)
        if pasimple_mod is not None:
            if prev_pas is not None:
                sys.modules["pasimple"] = prev_pas
            else:
                sys.modules.pop("pasimple", None)

    return fake_pulse, restore


def _wait_for(cond, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---- lazy-import cleanliness -------------------------------------------------

def test_modules_import_without_pulse_packages():
    # On this Windows host pulsectl/pasimple are genuinely absent; a clean
    # reload with no fakes installed proves both modules import lazily.
    for name in ("pulsectl", "pasimple"):
        assert name not in sys.modules or not isinstance(
            sys.modules.get(name), _FakePulseModule), "stale fake left installed"
    saved = {n: sys.modules.pop(n) for n in ("pulsectl", "pasimple") if n in sys.modules}
    try:
        importlib.reload(devices_linux)
        importlib.reload(capture_linux)
        assert "pulsectl" not in sys.modules, "devices_linux imported pulsectl at module level"
        assert "pasimple" not in sys.modules, "capture_linux imported pasimple at module level"
    finally:
        sys.modules.update(saved)
    print("  OK  capture_linux/devices_linux import with pulsectl and pasimple absent")


# ---- monitor classification and labels ----------------------------------------

def test_is_monitor_classification():
    mon_by_name_field = _fixture_sources()[1]
    mic = _fixture_sources()[0]
    assert devices_linux._is_monitor(mon_by_name_field) is True
    assert devices_linux._is_monitor(mic) is False
    # Robustness: a monitor identifiable only by its ".monitor" name suffix
    # (fields missing) must still classify as a monitor.
    bare = _FakeSource(1, "something.monitor", "Monitor of Something", 48000, 2)
    bare.monitor_of_sink = devices_linux.PA_INVALID_INDEX
    bare.monitor_of_sink_name = None
    assert devices_linux._is_monitor(bare) is True
    # And monitor_of_sink == 0 (sink index 0) counts even without the name.
    idx0 = _FakeSource(1, "weird-name", "Monitor of Sink Zero", 48000, 2, monitor_of_sink=0)
    assert devices_linux._is_monitor(idx0) is True
    print("  OK  monitor classification: name field, .monitor suffix, sink index 0")


def test_monitor_label_strips_prefix():
    s = _fixture_sources()[1]
    assert devices_linux._monitor_label(s) == "System audio: Built-in Audio Analog Stereo"
    print("  OK  monitor label renders as 'System audio: <sink description>'")


# ---- sample_spec garbage (real-libpulse regression, wp-l1-bug) -----------------

def test_sample_spec_garbage_resolves_to_channel_count_and_48k():
    # Real pulsectl exposes source.sample_spec as a raw ctypes struct whose
    # memory is invalid on the returned info objects (observed garbage on real
    # libpulse: rate 0/32764, channels 24/176). channel_count (unpacked by
    # pulsectl) is the trustworthy channel field; an implausible spec rate
    # falls back to 48000.
    garbage = _FakeSource(3, _MIC0, "Samson C01U Mono", 0, 176)
    garbage.channel_count = 2
    assert devices_linux._spec_rate(garbage) == 48000
    assert devices_linux._spec_channels(garbage) == 2
    # Implausibly high rate is also rejected; channels NEVER come from the
    # spec, even when the spec's channels field looks harmless.
    high = _FakeSource(4, _MIC1, "Built-in", 4_000_000, 2)
    high.channel_count = 1
    assert devices_linux._spec_rate(high) == 48000
    assert devices_linux._spec_channels(high) == 1
    # A plausible spec rate is still trusted (the server resamples record
    # streams, so wrong-but-plausible is safe and right-and-plausible is best).
    sane = _FakeSource(5, _MIC0, "Samson", 44100, 24)
    sane.channel_count = 1
    assert devices_linux._spec_rate(sane) == 44100
    assert devices_linux._spec_channels(sane) == 1
    print("  OK  sample_spec garbage: channel_count wins, implausible rates -> 48000")


def test_sample_spec_raising_attribute_resolves_safely():
    # A sample_spec attribute that raises on ACCESS (ctypes reading freed
    # memory can do exactly that) must still resolve: channel_count channels
    # and the 48 kHz fallback rate, end to end through the descriptor path.
    class _ExplodingSpecSource:
        index = 11
        name = _MIC0
        description = "Samson C01U Mono"
        channel_count = 2
        monitor_of_sink = devices_linux.PA_INVALID_INDEX
        monitor_of_sink_name = None

        @property
        def sample_spec(self):
            raise RuntimeError("invalid ctypes memory")

    src = _ExplodingSpecSource()
    assert devices_linux._spec_rate(src) == 48000
    assert devices_linux._spec_channels(src) == 2
    d = devices_linux._descriptor(0, src, devices_linux._mic_label(src))
    assert d["rate"] == 48000 and d["channels"] == 2
    print("  OK  raising sample_spec: descriptor still resolves (48000 Hz, channel_count ch)")


# ---- list_ui_devices -----------------------------------------------------------

def test_list_ui_devices_shape_and_defaults():
    _fake, restore = _install_fakes()
    try:
        d = devices_linux.list_ui_devices()
    finally:
        restore()
    assert set(d) == {"loopbacks", "mics", "default_loopback_index", "default_mic_index"}
    # Positional indices, server order preserved within each list.
    assert [lb["index"] for lb in d["loopbacks"]] == [0, 1]
    assert [m["index"] for m in d["mics"]] == [0, 1]
    assert d["loopbacks"][0]["name"] == "System audio: Built-in Audio Analog Stereo"
    assert d["loopbacks"][1]["name"] == "System audio: Headset"
    assert d["mics"][0]["name"] == "Samson C01U Mono"
    assert d["mics"][0]["rate"] == 44100
    # Defaults: default sink's monitor is loopback 0; default source is mic 0.
    assert d["default_loopback_index"] == 0
    assert d["default_mic_index"] == 0
    print("  OK  list_ui_devices: shape, positional indices, labels, defaults")


def test_list_ui_devices_default_mic_unset_when_ambiguous():
    # Default source names a monitor (not in the mic list): with two mics the
    # default must stay unset; with one mic it may be picked.
    server = _FakeServer(_SINK, f"{_SINK}.monitor")
    _fake, restore = _install_fakes(server=server)
    try:
        d = devices_linux.list_ui_devices()
    finally:
        restore()
    assert d["default_mic_index"] is None
    single = [_fixture_sources()[0], _fixture_sources()[1]]
    _fake, restore = _install_fakes(sources=single, server=server)
    try:
        d = devices_linux.list_ui_devices()
    finally:
        restore()
    assert d["default_mic_index"] == 0
    print("  OK  list_ui_devices: phantom default mic -> unset (multi) / picked (single)")


# ---- resolve_loopback ----------------------------------------------------------

def test_resolve_loopback_default_is_default_sinks_monitor():
    _fake, restore = _install_fakes()
    try:
        d = devices_linux.resolve_loopback(None, None)
    finally:
        restore()
    assert d["source"] == f"{_SINK}.monitor"
    assert d["index"] == 0
    assert d["rate"] == 48000 and d["channels"] == 2
    print("  OK  resolve_loopback: None -> default sink name + '.monitor'")


def test_resolve_loopback_index_name_and_errors():
    _fake, restore = _install_fakes()
    try:
        assert devices_linux.resolve_loopback(None, 1)["source"] == _MON1
        assert devices_linux.resolve_loopback(None, "1")["source"] == _MON1
        assert devices_linux.resolve_loopback(None, "headset")["source"] == _MON1
        # Matching on the raw pulse name also works.
        assert devices_linux.resolve_loopback(None, "bluez_output")["source"] == _MON1
        for bad in (5, "no such sink"):
            try:
                devices_linux.resolve_loopback(None, bad)
            except ValueError:
                continue
            raise AssertionError(f"resolve_loopback accepted {bad!r}")
    finally:
        restore()
    print("  OK  resolve_loopback: index / numeric string / substring; bad specs raise")


def test_resolve_loopback_no_monitors_raises():
    mics_only = [_fixture_sources()[0]]
    _fake, restore = _install_fakes(sources=mics_only)
    try:
        devices_linux.resolve_loopback(None, None)
    except ValueError as e:
        assert "monitor" in str(e).lower()
        restore()
        print("  OK  resolve_loopback: no monitor sources -> ValueError")
        return
    restore()
    raise AssertionError("resolve_loopback with no monitors did not raise")


def test_resolve_loopback_default_sink_without_monitor_falls_back():
    # Default sink has no matching monitor source: fall back to monitor 0.
    server = _FakeServer("some_other_sink", _MIC0)
    _fake, restore = _install_fakes(server=server)
    try:
        d = devices_linux.resolve_loopback(None, None)
    finally:
        restore()
    assert d["source"] == f"{_SINK}.monitor" and d["index"] == 0
    print("  OK  resolve_loopback: unmatched default sink falls back to first monitor")


# ---- resolve_mic ---------------------------------------------------------------

def test_resolve_mic_default_index_name_and_errors():
    _fake, restore = _install_fakes()
    try:
        d = devices_linux.resolve_mic(None, None)
        assert d["source"] == _MIC0 and d["rate"] == 44100 and d["channels"] == 1
        assert devices_linux.resolve_mic(None, 1)["source"] == _MIC1
        assert devices_linux.resolve_mic(None, "built-in")["source"] == _MIC1
        for bad in (7, "webcam"):
            try:
                devices_linux.resolve_mic(None, bad)
            except ValueError:
                continue
            raise AssertionError(f"resolve_mic accepted {bad!r}")
    finally:
        restore()
    print("  OK  resolve_mic: default / index / substring; bad specs raise")


def test_resolve_mic_monitor_default_falls_back_to_first_mic():
    server = _FakeServer(_SINK, f"{_SINK}.monitor")
    _fake, restore = _install_fakes(server=server)
    try:
        assert devices_linux.resolve_mic(None, None)["source"] == _MIC0
    finally:
        restore()
    print("  OK  resolve_mic: monitor-as-default-source falls back to the first mic")


def test_resolve_mic_no_mics_raises():
    mons_only = [_fixture_sources()[1]]
    _fake, restore = _install_fakes(sources=mons_only)
    try:
        devices_linux.resolve_mic(None, None)
    except ValueError as e:
        assert "microphone" in str(e).lower()
        restore()
        print("  OK  resolve_mic: no input sources -> ValueError")
        return
    restore()
    raise AssertionError("resolve_mic with no mics did not raise")


# ---- capture backend: open / ingest / close ------------------------------------

def test_open_sources_registers_mic_and_sys():
    pas = _make_fake_pasimple(float_value=0.25)
    _fake, restore = _install_fakes(pasimple_mod=pas)
    cap = capture_linux.AudioCapture()
    cap._t0 = 0.0
    try:
        cap._open_sources()
        # Both sources registered at their native rates/channels ("MIC"/"SYS"
        # names are the LOCKED cross-platform contract).
        assert set(cap._buffers) == {"MIC", "SYS"}, cap._buffers.keys()
        assert cap._rates == {"MIC": 44100, "SYS": 48000}, cap._rates
        assert cap._channels == {"MIC": 1, "SYS": 2}, cap._channels
        # pasimple streams opened as RECORD/FLOAT32 against the pulse NAMES.
        by_dev = {s.device_name: s for s in pas.PaSimple.instances}
        assert set(by_dev) == {_MIC0, f"{_SINK}.monitor"}, by_dev.keys()
        for s in by_dev.values():
            assert s.direction == pas.PA_STREAM_RECORD
            assert s.format == pas.PA_SAMPLE_FLOAT32LE
            assert s.fragsize > 0, "fragsize left at the ~2s server default"
        assert by_dev[_MIC0].rate == 44100 and by_dev[_MIC0].channels == 1
        mon = by_dev[f"{_SINK}.monitor"]
        assert mon.rate == 48000 and mon.channels == 2
        # Readers must aggregate and ingest float32 (frames, channels) blocks.
        assert _wait_for(lambda: cap._buffer_counts.get("MIC", 0) > 0
                         and cap._buffer_counts.get("SYS", 0) > 0), "no audio ingested"
        with cap._buffer_locks["SYS"]:
            block = cap._buffers["SYS"][0]
        assert block.ndim == 2 and block.shape[1] == 2, block.shape
        assert block.dtype == np.float32
        assert np.allclose(block, 0.25, atol=1e-6)
        with cap._buffer_locks["MIC"]:
            mic_block = cap._buffers["MIC"][0]
        assert mic_block.shape[1] == 1
        # The shared core's level meter saw both sources.
        assert _wait_for(lambda: set(cap.levels()) == {"MIC", "SYS"})
    finally:
        cap._close_sources()
        restore()
    assert all(s.closed for s in pas.PaSimple.instances), "streams left open"
    assert cap._streams == []
    print("  OK  _open_sources: MIC+SYS registered (native rate/ch), float32 blocks ingested, close closes")


def test_s16_fallback_scales_to_float():
    # A pasimple without PA_SAMPLE_FLOAT32LE must fall back to S16LE with a
    # 1/32768 scale. int16 16384 -> 0.5 float32.
    pas = _make_fake_pasimple(with_float32=False, s16_value=16384)
    sources = [_fixture_sources()[0]]  # mic only (no monitors: SYS resolve fails, prints)
    _fake, restore = _install_fakes(sources=sources, pasimple_mod=pas)
    cap = capture_linux.AudioCapture()
    cap._t0 = 0.0
    try:
        cap._open_sources()
        assert set(cap._buffers) == {"MIC"}
        assert pas.PaSimple.instances[0].format == pas.PA_SAMPLE_S16LE
        assert _wait_for(lambda: cap._buffer_counts.get("MIC", 0) > 0)
        with cap._buffer_locks["MIC"]:
            block = cap._buffers["MIC"][0]
        assert block.dtype == np.float32
        assert np.allclose(block, 0.5, atol=1e-4), block[:4]
    finally:
        cap._close_sources()
        restore()
    print("  OK  S16 fallback: PA_SAMPLE_S16LE requested, samples scaled by 1/32768")


def test_s16_fallback_on_float_open_failure():
    # PA_SAMPLE_FLOAT32LE exists but the FLOAT32LE stream OPEN raises: the
    # backend must retry that source ONCE with S16LE (same rate/channels,
    # consistent width/scale/read-size), not fail the open.
    pas = _make_fake_pasimple(with_float32=True, s16_value=16384,
                              float_fail_devices=(_MIC0,))
    sources = [_fixture_sources()[0]]  # mic only
    _fake, restore = _install_fakes(sources=sources, pasimple_mod=pas)
    cap = capture_linux.AudioCapture()
    cap._t0 = 0.0
    try:
        cap._open_sources()
        assert set(cap._buffers) == {"MIC"}
        opened = [s for s in pas.PaSimple.instances if s.device_name == _MIC0]
        assert len(opened) == 1, "retry must construct exactly one surviving stream"
        assert opened[0].format == pas.PA_SAMPLE_S16LE
        # read size arithmetic must match the 2-byte S16 width (mono 44100).
        assert opened[0].fragsize == 2 * int(44100 * capture_linux.READ_SECONDS)
        assert _wait_for(lambda: cap._buffer_counts.get("MIC", 0) > 0)
        with cap._buffer_locks["MIC"]:
            block = cap._buffers["MIC"][0]
        assert block.dtype == np.float32
        assert np.allclose(block, 0.5, atol=1e-4), block[:4]  # 16384/32768
    finally:
        cap._close_sources()
        restore()
    print("  OK  FLOAT32LE open failure retries once with S16LE (scaled ingest)")


def test_open_sources_nothing_available_raises():
    _fake, restore = _install_fakes(sources=[], pasimple_mod=_make_fake_pasimple())
    cap = capture_linux.AudioCapture()
    try:
        cap._open_sources()
    except RuntimeError as e:
        assert "no audio sources opened" in str(e)
        restore()
        print("  OK  _open_sources: no resolvable sources -> RuntimeError")
        return
    finally:
        if cap._streams:
            cap._close_sources()
    restore()
    raise AssertionError("_open_sources with no sources did not raise")


def test_open_failure_names_the_dropdown():
    # A SYS stream that fails to open must surface a message pointing at the
    # System audio dropdown (capture_win parity), and the transactional wrapper
    # must leave nothing half-open.
    pas = _make_fake_pasimple(fail_devices=(f"{_SINK}.monitor",))
    _fake, restore = _install_fakes(pasimple_mod=pas)
    cap = capture_linux.AudioCapture()
    try:
        cap._open_sources()
    except RuntimeError as e:
        assert "System audio dropdown" in str(e), e
        assert cap._streams == [], "half-open backend left behind"
        assert "SYS" not in cap._buffers, "failed SYS left registered"
        restore()
        print("  OK  SYS open failure: dropdown-naming RuntimeError, clean rollback")
        return
    finally:
        if cap._streams:
            cap._close_sources()
    restore()
    raise AssertionError("SYS open failure did not raise")


def test_close_sources_flushes_partial_tail():
    # Frames short of a full BLOCK_SECONDS aggregate at stop time must still be
    # ingested (the reader's finally flush), so the last words survive.
    pas = _make_fake_pasimple(float_value=0.1)

    real_read = pas.PaSimple.read
    reads = {"n": 0}

    def limited_read(self, num_bytes):
        # Deliver only two 20ms reads' worth, then block until stop.
        if reads["n"] >= 2:
            time.sleep(0.005)
            if self.closed:
                raise pas.PaSimpleError("closed")
            return b""
        reads["n"] += 1
        return real_read(self, num_bytes)

    pas.PaSimple.read = limited_read
    sources = [_fixture_sources()[0]]
    _fake, restore = _install_fakes(sources=sources, pasimple_mod=pas)
    cap = capture_linux.AudioCapture()
    cap._t0 = 0.0
    try:
        cap._open_sources()
        assert _wait_for(lambda: reads["n"] >= 2)
        assert cap._buffer_counts.get("MIC", 0) == 0, "partial tail ingested early"
        cap._close_sources()
        expected = 2 * int(44100 * capture_linux.READ_SECONDS)
        assert cap._buffer_counts.get("MIC", 0) == expected, cap._buffer_counts
    finally:
        pas.PaSimple.read = real_read
        restore()
    print("  OK  _close_sources: sub-block tail flushed into the buffer on stop")


# ---- selector routing ----------------------------------------------------------

def test_selectors_route_linux():
    import live_transcribe.capture as capture
    import live_transcribe.devices as devices
    orig = sys.platform
    try:
        sys.platform = "linux"
        importlib.reload(capture)
        importlib.reload(devices)
        from live_transcribe import capture_linux as cl
        from live_transcribe import devices_linux as dl
        assert capture.AudioCapture is cl.AudioCapture
        assert devices.list_ui_devices is dl.list_ui_devices
        assert devices.print_devices is dl.print_devices
        assert devices.resolve_loopback is dl.resolve_loopback
        assert devices.resolve_mic is dl.resolve_mic
    finally:
        sys.platform = orig
        importlib.reload(capture)
        importlib.reload(devices)
    print("  OK  selectors route linux -> capture_linux / devices_linux")


def test_selectors_unknown_platform_message_names_all_three():
    import live_transcribe.capture as capture
    orig = sys.platform
    try:
        sys.platform = "sunos5"
        try:
            importlib.reload(capture)
        except ImportError as e:
            msg = str(e)
            assert "capture_linux" in msg and "capture_mac" in msg and "capture_win" in msg, msg
        else:
            raise AssertionError("unknown platform did not raise ImportError")
    finally:
        sys.platform = orig
        importlib.reload(capture)
    print("  OK  unknown-platform ImportError names all three backends")


def test_selectors_route_native():
    # After the reload dance restores the real platform, the live selectors
    # must point at this host's backend (win32 here).
    import live_transcribe.capture as capture
    if sys.platform == "win32":
        from live_transcribe import capture_win as cw
        assert capture.AudioCapture is cw.AudioCapture
        print("  OK  selectors route win32 -> capture_win on this host")
    else:
        print("  SKIP native-routing assertion (host is not win32)")


TESTS = [
    test_modules_import_without_pulse_packages,
    test_is_monitor_classification,
    test_monitor_label_strips_prefix,
    test_sample_spec_garbage_resolves_to_channel_count_and_48k,
    test_sample_spec_raising_attribute_resolves_safely,
    test_list_ui_devices_shape_and_defaults,
    test_list_ui_devices_default_mic_unset_when_ambiguous,
    test_resolve_loopback_default_is_default_sinks_monitor,
    test_resolve_loopback_index_name_and_errors,
    test_resolve_loopback_no_monitors_raises,
    test_resolve_loopback_default_sink_without_monitor_falls_back,
    test_resolve_mic_default_index_name_and_errors,
    test_resolve_mic_monitor_default_falls_back_to_first_mic,
    test_resolve_mic_no_mics_raises,
    test_open_sources_registers_mic_and_sys,
    test_s16_fallback_scales_to_float,
    test_s16_fallback_on_float_open_failure,
    test_open_sources_nothing_available_raises,
    test_open_failure_names_the_dropdown,
    test_close_sources_flushes_partial_tail,
    test_selectors_route_linux,
    test_selectors_unknown_platform_message_names_all_three,
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
    print("\nAll linux capture-backend tests passed.")
