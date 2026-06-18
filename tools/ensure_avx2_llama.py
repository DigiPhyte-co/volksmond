"""Build guard: the bundled llama.cpp must contain NO AVX-512.

Why: abetlen's llama-cpp-python 0.3.23 "cpu" wheel is compiled WITH AVX-512, so the summary
engine executes an illegal instruction and crashes on AVX2-only CPUs (e.g. an Intel i7-9750H).
The 0.3.22 "cpu" wheel is AVX2-safe (0 AVX-512 instructions) and still supports Gemma 4.

A WHEEL-tag check is NOT enough: both wheels are tagged `py3-none-win_amd64`. So this guard
pins 0.3.22 and then VERIFIES by disassembling ggml-cpu.dll and counting AVX-512 instructions.
Exit non-zero to fail the build rather than ship a binary that crashes on most laptops.

Run from build-app.ps1 with the venv python.
"""
import os
import subprocess
import sys

PIN = "0.3.22"
INDEX = "https://abetlen.github.io/llama-cpp-python/whl/cpu"


def _pip(*args):
    subprocess.run([sys.executable, "-m", "pip", *args], check=True)


def _ensure_scan_tools():
    try:
        import pefile, capstone  # noqa: F401
    except ImportError:
        _pip("install", "-q", "pefile", "capstone")


def _dll_path():
    import llama_cpp
    return os.path.join(os.path.dirname(llama_cpp.__file__), "lib", "ggml-cpu.dll")


def _avx512_count(path):
    import pefile
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
    from capstone.x86 import X86_GRP_AVX512
    pe = pefile.PE(path, fast_load=True)
    code = base = None
    for s in pe.sections:
        if s.Name.rstrip(b"\x00") == b".text":
            code = s.get_data()
            base = pe.OPTIONAL_HEADER.ImageBase + s.VirtualAddress
            break
    if code is None:
        raise RuntimeError("no .text section in %s" % path)
    cs = Cs(CS_ARCH_X86, CS_MODE_64)
    cs.detail = True
    return sum(1 for ins in cs.disasm(code, base) if X86_GRP_AVX512 in ins.groups)


def _pin_avx2():
    _pip("install", "--force-reinstall", "--no-deps", "--only-binary=:all:",
         "--extra-index-url", INDEX, "llama-cpp-python==%s" % PIN)


def main():
    import importlib.metadata as md
    _ensure_scan_tools()
    try:
        cur = md.version("llama-cpp-python")
    except Exception:
        cur = None
    if cur != PIN:
        print("  llama-cpp-python is %s; pinning AVX2-safe %s" % (cur, PIN))
        _pin_avx2()

    n = _avx512_count(_dll_path())
    print("  ggml-cpu.dll AVX-512 instructions:", n)
    if n != 0:
        print("  contains AVX-512; reinstalling %s and re-checking" % PIN)
        _pin_avx2()
        n = _avx512_count(_dll_path())
        print("  after reinstall, AVX-512 instructions:", n)
    if n != 0:
        print("  FAIL: could not get an AVX-512-free llama.cpp; aborting build.")
        return 1
    print("  OK: llama-cpp-python %s, AVX2-safe (no AVX-512)." % md.version("llama-cpp-python"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
