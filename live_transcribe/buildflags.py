"""Build-profile flags, decided at freeze time by the PyInstaller target.

Volksmond ships three builds from one codebase (docs/distribution-and-landing-plan.md
section 3): the default CONNECTED build (local-first, with the optional online
features and the manual update check), an OFFLINE-ONLY build that compiles OUT
every network path (the app update check, the Outlook calendar, any cloud module) so
it provably cannot phone home, and a STORE build (Microsoft Store MSIX) that is the
connected build minus the in-app update check, because the Store owns updates and a
Store app that fetches its own update manifest is at best noise and at worst a
policy problem.

Each frozen edition sets its flag the same way (see sa-live-transcribe.spec): the
offline target, driven by VOLKSMOND_OFFLINE at build time, excludes the online-only
modules from the bundle and installs a runtime hook that sets SA_LIVE_OFFLINE=1
before any app code runs; the store target, driven by VOLKSMOND_STORE, excludes only
the updatecheck module and installs a hook that sets SA_LIVE_STORE=1. In source, in
a normal dev run, and in the connected build both flags stay False.
"""
import os

OFFLINE_ONLY = os.environ.get("SA_LIVE_OFFLINE") == "1"
STORE_BUILD = os.environ.get("SA_LIVE_STORE") == "1"
