"""Platform shim tests: the shared data-dir mapping + the cudadl platform gate.

paths.data_dir() must resolve to the EXACT historical Windows folder (character
identical to the inline expression it replaced, so nothing moves for existing
installs), the macOS Application Support folder on darwin, and the XDG-style
folder everywhere else. The cudadl gate must make every GPU probe report
False/None on non-Windows platforms WITHOUT importing ctranslate2 or shelling
nvidia-smi, and the download entry point must refuse.

Run:  python tests/test_paths.py   (from the project root; exit 0 = pass)
"""
import importlib
import os
import sys
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import paths


def test_data_dir_windows_is_character_identical():
    # The exact pre-paths.py expression, verbatim. If this ever diverges, existing
    # installs would lose their settings, licence and models.
    legacy = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "sa-live-transcribe"
    assert str(paths.data_dir_for("win32")) == str(legacy), \
        f"win32 data dir moved: {paths.data_dir_for('win32')} != {legacy}"
    print("  OK  win32 data dir is character-identical to the legacy expression")


def test_data_dir_darwin():
    expect = Path.home() / "Library" / "Application Support" / "Volksmond"
    assert paths.data_dir_for("darwin") == expect, paths.data_dir_for("darwin")
    print("  OK  darwin data dir is ~/Library/Application Support/Volksmond")


def test_data_dir_other():
    expect = Path.home() / ".local" / "share" / "volksmond"
    for plat in ("linux", "linux2", "freebsd13"):
        assert paths.data_dir_for(plat) == expect, (plat, paths.data_dir_for(plat))
    print("  OK  other platforms get ~/.local/share/volksmond")


def test_data_dir_follows_sys_platform():
    assert paths.data_dir() == paths.data_dir_for(sys.platform), \
        (paths.data_dir(), sys.platform)
    print("  OK  data_dir() dispatches on sys.platform")


def test_default_sessions_dir_windows():
    # Frozen Windows saves to a visible per-user folder, NOT app data (buried,
    # and removed on uninstall under the MSIX edition, which virtualises AppData
    # writes) and NOT Documents (commonly OneDrive-redirected, which would
    # undermine the local-only posture).
    expect = Path.home() / "Volksmond"
    assert paths.default_sessions_dir_for("win32") == expect, \
        paths.default_sessions_dir_for("win32")
    print("  OK  win32 default sessions dir is ~/Volksmond (USERPROFILE)")


def test_default_sessions_dir_darwin_and_linux_unchanged():
    # macOS and Linux deliberately keep sessions under the data dir (~/Volksmond
    # is unidiomatic on macOS and violates the XDG posture on Linux).
    for plat in ("darwin", "linux", "linux2"):
        expect = paths.data_dir_for(plat) / "sessions"
        assert paths.default_sessions_dir_for(plat) == expect, \
            (plat, paths.default_sessions_dir_for(plat))
    print("  OK  darwin/linux default sessions dir stays under the data dir")


def test_default_sessions_dir_follows_sys_platform():
    assert paths.default_sessions_dir() == paths.default_sessions_dir_for(sys.platform), \
        (paths.default_sessions_dir(), sys.platform)
    print("  OK  default_sessions_dir() dispatches on sys.platform")


def test_callers_share_the_data_dir():
    # The five migrated call sites must all hang off the same folder. config._DIR and
    # licensing._LICENSE_PATH are module-level singletons frozen at import time, and an
    # earlier test in the same pytest process may have repointed them at a throwaway dir
    # (test_modeldl._isolate does exactly that), so reload the modules first: the assertion
    # is about the WIRING (everything resolves via paths.data_dir()), not about whatever
    # state a previous test left behind. Reload is in-place, so held references stay valid.
    from live_transcribe import config, cudadl, licensing
    importlib.reload(config)
    importlib.reload(licensing)
    base = paths.data_dir()
    assert config._DIR == base, config._DIR
    assert licensing._LICENSE_PATH == base / "license.key", licensing._LICENSE_PATH
    assert cudadl.cuda_dir() == base / "cuda", cudadl.cuda_dir()
    print("  OK  config, licensing and cudadl all resolve under paths.data_dir()")


def test_cudadl_gate_on_this_platform():
    from live_transcribe import cudadl
    assert cudadl.SUPPORTED is (sys.platform == "win32"), cudadl.SUPPORTED
    print("  OK  cudadl.SUPPORTED matches sys.platform (win32 -> True)")


def test_cudadl_gate_non_windows():
    # Simulate macOS: reload cudadl with sys.platform patched to "darwin" and prove
    # every probe short-circuits (no ctranslate2 import, no nvidia-smi subprocess),
    # the DLL registration no-ops, and the download entry point refuses.
    import live_transcribe.cudadl as cudadl
    saved_ct2 = sys.modules.pop("ctranslate2", None)  # so a stray import is detectable
    orig_platform = sys.platform
    try:
        sys.platform = "darwin"
        importlib.reload(cudadl)
        assert cudadl.SUPPORTED is False, "SUPPORTED must be False on darwin"
        assert cudadl.gpu_present() is False
        assert cudadl.cuda_ready() is False
        assert cudadl.installed() is False
        assert cudadl.vram_mb() is None
        assert cudadl.gpu_name() is None
        assert cudadl.register_dll_dir() is None      # no-op, must not raise
        ok, err = cudadl.self_test()
        assert ok is False and err, (ok, err)
        try:
            cudadl.start_download()
            raise SystemExit("start_download did not refuse on an unsupported platform")
        except RuntimeError:
            pass
        assert "ctranslate2" not in sys.modules, "a gated probe imported ctranslate2"
        assert cudadl._PROBE == {}, "a gated probe still cached a hardware result"
    finally:
        sys.platform = orig_platform
        importlib.reload(cudadl)                      # restore real (win32) behaviour
        if saved_ct2 is not None:
            sys.modules["ctranslate2"] = saved_ct2
    assert cudadl.SUPPORTED is (sys.platform == "win32")
    print("  OK  darwin gate: probes False/None, no ctranslate2 import, no-ops, download refused")


def test_gpu_vram_probe_gated():
    # __main__._gpu_vram_mb must short-circuit to None off-Windows (never shell
    # nvidia-smi). Prove it via the same reload trick.
    import live_transcribe.cudadl as cudadl
    from live_transcribe.__main__ import _gpu_vram_mb
    orig_platform = sys.platform
    try:
        sys.platform = "darwin"
        importlib.reload(cudadl)
        assert _gpu_vram_mb() is None, "_gpu_vram_mb probed on an unsupported platform"
    finally:
        sys.platform = orig_platform
        importlib.reload(cudadl)
    print("  OK  _gpu_vram_mb returns None behind the gate (no nvidia-smi off-Windows)")


if __name__ == "__main__":
    failures = 0
    for fn in (test_data_dir_windows_is_character_identical,
               test_data_dir_darwin,
               test_data_dir_other,
               test_data_dir_follows_sys_platform,
               test_default_sessions_dir_windows,
               test_default_sessions_dir_darwin_and_linux_unchanged,
               test_default_sessions_dir_follows_sys_platform,
               test_callers_share_the_data_dir,
               test_cudadl_gate_on_this_platform,
               test_cudadl_gate_non_windows,
               test_gpu_vram_probe_gated):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll paths/platform-gate tests passed.")
