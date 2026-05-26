"""One-click download of the optional local summary model (a GGUF).

Summaries are an on-device extra: a small Gemma instruct model runs in
llama.cpp, separate from the transcription engine. Rather than make the user
hunt for a .gguf file, this fetches a pinned, public model straight into
<data>/models/ (where config.summary_model_path() already looks) and points
settings at it. It is the same kind of model fetch the Whisper tier already does
on first run: a public model file is pulled down, and nothing about the user is
sent. Transcription stays fully offline.
"""
import hashlib
import threading

from . import config

# Gemma 4 is the locked choice: smaller models are too weak for SA Afrikaans, which
# is why the app carries llama.cpp (ct2 4.7.2 cannot convert Gemma 4's KV-shared
# layers). Pinned to the UNGATED unsloth GGUF mirrors (no licence click). Q4_K_M is
# the speed/quality sweet spot for CPU. approx_bytes are the exact sizes. Each entry
# pins a commit SHA + the file's SHA-256, so the download is reproducible and verified
# (a changed/compromised repo, or a wrong-size stream, is rejected before install).
CATALOGUE = [
    {
        "key": "gemma-4-e2b",
        "params": "2B",
        "approx_bytes": 3_106_736_256,      # ~3.11 GB (exact, pinned)
        "repo_id": "unsloth/gemma-4-E2B-it-GGUF",
        "filename": "gemma-4-E2B-it-Q4_K_M.gguf",
        "revision": "90f9618340396838ee7ff5b0ba2da27da62953d3",
        "sha256": "9378bc471710229ef165709b62e34bfb62231420ddaf6d729e727305b5b8672d",
    },
    {
        "key": "gemma-4-e4b",
        "params": "4B",
        "approx_bytes": 4_977_169_568,      # ~4.98 GB (exact, pinned)
        "repo_id": "unsloth/gemma-4-E4B-it-GGUF",
        "filename": "gemma-4-E4B-it-Q4_K_M.gguf",
        "revision": "653803f092503c04a65164346f3208a36e707693",
        "sha256": "519b9793ed6ce0ff530f1b7c96e848e08e49e7af4d57bb97f76215963a54146d",
    },
]
_BY_KEY = {m["key"]: m for m in CATALOGUE}

_LOCK = threading.Lock()
_STATE = {"state": "idle", "key": None, "downloaded": 0, "total": 0, "error": None}


def catalogue_public():
    """Catalogue entries the UI shows. `present` = the file is on disk; `active` = it
    is the currently selected summary model. A present-but-unselected model can be
    turned on in one click (start_download short-circuits when the file already exists)."""
    mdir = config.models_dir()
    selected = (config.load().get("summary_model") or "").strip()
    out = []
    for m in CATALOGUE:
        present = (mdir / m["filename"]).is_file()
        out.append({"key": m["key"], "params": m["params"], "approx_bytes": m["approx_bytes"],
                    "present": present, "active": present and selected == m["filename"]})
    return out


def progress():
    with _LOCK:
        return dict(_STATE)


def _set(**kw):
    with _LOCK:
        _STATE.update(kw)


def start_download(key):
    """Begin a background download in a worker thread.

    Raises ValueError for an unknown key, RuntimeError if one is already running. A
    correct copy already on disk is verified and used without re-downloading; a
    missing, wrong-size, or wrong-hash file is (re)downloaded. Verification runs in
    the worker so the request returns immediately.
    """
    m = _BY_KEY.get(key)
    if not m:
        raise ValueError("Unknown model")
    with _LOCK:
        if _STATE["state"] == "downloading":
            raise RuntimeError("A model is already downloading.")
        _STATE.update({"state": "downloading", "key": key, "downloaded": 0,
                       "total": m["approx_bytes"], "error": None})
    threading.Thread(target=_run, args=(m,), daemon=True).start()


def _sha256(path):
    """Stream the SHA-256 of a file without loading it all into memory."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(m):
    import requests
    from huggingface_hub import hf_hub_url
    final = config.models_dir(create=True) / m["filename"]
    part = final.with_name(final.name + ".part")
    total = m["approx_bytes"]
    try:
        _set(total=total)
        # A correct copy already on disk: verify its hash and use it, no re-download.
        # A wrong or corrupt file is not trusted; fall through and download afresh.
        if final.is_file() and _sha256(final) == m["sha256"]:
            config.update({"summary_model": m["filename"]})
            _set(state="done", downloaded=total)
            return
        # Pin the exact commit so a force-push to the repo cannot change what we fetch.
        url = hf_hub_url(repo_id=m["repo_id"], filename=m["filename"], revision=m["revision"])
        cap = total + 16 * 1024 * 1024      # exact size + slack; abort a runaway stream
        digest = hashlib.sha256()
        with requests.get(url, stream=True, timeout=(15, 60)) as r:
            r.raise_for_status()
            done = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if done > cap:
                        raise RuntimeError("download exceeded the expected size")
                    _set(downloaded=done)
        if digest.hexdigest() != m["sha256"]:
            raise RuntimeError("checksum mismatch; the downloaded model did not verify")
        part.replace(final)
        # Point settings at the freshly installed (verified) model so summaries light up.
        config.update({"summary_model": m["filename"]})
        _set(state="done", downloaded=total)
    except Exception as e:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        _set(state="error", error=str(e))
