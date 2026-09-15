"""One-shot NVIDIA GPU usage snapshot for the struggle diagnostic.

When the engine reports it is struggling (or drops chunks), we want ONE log line naming what
else is on the NVIDIA card, so the next "it fell behind on my 3090" report can be root-caused
from the log instead of guessing after the fact. That is all this module does: it shells out to
nvidia-smi, reads the utilisation, the memory split and the compute apps, and returns one compact
line. Any failure returns None, and the caller never blocks on it (it runs on a daemon thread).

On Windows it also reads the WDDM adapter memory counters (via typeperf) and appends the dedicated
and shared VRAM totals. That is the one reading nvidia-smi cannot give and the failure we most
suspect: under WDDM, when dedicated VRAM overflows the driver silently pages into system memory
("Shared GPU memory") and kernels crawl 10 to 50x WITHOUT the card ever looking busy, so a
utilisation number alone would read as healthy while transcription falls minutes behind. A non-zero
shared figure in the log is the tell.

Deliberate limits:
  - Windows and Linux only. On macOS (the MLX/Metal backend) nvidia-smi does not exist and must
    never be called; snapshot() returns None there before touching subprocess.
  - nvidia-smi may be absent on customer machines (CPU laptops) or the card may be AMD/Intel. A
    missing binary is not an error, it is None.
  - A hard 2 s timeout per external call, so a wedged driver cannot stall the caller's thread.
  - CREATE_NO_WINDOW on Windows: a frozen windowed build must not flash a console window.
  - Rate limited to at most one real snapshot per 60 s. A drop burst of dozens of chunks funnels
    through the same delivery point; the first call runs nvidia-smi, the rest get the cached line
    with a "(cached)" suffix, so a burst costs one nvidia-smi call, not dozens.
  - Process basenames only, never full paths or arguments (a path can carry a username). Under WDDM
    nvidia-smi reports per-process memory as "[N/A]"; that figure is then omitted, not printed.
  - The typeperf VRAM read is Windows-only and fail-soft: if it fails, that part is left off the
    line, and it NEVER fails the whole snapshot.
  - Never prints transcript text (it prints no transcript at all: it reads the GPU, nothing else).
"""

import csv
import os
import shutil
import subprocess
import sys
import threading
import time

# Per-call wall-clock ceiling. A healthy nvidia-smi returns in tens of milliseconds; 2 s is a
# generous bound that still guarantees a wedged driver cannot hold the daemon thread for long.
_TIMEOUT_SECONDS = 2.0

# At most one real snapshot per this many seconds; within the window the cached line is returned
# with a "(cached)" suffix. One delivered struggle notice per session means this is belt-and-braces
# rather than load-bearing, but a forced-drop burst can call in fast and must cost one smi call.
_CACHE_SECONDS = 60.0

# CREATE_NO_WINDOW (0x08000000) suppresses the console window a windowed (frozen) build would
# otherwise flash when spawning a child process. Windows only; 0 elsewhere so the kwarg is inert.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# Usual Windows locations for nvidia-smi when it is not on PATH. The driver drops it in System32;
# older kits kept it under Program Files. Checked in order, first hit wins.
_WINDOWS_FALLBACKS = (
    os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe"),
    r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
)

# Guarded cache. Touched only from short-lived daemon threads, but locked anyway: cheap, and it
# keeps a concurrent burst from racing the timestamp.
_lock = threading.Lock()
_cache = {"line": None, "ts": 0.0}


def _find_nvidia_smi():
    """Absolute path to nvidia-smi, or None. PATH first (covers Linux and a normal Windows install),
    then the usual Windows fallbacks. A missing binary is the common, expected case on a CPU laptop
    or an AMD/Intel machine, and is not an error."""
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if sys.platform == "win32":
        for candidate in _WINDOWS_FALLBACKS:
            if os.path.isfile(candidate):
                return candidate
    return None


def _run(argv):
    """Run one external command and return its stdout, or None on any failure (non-zero exit, a
    timeout, a missing binary, an OS error). CREATE_NO_WINDOW on Windows so a frozen build spawns
    no visible console. Shared by the nvidia-smi queries and the Windows typeperf read."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        # subprocess.TimeoutExpired, OSError (binary vanished, permissions), anything: all None.
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _run_smi(exe, query_flag):
    """Run one nvidia-smi query and return its stdout, or None on any failure."""
    return _run([exe, query_flag, "--format=csv,noheader,nounits"])


def _format_gpu(gpu_out):
    """Format the utilisation/memory query into the leading part of the line, or None if it did not
    parse. `gpu_out` is one CSV row per GPU: `utilization.gpu, memory.used, memory.total`."""
    parts = []
    rows = [row.strip() for row in gpu_out.splitlines() if row.strip()]
    if not rows:
        return None
    for idx, row in enumerate(rows):
        fields = [f.strip() for f in row.split(",")]
        if len(fields) < 3:
            return None
        util, used, total = fields[0], fields[1], fields[2]
        label = "gpu" if len(rows) == 1 else f"gpu{idx}"
        parts.append(f"{label} util={util}% mem={used}/{total} MB")
    return " ".join(parts)


def _format_apps(apps_out):
    """Format the compute-apps query into the `apps: ...` tail. `apps_out` is one CSV row per app:
    `pid, process_name, used_memory`. process_name is reduced to its basename (no path, no args).
    An empty result is a valid state (nothing else on the card), rendered as `apps: none`.

    Privacy: a Windows user profile path can contain a comma (e.g. "C:\\Users\\Example, Person\\..."),
    so a naive comma split would shift a directory fragment into the memory field and leak that
    directory name into the log. Each row is parsed with the csv module (nvidia-smi quotes a field
    that contains a comma) AND reconstructed positionally as a fallback: the FIRST field is the pid,
    the LAST is the memory, and everything between is the process name rejoined. pid must be an
    integer and memory an integer or an N/A marker, or the row is dropped; the name is basenamed on
    BOTH separators BEFORE it can reach the log, so only the executable name ever appears."""
    entries = []
    for raw in apps_out.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            # skipinitialspace: nvidia-smi writes ", " between fields, so the quote that guards a
            # comma-bearing path does not sit flush against the delimiter; without this the csv
            # dialect treats that quote as a literal and a stray quote clings to the basename.
            fields = next(csv.reader([raw], skipinitialspace=True))
        except Exception:
            fields = raw.split(",")
        fields = [f.strip() for f in fields]
        if len(fields) < 3:
            continue
        pid, mem = fields[0], fields[-1]
        name = ",".join(fields[1:-1])   # rejoin the middle: a comma in the path cannot reach `mem`
        if not pid.isdigit():
            continue                    # a malformed row never reaches the log
        # basename on both separators, then strip any residual quote (invalid in a Windows filename
        # anyway), so only the bare executable name can ever reach the log.
        base = (os.path.basename(name.replace("\\", "/")) or name).strip('"')
        # Under WDDM nvidia-smi cannot see per-process VRAM and reports "[N/A]"; omit it then (and on
        # any non-integer memory) rather than print a useless value. The adapter dedicated/shared
        # totals cover memory instead.
        if mem.isdigit():
            entries.append(f"{base}(pid {pid}, {mem} MB)")
        else:
            entries.append(f"{base}(pid {pid})")
    if not entries:
        return "apps: none"
    return "apps: " + ", ".join(entries)


def _normalise_number(value):
    """typeperf formats its decimals with the machine's locale separator, so on an af-ZA or de-DE
    box a byte count reads "24956108800,000000" (comma decimal) and a plain float() would raise,
    silently losing the VRAM diagnostic exactly on SA machines. Normalise to a dot-decimal string
    without touching process-global locale: a value with BOTH separators treats the LAST one as the
    decimal point and drops the other (thousands); a value with only a comma treats it as the
    decimal point. A dot-only or separator-free value is returned unchanged."""
    value = value.strip().strip('"')
    has_dot, has_comma = "." in value, "," in value
    if has_dot and has_comma:
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")   # comma is the decimal
        else:
            value = value.replace(",", "")                     # dot is the decimal
    elif has_comma:
        value = value.replace(",", ".")
    return value


def _parse_typeperf(out):
    """Sum the WDDM adapter memory counters from one typeperf sample and return (dedicated_mb,
    shared_mb), or None if it did not parse. typeperf prints a quoted CSV header row (counter paths,
    the first field a "(PDH-CSV ...)" timestamp label) then one data row of values in BYTES; the
    wildcard instance expands to one column per adapter, which are summed per counter."""
    rows = [r for r in out.splitlines() if r.strip()]
    header = data = None
    for i, row in enumerate(rows):
        if "PDH-CSV" in row and i + 1 < len(rows):
            header = next(csv.reader([row]))
            data = next(csv.reader([rows[i + 1]]))
            break
    if not header or not data or len(header) != len(data):
        return None
    dedicated = shared = 0.0
    matched = False
    for name, value in zip(header, data):
        low = name.lower()
        try:
            num = float(_normalise_number(value))
        except ValueError:
            continue
        if "dedicated usage" in low:
            dedicated += num
            matched = True
        elif "shared usage" in low:
            shared += num
            matched = True
    if not matched:
        return None
    return int(dedicated // (1024 * 1024)), int(shared // (1024 * 1024))


def _windows_gpu_memory():
    """`dedicated=<MB> shared=<MB>` tail from the WDDM adapter memory counters, or None. Windows
    only and fail-soft: any failure returns None and the caller leaves the tail off the line. A
    non-zero `shared` is the VRAM-oversubscription tell that nvidia-smi utilisation cannot show."""
    if sys.platform != "win32":
        return None
    out = _run([
        "typeperf",
        r"\GPU Adapter Memory(*)\Dedicated Usage",
        r"\GPU Adapter Memory(*)\Shared Usage",
        "-sc", "1",
    ])
    if out is None:
        return None
    parsed = _parse_typeperf(out)
    if parsed is None:
        return None
    dedicated, shared = parsed
    return f"dedicated={dedicated} MB shared={shared} MB"


def _build_line(exe):
    """Run both queries and assemble the line, or None if the utilisation query did not come back."""
    gpu_out = _run_smi(exe, "--query-gpu=utilization.gpu,memory.used,memory.total")
    if gpu_out is None:
        return None
    gpu_part = _format_gpu(gpu_out)
    if gpu_part is None:
        return None
    apps_out = _run_smi(exe, "--query-compute-apps=pid,process_name,used_memory")
    if apps_out is None:
        return None
    line = f"{gpu_part} {_format_apps(apps_out)}"
    # Windows-only, fail-soft: append the WDDM dedicated/shared VRAM totals when they read, leave
    # them off otherwise. Never let this part fail the whole snapshot.
    mem_tail = _windows_gpu_memory()
    if mem_tail:
        line = f"{line} {mem_tail}"
    return line


def snapshot():
    """Return one compact line describing NVIDIA GPU usage, or None.

    Example (Windows, WDDM: per-process memory reads as N/A so it is omitted, adapter totals appended):
        gpu util=97% mem=21504/24576 MB apps: Volksmond.exe(pid 1234), ollama.exe(pid 9) dedicated=23800 MB shared=4200 MB

    None when: the platform is not Windows or Linux; nvidia-smi is not found; or any nvidia-smi call
    fails or times out. Rate limited to one real snapshot per 60 s; a call inside that window returns
    the previous line with a " (cached)" suffix (and None if the previous call returned None)."""
    if sys.platform not in ("win32", "linux"):
        # macOS (MLX/Metal) and anything else: never touch nvidia-smi.
        return None

    # Lookup, probe and publish under ONE acquisition, so two diagnostic threads arriving together
    # cannot both probe: the second blocks until the first has published, then reads the cache. Only
    # short-lived diagnostic threads call this, so holding the lock across the (2 s bounded) probe is
    # fine, and it is what keeps a 45-chunk drop burst to a single nvidia-smi call.
    with _lock:
        now = time.monotonic()
        if _cache["ts"] and (now - _cache["ts"]) < _CACHE_SECONDS:
            cached = _cache["line"]
            return f"{cached} (cached)" if cached is not None else None
        exe = _find_nvidia_smi()
        line = _build_line(exe) if exe else None
        _cache["line"] = line
        _cache["ts"] = time.monotonic()
    return line
