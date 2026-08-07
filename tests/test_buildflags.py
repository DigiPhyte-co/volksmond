"""Unit tests for live_transcribe/buildflags.py (the edition switches).

The flags are read from the environment ONCE, at import, because the frozen editions set
them from a PyInstaller runtime hook before any app code runs. So each case runs in a
SUBPROCESS with a controlled environment rather than reloading the module in place: that
exercises the real mechanism (import-time read), and cannot disturb the flags other tests
in this process have already imported.

Run:  python tests/test_buildflags.py   (from the project root; exit 0 = pass)
"""
import os
import subprocess
import sys

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _flags(extra_env):
    """(OFFLINE_ONLY, STORE_BUILD) as a fresh interpreter with `extra_env` sees them."""
    env = {k: v for k, v in os.environ.items() if k not in ("SA_LIVE_OFFLINE", "SA_LIVE_STORE")}
    env.update(extra_env)
    code = ("from live_transcribe import buildflags\n"
            "print(buildflags.OFFLINE_ONLY, buildflags.STORE_BUILD)\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=ROOT, env=env, timeout=300)
    assert out.returncode == 0, f"buildflags failed to import:\n{out.stdout}\n{out.stderr}"
    return out.stdout.split()[-2] == "True", out.stdout.split()[-1] == "True"


def test_default_is_neither_edition():
    # A dev run / the connected build: no env var, both flags off.
    assert _flags({}) == (False, False), "flags should default to False with no edition env var"
    print("  OK  no edition env var -> OFFLINE_ONLY and STORE_BUILD both False")


def test_offline_env_sets_only_offline():
    assert _flags({"SA_LIVE_OFFLINE": "1"}) == (True, False)
    print("  OK  SA_LIVE_OFFLINE=1 -> OFFLINE_ONLY only")


def test_store_env_sets_only_store():
    assert _flags({"SA_LIVE_STORE": "1"}) == (False, True)
    print("  OK  SA_LIVE_STORE=1 -> STORE_BUILD only")


def test_flags_require_exactly_1():
    # The runtime hooks set "1" exactly; any other value must not flip a flag on, or a stray
    # truthy-looking env var could silently reshape an edition.
    assert _flags({"SA_LIVE_OFFLINE": "true", "SA_LIVE_STORE": "yes"}) == (False, False)
    print("  OK  only the literal '1' flips a flag")


if __name__ == "__main__":
    failures = 0
    for fn in (test_default_is_neither_edition,
               test_offline_env_sets_only_offline,
               test_store_env_sets_only_store,
               test_flags_require_exactly_1):
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    if failures:
        print(f"\n{failures} test(s) FAILED")
        sys.exit(1)
    print("\nAll buildflags tests passed.")
