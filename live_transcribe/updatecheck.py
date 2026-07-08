"""The app's single outbound "is there a newer version" call, isolated in one module.

This is the ONLY place Volksmond fetches its own version manifest, so the offline-only
build can compile it out cleanly: sa-live-transcribe.spec excludes this module from the
offline bundle, and web/app.py only registers /api/check-updates when it is present. Keep
every network line for the app update check here; nothing else in the app fetches
volksmond.digiphyte.com.

The check is manual, user-initiated, and CSRF-protected at the route. It makes one HTTPS GET
to our OWN published manifest (latest.json), sends no user data (only a generic User-Agent),
and compares the manifest version to this build. We host the manifest ourselves rather than a
third-party release feed, so the only server that ever sees an update check is ours, and a
release is a one-line manifest edit.
"""
import json
import urllib.request

MANIFEST_URL = "https://volksmond.digiphyte.com/latest.json"
SITE_URL = "https://volksmond.digiphyte.com/"


class UpdateCheckError(RuntimeError):
    """The manifest could not be reached or parsed. The message is safe to show the user."""


def _version_tuple(v):
    """Numeric version tuple for comparison. Takes the leading digits of each dotted part, so
    "1.1.1" -> (1,1,1) and "1.2.0-beta" -> (1,2,0); stops at a part with no leading digit."""
    parts = []
    for p in str(v or "").strip().lstrip("vV").split("."):
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def check(current_version):
    """Fetch the manifest and compare it to current_version. Returns the dict the UI expects, or
    raises UpdateCheckError if the manifest cannot be reached."""
    try:
        rq = urllib.request.Request(MANIFEST_URL, headers={
            "Accept": "application/json",
            "User-Agent": "Volksmond-update-check",
        })
        with urllib.request.urlopen(rq, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise UpdateCheckError(
            "Could not reach the update server. Check your internet connection and try again."
        ) from e
    latest = (data.get("version") or "").strip().lstrip("vV")
    available = bool(latest) and _version_tuple(latest) > _version_tuple(current_version)
    return {
        "current": current_version,
        "latest": latest or None,
        "update_available": available,
        # Where the in-app "Download" link sends the user. The manifest points it at the gated
        # download page (every download stays a captured lead); switch it to a direct link in
        # latest.json if existing-user update friction ever outweighs the capture.
        "url": data.get("url") or SITE_URL,
    }
