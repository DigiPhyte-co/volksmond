#!/usr/bin/env bash
# pulse-fixture.sh - Headless null-sink capture fixture (linux-port plan section 2.2):
# proves the REAL Linux capture backend (pulsectl + pasimple over libpulse) registers
# MIC and SYS and delivers non-silent audio, with zero audio hardware. Runs INSIDE
# the volksmond-linux-build image (which has pulseaudio + the venv + /src mounted),
# driven by build-linux.ps1 as an optional second gate stage:
#
#   docker run --rm -v <repo>:/src:ro volksmond-linux-build \
#       bash -lc "sed 's/\r$//' /src/linux/pulse-fixture.sh > /tmp/f.sh && bash /tmp/f.sh"
#
# Mechanism: a user pulseaudio daemon + module-null-sink (vmtest) gives us
# vmtest.monitor, a real monitor source = the SYS channel. module-remap-source over
# that monitor gives us vmic, a real NON-monitor source = the MIC channel (the
# devices_linux mic/loopback split keys on monitor-ness, so a remap source is
# enumerated exactly like a physical mic). paplay a 440 Hz tone into vmtest and both
# channels carry it; linux/pulse_fixture.py then drives the actual AudioCapture
# backend and asserts non-silent chunks arrive tagged MIC and SYS.
#
# This exercises the SOURCE tree (WP-L1's backend), not the frozen app: the frozen
# boot path is covered by linux/smoke.sh. Both PulseAudio here and pipewire-pulse on
# real hardware speak the same libpulse client protocol (plan section 2.2); the
# pipewire-pulse container variant stays TODO(linux-hw) with the Mint box pass.
set -euo pipefail

SRC="${SRC:-/src}"
PY="${PY:-/opt/buildvenv/bin/python}"

# A user daemon as root draws a warning but runs fine in a container (no session
# dbus needed for null-sink/remap modules).
export XDG_RUNTIME_DIR=/tmp/pulse-rt
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$XDG_RUNTIME_DIR"

echo "==> Starting pulseaudio (headless)"
pulseaudio --daemonize=yes --exit-idle-time=-1 --disallow-exit --log-target=file:/tmp/pulse.log \
    || { echo "ERROR: pulseaudio failed to start"; cat /tmp/pulse.log 2>/dev/null; exit 1; }
ok=0
for _ in $(seq 1 20); do
    if pactl info >/dev/null 2>&1; then ok=1; break; fi
    sleep 0.5
done
[ "$ok" = 1 ] || { echo "ERROR: pulse server never answered pactl info"; cat /tmp/pulse.log 2>/dev/null; exit 1; }
pactl info | sed -n '1,4p' | sed 's/^/    /'

echo "==> Loading module-null-sink (vmtest) + module-remap-source (vmic)"
pactl load-module module-null-sink sink_name=vmtest sink_properties=device.description=VMTestSink >/dev/null
pactl load-module module-remap-source master=vmtest.monitor source_name=vmic \
    source_properties=device.description=VolksmondVirtualMic >/dev/null

echo "==> Generating and playing a 10 s 440 Hz tone into vmtest"
"$PY" - <<'PYEOF'
import wave
import numpy as np
sr = 44100
t = np.arange(sr * 10) / sr
x = (0.5 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)
pcm = (x * 32767.0).astype("<i2")
with wave.open("/tmp/tone.wav", "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(pcm.tobytes())
PYEOF
paplay --device=vmtest /tmp/tone.wav &
PLAY_PID=$!

cd "$SRC"
# PYTHONPATH: python puts the SCRIPT's dir (linux/) on sys.path, not the cwd, so the
# repo root must be added explicitly for `import live_transcribe` to resolve.
PYTHONPATH="$SRC" "$PY" linux/pulse_fixture.py

kill "$PLAY_PID" 2>/dev/null || true
echo "==> PULSE FIXTURE PASS"
