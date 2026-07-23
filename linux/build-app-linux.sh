#!/usr/bin/env bash
# build-app-linux.sh - Build the Volksmond Linux artifacts. Runs INSIDE the
# volksmond-linux-build container (linux/Dockerfile, ubuntu:22.04 + Python 3.12),
# driven by build-linux.ps1 on the Windows release machine:
#
#   docker run --rm -v <repo>:/src -v <repo>/dist-linux:/out volksmond-linux-build \
#       bash -lc "sed 's/\r$//' /src/linux/build-app-linux.sh > /tmp/b.sh && bash /tmp/b.sh"
#
# Steps: PyInstaller (volksmond-linux.spec, onedir) -> dpkg-deb staging tree
# (/opt/volksmond payload, /usr/bin/volksmond symlink, .desktop entry, icon,
# control file with the WebKitGTK/GTK/libpulse Depends) -> Volksmond-<ver>.deb
# -> Volksmond-<ver>-linux-x64.tar.gz byproduct (with a README naming the system
# packages apt would otherwise install).
#
# Everything heavy is written to $BUILD (container-local filesystem), NEVER to the
# bind mount: PyInstaller's onedir layout uses symlinks, which a Windows-backed
# mount cannot represent, and mount IO is slow. Only the two final artifacts land
# in /out (which build-linux.ps1 maps to <repo>/dist-linux, gitignored, on C:\dev
# and therefore outside OneDrive).
set -euo pipefail

SRC="${SRC:-/src}"
OUT="${OUT:-/out}"
BUILD="${BUILD:-/build}"
PY="${PY:-/opt/buildvenv/bin/python}"

cd "$SRC"

# --- App version (single source of truth: licensing.py, same regex as every lane) -----
VER="$(sed -n 's/.*APP_VERSION *= *"\([0-9]*\.[0-9]*\.[0-9]*\)".*/\1/p' live_transcribe/licensing.py | head -n1)"
[ -n "$VER" ] || { echo "ERROR: could not read APP_VERSION from live_transcribe/licensing.py" >&2; exit 1; }
echo "==> Building Volksmond $VER (linux x86_64, glibc floor $(ldd --version | sed -n '1s/.* //p'))"

rm -rf "$BUILD"
mkdir -p "$BUILD" "$OUT"

# Record whether livekit made it into the build venv (soft dependency; see the
# Dockerfile). The spec prints its own warning; this one lands in the wrapper log.
if "$PY" -c "import livekit" 2>/dev/null; then
    echo "==> livekit present: live AEC will be bundled"
else
    echo "==> WARNING: livekit NOT in the build venv; building WITHOUT live AEC (graceful degrade)"
fi

# --- PyInstaller (onedir) --------------------------------------------------------------
echo "==> PyInstaller (volksmond-linux.spec)"
"$PY" -m PyInstaller --noconfirm --workpath "$BUILD/work" --distpath "$BUILD/dist" volksmond-linux.spec
APPDIR="$BUILD/dist/volksmond"
[ -x "$APPDIR/volksmond" ] || { echo "ERROR: PyInstaller did not produce $APPDIR/volksmond" >&2; exit 1; }
echo "==> Built: $APPDIR ($(du -sh "$APPDIR" | cut -f1))"

# --- .deb staging tree -------------------------------------------------------------------
# Layout per docs/linux-port-plan.md section 2.6: payload at /opt/volksmond, launcher
# symlink /usr/bin/volksmond, .desktop + icon under /usr/share (pixmaps: no size-keyed
# hicolor tree needed for a single PNG). Text assets are CR-stripped defensively: the
# repo checkout on the Windows release machine may carry CRLF, and dpkg chokes on CRs
# in the control file.
PKG="$BUILD/pkg"
mkdir -p "$PKG/DEBIAN" "$PKG/opt" "$PKG/usr/bin" \
         "$PKG/usr/share/applications" "$PKG/usr/share/pixmaps"
cp -a "$APPDIR" "$PKG/opt/volksmond"
ln -s /opt/volksmond/volksmond "$PKG/usr/bin/volksmond"
tr -d '\r' < linux/debian/volksmond.desktop > "$PKG/usr/share/applications/volksmond.desktop"
cp brand/volksmond-mark-blue.png "$PKG/usr/share/pixmaps/volksmond.png"
chmod 0644 "$PKG/usr/share/applications/volksmond.desktop" "$PKG/usr/share/pixmaps/volksmond.png"

SIZE_KB="$(du -ks --exclude=DEBIAN "$PKG" | cut -f1)"
tr -d '\r' < linux/debian/control.in \
    | sed -e "s/@VERSION@/$VER/" -e "s/@SIZE_KB@/$SIZE_KB/" > "$PKG/DEBIAN/control"
chmod 0644 "$PKG/DEBIAN/control"
echo "==> control:"
sed 's/^/    /' "$PKG/DEBIAN/control"

# --- Volksmond-<ver>.deb (the pinned release-lane artifact name) -------------------------
# xz -1: fast enough for a ~1 GB payload of mostly already-dense .so files, and what
# apt/dpkg on every floor distro decompresses natively. --root-owner-group so the
# payload installs root-owned without needing fakeroot.
DEB="$OUT/Volksmond-$VER.deb"
rm -f "$DEB"
echo "==> dpkg-deb ($DEB)"
dpkg-deb --build --root-owner-group -Zxz -z1 "$PKG" "$DEB"

# --- Tarball byproduct (non-apt users; README names the system deps) ---------------------
TARROOT="$BUILD/tar"
TDIR="$TARROOT/Volksmond-$VER-linux-x64"
mkdir -p "$TDIR"
cp -a "$APPDIR/." "$TDIR/"
cat > "$TDIR/README-linux.txt" <<EOF
Volksmond $VER (Linux x86_64, glibc 2.35+: Ubuntu 22.04 / Mint 21 / Debian 12 or newer)

This tarball is the raw application folder. The .deb is the recommended install
(it declares and auto-installs the system libraries below). If you use this
tarball instead, install them yourself:

    sudo apt install gir1.2-webkit2-4.1 gir1.2-gtk-3.0 libgtk-3-0 libpulse0 xdg-utils

Run:      ./volksmond            (native window)
          ./volksmond --browser  (open in your browser instead)

Everything runs locally on this machine: no cloud transcription, no telemetry.
Data lands in ~/.local/share/volksmond. https://volksmond.com
EOF
TAR="$OUT/Volksmond-$VER-linux-x64.tar.gz"
rm -f "$TAR"
echo "==> tar ($TAR)"
tar -C "$TARROOT" -czf "$TAR" "Volksmond-$VER-linux-x64"

# --- Evidence ----------------------------------------------------------------------------
echo "==> Artifacts:"
ls -lh "$DEB" "$TAR" | sed 's/^/    /'
sha256sum "$DEB" "$TAR" | sed 's/^/    /'
echo "==> dpkg-deb --info:"
dpkg-deb --info "$DEB" | sed 's/^/    /'
echo "==> Done ($VER). Smoke gates (clean ubuntu:22.04 + debian:12) run next via build-linux.ps1."
