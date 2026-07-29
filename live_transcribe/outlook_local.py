r"""Read the LOCAL Outlook desktop calendar (no cloud, no login, no app registration).

This is the offline, on-brand alternative to outlook.py (which uses Microsoft Graph).
It talks to the classic Outlook desktop app already installed and signed in on this
machine, over COM, and returns the current or next meeting's subject + attendee names
so the prompt can be seeded without typing. Nothing leaves the computer: Volksmond
never makes a network call here; it reads the local MAPI store that Outlook keeps.

ALL accounts are read, not just the default one. Outlook's default delivery store is only
one of the data files a profile has open, so a meeting living in a second Exchange / M365
/ PST account used to be invisible here. Every open store now contributes its calendar
(plus one level of calendar sub-folders), the candidates are merged, and the winner is
chosen across all of them. Shared/delegate calendars stay out of scope on purpose:
opening one of those is an Exchange RPC call, which would break the local-only claim.

Requirements and limits (read before trusting this):
  - Windows + pywin32 (win32com). Imported lazily, so the app runs fine without it.
  - The CLASSIC Outlook desktop app must be installed and signed in. The new "Outlook"
    Store app has no COM automation interface, so this returns a clear error there.
  - COM runs on the calling thread, so callers on a worker thread must let this module
    do its own CoInitialize (it does, per call).
  - A Gmail account added as IMAP appears as a store but carries no calendar data (IMAP
    has no calendar), so it simply contributes no meetings. This is not Gmail support.

Every failure path raises OutlookUnavailable with a plain message the UI can show, or
returns None when Outlook is reachable but there is simply no meeting in the window.

Standalone test on a machine with Outlook:
    python -m live_transcribe.outlook_local
    python -m live_transcribe.outlook_local --stores    (read-only per-account diagnostic)
"""
from datetime import datetime, timedelta

OL_FOLDER_CALENDAR = 9  # olFolderCalendar
OL_APPOINTMENT_ITEM = 1  # olAppointmentItem, the DefaultItemType of a calendar folder

_UNAVAILABLE_MSG = (
    "Could not reach the Outlook desktop app. Open classic Outlook and sign in, "
    "then try again. (The new Outlook app does not support this.)"
)

# Outlook's Restrict wants a date string whose parsing depends on the machine locale, so lead with
# UNAMBIGUOUS year-first formats (which cannot be read as either d/m or m/d) and keep US m/d/Y only
# as a last resort. The en-ZA d/m/Y format is deliberately NOT in this list: on this machine it
# silently matched the wrong window. The order is load-bearing; a guard test pins the first entry.
_RESTRICT_FORMATS = ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%m/%d/%Y %I:%M %p")


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
    """Organizer + invitee display names for an appointment, de-duplicated, order kept.

    Called for the WINNING appointment only: resolving Recipients is the expensive part of this
    module, and doing it while scanning every calendar would multiply that cost by the candidate
    count for names that get thrown away."""
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


def _sub_calendars(cal):
    """One level of calendar sub-folders under `cal` (a second calendar usually lives there).

    One level only, on purpose: a deep walk multiplies the per-poll cost for folders that almost
    never hold the meeting you are about to attend. Anything unreadable is skipped."""
    out = []
    try:
        subs = cal.Folders
        count = int(subs.Count)
    except Exception:
        return out
    for i in range(1, count + 1):  # COM collections are 1-based
        try:
            f = subs.Item(i)
        except Exception:
            continue
        if f is None:
            continue
        try:
            if int(f.DefaultItemType) != OL_APPOINTMENT_ITEM:
                continue  # a notes/tasks sub-folder, not a calendar
        except Exception:
            pass  # cannot tell what it is: keep it, the scan tolerates a folder with no items
        out.append(f)
    return out


def _calendar_folders(ns):
    """Every LOCAL calendar folder in the profile: one per open store, plus one level of sub-folders.

    ns.GetDefaultFolder resolves the DEFAULT DELIVERY STORE only, which is exactly why secondary
    accounts used to be invisible. Walking ns.Stores instead picks up every data file the profile has
    open. Archives, public-folder stores and closed .pst files raise on GetDefaultFolder, so each
    store is tried on its own and failures are skipped rather than aborting the sweep. Falls back to
    the old single-folder behaviour when Stores is unavailable or yields nothing, so the worst case
    here is exactly the previous behaviour."""
    folders = []
    stores, count = None, 0
    try:
        stores = ns.Stores
        count = int(stores.Count)
    except Exception:
        stores, count = None, 0
    if stores is not None and count > 0:
        for i in range(1, count + 1):  # COM collections are 1-based
            try:
                store = stores.Item(i)
            except Exception:
                continue
            if store is None:
                continue
            try:
                cal = store.GetDefaultFolder(OL_FOLDER_CALENDAR)
            except Exception:
                continue  # archive / public / closed store with no calendar: not an error
            if cal is None:
                continue
            folders.append(cal)
            folders.extend(_sub_calendars(cal))
    if folders:
        return folders
    try:
        cal = ns.GetDefaultFolder(OL_FOLDER_CALENDAR)
    except Exception:
        return []
    return [cal] if cal is not None else []


def _scan_calendar(cal, now, window_start, window_end, cap=200):
    """The appointments in ONE calendar folder that overlap the window.

    Returns a list of (start, end, appt) with naive local datetimes. COLLECTS every match instead of
    stopping at the first ongoing one: with N calendars merged, "first hit wins" would let whichever
    store happened to be enumerated first beat a better candidate elsewhere. Anything unreadable is
    skipped, and the per-folder cap bounds the work on a calendar with a dense recurrence set."""
    try:
        items = cal.Items
        items.IncludeRecurrences = True
        items.Sort("[Start]")  # so the cap truncates the far end, not the imminent meetings
    except Exception:
        return []

    # Recurring items expand from the beginning of time, so an unbounded scan is unsafe; Restrict to
    # the window first, walking the locale-safe format ladder (see _RESTRICT_FORMATS for why the
    # order matters). A miss just means "no meeting found" (a safe degradation), never a crash.
    restricted = None
    for fmt in _RESTRICT_FORMATS:
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
        return []

    found = []
    scanned = 0
    try:
        appt = restricted.GetFirst()
    except Exception:
        return []
    while appt is not None and scanned < cap:
        scanned += 1
        start = end = None
        try:
            start = _to_naive(appt.Start)
            end = _to_naive(appt.End)
        except Exception:
            start = end = None
        if start is not None and end is not None and end >= now and start <= window_end:
            found.append((start, end, appt))
        try:
            appt = restricted.GetNext()
        except Exception:
            break
    return found


def _dedup_key(appt, start):
    """A stable identity for an appointment, so one meeting sitting in two accounts counts once.

    GlobalAppointmentID is the real answer. When it is missing or unreadable (some providers, and
    .ics subscriptions), fall back to a subject + start signature."""
    gid = None
    try:
        gid = getattr(appt, "GlobalAppointmentID", None)
    except Exception:
        gid = None
    if gid:
        return ("gid", str(gid))
    subject = ""
    try:
        subject = (appt.Subject or "").strip().casefold()
    except Exception:
        subject = ""
    return ("sig", subject, start.isoformat())


def _choose(candidates, now):
    """The winning appointment out of the merged candidate list, or None.

    A meeting happening right now beats an upcoming one outright; within each class the earliest
    start wins. Duplicates (the same meeting in two accounts) drop out, first occurrence kept.
    Sorting per folder is not enough once folders are merged, so the comparison is explicit."""
    best_ongoing = None   # (start, appt)
    best_upcoming = None
    seen = set()
    for start, end, appt in candidates:
        key = _dedup_key(appt, start)
        if key in seen:
            continue
        seen.add(key)
        if start <= now <= end:
            if best_ongoing is None or start < best_ongoing[0]:
                best_ongoing = (start, appt)
        elif start > now:
            if best_upcoming is None or start < best_upcoming[0]:
                best_upcoming = (start, appt)
    winner = best_ongoing or best_upcoming
    return winner[1] if winner else None


def _lookup(ns, now, look_back_min: int = 15, look_ahead_hours: int = 8):
    """The whole calendar read, given an already-opened MAPI namespace and "now".

    Split out from current_or_next_meeting so the merge/dedup/choose logic is testable against a
    stubbed namespace: no COM, no pywin32. Raises OutlookUnavailable when not one calendar folder
    could be opened, which is the same "Outlook is not really there" condition as before."""
    folders = _calendar_folders(ns)
    if not folders:
        raise OutlookUnavailable(_UNAVAILABLE_MSG)

    window_end = now + timedelta(hours=look_ahead_hours)
    window_start = now - timedelta(minutes=look_back_min)

    candidates = []
    for cal in folders:
        candidates.extend(_scan_calendar(cal, now, window_start, window_end))

    chosen = _choose(candidates, now)
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


def current_or_next_meeting(look_back_min: int = 15, look_ahead_hours: int = 8):
    """The meeting happening now, else the soonest upcoming one, else None.

    Reads every account/store in the Outlook profile, not just the default one.
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
        except Exception as e:
            raise OutlookUnavailable(_UNAVAILABLE_MSG) from e
        return _lookup(ns, datetime.now(), look_back_min, look_ahead_hours)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def _stores_report(look_back_min: int = 15, look_ahead_hours: int = 8):
    """Read-only diagnostic: what every store in this Outlook profile looks like to Volksmond.

    Writes nothing and sends nothing. Cross-check the list against Outlook itself:
    File > Account Settings > Account Settings > Data Files."""
    try:
        import pythoncom
        import win32com.client
    except Exception as e:
        print(f"[unavailable] pywin32 is missing in this interpreter ({e}).", flush=True)
        return 1

    pythoncom.CoInitialize()
    try:
        try:
            ns = win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
        except Exception as e:
            print(f"[unavailable] {_UNAVAILABLE_MSG} ({e})", flush=True)
            return 1

        now = datetime.now()
        window_end = now + timedelta(hours=look_ahead_hours)
        window_start = now - timedelta(minutes=look_back_min)
        print(
            "now={}  window={} .. {}".format(
                now.isoformat(timespec="seconds"),
                window_start.strftime("%Y-%m-%d %H:%M"),
                window_end.strftime("%Y-%m-%d %H:%M"),
            ),
            flush=True,
        )

        def report(cal, label):
            try:
                total = int(cal.Items.Count)
            except Exception as e:
                total = "?({})".format(e)
            hits = _scan_calendar(cal, now, window_start, window_end)
            print(f"      {label}: Items.Count={total} in-window={len(hits)}", flush=True)
            for start, end, appt in hits:
                try:
                    subject = (appt.Subject or "").strip() or "(no subject)"
                except Exception:
                    subject = "(unreadable subject)"
                print(
                    "        - {} -> {}  {}".format(
                        start.strftime("%Y-%m-%d %H:%M"), end.strftime("%H:%M"), subject
                    ),
                    flush=True,
                )

        try:
            stores = ns.Stores
            count = int(stores.Count)
        except Exception as e:
            print(f"ns.Stores unavailable ({e}); the default calendar is all that can be read.", flush=True)
            stores, count = None, 0
        print(f"stores: {count}", flush=True)

        for i in range(1, count + 1):
            try:
                store = stores.Item(i)
            except Exception as e:
                print(f"  [{i}] <could not open this store: {e}>", flush=True)
                continue

            def attr(name, _store=store):
                try:
                    return getattr(_store, name)
                except Exception as e:
                    return "?({})".format(e)

            print(
                "  [{}] DisplayName={!r} ExchangeStoreType={} IsDataFileOpen={}".format(
                    i, attr("DisplayName"), attr("ExchangeStoreType"), attr("IsDataFileOpen")
                ),
                flush=True,
            )
            try:
                cal = store.GetDefaultFolder(OL_FOLDER_CALENDAR)
            except Exception as e:
                print(f"      calendar: NO ({e})", flush=True)
                continue
            if cal is None:
                print("      calendar: NO (returned None)", flush=True)
                continue
            print("      calendar: yes", flush=True)
            report(cal, "default calendar")
            for sub in _sub_calendars(cal):
                try:
                    name = sub.Name
                except Exception:
                    name = "(unnamed)"
                report(sub, f"sub-folder {name!r}")

        print(f"\ncalendar folders Volksmond would scan: {len(_calendar_folders(ns))}", flush=True)
        return 0
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
    if "--stores" in sys.argv[1:]:
        return _stores_report()
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
