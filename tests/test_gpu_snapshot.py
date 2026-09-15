"""Tests for the GPU usage snapshot (live_transcribe/gpu_snapshot.py) and its one hook in the
engine's struggle-delivery point (transcribe.Engine._deliver_struggle -> _fire_gpu_snapshot).

The snapshot names what else is on the NVIDIA card when a CUDA session is struggling, so the next
"it fell behind on my 3090" report can be root-caused from the log. It shells out to nvidia-smi, so
every test that touches the module monkeypatches subprocess.run (or _find_nvidia_smi) rather than
running the real binary, and each resets the module cache first so the 60 s rate limiter is
deterministic. No audio, no model, no real capture: the engine hook is driven on a stub Engine built
via __new__, exactly like the struggle-signal tests.

Covered:
  1. The formatted line from canned nvidia-smi output (utilisation + memory + compute apps, process
     basenames only).
  2. None when nvidia-smi is missing, on a non-zero exit, and on a timeout.
  3. None on a non-Windows/non-Linux platform, WITHOUT touching subprocess (the Mac/MLX guard).
  4. The 60 s cache: a second call inside the window returns the cached line with "(cached)" and
     runs no second nvidia-smi.
  5. CREATE_NO_WINDOW is passed to subprocess.run on Windows (kwargs inspected).
  6. The engine hook fires the snapshot OFF the caller's thread on a delivered notice for a CUDA
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


def _fake_run_factory(record=None):
    """A subprocess.run stand-in that answers each nvidia-smi query from the canned output, keyed off
    the query flag in argv. `record` (a list) captures each call's kwargs so a test can inspect
    creationflags."""
    def _fake_run(args, **kwargs):
        if record is not None:
            record.append(kwargs)
        flag = args[1] if len(args) > 1 else ""
        if "--query-gpu" in flag:
            return _Result(_GPU_OUT, 0)
        if "--query-compute-apps" in flag:
            return _Result(_APPS_OUT, 0)
        return _Result("", 1)
    return _fake_run


def _patch(obj, name, value):
    """Save-and-return an attribute so a finally can restore it. Manual, so the file runs both as a
    plain script and under pytest (no monkeypatch fixture)."""
    saved = getattr(obj, name)
    setattr(obj, name, value)
    return saved


def _snapshot_engine(device="cuda"):
    """A minimal Engine carrying only what _deliver_struggle / _fire_gpu_snapshot read: the device,
    the (absent) struggle callback and the one-shot flag. Built via __new__ so no model loads."""
    eng = transcribe.Engine.__new__(transcribe.Engine)
    eng._device = device
    eng.on_struggle = None
    eng._gpu_snapshot_fired = False
    return eng


# --- 1. the module ---------------------------------------------------------

def test_formats_the_line_from_canned_smi_output():
    _reset_cache()
    saved = _patch(gpu_snapshot.subprocess, "run", _fake_run_factory())
    try:
        line = gpu_snapshot.snapshot()
    finally:
        gpu_snapshot.subprocess.run = saved
    assert line == _EXPECTED, line
    print("  OK  formats util + memory + compute apps, basenames only")


def test_none_when_nvidia_smi_is_missing():
    _reset_cache()
    saved = _patch(gpu_snapshot, "_find_nvidia_smi", lambda: None)
    try:
        line = gpu_snapshot.snapshot()
    finally:
        gpu_snapshot._find_nvidia_smi = saved
    assert line is None, line
    print("  OK  None when nvidia-smi is not found (a CPU laptop / AMD / Intel machine)")


def test_none_on_nonzero_exit():
    _reset_cache()
    saved = _patch(gpu_snapshot.subprocess, "run", lambda args, **kw: _Result("", 1))
    try:
        line = gpu_snapshot.snapshot()
    finally:
        gpu_snapshot.subprocess.run = saved
    assert line is None, line
    print("  OK  None when nvidia-smi exits non-zero")


def test_none_on_timeout():
    _reset_cache()

    def _boom(args, **kw):
        raise subprocess.TimeoutExpired(cmd=args, timeout=gpu_snapshot._TIMEOUT_SECONDS)

    saved = _patch(gpu_snapshot.subprocess, "run", _boom)
    try:
        line = gpu_snapshot.snapshot()
    finally:
        gpu_snapshot.subprocess.run = saved
    assert line is None, line
    print("  OK  None when an nvidia-smi call times out")


def test_none_on_a_non_windows_non_linux_platform_without_touching_subprocess():
    _reset_cache()
    calls = []
    saved_plat = _patch(gpu_snapshot.sys, "platform", "darwin")
    saved_run = _patch(gpu_snapshot.subprocess, "run", lambda *a, **k: calls.append(1))
    try:
        line = gpu_snapshot.snapshot()
    finally:
        gpu_snapshot.subprocess.run = saved_run
        gpu_snapshot.sys.platform = saved_plat
    assert line is None, line
    assert calls == [], "nvidia-smi must never be spawned on macOS (the MLX/Metal backend)"
    print("  OK  None on macOS, and subprocess is never touched")


def test_cache_returns_the_cached_line_within_the_window_and_runs_smi_once():
    _reset_cache()
    calls = []

    def _counting(args, **kw):
        calls.append(1)
        return _fake_run_factory()(args, **kw)

    saved = _patch(gpu_snapshot.subprocess, "run", _counting)
    try:
        first = gpu_snapshot.snapshot()
        after_first = len(calls)
        second = gpu_snapshot.snapshot()
    finally:
        gpu_snapshot.subprocess.run = saved
    assert first == _EXPECTED, first
    assert second == _EXPECTED + " (cached)", second
    # The cached second call spawns NO further processes, whatever the first cost (2 on Linux, 3 on
    # Windows where typeperf is also probed). That is what keeps a 45-chunk drop burst to one probe.
    assert len(calls) == after_first, (after_first, len(calls))
    print("  OK  a second call inside 60 s returns the cached line and spawns no further probe")


def test_wddm_omits_na_process_memory_and_appends_the_typeperf_vram_totals():
    """WDDM path: nvidia-smi reports per-process memory as [N/A] (so it is omitted) and full paths
    (so they are basenamed), while typeperf supplies the dedicated/shared adapter totals. sys.platform
    is forced to win32 so the tail is exercised on a Linux CI runner too."""
    _reset_cache()

    def _fake_run(args, **kw):
        flag = args[1] if len(args) > 1 else ""
        if "--query-gpu" in flag:
            return _Result("50, 1298, 24576\n", 0)
        if "--query-compute-apps" in flag:
            return _Result("24724, C:\\Program Files\\Volksmond\\Volksmond.exe, [N/A]\n"
                           "88, C:\\Program Files\\Google\\Chrome\\chrome.exe, [N/A]\n", 0)
        if "Dedicated Usage" in flag:
            return _Result(_TYPEPERF_OUT, 0)
        return _Result("", 1)

    saved_plat = _patch(gpu_snapshot.sys, "platform", "win32")
    saved_run = _patch(gpu_snapshot.subprocess, "run", _fake_run)
    try:
        line = gpu_snapshot.snapshot()
    finally:
        gpu_snapshot.subprocess.run = saved_run
        gpu_snapshot.sys.platform = saved_plat
    expected = ("gpu util=50% mem=1298/24576 MB "
                "apps: Volksmond.exe(pid 24724), chrome.exe(pid 88) "
                "dedicated=23800 MB shared=4200 MB")
    assert line == expected, line
    print("  OK  N/A per-process memory omitted, basenames kept, VRAM totals appended")


def test_parse_typeperf_sums_across_instances_and_ignores_a_failed_read():
    # Two adapter instances per counter; the totals sum. Values in bytes.
    two = (
        '"(PDH-CSV 4.0)",'
        '"\\\\H\\GPU Adapter Memory(a)\\Dedicated Usage","\\\\H\\GPU Adapter Memory(a)\\Shared Usage",'
        '"\\\\H\\GPU Adapter Memory(b)\\Dedicated Usage","\\\\H\\GPU Adapter Memory(b)\\Shared Usage"\n'
        '"ts","1048576.000000","2097152.000000","3145728.000000","0.000000"\n'
    )
    assert gpu_snapshot._parse_typeperf(two) == (4, 2), gpu_snapshot._parse_typeperf(two)
    # Garbage / a header with no data row parses to None, which the caller turns into an omitted tail.
    assert gpu_snapshot._parse_typeperf("no counters here") is None
    assert gpu_snapshot._parse_typeperf('"(PDH-CSV 4.0)","\\\\H\\Other(a)\\Foo"\n"ts","5"\n') is None
    print("  OK  typeperf instances sum, an unparseable sample is None")


def test_typeperf_failure_never_fails_the_whole_snapshot():
    """The VRAM read is fail-soft: a typeperf that errors must leave the tail off, not lose the line."""
    _reset_cache()

    def _fake_run(args, **kw):
        flag = args[1] if len(args) > 1 else ""
        if "--query-gpu" in flag:
            return _Result(_GPU_OUT, 0)
        if "--query-compute-apps" in flag:
            return _Result(_APPS_OUT, 0)
        return _Result("", 1)      # typeperf fails

    saved_plat = _patch(gpu_snapshot.sys, "platform", "win32")
    saved_run = _patch(gpu_snapshot.subprocess, "run", _fake_run)
    try:
        line = gpu_snapshot.snapshot()
    finally:
        gpu_snapshot.subprocess.run = saved_run
        gpu_snapshot.sys.platform = saved_plat
    assert line == _EXPECTED, line          # the nvidia-smi line survived, only the tail is gone
    print("  OK  a failed typeperf omits the VRAM tail and keeps the snapshot")


def test_create_no_window_is_passed_on_windows():
    _reset_cache()
    record = []
    saved = _patch(gpu_snapshot.subprocess, "run", _fake_run_factory(record))
    try:
        gpu_snapshot.snapshot()
    finally:
        gpu_snapshot.subprocess.run = saved
    assert record, "subprocess.run was never called"
    for kwargs in record:
        assert kwargs.get("creationflags") == gpu_snapshot._CREATE_NO_WINDOW, kwargs
        assert kwargs.get("timeout") == gpu_snapshot._TIMEOUT_SECONDS, kwargs
    if sys.platform == "win32":
        assert gpu_snapshot._CREATE_NO_WINDOW == 0x08000000, gpu_snapshot._CREATE_NO_WINDOW
    print("  OK  CREATE_NO_WINDOW and the 2 s timeout are passed to every nvidia-smi call")


# --- 2. the engine hook ----------------------------------------------------

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
             test_none_when_nvidia_smi_is_missing,
             test_none_on_nonzero_exit,
             test_none_on_timeout,
             test_none_on_a_non_windows_non_linux_platform_without_touching_subprocess,
             test_cache_returns_the_cached_line_within_the_window_and_runs_smi_once,
             test_wddm_omits_na_process_memory_and_appends_the_typeperf_vram_totals,
             test_parse_typeperf_sums_across_instances_and_ignores_a_failed_read,
             test_typeperf_failure_never_fails_the_whole_snapshot,
             test_create_no_window_is_passed_on_windows,
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
