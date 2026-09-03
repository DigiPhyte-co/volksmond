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

from . import paths

_DIR = paths.data_dir()
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
    "transcription_language": "af",   # default spoken language mode: "af" | "en" | "sa" (SA group) | a code like "zu"/"de"; "" == auto-detect
    "transcribe_languages": ["af", "en"],  # languages the user transcribes; drives the per-session language picker and (later) which model families to provision. The chosen language picks the model FAMILY (Afrikaans -> Fluister), the hardware picks the size.
    "tier": "auto",                   # default hardware tier
    "save_location": "",              # "" == default folder (project sessions/ in dev; frozen: %USERPROFILE%\Volksmond on Windows, data-dir sessions/ elsewhere)
    "save_location_migrated": False,  # the one-time save-location upgrade pin has run; never re-pin (a user may clear save_location later to adopt the new default)
    "default_context": "",            # standing names/jargon seeded every session
    "ai_backend": "local",            # "local" | "cloud" (cloud is a paid add-on)
    "ai_instructions": [],            # [{"id","name","prompt"}] saved system prompts
    "active_instruction_id": "",      # which saved instruction is active
    "summary_model": "",              # installed summary model: a .gguf filename in <_DIR>/models/ (or an absolute path)
    "setup_complete": False,          # first-run wizard done; persisted here so it survives WebView storage resets
    "device": "auto",                 # transcription device: "auto"/"gpu" use the GPU when ready, "cpu" forces CPU
    "engine": "auto",                 # model family override: "auto" (by language) | "fluister" | "whisper"
    "summary_device": "auto",         # summary device: "auto" uses the GPU when the build supports it and the model fits VRAM, "cpu" forces CPU
    "aec": False,                     # echo cancellation when re-transcribing (off by default: it cleans echo-only/you-listening audio well, but can garble YOUR words during sustained double-talk, so it is opt-in)
    "aec_live": True,                 # echo cancellation DURING a live meeting (mic + system loopback -> WebRTC APM). ON by default so the saved recording (a single AEC-cleaned stereo file) and the live transcript are echo-free. Tradeoff: can blur YOUR words during sustained double-talk.
    "agc_live": True,                 # live mic auto-gain (WebRTC AGC on the mic path, the Meet/Teams behaviour). ON by default so a quiet mic reaches the engine at a healthy level instead of hallucinating on near-silent speech. Independent of aec_live: applies in both AEC states and on mic-only sessions. Never applied to the SYS loopback.
    "mic_gate": True,                 # skip microphone chunks with no speech evidence in them before decoding (ON by default: it halves the mic's decode load and takes the fabricated lines that a near-silent mic produces with it). Decoding only, never the recording. A quiet mic in a loud room is protected by the in-session safety valve, and the live toggle can switch it off mid-meeting.
    "installed_models": {},           # versioned models installed on this machine: {repo_id: {"version","revision"}}. Written when a Fluister model is downloaded/updated, so the manual update check can tell when a newer one (e.g. Fluister v2) is published. Machine state, not a user preference.
    "licence_accepted": False,        # first-run licence agreement accepted. Honour-system gate; also mirrored to localStorage (vm_licence_accepted), same pattern as setup_complete.
    "session_count": 0,               # completed sessions on this machine; drives the one-time business-use nudge. Local only, never sent anywhere. Incremented by the server at session finalisation, not by the UI.
    "business_nudge_seen": False,     # the one-time business-use nudge has been shown and dismissed, so it never repeats.
    "summary_footer": True,           # append a small "Made with Volksmond" line to the summary file. Never the raw transcript, never any export of it.
    "calendar_reminders": True,       # while the app is open, poll the LOCAL Outlook calendar and nudge "start transcribing?" when a meeting begins. Business feature; inert without a licence + Outlook + pywin32. Local only, no network call.
    "live_notes_width": 0,            # width (px) of the live-screen notes column; 0 = default. Disk mirror of localStorage vm_live_split, which the WebView can wipe between launches.
    "os_toasts": True,                # show Windows desktop notifications (toasts) when Volksmond has something to say while its window is behind a call. ON by default: the whole point is to be seen when the window is not. One shared switch for every notification the app sends; each feature keeps its own on/off. Hard kill: SA_LIVE_TOASTS=0. Local only, no network path (Shell_NotifyIcon is a shell call).
    "silence_nudge": True,            # warn (banner + Windows notification) when NOTHING has been heard on either the mic or the system audio for a long stretch of a live session: the "recording an hour of nothing because Windows switched the mic" failure. Not a Business feature; it is data integrity, not a nicety. Hard kill: SA_LIVE_SILENCE_NUDGE=0.
    "silence_nudge_minutes": 5,       # how long everything must stay silent before that warning. Picker offers 3/5/10/15; a hand-edited value is clamped to 1..120.
    "struggle_nudge": True,           # surface (banner + Windows notification) the CPU auto-downgrade that fires when the machine cannot transcribe in real time. The downgrade ALWAYS happens regardless; this only gates the SURFACING, so a user who knowingly runs a weak CPU can silence it. Not a Business feature; data integrity, like silence_nudge. Hard kill: SA_LIVE_STRUGGLE_NUDGE=0.
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
