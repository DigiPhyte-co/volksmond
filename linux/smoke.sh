#!/usr/bin/env bash
# smoke.sh - Deploy-time smoke gate for the Linux .deb. Runs INSIDE a CLEAN distro
# container (NOT the build image), once each on ubuntu:22.04 and debian:12, driven
# by build-linux.ps1:
#
#   docker run --rm -v <repo>:/src:ro -v <repo>/dist-linux:/out:ro ubuntu:22.04 \
#       bash -lc "sed 's/\r$//' /src/linux/smoke.sh > /tmp/s.sh && bash /tmp/s.sh /out/Volksmond-<ver>.deb"
#
# Gate: `apt-get install ./Volksmond-<ver>.deb` must resolve and pull the declared
# Depends (the whole point of the .deb over AppImage: WebKitGTK cannot be bundled,
# so the package must be able to DECLARE it), then the frozen app must boot
# headless (--server-only) and serve HTTP 200 on the web UI root. curl + CA certs
# are harness tooling, installed separately BEFORE the .deb so they can never mask
# a hole in the Depends line.
#
# Optional second stage ("window" as $2, run on the ubuntu:22.04 smoke only; debian
# stays server-only for wall time): the server-only boot never imports pywebview or
# the GTK/gi stack, so a frozen build with missing gi typelibs would pass stage 1
# and crash on a real desktop. The window stage installs xvfb AND a headless
# pulseaudio server (both harness tooling, like curl: a virtual display/audio
# server is environment, not a package Depends; the .deb declares libpulse0, the
# CLIENT) and boots the app in DEFAULT window mode under xvfb-run; PASS = the
# process is still alive after ~20 s with no Python traceback in its log. Without
# the pulse server the strict no-traceback criterion would trip on a purely
# environmental /api/devices connect failure once the window's UI JS loads.
set -euo pipefail

DEB="${1:?usage: smoke.sh /path/to/Volksmond-<ver>.deb [window]}"
WINDOW_STAGE="${2:-}"
export DEBIAN_FRONTEND=noninteractive

. /etc/os-release
echo "==> Smoke on $PRETTY_NAME"
[ -f "$DEB" ] || { echo "ERROR: no such .deb: $DEB" >&2; exit 1; }

apt-get update -qq
apt-get install -y -qq --no-install-recommends curl ca-certificates >/dev/null

echo "==> apt-get install $DEB (must pull the declared Depends)"
apt-get install -y "$DEB"

echo "==> Installed package:"
dpkg -s volksmond | grep -E '^(Package|Version|Architecture|Depends)' | sed 's/^/    /'
[ -x /opt/volksmond/volksmond ] || { echo "ERROR: /opt/volksmond/volksmond missing or not executable" >&2; exit 1; }
[ -L /usr/bin/volksmond ] || { echo "ERROR: /usr/bin/volksmond launcher symlink missing" >&2; exit 1; }
[ -f /usr/share/applications/volksmond.desktop ] || { echo "ERROR: .desktop entry missing" >&2; exit 1; }

# Headless boot. A fresh container has port 8765 free, so desktop.free_port()
# always lands on the preferred port; the web UI root serves the app HTML.
echo "==> volksmond --server-only"
volksmond --server-only &
APP_PID=$!

code=000
for _ in $(seq 1 120); do
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        echo "ERROR: the app exited before serving HTTP" >&2
        exit 1
    fi
    code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/ || true)"
    [ "$code" = "200" ] && break
    sleep 1
done

echo "==> HTTP $code from http://127.0.0.1:8765/ ($PRETTY_NAME)"
if [ "$code" != "200" ]; then
    echo "ERROR: web UI root never returned 200" >&2
    kill "$APP_PID" 2>/dev/null || true
    exit 1
fi

kill "$APP_PID" 2>/dev/null || true
wait "$APP_PID" 2>/dev/null || true

if [ "$WINDOW_STAGE" = "window" ]; then
    echo "==> Window stage: xvfb-run volksmond (default pywebview/GTK window mode)"
    apt-get install -y -qq --no-install-recommends xvfb pulseaudio >/dev/null
    # Headless pulse server so the UI's /api/devices probe has something to talk
    # to (same daemon recipe as linux/pulse-fixture.sh; module-always-sink gives
    # it a null sink + monitor with zero hardware).
    export XDG_RUNTIME_DIR=/tmp/pulse-rt
    mkdir -p "$XDG_RUNTIME_DIR"
    pulseaudio --daemonize=yes --exit-idle-time=-1 --disallow-exit \
        --log-target=file:/tmp/pulse-smoke.log \
        || { echo "ERROR: headless pulseaudio failed to start for the window stage" >&2; exit 1; }
    WLOG=/tmp/volksmond-window.log
    : > "$WLOG"
    xvfb-run -a -s "-screen 0 1280x800x24" volksmond > "$WLOG" 2>&1 &
    WIN_PID=$!
    sleep 20
    if ! kill -0 "$WIN_PID" 2>/dev/null; then
        echo "ERROR: window-mode app exited within 20 s; log tail:" >&2
        tail -n 60 "$WLOG" >&2 || true
        exit 1
    fi
    if grep -q "Traceback (most recent call last)" "$WLOG"; then
        echo "ERROR: window-mode app logged a Python traceback:" >&2
        grep -A 40 "Traceback (most recent call last)" "$WLOG" >&2 || true
        kill "$WIN_PID" 2>/dev/null || true
        exit 1
    fi
    echo "==> WINDOW STAGE PASS ($PRETTY_NAME): process alive after 20 s, no traceback"
    # Kill the xvfb-run wrapper and the frozen app; the container teardown
    # reaps anything that lingers.
    kill "$WIN_PID" 2>/dev/null || true
    pkill -f /opt/volksmond/volksmond 2>/dev/null || true
    wait "$WIN_PID" 2>/dev/null || true
fi

echo "==> SMOKE PASS ($PRETTY_NAME)"
