"""Tests for the Windows audio-device resolver (live_transcribe.devices_win).

These pin the 1.14.1 device-identity fix: selections resolve by cleaned device NAME (stable across
the endpoint renumbering that plugging headphones triggers), not by positional index, while a bare
numeric index still works for the CLI and a stale one that lands on the wrong class still raises.

No real audio: a fake PyAudio (a fixed device table) is passed straight into the resolvers, and
devices_win.pa is monkeypatched for the two functions that construct their own PyAudio. So the file
is Windows-independent, it never opens a device and never needs the real pyaudiowpatch present.

Run:  python tests/test_devices_win.py   (from the project root; exit 0 = pass)
"""
import os
import sys
import types

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# devices_win imports pyaudiowpatch at module load; the real package is Windows-only, so stub it
# when it is absent (a non-Windows CI) BEFORE importing. On Windows the real one loads; either way
# every test that needs a PyAudio() monkeypatches devices_win.pa to the fake below.
try:
    import pyaudiowpatch  # noqa: F401
except Exception:
    _stub = types.ModuleType("pyaudiowpatch")
    _stub.paWASAPI = 2
    _stub.PyAudio = object
    sys.modules["pyaudiowpatch"] = _stub

from live_transcribe import devices_win

WASAPI_IDX = 2
MME_IDX = 0

# A device table modelled on the reproduced bug: render endpoints, real mics (one on a second host
# API to exercise the dedupe), WASAPI loopbacks, a mojibake mic name, and an "Aux" loopback pair that
# would resolve to the WRONG device under a substring-first search (so exact-first is provable).
DEVICES = [
    {"index": 0, "name": "Realtek HD Audio 2nd output (Realtek(R) Audio)", "maxInputChannels": 0, "isLoopbackDevice": False, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
    {"index": 1, "name": "Speakers (Realtek(R) Audio)", "maxInputChannels": 0, "isLoopbackDevice": False, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
    {"index": 2, "name": "Microphone (2- Samson C01U              )", "maxInputChannels": 1, "isLoopbackDevice": False, "hostApi": WASAPI_IDX, "defaultSampleRate": 44100.0},
    {"index": 3, "name": "Mic in at front panel (Pink)", "maxInputChannels": 2, "isLoopbackDevice": False, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
    {"index": 4, "name": "Realtek HD Audio 2nd output (Realtek(R) Audio) [Loopback]", "maxInputChannels": 2, "isLoopbackDevice": True, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
    {"index": 5, "name": "Speakers (Realtek(R) Audio) [Loopback]", "maxInputChannels": 2, "isLoopbackDevice": True, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
    # Raw mojibake: real "Café Mic" comes back from PyAudio as its UTF-8 bytes decoded as latin-1.
    {"index": 6, "name": "CafÃ© Mic", "maxInputChannels": 1, "isLoopbackDevice": False, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
    # The same Samson enumerated again on a second host API (MME): list_ui_devices must collapse it.
    {"index": 7, "name": "Microphone (2- Samson C01U              )", "maxInputChannels": 1, "isLoopbackDevice": False, "hostApi": MME_IDX, "defaultSampleRate": 44100.0},
    {"index": 8, "name": "Aux [Loopback] (2)", "maxInputChannels": 2, "isLoopbackDevice": True, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
    {"index": 9, "name": "Aux [Loopback]", "maxInputChannels": 2, "isLoopbackDevice": True, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
]


class FakePyAudio:
    """The slice of the pyaudiowpatch.PyAudio surface the resolvers touch, over a device table (the
    module DEVICES by default). Index equals position (as it does for real PyAudio), so
    get_device_info_by_index(i) returns table[i]."""

    def __init__(self, table=None):
        self.terminated = False
        self._table = table if table is not None else DEVICES

    def get_device_count(self):
        return len(self._table)

    def get_device_info_by_index(self, i):
        for d in self._table:
            if d["index"] == i:
                return dict(d)
        raise ValueError(f"[Errno -9996] Invalid device #{i}")   # what real PyAudio raises

    def get_loopback_device_info_generator(self):
        for d in self._table:
            if d.get("isLoopbackDevice"):
                yield dict(d)

    def get_default_wasapi_loopback(self):
        return dict(DEVICES[4])   # the "2nd output" loopback is the current default output

    def get_default_input_device_info(self):
        return dict(DEVICES[2])   # the Samson is the default mic

    def get_host_api_info_by_type(self, t):
        return {"index": WASAPI_IDX}

    def terminate(self):
        self.terminated = True


def _fake_pa_module():
    m = types.SimpleNamespace()
    m.PyAudio = FakePyAudio
    m.paWASAPI = WASAPI_IDX
    return m


def test_stale_numeric_index_pointing_at_a_render_device_raises():
    # The whole bug: after renumbering, a stored numeric index can land on a render (output) device.
    # A numeric spec is still accepted (CLI parity), but the class is checked, so it raises rather
    # than silently opening the wrong endpoint. Both string and int forms take the numeric branch.
    p = FakePyAudio()
    for spec in ("0", 0, 1):
        try:
            devices_win.resolve_loopback(p, spec)
        except ValueError as e:
            assert "is not a loopback" in str(e), e
        else:
            raise AssertionError(f"resolve_loopback({spec!r}) on a render device should raise")
    # A valid loopback index still resolves (the CLI --loopback-device N path).
    assert devices_win.resolve_loopback(p, 4)["index"] == 4
    assert devices_win.resolve_loopback(p, "5")["index"] == 5
    print("  OK  a stale numeric index on a render device raises; a valid loopback index still resolves")


def test_name_resolution_is_exact_first_then_substring():
    p = FakePyAudio()
    # Exact cleaned-name match (what the UI sends) wins.
    assert devices_win.resolve_loopback(p, "Speakers (Realtek(R) Audio) [Loopback]")["index"] == 5
    assert devices_win.resolve_loopback(p, "Realtek HD Audio 2nd output (Realtek(R) Audio) [Loopback]")["index"] == 4
    # Case-insensitive substring is the fallback when there is no exact match.
    assert devices_win.resolve_loopback(p, "peakers")["index"] == 5
    # Exact beats substring: "Aux [Loopback]" is a substring of "Aux [Loopback] (2)" (index 8, seen
    # first), but the EXACT device is index 9. A substring-first search would return 8; exact-first
    # returns 9.
    assert devices_win.resolve_loopback(p, "Aux [Loopback]")["index"] == 9, \
        "exact match must win over an earlier substring match"
    # No match at all still raises.
    try:
        devices_win.resolve_loopback(p, "no such device")
    except ValueError:
        pass
    else:
        raise AssertionError("a name matching nothing should raise")
    print("  OK  loopback name resolution: exact first, then substring; exact beats an earlier substring")


def test_mojibake_names_compare_equal():
    # The UI shows the CLEANED name ("Café Mic"); the raw PyAudio name is the latin-1 mojibake. A
    # name match must clean the raw side so the UI-visible form resolves.
    p = FakePyAudio()
    assert devices_win.resolve_mic(p, "Café Mic")["index"] == 6, "cleaned UI name did not match the mojibake device"
    print("  OK  a mojibake device name matches the cleaned name the UI sends")


def test_resolve_mic_ignores_loopbacks_and_trailing_spaces():
    p = FakePyAudio()
    # The UI-visible Samson name carries internal trailing spaces; the exact form still resolves.
    assert devices_win.resolve_mic(p, "Microphone (2- Samson C01U              )")["index"] == 2
    # A loopback NAME must never resolve as a mic (name search skips loopbacks): it raises.
    for spec in ("Realtek HD Audio 2nd output (Realtek(R) Audio) [Loopback]", "[Loopback]"):
        try:
            devices_win.resolve_mic(p, spec)
        except ValueError:
            pass
        else:
            raise AssertionError(f"resolve_mic({spec!r}) must not return a loopback device")
    # A render device with no input channels is not a mic either.
    try:
        devices_win.resolve_mic(p, "Speakers (Realtek(R) Audio)")
    except ValueError:
        pass
    else:
        raise AssertionError("a render device with no input channels must not resolve as a mic")
    print("  OK  resolve_mic resolves by exact name (with trailing spaces) and never returns a loopback/output")


def test_resolve_mic_prefers_wasapi_over_an_earlier_mme_duplicate():
    # codex F3: an identically named MME entry placed BEFORE the WASAPI endpoint must not win. The UI
    # lists the WASAPI endpoint, so resolution has to resolve against the same WASAPI-first pool, or a
    # switch could open a device with different channel/rate capabilities from the one advertised.
    MME = 0
    table = [
        {"index": 0, "name": "Studio Mic", "maxInputChannels": 2, "isLoopbackDevice": False, "hostApi": MME, "defaultSampleRate": 44100.0},
        {"index": 1, "name": "Studio Mic", "maxInputChannels": 2, "isLoopbackDevice": False, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
    ]
    p = FakePyAudio(table)
    got = devices_win.resolve_mic(p, "Studio Mic")
    assert got["index"] == 1 and got["hostApi"] == WASAPI_IDX, \
        f"resolve_mic must return the WASAPI endpoint, not the earlier MME duplicate: {got}"
    print("  OK  resolve_mic returns the WASAPI endpoint over an earlier identically named MME entry (F3)")


def test_a_numeric_device_name_resolves_as_a_name_not_an_index():
    # codex F4: a device whose friendly name is "123" is sent from the UI as "123". Exact-name-first
    # means it resolves to that device, not to positional index 123. A CLI numeric spec (no device is
    # named "26") still selects positionally.
    table = [
        {"index": 0, "name": "Speakers X", "maxInputChannels": 0, "isLoopbackDevice": False, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
        {"index": 1, "name": "123", "maxInputChannels": 1, "isLoopbackDevice": False, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
        {"index": 2, "name": "Normal Mic", "maxInputChannels": 1, "isLoopbackDevice": False, "hostApi": WASAPI_IDX, "defaultSampleRate": 48000.0},
    ]
    p = FakePyAudio(table)
    got = devices_win.resolve_mic(p, "123")
    assert got["index"] == 1 and got["name"] == "123", f"a numeric NAME must resolve as a name: {got}"
    # A CLI positional index still works when no device carries that name.
    assert devices_win.resolve_mic(p, "2")["index"] == 2
    print("  OK  a numeric device NAME resolves as a name; a positional CLI index still works (F4)")


def test_default_loopback_name_is_cleaned():
    saved = devices_win.pa
    try:
        devices_win.pa = _fake_pa_module()
        assert devices_win.default_loopback_name() == "Realtek HD Audio 2nd output (Realtek(R) Audio) [Loopback]"
    finally:
        devices_win.pa = saved
    print("  OK  default_loopback_name returns the cleaned default-loopback name")


def test_list_ui_devices_includes_the_cleaned_name_and_dedupes():
    saved = devices_win.pa
    try:
        devices_win.pa = _fake_pa_module()
        out = devices_win.list_ui_devices()
    finally:
        devices_win.pa = saved
    mic_names = [m["name"] for m in out["mics"]]
    loop_names = [l["name"] for l in out["loopbacks"]]
    # The values the UI puts in its dropdowns and sends back to resolve_* are these cleaned names.
    assert "Microphone (2- Samson C01U              )" in mic_names, mic_names
    assert "Mic in at front panel (Pink)" in mic_names, mic_names
    assert "Café Mic" in mic_names, "mojibake was not cleaned in the UI listing"
    # The Samson is enumerated twice (WASAPI + MME) but appears once.
    assert mic_names.count("Microphone (2- Samson C01U              )") == 1, mic_names
    assert "Realtek HD Audio 2nd output (Realtek(R) Audio) [Loopback]" in loop_names, loop_names
    assert "Speakers (Realtek(R) Audio) [Loopback]" in loop_names, loop_names
    # The defaults point at real listed devices, and the name behind each is resolvable.
    assert out["default_mic_index"] == 2 and out["default_loopback_index"] == 4, out
    print("  OK  list_ui_devices carries cleaned names, dedupes per-host-API duplicates, keeps defaults")


if __name__ == "__main__":
    tests = (test_stale_numeric_index_pointing_at_a_render_device_raises,
             test_name_resolution_is_exact_first_then_substring,
             test_mojibake_names_compare_equal,
             test_resolve_mic_ignores_loopbacks_and_trailing_spaces,
             test_resolve_mic_prefers_wasapi_over_an_earlier_mme_duplicate,
             test_a_numeric_device_name_resolves_as_a_name_not_an_index,
             test_default_loopback_name_is_cleaned,
             test_list_ui_devices_includes_the_cleaned_name_and_dedupes)
    failures = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll devices_win tests passed.")
