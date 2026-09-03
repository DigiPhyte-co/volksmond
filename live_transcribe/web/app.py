"""FastAPI web server, controls the transcription engine from a browser.

Singleton-state design: only one session at a time (live or file). The server
holds the engine, audio capture, recorder, and sinks; HTTP endpoints start/stop
the session and stream segments to the browser via Server-Sent Events.

A session can transcribe live, record live (off by default, POPIA), do both, or
transcribe an existing file. Transcripts and recordings save to the user's chosen
save_location (validated; falls back to a per-platform default folder, see
_sessions_dir).
"""
import asyncio
import collections
import json
import os
import platform
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import audioboost, buildflags, capture, config, licensing, paths, silencewatch, sinks, transcribe
from ..__main__ import default_chunk_seconds, pick_tier, resolve_tier, resolve_tier_engine

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
        # Latch: has recording EVER been active this session (a start-time recording OR a mid-session
        # record-from-here)? Stays True after a what="recording" stop, so a second record-from-here
        # cannot reuse the session stem and TRUNCATE the first <stem>.wav on its close. Surfaced as
        # /api/status "recording_started"; cleared only by reset() for a fresh session.
        self.recording_started: bool = False
        self.record_raw_mic: bool = False   # live AEC + recording: recorder takes the raw MIC_RAW,
                                             # engine takes the cleaned MIC, so the recording stays raw
        self.transcribing: bool = False
        self.source_kind: Optional[str] = None   # "live" | "file"
        self.stopping: bool = False  # True while draining the backlog after Stop
        # True once this session has been counted (see _bump_session_count). More than one
        # finalisation path can complete a session, so the "count it once" rule is an explicit
        # session-scoped flag rather than an assumption that the call sites never overlap.
        self.session_counted: bool = False
        # Sticky transcript/recording write error, surfaced via /api/status. Set
        # during finalisation and kept across reset() so the UI can show it after
        # the session ends; cleared when the next session starts.
        self.sink_error: Optional[str] = None
        # Non-fatal notice about the running (or just-finished) session, surfaced via
        # /api/status the same sticky way (e.g. "stereo interview requested but the file
        # is mono"). Cleared when the next session starts.
        self.notice: Optional[str] = None
        # Long-silence nudge (WP-9b). silence_nudge is the outstanding warning the UI
        # renders as a banner ({"minutes", "count", "at"}) or None; silence_watch is the
        # SilenceWatch the 1 Hz watcher thread ticks and the endpoint answers; silence_stop
        # is that thread's exit signal. All three are session-scoped, so reset() clears
        # them (and sets the event, so a watcher can never outlive its session).
        self.silence_nudge: Optional[dict] = None
        self.silence_watch = None
        self.silence_stop: Optional[threading.Event] = None
        # "Model struggling to keep up" nudge. struggle_nudge is the outstanding warning the UI
        # renders as a banner ({"old_size", "new_size", "recording"}) or None; it is set by the
        # engine's on_downgrade callback when a live CPU session auto-downgrades. struggle_notified
        # is the once-per-session latch: the Windows toast fires only on the first downgrade, and a
        # banner the user has dismissed is not re-raised by a later rung. Both session-scoped, so
        # reset() clears them.
        self.struggle_nudge: Optional[dict] = None
        self.struggle_notified: bool = False
        # t0-capture: capture (and recording, if on) start the instant Begin is clicked, while the
        # transcription model loads on a background thread. `preparing` is True from Begin until that
        # engine is ready (or errors); `prepare_error` carries a short model-load failure message for
        # the UI (None while healthy). `pending_audio` is the bounded, thread-safe hold for the
        # transcription copy of every chunk captured before the engine exists, drained into the
        # engine (in order, no drops) once it is ready. See _PendingAudio and _build_engine_async.
        self.preparing: bool = False
        self.prepare_error: Optional[str] = None
        self.pending_audio: Optional["_PendingAudio"] = None
        # Ready-state hardening (WP-1): model_ready is the AUTHORITATIVE "transcription is live" flag,
        # set to True the instant the engine is built AND engine.start() is called (phase-1 end), NOT
        # derived from STATE.engine (which stays None during the backlog drain, so deriving readiness
        # from it is the "stuck on preparing" bug). prepare_phase is a breadcrumb
        # ("downloading"|"loading"|"ready"|""); prepare is the live download/load progress dict the UI
        # polls (or None); prepare_args stashes the background-build arguments so /api/prepare/retry can
        # re-spawn the build after a bounded failure without restarting capture/recording.
        self.model_ready: bool = False
        self.prepare_phase: str = ""
        self.prepare: Optional[dict] = None
        self.prepare_args: Optional[tuple] = None
        # Stop-safety for the catch-up window (P1-3): while the background build has an engine that is
        # built + started but NOT yet published (STATE.engine stays None during the backlog drain), the
        # engine is reachable ONLY here, so /api/stop can drain it instead of losing the whole
        # transcript. Set at phase-1 end (before model_ready), nulled at publish/reset/abandon. build_thread
        # is the builder thread so a Stop can join it (guaranteeing it has released the engine) before
        # draining. Both session-scoped.
        self.preparing_engine: Optional[transcribe.Engine] = None
        self.build_thread: Optional[threading.Thread] = None

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
        self.recording_started = False
        self.record_raw_mic = False
        self.transcribing = False
        self.source_kind = None
        self.stopping = False
        self.session_counted = False
        # Signal BEFORE dropping the reference: reset() is the one call every finalisation
        # path makes, so this is what guarantees no silence watcher survives its session.
        if self.silence_stop is not None:
            self.silence_stop.set()
        self.silence_stop = None
        self.silence_watch = None
        self.silence_nudge = None
        self.struggle_nudge = None
        self.struggle_notified = False
        # t0-capture: clear the preparing flag, any load error, and drop the pending-audio hold so a
        # never-loaded model's buffer cannot outlive its session (and its RAM is freed at finalise).
        self.preparing = False
        self.prepare_error = None
        self.pending_audio = None
        # Ready-state hardening (WP-1): a fresh session is not ready and has no prepare progress.
        self.model_ready = False
        self.prepare_phase = ""
        self.prepare = None
        self.prepare_args = None
        self.preparing_engine = None
        self.build_thread = None


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


# t0-capture pending-audio buffer cap. Holds the TRANSCRIPTION copy of captured chunks while the
# model loads, so nothing between Begin and engine-ready is dropped. float32 @ 16 kHz is ~64 KB/s
# per source, ~128 KB/s for both -> ~115 MB per 15 minutes. We cap at 20 minutes of both-source
# audio (~154 MB) and drop the OLDEST chunk on overflow. RAM tradeoff: with recording on the same
# audio is already on disk from t0 (the recorder), so this buffer is only the transcription copy and
# only has to cover a realistic model load/download; an unbounded buffer against a model that never
# loads would OOM the app, so the cap is deliberate, not incidental.
_PENDING_MAX_SAMPLES = 16000 * 60 * 20 * 2   # 20 min of both-source 16 kHz float32 samples

# Bounded-failure thresholds for the background model prepare (WP-2). A first-run download that makes
# NO progress for PREPARE_DOWNLOAD_STALL_SECONDS is treated as stalled (dead connection / HuggingFace
# unreachable) and surfaced as a retryable error rather than an indefinite spinner - the exact MS Store
# 10.1.2.10 failure. PREPARE_LOAD_TIMEOUT_SECONDS bounds the model LOAD (a fast local_files_only cache
# hit once the files are present); a load that never returns is a retryable error too. Capture and
# recording keep running through either failure, so the audio is never lost.
PREPARE_DOWNLOAD_STALL_SECONDS = 60
# The no-write VERIFICATION tail of a download (checksum, extraction, antivirus scan) writes nothing to
# the cache dir for a while on a slow machine, so raw byte-growth stall detection would false-fire and
# fail a healthy download (P2-2). Once the bytes are essentially all on disk we are in that tail, so we
# allow a much longer grace before declaring a stall; a genuine mid-download stall (bytes far below
# total, flat) still fails at PREPARE_DOWNLOAD_STALL_SECONDS.
PREPARE_VERIFY_GRACE_SECONDS = 180
# Load budget, BY DEVICE (WP-1). The slow part of a load is not the constructor, it is the first
# inference: CTranslate2 materialises the weights lazily, so the real cost lands in the warm-up done
# inside the model build lock. Measured on a Ryzen 7 7700X: medium ~35 s, small ~16 s, base ~5 s. A
# laptop CPU is 2 to 4x slower, so a first medium load there is 70 to 140 s and a whole cold start
# (disk read on a slow SSD, antivirus, a busy machine) can be minutes. A CUDA or Metal load is a few
# seconds, so a long budget there would only hide a real hang. One flat 120 s therefore declared
# failure on a perfectly healthy CPU laptop that just needed another two minutes.
#
# The budget is the point at which we GIVE UP, not the point at which we tell the user it is slow:
# while the load thread is alive the prepare state stays "loading" with an elapsed counter, and on
# CPU a soft hint appears after PREPARE_LOAD_SLOW_HINT_SECONDS. See load_budget_seconds().
PREPARE_LOAD_TIMEOUT_SECONDS = 120        # CUDA / Metal (and the historical default)
PREPARE_LOAD_TIMEOUT_SECONDS_CPU = 600    # CPU: generous, because a healthy first load is minutes
PREPARE_LOAD_SLOW_HINT_SECONDS = 60       # CPU only: "first load can take a few minutes" hint
# How often the load watchdog wakes to publish the elapsed counter into STATE.prepare. The UI polls
# /api/status about every 1.5 s, so one second is fine and costs one short lock hold per tick.
_PREPARE_LOAD_POLL_SECONDS = 1.0
# A DIFFERENT model may already own the single global download slot (a Settings download). We wait for
# it rather than fight, but only for a bounded time: an indefinitely-occupied slot would starve this
# session forever (another "loads indefinitely"), so past this we surface a distinct retryable error and
# let Retry pick up once the slot is free (new P2).
PREPARE_FOREIGN_SLOT_TIMEOUT_SECONDS = 180
# How often the background builder polls voicedl.progress() into STATE.prepare while downloading.
_PREPARE_POLL_SECONDS = 0.5


def load_device_for(tier):
    """The backend a tier loads on: "cpu", "cuda" or "mlx". Unknown tiers are treated as CPU, which
    is the conservative answer (the generous budget, and pre-warm skips nothing it should warm)."""
    try:
        return (transcribe.TIER_CONFIG.get(tier) or {}).get("device") or "cpu"
    except Exception:
        return "cpu"


def load_budget_seconds(tier=None, device=None):
    """How long the model LOAD may run before it is called a failure, for this tier/device.

    Pass a tier (the usual case) or a device directly. One function so the CPU/GPU split lives in
    exactly one place and the numbers are testable without building a model."""
    dev = device or load_device_for(tier)
    return PREPARE_LOAD_TIMEOUT_SECONDS_CPU if dev == "cpu" else PREPARE_LOAD_TIMEOUT_SECONDS

# Human-readable quality label per model size, mirroring the picker's four tiers (voicedl._OFFER):
# Fast=small, Balanced=medium, High quality=large-v3-turbo, Best=large-v3. base/tiny are internal
# live-downgrade rungs; they only appear here as a fallback label for a resolved cpu-min start.
_QUALITY_LABEL = {
    "small": "Fast", "medium": "Balanced",
    "large-v3-turbo": "High quality", "large-v3": "Best",
    "base": "Basic", "tiny": "Basic",
}


class _PendingAudio:
    """Thread-safe, bounded, drop-oldest hold for transcription chunks captured before the engine is
    published (t0-capture). _feed appends here from the capture/chunker threads for as long as a live
    session is still catching up; the builder thread (_build_engine_async) drains it, in order, into
    the engine via engine.on_chunk(block=True) so the replay never drops on the engine's maxsize queue.

    The buffer is the SOLE channel to the engine until the backlog is fully caught up: the builder
    keeps STATE.engine None while it drains with take_all() (which leaves the buffer OPEN), so a
    concurrent _feed keeps APPENDING live chunks behind the backlog instead of racing ahead of it into
    the engine. Only when a drain pass finds the buffer empty does finalise_if_empty() close it and
    publish the engine, both under this buffer's lock, so no _feed.append can slip through the seam.

    Bounded by total float32 samples (see _PENDING_MAX_SAMPLES); see that comment for the RAM
    tradeoff. Its own lock makes append (many producer threads) and the drain/finalise handoff atomic."""

    def __init__(self, max_samples):
        self._buf = collections.deque()
        self._samples = 0
        self._max = max_samples
        self._lock = threading.Lock()
        self._closed = False
        self._warned = False
        # Count of chunks at the FRONT protected from cap eviction (P2-c): set by putback_front (the
        # hand-off/retry tail that must survive), cleared once the buffer is drained (take_all /
        # finalise_if_empty). While it is >0, cap enforcement drops the NEWEST (right) chunk, never the
        # protected front; while it is 0 (the common case) append() drops the OLDEST exactly as before.
        self._protected = 0
        # Span of audio EVICTED at the cap, so a drop can be admitted in the transcript instead of
        # only in the console log (WP-1 no-silent-loss). Earliest t_start and latest chunk end seen
        # across every eviction; the SPAN is the honest number because MIC and SYS overlap in time.
        self._dropped_lo = None
        self._dropped_hi = None

    def _note_dropped(self, audio, t_start):
        """Record the time span of one evicted chunk (called under the lock)."""
        try:
            dur = len(audio) / 16000.0
        except TypeError:
            dur = 0.0
        try:
            t0 = float(t_start)
        except (TypeError, ValueError):
            return
        self._dropped_lo = t0 if self._dropped_lo is None else min(self._dropped_lo, t0)
        hi = t0 + dur
        self._dropped_hi = hi if self._dropped_hi is None else max(self._dropped_hi, hi)

    def dropped_span(self):
        """(t_start, seconds) of the audio evicted at the cap, or (None, 0.0) if nothing was."""
        with self._lock:
            if self._dropped_lo is None:
                return None, 0.0
            return self._dropped_lo, max(0.0, (self._dropped_hi or self._dropped_lo) - self._dropped_lo)

    def clear_dropped(self):
        """Forget the evicted span once it has been reported, so a later build (a retry after a
        catch-up failure reuses this same buffer) cannot write the same gap line twice."""
        with self._lock:
            self._dropped_lo = None
            self._dropped_hi = None

    def held_span(self):
        """(t_start, seconds) covered by the audio still held, or (None, 0.0) when empty. Used to
        state plainly in the transcript how much was never transcribed live when a session is
        stopped before the model ever finished loading."""
        with self._lock:
            if not self._buf:
                return None, 0.0
            lo = hi = None
            for _s, audio, t_start in self._buf:
                try:
                    t0 = float(t_start)
                except (TypeError, ValueError):
                    continue
                try:
                    dur = len(audio) / 16000.0
                except TypeError:
                    dur = 0.0
                lo = t0 if lo is None else min(lo, t0)
                hi = (t0 + dur) if hi is None else max(hi, t0 + dur)
            if lo is None:
                return None, 0.0
            return lo, max(0.0, (hi or lo) - lo)

    def _warn_once(self, newest):
        if self._warned:
            return
        self._warned = True
        which = "NEWEST" if newest else "oldest"
        why = ("to preserve the earlier hand-off backlog " if newest else "")
        print(f"[start] pending-audio buffer full while the model loads; dropping the {which} held "
              f"transcription chunk {why}(recording, if on, is unaffected).", flush=True)

    def _enforce_cap(self):
        """Trim to the sample cap under the lock. With a protected front (a putback_front tail), evict the
        NEWEST (right) and NEVER into the protected prefix - if the protected prefix alone already fills
        the cap, the just-appended newest chunk is the one dropped. With no protection, drop the OLDEST,
        exactly as the original bounded-drop-oldest behaviour (pinned by tests)."""
        if self._protected > 0:
            while self._samples > self._max and len(self._buf) > self._protected:
                _s, new_audio, _t = self._buf.pop()          # newest (right)
                try:
                    self._samples -= len(new_audio)
                except TypeError:
                    pass
                self._note_dropped(new_audio, _t)
                self._warn_once(newest=True)
        else:
            while self._samples > self._max and len(self._buf) > 1:
                _s, old_audio, _t = self._buf.popleft()      # oldest (left)
                try:
                    self._samples -= len(old_audio)
                except TypeError:
                    pass
                self._note_dropped(old_audio, _t)
                self._warn_once(newest=False)

    def append(self, source, audio, t_start):
        """Hold a chunk. Returns True if buffered, False once the buffer has been closed at the final
        handoff (the caller then feeds the now-published engine directly, so nothing slips the seam)."""
        try:
            n = len(audio)
        except TypeError:
            n = 0   # a non-sized stub (only ever in tests): count it as weightless
        with self._lock:
            if self._closed:
                return False
            self._buf.append((source, audio, t_start))
            self._samples += n
            self._enforce_cap()
            return True

    def take_all(self):
        """Return everything currently held, in order, and clear it, LEAVING THE BUFFER OPEN so a
        concurrent _feed keeps appending live chunks behind the backlog (never dropped, never
        reordered). The drain loop replays each batch, then loops, until a batch comes back empty and
        finalise_if_empty publishes the engine. Draining clears any protected-front marker (P2-c)."""
        with self._lock:
            items = list(self._buf)
            self._buf.clear()
            self._samples = 0
            self._protected = 0
            return items

    def putback_front(self, items):
        """Return an unsubmitted batch to the FRONT of the buffer, in order, so it drains again AHEAD of
        any later chunks. Used at the stop hand-off and after a catch-up failure (P1-3/P1-6): the builder
        puts its not-yet-enqueued tail back here (earliest t_start) so a stop-drain or a retry replays it
        before the chunks that arrived behind it, with no reorder and no loss.

        Cap enforcement (P2-c): the caller does NOT always drain immediately (a catch-up failure leaves
        the session in an error/retry state, still buffering), so the returned older tail plus the later
        pending audio can exceed the cap. The returned tail is the resume point a retry needs, so it is
        marked PROTECTED: this putback and every SUBSEQUENT append() evict the NEWEST (rightmost) chunk
        over the cap, never the protected front. The protection is cleared once the buffer is drained
        (take_all / finalise_if_empty). A no-op once the buffer is closed."""
        if not items:
            return
        with self._lock:
            if self._closed:
                return
            for it in reversed(items):
                self._buf.appendleft(it)
                try:
                    self._samples += len(it[1])
                except TypeError:
                    pass
            # All prepended chunks sit at the front and must survive later appends until drained.
            self._protected = min(self._protected + len(items), len(self._buf))
            self._enforce_cap()

    def finalise_if_empty(self, on_close):
        """The atomic buffer->engine flip. Under the buffer's lock: if the buffer is EMPTY, close it
        and run on_close() (which publishes the engine) BEFORE releasing the lock, then return None;
        if it is NOT empty, return the current items (leaving the buffer OPEN) for the caller to replay
        and loop. Because the close and the publish both happen under the same lock a _feed.append
        takes, no chunk can slip in between 'closed' and 'engine published': a chunk lands either
        before (replayed in the next take) or after (fed to the published engine directly, newest).
        Either way the buffer is emptied/sealed, so the protected-front marker is cleared (P2-c).
        Returns None when finalised, or the straggler list (not finalised)."""
        with self._lock:
            self._protected = 0
            if self._buf:
                items = list(self._buf)
                self._buf.clear()
                self._samples = 0
                return items
            self._closed = True
            on_close()
            return None


def _engine_alive(engine):
    """True while the engine's transcription worker is running. Defensive so the stubbed engines in the
    test suite (which have no is_alive) count as alive, and the real Engine's dead worker is caught."""
    try:
        return bool(engine.is_alive())
    except Exception:
        return True


# --- one model load in flight per build key (WP-1) --------------------------------------------
# A prepare that finds a load already running for its exact build key ATTACHES to it: same thread,
# same result. Before this, a Retry after a load timeout started a SECOND Engine, which could only
# sit on transcribe._BUILD_LOCK until the first one finished and then take the cache hit, so the
# user paid the whole first load again in wall-clock time before anything was transcribed. An
# already-FINISHED load whose engine nobody claimed is also attachable, so a Retry clicked after a
# slow load quietly completed gets that engine instantly instead of building another.
# A FAILED load is never attachable: Retry must genuinely try again.
_LOAD_LOCK = threading.Lock()
_LOAD_INFLIGHT = {}     # key -> {"thread": Thread, "result": dict, "started": monotonic}


def _load_in_flight(key):
    """Is a load for `key` still running, or finished with an engine nobody has claimed? Small,
    read-only view of the registry for tests and callers that only want the fact."""
    with _LOAD_LOCK:
        rec = _LOAD_INFLIGHT.get(key)
        if rec is None:
            return False
        return bool(rec["thread"].is_alive() or "engine" in rec["result"])


def _start_or_attach_load(key, make_engine):
    """Return (thread, result, started_at, attached) for the model load of `key`.

    Starts a load only when there is not already a usable one: a live thread, or a finished one
    whose engine is still unclaimed. `result` is filled with "engine" or "error" by the thread.
    `started_at` is the ORIGINAL start, so an attached caller reports the true elapsed time."""
    with _LOAD_LOCK:
        rec = _LOAD_INFLIGHT.get(key)
        if rec is not None and (rec["thread"].is_alive() or "engine" in rec["result"]):
            return rec["thread"], rec["result"], rec["started"], True
        result = {}

        def _load():
            try:
                result["engine"] = make_engine()
            except Exception as e:   # surfaced as prepare_error by the caller; never crashes the app
                result["error"] = e

        t = threading.Thread(target=_load, daemon=True, name="engine-load")
        _LOAD_INFLIGHT[key] = {"thread": t, "result": result, "started": time.monotonic()}
        t.start()
        return t, result, _LOAD_INFLIGHT[key]["started"], False


def _clear_load(key, result):
    """Forget the in-flight record for `key`, but only if it is still the one that produced
    `result` (identity-guarded, so a newer load started meanwhile is never dropped)."""
    with _LOAD_LOCK:
        rec = _LOAD_INFLIGHT.get(key)
        if rec is not None and rec["result"] is result:
            _LOAD_INFLIGHT.pop(key, None)


def _reset_loads():
    """Forget every in-flight load record. For tests, which reuse one process and must not let one
    case's abandoned fake engine be attached to by the next."""
    with _LOAD_LOCK:
        _LOAD_INFLIGHT.clear()


def _fmt_gap(seconds):
    """A duration in the plain style the app uses elsewhere: "47 s", "10 min", "5 min 27 s"."""
    s = int(round(max(0.0, float(seconds))))
    if s < 60:
        return f"{s} s"
    if s % 60 == 0:
        return f"{s // 60} min"
    return f"{s // 60} min {s % 60} s"


def _note_untranscribed(md_sink, browser_sink, t_start, seconds, recording):
    """Write ONE honest line into the transcript where live transcription did not happen.

    Silence in a transcript reads as silence in the room, which is the one thing the app must never
    imply. Every path that gives up on held audio (a stop before the model ever loaded, an eviction
    at the pending-buffer cap) calls this, so a gap is always stated rather than left blank. Written
    straight to the sinks in the same shape and voice as the engine's own notices (see
    transcribe._emit_notice); the recorder is untouched, so when recording is on the audio itself is
    still on disk and can be transcribed afterwards."""
    if seconds is None or seconds < 1.0:
        return   # sub-second rounding noise is not a gap worth a line
    tail = ("the recording still has them" if recording
            else "there is no recording of them")
    seg = transcribe.Segment(
        source="SYS", t_start=float(t_start or 0.0), t_end=float(t_start or 0.0),
        text=f"[engine: {_fmt_gap(seconds)} before the model loaded were not transcribed live, {tail}]")
    for sink in (md_sink, browser_sink):
        if sink is None:
            continue
        try:
            sink(seg)
        except Exception as e:
            print(f"[start] could not record the untranscribed-audio notice: {e}", flush=True)


def _mark_abandoned_backlog(md_sink, browser_sink, pb, recording):
    """The pending buffer is about to be thrown away with no engine to replay it into (a Stop while
    the model was still loading). Say so in the transcript instead of leaving a silent hole."""
    if pb is None:
        return
    t_start, seconds = pb.held_span()
    if seconds <= 0:
        return
    print(f"[start] {_fmt_gap(seconds)} of held audio was never transcribed (stopped before the "
          f"model finished loading); noting the gap in the transcript.", flush=True)
    _note_untranscribed(md_sink, browser_sink, t_start, seconds, recording)


def _mark_dropped_backlog(md_sink, browser_sink, pb, recording):
    """Some held audio was evicted at the pending-buffer cap while the model loaded. The replay can
    never bring it back, so state the gap once, at the point the engine goes live."""
    if pb is None:
        return
    t_start, seconds = pb.dropped_span()
    if seconds <= 0:
        return
    pb.clear_dropped()      # reported once, never twice (a retry reuses this same buffer)
    _note_untranscribed(md_sink, browser_sink, t_start, seconds, recording)


def _drain_pending_into_engine(engine, pb):
    """Feed everything still held in the pending-audio buffer into `engine`, in order, so a Stop (or a
    partial 'stop transcription') during catch-up saves the whole transcript instead of discarding the
    backlog (P1-3). block=True so it never drops on the maxsize queue; bails only if the worker dies. The
    caller then calls engine.stop(drain=True) to flush the queue into the sink. MUST be called only after
    the background builder has been joined, so this is the SOLE feeder of the engine (no double-delivery,
    no reorder)."""
    if pb is None or engine is None:
        return
    while True:
        items = pb.take_all()
        if not items:
            return
        for (src, audio, t_start) in items:
            while not engine.on_chunk(src, audio, t_start, block=True, timeout=0.5):
                if not _engine_alive(engine):
                    return


def _feed(source, audio, t_start):
    """Route a captured chunk to the recorder and/or the engine, honouring the live flags.

    Tapped before the engine so a recording stays complete even if transcription drops
    chunks under load. Module-level (not a closure) so /api/switch-device can rebuild the
    capture with the same feed without re-deriving it; the flags and targets are read live
    off STATE, so a three-way stop or a device switch is picked up without rewiring.

    Live AEC + recording (STATE.record_raw_mic): capture emits the RAW mic on a "MIC_RAW" source for
    the recorder (saved as the -MIC channel) and the cleaned mic on "MIC" for the engine, so the
    recording stays raw while the live transcript still benefits from echo cancellation.

    t0-capture: while a live transcription session is still loading its model (STATE.preparing, engine
    not yet built), the engine-bound chunk is HELD in STATE.pending_audio instead of dropped, so
    transcription can start from t0 once the model is ready. The recorder path above is unchanged, so
    recording is already on disk from t0."""
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
        return
    # Engine not live at entry. A live transcription session still loading its model holds the chunk
    # (t0-capture) so transcription starts from t0 once ready, instead of dropping it as before.
    if not STATE.transcribing:
        return   # record-only (or nothing to transcribe): nothing else to do
    pb = STATE.pending_audio
    # Buffer-OPEN is the gate, not STATE.preparing (WP-1): the ready flip clears `preparing` at
    # phase-1 end while the engine is still None and the buffer is still open for the backlog drain,
    # so gating on `preparing` here could drop a chunk in that window. pending_audio is set to None
    # only at the atomic engine-publish (finalise_if_empty) and in reset()/the failure path, so
    # "pb is not None and append() succeeded" is exactly "still catching up, hold this chunk".
    if pb is not None and pb.append(source, audio, t_start):
        return   # held in the buffer; the builder replays it in order once the engine is ready
    # Not buffered (buffer closed at the handoff instant, or the engine came up between our reads):
    # feed the engine directly if it is live now, so nothing slips through the preparing->ready seam.
    eng2 = STATE.engine
    if eng2 is not None:
        eng2.on_chunk(source, audio, t_start)


# --- long-silence nudge (WP-9b) ------------------------------------------------
# A live session that hears NOTHING for minutes is almost always broken rather than quiet:
# Windows moved the default mic to a headset in a drawer, the meeting app took the device
# exclusively, or the mic is muted in the OS mixer. The app looks busy the whole time, so
# the user finds out an hour later. This watcher says it once, in the app and (on Windows)
# as a desktop notification, and offers to stop and save.
#
# Signal: the WP-4 raw energy rings on the ENGINE (mic_env / sys_env), which are fed per
# 100 ms frame from the RAW pre-APM capture blocks. Deliberately NOT capture.levels() /
# /api/levels: the mic side of those is POST-AGC, and live AGC lifts an empty room toward
# -25 dBFS, so a silence test there would never fire. Reading the rings also means zero
# change to capture_core.py.
SILENCE_ENV = "SA_LIVE_SILENCE_NUDGE"
SILENCE_TICK_S = 1.0        # watcher cadence
SILENCE_LOOKBACK_S = 2.0    # ring window each tick: wider than the tick so a late 0.5 s
                            # block, or a tick that drifts under load, cannot read as silence


def _silence_env_on() -> bool:
    """False when SA_LIVE_SILENCE_NUDGE is set to 0/false/no/off: the hard kill switch,
    checked before anything else so a support session can turn the whole feature off."""
    return (os.environ.get(SILENCE_ENV, "") or "").strip().lower() not in ("0", "false", "no", "off")


def _silence_settings():
    """(on, threshold_s) for this session, from the env kill switch plus settings.

    The minutes value is clamped rather than restricted to the picker's 3/5/10/15, so a
    hand-edited settings file gets what it asked for as long as it is sane."""
    if not _silence_env_on():
        return False, 0.0
    try:
        cfg = config.load()
    except Exception:
        return True, 300.0
    on = cfg.get("silence_nudge", True) is not False
    try:
        mins = int(cfg.get("silence_nudge_minutes", 5) or 5)
    except (TypeError, ValueError):
        mins = 5
    mins = max(1, min(120, mins))
    return on, float(mins * 60)


def _silence_tick(engine, watch, now):
    """One watcher tick: read the rings, feed the watch, and on a trip publish the nudge.

    Split out of the loop and given its arguments explicitly so the whole decision path is
    driveable from a test with a fake engine and a hand-wound clock, no threads involved.
    Returns the published nudge dict, or None.

    `now` MUST be on the SESSION clock (time.monotonic() - capture._t0), because that is
    the clock the rings are timestamped on (capture_core._ingest_block). Mixing in a wall
    clock would query a window the rings never had frames in, and every tick would read as
    silence.
    """
    if engine is None or watch is None or now is None:
        return None
    levels = {}
    for name, attr in (("MIC", "mic_env"), ("SYS", "sys_env")):
        ring = getattr(engine, attr, None)
        if ring is None:
            continue          # this channel has no ring: not measurable, so not a source
        try:
            levels[name] = ring.max_db(now - SILENCE_LOOKBACK_S, now)
        except Exception:
            levels[name] = None
    try:
        tripped = watch.sample(now, levels)
    except Exception:
        return None
    if not tripped:
        return None
    st = watch.state()
    minutes = st.get("minutes") or silencewatch.minutes_of(watch.threshold_s)
    nudge = {"minutes": minutes, "count": st.get("nudges", 1),
             "at": datetime.now().isoformat(timespec="seconds")}
    with STATE.lock:
        # A session that is already finishing must never be nudged: the audio has stopped
        # on purpose, and the user is watching the drain.
        if STATE.stopping:
            return None
        # Re-check the watch itself, under the lock, immediately before publishing. sample()
        # decided to trip a few microseconds ago on the watcher thread; in that gap the request
        # thread can have answered the banner (mute) or the session can have been replaced by a
        # device switch or a new start. Publishing then puts a nudge on screen the user has
        # already dismissed, or attributes one session's silence to another. SilenceWatch's own
        # lock makes each transition atomic; this is the second half of the same problem, and it
        # has to be settled here because only STATE knows which watch is current.
        if STATE.silence_watch is not watch:
            return None
        if watch.state().get("muted"):
            return None
        STATE.silence_nudge = nudge
    # Best-effort desktop notification: notify.show() is a no-op when os_toasts is off, on
    # a non-Windows machine or without pywin32, and never raises. Clicking it brings the
    # window forward, where the banner is already waiting.
    from .. import notify
    notify.show(f"Nothing heard for {minutes} minutes",
                "Volksmond is still recording, but the microphone and the system audio have "
                "both been silent. Check your device, or stop and save.",
                tag="silence", on_click=notify.focus_app)
    return nudge


def _silence_loop(stop, t0):
    """The 1 Hz watcher thread. Exits within one tick of the session ending.

    Everything except the session's t0 is re-read from STATE each tick (the same posture as
    _feed), so a device switch, a mid-meeting /api/reconfigure or a mute/snooze needs no
    rebinding here: whatever rings the engine currently holds are the ones read. t0 is the
    exception because it is fixed for the session, and is threaded through a device switch
    on purpose (see switch_device) - so the clock survives even a switch that ends with no
    capture at all, which is exactly the dead-capture case this watcher must still catch.
    """
    while not stop.wait(SILENCE_TICK_S):
        with STATE.lock:
            if not STATE.running or STATE.stopping or STATE.source_kind != "live":
                return
            engine = STATE.engine
            watch = STATE.silence_watch
        if watch is None:
            return
        try:
            _silence_tick(engine, watch, time.monotonic() - t0)
        except Exception:
            pass          # a watchdog must never take the session down with it


def _silence_start(cap):
    """Arm the watcher for a live session. Returns the thread, or None when it is off.

    Called at the very end of /api/start's LIVE branch (with STATE.lock held) and nowhere
    else: file transcription never gets a watcher, because a file that goes quiet is just a
    quiet file.
    """
    on, threshold_s = _silence_settings()
    if not on:
        return None
    # Record-only (no engine) has no energy rings, because the rings live on the engine and are
    # fed by the capture callback for it. A watcher started here would read an empty levels dict
    # every tick, which sample() correctly treats as absence of evidence rather than silence: it
    # could never trip, but /api/status would report an armed watch and the feature would look
    # like it was covering a session it cannot see. Say so once and stay out. Giving record-only
    # real coverage means moving the rings off the engine (or feeding a second pair from
    # capture_core); that is future work, deliberately not smuggled in behind a nudge.
    if STATE.engine is None:
        print("[silence] watcher not started (record-only session has no energy rings)", flush=True)
        return None
    t0 = getattr(cap, "_t0", None)
    if t0 is None:
        return None       # no session clock, so no honest ring query: stay quiet
    watch = silencewatch.SilenceWatch(threshold_s=threshold_s)
    stop = threading.Event()
    STATE.silence_watch = watch
    STATE.silence_stop = stop
    STATE.silence_nudge = None
    th = threading.Thread(target=_silence_loop, args=(stop, t0), daemon=True, name="silence-watch")
    th.start()
    return th


def _silence_after_switch():
    """Restart the silence clock after a live device switch. Caller holds STATE.lock.

    Two reasons, both real: a switch tears the capture down for about a second (a gap the
    rings genuinely have no frames for), and changing the mic IS the fix the nudge asks
    for, so an outstanding warning should clear rather than sit there contradicting the
    user's action. The nudge COUNT is untouched, so the per-session cap still holds.

    Note what is deliberately NOT here: rebinding the watcher to the engine's rings.
    _silence_loop re-reads STATE.engine every tick, so it already follows whatever rings
    the engine holds (the switch re-attaches the SAME ring objects anyway), and the session
    clock it uses is the t0 threaded through the rebuild.
    """
    watch = STATE.silence_watch
    STATE.silence_nudge = None
    if watch is None:
        return
    t0 = getattr(STATE.capture, "_t0", None)
    try:
        # No usable t0 (a failed switch can leave no capture at all): snooze() with no
        # argument restarts from the last SAMPLED clock value, which is always honest.
        watch.snooze(None if t0 is None else time.monotonic() - t0)
    except Exception:
        pass


def _silence_signal():
    """Tell the watcher to exit now and forget it. Caller holds STATE.lock.

    reset() does this too, but reset happens after the drain, which can take a while; a
    stop should not leave a watcher ticking over a session that is finishing."""
    ev, STATE.silence_stop = STATE.silence_stop, None
    STATE.silence_watch = None
    STATE.silence_nudge = None
    if ev is not None:
        ev.set()


# --- "model struggling to keep up" nudge --------------------------------------
# When a live CPU session auto-downgrades (transcribe.Engine._maybe_downgrade, ladder
# medium->small->base->tiny) because it cannot hold real time, the transcription silently gets
# rougher. Surface it: a one-time banner (STATE.struggle_nudge, polled via /api/status) plus a
# single Windows toast, with the offer to start recording so the meeting can be re-transcribed at
# full accuracy afterwards. The downgrade ITSELF always happens; this only makes it visible, and
# only for a CPU + adaptive(live) + transcribing session (a GPU tier never downgrades, Swivuriso is
# a single fixed model, a record-only session has no engine). Data integrity, not a Business
# nicety, so the toast fires ungated like the silence one, never through /api/notify-meeting.
STRUGGLE_ENV = "SA_LIVE_STRUGGLE_NUDGE"


def _struggle_env_on() -> bool:
    """False when SA_LIVE_STRUGGLE_NUDGE is set to 0/false/no/off: the hard kill switch, checked
    before the setting so a support session can turn the whole surfacing off in one place."""
    return (os.environ.get(STRUGGLE_ENV, "") or "").strip().lower() not in ("0", "false", "no", "off")


def _struggle_nudge_on() -> bool:
    """True when the struggle-nudge SURFACING (banner + toast) is enabled: the env kill switch plus
    the struggle_nudge setting (default on). The auto-downgrade is unaffected either way."""
    if not _struggle_env_on():
        return False
    try:
        return config.load().get("struggle_nudge", True) is not False
    except Exception:
        return True


def _on_downgrade(engine, old_size, new_size):
    """Engine.on_downgrade callback: surface a CPU auto-downgrade. Runs on the TRANSCRIPTION
    WORKER thread (transcribe.Engine._maybe_downgrade), so it takes STATE.lock and guards the
    session's identity exactly like _silence_tick before touching STATE, then fires the toast
    outside the lock (best-effort, never raises). Returns the published nudge dict, or None.

    Once per session: the FIRST downgrade sets the banner and fires the toast; a later rung UPDATES
    the banner's new_size in place (keeping the original old_size, the full-quality model the
    session began degrading from) but never re-fires the toast, and a banner the user has already
    dismissed is not re-raised. `recording` is captured at emit time (STATE.recording), so the
    frontend can drop the record offer when the session is already recording."""
    if not _struggle_nudge_on():
        return None
    published = None
    fire_toast = False
    with STATE.lock:
        # A session that is finishing must never be nudged (its audio has stopped on purpose), and
        # a callback from an engine that is no longer the session's (a stop/switch mid-drain, or a
        # new session) must not publish onto the current one.
        if STATE.stopping or STATE.engine is not engine:
            return None
        # Already surfaced once this session and the user dismissed it: do not nag again. A banner
        # still on screen falls through and is updated in place below.
        if STATE.struggle_notified and STATE.struggle_nudge is None:
            return None
        prior = STATE.struggle_nudge
        STATE.struggle_nudge = {
            "old_size": prior["old_size"] if prior else old_size,
            "new_size": new_size,
            "recording": STATE.recording,
        }
        published = STATE.struggle_nudge
        fire_toast = not STATE.struggle_notified
        STATE.struggle_notified = True
    if fire_toast:
        # Best-effort desktop toast: a no-op when os_toasts is off, on a non-Windows machine or
        # without pywin32, and never raises. Clicking it brings the window forward, where the
        # banner (with the actions) is already waiting. NOT routed through /api/notify-meeting,
        # which is Business-gated; this mirrors the silence watcher, called directly and ungated.
        from .. import notify
        notify.show("Volksmond switched to a faster model",
                    "Your computer can't transcribe this meeting at full quality in real time, so "
                    "Volksmond stepped down to a faster model to keep up. It is still running. Open "
                    "Volksmond to record the audio and re-transcribe at full accuracy later.",
                    tag="struggle", on_click=notify.focus_app)
    return published


class StartRequest(BaseModel):
    topic: str = ""
    tier: str = "auto"            # "auto" | "gpu" | "cpu-strong" | "cpu-mid"
    device: str = "auto"          # "auto"/"gpu" use the GPU when ready; "cpu" forces CPU
    language: str = "af"          # "af" | "en" | "sa" (SA group) | a code like "zu"/"de" | "" (empty == auto-detect)
    engine: str = "auto"          # model family: "auto" (by language) | "fluister" | "whisper"
    prompt: str = ""
    # Per-meeting override of the saved default_context. None -> use settings.default_context;
    # a string (including "") -> use it verbatim for THIS run only, never persisted to settings.
    context_override: Optional[str] = None
    mic_device: Optional[str] = None
    loopback_device: Optional[str] = None
    record: bool = False          # also save the audio (POPIA: needs consent)
    transcribe: bool = True       # False == record-only (for machines too slow to keep up live)
    aec_live: Optional[bool] = None  # live echo cancellation (None -> settings default)
    agc_live: Optional[bool] = None  # live mic auto-gain (None -> settings default)


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
    invalid to the project sessions/ folder in dev, or the per-platform default
    when frozen (%USERPROFILE%\\Volksmond on Windows, the data dir elsewhere; see
    paths.default_sessions_dir_for). Validates the configured path is a real,
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
    # PROJECT_ROOT points INSIDE the PyInstaller bundle, so use a persistent
    # user folder (on Windows a visible one the user can find, that survives
    # uninstall and is not cloud-synced by default) instead - otherwise
    # transcripts bury inside the app and vanish on reinstall.
    if getattr(sys, "frozen", False):
        p = paths.default_sessions_dir()
    else:
        p = PROJECT_ROOT / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pin_save_location_on_upgrade() -> None:
    """One-time pin so the moved Windows default never hides existing transcripts.

    The frozen Windows default moved from %LOCALAPPDATA%\\sa-live-transcribe\\sessions
    to %USERPROFILE%\\Volksmond (new installs only). An upgraded install with
    transcripts in the old default and save_location unset would suddenly list an
    empty History, so pin the old folder as an explicit save_location once. Reads
    the raw setting, never the resolved _sessions_dir (which cannot express
    "unset"). Only the old folder's CONTENTS decide: _sessions_dir has always
    mkdir'd eagerly, so the folder itself exists on every install, empty or not.
    Any entry counts, not just files: transcripts, recordings and notes sidecars
    all live flat in that folder, and a subfolder there is user-created and worth
    keeping visible too. Gated to frozen Windows because the default moved
    nowhere else; a failure must never stop the app.

    Runs once EVER, not once per launch: the save_location_migrated sentinel is
    written on the first evaluation, so a user who later clears save_location to
    adopt the new default is never re-pinned to the old folder. The pin and the
    sentinel land in one config.update() call, one atomic write.
    """
    if not (getattr(sys, "frozen", False) and sys.platform == "win32"):
        return
    try:
        s = config.load()
        if s.get("save_location_migrated"):
            return  # already decided once; respect whatever the user did since
        patch = {"save_location_migrated": True}
        if not (s.get("save_location") or "").strip():
            old = paths.data_dir() / "sessions"
            if old.is_dir() and any(old.iterdir()):
                patch["save_location"] = str(old)
        config.update(patch)
    except Exception as e:
        print(f"[sessions] save-location upgrade pin skipped: {e}", flush=True)


# Module scope: runs once per process and covers every entrypoint (desktop
# window, --browser, --server-only, python -m live_transcribe.web).
_pin_save_location_on_upgrade()


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
            # The outstanding long-silence warning ({"minutes","count","at"}) or None. The UI
            # polls this while a live session runs and floats a banner when it appears.
            "silence_nudge": STATE.silence_nudge,
            # The outstanding "model struggling to keep up" warning ({"old_size","new_size",
            # "recording"}) or None, set when a live CPU session auto-downgrades. Same poll, a
            # parallel banner. `recording` is the session's recording state when it was raised.
            "struggle_nudge": STATE.struggle_nudge,
            # True iff recording is, or has ever been, active this session (start-time or a
            # mid-session record-from-here). The live screen and finish handoff key off it, and once
            # true /api/record-from-here refuses (re-recording the same stem would truncate the WAV).
            "recording_started": STATE.recording_started,
            # t0-capture: transcription-model readiness. Capture (and recording, if on) are already
            # live from Begin; while the model loads on the background thread the UI shows a
            # "preparing" state and polls this. model_ready is the AUTHORITATIVE flag (set at phase-1
            # end, engine built + started), NOT derived from STATE.engine (which stays None during the
            # backlog drain); deriving it from the engine was the "stuck on preparing" residual bug.
            # prepare_error carries a short model-load failure message (None while healthy) so a
            # failed load can be surfaced without hiding that capture/recording carried on.
            "model_ready": STATE.model_ready,
            "preparing": STATE.preparing,
            "prepare_error": STATE.prepare_error,
            # Live download/load progress for the non-blocking "preparing" UI, present while preparing
            # OR on error (else null). Shape: {phase, model, family, size, label, downloaded, total,
            # stalled}. See _build_engine_async / the pinned API contract.
            "prepare": STATE.prepare if (STATE.preparing or STATE.prepare_error) else None,
        }
        # Live AEC truth for the in-meeting toggle: the ENGINE'S actual state, never the stored
        # setting (a long-running instance can drift from disk; the toggle must not lie).
        if STATE.source_kind == "live" and STATE.capture is not None:
            avail, active = STATE.capture.aec_state()
            resp["aec_live_available"] = avail
            resp["aec_live_active"] = active
            # H1: system-audio capture health, one of 'disabled'|'pending'|'active'|
            # 'permission_denied'|'failed'. Read defensively: a capture backend that does not
            # expose this yet (or a mock in tests) reports as 'active' so the UI never raises a
            # false warning. The live screen banners only on the last two values.
            resp["sys_state"] = getattr(STATE.capture, "sys_state", "active")
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
                                     agc=old_cap.agc, record_raw_mic=old_cap.record_raw_mic)
            eng = STATE.engine
            # Re-attach BOTH energy rings, or the guards' level reference dies at the first
            # mid-meeting device change (the rings survive on the engine; the new capture has to
            # be told about them). Same rings, same session clock, so history stays continuous.
            if eng is not None and getattr(eng, "sys_env", None) is not None:
                c.attach_sys_ring(eng.sys_env)   # keep the echo-veto reference fed across the switch
            if eng is not None and getattr(eng, "mic_env", None) is not None:
                c.attach_mic_ring(eng.mic_env)   # keep the raw-mic (gain-invariant) feed alive
            return c

        def _reset_loop_history():
            # A different device changes what the model hears, so an already-armed
            # cross-segment loop guard must not suppress the first genuine identical line
            # from the new one. RecentEmissions is owned by the transcription worker, so
            # ASK (a pending flag the worker consumes between chunks) rather than mutate it
            # from this request thread.
            eng = STATE.engine
            if eng is not None:
                try:
                    eng.request_loop_history_reset()
                except Exception:
                    pass

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
                _reset_loop_history()   # the revert is a device change too (capture gap included)
                _silence_after_switch()
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
        _reset_loop_history()
        _silence_after_switch()
        return {"which": req.which, "device": req.device, "mic_device": mic, "loopback_device": loop}


class SilenceNudgeRequest(BaseModel):
    action: Literal["snooze", "mute"]


@app.post("/api/silence-nudge")
def silence_nudge_action(req: SilenceNudgeRequest):
    """Answer an outstanding long-silence warning, from the banner's own buttons.

    "snooze" ("Keep recording") clears it and restarts the silence clock, so a session that
    stays silent is warned once more (the watcher caps the session at two); "mute" (the X)
    stops it asking for the rest of the session. Neither touches the audio, and neither is
    persisted: both are answers about THIS meeting, not settings.

    409 when there is no live session to answer for, which is also what the UI gets if the
    session ended between the banner appearing and the click landing."""
    with STATE.lock:
        if not STATE.running or STATE.source_kind != "live":
            raise HTTPException(status_code=409, detail="No live session is running.")
        watch = STATE.silence_watch
        STATE.silence_nudge = None
        if watch is not None:
            # No clock argument: the watch restarts from the last value the watcher sampled,
            # so the request thread never has to reconstruct the session clock.
            if req.action == "mute":
                watch.mute()
            else:
                watch.snooze()
        st = watch.state() if watch is not None else {}
        return {"action": req.action, "silence_nudge": None,
                "muted": bool(st.get("muted")), "nudges": st.get("nudges", 0)}


@app.post("/api/record-from-here")
def record_from_here():
    """Start recording audio partway through a running live transcription ("I forgot to record", or
    the struggle nudge's offer). Captures IDENTICALLY to a start-time recording: the AEC-cleaned MIC
    + SYS folded to one L/R stereo <stem>.wav, reusing the session stem so it lands as a normal
    History row and re-transcribes unchanged.

    Records strictly from the click on: the recorder is given the session-clock time of THIS call as
    a shared anchor, so buffered pre-click audio (up to a chunk of it) is dropped/sliced and never
    written, and MIC/SYS stay aligned to that one moment. Earlier audio is not recoverable and is not
    saved (nothing was buffered before this call).

    409 when there is no running live transcription session to attach to, when the session has ALREADY
    recorded this session (a start-time recording or an earlier record-from-here: recording once per
    session, since a re-record would reuse the stem and truncate the first WAV), or when there is no
    live capture to record from (a failed device switch can leave a running session with none)."""
    with STATE.lock:
        if not (STATE.running and not STATE.stopping and STATE.source_kind == "live"
                and STATE.transcribing and STATE.engine is not None):
            raise HTTPException(status_code=409,
                                detail="Recording can only be started during a live transcription session.")
        if STATE.recording:
            raise HTTPException(status_code=409, detail="This session is already recording.")
        if STATE.recording_started:
            # Recorded earlier this session and stopped: a new recorder on the same stem would
            # truncate the finalised <stem>.wav on close, losing the first take. Record once.
            raise HTTPException(status_code=409, detail="This session has already recorded audio.")
        cap = STATE.capture
        t0 = getattr(cap, "_t0", None)
        if cap is None or t0 is None:
            # A failed live device switch can leave a running session with STATE.capture=None; a
            # recorder attached now would get no audio, and there is no session clock to anchor to.
            raise HTTPException(status_code=409, detail="There is no live audio capture to record from.")
        if STATE.output_path is None:
            raise HTTPException(status_code=409, detail="No active session to record.")
        # The click moment on the session clock (the same clock every chunk's t_start uses). The
        # recorder drops/slices anything before it, so nothing captured before the click is written
        # and both channels start at 0 aligned to this shared anchor.
        anchor = time.monotonic() - t0
        stem = STATE.output_path.with_suffix("")
        rec = sinks.AudioRecorder(stem, anchor=anchor)
        # Attach order matters: _feed reads STATE.recorder / STATE.recording LOCK-FREE every chunk,
        # so publish the recorder BEFORE the flag; the next captured chunk of each source then
        # begins writing. Never the reverse (recording=True with recorder=None). Stop closes this
        # recorder generically (the existing what="all"/"recording" paths), so do NOT close it here.
        STATE.recorder = rec
        STATE.recording = True
        STATE.recording_started = True   # latch: this session has now recorded; refuse a re-record
        # Taking the offered action answers the banner: clear it so it cannot linger contradicting
        # the fact that we are now recording (the frontend also drops it optimistically).
        STATE.struggle_nudge = None
        audio_stem = str(stem)
    # audio_stem must reach the client: the finish-screen re-transcribe handoff keys off it.
    return {"recording": True, "audio_stem": audio_stem}


class StruggleNudgeRequest(BaseModel):
    action: Literal["dismiss", "mute"]


@app.post("/api/struggle-nudge")
def struggle_nudge_action(req: StruggleNudgeRequest):
    """Answer an outstanding "model struggling to keep up" banner, from its own buttons.

    "dismiss" ("Keep going" / the X) clears it for this session; the auto-downgrade carries on, and
    the once-per-session latch means a later rung will not re-raise a dismissed banner. "mute"
    clears it AND persists struggle_nudge=false, so this machine stops surfacing the downgrade
    entirely (for a user who knowingly runs a weak CPU). Neither touches the transcription or the
    downgrade itself. 409 when there is no live session to answer for."""
    with STATE.lock:
        if not STATE.running or STATE.source_kind != "live":
            raise HTTPException(status_code=409, detail="No live session is running.")
        STATE.struggle_nudge = None
    if req.action == "mute":
        # Persist outside the state lock (config.update does disk I/O). The session-level clear
        # already took effect, so a failed write must not fail the request; log it and carry on.
        try:
            config.update({"struggle_nudge": False})
        except Exception as e:
            print(f"[struggle] muted for the session but the setting could not be saved: {e}", flush=True)
    return {"struggle_nudge": None}


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
        # D8: the engine carries its concrete device ("cpu"/"cuda"/"mlx"). Legacy engine
        # objects without _device keep the old _is_cpu reconstruction, which can never
        # say "mlx" (mlx engines always have _device).
        cur_device = getattr(engine, "_device", None) or ("cpu" if cur_is_cpu else "cuda")
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
        device_str = cur_device
        if data.get("tier"):
            # A quality change: map the key to a size on THIS device (never flip CPU<->GPU live on
            # Windows). An mlx session resolves with "auto" so the new size lands back on mlx when
            # it has an MLX form and on the CPU otherwise; live mlx <-> cpu swaps are ordinary
            # serial-worker model reloads on a Mac (D8), so the load device (and its compute type)
            # follows the resolved tier there.
            new_tier = resolve_tier(data["tier"], "cpu" if cur_device == "cpu" else "auto", new_lang, new_engine_pref)
            new_size = transcribe.TIER_CONFIG[new_tier]["model"]
        else:
            new_size = cur_size                   # engine/family-only change: keep the running size
            if cur_device == "mlx":
                # A language/engine change on an MLX session can leave the kept size with no MLX
                # form for the NEW family (Fluister turbo -> English resolves STOCK turbo, which
                # is unmapped; English large-v3 -> Fluister resolves fluister-large-v3, also
                # unmapped), so loading it on "mlx" would raise. Re-resolve the kept size for the
                # new (language, engine) so the load lands on mlx when mapped and on the CPU
                # otherwise (codex H2). Windows never enters here: cur_device is cpu/cuda.
                new_tier = resolve_tier(cur_size, "auto", new_lang, new_engine_pref)
        if cur_device == "mlx" and new_tier is not None:
            device_str = transcribe.TIER_CONFIG[new_tier]["device"]
            if device_str != "mlx":
                compute = transcribe.TIER_CONFIG[new_tier]["compute_type"]
        model_name, family = transcribe.resolve_model(new_size, new_lang, new_engine_pref)
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
        # With a model swap, also hand over the backend it was built on so the engine's
        # device identity (_device/_is_cpu/_compute_type) moves with the model: an
        # mlx -> cpu change must enable the CPU downgrade ladder and make the NEXT
        # reconfigure resolve from "cpu", not a stale "mlx" (codex M1). On Windows
        # device_str always equals the engine's current device, so this is a no-op there.
        engine.request_change(language=decode_lang, engine=new_engine_pref,
                              model=model, model_name=model_name, size=new_size, family=family,
                              device=(device_str if model is not None else None),
                              compute_type=(compute if model is not None else None))
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
    # Thread the auto cross-family override (WP-3) so warm-up warms the model Begin will ACTUALLY use
    # (e.g. Afrikaans falling back to a downloaded Whisper), not the language-default family.
    tier, engine_override = resolve_tier_engine(quality, device, language, engine_pref)
    return transcribe.warm_up_async(tier, language, engine_override or engine_pref)


def prewarm_at_startup():
    """Warm the CPU model the next Begin will load, at APP START rather than at the first Begin.

    On CPU the load is minutes and it is otherwise paid in full, with the meeting already running, at
    the worst possible moment. Starting it when the app opens means the first session usually finds a
    warm model, and when it does not it is at least already part-way through.

    Deliberately narrow:
      * CPU tiers only. A CUDA or Metal load is a few seconds, so there is nothing to hide there.
      * Only a model already on disk. This never downloads anything: no network at app start.
      * The tier is resolved exactly as Begin resolves it, from saved settings, so an explicit model
        choice is honoured and never overridden by a guess.
      * A no-op while a session is running, and idempotent against the pre-meeting screen's
        /api/warm-up (transcribe.warm_up_async returns early when that model is cached or warming),
        so the two can never double-warm. A user who then changes the model simply warms the new one;
        the wasted work is one background load of a model they had selected at the time.
    Returns a small dict describing what it did, so it is testable without a model.
    SA_LIVE_PREWARM=0 turns it off.
    """
    if os.environ.get("SA_LIVE_PREWARM", "1") == "0":
        return {"state": "skipped", "why": "disabled"}
    try:
        with STATE.lock:
            if STATE.running:
                return {"state": "skipped", "why": "session running"}
        settings = config.load()
        quality = settings.get("tier") or "auto"
        device = settings.get("device") or "auto"
        language = settings.get("language") or None
        engine_pref = settings.get("engine") or "auto"
        tier, engine_override = resolve_tier_engine(quality, device, language, engine_pref)
        effective_engine = engine_override or engine_pref
        if load_device_for(tier) != "cpu":
            return {"state": "skipped", "why": "not a CPU tier", "tier": tier}
        # Present-on-disk is the gate: pre-warm must never start a download.
        plan = _resolve_download_plan(tier, language, effective_engine)
        if not plan.get("present"):
            return {"state": "skipped", "why": "model not downloaded", "tier": tier}
        print(f"[warmup] pre-warming {plan.get('model')} for {tier} at app start (CPU)", flush=True)
        return transcribe.warm_up_async(tier, language, effective_engine)
    except Exception as e:      # best-effort: a pre-warm problem must never affect starting the app
        print(f"[warmup] pre-warm at start skipped: {e}", flush=True)
        return {"state": "skipped", "why": str(e)}


def _prewarm_on_startup():
    """ASGI startup hook: run the pre-warm decision OFF the startup path. Resolving the tier can
    probe the GPU and the download plan touches the disk, so even the decision runs on its own
    thread; the server binds and serves the UI without waiting for any of it."""
    threading.Thread(target=prewarm_at_startup, daemon=True, name="prewarm").start()


# Registered on the router directly (rather than the deprecated @app.on_event decorator) so any ASGI
# host that runs the lifespan - uvicorn in web/__main__.py, and the desktop shell through it - pays
# the same start-up pre-warm.
app.router.on_startup.append(_prewarm_on_startup)


class PreflightRequest(BaseModel):
    tier: str = "auto"
    device: str = "auto"
    language: str = "af"
    engine: str = "auto"


def _preflight_device(device_pref):
    """The honest processor Begin will use: 'cpu' when forced or no usable accelerator, 'gpu' for
    CUDA, 'mlx' for the Apple GPU (darwin-arm64 with mlx-whisper). Surfaced so the modal's
    size/label/time match reality even on a frozen build with no GPU libs."""
    if device_pref == "cpu":
        return "cpu"
    try:
        from .. import accel
        backend = accel.asr_backend(device_pref)     # "cuda" | "mlx" | "cpu"
        return "gpu" if backend == "cuda" else backend
    except Exception:
        return "cpu"


def _asr_download_target(family, size):
    """The honest voicedl download target for (family, size): the MLX repo on a ready Mac for the
    mapped pairs (voicedl.asr_download_target, WP-M3), else today's ct2 target. The getattr guard
    keeps every caller working against an older voicedl, where the answer IS today's target."""
    from .. import voicedl
    fn = getattr(voicedl, "asr_download_target", None)
    if fn is not None:
        try:
            return fn(family, size)
        except Exception:
            pass
    return transcribe.FLUISTER_REPOS.get(size) if family == "fluister" else size


def _asr_approx_bytes(family, size, target):
    """Rough on-disk bytes for the download target: the repo-keyed entry when voicedl knows the MLX
    repo (WP-M3 adds those; stock MLX repos live in _MLX_SIZES, kept out of _SIZES so the download
    API allow-list stays size-keyed, codex L1), else the existing size-keyed entry (today's answer)."""
    from .. import voicedl
    sizes = voicedl._FLUISTER_SIZES if family == "fluister" else voicedl._SIZES
    mlx_sizes = getattr(voicedl, "_MLX_SIZES", {})
    return mlx_sizes.get(target) or sizes.get(target) or sizes.get(size, 0)


def _downloaded_alternatives(exclude_family, exclude_size):
    """Usable transcription models already on disk (any family), for the pre-start modal's
    instant-switch list. Excludes the model Begin will already use. Each entry mirrors the pinned
    contract: {size, family, label, model, approx_bytes, quality_note}."""
    from ..__main__ import _downloaded_sizes, _FAMILY_SIZE_ORDER
    from .. import voicedl
    notes = {"fluister": "Afrikaans-tuned", "whisper": "Good; not Afrikaans-tuned",
             "swivuriso": "South African languages"}
    out = []
    for family in ("fluister", "whisper"):
        try:
            sizes = _downloaded_sizes(family)
        except Exception:
            sizes = set()
        for size in _FAMILY_SIZE_ORDER.get(family, []):
            if size not in sizes:
                continue
            if family == exclude_family and size == exclude_size:
                continue
            target = _asr_download_target(family, size)
            approx = _asr_approx_bytes(family, size, target)
            out.append({"size": size, "family": family, "label": _QUALITY_LABEL.get(size, size),
                        "model": target, "approx_bytes": approx, "quality_note": notes.get(family, "")})
    # The MLX store (codex M3 residual): _downloaded_sizes is deliberately ct2-only, so on a
    # ready Mac the mapped MLX models are offered from their OWN store when actually cached
    # (an MLX-only download is instantly usable via the mlx tiers). Deduped against the ct2
    # entries above (same family+size). On Windows accel.mlx_ready() is always False, so the
    # list is byte-identical there; any failure changes nothing (best-effort, like the rest).
    try:
        from .. import accel
        from ..mlxbackend import MLX_REPOS
        if accel.mlx_ready():
            fl_size = {repo: size for size, repo in transcribe.FLUISTER_REPOS.items()}
            seen = {(e["family"], e["size"]) for e in out}
            for ct2_id, repo in MLX_REPOS.items():
                family = "fluister" if ct2_id in fl_size else "whisper"
                size = fl_size.get(ct2_id, ct2_id)
                if (family, size) in seen or (family == exclude_family and size == exclude_size):
                    continue
                if not voicedl._mlx_present(repo):
                    continue
                approx = (voicedl._FLUISTER_SIZES.get(repo) if family == "fluister"
                          else getattr(voicedl, "_MLX_SIZES", {}).get(repo)) or 0
                out.append({"size": size, "family": family, "label": _QUALITY_LABEL.get(size, size),
                            "model": repo, "approx_bytes": approx, "quality_note": notes.get(family, "")})
    except Exception:
        pass
    # Swivuriso is one model at a nominal size; list it as an INSTANT switch only when it is actually
    # cached on disk (a local ct2 build, or the hosted repo already downloaded). swivuriso_available()
    # is ~always True (the repo is hosted), so it must NOT gate this list, or the modal would advertise
    # a multi-GB download as instant (P1-1).
    try:
        if (os.path.isdir(transcribe.SWIVURISO_LOCAL) or voicedl._present(transcribe.SWIVURISO_REPO)) \
                and exclude_family != "swivuriso":
            out.append({"size": "turbo", "family": "swivuriso", "label": "South African",
                        "model": transcribe.SWIVURISO_REPO, "approx_bytes": voicedl._SWIVURISO_SIZE,
                        "quality_note": notes["swivuriso"]})
    except Exception:
        pass
    return out


@app.post("/api/preflight-model")
def preflight_model(req: PreflightRequest):
    """Stateless pre-flight for the pre-start modal: what model will Begin load, is it already on disk
    (present==True means Begin will NOT download), how big is it, and which downloaded alternatives
    could be used instantly. Starts nothing and takes no lock; uses the SAME resolver + download-plan
    Begin uses, so the picker/modal/Begin always agree."""
    settings = config.load()
    quality = req.tier if (req.tier and req.tier != "auto") else (settings.get("tier") or "auto")
    device = (req.device or settings.get("device") or "auto")
    language = req.language if req.language else None    # "" -> None (auto-detect), matching Begin
    engine_pref = (req.engine or settings.get("engine") or "auto")
    tier, engine_override = resolve_tier_engine(quality or "auto", device, language, engine_pref)
    effective_engine = engine_override or engine_pref
    plan = _resolve_download_plan(tier, language, effective_engine)
    return {
        "model": plan["target"] or plan["model"],
        "size": plan["size"],
        "family": plan["family"],
        "label": plan["label"],
        "present": plan["present"],
        "approx_bytes": plan["approx_bytes"] or 0,
        "device": _preflight_device(device),
        "engine_override": engine_override,
        "downloaded_alternatives": _downloaded_alternatives(plan["family"], plan["size"]),
    }


@app.post("/api/prepare/retry")
def prepare_retry():
    """Retry a bounded model-prepare failure without disturbing the live capture/recording. Clears the
    error, re-opens the pending-audio buffer so _feed holds chunks again, and re-spawns the background
    build from the stashed args (same session, same t0). 409 if there is no error to retry."""
    with STATE.lock:
        if not (STATE.running and STATE.prepare_error):
            raise HTTPException(status_code=409, detail="There is no model-preparation error to retry.")
        args = STATE.prepare_args
        if not args:
            raise HTTPException(status_code=409, detail="Nothing to retry for this session.")
        # Clear the error and re-arm the preparing state. Capture + recording are untouched.
        STATE.prepare_error = None
        STATE.preparing = True
        STATE.model_ready = False
        STATE.prepare_phase = ""
        STATE.prepare = None
        # Reuse the SAME pending-audio buffer that has been holding chunks since Begin (and kept filling
        # through the error state), so a successful retry replays from t0, not from the Retry click
        # (P1-2). Only create a fresh one if it was somehow lost (defensive; e.g. a prior final abandon).
        if STATE.pending_audio is None:
            STATE.pending_audio = _PendingAudio(_PENDING_MAX_SAMPLES)
        t = threading.Thread(target=_build_engine_async, args=args, daemon=True, name="engine-build")
        STATE.build_thread = t
        t.start()
    return {"ok": True}


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
    # Auto model resolution (WP-3) can CROSS families when the language-preferred family has nothing
    # downloaded but another usable family does (e.g. Afrikaans with only stock large-v3 on disk ->
    # Whisper). resolve_tier_engine returns that crossing; fold it into the effective engine pref so
    # the engine, warm-up and pre-flight all load the model Begin will actually use.
    tier, engine_override = resolve_tier_engine(quality or "auto", device, language, engine_pref)
    engine_pref = engine_override or engine_pref
    # Record the device decision in the log so a "why is it on CPU?" is answerable at a
    # glance (calling cuda_ready here also registers the libs before the engine loads).
    try:
        from .. import cudadl
        print(f"[tier] quality={quality!r} device={device!r} gpu_present={cudadl.gpu_present()} "
              f"installed={cudadl.installed()} cuda_ready={cudadl.cuda_ready()} "
              f"engine_override={engine_override!r} -> {tier}", flush=True)
    except Exception:
        pass
    # default_context is prepended to the per-meeting prompt (participants + terms). Both StartRequest
    # and TranscribeFileRequest carry an optional context_override; getattr keeps the resolver safe for
    # any other caller too. None -> use the saved default; a string (incl. "") replaces it for THIS run
    # only, never persisted.
    ctx = getattr(req, "context_override", None)
    if ctx is None:
        ctx = settings.get("default_context", "")
    parts = [p for p in (ctx.strip(), req.prompt.strip()) if p]
    prompt = ", ".join(parts) or None
    return tier, language, prompt, engine_pref


def _resolve_download_plan(tier, language, engine_pref):
    """The concrete model Begin will load AND its on-disk download target, so the async builder,
    /api/preflight-model and Begin all agree on exactly what will (or will not) be fetched. Pure and
    network-free (voicedl._present is a local-cache probe). Returns a dict:
      model  - the concrete id to display/track (a Whisper size, a Fluister/Swivuriso repo, or a
               local ct2 path on a dev machine),
      target - the voicedl download target (a size for stock Whisper, a repo id for Fluister/
               Swivuriso, or the MLX repo id on a ready Mac for the mapped sizes),
      family - "fluister" | "whisper" | "swivuriso",
      size   - the stock size key (from the tier),
      label  - the human quality label ("Fast"/"Balanced"/...),
      approx_bytes - rough on-disk size for the progress estimate,
      present - True iff Begin will NOT have to download (files already cached, or a local build)."""
    from .. import voicedl
    size = transcribe.TIER_CONFIG.get(tier, {}).get("model", "small")
    model_id, family = transcribe.resolve_model(size, language, engine_pref)
    if family == "swivuriso":
        target = transcribe.SWIVURISO_REPO
        approx = voicedl._SWIVURISO_SIZE
    else:   # fluister / whisper: the honest target, an MLX repo on a ready Mac (WP-M3)
        target = _asr_download_target(family, size)
        approx = _asr_approx_bytes(family, size, target)
    # A local ct2 build (dev machine SA_LIVE_AF_MODEL / an af-lora-* or swivuriso dir) is present even
    # though its HF repo is not cached; treat an existing local model dir as present so we never trigger
    # a spurious repo download over a working local build.
    present = False
    if isinstance(model_id, str) and os.path.isdir(model_id):
        present = True
    elif target:
        try:
            present = voicedl._present(target)
        except Exception:
            present = False
    return {"model": model_id, "target": target, "family": family, "size": size,
            "label": _QUALITY_LABEL.get(size, size), "approx_bytes": approx, "present": present}


def _start_model_download(family, size):
    """Kick the matching voicedl background download for this family/size. Tolerates the single global
    download slot already being busy (a Settings download, or a prior prepare attempt) by treating
    "already downloading" as success - the builder then polls the existing progress. Returns True if a
    download is running (ours or a pre-existing one), False if the kick failed for another reason."""
    from .. import voicedl
    try:
        if family == "fluister":
            voicedl.start_fluister_download(size)
        elif family == "swivuriso":
            voicedl.start_swivuriso_download()
        else:
            voicedl.start_download(size)
        return True
    except RuntimeError:
        return True     # already downloading: poll the existing progress rather than erroring
    except Exception as e:
        print(f"[start] could not begin model download ({family}/{size}): {e}", flush=True)
        return False


def _download_owner_repo(plan):
    """The HuggingFace cache repo voicedl.progress()['repo'] will report for THIS plan's download,
    used by the prepare polling loop to tell OUR download from a foreign one. Fluister/Swivuriso
    targets and any explicit org/repo id (an MLX target on a ready Mac) already ARE the cache repo
    voicedl records; only a bare stock-Whisper SIZE name resolves through voicedl._repo_id.
    Mangling an MLX repo id through _repo_id would produce a name that never matches the recorded
    repo, so our own download would read as foreign and die on the foreign-slot timeout (codex M2).
    Best-effort: any error falls back to the raw target (an older/stub voicedl records no repo and
    is trusted via the loop's None check)."""
    from .. import voicedl
    target = plan.get("target")
    try:
        if plan.get("family") in ("fluister", "swivuriso") or "/" in (target or ""):
            return target
        return voicedl._repo_id(target)
    except Exception:
        return target


def _build_engine_async(session_token, tier, language, prompt, engine_pref, md_sink, browser_sink):
    """Build the transcription Engine off the request thread (t0-capture), then attach it to the
    already-capturing session and replay everything held since Begin, IN ORDER and with zero drops.

    The model load (Engine -> load_model) is the slow part and runs OUTSIDE STATE.lock, so /api/status
    and /api/levels stay responsive throughout: a warm model is a fast cache hit, and a first-time
    download can take minutes, which is fine because capture + recording are already running and
    nothing is dropped.

    The buffer->live handoff is the delicate part. STATE.engine is published only at the very END,
    after the backlog is fully caught up. Until then STATE.engine stays None, so _feed keeps APPENDING
    live chunks to the pending buffer (behind the backlog) instead of feeding the engine directly:
    that is what keeps the replayed backlog strictly ahead of every live chunk AND stops any live
    chunk being dropped on the full queue during a long replay. The drain runs OUTSIDE STATE.lock
    (block=True with a short timeout so a Stop is honoured within ~0.5s and a full queue never drops);
    the final flip (close the buffer, publish the engine) is done under STATE.lock AND the buffer's
    own lock, so no _feed.append can interleave. Session identity (started_at object identity,
    mirroring record_from_here / _on_downgrade) is re-checked at every step; a Stop/switch/new-start
    discards the engine. On build failure we surface prepare_error and leave capture + recording
    running, so a model problem never loses the audio.

    WP-2 adds a real two-phase prepare BEFORE the engine is wired up: a "downloading" phase (only when
    the model is not already cached) that polls voicedl.progress() into STATE.prepare and gives up with
    a retryable error if no bytes arrive for PREPARE_DOWNLOAD_STALL_SECONDS, then a "loading" phase
    (the Engine build) bounded by a device-aware budget (load_budget_seconds: minutes on CPU, where a
    healthy first load genuinely takes that long, seconds-scale on CUDA/Metal). Either failure leaves
    capture + recording running and is retryable via /api/prepare/retry, and a retry ATTACHES to a load
    already in flight rather than queueing a second Engine behind it (_start_or_attach_load).
    WP-1 flips STATE.model_ready True at phase-1 end (engine built + started), independent of the
    backlog drain, so a slow CPU can never leave the UI stuck on "preparing"."""
    from .. import voicedl

    def _superseded():
        # The session this build belongs to is GONE or REPLACED (reset/new-start): started_at no longer
        # matches, or nothing is running. This is the DISCARD signal (throw the private engine away). A
        # plain Stop of THIS session is NOT a supersede - running stays True and started_at matches until
        # reset(), so Stop is handled as a hand-off (the stop path drains the private engine), never a
        # discard, which is what stops a Stop-during-catch-up from losing the transcript (P1-3).
        return not (STATE.running and STATE.started_at is session_token)

    def _still_ours():
        # Cheap identity/liveness guard. NOTE it deliberately does NOT test STATE.preparing: WP-1 flips
        # preparing False at phase-1 end while the drain is still running (and STATE.engine still None),
        # so gating on preparing here would abort the drain the instant readiness flips. Stop/switch/
        # new-start are detected via running/stopping/started_at; STATE.transcribing catches a partial
        # "stop transcription" (recording continues) that keeps the session running - without it an
        # in-flight build could resurrect after the user stopped transcribing (P1-4).
        return (STATE.running and not STATE.stopping and STATE.transcribing
                and STATE.started_at is session_token)

    def _fail(msg, stalled=False):
        # Surface a bounded, retryable prepare failure and stop waiting. Capture + recording keep
        # running (audio safe on disk if recording). The held transcription backlog is RETAINED (P1-2):
        # _feed keeps buffering through the error state and /api/prepare/retry reuses this same buffer,
        # so a successful retry replays from t0, not from the Retry click. Session-identity guarded so a
        # Stop/switch/new-start is never clobbered.
        print(f"[start] model prepare failed: {msg}", flush=True)
        with STATE.lock:
            if (STATE.running and not STATE.stopping and STATE.started_at is session_token
                    and STATE.preparing):
                if isinstance(STATE.prepare, dict):
                    STATE.prepare.update(phase="error", stalled=stalled)
                STATE.prepare_error = msg
                STATE.preparing = False
                STATE.model_ready = False
                STATE.prepare_phase = "error"

    # Stash the build args so /api/prepare/retry can re-spawn this build (idempotent; start() stashes
    # them too, but a retry re-enters here and must keep them fresh).
    with STATE.lock:
        if _still_ours() and STATE.preparing:
            STATE.prepare_args = (session_token, tier, language, prompt, engine_pref, md_sink, browser_sink)

    # Resolve the concrete model + on-disk download target (network-free) so progress + preflight agree.
    try:
        plan = _resolve_download_plan(tier, language, engine_pref)
    except Exception as e:
        _fail(f"Could not work out which transcription model to load: {e}")
        return

    def _set_prepare(phase, **extra):
        with STATE.lock:
            if not (_still_ours() and STATE.preparing):
                return
            d = {"phase": phase, "model": plan["target"] or plan["model"], "family": plan["family"],
                 "size": plan["size"], "label": plan["label"],
                 "downloaded": 0, "total": plan["approx_bytes"] or 0, "stalled": False}
            if isinstance(STATE.prepare, dict):      # carry any bytes already observed across a phase change
                for k in ("downloaded", "total"):
                    d[k] = STATE.prepare.get(k, d[k])
            d.update(extra)
            STATE.prepare = d
            STATE.prepare_phase = phase

    # --- phase "downloading": only when the exact model file set is not already on disk. ---
    if not plan["present"]:
        _set_prepare("downloading")
        if not _still_ours():
            return
        # The single global download slot may be busy with a DIFFERENT model (e.g. a Settings download).
        # Identify OUR target by its cache repo so we never read a foreign download's bytes/done/error as
        # ours and then try to load a model that is still missing (P1-5). progress()["repo"] is the repo
        # folder being measured; a stub/older voicedl with no repo is trusted (None).
        target_repo = _download_owner_repo(plan)
        if not _start_model_download(plan["family"], plan["size"]):
            _fail("Could not start the transcription model download. Check your connection and try again.")
            return
        last_downloaded = -1
        last_change = time.monotonic()
        foreign_since = None   # when a DIFFERENT model started occupying the slot (bounded, see below)
        while True:
            if not _still_ours():
                return
            try:
                prog = voicedl.progress()
            except Exception:
                prog = {}
            state = prog.get("state")
            ours = prog.get("repo") in (None, target_repo)   # None = no repo info -> trust it
            # Completion is the on-disk truth, or an OURS-scoped 'done' (never a foreign download's).
            done = False
            try:
                done = voicedl._present(plan["target"])
            except Exception:
                done = False
            if not done and state == "done" and ours:
                done = True
            if done:
                break
            if state == "error" and ours:
                _fail("The model download failed. Check your connection and try again.")
                return
            if state == "downloading" and ours:
                foreign_since = None
                dl = int(prog.get("downloaded") or 0)
                tot = int(prog.get("total") or plan["approx_bytes"] or 0)
                with STATE.lock:
                    if _still_ours() and isinstance(STATE.prepare, dict):
                        STATE.prepare.update(downloaded=dl, total=tot)
                # Stall detection, verification-aware (P2-2): flat bytes near the total are the no-write
                # verification tail (longer grace); flat bytes far below the total are a dead connection.
                if dl > last_downloaded:
                    last_downloaded = dl
                    last_change = time.monotonic()
                else:
                    near_done = tot > 0 and dl >= tot * 0.95
                    limit = PREPARE_VERIFY_GRACE_SECONDS if near_done else PREPARE_DOWNLOAD_STALL_SECONDS
                    if time.monotonic() - last_change >= limit:
                        _fail("The download stalled. Check your connection and try again.", stalled=True)
                        return
            elif state != "downloading":
                # The slot is free (idle / a foreign download finished or errored) but our model is not
                # present yet: (re)start our own download rather than poll a slot that will never become
                # ours (P1-5). A stall attempt that reached a terminal state is thus restartable on retry.
                foreign_since = None
                if not _start_model_download(plan["family"], plan["size"]):
                    _fail("Could not start the transcription model download. Check your connection and try again.")
                    return
                last_downloaded = -1
                last_change = time.monotonic()
            else:
                # A DIFFERENT model holds the slot: wait for it, but BOUNDED - a stuck foreign download
                # must not starve us forever. Keep our own stall clock paused (we are not the one
                # downloading), and after the bound surface a distinct retryable error so Retry can pick up
                # once the slot is free (new P2 from the P1-5 fix).
                now = time.monotonic()
                if foreign_since is None:
                    foreign_since = now
                elif now - foreign_since >= PREPARE_FOREIGN_SLOT_TIMEOUT_SECONDS:
                    _fail("Another model is still downloading. Please try again shortly.")
                    return
                last_downloaded = -1
                last_change = now
            time.sleep(_PREPARE_POLL_SECONDS)

    # --- phase "loading": build the Engine. On a warm cache this is quick; on CPU it is dominated by
    # the first inference and can honestly take minutes (see load_budget_seconds). The load runs in a
    # sub-thread that we watch rather than blind-join, so that:
    #   * while the thread is ALIVE the prepare state stays "loading" with an elapsed counter, never
    #     the error screen: a slow machine is not a failure;
    #   * a repeat prepare (Retry) ATTACHES to the live load instead of starting a second Engine, which
    #     would only queue behind the first on the model build lock. That queueing is exactly what
    #     turned a 120 s timeout into a multi-minute wait after Retry;
    #   * only a dead thread (exception surfaced) or an exhausted budget becomes an error.
    _set_prepare("loading")
    if not _still_ours():
        return
    device = load_device_for(tier)
    budget = load_budget_seconds(device=device)
    lt, build, load_started, attached = _start_or_attach_load(
        (tier, language, prompt, engine_pref),
        lambda: transcribe.Engine(tier=tier, language=language,
                                  initial_prompt=prompt, engine=engine_pref))
    if attached:
        print(f"[start] attaching to the model load already in flight for {tier} "
              f"({time.monotonic() - load_started:.0f}s so far)", flush=True)
    while lt.is_alive():
        elapsed = time.monotonic() - load_started
        if elapsed >= budget:
            break
        if not _still_ours():
            return               # stop/switch/new-start: leave the load running for whoever is next
        # Honest waiting: publish the elapsed seconds (and, on CPU, the "this is normal" hint) so the
        # UI can count up instead of pretending nothing is happening or claiming a failure.
        _set_prepare("loading", elapsed=round(elapsed, 1), budget=budget,
                     slow=bool(device == "cpu" and elapsed >= PREPARE_LOAD_SLOW_HINT_SECONDS))
        lt.join(_PREPARE_LOAD_POLL_SECONDS)
    if lt.is_alive():
        _fail(f"The transcription model did not finish loading after {_fmt_gap(budget)}. "
              f"Please try again.")
        return
    if "error" in build:
        _fail(f"Could not load the transcription model: {build['error']}")
        return
    engine = build.get("engine")
    if engine is None:
        _fail("Could not load the transcription model on this computer.")
        return
    # This build owns the engine now; drop the in-flight record so the NEXT prepare starts a fresh
    # load rather than attaching to a finished one and handing out an engine already in use.
    _clear_load((tier, language, prompt, engine_pref), build)

    def _release_prep_engine():
        # Drop the private handle ONLY if it still points at THIS build's engine (identity-guarded like
        # _on_downgrade), so a discard here can never clobber a newer session's preparing engine.
        with STATE.lock:
            if STATE.preparing_engine is engine:
                STATE.preparing_engine = None

    def _catchup_failed(msg, remainder=None):
        # A failure DURING catch-up, i.e. AFTER phase-1 already flipped model_ready True and cleared
        # STATE.preparing (a dead/wedged engine worker). _fail() is keyed on STATE.preparing and would be
        # a no-op here, which used to leave model_ready True, engine None and the buffer open forever -
        # a permanent "Listening" lie (P1-6). This transition is keyed on preparing_engine identity
        # INSTEAD, undoes readiness and surfaces a retryable prepare_error. The pending buffer is KEPT
        # (P1-2) and any unsubmitted batch is put back at its front so a retry resumes from where the
        # worker died rather than dropping more audio.
        #
        # Ownership first (P2-a): claim under STATE.lock BEFORE stopping the engine. If a Stop (or a
        # partial transcription-stop) is already in flight, the stop worker OWNS this engine and will
        # drain + stop it - we must only return the tail and leave the engine UNTOUCHED, or we would
        # double-stop / use-after-stop the engine it captured. Otherwise we atomically clear
        # preparing_engine + publish the failure, then stop the engine OUTSIDE the lock.
        print(f"[start] transcription engine failed during catch-up: {msg}", flush=True)
        stop_engine = False
        with STATE.lock:
            if STATE.preparing_engine is not engine:
                return   # superseded / already handled by a newer path
            if remainder:
                pb2 = STATE.pending_audio
                if pb2 is not None:
                    pb2.putback_front(remainder)
            if STATE.stopping or not STATE.transcribing:
                # The stop worker owns the drain + engine.stop; leave the engine to it (no double-stop).
                return
            STATE.model_ready = False
            STATE.preparing = False
            STATE.prepare_error = msg
            STATE.prepare_phase = "error"
            if isinstance(STATE.prepare, dict):
                STATE.prepare.update(phase="error")
            STATE.preparing_engine = None
            stop_engine = True
        if stop_engine:
            try:
                engine.stop(drain=False)
            except Exception:
                pass

    def _replay(items):
        # Replay a batch into the engine in order. block=True with a short timeout so a healthy replay
        # never drops on the maxsize=32 queue (it waits for space and retries). Returns (status, remainder)
        # where remainder is the not-yet-enqueued tail of `items`:
        #   ("ok", [])            - the whole batch was enqueued;
        #   ("superseded", tail)  - the session was replaced/reset mid-batch (discard the engine);
        #   ("dead", tail)        - the engine worker exited mid-batch (P1-6: fail, don't loop forever
        #                           lying that we are Listening);
        #   ("handoff", tail)     - THIS session is stopping: stop feeding NOW and give the tail back so
        #                           the stop path owns the drain, with no two-feeder race (P1-3).
        # Ownership is checked BEFORE every enqueue (P1-3), not only after a full queue: on a large
        # backlog where every chunk is accepted the old post-only check let the builder feed for seconds
        # without noticing the Stop; now hand-off happens within one chunk, so the stop path's join
        # returns promptly and its safety timeout is effectively unreachable.
        # DEFERRED (1.13.2): a worker that DIES here can still lose up to ~queue-size (~32) chunks already
        # accepted but not yet processed - detected only once the queue fills - because faster-whisper's
        # queue has no per-chunk acknowledgement. Changing that is an Engine-contract change, too risky
        # for this cert release. The PRIMARY P1-6 fix (dead worker -> retryable prepare_error, never a
        # permanent false "Listening") is unaffected.
        for i, (src, audio, t_start) in enumerate(items):
            if _superseded():
                return "superseded", items[i:]
            if not _engine_alive(engine):
                return "dead", items[i:]
            if not _still_ours():
                return "handoff", items[i:]
            while not engine.on_chunk(src, audio, t_start, block=True, timeout=0.5):
                if _superseded():
                    return "superseded", items[i:]
                if not _engine_alive(engine):
                    return "dead", items[i:]
                if not _still_ours():
                    # Our session is stopping (or transcription-stopping): release promptly with the
                    # unsubmitted tail so the stop path is the SOLE feeder (never concurrent with us).
                    return "handoff", items[i:]
        return "ok", []

    # --- phase 1: wire the engine up and DECLARE READINESS, but DO NOT publish STATE.engine yet.
    # STATE.engine stays None and the buffer stays OPEN so _feed keeps buffering live chunks behind the
    # backlog (ordering integrity); STATE.model_ready flips True here (WP-1) so the UI goes live the
    # instant transcription can run, decoupled from the drain. If catch-up never completes, STATE.engine
    # simply stays None forever and that is fine: the UI is already live and the transcript streams.
    with STATE.lock:
        if not _still_ours():
            # Stop/switch/new-start landed during the model load, before the engine was ever exposed:
            # discard it (the pre-ready backlog is treated as "stopped before ready"). preparing_engine
            # was never set for this build, so there is nothing for a stop path to drain.
            try:
                engine.stop()
            except Exception:
                pass
            return
        engine.subscribe(md_sink)
        engine.subscribe(browser_sink)
        engine.start()
        # Liveness gate (P1-6): a worker that died the instant it started must NOT be advertised ready.
        # Check before flipping model_ready; if it is dead, leave preparing/model_ready untouched and
        # surface the failure below (outside the lock, to avoid re-entering STATE.lock).
        started_alive = _engine_alive(engine)
        # Stop-safety (P1-3): expose the built + started engine under its own handle BEFORE flipping
        # model_ready, while STATE.engine stays None so _feed keeps routing through pending_audio. From
        # here a Stop during catch-up can reach and drain this engine instead of losing the transcript.
        STATE.preparing_engine = engine
        if started_alive:
            # Attach the energy rings NOW (they live on the engine, fed by the capture callback in real
            # time): the live CPU-downgrade nudge, the SYS echo-veto reference, and the gain-invariant
            # raw-MIC feed. Attaching here means every chunk captured from now on has ring history by the
            # time it is replayed; the pre-engine backlog (captured during the model load) has none and
            # falls back to sample-based tests. on_downgrade is inert until STATE.engine is published (the
            # _on_downgrade guard drops callbacks whose engine is not STATE.engine), so no spurious
            # "struggling" nudge fires during the expected catch-up.
            cap = STATE.capture
            engine.on_downgrade = lambda old, new, _e=engine: _on_downgrade(_e, old, new)
            _sys_ring = transcribe.EnergyRing()
            engine.sys_env = _sys_ring
            if cap is not None:
                cap.attach_sys_ring(_sys_ring)
            if transcribe.raw_mic_ring_on():
                _mic_ring = transcribe.EnergyRing()
                engine.mic_env = _mic_ring
                if cap is not None:
                    cap.attach_mic_ring(_mic_ring)
            # Re-affirm model/family from the built engine (set optimistically from resolve_model at Begin).
            STATE.model = engine.model_name
            STATE.family = engine.family
            # WP-1 ready flip: transcription is live NOW (engine built + started). Clear preparing, mark
            # ready, drop the prepare-progress object. Keep STATE.engine None + the buffer OPEN for the
            # drain below. This is the single point that eliminates the "stuck on preparing" residual.
            STATE.preparing = False
            STATE.model_ready = True
            STATE.prepare_phase = "ready"
            STATE.prepare = None
            STATE.prepare_error = None
        pb = STATE.pending_audio

    if not started_alive:                # P1-6: worker died at start -> retryable error, not a fake ready
        _catchup_failed("The transcription model failed to start on this computer. Please try again.")
        return

    if pb is None:                       # defensive: a full reset between publish and here cleared it
        try:
            engine.stop()
        except Exception:
            pass
        _release_prep_engine()
        return

    # If the load ran long enough for the buffer to hit its cap, some early audio was evicted and no
    # replay can bring it back. Say so once, here, so the transcript never presents an eviction as
    # silence (the console warning alone is invisible to the person reading the transcript).
    _mark_dropped_backlog(md_sink, browser_sink, pb, bool(STATE.recording))

    # --- phase 2: drain the buffer into the engine, in order, OUTSIDE STATE.lock, until a pass comes
    # back empty and the atomic flip publishes the engine. Because STATE.engine is still None, any
    # _feed running now is still APPENDING to pb, so live-during-drain chunks queue behind the backlog
    # rather than racing ahead of it.
    #
    # Three exits: SUPERSEDE (the session was replaced/reset) discards this private engine; a plain STOP
    # of this session (running still True, stopping/transcribing flipped) HANDS OFF - we return WITHOUT
    # stopping the engine, leaving it under STATE.preparing_engine for /api/stop to drain into the
    # transcript (P1-3); a DEAD worker fails the prepare (P1-6).
    while True:
        if _superseded():
            try:
                engine.stop()
            except Exception:
                pass
            _release_prep_engine()
            return
        if not _still_ours():
            return                       # Stop/partial-stop: hand the engine off to the stop path
        items = pb.take_all()
        if items:
            r, remainder = _replay(items)
            if r == "superseded":
                try:
                    engine.stop()
                except Exception:
                    pass
                _release_prep_engine()
                return
            if r == "dead":
                _catchup_failed("The transcription model stopped responding while catching up. "
                                "Please try again.", remainder)
                return
            if r == "handoff":
                # Stop/partial-stop mid-replay: return the unsubmitted tail to the FRONT of the buffer so
                # the stop path re-drains it (ahead of later chunks), and exit WITHOUT touching the engine.
                # The stop path joins this thread, so once we return it is the SOLE feeder - no race (P1-3).
                pb.putback_front(remainder)
                return
            continue                     # more live chunks may have queued behind; drain again
        # The buffer looked empty: try to finalise. Under STATE.lock (re-check identity) AND the
        # buffer's own lock (finalise_if_empty), the close + publish are atomic against _feed.append.
        stragglers = None
        publish_dead = False
        with STATE.lock:
            if not _still_ours():
                # Supersede -> discard; plain stop -> hand off (leave the engine for the stop path).
                if _superseded():
                    try:
                        engine.stop()
                    except Exception:
                        pass
                    _release_prep_engine()
                return
            if not _engine_alive(engine):
                # P1-6: never seal + publish a dead worker as ready. Handle below (outside the lock).
                # DEFERRED (1.13.2): this is a check-then-publish, so a worker that dies in the instant
                # BETWEEN this liveness check and finalise_if_empty's _publish could still be published
                # ready. Closing that window needs the Engine to expose liveness atomically with the seal
                # (an Engine-contract change), too risky for this cert release. The primary P1-6 fix
                # stands: a worker that is dead at this check surfaces a retryable prepare_error instead of
                # a permanent false "Listening".
                publish_dead = True
            else:
                def _publish():
                    # Runs under the buffer lock (see finalise_if_empty): from here append() returns False,
                    # so _feed feeds the now-published engine directly, in order, after everything drained.
                    # preparing/model_ready were already settled at phase-1 end; this only completes the
                    # backlog->live ordering flip. The private handle is dropped now STATE.engine owns it.
                    STATE.engine = engine
                    STATE.preparing = False
                    STATE.pending_audio = None
                    STATE.preparing_engine = None

                stragglers = pb.finalise_if_empty(_publish)
                if stragglers is None:
                    # Published. Arm the long-silence watcher (it reads STATE.engine + rings); a
                    # record-only session armed its own in start(). Under the lock, as _silence_start
                    # expects. Kept at the publish point (not phase-1 end) deliberately: the watcher reads
                    # STATE.engine, which is None until here; a minutes-scale silence nudge is unaffected
                    # by arming ~1 drain cycle later, and in the pathological never-publish case the audio
                    # is demonstrably non-silent (a growing backlog), so there is nothing to catch anyway.
                    _silence_start(STATE.capture)
        if publish_dead:
            _catchup_failed("The transcription model stopped responding while catching up. Please try again.")
            return
        if stragglers is None:
            break                        # finalised: the engine is live and _feed feeds it directly
        # A straggler slipped in between take_all and the flip: replay it (outside the lock, buffer
        # left OPEN) and loop. Converges because once caught up the engine consumes at >= real time. A
        # plain stop returns "handoff" here; supersede/dead discard or fail.
        r, remainder = _replay(stragglers)
        if r == "superseded":
            try:
                engine.stop()
            except Exception:
                pass
            _release_prep_engine()
            return
        if r == "dead":
            _catchup_failed("The transcription model stopped responding while catching up. "
                            "Please try again.", remainder)
            return
        if r == "handoff":
            pb.putback_front(remainder)
            return


@app.post("/api/start")
def start(req: StartRequest):
    if _summary_running():
        raise HTTPException(status_code=409, detail="A summary is being generated. Wait for it to finish before starting a new session, so the two never compete for the machine.")
    # t0-capture: the transcription model is NOT loaded here. Capture (and recording, if on) start
    # the instant Begin is clicked; the model builds on a background thread (_build_engine_async) and
    # attaches once ready, replaying everything held since t0. So /api/start returns immediately and
    # a slow first-time model download never blocks it or loses a moment of audio.
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

        # Engine-bound sinks are created NOW (not with the engine) so the SSE stream binds to a stable
        # browser sink from t0 and no replayed segment is missed by the live view, and so /api/status
        # can read md_sink.last_error. They are subscribed to the engine once it is built. md_sink
        # opens the transcript file immediately (header only); the engine fills it in once ready.
        browser_sink = BrowserSink()
        md_sink = sinks.MarkdownSink(output_path) if transcribe_on else None
        # Resolve the concrete model + family now (no load, no network) so /api/status and the client
        # can label the session honestly while the real model builds in the background; the built
        # engine re-affirms them.
        model_name = family = None
        if transcribe_on:
            try:
                model_name, family = transcribe.resolve_model(transcribe.TIER_CONFIG[tier]["model"], language, engine_pref)
            except Exception:
                model_name = family = None

        recorder = sinks.AudioRecorder(output_path.with_suffix("")) if record_on else None

        # Publish state BEFORE capture starts so the feed sees consistent flags. For a transcription
        # session the engine is still None here and `preparing` is True: that is the signal _feed uses
        # to HOLD engine-bound chunks in pending_audio until the background build attaches the engine.
        STATE.engine = None
        STATE.md_sink = md_sink
        STATE.browser_sink = browser_sink
        STATE.recorder = recorder
        STATE.recording = record_on
        STATE.recording_started = record_on   # latch: a start-time recording counts as "has recorded"
        STATE.transcribing = transcribe_on
        STATE.preparing = transcribe_on       # only a transcription session waits on a model load
        # Ready-state hardening (WP-1): a record-only session is "ready" immediately (nothing to load);
        # a transcription session is not ready until the background build flips model_ready at phase-1
        # end. Clear any prior error / progress so a fresh session starts clean.
        STATE.model_ready = (not transcribe_on)
        STATE.prepare_error = None
        STATE.prepare_phase = ""
        STATE.prepare = None
        STATE.preparing_engine = None   # no private engine yet; the builder sets it at phase-1 end
        STATE.build_thread = None
        # The transcription copy of every pre-engine chunk (bounded, drop-oldest). Recording, if on,
        # is already on disk from t0 via the recorder, so this only has to cover the model load.
        STATE.pending_audio = _PendingAudio(_PENDING_MAX_SAMPLES) if transcribe_on else None
        STATE.started_at = datetime.now()
        STATE.tier = tier if transcribe_on else None
        STATE.model = model_name if transcribe_on else None
        STATE.family = family if transcribe_on else None
        STATE.output_path = output_path
        STATE.language = (language or "auto") if transcribe_on else None
        STATE.source_kind = "live"
        STATE.running = True
        STATE.session_counted = False   # a fresh session is uncounted until it finalises
        STATE.mic_device = req.mic_device
        STATE.loopback_device = req.loopback_device
        STATE.chunk_seconds = chunk_seconds
        # Object-identity token: the background build re-checks this is still the current session
        # before publishing its engine (a fast Stop/switch/new-start can race). datetime.now() makes a
        # fresh object per session, and reset() sets started_at=None, so `is` never aliases.
        session_token = STATE.started_at

        # Recorder is tapped BEFORE the engine (see _feed), so the recording stays complete
        # even when transcription drops chunks under load.
        aec_live = req.aec_live if req.aec_live is not None else bool(config.load().get("aec_live", False))
        # Explicit request value wins (exactly like aec_live): the pre-meeting toggle's
        # settings save is async and unawaited, so toggle-then-immediately-Begin must not
        # start with the stale on-disk value. None -> settings default.
        agc_live = req.agc_live if req.agc_live is not None else bool(config.load().get("agc_live", True))
        cap = capture.AudioCapture(
            mic_device=req.mic_device,
            loopback_device=req.loopback_device,
            chunk_seconds=chunk_seconds,
            on_chunk=_feed,
            aec=aec_live,
            agc=agc_live,
            record_raw_mic=False,   # record the AEC-cleaned mic into the single stereo file, not a raw stem
        )
        # The engine's energy rings (SYS echo-veto reference, gain-invariant raw MIC) live on the
        # engine and are attached by _build_engine_async once it exists, not here, because there is no
        # engine yet. They only matter once transcription runs, which is after the model is ready.
        try:
            cap.start()
        except Exception as e:
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
        # Long-silence watcher: a record-only session has no engine (its rings live on the engine), so
        # it arms here exactly as before. A transcription session arms its watcher from
        # _build_engine_async, once the engine + rings exist.
        if not transcribe_on:
            _silence_start(cap)

        # Kick off the background model load + engine attach. Capture and recording are already live;
        # this only fills in transcription, replaying everything held since Begin once the model is up.
        if transcribe_on:
            build_args = (session_token, tier, language, prompt, engine_pref, md_sink, browser_sink)
            # Stash the build args so /api/prepare/retry can re-spawn the background build after a
            # bounded failure without restarting capture/recording (same session_token => same session).
            STATE.prepare_args = build_args
            build_thread = threading.Thread(
                target=_build_engine_async,
                args=build_args,
                daemon=True, name="engine-build")
            # Track the builder so a Stop during catch-up can join it (guaranteeing it has released the
            # private engine) before draining that engine into the transcript (P1-3).
            STATE.build_thread = build_thread
            build_thread.start()

        return {
            "tier": tier if transcribe_on else None,
            "model": model_name if transcribe_on else None,
            "family": family if transcribe_on else None,
            "language": STATE.language,
            "output_path": str(output_path),
            "chunk_seconds": chunk_seconds,
            "recording": record_on,
            "transcribing": transcribe_on,
            "audio_stem": str(output_path.with_suffix("")) if record_on else None,
            # t0-capture: capture is live now; transcription may still be loading its model. False here
            # tells the UI to show "preparing" and poll /api/status until model_ready flips true. A
            # record-only session has nothing to load, so it is ready immediately.
            "model_ready": not transcribe_on,
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
    context_override: Optional[str] = None  # per-run replacement for settings.default_context (None -> use setting)
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
        STATE.session_counted = False   # a fresh session is uncounted until it finalises

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
                active median looped, -28.3 dBFS was fine). Boosts a channel toward
                -20 dBFS active median only when it sits below -30 dBFS; healthy audio
                passes through byte-identical. Engine input only, never the source file.
                The logged landing is the MEASURED post-chain median: when the +20 dB
                static cap or the +-3 dB trim clamp engages, it is short of -20."""
                out, g, landing = audioboost.boost_if_quiet(chan)
                if g:
                    boosts.append(g)
                    where = (f"to {landing:.1f} dBFS" if landing is not None
                             else f"toward {audioboost.TARGET_DB:.0f} dBFS")
                    print(f"[transcribe-file] quiet-channel boost: {name} +{g:.1f} dB "
                          f"{where} active median", flush=True)
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
                    # and needlessly rewrites a healthy channel. The SYS ring below is built from
                    # the boosted SYS so the engine's echo veto sees the same signal it
                    # transcribes. (Before WP-4 the veto read the MIC from the BOOSTED chunk, which
                    # loosened its relative margin by the gain difference and pushed a boosted MIC
                    # over the -28 dBFS ceiling - safe-by-accident, since it could then veto almost
                    # nothing. The mic ring below restores the calibrated basis on this path too,
                    # so the veto is live again here: bleed the cross-channel gate missed can now
                    # be dropped, and the -28 ceiling means what it was measured to mean.)
                    # The MIC energy ring is built from the UNBOOSTED channel, for the same reason
                    # the cross-channel gate runs before the boost: the mic level tests (silence
                    # gate, echo veto ceiling) are calibrated on unboosted audio, and a per-channel
                    # boost would shift the MIC/SYS relationship by the gain difference. This is the
                    # file path's stand-in for the live raw tap - the recording is already
                    # AGC-processed, so the ring is marked non-raw and the silence gate derives its
                    # floor from the channel's own speech level instead of the absolute -45.
                    _mic_unboosted = mic_ch
                    mic_ch = _boost("MIC", mic_ch)
                    sys_ch = _boost("SYS", sys_ch)
                    # Build the SYS energy ring from the aligned far-end channel so the engine's echo
                    # veto has a reference for every MIC segment (the same mechanism it uses live).
                    # 100 ms frames on both, matching the live rings' resolution.
                    _retain = len(sys_ch) / 16000.0 + 60.0
                    _ring = transcribe.EnergyRing(retain_s=_retain, raw=False)
                    for _i in range(0, len(sys_ch), 1600):
                        _ring.add_block(_i / 16000.0, sys_ch[_i:_i + 1600])
                    engine.sys_env = _ring
                    if transcribe.raw_mic_ring_on():
                        _mring = transcribe.EnergyRing(retain_s=_retain, raw=False)
                        for _i in range(0, len(_mic_unboosted), 1600):
                            _mring.add_block(_i / 16000.0, _mic_unboosted[_i:_i + 1600])
                        engine.mic_env = _mring
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
            if not aborted:
                # One completed file transcription (not a user cancel). Before reset(),
                # like every other finalise path, so the count belongs to THIS session's
                # counted-once flag and not to whatever starts next.
                _bump_session_count()
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


def _bump_session_count():
    """Count one completed session, at most ONCE per session. Local only: it drives the
    one-time business-use nudge in the UI and never leaves this machine (the model is
    honour-system, not enforcement). A record-only session that is later re-transcribed
    can count twice; that is fine for a soft nudge and simpler than tracking session
    identity.

    Three different paths can finalise one session (the what="all" stop, the partial-stop
    drain that turns into a full finalise, and the window-close handler that may fire while
    a UI stop is already in flight), so "once per session" is enforced here by an explicit
    STATE flag cleared at session start and in STATE.reset(), not by hoping the call sites
    never overlap. Never raises - a failed count must not break finalisation - but the
    failure is printed (stdout is captured in volksmond.log) instead of vanishing, because
    a silently stuck counter is exactly how session_count sat at 1 for 50+ sessions."""
    with STATE.lock:
        if STATE.session_counted:
            return
        STATE.session_counted = True
    try:
        config.update({"session_count": int(config.load().get("session_count", 0)) + 1})
    except Exception as e:
        print(f"[session-count] could not record completed session: {e}", flush=True)


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
            # Transcription is what owns the energy rings, so once it goes there is nothing
            # left to measure silence with: stop the watcher rather than leave it blind.
            _silence_signal()
            engine = STATE.engine
            md_sink = STATE.md_sink
            # Stop-during-catch-up (P1-3 / P1-4): the model may have been mid-build, so STATE.engine is
            # None and the transcript-so-far lives only in the background builder's private engine
            # (STATE.preparing_engine) plus the still-held backlog (STATE.pending_audio). Take ownership
            # of both so we drain them into the transcript; clearing STATE.transcribing above already
            # makes _still_ours() false so the builder bails (no resurrection).
            prep_eng = STATE.preparing_engine
            pb = STATE.pending_audio
            build_thread = STATE.build_thread
            preparing_case = engine is None and prep_eng is not None
            # No engine at all (the model never finished loading) and audio still held: there is
            # nowhere to replay it, so the gap gets STATED in the transcript rather than left blank.
            abandoned_pb = pb if (engine is None and prep_eng is None) else None
            browser_sink = STATE.browser_sink
            was_recording = bool(STATE.recording)
            pending = (prep_eng.pending() if prep_eng else (engine.pending() if engine else 0))

            def _drain_transcription():
                _mark_abandoned_backlog(md_sink, browser_sink, abandoned_pb, was_recording)
                if preparing_case:
                    # Wait for the builder to RELEASE the private engine - return from its thread - before
                    # draining it, so we are the SOLE feeder (no two-feeder race). The builder releases
                    # within ~one chunk of the stop: _replay checks ownership BEFORE every enqueue, then
                    # hands its unsubmitted tail back to the buffer and returns (P1-3). We deliberately do
                    # NOT bound-and-proceed - draining or stopping the engine while the builder might still
                    # feed it is exactly the bug. In the (pre-enqueue-check makes it unreachable) event the
                    # builder never returned, this join simply keeps the session in `stopping` rather than
                    # reset/drain over a live builder. Recording (if on) is untouched; capture keeps running.
                    if build_thread is not None:
                        build_thread.join()
                    _drain_pending_into_engine(prep_eng, pb)
                    drained_engine = prep_eng
                else:
                    drained_engine = engine
                try:
                    if drained_engine is not None:
                        drained_engine.stop(drain=True)
                except Exception:
                    pass
                try:
                    if md_sink is not None:
                        md_sink.close()
                except Exception:
                    pass
                err = md_sink.last_error if md_sink else None
                cap_to_stop = None
                should_finalise = False
                with STATE.lock:
                    STATE.engine = None
                    STATE.md_sink = None
                    # Clear all preparation state so a stray retry / late builder cannot revive a
                    # transcription the user has stopped (P1-4). Harmless for the already-published case.
                    STATE.preparing_engine = None
                    STATE.pending_audio = None
                    STATE.preparing = False
                    STATE.model_ready = False
                    STATE.prepare = None
                    STATE.prepare_error = None
                    STATE.prepare_phase = ""
                    STATE.prepare_args = None
                    if err:
                        STATE.sink_error = err
                    if STATE.recording:
                        STATE.stopping = False  # recording carries on; session still running
                    else:
                        # Recording was also stopped while we were draining: nothing
                        # is left running, so finalise. Stop capture OUTSIDE the lock
                        # (it can block), then reset the session.
                        should_finalise = True
                        cap_to_stop = STATE.capture
                        STATE.capture = None
                if cap_to_stop is not None:
                    try:
                        cap_to_stop.stop()
                    except Exception:
                        pass
                if should_finalise:
                    # This branch IS the end of the session (transcription was stopped
                    # first, recording stopped while we drained), so it must count like
                    # any other finalise. Deliberately NOT conditional on there being a
                    # capture to stop: a failed device switch can leave STATE.capture None
                    # on a still-running session (see switch_device), and hanging the whole
                    # count-and-reset off `cap_to_stop` then left STATE.running stuck True
                    # forever, so every later session 409'd. Outside STATE.lock:
                    # _bump_session_count takes it itself. Idempotent, so a racing
                    # what="all" cannot double-count.
                    _bump_session_count()
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
        _silence_signal()   # the session is over; the watcher must not outlive the drain
        engine = STATE.engine
        cap = STATE.capture
        md_sink = STATE.md_sink
        rec = STATE.recorder
        # Stop-during-catch-up (P1-3): if the model was still catching up, STATE.engine is None and the
        # transcript-so-far lives only in the builder's private engine + the held backlog. Take both so
        # the drain saves the transcript instead of losing it. stopping=True already makes _still_ours()
        # false, so the builder bails (hands the engine off, does not discard it).
        prep_eng = STATE.preparing_engine
        pb = STATE.pending_audio
        build_thread = STATE.build_thread
        preparing_case = engine is None and prep_eng is not None
        # No engine at all (the model never finished loading): the held backlog has nowhere to go, so
        # the transcript says how much was never transcribed instead of just ending short.
        no_engine_case = engine is None and prep_eng is None
        browser_sink = STATE.browser_sink
        pending = (engine.pending() if engine else (prep_eng.pending() if prep_eng else 0))

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
        if no_engine_case:
            # Stopped before the model ever loaded. cap.stop() has just flushed the last chunk into the
            # buffer, so this covers the whole span; done here, before md_sink.close(), so the notice is
            # part of the saved transcript.
            _mark_abandoned_backlog(md_sink, browser_sink, pb, rec is not None)
        if preparing_case:
            # Model still catching up: cap.stop() above flushed the final chunk into pending_audio (the
            # engine is unpublished, so _feed buffers it). Wait for the builder to RELEASE the private
            # engine - return from its thread - before draining it, so we are the SOLE feeder. The builder
            # releases within ~one chunk of the stop (_replay checks ownership before every enqueue, then
            # hands its unsubmitted tail back to the buffer), so this join returns promptly. We do NOT
            # bound-and-proceed: draining or stopping the engine while the builder might still feed it is
            # exactly the P1-3 bug, and resetting over a live builder would let a new session start on top
            # of it. In the (unreachable) event the builder never returns, the session simply stays in
            # `stopping` rather than corrupt state. Then a slow-CPU Stop saves the transcript from t0.
            if build_thread is not None:
                build_thread.join()
            _drain_pending_into_engine(prep_eng, pb)
            drained_engine = prep_eng
        else:
            drained_engine = engine
        try:
            if drained_engine is not None:
                drained_engine.stop(drain=True)
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
    agc_live: Optional[bool] = None
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
    os_toasts: Optional[bool] = None        # Windows desktop notifications (toasts); shared by every notifying feature
    silence_nudge: Optional[bool] = None    # warn when nothing has been heard for a long stretch of a live session
    silence_nudge_minutes: Optional[int] = None   # how long that stretch is (picker: 3/5/10/15)
    struggle_nudge: Optional[bool] = None   # surface the live CPU auto-downgrade (banner + toast)
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

    summary_gpu_capable is True only when a usable GPU is present (NVIDIA on Windows,
    Apple silicon's Metal on a Mac) AND this build's llama.cpp can offload to it (the
    CPU-only wheel cannot), so the UI shows a GPU/CPU choice for summaries only when
    it would actually do something."""
    from .. import summarise as _summarise, accel
    try:
        gpu_capable = bool(accel.summary_gpu_ready() and _summarise.gpu_offload_supported())
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
        # Refuse removing ANY transcription model while an engine is loaded OR a session is still
        # preparing one (STATE.engine is None during prepare, so preparing must be checked too).
        if STATE.engine is not None or STATE.preparing:
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
        if STATE.engine is not None or STATE.preparing:
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
        if STATE.engine is not None or STATE.preparing:
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
        if STATE.engine is not None or STATE.preparing:
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
# the user clicks it. Every build has it EXCEPT the airtight offline-only edition and the Store
# (MSIX) edition, both of which compile it out entirely: the guard skips the route registration
# below, and the updatecheck module that performs the fetch is excluded from those bundles
# (sa-live-transcribe.spec), so the manifest URL is not even present. Offline strips it because it
# strips every network path; the Store edition strips ONLY this, because the Store owns updates
# (its siblings, the model-update check and the calendar, gate on OFFLINE_ONLY alone and stay in).
if not (buildflags.OFFLINE_ONLY or buildflags.STORE_BUILD):
    @app.post("/api/check-updates")
    def check_updates():
        """Manual, user-initiated app update check. Present in every build except the airtight
        offline edition and the Store edition (the guard skips this route, and updatecheck is
        excluded from those bundles). Delegates the one outbound HTTPS GET to updatecheck.check. No
        user data is sent, it runs only on click, and it is CSRF-protected like every other POST."""
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
        # "store" is the Microsoft Store (MSIX) edition: the connected build minus the in-app
        # update check, which the UI hides because the Store owns updates. Nothing else differs.
        "store": buildflags.STORE_BUILD,
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
            # 1 hour, not the 8-hour default: this poll only needs the reminder window (+2/-15 min),
            # and the lookahead is what drives Outlook's recurrence expansion, now paid once per
            # account. /api/calendar-seed keeps 8 hours, since that one is user-initiated.
            meeting = outlook_local.current_or_next_meeting(look_ahead_hours=1)
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

    class NotifyMeetingRequest(BaseModel):
        subject: Optional[str] = None
        # The meeting's start time, as the UI already has it (app.js reminderTick). Only used to
        # make the coalescing tag unique per OCCURRENCE: a weekly "Standup" is the same subject
        # every week, and a tag of just "meeting" would swallow every one after the first.
        start: Optional[str] = None

    @app.post("/api/notify-meeting")
    def notify_meeting(req: NotifyMeetingRequest):
        """Show a Windows notification for a meeting that is starting.

        Fired by the UI's own calendar poll (app.js reminderTick), not by a second server-side
        loop: the poll is client-driven either way, and the UI already owns the once-per-meeting
        bookkeeping, so a server-side watcher would mean another thread, another COM init and a
        duplicate dedup table for nothing.

        Sits with the calendar routes, inside the OFFLINE_ONLY guard and behind the same Business
        entitlement, because this notification IS the calendar reminder wearing a different coat.
        The generic notification machinery (notify.py) is not gated and not compiled out; only this
        calendar-shaped use of it is. Never starts anything: clicking the toast just brings the
        window forward, where the reminder card is already waiting."""
        if not licensing.current().has("calendar"):
            raise HTTPException(status_code=402, detail="Calendar reminders need a business licence.")
        from .. import notify
        # Tag per occurrence, not per feature: notify.show swallows an identical same-tag toast
        # while its balloon is outstanding, which is right for a 1 Hz watchdog and wrong for a
        # recurring meeting whose subject never changes. The start time is what makes two
        # occurrences of "Standup" two different notifications.
        shown = notify.show("A meeting is starting", (req.subject or "").strip(),
                            tag=f"meeting:{(req.start or '').strip()}")
        return {"shown": bool(shown)}


class SummariseRequest(BaseModel):
    file: str                      # session filename within the save location
    instruction: Optional[str] = None
    language: Optional[Literal["af", "en"]] = None  # output language for the summary
    include_notes: Optional[bool] = None            # fold the user's <stem>-notes.md into the summary


def _generate_summary(model_path, transcript, instruction, language, notes=None):
    """Run the local summariser, preferring the GPU when it is usable, falling back to CPU."""
    from .. import summarise as _summarise, accel
    # GPU only when: the user has not forced CPU, a usable GPU is present (NVIDIA on
    # Windows, Metal on Apple silicon), this build's llama.cpp can offload (the CPU
    # wheel cannot), and the model fits in the GPU memory budget with headroom.
    device = (config.load().get("summary_device") or "auto").strip().lower()
    n_gpu_layers = 0
    if (device != "cpu" and _summarise.gpu_offload_supported() and accel.summary_gpu_ready()
            and _summarise.fits_on_gpu(model_path, accel.summary_vram_mb())):
        n_gpu_layers = -1
    print(f"[summarise] device={device!r} offload={_summarise.gpu_offload_supported()} "
          f"gpu_ready={accel.summary_gpu_ready()} vram={accel.summary_vram_mb()} -> n_gpu_layers={n_gpu_layers}", flush=True)

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
