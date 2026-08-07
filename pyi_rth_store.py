# PyInstaller runtime hook for the STORE (Microsoft Store MSIX) build (see
# sa-live-transcribe.spec, only that target includes this hook). Runtime hooks run before any
# application code, so by the time live_transcribe.buildflags is first imported, SA_LIVE_STORE is
# already set and STORE_BUILD is True. Set unconditionally (not setdefault): the Store edition
# HARD-declares itself a Store build (the Store owns updates, so the in-app update check must stay
# off), so it cannot be talked out of it by an environment variable.
import os

os.environ["SA_LIVE_STORE"] = "1"
