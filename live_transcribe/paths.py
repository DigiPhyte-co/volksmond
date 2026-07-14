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
