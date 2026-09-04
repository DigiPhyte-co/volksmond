r"""Diagnosability: a log that survives launches, a launch header, and a support bundle.

Why this module exists. The shipped build is windowed, so the only record of a run is
<data_dir>\volksmond.log, and that file used to be truncated on every launch. A user who
hits "the model did not load", closes the app and reopens it to try again has, by the time
they write to us, destroyed the only evidence. Two support cases were undiagnosable for
exactly that reason; we could not even establish which CPU the machine had. So:

  1. rotate_for_launch() keeps the current run plus the previous five (volksmond.log,
     volksmond.log.1 .. .5), each capped, so the whole set stays small and bounded.
  2. RotatingLog flushes every write, so a hard crash (there are real crash dumps in
     %LOCALAPPDATA%\CrashDumps) still leaves the last lines on disk.
  3. header_text() records what the machine actually is, once per launch.
  4. save_bundle() zips exactly those artefacts for the user to attach to an email.

POPIA. Transcripts, notes, recordings and the licence token are personal or secret and are
NEVER collected here. The bundle is a fixed, allow-listed file set: logs, the system header, a
model inventory (names and sizes, no contents), and settings.json reduced to an ALLOW-LIST of
operational keys, so what the user typed into the app (default_context, ai_instructions) is
"<omitted>" and a secret-shaped key is "<redacted>". Every text member is run through redact()
so the user's profile path does not travel with it. Nothing is ever uploaded: the file is written to the user's own Downloads
folder and the user chooses whether to send it.

Deliberately stdlib-only and import-light, the same contract paths.py keeps: app_main.py
imports this BEFORE the rest of the package, to place the log. In particular nothing here
imports ctranslate2, faster_whisper or huggingface_hub at module import time, and the GPU
probe deliberately uses nvidia-smi (via cudadl) rather than ctranslate2: transcribe.py must
be the first thing to import ctranslate2, after cudadl.register_dll_dir() has put the
downloaded CUDA folder on the DLL search path.
"""
import json
import os
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from .paths import data_dir

LOG_NAME = "volksmond.log"
# Six files at 5 MB is a 30 MB worst case, which is small next to a multi-GB model cache
# and large enough that a chatty run does not lose its own beginning.
MAX_LOG_BYTES = 5 * 1024 * 1024
KEEP = 5


# --- the log file ----------------------------------------------------------

def log_path(base=None) -> Path:
    return (Path(base) if base is not None else data_dir()) / LOG_NAME


def rotate(path, keep: int = KEEP) -> None:
    """Shift <path> to <path>.1, .1 to .2 and so on, dropping anything past `keep`.

    Best-effort by design: a locked or vanished file must never stop the app from
    starting, so every step swallows its own OSError. If the current log cannot be
    renamed (another instance holds it open) the caller simply appends to it.
    """
    path = Path(path)
    for i in range(keep, 0, -1):
        src = path if i == 1 else path.with_name(f"{path.name}.{i - 1}")
        dst = path.with_name(f"{path.name}.{i}")
        try:
            if src.exists():
                os.replace(src, dst)      # atomic, overwrites dst on Windows too
        except OSError:
            pass
    try:
        stale = path.with_name(f"{path.name}.{keep + 1}")
        if stale.exists():
            stale.unlink()                # left by an older, larger keep setting
    except OSError:
        pass


class RotatingLog:
    """A text sink for sys.stdout/sys.stderr that rotates instead of growing.

    Flushes on every write: without that, a hard crash loses precisely the lines that
    explain it. Unknown attributes delegate to the underlying file object, so code that
    reaches for stream.fileno() / .encoding / .buffer keeps working exactly as it did
    when this was a plain open() handle.
    """

    def __init__(self, path, max_bytes: int = MAX_LOG_BYTES, keep: int = KEEP):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.keep = keep
        self._f = None
        self._size = 0
        self._open()

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "a", encoding="utf-8", errors="replace", buffering=1)
        try:
            self._size = self.path.stat().st_size
        except OSError:
            self._size = 0

    def _roll(self):
        try:
            self._f.close()
        except Exception:
            pass
        rotate(self.path, self.keep)
        self._open()

    def write(self, s):
        if not isinstance(s, str):
            s = str(s)
        if self._f is None or self._f.closed:
            return 0
        n = self._f.write(s)
        try:
            self._f.flush()
        except Exception:
            pass
        self._size += len(s.encode("utf-8", "replace"))
        if self._size >= self.max_bytes:
            self._roll()
        return n

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        try:
            if self._f is not None and not self._f.closed:
                self._f.flush()
        except Exception:
            pass

    def close(self):
        try:
            if self._f is not None:
                self._f.close()
        except Exception:
            pass

    def isatty(self):
        return False

    def __getattr__(self, name):
        f = self.__dict__.get("_f")
        if f is None:
            raise AttributeError(name)
        return getattr(f, name)


def open_log(base=None, max_bytes: int = MAX_LOG_BYTES, keep: int = KEEP) -> RotatingLog:
    """Rotate the previous launch out of the way, then open this launch's log."""
    p = log_path(base)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    rotate(p, keep)
    return RotatingLog(p, max_bytes=max_bytes, keep=keep)


def log_files(base=None):
    """Existing log files, current first: volksmond.log, .1 .. .KEEP."""
    p = log_path(base)
    out = []
    for cand in [p] + [p.with_name(f"{p.name}.{i}") for i in range(1, KEEP + 1)]:
        try:
            if cand.is_file():
                out.append(cand)
        except OSError:
            pass
    return out


# --- what this machine is --------------------------------------------------

def install_kind() -> str:
    """How this copy was installed: store, direct, offline or source.

    Primary evidence is the edition flag the frozen build's PyInstaller runtime hook
    sets before any app code runs (buildflags). The WindowsApps path check is the
    independent corroboration: an MSIX package always runs from under WindowsApps.
    """
    try:
        from . import buildflags
        if buildflags.STORE_BUILD:
            return "store"
        if buildflags.OFFLINE_ONLY:
            return "offline"
    except Exception:
        pass
    if not getattr(sys, "frozen", False):
        return "source"
    return "store" if _under_windows_apps(sys.executable) else "direct"


def _under_windows_apps(exe) -> bool:
    return "\\windowsapps\\" in (str(exe or "").replace("/", "\\").lower())


def _cpu_name() -> str:
    """The marketing CPU name, the fact a support case needs first and platform
    .processor() does not give on Windows (it returns the family/model string)."""
    try:
        if sys.platform == "win32":
            import winreg
            key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        if sys.platform == "darwin":
            import subprocess
            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                 capture_output=True, text=True, timeout=5)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        else:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or platform.machine() or "unknown"


def _total_ram_mb():
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullTotalPhys // (1024 * 1024))
        elif sys.platform == "darwin":
            import subprocess
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True, timeout=5)
            if out.returncode == 0:
                return int(out.stdout.strip()) // (1024 * 1024)
        else:
            with open("/proc/meminfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // 1024
    except Exception:
        pass
    return None


def _app_version() -> str:
    try:
        from . import licensing
        return licensing.APP_VERSION
    except Exception:
        return "unknown"


def _pkg_version(name) -> str:
    """A dependency's version from its installed metadata, WITHOUT importing it.
    ctranslate2 in particular must not be imported before transcribe.py registers
    the CUDA DLL folder, so metadata is the only safe way to ask at launch."""
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return "unknown"


def _ram_text() -> str:
    mb = _total_ram_mb()
    return f"{mb / 1024:.1f} GB" if mb else "unknown"


def system_lines():
    """The cheap facts, safe to gather synchronously on the launch path.

    No subprocess, no heavy import: a registry read, a ctypes call and stdlib
    strings. Nothing here is secret; the licence token is never read.
    """
    return [
        f"app: Volksmond {_app_version()} (install: {install_kind()}, frozen: "
        f"{'yes' if getattr(sys, 'frozen', False) else 'no'})",
        f"os: {platform.platform()}",
        f"cpu: {_cpu_name()} ({os.cpu_count() or '?'} logical cores)",
        f"ram: {_ram_text()}",
        f"python: {platform.python_version()}  ct2: {_pkg_version('ctranslate2')}",
    ]


_HW = {}


def gpu_note() -> str:
    """One line on the GPU. nvidia-smi only: importing ctranslate2 here would beat
    transcribe.py to it and rob cudadl.register_dll_dir() of its chance to put the
    downloaded CUDA libraries on the DLL search path first."""
    try:
        from . import cudadl
        if cudadl.SUPPORTED:
            name = cudadl.gpu_name()
            if not name:
                return "none detected (no nvidia-smi)"
            vram = cudadl.vram_mb()
            pack = "installed" if cudadl.installed() else "not installed"
            return f"{name}" + (f", {vram} MB VRAM" if vram else "") + f", CUDA runtime pack: {pack}"
    except Exception as e:
        return f"probe failed: {e!r}"
    try:
        from . import accel
        if accel.mlx_supported():
            return "Apple Metal (MLX runtime " + ("ready" if accel.mlx_ready() else "not ready") + ")"
    except Exception:
        pass
    return "none"


def hardware_lines():
    """The slower probes (a subprocess), cached. Kept off the synchronous launch path."""
    if "lines" not in _HW:
        _HW["lines"] = [f"gpu: {gpu_note()}"]
    return list(_HW["lines"])


def header_text(with_hardware: bool = True) -> str:
    """The full launch header. Same text in the log and in the diagnostics bundle."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"=== Volksmond launch {stamp} ==="] + system_lines()
    if with_hardware:
        lines += hardware_lines()
    return "\n".join(lines)


def summary_line() -> str:
    """One short line for the feedback email body: the facts a first reply needs."""
    return (f"{_cpu_name()} | {os.cpu_count() or '?'} cores | {_ram_text()} RAM | "
            f"GPU: {gpu_note()} | install: {install_kind()}")


# --- redaction -------------------------------------------------------------

def redact(text: str) -> str:
    """Replace the user's profile path with <user> in any text leaving the machine.

    Windows paths appear with either slash, and case varies (C:\\Users vs c:\\users), and a path
    that has been through json.dumps carries DOUBLED backslashes (C:\\\\Users\\\\name). All three
    forms are matched, case-insensitively; the longest form goes first so a partial match can
    never eat the start of a longer one.
    """
    if not text:
        return text
    try:
        home = str(Path.home())
    except Exception:
        return text
    forms = {home, home.replace("\\", "/"), home.replace("\\", "\\\\")}
    for form in sorted((f for f in forms if f), key=len, reverse=True):
        text = re.sub(re.escape(form), "<user>", text, flags=re.IGNORECASE)
    return text


# --- the bundle ------------------------------------------------------------

# Settings keys whose VALUE must never be collected. cloud_api_key is the encrypted
# provider key blob; the rest are defensive, so a future secret-bearing key is redacted
# by shape rather than by somebody remembering to add it here.
_SECRET_KEY_HINTS = ("key", "secret", "token", "password", "licence", "license")

# The settings the bundle may carry: an explicit ALLOW-list, because the deny-list it replaced
# only masked secret-SHAPED keys and shipped everything else verbatim. settings.json also holds
# content the user typed - default_context (names, jargon, who is in the room) and
# ai_instructions (custom prompts) - and the UI promises "No transcripts, no notes". A deny-list
# gets that wrong by default every time a content-bearing key is added; an allow-list gets it
# right by default and only ever fails closed.
#
# Derived by classifying config.DEFAULTS: everything here is a knob, a choice or a machine fact
# that a support case needs (which model, which device, which language, which switches, which
# folder) and none of it is anything the user wrote. Every other key, known or unknown, is
# exported as "<omitted>". Paths still go through redact() on the way out.
_SETTINGS_ALLOW = frozenset({
    # what to transcribe, and with what
    "interface_language", "transcription_language", "transcribe_languages",
    "tier", "device", "engine", "summary_device", "summary_model", "ai_backend",
    # audio switches (the first questions a "it heard nothing" case asks)
    "aec", "aec_live", "agc_live", "mic_gate", "record_sessions", "recording_format",
    # where files go, and what is installed here
    "save_location", "save_location_migrated", "installed_models",
    # notifications and nudges
    "os_toasts", "silence_nudge", "silence_nudge_minutes", "struggle_nudge",
    "summary_footer", "calendar_reminders",
    # first-run / UI state markers, all booleans, counters or a catalogue id
    "setup_complete", "session_count", "business_nudge_seen",
    "active_instruction_id", "live_notes_width",
})


def _redacted_settings():
    """(json text, sorted names of the keys omitted by policy) for the bundle.

    Secret-shaped keys read "<redacted>" as before; everything outside _SETTINGS_ALLOW reads
    "<omitted>" and its name is reported so system.txt can say what was left out. The key NAMES
    stay in the file on purpose: knowing that default_context was set (and not what it said) is
    often the whole answer to a support question.
    """
    try:
        raw = json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "{}", []
    omitted = []
    if isinstance(raw, dict):
        for k in list(raw):
            low = k.lower()
            if any(h in low for h in _SECRET_KEY_HINTS):
                raw[k] = "<redacted>"
            elif k not in _SETTINGS_ALLOW:
                raw[k] = "<omitted>"
                omitted.append(k)
    return redact(json.dumps(raw, ensure_ascii=False, indent=2)), sorted(omitted)


# Log lines that used to carry the TEXT of a rejected segment. transcribe._logtxt fixed that at
# the source (a length, unless SA_LIVE_LOG_TEXT=1); this is the belt to that pair of braces, so a
# log written by an older build, or by a support session with the flag on, still cannot put
# meeting content in a bundle. Deliberately narrow: only these four line shapes are touched.
_TEXT_LOG_PREFIXES = ("[engine] prompt-leak dropped", "[engine] echo-veto dropped",
                      "[engine] xchan-echo dropped", "[engine] loop-guard suppressed")
_LOG_ANCHOR = re.compile(r"^(.*?@ -?\d+(?:\.\d+)?s)(.*)$")
_LOG_SAFE_TAIL = re.compile(r"^<\d+ chars>$")     # what _logtxt writes now


def _sanitise_log_line(line: str) -> str:
    """Strip the segment-text payload off one of the known engine drop lines.

    Everything up to the "@ <time>s" anchor is kept, then each following [bracketed] group or
    key=value field, because those are numbers. The first token that is neither begins the text,
    and the rest of the line becomes <text omitted> unless it is already _logtxt's length form.
    """
    body = line.rstrip("\n")
    if not body.lstrip().startswith(_TEXT_LOG_PREFIXES):
        return line
    m = _LOG_ANCHOR.match(body)
    if not m:
        return line
    kept, rest = [m.group(1)], m.group(2).strip()
    while rest:
        if rest.startswith("["):
            end = rest.find("]")
            if end < 0:
                break
            kept.append(rest[:end + 1])
            rest = rest[end + 1:].lstrip()
            continue
        tok, _, remainder = rest.partition(" ")
        if "=" not in tok:
            break
        kept.append(tok)
        rest = remainder.lstrip()
    if rest and not _LOG_SAFE_TAIL.match(rest):
        rest = "<text omitted>"
    out = " ".join(kept + ([rest] if rest else []))
    return out + "\n" if line.endswith("\n") else out


def _sanitise_log(text: str) -> str:
    return "".join(_sanitise_log_line(ln) for ln in text.splitlines(keepends=True))


def _hub_cache() -> Path:
    """The HuggingFace cache holding the voice models. Same resolution voicedl uses,
    reimplemented on stdlib so the bundle never drags the model stack into memory."""
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        return Path(HF_HUB_CACHE)
    except Exception:
        base = os.environ.get("HF_HOME") or os.path.join(str(Path.home()), ".cache", "huggingface")
        return Path(base) / "hub"


def _dir_size(p) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except OSError:
        pass
    return total


def models_text() -> str:
    """Names and sizes of the installed models. Never any file content: this answers
    'which model was it trying to load, and is it fully there', nothing more."""
    lines = []
    hub = _hub_cache()
    lines.append(f"voice model cache: {redact(str(hub))}")
    try:
        entries = sorted(d for d in os.listdir(hub) if d.startswith("models--"))
    except OSError:
        entries = []
    if not entries:
        lines.append("  (none)")
    for d in entries:
        lines.append(f"  {d}  {_dir_size(hub / d) / (1024 * 1024):.1f} MB")
    try:
        from . import config
        sm = config.models_dir()
    except Exception:
        sm = data_dir() / "models"
    lines.append(f"summary model folder: {redact(str(sm))}")
    try:
        files = sorted(f for f in os.listdir(sm) if f.lower().endswith(".gguf"))
    except OSError:
        files = []
    if not files:
        lines.append("  (none)")
    for f in files:
        try:
            mb = os.path.getsize(sm / f) / (1024 * 1024)
        except OSError:
            mb = 0.0
        lines.append(f"  {f}  {mb:.1f} MB")
    return "\n".join(lines) + "\n"


def default_bundle_dir() -> Path:
    """Downloads, else Desktop, else the app data folder. Downloads is where a user
    looks for something to attach to an email."""
    try:
        home = Path.home()
    except Exception:
        return data_dir()
    for cand in (home / "Downloads", home / "Desktop"):
        try:
            if cand.is_dir():
                return cand
        except OSError:
            pass
    return data_dir()


def bundle_name(now=None) -> str:
    return "volksmond-diagnostics-" + (now or datetime.now()).strftime("%Y%m%d-%H%M") + ".zip"


def save_bundle(dest_dir=None, base=None) -> Path:
    r"""Write the support bundle and return its path.

    Exactly four kinds of member, nothing else, ever:
      system.txt          the same header the log gets at launch, plus the omission note
      settings.json       the allow-listed operational settings only; secret-bearing keys read
                          <redacted> and everything the user typed reads <omitted>
      models.txt          installed model names and sizes, no contents
      logs/volksmond.log* this launch and the previous five

    Transcripts, notes and recordings live in the sessions folder and are never read
    by this function. Every member is text, every member is passed through redact()
    first, and the log copies also go through _sanitise_log().
    """
    dest = Path(dest_dir) if dest_dir is not None else default_bundle_dir()
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / bundle_name()
    settings_text, omitted = _redacted_settings()
    note = ("settings omitted by policy (content the user typed): "
            + (", ".join(omitted) if omitted else "none") + "\n")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.txt", redact(header_text()) + "\n" + redact(note))
        z.writestr("settings.json", settings_text)
        z.writestr("models.txt", models_text())
        for lf in log_files(base):
            try:
                text = lf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            z.writestr("logs/" + lf.name, redact(_sanitise_log(text)))
    return out
