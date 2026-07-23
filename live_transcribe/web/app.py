"""FastAPI web server, controls the transcription engine from a browser.

Singleton-state design: only one session at a time (live or file). The server
holds the engine, audio capture, recorder, and sinks; HTTP endpoints start/stop
the session and stream segments to the browser via Server-Sent Events.

A session can transcribe live, record live (off by default, POPIA), do both, or
transcribe an existing file. Transcripts and recordings save to the user's chosen
save_location (validated; falls back to the project sessions/ folder).
"""
import asyncio
import json
import os
import platform
import queue
import re
import secrets
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import audioboost, buildflags, capture, config, licensing, paths, sinks, transcribe
from ..__main__ import default_chunk_seconds, pick_tier, resolve_tier

app = FastAPI(title="SA-Live-Transcribe")
STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Serve styles.css / app.js (and any future assets) from the static folder.
# Localhost-only server; these are the app's own files, no user data.
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

# Per-process CSRF token. The server listens on localhost, so any web page the
# user happens to visit can fire a *simple* cross-origin POST at it (e.g.
# /api/stop?what=all, /api/open-folder, /api/pick) and interrupt a meeting or
# spawn native dialogs. We mint a fresh token each launch, embed it in the served
# page, and require it on every state-changing request; a third-party page can
# neither read it (same-origin policy) nor guess it.
CSRF_TOKEN = secrets.token_urlsafe(32)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# We only ever serve ourselves on loopback. Any other Host means a remote page
# rebound its name to 127.0.0.1 (DNS rebinding) so the browser treats it as
# same-origin and can read transcripts. Reject it before serving anything,
# including the CSRF token, which a same-origin rebinding page would otherwise read.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@app.middleware("http")
async def _csrf_and_security_headers(request: Request, call_next):
    if (request.url.hostname or "").lower() not in _ALLOWED_HOSTS:
        return JSONResponse({"detail": "Bad host"}, status_code=400)
    if request.method not in _SAFE_METHODS:
        if request.headers.get("x-volksmond-csrf") != CSRF_TOKEN:
            return JSONResponse({"detail": "Bad or missing CSRF token"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and (urlparse(origin).hostname or "").lower() not in _ALLOWED_HOSTS:
            return JSONResponse({"detail": "Bad origin"}, status_code=403)
    response = await call_next(request)
    # Defence in depth: never let this localhost UI be framed by another page.
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


class BrowserSink:
    """Fan-out sink: each connected SSE client gets its own queue.

    source_labels: optional {internal_tag: display_label} map applied to the streamed
    payload only (e.g. MIC/SYS -> Speaker L/Speaker R for a stereo interview upload);
    the pipeline's internal tags are never changed."""

    def __init__(self, source_labels=None):
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._labels = source_labels or {}

    def add_subscriber(self) -> queue.Queue:
        q = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(q)
        return q

    def remove_subscriber(self, q: queue.Queue):
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def __call__(self, segment):
        payload = {
            "source": self._labels.get(segment.source, segment.source),
            "t_start": segment.t_start,
            "t_end": segment.t_end,
            "text": segment.text,
        }
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass  # don't block engine on a slow consumer


class _State:
    """Singleton session state."""
    def __init__(self):
        self.lock = threading.Lock()
        self.engine: Optional[transcribe.Engine] = None
        self.capture: Optional[capture.AudioCapture] = None
        self.recorder: Optional[sinks.AudioRecorder] = None
        self.md_sink: Optional[sinks.MarkdownSink] = None
        self.browser_sink: Optional[BrowserSink] = None
        self.started_at: Optional[datetime] = None
        self.tier: Optional[str] = None
        self.model: Optional[str] = None
        self.family: Optional[str] = None    # "fluister" | "whisper" | "swivuriso", for the lean engine label
        self.output_path: Optional[Path] = None
        self.language: Optional[str] = None
        # Current capture device specs + chunk size, so a live device switch can rebuild
        # the capture with one source changed and the rest identical.
        self.mic_device: Optional[str] = None
        self.loopback_device: Optional[str] = None
        self.chunk_seconds: Optional[int] = None
        self.running: bool = False
        self.recording: bool = False
        self.record_raw_mic: bool = False   # live AEC + recording: recorder takes the raw MIC_RAW,
                                             # engine takes the cleaned MIC, so the recording stays raw
        self.transcribing: bool = False
        self.source_kind: Optional[str] = None   # "live" | "file"
        self.stopping: bool = False  # True while draining the backlog after Stop
        # Sticky transcript/recording write error, surfaced via /api/status. Set
        # during finalisation and kept across reset() so the UI can show it after
        # the session ends; cleared when the next session starts.
        self.sink_error: Optional[str] = None
        # Non-fatal notice about the running (or just-finished) session, surfaced via
        # /api/status the same sticky way (e.g. "stereo interview requested but the file
        # is mono"). Cleared when the next session starts.
        self.notice: Optional[str] = None

    def reset(self):
        self.engine = None
        self.capture = None
        self.recorder = None
        self.md_sink = None
        self.browser_sink = None
        self.started_at = None
        self.tier = None
        self.model = None
        self.family = None
        self.output_path = None
        self.language = None
        self.mic_device = None
        self.loopback_device = None
        self.chunk_seconds = None
        self.running = False
        self.recording = False
        self.record_raw_mic = False
        self.transcribing = False
        self.source_kind = None
        self.stopping = False


STATE = _State()

# Only one native file dialog (tkinter) at a time: concurrent Tk roots on worker
# threads can hang or trip Tk's thread assumptions. A second pick returns 409.
_PICK_LOCK = threading.Lock()

# Async summary jobs: stem -> {"state": "running"|"done"|"error", "saved": str|None,
# "summary": str|None, "error": str|None}. A summary runs in a worker thread so the UI can
# show "in progress" on the reader AND in the History list and survive navigating away.
_SUMMARY_JOBS = {}
_SUMMARY_LOCK = threading.Lock()


def _summarising_stems():
    """Stems whose summary is currently being generated (for the sessions list)."""
    with _SUMMARY_LOCK:
        return sorted(stem for stem, j in _SUMMARY_JOBS.items() if j.get("state") == "running")


def _summary_running():
    """True iff a summary worker is currently running. Used to gate live ASR + file import,
    so transcription and summary never compete for the machine (the design rule from v1.0)."""
    with _SUMMARY_LOCK:
        return any(j.get("state") == "running" for j in _SUMMARY_JOBS.values())


def _feed(source, audio, t_start):
    """Route a captured chunk to the recorder and/or the engine, honouring the live flags.

    Tapped before the engine so a recording stays complete even if transcription drops
    chunks under load. Module-level (not a closure) so /api/switch-device can rebuild the
    capture with the same feed without re-deriving it; the flags and targets are read live
    off STATE, so a three-way stop or a device switch is picked up without rewiring.

    Live AEC + recording (STATE.record_raw_mic): capture emits the RAW mic on a "MIC_RAW" source for
    the recorder (saved as the -MIC channel) and the cleaned mic on "MIC" for the engine, so the
    recording stays raw while the live transcript still benefits from echo cancellation."""
    rec = STATE.recorder if (STATE.recording and STATE.recorder is not None) else None
    eng = STATE.engine if (STATE.transcribing and STATE.engine is not None) else None
    if source == "MIC_RAW":
        if rec is not None:
            rec.on_chunk("MIC", audio, t_start)   # raw mic -> recording only; never the engine
        return
    # With live AEC recording the raw mic via MIC_RAW, the cleaned "MIC" here is for the engine only.
    if rec is not None and not (source == "MIC" and STATE.record_raw_mic):
        rec.on_chunk(source, audio, t_start)
    if eng is not None:
        eng.on_chunk(source, audio, t_start)


class StartRequest(BaseModel):
    topic: str = ""
    tier: str = "auto"            # "auto" | "gpu" | "cpu-strong" | "cpu-mid"
    device: str = "auto"          # "auto"/"gpu" use the GPU when ready; "cpu" forces CPU
    language: str = "af"          # "af" | "en" | "sa" (SA group) | a code like "zu"/"de" | "" (empty == auto-detect)
    engine: str = "auto"          # model family: "auto" (by language) | "fluister" | "whisper"
    prompt: str = ""
    mic_device: Optional[str] = None
    loopback_device: Optional[str] = None
    record: bool = False          # also save the audio (POPIA: needs consent)
    transcribe: bool = True       # False == record-only (for machines too slow to keep up live)
    aec_live: Optional[bool] = None  # live echo cancellation (None -> settings default)


def _slugify(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "session"


# Windows reserved device names, rejected as transcript filenames.
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _validate_session_filename(name: str) -> None:
    """Reject anything that is not a plain `*.md` basename. resolve()+relative_to()
    at the call site already blocks traversal; this adds a strict allow-list for
    Windows oddities (alternate data streams via ':', NUL, reserved device names) and
    glob metacharacters (* ? [ ]) that would otherwise widen a later glob() over the
    sessions dir into a match on other sessions' files."""
    if (not name
            or "/" in name or "\\" in name or ":" in name or "\x00" in name
            or any(c in name for c in "*?[]")
            or name != Path(name).name
            or not name.lower().endswith(".md")
            or name.split(".", 1)[0].upper() in _RESERVED_NAMES):
        raise HTTPException(status_code=400, detail="Invalid filename")


def _sessions_dir() -> Path:
    """Where transcripts and recordings are saved.

    User-configurable via the save_location setting. Falls back when unset or
    invalid to the project sessions/ folder in dev, or a per-user app-data folder
    when frozen (see below). Validates the configured path is a real,
    writable directory before trusting it (defence against a typo'd or read-only
    location silently losing a meeting's transcript).
    """
    loc = (config.load().get("save_location") or "").strip()
    if loc:
        try:
            p = Path(loc)
            p.mkdir(parents=True, exist_ok=True)
            if p.is_dir() and os.access(p, os.W_OK):
                return p
        except Exception:
            pass  # fall through to the default
    # Default save location: the project sessions/ folder in dev. When frozen,
    # PROJECT_ROOT points INSIDE the PyInstaller bundle, so use a persistent,
    # non-synced user folder (same base as settings/models) instead - otherwise
    # transcripts bury inside the app and vanish on reinstall.
    if getattr(sys, "frozen", False):
        p = paths.data_dir() / "sessions"
    else:
        p = PROJECT_ROOT / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _build_output_path(topic: str) -> Path:
    """A unique transcript path. Seconds plus a dedup suffix guarantee that two
    meetings with the same topic in the same minute never share a file, because
    MarkdownSink opens in append mode and a collision would merge two separate
    (possibly confidential) transcripts."""
    stem = f"{datetime.now():%Y-%m-%d-%H%M%S}-{_slugify(topic)}"
    sdir = _sessions_dir()
    for i in range(1, 1000):
        cand = sdir / (f"{stem}.md" if i == 1 else f"{stem}-{i}.md")
        if not cand.exists():
            return cand
    raise HTTPException(status_code=500, detail="Could not allocate a unique transcript filename")


@app.get("/", response_class=HTMLResponse)
def index():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # Hand the page the CSRF token so app.js can echo it on unsafe requests.
    tag = f'<meta name="vm-csrf" content="{CSRF_TOKEN}" />'
    return html.replace("</head>", f"  {tag}\n</head>", 1)


@app.get("/api/status")
def status():
    with STATE.lock:
        if not STATE.running:
            return {"running": False, "stopping": False, "sink_error": STATE.sink_error,
                    "notice": STATE.notice}
        live_err = STATE.md_sink.last_error if STATE.md_sink else None
        resp = {
            "running": True,
            "stopping": STATE.stopping,
            "recording": STATE.recording,
            "transcribing": STATE.transcribing,
            "source_kind": STATE.source_kind,
            "tier": STATE.tier,
            "model": STATE.model,
            "family": STATE.family,
            "language": STATE.language,
            "engine": STATE.engine.engine if STATE.engine else None,  # family override pref, so a reload restores the live picker
            "output_path": str(STATE.output_path) if STATE.output_path else None,
            "started_at": STATE.started_at.isoformat() if STATE.started_at else None,
            "sink_error": live_err or STATE.sink_error,
            "notice": STATE.notice,
            "mic_device": STATE.mic_device,
            "loopback_device": STATE.loopback_device,
        }
        # Live AEC truth for the in-meeting toggle: the ENGINE'S actual state, never the stored
        # setting (a long-running instance can drift from disk; the toggle must not lie).
        if STATE.source_kind == "live" and STATE.capture is not None:
            avail, active = STATE.capture.aec_state()
            resp["aec_live_available"] = avail
            resp["aec_live_active"] = active
        if STATE.stopping and STATE.engine is not None:
            resp["pending"] = STATE.engine.pending()
        return resp


@app.get("/api/devices")
def devices_list():
    """List the mics and loopbacks the user can pick.

    Enumeration is platform-specific (WASAPI-only filtering, per-host-API
    dedupe and the device-name mojibake fix on Windows), so the body lives
    behind the devices seam (devices.list_ui_devices); this endpoint just
    serves its dict: {loopbacks, mics, default_loopback_index,
    default_mic_index}.
    """
    from .. import devices
    return devices.list_ui_devices()


@app.get("/api/levels")
def levels():
    """Latest mic and system-audio input levels for the live meter: peak + rms, 0..1.
    Zeros when nothing is capturing. GET and cheap, so the UI can poll a few times a second."""
    z = {"peak": 0.0, "rms": 0.0}
    with STATE.lock:
        cap = STATE.capture if (STATE.running and STATE.capture is not None) else None
    lv = cap.levels() if cap is not None else {}
    return {"running": cap is not None, "mic": lv.get("MIC", z), "sys": lv.get("SYS", z)}


class SwitchDeviceRequest(BaseModel):
    which: Literal["mic", "loopback"]
    device: Optional[str] = None    # device index (or name substring); None = system default


@app.post("/api/switch-device")
def switch_device(req: SwitchDeviceRequest):
    """Change the mic or system-audio device during a LIVE session without ending it.

    The capture is restarted on the new device while the engine, recorder, and transcript
    file keep running; the original timeline (t0) is preserved so timestamps stay continuous.
    There is a brief (~1s) capture gap during the switch. On failure we revert to the
    previously working devices, so a bad pick never leaves the session with no audio."""
    with STATE.lock:
        if not STATE.running or STATE.stopping or STATE.source_kind != "live" or STATE.capture is None:
            raise HTTPException(status_code=409, detail="Switching devices is only available during a live session.")
        old_cap = STATE.capture
        prev_mic, prev_loop = STATE.mic_device, STATE.loopback_device
        mic = req.device if req.which == "mic" else prev_mic
        loop = req.device if req.which == "loopback" else prev_loop
        chunk = STATE.chunk_seconds or 15

        def _build(m, l):
            # Carry the session's live-AEC + raw-mic-recording settings across the switch, else
            # changing mic/loopback mid-meeting would silently turn echo cancellation (or the raw
            # recording side channel) off.
            c = capture.AudioCapture(mic_device=m, loopback_device=l, chunk_seconds=chunk,
                                     on_chunk=_feed, t0=old_cap._t0, aec=old_cap.aec,
                                     record_raw_mic=old_cap.record_raw_mic)
            eng = STATE.engine
            if eng is not None and getattr(eng, "sys_env", None) is not None:
                c.attach_sys_ring(eng.sys_env)   # keep the echo-veto reference fed across the switch
            return c

        try:
            old_cap.stop()
        except Exception:
            pass
        new_cap = None
        try:
            new_cap = _build(mic, loop)
            new_cap.start()
        except Exception as e:
            # The new device would not open. Stop the half-opened attempt first so it cannot
            # leak the audio device and a thread, then bring the previous (working) one back
            # so the session keeps capturing rather than going silent.
            if new_cap is not None:
                try:
                    new_cap.stop()
                except Exception:
                    pass
            revert = None
            try:
                revert = _build(prev_mic, prev_loop)
                revert.start()
                STATE.capture = revert
            except Exception:
                if revert is not None:
                    try:
                        revert.stop()
                    except Exception:
                        pass
                STATE.capture = None  # both failed: session stays running with no capture; the user can Stop
            raise HTTPException(status_code=500, detail=f"Could not switch the {req.which}: {e}")
        STATE.capture = new_cap
        STATE.record_raw_mic = new_cap.has_raw_mic()   # AEC may re-engage (or not) on the new device
        STATE.mic_device, STATE.loopback_device = mic, loop
        return {"which": req.which, "device": req.device, "mic_device": mic, "loopback_device": loop}


class AecLiveRequest(BaseModel):
    enabled: bool


@app.post("/api/aec-live")
def set_aec_live(req: AecLiveRequest):
    """Turn live echo cancellation on or off DURING a live session, without ending it.

    The capture keeps the WebRTC APM worker running (bypassed when off), so both directions are a
    per-frame flip: no capture rebuild, no audio gap, effective within ~1s. The user's choice is
    also persisted as the new default, so the pre-meeting toggle and disk stay in sync with what
    the engine is actually doing. Returns the CONFIRMED engine state, which the UI renders."""
    with STATE.lock:
        if not (STATE.running and not STATE.stopping and STATE.source_kind == "live"
                and STATE.capture is not None):
            raise HTTPException(status_code=409, detail="Echo cancellation can only be changed during a live session.")
        cap = STATE.capture
        if not cap.set_aec(bool(req.enabled)):
            raise HTTPException(status_code=409, detail="Echo cancellation is not available in this session. It needs both a microphone and system audio captured from the start.")
        avail, active = cap.aec_state()
    # Persist outside the state lock (config.update does disk I/O). The live toggle already took
    # effect either way, so a failed write must never fail the request; report it honestly in
    # `persisted` instead, so the UI can warn that the choice will not survive as the default.
    persisted = True
    try:
        config.update({"aec_live": bool(req.enabled)})
    except Exception as e:
        persisted = False
        print(f"[aec-live] toggle applied but the setting could not be saved: {e}", flush=True)
    return {"aec_live_available": avail, "aec_live_active": active, "persisted": persisted}


class ReconfigureRequest(BaseModel):
    # All optional; omit a field to leave it unchanged. language "" == auto-detect.
    language: Optional[str] = None    # "af" | "en" | "sa" | a code like "zu"/"de" | ""
    tier: Optional[str] = None        # quality/model key (a model size like "medium", or "auto")
    engine: Optional[str] = None      # "auto" | "fluister" | "whisper"


@app.post("/api/reconfigure")
def reconfigure(req: ReconfigureRequest):
    """Change the transcription LANGUAGE and/or MODEL during a live session, without ending it.

    A language-only change keeps the loaded model and only re-points the decoder (instant): a meeting
    that started in Afrikaans on Fluister can flip to English on the SAME model when the room switches,
    instead of force-decoding English as Afrikaans. Changing the model (a different quality size, or
    forcing the Fluister/Whisper family) reloads it, cached after the first time. The model loads
    OUTSIDE the state lock so /api/status and /api/levels keep responding, and the engine applies the
    swap between chunks so no live audio is dropped. The device (CPU/GPU) and chunk size are kept; only
    a live session can be reconfigured (a file import is re-run with new settings instead)."""
    data = req.model_dump(exclude_unset=True)
    change_lang = "language" in data
    change_model = bool(data.get("tier")) or bool(data.get("engine"))
    if not change_lang and not change_model:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    with STATE.lock:
        if not (STATE.running and STATE.transcribing and not STATE.stopping
                and STATE.source_kind == "live" and STATE.engine is not None):
            raise HTTPException(status_code=409, detail="Changing the language or model is only available during a live transcription.")
        engine = STATE.engine
        cur_is_cpu = engine._is_cpu
        compute = engine._compute_type
        threads = engine._cpu_threads
        cur_size = engine.size
        # The engine stores the DECODE token, which is None for auto-detect AND for every South
        # African language (see transcribe.decode_language), so it cannot stand in for the user's
        # language here: an isiZulu session would read as auto-detect and a tier-only change would
        # re-route the family off Swivuriso. The canonical user-selected code lives in
        # STATE.language ("zu" / "sa" / "af" / ... / "auto").
        cur_language = None if STATE.language in (None, "auto") else STATE.language
        cur_engine_pref = engine.engine
        cur_family = engine.family                # "fluister" | "whisper" | "swivuriso"

    new_lang = (data["language"] or None) if change_lang else cur_language   # "" -> None (auto-detect)
    new_engine_pref = (data.get("engine") or cur_engine_pref)

    # The family the new language/engine wants. If it differs from the running model's family (e.g.
    # switching to a South African language needs the Swivuriso model, or switching back off it), the model
    # MUST swap even on a language-only request, not just re-point the decoder.
    want_family = (new_engine_pref.lower() if new_engine_pref and new_engine_pref.lower() in ("fluister", "whisper", "swivuriso")
                   else transcribe.family_for_language(new_lang))
    family_change = want_family != cur_family

    # Resolve + build a new model when the model OR the family is changing; a same-family language-only
    # change keeps the current model (the whole point for a bilingual meeting on a both-capable model).
    model = model_name = family = None
    new_tier = None
    new_size = cur_size
    if change_model or family_change:
        if data.get("tier"):
            # A quality change: map the key to a size on THIS device (never flip CPU<->GPU live).
            new_tier = resolve_tier(data["tier"], "cpu" if cur_is_cpu else "auto", new_lang, new_engine_pref)
            new_size = transcribe.TIER_CONFIG[new_tier]["model"]
        else:
            new_size = cur_size                   # engine/family-only change: keep the running size
        model_name, family = transcribe.resolve_model(new_size, new_lang, new_engine_pref)
        device_str = "cpu" if cur_is_cpu else "cuda"
        try:
            model = transcribe.load_model(model_name, device_str, compute, cpu_threads=threads)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Could not load the {new_size} model: {e}")

    with STATE.lock:
        # The session could have ended while the model loaded outside the lock.
        if not (STATE.running and STATE.transcribing and not STATE.stopping
                and STATE.source_kind == "live" and STATE.engine is engine):
            raise HTTPException(status_code=409, detail="The session changed before the new settings could apply.")
        # Swivuriso (and any South African code on any family) decodes on auto-detect; every
        # other explicit code is forced as-is (see transcribe.decode_language).
        eff_family = family if family is not None else cur_family
        decode_lang = transcribe.decode_language(eff_family, new_lang)
        engine.request_change(language=decode_lang, engine=new_engine_pref,
                              model=model, model_name=model_name, size=new_size, family=family)
        # Only a request that actually carries a language change may rewrite the session's
        # canonical language; a tier/engine-only change keeps it exactly as the user chose it.
        if change_lang:
            STATE.language = (new_lang or "auto")
        if model is not None:
            if new_tier is not None:
                STATE.tier = new_tier
            STATE.model = model_name
            STATE.family = family
        return {"language": STATE.language, "tier": STATE.tier, "model": STATE.model,
                "family": STATE.family, "engine": new_engine_pref}


class WarmUpRequest(BaseModel):
    tier: str = "auto"
    device: str = "auto"
    language: str = "af"          # warm the family the session will use (af -> Fluister)
    engine: str = "auto"          # model family override, mirrors StartRequest


@app.get("/api/warm-up")
def warm_up_status():
    """Current background warm-up state, so the UI can show 'preparing' / 'ready'."""
    return transcribe.warm_status()


@app.post("/api/warm-up")
def warm_up(req: WarmUpRequest):
    """Pre-load (and lightly exercise) the transcription model in the background so the first
    Begin is instant instead of a multi-minute first-use stall. Idempotent and best-effort:
    safe to call whenever the user reaches a pre-meeting screen. A no-op during a running
    session, which already owns the model."""
    with STATE.lock:
        if STATE.running:
            return {"state": "busy", "tier": None}
    settings = config.load()
    quality = req.tier if (req.tier and req.tier != "auto") else (settings.get("tier") or "auto")
    device = (getattr(req, "device", None) or settings.get("device") or "auto")
    language = req.language or None     # "" (auto-detect) -> None, matching Begin
    engine_pref = (req.engine or settings.get("engine") or "auto")
    tier = resolve_tier(quality, device, language, engine_pref)
    return transcribe.warm_up_async(tier, language, engine_pref)


def _resolve_tier_lang_prompt(req):
    """Shared start/file-import resolution of tier, language, and seeded prompt.

    req.tier is a UI quality key (a model name like "medium"/"large-v3", or "auto",
    or a legacy tier key); resolve_tier maps it to a concrete TIER_CONFIG tier."""
    settings = config.load()
    quality = req.tier if (req.tier and req.tier != "auto") else None
    if quality is None and settings.get("tier") and settings["tier"] != "auto":
        quality = settings["tier"]
    device = getattr(req, "device", None) or settings.get("device") or "auto"
    language = req.language if req.language else None  # "" -> None (auto-detect)
    engine_pref = (getattr(req, "engine", None) or settings.get("engine") or "auto")
    tier = resolve_tier(quality or "auto", device, language, engine_pref)
    # Record the device decision in the log so a "why is it on CPU?" is answerable at a
    # glance (calling cuda_ready here also registers the libs before the engine loads).
    try:
        from .. import cudadl
        print(f"[tier] quality={quality!r} device={device!r} gpu_present={cudadl.gpu_present()} "
              f"installed={cudadl.installed()} cuda_ready={cudadl.cuda_ready()} -> {tier}", flush=True)
    except Exception:
        pass
    parts = [p for p in (settings.get("default_context", "").strip(), req.prompt.strip()) if p]
    prompt = ", ".join(parts) or None
    return tier, language, prompt, engine_pref


@app.post("/api/start")
def start(req: StartRequest):
    if _summary_running():
        raise HTTPException(status_code=409, detail="A summary is being generated. Wait for it to finish before starting a new session, so the two never compete for the machine.")
    # Pre-warm the model OUTSIDE the state lock. A cold or first-time load takes seconds (longer on
    # a network fallback), and doing it under STATE.lock (as the Engine build below does) freezes
    # /api/status and /api/levels, which the UI polls ~1/s, so the app reads as hung. load_model
    # caches by (model_name, device, compute_type), so the Engine build reuses this warm entry.
    # Best-effort: if this resolution ever drifts from Engine's, the build just loads under the lock
    # as before (slower, never wrong).
    if bool(req.transcribe):
        try:
            _wt, _wlang, _wp, _weng = _resolve_tier_lang_prompt(req)
            _wcfg = transcribe.TIER_CONFIG[_wt]
            _wmodel, _wfam = transcribe.resolve_model(_wcfg["model"], _wlang, _weng)
            transcribe.load_model(_wmodel, _wcfg["device"], _wcfg["compute_type"])
        except Exception:
            pass
    with STATE.lock:
        if STATE.running:
            raise HTTPException(status_code=409, detail="Session already running")
        STATE.sink_error = None  # fresh session: clear any prior write error
        STATE.notice = None

        transcribe_on = bool(req.transcribe)
        record_on = bool(req.record)
        if not transcribe_on and not record_on:
            raise HTTPException(status_code=400, detail="Nothing to do: enable transcription or recording.")

        tier, language, prompt, engine_pref = _resolve_tier_lang_prompt(req)
        chunk_seconds = default_chunk_seconds(tier)
        output_path = _build_output_path(req.topic)

        engine = None
        md_sink = None
        browser_sink = BrowserSink()
        if transcribe_on:
            # Load model (synchronous; takes a few seconds even when cached)
            try:
                engine = transcribe.Engine(tier=tier, language=language, initial_prompt=prompt, engine=engine_pref)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Could not load model ({tier}): {e}")
            md_sink = sinks.MarkdownSink(output_path)
            engine.subscribe(md_sink)
            engine.subscribe(browser_sink)
            engine.start()

        recorder = sinks.AudioRecorder(output_path.with_suffix("")) if record_on else None

        # Publish state BEFORE capture starts so the feed closure sees consistent flags.
        STATE.engine = engine
        STATE.md_sink = md_sink
        STATE.browser_sink = browser_sink
        STATE.recorder = recorder
        STATE.recording = record_on
        STATE.transcribing = transcribe_on
        STATE.started_at = datetime.now()
        STATE.tier = tier if transcribe_on else None
        STATE.model = engine.model_name if engine else None
        STATE.family = engine.family if engine else None
        STATE.output_path = output_path
        STATE.language = (language or "auto") if transcribe_on else None
        STATE.source_kind = "live"
        STATE.running = True
        STATE.mic_device = req.mic_device
        STATE.loopback_device = req.loopback_device
        STATE.chunk_seconds = chunk_seconds

        # Recorder is tapped BEFORE the engine (see _feed), so the recording stays complete
        # even when transcription drops chunks under load.
        aec_live = req.aec_live if req.aec_live is not None else bool(config.load().get("aec_live", False))
        cap = capture.AudioCapture(
            mic_device=req.mic_device,
            loopback_device=req.loopback_device,
            chunk_seconds=chunk_seconds,
            on_chunk=_feed,
            aec=aec_live,
            record_raw_mic=False,   # record the AEC-cleaned mic into the single stereo file, not a raw stem
        )
        # Feed a SYS energy ring from live capture so the engine vetoes MIC echo segments in real
        # time. Fed per-block from the callback (not from late SYS chunks); see SysEnergyRing.
        if engine is not None:
            _sys_ring = transcribe.SysEnergyRing()
            engine.sys_env = _sys_ring
            cap.attach_sys_ring(_sys_ring)
        try:
            cap.start()
        except Exception as e:
            if engine is not None:
                engine.stop()
            if md_sink is not None:
                md_sink.close()
            if recorder is not None:
                recorder.close()
            STATE.reset()
            raise HTTPException(status_code=500, detail=f"Could not start audio capture: {e}")
        STATE.capture = cap
        # True only if live AEC actually engaged (the raw side channel exists). If AEC could not
        # start, this stays False and the recorder takes the normal MIC, which is already raw.
        STATE.record_raw_mic = cap.has_raw_mic()

        return {
            "tier": tier if transcribe_on else None,
            "model": engine.model_name if engine else None,
            "family": engine.family if engine else None,
            "language": STATE.language,
            "output_path": str(output_path),
            "chunk_seconds": chunk_seconds,
            "recording": record_on,
            "transcribing": transcribe_on,
            "audio_stem": str(output_path.with_suffix("")) if record_on else None,
        }


def _expand_recording_channels(files):
    """When an uploaded file is ONE channel of a saved Volksmond recording (named
    <stem>-MIC/-SYS/-MIXED.wav), pull in its sibling channels from the same folder, so a single-file
    upload still transcribes BOTH sides (and can cancel echo, which needs the MIC + SYS pair). The
    summed -MIXED is dropped to avoid double-counting once the separate channels are present. A
    normal media file with no such sibling is returned unchanged. Read-only; the caller's own
    is_file() filter still applies afterwards."""
    extra = []
    for f in list(files):
        m = re.match(r"^(.+)-(?:mic|sys|mixed)\.wav$", Path(f).name, re.IGNORECASE)
        if not m:
            continue
        prefix = m.group(1).lower() + "-"
        try:
            for p in sorted(Path(f).parent.iterdir()):
                n = p.name.lower()
                if p.is_file() and n.startswith(prefix) and n.endswith(".wav") and not n.endswith("-mixed.wav"):
                    extra.append(str(p))
        except OSError:
            pass
    if not extra:
        return files
    seen, merged = set(), []
    for f in files + extra:
        if Path(f).name.lower().endswith("-mixed.wav"):
            continue   # the summed track double-counts once we have the separate channels
        key = os.path.normcase(os.path.abspath(f))
        if key not in seen:
            seen.add(key)
            merged.append(f)
    return merged


class TranscribeFileRequest(BaseModel):
    paths: list[str] = []          # explicit file paths (one for import, several for a recording)
    stem: Optional[str] = None     # alternatively a recording stem; globs <stem>-*.wav
    topic: str = ""
    tier: str = "auto"
    device: str = "auto"
    language: str = "af"
    prompt: str = ""
    engine: str = "auto"           # model family override, mirrors StartRequest
    aec: Optional[bool] = None     # echo cancellation on a re-transcribe (None -> settings default)
    stereo_split: bool = False     # upload option: a 2-channel file is an interview, one speaker
                                   # per channel (e.g. Samsung Interview mode); transcribe L and R
                                   # separately. Mono files fall back to the normal single track.


@app.post("/api/transcribe-file")
def transcribe_file(req: TranscribeFileRequest):
    """Transcribe one or more existing audio/video files through the live engine.

    Used by both 'import a recording' (one file) and 'record now, transcribe later'
    (the MIC/SYS WAVs of a record-only session, passed via stem). Streams to the
    same SSE transcript view and saves a Markdown transcript.
    """
    if _summary_running():
        raise HTTPException(status_code=409, detail="A summary is being generated. Wait for it to finish before starting a transcription, so the two never compete for the machine.")
    sdir = _sessions_dir()
    files = list(req.paths or [])
    base = None
    if req.stem:
        # Validate the stem against the same allow-list used for transcript filenames, so glob
        # metacharacters (* ? [), Windows-reserved names, ADS streams, and traversal segments
        # all get rejected the same way as a malformed transcript name. The validator wants a
        # ".md" filename, so we round-trip through that and strip the suffix.
        candidate = Path(req.stem).name + ".md"
        _validate_session_filename(candidate)
        base = candidate[:-3]
        stereo = sdir / (base + ".wav")
        if stereo.is_file():
            # New format: one stereo recording (left = MIC, right = SYS), already echo-cancelled.
            files.append(str(stereo))
        else:
            # Legacy format: the per-source channels (-MIC/-SYS), never the summed -MIXED.wav
            # (it is the same audio summed, so including it would double-count).
            files += sorted(str(p) for p in sdir.glob(base + "-*.wav")
                            if not p.name.lower().endswith("-mixed.wav"))
    elif files:
        files = _expand_recording_channels(files)
    files = [f for f in files if Path(f).is_file()]
    if not files:
        raise HTTPException(status_code=400, detail="No readable audio files found to transcribe.")

    with STATE.lock:
        if STATE.running:
            raise HTTPException(status_code=409, detail="A session is already running")
        STATE.sink_error = None  # fresh session: clear any prior write error
        STATE.notice = None
        tier, language, prompt, engine_pref = _resolve_tier_lang_prompt(req)
        chunk_seconds = default_chunk_seconds(tier)
        # Echo cancellation on a re-transcribe: only when both channels (MIC + SYS) are present
        # (the SYS channel is the echo reference). Opt-in / OFF by default: it cleans echo-only or
        # one-sided audio well, but can blur the near speaker during sustained double-talk.
        aec_on = req.aec if req.aec is not None else bool(config.load().get("aec", False))
        if base:
            # Re-transcribing a saved recording: write the transcript at the recording's own
            # stem so the single History session gains its transcript, instead of spawning a
            # second, differently-timestamped row.
            output_path = sdir / (base + ".md")
        else:
            topic = req.topic or Path(files[0]).stem
            output_path = _build_output_path(topic)
        try:
            # adaptive=False: a file is not real-time, so never downgrade the model
            # or cut the beam to "keep up" — keep the chosen quality and take longer.
            engine = transcribe.Engine(tier=tier, language=language, initial_prompt=prompt, adaptive=False, engine=engine_pref)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Could not load model ({tier}): {e}")
        if base and output_path.exists():
            # Regenerating replaces the prior transcript (the audio is kept as the source of
            # truth). Remove it only now the engine has loaded, so a load failure never loses
            # it, and the append-mode sink then writes a clean file.
            try:
                output_path.unlink()
            except OSError:
                pass
        # Stereo interview mode is an UPLOAD option only, never for a saved Volksmond recording
        # (whose stereo file means MIC/SYS = you/everyone-else, handled by the branch below).
        stereo_split = bool(req.stereo_split) and not base
        # Presentation seam for interview mode: the pipeline keeps its two internal source tags
        # (MIC = left, SYS = right) so chunking, echo dedup, and merging all work unchanged, and
        # only the sinks relabel them at write/stream time. An interview upload must not claim
        # the channels are "your mic" and "your computer's audio".
        src_labels = {"MIC": "Speaker L", "SYS": "Speaker R"} if stereo_split else None
        md_sink = sinks.MarkdownSink(output_path, source_labels=src_labels)
        browser_sink = BrowserSink(source_labels=src_labels)
        engine.subscribe(md_sink)
        engine.subscribe(browser_sink)
        engine.start()

        STATE.engine = engine
        STATE.md_sink = md_sink
        STATE.browser_sink = browser_sink
        STATE.started_at = datetime.now()
        STATE.tier = tier
        STATE.model = engine.model_name
        STATE.family = engine.family
        STATE.output_path = output_path
        STATE.language = language or "auto"
        STATE.transcribing = True
        STATE.recording = False
        STATE.recorder = None
        STATE.source_kind = "file"
        STATE.running = True

    def _run():
        try:
            from faster_whisper.audio import decode_audio
            from ..capture_core import iter_silence_chunks
            items = []  # (t_start, source, window), merged across files by time
            aborted = False  # set True on user cancel; controls drain-vs-abort in the finally
            boosts = []  # net dB applied per quiet-boosted channel, for the UI notice

            def _boost(name, chan):
                """Quiet-channel auto boost (audioboost.py): a channel captured well below
                normal level makes Whisper loop and hallucinate (measured: -33.6 dBFS
                active median looped, -28.3 dBFS was fine). Boosts a channel to -20 dBFS
                active median only when it sits below -30 dBFS; healthy audio passes
                through byte-identical. Engine input only, never the source file."""
                out, g = audioboost.boost_if_quiet(chan)
                if g:
                    boosts.append(g)
                    print(f"[transcribe-file] quiet-channel boost: {name} +{g:.1f} dB "
                          f"to {audioboost.TARGET_DB:.0f} dBFS active median", flush=True)
                return out
            # New single stereo recording (<stem>.wav: left = MIC, right = SYS), already
            # echo-cancelled at capture. Split the channels and skip offline AEC entirely.
            if base and len(files) == 1:
                import wave as _wave
                import numpy as _np
                ok, raw = False, b""
                try:
                    with _wave.open(files[0], "rb") as w:
                        ok = (w.getnchannels() == 2 and w.getframerate() == 16000)
                        if ok:
                            raw = w.readframes(w.getnframes())
                except Exception:
                    ok = False
                if ok:
                    data = _np.frombuffer(raw, dtype="<i2").astype(_np.float32).reshape(-1, 2) / 32768.0
                    mic_ch, sys_ch = data[:, 0], data[:, 1]
                    # Cross-channel bleed gate. Even on headphones the far side leaks into the MIC at
                    # low level; Whisper transcribes that leak as garbled ghost lines the text de-dup
                    # cannot catch (different words). Silence those MIC frames before transcribing.
                    # No-op on a clean recording (the MIC is never far below the SYS there). Toggle
                    # off with SA_LIVE_XCHAN_GATE=0.
                    gate_on = os.environ.get("SA_LIVE_XCHAN_GATE", "1") != "0"
                    if gate_on:
                        mic_ch, _sil, _tot = transcribe.xchan_gate_mic(mic_ch, sys_ch)
                        print(f"[transcribe-file] cross-channel gate silenced {_sil}/{_tot} mic frames", flush=True)
                    # Quiet-channel boost, deliberately AFTER the cross-channel gate and BEFORE
                    # the SysEnergyRing. The gate's 10 dB relative margin (and -50 dBFS sys
                    # floor) were calibrated on UNBOOSTED audio; the boost is per channel, so
                    # boosting first would shift the MIC/SYS relative levels by the gain
                    # difference (up to 13.6 dB on the measured recording) and stop the gate
                    # catching bleed. Gating first keeps both channels on the same unboosted
                    # decision basis (the calibrated one, exactly); the boost then only scales
                    # the kept frames, and gate-zeroed bleed stays zero at any gain. Boosting
                    # BOTH channels jointly when either qualifies was rejected: it still shifts
                    # the relative levels (each channel needs a different gain to reach -20)
                    # and needlessly rewrites a healthy channel. The ring below is built from
                    # the boosted SYS so the engine's echo veto sees the same signal it
                    # transcribes; the veto's relative margin loosens by the gain difference
                    # for MIC segments, which is safe because the gate has already zeroed the
                    # bleed the veto exists to catch, and a boosted MIC clears the veto's
                    # -28 dBFS absolute ceiling, so quiet REAL speech can no longer be vetoed.
                    mic_ch = _boost("MIC", mic_ch)
                    sys_ch = _boost("SYS", sys_ch)
                    # Build the SYS energy ring from the aligned far-end channel so the engine's echo
                    # veto has a reference for every MIC segment (the same mechanism it uses live).
                    _ring = transcribe.SysEnergyRing(retain_s=len(sys_ch) / 16000.0 + 60.0)
                    for _i in range(0, len(sys_ch), 8000):
                        _ring.add_block(_i / 16000.0, sys_ch[_i:_i + 8000])
                    engine.sys_env = _ring
                    for src, chan in (("MIC", mic_ch), ("SYS", sys_ch)):
                        for i, chunk in iter_silence_chunks(chan, 16000, chunk_seconds):
                            chunk = _np.ascontiguousarray(chunk)
                            # A MIC chunk the gate left as near-silence is pure far-end bleed: skip it
                            # so Whisper never runs on it (no words to echo, no silence hallucination).
                            if src == "MIC" and gate_on and float(_np.sqrt(_np.mean(chunk * chunk))) < 1.8e-3:
                                continue
                            items.append((i / 16000.0, src, chunk))

            # Stereo interview upload: one speaker per channel. Decode the channels separately
            # (any rate/codec; PyAV resamples to 16k) and feed them through the same two-source
            # merge as a saved recording, so each side transcribes on its own and the sinks
            # label them Speaker L / Speaker R. The cross-channel bleed gate is applied
            # SYMMETRICALLY (each side gated against a pristine copy of the other), unlike the
            # saved-recording branch which only gates MIC: the close-time text dedup and the
            # engine's echo veto both clean only the MIC side, so ungated bleed in the RIGHT
            # channel would survive as ghost lines. Measured on the real Samsung Interview-mode
            # test recording the channels sit a median 22 dB apart, so the shipped 10 dB
            # threshold separates cleanly; genuine double-talk (within 10 dB) is kept on both
            # sides. The SysEnergyRing echo veto is NOT wired here: it is one-directional
            # (MIC-only) and the symmetric gate already removed the bleed it would judge.
            # A mono source upmixes to two identical channels, detected exactly and sent down
            # the normal single-track path with a notice for the UI.
            if not items and stereo_split and len(files) == 1:
                import numpy as _np
                left = right = None
                try:
                    left, right = decode_audio(files[0], sampling_rate=16000, split_stereo=True)
                except Exception as e:
                    print(f"[transcribe-file] stereo split decode failed ({e}); using the normal path", flush=True)
                if left is not None and not _np.array_equal(left, right):
                    gate_on = os.environ.get("SA_LIVE_XCHAN_GATE", "1") != "0"
                    if gate_on:
                        gl, _sl, _tl = transcribe.xchan_gate_mic(left, right)
                        gr, _sr, _tr = transcribe.xchan_gate_mic(right, left)
                        print(f"[transcribe-file] stereo interview gate: L {_sl}/{_tl}, R {_sr}/{_tr} frames silenced", flush=True)
                        left, right = gl, gr
                    # Quiet-channel boost AFTER the symmetric gate, for the same reason as the
                    # saved-recording branch above: the gate's relative thresholds are
                    # calibrated on unboosted audio, and gating first keeps both sides on that
                    # same decision basis while each side still gets its own trigger decision
                    # (an interview file often has one quiet speaker). No SysEnergyRing here.
                    left = _boost("Speaker L", left)
                    right = _boost("Speaker R", right)
                    for src, chan in (("MIC", left), ("SYS", right)):
                        for i, chunk in iter_silence_chunks(chan, 16000, chunk_seconds):
                            chunk = _np.ascontiguousarray(chunk)
                            # A chunk the gate reduced to near-silence is pure bleed: skip it so
                            # Whisper never hallucinates on it (same guard as the saved-recording
                            # branch, applied to both sides because the gate ran on both).
                            if gate_on and float(_np.sqrt(_np.mean(chunk * chunk))) < 1.8e-3:
                                continue
                            items.append((i / 16000.0, src, chunk))
                    print("[transcribe-file] stereo interview split: left/right transcribed as two speakers", flush=True)
                elif left is not None:
                    with STATE.lock:
                        STATE.notice = "File is mono, transcribed as a single track"
                    print("[transcribe-file] stereo split requested but the file is mono; single track", flush=True)

            if not items:
                # Legacy / import path. When the file set has both a MIC and a SYS channel (an old
                # per-source recording, or an uploaded recording whose siblings were pulled in),
                # subtract the SYS (speaker) echo from the MIC before transcribing, so the other
                # side is not transcribed twice. The cleaned MIC + raw SYS keep their source tags,
                # so the you/other-side split is preserved. Best-effort: any failure falls back to
                # the raw MIC. Decoded audio is cached so SYS is not decoded twice.
                decoded = {}
                if aec_on:
                    from .. import aec as _aec
                    if _aec.available():
                        mic_fp = next((f for f in files if f.lower().endswith("-mic.wav")), None)
                        sys_fp = next((f for f in files if f.lower().endswith("-sys.wav")), None)
                        if mic_fp and sys_fp:
                            try:
                                mic_audio = decode_audio(mic_fp, sampling_rate=16000)
                                sys_audio = decode_audio(sys_fp, sampling_rate=16000)
                                decoded[sys_fp] = sys_audio
                                decoded[mic_fp] = _aec.cancel_echo(mic_audio, sys_audio)
                                print(f"[transcribe-file] echo cancellation applied to {Path(mic_fp).name}", flush=True)
                            except Exception as e:
                                decoded.clear()
                                print(f"[transcribe-file] echo cancellation skipped: {e}", flush=True)
                for fp in files:
                    low = fp.lower()
                    src = "MIC" if low.endswith("-mic.wav") else ("SYS" if low.endswith("-sys.wav") else "FILE")
                    audio = decoded.get(fp)
                    if audio is None:
                        audio = decode_audio(fp, sampling_rate=16000)
                    # Quiet-channel boost, per track. No cross-channel gate or energy ring in
                    # this branch, so each file is independent; for a legacy MIC/SYS pair with
                    # AEC on, the boost measures the echo-cleaned MIC (the audio that will
                    # actually be transcribed), which is the right basis.
                    audio = _boost(src, audio)
                    for i, chunk in iter_silence_chunks(audio, 16000, chunk_seconds):
                        items.append((i / 16000.0, src, chunk))
            if boosts:
                # Sticky notice for the UI toast (same channel as the mono-fallback notice).
                # The fixed phrase is an i18n key (trNotice in app.js translates it and keeps
                # the dynamic dB values); notices combine with " · " so neither is lost.
                msg = ("Quiet audio boosted for transcription ("
                       + ", ".join(f"+{g:.1f} dB" for g in boosts) + ")")
                with STATE.lock:
                    STATE.notice = (STATE.notice + " · " + msg) if STATE.notice else msg
            items.sort(key=lambda x: x[0])
            for t_start, src, window in items:
                with STATE.lock:
                    if STATE.stopping:
                        aborted = True
                        break
                # Wait for the transcriber instead of dropping (the 32-slot queue
                # would otherwise lose everything past the first ~32 chunks - the
                # hour-file truncation bug). Retry in short waits, re-checking the
                # stop flag and worker health, so a stalled or dead worker can never
                # wedge the import on a full queue.
                while not engine.on_chunk(src, window, t_start, block=True, timeout=0.5):
                    with STATE.lock:
                        if STATE.stopping:
                            aborted = True
                            break
                    if not engine.is_alive():
                        aborted = True
                        break
                if aborted:
                    break
        except Exception as e:
            print(f"[transcribe-file] error: {e}", flush=True)
        finally:
            try:
                # On user Cancel (aborted) do NOT drain: draining keeps transcribing the queued
                # backlog (minutes on CPU) after the user asked to stop, and holds STATE.running so
                # the next session 409s. Drain only on natural completion.
                engine.stop(drain=not aborted)
            except Exception:
                pass
            try:
                md_sink.close()
            except Exception:
                pass
            err = md_sink.last_error if md_sink else None
            with STATE.lock:
                STATE.reset()
                STATE.sink_error = err
            if not aborted:
                _bump_session_count()  # one completed file transcription (not a user cancel)

    threading.Thread(target=_run, daemon=True, name="file-transcribe").start()
    return {
        "tier": tier,
        "model": engine.model_name,
        "family": engine.family,
        "output_path": str(output_path),
        "source_kind": "file",
        "files": files,
    }


def _bump_session_count():
    """Count one completed session. Local only: it drives the one-time business-use
    nudge in the UI and never leaves this machine (the model is honour-system, not
    enforcement). A record-only session that is later re-transcribed can count twice;
    that is fine for a soft nudge and simpler than tracking session identity."""
    try:
        config.update({"session_count": int(config.load().get("session_count", 0)) + 1})
    except Exception:
        pass


@app.post("/api/stop")
def stop(what: str = "all"):
    """Stop the session, or part of it.

    what="all" (default): stop everything and finalise.
    what="transcription": stop transcribing, keep recording (if recording).
    what="recording": stop recording, keep transcribing (if transcribing).
    A partial stop that would leave nothing running is treated as "all".
    """
    with STATE.lock:
        if not STATE.running:
            raise HTTPException(status_code=409, detail="No session running")
        out = str(STATE.output_path) if STATE.output_path else None

        # File transcription: the feeder thread owns teardown; just signal it.
        if STATE.source_kind == "file":
            STATE.stopping = True
            return {"stopping": True, "output_path": out}

        # Stopping the recorder must take effect immediately, even while
        # transcription is still draining, otherwise audio keeps recording until
        # the ASR backlog clears.
        if what == "recording":
            if STATE.recording:
                STATE.recording = False
                rec, STATE.recorder = STATE.recorder, None
                if rec is not None:
                    try:
                        rec.close()
                    except Exception:
                        pass
                    if rec.last_error:
                        STATE.sink_error = rec.last_error
            if STATE.transcribing or STATE.stopping:
                return {"stopped": "recording", "recording": False,
                        "transcribing": STATE.transcribing, "stopping": STATE.stopping,
                        "output_path": out}
            what = "all"  # nothing left running: fall through to finalise

        # Narrow "stop transcription" to a full stop when nothing else is running.
        if what == "transcription" and not STATE.recording:
            what = "all"

        if what == "transcription":
            if STATE.stopping:
                pending = STATE.engine.pending() if STATE.engine else 0
                return {"stopped": "transcription", "stopping": True, "pending": pending, "output_path": out}
            STATE.stopping = True
            STATE.transcribing = False
            engine = STATE.engine
            md_sink = STATE.md_sink
            pending = engine.pending() if engine else 0

            def _drain_transcription():
                try:
                    if engine is not None:
                        engine.stop(drain=True)
                except Exception:
                    pass
                try:
                    if md_sink is not None:
                        md_sink.close()
                except Exception:
                    pass
                err = md_sink.last_error if md_sink else None
                cap_to_stop = None
                with STATE.lock:
                    STATE.engine = None
                    STATE.md_sink = None
                    if err:
                        STATE.sink_error = err
                    if STATE.recording:
                        STATE.stopping = False  # recording carries on; session still running
                    else:
                        # Recording was also stopped while we were draining: nothing
                        # is left running, so finalise. Stop capture OUTSIDE the lock
                        # (it can block), then reset the session.
                        cap_to_stop = STATE.capture
                        STATE.capture = None
                if cap_to_stop is not None:
                    try:
                        cap_to_stop.stop()
                    except Exception:
                        pass
                    with STATE.lock:
                        saved_err = STATE.sink_error
                        STATE.reset()
                        STATE.sink_error = saved_err

            threading.Thread(target=_drain_transcription, daemon=True, name="stop-transcription").start()
            return {"stopped": "transcription", "stopping": True, "pending": pending,
                    "recording": STATE.recording, "output_path": out}

        # what == "all"
        if STATE.stopping:
            pending = STATE.engine.pending() if STATE.engine else 0
            return {"stopping": True, "pending": pending, "output_path": out}
        STATE.stopping = True
        engine = STATE.engine
        cap = STATE.capture
        md_sink = STATE.md_sink
        rec = STATE.recorder
        pending = engine.pending() if engine else 0

    def _drain_and_close():
        # Stop capturing FIRST (flushes the final partial chunk into the engine
        # queue), THEN drain everything already queued before closing the file, so
        # the tail of the session is captured, not discarded. Runs off the request
        # thread and without holding STATE.lock because it can take a while.
        try:
            if cap is not None:
                cap.stop()
        except Exception:
            pass
        try:
            if engine is not None:
                engine.stop(drain=True)
        except Exception:
            pass
        try:
            if md_sink is not None:
                md_sink.close()
        except Exception:
            pass
        try:
            if rec is not None:
                rec.close()
        except Exception:
            pass
        err = (md_sink.last_error if md_sink else None) or (rec.last_error if rec else None)
        with STATE.lock:
            STATE.reset()
            STATE.sink_error = err

    _bump_session_count()  # one completed live/record session; file transcription is counted on its own completion
    threading.Thread(target=_drain_and_close, daemon=True, name="stop-drain").start()
    return {"stopping": True, "pending": pending, "output_path": out}


def _parse_session_filename(name: str) -> dict:
    """Extract date/time/topic from `YYYY-MM-DD-HHMMSS-topic-slug.md`
    (older files use HHMM, so the seconds group is optional)."""
    stem = name[:-3] if name.endswith(".md") else name
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})(?:\d{2})?-(.*)$", stem)
    if m:
        date_part, hh, mm, topic = m.group(1), m.group(2), m.group(3), m.group(4)
        return {
            "date": date_part,
            "time": f"{hh}:{mm}",
            "topic": topic.replace("-", " ") or "(session)",
        }
    return {"date": "", "time": "", "topic": stem}


@app.get("/api/sessions")
def sessions_list():
    """List sessions in the active save location, newest first.

    A "session" is a stem that has a transcript (`<stem>.md`) and/or a recording
    (`<stem>-MIC/SYS/MIXED.wav`). Enumerating by stem means a record-only session
    (audio captured but not transcribed yet) still appears, ready to re-transcribe.
    Each row carries status flags: `recorded`, `transcribed`, `has_summary`.

    Derived summaries (`<stem>-summary.md`, `<stem>-summary-N.md`) are not their own
    sessions; they are folded into `has_summary`, confirmed by the summary header so a
    real transcript whose topic merely ends in "summary" is never wrongly suppressed.

    `active` names the currently-running session's stem (when its output is in this
    folder) so the list can show recording/transcribing in progress."""
    sdir = _sessions_dir()
    md_names = {p.name for p in sdir.glob("*.md")}

    def _summary_stem(base):
        if base.endswith("-summary"):
            return base[:-len("-summary")]
        i = base.rfind("-summary-")
        if i >= 0 and base[i + len("-summary-"):].isdigit():
            return base[:i]
        return None

    # Which .md files are derived summaries (header-verified) -> neither a session nor a transcript.
    summary_mds = set()
    for name in md_names:
        stem = _summary_stem(name[:-3])
        if stem is not None and (stem + ".md") in md_names:
            try:
                with open(sdir / name, "r", encoding="utf-8") as fh:
                    if fh.readline(64).startswith("# Summary:"):
                        summary_mds.add(name)
            except OSError:
                pass

    # Same for <stem>-notes.md: the user's own notes sidecar, never its own session. Identified by
    # the "# Notes:" header alone (a real ASR transcript never starts with that), so an orphan notes
    # file with no transcript yet still never shows up as a phantom session.
    notes_mds = set()
    for name in md_names:
        if name[:-3].endswith("-notes"):
            try:
                with open(sdir / name, "r", encoding="utf-8") as fh:
                    if fh.readline(64).startswith("# Notes:"):
                        notes_mds.add(name)
            except OSError:
                pass

    sessions = {}

    def _row(stem):
        r = sessions.get(stem)
        if r is None:
            r = {"name": stem + ".md", "stem": stem, "recorded": False, "transcribed": False,
                 "has_summary": False, "has_notes": False, "size": 0, "mtime": 0.0, **_parse_session_filename(stem)}
            sessions[stem] = r
        return r

    for name in md_names:
        if name in summary_mds or name in notes_mds:
            continue
        r = _row(name[:-3])
        r["transcribed"] = True
        try:
            st = (sdir / name).stat()
            r["size"] = st.st_size
            r["mtime"] = max(r["mtime"], st.st_mtime)
        except OSError:
            pass

    for p in sdir.glob("*.wav"):
        low = p.name.lower()
        suff = next((s for s in ("-mic.wav", "-sys.wav", "-mixed.wav") if low.endswith(s)), None)
        # New recordings are a single stereo `<stem>.wav`; legacy ones are per-source
        # `<stem>-MIC/-SYS/-MIXED.wav`. Either way, map back to the session stem.
        stem = p.name[:-len(suff)] if suff is not None else p.name[:-4]
        r = _row(stem)
        r["recorded"] = True
        try:
            r["mtime"] = max(r["mtime"], p.stat().st_mtime)
        except OSError:
            pass

    for stem, r in sessions.items():
        if (stem + "-summary.md") in md_names:
            r["has_summary"] = True
        if (stem + "-notes.md") in notes_mds:
            r["has_notes"] = True

    files = sorted(sessions.values(), key=lambda f: f["mtime"], reverse=True)

    active = None
    with STATE.lock:
        if STATE.running and STATE.output_path:
            ap = Path(STATE.output_path)
            try:
                same = ap.parent.resolve() == sdir.resolve()
            except OSError:
                same = False
            if same:
                active = {"stem": ap.stem, "transcribing": bool(STATE.transcribing),
                          "recording": bool(STATE.recording)}
    return {"files": files, "folder": str(sdir), "active": active,
            "summarising": _summarising_stems()}


@app.get("/sessions/{filename}", response_class=PlainTextResponse)
def session_content(filename: str):
    """Serve a session file as plain text. Path-traversal-safe."""
    _validate_session_filename(filename)
    sdir = _sessions_dir()
    target = (sdir / filename).resolve()
    try:
        target.relative_to(sdir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Filename escapes sessions directory")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return target.read_text(encoding="utf-8")


@app.post("/api/open-folder")
def open_folder(which: str = "sessions"):
    """Open a known app folder in the OS file browser (Windows: Explorer).

    which: "sessions" (default, transcripts + recordings) | "voice_models" (the
    Whisper model cache) | "summary_models" (the local summary GGUFs), so the user
    can find and remove models on disk themselves."""
    if which == "voice_models":
        from .. import voicedl
        p = Path(voicedl.cache_dir())
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        target = str(p)
    elif which == "summary_models":
        target = str(config.models_dir(create=True))
    elif which == "cuda":
        from .. import cudadl
        target = str(cudadl.cuda_dir(create=True))
    else:
        target = str(_sessions_dir())
    try:
        if sys.platform == "win32":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return {"opened": target}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not open folder: {e}")


class SettingsPatch(BaseModel):
    interface_language: Optional[str] = None
    transcription_language: Optional[str] = None
    transcribe_languages: Optional[list] = None
    tier: Optional[str] = None
    device: Optional[str] = None
    engine: Optional[str] = None
    aec: Optional[bool] = None
    aec_live: Optional[bool] = None
    summary_device: Optional[str] = None
    save_location: Optional[str] = None
    default_context: Optional[str] = None
    ai_backend: Optional[str] = None
    ai_instructions: Optional[list] = None
    active_instruction_id: Optional[str] = None
    setup_complete: Optional[bool] = None
    licence_accepted: Optional[bool] = None
    session_count: Optional[int] = None
    business_nudge_seen: Optional[bool] = None
    summary_footer: Optional[bool] = None
    calendar_reminders: Optional[bool] = None
    live_notes_width: Optional[int] = None  # live-screen notes column width (px); 0 = default
    # summary_model is intentionally NOT settable here: only the verified downloader
    # (modeldl.py) sets it, to a pinned catalogue filename, so an arbitrary or
    # unverified path cannot be made the active summary model via the settings API.
    cloud_api_key: Optional[str] = None  # write-only; never returned by GET


@app.get("/api/settings")
def get_settings():
    return config.public_view()


@app.post("/api/settings")
def set_settings(patch: SettingsPatch):
    data = patch.model_dump(exclude_unset=True)
    if "cloud_api_key" in data:
        config.set_cloud_api_key(data.pop("cloud_api_key") or None)
    if "save_location" in data and data["save_location"]:
        # Validate up front so the user gets immediate feedback on a bad folder.
        loc = Path(data["save_location"])
        try:
            loc.mkdir(parents=True, exist_ok=True)
            if not (loc.is_dir() and os.access(loc, os.W_OK)):
                raise OSError("not a writable directory")
        except Exception:
            raise HTTPException(status_code=400, detail="Save location is not a writable folder.")
    if data:
        config.update(data)
    return config.public_view()


class LicenseRequest(BaseModel):
    key: str


@app.get("/api/license")
def get_license():
    return licensing.status(licensing.load_token())


@app.post("/api/license")
def set_license(req: LicenseRequest):
    try:
        return licensing.save_token(req.key.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/license")
def delete_license():
    licensing.clear_token()
    return licensing.status(None)


@app.get("/api/features")
def get_features():
    """Resolved entitlements for the UI to gate on: what is unlocked right now,
    plus the full catalogue so the UI can show locked items honestly."""
    ent = licensing.current()
    return {
        "tier": ent.tier,
        "unlocked": sorted(ent.features),
        "catalogue": {
            "pro": sorted(licensing.PRO_FEATURES),
            "addons": sorted(licensing.ADDON_FEATURES),
        },
    }


@app.get("/api/models")
def models_status():
    """Which summary model is installed (the Whisper model is handled by the tier).

    summary_gpu_capable is True only when an NVIDIA GPU is present AND this build's
    llama.cpp can offload to it (the CPU-only wheel cannot), so the UI shows a GPU/CPU
    choice for summaries only when it would actually do something."""
    from .. import summarise as _summarise, cudadl
    try:
        gpu_capable = bool(cudadl.gpu_present() and _summarise.gpu_offload_supported())
    except Exception:
        gpu_capable = False
    return {
        "summary_model": config.load().get("summary_model") or "",
        "summary_installed": config.summary_model_path() is not None,
        "summary_gpu_capable": gpu_capable,
        "summary_device": config.load().get("summary_device") or "auto",
    }


class ModelDownloadRequest(BaseModel):
    key: str


@app.get("/api/summary-models")
def summary_models():
    """Catalogue of downloadable summary models plus live download progress, so
    the UI can offer a one-click download instead of a manual file picker."""
    from .. import modeldl
    return {
        "models": modeldl.catalogue_public(),
        "progress": modeldl.progress(),
        "installed": config.summary_model_path() is not None,
    }


@app.post("/api/summary-model/download")
def summary_model_download(req: ModelDownloadRequest):
    """Start downloading a summary model to this machine (background). Returns the
    current progress snapshot; the UI polls /api/summary-models for updates."""
    from .. import modeldl
    try:
        modeldl.start_download(req.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return modeldl.progress()


@app.post("/api/summary-model/delete")
def summary_model_delete(req: ModelDownloadRequest):
    """Remove a downloaded summary model to free space (re-downloadable later)."""
    from .. import modeldl
    try:
        modeldl.delete(req.key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@app.get("/api/voice-models")
def voice_models():
    """Catalogue of downloadable transcription (Whisper) models plus live download
    progress, so first-run setup can pull the model down up front with a progress
    bar instead of faster-whisper fetching it silently at the first Begin."""
    from .. import voicedl
    cat = voicedl.catalogue_public()
    cat["progress"] = voicedl.progress()
    # Whether an Afrikaans session will actually run on a Fluister (Afrikaans-tuned) model yet,
    # so the UI can label the engine honestly (Fluister vs stock Whisper) before the tuned
    # models are hosted/installed.
    cat["fluister_available"] = transcribe.fluister_available()
    # Install state + version of each Fluister model on this machine (local only, no network), so the
    # voice-model card can show "installed v1.0.0" and, after a manual check, "update available".
    cat["fluister"] = voicedl.fluister_catalogue()
    # Swivuriso (DSFSI / African Next Voices): one credited third-party model for seven South African
    # languages. Install state only (no network), so the card can show installed / not installed.
    cat["swivuriso"] = voicedl.swivuriso_catalogue()
    return cat


class VoiceDownloadRequest(BaseModel):
    model: str


@app.post("/api/voice-model/download")
def voice_model_download(req: VoiceDownloadRequest):
    """Start downloading a transcription model to this machine (background). Returns
    the current progress snapshot; the UI polls /api/voice-models for updates."""
    from .. import voicedl
    try:
        voicedl.start_download(req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return voicedl.progress()


@app.post("/api/voice-model/delete")
def voice_model_delete(req: VoiceDownloadRequest):
    """Remove a downloaded transcription model to free space (re-downloadable later).
    Refuses to delete the model the running session is currently using."""
    from .. import voicedl
    with STATE.lock:
        # A live CPU session can downgrade to a smaller model on the fly (the CPU
        # ladder in transcribe.py), so STATE.model is not a reliable "in use" marker.
        # Refuse removing ANY transcription model while an engine is loaded.
        if STATE.engine is not None:
            raise HTTPException(status_code=409, detail="A transcription session is running. Stop it before removing a transcription model.")
    try:
        voicedl.delete(req.model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


# The model-update check is the model-version twin of /api/check-updates: another outbound GET to
# our own manifest. The default build keeps it (an offline user still wants a chance at an improved
# Fluister), but the airtight offline-only build compiles it out too, so it makes no outbound call
# at all beyond the models the user explicitly downloads.
if not buildflags.OFFLINE_ONLY:
    @app.post("/api/model-updates")
    def model_updates():
        """Manual, user-initiated check for a newer transcription model (e.g. an improved Fluister).
        Makes ONE outbound HTTPS GET to our OWN models.json manifest and compares it to the model
        versions installed on this machine. Sends no user data, runs only when the user clicks, and is
        CSRF-protected. The model-version twin of /api/check-updates (which checks the app version).
        Because load_model() reads the local cache with local_files_only, an improved model can only
        reach an existing install through this opt-in path; the app never revalidates against HF on its
        own."""
        from .. import voicedl
        try:
            manifest = voicedl.fetch_manifest()
        except Exception:
            raise HTTPException(status_code=502, detail="Could not reach the update server. Check your internet connection and try again.")
        updates = voicedl.model_update_status(manifest)
        return {"checked": True, "updates": updates,
                "any_update": any(u["update_available"] for u in updates)}


class VoiceUpdateRequest(BaseModel):
    size: str


@app.post("/api/voice-model/update")
def voice_model_update(req: VoiceUpdateRequest):
    """Download the newest published version of one Afrikaans (Fluister) model and record it as
    installed, so an existing user can opt in to an improved model. Background; the UI polls
    /api/voice-models for progress (shared with the normal download). Refused while a session runs,
    since updating swaps the files a live engine may load."""
    from .. import voicedl
    with STATE.lock:
        if STATE.engine is not None:
            raise HTTPException(status_code=409, detail="A transcription session is running. Stop it before updating a model.")
    try:
        voicedl.start_fluister_update(req.size)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach the update server. Check your internet connection and try again.")
    return voicedl.progress()


@app.post("/api/voice-model/swivuriso-download")
def voice_model_swivuriso_download():
    """Download the Swivuriso model (one model for seven South African languages, by DSFSI / African Next
    Voices) to this machine up front, with a progress bar, instead of faster-whisper fetching it
    silently at first use. Background; the UI polls /api/voice-models for progress (shared with the
    other downloads). Refused while a session runs, since it writes into the model cache a live
    engine may read."""
    from .. import voicedl
    with STATE.lock:
        if STATE.engine is not None:
            raise HTTPException(status_code=409, detail="A transcription session is running. Stop it before downloading a model.")
    try:
        voicedl.start_swivuriso_download()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return voicedl.progress()


@app.post("/api/voice-model/fluister-download")
def voice_model_fluister_download(req: VoiceUpdateRequest):
    """Download one Afrikaans (Fluister) model to this machine up front (a plain first-install pull),
    so the model card can install a size with a progress bar instead of faster-whisper fetching it
    silently at first use. Background; the UI polls /api/voice-models. Refused while a session runs."""
    from .. import voicedl
    with STATE.lock:
        if STATE.engine is not None:
            raise HTTPException(status_code=409, detail="A transcription session is running. Stop it before downloading a model.")
    try:
        voicedl.start_fluister_download(req.size)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return voicedl.progress()


@app.get("/api/cuda")
def cuda_status():
    """NVIDIA CUDA (optional GPU acceleration) status. NVIDIA ONLY; AMD/Intel GPUs use
    the CPU path. gpu_present = a CUDA device is visible; installed = the libs are on
    disk; ready = the GPU will actually be used (needs a restart after a fresh download)."""
    from .. import cudadl
    present = cudadl.gpu_present()
    return {
        # False on platforms without CUDA support (e.g. macOS); the UI hides the
        # whole GPU card when this is False. Always True on Windows.
        "supported": cudadl.SUPPORTED,
        "gpu_present": present,
        "installed": cudadl.installed(),
        "ready": cudadl.cuda_ready(),
        "vram_mb": cudadl.vram_mb() if present else None,
        "gpu_name": cudadl.gpu_name() if present else None,
        "approx_bytes": cudadl.APPROX_BYTES,
        "progress": cudadl.progress(),
    }


@app.post("/api/cuda/download")
def cuda_download():
    """Start downloading the NVIDIA CUDA libraries (background). 400 if no NVIDIA GPU."""
    from .. import cudadl
    if not cudadl.gpu_present():
        raise HTTPException(status_code=400, detail="No NVIDIA GPU detected on this computer.")
    try:
        cudadl.start_download()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return cudadl.progress()


@app.post("/api/cuda/remove")
def cuda_remove():
    """Remove the downloaded CUDA libraries to free space. Refused while a session runs."""
    from .. import cudadl
    with STATE.lock:
        if STATE.engine is not None:
            raise HTTPException(status_code=409, detail="A transcription session is running. Stop it first.")
    try:
        cudadl.remove()
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@app.post("/api/cuda/self-test")
def cuda_self_test():
    """Load the CUDA libraries now and report the result, so the user can confirm the GPU
    will be used (or see the exact error) without starting a meeting."""
    from .. import cudadl
    if not cudadl.gpu_present():
        raise HTTPException(status_code=400, detail="No NVIDIA GPU detected on this computer.")
    ok, err = cudadl.self_test()
    return {"ok": bool(ok), "error": err, "ready": cudadl.cuda_ready()}


def _connected():
    """True only in the connected (online-features) build. The offline-only build sets
    buildflags.OFFLINE_ONLY, and the default build simply never sets SA_LIVE_CONNECTED, so both
    leave this False and the online-only routes (the app update check, calendar) refuse. A frozen
    connected build sets SA_LIVE_CONNECTED=1 to turn them on."""
    return (not buildflags.OFFLINE_ONLY) and os.environ.get("SA_LIVE_CONNECTED") == "1"


# The app update check is the single outbound call the app makes on its own behalf, and only when
# the user clicks it. Every build has it EXCEPT the airtight offline-only edition, which compiles it
# out entirely: buildflags.OFFLINE_ONLY skips the route registration below, and the updatecheck
# module that performs the fetch is excluded from that bundle (sa-live-transcribe.spec), so the
# manifest URL is not even present. This gates exactly like the model-update check above.
if not buildflags.OFFLINE_ONLY:
    @app.post("/api/check-updates")
    def check_updates():
        """Manual, user-initiated app update check. Present in every build except the airtight
        offline edition (the OFFLINE_ONLY guard skips this route, and updatecheck is excluded from
        that bundle). Delegates the one outbound HTTPS GET to updatecheck.check. No user data is
        sent, it runs only on click, and it is CSRF-protected like every other POST."""
        from .. import updatecheck
        try:
            return updatecheck.check(licensing.APP_VERSION)
        except updatecheck.UpdateCheckError as e:
            raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/app-info")
def app_info():
    """Light, non-sensitive facts for the footer and the bug-report mailto: the
    display name, version, OS string, and where files are saved. Nothing here
    leaves the machine unless the user chooses to send a bug report."""
    from .. import voicedl, cudadl
    return {
        "name": "Volksmond",
        "version": licensing.APP_VERSION,
        "platform": platform.platform(),
        "save_dir": str(_sessions_dir()),
        # Where downloaded models live on disk, so the UI can show the user where to
        # find and remove them (voice = the HuggingFace cache; summary = our folder).
        "voice_models_dir": voicedl.cache_dir(),
        "summary_models_dir": str(config.models_dir()),
        "cuda_dir": str(cudadl.cuda_dir()),
        # Edition flags for the UI. "offline" is the flagship offline-only build, which compiles the
        # online modules (the app + model update checks, the calendar) OUT of the bundle, so the UI
        # hides them; those features are present in every other build. "connected" is a stricter flag
        # for genuinely online-only extras (the cloud-key danger zone) that only a build with
        # SA_LIVE_CONNECTED=1 turns on; it does NOT gate the user-initiated update check.
        "connected": _connected(),
        "offline": buildflags.OFFLINE_ONLY,
    }


@app.post("/api/pick")
def pick_path(kind: str = "file"):
    """Open a native OS picker on this machine and return the chosen absolute path.

    This is a local-only convenience: the server and the user are the same machine,
    so picking a path on disk is the right way to import a (possibly multi-GB) media
    file, rather than uploading bytes through the browser. tkinter is imported lazily
    so headless or test environments that never call this pay nothing. The UI falls
    back to a paste-a-path field if no dialog is available.

    Serialised: only one dialog at a time (409 otherwise), and the Tk root is
    always destroyed. Returns {"path": <absolute path> | None}; None on cancel.
    """
    if not _PICK_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A file dialog is already open.")
    try:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except Exception as e:
            raise HTTPException(status_code=501, detail=f"No native file dialog on this machine: {e}")
        root = None
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            if kind == "folder":
                chosen = filedialog.askdirectory(title="Choose a folder")
            else:
                chosen = filedialog.askopenfilename(
                    title="Choose a recording to transcribe",
                    filetypes=[
                        ("Audio and video", "*.mp3 *.m4a *.wav *.mp4 *.mov *.ogg *.flac *.aac *.webm *.mkv *.avi"),
                        ("All files", "*.*"),
                    ],
                )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Could not open the file dialog: {e}")
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:
                    pass
        return {"path": chosen or None}
    finally:
        _PICK_LOCK.release()


# --- meeting notes (the user's own notes, typed live, kept beside the transcript) -----------
# Stored as <stem>-notes.md next to the transcript, with a "# Notes:" header so the session list
# can tell them apart from real transcripts (the same trick summaries use). They are the user's
# own words, never the ASR transcript, and only ever reach a summary when the user opts in.

def _notes_path(stem: str) -> Path:
    return _sessions_dir() / (stem + "-notes.md")


def _strip_notes_header(raw: str) -> str:
    return re.sub(r"^#\s*Notes:[^\n]*\n+", "", raw or "", count=1)


def _read_notes(stem: str) -> str:
    """The user's notes for a session, header stripped, or '' if there are none."""
    try:
        return _strip_notes_header(_notes_path(stem).read_text(encoding="utf-8")).strip()
    except OSError:
        return ""


class NotesRequest(BaseModel):
    stem: str
    text: str = ""


@app.get("/api/notes")
def get_notes(stem: str):
    """The user's meeting notes for a session (empty string when there are none)."""
    _validate_session_filename(stem + ".md")  # validate the stem via the transcript allow-list
    return {"stem": stem, "text": _read_notes(stem)}


@app.post("/api/notes")
def set_notes(req: NotesRequest):
    """Save, or when empty clear, the user's notes for a session. Written to <stem>-notes.md
    next to the transcript; never mixed into the transcript file itself."""
    _validate_session_filename(req.stem + ".md")
    p = _notes_path(req.stem)
    text = (req.text or "").strip()
    if not text:
        try:
            p.unlink()
        except OSError:
            pass
        return {"stem": req.stem, "text": ""}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# Notes: {req.stem}\n\n{text}\n", encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not save notes: {e}")
    return {"stem": req.stem, "text": text}


# Calendar seeding is a connected/Business feature, so the offline-only build compiles it out with
# the rest of the online modules (both outlook_local.py and its Graph sibling outlook.py are excluded
# from that bundle). The local COM read makes no network call itself, but outlook.py does, and the
# locked plan (section 3) drops the whole calendar feature from the airtight edition. Guarding the
# registration also keeps the offline build from importing a module that is not in its bundle.
if not buildflags.OFFLINE_ONLY:
    @app.post("/api/calendar-seed")
    def calendar_seed():
        """Seed the prompt from the LOCAL Outlook desktop calendar. Fully offline: reads the current
        or next meeting's subject + attendee names over COM from the classic Outlook app on this
        machine, with no network call. A Business feature (calendar entitlement), since it is a
        professional convenience; personal use stays fully manual and free.

        Runs as a sync def, so FastAPI puts it on a worker thread; outlook_local does its own
        per-thread COM init. 402 when unlicensed, 503 when Outlook/pywin32 is not reachable, and a
        plain {"found": false} when Outlook is reachable but there is no meeting in the window."""
        if not licensing.current().has("calendar"):
            raise HTTPException(status_code=402, detail="Pulling attendees from your calendar needs a business licence.")
        from .. import outlook_local
        try:
            meeting = outlook_local.current_or_next_meeting()
        except outlook_local.OutlookUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        if not meeting:
            return {"found": False, "subject": "", "attendees": []}
        return {"found": True, "subject": meeting["subject"], "attendees": meeting["attendees"]}

    @app.get("/api/calendar-upcoming")
    def calendar_upcoming():
        """Poll target for the calendar reminder: the current or next local Outlook meeting plus how
        many minutes until it starts, so the UI can nudge "start transcribing?" when a meeting
        begins. Fully offline (local COM read). Business-gated. Returns `available: false` (NOT an
        error) when Outlook/pywin32 is not present, so the UI's repeating poll skips a tick quietly
        rather than surfacing failures."""
        if not licensing.current().has("calendar"):
            raise HTTPException(status_code=402, detail="Calendar reminders need a business licence.")
        from .. import outlook_local
        try:
            meeting = outlook_local.current_or_next_meeting()
        except outlook_local.OutlookUnavailable:
            return {"available": False, "found": False}
        if not meeting:
            return {"available": True, "found": False}
        starts_in_min = None
        start = meeting.get("start")
        if start:
            try:
                starts_in_min = round((datetime.fromisoformat(start) - datetime.now()).total_seconds() / 60.0)
            except ValueError:
                starts_in_min = None
        return {"available": True, "found": True, "subject": meeting["subject"],
                "attendees": meeting["attendees"], "start": start, "starts_in_min": starts_in_min}


class SummariseRequest(BaseModel):
    file: str                      # session filename within the save location
    instruction: Optional[str] = None
    language: Optional[Literal["af", "en"]] = None  # output language for the summary
    include_notes: Optional[bool] = None            # fold the user's <stem>-notes.md into the summary


def _generate_summary(model_path, transcript, instruction, language, notes=None):
    """Run the local summariser, preferring the GPU when it is usable, falling back to CPU."""
    from .. import summarise as _summarise, cudadl
    # GPU only when: the user has not forced CPU, an NVIDIA GPU is present, this build's
    # llama.cpp can offload (the CPU wheel cannot), and the model fits in VRAM with headroom.
    device = (config.load().get("summary_device") or "auto").strip().lower()
    n_gpu_layers = 0
    if (device != "cpu" and _summarise.gpu_offload_supported() and cudadl.gpu_present()
            and _summarise.fits_on_gpu(model_path, cudadl.vram_mb())):
        n_gpu_layers = -1
    print(f"[summarise] device={device!r} offload={_summarise.gpu_offload_supported()} "
          f"gpu_present={cudadl.gpu_present()} vram={cudadl.vram_mb()} -> n_gpu_layers={n_gpu_layers}", flush=True)

    def _run(layers):
        s = _summarise.Summariser(model_path, n_gpu_layers=layers)
        return s.summarise(transcript, instruction=instruction, language=language, notes=notes)
    try:
        return _run(n_gpu_layers)
    except Exception as e:
        # A GPU run can fail (e.g. CUDA out of memory on a big KV cache). Fall back to CPU.
        if n_gpu_layers != 0:
            print(f"[summarise] GPU run failed ({e}); retrying on CPU", flush=True)
            return _run(0)
        raise


# Appended to the summary FILE only, and only when the summary_footer setting is on. Never the
# raw transcript, and never any export the user shares onward (these come from counselling and
# legal sessions, so exports stay clean). A small, one-click-off growth surface. No em dash, by
# house style.
SUMMARY_FOOTER_TEXT = "Made with Volksmond - volksmond.digiphyte.com"


def _summary_body(target, summary):
    """The Markdown for a summary file: a header, the summary, and (when enabled) a small
    Volksmond footer. The footer is gated on the summary_footer setting and touches nothing else."""
    body = f"# Summary: {target.stem}\n\n{summary}\n"
    try:
        footer_on = bool(config.load().get("summary_footer", True))
    except Exception:
        footer_on = True
    if footer_on:
        body += "\n---\n_" + SUMMARY_FOOTER_TEXT + "_\n"
    return body


def _save_summary(target, summary):
    """Write <stem>-summary.md (the latest, which the reader loads), archiving any previous
    latest to <stem>-summary-N.md first so regenerating never destroys an earlier summary.
    Returns the path actually written."""
    body = _summary_body(target, summary)
    out = target.with_name(target.stem + "-summary.md")
    if out.exists():
        n = 1
        while out.with_name(f"{target.stem}-summary-{n}.md").exists():
            n += 1
        archive = out.with_name(f"{target.stem}-summary-{n}.md")
        try:
            out.rename(archive)
        except OSError:
            # Could not archive the previous latest (e.g. a file lock). Do NOT overwrite and
            # lose it: save THIS run under the fresh archive name, leave the old latest in place.
            archive.write_text(body, encoding="utf-8")
            return archive
    out.write_text(body, encoding="utf-8")
    return out


@app.post("/api/summarise")
def summarise_endpoint(req: SummariseRequest):
    """Start a local summary of a finished transcript, in a worker thread.

    Tracked as a job (poll /api/summary-status), so the UI can show "in progress" on the
    reader AND in the History list and survive navigating away. Free + fully on-device.
    One summary at a time: a second request while one is running returns 409, matching the
    old synchronous behaviour and keeping two summariser models off the machine at once."""
    with STATE.lock:
        if STATE.running:
            raise HTTPException(
                status_code=409,
                detail="Finish the current session first. Summaries run once the transcript "
                       "is complete, so the transcription and summary engines never compete for the machine.",
            )

    fn = req.file
    _validate_session_filename(fn)
    sdir = _sessions_dir()
    target = (sdir / fn).resolve()
    try:
        target.relative_to(sdir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Filename escapes sessions directory")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Transcript not found")

    model_path = config.summary_model_path()
    if not model_path:
        raise HTTPException(status_code=409, detail="No summary model installed. Choose one in Settings.")

    # Read the transcript + resolve the instruction BEFORE marking a job running, so any failure
    # here surfaces as a real HTTP error instead of leaving the job dict permanently stuck.
    try:
        transcript = target.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not read the transcript: {e}")
    instruction = (req.instruction or config.active_instruction()) or None
    language = req.language
    # The user's own meeting notes, only when they asked to include them. Read here (not in the
    # worker) so a read error surfaces as a normal request, and so the worker closes over a value.
    notes = _read_notes(target.stem) or None if req.include_notes else None

    stem = target.stem
    with _SUMMARY_LOCK:
        if any(j.get("state") == "running" for j in _SUMMARY_JOBS.values()):
            raise HTTPException(status_code=409, detail="A summary is already being generated. Let it finish first.")
        _SUMMARY_JOBS[stem] = {"state": "running", "saved": None, "summary": None, "error": None}

    def _worker():
        try:
            summary = _generate_summary(model_path, transcript, instruction, language, notes=notes)
            saved = _save_summary(target, summary)
            with _SUMMARY_LOCK:
                _SUMMARY_JOBS[stem] = {"state": "done", "saved": str(saved), "summary": summary, "error": None}
        except Exception as e:
            with _SUMMARY_LOCK:
                _SUMMARY_JOBS[stem] = {"state": "error", "saved": None, "summary": None, "error": str(e)}
            print(f"[summarise] job failed for {stem}: {e}", flush=True)

    try:
        threading.Thread(target=_worker, daemon=True, name="summarise").start()
    except RuntimeError as e:
        # Could not spawn the worker (very rare). Roll back the running flag so the next request
        # is not blocked forever, then surface the failure.
        with _SUMMARY_LOCK:
            _SUMMARY_JOBS.pop(stem, None)
        raise HTTPException(status_code=500, detail=f"Could not start summary worker: {e}")
    return {"status": "started", "stem": stem}


@app.get("/api/summary-status")
def summary_status(file: str):
    """Poll a summary job by transcript filename. Returns the job state for that stem:
    state in {running, done, error, idle}, plus saved/summary/error when present."""
    _validate_session_filename(file)
    stem = file[:-3] if file.endswith(".md") else file
    with _SUMMARY_LOCK:
        job = _SUMMARY_JOBS.get(stem)
        return dict(job) if job else {"state": "idle"}


@app.get("/api/stream")
async def stream():
    """Server-Sent Events stream of transcription segments."""
    async def event_generator():
        # Snapshot the current browser sink. If a new session starts, this
        # connection stays bound to the old sink; the browser re-opens the
        # EventSource on /api/start or /api/transcribe-file.
        with STATE.lock:
            sink = STATE.browser_sink

        if sink is None:
            yield "event: idle\ndata: {}\n\n"
            return

        q = sink.add_subscriber()
        try:
            while True:
                try:
                    seg = await asyncio.to_thread(q.get, True, 2.0)
                    yield f"data: {json.dumps(seg)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
                except asyncio.CancelledError:
                    raise
        finally:
            sink.remove_subscriber(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
