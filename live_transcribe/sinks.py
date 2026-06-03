"""Segment subscribers: stdout console + incremental Markdown file.

Also houses AudioRecorder, which is NOT a Segment subscriber, it consumes raw
audio chunks `(source, audio, t_start)` (the same signature as Engine.on_chunk)
and writes them to disk. It's tapped BEFORE the engine queue, so it captures
everything even when transcription drops chunks to backpressure, which is what
makes a faithful post-meeting re-transcribe possible.
"""
import atexit
import threading
import wave
from pathlib import Path

import numpy as np


def _fmt_ts(t):
    t = max(0.0, t)
    m = int(t // 60)
    s = int(t % 60)
    return f"{m:02d}:{s:02d}"


class StdoutSink:
    """Prints segments to stdout, one per line."""
    def __call__(self, segment):
        ts = _fmt_ts(segment.t_start)
        print(f"[{ts}] [{segment.source}] {segment.text}", flush=True)


class MarkdownSink:
    """Appends segments to a Markdown file, line-buffered, SIGINT-safe."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The clean rewrite on close() replaces the whole file from our in-memory segments, so
        # it is only safe when we own the entire file. If the path already has content we are
        # appending to someone else's data and must NOT rewrite it. The app always uses unique
        # filenames, so this only guards edge cases (CLI path reuse, an --output that exists).
        self._owns_file = not (self.path.exists() and self.path.stat().st_size > 0)
        from datetime import datetime
        self._started = datetime.now()
        self._segments = []         # retained so the end-of-session rewrite can order + clean
        # Line-buffered, append mode. utf-8 for Afrikaans diacritics.
        self.fh = open(self.path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self._closed = False
        self.last_error = None      # human-readable write/close failure, surfaced in the UI
        self._write_header()
        atexit.register(self.close)

    def _header_text(self):
        return (
            "# Volksmond session\n\n"
            f"- Started: {self._started.isoformat(timespec='seconds')}\n"
            f"- File: `{self.path.name}`\n"
            "- Format: `[mm:ss] [SOURCE] text`, where `MIC` is your microphone and `SYS` is everyone else (your computer's audio)\n\n"
            "---\n\n"
        )

    def _write_header(self):
        self.fh.write(self._header_text())
        self.fh.flush()

    def __call__(self, segment):
        if self._closed:
            return
        line = f"[{_fmt_ts(segment.t_start)}] [{segment.source}] {segment.text}\n"
        with self._lock:
            if self._closed:   # re-check under the lock: close() may have run since the check above
                return
            self._segments.append(segment)
            try:
                self.fh.write(line)
                self.fh.flush()
            except Exception as e:
                self.last_error = f"Could not write the transcript to {self.path.name}: {e}"
                print(f"[markdown-sink] write error: {e}", flush=True)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            snapshot = list(self._segments)   # under the lock: a stable view for the rewrite
            try:
                self.fh.write("\n---\n\n_End of session._\n")
                self.fh.flush()
                self.fh.close()
            except Exception as e:
                self.last_error = f"Could not finalise the transcript {self.path.name}: {e}"
                print(f"[markdown-sink] close error: {e}", flush=True)
        self._rewrite_clean(snapshot)

    def _rewrite_clean(self, segments):
        """Rewrite the finished transcript in chronological order with MIC echoes of the
        system audio removed (see dedup.strip_mic_echoes). Done once at the end (guarded by
        the _closed flag set under the lock), when both channels are fully transcribed, so the
        order they arrived in no longer matters.

        Best-effort and crash-safe: write a temp file then atomically replace the original,
        and on any failure leave the incrementally written file untouched, so a transcript
        is never lost.
        """
        if not segments or not self._owns_file:
            return  # nothing to clean, or we are appending to a file we did not create
        try:
            from . import dedup
            ordered = sorted(segments, key=lambda s: s.t_start)
            kept = dedup.strip_mic_echoes(ordered)
            body = "".join(f"[{_fmt_ts(s.t_start)}] [{s.source}] {s.text}\n" for s in kept)
            text = self._header_text() + body + "\n---\n\n_End of session._\n"
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            print(f"[markdown-sink] clean rewrite skipped: {e}", flush=True)


class AudioRecorder:
    """Writes raw 16k mono chunks to a WAV per source, incrementally.

    One file per source (`<stem>-MIC.wav`, `<stem>-SYS.wav`), kept separate to
    match the capture pipeline (never mixed in the audio domain) and to preserve
    the free MIC/SYS diarisation split for a later re-transcribe.

    POPIA: only instantiated when the user passes --keep-audio, which requires
    consent from everyone recorded. Audio is the highest-sensitivity artefact;
    do not enable by default.
    """
    TARGET_RATE = 16000

    def __init__(self, path_stem):
        self.stem = Path(path_stem)
        self.stem.parent.mkdir(parents=True, exist_ok=True)
        self._writers = {}     # source -> wave.Wave_write
        self._lock = threading.Lock()
        self._closed = False
        self.last_error = None      # human-readable write/close failure, surfaced in the UI
        atexit.register(self.close)

    def on_chunk(self, source, audio, t_start):
        if self._closed:
            return
        with self._lock:
            w = self._writers.get(source)
            if w is None:
                path = self.stem.with_name(f"{self.stem.name}-{source}.wav")
                w = wave.open(str(path), "wb")
                w.setnchannels(1)
                w.setsampwidth(2)          # int16
                w.setframerate(self.TARGET_RATE)
                self._writers[source] = w
                print(f"[recorder] writing {path.name}", flush=True)
            pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            try:
                w.writeframes(pcm16)
            except Exception as e:
                self.last_error = f"Could not write audio ({source}): {e}"
                print(f"[recorder] write error ({source}): {e}", flush=True)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for w in self._writers.values():
                try:
                    w.close()
                except Exception as e:
                    self.last_error = f"Could not finalise the recording: {e}"
                    print(f"[recorder] close error: {e}", flush=True)
