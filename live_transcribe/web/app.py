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

from .. import capture, config, licensing, sinks, transcribe
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
    """Fan-out sink: each connected SSE client gets its own queue."""

    def __init__(self):
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

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
            "source": segment.source,
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
        self.family: Optional[str] = None    # "fluister" | "whisper", for the lean engine label
        self.output_path: Optional[Path] = None
        self.language: Optional[str] = None
        # Current capture device specs + chunk size, so a live device switch can rebuild
        # the capture with one source changed and the rest identical.
        self.mic_device: Optional[str] = None
        self.loopback_device: Optional[str] = None
        self.chunk_seconds: Optional[int] = None
        self.running: bool = False
        self.recording: bool = False
        self.transcribing: bool = False
        self.source_kind: Optional[str] = None   # "live" | "file"
        self.stopping: bool = False  # True while draining the backlog after Stop
        # Sticky transcript/recording write error, surfaced via /api/status. Set
        # during finalisation and kept across reset() so the UI can show it after
        # the session ends; cleared when the next session starts.
        self.sink_error: Optional[str] = None

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
        self.transcribing = False
        self.source_kind = None
        self.stopping = False


STATE = _State()

# Only one native file dialog (tkinter) at a time: concurrent Tk roots on worker
# threads can hang or trip Tk's thread assumptions. A second pick returns 409.
_PICK_LOCK = threading.Lock()


def _feed(source, audio, t_start):
    """Route a captured chunk to the recorder and/or the engine, honouring the live flags.

    Tapped before the engine so a recording stays complete even if transcription drops
    chunks under load. Module-level (not a closure) so /api/switch-device can rebuild the
    capture with the same feed without re-deriving it; the flags and targets are read live
    off STATE, so a three-way stop or a device switch is picked up without rewiring."""
    if STATE.recording and STATE.recorder is not None:
        STATE.recorder.on_chunk(source, audio, t_start)
    if STATE.transcribing and STATE.engine is not None:
        STATE.engine.on_chunk(source, audio, t_start)


class StartRequest(BaseModel):
    topic: str = ""
    tier: str = "auto"            # "auto" | "gpu" | "cpu-strong" | "cpu-mid"
    device: str = "auto"          # "auto"/"gpu" use the GPU when ready; "cpu" forces CPU
    language: str = "af"          # "af" | "en" | "" (empty == auto-detect)
    prompt: str = ""
    mic_device: Optional[str] = None
    loopback_device: Optional[str] = None
    record: bool = False          # also save the audio (POPIA: needs consent)
    transcribe: bool = True       # False == record-only (for machines too slow to keep up live)


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
    Windows oddities (alternate data streams via ':', NUL, reserved device names)."""
    if (not name
            or "/" in name or "\\" in name or ":" in name or "\x00" in name
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
        p = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "sa-live-transcribe" / "sessions"
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
            return {"running": False, "stopping": False, "sink_error": STATE.sink_error}
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
            "output_path": str(STATE.output_path) if STATE.output_path else None,
            "started_at": STATE.started_at.isoformat() if STATE.started_at else None,
            "sink_error": live_err or STATE.sink_error,
            "mic_device": STATE.mic_device,
            "loopback_device": STATE.loopback_device,
        }
        if STATE.stopping and STATE.engine is not None:
            resp["pending"] = STATE.engine.pending()
        return resp


@app.get("/api/devices")
def devices_list():
    """List the mics and loopbacks the user can pick.

    PyAudio enumerates every physical device once PER HOST API (MME +
    DirectSound + WASAPI + WDM-KS), so on a typical laptop a single Realtek
    mic appears 3-4 times under the same name. Plus the MME / DirectSound
    meta-devices ("Microsoft Sound Mapper", "Primary Sound Capture Driver")
    that point at "whatever Windows currently calls default" are not real
    devices users should pick.

    We filter to WASAPI-only for mics, matching what we already do for
    loopbacks (loopback is WASAPI-exclusive on Windows). One entry per
    physical device, all on the modern API. If WASAPI itself misbehaves
    on a particular machine, the CLI `--list-devices` still shows every
    host API for diagnostic purposes; this endpoint is for the UI.
    """
    import pyaudiowpatch as pa
    def _fix_name(s):
        # PyAudio returns device names as latin-1-encoded bytes wrapped in a
        # Python str, so a real "Intel(R)" comes back as the mojibake we'd see
        # if you decoded UTF-8 as latin-1. Reverse it: encode the str's code
        # points as latin-1 bytes, decode those bytes as UTF-8. Falls open if
        # the name was actually plain ASCII (no Unicode chars to misencode).
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    p = pa.PyAudio()
    try:
        loopbacks = [
            {"index": info["index"], "name": _fix_name(info["name"]), "rate": int(info["defaultSampleRate"])}
            for info in p.get_loopback_device_info_generator()
        ]
        try:
            default_lb = p.get_default_wasapi_loopback()
            default_lb_idx = default_lb["index"]
        except Exception:
            default_lb_idx = None

        try:
            wasapi_idx = p.get_host_api_info_by_type(pa.paWASAPI)["index"]
        except Exception:
            wasapi_idx = None

        try:
            default_in = p.get_default_input_device_info()
            default_in_idx = default_in["index"]
        except Exception:
            default_in_idx = None

        def _collect_mics(wasapi_only):
            # Dedupe by (cleaned name, rate): the same physical mic is enumerated once
            # per host API (MME / DirectSound / WASAPI), so collapse those duplicates.
            out, seen = [], set()
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info["maxInputChannels"] <= 0 or info.get("isLoopbackDevice"):
                    continue
                if wasapi_only and wasapi_idx is not None and info["hostApi"] != wasapi_idx:
                    continue
                name = _fix_name(info["name"])
                rate = int(info["defaultSampleRate"])
                if (name, rate) in seen:
                    continue
                seen.add((name, rate))
                out.append({"index": info["index"], "name": name, "rate": rate})
            return out

        # WASAPI-only by default (clean list); if WASAPI exposes no input endpoints,
        # fall back to every real mic so the dropdown is never empty.
        mics = _collect_mics(wasapi_only=True)
        if not mics:
            mics = _collect_mics(wasapi_only=False)

        # The system default mic may be on a non-WASAPI host API. Map it to the device
        # with the same CLEANED name so the dropdown's default highlight is correct
        # (compare _fix_name to _fix_name; a raw vs cleaned mismatch would miss). Never
        # silently pick a different mic: with no match and more than one candidate, leave
        # the default unset rather than risk opening the wrong device at /api/start.
        if default_in_idx is not None and not any(m["index"] == default_in_idx for m in mics):
            try:
                default_name = _fix_name(p.get_device_info_by_index(default_in_idx)["name"])
                match = next((m for m in mics if m["name"] == default_name), None)
            except Exception:
                match = None
            if match:
                default_in_idx = match["index"]
            elif len(mics) == 1:
                default_in_idx = mics[0]["index"]
            else:
                default_in_idx = None

        return {
            "loopbacks": loopbacks,
            "mics": mics,
            "default_loopback_index": default_lb_idx,
            "default_mic_index": default_in_idx,
        }
    finally:
        p.terminate()


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
            return capture.AudioCapture(mic_device=m, loopback_device=l, chunk_seconds=chunk,
                                        on_chunk=_feed, t0=old_cap._t0)

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
        STATE.mic_device, STATE.loopback_device = mic, loop
        return {"which": req.which, "device": req.device, "mic_device": mic, "loopback_device": loop}


class WarmUpRequest(BaseModel):
    tier: str = "auto"
    device: str = "auto"
    language: str = "af"          # warm the family the session will use (af -> Fluister)


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
    tier = resolve_tier(quality, device)
    language = req.language or None     # "" (auto-detect) -> None -> stock Whisper, matching Begin
    return transcribe.warm_up_async(tier, language)


def _resolve_tier_lang_prompt(req):
    """Shared start/file-import resolution of tier, language, and seeded prompt.

    req.tier is a UI quality key (a model name like "medium"/"large-v3", or "auto",
    or a legacy tier key); resolve_tier maps it to a concrete TIER_CONFIG tier."""
    settings = config.load()
    quality = req.tier if (req.tier and req.tier != "auto") else None
    if quality is None and settings.get("tier") and settings["tier"] != "auto":
        quality = settings["tier"]
    device = getattr(req, "device", None) or settings.get("device") or "auto"
    tier = resolve_tier(quality or "auto", device)
    # Record the device decision in the log so a "why is it on CPU?" is answerable at a
    # glance (calling cuda_ready here also registers the libs before the engine loads).
    try:
        from .. import cudadl
        print(f"[tier] quality={quality!r} device={device!r} gpu_present={cudadl.gpu_present()} "
              f"installed={cudadl.installed()} cuda_ready={cudadl.cuda_ready()} -> {tier}", flush=True)
    except Exception:
        pass
    language = req.language if req.language else None  # "" -> None (auto-detect)
    parts = [p for p in (settings.get("default_context", "").strip(), req.prompt.strip()) if p]
    prompt = ", ".join(parts) or None
    return tier, language, prompt


@app.post("/api/start")
def start(req: StartRequest):
    with STATE.lock:
        if STATE.running:
            raise HTTPException(status_code=409, detail="Session already running")
        STATE.sink_error = None  # fresh session: clear any prior write error

        transcribe_on = bool(req.transcribe)
        record_on = bool(req.record)
        if not transcribe_on and not record_on:
            raise HTTPException(status_code=400, detail="Nothing to do: enable transcription or recording.")

        tier, language, prompt = _resolve_tier_lang_prompt(req)
        chunk_seconds = default_chunk_seconds(tier)
        output_path = _build_output_path(req.topic)

        engine = None
        md_sink = None
        browser_sink = BrowserSink()
        if transcribe_on:
            # Load model (synchronous; takes a few seconds even when cached)
            try:
                engine = transcribe.Engine(tier=tier, language=language, initial_prompt=prompt)
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
        cap = capture.AudioCapture(
            mic_device=req.mic_device,
            loopback_device=req.loopback_device,
            chunk_seconds=chunk_seconds,
            on_chunk=_feed,
        )
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


class TranscribeFileRequest(BaseModel):
    paths: list[str] = []          # explicit file paths (one for import, several for a recording)
    stem: Optional[str] = None     # alternatively a recording stem; globs <stem>-*.wav
    topic: str = ""
    tier: str = "auto"
    device: str = "auto"
    language: str = "af"
    prompt: str = ""


@app.post("/api/transcribe-file")
def transcribe_file(req: TranscribeFileRequest):
    """Transcribe one or more existing audio/video files through the live engine.

    Used by both 'import a recording' (one file) and 'record now, transcribe later'
    (the MIC/SYS WAVs of a record-only session, passed via stem). Streams to the
    same SSE transcript view and saves a Markdown transcript.
    """
    files = list(req.paths or [])
    if req.stem:
        stem = Path(req.stem)
        files += sorted(str(p) for p in stem.parent.glob(stem.name + "-*.wav"))
    files = [f for f in files if Path(f).is_file()]
    if not files:
        raise HTTPException(status_code=400, detail="No readable audio files found to transcribe.")

    with STATE.lock:
        if STATE.running:
            raise HTTPException(status_code=409, detail="A session is already running")
        STATE.sink_error = None  # fresh session: clear any prior write error
        tier, language, prompt = _resolve_tier_lang_prompt(req)
        chunk_seconds = default_chunk_seconds(tier)
        topic = req.topic or Path(files[0]).stem
        output_path = _build_output_path(topic)
        try:
            # adaptive=False: a file is not real-time, so never downgrade the model
            # or cut the beam to "keep up" — keep the chosen quality and take longer.
            engine = transcribe.Engine(tier=tier, language=language, initial_prompt=prompt, adaptive=False)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Could not load model ({tier}): {e}")
        md_sink = sinks.MarkdownSink(output_path)
        browser_sink = BrowserSink()
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
            win = int(16000 * chunk_seconds)
            items = []  # (t_start, source, window), merged across files by time
            for fp in files:
                low = fp.lower()
                src = "MIC" if low.endswith("-mic.wav") else ("SYS" if low.endswith("-sys.wav") else "FILE")
                audio = decode_audio(fp, sampling_rate=16000)
                for i in range(0, len(audio), win):
                    items.append((i / 16000.0, src, audio[i:i + win]))
            items.sort(key=lambda x: x[0])
            aborted = False
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
                engine.stop(drain=True)
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

    threading.Thread(target=_run, daemon=True, name="file-transcribe").start()
    return {
        "tier": tier,
        "model": engine.model_name,
        "family": engine.family,
        "output_path": str(output_path),
        "source_kind": "file",
        "files": files,
    }


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
    """List session transcripts in the active save location, newest first.

    A summary is saved next to its transcript as `<transcript>-summary.md`. That is a
    derived artifact, not its own session, so it is hidden from the list (it was showing
    up as a separate row and, when opened, masqueraded as a transcript). Each transcript
    instead carries `has_summary` so the reader can surface the saved summary."""
    sdir = _sessions_dir()
    names = {p.name for p in sdir.glob("*.md")}
    files = []
    for p in sdir.glob("*.md"):
        name = p.name
        # Hide derived summaries (the latest <stem>-summary.md and any archived
        # <stem>-summary-N.md), but only when the transcript actually exists, so a meeting whose
        # topic happens to end in "summary" is never wrongly suppressed.
        if name.endswith(".md"):
            base = name[:-3]
            stem = None
            if base.endswith("-summary"):
                stem = base[:-len("-summary")]
            else:
                i = base.rfind("-summary-")
                if i >= 0 and base[i + len("-summary-"):].isdigit():
                    stem = base[:i]
            if stem is not None and (stem + ".md") in names:
                # Confirm it really is a derived summary (its content starts with the summary
                # header) before hiding, so a real transcript that merely matches the name
                # pattern is never wrongly suppressed (that would look like data loss).
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        if fh.readline(64).startswith("# Summary:"):
                            continue
                except OSError:
                    pass
        try:
            st = p.stat()
        except OSError:
            continue
        meta = _parse_session_filename(name)
        files.append({
            "name": name,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "has_summary": (name[:-3] + "-summary.md") in names,
            **meta,
        })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return {"files": files, "folder": str(sdir)}


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
    summary_device: Optional[str] = None
    save_location: Optional[str] = None
    default_context: Optional[str] = None
    ai_backend: Optional[str] = None
    ai_instructions: Optional[list] = None
    active_instruction_id: Optional[str] = None
    setup_complete: Optional[bool] = None
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


@app.get("/api/cuda")
def cuda_status():
    """NVIDIA CUDA (optional GPU acceleration) status. NVIDIA ONLY; AMD/Intel GPUs use
    the CPU path. gpu_present = a CUDA device is visible; installed = the libs are on
    disk; ready = the GPU will actually be used (needs a restart after a fresh download)."""
    from .. import cudadl
    present = cudadl.gpu_present()
    return {
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


def _version_tuple(v):
    """Numeric version tuple for comparison. Takes the leading digits of each dotted part, so
    "1.1.1" -> (1,1,1) and "1.2.0-beta" -> (1,2,0); stops at a part with no leading digit."""
    parts = []
    for p in str(v or "").strip().lstrip("vV").split("."):
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


@app.post("/api/check-updates")
def check_updates():
    """Manual, user-initiated update check. Makes ONE outbound HTTPS GET to the PUBLIC GitHub
    releases API for the latest published version and compares it to this build. It sends no
    user data (only a generic User-Agent), runs only when the user clicks Check for updates, and
    is the single outbound call the app ever makes. CSRF-protected like every other POST."""
    import urllib.request
    import json as _json
    url = "https://api.github.com/repos/DigiPhyte-co/volksmond-releases/releases/latest"
    current = licensing.APP_VERSION
    try:
        rq = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Volksmond-update-check",
        })
        with urllib.request.urlopen(rq, timeout=8) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach the update server. Check your internet connection and try again.")
    latest = (data.get("tag_name") or "").strip().lstrip("vV")
    available = bool(latest) and _version_tuple(latest) > _version_tuple(current)
    return {
        "current": current,
        "latest": latest or None,
        "update_available": available,
        "url": data.get("html_url") or "https://github.com/DigiPhyte-co/volksmond-releases/releases/latest",
    }


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
        # Edition flag. The offline-only build (default) hides the online-feature UI:
        # the cloud-key danger zone and the in-app Pro pricing page. A future connected
        # build sets SA_LIVE_CONNECTED=1 to surface them. The actual cloud paths are not
        # built either way, so this only governs which UI the user can reach.
        "connected": os.environ.get("SA_LIVE_CONNECTED") == "1",
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


class SummariseRequest(BaseModel):
    file: str                      # session filename within the save location
    instruction: Optional[str] = None
    language: Optional[Literal["af", "en"]] = None  # output language for the summary


@app.post("/api/summarise")
def summarise_endpoint(req: SummariseRequest):
    """Summarise a finished transcript locally. Free: it runs on this machine, so
    it is never gated. Needs a summary model installed (chosen in Settings)."""
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

    transcript = target.read_text(encoding="utf-8")
    instruction = (req.instruction or config.active_instruction()) or None
    from .. import summarise as _summarise, cudadl

    # Decide where to run. GPU only when: the user has not forced CPU, an NVIDIA GPU is
    # present, this build's llama.cpp can offload (the CPU wheel cannot), and the model
    # fits in VRAM with headroom. Anything else stays on the CPU.
    device = (config.load().get("summary_device") or "auto").strip().lower()
    n_gpu_layers = 0
    if (device != "cpu" and _summarise.gpu_offload_supported() and cudadl.gpu_present()
            and _summarise.fits_on_gpu(model_path, cudadl.vram_mb())):
        n_gpu_layers = -1
    print(f"[summarise] device={device!r} offload={_summarise.gpu_offload_supported()} "
          f"gpu_present={cudadl.gpu_present()} vram={cudadl.vram_mb()} -> n_gpu_layers={n_gpu_layers}", flush=True)

    def _run(layers):
        s = _summarise.Summariser(model_path, n_gpu_layers=layers)
        return s.summarise(transcript, instruction=instruction, language=req.language)

    try:
        try:
            summary = _run(n_gpu_layers)
        except Exception as e:
            # A GPU run can fail (e.g. CUDA out of memory on a transcript with a big
            # KV cache). Fall back to the CPU rather than failing the summary outright.
            if n_gpu_layers != 0:
                print(f"[summarise] GPU run failed ({e}); retrying on CPU", flush=True)
                summary = _run(0)
            else:
                raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summarise failed: {e}")

    # Keep a history of summaries: the latest is always <stem>-summary.md (the reader loads that),
    # and any previous latest is archived as <stem>-summary-N.md (N = next free index) before the
    # new one is written, so regenerating never destroys an earlier summary.
    out = target.with_name(target.stem + "-summary.md")
    if out.exists():
        n = 1
        while out.with_name(f"{target.stem}-summary-{n}.md").exists():
            n += 1
        archive = out.with_name(f"{target.stem}-summary-{n}.md")
        try:
            out.rename(archive)
        except OSError:
            # Could not archive the previous summary (e.g. a file lock). Do NOT overwrite it and
            # lose it: save the NEW summary under the fresh archive name and leave the old latest
            # in place. Nothing is lost, and the user still sees this run's summary.
            try:
                archive.write_text(f"# Summary: {target.stem}\n\n{summary}\n", encoding="utf-8")
            except OSError as e:
                raise HTTPException(status_code=500, detail=f"Could not save the summary: {e}")
            return {"summary": summary, "saved": str(archive)}
    out.write_text(f"# Summary: {target.stem}\n\n{summary}\n", encoding="utf-8")
    return {"summary": summary, "saved": str(out)}


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
