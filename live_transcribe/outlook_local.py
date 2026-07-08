r"""Read the LOCAL Outlook desktop calendar (no cloud, no login, no app registration).

This is the offline, on-brand alternative to outlook.py (which uses Microsoft Graph).
It talks to the classic Outlook desktop app already installed and signed in on this
machine, over COM, and returns the current or next meeting's subject + attendee names
so the prompt can be seeded without typing. Nothing leaves the computer: Volksmond
never makes a network call here; it reads the local MAPI store that Outlook keeps.

Requirements and limits (read before trusting this):
  - Windows + pywin32 (win32com). Imported lazily, so the app runs fine without it.
  - The CLASSIC Outlook desktop app must be installed and signed in. The new "Outlook"
    Store app has no COM automation interface, so this returns a clear error there.
  - COM runs on the calling thread, so callers on a worker thread must let this module
    do its own CoInitialize (it does, per call).

Every failure path raises OutlookUnavailable with a plain message the UI can show, or
returns None when Outlook is reachable but there is simply no meeting in the window.

Standalone test on a machine with Outlook:
    python -m live_transcribe.outlook_local
"""
from datetime import datetime, timedelta

OL_FOLDER_CALENDAR = 9  # olFolderCalendar


class OutlookUnavailable(RuntimeError):
    """Outlook/pywin32 could not be reached. The message is safe to show the user."""


def _to_naive(pt):
    """A COM/pywintypes datetime to a naive LOCAL datetime, matching the wall-clock time Outlook
    shows (the value to compare against a local datetime.now()).

    pywin32 returns AppointmentItem.Start with the LOCAL wall-clock components (e.g. 09:00) but tags
    them with a bogus +00:00 tzinfo, so .timestamp() misreads 09:00 as UTC and shifts it by the UTC
    offset (09:00 -> 11:00 in SAST). Read the calendar fields directly instead; never round-trip
    through .timestamp()."""
    return datetime(pt.year, pt.month, pt.day, pt.hour, pt.minute, getattr(pt, "second", 0))


def _names(appt):
    """Organizer + invitee display names for an appointment, de-duplicated, order kept."""
    names, seen = [], set()

    def add(n):
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)

    try:
        add(appt.Organizer)
    except Exception:
        pass
    try:
        for r in appt.Recipients:  # 1-based COM collection, iterable via win32com
            try:
                add(r.Name)
            except Exception:
                continue
    except Exception:
        pass
    return names


def current_or_next_meeting(look_back_min: int = 15, look_ahead_hours: int = 8):
    """The meeting happening now, else the soonest upcoming one, else None.

    Returns {"subject": str, "attendees": [str, ...], "start": iso-str-or-None} or None.
    Raises OutlookUnavailable if Outlook/pywin32 is not reachable.
    """
    try:
        import pythoncom
        import win32com.client
    except Exception as e:
        raise OutlookUnavailable(
            "Outlook integration is not installed in this build (pywin32 missing)."
        ) from e

    pythoncom.CoInitialize()
    try:
        try:
            ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
            cal = ns.GetDefaultFolder(OL_FOLDER_CALENDAR)
        except Exception as e:
            raise OutlookUnavailable(
                "Could not reach the Outlook desktop app. Open classic Outlook and sign in, "
                "then try again. (The new Outlook app does not support this.)"
            ) from e

        now = datetime.now()
        window_end = now + timedelta(hours=look_ahead_hours)
        window_start = now - timedelta(minutes=look_back_min)

        items = cal.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")

        # Recurring items expand from the beginning of time, so an unbounded scan is unsafe;
        # Restrict to the window first. Outlook's Restrict wants a date string whose parsing
        # depends on the machine locale, so lead with UNAMBIGUOUS year-first formats (which cannot
        # be read as either d/m or m/d) and keep US m/d/Y only as a last resort. The en-ZA d/m/Y
        # format is deliberately NOT used: on this machine it silently matched the wrong window.
        # A miss just means "no meeting found" (a safe degradation), never a crash.
        restricted = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%m/%d/%Y %I:%M %p"):
            try:
                query = "[Start] <= '{}' AND [End] >= '{}'".format(
                    window_end.strftime(fmt), window_start.strftime(fmt)
                )
                r = items.Restrict(query)
                # Force evaluation; a bad format tends to yield an empty/erroring collection.
                if r.Count >= 0:
                    restricted = r
                    if r.Count > 0:
                        break
            except Exception:
                continue
        if restricted is None:
            return None

        ongoing, upcoming = None, None
        scanned = 0
        appt = restricted.GetFirst()
        while appt is not None and scanned < 200:
            scanned += 1
            try:
                start = _to_naive(appt.Start)
                end = _to_naive(appt.End)
            except Exception:
                appt = restricted.GetNext()
                continue
            if end >= now and start <= window_end:
                if start <= now and ongoing is None:
                    ongoing = appt
                    break  # a meeting on right now wins outright
                if start > now and upcoming is None:
                    upcoming = appt  # earliest upcoming (collection is Start-sorted)
            appt = restricted.GetNext()

        chosen = ongoing or upcoming
        if chosen is None:
            return None
        subject = ""
        try:
            subject = (chosen.Subject or "").strip()
        except Exception:
            pass
        start_iso = None
        try:
            start_iso = _to_naive(chosen.Start).isoformat()
        except Exception:
            pass
        return {"subject": subject, "attendees": _names(chosen), "start": start_iso}
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        meeting = current_or_next_meeting()
    except OutlookUnavailable as e:
        print(f"[unavailable] {e}", flush=True)
        return 1
    if not meeting:
        print("No current or upcoming meeting in the window.", flush=True)
        return 0
    print(f"Subject:   {meeting['subject'] or '(no subject)'}", flush=True)
    print(f"Attendees: {', '.join(meeting['attendees']) or '(none listed)'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
