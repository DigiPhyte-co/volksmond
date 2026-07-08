# PyInstaller runtime hook for the OFFLINE-ONLY build (see sa-live-transcribe.spec, only that
# target includes this hook). Runtime hooks run before any application code, so by the time
# live_transcribe.buildflags is first imported, SA_LIVE_OFFLINE is already set and OFFLINE_ONLY is
# True. Set unconditionally (not setdefault): the offline edition HARD-forces itself offline, so it
# cannot be talked out of it by an environment variable.
import os

os.environ["SA_LIVE_OFFLINE"] = "1"
