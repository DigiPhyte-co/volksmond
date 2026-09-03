r"""Tests for live_transcribe/diagnostics.py: the rotating log, the launch header,
and the support bundle.

What these lock down, and why each one exists:
  * Rotation keeps the current launch plus five, and caps each file. The old code
    truncated volksmond.log on every launch, so a user who restarted the app after a
    failure destroyed the only evidence of it. Two support cases died that way.
  * The header carries the facts a first reply needs (version, install kind, OS, CPU,
    cores, RAM, Python) and NEVER the licence token.
  * The bundle contains exactly the allow-listed members, the user's profile path is
    redacted, and a transcript sitting in the sessions folder is not swept in. That
    last one is the POPIA guarantee: transcripts are personal information and do not
    leave the machine.
  * The feedback email body gained the machine line and lost the old "No logs" excuse.

Run:  python tests/test_diagnostics.py   (from the project root; exit 0 = pass)
"""
import json
import os
import re
import sys
import zipfile
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import diagnostics

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_JS = ROOT / "live_transcribe" / "web" / "static" / "app.js"


def _tmpdir(name):
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="vm-diag-" + name + "-"))
    return d


# --- rotation --------------------------------------------------------------

def test_rotation_keeps_five_previous_launches():
    base = _tmpdir("rot")
    for launch in range(1, 9):                      # eight launches in a row
        log = diagnostics.open_log(base)
        log.write(f"launch {launch}\n")
        log.close()
    names = sorted(p.name for p in base.iterdir())
    expect = sorted(["volksmond.log"] + [f"volksmond.log.{i}" for i in range(1, 6)])
    assert names == expect, names
    # Current file is the newest launch; .1 is the one before it, and nothing older
    # than five launches back survived.
    assert (base / "volksmond.log").read_text(encoding="utf-8").strip() == "launch 8"
    assert (base / "volksmond.log.1").read_text(encoding="utf-8").strip() == "launch 7"
    assert (base / "volksmond.log.5").read_text(encoding="utf-8").strip() == "launch 3"
    print("  OK  rotation keeps the current launch plus the previous five")


def test_rotation_caps_file_size_mid_run():
    # A runaway logger inside ONE launch must still be bounded: the writer rolls when
    # the current file passes the cap, so the set can never grow without limit.
    base = _tmpdir("cap")
    log = diagnostics.open_log(base, max_bytes=2000, keep=5)
    for i in range(2000):
        log.write(f"line {i} " + "x" * 50 + "\n")
    log.close()
    files = list(base.iterdir())
    assert len(files) <= 6, [f.name for f in files]
    for f in files:
        assert f.stat().st_size < 2000 + 200, (f.name, f.stat().st_size)
    print("  OK  a single run is capped and rolls instead of growing")


def test_writes_are_flushed_for_a_hard_crash():
    # There are real crash dumps for this app; a write still buffered when the process
    # dies is a line we never get to read. Every write flushes, so the bytes are on
    # disk before the call returns, WITHOUT closing the file.
    base = _tmpdir("flush")
    log = diagnostics.open_log(base)
    log.write("last words before the crash\n")
    on_disk = (base / "volksmond.log").read_text(encoding="utf-8")
    assert "last words before the crash" in on_disk, repr(on_disk)
    log.close()
    print("  OK  every write reaches disk immediately (crash-survivable)")


def test_writer_behaves_like_the_stream_it_replaces():
    # It is assigned straight to sys.stdout/sys.stderr in a windowed build, so anything
    # that reached for a real file handle must keep working.
    base = _tmpdir("stream")
    log = diagnostics.open_log(base)
    try:
        log.writelines(["a\n", "b\n"])
        assert log.isatty() is False
        assert isinstance(log.fileno(), int)        # delegated to the real file
        assert log.encoding.lower().startswith("utf")
        log.flush()
        print("hello from print()", file=log)
        assert "hello from print()" in (base / "volksmond.log").read_text(encoding="utf-8")
    finally:
        log.close()
    print("  OK  the rotating writer is a drop-in for the plain file handle")


# --- header ----------------------------------------------------------------

def test_header_has_the_fields_a_support_case_needs():
    text = diagnostics.header_text(with_hardware=False)
    for field in ("app: Volksmond", "install:", "frozen:", "os:", "cpu:",
                  "logical cores", "ram:", "python:", "ct2:"):
        assert field in text, (field, text)
    # The CPU model was the exact fact we could not learn from the last support case.
    assert re.search(r"cpu: \S", text), text
    print("  OK  header carries version, install kind, OS, CPU, cores, RAM, versions")


def test_header_never_carries_the_licence_token():
    # The licence is a signed token in <data_dir>/license.key. It is a secret and must
    # never reach a log or a bundle. Plant one and prove the header ignores it.
    from live_transcribe import licensing
    token = "PLANTEDLICENCETOKEN.abcdef0123456789"
    saved = licensing._LICENSE_PATH
    tmp = _tmpdir("lic") / "license.key"
    tmp.write_text(token, encoding="utf-8")
    licensing._LICENSE_PATH = tmp
    try:
        text = diagnostics.header_text(with_hardware=True) + diagnostics.summary_line()
    finally:
        licensing._LICENSE_PATH = saved
    assert token not in text and "PLANTEDLICENCETOKEN" not in text, text
    for word in ("license.key", "licence key", "token"):
        assert word not in text.lower(), (word, text)
    print("  OK  no licence token (or hint of one) in the header or the email line")


def test_install_kind_reads_the_evidence():
    # buildflags wins (the frozen editions' runtime hooks set it before any app code
    # runs); the WindowsApps path is the independent corroboration for an MSIX install.
    assert diagnostics.install_kind() == "source", "a source run must report source"
    assert diagnostics._under_windows_apps(
        r"C:\Program Files\WindowsApps\DigiPhyte.Volksmond_1.13.2_x64__abc\Volksmond.exe")
    assert diagnostics._under_windows_apps(r"C:\Program Files\Volksmond\Volksmond.exe") is False
    print("  OK  install kind: source here, WindowsApps path recognised as the Store build")


# --- bundle ----------------------------------------------------------------

_ALLOWED = {"system.txt", "settings.json", "models.txt"}


def test_bundle_holds_exactly_the_allowed_members():
    base = _tmpdir("bundle-logs")
    for launch in range(3):
        log = diagnostics.open_log(base)
        log.write(f"launch {launch}\n")
        log.close()
    dest = _tmpdir("bundle-out")
    out = diagnostics.save_bundle(dest_dir=dest, base=base)
    assert out.exists() and out.parent == dest, out
    assert re.fullmatch(r"volksmond-diagnostics-\d{8}-\d{4}\.zip", out.name), out.name
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    logs = {n for n in names if n.startswith("logs/")}
    assert names - logs == _ALLOWED, names - logs
    assert logs == {"logs/volksmond.log", "logs/volksmond.log.1", "logs/volksmond.log.2"}, logs
    print("  OK  bundle is exactly system.txt, settings.json, models.txt and the logs")


def test_bundle_never_includes_a_transcript():
    # THE privacy test. A transcript, a notes file and a recording sitting in the
    # sessions folder must not be swept into a file the user emails us.
    from live_transcribe import paths
    sessions = _tmpdir("sessions")
    secret = "Hierdie is 'n vertroulike gesprek oor Jan se mediese toestand."
    (sessions / "2026-09-03-meeting.md").write_text(secret, encoding="utf-8")
    (sessions / "2026-09-03-meeting-notes.md").write_text(secret, encoding="utf-8")
    (sessions / "2026-09-03-meeting.wav").write_bytes(b"RIFF fake audio")
    base = _tmpdir("bundle-logs2")
    log = diagnostics.open_log(base)
    log.write("nothing sensitive here\n")
    log.close()
    dest = _tmpdir("bundle-out2")
    saved = paths.default_sessions_dir
    paths.default_sessions_dir = lambda: sessions          # make it maximally findable
    try:
        out = diagnostics.save_bundle(dest_dir=dest, base=base)
    finally:
        paths.default_sessions_dir = saved
    with zipfile.ZipFile(out) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
        assert not any("meeting" in n for n in z.namelist()), z.namelist()
    assert secret.encode("utf-8") not in blob
    assert b"RIFF fake audio" not in blob
    print("  OK  transcripts, notes and audio are never collected")


def test_bundle_redacts_the_profile_path_and_secret_settings():
    base = _tmpdir("bundle-logs3")
    home = str(Path.home())
    log = diagnostics.open_log(base)
    log.write("model path " + os.path.join(home, "AppData", "Local", "model.bin") + "\n")
    log.close()
    dest = _tmpdir("bundle-out3")
    out = diagnostics.save_bundle(dest_dir=dest, base=base)
    with zipfile.ZipFile(out) as z:
        logtext = z.read("logs/volksmond.log").decode("utf-8")
        settings = json.loads(z.read("settings.json").decode("utf-8") or "{}")
    assert home not in logtext, logtext
    assert "<user>" in logtext, logtext
    for k, v in settings.items():
        if any(h in k.lower() for h in ("key", "secret", "token", "password", "licen")):
            assert v == "<redacted>", (k, v)
    print("  OK  the user's profile path is redacted and secret-bearing keys are masked")


def test_redact_handles_both_slash_forms_and_case():
    home = str(Path.home())
    for form in (home, home.replace("\\", "/"), home.upper()):
        assert home.lower() not in diagnostics.redact("see " + form + "/x").lower(), form
    print("  OK  redact() covers both slash forms and either case")


def test_redact_handles_the_json_escaped_path_form():
    """settings.json is written by json.dumps, so a path in it carries DOUBLED backslashes.
    The single-backslash pattern does not match that at all, so the profile path used to travel
    with every bundle that carried a save_location."""
    home = str(Path.home())
    escaped = json.dumps(os.path.join(home, "Volksmond"))       # "C:\\Users\\name\\Volksmond"
    out = diagnostics.redact(escaped)
    assert home.lower() not in out.lower(), out
    assert home.replace("\\", "\\\\").lower() not in out.lower(), out
    assert "<user>" in out, out
    print("  OK  redact() covers the JSON-escaped path form too")


class _settings_at:
    """Point diagnostics' data_dir at a temp folder holding this settings.json."""

    def __init__(self, raw):
        self.raw = raw
        self.dir = _tmpdir("settings")

    def __enter__(self):
        (self.dir / "settings.json").write_text(self.raw, encoding="utf-8")
        self.prev = diagnostics.data_dir
        diagnostics.data_dir = lambda: self.dir
        return self.dir

    def __exit__(self, *exc):
        diagnostics.data_dir = self.prev
        return False


def test_bundle_omits_the_settings_the_user_typed():
    """The second privacy hole, and the reason settings.json is now an allow-list: the bundle
    used to export every setting whose NAME did not look secret. default_context and
    ai_instructions are things the user wrote (names, jargon, a custom prompt) while the UI
    promises "No transcripts, no notes"."""
    context = "Jan Vermeulen, Chenelle, mediese verslag, Rekeningnommer 4471"
    prompt = "Skryf altyd op oor Jan se toestand en noem sy prokureur"
    raw = json.dumps({
        "default_context": context,
        "ai_instructions": [{"id": "custom", "name": "Mine", "prompt": prompt}],
        "cloud_api_key": {"b64": "c2stbGl2ZS1TRUNSRVQ="},
        "licence_accepted": True,
        # the operational half, which must survive: this is what a support case reads
        "tier": "medium", "device": "cpu", "engine": "fluister", "mic_gate": False,
        "record_sessions": True, "transcription_language": "af",
    }, indent=2)
    base = _tmpdir("bundle-logs4")
    log = diagnostics.open_log(base)
    log.write("nothing sensitive here\n")
    log.close()
    dest = _tmpdir("bundle-out4")
    with _settings_at(raw):
        out = diagnostics.save_bundle(dest_dir=dest, base=base)
    with zipfile.ZipFile(out) as z:
        blob = b"".join(z.read(n) for n in z.namelist())
        settings = json.loads(z.read("settings.json").decode("utf-8") or "{}")
        system = z.read("system.txt").decode("utf-8")
    for secret in (context, prompt, "Jan Vermeulen", "4471", "c2stbGl2ZS1TRUNSRVQ="):
        assert secret.encode("utf-8") not in blob, secret
    assert settings["default_context"] == "<omitted>", settings
    assert settings["ai_instructions"] == "<omitted>", settings
    assert settings["cloud_api_key"] == "<redacted>", settings
    # the operational settings are still there, and still true
    assert settings["tier"] == "medium" and settings["device"] == "cpu", settings
    assert settings["engine"] == "fluister" and settings["mic_gate"] is False, settings
    assert settings["record_sessions"] is True, settings
    # and system.txt says what was left out, by name
    assert "omitted by policy" in system, system
    assert "default_context" in system and "ai_instructions" in system, system
    print("  OK  settings the user typed are omitted, and system.txt says which")


def test_settings_allow_list_covers_only_operational_keys():
    """The allow-list is derived from config.DEFAULTS, so a NEW setting is omitted until somebody
    classifies it. This pins both halves: nothing content-bearing is on the list, and the list
    names no key that is not a real setting."""
    from live_transcribe import config
    content_bearing = {"default_context", "ai_instructions"}
    assert not (content_bearing & diagnostics._SETTINGS_ALLOW), \
        "a key the user types must never be on the allow-list"
    stray = diagnostics._SETTINGS_ALLOW - set(config.DEFAULTS)
    assert not stray, f"the allow-list names settings that do not exist: {sorted(stray)}"
    for k in set(config.DEFAULTS) - diagnostics._SETTINGS_ALLOW:
        low = k.lower()
        assert k in content_bearing or any(h in low for h in diagnostics._SECRET_KEY_HINTS), \
            f"{k} is neither allow-listed nor content/secret: classify it in diagnostics.py"
    print("  OK  the allow-list classifies every key in config.DEFAULTS")


# --- the feedback email ----------------------------------------------------

def test_feedback_body_asks_for_the_diagnostics_file():
    js = APP_JS.read_text(encoding="utf-8")
    assert "No logs or transcripts are attached" not in js, \
        "the old 'no logs attached' line is still in the feedback body"
    assert "Geen logs of transkripsies is aangeheg nie" not in js, \
        "the old Afrikaans 'no logs attached' line is still in the feedback body"
    assert "Please attach this file: " in js and "Please attach the diagnostics file" in js
    # The machine line sits immediately after the version line in the body.
    body = js[js.index("function feedbackBody("):]
    body = body[:body.index("\nfunction ")]
    assert body.index('"Volksmond version "') < body.index("machine ?"), body
    assert body.index("machine ?") < body.index("plat +"), body
    assert 'subject = "Volksmond feedback (v"' in js, "the subject format changed"
    print("  OK  feedback body: machine line after the version line, asks for the zip")


def test_summary_line_is_one_short_line():
    line = diagnostics.summary_line()
    assert "\n" not in line and len(line) < 300, repr(line)
    for part in ("cores", "RAM", "GPU", "install"):
        assert part in line, (part, line)
    print("  OK  the email machine line is one line with CPU, cores, RAM, GPU, install")


if __name__ == "__main__":
    failures = 0
    for fn in (test_rotation_keeps_five_previous_launches,
               test_rotation_caps_file_size_mid_run,
               test_writes_are_flushed_for_a_hard_crash,
               test_writer_behaves_like_the_stream_it_replaces,
               test_header_has_the_fields_a_support_case_needs,
               test_header_never_carries_the_licence_token,
               test_install_kind_reads_the_evidence,
               test_bundle_holds_exactly_the_allowed_members,
               test_bundle_never_includes_a_transcript,
               test_bundle_redacts_the_profile_path_and_secret_settings,
               test_redact_handles_both_slash_forms_and_case,
               test_redact_handles_the_json_escaped_path_form,
               test_bundle_omits_the_settings_the_user_typed,
               test_settings_allow_list_covers_only_operational_keys,
               test_feedback_body_asks_for_the_diagnostics_file,
               test_summary_line_is_one_short_line):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll diagnostics tests passed.")
