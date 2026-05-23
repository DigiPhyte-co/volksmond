"""Audio device enumeration via pyaudiowpatch.

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
