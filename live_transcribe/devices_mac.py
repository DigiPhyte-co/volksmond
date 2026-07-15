"""Audio device enumeration for the macOS backend (sounddevice / Core Audio).

Mirrors the four callables the Windows backend (devices_win) exposes, so the
`devices.py` selector and every caller (the CLI --list-devices, the web UI's
/api/devices) work unchanged on macOS.

Two things differ from Windows and are load-bearing:

- Mics come from `sounddevice` (PortAudio over the Core Audio host API), not
  pyaudiowpatch. Core Audio does not enumerate the same physical device once
  per host API the way Windows MME/DirectSound/WASAPI do, so the per-host-API
  dedupe the Windows path needs is unnecessary here.
- System audio has NO per-device choice in v1: a Core Audio process tap
  (the signed Swift helper, see capture_mac) captures the whole system mix.
  So `list_ui_devices` returns exactly ONE synthetic loopback entry at index
  SYS_LOOPBACK_INDEX (-1), and `resolve_loopback` collapses to "tap on/off".
  The returned dict has the identical shape the Windows side serves
  ({loopbacks, mics, default_loopback_index, default_mic_index}), so the web
  UI dropdown renders the lone entry as its default with no platform branch.

`sounddevice` is imported lazily (via `_sd()`), never at module import time,
so this file stays importable on Windows for the test suite (the mac-only
package is absent there).
"""

# The Swift helper is always invoked with `--sample-rate 16000 --mono`
# (capture_mac, frozen contract in the mac-port plan section 2.2), so the SYS
# source is fixed at 16 kHz mono. Kept local (not imported from capture_core)
# to keep this module import-light.
TARGET_RATE = 16000

# Synthetic system-audio pseudo-device. A process-wide tap has no per-endpoint
# choice, so there is exactly one entry and it carries a reserved sentinel index
# that never collides with a real sounddevice index (those are >= 0).
SYS_LOOPBACK_INDEX = -1
SYS_LOOPBACK_NAME = "System audio (everything this Mac plays)"


def _sd():
    """Lazy sounddevice import: keeps this module importable on Windows (where
    the package is absent) so the test suite can exercise the pure logic with
    the real audio backend mocked out."""
    import sounddevice as sd
    return sd


def _sys_loopback_entry():
    """The single synthetic loopback descriptor. `rate` is included so the dict
    shape matches a Windows loopback entry ({index, name, rate}); the web UI
    only reads index + name, but keeping the shape identical avoids surprises."""
    return {"index": SYS_LOOPBACK_INDEX, "name": SYS_LOOPBACK_NAME, "rate": TARGET_RATE}


def _mic_entry(index, info):
    """Normalise one sounddevice query_devices() record into the UI/enumeration
    shape. `info` is the dict sounddevice returns for that device index."""
    rate = int(info.get("default_samplerate") or TARGET_RATE)
    return {"index": int(index), "name": str(info["name"]), "rate": rate}


def _input_devices(sd):
    """Yield (index, info) for every device with at least one input channel."""
    for i, info in enumerate(sd.query_devices()):
        if int(info.get("max_input_channels", 0)) > 0:
            yield i, info


def _default_input_index(sd):
    """The Core Audio default input device index, or None. sounddevice reports
    the default as (input, output); -1 there means 'no default'."""
    try:
        idx = sd.default.device[0]
    except (AttributeError, IndexError, TypeError):
        return None
    if idx is None or int(idx) < 0:
        return None
    return int(idx)


def print_devices():
    """Print available system-audio (single synthetic entry) and mic devices.
    The macOS analogue of the Windows CLI --list-devices listing."""
    sd = _sd()
    default_mic = _default_input_index(sd)

    print()
    print("System audio (macOS process tap, captures everything this Mac plays):")
    print(f"  [{SYS_LOOPBACK_INDEX:>3}] {SYS_LOOPBACK_NAME}  ({TARGET_RATE} Hz x1ch)  <-- default")
    print("  (whole-system tap; there is no per-app choice in v1)")

    print()
    print("Microphones (real input devices):")
    for i, info in _input_devices(sd):
        marker = "  <-- default" if default_mic is not None and i == default_mic else ""
        rate = int(info.get("default_samplerate") or TARGET_RATE)
        ch = int(info.get("max_input_channels", 0))
        print(f"  [{i:>3}] {info['name']}  ({rate} Hz x{ch}ch){marker}")

    print()
    print("Override the mic with:")
    print("  --mic-device <index>        or   --mic-device 'name substring'")
    print("System audio is the whole-system tap; --loopback-device selects it on/off only.")


def resolve_loopback(p, spec):
    """Resolve the system-audio request to the synthetic tap descriptor, or raise.

    On macOS there is exactly one tap, so this collapses to "helper on/off":
    None (default), the sentinel index (-1 / "-1"), or a substring of the
    synthetic entry's name all mean "run the tap". Anything else raises, which
    the capture backend treats the same way Windows treats a failed loopback
    resolve: log it and continue mic-only.

    `p` is accepted for signature parity with the Windows backend (which passes
    its PyAudio handle) and is ignored here.
    """
    if spec is None:
        return _sys_loopback_entry()
    # Numeric spec: only the sentinel index selects the tap.
    try:
        idx = int(spec)
    except (TypeError, ValueError):
        idx = None
    if idx is not None:
        if idx == SYS_LOOPBACK_INDEX:
            return _sys_loopback_entry()
        raise ValueError(
            f"No system-audio device #{idx} on macOS: the only option is the "
            f"whole-system tap at index {SYS_LOOPBACK_INDEX}."
        )
    # Name-substring spec: accept a substring of the synthetic entry's name.
    sub = str(spec).strip().lower()
    if sub and sub in SYS_LOOPBACK_NAME.lower():
        return _sys_loopback_entry()
    raise ValueError(
        f"No system-audio device matching {spec!r} on macOS: the only option is "
        f"the whole-system tap ({SYS_LOOPBACK_NAME!r})."
    )


def resolve_mic(p, spec):
    """Return a normalised mic descriptor ({index, name, rate, channels}) that
    capture_mac can hand straight to a sounddevice InputStream.

    Resolution order mirrors the Windows backend: None -> the Core Audio default
    input; an integer -> that device index (must have input channels); otherwise
    a case-insensitive name-substring match. `p` is ignored (parity with the
    Windows signature).
    """
    sd = _sd()
    devices = sd.query_devices()

    def _descriptor(index):
        info = devices[index]
        return {
            "index": int(index),
            "name": str(info["name"]),
            "rate": int(info.get("default_samplerate") or TARGET_RATE),
            "channels": max(1, int(info.get("max_input_channels", 1))),
        }

    if spec is None:
        idx = _default_input_index(sd)
        if idx is not None:
            return _descriptor(idx)
        # No system default: fall back to the first device with input channels.
        for i, _info in _input_devices(sd):
            return _descriptor(i)
        raise ValueError("No microphone (input device) found. Run --list-devices.")

    # Integer index.
    try:
        idx = int(spec)
    except (TypeError, ValueError):
        idx = None
    if idx is not None:
        if idx < 0 or idx >= len(devices):
            raise ValueError(f"No device #{idx}. Run --list-devices.")
        if int(devices[idx].get("max_input_channels", 0)) == 0:
            raise ValueError(f"Device #{idx} '{devices[idx]['name']}' has no input channels.")
        return _descriptor(idx)

    # Name substring.
    sub = str(spec).lower()
    for i, info in _input_devices(sd):
        if sub in str(info["name"]).lower():
            return _descriptor(i)
    raise ValueError(f"No mic matching {spec!r}. Run --list-devices.")


def _merge_ui_devices(mics, default_mic_index):
    """Assemble the /api/devices dict from an already-collected mic list and the
    resolved default mic index. Split out (no sounddevice needed) so tests can
    drive the merge/shape logic directly."""
    default = default_mic_index
    if default is not None and not any(m["index"] == default for m in mics):
        # The reported default is not an input device we listed (should not
        # happen on Core Audio, but never point the UI at a phantom index):
        # highlight nothing when unsure, exactly as the Windows path does.
        default = mics[0]["index"] if len(mics) == 1 else None
    return {
        "loopbacks": [_sys_loopback_entry()],
        "mics": mics,
        "default_loopback_index": SYS_LOOPBACK_INDEX,
        "default_mic_index": default,
    }


def list_ui_devices():
    """The dict /api/devices serves: {loopbacks, mics, default_loopback_index,
    default_mic_index}. One synthetic loopback (the system tap) plus the real
    mics from sounddevice. Shape-identical to the Windows backend so the UI
    needs no platform branch."""
    sd = _sd()
    mics = [_mic_entry(i, info) for i, info in _input_devices(sd)]
    return _merge_ui_devices(mics, _default_input_index(sd))
