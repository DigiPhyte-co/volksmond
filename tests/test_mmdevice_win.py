"""Tests for the detailed MMDevice endpoint probe (live_transcribe.mmdevice_win, WP2 of 1.14.2).

Two layers, matching the repo convention (runs as a plain script AND under pytest):
  * Pure helpers with no COM: amplitude-to-dBFS, role aggregation, name cleaning, the seven-key dict
    shape and its per-key fail-soft isolation, and flow/role normalisation. These run everywhere.
  * A Windows-only live smoke (skipped elsewhere) that calls the real probe against the real audio
    endpoints and asserts: no exception, exactly the seven contract keys, a render endpoint that is
    the multimedia default, a sub-50 ms median probe, and no material handle growth over 2000 calls.

Run:  python tests/test_mmdevice_win.py     (from the project root; exit 0 = pass)
"""
import os
import statistics
import sys
import time

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pytest
except ImportError:                     # standalone run without pytest
    pytest = None

from live_transcribe import mmdevice_win as m

_WIN = sys.platform == "win32"
_STANDALONE = False                     # the __main__ runner sets this True

# The pinned contract: exactly these seven keys, no more, no less.
_KEYS = {"id", "name", "flow", "form_factor", "muted", "peak_db", "roles"}


def _skip(reason):
    """Skip under pytest; print and return a sentinel in standalone mode (so the runner carries on)."""
    if not _STANDALONE and pytest is not None:
        pytest.skip(reason)
    print("  SKIP " + reason)
    return "skip"


# --- pure helpers: amplitude to dBFS ------------------------------------------

def test_amp_to_dbfs_full_scale_and_silence():
    assert m._amp_to_dbfs(1.0) == 0.0
    assert m._amp_to_dbfs(0.0) == -120.0            # silence floors, never -inf
    assert m._amp_to_dbfs(-0.5) == -120.0           # a nonsensical negative also floors
    # A tiny amplitude clamps to the -120 floor rather than running off to -inf.
    assert m._amp_to_dbfs(1e-12) == -120.0
    print("  OK  amp_to_dbfs: full scale 0 dB, silence and sub-floor clamp to -120")


def test_amp_to_dbfs_known_ratios_and_bad_input():
    assert abs(m._amp_to_dbfs(0.5) - (-6.0206)) < 0.01     # halving is about -6 dB
    assert abs(m._amp_to_dbfs(0.1) - (-20.0)) < 0.01       # a tenth is -20 dB
    assert m._amp_to_dbfs("not a number") is None          # non-numeric is None, not a crash
    assert m._amp_to_dbfs(None) is None
    print("  OK  amp_to_dbfs: -6 dB and -20 dB ratios, non-numeric returns None")


# --- pure helpers: role aggregation -------------------------------------------

def test_roles_for_matches_in_fixed_order():
    defaults = {"console": "A", "multimedia": "A", "communications": "B"}
    assert m._roles_for("A", defaults) == ["console", "multimedia"]
    assert m._roles_for("B", defaults) == ["communications"]
    assert m._roles_for("C", defaults) == []               # default of no role
    print("  OK  roles_for: canonical order, only the roles this id is the default of")


def test_roles_for_empty_id_and_missing_defaults():
    assert m._roles_for(None, {"console": "A"}) == []
    assert m._roles_for("", {"console": ""}) == []         # an empty id owns no role
    assert m._roles_for("A", {"console": None, "multimedia": None, "communications": None}) == []
    print("  OK  roles_for: empty id and all-None defaults yield no roles")


# --- pure helpers: name cleaning ----------------------------------------------

def test_clean_endpoint_name_strips_only_loopback_suffix():
    assert m._clean_endpoint_name("Speakers (Realtek(R) Audio) [Loopback]") == "Speakers (Realtek(R) Audio)"
    assert m._clean_endpoint_name("Speakers (Realtek(R) Audio)") == "Speakers (Realtek(R) Audio)"
    assert m._clean_endpoint_name("Aux [Loopback] (2)") == "Aux [Loopback] (2)"   # only a TRAILING suffix
    assert m._clean_endpoint_name(None) is None
    assert m._clean_endpoint_name("") == ""
    print("  OK  clean_endpoint_name: strips a trailing [Loopback] only, passes None/empty through")


# --- pure helpers: flow / role normalisation ----------------------------------

def test_norm_flow_and_role_accept_ints_and_names():
    assert m._norm_flow("render") == m._eRender and m._norm_flow("capture") == m._eCapture
    assert m._norm_flow(m._eRender) == m._eRender          # an int passes through
    assert m._norm_role("multimedia") == m._eMultimedia
    assert m._norm_role("console") == m._eConsole and m._norm_role("communications") == m._eCommunications
    assert m._norm_role(m._eMultimedia) == m._eMultimedia
    print("  OK  norm_flow/norm_role: accept both the ints and the friendly names")


# --- dict shape and fail-soft isolation (no COM, monkeypatched sub-probes) -----

def _fake_subprobes(monkey, *, eid="ep-1", name="Speakers", ff=1, muted=False, peak=-12.0,
                    raise_key=None):
    """Point mmdevice_win's COM-touching per-endpoint helpers at fakes so _one_endpoint runs with no
    COM at all. `raise_key` forces one named sub-probe to raise, to prove per-key isolation."""
    def mk(key, val):
        def _f(*a, **k):
            if raise_key == key:
                raise RuntimeError("boom")
            return val
        return _f
    monkey("_endpoint_id", mk("id", eid))
    monkey("_name_and_formfactor", mk("name", (name, ff)))
    monkey("_endpoint_muted", mk("muted", muted))
    monkey("_endpoint_peak_db", mk("peak", peak))


class _Patcher:
    """Minimal monkeypatch usable both under pytest and standalone: records and restores attrs."""
    def __init__(self):
        self._saved = []

    def set(self, attr, val):
        self._saved.append((attr, getattr(m, attr)))
        setattr(m, attr, val)

    def undo(self):
        for attr, val in reversed(self._saved):
            setattr(m, attr, val)
        self._saved = []


def test_one_endpoint_has_exactly_seven_keys():
    p = _Patcher()
    try:
        _fake_subprobes(p.set, name="Speakers [Loopback]", ff=9, muted=True, peak=-3.0)
        d = m._one_endpoint(None, None, "render", {"console": "ep-1"}, with_peak=True)
        assert set(d.keys()) == _KEYS, d.keys()
        assert d["id"] == "ep-1"
        assert d["name"] == "Speakers"                 # cleaned: no [Loopback] suffix
        assert d["flow"] == "render"
        assert d["form_factor"] == 9
        assert d["muted"] is True
        assert d["peak_db"] == -3.0
        assert d["roles"] == ["console"]
        print("  OK  one_endpoint: exactly the seven contract keys, cleaned name, correct values")
    finally:
        p.undo()


def test_one_endpoint_with_peak_false_sets_peak_none():
    p = _Patcher()
    try:
        _fake_subprobes(p.set, peak=-5.0)
        d = m._one_endpoint(None, None, "capture", {}, with_peak=False)
        assert set(d.keys()) == _KEYS
        assert d["peak_db"] is None                    # with_peak=False never reads the meter
        print("  OK  one_endpoint: with_peak=False yields peak_db None, keys unchanged")
    finally:
        p.undo()


def test_one_endpoint_sub_failure_sets_only_that_key_none():
    for bad in ("id", "name", "muted", "peak"):
        p = _Patcher()
        try:
            _fake_subprobes(p.set, raise_key=bad)
            d = m._one_endpoint(None, None, "render", {"console": "ep-1"}, with_peak=True)
            assert set(d.keys()) == _KEYS, (bad, d.keys())      # still exactly seven keys
            if bad == "id":
                assert d["id"] is None and d["roles"] == []     # no id means no role
            elif bad == "name":
                assert d["name"] is None and d["form_factor"] is None
            elif bad == "muted":
                assert d["muted"] is None and d["peak_db"] == -12.0   # neighbours survive
            elif bad == "peak":
                assert d["peak_db"] is None and d["muted"] is False
        finally:
            p.undo()
    print("  OK  one_endpoint: a raising sub-probe nulls only its key, others still fill")


def test_list_detailed_bad_flow_returns_empty_without_com():
    # An invalid flow is rejected before any COM call, so this is safe on every platform.
    assert m.list_endpoints_detailed(flow="bogus") == []
    print("  OK  list_endpoints_detailed: an invalid flow returns [] without touching COM")


# --- Windows-only live smoke --------------------------------------------------

def _handle_count():
    """Open handle count for this process (a COM-pointer leak shows up here), or None off Windows."""
    import ctypes
    k32 = ctypes.windll.kernel32
    # Explicit signatures: the process pseudo-handle is pointer-sized, so it must not be marshalled
    # as a 32-bit int on x64 (that is what silently fails the call).
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    k32.GetProcessHandleCount.restype = ctypes.c_int
    c = ctypes.c_ulong(0)
    if not k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(c)):
        return None
    return c.value


def test_live_probe_shape_and_multimedia_default():
    if not _WIN:
        return _skip("live MMDevice probe is Windows-only")
    rows = m.list_endpoints_detailed()                 # must not raise
    assert isinstance(rows, list) and rows, "expected at least one active endpoint"
    for d in rows:
        assert set(d.keys()) == _KEYS, d.keys()
        assert d["flow"] in ("render", "capture")
        assert d["id"] is None or isinstance(d["id"], str)
        assert d["form_factor"] is None or isinstance(d["form_factor"], int)
        assert d["muted"] in (True, False, None)
        assert d["peak_db"] is None or isinstance(d["peak_db"], float)
        assert isinstance(d["roles"], list)
        assert set(d["roles"]) <= {"console", "multimedia", "communications"}
    renders = [d for d in rows if d["flow"] == "render"]
    assert any("multimedia" in d["roles"] for d in renders), "no render endpoint is the multimedia default"
    # The render/capture filters are honoured.
    assert all(d["flow"] == "render" for d in m.list_endpoints_detailed(flow="render"))
    assert all(d["flow"] == "capture" for d in m.list_endpoints_detailed(flow="capture"))
    print(f"  OK  live probe: {len(rows)} endpoints, seven keys each, a multimedia-default render present")


def test_live_probe_timing_under_50ms_median():
    if not _WIN:
        return _skip("live MMDevice probe is Windows-only")
    for _ in range(10):                                # warm up COM / device table
        m.list_endpoints_detailed()
    samples = []
    for _ in range(25):
        t0 = time.perf_counter()
        m.list_endpoints_detailed()
        samples.append(time.perf_counter() - t0)
    med = statistics.median(samples)
    print(f"  OK  live probe timing: median {med * 1000:.1f} ms over 25 calls "
          f"(min {min(samples) * 1000:.1f}, max {max(samples) * 1000:.1f})")
    assert med < 0.050, f"median probe {med * 1000:.1f} ms exceeds the 50 ms budget"


def test_live_probe_no_handle_leak_over_2000_calls():
    if not _WIN:
        return _skip("live MMDevice probe is Windows-only")
    for _ in range(50):                                # settle before the baseline
        m.list_endpoints_detailed()
    start = _handle_count()
    for _ in range(2000):
        m.list_endpoints_detailed()
    end = _handle_count()
    print(f"  OK  handle count: {start} -> {end} over 2000 probes (delta {end - start})")
    # A leaked COM pointer per call would add thousands of handles; allow a small, noisy margin.
    assert end - start < 50, f"handle count grew by {end - start} over 2000 probes (suspected leak)"


if __name__ == "__main__":
    _STANDALONE = True
    tests = (test_amp_to_dbfs_full_scale_and_silence,
             test_amp_to_dbfs_known_ratios_and_bad_input,
             test_roles_for_matches_in_fixed_order,
             test_roles_for_empty_id_and_missing_defaults,
             test_clean_endpoint_name_strips_only_loopback_suffix,
             test_norm_flow_and_role_accept_ints_and_names,
             test_one_endpoint_has_exactly_seven_keys,
             test_one_endpoint_with_peak_false_sets_peak_none,
             test_one_endpoint_sub_failure_sets_only_that_key_none,
             test_list_detailed_bad_flow_returns_empty_without_com,
             test_live_probe_shape_and_multimedia_default,
             test_live_probe_timing_under_50ms_median,
             test_live_probe_no_handle_leak_over_2000_calls)
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
    print("\nAll mmdevice_win tests passed.")
