import json
import stat
from datetime import date, datetime, timedelta

import pytest

from daytrace import egress, episodes, llm, summarize
from daytrace.models import UTC, RawEvent, iso
from daytrace.report import day_bounds
from daytrace.store import Store
from daytrace.sync import sync

DAY = date(2026, 10, 22)
ZERO = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)


class Fake:
    name, overlap = "fake", timedelta(0)

    def __init__(self, events):
        self.events = events

    def fetch(self, since, until):
        return self.events


def at(h, m=0):
    return datetime.combine(DAY, datetime.min.time()).astimezone() + timedelta(hours=h, minutes=m)


@pytest.fixture
def day_store(tmp_path, rules, projects, monkeypatch):
    monkeypatch.setenv("DAYTRACE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LLM_MODEL", "test-model")
    s = Store(tmp_path / "t.db")
    s.set_meta("day_zero", iso(ZERO))
    pdg = str(projects / "PDG")
    ev = lambda sid, start, minutes=None, **kw: RawEvent(
        "fake", sid, "span" if minutes else "point", "recorded", start,
        start + timedelta(minutes=minutes) if minutes else None, **kw)
    events = [
        ev("a1", at(9), 30, category="code", path=pdg, attrs={"branch": "feature/water"}),
        ev("a2", at(9, 35), 20, category="code", path=pdg),             # 5-min gap: same episode
        ev("a3", at(11), 15, category="code", path=pdg),                # 45-min gap: new episode
        ev("c1", at(9, 50), category="commit", title="Fix it for bob@x.org", path=pdg),
        ev("m1", at(10), 60, category="meeting", title="PDG weekly"),
        ev("in", at(10, 5), category="inbox", title="Newsletter"),
        ev("p1", at(12), 40, app="Spotify", title="Very private song"),
        ev("f1", at(20), 30, category="code", path=pdg),                # after "now": not counted
    ]
    events[4].kind = "plan"
    sync(s, rules, [Fake(events)], at(13))
    return s


def test_episodes_split_on_gaps_and_skip_mail_and_future(day_store):
    start, end = day_bounds(DAY)
    eps = episodes.build(day_store.events_between(iso(start), iso(end)), start, end, at(13))
    kinds = [(e["kind"], e["project"], e["minutes"]) for e in eps]
    assert kinds == [("project", "pdg", 50), ("meeting", None, 60), ("project", "pdg", 15)]
    assert eps[0]["counts"] == {"commit": 1} and eps[0]["branches"] == ["feature/water"]
    assert all("Newsletter" not in e["titles"] for e in eps)


def test_final_check_refuses_leaks():
    with pytest.raises(ValueError):
        egress.check({"episodes": [{"titles": ["mail carol@example.edu"]}]})
    with pytest.raises(ValueError):
        egress.check({"episodes": [{"titles": ["key sk-" + "A" * 30]}]})
    egress.check({"episodes": [{"titles": ["Opened PR #2"]}]})


def test_parse_reply_variants():
    assert llm.parse('{"narrative": "x", "themes": ["a"], "episodes": []}')["narrative"] == "x"
    with pytest.raises(ValueError):
        llm.parse("Sorry, I can't help with that.")


def test_llm_settings_accept_the_older_lumen_names(monkeypatch):
    for name in ("BASE_URL", "MODEL", "API_KEY"):
        monkeypatch.delenv(f"LLM_{name}", raising=False)
        monkeypatch.setenv(f"LUMEN_{name}", name.lower())
    monkeypatch.setenv("LLM_MODEL", "new")
    assert llm.settings() == ("base_url", "new", "api_key")


def test_entries_group_by_project_and_keep_browsing_to_sites():
    def ep(start, kind="project", project=None, group=None, minutes=10, **kw):
        return {"id": start, "kind": kind, "project": project, "group": group, "start_at": f"2026-10-22T{start}:00Z",
                "end_at": f"2026-10-22T{start}:30Z", "minutes": minutes, "activities": {}, "counts": {},
                "titles": [], "subjects": [], "sites": {}, "branches": [], **kw}
    items, also = episodes.entries([
        ep("14:00", project="pdg", titles=["app.py — PDG"], subjects=["Fix ingest"], counts={"commit": 1}),
        ep("15:00", kind="activity", group="browse", sites={"GitHub": 6, "arxiv.org": 4}),
        ep("16:00", project="pdg", titles=["a", "b", "c"], subjects=["Open PR"]),
        ep("17:00", kind="activity", group="other", minutes=1),               # too short: only counted
        ep("17:30", project="incore", minutes=1, counts={"push": 1}),         # short but a trace: kept
    ])
    assert [i["key"] for i in items] == ["pdg", "browse", "incore"]
    assert items[0]["titles"] == ["Fix ingest", "Open PR", "app.py — PDG"] and items[0]["minutes"] == 20
    assert len(items[0]["times"]) == 2 and items[0]["counts"] == {"commit": 1}
    assert items[1]["titles"] == [] and items[1]["sites"] == {"GitHub": 6, "arxiv.org": 4}
    assert also == {"episodes": 1, "minutes": 1}


WEEK = (date(2026, 10, 18), date(2026, 10, 24))


def request(store) -> str:
    """Build this week's request (nothing is sent) and return its audit copy."""
    return summarize.prepare_period(store, "week", *WEEK, at(13)).audit.read_text()


def test_week_request_is_built_from_gated_days_and_kept_for_review(day_store, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda body: pytest.fail("building a request must not call the LLM"))
    r = summarize.prepare_period(day_store, "week", *WEEK, at(13))
    assert r.mode == "dry-run" and r.audit.parent.name == "week-2026-10-18"
    assert stat.S_IMODE(r.audit.stat().st_mode) == 0o600
    body = json.loads(r.audit.read_text())
    assert body["messages"][0]["content"] == llm.WEEK_PROMPT
    payload = json.loads(body["messages"][1]["content"])
    text = json.dumps(payload)
    assert "Very private song" not in text and "Spotify" not in text and "Newsletter" not in text
    assert "bob@x.org" not in text and "Fix it for [email]" in text
    assert set(payload) == {"period", "stats", "days", "entries", "meetings", "also", "private"}
    assert payload["private"] == {"minutes": 40}  # a total only, no time ranges
    [day] = payload["days"]
    assert {k: day[k] for k in ("date", "active_min", "meeting_min", "first", "last")} == {
        "date": "Thu 10-22", "active_min": 165, "meeting_min": 60, "first": "09:00", "last": "12:40"}
    assert {"switches", "meeting_runs", "focus", "outside_9_5_min"} <= set(day)
    pdg = payload["entries"][0]
    assert set(pdg) == set(egress.PERIOD_ENTRY_FIELDS) and pdg["key"] == "pdg"
    assert pdg["days"] == ["Thu 10-22"]
    assert pdg["minutes"] == 65 and pdg["counts"] == {"commit": 1}
    story = summarize.current_period(day_store, "week", *WEEK)
    assert story["stats"]["dry_run"] is True and story["stats"]["request"] == payload


def test_nothing_is_sent_without_a_prepared_request(day_store, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda body: pytest.fail("must not call the LLM"))
    with pytest.raises(ValueError, match="review it first"):
        summarize.send_period(day_store, "week", WEEK[0], at(13))


def test_send_sends_the_reviewed_request_minus_exclusions(day_store, monkeypatch):
    sent = []
    monkeypatch.setattr(llm, "complete", lambda body: sent.append(body) or json.dumps(
        {"narrative": "Your week was PDG.", "themes": ["PDG"]}))
    summarize.prepare_period(day_store, "week", *WEEK, at(13))
    r = summarize.send_period(day_store, "week", WEEK[0], at(13), [
        {"entry": "meetings"},
        {"entry": "pdg", "field": "titles", "value": "Fix it for [email]"},
        {"entry": "pdg", "field": "titles", "value": "not in the request"},  # ignored: a review can't add
    ])
    payload = json.loads(sent[0]["messages"][1]["content"])
    assert [e["key"] for e in payload["entries"]] == ["pdg"] and payload["entries"][0]["titles"] == []
    assert r.mode == "sent" and json.loads(r.audit.read_text()) == sent[0]  # the audit holds what was sent
    assert summarize.current_period(day_store, "week", *WEEK)["narrative"] == "Your week was PDG."
    with pytest.raises(ValueError):  # the request is used up; a new one needs a new review
        summarize.send_period(day_store, "week", WEEK[0], at(13))


def test_week_story_is_withheld_once_its_events_turn_private(day_store, monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda body: json.dumps({"narrative": "Your week was PDG.", "themes": []}))
    summarize.prepare_period(day_store, "week", *WEEK, at(13))
    summarize.send_period(day_store, "week", WEEK[0], at(13))
    with day_store.conn:  # as if the PDG folder were made private and re-synced
        day_store.conn.execute("UPDATE events SET sensitivity = 'private', category = 'private', project = NULL, "
                               "title = NULL WHERE project = 'pdg'")
    assert summarize.current_period(day_store, "week", *WEEK) is None


def test_week_with_nothing_recorded_is_refused(day_store):
    with pytest.raises(ValueError):
        summarize.prepare_period(day_store, "week", date(2026, 10, 25), date(2026, 10, 31), at(13))


def test_a_page_that_names_no_site_goes_by_its_title(day_store, rules):
    page = RawEvent("fake", "b1", "span", "observed", at(12, 45), at(12, 50), app="Google Chrome",
                    title="Quarterly budget planning notes for the lab")
    sync(day_store, rules, [Fake([page])], at(13))
    assert "Quarterly budget planning notes for the lab" in request(day_store)  # for review before anything is sent


@pytest.mark.parametrize("title, expected", [
    ("Alex (DM) - Acme Lab - Slack", "Direct message - Acme Lab"),
    ("Ann Lee, Bo Park (DM) - Acme Lab - 2 new items - Slack", "Direct message - Acme Lab"),
    ("* pdg-team (Channel) - Acme Lab - 1 new item - Slack", "#pdg-team - Acme Lab"),
    ("Activity - Acme Lab - Slack", "Activity - Acme Lab"),
    ("Huddle with Alex - Acme Lab - Slack", "Slack - Acme Lab"),  # unknown shape: no name kept
])
def test_slack_titles_lose_names(title, expected):
    assert episodes.slack_title(title) == expected


def test_slack_dm_names_stay_local(day_store, rules):
    dm = RawEvent("fake", "s1", "span", "observed", at(12, 45), at(12, 50), app="Slack",
                  title="Alex (DM) - Acme Lab - Slack")
    sync(day_store, rules, [Fake([dm])], at(13))
    text = request(day_store)
    assert "Alex" not in text and "Direct message - Acme Lab" in text


@pytest.mark.parametrize("app, title, expected", [
    ("Microsoft Teams", "Chat | Alex Rivera | Microsoft Teams", "Microsoft Teams - Chat"),
    ("Microsoft Teams", "Alex Rivera | Microsoft Teams", "Microsoft Teams"),
    ("Microsoft Outlook", "Inbox • jdoe@example.edu", "Outlook - Inbox"),
    ("Microsoft Outlook", "Re: budget for Alex", "Outlook"),
    ("Google Chrome", "Mail - Pat Lee - Outlook", "Outlook - Mail"),  # Outlook on the web is category email
])
def test_outlook_and_teams_windows_keep_only_the_view(app, title, expected):
    row = {"title": title, "source": "activitywatch", "category": "email" if "Outlook" in title else "chat"}
    assert episodes.shared_title(row, {"app": app}) == expected


def test_sent_mail_subjects_and_meeting_names_stay_local(day_store, rules):
    from daytrace.adapters.outlook import calendar_raw, mail_raw
    meeting = calendar_raw({
        "id": "e1", "subject": "1:1 with Alex Rivera", "isAllDay": False, "isCancelled": False, "showAs": "busy",
        "start": {"dateTime": "2026-10-22T17:00:00"}, "end": {"dateTime": "2026-10-22T17:15:00"},
        "responseStatus": {"response": "accepted"},
        "organizer": {"emailAddress": {"name": "Rivera, Alex", "address": "ar@example.edu"}}, "attendees": []})
    sent = mail_raw({"id": "m1", "subject": "Accepted: Colloquium - Sam Taylor",
                     "sentDateTime": "2026-10-22T17:20:00Z", "toRecipients": []}, sent=True)
    assert meeting.title == "1:1 with Alex Rivera" and meeting.attrs["shared_title"] == "1:1 with [person]"
    assert "Alex" not in repr(meeting.attrs)
    sync(day_store, rules, [Fake([meeting, sent])], at(13))
    text = request(day_store)
    assert "Alex" not in text and "Sam" not in text and "1:1 with [person]" in text
