"""Smoke + regression tests for the web API, focused on the 2026-05-23 changes.

The Volksmond UI rebuild moved local AI summaries from Pro to Free (the design's
"Pro principle": Pro is only for things that need an online connection; anything
on-device stays free). It also added /api/app-info and split the frontend into
/assets. These tests pin that behaviour so it cannot silently regress.

No audio and no model load: the summarise check uses a bogus filename, which
returns 404 (transcript not found) BEFORE any model would load. That is exactly
what proves the Pro paywall is gone (a gated endpoint would 403 first).

Run:  python tests/test_web_api.py   (from the project root; exit 0 = pass)
"""
import os
import sys

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException
from fastapi.testclient import TestClient

from live_transcribe import licensing
from live_transcribe.web import app as webapp
from live_transcribe.web.app import CSRF_TOKEN, app

# The real UI always sends the CSRF token (app.js reads it from the page) and is
# served on loopback; mirror both so the existing tests exercise the endpoints,
# not the Host/CSRF guards (TestClient's default Host "testserver" would 400).
client = TestClient(app, base_url="http://localhost")
client.headers.update({"X-Volksmond-CSRF": CSRF_TOKEN})


def test_app_info():
    r = client.get("/api/app-info")
    assert r.status_code == 200, r.status_code
    j = r.json()
    for k in ("name", "version", "platform", "save_dir"):
        assert k in j and j[k], f"app-info missing {k}: {j}"
    print("  OK  /api/app-info returns name/version/platform/save_dir")


def test_summaries_are_free():
    # Local summaries must not be Pro-gated any more.
    assert "ai_summary" not in licensing.PRO_FEATURES, "ai_summary still in PRO_FEATURES"
    assert licensing.PRO_FEATURES == frozenset({"calendar"}), \
        f"PRO_FEATURES changed unexpectedly: {set(licensing.PRO_FEATURES)}"
    feats = client.get("/api/features").json()
    assert "ai_summary" not in feats["catalogue"]["pro"], feats
    print("  OK  summaries are free (ai_summary absent from PRO_FEATURES and /api/features)")


def test_summarise_not_pro_gated():
    # A free user (no licence) hitting summarise with a bogus file must get 404
    # (transcript not found), NOT 403 (Pro). That proves the paywall is gone and
    # nothing loads a model along the way.
    r = client.post("/api/summarise", json={"file": "does-not-exist-xyz.md"})
    assert r.status_code != 403, "summarise is still Pro-gated (403)"
    assert r.status_code == 404, f"expected 404 for missing transcript, got {r.status_code}: {r.text}"
    assert "Pro" not in r.json().get("detail", ""), r.json()
    print("  OK  /api/summarise is not Pro-gated (bogus file -> 404, not 403)")


def test_summary_model_download_api():
    # One-click summary-model download: the catalogue lists the two pinned models,
    # a bad key is rejected, and the download endpoint is CSRF-protected. No real
    # download is triggered (a bad key with the token, and a good key without it,
    # are both refused before any bytes move).
    j = client.get("/api/summary-models").json()
    keys = [m["key"] for m in j["models"]]
    assert keys == ["gemma-4-e2b", "gemma-4-e4b", "gemma-4-12b"], keys
    assert all(m["approx_bytes"] > 0 for m in j["models"]), j
    r = client.post("/api/summary-model/download", json={"key": "nope"})
    assert r.status_code == 400, f"bad model key should 400, got {r.status_code}"
    bare = TestClient(app, base_url="http://localhost")
    r2 = bare.post("/api/summary-model/download", json={"key": "gemma-4-e2b"})
    assert r2.status_code == 403, f"download endpoint not CSRF-protected: {r2.status_code}"
    assert client.get("/api/summary-models").json()["progress"]["state"] in ("idle", "done"), \
        "a rejected request still started a download"
    print("  OK  summary-model download: catalogue listed, bad key 400, CSRF-protected, no stray download")


def test_voice_model_download_api():
    # Voice-model pre-download (so the first Begin is not a silent multi-GB wait):
    # the catalogue lists models with sizes, flags a recommended pick for this
    # machine, and exposes the tier->model map the UI needs. A bad model is
    # rejected and the download endpoint is CSRF-protected. No real download is
    # triggered (a bad model with the token, and a good one without it, are both
    # refused before any bytes move).
    j = client.get("/api/voice-models").json()
    assert j.get("recommended_model"), j
    models = j["models"]
    # The four reconciled quality models, shown identically here and on the meeting screen.
    assert [m["model"] for m in models] == ["small", "medium", "large-v3-turbo", "large-v3"], models
    assert all(m["approx_bytes"] > 0 and "size_on_disk" in m for m in models), j
    assert any(m["recommended"] for m in models), "no recommended voice model flagged"
    r = client.post("/api/voice-model/download", json={"model": "nope"})
    assert r.status_code == 400, f"bad voice model should 400, got {r.status_code}: {r.text}"
    bare = TestClient(app, base_url="http://localhost")
    r2 = bare.post("/api/voice-model/download", json={"model": "small"})
    assert r2.status_code == 403, f"voice download endpoint not CSRF-protected: {r2.status_code}"
    assert client.get("/api/voice-models").json()["progress"]["state"] in ("idle", "done"), \
        "a rejected request still started a voice download"
    print("  OK  voice-model download: catalogue + recommended + tier map, bad model 400, CSRF-protected, no stray download")


def test_cuda_api():
    # Optional NVIDIA CUDA (GPU) status + the download/remove endpoints. The endpoints
    # are CSRF-protected; the test only hits them WITHOUT the token (403), so it never
    # triggers a real multi-GB download regardless of whether this machine has a GPU.
    j = client.get("/api/cuda").json()
    for k in ("supported", "gpu_present", "installed", "ready", "approx_bytes", "progress"):
        assert k in j, (k, j)
    assert j["supported"] is True, j   # this suite runs on Windows, where CUDA is supported
    assert j["approx_bytes"] > 0, j
    bare = TestClient(app, base_url="http://localhost")
    assert bare.post("/api/cuda/download").status_code == 403, "cuda download not CSRF-protected"
    assert bare.post("/api/cuda/remove").status_code == 403, "cuda remove not CSRF-protected"
    print("  OK  cuda: status shape + download/remove CSRF-protected (no real download triggered)")


def test_quality_resolution():
    # An EXPLICIT quality is honoured as the model the user asked for, on CPU or GPU.
    # "auto" picks the best model for the language's family (Afrikaans -> turbo, English ->
    # large-v3). The CPU path is deterministic; the GPU path is exercised with cuda_ready stubbed.
    from live_transcribe.__main__ import resolve_tier
    from live_transcribe.transcribe import TIER_CONFIG
    from live_transcribe import cudadl as _cudadl
    # CPU path: explicit size honoured, never a GPU tier.
    assert TIER_CONFIG[resolve_tier("small", "cpu")]["model"] == "small"
    assert TIER_CONFIG[resolve_tier("medium", "cpu")]["model"] == "medium"
    assert TIER_CONFIG[resolve_tier("large-v3-turbo", "cpu")]["model"] == "large-v3-turbo"
    assert TIER_CONFIG[resolve_tier("large-v3", "cpu")]["model"] == "large-v3"
    assert resolve_tier("auto") in TIER_CONFIG
    assert resolve_tier("cpu-mid") in TIER_CONFIG          # legacy tier key passthrough
    assert "cpu-large" in TIER_CONFIG and TIER_CONFIG["cpu-large"]["device"] == "cpu"
    # GPU path (cuda_ready stubbed True): explicit picks are HONOURED, not overridden to large-v3.
    orig = _cudadl.cuda_ready
    _cudadl.cuda_ready = lambda: True
    try:
        assert TIER_CONFIG[resolve_tier("medium", "auto")]["model"] == "medium"
        assert TIER_CONFIG[resolve_tier("small", "auto")]["model"] == "small"
        assert TIER_CONFIG[resolve_tier("large-v3-turbo", "auto")]["model"] == "large-v3-turbo"
        # "auto" = best model for the language: Afrikaans -> turbo, English -> large-v3.
        assert TIER_CONFIG[resolve_tier("auto", "auto", "af")]["model"] == "large-v3-turbo"
        assert TIER_CONFIG[resolve_tier("auto", "auto", "en")]["model"] == "large-v3"
        # device="cpu" still forces the CPU even when a GPU is ready.
        assert TIER_CONFIG[resolve_tier("medium", "cpu")]["device"] == "cpu"
    finally:
        _cudadl.cuda_ready = orig
    print("  OK  quality resolution: explicit honoured (CPU+GPU), auto = best per language")


def test_family_resolution():
    # Language picks the model FAMILY: Afrikaans AND auto-detect -> Fluister; explicit English/other
    # -> stock Whisper. A manual engine override forces either family. The tier holds a stock SIZE;
    # the Engine pairs size + language + engine at load time.
    from live_transcribe import transcribe as T
    assert T.family_for_language("af") == "fluister"
    assert T.family_for_language("af-ZA") == "fluister"
    assert T.family_for_language("en") == "whisper"
    assert T.family_for_language("") == "fluister"      # auto-detect -> Fluister
    assert T.family_for_language(None) == "fluister"    # None == auto -> Fluister
    # resolve_model now returns (model_id, family) where family is "fluister" | "whisper" |
    # "swivuriso". Explicit English -> stock size; a size with no Fluister build stays stock even for
    # Afrikaans (base/tiny have no app-side Fluister entry).
    assert T.resolve_model("small", "en") == ("small", "whisper")
    assert T.resolve_model("base", "af") == ("base", "whisper")
    # Auto-detect ("") resolves to a Fluister build when one exists for that size.
    assert T.resolve_model("large-v3", "")[1] == "fluister"
    # Engine override forces the family regardless of language.
    assert T.resolve_model("small", "en", "fluister")[1] == "fluister"
    assert T.resolve_model("small", "af", "whisper") == ("small", "whisper")
    # Swivuriso: the seven South African languages route to it; it falls back to stock Whisper only
    # when no Swivuriso model is installed/hosted on this machine.
    assert T.family_for_language("zu") == "swivuriso"
    assert T.family_for_language("xh-ZA") == "swivuriso"
    assert T.family_for_language("sa") == "swivuriso"   # the "South African languages" group code the picker sends
    _, sv_fam = T.resolve_model("large-v3-turbo", "zu")
    assert sv_fam == ("swivuriso" if T.swivuriso_available() else "whisper"), sv_fam
    # Every tier now stores a stock size name, never a hardcoded Fluister path.
    sizes = {"tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"}
    for t, cfg in T.TIER_CONFIG.items():
        assert cfg["model"] in sizes, (t, cfg["model"])
    # /api/voice-models exposes Fluister-installed + the Swivuriso catalogue, so the UI can be honest.
    vm = client.get("/api/voice-models").json()
    assert isinstance(vm.get("fluister_available"), bool)
    assert isinstance(vm.get("swivuriso"), dict) and "present" in vm["swivuriso"], vm
    print("  OK  family resolution: af->Fluister, SA languages->Swivuriso, others->Whisper; voice-models exposes both")


def test_model_delete_api():
    # Removing models to free space: both delete endpoints reject a bad id (400) and
    # are CSRF-protected (403 without the token). We deliberately never issue a valid
    # delete, so no real model is removed by the test.
    assert client.post("/api/voice-model/delete", json={"model": "nope"}).status_code == 400, "bad voice model should 400"
    assert client.post("/api/summary-model/delete", json={"key": "nope"}).status_code == 400, "bad summary key should 400"
    bare = TestClient(app, base_url="http://localhost")
    assert bare.post("/api/voice-model/delete", json={"model": "small"}).status_code == 403, "voice delete not CSRF-protected"
    assert bare.post("/api/summary-model/delete", json={"key": "gemma-4-e2b"}).status_code == 403, "summary delete not CSRF-protected"
    # The catalogue now reports real on-disk size for present models (so the UI can
    # show what removing one frees); the field is always present.
    vm = client.get("/api/voice-models").json()
    assert all("size_on_disk" in m for m in vm["models"]), vm
    sm = client.get("/api/summary-models").json()
    assert all("size_on_disk" in m for m in sm["models"]), sm
    print("  OK  model delete: bad id -> 400, CSRF-protected (no real deletion); size_on_disk exposed")


def test_summary_language_validated():
    # The summary output language is constrained to af/en; junk is a 422 (not silently ignored).
    bad = client.post("/api/summarise", json={"file": "no-such.md", "language": "fr"})
    assert bad.status_code == 422, f"invalid language should 422, got {bad.status_code}"
    ok = client.post("/api/summarise", json={"file": "no-such.md", "language": "af"})
    assert ok.status_code == 404, f"valid language should pass validation (404 on missing file), got {ok.status_code}"
    print("  OK  summary language validated (af/en only; junk -> 422)")


def test_settings_never_leak_secret():
    j = client.get("/api/settings").json()
    assert "cloud_api_key" not in j, "secret cloud_api_key leaked in GET /api/settings"
    assert "has_cloud_api_key" in j, "presence flag missing from settings"
    print("  OK  GET /api/settings exposes the presence flag, never the secret")


def test_static_assets_served():
    assert client.get("/").status_code == 200, "index did not serve"
    assert client.get("/assets/app.js").status_code == 200, "app.js did not serve"
    assert client.get("/assets/styles.css").status_code == 200, "styles.css did not serve"
    print("  OK  index + /assets/app.js + /assets/styles.css all serve")


def test_csrf_blocks_unsafe_requests():
    # A page without the token (e.g. a random site firing a simple cross-origin
    # POST at localhost) must be rejected on any state-changing method.
    bare = TestClient(app, base_url="http://localhost")
    r = bare.post("/api/stop?what=all")
    assert r.status_code == 403, f"unsafe POST without CSRF token should be 403, got {r.status_code}"
    # GETs are safe and must still work without the token.
    assert bare.get("/api/app-info").status_code == 200, "GET should not require the CSRF token"
    # The token is handed to the page so the real UI can echo it.
    assert 'name="vm-csrf"' in client.get("/").text, "index does not embed the CSRF token"
    print("  OK  CSRF: unsafe POST blocked without token; GET open; token embedded in page")


def test_unique_transcript_filenames():
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    orig = webapp._sessions_dir
    webapp._sessions_dir = lambda: tmp
    try:
        p1 = webapp._build_output_path("Collision Test")
        p1.write_text("first meeting", encoding="utf-8")   # meeting A is on disk
        p2 = webapp._build_output_path("Collision Test")
        assert p2 != p1, "second transcript reused the first path (meetings would merge)"
        assert not p2.exists(), "second path already exists on disk"
    finally:
        webapp._sessions_dir = orig
    print("  OK  two same-topic sessions never share a transcript file")


def test_filename_allow_list():
    bad = ["../secret.md", "a/b.md", "a\\b.md", "CON.md", "CON.foo.md", "COM1.report.md",
           "report.txt", "stream.md:hidden", "", "..", "trans\x00.md"]
    for name in bad:
        try:
            webapp._validate_session_filename(name)
            assert False, f"accepted invalid filename: {name!r}"
        except HTTPException as e:
            assert e.status_code == 400, (name, e.status_code)
    webapp._validate_session_filename("2026-05-23-120000-weekly-standup.md")  # a normal name passes
    # The summarise endpoint rejects traversal with 400 (not 404/500).
    r = client.post("/api/summarise", json={"file": "../secret.md"})
    assert r.status_code == 400, f"summarise should 400 on a bad filename, got {r.status_code}"
    print("  OK  filename allow-list rejects traversal, ADS, reserved names, non-.md")


def test_license_pubkey_precedence():
    # A baked-in key must win, so a shipped build can't be pointed at another key
    # via SA_LIVE_LICENSE_PUBKEY to self-sign Pro.
    orig_baked = licensing._PUBLIC_KEY_HEX
    orig_env = os.environ.get("SA_LIVE_LICENSE_PUBKEY")
    try:
        licensing._PUBLIC_KEY_HEX = "baked"
        os.environ["SA_LIVE_LICENSE_PUBKEY"] = "attacker"
        assert licensing._pubkey_hex() == "baked", "env var overrode a baked-in key"
        licensing._PUBLIC_KEY_HEX = ""        # dev/test: no key shipped
        assert licensing._pubkey_hex() == "attacker", "env var ignored when nothing baked"
    finally:
        licensing._PUBLIC_KEY_HEX = orig_baked
        if orig_env is None:
            os.environ.pop("SA_LIVE_LICENSE_PUBKEY", None)
        else:
            os.environ["SA_LIVE_LICENSE_PUBKEY"] = orig_env
    print("  OK  licence pubkey: baked-in wins; env override only when none baked")


def test_host_rebinding_blocked():
    # DNS-rebinding defence: a non-loopback Host is rejected before anything is
    # served, including the page that carries the CSRF token.
    evil = TestClient(app, base_url="http://attacker.example:8765")
    assert evil.get("/api/app-info").status_code == 400, "non-loopback Host not rejected"
    assert evil.get("/").status_code == 400, "token page served to a non-loopback Host"
    # Loopback Host is served normally (the shared client uses http://localhost).
    assert client.get("/api/app-info").status_code == 200
    print("  OK  non-loopback Host rejected (DNS-rebinding defence); loopback served")


def test_summary_device_and_capability():
    # /api/models reports whether summaries can use the GPU on this build, plus the current
    # summary device. summary_gpu_capable is True only when an NVIDIA GPU is present AND this
    # build's llama.cpp can offload (the CPU wheel cannot), so it is a plain bool here.
    m = client.get("/api/models").json()
    for k in ("summary_installed", "summary_gpu_capable", "summary_device"):
        assert k in m, (k, m)
    assert isinstance(m["summary_gpu_capable"], bool), m
    # summary_device round-trips through settings; save and restore the real value.
    from live_transcribe import config
    orig = config.load().get("summary_device")
    try:
        assert client.post("/api/settings", json={"summary_device": "cpu"}).json()["summary_device"] == "cpu"
        assert client.post("/api/settings", json={"summary_device": "auto"}).json()["summary_device"] == "auto"
    finally:
        config.update({"summary_device": orig or "auto"})
    print("  OK  /api/models reports summary GPU capability + device; summary_device round-trips")


def test_live_notes_width_roundtrip():
    # The live-screen notes column width persists to disk (settings.json) as well as
    # localStorage, because the WebView can wipe localStorage between launches.
    from live_transcribe import config
    orig = config.load().get("live_notes_width")
    try:
        assert client.post("/api/settings", json={"live_notes_width": 340}).json()["live_notes_width"] == 340
        assert client.get("/api/settings").json()["live_notes_width"] == 340
        assert client.post("/api/settings", json={"live_notes_width": 0}).json()["live_notes_width"] == 0
    finally:
        config.update({"live_notes_width": orig or 0})
    print("  OK  live_notes_width round-trips through /api/settings")


def test_default_language_roundtrip():
    # Settings must accept EVERY pre-meeting language mode as the default: the classics
    # ("af"/"en"), the Swivuriso group ("sa"), a specific South African language, a world
    # language, and auto-detect (""). Save and restore the real value (repo convention).
    orig = client.get("/api/settings").json()["transcription_language"]
    try:
        for v in ("af", "en", "sa", "zu", "de", ""):
            assert client.post("/api/settings", json={"transcription_language": v}).json()["transcription_language"] == v, v
            assert client.get("/api/settings").json()["transcription_language"] == v, v
    finally:
        client.post("/api/settings", json={"transcription_language": orig})
    print("  OK  default language round-trips for every mode (af/en/sa/zu/de/auto)")


def test_language_mode_tokens():
    # Each pre-meeting mode maps to the right FAMILY, and to the decode token faster-whisper
    # actually receives. A specific world language forces its token (no per-chunk language
    # flapping); the South African codes never leak into a family that has no token for them.
    from live_transcribe import transcribe as T
    assert T.family_for_language("de") == "whisper"
    assert T.family_for_language("fr-FR") == "whisper"
    assert T.family_for_language("tn") == "swivuriso"
    assert T.decode_language("whisper", "de") == "de"
    assert T.decode_language("whisper", "en") == "en"
    assert T.decode_language("fluister", "af") == "af"
    assert T.decode_language("fluister", "") is None       # auto-detect stays auto
    assert T.decode_language("fluister", "auto") is None
    assert T.decode_language("swivuriso", "zu") is None    # Swivuriso always decodes on auto
    assert T.decode_language("swivuriso", "sa") is None
    assert T.decode_language("whisper", "zu") is None      # SA code on stock Whisper: no token exists
    assert T.decode_language("whisper", "sa") is None      # "sa" must never decode as Sanskrit
    print("  OK  language mode -> family + decode token (world forced, SA codes auto, no Sanskrit trap)")


def test_settings_migration_old_default_language():
    # A settings.json written by an older build (default language "af" or "en", no knowledge
    # of the new modes) must load exactly as before: same value out, every other key at its
    # default. Sandboxed: config is pointed at a temp folder, never the user's real file.
    import json as _json
    import tempfile
    from pathlib import Path
    from live_transcribe import config as C
    orig_dir, orig_path = C._DIR, C._SETTINGS_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            C._DIR = Path(td)
            C._SETTINGS_PATH = C._DIR / "settings.json"
            for old in ("af", "en"):
                C._SETTINGS_PATH.write_text(_json.dumps({"transcription_language": old,
                                                         "transcribe_languages": ["af", "en"]}), encoding="utf-8")
                s = C.load()
                assert s["transcription_language"] == old, s
                assert s["tier"] == "auto" and s["engine"] == "auto", s
            # The new mode values persist and survive a reload unchanged.
            for new in ("sa", "zu", "de", ""):
                C.update({"transcription_language": new})
                assert C.load()["transcription_language"] == new, new
    finally:
        C._DIR, C._SETTINGS_PATH = orig_dir, orig_path
    print("  OK  old settings files ('af'/'en') load unchanged; new mode values persist (sandboxed)")


def test_fits_on_gpu_logic():
    # The GPU fit check: full offload only when the model file plus a working-memory
    # headroom fits in VRAM. A tiny real file fits a big card; nothing fits an unknown
    # or too-small card. (Matches the rule that a 12B cannot be offloaded to a 4 GB card.)
    from live_transcribe import summarise as sm
    small = os.path.abspath(__file__)            # a real, tiny file
    assert sm.fits_on_gpu(small, 24000) is True
    assert sm.fits_on_gpu(small, 1000) is False  # below the 2 GB headroom
    assert sm.fits_on_gpu(small, None) is False
    assert sm.fits_on_gpu("does-not-exist.gguf", 24000) is False  # missing file -> not on GPU
    print("  OK  fits_on_gpu: fits a big card, refuses a small/unknown card and a missing file")


def test_levels_and_switch_device():
    # The live meter endpoint is always safe to poll; idle -> running False + zeroed levels.
    j = client.get("/api/levels").json()
    assert j["running"] is False and j["mic"]["peak"] == 0.0 and j["sys"]["rms"] == 0.0, j
    # Switching devices is only valid during a live session: idle -> 409 (not a crash).
    assert client.post("/api/switch-device", json={"which": "mic", "device": "1"}).status_code == 409
    # which is constrained to mic/loopback; junk -> 422.
    assert client.post("/api/switch-device", json={"which": "nope"}).status_code == 422
    # State-changing, so CSRF-protected.
    bare = TestClient(app, base_url="http://localhost")
    assert bare.post("/api/switch-device", json={"which": "mic"}).status_code == 403
    # The capture stores a passed-in t0, so a live device switch can keep the timeline.
    from live_transcribe import capture as _cap
    assert _cap.AudioCapture(t0=123.0)._t0_init == 123.0, "t0 not stored for timeline continuity"
    print("  OK  /api/levels idle-safe; /api/switch-device session-gated + validated + CSRF; capture keeps t0")


def test_switch_device_preserves_recording_clock():
    # A mid-recording device switch rebuilds the capture but must NOT restart the session
    # clock: it threads the original t0 into the new capture, so post-switch chunk t_starts
    # keep climbing on the session clock. That is exactly what the recorder's time-aligned
    # fold relies on: the ~1s switch gap becomes a zero-filled gap at its true wall-clock
    # position, not a "behind" chunk that gets appended as-is and shifts SYS forever.
    #
    # test_levels_and_switch_device only proves the CONSTRUCTOR stores a passed t0; this
    # drives the real handler end-to-end and proves it PASSES t0 through the rebuild. If a
    # refactor ever drops `t0=old_cap._t0` from switch_device, the stereo recording after a
    # device switch silently misaligns, and this test fails.
    import time as _time
    built = []   # kwargs each rebuilt AudioCapture is constructed with

    class _FakeCapture:
        def __init__(self, mic_device=None, loopback_device=None, chunk_seconds=15,
                     on_chunk=None, t0=None, aec=False, agc=True, record_raw_mic=False):
            self._t0_init = t0
            self._t0 = t0 if t0 is not None else _time.monotonic()
            self.aec = aec
            self.agc = agc   # switch_device carries agc across the rebuild (agc=old_cap.agc)
            self.record_raw_mic = record_raw_mic
            built.append({"t0": t0, "aec": aec, "agc": agc, "record_raw_mic": record_raw_mic})

        def start(self):
            self._t0 = self._t0_init if self._t0_init is not None else _time.monotonic()

        def stop(self):
            pass

        def attach_sys_ring(self, ring):
            pass

        def has_raw_mic(self):
            return self.record_raw_mic

    st = webapp.STATE
    session_t0 = _time.monotonic() - 12.0    # session started ~12s ago
    old_cap = _FakeCapture(mic_device="0", loopback_device="1", t0=session_t0,
                           aec=True, agc=True, record_raw_mic=True)
    old_cap.start()
    saved_factory = webapp.capture.AudioCapture
    saved = (st.running, st.stopping, st.source_kind, st.capture, st.engine,
             st.mic_device, st.loopback_device, st.chunk_seconds, st.record_raw_mic)
    try:
        webapp.capture.AudioCapture = _FakeCapture
        st.running = True; st.stopping = False; st.source_kind = "live"
        st.capture = old_cap; st.engine = None
        st.mic_device = "0"; st.loopback_device = "1"; st.chunk_seconds = 15
        built.clear()
        r = client.post("/api/switch-device", json={"which": "mic", "device": "2"})
        assert r.status_code == 200, (r.status_code, r.text)
        assert built, "switch_device did not rebuild the capture"
        assert built[-1]["t0"] == session_t0, \
            f"switch_device must thread t0 through the rebuild, got {built[-1]['t0']} != {session_t0}"
        # Settings carried across so the switch does not silently disable AEC / AGC / raw-mic recording.
        assert built[-1]["aec"] is True and built[-1]["agc"] is True \
            and built[-1]["record_raw_mic"] is True, built[-1]
        # The new capture is on the same clock, so a chunk produced now reads ~12s, not ~0s.
        assert (_time.monotonic() - st.capture._t0) > 5.0, "post-switch clock restarted near zero"
    finally:
        webapp.capture.AudioCapture = saved_factory
        (st.running, st.stopping, st.source_kind, st.capture, st.engine,
         st.mic_device, st.loopback_device, st.chunk_seconds, st.record_raw_mic) = saved
    print("  OK  /api/switch-device threads t0 through the rebuild: recording clock survives a device switch")


def test_recording_channel_bundling():
    # Uploading ONE channel of a saved recording must pull in its MIC+SYS pair (so a single-file
    # upload still transcribes both sides and can cancel echo), and drop the summed -MIXED. A normal
    # media file with no sibling channels is left exactly as-is.
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    for suff in ("MIC", "SYS", "MIXED"):
        (d / f"call-{suff}.wav").write_bytes(b"\0")
    (d / "external.mp3").write_bytes(b"\0")
    exp = webapp._expand_recording_channels
    assert sorted(pathlib.Path(p).name for p in exp([str(d / "call-MIC.wav")])) == ["call-MIC.wav", "call-SYS.wav"]
    # Picking the MIXED also resolves to the two real channels, never the summed mix.
    assert sorted(pathlib.Path(p).name for p in exp([str(d / "call-MIXED.wav")])) == ["call-MIC.wav", "call-SYS.wav"]
    # A normal external file (no -MIC/-SYS sibling) is untouched.
    assert exp([str(d / "external.mp3")]) == [str(d / "external.mp3")]
    print("  OK  upload of one recording channel bundles MIC+SYS (drops MIXED); external file untouched")


def test_recorder_stereo_fold():
    # The recorder streams per-source mono during the session, then on close folds MIC+SYS into
    # ONE stereo <stem>.wav (left = MIC / you, right = SYS / everyone else) and removes the
    # per-source files, so the user is left with a single clean playable + re-transcribable file.
    import tempfile, pathlib, wave
    import numpy as np
    from live_transcribe import sinks
    d = pathlib.Path(tempfile.mkdtemp())
    stem = d / "2026-01-01-0000-call"
    rec = sinks.AudioRecorder(stem)
    rec.on_chunk("MIC", np.full(16000, 0.5, dtype=np.float32), 0.0)    # 1s, left, positive
    rec.on_chunk("SYS", np.full(16000, -0.25, dtype=np.float32), 0.0)  # 1s, right, negative
    rec.close()
    out = stem.with_name(stem.name + ".wav")
    assert out.is_file(), "single stereo <stem>.wav not written"
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 2 and w.getframerate() == 16000, "recording is not 16k stereo"
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, 2)
    assert data[100, 0] > 10000 and data[100, 1] < -4000, ("channels swapped/wrong", data[100].tolist())
    assert not stem.with_name(stem.name + "-MIC.wav").is_file(), "MIC stem not removed"
    assert not stem.with_name(stem.name + "-SYS.wav").is_file(), "SYS stem not removed"
    print("  OK  recorder folds MIC+SYS into one stereo <stem>.wav (left=MIC, right=SYS); stems removed")


def test_feed_raw_mic_routing():
    # Live AEC + recording: the recorder must get the RAW mic (MIC_RAW, saved as the -MIC channel)
    # while the engine transcribes the cleaned MIC, so recordings stay raw. SYS goes to both. This
    # pins the _feed routing; the capture-side MIC_RAW production needs a real-audio test.
    class _Sink:
        def __init__(self):
            self.calls = []
        def on_chunk(self, source, audio, t):
            self.calls.append((source, audio))
    rec, eng = _Sink(), _Sink()
    st = webapp.STATE
    saved = (st.recording, st.transcribing, st.recorder, st.engine, st.record_raw_mic)
    try:
        st.recording = True; st.transcribing = True; st.recorder = rec; st.engine = eng
        st.record_raw_mic = True
        webapp._feed("MIC", "cleaned", 1.0)     # cleaned mic -> engine only
        webapp._feed("MIC_RAW", "raw", 1.0)     # raw mic -> recorder as -MIC only
        webapp._feed("SYS", "sys", 1.0)         # system -> both
        assert eng.calls == [("MIC", "cleaned"), ("SYS", "sys")], eng.calls
        assert rec.calls == [("MIC", "raw"), ("SYS", "sys")], rec.calls
        # AEC off (no raw side channel): MIC goes to both, unchanged behaviour.
        rec.calls.clear(); eng.calls.clear(); st.record_raw_mic = False
        webapp._feed("MIC", "raw", 2.0)
        assert rec.calls == [("MIC", "raw")] and eng.calls == [("MIC", "raw")], (rec.calls, eng.calls)
    finally:
        st.recording, st.transcribing, st.recorder, st.engine, st.record_raw_mic = saved
    print("  OK  _feed: live-AEC recording saves raw MIC_RAW as -MIC, engine gets the cleaned MIC")


def test_reconfigure_session_gated():
    # Changing language/model mid-session is only valid during a live transcription.
    # An empty patch (nothing to change) is rejected up front with 400, before the session check.
    assert client.post("/api/reconfigure", json={}).status_code == 400
    # A real change while idle -> 409 (no live session), not a crash.
    assert client.post("/api/reconfigure", json={"language": "en"}).status_code == 409
    assert client.post("/api/reconfigure", json={"tier": "small"}).status_code == 409
    assert client.post("/api/reconfigure", json={"engine": "fluister"}).status_code == 409
    # State-changing, so CSRF-protected.
    bare = TestClient(app, base_url="http://localhost")
    assert bare.post("/api/reconfigure", json={"language": "en"}).status_code == 403
    print("  OK  /api/reconfigure: empty patch 400, idle 409, CSRF-protected")


def test_reconfigure_keeps_user_language():
    # Review wave 1, F1: the engine stores the DECODE token, which is None for every South
    # African language (Swivuriso decodes on auto-detect), so it must never stand in for the
    # user's language. A mid-meeting Quality-only change on an isiZulu session must keep the
    # session language "zu" and the family Swivuriso, not read None as auto-detect, re-route
    # the family to Fluister and rewrite the language to "auto". Simulated session with the
    # loader stubbed: no audio and no model weights are touched.
    from live_transcribe import transcribe as T

    class _FakeEngine:
        _is_cpu = True
        _compute_type = "int8"
        _cpu_threads = 4
        size = "medium"
        language = None          # the decode token a zu session really stores
        engine = "auto"
        family = "swivuriso"

        def __init__(self):
            self.changes = []

        def request_change(self, **kw):
            self.changes.append(kw)

    st = webapp.STATE
    fake = _FakeEngine()
    saved = (st.running, st.transcribing, st.stopping, st.source_kind, st.engine,
             st.language, st.tier, st.model, st.family)
    orig_load, orig_hosted = T.load_model, T.SWIVURISO_HOSTED
    try:
        T.load_model = lambda *a, **k: object()   # never load real weights
        T.SWIVURISO_HOSTED = True                 # Swivuriso resolvable on any machine
        st.running, st.transcribing, st.stopping = True, True, False
        st.source_kind, st.engine = "live", fake
        st.language, st.tier, st.model, st.family = "zu", "cpu-mid", T.swivuriso_model(), "swivuriso"
        r = client.post("/api/reconfigure", json={"tier": "small"})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["language"] == "zu", j            # NOT rewritten to "auto"
        assert j["family"] == "swivuriso", j       # NOT re-routed to fluister
        assert st.language == "zu" and st.family == "swivuriso", (st.language, st.family)
        assert client.get("/api/status").json()["language"] == "zu"
        # The engine still receives the decode token (None: Swivuriso decodes on auto-detect).
        assert fake.changes and fake.changes[-1]["language"] is None, fake.changes
    finally:
        T.load_model, T.SWIVURISO_HOSTED = orig_load, orig_hosted
        (st.running, st.transcribing, st.stopping, st.source_kind, st.engine,
         st.language, st.tier, st.model, st.family) = saved
    print("  OK  reconfigure: tier-only change on a zu session keeps language zu + family swivuriso (decode token stays auto)")


def test_aec_live_reports_persistence():
    # Review wave 1, F7: /api/aec-live must keep working when the settings file cannot be
    # written (the live toggle already took effect on the engine), but must report it via
    # persisted: false so the UI can warn instead of the choice silently reverting next
    # meeting. Simulated live session; config.update stubbed, the real settings never touched.
    from live_transcribe import config as C

    class _FakeCapture:
        def set_aec(self, on):
            return True

        def aec_state(self):
            return (True, True)

    st = webapp.STATE
    saved = (st.running, st.stopping, st.source_kind, st.capture)
    orig_update = C.update
    try:
        st.running, st.stopping, st.source_kind, st.capture = True, False, "live", _FakeCapture()

        def _boom(d):
            raise OSError("disk full")
        C.update = _boom
        r = client.post("/api/aec-live", json={"enabled": True})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["aec_live_active"] is True and j["persisted"] is False, j
        # When persistence works the flag is True, and only aec_live is written.
        wrote = {}
        C.update = lambda d: wrote.update(d)
        j2 = client.post("/api/aec-live", json={"enabled": False}).json()
        assert j2["persisted"] is True and wrote == {"aec_live": False}, (j2, wrote)
    finally:
        C.update = orig_update
        st.running, st.stopping, st.source_kind, st.capture = saved
    print("  OK  /api/aec-live: toggle survives a failed settings write; persisted reported honestly")


def test_warm_up():
    # Warm-up status is always readable, and shaped for the UI.
    st = client.get("/api/warm-up").json()
    assert "state" in st and "tier" in st, st
    # POST is state-changing -> CSRF-protected (a bare client is refused before any warm-up runs).
    bare = TestClient(app, base_url="http://localhost")
    assert bare.post("/api/warm-up", json={}).status_code == 403
    # With the token it returns a state. Stub the loader so the test never builds or downloads
    # a model; we are checking the endpoint contract, not loading weights.
    import live_transcribe.transcribe as T
    orig = T.warm_up_async
    T.warm_up_async = lambda tier, language=None, engine="auto": {"state": "warming", "tier": tier}
    try:
        r = client.post("/api/warm-up", json={"tier": "small", "device": "cpu"})
        assert r.status_code == 200 and r.json().get("state") in ("warming", "ready", "busy", "idle"), r.text
    finally:
        T.warm_up_async = orig
    print("  OK  /api/warm-up: status readable, CSRF-protected, trigger returns a state (loader stubbed)")


def test_summarise_accepts_instruction():
    # The summary-style feature sends a free-text instruction. The endpoint must accept it
    # (a bogus file still 404s, proving the field validates rather than 422-ing), so custom
    # and template summaries are not rejected at the schema.
    r = client.post("/api/summarise", json={"file": "no-such.md",
                                            "instruction": "List only the action items.", "language": "en"})
    assert r.status_code == 404, f"instruction should validate; expected 404 on missing file, got {r.status_code}: {r.text}"
    print("  OK  /api/summarise accepts a custom instruction (bogus file -> 404, not 422)")


def test_model_update_status_logic():
    # Pure version logic (no network): _vtuple compares dotted versions numerically, and
    # model_update_status flags an update only for a PRESENT model whose manifest version beats the
    # installed (recorded, else baseline) one. Stub _present + the install record so nothing touches
    # HuggingFace or settings.
    from live_transcribe import voicedl as V
    from live_transcribe.transcribe import FLUISTER_REPOS
    assert V._vtuple("2.0.0") > V._vtuple("1.9.9")
    assert V._vtuple("v1.2") == (1, 2)
    repo = FLUISTER_REPOS["large-v3"]
    orig_present, orig_inst = V._present, V._installed_versions
    try:
        V._present = lambda m: m == repo          # only this Fluister model is "installed"
        V._installed_versions = lambda: {}         # nothing recorded -> baseline floor
        man = {"models": [{"repo": repo, "version": "2.0.0", "revision": "abc", "approx_bytes": 123}]}
        ups = V.model_update_status(man)
        assert len(ups) == 1 and ups[0]["repo"] == repo, ups
        assert ups[0]["installed"] == V._FLUISTER_BASELINE and ups[0]["latest"] == "2.0.0", ups
        assert ups[0]["update_available"] is True, ups
        # Same version as the baseline -> not an update.
        same = {"models": [{"repo": repo, "version": V._FLUISTER_BASELINE}]}
        assert V.model_update_status(same)[0]["update_available"] is False
        # A model that is not installed is never reported (we don't nag about models you lack).
        V._present = lambda m: False
        assert V.model_update_status(man) == []
    finally:
        V._present, V._installed_versions = orig_present, orig_inst
    print("  OK  model_update_status: present+versioned only; update when manifest > installed baseline")


def test_model_update_endpoints():
    # /api/voice-models now carries the local Fluister install catalogue (no network), and the manual
    # update endpoints mirror /api/check-updates: CSRF-protected, session-gated, and the network fetch
    # is stubbed so the test never reaches HuggingFace or our site.
    from live_transcribe import voicedl as V
    from live_transcribe.transcribe import FLUISTER_REPOS
    j = client.get("/api/voice-models").json()
    assert isinstance(j.get("fluister"), list) and j["fluister"], j
    for m in j["fluister"]:
        for k in ("size", "repo", "present", "installed_version", "approx_bytes", "size_on_disk"):
            assert k in m, (k, m)
    repo = FLUISTER_REPOS["small"]
    orig_fetch, orig_present, orig_inst = V.fetch_manifest, V._present, V._installed_versions
    try:
        V.fetch_manifest = lambda timeout=8: {"models": [{"repo": repo, "version": "9.9.9", "revision": "main"}]}
        V._present = lambda m: m == repo
        V._installed_versions = lambda: {}
        d = client.post("/api/model-updates").json()
        assert d["checked"] is True and d["any_update"] is True, d
        assert any(u["repo"] == repo and u["update_available"] for u in d["updates"]), d
    finally:
        V.fetch_manifest, V._present, V._installed_versions = orig_fetch, orig_present, orig_inst
    # CSRF: both update endpoints reject a tokenless request before doing anything.
    bare = TestClient(app, base_url="http://localhost")
    assert bare.post("/api/model-updates").status_code == 403, "model-updates not CSRF-protected"
    assert bare.post("/api/voice-model/update", json={"size": "small"}).status_code == 403, "voice-model update not CSRF-protected"
    # Unknown size -> 400 (rejected before any network). A running session -> 409.
    assert client.post("/api/voice-model/update", json={"size": "nope"}).status_code == 400
    saved = webapp.STATE.engine
    try:
        webapp.STATE.engine = object()
        assert client.post("/api/voice-model/update", json={"size": "small"}).status_code == 409
    finally:
        webapp.STATE.engine = saved
    print("  OK  model updates: voice-models carries fluister catalogue; /api/model-updates + update CSRF + session-gated; bad size 400")


def _sandbox_settings():
    """Context manager pointing config at a throwaway settings.json, so the session-count
    tests below can read and write it without touching the real one."""
    import contextlib
    import tempfile
    from pathlib import Path
    from live_transcribe import config as C

    @contextlib.contextmanager
    def _cm():
        orig_dir, orig_path = C._DIR, C._SETTINGS_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                C._DIR = Path(td)
                C._SETTINGS_PATH = C._DIR / "settings.json"
                yield C
        finally:
            C._DIR, C._SETTINGS_PATH = orig_dir, orig_path
    return _cm()


def _wait_idle(st, timeout=10.0):
    """Wait for the background drain thread to finish the session."""
    import time as _time
    deadline = _time.time() + timeout
    while st.running and _time.time() < deadline:
        _time.sleep(0.02)
    return not st.running


def test_session_count_bumped_on_full_stop():
    # WP-6: a normal "Stop and save" counts exactly one completed session. This is the path
    # that already worked; it is pinned because the once-per-session guard added for the other
    # paths must not break it. Sandboxed settings: the real settings.json is never touched.
    st = webapp.STATE
    saved = (st.running, st.stopping, st.source_kind, st.transcribing, st.recording,
             st.session_counted, st.sink_error)
    try:
        with _sandbox_settings() as C:
            st.running, st.stopping, st.source_kind = True, False, "live"
            st.transcribing = st.recording = False
            st.session_counted = False
            r = client.post("/api/stop?what=all")
            assert r.status_code == 200, r.text
            assert C.load()["session_count"] == 1, C.load()
            assert _wait_idle(st), "stop drain never finished"
            # Stopping again finds nothing running (409) and cannot count a second time.
            assert client.post("/api/stop?what=all").status_code == 409
            assert C.load()["session_count"] == 1, C.load()
    finally:
        (st.running, st.stopping, st.source_kind, st.transcribing, st.recording,
         st.session_counted, st.sink_error) = saved
    print("  OK  /api/stop?what=all counts exactly one completed session")


def test_session_count_bumped_on_transcription_branch_finalise():
    # WP-6, the bug: "stop transcription" (recording continues) and then "stop recording"
    # finalises the session inside _drain_transcription's `cap_to_stop is not None` branch,
    # which never called _bump_session_count. That is how session_count sat at 1 for 50+
    # meetings. No audio and no model: a fake engine gates the drain so the two stops
    # interleave exactly as they do in the app.
    import threading as _th
    gate = _th.Event()
    stopped = {"capture": False}

    class _GatedEngine:
        def pending(self):
            return 0

        def stop(self, drain=False):
            gate.wait(10)      # hold the drain open until the test stops the recorder

    class _FakeCapture:
        def stop(self):
            stopped["capture"] = True

    st = webapp.STATE
    saved = (st.running, st.stopping, st.source_kind, st.transcribing, st.recording,
             st.engine, st.capture, st.md_sink, st.recorder, st.session_counted, st.sink_error)
    try:
        with _sandbox_settings() as C:
            st.running, st.stopping, st.source_kind = True, False, "live"
            st.transcribing, st.recording = True, True
            st.engine, st.capture = _GatedEngine(), _FakeCapture()
            st.md_sink = st.recorder = None
            st.session_counted = False

            r1 = client.post("/api/stop?what=transcription")
            assert r1.status_code == 200 and r1.json()["stopped"] == "transcription", r1.text
            r2 = client.post("/api/stop?what=recording")          # user stops recording mid-drain
            assert r2.status_code == 200, r2.text
            assert C.load()["session_count"] == 0, "counted before the session actually finalised"
            gate.set()                                            # let the drain complete
            assert _wait_idle(st), "transcription drain never finalised the session"
            assert stopped["capture"], "the drain did not stop the capture"
            assert C.load()["session_count"] == 1, \
                f"partial-stop finalise did not count the session: {C.load()}"
    finally:
        gate.set()
        (st.running, st.stopping, st.source_kind, st.transcribing, st.recording,
         st.engine, st.capture, st.md_sink, st.recorder, st.session_counted, st.sink_error) = saved
    print("  OK  the stop-transcription-then-recording finalise counts the session (WP-6 bug)")


def test_session_count_never_double_bumps():
    # WP-6: the finalisation paths can overlap (a window close while a UI stop is in flight),
    # so the count is guarded by an explicit session-scoped flag, not by call-site discipline.
    import threading as _th
    st = webapp.STATE
    saved = st.session_counted
    try:
        with _sandbox_settings() as C:
            st.session_counted = False
            webapp._bump_session_count()      # e.g. the what="all" stop
            webapp._bump_session_count()      # e.g. the drain finalising the same session
            assert C.load()["session_count"] == 1, C.load()
            # Concurrent finalisers still count once.
            st.session_counted = False
            threads = [_th.Thread(target=webapp._bump_session_count) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
            assert C.load()["session_count"] == 2, f"double-counted under a race: {C.load()}"
            # A new session clears the flag, so the NEXT session still counts.
            st.session_counted = False
            webapp._bump_session_count()
            assert C.load()["session_count"] == 3, C.load()
    finally:
        st.session_counted = saved
    # reset() (end of session) clears the flag, which is what re-arms the counter.
    fresh = webapp._State()
    fresh.session_counted = True
    fresh.reset()
    assert fresh.session_counted is False, "STATE.reset() must clear session_counted"
    print("  OK  session count is bumped once per session (overlapping/concurrent finalisers, reset re-arms)")


def test_session_count_failure_is_logged_not_raised():
    # WP-6: the bump used to swallow every failure with `except Exception: pass`, so a
    # settings.json that could not be written left the counter silently stuck. It must still
    # never raise (finalisation must not break) but it must say so in the log.
    import contextlib
    import io
    from live_transcribe import config as C
    st = webapp.STATE
    saved, orig_update = st.session_counted, C.update
    try:
        def _boom(d):
            raise OSError("disk full")
        C.update = _boom
        st.session_counted = False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            webapp._bump_session_count()      # must not raise
        out = buf.getvalue()
        assert "disk full" in out, f"bump failure was swallowed silently: {out!r}"
    finally:
        C.update = orig_update
        st.session_counted = saved
    print("  OK  a failed session count logs the reason and never raises")


if __name__ == "__main__":
    failures = 0
    for fn in (test_app_info,
               test_summaries_are_free,
               test_summarise_not_pro_gated,
               test_summary_model_download_api,
               test_voice_model_download_api,
               test_cuda_api,
               test_quality_resolution,
               test_family_resolution,
               test_model_delete_api,
               test_summary_language_validated,
               test_settings_never_leak_secret,
               test_static_assets_served,
               test_csrf_blocks_unsafe_requests,
               test_host_rebinding_blocked,
               test_unique_transcript_filenames,
               test_filename_allow_list,
               test_license_pubkey_precedence,
               test_summary_device_and_capability,
               test_live_notes_width_roundtrip,
               test_default_language_roundtrip,
               test_language_mode_tokens,
               test_settings_migration_old_default_language,
               test_fits_on_gpu_logic,
               test_levels_and_switch_device,
               test_switch_device_preserves_recording_clock,
               test_recording_channel_bundling,
               test_recorder_stereo_fold,
               test_feed_raw_mic_routing,
               test_reconfigure_session_gated,
               test_reconfigure_keeps_user_language,
               test_aec_live_reports_persistence,
               test_warm_up,
               test_summarise_accepts_instruction,
               test_model_update_status_logic,
               test_model_update_endpoints,
               test_session_count_bumped_on_full_stop,
               test_session_count_bumped_on_transcription_branch_finalise,
               test_session_count_never_double_bumps,
               test_session_count_failure_is_logged_not_raised):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll web-API tests passed.")
