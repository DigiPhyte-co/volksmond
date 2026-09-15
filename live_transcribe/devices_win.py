"""Audio device enumeration via pyaudiowpatch (Windows backend).

pyaudiowpatch is a PyAudio fork that exposes WASAPI loopback devices as
first-class PortAudio devices. Default soundcard/sounddevice don't support
WASAPI loopback at all on Windows, pyaudiowpatch is the right tool.
"""
import pyaudiowpatch as pa


def _fix_name(s):
    # PyAudio returns device names as latin-1-encoded bytes wrapped in a Python str, so a real
    # "Intel(R)" comes back as the mojibake we'd see if you decoded UTF-8 as latin-1. Reverse it:
    # encode the str's code points as latin-1 bytes, decode those bytes as UTF-8. Falls open if the
    # name was actually plain ASCII (no Unicode chars to misencode). Module-level (was nested in
    # list_ui_devices) so the name-based resolvers clean BOTH sides of a comparison the same way:
    # the UI sends the cleaned name, and the raw PyAudio name has to be cleaned before it can match.
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _as_index(spec):
    """Return spec as an int when it is one (a bare index, from the CLI --loopback-device N), or
    None when it is a name. A device NAME is never int-parseable, so this is what decides whether a
    spec takes the positional branch (kept for the CLI) or the name branch (what the UI sends)."""
    try:
        return int(spec)
    except (TypeError, ValueError):
        return None


def print_devices():
    """Print available loopback (system audio) and mic devices."""
    p = pa.PyAudio()
    try:
        try:
            default_lb = p.get_default_wasapi_loopback()
        except Exception:
            default_lb = None
        try:
            default_mic = p.get_default_input_device_info()
        except Exception:
            default_mic = None

        print()
        print("Loopback sources (WASAPI, captures audio playing through these speakers):")
        for info in p.get_loopback_device_info_generator():
            marker = "  <-- default" if default_lb and info["index"] == default_lb["index"] else ""
            print(f"  [{info['index']:>3}] {info['name']}  ({int(info['defaultSampleRate'])} Hz x{info['maxInputChannels']}ch){marker}")

        print()
        print("Microphones (real input devices):")
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0 and not info.get("isLoopbackDevice"):
                marker = "  <-- default" if default_mic and info["index"] == default_mic["index"] else ""
                print(f"  [{info['index']:>3}] {info['name']}  ({int(info['defaultSampleRate'])} Hz x{info['maxInputChannels']}ch){marker}")

        print()
        print("Override defaults with:")
        print("  --loopback-device <index>   or   --loopback-device 'name substring'")
        print("  --mic-device <index>        or   --mic-device 'name substring'")
    finally:
        p.terminate()


def _wasapi_host_index(p):
    """The WASAPI host-API index, or None when it cannot be read."""
    try:
        return p.get_host_api_info_by_type(pa.paWASAPI)["index"]
    except Exception:
        return None


def _mic_pool(p):
    """The candidate input devices, WASAPI-first (codex F3). resolve_mic must resolve against the
    SAME preferred pool list_ui_devices enumerates, or an identically named MME/DirectSound duplicate
    at a lower index could win over the WASAPI endpoint the UI actually listed and offered. So: the
    WASAPI mics when WASAPI exposes any, otherwise every real input device (the same fallback the UI
    listing uses when WASAPI has none)."""
    allmics = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0 and not info.get("isLoopbackDevice", False):
            allmics.append(info)
    wasapi_idx = _wasapi_host_index(p)
    wasapi_mics = [m for m in allmics if wasapi_idx is not None and m["hostApi"] == wasapi_idx]
    return wasapi_mics if wasapi_mics else allmics


def resolve_loopback(p, spec):
    """Return a PortAudio device info dict for a loopback (system audio) device.

    Resolution order (codex F4): None -> the system default; then an EXACT cleaned-name match (the
    UI's value, which can itself be numeric like "123"); then a bare integer as a positional index
    (the CLI --loopback-device N, which still raises when it lands on a non-loopback device); then the
    historical case-insensitive substring. Exact-name-before-index is what lets a device whose
    friendly name happens to be a number resolve as a name, while a CLI numeric spec (no device is
    named "26") still selects by position. Resolving by name is what makes a selection survive the
    endpoint renumbering that plugging headphones triggers."""
    if spec is None:
        return p.get_default_wasapi_loopback()
    want = str(spec).strip()
    loopbacks = list(p.get_loopback_device_info_generator())
    for info in loopbacks:
        if _fix_name(info["name"]).strip() == want:
            return info
    idx = _as_index(spec)
    if idx is not None:
        info = p.get_device_info_by_index(idx)
        if not info.get("isLoopbackDevice"):
            raise ValueError(f"Device #{idx} '{_fix_name(info['name'])}' is not a loopback device.")
        return info
    sub = want.lower()
    for info in loopbacks:
        if sub in _fix_name(info["name"]).strip().lower():
            return info
    raise ValueError(f"No loopback device matching {spec!r}. Run --list-devices.")


def resolve_mic(p, spec):
    """Return a PortAudio device info dict for a microphone (non-loopback input).

    Same order as resolve_loopback: None -> the system default; EXACT cleaned-name match over the
    WASAPI-first pool (codex F3/F4); then a bare integer as a positional index (CLI; raises on no
    input channels); then substring over the pool. Resolving against the same preferred pool the UI
    lists means an identically named MME duplicate can never win over the WASAPI endpoint that was
    offered, and loopbacks are excluded throughout so a name can never cross classes."""
    if spec is None:
        return p.get_default_input_device_info()
    want = str(spec).strip()
    pool = _mic_pool(p)
    for info in pool:
        if _fix_name(info["name"]).strip() == want:
            return info
    idx = _as_index(spec)
    if idx is not None:
        info = p.get_device_info_by_index(idx)
        if info["maxInputChannels"] == 0:
            raise ValueError(f"Device #{idx} '{_fix_name(info['name'])}' has no input channels.")
        return info
    sub = want.lower()
    for info in pool:
        if sub in _fix_name(info["name"]).strip().lower():
            return info
    raise ValueError(f"No mic matching {spec!r}. Run --list-devices.")


def default_loopback_name(p=None):
    """Cleaned name of the current default WASAPI loopback, or None when there is none.

    Opens and terminates its OWN PyAudio when not given one, so the follow-the-default watcher can
    read the default each tick without holding a PyAudio handle open between ticks (holding one is
    what the task brief forbids: a stale handle would not see the endpoint the OS just switched to)."""
    own = p is None
    if own:
        p = pa.PyAudio()
    try:
        return _fix_name(p.get_default_wasapi_loopback()["name"]).strip()
    except Exception:
        return None
    finally:
        if own:
            p.terminate()


def list_ui_devices():
    """List the mics and loopbacks the user can pick, for the UI's /api/devices.

    PyAudio enumerates every physical device once PER HOST API (MME +
    DirectSound + WASAPI + WDM-KS), so on a typical laptop a single Realtek
    mic appears 3-4 times under the same name. Plus the MME / DirectSound
    meta-devices ("Microsoft Sound Mapper", "Primary Sound Capture Driver")
    that point at "whatever Windows currently calls default" are not real
    devices users should pick.

    We filter to WASAPI-only for mics, matching what we already do for
    loopbacks (loopback is WASAPI-exclusive on Windows). One entry per
    physical device, all on the modern API. If WASAPI itself misbehaves
    on a particular machine, the CLI `--list-devices` still shows every
    host API for diagnostic purposes; this function is for the UI.
    """
    p = pa.PyAudio()
    try:
        loopbacks = [
            {"index": info["index"], "name": _fix_name(info["name"]), "rate": int(info["defaultSampleRate"])}
            for info in p.get_loopback_device_info_generator()
        ]
        try:
            default_lb = p.get_default_wasapi_loopback()
            default_lb_idx = default_lb["index"]
        except Exception:
            default_lb_idx = None

        try:
            wasapi_idx = p.get_host_api_info_by_type(pa.paWASAPI)["index"]
        except Exception:
            wasapi_idx = None

        try:
            default_in = p.get_default_input_device_info()
            default_in_idx = default_in["index"]
        except Exception:
            default_in_idx = None

        def _collect_mics(wasapi_only):
            # Dedupe by (cleaned name, rate): the same physical mic is enumerated once
            # per host API (MME / DirectSound / WASAPI), so collapse those duplicates.
            out, seen = [], set()
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info["maxInputChannels"] <= 0 or info.get("isLoopbackDevice"):
                    continue
                if wasapi_only and wasapi_idx is not None and info["hostApi"] != wasapi_idx:
                    continue
                name = _fix_name(info["name"])
                rate = int(info["defaultSampleRate"])
                if (name, rate) in seen:
                    continue
                seen.add((name, rate))
                out.append({"index": info["index"], "name": name, "rate": rate})
            return out

        # WASAPI-only by default (clean list); if WASAPI exposes no input endpoints,
        # fall back to every real mic so the dropdown is never empty.
        mics = _collect_mics(wasapi_only=True)
        if not mics:
            mics = _collect_mics(wasapi_only=False)

        # The system default mic may be on a non-WASAPI host API. Map it to the device
        # with the same CLEANED name so the dropdown's default highlight is correct
        # (compare _fix_name to _fix_name; a raw vs cleaned mismatch would miss). Never
        # silently pick a different mic: with no match and more than one candidate, leave
        # the default unset rather than risk opening the wrong device at /api/start.
        if default_in_idx is not None and not any(m["index"] == default_in_idx for m in mics):
            try:
                default_name = _fix_name(p.get_device_info_by_index(default_in_idx)["name"])
                match = next((m for m in mics if m["name"] == default_name), None)
            except Exception:
                match = None
            if match:
                default_in_idx = match["index"]
            elif len(mics) == 1:
                default_in_idx = mics[0]["index"]
            else:
                default_in_idx = None

        return {
            "loopbacks": loopbacks,
            "mics": mics,
            "default_loopback_index": default_lb_idx,
            "default_mic_index": default_in_idx,
        }
    finally:
        p.terminate()
