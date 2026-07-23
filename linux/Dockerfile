# syntax=docker/dockerfile:1
# Build image for the Volksmond Linux release lane (docs/linux-port-plan.md section 2.6).
#
# ubuntu:22.04 ON PURPOSE: glibc 2.35 is the support floor (Ubuntu 22.04 / Mint 21 /
# Debian 12). glibc is forward-compatible only, so the binary MUST be built on the
# oldest supported base. NEVER swap this for a python:3.12 image: those are Debian
# bookworm (glibc 2.36) and would silently exclude Mint 21. Python 3.12 (the project
# pin; ctranslate2 has no 3.13+ wheels) comes from the deadsnakes PPA instead.
#
# The image bakes the full pip environment into /opt/buildvenv so repeat builds are
# layer-cached; the repo is bind-mounted at /src at run time and the build itself
# (PyInstaller + dpkg-deb) runs via linux/build-app-linux.sh. Driven by
# build-linux.ps1 at the repo root.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# System deps:
# - deadsnakes PPA: python3.12 on a 22.04 base (which ships 3.10).
# - build-essential/pkg-config/libgirepository1.0-dev/libcairo2-dev/libffi-dev/
#   gobject-introspection: PyGObject 3.50.x + pycairo build from source (no wheels).
# - gir1.2-gtk-3.0 + gir1.2-webkit2-4.1: the typelibs PyInstaller's gi hooks collect
#   (the pywebview GTK backend's runtime set; the native libs stay SYSTEM-side via
#   the .deb Depends).
# - libpulse0: pulsectl/pasimple load libpulse.so.0 (capture backend).
# - pulseaudio + pulseaudio-utils: the headless null-sink capture fixture
#   (linux/pulse-fixture.sh), not needed by the app build itself.
# - cmake: fallback tooling only; llama-cpp-python is installed wheel-only below,
#   a source build would silently produce -march=native code (AVX-512 crash risk
#   on user CPUs), so the pip install for it forbids sdists.
RUN apt-get update && apt-get install -y --no-install-recommends \
        software-properties-common gnupg ca-certificates curl \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3.12-dev \
        build-essential pkg-config cmake \
        libgirepository1.0-dev libcairo2-dev libffi-dev gobject-introspection \
        gir1.2-gtk-3.0 gir1.2-webkit2-4.1 \
        libpulse0 pulseaudio pulseaudio-utils \
        xz-utils binutils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

# Python env, in careful stages (pip cache mounted so iterations are incremental):
# 1. requirements.txt minus livekit and llama-cpp-python (the two special cases);
#    the linux markers activate pulsectl + pasimple, and deactivate the win32/darwin
#    backends, automatically.
# 2. llama-cpp-python 0.3.22 WHEEL-ONLY from abetlen's CPU index (--only-binary:
#    a silent source build would compile llama.cpp with -march=native and crash on
#    AVX2-only user CPUs, the exact failure the Windows lane's AVX-512 guard exists
#    to prevent).
# 3. PyGObject pinned 3.50.* (3.52+ needs girepository-2.0, absent on the glibc-floor
#    distros) + PyInstaller.
# 4. livekit WHEEL-ONLY, soft-fail: manylinux_2_28 clears the 22.04 floor, but if no
#    wheel resolves the app ships without live AEC (graceful AEC-off degrade); the
#    spec (volksmond-linux.spec) handles its absence and build-app-linux.sh reports it.
RUN --mount=type=cache,target=/root/.cache/pip \
    python3.12 -m venv /opt/buildvenv \
    && /opt/buildvenv/bin/pip install --upgrade pip \
    && grep -viE '^(livekit|llama-cpp-python)==' /tmp/requirements.txt > /tmp/req-core.txt \
    && /opt/buildvenv/bin/pip install -r /tmp/req-core.txt \
    && /opt/buildvenv/bin/pip install --only-binary=:all: "llama-cpp-python==0.3.22" \
        --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
    && /opt/buildvenv/bin/pip install "PyGObject==3.50.*" "pyinstaller<8" \
    && { /opt/buildvenv/bin/pip install --only-binary=:all: "livekit==1.1.11" \
         || echo "WARNING: no livekit wheel for this platform; building WITHOUT live AEC" ; }

WORKDIR /src
