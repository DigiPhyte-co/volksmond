"""Tests for the GPU usage snapshot (live_transcribe/gpu_snapshot.py) and its one hook in the
engine's struggle-delivery point (transcribe.Engine._deliver_struggle -> _fire_gpu_snapshot).

The snapshot names what else is on the NVIDIA card when a CUDA session is struggling, so the next
"it fell behind on my 3090" report can be root-caused from the log. It shells out to nvidia-smi, so
every test that touches the module stubs BOTH the platform (gpu_snapshot.sys.platform) AND the
discovery (gpu_snapshot._find_nvidia_smi), then feeds canned output through a recording
subprocess.run stand-in, and asserts the stand-in was actually reached. Doing all three keeps the
suite green and meaningful on a machine that has no nvidia-smi (where the real discovery would
return None and the mocks would never run). Each test resets the module cache first so the 60 s rate
limiter is deterministic. No audio, no model, no real capture: the engine hook is driven on a stub
Engine built via __new__, exactly like the struggle-signal tests.

Covered:
  1. The formatted line from canned nvidia-smi output (utilisation + memory + compute apps).
  2. None when nvidia-smi is missing (discovery returns None and subprocess is never touched), on a
     non-zero exit, and on a timeout, each proving the mock was reached where a probe is expected.
  3. None on a non-Windows/non-Linux platform, WITHOUT touching subprocess (the Mac/MLX guard).
  4. The 60 s cache: a call inside the window returns "(cached)" and spawns no probe; a call after
     the window probes again.
  5. Privacy (H1): a comma in a process path never leaks a directory fragment; only the basename
     appears, whether nvidia-smi quoted the field or not.
  6. Locale (H2): typeperf comma-decimals and quoted byte values still parse to the VRAM totals.
  7. Concurrency (H3): two callers arriving together probe exactly once.
  8. CREATE_NO_WINDOW and the 2 s timeout are passed to every external call (kwargs inspected).
  9. The engine hook fires the snapshot OFF the caller's thread on a delivered notice for a CUDA
     session, prints "[gpu] <line>", and does not fire for a CPU or MLX session.

Run:  python tests/test_gpu_snapshot.py   (from the project root; exit 0 = pass)
"""
import io
import os
import subprocess
import sys
import threading
import time

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import gpu_snapshot, transcribe


# --- helpers ---------------------------------------------------------------

_GPU_OUT = "97, 21504, 24576\n"
# process_name comes back as a path on Windows, a path on Linux, or a bare name; all three must
# reduce to a basename with no directory and no arguments.
_APPS_OUT = (
    "1234, C:\\Program Files\\Volksmond\\Volksmond.exe, 3100\n"
    "88, /usr/bin/chrome, 1800\n"
    "9, ollama.exe, 14000\n"
)
_EXPECTED = ("gpu util=97% mem=21504/24576 MB apps: "
             "Volksmond.exe(pid 1234, 3100 MB), chrome(pid 88, 1800 MB), "
             "ollama.exe(pid 9, 14000 MB)")

# One WDDM typeperf sample: a quoted CSV header (counter paths) then a data row of BYTE values.
# 24956108800 B = 23800 MiB dedicated; 4404019200 B = 4200 MiB shared.
_TYPEPERF_OUT = (
    '"(PDH-CSV 4.0)","\\\\HOST\\GPU Adapter Memory(luid_0x1)\\Dedicated Usage",'
    '"\\\\HOST\\GPU Adapter Memory(luid_0x1)\\Shared Usage"\n'
    '"09/15/2026 10:00:00.000","24956108800.000000","4404019200.000000"\n'
)


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _reset_cache():
    gpu_snapshot._cache["line"] = None
    gpu_snapshot._cache["ts"] = 0.0


def _patch(obj, name, value):
    """Save-and-return an attribute so a finally can restore it. Manual, so the file runs both as a
    plain script and under pytest (no monkeypatch fixture)."""
    saved = getattr(obj, name)
    setattr(obj, name, value)
    return saved


def _default_dispatch(args, **kw):
    """Answer the two nvidia-smi queries from the canned output; fail any typeperf probe (so the
    default, Linux-flavoured, line carries no VRAM tail)."""
    flag = args[1] if len(args) > 1 else ""
    if "--query-gpu" in flag:
        return _Result(_GPU_OUT, 0)
    if "--query-compute-apps" in flag:
        return _Result(_APPS_OUT, 0)
    return _Result("", 1)


def _recording(dispatch=_default_dispatch, delay=0.0):
    """A subprocess.run stand-in that records every call (argv + kwargs) and answers via `dispatch`.
    `delay` widens the probe window for the concurrency test."""
    calls = []

    def _run(args, **kwargs):
        calls.append((list(args), kwargs))
        if delay:
            time.sleep(delay)
        return dispatch(args, **kwargs)

    _run.calls = calls
    return _run


def _install(run_fn, platform="linux", exe="nvidia-smi"):
    """Stub the platform, the nvidia-smi discovery and subprocess.run together, so a machine with no
    real nvidia-smi still reaches the mock. Returns a restore callable for a finally."""
    s_plat = _patch(gpu_snapshot.sys, "platform", platform)
    s_find = _patch(gpu_snapshot, "_find_nvidia_smi", lambda: exe)
    s_run = _patch(gpu_snapshot.subprocess, "run", run_fn)

    def restore():
        gpu_snapshot.subprocess.run = s_run
        gpu_snapshot._find_nvidia_smi = s_find
        gpu_snapshot.sys.platform = s_plat

    return restore


def _snapshot_engine(device="cuda"):
    """A minimal Engine carrying only what _deliver_struggle / _fire_gpu_snapshot read: the device,
    the (absent) struggle callback and the one-shot flag. Built via __new__ so no model loads."""
    eng = transcribe.Engine.__new__(transcribe.Engine)
    eng._device = device
    eng.on_struggle = None
    eng._gpu_snapshot_fired = False
    return eng


# --- 1. the module: formatting and the None paths --------------------------

def test_formats_the_line_from_canned_smi_output():
    _reset_cache()
    run = _recording()
    restore = _install(run, platform="linux")
    try:
        line = gpu_snapshot.snapshot()
    finally:
        restore()
    assert line == _EXPECTED, line
    assert run.calls, "subprocess.run was never reached"
    print("  OK  formats util + memory + compute apps, basenames only")


def test_none_when_nvidia_smi_is_missing_without_touching_subprocess():
    _reset_cache()
    run = _recording()
    restore = _install(run, platform="linux", exe=None)   # exe=None -> discovery yields nothing
    try:
        line = gpu_snapshot.snapshot()
    finally:
        restore()
    assert line is None, line
    assert run.calls == [], "nvidia-smi must not be spawned when discovery finds nothing"
    print("  OK  None when nvidia-smi is not found, and subprocess is never touched")


def test_none_on_nonzero_exit_reaches_the_mock():
    _reset_cache()
    run = _recording(lambda args, **kw: _Result("", 1))
    restore = _install(run, platform="linux")
    try:
        line = gpu_snapshot.snapshot()
    finally:
        restore()
    assert line is None, line
    assert run.calls, "the non-zero-exit path never reached the mock"
    print("  OK  None when nvidia-smi exits non-zero (mock reached)")


def test_none_on_timeout_reaches_the_mock():
    _reset_cache()

    def _boom(args, **kw):
        raise subprocess.TimeoutExpired(cmd=args, timeout=gpu_snapshot._TIMEOUT_SECONDS)

    run = _recording(_boom)
    restore = _install(run, platform="linux")
    try:
        line = gpu_snapshot.snapshot()
    finally:
        restore()
    assert line is None, line
    assert run.calls, "the timeout path never reached the mock"
    print("  OK  None when an nvidia-smi call times out (mock reached)")


def test_none_on_a_non_windows_non_linux_platform_without_touching_subprocess():
    _reset_cache()
    run = _recording()
    restore = _install(run, platform="darwin")
    try:
        line = gpu_snapshot.snapshot()
    finally:
        restore()
    assert line is None, line
    assert run.calls == [], "nvidia-smi must never be spawned on macOS (the MLX/Metal backend)"
    print("  OK  None on macOS, and subprocess is never touched")


# --- 2. the 60 s cache -----------------------------------------------------

def test_cache_returns_the_cached_line_within_the_window_and_spawns_no_probe():
    _reset_cache()
    run = _recording()
    restore = _install(run, platform="linux")
    try:
        first = gpu_snapshot.snapshot()
        after_first = len(run.calls)
        second = gpu_snapshot.snapshot()
    finally:
        restore()
    assert first == _EXPECTED, first
    assert second == _EXPECTED + " (cached)", second
    assert after_first >= 2, after_first             # gpu + apps at least: the mock was reached
    assert len(run.calls) == after_first, (after_first, len(run.calls))
    print("  OK  a second call inside 60 s returns the cached line and spawns no further probe")


def test_cache_expires_after_the_window_and_probes_again():
    _reset_cache()
    run = _recording()
    restore = _install(run, platform="linux")
    try:
        first = gpu_snapshot.snapshot()
        after_first = len(run.calls)
        # Age the cache past the 60 s window, then a second call must probe afresh.
        gpu_snapshot._cache["ts"] = time.monotonic() - gpu_snapshot._CACHE_SECONDS - 1.0
        second = gpu_snapshot.snapshot()
    finally:
        restore()
    assert first == _EXPECTED, first
    assert second == _EXPECTED, second               # a fresh probe, NOT the "(cached)" line
    assert len(run.calls) > after_first, (after_first, len(run.calls))
    print("  OK  a call after 60 s probes again rather than serving a stale line")


# --- 3. privacy (H1): a comma in a process path must not leak a directory --

def test_a_comma_in_a_process_path_never_leaks_a_directory():
    """A Windows profile directory can contain a comma; a naive comma split would push a path
    fragment into the memory field and leak the directory name. Only the basename may appear, whether
    nvidia-smi quoted the field (the standard case) or left it bare."""
    _reset_cache()
    unquoted = "4321, C:\\Users\\Example, Person\\private\\tool.exe, 500\n"
    quoted = '7777, "D:\\Clients\\Acme, Inc\\secret\\agent.exe", 250\n'

    def _dispatch(args, **kw):
        flag = args[1] if len(args) > 1 else ""
        if "--query-gpu" in flag:
            return _Result(_GPU_OUT, 0)
        if "--query-compute-apps" in flag:
            return _Result(unquoted + quoted, 0)
        return _Result("", 1)

    run = _recording(_dispatch)
    restore = _install(run, platform="linux")
    try:
        line = gpu_snapshot.snapshot()
    finally:
        restore()
    assert "tool.exe(pid 4321, 500 MB)" in line, line
    assert "agent.exe(pid 7777, 250 MB)" in line, line
    for leaked in ("Example", "Person", "Clients", "Acme", "private", "secret", "Users"):
        assert leaked not in line, (leaked, line)
    print("  OK  a comma in a process path leaks no directory, only the basename appears")


# --- 4. locale (H2): typeperf comma decimals and quoted values -------------

def test_normalise_number_handles_locale_separators():
    n = gpu_snapshot._normalise_number
    assert n("12345.00") == "12345.00"          # dot decimal, unchanged
    assert n("123,45") == "123.45"              # comma decimal -> dot
    assert n("1,234,567.00") == "1234567.00"    # both, dot is decimal -> drop commas
    assert n("1.234.567,00") == "1234567.00"    # both, comma is decimal -> drop dots, swap
    assert n('"9,5"') == "9.5"                  # quotes stripped, comma decimal
    print("  OK  _normalise_number folds comma decimals and thousands separators to a dot")


def test_typeperf_parses_locale_comma_decimals_and_sums_instances():
    # Comma decimals (af-ZA / de-DE), quoted values, two adapter instances per counter.
    comma = (
        '"(PDH-CSV 4.0)","\\\\H\\GPU Adapter Memory(a)\\Dedicated Usage",'
        '"\\\\H\\GPU Adapter Memory(a)\\Shared Usage"\n'
        '"09/15/2026","24956108800,000000","4404019200,000000"\n'
    )
    assert gpu_snapshot._parse_typeperf(comma) == (23800, 4200), gpu_snapshot._parse_typeperf(comma)
    two = (
        '"(PDH-CSV 4.0)",'
        '"\\\\H\\GPU Adapter Memory(a)\\Dedicated Usage","\\\\H\\GPU Adapter Memory(a)\\Shared Usage",'
        '"\\\\H\\GPU Adapter Memory(b)\\Dedicated Usage","\\\\H\\GPU Adapter Memory(b)\\Shared Usage"\n'
        '"ts","1048576.000000","2097152.000000","3145728.000000","0.000000"\n'
    )
    assert gpu_snapshot._parse_typeperf(two) == (4, 2), gpu_snapshot._parse_typeperf(two)
    # Garbage / a header with no memory counters parses to None (the caller then omits the tail).
    assert gpu_snapshot._parse_typeperf("no counters here") is None
    assert gpu_snapshot._parse_typeperf('"(PDH-CSV 4.0)","\\\\H\\Other(a)\\Foo"\n"ts","5"\n') is None
    print("  OK  typeperf parses comma decimals and quoted values, sums instances, None on garbage")


# --- 5. the WDDM Windows path (N/A memory + VRAM tail) ---------------------

def test_wddm_omits_na_process_memory_and_appends_the_typeperf_vram_totals():
    """WDDM path: nvidia-smi reports per-process memory as [N/A] (so it is omitted) and full paths
    (so they are basenamed), while typeperf supplies the dedicated/shared adapter totals. sys.platform
    is forced to win32 so the tail is exercised on a Linux CI runner too."""
    _reset_cache()

    def _dispatch(args, **kw):
        flag = args[1] if len(args) > 1 else ""
        if "--query-gpu" in flag:
            return _Result("50, 1298, 24576\n", 0)
        if "--query-compute-apps" in flag:
            return _Result("24724, C:\\Program Files\\Volksmond\\Volksmond.exe, [N/A]\n"
                           "88, C:\\Program Files\\Google\\Chrome\\chrome.exe, [N/A]\n", 0)
        if "Dedicated Usage" in flag:
            return _Result(_TYPEPERF_OUT, 0)
        return _Result("", 1)

    run = _recording(_dispatch)
    restore = _install(run, platform="win32")
    try:
        line = gpu_snapshot.snapshot()
    finally:
        restore()
    expected = ("gpu util=50% mem=1298/24576 MB "
                "apps: Volksmond.exe(pid 24724), chrome.exe(pid 88) "
                "dedicated=23800 MB shared=4200 MB")
    assert line == expected, line
    print("  OK  N/A per-process memory omitted, basenames kept, VRAM totals appended")


def test_typeperf_failure_never_fails_the_whole_snapshot():
    """The VRAM read is fail-soft: a typeperf that errors must leave the tail off, not lose the line."""
    _reset_cache()

    def _dispatch(args, **kw):
        flag = args[1] if len(args) > 1 else ""
        if "--query-gpu" in flag:
            return _Result(_GPU_OUT, 0)
        if "--query-compute-apps" in flag:
            return _Result(_APPS_OUT, 0)
        return _Result("", 1)      # typeperf fails

    run = _recording(_dispatch)
    restore = _install(run, platform="win32")
    try:
        line = gpu_snapshot.snapshot()
    finally:
        restore()
    assert line == _EXPECTED, line          # the nvidia-smi line survived, only the tail is gone
    print("  OK  a failed typeperf omits the VRAM tail and keeps the snapshot")


# --- 6. concurrency (H3): two callers probe once ---------------------------

def test_two_concurrent_callers_probe_exactly_once():
    """Lookup, probe and publish run under one lock, so two diagnostic threads arriving together
    cannot both probe: one probes, the other blocks then reads the cache."""
    _reset_cache()
    run = _recording(delay=0.05)                 # widen the probe window so the threads truly overlap
    restore = _install(run, platform="linux")    # Linux: exactly two calls per probe (gpu + apps)
    results = {}
    barrier = threading.Barrier(2)

    def _worker(key):
        barrier.wait()
        results[key] = gpu_snapshot.snapshot()

    try:
        threads = [threading.Thread(target=_worker, args=(k,)) for k in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
    finally:
        restore()
    assert len(run.calls) == 2, f"expected one probe (2 calls), got {len(run.calls)}"
    values = sorted(results.values(), key=lambda v: v.endswith("(cached)"))
    assert values[0] == _EXPECTED, values
    assert values[1] == _EXPECTED + " (cached)", values
    print("  OK  two concurrent callers share a single probe")


# --- 7. process-creation flags ---------------------------------------------

def test_create_no_window_and_timeout_are_passed_to_every_call():
    _reset_cache()

    def _dispatch(args, **kw):
        flag = args[1] if len(args) > 1 else ""
        if "--query-gpu" in flag:
            return _Result(_GPU_OUT, 0)
        if "--query-compute-apps" in flag:
            return _Result(_APPS_OUT, 0)
        if "Dedicated Usage" in flag:
            return _Result(_TYPEPERF_OUT, 0)
        return _Result("", 1)

    run = _recording(_dispatch)
    restore = _install(run, platform="win32")     # win32 so typeperf is probed too
    try:
        gpu_snapshot.snapshot()
    finally:
        restore()
    assert run.calls, "subprocess.run was never called"
    for argv, kwargs in run.calls:
        assert kwargs.get("creationflags") == gpu_snapshot._CREATE_NO_WINDOW, (argv, kwargs)
        assert kwargs.get("timeout") == gpu_snapshot._TIMEOUT_SECONDS, (argv, kwargs)
    if sys.platform == "win32":
        assert gpu_snapshot._CREATE_NO_WINDOW == 0x08000000, gpu_snapshot._CREATE_NO_WINDOW
    print("  OK  CREATE_NO_WINDOW and the 2 s timeout are passed to every external call")


# --- 8. the engine hook ----------------------------------------------------

def test_hook_fires_snapshot_off_thread_on_a_cuda_delivery():
    """The single delivery point (_deliver_struggle, non-None why) fires the snapshot for a CUDA
    session on its OWN daemon thread and prints one [gpu] line. Off-thread matters: nvidia-smi must
    never run on the audio or worker thread."""
    eng = _snapshot_engine("cuda")
    seen = {"called": 0, "thread": None}

    def _fake_snapshot():
        seen["called"] += 1
        seen["thread"] = threading.current_thread().name
        return "gpu util=50% mem=1/2 MB apps: none"

    buf = io.StringIO()
    saved_snap = _patch(transcribe.gpu_snapshot, "snapshot", _fake_snapshot)
    saved_out = sys.stdout
    try:
        sys.stdout = buf
        eng._deliver_struggle("queue 24 -> 30 of 32 over 12 completions")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and "[gpu]" not in buf.getvalue():
            time.sleep(0.01)
    finally:
        sys.stdout = saved_out
        transcribe.gpu_snapshot.snapshot = saved_snap
    out = buf.getvalue()
    assert "[gpu] gpu util=50% mem=1/2 MB apps: none" in out, out
    assert seen["called"] == 1, seen
    assert seen["thread"] and seen["thread"] != threading.main_thread().name, seen["thread"]
    print("  OK  a CUDA delivery prints one [gpu] line off the caller's thread")


def test_hook_is_skipped_for_cpu_and_mlx_sessions():
    """nvidia-smi is a CUDA thing: a CPU laptop and a Mac (MLX/Metal) session must never reach the
    snapshot at all, even when the same struggle notice is delivered."""
    for device in ("cpu", "mlx"):
        eng = _snapshot_engine(device)
        called = []
        buf = io.StringIO()
        saved_snap = _patch(transcribe.gpu_snapshot, "snapshot",
                            lambda: (called.append(1), "x")[1])
        saved_out = sys.stdout
        try:
            sys.stdout = buf
            eng._deliver_struggle("queue 24 -> 30 of 32 over 12 completions")
            time.sleep(0.1)
        finally:
            sys.stdout = saved_out
            transcribe.gpu_snapshot.snapshot = saved_snap
        assert called == [], f"the snapshot ran on a {device} session"
        assert "[gpu]" not in buf.getvalue(), buf.getvalue()
    print("  OK  no snapshot on a CPU or MLX session")


if __name__ == "__main__":
    tests = (test_formats_the_line_from_canned_smi_output,
             test_none_when_nvidia_smi_is_missing_without_touching_subprocess,
             test_none_on_nonzero_exit_reaches_the_mock,
             test_none_on_timeout_reaches_the_mock,
             test_none_on_a_non_windows_non_linux_platform_without_touching_subprocess,
             test_cache_returns_the_cached_line_within_the_window_and_spawns_no_probe,
             test_cache_expires_after_the_window_and_probes_again,
             test_a_comma_in_a_process_path_never_leaks_a_directory,
             test_normalise_number_handles_locale_separators,
             test_typeperf_parses_locale_comma_decimals_and_sums_instances,
             test_wddm_omits_na_process_memory_and_appends_the_typeperf_vram_totals,
             test_typeperf_failure_never_fails_the_whole_snapshot,
             test_two_concurrent_callers_probe_exactly_once,
             test_create_no_window_and_timeout_are_passed_to_every_call,
             test_hook_fires_snapshot_off_thread_on_a_cuda_delivery,
             test_hook_is_skipped_for_cpu_and_mlx_sessions)
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll gpu-snapshot tests passed.")
