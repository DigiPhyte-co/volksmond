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
import contextlib
import os
import shutil
import threading
from pathlib import Path

from . import config
from .__main__ import pick_tier
from .transcribe import (TIER_CONFIG, FLUISTER_REPOS,
                         SWIVURISO_REPO, SWIVURISO_LOCAL, SWIVURISO_LANGS, swivuriso_available)

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

# ── our own (versioned) models: Fluister ───────────────────────────────────
# Stock Whisper above is upstream and unversioned. Fluister is OURS, so it improves over time
# (Fluister v2, ...). load_model() reads the local cache with local_files_only=True, so a newer
# Fluister pushed to the same repo does NOT silently reach an existing install. The manual update
# check closes that gap: the app fetches our own models.json, compares the published version to
# what is recorded as installed here, and offers an opt-in update. Nothing is sent and nothing is
# fetched until the user clicks (same privacy stance as the app-version check).
# Default is the production manifest. SA_LIVE_MODELS_MANIFEST_URL overrides it (e.g. to a staging
# Pages URL) so the opt-in update flow can be tested end-to-end before the prod domain is cut over.
MODELS_MANIFEST_URL = os.environ.get(
    "SA_LIVE_MODELS_MANIFEST_URL", "https://volksmond.digiphyte.com/models.json")

# Approx on-disk sizes (bytes) of the Fluister ct2-int8 repos, for the progress estimate only;
# HuggingFace verifies the real file hashes. The live manifest's approx_bytes overrides these.
_FLUISTER_SIZES = {
    "large-v3":       1_530_000_000,
    "large-v3-turbo":   819_000_000,
    "medium":           774_000_000,
    "small":            250_000_000,
}

# The Fluister version shipped with THIS build. Used as the floor for a model that is in the cache
# but has no install record yet (downloaded before version tracking existed), so a later manifest
# that raises the version is still seen as an update. The live manifest is the real source of truth.
_FLUISTER_BASELINE = "1.0.0"

# Swivuriso (DSFSI / African Next Voices): one model (turbo) covering seven South African languages.
_SWIVURISO_SIZE = 820_000_000
_SWIVURISO_BASELINE = "1.0.0"

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
    if local_only:
        # Presence probe only: no network, no progress needed.
        return download_model(model, local_files_only=True)
    # Real download: instrument it so progress() tracks true transferred bytes in granular HTTP chunks
    # (not the blind on-disk snapshot, and not Xet's coarse/late reporting). Same file set / kwargs as
    # faster_whisper; only the tqdm class and the backend selection change for the duration.
    with _download_ctx():
        return download_model(model, local_files_only=False)


# A real ct2 model.bin is tens of MB up to multiple GB. Anything below this is a stub/truncation, not a
# usable weight file. We cannot verify the full bytes without the recorded hash (not available offline),
# so this is a non-trivial-size floor: it reliably catches the "missing / 0-byte / interrupted-early"
# cases that make snapshot_download hand back a path for a cache that will not load.
_MIN_MODEL_BIN_BYTES = 1_000_000


def _snapshot_has_weights(path):
    """True iff `path` (a resolved snapshot dir) holds a non-trivial model.bin. Fail-safe: a falsy path,
    a non-directory, a missing/tiny model.bin, or any error -> False (treat as not-present, so an
    interrupted download is re-fetched instead of failing to load)."""
    try:
        if not path or not os.path.isdir(path):
            return False
        binp = os.path.join(path, "model.bin")
        return os.path.isfile(binp) and os.path.getsize(binp) > _MIN_MODEL_BIN_BYTES
    except Exception:
        return False


def _present(model):
    """True iff faster-whisper's required files for this model are already cached AND the core weight
    file is really there (checked with local_files_only, so no network); i.e. Begin will not download.

    Trap this guards: hf_hub's snapshot_download(local_files_only=True) returns the snapshot path as soon
    as refs/main + a snapshots/<hash>/ dir survive, WITHOUT verifying the files are complete ("we can't
    check if all the files are actually there"). So a partial/interrupted download (model.bin missing or
    truncated) would otherwise read as present here and then fail to load at Begin. Verify a non-trivial
    model.bin under the returned snapshot dir before trusting it; any error -> not present (fail-safe)."""
    try:
        path = _download_model(model, local_only=True)
    except Exception:
        return False
    return _snapshot_has_weights(path)


def fluister_present(size, repo=None):
    """True iff the Fluister model for `size` is ready to load without a download: a local ct2 build dir
    exists (exactly what resolve_model will load on a dev machine / SA_LIVE_AF_MODEL override), OR the
    hosted HF repo is fully cached. The picker/preflight must judge Fluister presence this way, or a
    working local build reads as 'download first' while the engine actually loads it instantly (field
    bug). Never touches the af-lora dirs beyond an isdir() probe. No network."""
    try:
        from . import transcribe
        resolved = transcribe._FLUISTER.get(size)
        if isinstance(resolved, str) and os.path.isdir(resolved):
            return True
    except Exception:
        pass
    repo = repo or FLUISTER_REPOS.get(size)
    return bool(repo and _present(repo))


_LOCK = threading.Lock()
# `repo` is the HuggingFace cache folder to measure for progress (a stock _repo_id, or a Fluister
# repo). `kind` is "whisper" | "fluister"; `version`/`revision` carry the Fluister update being
# applied so _run_fluister can record it on success. One global download at a time, so one slot.
_STATE = {"state": "idle", "model": None, "repo": None, "kind": None,
          "version": None, "revision": None, "downloaded": 0, "total": 0, "error": None}


def _set(**kw):
    with _LOCK:
        _STATE.update(kw)


# ── real transferred-byte progress ─────────────────────────────────────────
# With HF_HUB_DISABLE_SYMLINKS=1 (forced in __init__ so ct2 can open the cache), hf_hub streams the
# big model.bin into a temp/incomplete location and only drops the finished file into the counted
# snapshot folder at the very END. Worse, on the xet backend the transferring bytes are not visible
# ANYWHERE under the HF cache during transfer. So an os.walk of the cache reads ~0 bytes for minutes
# and the byte-delta stall detector false-fires on a healthy download (field bug: "The download
# stalled" after 60s on every fresh model). Instead we drive progress from the REAL bytes hf transfers
# via the tqdm_class it calls per chunk. faster_whisper hardcodes tqdm_class=disabled_tqdm; we swap it
# for the duration of one download (safe: one global download slot at a time, guarded by _STATE/_LOCK).

def _add_transferred(n):
    """Accumulate real transferred bytes (from hf's per-chunk tqdm callback) into the download state,
    but only while a download is in flight. Monotonic by construction; the sole writer of downloaded."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return
    if n <= 0:
        return
    with _LOCK:
        if _STATE["state"] == "downloading":
            _STATE["downloaded"] = int(_STATE.get("downloaded") or 0) + n


_PROGRESS_TQDM_CLS = None


def _progress_tqdm_cls():
    """A never-rendered tqdm subclass that harvests real transferred bytes into the download state.
    hf_hub 1.15 uses the injected tqdm_class in two roles: a SHARED bytes bar (unit="B") that every
    per-file download forwards its chunk bytes into, and the thread_map outer FILE-count bar (no
    unit). We subclass the real tqdm so the full contract thread_map needs (get_lock/set_lock, the
    iteration protocol, total/refresh) still works, force disable=True so nothing is drawn (frozen app
    has no console), and override update() to accumulate ONLY the bytes bar. Built lazily and cached so
    voicedl imports without a hard tqdm dependency."""
    global _PROGRESS_TQDM_CLS
    if _PROGRESS_TQDM_CLS is not None:
        return _PROGRESS_TQDM_CLS
    from tqdm.auto import tqdm as _base_tqdm

    class _ProgressTqdm(_base_tqdm):
        def __init__(self, *args, **kwargs):
            # Capture the byte-bar identity from the CONSTRUCTOR kwargs, not self.unit: a disabled tqdm
            # never sets self.unit as an instance attribute, so reading it back returns nothing. In hf
            # 1.15 snapshot_download the shared "bytes_progress" bar (unit="B") receives every file's
            # transferred chunks (via _AggregatedTqdm); the thread_map outer bar (no unit) only counts
            # files - so we sum bytes ONLY from the unit="B" bar and never double-count file ticks.
            self._vm_is_bytes = kwargs.get("unit") == "B"
            kwargs["disable"] = True   # never render; we only harvest bytes
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            if self._vm_is_bytes:
                _add_transferred(n)
            return super().update(n)

    _PROGRESS_TQDM_CLS = _ProgressTqdm
    return _PROGRESS_TQDM_CLS


@contextlib.contextmanager
def _download_ctx():
    """Instrument ONE model download so its progress is both accurate and granular:

    1. Swap faster_whisper.download_model's hardcoded disabled tqdm for our accumulating one, so
       progress()/the stall detector track REAL transferred bytes (download_model reads `disabled_tqdm`
       from faster_whisper.utils' globals at call time, so reassigning it there takes effect).
    2. Force the plain HTTP backend by disabling hf's Xet backend for the duration. Xet reports
       progress only when each ~64 MB reconstruction block finishes AND spends the first tens of
       seconds in silent connection/negotiation, so on a slower link the transferred-byte signal stays
       FLAT past PREPARE_DOWNLOAD_STALL_SECONDS and the stall detector false-fires on a healthy
       download (the field bug). HTTP streams in 10 MB chunks (constants.DOWNLOAD_CHUNK_SIZE), so bytes
       move every few seconds and the stall detector only fires on a genuinely dead connection. Xet's
       first-download speed edge is marginal (nothing local to dedup against); reliable progress wins.

    The global download slot serialises downloads, so both process-wide swaps are never concurrent, and
    both are restored on exit. Any failure to set up degrades gracefully (progress() still has the
    on-disk floor; the download itself is unaffected)."""
    restore = []
    try:
        import faster_whisper.utils as u
        cls = _progress_tqdm_cls()
        restore.append(("tqdm", u, "disabled_tqdm", getattr(u, "disabled_tqdm", None)))
        u.disabled_tqdm = cls
    except Exception:
        pass
    try:
        import huggingface_hub.constants as _hc
        restore.append(("xet", _hc, "HF_HUB_DISABLE_XET", _hc.HF_HUB_DISABLE_XET))
        _hc.HF_HUB_DISABLE_XET = True
    except Exception:
        pass
    try:
        yield
    finally:
        for _tag, mod, attr, prev in reversed(restore):
            try:
                setattr(mod, attr, prev)
            except Exception:
                pass


def progress():
    with _LOCK:
        snap = dict(_STATE)
    # While downloading, report the REAL transferred-byte count (snap["downloaded"], driven by the
    # tqdm hook hf calls per chunk). Fall back to the on-disk snapshot size only as a floor, so a
    # backend that somehow reported no tqdm bytes still shows the final file drop. The floor is NOT
    # written back into _STATE, so the tqdm accumulator stays the sole writer of downloaded.
    if snap["state"] == "downloading" and snap.get("model"):
        floor = _dir_size(_repo_dir(snap.get("repo") or _repo_id(snap["model"])))
        if floor > snap["downloaded"]:
            snap["downloaded"] = floor
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
        _STATE.update({"state": "downloading", "model": model, "repo": _repo_id(model),
                       "kind": "whisper", "version": None, "revision": None,
                       "downloaded": 0, "total": _SIZES[model], "error": None})
    threading.Thread(target=_run, args=(model,), daemon=True).start()


def delete(model):
    """Remove a downloaded voice model from the cache to free space (re-downloadable later). `model`
    is a Whisper size (small/medium/...), a Fluister repo id (digiphyte/fluister-*), or the Swivuriso
    repo id. Refuses while any download is running. Only ever removes a cache folder for one of our
    known models, never an arbitrary path; also clears the recorded install version for our models."""
    fluister_repos = set(FLUISTER_REPOS.values())
    if model in _SIZES:
        dirs = [_repo_dir(_repo_id(model))]
    elif model in fluister_repos:
        dirs = [_repo_dir(model)]
    elif model == SWIVURISO_REPO:
        # Remove both the hosted-repo cache and, if present, the local ct2 build, so "Removed" is true.
        dirs = [_repo_dir(model)] + ([Path(SWIVURISO_LOCAL)] if os.path.isdir(SWIVURISO_LOCAL) else [])
    else:
        raise ValueError("Unknown model")
    with _LOCK:
        if _STATE["state"] == "downloading":
            raise RuntimeError("A model is downloading. Wait for it to finish.")
    for d in dirs:
        if d.exists():
            try:
                shutil.rmtree(d)
            except OSError as e:
                # Surface the failure (e.g. a Windows file lock) instead of reporting a
                # silent success that did not actually free any space.
                raise RuntimeError(f"Could not remove the model files: {e}")
    if model in fluister_repos or model == SWIVURISO_REPO:
        _forget_installed(model)


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


# ── Fluister: versioning, manifest, and opt-in update ──────────────────────

def _vtuple(v):
    """Numeric version tuple for comparison: "1.10.0" -> (1,10,0), "v2.0-beta" -> (2,0). Takes the
    leading digits of each dotted part and stops at the first part with no leading digit, so a
    pre-release suffix never makes a newer version sort older. Mirrors app.py's _version_tuple."""
    parts = []
    for p in str(v or "").strip().lstrip("vV").split("."):
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _installed_versions():
    """The {repo_id: {"version","revision"}} record of versioned models installed on this machine,
    read from settings. Empty (not an error) before anything has been recorded."""
    v = config.load().get("installed_models")
    return v if isinstance(v, dict) else {}


def record_installed(repo, version, revision=None):
    """Persist that `repo` is installed at `version` (and the commit we resolved), so a later manual
    update check can tell this is older than a newly published one. Atomic via config's write lock."""
    cur = dict(_installed_versions())
    cur[repo] = {"version": str(version or ""), "revision": str(revision or "")}
    config.update({"installed_models": cur})


def _forget_installed(repo):
    """Drop the recorded install version for a model we just removed, so the catalogue reports it as
    not-installed (and a later download re-records it)."""
    cur = dict(_installed_versions())
    if repo in cur:
        cur.pop(repo, None)
        config.update({"installed_models": cur})


def _ref_main_sha(repo):
    """The commit sha the local HF cache currently resolves 'main' to for this repo (i.e. what a
    local_files_only load will actually read), or '' if it cannot be determined."""
    try:
        return (_repo_dir(repo) / "refs" / "main").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _effective_version(repo, present, rec=None):
    """The version we believe is installed for `repo`: the recorded version, else the build baseline
    when the model is present (a pre-tracking install), else None when it is not installed at all."""
    rec = _installed_versions() if rec is None else rec
    recorded = (rec.get(repo) or {}).get("version")
    if recorded:
        return recorded
    return _FLUISTER_BASELINE if present else None


def fluister_catalogue():
    """Local-only install state of each Fluister (Afrikaans-tuned) model: size, repo, whether it is
    in the cache, the version we believe is installed, and on-disk size. Never touches the network
    (the manual update check does that). Presence is fluister_present(): a local ct2 build dir counts
    as present (it is what resolve_model loads on a dev machine / SA_LIVE_AF_MODEL override), so a
    working local build no longer reads as "download first"; the update flow still manages the HF repo."""
    rec = _installed_versions()
    out = []
    for size, repo in FLUISTER_REPOS.items():
        present = fluister_present(size, repo)
        out.append({
            "size": size,
            "repo": repo,
            "present": present,
            "installed_version": _effective_version(repo, present, rec),
            "approx_bytes": _FLUISTER_SIZES.get(size, 0),
            "size_on_disk": _dir_size(_repo_dir(repo)) if present else 0,
        })
    return out


def swivuriso_catalogue():
    """Local-only install state of the Swivuriso (DSFSI / African Next Voices) model: one model for
    seven South African languages. present = a local ct2 build OR the hosted repo cached. No network."""
    rec = _installed_versions()
    present = swivuriso_available() or _present(SWIVURISO_REPO)
    if os.path.isdir(SWIVURISO_LOCAL):
        on_disk = _dir_size(Path(SWIVURISO_LOCAL))
    elif _present(SWIVURISO_REPO):
        on_disk = _dir_size(_repo_dir(SWIVURISO_REPO))
    else:
        on_disk = 0
    return {
        "repo": SWIVURISO_REPO,
        "present": present,
        "installed_version": _effective_version(SWIVURISO_REPO, present, rec),
        "approx_bytes": _SWIVURISO_SIZE,
        "size_on_disk": on_disk,
        "languages": list(SWIVURISO_LANGS),
    }


def fetch_manifest(timeout=8):
    """Fetch our published models.json (ONE outbound GET, generic User-Agent, no user data). Returns
    the parsed dict; raises on any network/parse error for the caller to turn into a friendly 502.
    Only ever called from a user-initiated action (Check for updates / Update), never automatically."""
    import json as _json
    import urllib.request
    rq = urllib.request.Request(MODELS_MANIFEST_URL, headers={
        "Accept": "application/json",
        "User-Agent": "Volksmond-update-check",
    })
    with urllib.request.urlopen(rq, timeout=timeout) as resp:
        return _json.loads(resp.read().decode("utf-8"))


def model_update_status(manifest):
    """Given a fetched models.json, the per-Fluister-model update state for THIS machine. Only models
    that are actually installed here are considered (we never nag about a model you do not have).
    update_available = the manifest version is newer than the installed (recorded, else baseline)
    one. A pure function of (manifest, local state), so it is unit-testable without the network."""
    by_repo = {m.get("repo"): m for m in (manifest.get("models") or []) if m.get("repo")}
    rec = _installed_versions()
    updates = []
    for size, repo in FLUISTER_REPOS.items():
        if not _present(repo):
            continue
        man = by_repo.get(repo)
        if not man or not man.get("version"):
            continue
        installed = _effective_version(repo, True, rec)
        latest = str(man.get("version"))
        updates.append({
            "size": size,
            "repo": repo,
            "installed": installed,
            "latest": latest,
            "revision": man.get("revision") or "",
            "approx_bytes": man.get("approx_bytes") or _FLUISTER_SIZES.get(size, 0),
            "update_available": _vtuple(latest) > _vtuple(installed or ""),
        })
    # Swivuriso (one credited third-party model) rides the same channel once hosted + versioned.
    sv_present = swivuriso_available() or _present(SWIVURISO_REPO)
    sv_man = by_repo.get(SWIVURISO_REPO)
    if sv_present and sv_man and sv_man.get("version"):
        installed = _effective_version(SWIVURISO_REPO, True, rec)
        latest = str(sv_man.get("version"))
        updates.append({
            "size": "turbo",
            "repo": SWIVURISO_REPO,
            "installed": installed,
            "latest": latest,
            "revision": sv_man.get("revision") or "",
            "approx_bytes": sv_man.get("approx_bytes") or _SWIVURISO_SIZE,
            "update_available": _vtuple(latest) > _vtuple(installed or ""),
        })
    return updates


def start_fluister_update(size):
    """Begin a background download of the newest published version of one Fluister model, recording
    it as installed on success. Fetches the manifest first (the user clicked Update, itself a manual
    network action) to learn the version + pinned revision. Raises ValueError (unknown size / not in
    the manifest), RuntimeError (a download is already running), or a network error (let the caller
    map it to 502)."""
    repo = FLUISTER_REPOS.get(size)
    if not repo:
        raise ValueError("Unknown model")
    man = None
    for m in (fetch_manifest().get("models") or []):
        if m.get("repo") == repo:
            man = m
            break
    if not man:
        raise ValueError("That model is not in the update manifest.")
    version = str(man.get("version") or "")
    revision = man.get("revision") or ""
    total = man.get("approx_bytes") or _FLUISTER_SIZES.get(size, 0)
    with _LOCK:
        if _STATE["state"] == "downloading":
            raise RuntimeError("A model is already downloading.")
        _STATE.update({"state": "downloading", "model": size, "repo": repo, "kind": "fluister",
                       "version": version, "revision": revision,
                       "downloaded": 0, "total": total, "error": None})
    threading.Thread(target=_run_fluister, args=(repo, revision, version, total), daemon=True).start()


def _run_fluister(repo, revision, version, total):
    try:
        # Sync the repo's main ref to the latest published files. snapshot_download re-fetches only
        # the files whose hash changed (e.g. a new model.bin for v2). Because load_model() reads the
        # cache with local_files_only (no revision), syncing refs/main is what lets the offline load
        # pick up the update WITHOUT threading a revision through the whole engine.
        _download_model(repo, local_only=False)
        got = _ref_main_sha(repo)
        # Pin verification (supply-chain guard): when the manifest names a specific commit, refuse to
        # record the update if the bytes we got are not that commit. A "main"/blank pin accepts main.
        if revision and revision not in ("main", "") and got and got != revision:
            _set(state="error", error="The downloaded model did not match the published version. Please try again later.")
            return
        record_installed(repo, version, got or revision)
        _set(state="done", downloaded=total)
    except Exception as e:
        _set(state="error", error=str(e))


# ── Swivuriso: first-time download (DSFSI / African Next Voices) ────────────

def start_swivuriso_download():
    """Begin a background download of the Swivuriso model (one model, seven South African languages) from
    the hosted repo, recording it as installed at the build baseline version. A PLAIN repo pull, not
    a manifest-driven update: faster-whisper would fetch this repo from HuggingFace at first use
    anyway; this just does it up front with a progress bar instead of a silent multi-hundred-MB stall.
    Raises RuntimeError if a download is already running. Reuses _run_fluister (a generic repo sync +
    record-installed); the blank revision skips the pin check and records the baseline version."""
    repo = SWIVURISO_REPO
    with _LOCK:
        if _STATE["state"] == "downloading":
            raise RuntimeError("A model is already downloading.")
        _STATE.update({"state": "downloading", "model": "swivuriso", "repo": repo, "kind": "swivuriso",
                       "version": _SWIVURISO_BASELINE, "revision": "",
                       "downloaded": 0, "total": _SWIVURISO_SIZE, "error": None})
    threading.Thread(target=_run_fluister, args=(repo, "", _SWIVURISO_BASELINE, _SWIVURISO_SIZE),
                     daemon=True).start()


def start_fluister_download(size):
    """Begin a background first-download of one Fluister model from its hosted repo (a plain pull that
    records the build baseline version). For installing a size from the model card; the manifest-driven
    newer-version path is start_fluister_update. Raises ValueError (unknown size) or RuntimeError (a
    download is already running)."""
    repo = FLUISTER_REPOS.get(size)
    if not repo:
        raise ValueError("Unknown model")
    total = _FLUISTER_SIZES.get(size, 0)
    with _LOCK:
        if _STATE["state"] == "downloading":
            raise RuntimeError("A model is already downloading.")
        _STATE.update({"state": "downloading", "model": size, "repo": repo, "kind": "fluister",
                       "version": _FLUISTER_BASELINE, "revision": "",
                       "downloaded": 0, "total": total, "error": None})
    threading.Thread(target=_run_fluister, args=(repo, "", _FLUISTER_BASELINE, total), daemon=True).start()
