from datetime import date, datetime, timedelta

from daytrace import dayjson
from daytrace.adapters.outlook import OutlookCalendar, calendar_raw, mail_raw
from daytrace.intake import transform
from daytrace.models import UTC, iso
from daytrace.report import day_bounds
from daytrace.store import Store
from daytrace.sync import sync

ZERO = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)


def _event(**kw):
    e = {"id": "AAMk1", "subject": "PDG standup", "isAllDay": False, "isCancelled": False, "showAs": "busy",
         "start": {"dateTime": "2026-10-22T15:00:00.0000000", "timeZone": "UTC"},
         "end": {"dateTime": "2026-10-22T15:30:00.0000000", "timeZone": "UTC"},
         "responseStatus": {"response": "accepted"},
         "organizer": {"emailAddress": {"name": "Someone", "address": "someone@illinois.edu"}},
         "attendees": [{}, {}, {}], "isOnlineMeeting": True, "onlineMeetingProvider": "teamsForBusiness"}
    e.update(kw)
    return e


def test_calendar_event_mapping_and_filters(rules):
    raw = calendar_raw(_event())
    assert raw.kind == "plan" and raw.evidence == "scheduled" and raw.category == "meeting"
    assert iso(raw.start_at) == "2026-10-22T15:00:00Z" and iso(raw.end_at) == "2026-10-22T15:30:00Z"
    assert raw.attrs == {"attendees": 3, "response": "accepted", "organizer_domain": "illinois.edu",
                         "online": "teamsForBusiness"}
    assert "someone@" not in repr(raw)
    assert transform(raw, rules).sensitivity == "normal"
    for skipped in (dict(isCancelled=True), dict(isAllDay=True), dict(showAs="free"),
                    dict(responseStatus={"response": "declined"})):
        assert calendar_raw(_event(**skipped)) is None


def test_mail_keeps_domains_not_addresses():
    sent = mail_raw({"id": "m1", "subject": "Re: budget", "sentDateTime": "2026-10-22T16:00:00Z",
                     "toRecipients": [{"emailAddress": {"address": "Alice@Illinois.edu"}}],
                     "ccRecipients": [{"emailAddress": {"address": "bob@nsf.gov"}},
                                      {"emailAddress": {"address": "carol@illinois.edu"}}],
                     "conversationId": "AAQk" + "x" * 60}, sent=True)
    assert sent.category == "email" and sent.attrs["recipients"] == 3
    assert sent.attrs["domains"] == ["illinois.edu", "nsf.gov"]
    assert "alice" not in repr(sent).lower() and "bob" not in repr(sent)
    received = mail_raw({"id": "m2", "subject": "Hi", "receivedDateTime": "2026-10-22T16:05:00Z",
                         "from": {"emailAddress": {"address": "x@github.com"}}}, sent=False)
    assert received.category == "inbox" and received.attrs["domains"] == ["github.com"] and received.url is None


class FakeCalendar(OutlookCalendar):
    def __init__(self, events):
        self.events = events

    def fetch(self, since, until):
        return [r for r in map(calendar_raw, self.events) if r]


def _store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.set_meta("day_zero", iso(ZERO))
    return s


def test_moved_or_deleted_meetings_disappear(tmp_path, rules):
    s = _store(tmp_path)
    now = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
    sync(s, rules, [FakeCalendar([_event(), _event(id="AAMk2", subject="1:1")])], now)
    assert s.counts_by_source()["outlook_calendar"] == 2
    [result] = sync(s, rules, [FakeCalendar([_event()])], now + timedelta(minutes=10))
    assert result.removed == 1 and s.counts_by_source()["outlook_calendar"] == 1


def test_day_counts_only_the_past_and_calendar_meetings(tmp_path, rules):
    s = _store(tmp_path)
    local = datetime(2026, 10, 22, 0, 0).astimezone()
    meeting = lambda i, h: _event(id=f"m{i}", start={"dateTime": iso(local + timedelta(hours=h))[:19]},
                                  end={"dateTime": iso(local + timedelta(hours=h, minutes=30))[:19]})
    now = local + timedelta(hours=12)
    sync(s, rules, [FakeCalendar([meeting(1, 10), meeting(2, 15)])], now)  # 10:00 done, 15:00 upcoming
    start, end = day_bounds(date(2026, 10, 22))
    d = dayjson.build(date(2026, 10, 22), s.events_between(iso(start), iso(end)), {}, {}, ZERO, now)
    assert d["summary"]["counts"]["meetings"] == 1 and d["summary"]["counts"]["meeting_min"] == 30
    assert d["summary"]["active_min"] == 30
    assert len([b for b in d["blocks"] if b["category"] == "meeting"]) == 2  # upcoming one is still drawn
    assert d["line"].startswith("30m active · 1 meeting")
