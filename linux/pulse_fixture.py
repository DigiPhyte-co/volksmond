"""Null-sink capture fixture assertions (linux-port plan section 2.2).

Driven by linux/pulse-fixture.sh inside the build container, AFTER it has:
  - started a headless pulseaudio daemon,
  - loaded module-null-sink   -> sink 'vmtest' (whose 'vmtest.monitor' is SYS),
  - loaded module-remap-source -> source 'vmic' (a non-monitor source = MIC),
  - started paplay of a 440 Hz tone into 'vmtest' (feeding BOTH channels).

Drives the REAL backend stack (devices_linux enumeration over pulsectl, then
capture_linux.AudioCapture over pasimple) and asserts:
  1. enumeration sees at least one monitor (loopback) and one mic source,
  2. the spec strings resolve to the fixture's Pulse source names,
  3. a short capture session delivers non-silent chunks tagged both MIC and SYS.

Plain script by design (not pytest): it needs a live pulse daemon, so it runs in
the Docker gate, not in the Windows suite (which covers this backend with mocked
pulsectl/pasimple per the test_capture_linux.py convention).
"""
import sys
import time

# Chunks shorter than the tone so mid-session emits happen; stop() flushes tails.
CHUNK_SECONDS = 2
CAPTURE_SECONDS = 7
# The tone peaks at 0.5; anything above this is unambiguously "not silence".
MIN_PEAK = 0.05


def main():
    from live_transcribe.capture_linux import AudioCapture
    from live_transcribe.devices_linux import (list_ui_devices, resolve_loopback,
                                               resolve_mic)

    devs = list_ui_devices()
    print(f"enumerated: {len(devs['loopbacks'])} loopback(s), {len(devs['mics'])} mic(s)")
    for lb in devs["loopbacks"]:
        print(f"  loopback [{lb['index']}] {lb['name']} ({lb['rate']} Hz)")
    for m in devs["mics"]:
        print(f"  mic      [{m['index']}] {m['name']} ({m['rate']} Hz)")
    assert devs["loopbacks"], "no monitor (loopback) sources enumerated"
    assert devs["mics"], "no mic (non-monitor) sources enumerated"

    lb = resolve_loopback(None, "vmtest")
    mic = resolve_mic(None, "vmic")
    print(f"resolved SYS -> {lb['source']!r} ({lb['name']})")
    print(f"resolved MIC -> {mic['source']!r} ({mic['name']})")
    assert lb["source"] == "vmtest.monitor", f"SYS resolved to {lb['source']!r}"
    assert mic["source"] == "vmic", f"MIC resolved to {mic['source']!r}"

    chunks = []
    cap = AudioCapture(
        mic_device="vmic",
        loopback_device="vmtest",
        chunk_seconds=CHUNK_SECONDS,
        on_chunk=lambda src, audio, t_start: chunks.append(
            (src, float(abs(audio).max()) if audio.size else 0.0)),
    )
    cap.start()
    time.sleep(CAPTURE_SECONDS)
    cap.stop()

    sys_peaks = [p for s, p in chunks if s == "SYS"]
    mic_peaks = [p for s, p in chunks if s == "MIC"]
    print(f"SYS: {len(sys_peaks)} chunk(s), peaks {[round(p, 3) for p in sys_peaks]}")
    print(f"MIC: {len(mic_peaks)} chunk(s), peaks {[round(p, 3) for p in mic_peaks]}")
    assert sys_peaks, "no chunks arrived tagged SYS"
    assert mic_peaks, "no chunks arrived tagged MIC"
    assert max(sys_peaks) > MIN_PEAK, f"SYS chunks are silent (max peak {max(sys_peaks):.4f})"
    assert max(mic_peaks) > MIN_PEAK, f"MIC chunks are silent (max peak {max(mic_peaks):.4f})"
    print("pulse_fixture: MIC + SYS both registered and non-silent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
