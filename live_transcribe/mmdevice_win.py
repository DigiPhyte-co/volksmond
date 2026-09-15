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
_VT_LPWSTR = 31

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


def _default_endpoint_name(flow):
    def _fn(ole32, pEnum):
        pDev = ctypes.c_void_p()
        hr = _method(pEnum, 4, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)])(  # GetDefaultAudioEndpoint
            pEnum, flow, _eMultimedia, ctypes.byref(pDev))
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


def default_render_friendly_name():
    """Friendly name of the current default RENDER (output) endpoint, or None. The default loopback
    name is this plus ' [Loopback]'. Always live (unlike a PyAudio probe during capture)."""
    return _default_endpoint_name(_eRender)


def default_capture_friendly_name():
    """Friendly name of the current default CAPTURE (input) endpoint, or None."""
    return _default_endpoint_name(_eCapture)


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
