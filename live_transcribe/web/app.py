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
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import capture, config, licensing, sinks, transcribe
from ..__main__ import default_chunk_seconds, pick_tier

app = FastAPI(title="SA-Live-Transcribe")
STATIC_DIR = Path(__file__).parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Serve styles.css / app.js (and any future assets) from the static folder.
# Localhost-only server; these are the app's own files, no user data.
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


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
        self.output_path: Optional[Path] = None
        self.language: Optional[str] = None
        self.running: bool = False
        self.recording: bool = False
        self.transcribing: bool = False
        self.source_kind: Optional[str] = None   # "live" | "file"
        self.stopping: bool = False  # True while draining the backlog after Stop

    def reset(self):
        self.engine = None
        self.capture = None
        self.recorder = None
        self.md_sink = None
        self.browser_sink = None
        self.started_at = None
        self.tier = None
        self.model = None
        self.output_path = None
        self.language = None
        self.running = False
        self.recording = False
        self.transcribing = False
        self.source_kind = None
        self.stopping = False


STATE = _State()


class StartRequest(BaseModel):
    topic: str = ""
    tier: str = "auto"            # "auto" | "gpu" | "cpu-strong" | "cpu-mid"
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


def _sessions_dir() -> Path:
    """Where transcripts and recordings are saved.

    User-configurable via the save_location setting. Falls back to the project
    sessions/ folder if unset or invalid. Validates the configured path is a real,
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
    p = PROJECT_ROOT / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _build_output_path(topic: str) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d-%H%M")
    return _sessions_dir() / f"{ts}-{_slugify(topic)}.md"


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
def status():
    with STATE.lock:
        if not STATE.running:
            return {"running": False, "stopping": False}
        resp = {
            "running": True,
            "stopping": STATE.stopping,
            "recording": STATE.recording,
            "transcribing": STATE.transcribing,
            "source_kind": STATE.source_kind,
            "tier": STATE.tier,
            "model": STATE.model,
            "language": STATE.language,
            "output_path": str(STATE.output_path) if STATE.output_path else None,
            "started_at": STATE.started_at.isoformat() if STATE.started_at else None,
        }
        if STATE.stopping and STATE.engine is not None:
            resp["pending"] = STATE.engine.pending()
        return resp


@app.get("/api/devices")
def devices_list():
    import pyaudiowpatch as pa
    p = pa.PyAudio()
    try:
        loopbacks = [
            {"index": info["index"], "name": info["name"], "rate": int(info["defaultSampleRate"])}
            for info in p.get_loopback_device_info_generator()
        ]
        try:
            default_lb = p.get_default_wasapi_loopback()
            default_lb_idx = default_lb["index"]
        except Exception:
            default_lb_idx = None

        mics = []
        try:
            default_in = p.get_default_input_device_info()
            default_in_idx = default_in["index"]
        except Exception:
            default_in_idx = None

        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0 and not info.get("isLoopbackDevice"):
                mics.append({
                    "index": info["index"],
                    "name": info["name"],
                    "rate": int(info["defaultSampleRate"]),
                })

        return {
            "loopbacks": loopbacks,
            "mics": mics,
            "default_loopback_index": default_lb_idx,
            "default_mic_index": default_in_idx,
        }
    finally:
        p.terminate()


def _resolve_tier_lang_prompt(req):
    """Shared start/file-import resolution of tier, language, and seeded prompt."""
    settings = config.load()
    req_tier = req.tier if (req.tier and req.tier != "auto") else None
    if req_tier is None and settings["tier"] != "auto":
        req_tier = settings["tier"]
    tier = pick_tier(req_tier)
    language = req.language if req.language else None  # "" -> None (auto-detect)
    parts = [p for p in (settings.get("default_context", "").strip(), req.prompt.strip()) if p]
    prompt = ", ".join(parts) or None
    return tier, language, prompt


@app.post("/api/start")
def start(req: StartRequest):
    with STATE.lock:
        if STATE.running:
            raise HTTPException(status_code=409, detail="Session already running")

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
        STATE.output_path = output_path
        STATE.language = (language or "auto") if transcribe_on else None
        STATE.source_kind = "live"
        STATE.running = True

        # Recorder is tapped BEFORE the engine, so the recording stays complete
        # even when transcription drops chunks under load. Flags are read live so a
        # three-way stop can switch either stream off without restarting capture.
        def feed(source, audio, t_start):
            if STATE.recording and STATE.recorder is not None:
                STATE.recorder.on_chunk(source, audio, t_start)
            if STATE.transcribing and STATE.engine is not None:
                STATE.engine.on_chunk(source, audio, t_start)

        cap = capture.AudioCapture(
            mic_device=req.mic_device,
            loopback_device=req.loopback_device,
            chunk_seconds=chunk_seconds,
            on_chunk=feed,
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
        tier, language, prompt = _resolve_tier_lang_prompt(req)
        chunk_seconds = default_chunk_seconds(tier)
        topic = req.topic or Path(files[0]).stem
        output_path = _build_output_path(topic)
        try:
            engine = transcribe.Engine(tier=tier, language=language, initial_prompt=prompt)
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
            for t_start, src, window in items:
                with STATE.lock:
                    aborting = STATE.stopping
                if aborting:
                    break
                engine.on_chunk(src, window, t_start)
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
            with STATE.lock:
                STATE.reset()

    threading.Thread(target=_run, daemon=True, name="file-transcribe").start()
    return {
        "tier": tier,
        "model": engine.model_name,
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

        # Narrow a partial stop to "all" when it would leave nothing running.
        if what == "transcription" and not STATE.recording:
            what = "all"
        elif what == "recording" and not STATE.transcribing:
            what = "all"

        if what == "recording":
            STATE.recording = False
            rec, STATE.recorder = STATE.recorder, None
            if rec is not None:
                try:
                    rec.close()
                except Exception:
                    pass
            return {"stopped": "recording", "recording": False,
                    "transcribing": STATE.transcribing, "output_path": out}

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
                with STATE.lock:
                    STATE.engine = None
                    STATE.md_sink = None
                    STATE.stopping = False  # recording carries on; session still running

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
        with STATE.lock:
            STATE.reset()

    threading.Thread(target=_drain_and_close, daemon=True, name="stop-drain").start()
    return {"stopping": True, "pending": pending, "output_path": out}


def _parse_session_filename(name: str) -> dict:
    """Extract a display-friendly topic from `2026-05-20-1430-topic-slug.md`."""
    stem = name[:-3] if name.endswith(".md") else name
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-(\d{4})-(.*)$", stem)
    if m:
        date_part, time_part, topic = m.group(1), m.group(2), m.group(3)
        return {
            "date": date_part,
            "time": f"{time_part[:2]}:{time_part[2:]}",
            "topic": topic.replace("-", " ") or "(session)",
        }
    return {"date": "", "time": "", "topic": stem}


@app.get("/api/sessions")
def sessions_list():
    """List session Markdown files in the active save location, newest first."""
    sdir = _sessions_dir()
    files = []
    for p in sdir.glob("*.md"):
        try:
            st = p.stat()
        except OSError:
            continue
        meta = _parse_session_filename(p.name)
        files.append({
            "name": p.name,
            "size": st.st_size,
            "mtime": st.st_mtime,
            **meta,
        })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return {"files": files, "folder": str(sdir)}


@app.get("/sessions/{filename}", response_class=PlainTextResponse)
def session_content(filename: str):
    """Serve a session file as plain text. Path-traversal-safe."""
    if "/" in filename or "\\" in filename or filename.startswith(".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
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
def open_folder():
    """Open the active save location in the OS file browser (Windows: Explorer)."""
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
    tier: Optional[str] = None
    save_location: Optional[str] = None
    default_context: Optional[str] = None
    ai_backend: Optional[str] = None
    ai_instructions: Optional[list] = None
    active_instruction_id: Optional[str] = None
    summary_model: Optional[str] = None
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
    """Which summary model is installed (the Whisper model is handled by the tier)."""
    return {
        "summary_model": config.load().get("summary_model") or "",
        "summary_installed": config.summary_model_path() is not None,
    }


@app.get("/api/app-info")
def app_info():
    """Light, non-sensitive facts for the footer and the bug-report mailto: the
    display name, version, OS string, and where files are saved. Nothing here
    leaves the machine unless the user chooses to send a bug report."""
    return {
        "name": "Volksmond",
        "version": licensing.APP_VERSION,
        "platform": platform.platform(),
        "save_dir": str(_sessions_dir()),
    }


@app.post("/api/pick")
def pick_path(kind: str = "file"):
    """Open a native OS picker on this machine and return the chosen absolute path.

    This is a local-only convenience: the server and the user are the same machine,
    so picking a path on disk is the right way to import a (possibly multi-GB) media
    file, rather than uploading bytes through the browser. tkinter is imported lazily
    so headless or test environments that never call this pay nothing. The UI falls
    back to a paste-a-path field if no dialog is available.

    Returns {"path": <absolute path> | None}; None when the user cancels.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        raise HTTPException(status_code=501, detail=f"No native file dialog on this machine: {e}")
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
        root.destroy()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not open the file dialog: {e}")
    return {"path": chosen or None}


class SummariseRequest(BaseModel):
    file: str                      # session filename within the save location
    instruction: Optional[str] = None


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
    if "/" in fn or "\\" in fn or fn.startswith(".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
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
    from .. import summarise as _summarise
    try:
        s = _summarise.Summariser(model_path)
        summary = s.summarise(transcript, instruction=instruction)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summarise failed: {e}")

    out = target.with_name(target.stem + "-summary.md")
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
