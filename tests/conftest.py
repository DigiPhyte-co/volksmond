"""Pytest-wide test isolation for the user settings file.

Several tests write settings (WP4's device selection persists mic/loopback on start and switch; the
pre-existing agc_live / mic_gate / record_sessions / aec_live round-trips write too). Writing the
REAL %LOCALAPPDATA%\\sa-live-transcribe\\settings.json from a test is unsafe: a crash mid-test would
leave a customer-facing app with a bogus remembered device or setting.

config locates the file at config._SETTINGS_PATH, a module global every read/write reads. A module
that reassigns it once at import is not enough on its own, because test_paths.py does
importlib.reload(config), which re-runs config's body and RESETS the path back to the real one for
every test that runs afterwards. So this autouse fixture re-points the path to a throwaway temp copy
BEFORE EVERY test, which no reload can defeat. The temp is seeded once from the real file so reads
still see realistic values; all writes land in temp and the real file is never touched.

Plain-script test runs (python tests/test_x.py) do not load conftest; those files keep their own
module-level redirect for that case.
"""
import pathlib
import shutil
import tempfile

import pytest

from live_transcribe import config as _config

_REAL_SETTINGS_PATH = _config._SETTINGS_PATH
_TMP_DIR = pathlib.Path(tempfile.mkdtemp(prefix="vm-suite-settings-"))
_TMP_SETTINGS_PATH = _TMP_DIR / "settings.json"
try:
    if _REAL_SETTINGS_PATH.exists():
        shutil.copy2(_REAL_SETTINGS_PATH, _TMP_SETTINGS_PATH)
except Exception:
    pass


@pytest.fixture(autouse=True)
def _redirect_settings_to_temp():
    """Point config at the temp settings copy for the duration of every test, defeating any earlier
    importlib.reload(config) that reset the path to the real file."""
    _config._SETTINGS_PATH = _TMP_SETTINGS_PATH
    yield
