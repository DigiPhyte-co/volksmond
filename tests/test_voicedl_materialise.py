"""Dev-era symlinked HuggingFace caches must read as installed, load in the frozen build, and report a
truthful on-disk size (wp/hf-symlink-presence hotfix for 1.13.3), WITHOUT corrupting the shared cache.

Field bug: small/medium were downloaded on a Developer-Mode machine with the symlinked HF layout
(snapshots/<hash>/model.bin -> ../../blobs/<sha>). The frozen build's bundled runtime (MSVCP140 14.50)
cannot open a Windows symlink, so ct2 fails to load the model at Begin, and the Models page can read the
cache as not-installed; where it does read as installed, _dir_size DOUBLED the size (small 0.97 GB,
medium 3.1 GB, i.e. 2x their true 0.48 / 1.53 GB) because os.walk counted the blob AND the symlink that
follows into it.

What this pins, all WITHOUT any network:

  MATERIALISE - _materialise_snapshot rewrites every symlinked file in a snapshot dir as a real file
                backed by a HARDLINK to the shared blob (F1), so the frozen build can both see and LOAD
                it while the canonical blob is retained for other revisions. Windows only (F1): off
                Windows ordinary symlinks are valid, so it is a pure no-op there. It is idempotent, safe
                on a broken link (left as-is -> falls back to not-present), a no-op on a real-file cache,
                skips mutation while a download/delete holds the repo lock (F2), and only ever touches a
                link resolving to a regular file strictly under this repo's own blobs/ (F4).

  PRESENCE    - _present materialises the snapshot first, then requires (on Windows) that no
                loader-required file was left a symlink (F3), so a partial migration is not-present.

  SIZE        - _dir_size dedupes by resolved real path AND by (st_dev, st_ino), so a blob and either a
                symlink or a hardlink into it count once (no doubling), and a real-file cache counts once.

  LADDER      - transcribe.model_present routes through voicedl.ensure_snapshot_loadable (F5), so the CPU
                downgrade ladder materialises a dev-era symlinked rung instead of following the symlink.

Run:  python tests/test_voicedl_materialise.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pytest
except ImportError:                     # standalone run without pytest
    pytest = None

from live_transcribe import voicedl

_HASH = "536b0662742c02347bc0e980a01041f333bce120"
_WIN = sys.platform == "win32"          # materialisation only mutates on Windows (F1)


def _symlinks_ok():
    """True iff this runner can create a symlink (Developer Mode / admin on Windows, always on POSIX)."""
    with tempfile.TemporaryDirectory() as d:
        tgt = os.path.join(d, "t")
        open(tgt, "wb").write(b"x")
        try:
            os.symlink(tgt, os.path.join(d, "l"))
            return True
        except (OSError, NotImplementedError):
            return False


_SYMLINKS_OK = _symlinks_ok()


def _skip(reason):
    """Skip under pytest; print + signal a standalone skip otherwise."""
    if pytest is not None:
        pytest.skip(reason)
    print("  SKIP " + reason)


def _write(path, nbytes):
    with open(path, "wb") as f:
        f.write(b"\0" * nbytes)


def _repo_dir_of(snap):
    """The models--<repo> dir two levels above a snapshot dir, exactly as _materialise_snapshot derives
    it (so a test that holds the repo lock holds the same one the probe would try to acquire)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(snap)))


def _build_symlinked_repo(cache, model_bin_bytes=None, blob_of=None):
    """Create a HF cache repo for faster-whisper-small under `cache` in the dev-era SYMLINK layout:
    blobs/<sha> real files + snapshots/<hash>/<name> -> ../../blobs/<sha> relative symlinks + refs/main.
    Returns (repo, snap). `blob_of` lets a test drop one file's blob to simulate a broken link."""
    if model_bin_bytes is None:
        model_bin_bytes = voicedl._MIN_MODEL_BIN_BYTES + 4096
    repo = os.path.join(cache, "models--Systran--faster-whisper-small")
    blobs = os.path.join(repo, "blobs")
    snap = os.path.join(repo, "snapshots", _HASH)
    refs = os.path.join(repo, "refs")
    for d in (blobs, snap, refs):
        os.makedirs(d)
    files = {"model.bin": ("sha_model", model_bin_bytes),
             "config.json": ("sha_cfg", 500),
             "tokenizer.json": ("sha_tok", 2048),
             "vocabulary.txt": ("sha_voc", 1024)}
    for name, (sha, size) in files.items():
        if blob_of is not None and name == blob_of:
            continue                                        # omit this blob -> the link will dangle
        _write(os.path.join(blobs, sha), size)
    cwd = os.getcwd()
    os.chdir(snap)
    try:
        for name, (sha, _size) in files.items():
            os.symlink(os.path.join("..", "..", "blobs", sha), name)   # relative, exactly like HF
    finally:
        os.chdir(cwd)
    with open(os.path.join(refs, "main"), "w", encoding="utf-8") as f:
        f.write(_HASH)
    return repo, snap


def _build_two_snapshot_shared_blob(cache):
    """Two snapshots of faster-whisper-small that SHARE one model.bin blob (the HF content-addressed
    layout: several revisions point at the same bytes). Each snapshot has its own config.json blob.
    Returns (repo, snap1, snap2, model_blob_path)."""
    repo = os.path.join(cache, "models--Systran--faster-whisper-small")
    blobs = os.path.join(repo, "blobs")
    h1, h2 = "1" * 40, "2" * 40
    snap1 = os.path.join(repo, "snapshots", h1)
    snap2 = os.path.join(repo, "snapshots", h2)
    for d in (blobs, snap1, snap2):
        os.makedirs(d)
    model_sha = "sha_model_shared"
    _write(os.path.join(blobs, model_sha), voicedl._MIN_MODEL_BIN_BYTES + 4096)
    _write(os.path.join(blobs, "sha_cfg1"), 500)
    _write(os.path.join(blobs, "sha_cfg2"), 500)
    for snap, cfg_sha in ((snap1, "sha_cfg1"), (snap2, "sha_cfg2")):
        cwd = os.getcwd()
        os.chdir(snap)
        try:
            os.symlink(os.path.join("..", "..", "blobs", model_sha), "model.bin")
            os.symlink(os.path.join("..", "..", "blobs", cfg_sha), "config.json")
        finally:
            os.chdir(cwd)
    return repo, snap1, snap2, os.path.join(blobs, model_sha)


def _build_real_repo(cache):
    """The already-materialised (real-file) layout: no symlinks, no blobs/ dir. Returns (repo, snap)."""
    repo = os.path.join(cache, "models--Systran--faster-whisper-small")
    snap = os.path.join(repo, "snapshots", _HASH)
    os.makedirs(snap)
    _write(os.path.join(snap, "model.bin"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
    _write(os.path.join(snap, "config.json"), 500)
    return repo, snap


def test_materialise_converts_symlinks_and_keeps_blob():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        assert all(os.path.islink(os.path.join(snap, n)) for n in os.listdir(snap)), "setup: all symlinks"
        blobs = os.path.join(repo, "blobs")
        assert os.listdir(blobs), "setup: blobs present"
        voicedl._materialise_snapshot(snap)
        if _WIN:
            for n in os.listdir(snap):
                p = os.path.join(snap, n)
                assert not os.path.islink(p), f"{n} must be a real file after materialise"
                assert os.path.isfile(p)
            assert os.stat(os.path.join(snap, "model.bin")).st_size > voicedl._MIN_MODEL_BIN_BYTES, \
                "model.bin keeps its real bytes"
            # F1: the blob is NOT moved out - other revisions depend on it. Each snapshot file is a
            # HARDLINK to its surviving blob (same inode), not a copy.
            assert os.listdir(blobs), "the shared blobs must survive (hardlink, not move)"
            assert os.stat(os.path.join(snap, "model.bin")).st_ino == \
                os.stat(os.path.join(blobs, "sha_model")).st_ino, "materialised file hardlinks the blob"
        else:
            assert all(os.path.islink(os.path.join(snap, n)) for n in os.listdir(snap)), \
                "off Windows materialise is a no-op (symlinks are valid for the loader)"
            assert os.listdir(blobs), "blobs untouched"
    print("  OK  _materialise_snapshot() rewrites symlinks as hardlinks and keeps the shared blob (F1)")


def test_shared_blob_survives_and_both_revisions_readable():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap1, snap2, model_blob = _build_two_snapshot_shared_blob(cache)
        size = os.stat(model_blob).st_size
        voicedl._materialise_snapshot(snap1)                # migrate ONLY revision 1
        assert os.path.isfile(model_blob), "the shared model.bin blob must survive (other revisions need it)"
        for snap in (snap1, snap2):
            mb = os.path.join(snap, "model.bin")
            assert os.path.isfile(mb), f"{snap} model.bin must stay readable"
            assert os.stat(mb).st_size == size, f"{snap} model.bin keeps the right bytes"
        if _WIN:
            assert not os.path.islink(os.path.join(snap1, "model.bin")), "revision 1 materialised to a real file"
            assert os.stat(os.path.join(snap1, "model.bin")).st_ino == os.stat(model_blob).st_ino, \
                "revision 1 is a hardlink to the surviving blob"
            assert os.path.islink(os.path.join(snap2, "model.bin")), "revision 2 (untouched) still points at the blob"
        else:
            assert os.path.islink(os.path.join(snap1, "model.bin")), "no mutation off Windows"
    print("  OK  two revisions sharing one blob both stay readable after migrating one (F1)")


def test_present_materialises_symlinked_snapshot():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    orig_dl = voicedl._download_model
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        try:
            voicedl._download_model = lambda model, local_only=False: snap
            assert voicedl._present("small") is True, "a symlinked-but-valid cache must read as present"
            mb = os.path.join(snap, "model.bin")
            if _WIN:
                assert not os.path.islink(mb), \
                    "on Windows _present leaves the real-file shape the frozen runtime can open"
                assert os.path.isfile(os.path.join(repo, "blobs", "sha_model")), "the shared blob is retained"
            else:
                assert os.path.islink(mb), "off Windows the symlink is valid and left in place"
        finally:
            voicedl._download_model = orig_dl
    print("  OK  _present() materialises a symlinked snapshot and reports it installed")


def test_materialise_is_idempotent():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        voicedl._materialise_snapshot(snap)
        before = {n: os.stat(os.path.join(snap, n)).st_size for n in os.listdir(snap)}
        voicedl._materialise_snapshot(snap)                     # second pass: must be a harmless no-op
        after = {n: os.stat(os.path.join(snap, n)).st_size for n in os.listdir(snap)}
        assert before == after, "a materialised cache must be untouched by a second pass"
        if _WIN:
            assert all(not os.path.islink(os.path.join(snap, n)) for n in os.listdir(snap))
    print("  OK  _materialise_snapshot() is idempotent")


def test_materialise_broken_link_falls_back_to_not_present():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    orig_dl = voicedl._download_model
    with tempfile.TemporaryDirectory() as cache:
        # model.bin's blob is omitted -> its symlink dangles. Materialise must NOT delete or crash on it,
        # and presence must fail safe to False (better a re-fetch than a load failure at Begin).
        repo, snap = _build_symlinked_repo(cache, blob_of="model.bin")
        try:
            voicedl._materialise_snapshot(snap)                 # must not raise
            assert os.path.islink(os.path.join(snap, "model.bin")), "a broken link is left as-is"
            voicedl._download_model = lambda model, local_only=False: snap
            assert voicedl._present("small") is False, "a broken model.bin link must read as not-present"
        finally:
            voicedl._download_model = orig_dl
    print("  OK  a broken link is left as-is and falls back to not-present")


def test_probe_skips_mutation_while_download_lock_held():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        lock = voicedl._repo_lock(_repo_dir_of(snap))
        assert lock.acquire(blocking=False), "test setup: the repo lock is free to take"
        try:
            # A download owns the repo lock (F2): the probe must NOT mutate the cache under it.
            voicedl._materialise_snapshot(snap)
            assert all(os.path.islink(os.path.join(snap, n)) for n in os.listdir(snap)), \
                "a probe must skip migration while a download holds the repo lock (F2)"
        finally:
            lock.release()
    print("  OK  a presence probe does not mutate while a download holds the repo lock (F2)")


def test_probe_skips_mutation_while_delete_lock_held():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        lock = voicedl._repo_lock(_repo_dir_of(snap))
        assert lock.acquire(blocking=False), "test setup: the repo lock is free to take"
        try:
            # delete() holds the same per-repo lock across its rmtree (F2): a concurrent probe must not
            # rewrite a blob out from under the removal.
            voicedl._materialise_snapshot(snap)
            assert all(os.path.islink(os.path.join(snap, n)) for n in os.listdir(snap)), \
                "a probe must skip migration while a delete holds the repo lock (F2)"
        finally:
            lock.release()
    print("  OK  a presence probe does not mutate while a delete holds the repo lock (F2)")


def test_partial_migration_reports_not_present():
    if not _WIN:
        return _skip("materialisation only mutates on Windows")
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    orig_dl = voicedl._download_model
    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        # Inject a failure on a required NON-weight file: model.bin materialises, config.json cannot.
        if os.path.basename(str(dst)) == "config.json":
            raise OSError("injected os.replace failure")
        return real_replace(src, dst, *a, **k)

    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        blobs = os.path.join(repo, "blobs")
        try:
            os.replace = fake_replace
            voicedl._download_model = lambda model, local_only=False: snap
            assert voicedl._present("small") is False, \
                "a required file left as a symlink by a partial migration must read as not-present (F3)"
        finally:
            os.replace = real_replace
            voicedl._download_model = orig_dl
        # Nothing half-deleted: the failed file stayed a symlink and no blob was destroyed.
        assert os.path.islink(os.path.join(snap, "config.json")), "config.json stayed a symlink"
        assert not os.path.islink(os.path.join(snap, "model.bin")), "model.bin still materialised"
        assert os.path.isfile(os.path.join(blobs, "sha_cfg")), "config.json's blob survives the failed migration"
        assert os.path.isfile(os.path.join(blobs, "sha_model")), "model.bin's blob survives (hardlinked)"
    print("  OK  a partial migration reports not-present and destroys no blob (F3)")


def test_external_target_not_touched():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        # A same-volume symlink in the snapshot pointing OUTSIDE blobs/ (a user file in the cache root).
        external = os.path.join(cache, "user_secret.bin")
        _write(external, 4096)
        evil = os.path.join(snap, "evil.bin")
        os.symlink(external, evil)
        voicedl._materialise_snapshot(snap)
        assert os.path.islink(evil), "a link whose target is outside blobs/ must be left untouched (F4)"
        assert os.path.isfile(external), "the external file must NOT be relocated into the cache (F4)"
    print("  OK  a same-volume link to a file outside blobs/ is left untouched (F4)")


def test_incomplete_target_not_touched():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        blobs = os.path.join(repo, "blobs")
        _write(os.path.join(blobs, "sha_partial.incomplete"), voicedl._MIN_MODEL_BIN_BYTES + 10)
        cwd = os.getcwd()
        os.chdir(snap)
        try:
            os.symlink(os.path.join("..", "..", "blobs", "sha_partial.incomplete"), "pending.bin")
        finally:
            os.chdir(cwd)
        voicedl._materialise_snapshot(snap)
        assert os.path.islink(os.path.join(snap, "pending.bin")), \
            "a link to an .incomplete download temp must be left untouched (F4)"
    print("  OK  an .incomplete target is left untouched (F4)")


def test_dir_size_dedupes_symlink_and_blob():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache, model_bin_bytes=2_000_000)
        blob_bytes = 2_000_000 + 500 + 2048 + 1024             # one count of each real blob
        real = blob_bytes + len(_HASH)                         # + the small refs/main file
        doubled = blob_bytes * 2 + len(_HASH)                  # what the old os.walk sum produced
        got = voicedl._dir_size(repo)
        assert got == real, f"_dir_size must count each real file once ({real}), not doubled ({doubled}); got {got}"
    print("  OK  _dir_size() dedupes a blob and the symlink into it (no doubling)")


def test_dir_size_counts_hardlinked_blob_once_after_materialise():
    if not _WIN:
        return _skip("materialisation only mutates on Windows")
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache, model_bin_bytes=2_000_000)
        before = voicedl._dir_size(repo)
        voicedl._materialise_snapshot(snap)                    # symlinks -> hardlinks
        after = voicedl._dir_size(repo)
        assert after == before, \
            f"a hardlinked blob and its snapshot file must count ONCE ({before}), not doubled; got {after}"
    print("  OK  _dir_size() dedupes a hardlink and its blob by inode after migration (F1)")


def test_materialise_noop_and_present_on_real_file_cache():
    # The fallback case that needs NO symlinks: an already-materialised cache. Materialise is a no-op,
    # presence is True, and _dir_size counts each real file exactly once.
    orig_dl = voicedl._download_model
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_real_repo(cache)
        try:
            voicedl._materialise_snapshot(snap)                 # nothing to migrate: no-op, no error
            assert os.path.isfile(os.path.join(snap, "model.bin"))
            voicedl._download_model = lambda model, local_only=False: snap
            assert voicedl._present("small") is True
            expected = (voicedl._MIN_MODEL_BIN_BYTES + 4096) + 500
            assert voicedl._dir_size(repo) == expected, "a real-file cache counts once"
        finally:
            voicedl._download_model = orig_dl
    print("  OK  a real-file cache: materialise is a no-op, present is True, size counts once")


def test_ladder_model_present_materialises_symlinked_fixture():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    from live_transcribe import transcribe
    import huggingface_hub
    orig_sd = huggingface_hub.snapshot_download
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        try:
            huggingface_hub.snapshot_download = lambda model_id, **k: snap
            # The CPU downgrade ladder's usability probe must see a dev-era symlinked rung as present AND
            # (on Windows) materialise it, not just follow the symlink the frozen runtime cannot open (F5).
            assert transcribe.model_present("Systran/faster-whisper-small") is True, \
                "the ladder must read a symlinked dev-era rung as present (F5)"
            if _WIN:
                assert not os.path.islink(os.path.join(snap, "model.bin")), \
                    "the ladder path must materialise the rung through voicedl, not follow the symlink (F5)"
        finally:
            huggingface_hub.snapshot_download = orig_sd
    print("  OK  transcribe.model_present routes the ladder through voicedl materialise-and-check (F5)")


if __name__ == "__main__":
    tests = (test_materialise_converts_symlinks_and_keeps_blob,
             test_shared_blob_survives_and_both_revisions_readable,
             test_present_materialises_symlinked_snapshot,
             test_materialise_is_idempotent,
             test_materialise_broken_link_falls_back_to_not_present,
             test_probe_skips_mutation_while_download_lock_held,
             test_probe_skips_mutation_while_delete_lock_held,
             test_partial_migration_reports_not_present,
             test_external_target_not_touched,
             test_incomplete_target_not_touched,
             test_dir_size_dedupes_symlink_and_blob,
             test_dir_size_counts_hardlinked_blob_once_after_materialise,
             test_materialise_noop_and_present_on_real_file_cache,
             test_ladder_model_present_materialises_symlinked_fixture)
    if not _SYMLINKS_OK:
        print("  NOTE symlink creation unavailable: symlink-dependent tests will SKIP")
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
    print("\nAll voicedl-materialise tests passed.")
