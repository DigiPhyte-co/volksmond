# PyInstaller runtime hook for the OFFLINE-ONLY build (see sa-live-transcribe.spec, only that
# target includes this hook). Runtime hooks run before any application code, so by the time
# live_transcribe.buildflags is first imported, SA_LIVE_OFFLINE is already set and OFFLINE_ONLY is
# True. Set unconditionally (not setdefault): the offline edition HARD-forces itself offline, so it
# cannot be talked out of it by an environment variable. The store flag is cleared first for the
# same reason: an inherited SA_LIVE_STORE=1 would otherwise flip this build into a mixed edition no
# spec ever produces (the editions are mutually exclusive at build time, and must stay so at run
# time).
import os

os.environ.pop("SA_LIVE_STORE", None)
os.environ["SA_LIVE_OFFLINE"] = "1"
