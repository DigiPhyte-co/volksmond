"""Download-progress byte accounting (prep-dlfix): progress() must reflect the REAL transferred bytes
hf reports through its tqdm hook, not the on-disk cache snapshot.

Field bug: with HF_HUB_DISABLE_SYMLINKS=1 the model.bin streams into a temp/incomplete location (and on
the Xet backend, nowhere under the HF cache) until the very end, so walking the snapshot folder reads ~0
bytes for the whole transfer. The stall detector keyed on that on-disk delta, so it false-fired at 60s on
every fresh download. The fix drives progress from hf's per-chunk tqdm callback and forces the granular
HTTP backend. These tests pin that mechanism WITHOUT any network:

  (a) transferred bytes accumulate into progress()['downloaded'] even when the on-disk snapshot is empty;
  (b) the harvesting tqdm subclass counts ONLY the shared bytes bar (unit="B"), never the thread_map
      file-count bar, so file ticks are not summed as bytes and there is no double count;
  (c) _download_ctx() installs the harvesting tqdm AND forces Xet off for the duration, and restores both.

Run:  python tests/test_voicedl_progress.py   (from the project root; exit 0 = pass)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import voicedl


def _reset_state():
    voicedl._set(state="idle", model=None, repo=None, kind=None,
                 version=None, revision=None, downloaded=0, total=0, error=None)


def test_transferred_bytes_drive_progress_not_disk():
    # (a) While downloading, hf's tqdm callback (_add_transferred) is the sole driver of downloaded.
    # Point repo at a guaranteed-nonexistent cache folder so the on-disk floor is 0: progress() must
    # still climb from the transferred bytes, proving it does NOT depend on the snapshot walk.
    _reset_state()
    try:
        voicedl._set(state="downloading", model="tiny", repo="nonexistent/repo-xyz-123",
                     downloaded=0, total=1000)
        assert voicedl.progress()["downloaded"] == 0
        voicedl._add_transferred(100)
        voicedl._add_transferred(250)
        voicedl._add_transferred(150)
        p = voicedl.progress()
        assert p["downloaded"] == 500, p           # 100+250+150, from transfer only (disk floor is 0)
        assert p["total"] == 1000 and p["state"] == "downloading", p
        # Bytes are counted only while a download is in flight (guard against stray late callbacks).
        voicedl._set(state="done", downloaded=500)
        voicedl._add_transferred(999)
        assert voicedl.progress()["downloaded"] == 500, "bytes counted after the download ended"
    finally:
        _reset_state()
    print("  OK  progress() reflects real transferred bytes even when the on-disk snapshot is empty")


def test_only_the_bytes_bar_is_summed():
    # (b) hf 1.15 snapshot_download drives our tqdm_class in two roles: the shared bytes bar (unit="B")
    # gets every file's chunk bytes; the thread_map outer bar (no unit) only counts files. We must sum
    # ONLY the unit="B" bar, or file ticks would corrupt the byte total.
    _reset_state()
    cls = voicedl._progress_tqdm_cls()
    try:
        voicedl._set(state="downloading", model="tiny", repo="nonexistent/repo-xyz-123",
                     downloaded=0, total=1000)
        # The shared bytes bar: hf constructs it with unit="B" and forwards transferred chunks to it.
        bytes_bar = cls(total=0, initial=0, unit="B", unit_scale=True, desc="Downloading")
        bytes_bar.update(400)
        bytes_bar.update(600)
        assert voicedl.progress()["downloaded"] == 1000, voicedl.progress()
        # The thread_map file-count bar (no unit): its per-file ticks must NOT be summed as bytes.
        file_bar = cls(total=4, desc="Fetching 4 files")
        file_bar.update(1)
        file_bar.update(1)
        assert voicedl.progress()["downloaded"] == 1000, "file-count ticks were summed as bytes"
        # A disabled tqdm never renders (it only harvests): confirm the flag we gate on is correct.
        assert bytes_bar._vm_is_bytes is True and file_bar._vm_is_bytes is False
        assert bytes_bar.disable is True and file_bar.disable is True
    finally:
        _reset_state()
    print("  OK  only the unit='B' bytes bar is summed; the thread_map file-count bar is ignored")


def test_download_ctx_forces_http_and_restores():
    # (c) The download context installs the harvesting tqdm into faster_whisper AND forces hf's Xet
    # backend off (so downloads stream in granular HTTP chunks instead of Xet's coarse/late blocks),
    # then restores both. Missing optional deps degrade gracefully rather than crash.
    import faster_whisper.utils as fu
    import huggingface_hub.constants as hc
    before_tqdm = fu.disabled_tqdm
    before_xet = hc.HF_HUB_DISABLE_XET
    cls = voicedl._progress_tqdm_cls()
    with voicedl._download_ctx():
        assert fu.disabled_tqdm is cls, "harvesting tqdm not installed into faster_whisper for the download"
        assert hc.HF_HUB_DISABLE_XET is True, "Xet backend not forced off during the download"
    assert fu.disabled_tqdm is before_tqdm, "faster_whisper.disabled_tqdm not restored"
    assert hc.HF_HUB_DISABLE_XET is before_xet, "HF_HUB_DISABLE_XET not restored"
    print("  OK  _download_ctx installs the byte-harvesting tqdm + forces HTTP (Xet off), restores both")


if __name__ == "__main__":
    tests = (test_transferred_bytes_drive_progress_not_disk,
             test_only_the_bytes_bar_is_summed,
             test_download_ctx_forces_http_and_restores)
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
    print("\nAll voicedl-progress tests passed.")
