"""Audio device enumeration via pyaudiowpatch (Windows backend).

pyaudiowpatch is a PyAudio fork that exposes WASAPI loopback devices as
first-class PortAudio devices. Default soundcard/sounddevice don't support
WASAPI loopback at all on Windows, pyaudiowpatch is the right tool.
"""
import pyaudiowpatch as pa


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


def resolve_loopback(p, spec):
    """Return a PortAudio device info dict for a loopback (system audio) device."""
    if spec is None:
        return p.get_default_wasapi_loopback()
    try:
        idx = int(spec)
        info = p.get_device_info_by_index(idx)
        if not info.get("isLoopbackDevice"):
            raise ValueError(f"Device #{idx} '{info['name']}' is not a loopback device.")
        return info
    except (TypeError, ValueError) as e:
        if "is not a loopback" in str(e):
            raise
    sub = str(spec).lower()
    for info in p.get_loopback_device_info_generator():
        if sub in info["name"].lower():
            return info
    raise ValueError(f"No loopback device matching {spec!r}. Run --list-devices.")


def resolve_mic(p, spec):
    """Return a PortAudio device info dict for a microphone (non-loopback input)."""
    if spec is None:
        return p.get_default_input_device_info()
    try:
        idx = int(spec)
        info = p.get_device_info_by_index(idx)
        if info["maxInputChannels"] == 0:
            raise ValueError(f"Device #{idx} '{info['name']}' has no input channels.")
        return info
    except (TypeError, ValueError) as e:
        if "no input channels" in str(e):
            raise
    sub = str(spec).lower()
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if (
            info["maxInputChannels"] > 0
            and not info.get("isLoopbackDevice", False)
            and sub in info["name"].lower()
        ):
            return info
    raise ValueError(f"No mic matching {spec!r}. Run --list-devices.")


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
    def _fix_name(s):
        # PyAudio returns device names as latin-1-encoded bytes wrapped in a
        # Python str, so a real "Intel(R)" comes back as the mojibake we'd see
        # if you decoded UTF-8 as latin-1. Reverse it: encode the str's code
        # points as latin-1 bytes, decode those bytes as UTF-8. Falls open if
        # the name was actually plain ASCII (no Unicode chars to misencode).
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
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
