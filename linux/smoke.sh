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
set -euo pipefail

DEB="${1:?usage: smoke.sh /path/to/Volksmond-<ver>.deb}"
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
echo "==> SMOKE PASS ($PRETTY_NAME)"
