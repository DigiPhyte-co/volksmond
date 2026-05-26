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
        # Line-buffered, append mode. utf-8 for Afrikaans diacritics.
        self.fh = open(self.path, "a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()
        self._closed = False
        self.last_error = None      # human-readable write/close failure, surfaced in the UI
        self._write_header()
        atexit.register(self.close)

    def _write_header(self):
        from datetime import datetime
        self.fh.write("# Volksmond session\n\n")
        self.fh.write(f"- Started: {datetime.now().isoformat(timespec='seconds')}\n")
        self.fh.write(f"- File: `{self.path.name}`\n")
        self.fh.write("- Format: `[mm:ss] [SOURCE] text`, where `MIC` is your microphone and `SYS` is everyone else (your computer's audio)\n\n")
        self.fh.write("---\n\n")
        self.fh.flush()

    def __call__(self, segment):
        if self._closed:
            return
        line = f"[{_fmt_ts(segment.t_start)}] [{segment.source}] {segment.text}\n"
        with self._lock:
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
            try:
                self.fh.write("\n---\n\n_End of session._\n")
                self.fh.flush()
                self.fh.close()
            except Exception as e:
                self.last_error = f"Could not finalise the transcript {self.path.name}: {e}"
                print(f"[markdown-sink] close error: {e}", flush=True)


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
