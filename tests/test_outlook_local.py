"""Unit tests for the multi-account local Outlook calendar read (WP-11).

The bug this pins: outlook_local used to call ns.GetDefaultFolder(), which resolves the DEFAULT
DELIVERY STORE only, so a meeting in a second Exchange/M365/PST account was invisible. The whole
COM graph is stubbed here (fake Stores / folders / Items / Restrict / GetFirst / GetNext, and fake
appointment times that expose only year..second attributes, the way pywintypes datetimes do), so
these tests import NO pywin32 and run on any machine.

Run:  python tests/test_outlook_local.py   (exit 0 = pass)
"""
import inspect
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_transcribe import outlook_local as O

NOW = datetime(2026, 7, 30, 10, 0, 0)          # fixed "now" so nothing here is clock-dependent
YEAR_FIRST = re.compile(r"'\d{4}-\d{2}-\d{2} \d{2}:\d{2}'")
SLASH_FIRST = re.compile(r"'\d{4}/\d{2}/\d{2} \d{2}:\d{2}'")


# --------------------------------------------------------------------------- fake COM graph
class FakeTime:
    """A pywintypes-like datetime: only the wall-clock components, no usable tzinfo."""

    def __init__(self, dt, with_second=True):
        self.year, self.month, self.day = dt.year, dt.month, dt.day
        self.hour, self.minute = dt.hour, dt.minute
        if with_second:
            self.second = dt.second


class FakeRecipient:
    def __init__(self, name):
        self.Name = name


class FakeAppt:
    def __init__(self, subject, start, end, gid=None, organizer=None, recipients=(),
                 start_raises=False):
        self._start_raises = start_raises
        self.Subject = subject
        self.End = FakeTime(end)
        if not start_raises:
            self.Start = FakeTime(start)
        if gid is not None:
            self.GlobalAppointmentID = gid
        if organizer is not None:
            self.Organizer = organizer
        self.Recipients = [FakeRecipient(n) for n in recipients]

    def __getattr__(self, name):        # only reached for attributes not set above
        if name == "Start" and self.__dict__.get("_start_raises"):
            raise RuntimeError("this item cannot be read")   # a corrupt/unreadable COM item
        raise AttributeError(name)                           # e.g. GlobalAppointmentID absent

    def __repr__(self):
        return f"<FakeAppt {self.Subject!r}>"


class FakeCollection:
    """A 1-based COM collection."""

    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        if i < 1 or i > len(self._items):
            raise IndexError(i)
        return self._items[i - 1]


class FakeRestricted:
    def __init__(self, appts):
        self._appts = list(appts)
        self._i = 0

    @property
    def Count(self):
        return len(self._appts)

    def GetFirst(self):
        self._i = 0
        return self._step()

    def GetNext(self):
        return self._step()

    def _step(self):
        if self._i >= len(self._appts):
            return None
        a = self._appts[self._i]
        self._i += 1
        return a


class FakeItems:
    """Items collection. `accepts` decides which Restrict date format this fake locale parses."""

    def __init__(self, appts, accepts=YEAR_FIRST):
        self._appts = list(appts)
        self._accepts = accepts
        self.queries = []
        self.IncludeRecurrences = None
        self.sorted_by = None

    @property
    def Count(self):
        return len(self._appts)

    def Sort(self, key):
        self.sorted_by = key

    def Restrict(self, query):
        self.queries.append(query)
        if not self._accepts.search(query):
            raise RuntimeError("could not parse the date in this locale")
        return FakeRestricted(self._appts)


class FakeFolder:
    def __init__(self, name, appts=(), subs=(), item_type=O.OL_APPOINTMENT_ITEM, accepts=YEAR_FIRST):
        self.Name = name
        self.Items = FakeItems(appts, accepts=accepts)
        self.DefaultItemType = item_type
        self.Folders = FakeCollection(subs)


class FakeStore:
    def __init__(self, name, calendar=None, raises=False):
        self.DisplayName = name
        self._cal = calendar
        self._raises = raises

    def GetDefaultFolder(self, kind):
        assert kind == O.OL_FOLDER_CALENDAR
        if self._raises:
            raise RuntimeError("this store has no calendar")
        return self._cal


class FakeNS:
    def __init__(self, stores=None, default_cal=None, with_stores=True):
        if with_stores:
            self.Stores = FakeCollection(stores or [])
        self._default = default_cal
        self.default_calls = 0

    def GetDefaultFolder(self, kind):
        assert kind == O.OL_FOLDER_CALENDAR
        self.default_calls += 1
        if self._default is None:
            raise RuntimeError("no default calendar either")
        return self._default


def appt(subject, start_min, dur_min=30, **kw):
    """An appointment starting start_min minutes from NOW (negative = already started)."""
    start = NOW + timedelta(minutes=start_min)
    return FakeAppt(subject, start, start + timedelta(minutes=dur_min), **kw)


def scan(cal, **kw):
    return O._scan_calendar(cal, NOW, NOW - timedelta(minutes=15), NOW + timedelta(hours=8), **kw)


# --------------------------------------------------------------------------- _to_naive
def test_to_naive_reads_components():
    assert O._to_naive(FakeTime(datetime(2026, 7, 30, 9, 5, 7))) == datetime(2026, 7, 30, 9, 5, 7)

def test_to_naive_without_second():
    got = O._to_naive(FakeTime(datetime(2026, 7, 30, 9, 5, 7), with_second=False))
    assert got == datetime(2026, 7, 30, 9, 5, 0)


# --------------------------------------------------------------------------- _calendar_folders
def test_two_stores_both_contribute_calendars():
    a, b = FakeFolder("A", [appt("a", 30)]), FakeFolder("B", [appt("b", 60)])
    ns = FakeNS([FakeStore("Work", a), FakeStore("Personal", b)])
    assert O._calendar_folders(ns) == [a, b]
    assert ns.default_calls == 0          # the default store is never special-cased any more

def test_secondary_store_meeting_is_found():
    """The actual bug: the default store is empty, the meeting lives in account 2."""
    ns = FakeNS([FakeStore("Work", FakeFolder("A")),
                 FakeStore("Personal", FakeFolder("B", [appt("Board meeting", 45)]))])
    assert O._lookup(ns, NOW)["subject"] == "Board meeting"

def test_store_raising_on_getdefaultfolder_is_skipped():
    good = FakeFolder("Good", [appt("Kwartaalvergadering", 20)])
    ns = FakeNS([FakeStore("Archive", raises=True), FakeStore("Work", good)])
    assert O._calendar_folders(ns) == [good]
    assert O._lookup(ns, NOW)["subject"] == "Kwartaalvergadering"

def test_store_returning_none_is_skipped():
    good = FakeFolder("Good", [appt("x", 20)])
    ns = FakeNS([FakeStore("Weird", None), FakeStore("Work", good)])
    assert O._calendar_folders(ns) == [good]

def test_stores_absent_falls_back_to_default_folder():
    default = FakeFolder("Default", [appt("Standup", 15)])
    ns = FakeNS(default_cal=default, with_stores=False)      # no .Stores attribute at all
    assert O._calendar_folders(ns) == [default]
    assert ns.default_calls == 1
    assert O._lookup(ns, NOW)["subject"] == "Standup"

def test_empty_stores_falls_back_to_default_folder():
    default = FakeFolder("Default", [appt("Standup", 15)])
    ns = FakeNS([], default_cal=default)
    assert O._calendar_folders(ns) == [default]
    assert ns.default_calls == 1

def test_no_calendar_anywhere_raises_outlook_unavailable():
    ns = FakeNS([FakeStore("Archive", raises=True)])          # and no default calendar either
    assert O._calendar_folders(ns) == []
    try:
        O._lookup(ns, NOW)
    except O.OutlookUnavailable:
        pass
    else:
        raise AssertionError("expected OutlookUnavailable when no calendar could be opened")

def test_one_level_of_calendar_subfolders_included():
    sub = FakeFolder("Second calendar", [appt("Sub meeting", 5)])
    notes = FakeFolder("Notes", [], item_type=0)              # not a calendar: excluded
    cal = FakeFolder("Main", [appt("Main meeting", 90)], subs=[sub, notes])
    ns = FakeNS([FakeStore("Work", cal)])
    assert O._calendar_folders(ns) == [cal, sub]
    assert O._lookup(ns, NOW)["subject"] == "Sub meeting"     # earliest across folder + sub-folder


# --------------------------------------------------------------------------- _scan_calendar
def test_scan_collects_all_candidates_not_just_the_first_ongoing():
    cal = FakeFolder("A", [appt("ongoing", -10), appt("next", 20), appt("later", 200)])
    assert [a.Subject for _, _, a in scan(cal)] == ["ongoing", "next", "later"]

def test_scan_drops_finished_and_out_of_window():
    cal = FakeFolder("A", [appt("over", -120, dur_min=30), appt("tomorrow", 60 * 20)])
    assert scan(cal) == []

def test_scan_sets_include_recurrences_and_sort():
    cal = FakeFolder("A", [appt("x", 10)])
    scan(cal)
    assert cal.Items.IncludeRecurrences is True
    assert cal.Items.sorted_by == "[Start]"

def test_scan_skips_unreadable_appointment():
    cal = FakeFolder("A", [appt("bad", 10, start_raises=True), appt("good", 20)])
    assert [a.Subject for _, _, a in scan(cal)] == ["good"]

def test_per_calendar_cap_bounds_the_walk():
    cal = FakeFolder("A", [appt(f"m{i}", 10 + i) for i in range(5)])
    assert len(scan(cal, cap=2)) == 2
    assert len(scan(cal)) == 5

def test_default_cap_is_200():
    assert inspect.signature(O._scan_calendar).parameters["cap"].default == 200

def test_scan_returns_empty_when_folder_cannot_be_opened():
    class Broken:
        @property
        def Items(self):
            raise RuntimeError("store offline")
    assert scan(Broken()) == []


# --------------------------------------------------------------------------- Restrict ladder guard
def test_restrict_ladder_leads_with_year_first_format():
    assert O._RESTRICT_FORMATS[0] == "%Y-%m-%d %H:%M"
    assert list(O._RESTRICT_FORMATS) == ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%m/%d/%Y %I:%M %p"]
    assert "%d/%m/%Y" not in " ".join(O._RESTRICT_FORMATS)    # the en-ZA format that silently misfired

def test_first_restrict_query_uses_the_year_first_format():
    cal = FakeFolder("A", [appt("x", 10)])
    scan(cal)
    assert len(cal.Items.queries) == 1
    assert YEAR_FIRST.search(cal.Items.queries[0])
    assert cal.Items.queries[0].startswith("[Start] <= '")

def test_ladder_falls_through_to_the_next_format():
    cal = FakeFolder("A", [appt("x", 10)], accepts=SLASH_FIRST)   # locale rejects the dashed form
    assert [a.Subject for _, _, a in scan(cal)] == ["x"]
    assert len(cal.Items.queries) == 2


# --------------------------------------------------------------------------- _choose
def test_ongoing_beats_upcoming_across_stores():
    ns = FakeNS([FakeStore("A", FakeFolder("A", [appt("upcoming on A", 5)])),
                 FakeStore("B", FakeFolder("B", [appt("ongoing on B", -10)]))])
    assert O._lookup(ns, NOW)["subject"] == "ongoing on B"

def test_earliest_upcoming_wins_across_stores():
    ns = FakeNS([FakeStore("A", FakeFolder("A", [appt("late on A", 90)])),
                 FakeStore("B", FakeFolder("B", [appt("soon on B", 20)]))])
    assert O._lookup(ns, NOW)["subject"] == "soon on B"

def test_earliest_ongoing_wins_within_the_ongoing_class():
    ns = FakeNS([FakeStore("A", FakeFolder("A", [appt("started 5 min ago", -5, dur_min=60)])),
                 FakeStore("B", FakeFolder("B", [appt("started 12 min ago", -12, dur_min=60)]))])
    assert O._lookup(ns, NOW)["subject"] == "started 12 min ago"

def test_gid_dedup_counts_the_same_meeting_once():
    """Same meeting invited to both accounts. Without dedup the second copy (earlier start on the
    fake data) would win; with dedup the first occurrence is kept."""
    ns = FakeNS([FakeStore("A", FakeFolder("A", [appt("Sales sync", 60, gid="GID-1")])),
                 FakeStore("B", FakeFolder("B", [appt("Sales sync (copy)", 30, gid="GID-1")]))])
    out = O._lookup(ns, NOW)
    assert out["subject"] == "Sales sync"
    assert out["start"] == (NOW + timedelta(minutes=60)).isoformat()

def test_different_gids_are_not_deduped():
    ns = FakeNS([FakeStore("A", FakeFolder("A", [appt("A meeting", 60, gid="GID-1")])),
                 FakeStore("B", FakeFolder("B", [appt("B meeting", 30, gid="GID-2")]))])
    assert O._lookup(ns, NOW)["subject"] == "B meeting"

def test_dedup_key_without_gid_falls_back_to_subject_and_start():
    start = NOW + timedelta(minutes=30)
    a = FakeAppt("  Standup  ", start, start + timedelta(minutes=15))       # no GlobalAppointmentID
    b = FakeAppt("STANDUP", start, start + timedelta(minutes=45))
    assert O._dedup_key(a, start) == O._dedup_key(b, start)                # case + whitespace folded
    assert O._dedup_key(a, start) != O._dedup_key(b, start + timedelta(minutes=1))
    assert O._dedup_key(a, start)[0] == "sig"

def test_no_gid_duplicate_is_dropped_by_choose():
    start = NOW + timedelta(minutes=30)
    a = FakeAppt("Standup", start, start + timedelta(minutes=15))
    b = FakeAppt("standup", start, start + timedelta(minutes=15))
    end = start + timedelta(minutes=15)
    assert O._choose([(start, end, a), (start, end, b)], NOW) is a

def test_dedup_key_uses_gid_when_present():
    start = NOW + timedelta(minutes=30)
    a = FakeAppt("Standup", start, start, gid="GID-9")
    assert O._dedup_key(a, start) == ("gid", "GID-9")

def test_choose_returns_none_without_candidates():
    assert O._choose([], NOW) is None

def test_lookup_returns_none_when_nothing_in_window():
    ns = FakeNS([FakeStore("A", FakeFolder("A")), FakeStore("B", FakeFolder("B"))])
    assert O._lookup(ns, NOW) is None


# --------------------------------------------------------------------------- winner-only work
def test_names_called_exactly_once_for_the_winner():
    calls = []
    real = O._names

    def counting(a):
        calls.append(a)
        return real(a)

    ns = FakeNS([
        FakeStore("A", FakeFolder("A", [appt("A1", 40, organizer="Sean Freimond",
                                             recipients=["Chenelle", "Sean Freimond"]),
                                       appt("A2", 80)])),
        FakeStore("B", FakeFolder("B", [appt("B1", 20, organizer="Pieter", recipients=["Ansie"]),
                                        appt("B2", 200)])),
    ])
    O._names = counting
    try:
        out = O._lookup(ns, NOW)
    finally:
        O._names = real
    assert out["subject"] == "B1"
    assert out["attendees"] == ["Pieter", "Ansie"]
    assert len(calls) == 1 and calls[0].Subject == "B1"

def test_return_shape_is_unchanged():
    ns = FakeNS([FakeStore("A", FakeFolder("A", [appt("Begroting", 25, organizer="Sean",
                                                     recipients=["Sean", "Ansie", ""])]))])
    out = O._lookup(ns, NOW)
    assert sorted(out) == ["attendees", "start", "subject"]
    assert out["subject"] == "Begroting"
    assert out["attendees"] == ["Sean", "Ansie"]              # organizer first, deduped, blanks gone
    assert out["start"] == (NOW + timedelta(minutes=25)).isoformat()

def test_look_ahead_hours_narrows_the_window():
    """/api/calendar-upcoming passes look_ahead_hours=1; a meeting 3 hours out must drop out."""
    ns = FakeNS([FakeStore("A", FakeFolder("A", [appt("in 3 hours", 180)]))])
    assert O._lookup(ns, NOW, look_ahead_hours=8)["subject"] == "in 3 hours"
    assert O._lookup(ns, NOW, look_ahead_hours=1) is None

def test_look_back_min_keeps_a_just_started_meeting():
    ns = FakeNS([FakeStore("A", FakeFolder("A", [appt("started 10 min ago", -10, dur_min=45)]))])
    assert O._lookup(ns, NOW)["subject"] == "started 10 min ago"

def test_public_signature_unchanged():
    params = inspect.signature(O.current_or_next_meeting).parameters
    assert list(params) == ["look_back_min", "look_ahead_hours"]
    assert params["look_back_min"].default == 15
    assert params["look_ahead_hours"].default == 8


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print("ok ", t.__name__)
    print(f"\nall {len(tests)} outlook_local tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
