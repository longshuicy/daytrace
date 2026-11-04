import json
from datetime import date, datetime, timedelta

from daytrace import dayjson
from daytrace.adapters.claude_code import GAP
from daytrace.adapters.granola import Granola, notes_between
from daytrace.adapters.slack import (
    Slack,
    burst_raw,
    channel_label,
    cluster,
    huddle_raws,
    huddle_window,
    next_cursor,
    parse_hit,
    parse_match,
    parse_results,
)
from daytrace.intake import transform
from daytrace.models import UTC, RawEvent, iso
from daytrace.report import day_bounds
from daytrace.store import Store
from daytrace.sync import sync

from test_granola import FakeSession

T0 = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
ZERO = datetime(2026, 10, 22, 5, 0, tzinfo=UTC)


def _ts(dt):
    return f"{dt.timestamp():.6f}"


def _match(
    at,
    channel="C1",
    name="dev",
    text="",
    is_im=False,
    is_mpim=False,
    bot=False,
    permalink=None,
):
    return {
        "ts": _ts(at),
        "text": text,
        "bot_id": "B1" if bot else None,
        "permalink": permalink
        or f"https://slack.test/archives/{channel}/p{int(at.timestamp())}",
        "channel": {"id": channel, "name": name, "is_im": is_im, "is_mpim": is_mpim},
    }


def _hit(at, channel="C1", name="dev", text="", is_im=False, is_mpim=False, permalink=None):
    kind = "DM" if is_im else "Group DM" if is_mpim else f"#{name}"
    link = permalink or f"https://slack.test/archives/{channel}/p{int(at.timestamp())}"
    return (
        f"### Result 1 of 1\nChannel: {kind} (ID: {channel})\n"
        f"From: Pat Example (ID: U1)\nTime: {at:%Y-%m-%d %H:%M:%S} UTC\n"
        f"Message_ts: {_ts(at)}\nPermalink: [link]({link})\nText: \n{text}\n\n---\n"
    )


class SlackSession:
    def __init__(self, sent="", huddles=""):
        self.sent, self.huddles, self.calls = sent, huddles, []

    def call(self, name, args):
        self.calls.append((name, args))
        if "from:me" in (args.get("filters") or ""):
            return self.sent
        return self.huddles


def test_messages_in_one_channel_group_on_the_same_gap_as_agent_sessions():
    last = T0 + timedelta(minutes=8)
    items = [
        parse_match(_match(T0)),
        parse_match(_match(T0 + timedelta(minutes=3))),
        parse_match(_match(last)),
        parse_match(
            _match(last + GAP)
        ),  # exactly 10 min from last: same burst as Claude Code
        parse_match(_match(last + timedelta(hours=1))),  # well past the gap: splits
        parse_match(_match(T0 + timedelta(minutes=1), channel="C2", name="other")),
    ]
    groups = cluster(items)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 1, 4]
    burst = burst_raw(max(groups, key=len))
    assert burst.kind == "point" and burst.category == "chat"
    assert burst.title == "#dev · 4 messages" and burst.attrs["count"] == 4
    assert burst.url.startswith("https://slack.test/")
    assert "secret" not in repr(burst) and burst.attrs.keys() == {"count", "channel"}


def test_dm_and_group_dm_titles_have_no_names():
    dm = parse_match(_match(T0, channel="D1", name="alex", is_im=True, text="hi Pat"))
    mpim = parse_match(_match(T0, channel="G1", name="alex-sam", is_mpim=True))
    assert channel_label(dm["channel"]) == "Direct message"
    assert burst_raw([dm]).title == "Direct message"
    assert burst_raw([mpim]).title == "Group DM"
    assert "alex" not in burst_raw([dm]).title and "Pat" not in repr(burst_raw([dm]))


def test_huddle_from_system_message_not_from_someone_saying_huddle():
    start = huddle_raws(
        [
            parse_match(
                _match(T0, text="drshika2 has started a meeting", bot=True, name="dev")
            )
        ]
    )
    chat = huddle_raws([parse_match(_match(T0, text="shall we huddle now?"))])
    assert len(start) == 1 and not chat
    h = start[0]
    assert h.kind == "plan" and h.category == "meeting" and h.attrs["huddle"]
    assert h.title == "Huddle in #dev"
    assert h.end_at - h.start_at == timedelta(minutes=30)
    dm = huddle_raws(
        [
            parse_match(
                _match(
                    T0,
                    channel="D1",
                    name="alex",
                    is_im=True,
                    text="alex has started a huddle",
                    bot=True,
                )
            )
        ]
    )
    assert dm[0].title == "Huddle" and "alex" not in dm[0].title


def test_parse_results_reads_channel_time_and_link_and_drops_names():
    at = T0
    hits = parse_results(
        _hit(at, channel="C9", name="dev", text="pw: hunter2")
        + _hit(at, channel="D1", is_im=True, text="hi Pat")
        + _hit(at, channel="G1", is_mpim=True)
        + "For the next page of results use cursor `NEXT`\n"
    )
    assert [h["channel"]["id"] for h in hits] == ["C9", "D1", "G1"]
    assert channel_label(hits[0]["channel"]) == "#dev"
    assert channel_label(hits[1]["channel"]) == "Direct message"
    assert channel_label(hits[2]["channel"]) == "Group DM"
    assert hits[0]["permalink"].startswith("https://slack.test/")
    assert "Pat" not in repr({k: v for k, v in hits[1].items() if k != "text"})
    assert next_cursor("For the next page of results use cursor `NEXT`\n") == "NEXT"
    assert parse_hit("Channel: #dev (ID: C1)\n") is None  # no timestamp
    wrapped = (
        '{"results":'
        + json.dumps(_hit(at, channel="C9", name="dev", text="secret"))
        + ',"pagination_info":"For the next page of results use cursor `NEXT`\\n"}'
    )
    assert [h["channel"]["id"] for h in parse_results(wrapped)] == ["C9"]
    assert next_cursor(wrapped) == "NEXT"


def test_fetch_keeps_no_message_text(rules):
    secret = _hit(T0, text="pw: hunter2 please rotate")
    huddle = _hit(T0 + timedelta(minutes=5), text="pat has started a meeting")
    events = list(
        Slack(session=lambda: SlackSession(secret, huddle)).fetch(
            T0 - timedelta(hours=1), T0 + timedelta(hours=1)
        )
    )
    assert {e.kind for e in events} == {"point", "plan"}
    assert all("hunter2" not in repr(e) and "pw:" not in repr(e) for e in events)
    chats = [transform(e, rules) for e in events if e.category == "chat"]
    assert chats[0].title == "#dev" and chats[0].attrs["count"] == 1


def test_day_counts_grouped_messages_and_huddles_get_granola_notes(tmp_path, rules):
    now = T0 + timedelta(hours=4)
    huddle_at = datetime(
        2026, 10, 22, 15, 2, tzinfo=UTC
    )  # 10:02 local in the granola fixture
    sent = (
        _hit(T0)
        + _hit(T0 + timedelta(minutes=2))
        + _hit(T0 + timedelta(hours=2), channel="C2", name="pdg-ncsa")
    )
    huddle = _hit(huddle_at, text="bot has started a meeting")
    s = Store(tmp_path / "t.db")
    s.set_meta("day_zero", iso(ZERO))
    sync(s, rules, [Slack(session=lambda: SlackSession(sent, huddle))], now)
    Granola(tmp_path, session=lambda: FakeSession()).fetch(ZERO, now)
    start, end = day_bounds(date(2026, 10, 22))
    notes = notes_between(tmp_path, start, end)
    d = dayjson.build(
        date(2026, 10, 22),
        s.events_between(iso(start), iso(end)),
        {},
        {},
        ZERO,
        now,
        notes,
    )
    assert d["summary"]["counts"]["chats_sent"] == 3
    assert d["summary"]["counts"]["meetings"] == 1  # the huddle
    huddle = next(b for b in d["blocks"] if b["source"] == "slack")
    assert [n["title"] for n in huddle["notes"]] == ["Roadmap sync & planning"]
    assert any(
        p["title"] == "Hallway chat" for p in d["points"] if p["source"] == "granola"
    )
    titles = {p["title"] for p in d["points"] if p["source"] == "slack"}
    assert "#dev · 2 messages" in titles and "#pdg-ncsa" in titles


def test_calendar_keeps_granola_notes_when_a_huddle_overlaps(tmp_path, rules):
    now = T0 + timedelta(hours=4)
    s = Store(tmp_path / "t.db")
    s.set_meta("day_zero", iso(ZERO))
    # Same window as the Roadmap granola note (15:02 UTC).
    huddle = RawEvent(
        "slack",
        "h1",
        "plan",
        "recorded",
        datetime(2026, 10, 22, 15, 0, tzinfo=UTC),
        datetime(2026, 10, 22, 15, 30, tzinfo=UTC),
        category="meeting",
        title="Huddle in #dev",
        attrs={"huddle": True},
    )
    meeting = RawEvent(
        "outlook_calendar",
        "m1",
        "plan",
        "scheduled",
        datetime(2026, 10, 22, 15, 0, tzinfo=UTC),
        datetime(2026, 10, 22, 15, 30, tzinfo=UTC),
        category="meeting",
        title="Roadmap",
    )
    sync(
        s,
        rules,
        [
            type(
                "A",
                (),
                {
                    "name": "slack",
                    "overlap": timedelta(0),
                    "fetch": lambda self, a, b: [huddle],
                },
            )(),
            type(
                "B",
                (),
                {
                    "name": "outlook_calendar",
                    "overlap": timedelta(0),
                    "fetch": lambda self, a, b: [meeting],
                },
            )(),
        ],
        now,
    )
    Granola(tmp_path, session=lambda: FakeSession()).fetch(ZERO, now)
    start, end = day_bounds(date(2026, 10, 22))
    d = dayjson.build(
        date(2026, 10, 22),
        s.events_between(iso(start), iso(end)),
        {},
        {},
        ZERO,
        now,
        notes_between(tmp_path, start, end),
    )
    cal = next(b for b in d["blocks"] if b["source"] == "outlook_calendar")
    huddle_b = next(b for b in d["blocks"] if b["source"] == "slack")
    assert [n["title"] for n in cal["notes"]] == ["Roadmap sync & planning"]
    assert huddle_b["notes"] == []


def test_huddle_preview_window_is_a_meeting_block():
    assert huddle_window("Slack - Huddle Preview")
    assert not huddle_window("Huddles - Acme - Slack")
    row = {
        "source": "activitywatch", "category": "chat", "project": None, "sensitivity": "normal",
        "evidence": "observed", "kind": "span", "title": "Slack - Huddle Preview", "url": None,
        "attrs": "{}", "start_at": "2026-10-22T15:00:00Z", "end_at": "2026-10-22T15:00:04Z",
    }
    d = dayjson.build(date(2026, 10, 22), [row], {}, {}, ZERO, T0 + timedelta(hours=4))
    huddle = next(b for b in d["blocks"] if b["group"] == "meeting")
    assert huddle["category"] == "meeting" and huddle["minutes"] == 30
    assert d["summary"]["counts"]["meetings"] == 1
