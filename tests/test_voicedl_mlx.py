"""voicedl MLX targeting (WP-M3): downloads, presence, delete and updates for the MLX
(Apple Metal) model repos, with the ct2/Store-cert path provably unchanged.

What this pins, all WITHOUT any network and runnable on Windows:

  TARGETING - asr_download_target(family, size) returns the MLX repo id ONLY when
              accel.mlx_ready() is True and the (family, size) pair maps through
              mlxbackend.MLX_REPOS; in every other case it returns today's ct2 target,
              so on Windows (mlx_ready always False) every answer is byte-identical.

  PRESENCE  - _snapshot_has_mlx_weights accepts an MLX-shaped snapshot (a non-trivial
              weights.safetensors or weights.npz plus config.json) and fails safe on a
              truncated or degenerate one; _mlx_present probes the local cache only.

  DOWNLOADS - MLX repos download via snapshot_download with the byte-harvesting tqdm
              passed explicitly, inside _download_ctx() (so hf's Xet backend is OFF for
              the duration and restored after, same as the ct2 flow); the _STATE
              transitions mirror the ct2 ones. _download_model is never involved.

  DELETE    - delete() accepts an MLX repo id and removes only that cache dir.

  UPDATES   - model_update_status considers the MLX repo's own manifest entry when the
              MLX form is installed locally; start_fluister_update targets the MLX repo
              on a ready Mac.

Run:  python tests/test_voicedl_mlx.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import tempfile
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import voicedl
from live_transcribe.mlxbackend import MLX_REPOS

FLUISTER_TURBO_MLX = "digiphyte/fluister-turbo-mlx"
WHISPER_LARGE_MLX = "mlx-community/whisper-large-v3-mlx"


def _write(path, nbytes):
    with open(path, "wb") as f:
        f.write(b"\0" * nbytes)


def _reset_state():
    voicedl._set(state="idle", model=None, repo=None, kind=None,
                 version=None, revision=None, downloaded=0, total=0, error=None)


def _fake_accel(ready):
    return types.SimpleNamespace(mlx_ready=lambda: ready)


class _FakeConfig:
    """Stands in for live_transcribe.config so no test ever touches the real settings file."""

    def __init__(self, installed=None):
        self.data = {"installed_models": dict(installed or {})}

    def load(self):
        return dict(self.data)

    def update(self, kw):
        self.data.update(kw)


def _wait_state(*states, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = voicedl.progress()["state"]
        if s in states:
            return s
        time.sleep(0.01)
    return voicedl.progress()["state"]


def test_snapshot_has_mlx_weights_shapes():
    # An MLX snapshot is a non-trivial weights file (safetensors or npz) plus config.json.
    with tempfile.TemporaryDirectory() as d:
        assert voicedl._snapshot_has_mlx_weights(d) is False, "empty dir must not be present"
        _write(os.path.join(d, "config.json"), 500)
        assert voicedl._snapshot_has_mlx_weights(d) is False, "config alone is not a model"
        _write(os.path.join(d, "weights.safetensors"), 1000)
        assert voicedl._snapshot_has_mlx_weights(d) is False, \
            "a truncated/stub weights file must read not-present"
        _write(os.path.join(d, "weights.safetensors"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
        assert voicedl._snapshot_has_mlx_weights(d) is True, "safetensors + config -> present"
    with tempfile.TemporaryDirectory() as d:
        # The older mlx-community shape: weights.npz.
        _write(os.path.join(d, "config.json"), 500)
        _write(os.path.join(d, "weights.npz"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
        assert voicedl._snapshot_has_mlx_weights(d) is True, "npz + config -> present"
        os.remove(os.path.join(d, "config.json"))
        assert voicedl._snapshot_has_mlx_weights(d) is False, "weights without config.json fail"
    with tempfile.TemporaryDirectory() as d:
        # A ct2-shaped snapshot (model.bin) is NOT an MLX snapshot.
        _write(os.path.join(d, "config.json"), 500)
        _write(os.path.join(d, "model.bin"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
        assert voicedl._snapshot_has_mlx_weights(d) is False, "ct2 shape must not satisfy the MLX rule"
    # Fail-safe degenerate inputs, mirroring _snapshot_has_weights.
    assert voicedl._snapshot_has_mlx_weights("") is False
    assert voicedl._snapshot_has_mlx_weights(None) is False
    assert voicedl._snapshot_has_mlx_weights(os.path.join(tempfile.gettempdir(), "nope-xyz")) is False
    print("  OK  _snapshot_has_mlx_weights() accepts the MLX file set and fails safe otherwise")


def test_mlx_present_probes_local_cache_only():
    import huggingface_hub as hf
    orig = hf.snapshot_download
    with tempfile.TemporaryDirectory() as snap:
        _write(os.path.join(snap, "config.json"), 500)
        _write(os.path.join(snap, "weights.safetensors"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
        seen = {}
        try:
            def fake(repo, **kw):
                seen["repo"] = repo
                seen.update(kw)
                return snap
            hf.snapshot_download = fake
            assert voicedl._mlx_present(FLUISTER_TURBO_MLX) is True
            assert seen["repo"] == FLUISTER_TURBO_MLX
            assert seen.get("local_files_only") is True, \
                "_mlx_present must never touch the network (local_files_only)"
            # Any probe error (no cache at all is the common one) -> not present, fail-safe.
            def boom(repo, **kw):
                raise RuntimeError("not cached")
            hf.snapshot_download = boom
            assert voicedl._mlx_present(FLUISTER_TURBO_MLX) is False
        finally:
            hf.snapshot_download = orig
    print("  OK  _mlx_present() probes the local cache only and fails safe on any error")


def test_present_dispatches_by_repo_kind():
    # WP-M4's presence probes (_downloaded_sizes whisper branch, _resolve_download_plan) call
    # voicedl._present(target) directly, where target can be an MLX repo id on a ready Mac.
    # _present must answer honestly for those ids: judged by the MLX file-set rule, with
    # faster-whisper's downloader never consulted; every other id keeps the ct2 model.bin rule.
    import huggingface_hub as hf
    orig_dl, orig_hf = voicedl._download_model, hf.snapshot_download
    with tempfile.TemporaryDirectory() as snap:
        _write(os.path.join(snap, "config.json"), 500)
        _write(os.path.join(snap, "weights.safetensors"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
        try:
            def never(model, local_only=False):
                raise AssertionError("_download_model must not be consulted for an MLX repo id")
            voicedl._download_model = never
            hf.snapshot_download = lambda repo, **kw: snap
            assert voicedl._present(WHISPER_LARGE_MLX) is True, \
                "a fully cached MLX repo must read present through _present"
            assert voicedl._present(FLUISTER_TURBO_MLX) is True
            # A truncated MLX cache stays not-present (fail-safe intact through the dispatch).
            _write(os.path.join(snap, "weights.safetensors"), 1000)
            assert voicedl._present(WHISPER_LARGE_MLX) is False, \
                "a truncated MLX cache must stay not-present"
            # A ct2 id still goes through _download_model + the model.bin rule: the same
            # MLX-shaped dir (config + weights, no model.bin) is NOT ct2-present.
            _write(os.path.join(snap, "weights.safetensors"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
            voicedl._download_model = lambda model, local_only=False: snap
            assert voicedl._present("small") is False, \
                "the ct2 model.bin rule must be untouched by the MLX dispatch"
        finally:
            voicedl._download_model, hf.snapshot_download = orig_dl, orig_hf
    print("  OK  _present() dispatches MLX repo ids to the MLX rule; the ct2 rule is untouched")


def test_asr_download_target_ct2_when_not_ready():
    # Windows reality: accel.mlx_ready() is always False, so every answer is today's ct2
    # target. Pinned with an explicit fake so the test is deterministic everywhere.
    orig = voicedl.accel
    try:
        voicedl.accel = _fake_accel(False)
        assert voicedl.asr_download_target("fluister", "large-v3-turbo") == "digiphyte/fluister-turbo"
        assert voicedl.asr_download_target("fluister", "large-v3") == "digiphyte/fluister-large-v3"
        assert voicedl.asr_download_target("fluister", "medium") == "digiphyte/fluister-medium"
        assert voicedl.asr_download_target("whisper", "large-v3") == "large-v3"
        assert voicedl.asr_download_target("whisper", "medium") == "medium"
        assert voicedl.asr_download_target("whisper", "large-v3-turbo") == "large-v3-turbo"
    finally:
        voicedl.accel = orig
    # And the real probe on this machine agrees on win32 (no fake).
    if sys.platform == "win32":
        assert voicedl.asr_download_target("fluister", "large-v3-turbo") == "digiphyte/fluister-turbo"
        assert voicedl.asr_download_target("whisper", "large-v3") == "large-v3"
    print("  OK  asr_download_target() returns the ct2 targets whenever MLX is not ready")


def test_asr_download_target_mlx_when_ready():
    orig = voicedl.accel
    try:
        voicedl.accel = _fake_accel(True)
        # The two mapped pairs (the D3 map, via mlxbackend.MLX_REPOS as single source of truth).
        assert voicedl.asr_download_target("fluister", "large-v3-turbo") == FLUISTER_TURBO_MLX
        assert voicedl.asr_download_target("whisper", "large-v3") == WHISPER_LARGE_MLX
        assert voicedl.asr_download_target("fluister", "large-v3-turbo") == \
            MLX_REPOS["digiphyte/fluister-turbo"]
        assert voicedl.asr_download_target("whisper", "large-v3") == MLX_REPOS["large-v3"]
        # A map miss keeps the ct2 target even with MLX ready (D3: no special-casing).
        assert voicedl.asr_download_target("fluister", "medium") == "digiphyte/fluister-medium"
        assert voicedl.asr_download_target("whisper", "medium") == "medium"
        # An unknown Fluister size stays falsy so the callers' ValueError behaviour holds.
        assert not voicedl.asr_download_target("fluister", "nope")
    finally:
        voicedl.accel = orig
    print("  OK  asr_download_target() maps the two MLX pairs and keeps ct2 for every miss")


def test_delete_mlx_repo_removes_only_that_dir():
    from pathlib import Path
    orig_hub, orig_cfg = voicedl._hub_cache, voicedl.config
    with tempfile.TemporaryDirectory() as cache:
        mlx_dir = os.path.join(cache, "models--digiphyte--fluister-turbo-mlx")
        ct2_dir = os.path.join(cache, "models--digiphyte--fluister-turbo")
        for d in (mlx_dir, ct2_dir):
            os.makedirs(d)
            _write(os.path.join(d, "blob.bin"), 2048)
        try:
            voicedl._hub_cache = lambda: Path(cache)
            voicedl.config = _FakeConfig({FLUISTER_TURBO_MLX: {"version": "1.0.0", "revision": ""}})
            voicedl.delete(FLUISTER_TURBO_MLX)
            assert not os.path.exists(mlx_dir), "the MLX cache dir must be removed"
            assert os.path.exists(ct2_dir), "delete must touch ONLY the MLX repo's cache dir"
            assert FLUISTER_TURBO_MLX not in voicedl.config.data["installed_models"], \
                "the recorded install version must be forgotten on delete"
            # Still refuses genuinely unknown ids (never an arbitrary path).
            try:
                voicedl.delete("evil/other-repo")
                assert False, "an unknown repo id must raise ValueError"
            except ValueError:
                pass
        finally:
            voicedl._hub_cache, voicedl.config = orig_hub, orig_cfg
    print("  OK  delete() accepts the MLX repo id and removes only that cache dir")


def test_download_mlx_repo_xet_off_and_progress_tqdm():
    # During _download_mlx_repo, hf's Xet backend is forced off (HF_HUB_DISABLE_XET True)
    # and restored after, exactly like the ct2 download ctx; the byte-harvesting tqdm is
    # passed explicitly so progress() tracks real transferred bytes.
    import huggingface_hub as hf
    import huggingface_hub.constants as hc
    orig = hf.snapshot_download
    before_xet = hc.HF_HUB_DISABLE_XET
    seen = {}
    try:
        def fake(repo, **kw):
            seen["repo"] = repo
            seen["xet_disabled_during"] = hc.HF_HUB_DISABLE_XET
            seen["tqdm_class"] = kw.get("tqdm_class")
            return "snap-path"
        hf.snapshot_download = fake
        out = voicedl._download_mlx_repo(FLUISTER_TURBO_MLX)
    finally:
        hf.snapshot_download = orig
    assert out == "snap-path"
    assert seen["repo"] == FLUISTER_TURBO_MLX
    assert seen["xet_disabled_during"] is True, "Xet backend not forced off during the MLX download"
    assert hc.HF_HUB_DISABLE_XET == before_xet, "HF_HUB_DISABLE_XET not restored after"
    assert seen["tqdm_class"] is voicedl._progress_tqdm_cls(), \
        "the byte-harvesting tqdm must be passed explicitly to snapshot_download"
    print("  OK  _download_mlx_repo() runs with Xet off (restored after) and the progress tqdm")


def test_mlx_download_state_transitions_mirror_ct2():
    # start_fluister_download on a ready Mac targets the MLX repo through the SAME one-slot
    # _STATE machinery: downloading (with the MLX repo as `repo`) -> done, second download
    # refused while in flight, install recorded at the build baseline.
    orig_accel, orig_cfg = voicedl.accel, voicedl.config
    orig_dl, orig_sha = voicedl._download_mlx_repo, voicedl._ref_main_sha
    gate = threading.Event()
    calls = []
    _reset_state()
    try:
        voicedl.accel = _fake_accel(True)
        voicedl.config = _FakeConfig()
        voicedl._ref_main_sha = lambda repo: ""

        def fake_dl(repo):
            calls.append(repo)
            assert gate.wait(5), "test gate never released"
            return "snap"
        voicedl._download_mlx_repo = fake_dl

        voicedl.start_fluister_download("large-v3-turbo")
        p = voicedl.progress()
        assert p["state"] == "downloading" and p["repo"] == FLUISTER_TURBO_MLX, p
        assert p["model"] == "large-v3-turbo" and p["kind"] == "fluister", p
        assert p["version"] == voicedl._FLUISTER_BASELINE, p
        assert p["total"] == voicedl._FLUISTER_SIZES[FLUISTER_TURBO_MLX], p
        # The single global download slot still refuses a second download.
        try:
            voicedl.start_fluister_download("large-v3-turbo")
            assert False, "a second download must be refused while one is in flight"
        except RuntimeError:
            pass
        gate.set()
        assert _wait_state("done", "error") == "done", voicedl.progress()
        p = voicedl.progress()
        assert p["downloaded"] == p["total"], p
        assert calls == [FLUISTER_TURBO_MLX], "the MLX repo must download via _download_mlx_repo"
        rec = voicedl.config.data["installed_models"]
        assert rec.get(FLUISTER_TURBO_MLX, {}).get("version") == voicedl._FLUISTER_BASELINE, \
            "a first MLX download must be recorded at the build baseline version"
    finally:
        gate.set()
        voicedl.accel, voicedl.config = orig_accel, orig_cfg
        voicedl._download_mlx_repo, voicedl._ref_main_sha = orig_dl, orig_sha
        _reset_state()

    # And the error transition mirrors ct2: a failed fetch lands in state=error with the message.
    orig_accel, orig_dl = voicedl.accel, voicedl._download_mlx_repo
    try:
        voicedl.accel = _fake_accel(True)
        def boom(repo):
            raise RuntimeError("network down")
        voicedl._download_mlx_repo = boom
        voicedl.start_fluister_download("large-v3-turbo")
        assert _wait_state("done", "error") == "error", voicedl.progress()
        assert "network down" in (voicedl.progress()["error"] or "")
    finally:
        voicedl.accel, voicedl._download_mlx_repo = orig_accel, orig_dl
        _reset_state()
    print("  OK  MLX download _STATE transitions (downloading/done/error, one slot) mirror ct2")


def test_stock_large_v3_routes_by_readiness():
    # start_download("large-v3") targets the mlx-community repo on a ready Mac (kind
    # "whisper", nothing version-recorded: it is unversioned upstream), and today's
    # _run/_repo_id path everywhere else.
    orig_accel, orig_cfg, orig_dl = voicedl.accel, voicedl.config, voicedl._download_mlx_repo
    _reset_state()
    try:
        voicedl.accel = _fake_accel(True)
        voicedl.config = _FakeConfig()
        voicedl._download_mlx_repo = lambda repo: "snap"
        voicedl.start_download("large-v3")
        p = voicedl.progress()
        assert p["repo"] == WHISPER_LARGE_MLX and p["kind"] == "whisper", p
        assert p["total"] == voicedl._SIZES[WHISPER_LARGE_MLX], p
        assert _wait_state("done", "error") == "done", voicedl.progress()
        assert voicedl.config.data["installed_models"] == {}, \
            "an unversioned upstream MLX repo must not be version-recorded"
    finally:
        voicedl.accel, voicedl.config, voicedl._download_mlx_repo = orig_accel, orig_cfg, orig_dl
        _reset_state()

    # ct2 route unchanged: with MLX not ready the stock path still goes through _run with
    # the faster-whisper repo id (no MLX machinery involved).
    orig_accel, orig_run = voicedl.accel, voicedl._run
    _reset_state()
    try:
        voicedl.accel = _fake_accel(False)
        ran = {}
        def fake_run(model):
            ran["model"] = model
            voicedl._set(state="done")
        voicedl._run = fake_run
        voicedl.start_download("large-v3")
        assert voicedl.progress()["repo"] == voicedl._repo_id("large-v3")
        assert _wait_state("done", "error") == "done"
        assert ran["model"] == "large-v3"
    finally:
        voicedl.accel, voicedl._run = orig_accel, orig_run
        _reset_state()
    print("  OK  stock large-v3 downloads target MLX only on a ready Mac, ct2 otherwise")


def test_fluister_catalogue_targets_mlx_when_ready():
    from live_transcribe import transcribe
    orig_accel, orig_cfg = voicedl.accel, voicedl.config
    orig_fp = voicedl.fluister_present
    orig_fl = dict(transcribe._FLUISTER)
    try:
        voicedl.config = _FakeConfig()
        voicedl.fluister_present = lambda size, repo=None: False
        # Neutralise any real local ct2 build dirs on this machine.
        for k in list(transcribe._FLUISTER):
            transcribe._FLUISTER[k] = "digiphyte/fluister-" + k

        voicedl.accel = _fake_accel(True)
        rows = {r["size"]: r for r in voicedl.fluister_catalogue()}
        assert rows["large-v3-turbo"]["repo"] == FLUISTER_TURBO_MLX, rows["large-v3-turbo"]
        assert rows["large-v3-turbo"]["approx_bytes"] == voicedl._FLUISTER_SIZES[FLUISTER_TURBO_MLX]
        # Unmapped sizes keep their ct2 repos even on a ready Mac.
        assert rows["medium"]["repo"] == "digiphyte/fluister-medium"
        assert rows["medium"]["approx_bytes"] == voicedl._FLUISTER_SIZES["medium"]

        voicedl.accel = _fake_accel(False)
        rows = {r["size"]: r for r in voicedl.fluister_catalogue()}
        assert rows["large-v3-turbo"]["repo"] == "digiphyte/fluister-turbo", \
            "with MLX not ready the catalogue must be today's ct2 catalogue"
        assert rows["large-v3-turbo"]["approx_bytes"] == voicedl._FLUISTER_SIZES["large-v3-turbo"]
    finally:
        voicedl.accel, voicedl.config = orig_accel, orig_cfg
        voicedl.fluister_present = orig_fp
        transcribe._FLUISTER.clear(); transcribe._FLUISTER.update(orig_fl)
    print("  OK  fluister_catalogue() lists the MLX target (repo + size) only on a ready Mac")


def test_model_update_status_considers_mlx_repo():
    orig_cfg, orig_present = voicedl.config, voicedl._present
    orig_mlx, orig_sv = voicedl._mlx_present, voicedl.swivuriso_available
    manifest = {"models": [
        {"repo": FLUISTER_TURBO_MLX, "version": "1.1.0", "revision": "abc123", "approx_bytes": 777},
    ]}
    try:
        voicedl.config = _FakeConfig({FLUISTER_TURBO_MLX: {"version": "1.0.0", "revision": ""}})
        voicedl._present = lambda repo: False               # no ct2 repos installed
        voicedl.swivuriso_available = lambda: False
        voicedl._mlx_present = lambda repo: repo == FLUISTER_TURBO_MLX
        rows = voicedl.model_update_status(manifest)
        assert len(rows) == 1, rows
        r = rows[0]
        assert r["repo"] == FLUISTER_TURBO_MLX and r["size"] == "large-v3-turbo", r
        assert r["installed"] == "1.0.0" and r["latest"] == "1.1.0", r
        assert r["update_available"] is True and r["approx_bytes"] == 777, r
        assert r["revision"] == "abc123", r
        # Same _vtuple compare: an equal version is not an update.
        voicedl.config = _FakeConfig({FLUISTER_TURBO_MLX: {"version": "1.1.0", "revision": ""}})
        rows = voicedl.model_update_status(manifest)
        assert rows and rows[0]["update_available"] is False, rows
        # Not installed locally -> never mentioned (no nagging about a model you do not have).
        voicedl._mlx_present = lambda repo: False
        assert voicedl.model_update_status(manifest) == []
    finally:
        voicedl.config, voicedl._present = orig_cfg, orig_present
        voicedl._mlx_present, voicedl.swivuriso_available = orig_mlx, orig_sv
    print("  OK  model_update_status() rides the manifest channel for the installed MLX repo")


def test_start_fluister_update_targets_mlx_when_ready():
    orig_accel, orig_cfg = voicedl.accel, voicedl.config
    orig_dl, orig_sha, orig_fetch = voicedl._download_mlx_repo, voicedl._ref_main_sha, voicedl.fetch_manifest
    _reset_state()
    try:
        voicedl.accel = _fake_accel(True)
        voicedl.config = _FakeConfig({FLUISTER_TURBO_MLX: {"version": "1.0.0", "revision": ""}})
        voicedl.fetch_manifest = lambda timeout=8: {"models": [
            {"repo": FLUISTER_TURBO_MLX, "version": "1.1.0", "revision": "", "approx_bytes": 999},
        ]}
        voicedl._ref_main_sha = lambda repo: "abc123"
        calls = []
        voicedl._download_mlx_repo = lambda repo: calls.append(repo) or "snap"
        voicedl.start_fluister_update("large-v3-turbo")
        p = voicedl.progress()
        assert p["repo"] == FLUISTER_TURBO_MLX and p["version"] == "1.1.0", p
        assert p["total"] == 999, p
        assert _wait_state("done", "error") == "done", voicedl.progress()
        assert calls == [FLUISTER_TURBO_MLX]
        rec = voicedl.config.data["installed_models"]
        assert rec.get(FLUISTER_TURBO_MLX, {}).get("version") == "1.1.0", \
            "the applied MLX update must be recorded against the MLX repo id"
    finally:
        voicedl.accel, voicedl.config = orig_accel, orig_cfg
        voicedl._download_mlx_repo, voicedl._ref_main_sha = orig_dl, orig_sha
        voicedl.fetch_manifest = orig_fetch
        _reset_state()
    print("  OK  start_fluister_update() applies the update to the MLX repo on a ready Mac")


if __name__ == "__main__":
    tests = (test_snapshot_has_mlx_weights_shapes,
             test_mlx_present_probes_local_cache_only,
             test_present_dispatches_by_repo_kind,
             test_asr_download_target_ct2_when_not_ready,
             test_asr_download_target_mlx_when_ready,
             test_delete_mlx_repo_removes_only_that_dir,
             test_download_mlx_repo_xet_off_and_progress_tqdm,
             test_mlx_download_state_transitions_mirror_ct2,
             test_stock_large_v3_routes_by_readiness,
             test_fluister_catalogue_targets_mlx_when_ready,
             test_model_update_status_considers_mlx_repo,
             test_start_fluister_update_targets_mlx_when_ready)
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
    print("\nAll voicedl-mlx tests passed.")
