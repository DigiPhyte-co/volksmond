"""Tests for recording every session by default, and the guardrails that make that honest.

The default flipped from opt-in to ON: a live transcript that collapses on a slow machine is only
recoverable from the audio. What keeps that honest is checked here, cheapest first:

  1. The setting itself (config.py + web/app.py _record_default): "unset" and "false" are different
     things. An install whose settings.json has never carried a record_sessions key has never
     chosen, so it records; an explicit false is the user's own choice and is honoured. A
     corrupt/unreadable settings file reads as unset.
  2. The start endpoint: an omitted `record` follows the setting (so a record-only start needs no
     record flag at all, and a user who switched recording off gets "nothing to do" instead of a
     silent recording), while an explicit flag from the client still wins.
  3. The downgrade latch: STATE.downgraded is set on the FIRST engine downgrade even when the
     struggle banner is muted, is reported by /api/status, and rides the stop response, which is
     what the finish screen reads before offering a re-transcribe from the recording.
  4. Keep or delete at the end: GET /api/recording reports location and size, POST
     /api/recording/delete really removes the file (and any per-source channels), reports what it
     freed, refuses a traversal stem, and refuses while that session is still running.
  5. The recording format matches what the frozen build can actually write: FLAC would need
     soundfile/libsndfile in requirements.txt AND in the PyInstaller spec, so while neither is
     there the recorder must stay on WAV.
  6. The UI and the README say the true thing: an indicator while it records, keep-or-delete at the
     finish screen, a one-click switch in Settings, and no claim that audio is never kept.

No audio devices, no model load, no real capture: the seams are config._SETTINGS_PATH (a temp
file), webapp._sessions_dir (a temp folder), capture.AudioCapture (a fake) and STATE (hand-set and
restored).

Run:  python tests/test_recording_default.py   (from the project root; exit 0 = pass)
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from live_transcribe import config, sinks
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "live_transcribe" / "web" / "static" / "app.js").read_text(encoding="utf-8")


# --- helpers ---------------------------------------------------------------

class _settings_file:
    """Point config at a temp settings.json holding `raw` (None = no file at all)."""

    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.prev = config._SETTINGS_PATH
        config._SETTINGS_PATH = self.dir / "settings.json"
        if self.raw is not None:
            config._SETTINGS_PATH.write_text(self.raw, encoding="utf-8")
        return self

    def __exit__(self, *exc):
        config._SETTINGS_PATH = self.prev
        return False


class _FakeCapture:
    """Stands in for capture.AudioCapture on a record-only session: started and stopped, never
    fed. Carries the session clock and the two flags the start path reads back."""

    def __init__(self, **kw):
        self.kw = kw
        self.started = False
        self.stopped = False
        self._t0 = time.monotonic()
        self.sys_state = "active"

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def has_raw_mic(self):
        return False

    def aec_state(self):
        return (False, False)


_STATE_FIELDS = ("running", "stopping", "source_kind", "engine", "capture", "recording",
                 "recording_started", "recorder", "transcribing", "output_path", "started_at",
                 "struggle_nudge", "struggle_notified", "downgraded", "md_sink", "browser_sink",
                 "session_counted", "preparing", "model_ready", "pending_audio")


def _save_state():
    return {k: getattr(webapp.STATE, k) for k in _STATE_FIELDS}


def _restore_state(saved):
    for k, v in saved.items():
        setattr(webapp.STATE, k, v)


def _wait_stopped(timeout=10.0):
    """The stop path finalises on a background thread; wait for the session to be really over."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not webapp.STATE.running:
            return True
        time.sleep(0.05)
    return False


class _sessions_in:
    """Point the web layer's save folder at a temp directory."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp())

    def __enter__(self):
        self.prev = webapp._sessions_dir
        webapp._sessions_dir = lambda: self.dir
        return self.dir

    def __exit__(self, *exc):
        webapp._sessions_dir = self.prev
        return False


# --- 1. the setting: unset is not the same as false ------------------------

def test_schema_default_is_on():
    assert config.DEFAULTS["record_sessions"] is True, \
        "record_sessions must default to True: every session records unless the user says otherwise"


def test_unset_records_and_explicit_false_is_honoured():
    cases = [
        (None, True, "no settings file at all (a brand new install)"),
        ("{}", True, "an empty settings file"),
        ('{"aec_live": true, "tier": "auto"}', True, "an existing install that never chose"),
        ('{"record_sessions": true}', True, "explicitly on"),
        ('{"record_sessions": false}', False, "explicitly OFF: the user's choice, kept"),
        ("not json at all", True, "an unreadable settings file reads as unset"),
    ]
    for raw, expected, why in cases:
        with _settings_file(raw):
            got = webapp._record_default()
            assert got is expected, f"_record_default() should be {expected} for {why}, got {got}"
            # config.load() must agree: it is what the UI reads to draw the toggle.
            loaded = config.load().get("record_sessions", True) is not False
            assert loaded is expected, f"config.load() disagrees for {why}: {loaded}"


def test_saving_false_persists_a_real_false_not_a_missing_key():
    # "Off" has to survive a restart, which means it must be written to disk, not just left unset.
    with _settings_file("{}"):
        config.update({"record_sessions": False})
        raw = json.loads(config._SETTINGS_PATH.read_text(encoding="utf-8"))
        assert raw.get("record_sessions") is False, f"explicit off must be persisted: {raw}"
        assert webapp._record_default() is False
        config.update({"record_sessions": True})
        assert webapp._record_default() is True, "switching it back on must take effect"


# --- 2. the start endpoint follows the setting -----------------------------

def test_start_without_a_record_flag_follows_the_setting():
    saved = _save_state()
    prev_cap = webapp.capture.AudioCapture
    webapp.capture.AudioCapture = _FakeCapture
    try:
        with _sessions_in() as sdir, _settings_file("{}"):
            # No "record" key in the request at all, and no transcription: with the default ON this
            # is a valid record-only session.
            r = client.post("/api/start", json={"topic": "unset default", "transcribe": False})
            assert r.status_code == 200, f"{r.status_code}: {r.text}"
            j = r.json()
            assert j["recording"] is True, f"an omitted record flag must follow the setting: {j}"
            assert j["audio_stem"], "a recording session must return its audio stem"
            assert webapp.STATE.recording is True and webapp.STATE.recording_started is True
            # The finish screen reads `downgraded` off the stop response.
            webapp.STATE.downgraded = True
            r = client.post("/api/stop?what=all")
            assert r.status_code == 200, r.text
            assert r.json().get("downgraded") is True, \
                f"the stop response must carry the downgrade latch: {r.json()}"
            assert _wait_stopped(), "the session never finished stopping"
            assert list(sdir.iterdir()) is not None  # folder used, nothing else asserted here
    finally:
        webapp.capture.AudioCapture = prev_cap
        _restore_state(saved)


def test_start_with_recording_switched_off_does_not_record():
    saved = _save_state()
    prev_cap = webapp.capture.AudioCapture
    webapp.capture.AudioCapture = _FakeCapture
    try:
        with _sessions_in(), _settings_file('{"record_sessions": false}'):
            # Recording off + nothing to transcribe is genuinely nothing to do, and must be refused
            # rather than quietly recording anyway.
            r = client.post("/api/start", json={"topic": "off", "transcribe": False})
            assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
            assert not webapp.STATE.running, "a refused start must leave no session running"
            # An explicit flag from the client still wins over the setting.
            r = client.post("/api/start", json={"topic": "explicit", "transcribe": False, "record": True})
            assert r.status_code == 200, f"{r.status_code}: {r.text}"
            assert r.json()["recording"] is True, "an explicit record=true must override the setting"
            client.post("/api/stop?what=all")
            assert _wait_stopped(), "the session never finished stopping"
    finally:
        webapp.capture.AudioCapture = prev_cap
        _restore_state(saved)


# --- 3. the downgrade latch ------------------------------------------------

def test_downgrade_latch_survives_a_muted_banner_and_reaches_status():
    saved = _save_state()
    prev_load = config.load
    try:
        # Struggle banner MUTED: the surfacing is off, but the transcript still degraded, so the
        # finish screen's offer must still be able to fire.
        config.load = lambda: dict(config.DEFAULTS, struggle_nudge=False)

        class _StubEngine:
            engine = "auto"     # /api/status reads the family-override preference off the engine

            def pending(self):
                return 0

        engine = _StubEngine()
        webapp.STATE.running = True
        webapp.STATE.stopping = False
        webapp.STATE.source_kind = "live"
        webapp.STATE.transcribing = True
        webapp.STATE.engine = engine
        webapp.STATE.recording = True
        webapp.STATE.downgraded = False
        webapp.STATE.struggle_nudge = None
        webapp.STATE.struggle_notified = False
        published = webapp._on_downgrade(engine, "medium", "small")
        assert published is None, "a muted banner must publish no nudge"
        assert webapp.STATE.downgraded is True, \
            "the downgrade FACT must latch even when its banner is switched off"
        r = client.get("/api/status")
        assert r.status_code == 200
        assert r.json().get("downgraded") is True, f"/api/status must carry the latch: {r.json()}"

        # A callback from an engine that is no longer this session's must not mark it degraded.
        webapp.STATE.downgraded = False
        webapp._on_downgrade(_StubEngine(), "medium", "small")
        assert webapp.STATE.downgraded is False, "a stale engine must not latch the current session"
    finally:
        config.load = prev_load
        _restore_state(saved)


# --- 4. keep or delete at the end ------------------------------------------

def test_recording_info_reports_location_and_size():
    saved = _save_state()
    try:
        with _sessions_in() as sdir:
            stem = "2026-09-03-101500-review"
            (sdir / (stem + ".wav")).write_bytes(b"\x00" * 4096)
            r = client.get("/api/recording", params={"stem": stem})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j["exists"] is True and j["bytes"] == 4096, j
            assert j["name"] == stem + ".wav" and j["path"].endswith(stem + ".wav"), j
            # A stem with no recording is reported honestly, not as an error.
            j2 = client.get("/api/recording", params={"stem": "nothing-here"}).json()
            assert j2["exists"] is False and j2["bytes"] == 0, j2
    finally:
        _restore_state(saved)


def test_delete_removes_the_file_and_its_channels():
    saved = _save_state()
    try:
        with _sessions_in() as sdir:
            stem = "2026-09-03-102000-standup"
            rec = sdir / (stem + ".wav")
            mic = sdir / (stem + "-MIC.wav")
            transcript = sdir / (stem + ".md")
            rec.write_bytes(b"\x00" * 2048)
            mic.write_bytes(b"\x00" * 1024)      # only survives a failed fold, but delete means delete
            transcript.write_text("# transcript\n", encoding="utf-8")

            r = client.post("/api/recording/delete", json={"stem": stem})
            assert r.status_code == 200, r.text
            j = r.json()
            assert j["deleted"] is True and j["freed"] == 3072, j
            assert not rec.exists(), "the recording must actually be gone from disk"
            assert not mic.exists(), "a leftover per-source channel must go too"
            assert transcript.exists(), "deleting the audio must never touch the transcript"
            # Reporting it as deleted afterwards must stay true.
            assert client.get("/api/recording", params={"stem": stem}).json()["exists"] is False
            # Deleting again is harmless and honest about having removed nothing.
            again = client.post("/api/recording/delete", json={"stem": stem}).json()
            assert again["deleted"] is False and again["freed"] == 0, again
    finally:
        _restore_state(saved)


def test_delete_refuses_a_dodgy_stem_and_a_running_session():
    saved = _save_state()
    try:
        with _sessions_in() as sdir:
            for bad in ("../../secret", r"..\..\secret", "sess*", "CON"):
                r = client.post("/api/recording/delete", json={"stem": bad})
                assert r.status_code == 400, f"stem {bad!r} should be rejected, got {r.status_code}"
            stem = "2026-09-03-103000-live"
            (sdir / (stem + ".wav")).write_bytes(b"\x00" * 512)
            webapp.STATE.running = True
            webapp.STATE.output_path = sdir / (stem + ".md")
            r = client.post("/api/recording/delete", json={"stem": stem})
            assert r.status_code == 409, f"expected 409 while the session runs, got {r.status_code}"
            assert (sdir / (stem + ".wav")).exists(), "the running session's recording must survive"
    finally:
        _restore_state(saved)


# --- 5. the format matches what the build can write ------------------------

def test_recording_format_matches_what_the_spec_bundles():
    suffix = sinks.AudioRecorder.SUFFIX
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    spec = (ROOT / "sa-live-transcribe.spec").read_text(encoding="utf-8").lower()
    bundles_soundfile = "soundfile" in req and "soundfile" in spec
    if suffix == ".flac":
        assert bundles_soundfile, \
            "writing FLAC needs soundfile (libsndfile) in BOTH requirements.txt and the spec, " \
            "or the frozen build cannot write a recording at all"
    else:
        assert suffix == ".wav", f"unexpected recording format {suffix!r}"
        assert not bundles_soundfile, \
            "soundfile is now bundled: FLAC becomes available, revisit AudioRecorder.SUFFIX"


def test_the_web_layer_resolves_the_recording_through_that_one_suffix():
    saved = _save_state()
    try:
        with _sessions_in() as sdir:
            p = webapp._recording_path("2026-09-03-104000-x")
            assert p.name.endswith(sinks.AudioRecorder.SUFFIX), \
                f"the endpoint must look for the format the recorder writes: {p.name}"
            assert p.parent == sdir, "a recording is only ever resolved inside the save folder"
    finally:
        _restore_state(saved)


# --- 6. what the UI and the README say -------------------------------------

def test_ui_defaults_on_shows_it_and_offers_keep_or_delete():
    checks = [
        ('S.form.record = S.settings.record_sessions !== false;',
         "the pre-meeting toggle must start from the setting, with unset meaning on"),
        ('saveSettings({ record_sessions: !on });',
         "Settings must offer the one-click switch that persists the choice"),
        ('"Recording to this computer"',
         "the live screen must say the audio is being recorded, on this computer"),
        ('S.finish.recordingKept = true',
         "the finish screen must offer Keep"),
        ('api.post("/api/recording/delete"',
         "the finish screen must offer Delete, and it must really delete"),
        ('if (S.finish.downgraded)',
         "the finish screen must key the re-transcribe offer off the downgrade flag"),
        ('The live transcript ran on a smaller model for part of this meeting.',
         "the downgrade offer must say what actually happened"),
        ('function retranscribeFinishRecording',
         "the offer must reuse the existing file-transcription flow"),
    ]
    for needle, why in checks:
        assert needle in APP_JS, f"app.js: {why} (missing {needle!r})"
    assert 'go("importpre")' in APP_JS.split("function retranscribeFinishRecording")[1][:600], \
        "the re-transcribe offer must hand off to the import screen, not a second transcription path"
    assert "Audio is off by default" not in APP_JS, \
        "the Settings card must not still claim audio is off by default"


def test_readme_tells_the_truth_about_recordings():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    low = readme.lower()
    for stale in ("no audio is kept", "audio is off by default", "audio is never kept"):
        assert stale not in low, f"README still claims: {stale!r}"
    assert "recorded by default" in low, "README must say meetings are recorded by default"
    assert "never leaves" in low, "README must keep the local-only POPIA position"
    for phrase in ("delete", "settings"):
        assert phrase in low, f"README must say how to {phrase} the recording"


if __name__ == "__main__":
    tests = (test_schema_default_is_on,
             test_unset_records_and_explicit_false_is_honoured,
             test_saving_false_persists_a_real_false_not_a_missing_key,
             test_start_without_a_record_flag_follows_the_setting,
             test_start_with_recording_switched_off_does_not_record,
             test_downgrade_latch_survives_a_muted_banner_and_reaches_status,
             test_recording_info_reports_location_and_size,
             test_delete_removes_the_file_and_its_channels,
             test_delete_refuses_a_dodgy_stem_and_a_running_session,
             test_recording_format_matches_what_the_spec_bundles,
             test_the_web_layer_resolves_the_recording_through_that_one_suffix,
             test_ui_defaults_on_shows_it_and_offers_keep_or_delete,
             test_readme_tells_the_truth_about_recordings)
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"  OK  {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll recording-default tests passed.")
