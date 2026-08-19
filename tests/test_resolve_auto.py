"""WP-3: resolve_tier_engine - "Auto" prefers a DOWNLOADED model, crossing families and even using an
above-ceiling model when that is what avoids a surprise download at Begin (the MS Store cert win).

resolve_tier_engine(quality, device, language, engine) -> (tier, engine_override):
  - explicit quality is honoured, override None, no crossing;
  - auto prefers the language-family's downloaded size (within the CPU live ceiling, any size on GPU);
  - auto crosses to the OTHER usable family (engine_override set) when the preferred family has nothing
    downloaded but the other does;
  - on CPU, an above-ceiling-only download (e.g. large-v3) is USED rather than downloading the ceiling;
  - a South African (Swivuriso) session is never crossed off onto stock Whisper;
  - the resolve_tier wrapper still returns just the tier for existing callers.

Deterministic: cuda_ready, the on-disk check (_downloaded_sizes) and the CPU core-count pick
(_cpu_auto_tier) are all stubbed, so no real GPU or disk is touched.

Run:  python tests/test_resolve_auto.py   (from the project root; exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import __main__ as M
from live_transcribe import cudadl as _cudadl
from live_transcribe.transcribe import TIER_CONFIG


def _model(tier):
    return TIER_CONFIG[tier]["model"]


def test_gpu_matrix():
    # GPU ready: any downloaded size is usable (no real-time ceiling on the GPU).
    orig = (_cudadl.cuda_ready, M._downloaded_sizes)
    _cudadl.cuda_ready = lambda: True
    try:
        # af -> fluister. Preferred family has a download -> use its best, no crossing.
        M._downloaded_sizes = lambda fam: {"medium"} if fam == "fluister" else set()
        assert M.resolve_tier_engine("auto", "auto", "af") == ("gpu-medium", None)
        # af, fluister EMPTY but whisper has a size -> cross to whisper (engine_override="whisper").
        M._downloaded_sizes = lambda fam: {"large-v3"} if fam == "whisper" else set()
        tier, override = M.resolve_tier_engine("auto", "auto", "af")
        assert (_model(tier), override) == ("large-v3", "whisper"), (tier, override)
        # en, whisper EMPTY but fluister has a size -> cross to fluister.
        M._downloaded_sizes = lambda fam: {"small"} if fam == "fluister" else set()
        tier, override = M.resolve_tier_engine("auto", "auto", "en")
        assert (_model(tier), override) == ("small", "fluister"), (tier, override)
        # NOTHING downloaded anywhere -> today's ambitious best-size pick, no crossing.
        M._downloaded_sizes = lambda fam: set()
        assert M.resolve_tier_engine("auto", "auto", "af") == ("gpu-turbo", None)
        assert M.resolve_tier_engine("auto", "auto", "en") == ("gpu", None)   # gpu tier = large-v3
        # SA language (Swivuriso): never crossed to Whisper even with whisper models on disk.
        M._downloaded_sizes = lambda fam: {"large-v3"} if fam == "whisper" else set()
        assert M.resolve_tier_engine("auto", "auto", "zu") == ("gpu", None)
        # Explicit quality honoured, override None, regardless of downloads.
        M._downloaded_sizes = lambda fam: set()
        assert M.resolve_tier_engine("medium", "auto", "af") == ("gpu-medium", None)
    finally:
        _cudadl.cuda_ready, M._downloaded_sizes = orig
    print("  OK  GPU: family-downloaded pick, cross-family override, swivuriso never crossed, explicit honoured")


def test_cpu_matrix():
    # CPU (no usable GPU): prefer a within-ceiling download; else use an above-ceiling download anyway
    # (cert win); else cross families; else the ambitious core-count pick.
    orig = (_cudadl.cuda_ready, M._downloaded_sizes, M._cpu_auto_tier)
    _cudadl.cuda_ready = lambda: False
    try:
        M._cpu_auto_tier = lambda: "cpu-mid"          # >=8-core box, ceiling = medium
        # af, fluister medium within ceiling -> cpu-mid, no crossing.
        M._downloaded_sizes = lambda fam: {"medium"} if fam == "fluister" else set()
        assert M.resolve_tier_engine("auto", "cpu", "af") == ("cpu-mid", None)
        # af, fluister has ONLY above-ceiling large-v3 -> use it (cpu-large), no crossing, no download.
        M._downloaded_sizes = lambda fam: {"large-v3"} if fam == "fluister" else set()
        assert M.resolve_tier_engine("auto", "cpu", "af") == ("cpu-large", None)
        # af, fluister EMPTY, whisper has small -> cross to whisper.
        M._downloaded_sizes = lambda fam: {"small"} if fam == "whisper" else set()
        assert M.resolve_tier_engine("auto", "cpu", "af") == ("cpu", "whisper")
        # af, fluister EMPTY, whisper has ONLY above-ceiling large-v3 -> cross AND use it (cpu-large).
        # This is the WP-3 acceptance case: Afrikaans + only stock large-v3 on disk.
        M._downloaded_sizes = lambda fam: {"large-v3"} if fam == "whisper" else set()
        assert M.resolve_tier_engine("auto", "cpu", "af") == ("cpu-large", "whisper")
        # NOTHING downloaded -> ambitious core-count pick, no crossing.
        M._downloaded_sizes = lambda fam: set()
        assert M.resolve_tier_engine("auto", "cpu", "af") == ("cpu-mid", None)
        # SA language: never cross to a downloaded whisper; stays on the ambitious pick (downloads Swivuriso).
        M._downloaded_sizes = lambda fam: {"large-v3"} if fam == "whisper" else set()
        assert M.resolve_tier_engine("auto", "cpu", "zu") == ("cpu-mid", None)
        # Weak CPU (ceiling = small): only an above-ceiling medium -> use it (cpu-mid), no download.
        M._cpu_auto_tier = lambda: "cpu"
        M._downloaded_sizes = lambda fam: {"medium"} if fam == "fluister" else set()
        assert M.resolve_tier_engine("auto", "cpu", "af") == ("cpu-mid", None)
        # Explicit quality honoured, override None.
        assert M.resolve_tier_engine("large-v3", "cpu", "en") == ("cpu-large", None)
    finally:
        _cudadl.cuda_ready, M._downloaded_sizes, M._cpu_auto_tier = orig
    print("  OK  CPU: within-ceiling pick, above-ceiling use, cross-family override, swivuriso caveat, explicit honoured")


def test_explicit_engine_pref_is_never_crossed():
    # A user who explicitly picks an engine family is never silently crossed off it, even when nothing
    # is downloaded for it (Begin then downloads it).
    orig = (_cudadl.cuda_ready, M._downloaded_sizes)
    _cudadl.cuda_ready = lambda: True
    try:
        M._downloaded_sizes = lambda fam: {"large-v3"} if fam == "whisper" else set()
        # engine="fluister" explicitly, fluister empty, whisper full: must NOT cross to whisper.
        tier, override = M.resolve_tier_engine("auto", "auto", "af", "fluister")
        assert override is None, (tier, override)
        assert _model(tier) == "large-v3-turbo", tier   # fluister best-size fallback (downloads on demand)
    finally:
        _cudadl.cuda_ready, M._downloaded_sizes = orig
    print("  OK  an explicit engine pref is never crossed to another family")


def test_wrapper_unchanged_for_existing_callers():
    # resolve_tier is a thin wrapper returning JUST the tier (a string), so every existing caller/test
    # keeps working; it equals resolve_tier_engine(...)[0] for the same inputs.
    orig = (_cudadl.cuda_ready, M._downloaded_sizes, M._cpu_auto_tier)
    _cudadl.cuda_ready = lambda: False
    try:
        M._cpu_auto_tier = lambda: "cpu-mid"
        for args in (("small", "cpu"), ("large-v3", "cpu"), ("auto", "cpu", "af"), ("auto", "cpu", "en")):
            M._downloaded_sizes = lambda fam: {"medium"}
            t = M.resolve_tier(*args)
            assert isinstance(t, str) and t in TIER_CONFIG, (args, t)
            assert t == M.resolve_tier_engine(*args)[0], (args, t)
    finally:
        _cudadl.cuda_ready, M._downloaded_sizes, M._cpu_auto_tier = orig
    print("  OK  resolve_tier wrapper returns just the tier and matches resolve_tier_engine()[0]")


if __name__ == "__main__":
    tests = (test_gpu_matrix,
             test_cpu_matrix,
             test_explicit_engine_pref_is_never_crossed,
             test_wrapper_unchanged_for_existing_callers)
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
    print("\nAll resolve-auto tests passed.")
