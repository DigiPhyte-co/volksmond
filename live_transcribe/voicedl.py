"""One-click download of the Whisper transcription model(s), with a progress bar.

Voice transcription runs a faster-whisper model on this machine. faster-whisper
fetches that model from HuggingFace the first time it is used, which on a large
model is multi-GB and several minutes, with no visible progress. That silent wait
is exactly what makes "Begin" look frozen on a cold machine.

This module pulls the model down up front (during first-run setup, or from
Settings) with a real progress bar, into the SAME HuggingFace cache faster-whisper
reads, so the first meeting starts without a hidden download. It is the voice-model
twin of modeldl.py (which does the optional summary models the same way).

Nothing about the user is sent: only the public model weights are downloaded, the
same ones faster-whisper would fetch anyway. Transcription itself stays offline.

Security note: we download from the same Systran / mobiuslabsgmbh faster-whisper
repos faster-whisper already uses, and HuggingFace verifies each file's recorded
hash on download (a corrupt or truncated file is rejected). A per-model commit
revision can be pinned later via the optional "revision" field; we leave it unset
(latest) for now rather than bake in a guessed SHA that would break the fetch.
"""
import os
import shutil
import threading
from pathlib import Path

from .__main__ import pick_tier
from .transcribe import TIER_CONFIG

# Approx on-disk sizes (bytes) of the faster-whisper (CTranslate2) repos, dominated
# by model.bin. Used only for the progress estimate; HuggingFace verifies the real
# file hashes, so these need only be roughly right.
_SIZES = {
    "base":             145_000_000,
    "small":            484_000_000,
    "medium":         1_530_000_000,
    "large-v3-turbo": 1_620_000_000,
    "large-v3":       3_090_000_000,
}
# The four quality tiers shown to the user (and on the meeting screen), lowest ->
# highest accuracy: Fast=small, Balanced=medium, High quality=large-v3-turbo,
# Best=large-v3. tiny/base are internal live-downgrade rungs only, not offered here.
_OFFER = ["small", "medium", "large-v3-turbo", "large-v3"]

# Cache the (subprocess-backed) hardware probe: it cannot change during a run, and
# catalogue_public() is polled once a second while a download runs.
_AUTO_TIER = {"v": None}


def _auto_tier():
    if _AUTO_TIER["v"] is None:
        _AUTO_TIER["v"] = pick_tier("auto")
    return _AUTO_TIER["v"]


def _recommended_model():
    cfg = TIER_CONFIG.get(_auto_tier())
    return cfg["model"] if cfg else None


def _is_gpu():
    return _auto_tier() in ("gpu", "gpu-4gb")


def _repo_id(model):
    """The HuggingFace repo faster-whisper resolves this model name to."""
    try:
        from faster_whisper.utils import _MODELS
        if model in _MODELS:
            return _MODELS[model]
    except Exception:
        pass
    if model in ("large-v3-turbo", "turbo"):
        return "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    return "Systran/faster-whisper-" + model


def _hub_cache():
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        return Path(HF_HUB_CACHE)
    except Exception:
        base = os.environ.get("HF_HOME") or os.path.join(str(Path.home()), ".cache", "huggingface")
        return Path(base) / "hub"


def _repo_dir(repo_id):
    return _hub_cache() / ("models--" + repo_id.replace("/", "--"))


def _dir_size(p):
    total = 0
    try:
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _download_model(model, local_only=False):
    """Fetch (or, with local_only, just locate) the model via faster-whisper's OWN
    downloader, so the exact file set it loads (model.bin, config, tokenizer,
    vocabulary) is what we check/fetch. This is what makes "present" mean "Begin will
    not have to download": a partial cache fails local_files_only and reports
    not-present, instead of a generic snapshot check passing on incomplete files."""
    try:
        from faster_whisper import download_model
    except Exception:
        from faster_whisper.utils import download_model
    return download_model(model, local_files_only=local_only)


def _present(model):
    """True iff faster-whisper's required files for this model are already cached
    (checked with local_files_only, so no network); i.e. Begin will not download."""
    try:
        _download_model(model, local_only=True)
        return True
    except Exception:
        return False


_LOCK = threading.Lock()
_STATE = {"state": "idle", "model": None, "downloaded": 0, "total": 0, "error": None}


def _set(**kw):
    with _LOCK:
        _STATE.update(kw)


def progress():
    with _LOCK:
        snap = dict(_STATE)
    # While downloading, report the live on-disk size of the model's cache folder
    # (snapshot_download streams into it, including *.incomplete blobs), so the bar
    # moves without depending on a tqdm hook.
    if snap["state"] == "downloading" and snap.get("model"):
        live = _dir_size(_repo_dir(_repo_id(snap["model"])))
        if live > snap["downloaded"]:
            snap["downloaded"] = live
            with _LOCK:
                if _STATE["state"] == "downloading":
                    _STATE["downloaded"] = live
    return snap


def catalogue_public():
    """The voice models the UI can offer, plus what this machine should use.

    `recommended` marks the model the app would auto-pick on this hardware. The UI
    keys the Quality selector by tier, so `tier_models` lets it resolve any quality
    to its model (to show a "will download" hint)."""
    rec = _recommended_model()
    models = []
    for model in _OFFER:
        present = _present(model)
        models.append({
            "model": model,
            "approx_bytes": _SIZES.get(model, 0),
            "present": present,
            # Real bytes on disk (so the user can see what removing it frees).
            "size_on_disk": _dir_size(_repo_dir(_repo_id(model))) if present else 0,
            "recommended": model == rec,
        })
    return {
        "recommended_model": rec,
        "recommended_tier": _auto_tier(),
        "is_gpu": _is_gpu(),
        "models": models,
    }


def cache_dir():
    """The HuggingFace hub cache directory where the voice models live, so the UI can
    show the user where to find and remove them on disk."""
    return str(_hub_cache())


def start_download(model):
    """Begin a background download of one voice model. Raises ValueError for an
    unknown model, RuntimeError if one is already downloading. An already-cached
    model resolves to done immediately without re-fetching."""
    if model not in _SIZES:
        raise ValueError("Unknown model")
    with _LOCK:
        if _STATE["state"] == "downloading":
            raise RuntimeError("A model is already downloading.")
        _STATE.update({"state": "downloading", "model": model, "downloaded": 0,
                       "total": _SIZES[model], "error": None})
    threading.Thread(target=_run, args=(model,), daemon=True).start()


def delete(model):
    """Remove a downloaded voice model from the cache to free space. The user can
    download it again later. Refuses while that same model is downloading. Only ever
    removes the cache folder for one of our catalogue models, never an arbitrary path."""
    if model not in _SIZES:
        raise ValueError("Unknown model")
    with _LOCK:
        if _STATE["state"] == "downloading" and _STATE.get("model") == model:
            raise RuntimeError("That model is downloading.")
    d = _repo_dir(_repo_id(model))
    if d.exists():
        try:
            shutil.rmtree(d)
        except OSError as e:
            # Surface the failure (e.g. a Windows file lock) instead of reporting a
            # silent success that did not actually free any space.
            raise RuntimeError(f"Could not remove the model files: {e}")


def _run(model):
    total = _SIZES[model]
    try:
        # Already fully cached (exact faster-whisper file set): nothing to fetch.
        if _present(model):
            _set(state="done", downloaded=total)
            return
        # Download the exact files faster-whisper will load, into the same cache it
        # reads, so the first Begin loads without a network round-trip. faster-whisper
        # (via HuggingFace) verifies each file as it goes.
        _download_model(model, local_only=False)
        _set(state="done", downloaded=total)
    except Exception as e:
        _set(state="error", error=str(e))
