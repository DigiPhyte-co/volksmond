"""Dev-era symlinked HuggingFace caches must read as installed, load in the frozen build, and report a
truthful on-disk size (wp/hf-symlink-presence hotfix for 1.13.3).

Field bug: small/medium were downloaded on a Developer-Mode machine with the symlinked HF layout
(snapshots/<hash>/model.bin -> ../../blobs/<sha>). The frozen build's bundled runtime (MSVCP140 14.50)
cannot open a Windows symlink, so ct2 fails to load the model at Begin, and the Models page can read the
cache as not-installed; where it does read as installed, _dir_size DOUBLED the size (small 0.97 GB,
medium 3.1 GB, i.e. 2x their true 0.48 / 1.53 GB) because os.walk counted the blob AND the symlink that
follows into it.

What this pins, all WITHOUT any network:

  MATERIALISE - _materialise_snapshot rewrites every symlinked file in a snapshot dir as the real blob
                (one same-volume rename each, no copy), so the frozen build can both see and LOAD it. It
                is idempotent, safe on a broken link (left as-is -> falls back to not-present), and a
                no-op on an already-materialised (real-file) cache.

  PRESENCE    - _present materialises the snapshot first, so a symlinked cache reads as installed and is
                left in the real-file shape the frozen runtime can open.

  SIZE        - _dir_size dedupes by resolved real path, so a blob and the symlink into it count once
                (no doubling), and a real-file cache counts once too.

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


def _build_symlinked_repo(cache, model_bin_bytes=None, blob_of=None):
    """Create a HF cache repo for faster-whisper-small under `cache` in the dev-era SYMLINK layout:
    blobs/<sha> real files + snapshots/<hash>/<name> -> ../../blobs/<sha> relative symlinks + refs/main.
    Returns the snapshot dir. `blob_of` lets a test drop one file's blob to simulate a broken link."""
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


def _build_real_repo(cache):
    """The already-materialised (real-file) layout: no symlinks, no blobs/ dir. Returns (repo, snap)."""
    repo = os.path.join(cache, "models--Systran--faster-whisper-small")
    snap = os.path.join(repo, "snapshots", _HASH)
    os.makedirs(snap)
    _write(os.path.join(snap, "model.bin"), voicedl._MIN_MODEL_BIN_BYTES + 4096)
    _write(os.path.join(snap, "config.json"), 500)
    return repo, snap


def test_materialise_converts_symlinks_to_real_files():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        assert all(os.path.islink(os.path.join(snap, n)) for n in os.listdir(snap)), "setup: all symlinks"
        blobs = os.path.join(repo, "blobs")
        assert os.listdir(blobs), "setup: blobs present"
        voicedl._materialise_snapshot(snap)
        for n in os.listdir(snap):
            p = os.path.join(snap, n)
            assert not os.path.islink(p), f"{n} must be a real file after materialise"
            assert os.path.isfile(p)
        assert os.stat(os.path.join(snap, "model.bin")).st_size > voicedl._MIN_MODEL_BIN_BYTES, \
            "model.bin keeps its real bytes"
        assert os.listdir(blobs) == [], "each blob is MOVED into place (one rename, not a copy)"
    print("  OK  _materialise_snapshot() rewrites every symlinked file as its real blob (1x move)")


def test_present_materialises_symlinked_snapshot():
    if not _SYMLINKS_OK:
        return _skip("symlink creation not available on this runner")
    orig_dl = voicedl._download_model
    with tempfile.TemporaryDirectory() as cache:
        repo, snap = _build_symlinked_repo(cache)
        try:
            voicedl._download_model = lambda model, local_only=False: snap
            assert voicedl._present("small") is True, "a symlinked-but-valid cache must read as present"
            assert not os.path.islink(os.path.join(snap, "model.bin")), \
                "_present must leave the snapshot in the real-file shape the frozen runtime can open"
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


if __name__ == "__main__":
    tests = (test_materialise_converts_symlinks_to_real_files,
             test_present_materialises_symlinked_snapshot,
             test_materialise_is_idempotent,
             test_materialise_broken_link_falls_back_to_not_present,
             test_dir_size_dedupes_symlink_and_blob,
             test_materialise_noop_and_present_on_real_file_cache)
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
