import json
import re
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


def test_source_check_is_reported_as_a_warning(tmp_path, rules):
    s = store(tmp_path)

    class Broken(Fake):
        def check(self):
            return "watcher stopped"

    results = sync(s, rules, [Broken("aw"), Fake("ok")], ZERO + timedelta(hours=1))
    assert [(r.ok, r.warning) for r in results] == [(True, "watcher stopped"), (True, None)]
    assert json.loads(s.get_meta("status:aw"))["warning"] == "watcher stopped"


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
        raw("cc", nine, 60, category="code", path=str(projects / "PDG")),
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
    assert summary["sensitivity"]["private"] == timedelta(minutes=30)
    assert summary["commits"]["pdg"] == 1

    text = render(date(2026, 10, 22), rows, {"fake": s.get_meta("status:fake")}, ZERO)
    assert "pdg" in text and '"Fix it"' in text and "song" not in text


def test_day_json_groups_stretch_and_line(tmp_path, rules, projects):
    from daytrace import dayjson

    s = store(tmp_path)
    nine = datetime(2026, 10, 22, 9, 0).astimezone()
    pdg = str(projects / "PDG")
    events = [
        raw("cc1", nine, 40, category="code", path=pdg),
        raw("cc2", nine + timedelta(minutes=43), 30, category="code", path=pdg),  # 3-min gap: same stretch
        raw("zoom", nine + timedelta(hours=2), 60, app="zoom.us"),  # meetings never count as a stretch
        raw("song", nine, 20, app="Spotify", title="song"),
        raw("c1", nine + timedelta(minutes=50), category="commit", title="Fix it", path=pdg),
        raw("blip", nine + timedelta(hours=3), 0.2, app="Slack", title="glance"),  # 12 seconds
    ]
    now = nine + timedelta(hours=4)
    sync(s, rules, [Fake("fake", events)], now)
    start, end = day_bounds(date(2026, 10, 22))
    d = dayjson.build(date(2026, 10, 22), s.events_between(iso(start), iso(end)),
                      {"fake": {"ok": True}}, s.counts_by_source(), ZERO, now)

    # The 20-minute song (private) interrupts 9:00–9:20, so focus runs 9:20–10:13.
    longest = d["summary"]["longest"]
    assert (longest["label"], longest["kind"], longest["minutes"]) == ("pdg", "project", 53)
    assert d["summary"]["groups"]["meeting"] == 60 and d["summary"]["groups"]["private"] == 20 and d["summary"]["private_min"] == 20
    assert d["summary"]["active_min"] == 130  # 40 + 30 coding (gap excluded) + 60 meeting; the song overlaps
    assert d["summary"]["groups"]["code"] == 70
    assert d["line"] == "2h 10m active · longest focus 53m on pdg · 1 commit"
    song = next(b for b in d["blocks"] if b["sensitivity"] == "private")
    assert song["titles"] == [] and song["project"] is None
    assert [p["title"] for p in d["points"]] == ["Fix it"]
    # Too short to read as more than "0m": not drawn, though it still counts toward the totals.
    assert all(b["titles"] != ["glance"] for b in d["blocks"])


def test_highlights_from_stored_traces(tmp_path, rules, projects):
    from daytrace import dayjson

    s = store(tmp_path)
    nine = datetime(2026, 10, 22, 9, 0).astimezone()
    pdg = str(projects / "PDG")
    cc = lambda sid, start, minutes, branch: RawEvent("claude_code", sid, "span", "recorded", start,
                                                      start + timedelta(minutes=minutes), category="code",
                                                      path=pdg, attrs={"branch": branch})
    events = [
        cc("s1", nine, 30, "feature/water"),
        cc("s2", nine + timedelta(hours=1), 10, "main"),
        raw("c1", nine + timedelta(minutes=20), category="commit", title="Small fix", path=pdg,
            attrs={"insertions": 3, "deletions": 1, "files": 1, "branch": "fix/typo"}),
        raw("c2", nine + timedelta(minutes=25), category="commit", title="Water app", path=pdg,
            attrs={"insertions": 400, "deletions": 20, "files": 9}),
        raw("pr", nine + timedelta(minutes=28), category="pr", title="Opened PR #7: Water app", path=pdg),
        RawEvent("git", "p1", "point", "recorded", nine, category="commit", title="diary",
                 path=str(projects / "personal/daytrace")),
    ]
    rules.private_paths = [projects / "personal"]
    now = nine + timedelta(hours=3)
    sync(s, rules, [Fake("fake", events[2:6]), Fake("git", events[6:]), Fake("claude_code", events[:2])], now)
    start, end = day_bounds(date(2026, 10, 22))
    h = dayjson.build(date(2026, 10, 22), s.events_between(iso(start), iso(end)), {}, {}, ZERO, now)["highlights"]
    assert [x["title"] for x in h["shipped"]] == ["Opened PR #7: Water app"]
    # The private commit counts, but adds no project, files or lines.
    assert h["commits"] == {"count": 3, "private": 1, "files": 10, "insertions": 403, "deletions": 21,
                            "projects": ["pdg"]}
    # Commits name branches too, even ones no Claude Code session was on.
    # Trunks go last whatever their time.
    assert h["working_on"] == [
        {"project": "pdg", "branch": "feature/water", "minutes": 30, "commits": 0, "trunk": False},
        {"project": "pdg", "branch": "fix/typo", "minutes": 0, "commits": 1, "trunk": False},
        {"project": "pdg", "branch": "main", "minutes": 10, "commits": 0, "trunk": True},
    ]


def test_focus_counts_reading_and_breaks_on_interruptions(tmp_path, rules):
    from daytrace import dayjson

    s = store(tmp_path)
    nine = datetime(2026, 10, 22, 9, 0).astimezone()
    m = lambda n: nine + timedelta(minutes=n)
    events = [
        raw("pdf", m(0), 30, app="Preview", title="flood-model-review.pdf"),
        raw("web", m(31), 10, app="Google Chrome", title="Flood maps · arxiv.org"),
        raw("pdf2", m(42), 15, app="Preview", title="flood-model-review.pdf"),
        raw("slack", m(57), 1, app="Slack", title="general"),            # a glance: doesn't break focus
        raw("pdf3", m(58), 10, app="Preview", title="flood-model-review.pdf"),
        raw("mail", m(68), 5, app="Microsoft Outlook", title="Inbox"),   # 5 minutes of mail: breaks it
        raw("code", m(73), 20, app="Code", title="notes.py"),
    ]
    now = nine + timedelta(hours=3)
    sync(s, rules, [Fake("fake", events)], now)
    start, end = day_bounds(date(2026, 10, 22))
    longest = dayjson.build(date(2026, 10, 22), s.events_between(iso(start), iso(end)), {}, {}, ZERO, now)[
        "summary"]["longest"]
    assert (longest["label"], longest["kind"], longest["minutes"]) == ("flood-model-review.pdf", "document", 68)


def test_focus_is_time_on_one_thing_not_a_busy_hour(tmp_path, rules, projects):
    from daytrace import dayjson, period

    s = store(tmp_path)
    nine = datetime(2026, 10, 22, 9, 0).astimezone()
    m = lambda n: nine + timedelta(minutes=n)
    session = RawEvent("claude_code", "bg", "span", "recorded", m(0), m(55), category="code",
                       path=str(projects / "PDG"))
    hops = [raw(f"hop{n}", m(70 + n), 1, app="Google Chrome", title=f"Page {'abc'[n % 3]}") for n in range(30)]
    sync(s, rules, [Fake("claude_code", [session])], m(200))
    sync(s, rules, [Fake("aw", [
        raw("doc1", m(0), 25, app="Google Chrome", title="Plan - Google Docs"),
        raw("claude", m(25), 5, app="Claude", title="Claude"),  # the session's window: a short detour
        raw("doc2", m(30), 25, app="Google Chrome", title="Plan - Google Docs"),
        *hops,                                                   # three pages in turn: no focus on any
    ])], m(200))
    start, end = day_bounds(date(2026, 10, 22))
    rows = s.events_between(iso(start), iso(end))
    longest = dayjson.build(date(2026, 10, 22), rows, {}, {}, ZERO, m(200))["summary"]["longest"]
    # The background session ran all along, but the document was on screen.
    assert (longest["label"], longest["minutes"]) == ("Plan - Google Docs", 55)
    stretches = dayjson.focus_stretches(period._spans(rows, start, end, m(200)), [])
    assert [f["label"] for f in stretches if f["minutes"] >= 5] == ["Plan - Google Docs"]


def test_period_week_matches_its_days(tmp_path, rules, projects):
    from daytrace import dayjson, period

    assert period.week_of(date(2026, 10, 22)) == (date(2026, 10, 18), date(2026, 10, 24))
    assert period.month_of(date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))

    s = store(tmp_path)
    nine = datetime(2026, 10, 22, 9, 0).astimezone()
    pdg = str(projects / "PDG")
    sync(s, rules, [Fake("fake", [
        raw("cc1", nine, 40, category="code", path=pdg),
        raw("c1", nine + timedelta(minutes=30), category="commit", title="Fix it", path=pdg),
    ])], nine + timedelta(hours=2))
    now = nine + timedelta(hours=2)
    p = period.build(s, date(2026, 10, 18), date(2026, 10, 24), ZERO, now)

    start, end = day_bounds(date(2026, 10, 22))
    day = dayjson.build(date(2026, 10, 22), s.events_between(iso(start), iso(end)), {}, {}, ZERO, now)
    assert [d["has_data"] for d in p["days"]] == [False, False, False, False, True, False, False]
    assert [d["future"] for d in p["days"]] == [False] * 5 + [True, True]
    assert p["elapsed"] == 5
    assert p["totals"]["active_min"] == day["summary"]["active_min"] == 40
    assert p["projects"] == [{"project": "pdg", "minutes": 40, "inferred_min": 0, "commits": 1, "prs": 0}]
    assert p["focus"]["date"] == "2026-10-22"


def test_project_time_counts_each_moment_once_and_infers_the_rest(tmp_path, rules, projects):
    from daytrace import dayjson

    s = store(tmp_path)
    nine = datetime(2026, 10, 22, 9, 0).astimezone()
    m = lambda n: nine + timedelta(minutes=n)
    pdg, incore = str(projects / "PDG"), str(projects / "INCORE-2.0/incore-helm")
    session = lambda sid, start, minutes, path: RawEvent("claude_code", sid, "span", "recorded", m(start),
                                                         m(start + minutes), category="code", path=path)
    rules.project_titles = [(re.compile("pdg weekly", re.I), "pdg")]
    sync(s, rules, [Fake("claude_code", [session("a", 0, 30, pdg), session("b", 60, 20, pdg),
                                         session("c", 60, 20, incore)])], m(300))
    sync(s, rules, [Fake("aw", [
        raw("claude", m(0), 20, app="Claude", title="Claude"),               # during a pdg session: pdg, inferred
        raw("docs", m(20), 5, app="Google Chrome", title="Python docs"),     # between pdg and pdg: pdg, inferred
        raw("code", m(25), 10, app="Code", title=f"x — {pdg}"),              # tagged: pdg
        raw("slack", m(35), 5, app="Slack", title="general"),                # chat never borrows: Other
        raw("term", m(60), 20, app="iTerm2", title="zsh"),                   # two sessions: split pdg/incore
        raw("miro", m(100), 30, app="Google Chrome", title="Board - Miro"),  # nothing around it: Other
        RawEvent("aw", "meet", "plan", "observed", m(130), m(160), category="meeting", title="PDG weekly"),
        raw("cal-code", m(130), 30, app="Code", title=f"x — {incore}"),      # the meeting owns its time
        raw("song", m(200), 10, app="Spotify", title="song"),                # private: left out of projects
    ])], m(300))
    start, end = day_bounds(date(2026, 10, 22))
    d = dayjson.build(date(2026, 10, 22), s.events_between(iso(start), iso(end)), {}, {}, ZERO, m(300))
    rows = [(r["project"], r["minutes"], r["inferred_min"]) for r in d["summary"]["projects"]]
    assert rows == [("pdg", 75, 35), (None, 35, 0), ("incore", 10, 10)]  # Other ranks by time too
    assert d["summary"]["projects"][1]["parts"] == {"comms": 5, "untagged": 30}
    assert sum(m for _, m, _ in rows) + d["summary"]["private_min"] == d["summary"]["active_min"]


def test_period_average_counts_workdays_by_default(tmp_path, rules, projects):
    from daytrace import period

    s = store(tmp_path)
    pdg = str(projects / "PDG")
    thu = datetime(2026, 10, 22, 9, 0).astimezone()
    sat = datetime(2026, 10, 24, 9, 0).astimezone()
    now = sat + timedelta(hours=2)
    sync(s, rules, [Fake("fake", [
        raw("thu", thu, 60, category="code", path=pdg),
        raw("sat", sat, 20, category="code", path=pdg),
    ])], now)
    t = period.build(s, date(2026, 10, 18), date(2026, 10, 24), ZERO, now)["totals"]

    assert (t["workdays"], t["data_days"]) == (1, 2)
    assert t["avg_active_min"] == 60
    assert t["avg_active_min_all"] == 40


def test_top_sites_list_pages_without_a_site_by_title(tmp_path, rules):
    from daytrace import dayjson
    s = store(tmp_path)
    nine = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
    sync(s, rules, [Fake("fake", [
        raw("g", nine, 3, app="Google Chrome", title="Pull requests · acme/widgets · GitHub"),
        raw("p", nine + timedelta(minutes=5), 4, app="Google Chrome",
            title="Notes on choosing a widget library for the lab"),
        raw("n", nine + timedelta(minutes=10), 0.5, app="Google Chrome", title="Some short visit to a page"),
    ])], nine + timedelta(hours=1))
    start, end = day_bounds(date(2026, 10, 22))
    d = dayjson.build(date(2026, 10, 22), s.events_between(iso(start), iso(end)), {}, {}, ZERO,
                      nine + timedelta(hours=1))
    assert d["sites"] == [
        {"site": "Notes on choosing a widget library for the lab", "minutes": 4, "page": True},
        {"site": "GitHub", "minutes": 3, "page": False},
    ]


def test_backup_then_start_over(tmp_path, rules):
    s = store(tmp_path)
    nine = datetime(2026, 10, 22, 9, 0).astimezone()
    late = raw("late", nine.replace(hour=23, minute=50), 20, app="Code", title="a — PDG")  # runs past midnight
    sync(s, rules, [Fake("fake", [raw("old", nine, 30, app="Code", title="a — PDG"), late,
                                  raw("new", nine + timedelta(days=1), 30, app="Code", title="b — PDG")])],
         nine + timedelta(days=1, hours=1))
    s.save_summary("week:2026-10-18", {}, "a week", "m", nine)
    s.backup(tmp_path / "copy.db")

    zero = iso(datetime(2026, 10, 23).astimezone())
    assert s.start_over(zero) == 1
    assert {r["source_id"] for r in s.conn.execute("SELECT source_id FROM events")} == {"late", "new"}
    assert s.get_meta("day_zero") == zero and s.summary("week:2026-10-18") is None
    assert s.get_cursor("fake")  # syncing carries on where it was
    copy = Store(tmp_path / "copy.db")
    assert copy.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    assert copy.summary("week:2026-10-18")["narrative"] == "a week"
