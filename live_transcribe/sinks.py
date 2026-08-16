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


def _read_wav_i16(path):
    """Read a mono 16-bit PCM WAV into an int16 numpy array."""
    with wave.open(str(path), "rb") as r:
        frames = r.readframes(r.getnframes())
    return np.frombuffer(frames, dtype="<i2")


class StdoutSink:
    """Prints segments to stdout, one per line."""
    def __call__(self, segment):
        ts = _fmt_ts(segment.t_start)
        print(f"[{ts}] [{segment.source}] {segment.text}", flush=True)


class MarkdownSink:
    """Appends segments to a Markdown file, line-buffered, SIGINT-safe.

    source_labels: optional {internal_tag: display_label} map applied at write time only
    (e.g. MIC/SYS -> Speaker L/Speaker R for a stereo interview upload). The retained
    segments keep their internal tags, so the close-time echo dedup still works."""
    def __init__(self, path, source_labels=None):
        self.path = Path(path)
        self._labels = source_labels or {}
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
        if self._labels:
            # Written before the audio is decoded, so it must stay honest when a stereo
            # interview upload turns out to be mono (the body is then one `FILE` track).
            fmt = ("- Format: `[mm:ss] [SPEAKER] text`, where `Speaker L` and `Speaker R` are the "
                   "left and right channels of a stereo interview recording (a mono file is one `FILE` track)\n\n")
        else:
            fmt = "- Format: `[mm:ss] [SOURCE] text`, where `MIC` is your microphone and `SYS` is everyone else (your computer's audio)\n\n"
        return (
            "# Volksmond session\n\n"
            f"- Started: {self._started.isoformat(timespec='seconds')}\n"
            f"- File: `{self.path.name}`\n"
            + fmt +
            "---\n\n"
        )

    def _write_header(self):
        self.fh.write(self._header_text())
        self.fh.flush()

    def __call__(self, segment):
        if self._closed:
            return
        line = f"[{_fmt_ts(segment.t_start)}] [{self._labels.get(segment.source, segment.source)}] {segment.text}\n"
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
            body = "".join(f"[{_fmt_ts(s.t_start)}] [{self._labels.get(s.source, s.source)}] {s.text}\n" for s in kept)
            text = self._header_text() + body + "\n---\n\n_End of session._\n"
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.path)
        except Exception as e:
            print(f"[markdown-sink] clean rewrite skipped: {e}", flush=True)


class AudioRecorder:
    """Writes 16k mono chunks to a WAV per source incrementally, then folds them
    into ONE stereo file on close.

    During the session each source streams to its own file (`<stem>-MIC.wav`,
    `<stem>-SYS.wav`) so a crash mid-meeting still leaves recoverable audio. On
    close, when both channels exist they are interleaved into a single
    `<stem>.wav` (LEFT = your mic, RIGHT = everyone else) and the per-source files
    are removed, leaving one clean playable file that still carries the MIC/SYS
    split (left/right) for a diarised re-transcribe. With live AEC on (the default)
    the mic channel is already echo-cancelled, so the stereo file needs no further
    echo work.

    POPIA: only instantiated when the user passes --keep-audio, which requires
    consent from everyone recorded. Audio is the highest-sensitivity artefact;
    do not enable by default.
    """
    TARGET_RATE = 16000
    # Zero-fill a source only when its written samples lag the chunk's wall-clock
    # position (t_start) by more than this. The chunker derives t_start from the
    # session clock minus the buffered span, so its jitter is bounded by the WASAPI
    # block size (~0.5 s), the 0.1 s chunker poll, and resampler latency: well under
    # 1 s in practice. 2 s sits comfortably above all of that (a normal session never
    # zero-fills) yet far below the real failure, WASAPI loopback delivering nothing
    # for tens of seconds while no application renders audio.
    GAP_TOLERANCE_S = 2.0
    _FILL_BLOCK = TARGET_RATE * 10   # write gap silence in 10 s blocks to bound memory

    def __init__(self, path_stem, *, rebase=False):
        self.stem = Path(path_stem)
        self.stem.parent.mkdir(parents=True, exist_ok=True)
        self._writers = {}     # source -> wave.Wave_write
        self._samples_written = {}   # source -> samples on disk, to place chunks on the session clock
        self._gap_warned = set()     # sources already warned about a backwards t_start
        self._lock = threading.Lock()
        self._closed = False
        # Mid-session ("record from here") recorder: zero the timeline on the FIRST chunk of ANY
        # source instead of zero-filling the elapsed session lead (up to hours) with silence. One
        # SHARED offset across sources keeps MIC/SYS relatively aligned in the stereo fold, so the
        # file plays from 0. Default False: a start-time recorder keeps today's wall-clock placement
        # byte-for-byte (a session that begins recording at t=0 has nothing to rebase anyway).
        self._rebase = rebase
        self._t_offset = None    # set once, under the lock, by the first chunk seen
        self.last_error = None      # human-readable write/close failure, surfaced in the UI
        atexit.register(self.close)

    def on_chunk(self, source, audio, t_start):
        if self._closed:
            return
        with self._lock:
            if self._rebase:
                # First chunk (any source) anchors the shared zero; every later chunk of either
                # source subtracts the SAME offset, so the wall-clock placement below still keeps
                # the two channels aligned relative to each other, just shifted to start at 0.
                if self._t_offset is None:
                    self._t_offset = t_start
                t_start = max(0.0, t_start - self._t_offset)
            w = self._writers.get(source)
            if w is None:
                path = self.stem.with_name(f"{self.stem.name}-{source}.wav")
                w = wave.open(str(path), "wb")
                w.setnchannels(1)
                w.setsampwidth(2)          # int16
                w.setframerate(self.TARGET_RATE)
                self._writers[source] = w
                self._samples_written[source] = 0
                print(f"[recorder] writing {path.name}", flush=True)
            # Place the chunk at its wall-clock position. WASAPI loopback delivers NO
            # callbacks while no application renders audio, so a source can simply stop
            # producing for a while (start of session before the call renders, or a call
            # ending mid-session); appending by raw sample index would shift everything
            # after the gap earlier and permanently misalign the stereo fold. The expected
            # index is recomputed from t_start each chunk (absolute, not accumulated), so
            # rounding never drifts.
            written = self._samples_written[source]
            expected = int(t_start * self.TARGET_RATE)
            gap = expected - written
            if gap > int(self.GAP_TOLERANCE_S * self.TARGET_RATE):
                print(f"[recorder] {source}: no audio for {gap / self.TARGET_RATE:.1f}s "
                      f"(source idle), filling with silence to stay time-aligned", flush=True)
            elif gap < -int(self.GAP_TOLERANCE_S * self.TARGET_RATE):
                # Overlap: chunk starts before what we already wrote (should not happen;
                # chunks arrive in order per source). Append as-is rather than corrupt
                # what is on disk; warn once per source.
                if source not in self._gap_warned:
                    self._gap_warned.add(source)
                    print(f"[recorder] warning: {source} chunk at t={t_start:.1f}s is "
                          f"{-gap / self.TARGET_RATE:.1f}s behind the audio already written; "
                          f"appending as-is", flush=True)
                gap = 0
            else:
                gap = 0     # within normal chunker jitter: append, no fill
            pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
            try:
                for off in range(0, gap, self._FILL_BLOCK):
                    w.writeframes(b"\x00\x00" * min(self._FILL_BLOCK, gap - off))
                w.writeframes(pcm16)
                self._samples_written[source] = written + gap + len(pcm16) // 2
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
        # Channels are now flushed to disk. Fold them into one stereo file outside
        # the lock; best-effort, and the per-source channels are kept if it fails.
        self._finalise_recording()

    def _finalise_recording(self):
        """Fold the per-source channels into a single `<stem>.wav` and remove them.

        Both channels -> stereo (LEFT = MIC / you, RIGHT = SYS / everyone else), so the one
        file plays back cleanly AND still carries the diarisation split for a re-transcribe
        (which reads left as MIC, right as SYS). A single channel -> that channel as a mono
        `<stem>.wav`. Best-effort: on any failure the per-source files are left untouched as
        the source of truth, and only removed once the single file is written.
        """
        mic = self.stem.with_name(f"{self.stem.name}-MIC.wav")
        sys_ = self.stem.with_name(f"{self.stem.name}-SYS.wav")
        out = self.stem.with_name(f"{self.stem.name}.wav")
        have_mic, have_sys = mic.is_file(), sys_.is_file()
        if not (have_mic or have_sys):
            return
        try:
            # `with` guarantees the writer is closed even if writeframes raises mid-write,
            # so a partial-write failure leaves no leaked handle (and the per-source channels
            # remain the source of truth).
            with wave.open(str(out), "wb") as w:
                w.setsampwidth(2)
                w.setframerate(self.TARGET_RATE)
                if have_mic and have_sys:
                    # Both channels are already wall-clock aligned internally (on_chunk
                    # zero-fills any no-delivery gap at its true position), so tail-padding
                    # both to the same final length, the session duration as seen by the
                    # longer channel, is all that is left to do.
                    a, b = _read_wav_i16(mic), _read_wav_i16(sys_)
                    n = max(len(a), len(b))
                    a = np.pad(a, (0, n - len(a)))
                    b = np.pad(b, (0, n - len(b)))
                    stereo = np.empty(n * 2, dtype="<i2")
                    stereo[0::2] = a          # left  = MIC (you)
                    stereo[1::2] = b          # right = SYS (everyone else)
                    w.setnchannels(2)
                    w.writeframes(stereo.tobytes())
                else:
                    w.setnchannels(1)
                    w.writeframes(_read_wav_i16(mic if have_mic else sys_).tobytes())
            print(f"[recorder] wrote {out.name}", flush=True)
        except Exception as e:
            print(f"[recorder] stereo fold skipped: {e}", flush=True)
            return
        # Only reached on a successful write: drop the per-source channels.
        for p in (mic, sys_):
            try:
                p.unlink()
            except OSError:
                pass
