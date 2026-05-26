"""Tests for the summary-model downloader (modeldl.py).

Integrity is verified on a fresh download and on an already-present file, and a
checksum mismatch cleans up and errors instead of installing junk. No network and no
real 3 GB model: requests.get is mocked with a tiny in-memory stream, and a temporary
catalogue entry is used, so this runs in well under a second.

Run:  python tests/test_modeldl.py   (from the project root; exit 0 = pass)
"""
import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from live_transcribe import config, modeldl


class _FakeResp:
    """Minimal stand-in for a streaming requests.Response."""
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i:i + chunk_size]


def _isolate():
    """Point config at a throwaway data dir so the real settings are untouched."""
    tmp = Path(tempfile.mkdtemp())
    config._DIR = tmp
    config._SETTINGS_PATH = tmp / "settings.json"
    return tmp


def _entry(content, sha):
    modeldl._BY_KEY["__test__"] = {
        "key": "__test__", "params": "T", "approx_bytes": len(content),
        "repo_id": "x/y", "filename": "test-model.gguf", "revision": "r", "sha256": sha,
    }


def _wait(timeout=10):
    t = time.time()
    while modeldl.progress()["state"] == "downloading" and time.time() - t < timeout:
        time.sleep(0.02)
    return modeldl.progress()


def _reset():
    modeldl._BY_KEY.pop("__test__", None)
    modeldl._STATE.update({"state": "idle", "key": None, "downloaded": 0, "total": 0, "error": None})


def test_download_verifies_and_installs():
    tmp = _isolate()
    content = b"FAKE-GGUF" * 5000
    _entry(content, hashlib.sha256(content).hexdigest())
    orig = requests.get
    requests.get = lambda *a, **k: _FakeResp(content)
    try:
        modeldl.start_download("__test__")
        st = _wait()
        assert st["state"] == "done", st
        assert (tmp / "models" / "test-model.gguf").is_file(), "model not installed"
        assert config.load().get("summary_model") == "test-model.gguf", "settings not pointed at model"
        assert not (tmp / "models" / "test-model.gguf.part").exists(), ".part left behind"
    finally:
        requests.get = orig
        _reset()
    print("  OK  download verifies SHA-256, then installs and selects the model")


def test_checksum_mismatch_cleans_up():
    tmp = _isolate()
    content = b"FAKE-GGUF" * 5000
    _entry(content, "deadbeef")  # wrong hash: the stream will not verify
    orig = requests.get
    requests.get = lambda *a, **k: _FakeResp(content)
    try:
        modeldl.start_download("__test__")
        st = _wait()
        assert st["state"] == "error", st
        assert not (tmp / "models" / "test-model.gguf").exists(), "unverified model was installed"
        assert not (tmp / "models" / "test-model.gguf.part").exists(), ".part not cleaned up"
        assert not config.load().get("summary_model"), "settings pointed at an unverified model"
    finally:
        requests.get = orig
        _reset()
    print("  OK  checksum mismatch -> error, .part removed, model not selected")


def test_existing_correct_file_used_without_download():
    tmp = _isolate()
    content = b"REAL-MODEL" * 5000
    _entry(content, hashlib.sha256(content).hexdigest())
    (config.models_dir(create=True) / "test-model.gguf").write_bytes(content)
    orig = requests.get

    def _boom(*a, **k):
        raise AssertionError("must not download when a correct file is already present")

    requests.get = _boom
    try:
        modeldl.start_download("__test__")
        st = _wait()
        assert st["state"] == "done", st
        assert config.load().get("summary_model") == "test-model.gguf"
    finally:
        requests.get = orig
        _reset()
    print("  OK  existing correct file is verified and used, with no re-download")


if __name__ == "__main__":
    failures = 0
    for fn in (test_download_verifies_and_installs,
               test_checksum_mismatch_cleans_up,
               test_existing_correct_file_used_without_download):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll modeldl tests passed.")
