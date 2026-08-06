"""Per-user data directory, resolved per platform.

Windows keeps the historical location (%LOCALAPPDATA%\\sa-live-transcribe),
character-identical to the inline expression this module replaced, so nothing
moves for existing installs. macOS and Linux get their conventional homes.

Deliberately stdlib-only and import-light: app_main.py imports this BEFORE the
rest of the package to place the frozen-build crash log, so this module must
never pull in heavy dependencies or anything that could fail at import time.
"""
import os
import sys
from pathlib import Path


def data_dir_for(platform: str) -> Path:
    """The per-user data directory for a given sys.platform value.

    Split out from data_dir() so tests can cover all three mappings directly,
    without patching sys.platform or reloading the module.
    """
    if platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "sa-live-transcribe"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Volksmond"
    return Path.home() / ".local" / "share" / "volksmond"


def data_dir() -> Path:
    """Where settings, the licence, models, logs and downloaded libraries live."""
    return data_dir_for(sys.platform)


def default_sessions_dir_for(platform: str) -> Path:
    """The default transcript/recording folder for a given sys.platform value.

    Windows gets a visible per-user folder: transcripts are the user's documents,
    not app data, and burying them under %LOCALAPPDATA% means they are hard to
    find and vanish on uninstall (fatal under MSIX, which virtualises AppData
    writes and deletes them with the package). Deliberately NOT Documents, which
    is commonly OneDrive-redirected: transcripts silently syncing to the cloud
    would undermine the local-only posture. Path.home() honours USERPROFILE on
    Windows, same fallback style as data_dir_for above.

    macOS and Linux keep sessions under the data dir: ~/Volksmond is unidiomatic
    on macOS and would violate the XDG posture on Linux.
    """
    if platform == "win32":
        return Path.home() / "Volksmond"
    return data_dir_for(platform) / "sessions"


def default_sessions_dir() -> Path:
    """Where transcripts and recordings go when no save_location is configured."""
    return default_sessions_dir_for(sys.platform)
