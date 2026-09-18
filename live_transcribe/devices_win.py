"""Audio device enumeration via pyaudiowpatch (Windows backend).

pyaudiowpatch is a PyAudio fork that exposes WASAPI loopback devices as
first-class PortAudio devices. Default soundcard/sounddevice don't support
WASAPI loopback at all on Windows, pyaudiowpatch is the right tool.
"""
import contextlib
import threading
import time

import pyaudiowpatch as pa


# --- PortAudio lifecycle guard ------------------------------------------------
# PortAudio builds its device table exactly once, when Pa_Initialize takes the init count from 0 to
# 1, and only rebuilds it after EVERY PyAudio instance has terminated (the init-count trap, see the
# mmdevice_win header). So a capture that starts while a short-lived enumeration helper still holds a
# PyAudio open inherits that helper's table and cannot see an endpoint plugged in since. Every
# PyAudio in this process is now created through pa_acquire / pa_release (or the pa_session context
# manager), which counts the live instances thread-safely and records when the table was last
# (re)built, so a capture can wait briefly for a clean rebuild and the diagnostics can log its age.
class CapturePortAudioBusy(RuntimeError):
    """Raised by pa_acquire("enum") when a live capture OWNS PortAudio. Creating a second PyAudio then
    both inherits the capture's frozen device table AND breaks the locked rule "never a second PyAudio
    while a capture owns PortAudio", so enumeration is refused and the caller serves its last-good
    cached listing instead (see list_ui_devices). A capture acquisition (role="capture") is never
    refused; it waits for enum helpers to drain."""


_pa_lock = threading.Lock()
_pa_count = 0
_pa_built_at = None      # time.monotonic() when the count last went 0 -> 1 (the table was rebuilt)
_capture_ids = set()     # id() of live capture-role PyAudio instances: while any is live a capture
                         # OWNS PortAudio and no enumeration instance may be created (the locked rule)
_last_ui_devices = None  # last successful list_ui_devices() result, served when enumeration is refused
                         # because a capture owns PortAudio (codex F1)

# How long a capture waits for live enumeration helpers to release before it builds anyway. Bounded
# so a stuck helper can never deadlock a session start; on expiry the capture builds on the (stale)
# table and logs a loud warning with its age rather than fail the start.
_CAPTURE_WAIT_S = 1.5
_CAPTURE_POLL_S = 0.02


def pa_instances():
    """The number of live PyAudio instances in this process (PortAudio's init count)."""
    with _pa_lock:
        return _pa_count


def pa_table_age_s():
    """Seconds since PortAudio last (re)built its device table (the count went 0 -> 1), or None when
    no PyAudio instance is live. This is the age of the device view every live PyAudio() shares."""
    with _pa_lock:
        if _pa_count == 0 or _pa_built_at is None:
            return None
        return time.monotonic() - _pa_built_at


def capture_owns_portaudio():
    """True while a capture holds a PyAudio open: it OWNS the (frozen) PortAudio device table, and no
    enumeration instance may be created until it releases."""
    with _pa_lock:
        return bool(_capture_ids)


def await_capture_slot(timeout_s=_CAPTURE_WAIT_S):
    """Wait (bounded) for every live enumeration helper to release, so a capture about to start builds
    its device table from a clean 0 -> 1 transition (the init-count trap) rather than inheriting a
    stale one. Meant to be called OUTSIDE any request lock (codex F1): the session start drains helpers
    here BEFORE it takes STATE.lock, so the poll sleep never blocks /api/status. Never deadlocks; on
    expiry it returns and the caller builds anyway. Returns True when the slot is clean (no live
    instance), False when it timed out with one still live (a loud warning is logged then)."""
    deadline = time.monotonic() + timeout_s
    while True:
        with _pa_lock:
            live = _pa_count
            age = None if _pa_built_at is None else time.monotonic() - _pa_built_at
        if live == 0:
            return True
        if time.monotonic() >= deadline:
            age_txt = f"{age:.1f}s" if age is not None else "unknown"
            print(f"[devices] WARNING starting capture with {live} PyAudio instance(s) still live; "
                  f"PortAudio keeps its {age_txt}-old device table rather than rebuild it "
                  "(init-count trap)", flush=True)
            return False
        time.sleep(_CAPTURE_POLL_S)


def pa_acquire(role="enum"):
    """Create a PyAudio instance and account for it process-wide.

    role="capture" first waits (bounded) for any live enumeration helper to release, then takes
    ownership; while it is live, every role="enum" acquire is REFUSED with CapturePortAudioBusy (a
    second PyAudio during capture inherits the frozen table AND breaks the locked lifecycle rule).
    role="enum" (the short-lived listings) never waits, and raises CapturePortAudioBusy when a capture
    owns PortAudio so the caller can serve its last-good cache. The refusal check, the PyAudio create
    and the bookkeeping all happen under _pa_lock, so capture ownership and enumeration are mutually
    exclusive. Pair every call with pa_release, or use the pa_session context manager."""
    global _pa_count, _pa_built_at
    if role == "capture":
        await_capture_slot()
    with _pa_lock:
        if role != "capture" and _capture_ids:
            raise CapturePortAudioBusy(
                "a live capture owns PortAudio; enumeration is refused (serve the last-good listing)")
        p = pa.PyAudio()
        _pa_count += 1
        if _pa_count == 1:
            _pa_built_at = time.monotonic()
        if role == "capture":
            _capture_ids.add(id(p))
    return p


def pa_release(p):
    """Terminate a PyAudio instance from pa_acquire / pa_session and update the live-instance count.
    The count is decremented even when terminate() raises (in a finally), and the exception is
    re-raised so a caller that reports teardown success (capture._release_backend) still sees it."""
    global _pa_count, _pa_built_at
    try:
        p.terminate()
    finally:
        with _pa_lock:
            _pa_count = max(0, _pa_count - 1)
            _capture_ids.discard(id(p))
            if _pa_count == 0:
                _pa_built_at = None


@contextlib.contextmanager
def pa_session(role="enum"):
    """Context-manager form of pa_acquire / pa_release for a short-lived PyAudio: it always
    terminates its instance, even on an exception (the enumeration helpers' try/finally). Propagates
    CapturePortAudioBusy from pa_acquire when a capture owns PortAudio (the block never runs then)."""
    p = pa_acquire(role)
    try:
        yield p
    finally:
        pa_release(p)


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
    p = pa_acquire()
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
        pa_release(p)


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


def resolve_loopback(p, spec, positional=True):
    """Return a PortAudio device info dict for a loopback (system audio) device.

    `positional` (codex G2): the CLI passes True so a bare integer selects by position; the web layer
    passes False, because a UI value is ALWAYS a name (a device named "2", or a stale numeric value
    whose device has vanished, must resolve by name only and raise when missing, never fall through to
    positional index 2).

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
    if positional:
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


def resolve_mic(p, spec, positional=True):
    """Return a PortAudio device info dict for a microphone (non-loopback input).

    Same order as resolve_loopback: None -> the system default; EXACT cleaned-name match over the
    WASAPI-first pool (codex F3); then (only when `positional`) a bare integer as a positional index
    (CLI; raises on no input channels); then substring over the pool. `positional` is False from the
    web layer so a numeric UI value is name-only and never opens a positional index (codex G2).
    Resolving against the same preferred pool the UI lists means an identically named MME duplicate
    can never win over the WASAPI endpoint that was offered, and loopbacks are excluded throughout."""
    if spec is None:
        return p.get_default_input_device_info()
    want = str(spec).strip()
    pool = _mic_pool(p)
    for info in pool:
        if _fix_name(info["name"]).strip() == want:
            return info
    if positional:
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


def loopback_candidate_names(p):
    """Cleaned names resolve_loopback searches, in order, for the resolve-failure diagnostic. Device
    names only (never audio), so it is safe to log."""
    return [_fix_name(info["name"]).strip() for info in p.get_loopback_device_info_generator()]


def mic_candidate_names(p):
    """Cleaned names resolve_mic searches (the WASAPI-first pool), for the resolve-failure diagnostic.
    Device names only (never audio), so it is safe to log."""
    return [_fix_name(info["name"]).strip() for info in _mic_pool(p)]


def default_loopback_name(p=None):
    """Cleaned name of the current default WASAPI loopback, or None when there is none.

    Opens and terminates its OWN PyAudio when not given one, so the follow-the-default watcher can
    read the default each tick without holding a PyAudio handle open between ticks (holding one is
    what the task brief forbids: a stale handle would not see the endpoint the OS just switched to)."""
    with (contextlib.nullcontext(p) if p is not None else pa_session()) as p:
        try:
            return _fix_name(p.get_default_wasapi_loopback()["name"]).strip()
        except Exception:
            return None


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

    While a live capture OWNS PortAudio (codex F1) enumeration is refused rather than spinning a
    second PyAudio: this returns the last-good listing instead (or an empty shape when we have never
    listed), so the UI still has devices and the locked lifecycle rule holds.
    """
    global _last_ui_devices
    try:
        p = pa_acquire()
    except CapturePortAudioBusy:
        print("[devices] enumeration refused while a capture owns PortAudio; serving the last-good "
              "listing", flush=True)
        if _last_ui_devices is not None:
            return dict(_last_ui_devices)
        return {"loopbacks": [], "mics": [], "default_loopback_index": None, "default_mic_index": None}
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

        _last_ui_devices = {
            "loopbacks": loopbacks,
            "mics": mics,
            "default_loopback_index": default_lb_idx,
            "default_mic_index": default_in_idx,
        }
        return dict(_last_ui_devices)
    finally:
        pa_release(p)
