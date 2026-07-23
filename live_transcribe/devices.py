"""Platform selector for audio device enumeration.

Each backend module provides the same four callables: `print_devices()` (CLI
diagnostic listing), `resolve_loopback(p, spec)` / `resolve_mic(p, spec)`
(device resolution for the capture backend), and `list_ui_devices()` (the
exact dict the UI's /api/devices endpoint serves:
{loopbacks, mics, default_loopback_index, default_mic_index}).
"""
import sys

if sys.platform == "win32":
    from .devices_win import (
        list_ui_devices,
        print_devices,
        resolve_loopback,
        resolve_mic,
    )
elif sys.platform == "darwin":
    try:
        from .devices_mac import (
            list_ui_devices,
            print_devices,
            resolve_loopback,
            resolve_mic,
        )
    except ImportError as e:
        raise ImportError(
            "macOS device enumeration backend (live_transcribe.devices_mac) is "
            "not available in this build yet; see docs/mac-port-plan.md."
        ) from e
elif sys.platform.startswith("linux"):
    try:
        from .devices_linux import (
            list_ui_devices,
            print_devices,
            resolve_loopback,
            resolve_mic,
        )
    except ImportError as e:
        raise ImportError(
            "Linux device enumeration backend (live_transcribe.devices_linux) "
            "failed to import; see docs/linux-port-plan.md."
        ) from e
else:
    raise ImportError(
        f"no device enumeration backend for platform {sys.platform!r}: "
        "live_transcribe.devices_win covers Windows, "
        "live_transcribe.devices_mac covers macOS and "
        "live_transcribe.devices_linux covers Linux."
    )

__all__ = ["list_ui_devices", "print_devices", "resolve_loopback", "resolve_mic"]
