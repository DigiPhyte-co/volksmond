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


RECORDING_FORMATS = {
    "flac": (".flac", "FLAC", "PCM_16"),
    "opus": (".opus", "OGG", "OPUS"),
    "wav": (".wav", "WAV", "PCM_16"),
}
RECORDING_EXTENSIONS = tuple(spec[0] for spec in RECORDING_FORMATS.values())


def normalise_recording_format(value):
    """Return a supported recording format, defaulting invalid settings to FLAC."""
    value = str(value or "").strip().lower()
    return value if value in RECORDING_FORMATS else "flac"


def recording_suffix(recording_format):
    return RECORDING_FORMATS[normalise_recording_format(recording_format)][0]


def recording_format_from_suffix(suffix):
    suffix = str(suffix or "").lower()
    return next((name for name, spec in RECORDING_FORMATS.items() if spec[0] == suffix), "flac")


def _load_soundfile():
    # Lazy by design: if the bundled libsndfile DLL is ever broken or missing, the
    # recorder can still start immediately through the standard-library WAV path.
    import soundfile
    return soundfile


class _SoundFileWriter:
    """Small writeframes-compatible wrapper around soundfile.SoundFile."""

    def __init__(self, soundfile, path, rate, channels, recording_format):
        _suffix, container, subtype = RECORDING_FORMATS[recording_format]
        self._path = str(path)
        self._channels = channels
        self._file = soundfile.SoundFile(
            self._path, mode="w", samplerate=rate, channels=channels,
            format=container, subtype=subtype,
        )

    def writeframes(self, frames):
        audio = np.frombuffer(frames, dtype="<i2")
        if self._channels == 2:
            audio = audio.reshape(-1, 2)
        self._file.write(audio)
        # PyAV can decode FLAC's flushed blocks before close. Ogg/Opus writes its
        # final page only on close, so a crash can lose its buffered tail.
        self._file.flush()

    def close(self):
        self._file.close()


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
    """Writes 16k mono chunks per source incrementally, then folds them into one file.

    During the session each source streams to its OWN per-source file as lossless FLAC,
    whatever final format the user chose, so a crash leaves recoverable per-source audio
    up to its last flushed block for every format (FLAC flushes block by block; it never
    needs a clean close like Ogg/Opus). On close, both channels are folded into one file in
    the chosen format (LEFT = your mic, RIGHT = everyone else) and the per-source files are
    removed. Encoding to the chosen format happens ONCE, in that final fold, so Opus is not
    encoded per-source and then re-encoded. The final file still carries the MIC/SYS split
    for a diarised re-transcribe. With live AEC on (the default) the mic channel is already
    echo-cancelled, so the stereo file needs no further echo work.

    POPIA: the app records every live session by default (config record_sessions), because a
    live transcript that fails is only recoverable from the audio. That is honest, not silent:
    the file is written to the user's own save folder on this machine and never leaves it, the
    live screen says it is recording for as long as it runs, the finish screen shows where the
    file is and deletes it on one click, and Settings turns recording off for good. Audio is
    still the highest-sensitivity artefact, so anything that touches it keeps those guarantees.

    FLAC is the product default (SUFFIX) and keeps the recorder's exact 16-bit PCM.
    Opus uses an Ogg container for much smaller speech recordings. WAV is the
    standard-library escape hatch and remains the constructor default so low-level
    alignment callers retain their byte-exact legacy behaviour. The web app always
    passes the user's configured format explicitly.
    """
    TARGET_RATE = 16000
    # Product default. Direct low-level callers keep WAV unless they pass recording_format;
    # the web app always passes the saved preference, whose default is FLAC.
    SUFFIX = ".flac"
    # Zero-fill a source only when its written samples lag the chunk's wall-clock
    # position (t_start) by more than this. The chunker derives t_start from the
    # session clock minus the buffered span, so its jitter is bounded by the WASAPI
    # block size (~0.5 s), the 0.1 s chunker poll, and resampler latency: well under
    # 1 s in practice. 2 s sits comfortably above all of that (a normal session never
    # zero-fills) yet far below the real failure, WASAPI loopback delivering nothing
    # for tens of seconds while no application renders audio.
    GAP_TOLERANCE_S = 2.0
    _FILL_BLOCK = TARGET_RATE * 10   # write gap silence in 10 s blocks to bound memory
    _OPUS_RATES = {8000, 12000, 16000, 24000, 48000}

    def __init__(self, path_stem, *, anchor=None, recording_format="wav"):
        self.stem = Path(path_stem)
        self.stem.parent.mkdir(parents=True, exist_ok=True)
        # `recording_format`/`SUFFIX` are the FINAL fold format (what the user chose).
        self.recording_format = normalise_recording_format(recording_format)
        self.SUFFIX = recording_suffix(self.recording_format)
        self._soundfile = None
        self._fallback_warned = False
        if self.recording_format == "opus" and self.TARGET_RATE not in self._OPUS_RATES:
            print(f"[recorder] Opus does not support {self.TARGET_RATE} Hz; using FLAC", flush=True)
            self.recording_format = "flac"
            self.SUFFIX = recording_suffix(self.recording_format)
        # Per-source channel files are ALWAYS FLAC: lossless (so the chosen format is applied
        # only once, in the final fold), crash-safe (flushed block by block, no clean close
        # needed), and no rate guard needed. FLAC needs soundfile; if it is unavailable the
        # whole recorder falls back to WAV (per-source WAV, fold WAV), the standard-library
        # escape hatch. A caller that asked for WAV keeps WAV throughout, no soundfile needed.
        self._source_format = "flac" if self.recording_format != "wav" else "wav"
        self._source_suffix = recording_suffix(self._source_format)
        if self.recording_format != "wav":
            try:
                self._soundfile = _load_soundfile()
            except Exception as e:
                self._fallback_to_wav(f"soundfile unavailable: {e}")
        self._writers = {}     # source -> wave.Wave_write or _SoundFileWriter
        self._source_paths = {}
        self._samples_written = {}   # source -> samples on disk, to place chunks on the session clock
        self._gap_warned = set()     # sources already warned about a backwards t_start
        self._lock = threading.Lock()
        self._closed = False
        # Mid-session ("record from here") recorder: `anchor` is the session-clock time of the click,
        # captured at the endpoint and SHARED across sources. Audio before it is never written - a
        # chunk that ends at or before the anchor is dropped whole, and the chunk straddling it is
        # sliced at the anchor - and the SAME anchor is then subtracted from every source, so the file
        # starts at 0 with MIC/SYS still aligned to the one shared moment (never a per-source
        # first-chunk offset, which would place the two channels at different zeros). None (default) =
        # a start-time recorder, which keeps today's wall-clock placement byte-for-byte.
        self._anchor = anchor
        self.last_error = None      # human-readable write/close failure, surfaced in the UI
        atexit.register(self.close)

    def _fallback_to_wav(self, reason):
        # WAV for both the per-source files and the final fold: the standard-library escape
        # hatch when soundfile/libsndfile cannot be used.
        self.recording_format = "wav"
        self.SUFFIX = recording_suffix("wav")
        self._source_format = "wav"
        self._source_suffix = recording_suffix("wav")
        if not self._fallback_warned:
            self._fallback_warned = True
            print(f"[recorder] warning: {reason}; using WAV so the meeting is still recorded", flush=True)

    def _new_writer(self, path, channels, fmt):
        if fmt != "wav":
            return _SoundFileWriter(
                self._soundfile, path, self.TARGET_RATE, channels, fmt,
            )
        writer = wave.open(str(path), "wb")
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(self.TARGET_RATE)
        return writer

    def _open_source_writer(self, source):
        # Per-source files are FLAC (or WAV under the fallback), never the chosen fold format.
        path = self.stem.with_name(f"{self.stem.name}-{source}{self._source_suffix}")
        try:
            writer = self._new_writer(path, 1, self._source_format)
        except Exception as e:
            if self._source_format == "wav":
                raise
            failed_format = self._source_format
            failed_path = path
            self._fallback_to_wav(f"could not open the {failed_format.upper()} writer: {e}")
            try:
                failed_path.unlink()
            except OSError:
                pass
            path = self.stem.with_name(f"{self.stem.name}-{source}{self._source_suffix}")
            writer = self._new_writer(path, 1, self._source_format)
        self._source_paths[source] = path
        return path, writer

    def _read_source_i16(self, path):
        if path.suffix.lower() == ".wav":
            return _read_wav_i16(path)
        data, rate = self._soundfile.read(str(path), dtype="int16", always_2d=False)
        if rate != self.TARGET_RATE:
            raise ValueError(f"unexpected recording rate {rate} Hz")
        return np.asarray(data, dtype="<i2")

    def on_chunk(self, source, audio, t_start):
        if self._closed:
            return
        with self._lock:
            # Re-check under the lock. close() flips _closed and finalises (folds + deletes the
            # per-source files) under this SAME lock, so a stop landing between the check above and
            # here must not create a new writer AFTER finalisation: that would be an orphaned,
            # never-finalised WAV handle atexit cannot fold. Same discipline as MarkdownSink.
            if self._closed:
                return
            if self._anchor is not None:
                # Never write anything captured before the click. A chunk ending at or before the
                # anchor is dropped whole; the chunk straddling it is sliced at the anchor. Then the
                # ONE shared anchor is subtracted from every source, so both channels start at 0 and
                # stay aligned to the same moment - nothing before the click reaches disk.
                n = len(audio)
                t_end = t_start + n / self.TARGET_RATE
                if t_end <= self._anchor:
                    return
                if t_start < self._anchor:
                    # Cut with ceil, not int(): with a non-integer-second anchor an int() floor keeps
                    # the sample straddling the anchor, leaking one pre-click sample onto disk.
                    skip = min(int(np.ceil((self._anchor - t_start) * self.TARGET_RATE)), n)
                    audio = audio[skip:]
                    t_start = self._anchor
                t_start = t_start - self._anchor
            w = self._writers.get(source)
            first = w is None      # this source's first retained chunk (drives the anchor placement)
            if w is None:
                try:
                    path, w = self._open_source_writer(source)
                except Exception as e:
                    self.last_error = f"Could not open the audio recording ({source}): {e}"
                    print(f"[recorder] open error ({source}): {e}", flush=True)
                    return
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
            # Under a shared anchor, round so identical real times map to identical sample indices on
            # every source (the stereo alignment invariant); the wall-clock path keeps int().
            expected = (round(t_start * self.TARGET_RATE) if self._anchor is not None
                        else int(t_start * self.TARGET_RATE))
            gap = expected - written
            tol = int(self.GAP_TOLERANCE_S * self.TARGET_RATE)
            if self._anchor is not None and first:
                # A source's FIRST retained chunk after the anchor must land at its EXACT offset from
                # the shared origin, zero-filling the lead. The jitter-tolerance collapse below is
                # only safe between CONSECUTIVE chunks of the SAME source; applying it to a first
                # chunk that begins up to GAP_TOLERANCE_S after the anchor would snap it to sample 0
                # and skew this channel against the other (broken stereo/speaker separation).
                gap = max(0, gap)
                if gap:
                    print(f"[recorder] {source}: first audio {gap / self.TARGET_RATE:.2f}s after the "
                          f"record point; zero-filling the lead to keep the channels aligned", flush=True)
            elif gap > tol:
                print(f"[recorder] {source}: no audio for {gap / self.TARGET_RATE:.1f}s "
                      f"(source idle), filling with silence to stay time-aligned", flush=True)
            elif gap < -tol:
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
        """Fold the per-source channels into one selected-format file and remove them.

        Both channels become stereo (LEFT = MIC / you, RIGHT = SYS / everyone else), so the one
        file plays back cleanly and still carries the diarisation split for a re-transcribe. A
        single channel stays mono. Best-effort: on any failure the per-source files are left
        untouched as the source of truth, and only removed once the single file is written.
        """
        mic = self._source_paths.get("MIC")
        sys_ = self._source_paths.get("SYS")
        out = self.stem.with_name(f"{self.stem.name}{self.SUFFIX}")
        # Per-source files carry the per-source suffix (FLAC, or WAV under the fallback), not
        # the fold SUFFIX, so probe for them with _source_suffix.
        mic = mic if mic is not None else out.with_name(f"{self.stem.name}-MIC{self._source_suffix}")
        sys_ = sys_ if sys_ is not None else out.with_name(f"{self.stem.name}-SYS{self._source_suffix}")
        have_mic, have_sys = mic.is_file(), sys_.is_file()
        if not (have_mic or have_sys):
            return
        writer = None
        try:
            if have_mic and have_sys:
                # Both channels are already wall-clock aligned internally (on_chunk
                # zero-fills any no-delivery gap at its true position), so tail-padding
                # both to the same final length, the session duration as seen by the
                # longer channel, is all that is left to do.
                a, b = self._read_source_i16(mic), self._read_source_i16(sys_)
                n = max(len(a), len(b))
                a = np.pad(a, (0, n - len(a)))
                b = np.pad(b, (0, n - len(b)))
                audio = np.empty(n * 2, dtype="<i2")
                audio[0::2] = a          # left  = MIC (you)
                audio[1::2] = b          # right = SYS (everyone else)
                channels = 2
            else:
                audio = self._read_source_i16(mic if have_mic else sys_)
                channels = 1
            try:
                writer = self._new_writer(out, channels, self.recording_format)
            except Exception as e:
                if self.recording_format == "wav":
                    raise
                failed_format = self.recording_format
                failed_out = out
                self._fallback_to_wav(f"could not open the final {failed_format.upper()} writer: {e}")
                try:
                    failed_out.unlink()
                except OSError:
                    pass
                out = self.stem.with_name(f"{self.stem.name}{self.SUFFIX}")
                writer = self._new_writer(out, channels, self.recording_format)
            writer.writeframes(audio.tobytes())
            writer.close()
            writer = None
            print(f"[recorder] wrote {out.name}", flush=True)
        except Exception as e:
            print(f"[recorder] stereo fold skipped: {e}", flush=True)
            return
        finally:
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass
        # Only reached on a successful write: drop the per-source channels.
        for p in (mic, sys_):
            try:
                p.unlink()
            except OSError:
                pass
