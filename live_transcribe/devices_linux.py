"""Audio device enumeration for the Linux backend (pulsectl over native libpulse).

Mirrors the four callables the Windows backend (devices_win) exposes, so the
`devices.py` selector and every caller (the CLI --list-devices, the web UI's
/api/devices) work unchanged on Linux.

Mechanism (linux-port plan section 2.1): PortAudio, both the copy bundled in
the sounddevice wheel and the distro builds, has NO PulseAudio/PipeWire host
API, so monitor sources (the Linux equivalent of WASAPI loopback) are
invisible to it. We therefore enumerate through pulsectl, a ctypes-only
wrapper over libpulse.so.0, which both PulseAudio (Mint 21.x) and
pipewire-pulse (Mint 22, Debian 12+) serve identically.

Two shapes matter:

- Loopbacks are the servers' monitor sources: every output sink exposes its
  playback as a "<sink name>.monitor" source, an ordinary input from the
  client's point of view. The default is the default sink's monitor. Rendered
  for the UI as "System audio: <sink description>".
- Mics are the non-monitor sources (real inputs, human-readable description).

Indices in the returned lists are POSITIONS into those lists (0-based), not
Pulse source indices; `resolve_mic` / `resolve_loopback` map an index-or-name
spec back to the stable Pulse source NAME string, which is what the capture
backend hands to pasimple as `device_name`. Positional indices keep the
existing UI dropdown contract ({index, name, rate} entries) unchanged.

pulsectl is imported lazily (inside `_pulse()`), never at module import time,
so this file stays importable on Windows for the test suite (the linux-only
package is absent there).
"""

# libpulse's "no such index" sentinel: a real (non-monitor) source reports
# monitor_of_sink == PA_INVALID_INDEX.
PA_INVALID_INDEX = 0xFFFFFFFF

# Fallback rate when a source's sample spec is unreadable or implausible;
# 48 kHz is what PulseAudio and PipeWire both default to. Safe either way:
# Pulse resamples record streams server-side, so a wrong-but-plausible rate
# still yields a working stream at the rate we asked for.
_FALLBACK_RATE = 48000

# Plausibility window for a sample-spec rate. Real pulsectl exposes
# source.sample_spec as a RAW ctypes PA_SAMPLE_SPEC whose backing memory is
# NOT valid on the returned info objects (observed garbage on real libpulse:
# rate 0/32764, channels 24/176; every pasimple open then fails
# PA_ERR_INVALID), so a spec rate is trusted only inside this window.
_MIN_PLAUSIBLE_RATE = 8000
_MAX_PLAUSIBLE_RATE = 384000


def _pulse():
    """Lazy pulsectl connection: keeps this module importable on Windows (where
    the package is absent) so the test suite can exercise the pure logic with a
    fake pulsectl injected into sys.modules. Returns a context-manager Pulse."""
    import pulsectl
    return pulsectl.Pulse("volksmond")


def _is_monitor(src):
    """True when a Pulse source is a sink monitor (system audio), not a mic."""
    if getattr(src, "monitor_of_sink_name", None):
        return True
    mos = getattr(src, "monitor_of_sink", None)
    if isinstance(mos, int) and not isinstance(mos, bool) and 0 <= mos != PA_INVALID_INDEX:
        return True
    name = getattr(src, "name", "") or ""
    return name.endswith(".monitor")


def _spec_rate(src):
    """The source's sample rate: sample_spec.rate ONLY when it is a plausible
    integer (see _MIN/_MAX_PLAUSIBLE_RATE: the spec struct can be garbage
    memory on real libpulse), else the 48 kHz fallback. Access is fully
    guarded; a sample_spec that raises must not break enumeration."""
    try:
        ss = getattr(src, "sample_spec", None)
        if isinstance(ss, dict):
            rate = ss.get("rate")
        elif ss is not None:
            rate = getattr(ss, "rate", None)
        else:
            rate = None
        rate = int(rate)
    except Exception:
        return _FALLBACK_RATE
    if _MIN_PLAUSIBLE_RATE <= rate <= _MAX_PLAUSIBLE_RATE:
        return rate
    return _FALLBACK_RATE


def _spec_channels(src):
    """The source's native channel count from the UNPACKED channel_count
    field, clamped to at least 1. Never sample_spec.channels: on real libpulse
    that struct reads garbage memory (see _spec_rate), while channel_count is
    unpacked by pulsectl and verified correct on real hardware."""
    try:
        ch = int(getattr(src, "channel_count", None))
    except Exception:
        return 1
    return ch if ch >= 1 else 1


def _mic_label(src):
    """Human-readable mic name: the description, falling back to the raw name."""
    return (getattr(src, "description", None) or getattr(src, "name", "") or "").strip()


def _monitor_label(src):
    """UI label for a monitor source: 'System audio: <sink description>'.
    Pulse describes monitors as 'Monitor of <sink description>'; strip that
    prefix rather than looking the sink up (one less round trip)."""
    d = _mic_label(src)
    if d.lower().startswith("monitor of "):
        d = d[len("monitor of "):].strip()
    return f"System audio: {d}" if d else "System audio"


def _snapshot(pulse):
    """One enumeration pass: (mics, monitors, default_source_name,
    default_monitor_name). The lists hold raw pulsectl source objects in
    server order; positions into them are the UI-facing indices."""
    sources = list(pulse.source_list())
    server = pulse.server_info()
    mics = [s for s in sources if not _is_monitor(s)]
    monitors = [s for s in sources if _is_monitor(s)]
    default_source = getattr(server, "default_source_name", None)
    default_sink = getattr(server, "default_sink_name", None)
    default_monitor = f"{default_sink}.monitor" if default_sink else None
    return mics, monitors, default_source, default_monitor


def _descriptor(pos, src, label):
    """Normalise one source into the dict the capture backend consumes.
    `source` is the stable Pulse source NAME, which pasimple takes as its
    `device_name`; `index` is the positional UI index for error messages."""
    return {
        "index": pos,
        "name": label,
        "source": getattr(src, "name", ""),
        "rate": _spec_rate(src),
        "channels": _spec_channels(src),
    }


def _as_index(spec):
    """int(spec) if the spec is an integer or a numeric string, else None
    (meaning: treat it as a name substring). Mirrors the Windows resolvers."""
    try:
        return int(spec)
    except (TypeError, ValueError):
        return None


def _resolve_loopback(pulse, spec):
    _mics, monitors, _default_source, default_monitor = _snapshot(pulse)
    if not monitors:
        raise ValueError(
            "No system-audio monitor source found (is a PulseAudio or "
            "PipeWire server running?). Run --list-devices."
        )
    if spec is None:
        for i, s in enumerate(monitors):
            if getattr(s, "name", None) == default_monitor:
                return _descriptor(i, s, _monitor_label(s))
        # No monitor matches the default sink (unusual but seen with odd
        # server states); the first monitor is a sane default.
        # TODO(linux-hw): confirm on Mint whether this branch is ever hit
        # under pipewire-pulse and whether position 0 is the right pick.
        return _descriptor(0, monitors[0], _monitor_label(monitors[0]))
    idx = _as_index(spec)
    if idx is not None:
        if 0 <= idx < len(monitors):
            return _descriptor(idx, monitors[idx], _monitor_label(monitors[idx]))
        raise ValueError(f"No system-audio device #{idx}. Run --list-devices.")
    sub = str(spec).lower()
    for i, s in enumerate(monitors):
        if (sub in _monitor_label(s).lower()
                or sub in (getattr(s, "name", "") or "").lower()):
            return _descriptor(i, s, _monitor_label(s))
    raise ValueError(f"No system-audio device matching {spec!r}. Run --list-devices.")


def _resolve_mic(pulse, spec):
    mics, _monitors, default_source, _default_monitor = _snapshot(pulse)
    if not mics:
        raise ValueError("No microphone (input source) found. Run --list-devices.")
    if spec is None:
        for i, s in enumerate(mics):
            if getattr(s, "name", None) == default_source:
                return _descriptor(i, s, _mic_label(s))
        # The server default is a monitor or unset: fall back to the first
        # real input, matching the macOS backend's no-default behaviour.
        return _descriptor(0, mics[0], _mic_label(mics[0]))
    idx = _as_index(spec)
    if idx is not None:
        if 0 <= idx < len(mics):
            return _descriptor(idx, mics[idx], _mic_label(mics[idx]))
        raise ValueError(f"No microphone #{idx}. Run --list-devices.")
    sub = str(spec).lower()
    for i, s in enumerate(mics):
        if (sub in _mic_label(s).lower()
                or sub in (getattr(s, "name", "") or "").lower()):
            return _descriptor(i, s, _mic_label(s))
    raise ValueError(f"No mic matching {spec!r}. Run --list-devices.")


def resolve_loopback(p, spec):
    """Return a descriptor for a system-audio (monitor) source.

    `p` may be an already-open pulsectl.Pulse connection (it is left open), or
    None to open a short-lived one; the Windows backend passes its PyAudio
    handle in this slot, so the signature stays identical across platforms.
    """
    if p is not None:
        return _resolve_loopback(p, spec)
    with _pulse() as pulse:
        return _resolve_loopback(pulse, spec)


def resolve_mic(p, spec):
    """Return a descriptor for a microphone (non-monitor input source).

    Resolution order mirrors the Windows backend: None -> the server default
    source (first mic when the default is unset or itself a monitor); an
    integer -> position in the mic list; otherwise a case-insensitive
    substring match on the description or the Pulse source name.
    """
    if p is not None:
        return _resolve_mic(p, spec)
    with _pulse() as pulse:
        return _resolve_mic(pulse, spec)


def list_ui_devices():
    """The dict /api/devices serves: {loopbacks, mics, default_loopback_index,
    default_mic_index}. Shape-identical to the Windows backend so the UI needs
    no platform branch; indices are positions into the returned lists."""
    with _pulse() as pulse:
        mics_raw, mons_raw, default_source, default_monitor = _snapshot(pulse)

    loopbacks = [
        {"index": i, "name": _monitor_label(s), "rate": _spec_rate(s)}
        for i, s in enumerate(mons_raw)
    ]
    mics = [
        {"index": i, "name": _mic_label(s), "rate": _spec_rate(s)}
        for i, s in enumerate(mics_raw)
    ]

    default_lb = next(
        (i for i, s in enumerate(mons_raw) if getattr(s, "name", None) == default_monitor),
        None,
    )
    if default_lb is None and loopbacks:
        default_lb = 0

    default_mic = next(
        (i for i, s in enumerate(mics_raw) if getattr(s, "name", None) == default_source),
        None,
    )
    if default_mic is None and len(mics) == 1:
        # Only highlight a mic the server did not name when it is the sole
        # candidate; with several, leave the default unset rather than risk
        # pointing the UI at the wrong device (same rule as Windows/macOS).
        default_mic = 0

    return {
        "loopbacks": loopbacks,
        "mics": mics,
        "default_loopback_index": default_lb,
        "default_mic_index": default_mic,
    }


def print_devices():
    """Print available system-audio (monitor) and mic sources. The Linux
    analogue of the Windows CLI --list-devices listing."""
    with _pulse() as pulse:
        mics_raw, mons_raw, default_source, default_monitor = _snapshot(pulse)

    print()
    print("System audio (PulseAudio/PipeWire monitor sources, capture what plays on each output):")
    for i, s in enumerate(mons_raw):
        marker = "  <-- default" if getattr(s, "name", None) == default_monitor else ""
        print(f"  [{i:>3}] {_monitor_label(s)}  ({_spec_rate(s)} Hz x{_spec_channels(s)}ch){marker}")
    if not mons_raw:
        print("  (none found; is a PulseAudio or PipeWire server running?)")

    print()
    print("Microphones (real input sources):")
    for i, s in enumerate(mics_raw):
        marker = "  <-- default" if getattr(s, "name", None) == default_source else ""
        print(f"  [{i:>3}] {_mic_label(s)}  ({_spec_rate(s)} Hz x{_spec_channels(s)}ch){marker}")
    if not mics_raw:
        print("  (none found)")

    print()
    print("Override defaults with:")
    print("  --loopback-device <index>   or   --loopback-device 'name substring'")
    print("  --mic-device <index>        or   --mic-device 'name substring'")
