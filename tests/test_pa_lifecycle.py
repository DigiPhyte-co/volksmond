"""Tests for the PortAudio lifecycle guard and the start-failure leak fix (1.14.2, WP1).

The field failure this covers: the app "rarely selects the correct audio source". PortAudio builds
its device table exactly once, when the PyAudio init count goes 0 -> 1, and only rebuilds it after
every instance has terminated (the init-count trap). A /api/start that opened one source, failed on
the other and never released its PyAudio left the count stuck above zero, so the NEXT start reused a
stale table and offered a device whose endpoint the open path could no longer find. The guard counts
live instances process-wide and records the table's age; the leak fix releases the capture on a
failed start so the following start rebuilds a fresh table.

No real audio: a fake pyaudiowpatch module whose PyAudio() reads a mutable device table (so a device
that "appears" between starts is visible to the next PyAudio, exactly as a rebuilt table would be)
and counts init/terminate. capture_win and devices_win both point at it; nothing opens a device.

Run:  python tests/test_pa_lifecycle.py   (from the project root; exit 0 = pass)
"""
import io
import os
import sys
import types
from contextlib import redirect_stdout

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# capture_win / devices_win import pyaudiowpatch at module load; the real package is Windows-only, so
# stub it when absent BEFORE importing. Every test below repoints both modules' `pa` at a fake anyway.
try:
    import pyaudiowpatch  # noqa: F401
except Exception:
    _stub = types.ModuleType("pyaudiowpatch")
    _stub.paWASAPI = 2
    _stub.paFloat32 = 1
    _stub.paContinue = 0
    _stub.PyAudio = object
    sys.modules["pyaudiowpatch"] = _stub

from live_transcribe import capture_win, devices_win

WASAPI = 2

MIC = {"index": 3, "name": "USB Mic", "maxInputChannels": 1, "isLoopbackDevice": False,
       "hostApi": WASAPI, "defaultSampleRate": 48000.0}
OTHER_MIC = {"index": 4, "name": "Built-in Mic", "maxInputChannels": 1, "isLoopbackDevice": False,
             "hostApi": WASAPI, "defaultSampleRate": 48000.0}
OTHER_LOOP = {"index": 5, "name": "Headphones (Realtek) [Loopback]", "maxInputChannels": 2,
              "isLoopbackDevice": True, "hostApi": WASAPI, "defaultSampleRate": 48000.0}
# An output-only device: neither a mic nor a loopback, so a spec that names neither cannot resolve.
OUTPUT_ONLY = {"index": 0, "name": "Speakers (Realtek)", "maxInputChannels": 0,
               "isLoopbackDevice": False, "hostApi": WASAPI, "defaultSampleRate": 48000.0}


class _Counter:
    """Tracks PortAudio's real init count: +1 per PyAudio(), -1 per terminate(). `inits` is the
    running total of instances ever created (never decremented), so a test can prove a fresh one
    was built even when the peak concurrent count does not move."""
    def __init__(self):
        self.live = 0
        self.max_live = 0
        self.inits = 0


class _Stream:
    def __init__(self):
        self.started = False
        self.closed = False

    def start_stream(self):
        self.started = True

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class _FakePA:
    """The slice of pyaudiowpatch.PyAudio the capture path touches, over a device table snapshotted at
    construction (as real PortAudio freezes its table at init). Counts its own init/terminate."""
    def __init__(self, holder, counter):
        self._counter = counter
        counter.live += 1
        counter.inits += 1
        counter.max_live = max(counter.max_live, counter.live)
        self._table = list(holder["table"])
        self.terminated = False

    def get_device_count(self):
        return len(self._table)

    def get_device_info_by_index(self, i):
        # Real PortAudio indexes 0..count-1 by position; the resolvers iterate range(count), so map
        # by position rather than by the device's own "index" field (which need not be contiguous).
        if 0 <= i < len(self._table):
            return dict(self._table[i])
        raise ValueError(f"[Errno -9996] Invalid device #{i}")

    def get_loopback_device_info_generator(self):
        for d in self._table:
            if d.get("isLoopbackDevice"):
                yield dict(d)

    def get_default_wasapi_loopback(self):
        for d in self._table:
            if d.get("isLoopbackDevice"):
                return dict(d)
        raise OSError("no default WASAPI loopback")

    def get_default_input_device_info(self):
        for d in self._table:
            if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
                return dict(d)
        raise OSError("no default input device")

    def get_host_api_info_by_type(self, t):
        return {"index": WASAPI}

    def open(self, **kw):
        return _Stream()

    def terminate(self):
        self.terminated = True
        self._counter.live -= 1


def _install(table):
    """Point devices_win.pa and capture_win.pa at a fake whose PyAudio() reads holder['table'] live
    (so a device added between starts is seen by the next instance), reset the guard's counter, and
    return (holder, counter, restore). holder['table'] is mutable so a test can change what the next
    PyAudio enumerates."""
    holder = {"table": list(table)}
    counter = _Counter()
    mod = types.SimpleNamespace(PyAudio=lambda: _FakePA(holder, counter),
                                paWASAPI=WASAPI, paFloat32=1, paContinue=0)
    saved = (devices_win.pa, capture_win.pa, devices_win._pa_count, devices_win._pa_built_at)
    devices_win.pa = mod
    capture_win.pa = mod
    devices_win._pa_count = 0
    devices_win._pa_built_at = None

    def restore():
        (devices_win.pa, capture_win.pa,
         devices_win._pa_count, devices_win._pa_built_at) = saved

    return holder, counter, restore


def test_a_leaked_capture_is_released_so_the_count_returns_to_zero():
    # THE leak: a start that resolves neither source raises, and BEFORE the fix nobody terminated the
    # PyAudio it had already created, so the process init count stayed at 1 and PortAudio never rebuilt
    # its table. Mirror /api/start: start() raises, then stop() releases. The count must return to 0.
    holder, counter, restore = _install([OUTPUT_ONLY])   # no mic, no loopback -> both resolve fail
    try:
        cap = capture_win.AudioCapture(mic_device="Ghost Mic", loopback_device="Ghost Out",
                                       positional=False)
        try:
            cap.start()
            raise AssertionError("start() must raise when neither source resolves")
        except RuntimeError:
            pass
        # The leak, made visible: the PyAudio is still live until something releases it.
        assert counter.live == 1 and devices_win.pa_instances() == 1, \
            (counter.live, devices_win.pa_instances())
        cap.stop()   # exactly what /api/start's failure handler now does
        assert counter.live == 0, f"the leaked PyAudio was never terminated: live={counter.live}"
        assert devices_win.pa_instances() == 0, devices_win.pa_instances()
    finally:
        restore()
    print("  OK  a failed start leaves a live PyAudio; stop() releases it and the count returns to 0")


def test_a_device_that_appears_after_a_failure_resolves_on_the_next_start():
    # The whole point of releasing on failure: the next start rebuilds the table and sees a device the
    # failed one could not. First start fails (empty of inputs); the mic is then "plugged in"; the
    # second start (its PyAudio reads the now-current table) resolves and opens it.
    holder, counter, restore = _install([OUTPUT_ONLY])
    try:
        cap1 = capture_win.AudioCapture(mic_device="USB Mic", loopback_device=None, positional=False)
        try:
            cap1.start()
            raise AssertionError("the first start must fail: the mic is not in the table yet")
        except RuntimeError:
            pass
        cap1.stop()
        assert devices_win.pa_instances() == 0, "the failed start did not release PortAudio"
        # The mic appears (endpoint plugged in). A fresh PyAudio built from a 0 -> 1 transition sees it.
        holder["table"] = [OUTPUT_ONLY, MIC]
        cap2 = capture_win.AudioCapture(mic_device="USB Mic", loopback_device=None, positional=False)
        try:
            cap2._open_sources()   # start()'s device-open phase, without the chunker workers
            assert "MIC" in cap2._buffers, "the newly appeared mic did not resolve on the next start"
            assert len(cap2._streams) == 1, cap2._streams
        finally:
            cap2.stop()
        assert devices_win.pa_instances() == 0, devices_win.pa_instances()
    finally:
        restore()
    print("  OK  a device that appears after a failed start resolves on the next start (fresh table)")


def test_candidates_and_table_age_are_logged_on_a_resolve_failure():
    # The diagnostics: at each session open a table-age/instances/build line, and on a resolve failure
    # a FAILED line naming what was wanted and the candidate names searched (device names only).
    holder, counter, restore = _install([OUTPUT_ONLY, OTHER_MIC, OTHER_LOOP])
    try:
        cap = capture_win.AudioCapture(mic_device="Ghost Mic", loopback_device="Ghost Out",
                                       positional=False)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                cap.start()   # raises: neither "Ghost" spec matches
            raise AssertionError("start() must raise when neither source resolves")
        except RuntimeError:
            pass
        finally:
            cap.stop()
        out = buf.getvalue()
        assert "[devices] table age=" in out and "pa_instances=" in out and "build=" in out, out
        assert "[SYS] resolve FAILED want='Ghost Out'" in out, out
        assert "[MIC] resolve FAILED want='Ghost Mic'" in out, out
        # The candidate names the resolvers searched are in the log, so a field report is self-serving.
        assert "Built-in Mic" in out, out
        assert "Headphones (Realtek) [Loopback]" in out, out
    finally:
        restore()
    print("  OK  a resolve failure logs the table age and the candidate names searched (SYS and MIC)")


def test_enumeration_helper_always_terminates_even_when_its_body_raises():
    # Short-lived helpers must terminate their PyAudio in a finally, so a mid-enumeration error cannot
    # leak an instance and pin the init count above zero (which would freeze the table for later opens).
    holder, counter, restore = _install([OUTPUT_ONLY, OTHER_LOOP])
    try:
        # Break enumeration AFTER the instance exists: list_ui_devices builds its loopback list first,
        # so a raising generator propagates out of the body while the finally still runs pa_release.
        def _boom(self):
            raise RuntimeError("enumeration blew up mid-list")
            yield  # pragma: no cover  (makes this a generator, like the real method)

        holder["table"] = [OUTPUT_ONLY]
        orig = _FakePA.get_loopback_device_info_generator
        _FakePA.get_loopback_device_info_generator = _boom
        try:
            try:
                devices_win.list_ui_devices()
                raise AssertionError("the broken enumeration should have propagated")
            except RuntimeError:
                pass
        finally:
            _FakePA.get_loopback_device_info_generator = orig
        assert counter.live == 0, f"an enumeration helper leaked a PyAudio: live={counter.live}"
        assert devices_win.pa_instances() == 0, devices_win.pa_instances()
        # And the context-manager form (pa_session) is just as strict on an exception in the block.
        before = counter.inits
        try:
            with devices_win.pa_session():
                raise ValueError("boom inside the with-block")
        except ValueError:
            pass
        assert counter.inits == before + 1, "pa_session did not create its instance"
        assert counter.live == 0 and devices_win.pa_instances() == 0, counter.live
    finally:
        restore()
    print("  OK  enumeration helpers and pa_session always terminate their PyAudio, even on an error")


if __name__ == "__main__":
    tests = (test_a_leaked_capture_is_released_so_the_count_returns_to_zero,
             test_a_device_that_appears_after_a_failure_resolves_on_the_next_start,
             test_candidates_and_table_age_are_logged_on_a_resolve_failure,
             test_enumeration_helper_always_terminates_even_when_its_body_raises)
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"  ERROR {fn.__name__}: {e!r}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll PortAudio-lifecycle tests passed.")
