"""Redirect the user settings file to a throwaway temp copy for the whole test process.

Why this exists (codex F7): several tests reach config.save / config.update through the web app
(WP4 device persistence on /api/start and /api/switch-device, plus the agc_live / mic_gate /
record_sessions / aec_live round-trips). config writes settings.json at config._SETTINGS_PATH.
Writing the REAL %LOCALAPPDATA%\\sa-live-transcribe\\settings.json from a test is unsafe: a crash
mid-test would leave a customer-facing app with a bogus remembered device or setting.

conftest.py already re-points config._SETTINGS_PATH before EVERY test, so a `pytest` run is safe and
survives test_paths.py's importlib.reload(config) (the fixture is autouse, so it re-points per test).
But a PLAIN-SCRIPT run (python tests/test_x.py, the documented way each file runs) does NOT load
conftest.py, so those writes would hit the real file. Importing this module at the top of every test
module that reaches config through the app closes that gap for script mode: the redirect is applied
once, at import, before the app has a chance to write.

Idempotent (Python caches the module, so the body runs once) and harmless under pytest (both this and
conftest point config at a temp copy; conftest's per-test fixture simply wins). No plain-script test
reloads config AND then writes settings in the same run (only test_paths.py reloads, and it never
writes), so a one-shot module-level redirect is enough for script mode; the reload case belongs to
the pytest suite, which conftest already covers per test.
"""
import os
import pathlib
import shutil
import sys
import tempfile

# Make `from live_transcribe import config` resolve even when this is imported before the test file's
# own sys.path bootstrap (script mode puts tests/ on sys.path[0], not the project root).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from live_transcribe import config as _config

_REAL_SETTINGS_PATH = _config._SETTINGS_PATH
_TMP_DIR = pathlib.Path(tempfile.mkdtemp(prefix="vm-script-settings-"))
_TMP_SETTINGS_PATH = _TMP_DIR / "settings.json"
try:
    if _REAL_SETTINGS_PATH.exists():
        shutil.copy2(_REAL_SETTINGS_PATH, _TMP_SETTINGS_PATH)
except Exception:
    pass

# Every config read/write now hits the temp copy, never the real file.
_config._SETTINGS_PATH = _TMP_SETTINGS_PATH
