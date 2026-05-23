r"""Optional Microsoft Graph integration: seed the prompt from your calendar.

When you start a meeting, this pulls the current/next Outlook event and returns
its subject + attendee names, so the prompt is auto-seeded with the people in the
meeting (better proper-noun accuracy) without you typing anything as it kicks off.

Auth is MSAL device-code flow against a PUBLIC client app registration in YOUR
M365 tenant (no client secret). Set two non-secret env vars once:

    setx SA_LIVE_MS_CLIENT_ID  "<Application (client) ID>"
    setx SA_LIVE_MS_TENANT_ID  "<Directory (tenant) ID>"

First run prints a code to enter at https://microsoft.com/devicelogin; the token
is cached at %LOCALAPPDATA%\sa-live-transcribe\ms_token_cache.bin, so later runs
are silent. Needs delegated Calendars.Read (admin-consented).

Standalone test (after setting the env vars):
    python -m live_transcribe.outlook

msal/requests are imported lazily, so importing this module is cheap and the live
tool runs fine without them unless --seed-from-calendar is used.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCOPES = ["Calendars.Read"]
_CACHE = Path(os.environ.get("LOCALAPPDATA", ".")) / "sa-live-transcribe" / "ms_token_cache.bin"


def _config():
    cid = os.environ.get("SA_LIVE_MS_CLIENT_ID", "").strip()
    tid = os.environ.get("SA_LIVE_MS_TENANT_ID", "").strip()
    if not cid or not tid:
        raise RuntimeError(
            "set SA_LIVE_MS_CLIENT_ID and SA_LIVE_MS_TENANT_ID (from your Entra app "
            "registration), see live_transcribe/outlook.py"
        )
    return cid, tid


def get_token():
    """Return a Graph access token. Silent if cached; otherwise prints a device code."""
    import msal
    cid, tid = _config()
    cache = msal.SerializableTokenCache()
    if _CACHE.exists():
        cache.deserialize(_CACHE.read_text())
    app = msal.PublicClientApplication(
        cid, authority=f"https://login.microsoftonline.com/{tid}", token_cache=cache
    )
    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(f"device flow init failed: {flow.get('error_description', flow)}")
        print(flow["message"], flush=True)  # "go to microsoft.com/devicelogin and enter CODE"
        result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"auth failed: {result.get('error_description', result)}")
    if cache.has_state_changed:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(cache.serialize())
    return result["access_token"]


def _parse(dtobj):
    # Graph returns e.g. "2026-05-22T13:00:00.0000000" (UTC for calendarView).
    return datetime.strptime(dtobj["dateTime"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def current_or_next_meeting(token, look_back_min=15, look_ahead_hours=8):
    """The meeting happening now, else the soonest upcoming one. None if nothing."""
    import requests
    now = datetime.now(timezone.utc)
    start = (now - timedelta(minutes=look_back_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(hours=look_ahead_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = (
        "https://graph.microsoft.com/v1.0/me/calendarView"
        f"?startDateTime={start}&endDateTime={end}"
        "&$select=subject,start,end,attendees,organizer&$orderby=start/dateTime&$top=10"
    )
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    r.raise_for_status()
    events = [e for e in r.json().get("value", []) if _parse(e["end"]) >= now]
    if not events:
        return None
    ongoing = [e for e in events if _parse(e["start"]) <= now]
    chosen = ongoing[0] if ongoing else events[0]  # events are start-ordered ascending

    names, seen = [], set()
    org = (chosen.get("organizer") or {}).get("emailAddress", {}).get("name")
    candidates = [org] + [
        (a.get("emailAddress") or {}).get("name") for a in chosen.get("attendees", [])
    ]
    for n in candidates:
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    return {"subject": (chosen.get("subject") or "").strip(), "attendees": names}


def _main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        meeting = current_or_next_meeting(get_token())
    except Exception as e:
        print(f"[error] {e}", flush=True)
        return 1
    if not meeting:
        print("No current/upcoming meeting in the next 8 hours.", flush=True)
        return 0
    print(f"Subject:   {meeting['subject'] or '(no subject)'}", flush=True)
    print(f"Attendees: {', '.join(meeting['attendees']) or '(none listed)'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
