#!/usr/bin/env bash
# build-app-mac.sh - Build the Volksmond macOS .app (and a DMG) locally on a Mac.
#
# The local mirror of the CI build (docs/mac-port-plan.md WP-E/WP-F): it BUILDS only.
# CI (WP-F, .github/workflows/mac-release.yml) owns the authoritative Developer ID signing,
# notarisation and stapling; this script produces an ad-hoc-signed .app that launches and
# transcribes (the pre-notarisation acceptance bar), plus an unsigned DMG for hand-testing.
#
# Steps: venv -> compile the Swift SYS-capture helper (WP-B) -> PyInstaller (volksmond-mac.spec)
#        -> ad-hoc sign the bundled helper -> DMG (dmgbuild). Apple Silicon, arm64, macOS 14.4+.
#
# Env this script honours / sets:
#   PYTHON             python3 interpreter to seed the venv (default: python3).
#   CODESIGN_IDENTITY  passed through to the spec for inside-out signing. Unset -> ad-hoc
#                      (local dev). WP-F sets the real Developer ID Application cert.
#   VOLKSMOND_AUDIOTAP_BIN  set BY this script to the compiled helper path, then read by the
#                      spec to bundle it at Contents/Resources/bin/volksmond-audiotap.
#
# Usage:  mac/build-app-mac.sh            # .app + DMG
#         mac/build-app-mac.sh --no-dmg   # .app only
set -euo pipefail

# --- Locate the repo root (this script lives in mac/) ----------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

MAKE_DMG=1
[ "${1:-}" = "--no-dmg" ] && MAKE_DMG=0

# --- App version (single source of truth: licensing.py, same as build-app.ps1) ---------
VER="$(sed -n 's/.*APP_VERSION *= *"\([0-9]*\.[0-9]*\.[0-9]*\)".*/\1/p' live_transcribe/licensing.py | head -n1)"
[ -n "$VER" ] || { echo "ERROR: could not read APP_VERSION from live_transcribe/licensing.py" >&2; exit 1; }
echo "==> Building Volksmond $VER (macOS arm64)"

# --- Python venv -----------------------------------------------------------------------
# Reuse a dedicated venv so repeat builds are fast; create + populate it on first run.
PYTHON="${PYTHON:-python3}"
VENV="$ROOT/build-mac/venv"
if [ ! -x "$VENV/bin/python" ]; then
    echo "==> Creating build venv at $VENV"
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/python" -m pip install --upgrade pip
    # requirements.txt is platform-markered (WP-C: pyaudiowpatch is win32-only, sounddevice mac).
    "$VENV/bin/python" -m pip install -r requirements.txt
    "$VENV/bin/python" -m pip install pyinstaller dmgbuild
fi
PY="$VENV/bin/python"

# --- Swift SYS-capture helper (WP-B) ---------------------------------------------------
# Compile mac/volksmond-audiotap (its own SwiftPM package) and hand the binary to the spec via
# VOLKSMOND_AUDIOTAP_BIN. Not fatal if absent (WP-B may not have landed): the spec then builds a
# MIC-only app. `swift build -c release` puts the arm64 binary under .build/release/.
HELPER_PKG="$ROOT/mac/volksmond-audiotap"
if [ -f "$HELPER_PKG/Package.swift" ]; then
    echo "==> Compiling the Swift audiotap helper (swift build -c release)"
    ( cd "$HELPER_PKG" && swift build -c release )
    HELPER_BIN="$HELPER_PKG/.build/release/volksmond-audiotap"
    [ -x "$HELPER_BIN" ] || { echo "ERROR: swift build produced no volksmond-audiotap at $HELPER_BIN" >&2; exit 1; }
    export VOLKSMOND_AUDIOTAP_BIN="$HELPER_BIN"
    echo "    helper: $HELPER_BIN"
else
    echo "==> WARNING: no mac/volksmond-audiotap/Package.swift (WP-B not present); building MIC-only."
fi

# --- PyInstaller: dist/Volksmond.app ---------------------------------------------------
echo "==> PyInstaller (volksmond-mac.spec)"
rm -rf "$ROOT/dist/Volksmond.app"
"$PY" -m PyInstaller --noconfirm volksmond-mac.spec
APP="$ROOT/dist/Volksmond.app"
[ -d "$APP" ] || { echo "ERROR: PyInstaller did not produce $APP" >&2; exit 1; }

# --- Sign the bundled helper -----------------------------------------------------------
# The one signing step this build does: the helper is bundled as a DATA file (to land at
# Contents/Resources/bin/, the path WP-C's backend expects), so PyInstaller's binary-signing pass
# skips it, and an unsigned nested Mach-O will not launch under a signed parent. Ad-hoc ("-") when
# no identity is set; the real Developer ID + hardened-runtime inside-out re-sign of the whole app
# is WP-F's notarisation step. TODO(ci-verify): confirm WP-F re-signs this helper with the runtime.
HELPER_IN_APP="$APP/Contents/Resources/bin/volksmond-audiotap"
if [ -f "$HELPER_IN_APP" ]; then
    IDENTITY="${CODESIGN_IDENTITY:--}"
    echo "==> Signing bundled helper (identity: $IDENTITY)"
    codesign --force --options runtime --timestamp --sign "$IDENTITY" "$HELPER_IN_APP" || \
        codesign --force --sign "$IDENTITY" "$HELPER_IN_APP"   # timestamp needs network; ad-hoc has none
fi

echo "==> Built: $APP"

# --- DMG (dmgbuild) --------------------------------------------------------------------
# Volksmond-<ver>.dmg is exactly the name release.ps1 -MacDmg (WP-G) publishes and the CI
# notarisation.json sidecar is keyed to. Unsigned/un-notarised here; WP-F notarises + staples.
if [ "$MAKE_DMG" = "1" ]; then
    DMG="$ROOT/dist/Volksmond-$VER.dmg"
    echo "==> Building DMG: $DMG"
    rm -f "$DMG"
    SETTINGS="$(mktemp -t volksmond-dmg-XXXX.py)"
    cat > "$SETTINGS" <<'PYEOF'
# Minimal dmgbuild settings: the .app plus a drag-to-install Applications symlink.
# `app` is injected via -D app=<path>.
files = [app]
symlinks = {"Applications": "/Applications"}
icon_locations = {"Volksmond.app": (140, 120), "Applications": (400, 120)}
window_rect = ((200, 200), (560, 320))
PYEOF
    "$PY" -m dmgbuild -s "$SETTINGS" -D app="$APP" "Volksmond $VER" "$DMG"
    rm -f "$SETTINGS"
    echo "==> Built: $DMG"
fi

echo "==> Done ($VER). CI (WP-F) signs with the Developer ID, notarises and staples."
