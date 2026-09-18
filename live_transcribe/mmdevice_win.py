"""Windows Core Audio (MMDevice) endpoint probe, ctypes-only.

PortAudio builds its device table exactly once, when Pa_Initialize takes the init count from 0 to
1 (pa_front.c). While a live capture holds its PyAudio instance open, every extra PyAudio() in the
process only bumps that count and reads the SAME stale table, so it cannot see an endpoint that was
plugged in after capture started. The follow-the-default watcher and a live /api/devices refresh
therefore cannot use PyAudio to answer "what is the default output right now". This module asks
Windows directly through the MMDevice API, which always reflects the live endpoint set.

Deliberately ctypes-only: no comtypes, no pycaw. The venv has neither and the frozen build must not
grow a dependency. PortAudio's WASAPI device names ARE the MMDevice friendly names (a loopback is
the render endpoint's friendly name plus " [Loopback]"), so the names this module returns line up
with what devices_win enumerates once PortAudio is re-initialised on the next capture rebuild.

Everything is Windows-only and fails soft: any COM failure returns None / empty and logs once.
"""
import ctypes
import math
import sys

# --- COM / MMDevice constants -------------------------------------------------
_CLSCTX_ALL = 0x17
_COINIT_MULTITHREADED = 0x0
_S_OK = 0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = -2147417850   # 0x80010106: COM already inited in another apartment model
_E_NOTFOUND = -2147023728           # no default endpoint is a normal, quiet outcome
_STGM_READ = 0
_DEVICE_STATE_ACTIVE = 0x00000001
_eRender = 0
_eCapture = 1
# ERole: use eMultimedia (1), NOT eConsole (0). PyAudioWPatch's WASAPI host resolves its own default
# device with eMultimedia (pa_win_wasapi.c), so the PortAudio default loopback the capture opens and
# the follow-the-default bookkeeping compare against is the eMultimedia endpoint. Probing eConsole
# here would, when the two roles point at different endpoints, make the watcher chase the console
# default, move capture off the multimedia default, and then mark sys_following_default False,
# silently killing auto-follow. Matching the role keeps all three (probe, listing default,
# bookkeeping) on the same endpoint (codex G1).
_eMultimedia = 1
_eConsole = 0
_eCommunications = 2
_VT_LPWSTR = 31
_VT_UI4 = 19
# EndpointFormFactor (mmdeviceapi.h): 9 = DigitalAudioDisplayDevice, i.e. HDMI / DisplayPort audio.
# Exposed as an int so device_policy can deprioritise it without importing this constant.
_FORMFACTOR_HDMI = 9
_LOOPBACK_SUFFIX = " [Loopback]"

# ERole int -> the canonical role name the detailed probe reports, in a fixed order.
_ROLE_NAMES = {_eConsole: "console", _eMultimedia: "multimedia", _eCommunications: "communications"}
_ROLE_ORDER = ("console", "multimedia", "communications")
_ROLE_BY_NAME = {v: k for k, v in _ROLE_NAMES.items()}
_FLOW_NAMES = {_eRender: "render", _eCapture: "capture"}
_FLOW_BY_NAME = {v: k for k, v in _FLOW_NAMES.items()}

_logged_fail = False


def _log_once(msg):
    global _logged_fail
    if not _logged_fail:
        _logged_fail = True
        print(f"[mmdevice] {msg} (falling back; logged once)", flush=True)


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, d1, d2, d3, d4):
        super().__init__()
        self.Data1, self.Data2, self.Data3 = d1, d2, d3
        for i, b in enumerate(d4):
            self.Data4[i] = b


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_ulong)]


class _PROPVARIANT(ctypes.Structure):
    # Only the VT_LPWSTR case is read (the friendly name). The union is two pointer-sized slots so
    # sizeof matches the real PROPVARIANT on both x86 (16) and x64 (24); pwszVal sits at the union
    # start and is read ONLY when vt == VT_LPWSTR, so a non-string variant never dereferences junk.
    _fields_ = [("vt", ctypes.c_ushort), ("wReserved1", ctypes.c_ushort),
                ("wReserved2", ctypes.c_ushort), ("wReserved3", ctypes.c_ushort),
                ("pwszVal", ctypes.c_wchar_p), ("_pad", ctypes.c_void_p)]


_CLSID_MMDeviceEnumerator = _GUID(0xBCDE0395, 0xE52F, 0x467C, (0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E))
_IID_IMMDeviceEnumerator = _GUID(0xA95664D2, 0x9614, 0x4F35, (0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6))
_PKEY_Device_FriendlyName = _PROPERTYKEY()
_PKEY_Device_FriendlyName.fmtid = _GUID(0xA45C254E, 0xDF1C, 0x4EFD, (0x80, 0x20, 0x67, 0xD1, 0x46, 0xA8, 0x50, 0xE0))
_PKEY_Device_FriendlyName.pid = 14

# PKEY_AudioEndpoint_FormFactor (mmdeviceapi.h): {1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E}, pid 0,
# stored as a VT_UI4 EndpointFormFactor value.
_PKEY_AudioEndpoint_FormFactor = _PROPERTYKEY()
_PKEY_AudioEndpoint_FormFactor.fmtid = _GUID(0x1DA5D803, 0xD492, 0x4EDD, (0x8C, 0x23, 0xE0, 0xC0, 0xFF, 0xEE, 0x7F, 0x0E))
_PKEY_AudioEndpoint_FormFactor.pid = 0

# Interfaces reached through IMMDevice::Activate. IIDs and vtable slots come from the Windows SDK
# headers (endpointvolume.h): a wrong slot is a hard process crash, so these are copied, not guessed.
#   IAudioEndpointVolume  {5CDF2C82-841E-4546-9722-0CF74078229A}: GetMute is vtable slot 15.
#   IAudioMeterInformation {C02216F6-8C67-4B5B-9D00-D008E73E0064}: GetPeakValue is vtable slot 3.
_IID_IAudioEndpointVolume = _GUID(0x5CDF2C82, 0x841E, 0x4546, (0x97, 0x22, 0x0C, 0xF7, 0x40, 0x78, 0x22, 0x9A))
_IID_IAudioMeterInformation = _GUID(0xC02216F6, 0x8C67, 0x4B5B, (0x9D, 0x00, 0xD0, 0x08, 0xE7, 0x3E, 0x00, 0x64))


def _method(ptr, index, argtypes):
    """Bind vtable slot `index` of the COM interface at `ptr` (a c_void_p) as a stdcall callable
    returning HRESULT. `ptr` itself is passed as the implicit `this` first argument by the caller."""
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0]
    fn = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))[index]
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(fn)


def _release(ptr):
    if ptr:
        try:
            _method(ptr, 2, [])(ptr)   # IUnknown::Release
        except Exception:
            pass


def _friendly_name(ole32, pDevice):
    pStore = ctypes.c_void_p()
    hr = _method(pDevice, 4, [ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)])(  # IMMDevice::OpenPropertyStore
        pDevice, _STGM_READ, ctypes.byref(pStore))
    if hr < 0 or not pStore:
        return None
    try:
        pv = _PROPVARIANT()   # ctypes zero-inits it, which is a valid VT_EMPTY PROPVARIANT
        hr = _method(pStore, 5, [ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)])(  # IPropertyStore::GetValue
            pStore, ctypes.byref(_PKEY_Device_FriendlyName), ctypes.byref(pv))
        if hr < 0:
            return None
        try:
            return pv.pwszVal if pv.vt == _VT_LPWSTR else None
        finally:
            try:
                ole32.PropVariantClear(ctypes.byref(pv))
            except Exception:
                pass
    finally:
        _release(pStore)


def _with_enumerator(fn):
    """Init COM on this thread, create an MMDeviceEnumerator, hand it to fn(ole32, pEnum), and tear
    both down. Returns fn's result, or the fail-soft default via the caller's own try/except."""
    if sys.platform != "win32":
        return None
    ole32 = ctypes.windll.ole32
    hr = ole32.CoInitializeEx(None, _COINIT_MULTITHREADED)
    need_uninit = hr in (_S_OK, _S_FALSE)   # RPC_E_CHANGED_MODE: usable, but do not uninit another owner's COM
    try:
        pEnum = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(_CLSID_MMDeviceEnumerator), None, _CLSCTX_ALL,
                                    ctypes.byref(_IID_IMMDeviceEnumerator), ctypes.byref(pEnum))
        if hr < 0 or not pEnum:
            return None
        try:
            return fn(ole32, pEnum)
        finally:
            _release(pEnum)
    finally:
        if need_uninit:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


def _default_endpoint_name(flow, role):
    def _fn(ole32, pEnum):
        pDev = ctypes.c_void_p()
        hr = _method(pEnum, 4, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])(  # GetDefaultAudioEndpoint
            pEnum, flow, role, ctypes.byref(pDev))
        if hr == _E_NOTFOUND or hr < 0 or not pDev:
            return None
        try:
            return _friendly_name(ole32, pDev)
        finally:
            _release(pDev)
    try:
        return _with_enumerator(_fn)
    except Exception as e:
        _log_once(f"default endpoint probe failed: {e}")
        return None


def _norm_flow(flow):
    """Accept an ERole-flow int (_eRender/_eCapture) or 'render'/'capture' and return the int."""
    return _FLOW_BY_NAME.get(flow, flow)


def _norm_role(role):
    """Accept an ERole int or 'console'/'multimedia'/'communications' and return the int."""
    return _ROLE_BY_NAME.get(role, role)


def default_friendly_name(flow, role):
    """Friendly name of the default endpoint for a data flow and ERole, or None. `flow` is
    _eRender/_eCapture (or 'render'/'capture'); `role` is _eConsole/_eMultimedia/_eCommunications
    (or 'console'/'multimedia'/'communications'). Always live (unlike a PyAudio probe during
    capture). The default loopback name for a render endpoint is this plus ' [Loopback]'."""
    return _default_endpoint_name(_norm_flow(flow), _norm_role(role))


def default_render_friendly_name():
    """Friendly name of the current default RENDER (output) endpoint, or None. The default loopback
    name is this plus ' [Loopback]'. Always live (unlike a PyAudio probe during capture)."""
    return default_friendly_name(_eRender, _eMultimedia)


def default_capture_friendly_name():
    """Friendly name of the current default CAPTURE (input) endpoint, or None."""
    return default_friendly_name(_eCapture, _eMultimedia)


def _endpoint_names(ole32, pEnum, flow):
    """The friendly names of this data flow's ACTIVE endpoints, or None when enumeration itself
    failed (codex G4). None is distinct from []: [] means "successfully enumerated, nothing active",
    which is a real answer the UI can show; None means "could not enumerate this class", which must
    fall the whole listing back to PortAudio rather than silently drop a dropdown. A single device
    whose property read fails is skipped (logged once), never failing the whole class."""
    pColl = ctypes.c_void_p()
    hr = _method(pEnum, 3, [ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)])(  # EnumAudioEndpoints
        pEnum, flow, _DEVICE_STATE_ACTIVE, ctypes.byref(pColl))
    if hr < 0 or not pColl:
        return None
    try:
        count = ctypes.c_uint()
        hr = _method(pColl, 3, [ctypes.POINTER(ctypes.c_uint)])(pColl, ctypes.byref(count))  # GetCount
        if hr < 0:
            return None
        out = []
        for i in range(count.value):
            pDev = ctypes.c_void_p()
            hr = _method(pColl, 4, [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)])(  # Item
                pColl, i, ctypes.byref(pDev))
            if hr < 0 or not pDev:
                _log_once("an endpoint could not be opened; skipping it")
                continue
            try:
                nm = _friendly_name(ole32, pDev)
                if nm:
                    out.append(nm)
                else:
                    _log_once("an endpoint's friendly name could not be read; skipping it")
            finally:
                _release(pDev)
        return out
    finally:
        _release(pColl)


def list_endpoints():
    """{'render': names_or_None, 'capture': names_or_None} for the ACTIVE endpoints, live. A class is
    None when it could not be enumerated (COM failure), [] when it enumerated to nothing; the caller
    falls the whole listing back to PortAudio when EITHER class is None (codex G4). Loopback names are
    render names + ' [Loopback]'."""
    def _fn(ole32, pEnum):
        return {"render": _endpoint_names(ole32, pEnum, _eRender),
                "capture": _endpoint_names(ole32, pEnum, _eCapture)}
    try:
        r = _with_enumerator(_fn)
        return r if r is not None else {"render": None, "capture": None}
    except Exception as e:
        _log_once(f"endpoint enumeration failed: {e}")
        return {"render": None, "capture": None}


# --- detailed endpoint probe (WP2) --------------------------------------------
# Pure helpers below take no COM and are unit-tested without Windows.

def _clean_endpoint_name(name):
    """The MMDevice friendly name with any trailing ' [Loopback]' removed. MMDevice itself never
    emits that suffix (PortAudio adds it to loopback capture devices); stripping it defensively
    keeps the detailed probe's names identical to the render friendly names the rest of the app
    compares against."""
    if not name:
        return name
    return name[:-len(_LOOPBACK_SUFFIX)] if name.endswith(_LOOPBACK_SUFFIX) else name


def _amp_to_dbfs(peak):
    """Linear peak amplitude (0.0..1.0, as IAudioMeterInformation::GetPeakValue reports it) to dBFS.
    Silence (peak <= 0) floors at -120.0 dB rather than -inf so callers can sort and format it; any
    result below the floor is clamped to it. Returns None only for a non-numeric input."""
    try:
        p = float(peak)
    except (TypeError, ValueError):
        return None
    if p <= 0.0:
        return -120.0
    db = 20.0 * math.log10(p)
    return -120.0 if db < -120.0 else db


def _roles_for(endpoint_id, default_ids):
    """The roles this endpoint is the default for, in a fixed order. `default_ids` maps a role name
    to the default endpoint ID for that role (or None). An endpoint with no ID owns no role."""
    if not endpoint_id:
        return []
    return [name for name in _ROLE_ORDER if default_ids.get(name) == endpoint_id]


def _endpoint_id(ole32, pDevice):
    """IMMDevice::GetId (vtable slot 5) as a Python str, or None. The returned LPWSTR is owned by the
    caller, so it is freed with CoTaskMemFree once read."""
    pId = ctypes.c_void_p()
    hr = _method(pDevice, 5, [ctypes.POINTER(ctypes.c_void_p)])(pDevice, ctypes.byref(pId))  # GetId
    if hr < 0 or not pId:
        return None
    try:
        return ctypes.wstring_at(pId)
    finally:
        try:
            ole32.CoTaskMemFree(pId)
        except Exception:
            pass


def _name_and_formfactor(ole32, pDevice):
    """Open the device's property store once and read FriendlyName (VT_LPWSTR) and FormFactor
    (VT_UI4). Returns (name_or_None, form_factor_or_None); each is None on its own read failure or a
    wrong variant type. One open serves both properties, which matters on the 1 s probe cadence."""
    pStore = ctypes.c_void_p()
    hr = _method(pDevice, 4, [ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)])(  # OpenPropertyStore
        pDevice, _STGM_READ, ctypes.byref(pStore))
    if hr < 0 or not pStore:
        return None, None
    try:
        name = _store_lpwstr(ole32, pStore, _PKEY_Device_FriendlyName)
        ff = _store_ui4(ole32, pStore, _PKEY_AudioEndpoint_FormFactor)
        return name, ff
    finally:
        _release(pStore)


def _store_lpwstr(ole32, pStore, pkey):
    pv = _PROPVARIANT()
    hr = _method(pStore, 5, [ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)])(  # GetValue
        pStore, ctypes.byref(pkey), ctypes.byref(pv))
    if hr < 0:
        return None
    try:
        return pv.pwszVal if pv.vt == _VT_LPWSTR else None
    finally:
        try:
            ole32.PropVariantClear(ctypes.byref(pv))
        except Exception:
            pass


def _store_ui4(ole32, pStore, pkey):
    pv = _PROPVARIANT()
    hr = _method(pStore, 5, [ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT)])(  # GetValue
        pStore, ctypes.byref(pkey), ctypes.byref(pv))
    if hr < 0:
        return None
    try:
        if pv.vt != _VT_UI4:
            return None
        # The UI4 shares the union offset with pwszVal; read the ULONG there (offset is 8 on both
        # x86 and x64, but taken from the field so it stays correct if the struct ever changes).
        return int(ctypes.cast(ctypes.byref(pv, _PROPVARIANT.pwszVal.offset),
                               ctypes.POINTER(ctypes.c_ulong))[0])
    finally:
        try:
            ole32.PropVariantClear(ctypes.byref(pv))
        except Exception:
            pass


def _endpoint_muted(pDevice):
    """IAudioEndpointVolume::GetMute (vtable slot 15) as a bool, or None if the interface will not
    activate or the call fails. Activated through IMMDevice::Activate (slot 3); reads nothing but the
    mute flag, captures no audio."""
    pVol = ctypes.c_void_p()
    hr = _method(pDevice, 3, [ctypes.POINTER(_GUID), ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)])(  # Activate
        pDevice, ctypes.byref(_IID_IAudioEndpointVolume), _CLSCTX_ALL, None, ctypes.byref(pVol))
    if hr < 0 or not pVol:
        return None
    try:
        mute = ctypes.c_int()
        hr = _method(pVol, 15, [ctypes.POINTER(ctypes.c_int)])(pVol, ctypes.byref(mute))  # GetMute
        if hr < 0:
            return None
        return bool(mute.value)
    finally:
        _release(pVol)


def _endpoint_peak_db(pDevice):
    """IAudioMeterInformation::GetPeakValue (vtable slot 3) converted to dBFS, or None. Activated
    through IMMDevice::Activate (slot 3). The meter is a passive reading of the endpoint's session
    mix and captures no audio. A render endpoint reports the level currently PLAYING; a capture
    endpoint's meter generally reads 0.0 (floor -120.0 dB) unless some stream is open on it."""
    pMeter = ctypes.c_void_p()
    hr = _method(pDevice, 3, [ctypes.POINTER(_GUID), ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)])(  # Activate
        pDevice, ctypes.byref(_IID_IAudioMeterInformation), _CLSCTX_ALL, None, ctypes.byref(pMeter))
    if hr < 0 or not pMeter:
        return None
    try:
        peak = ctypes.c_float()
        hr = _method(pMeter, 3, [ctypes.POINTER(ctypes.c_float)])(pMeter, ctypes.byref(peak))  # GetPeakValue
        if hr < 0:
            return None
        return _amp_to_dbfs(peak.value)
    finally:
        _release(pMeter)


def _default_ids(ole32, pEnum, flow):
    """{role_name: default_endpoint_id_or_None} for all three ERoles of this data flow. A missing
    default (E_NOTFOUND) is a normal, quiet None, not a failure."""
    out = {}
    for role_int, role_name in _ROLE_NAMES.items():
        pDev = ctypes.c_void_p()
        hr = _method(pEnum, 4, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])(  # GetDefaultAudioEndpoint
            pEnum, flow, role_int, ctypes.byref(pDev))
        if hr < 0 or not pDev:
            out[role_name] = None
            continue
        try:
            out[role_name] = _endpoint_id(ole32, pDev)
        finally:
            _release(pDev)
    return out


def _one_endpoint(ole32, pDevice, flow_name, default_ids, with_peak):
    """Build one detailed dict for an already-opened IMMDevice. Every sub-probe is isolated: a
    failure sets that key to None and the others still fill (the pinned contract's seven keys are
    always present)."""
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default
    eid = _safe(lambda: _endpoint_id(ole32, pDevice))
    name, form_factor = _safe(lambda: _name_and_formfactor(ole32, pDevice), (None, None))
    muted = _safe(lambda: _endpoint_muted(pDevice))
    peak_db = _safe(lambda: _endpoint_peak_db(pDevice)) if with_peak else None
    roles = _safe(lambda: _roles_for(eid, default_ids), [])
    return {
        "id": eid,
        "name": _clean_endpoint_name(name) if name is not None else None,
        "flow": flow_name,
        "form_factor": form_factor,
        "muted": muted,
        "peak_db": peak_db,
        "roles": roles,
    }


def _endpoints_detailed(ole32, pEnum, flow, with_peak):
    """The detailed dicts for one data flow's ACTIVE endpoints. Returns [] on an enumeration failure
    (the whole detailed probe fails soft to [], unlike list_endpoints which distinguishes None)."""
    default_ids = _default_ids(ole32, pEnum, flow)
    flow_name = _FLOW_NAMES[flow]
    pColl = ctypes.c_void_p()
    hr = _method(pEnum, 3, [ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)])(  # EnumAudioEndpoints
        pEnum, flow, _DEVICE_STATE_ACTIVE, ctypes.byref(pColl))
    if hr < 0 or not pColl:
        return []
    try:
        count = ctypes.c_uint()
        hr = _method(pColl, 3, [ctypes.POINTER(ctypes.c_uint)])(pColl, ctypes.byref(count))  # GetCount
        if hr < 0:
            return []
        rows = []
        for i in range(count.value):
            pDev = ctypes.c_void_p()
            hr = _method(pColl, 4, [ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p)])(  # Item
                pColl, i, ctypes.byref(pDev))
            if hr < 0 or not pDev:
                _log_once("an endpoint could not be opened; skipping it")
                continue
            try:
                rows.append(_one_endpoint(ole32, pDev, flow_name, default_ids, with_peak))
            finally:
                _release(pDev)
        return rows
    finally:
        _release(pColl)


def list_endpoints_detailed(flow=None, with_peak=True):
    """One dict per ACTIVE endpoint, live from the MMDevice API. `flow` filters to 'render',
    'capture' or None (both). Each dict has EXACTLY these seven keys (a contract two other builders
    code against):

        id           str    IMMDevice::GetId endpoint ID
        name         str    friendly name, no ' [Loopback]' suffix
        flow         str    'render' or 'capture'
        form_factor  int?   PKEY_AudioEndpoint_FormFactor (9 = HDMI/DP), None if unreadable
        muted        bool?  IAudioEndpointVolume::GetMute, None if unreadable
        peak_db      float? IAudioMeterInformation::GetPeakValue in dBFS (floor -120.0 for silence),
                            None if unreadable or with_peak=False
        roles        list   subset of ['console','multimedia','communications'] this endpoint is the
                            default of, for its flow

    Returns [] on any top-level failure (fails soft, logged once). A per-endpoint sub-failure sets
    just that key to None and the probe carries on. Cheap and re-entrant: safe to call on a 1 s
    timer thread; it inits and uninits COM on the calling thread each time and leaks no COM pointer.
    """
    fint = None if flow is None else _norm_flow(flow)
    if fint is not None and fint not in (_eRender, _eCapture):
        return []
    flows = (_eRender, _eCapture) if fint is None else (fint,)

    def _fn(ole32, pEnum):
        rows = []
        for f in flows:
            rows.extend(_endpoints_detailed(ole32, pEnum, f, with_peak))
        return rows
    try:
        r = _with_enumerator(_fn)
        return r if r is not None else []
    except Exception as e:
        _log_once(f"detailed endpoint probe failed: {e}")
        return []


def _print_table():
    rows = list_endpoints_detailed()
    if not rows:
        print("(no endpoints; detailed probe returned empty)")
        return
    print(f"{'flow':8} {'ff':14} {'mute':5} {'peak':>10}  {'roles':30} name")
    for r in rows:
        ff = r["form_factor"]
        if ff is None:
            ff_s = "-"
        else:
            ff_s = f"{ff} (HDMI/DP)" if ff == _FORMFACTOR_HDMI else str(ff)
        peak = r["peak_db"]
        peak_s = f"{peak:8.1f}dB" if peak is not None else "-"
        if r["muted"] is None:
            mute_s = "?"
        else:
            mute_s = "MUTE" if r["muted"] else "-"
        roles = ",".join(r["roles"]) or "-"
        print(f"{r['flow']:8} {ff_s:14} {mute_s:5} {peak_s:>10}  {roles:30} {r['name']}")


if __name__ == "__main__":
    _print_table()
