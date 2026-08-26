"""accel.py tests: the platform-neutral accelerator probes.

The invariants (same discipline as the cudadl gate in test_paths.py):
- mlx_supported()/mlx_ready() answer correctly per platform and NEVER import
  mlx, mlx_whisper or ctranslate2 (find_spec only).
- asr_backend() honours a forced CPU, prefers CUDA, then mlx, then CPU.
- On win32 the summary helpers delegate to today's cudadl calls, so the
  summariser conjunction evaluates to exactly the old expression.
- On darwin the summary helpers report the Metal + unified-memory truth.

Run:  python tests/test_accel.py   (from the project root; exit 0 = pass)
"""
import contextlib
import importlib
import importlib.util
import os
import platform
import sys

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import accel, cudadl

_HEAVY = ("mlx", "mlx_whisper", "ctranslate2")


@contextlib.contextmanager
def _fake_platform(sys_platform, machine):
    """Patch sys.platform + platform.machine, reload cudadl so its SUPPORTED gate
    matches (accel holds a module reference; reload is in-place so it stays valid),
    and clear accel's probe cache. Everything is restored on exit."""
    orig_platform = sys.platform
    orig_machine = platform.machine
    saved = {name: sys.modules.pop(name, None) for name in _HEAVY}
    try:
        sys.platform = sys_platform
        platform.machine = lambda: machine
        importlib.reload(cudadl)
        accel._PROBE.clear()
        yield
    finally:
        sys.platform = orig_platform
        platform.machine = orig_machine
        importlib.reload(cudadl)
        accel._PROBE.clear()
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod


def test_mlx_supported_matrix():
    with _fake_platform("darwin", "arm64"):
        assert accel.mlx_supported() is True, "darwin-arm64 must support mlx"
    with _fake_platform("darwin", "x86_64"):
        assert accel.mlx_supported() is False, "Intel Macs must not support mlx"
    with _fake_platform("win32", "AMD64"):
        assert accel.mlx_supported() is False, "win32 must not support mlx"
    print("  OK  mlx_supported: True only on darwin-arm64")


def test_mlx_ready_uses_find_spec_only():
    orig_find_spec = importlib.util.find_spec
    with _fake_platform("darwin", "arm64"):
        try:
            importlib.util.find_spec = lambda name: None
            assert accel.mlx_ready() is False, "mlx_ready must be False when find_spec finds nothing"
            importlib.util.find_spec = lambda name: object()   # pretend installed
            accel._PROBE.clear()
            assert accel.mlx_ready() is True, "mlx_ready must be True when the package resolves"
        finally:
            importlib.util.find_spec = orig_find_spec
    print("  OK  mlx_ready follows find_spec (no import), False when absent")


def test_probes_import_nothing_heavy():
    # Call EVERY probe on a faked darwin-arm64 and prove none of the heavy modules
    # (mlx, mlx_whisper, ctranslate2) lands in sys.modules. find_spec resolves
    # without importing; cudadl is reloaded darwin so cuda_ready short-circuits.
    with _fake_platform("darwin", "arm64"):
        accel.mlx_supported()
        accel.mlx_ready()
        accel.asr_backend("auto")
        accel.summary_gpu_ready()
        accel.summary_vram_mb()
        for name in _HEAVY:
            assert name not in sys.modules, f"a probe imported {name}"
    print("  OK  probes import none of mlx / mlx_whisper / ctranslate2")


def test_asr_backend_order():
    with _fake_platform("darwin", "arm64"):
        orig_ready = accel.mlx_ready
        orig_cuda = cudadl.cuda_ready
        try:
            accel.mlx_ready = lambda: True
            # Forced CPU wins over everything.
            cudadl.cuda_ready = lambda: True
            assert accel.asr_backend("cpu") == "cpu", "forced cpu must win"
            # CUDA beats mlx when both are usable (never true in real life, but
            # pins the precedence).
            assert accel.asr_backend("auto") == "cuda"
            # mlx when CUDA is not usable.
            cudadl.cuda_ready = lambda: False
            assert accel.asr_backend("auto") == "mlx"
            assert accel.asr_backend("gpu") == "mlx"
            # CPU when nothing is ready.
            accel.mlx_ready = lambda: False
            assert accel.asr_backend("auto") == "cpu"
        finally:
            accel.mlx_ready = orig_ready
            cudadl.cuda_ready = orig_cuda
    print("  OK  asr_backend: forced cpu > cuda > mlx > cpu")


def test_win32_delegates_to_cudadl():
    with _fake_platform("win32", "AMD64"):
        orig_present = cudadl.gpu_present
        orig_vram = cudadl.vram_mb
        try:
            cudadl.gpu_present = lambda: True
            cudadl.vram_mb = lambda: 4096
            assert accel.summary_gpu_ready() is True
            assert accel.summary_vram_mb() == 4096
            cudadl.gpu_present = lambda: False
            assert accel.summary_gpu_ready() is False
        finally:
            cudadl.gpu_present = orig_present
            cudadl.vram_mb = orig_vram
    print("  OK  win32 summary helpers delegate to cudadl.gpu_present/vram_mb")


def test_darwin_summary_helpers():
    import psutil
    with _fake_platform("darwin", "arm64"):
        assert accel.summary_gpu_ready() is True, "Apple silicon is always summary-GPU-ready"
        orig_vm = psutil.virtual_memory
        try:
            class _VM:
                total = 16384 * 2**20
            psutil.virtual_memory = lambda: _VM()
            assert accel.summary_vram_mb() == 16384, accel.summary_vram_mb()
        finally:
            psutil.virtual_memory = orig_vm
    print("  OK  darwin summary helpers: gpu_ready True, vram = total RAM in MB")


def test_this_platform_is_consistent():
    # On the real (unpatched) platform the helpers must agree with the gates:
    # on Windows mlx is never supported and the summariser sees cudadl's answers.
    if sys.platform == "win32":
        assert accel.mlx_supported() is False
        assert accel.mlx_ready() is False
        assert accel.summary_gpu_ready() == cudadl.gpu_present()
        assert accel.summary_vram_mb() == cudadl.vram_mb()
    print("  OK  real-platform helpers agree with the cudadl gate")


if __name__ == "__main__":
    failures = 0
    for fn in (test_mlx_supported_matrix,
               test_mlx_ready_uses_find_spec_only,
               test_probes_import_nothing_heavy,
               test_asr_backend_order,
               test_win32_delegates_to_cudadl,
               test_darwin_summary_helpers,
               test_this_platform_is_consistent):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll accel tests passed.")
