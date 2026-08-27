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


def test_mlx_auto_ladder():
    # WP-M4 / D6: on darwin-arm64 with mlx-whisper ready (accel stubbed), "auto" runs the honesty
    # ladder: (1) downloaded MLX model -> mlx tier; (2) downloaded ct2 size -> today's CPU logic;
    # (3) cross-family, the other family's MLX first then its ct2; (4) nothing -> ambitious MLX pick.
    # TWO stores are modelled (codex M3): _downloaded_sizes is the CT2 store only, and the MLX
    # store is voicedl._mlx_present keyed by the exact MLX repo id, exactly like production.
    from live_transcribe import accel as _accel
    from live_transcribe import voicedl as _V
    FL_MLX = "digiphyte/fluister-turbo-mlx"
    WH_MLX = "mlx-community/whisper-large-v3-mlx"
    orig = (_cudadl.cuda_ready, M._downloaded_sizes, M._cpu_auto_tier, _accel.asr_backend,
            _V._mlx_present)
    _cudadl.cuda_ready = lambda: False
    _accel.asr_backend = lambda pref="auto": "cpu" if pref == "cpu" else "mlx"
    mlx_store = set()   # of MLX repo ids "on disk"
    _V._mlx_present = lambda repo: repo in mlx_store
    try:
        M._cpu_auto_tier = lambda: "cpu-mid"
        # (1) af + the Fluister MLX turbo downloaded (no ct2 anywhere) -> mlx-turbo, no crossing.
        M._downloaded_sizes = lambda fam: set()
        mlx_store = {FL_MLX}
        assert M.resolve_tier_engine("auto", "auto", "af") == ("mlx-turbo", None)
        # (1) en + the stock MLX large-v3 downloaded -> mlx.
        mlx_store = {WH_MLX}
        assert M.resolve_tier_engine("auto", "auto", "en") == ("mlx", None)
        # (2) af + only a ct2 medium downloaded (MLX store empty) -> today's CPU answer, verbatim.
        mlx_store = set()
        M._downloaded_sizes = lambda fam: {"medium"} if fam == "fluister" else set()
        assert M.resolve_tier_engine("auto", "auto", "af") == ("cpu-mid", None)
        # (2, codex M3) a cached CT2 copy of the MAPPED size with NO MLX download must land on
        # the CPU ladder (never claim the MLX model is downloaded, never re-download).
        M._downloaded_sizes = lambda fam: {"large-v3-turbo"} if fam == "fluister" else set()
        assert M.resolve_tier_engine("auto", "auto", "af") == ("cpu-strong", None)
        M._downloaded_sizes = lambda fam: {"large-v3"} if fam == "whisper" else set()
        assert M.resolve_tier_engine("auto", "auto", "en") == ("cpu-large", None)
        # (3) af, fluister EMPTY, whisper's MLX model downloaded -> cross to whisper on mlx.
        M._downloaded_sizes = lambda fam: set()
        mlx_store = {WH_MLX}
        assert M.resolve_tier_engine("auto", "auto", "af") == ("mlx", "whisper")
        # (3) MLX beats ct2 within the crossed family: whisper has BOTH -> mlx, not cpu.
        M._downloaded_sizes = lambda fam: {"small"} if fam == "whisper" else set()
        assert M.resolve_tier_engine("auto", "auto", "af") == ("mlx", "whisper")
        # (3) crossed family with only ct2 -> its CPU tier, override still set.
        mlx_store = set()
        assert M.resolve_tier_engine("auto", "auto", "af") == ("cpu", "whisper")
        # (4) NOTHING downloaded anywhere -> the ambitious MLX pick (download is legitimate).
        M._downloaded_sizes = lambda fam: set()
        assert M.resolve_tier_engine("auto", "auto", "af") == ("mlx-turbo", None)
        assert M.resolve_tier_engine("auto", "auto", "en") == ("mlx", None)
        # SA language (Swivuriso): the existing CPU behaviour, never an mlx tier, never crossed.
        mlx_store = {FL_MLX, WH_MLX}
        M._downloaded_sizes = lambda fam: {"large-v3"} if fam == "whisper" else set()
        assert M.resolve_tier_engine("auto", "auto", "zu") == ("cpu-mid", None)
        # A forced CPU device never reaches the mlx branch (ct2 turbo on disk -> cpu-strong).
        M._downloaded_sizes = lambda fam: {"large-v3-turbo"} if fam == "fluister" else set()
        assert M.resolve_tier_engine("auto", "cpu", "af") == ("cpu-strong", None)
    finally:
        (_cudadl.cuda_ready, M._downloaded_sizes, M._cpu_auto_tier, _accel.asr_backend,
         _V._mlx_present) = orig
    print("  OK  mlx auto: D6 ladder over TWO stores (MLX repo probe vs ct2 sizes; divergent stores honest)")


def test_mlx_explicit_quality():
    # WP-M4 / D6: an EXPLICIT size with an MLX form resolves to its mlx tier; a size with no MLX
    # form resolves to today's CPU tier for that size (honest: MLX cannot provide it).
    from live_transcribe import accel as _accel
    from live_transcribe import transcribe as _T
    orig = (_cudadl.cuda_ready, M._downloaded_sizes, _accel.asr_backend, _T.resolve_model)
    _cudadl.cuda_ready = lambda: False
    _accel.asr_backend = lambda pref="auto": "cpu" if pref == "cpu" else "mlx"
    M._downloaded_sizes = lambda fam: set()
    try:
        # en + explicit large-v3 -> stock large-v3, which has an MLX form -> mlx.
        assert M.resolve_tier_engine("large-v3", "auto", "en") == ("mlx", None)
        # en + explicit large-v3-turbo -> STOCK turbo has no MLX form -> today's CPU tier.
        assert M.resolve_tier_engine("large-v3-turbo", "auto", "en") == ("cpu-strong", None)
        # Explicit medium: no MLX form for any family -> today's CPU tier.
        assert M.resolve_tier_engine("medium", "auto", "af") == ("cpu-mid", None)
        assert M.resolve_tier_engine("small", "auto", "en") == ("cpu", None)
        # af + explicit large-v3-turbo resolves to the Fluister turbo repo, which HAS an MLX form
        # (resolve_model stubbed to the hosted repo id, so a dev machine's local ct2 dir cannot
        # shadow the answer; a real local dir misses the map and honestly lands on the CPU).
        _T.resolve_model = lambda size, language, engine="auto": ("digiphyte/fluister-turbo", "fluister")
        assert M.resolve_tier_engine("large-v3-turbo", "auto", "af") == ("mlx-turbo", None)
        # af + explicit large-v3 -> the Fluister large-v3 repo has no MLX form -> cpu-large.
        _T.resolve_model = lambda size, language, engine="auto": ("digiphyte/fluister-large-v3", "fluister")
        assert M.resolve_tier_engine("large-v3", "auto", "af") == ("cpu-large", None)
    finally:
        _cudadl.cuda_ready, M._downloaded_sizes, _accel.asr_backend, _T.resolve_model = orig
    print("  OK  mlx explicit: mapped sizes -> mlx tiers, non-mapped sizes -> today's CPU tiers")


def test_mlx_chunk_seconds_and_tier_choices():
    # mlx tiers are GPU-class for chunking, and are NOT reachable from the CLI/env surface.
    assert M.default_chunk_seconds("mlx-turbo") == 8
    assert M.default_chunk_seconds("mlx") == 8
    assert M.default_chunk_seconds("cpu-mid") == 15
    assert "mlx" not in M.TIER_CHOICES and "mlx-turbo" not in M.TIER_CHOICES
    print("  OK  default_chunk_seconds: mlx tiers are GPU-class (8 s); mlx tiers absent from TIER_CHOICES")


def test_windows_regression_sweep():
    # WP-M4 hard constraint: with accel reporting "cpu"/"cuda" (every non-Mac machine), resolution
    # is byte-identical to today for the full parametrised matrix. Expected values are PINNED
    # literals, not recomputed, so a behaviour change here cannot hide.
    from live_transcribe import accel as _accel
    orig = (_cudadl.cuda_ready, M._downloaded_sizes, M._cpu_auto_tier, _accel.asr_backend)
    try:
        M._cpu_auto_tier = lambda: "cpu-mid"
        for cuda in (False, True):
            _cudadl.cuda_ready = (lambda: True) if cuda else (lambda: False)
            _accel.asr_backend = (lambda pref="auto": "cpu" if pref == "cpu" else "cuda") if cuda \
                else (lambda pref="auto": "cpu")
            if cuda:
                cases = [
                    (("auto", "auto", "af"), {"fluister": {"medium"}}, ("gpu-medium", None)),
                    (("auto", "auto", "af"), {"whisper": {"large-v3"}}, ("gpu", "whisper")),
                    (("auto", "auto", "en"), {"fluister": {"small"}}, ("gpu-small", "fluister")),
                    (("auto", "auto", "af"), {}, ("gpu-turbo", None)),
                    (("auto", "auto", "en"), {}, ("gpu", None)),
                    (("auto", "auto", "zu"), {"whisper": {"large-v3"}}, ("gpu", None)),
                    (("medium", "auto", "af"), {}, ("gpu-medium", None)),
                    (("large-v3", "auto", "en"), {}, ("gpu", None)),
                    (("large-v3-turbo", "auto", "en"), {}, ("gpu-turbo", None)),
                ]
            else:
                cases = [
                    (("auto", "auto", "af"), {"fluister": {"medium"}}, ("cpu-mid", None)),
                    (("auto", "cpu", "af"), {"fluister": {"large-v3"}}, ("cpu-large", None)),
                    (("auto", "auto", "af"), {"whisper": {"small"}}, ("cpu", "whisper")),
                    (("auto", "cpu", "af"), {"whisper": {"large-v3"}}, ("cpu-large", "whisper")),
                    (("auto", "auto", "af"), {}, ("cpu-mid", None)),
                    (("auto", "auto", "en"), {}, ("cpu-mid", None)),
                    (("auto", "cpu", "zu"), {"whisper": {"large-v3"}}, ("cpu-mid", None)),
                    (("medium", "auto", "af"), {}, ("cpu-mid", None)),
                    (("small", "cpu", "en"), {}, ("cpu", None)),
                    (("large-v3", "cpu", "en"), {}, ("cpu-large", None)),
                    (("large-v3-turbo", "auto", "en"), {}, ("cpu-strong", None)),
                ]
            for args, downloaded, want in cases:
                M._downloaded_sizes = lambda fam, d=downloaded: set(d.get(fam, set()))
                got = M.resolve_tier_engine(*args)
                assert got == want, (cuda, args, downloaded, got, want)
    finally:
        _cudadl.cuda_ready, M._downloaded_sizes, M._cpu_auto_tier, _accel.asr_backend = orig
    print("  OK  regression sweep: cpu/cuda resolution byte-identical to today's pinned outputs")


if __name__ == "__main__":
    tests = (test_gpu_matrix,
             test_cpu_matrix,
             test_explicit_engine_pref_is_never_crossed,
             test_wrapper_unchanged_for_existing_callers,
             test_mlx_auto_ladder,
             test_mlx_explicit_quality,
             test_mlx_chunk_seconds_and_tier_choices,
             test_windows_regression_sweep)
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
