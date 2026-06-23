r"""User settings and secrets for SA-Live-Transcribe.

All settings live in one JSON file at
%LOCALAPPDATA%\sa-live-transcribe\settings.json (the same per-user folder the
MSAL token cache and the licence already use). Everything is local to the
machine, in keeping with the privacy promise: nothing here is ever sent off the
device.

Secrets (currently just an optional cloud API key for the future
bring-your-own-model add-on) are not kept in plaintext. On Windows they are
encrypted at rest with DPAPI (CryptProtectData, current-user scope), so the key
cannot be read by another account or lifted straight out of the file. On other
platforms (macOS later) we fall back to base64, which is obfuscation and not
protection; that path is flagged so it can be swapped for the Keychain when
macOS support lands.
"""
import base64
import json
import os
import sys
import threading
from pathlib import Path

_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "sa-live-transcribe"
_SETTINGS_PATH = _DIR / "settings.json"

# settings.json is written from more than one thread (the request thread and the
# model-download thread). Serialise every read-modify-write so a long download
# finishing mid-edit cannot clobber another write. Reentrant because update() -> save().
_WRITE_LOCK = threading.RLock()

# DEFAULTS is the source of truth for the settings schema. load() merges the
# saved file over these, so a file written by an older build simply picks up any
# new key at its default rather than breaking.
DEFAULTS = {
    "interface_language": "en-ZA",    # language of the app's own UI (i18n later)
    "transcription_language": "af",   # default spoken language; "" == auto-detect
    "transcribe_languages": ["af", "en"],  # languages the user transcribes; drives the per-session language picker and (later) which model families to provision. The chosen language picks the model FAMILY (Afrikaans -> Fluister), the hardware picks the size.
    "tier": "auto",                   # default hardware tier
    "save_location": "",              # "" == default folder (project sessions/ in dev, per-user app-data when frozen)
    "default_context": "",            # standing names/jargon seeded every session
    "ai_backend": "local",            # "local" | "cloud" (cloud is a paid add-on)
    "ai_instructions": [],            # [{"id","name","prompt"}] saved system prompts
    "active_instruction_id": "",      # which saved instruction is active
    "summary_model": "",              # installed summary model: a .gguf filename in <_DIR>/models/ (or an absolute path)
    "setup_complete": False,          # first-run wizard done; persisted here so it survives WebView storage resets
    "device": "auto",                 # transcription device: "auto"/"gpu" use the GPU when ready, "cpu" forces CPU
    "engine": "auto",                 # model family override: "auto" (by language) | "fluister" | "whisper"
    "summary_device": "auto",         # summary device: "auto" uses the GPU when the build supports it and the model fits VRAM, "cpu" forces CPU
}


def _read_raw() -> dict:
    try:
        return json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_raw(raw: dict) -> None:
    _DIR.mkdir(parents=True, exist_ok=True)
    tmp = _SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _SETTINGS_PATH)  # atomic on Windows


def load() -> dict:
    """Full settings dict: DEFAULTS merged with whatever is on disk."""
    merged = dict(DEFAULTS)
    for k, v in _read_raw().items():
        if k in DEFAULTS:
            merged[k] = v
    return merged


def save(settings: dict) -> dict:
    """Persist the known settings keys. Any secret blob already on disk is kept."""
    with _WRITE_LOCK:
        raw = _read_raw()
        for k in DEFAULTS:
            if k in settings:
                raw[k] = settings[k]
        _write_raw(raw)
        return load()


def update(patch: dict) -> dict:
    with _WRITE_LOCK:
        s = load()
        for k, v in patch.items():
            if k in DEFAULTS:
                s[k] = v
        return save(s)


def public_view() -> dict:
    """Settings safe to hand the UI: the real settings plus secret-presence flags,
    never the secret values themselves."""
    s = load()
    s["has_cloud_api_key"] = get_cloud_api_key() is not None
    return s


def active_instruction() -> str:
    """The prompt text of the active saved AI instruction, or '' if none set."""
    s = load()
    aid = s.get("active_instruction_id") or ""
    for it in s.get("ai_instructions") or []:
        if isinstance(it, dict) and it.get("id") == aid:
            return (it.get("prompt") or "").strip()
    return ""


def summary_model_path():
    """Path to the installed summary model (.gguf), or None if not installed.

    Accepts either an absolute path or a filename resolved under <_DIR>/models/.
    """
    name = (load().get("summary_model") or "").strip()
    if not name:
        return None
    p = Path(name)
    if p.is_absolute() and p.is_file():
        return str(p)
    cand = _DIR / "models" / name
    return str(cand) if cand.is_file() else None


def models_dir(create: bool = False) -> Path:
    """Folder for downloadable local models (summary GGUFs).

    summary_model_path() resolves a bare filename here, so a model downloaded
    into this folder is found by storing just its filename in settings. Pass
    create=True only when about to write; a status read must not create the folder.
    """
    d = _DIR / "models"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


# --- secrets ---------------------------------------------------------------

def _dpapi(data: bytes, decrypt: bool) -> bytes:
    """Windows DPAPI round-trip (current-user scope). Raises on failure."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    fn.restype = wintypes.BOOL

    buf = ctypes.create_string_buffer(bytes(data), len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = fn(ctypes.byref(blob_in), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI call failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def set_cloud_api_key(key) -> None:
    """Store (encrypted) or, when key is falsy, clear the cloud API key."""
    with _WRITE_LOCK:
        raw = _read_raw()
        if not key:
            raw.pop("cloud_api_key", None)
        elif sys.platform == "win32":
            enc = _dpapi(key.encode("utf-8"), decrypt=False)
            raw["cloud_api_key"] = {"dpapi": base64.b64encode(enc).decode("ascii")}
        else:
            # Not real protection; replaced with the Keychain when macOS lands.
            raw["cloud_api_key"] = {"b64": base64.b64encode(key.encode("utf-8")).decode("ascii")}
        _write_raw(raw)


def get_cloud_api_key():
    raw = _read_raw().get("cloud_api_key")
    if not isinstance(raw, dict):
        return None
    try:
        if "dpapi" in raw:
            return _dpapi(base64.b64decode(raw["dpapi"]), decrypt=True).decode("utf-8")
        if "b64" in raw:
            return base64.b64decode(raw["b64"]).decode("utf-8")
    except Exception:
        return None
    return None
