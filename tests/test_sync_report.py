import json
from datetime import date, datetime, timedelta

from daytrace.models import UTC, RawEvent, iso
from daytrace.report import day_bounds, merge, render, summarize
from daytrace.store import Store
from daytrace.sync import sync

ZERO = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)


class Fake:
    overlap = timedelta(0)

    def __init__(self, name, events=(), fail=False):
        self.name, self.events, self.fail, self.calls = name, list(events), fail, []
        self.purged = []

    def fetch(self, since, until):
        self.calls.append(since)
        if self.fail:
            raise ConnectionError("not running")
        return list(self.events)


def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.set_meta("day_zero", iso(ZERO))
    return s


def raw(source_id, start, minutes=None, **kw):
    end = start + timedelta(minutes=minutes) if minutes else None
    return RawEvent("fake", source_id, "span" if end else "point", "observed", start, end, **kw)


def test_forward_only_and_cursor(tmp_path, rules, projects):
    s = store(tmp_path)
    before = raw("old", ZERO - timedelta(hours=2), 30)
    straddle = raw("straddle", ZERO - timedelta(minutes=10), 30, path=str(projects / "PDG"))
    fake = Fake("fake", [before, straddle])
    now = ZERO + timedelta(hours=3)
    [result] = sync(s, rules, [fake], now)
    assert result.ok and result.stored == 1
    [row] = s.events_between(iso(ZERO), iso(now))
    assert row["start_at"] == iso(ZERO) and row["project"] == "pdg"

    sync(s, rules, [fake], now + timedelta(hours=1))
    assert fake.calls == [ZERO, now]  # second run starts from the cursor


def test_failing_source_does_not_stop_others(tmp_path, rules):
    s = store(tmp_path)
    good = Fake("good", [raw("a", ZERO + timedelta(hours=1), 5, app="Microsoft Outlook")])
    results = sync(s, rules, [Fake("bad", fail=True), good], ZERO + timedelta(hours=2))
    assert [r.ok for r in results] == [False, True]
    assert s.get_cursor("bad") is None and s.get_cursor("good")
    assert json.loads(s.get_meta("status:bad"))["error"].startswith("ConnectionError")


def test_upsert_extends_growing_span(tmp_path, rules):
    s = store(tmp_path)
    start = ZERO + timedelta(hours=1)
    sync(s, rules, [Fake("f", [raw("w", start, 5)])], start + timedelta(minutes=5))
    sync(s, rules, [Fake("f", [raw("w", start, 25)])], start + timedelta(minutes=30))
    [row] = s.events_between(iso(ZERO), iso(start + timedelta(hours=1)))
    assert row["end_at"] == iso(start + timedelta(minutes=25))


def test_purge_only_ingested_and_old(tmp_path, rules):
    s = store(tmp_path)

    class AW(Fake):
        def purge(self, before, since, ingested):
            self.purged.append((before, since, ingested(["fake:x", "nope"])))
            return 1

    aw = AW("fake", [raw("fake:x", ZERO + timedelta(hours=1), 5)])
    sync(s, rules, [aw], ZERO + timedelta(days=1))
    assert aw.purged == []  # nothing is older than the retention window yet
    aw.events[0].source_id = "fake:x"
    sync(s, rules, [aw], ZERO + timedelta(days=9))
    before, since, kept = aw.purged[0]
    assert since == ZERO and before == ZERO + timedelta(days=2) and kept == {"fake:x"}


def test_merge_and_overlap_aware_totals(tmp_path, rules, projects):
    t = datetime(2026, 10, 22, 0, 0).astimezone()
    assert merge([(t, t + timedelta(minutes=10)), (t + timedelta(minutes=5), t + timedelta(minutes=20))]) == \
        [(t, t + timedelta(minutes=20))]

    s = store(tmp_path)
    nine = datetime(2026, 10, 22, 9, 0).astimezone()
    events = [
        raw("cc", nine, 60, category="ai", path=str(projects / "PDG")),
        raw("w1", nine + timedelta(minutes=30), 60, app="Code", title="x — PDG"),
        raw("zoom", nine + timedelta(minutes=45), 30, app="zoom.us"),
        raw("song", nine, 30, app="Spotify", title="song"),
        raw("commit", nine + timedelta(minutes=50), category="commit", title="Fix it",
            path=str(projects / "PDG")),
    ]
    sync(s, rules, [Fake("fake", events)], nine + timedelta(hours=3))
    start, end = day_bounds(date(2026, 10, 22))
    rows = s.events_between(iso(start), iso(end))
    summary = summarize(rows, start, end)
    assert summary["projects"]["pdg"] == timedelta(minutes=90)  # union of 9:00–10:00 and 9:30–10:30
    assert summary["sensitivity"]["personal"] == timedelta(minutes=30)
    assert summary["commits"]["pdg"] == 1

    text = render(date(2026, 10, 22), rows, {"fake": s.get_meta("status:fake")}, ZERO)
    assert "pdg" in text and '"Fix it"' in text and "song" not in text
