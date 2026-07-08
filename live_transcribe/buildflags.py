"""Build-profile flag, decided at freeze time by the PyInstaller target.

Volksmond ships two builds from one codebase (docs/distribution-and-landing-plan.md
section 3): the default CONNECTED build (local-first, with the optional online
features and the manual update check), and an OFFLINE-ONLY build that compiles OUT
every network path (the app update check, the Outlook calendar, any cloud module) so
it provably cannot phone home.

The offline PyInstaller target does two things (see sa-live-transcribe.spec, driven by
the VOLKSMOND_OFFLINE env var at build time): it excludes the online-only modules from
the bundle, and it installs a runtime hook that sets SA_LIVE_OFFLINE=1 before any app
code runs, which flips OFFLINE_ONLY on in the frozen offline app. In source, in a normal
dev run, and in the connected build the flag stays False.
"""
import os

OFFLINE_ONLY = os.environ.get("SA_LIVE_OFFLINE") == "1"
