"""Model-presence honesty (prep-harden): _present() and fluister_present() must report a model as
"already here" ONLY when Begin can really load it without a download.

Two field traps this pins, both WITHOUT any network:

  FIX 1 - Fluister presence must count a LOCAL ct2 build dir, not only the downloaded HF repo. On a
          machine that has a locally built Fluister (SA_LIVE_AF_MODEL / an af-lora-* dir), resolve_model
          loads that dir and starts instantly, yet the old picker probed only the HF repo and said
          "download first". fluister_present() must return True when the local build dir exists.

  FIX 2 - _present() must be truthful for a PARTIAL/corrupt cache. hf_hub's snapshot_download with
          local_files_only=True hands back the snapshot path as soon as refs/main + snapshots/<hash>/
          survive, WITHOUT checking the files are complete. So an interrupted download (model.bin
          missing/truncated) would read as present and then fail to load. _present() must verify a
          non-trivial model.bin under the returned snapshot dir before returning True.

Run:  python tests/test_voicedl_present.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import voicedl, transcribe


def _write(path, nbytes):
    with open(path, "wb") as f:
        f.write(b"\0" * nbytes)


def test_fluister_present_counts_local_build_dir():
    # FIX 1: a size whose _FLUISTER entry resolves to an EXISTING local dir is present even though the
    # HF repo is not cached. Force _present(repo)==False so the ONLY thing that can make it True is the
    # local dir, proving the local build is what flips presence.
    orig_fl, orig_present = dict(transcribe._FLUISTER), voicedl._present
    with tempfile.TemporaryDirectory() as d:
        try:
            transcribe._FLUISTER["medium"] = d          # resolve_model would load this dir
            voicedl._present = lambda repo: False        # HF repo NOT cached
            assert voicedl.fluister_present("medium") is True, \
                "local ct2 build dir must count as present (FIX 1)"
            # And it stays a real filesystem probe: a non-existent local dir does NOT count.
            transcribe._FLUISTER["medium"] = os.path.join(d, "does-not-exist")
            assert voicedl.fluister_present("medium") is False, \
                "a missing local dir must not read as present"
        finally:
            transcribe._FLUISTER.clear(); transcribe._FLUISTER.update(orig_fl)
            voicedl._present = orig_present
    print("  OK  fluister_present() counts an existing local ct2 build dir, not only the HF repo (FIX 1)")


def test_fluister_present_falls_back_to_repo_probe():
    # FIX 1 (other half): with no local build, presence follows the HF repo probe exactly.
    orig_fl, orig_present = dict(transcribe._FLUISTER), voicedl._present
    try:
        transcribe._FLUISTER["small"] = "digiphyte/fluister-small"   # a repo id, not a dir on disk
        seen = {}
        def fake_present(repo):
            seen["repo"] = repo
            return True
        voicedl._present = fake_present
        assert voicedl.fluister_present("small") is True
        assert seen["repo"] == transcribe.FLUISTER_REPOS["small"], \
            "repo probe must use the canonical Fluister repo id"
        voicedl._present = lambda repo: False
        assert voicedl.fluister_present("small") is False, "no local build + repo absent -> not present"
    finally:
        transcribe._FLUISTER.clear(); transcribe._FLUISTER.update(orig_fl)
        voicedl._present = orig_present
    print("  OK  fluister_present() falls back to the HF repo probe when there is no local build (FIX 1)")


def test_present_false_when_model_bin_missing_in_valid_snapshot():
    # FIX 2: _download_model(local_only=True) returns a snapshot dir (refs/main + snapshots survived),
    # but model.bin is absent -> _present must be False, not True.
    orig_dl = voicedl._download_model
    with tempfile.TemporaryDirectory() as snap:
        try:
            # A snapshot dir that looks valid (has config/tokenizer) but is MISSING model.bin.
            _write(os.path.join(snap, "config.json"), 500)
            _write(os.path.join(snap, "tokenizer.json"), 500)
            voicedl._download_model = lambda model, local_only=False: snap
            assert voicedl._present("small") is False, \
                "a snapshot with no model.bin must report not-present (FIX 2)"
            # A model.bin that exists but is a stub/truncation (below the non-trivial floor) is not usable.
            _write(os.path.join(snap, "model.bin"), 1000)
            assert voicedl._present("small") is False, \
                "a truncated/stub model.bin must report not-present (FIX 2)"
            # A non-trivial model.bin (above the floor) -> present.
            _write(os.path.join(snap, "model.bin"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
            assert voicedl._present("small") is True, \
                "a complete snapshot with a real model.bin must report present"
        finally:
            voicedl._download_model = orig_dl
    print("  OK  _present() verifies a non-trivial model.bin under the snapshot dir (FIX 2)")


def test_present_false_when_download_model_raises():
    # FIX 2 fail-safe: any error from the probe (no cache at all is the common one) -> not present.
    orig_dl = voicedl._download_model
    try:
        def boom(model, local_only=False):
            raise RuntimeError("no local cache")
        voicedl._download_model = boom
        assert voicedl._present("small") is False
    finally:
        voicedl._download_model = orig_dl
    print("  OK  _present() treats any probe error as not-present (fail-safe, FIX 2)")


def test_ct2_rule_unchanged_by_mlx_shape():
    # WP-M3 guard: the MLX additions must not loosen the ct2 model.bin rule. An MLX-shaped
    # snapshot (config.json + a big weights.safetensors, NO model.bin) satisfies the MLX
    # probe yet still reads not-present for a ct2 repo id.
    orig_dl = voicedl._download_model
    with tempfile.TemporaryDirectory() as snap:
        try:
            _write(os.path.join(snap, "config.json"), 500)
            _write(os.path.join(snap, "weights.safetensors"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
            voicedl._download_model = lambda model, local_only=False: snap
            assert voicedl._snapshot_has_weights(snap) is False, \
                "the ct2 rule must still demand model.bin, whatever else is in the dir"
            assert voicedl._present("small") is False, \
                "an MLX-shaped snapshot must stay not-present for a ct2 repo id"
            assert voicedl._snapshot_has_mlx_weights(snap) is True, \
                "sanity: the same dir DOES satisfy the MLX rule (the rules are disjoint)"
        finally:
            voicedl._download_model = orig_dl
    print("  OK  the ct2 model.bin rule is unchanged: an MLX-shaped dir is not ct2-present")


def test_snapshot_has_weights_edge_cases():
    # _snapshot_has_weights is the shared guard: falsy path, non-dir, and a directory-named model.bin
    # all fail safe to False.
    assert voicedl._snapshot_has_weights("") is False
    assert voicedl._snapshot_has_weights(None) is False
    with tempfile.TemporaryDirectory() as d:
        assert voicedl._snapshot_has_weights(os.path.join(d, "nope")) is False   # missing dir
        os.mkdir(os.path.join(d, "model.bin"))                                   # model.bin is a DIR
        assert voicedl._snapshot_has_weights(d) is False, "a model.bin that is a directory must fail"
    print("  OK  _snapshot_has_weights() fails safe on falsy/non-dir/degenerate inputs (FIX 2)")


if __name__ == "__main__":
    tests = (test_fluister_present_counts_local_build_dir,
             test_fluister_present_falls_back_to_repo_probe,
             test_present_false_when_model_bin_missing_in_valid_snapshot,
             test_present_false_when_download_model_raises,
             test_ct2_rule_unchanged_by_mlx_shape,
             test_snapshot_has_weights_edge_cases)
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
    print("\nAll voicedl-present tests passed.")
