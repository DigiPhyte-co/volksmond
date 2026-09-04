"""Tests for FLAC, Opus and WAV meeting recording formats.

Run:  python tests/test_recording_format.py   (from the project root; exit 0 = pass)
"""
import builtins
import contextlib
import io
import os
import sys
import shutil
import uuid
from pathlib import Path

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import soundfile as sf
from faster_whisper.audio import decode_audio

from live_transcribe import config, sinks
from live_transcribe.web import app as webapp

ROOT = Path(__file__).resolve().parent.parent
RATE = sinks.AudioRecorder.TARGET_RATE


def tone(seconds=2.0, frequency=440.0):
    t = np.arange(int(seconds * RATE), dtype=np.float32) / RATE
    return (0.3 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


class _workspace_tmp:
    """Native libsndfile needs a sandbox-writable path during headless tests."""

    def __enter__(self):
        self.path = ROOT / (".recording-format-" + uuid.uuid4().hex)
        self.path.mkdir()
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.path)
        return False


def test_recording_format_default_and_suffix_helpers():
    assert config.DEFAULTS["record_sessions"] is True, "the existing recording default must stay on"
    assert config.DEFAULTS["recording_format"] == "flac"
    assert sinks.AudioRecorder.SUFFIX == ".flac", "the product default extension must be FLAC"
    assert sinks.RECORDING_EXTENSIONS == (".flac", ".opus", ".wav")
    for name, ext in (("flac", ".flac"), ("opus", ".opus"), ("wav", ".wav")):
        assert sinks.recording_suffix(name) == ext
        assert sinks.recording_format_from_suffix(ext.upper()) == name
    assert sinks.normalise_recording_format("broken") == "flac"
    for name in ("flac", "opus", "wav"):
        assert webapp.SettingsPatch(recording_format=name).recording_format == name
    try:
        webapp.SettingsPatch(recording_format="aac")
    except Exception:
        pass
    else:
        raise AssertionError("the settings endpoint must reject unsupported recording formats")


def test_each_format_round_trips_through_faster_whisper():
    expected = 2 * RATE
    tolerances = {"flac": 0.01, "opus": 0.05, "wav": 0.01}
    counts = {}
    with _workspace_tmp() as tmp:
        for recording_format in ("flac", "opus", "wav"):
            stem = tmp / recording_format
            recorder = sinks.AudioRecorder(stem, recording_format=recording_format)
            recorder.on_chunk("MIC", tone(frequency=440.0), 0.0)
            if recording_format == "flac":
                partial_path = stem.with_name(stem.name + "-MIC.flac")
                partial = decode_audio(str(partial_path), sampling_rate=RATE)
                assert 0 < len(partial) <= expected, "flushed FLAC blocks must be crash-readable"
                print(f"  FLAC pre-close readable samples: {len(partial)}/{expected}")
            recorder.on_chunk("SYS", tone(frequency=660.0), 0.0)
            assert recorder._samples_written == {"MIC": expected, "SYS": expected}
            recorder.close()
            path = stem.with_suffix(sinks.recording_suffix(recording_format))
            assert path.is_file(), f"{recording_format} final recording was not written"
            assert not stem.with_name(f"{stem.name}-MIC{path.suffix}").exists()
            decoded = decode_audio(str(path), sampling_rate=RATE)
            counts[recording_format] = len(decoded)
            error = abs(len(decoded) - expected) / expected
            assert error <= tolerances[recording_format], (
                recording_format, len(decoded), expected, error,
            )
            info = sf.info(str(path))
            assert info.samplerate == RATE and info.channels == 2
            left, right = decode_audio(str(path), sampling_rate=RATE, split_stereo=True)
            assert len(left) == len(right) == len(decoded)
            if recording_format == "flac":
                assert info.format == "FLAC" and info.subtype == "PCM_16"
                assert len(decoded) == expected, "lossless FLAC should preserve every sample"
            elif recording_format == "opus":
                assert info.format == "OGG" and info.subtype == "OPUS"
            else:
                assert info.format == "WAV" and info.subtype == "PCM_16"
    print("  ROUND TRIP sample counts: " + ", ".join(
        f"{name}={counts[name]}/{expected}" for name in ("flac", "opus", "wav")
    ))


def test_soundfile_import_failure_falls_back_to_wav_once():
    real_import = builtins.__import__

    def failed_import(name, *args, **kwargs):
        if name == "soundfile":
            raise ImportError("simulated missing libsndfile")
        return real_import(name, *args, **kwargs)

    with _workspace_tmp() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
        builtins.__import__ = failed_import
        try:
            recorder = sinks.AudioRecorder(tmp / "fallback", recording_format="flac")
        finally:
            builtins.__import__ = real_import
        assert recorder.recording_format == "wav"
        recorder.on_chunk("MIC", tone(), 0.0)
        recorder.close()
        path = tmp / "fallback.wav"
        assert path.is_file()
        assert len(decode_audio(str(path), sampling_rate=RATE)) == 2 * RATE
    warnings = [line for line in output.getvalue().splitlines() if "[recorder] warning:" in line]
    assert len(warnings) == 1, warnings
    assert "using WAV" in warnings[0]


def test_soundfile_open_failure_falls_back_to_wav_once():
    real_writer = sinks._SoundFileWriter

    class _BrokenWriter:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("simulated writer open failure")

    with _workspace_tmp() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
        sinks._SoundFileWriter = _BrokenWriter
        try:
            recorder = sinks.AudioRecorder(tmp / "open-fallback", recording_format="opus")
            recorder.on_chunk("MIC", tone(), 0.0)
            recorder.close()
        finally:
            sinks._SoundFileWriter = real_writer
        assert recorder.recording_format == "wav"
        assert (tmp / "open-fallback.wav").is_file()
    warnings = [line for line in output.getvalue().splitlines() if "[recorder] warning:" in line]
    assert len(warnings) == 1, warnings
    assert "could not open the OPUS writer" in warnings[0]


def test_extension_plumbing_finds_current_and_legacy_recordings():
    previous_sessions_dir = webapp._sessions_dir
    try:
        with _workspace_tmp() as tmp:
            webapp._sessions_dir = lambda: tmp
            missing = webapp._recording_path("missing")
            assert missing == tmp / "missing.flac"
            for recording_format, ext in (("flac", ".flac"), ("opus", ".opus"), ("wav", ".wav")):
                stem = f"session-{recording_format}"
                expected = tmp / (stem + ext)
                expected.write_bytes(b"recording")
                assert webapp._recording_path(stem) == expected
                assert webapp._recording_format_for_stem(stem) == recording_format
            mic = tmp / "channels-MIC.flac"
            sys_ = tmp / "channels-SYS.opus"
            mixed = tmp / "channels-MIXED.wav"
            for path in (mic, sys_, mixed):
                path.write_bytes(b"channel")
            expanded = webapp._expand_recording_channels([str(mic)])
            assert set(expanded) == {str(mic), str(sys_)}
    finally:
        webapp._sessions_dir = previous_sessions_dir


if __name__ == "__main__":
    tests = (
        test_recording_format_default_and_suffix_helpers,
        test_each_format_round_trips_through_faster_whisper,
        test_soundfile_import_failure_falls_back_to_wav_once,
        test_soundfile_open_failure_falls_back_to_wav_once,
        test_extension_plumbing_finds_current_and_legacy_recordings,
    )
    failures = 0
    for test in tests:
        try:
            test()
            print(f"  OK  {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"  FAIL  {test.__name__}: {exc}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll recording-format tests passed.")