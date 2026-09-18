r"""Verification kit for the 1.14.2 Automatic audio-source behaviour.

This talks to an ALREADY-RUNNING Volksmond over HTTP on the loopback interface. It reads nothing but
the local API and it starts nothing without asking you first. Standard library only.

What it does, in order:

  1. Reads GET /api/devices and prints the microphones and system-audio outputs the app can open
     right now, which ones it treats as junk (webcam mics, monitor speakers, virtual devices), the
     device Automatic would pick for each side, and the live mic / loopback modes.

  2. (Only after you confirm.) Starts a very short RECORD-ONLY session with a deliberately bogus
     microphone name, then reads GET /api/status to show that the app fell back to Automatic with a
     notice instead of failing, and immediately stops the session so nothing keeps recording. This
     step captures a moment of audio and writes a short recording into your sessions folder. Skip it
     with --no-start (or by answering "n") and the rest still runs.

  3. Asks you to plug in or unplug an audio device and press Enter, then reads GET /api/devices again
     and shows what changed (devices added or removed, and whether the Automatic picks moved). This
     is how you confirm the app re-reads the live device set rather than a stale, frozen table.

Usage:
    python scripts/repro_frozen_table.py                 # talk to the default port 8765
    python scripts/repro_frozen_table.py --port 8799     # talk to a backend on another port
    python scripts/repro_frozen_table.py --no-start      # never start a session (listing + hot-plug only)
    python scripts/repro_frozen_table.py --yes           # skip the confirmation prompt for the start step
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request

BOGUS_MIC = "Nonexistent Microphone (repro probe)"


def _url(host, port, path):
    return f"http://{host}:{port}{path}"


def _get(host, port, path):
    req = urllib.request.Request(_url(host, port, path), method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(host, port, path, body, csrf):
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    req = urllib.request.Request(_url(host, port, path), data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Volksmond-CSRF", csrf)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _csrf_token(host, port):
    """Read the per-process CSRF token the app embeds in its page, so this script can POST."""
    req = urllib.request.Request(_url(host, port, "/"), method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        html = r.read().decode("utf-8", "replace")
    m = re.search(r'name="vm-csrf"\s+content="([^"]+)"', html)
    if not m:
        raise RuntimeError("Could not find the CSRF token on the page. Is this really Volksmond?")
    return m.group(1)


def _names(devices):
    return [d.get("name", "") for d in devices]


def _print_devices(dev):
    mics = dev.get("mics", []) or []
    loops = dev.get("loopbacks", []) or []
    print("  Microphones:")
    for d in mics:
        tag = "  [junk]" if d.get("junk") else ""
        print(f"    - {d.get('name', '')}{tag}")
    print("  System-audio outputs:")
    for d in loops:
        tag = "  [junk]" if d.get("junk") else ""
        print(f"    - {d.get('name', '')}{tag}")
    print(f"  Automatic mic pick:      {dev.get('auto_mic_name')!r}   (mode: {dev.get('mic_mode')})")
    print(f"  Automatic output pick:   {dev.get('auto_loopback_name')!r}   (mode: {dev.get('loopback_mode')})")
    print(f"  Remembered mic / output: {dev.get('saved_mic_name')!r} / {dev.get('saved_loopback_name')!r}")


def _diff_devices(before, after):
    def _report(label, key):
        b, a = set(_names(before.get(key, []) or [])), set(_names(after.get(key, []) or []))
        added, removed = sorted(a - b), sorted(b - a)
        if not added and not removed:
            print(f"  {label}: no change")
        for n in added:
            print(f"  {label}: + {n}   (appeared)")
        for n in removed:
            print(f"  {label}: - {n}   (gone)")

    _report("Microphones", "mics")
    _report("System-audio outputs", "loopbacks")
    for key, label in (("auto_mic_name", "Automatic mic pick"), ("auto_loopback_name", "Automatic output pick")):
        if before.get(key) != after.get(key):
            print(f"  {label} moved: {before.get(key)!r} -> {after.get(key)!r}")
        else:
            print(f"  {label} unchanged: {after.get(key)!r}")


def _confirm(question):
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run(host, port, allow_start, assume_yes):
    try:
        csrf = _csrf_token(host, port)
    except (urllib.error.URLError, ConnectionError) as e:
        print(f"Could not reach Volksmond at {_url(host, port, '/')}: {e}")
        print("Start Volksmond first, or point this at the right port with --port.")
        return 2

    print(f"Connected to Volksmond at http://{host}:{port}\n")

    print("STEP 1: the devices the app can open right now")
    before = _get(host, port, "/api/devices")
    _print_devices(before)
    print()

    if allow_start:
        print("STEP 2: prove a bogus microphone falls back to Automatic (does not fail)")
        print("  This STARTS a short RECORD-ONLY session with a deliberately bogus microphone name,")
        print("  captures a moment of audio, writes a short recording to your sessions folder, reads")
        print("  the status, then STOPS the session immediately.")
        go = assume_yes or _confirm("  Start the short repro session now? [y/N] ")
        if go:
            started = _post(host, port, "/api/start", {
                "topic": "Automatic fallback repro",
                "transcribe": False,
                "record": True,
                "mic_device": BOGUS_MIC,
            }, csrf)
            print(f"  /api/start returned (recording={started.get('recording')}, transcribing={started.get('transcribing')}).")
            status = _get(host, port, "/api/status")
            notice = status.get("device_notice")
            print(f"  Live mic mode:  {status.get('mic_mode')!r}   (expected 'auto': it fell back)")
            print(f"  Device notice:  {notice!r}")
            if status.get("mic_mode") == "auto" and notice:
                print("  PASS: the bogus mic was not used; the app fell back to Automatic with a notice.")
            else:
                print("  NOTE: no fallback notice seen. On non-Windows this path is a no-op.")
            _post(host, port, "/api/stop?what=all", None, csrf)
            print("  Session stopped.")
        else:
            print("  Skipped the start step.")
    else:
        print("STEP 2: skipped (--no-start): no session is started.")
    print()

    print("STEP 3: confirm the device list is live, not a frozen table")
    try:
        input("  Plug in OR unplug an audio device (headphones, a webcam, a USB mic), then press Enter... ")
    except EOFError:
        print("  No interactive input available; reading the device list again without a change.")
    after = _get(host, port, "/api/devices")
    _diff_devices(before, after)
    print("\nDone.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Verify Volksmond's Automatic audio-source behaviour against a running app.")
    p.add_argument("--host", default="127.0.0.1", help="Host the app is on (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765, help="Port the app is on (default: 8765)")
    p.add_argument("--no-start", action="store_true", help="Never start a session; only list devices and diff a hot-plug")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation prompt before the start step")
    args = p.parse_args(argv)
    return run(args.host, args.port, allow_start=not args.no_start, assume_yes=args.yes)


if __name__ == "__main__":
    sys.exit(main())
