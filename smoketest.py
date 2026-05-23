r"""Timed smoke test, captures 25s of audio, transcribes, exits cleanly.

Run from project root: `.venv\Scripts\python.exe smoketest.py`
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from live_transcribe import capture, transcribe, sinks

DURATION = 25  # seconds

print(f"=== Smoke test: {DURATION}s capture, GPU tier, language auto-detect ===", flush=True)
print("Loading large-v3 on CUDA...", flush=True)
engine = transcribe.Engine(tier="gpu", language=None)  # None = auto-detect language per chunk
print(f"Loaded {engine.model_name}", flush=True)

output = Path("sessions/claude-smoketest.md")
md_sink = sinks.MarkdownSink(output)
stdout_sink = sinks.StdoutSink()
engine.subscribe(stdout_sink)
engine.subscribe(md_sink)
engine.start()

cap = capture.AudioCapture(chunk_seconds=8, on_chunk=engine.on_chunk)
print(f"Capturing for {DURATION}s. Keep playing audio...", flush=True)
print("-" * 64, flush=True)
cap.start()

t0 = time.time()
try:
    while time.time() - t0 < DURATION:
        time.sleep(0.5)
finally:
    print("-" * 64, flush=True)
    print("Stopping capture, draining queue...", flush=True)
    cap.stop()
    engine.stop()
    md_sink.close()

print(f"\nDone. Transcript: {output}", flush=True)
