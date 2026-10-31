from datetime import datetime, timedelta

from daytrace import egress, llm
from daytrace.patterns import day_patterns

# A Thursday, in local time, so 9-to-5 boundaries mean what they say wherever the tests run.
DAY = datetime(2026, 10, 22).astimezone()


def _row(start, end, category="code", project=None, kind="span", source="activitywatch", sensitivity="normal",
         title=None):
    at = lambda hm: (DAY + timedelta(hours=int(hm[:2]), minutes=int(hm[3:]))).isoformat()
    return {"start_at": at(start), "end_at": at(end), "category": category, "project": project, "kind": kind,
            "source": source, "sensitivity": sensitivity, "title": title, "attrs": "{}", "evidence": "observed"}


def _meeting(start, end):
    return _row(start, end, category="meeting", kind="plan", source="outlook_calendar", title="Sync")


def test_day_patterns_count_switches_meetings_focus_and_hours():
    rows = [
        _row("08:00", "08:40", project="alpha"),             # 8:00 to 9:00 is outside 9-5
        _row("08:40", "08:41", project="beta"),              # too short to be a switch
        _row("08:41", "09:30", project="alpha"),
        _meeting("09:30", "10:00"), _meeting("10:05", "10:30"),  # back to back, splitting the morning
        _row("10:30", "11:40", project="beta"),
        _row("11:40", "12:00", category="email"),            # mail ends the beta stretch
        _row("13:00", "14:00", project="alpha"),
        _row("17:00", "17:20", project="alpha"),             # after 5: 20 more minutes outside
        _row("14:00", "15:00", sensitivity="private"),       # private: never counted as a project
    ]
    p = day_patterns(rows, DAY, DAY + timedelta(days=1), DAY + timedelta(days=1))
    assert p["weekend"] is False
    assert p["projects"] == 2 and p["switches"] == 2  # alpha -> beta -> alpha
    assert p["meetings"] == 2 and p["meeting_runs"] == ["09:30–10:30 (2 meetings)"]
    assert p["morning_meetings"] == 2 and p["morning_free_max_min"] == 90  # 10:30 to 12:00
    assert p["outside_9_5_min"] == 80
    focus = {f["time"]: f for f in p["focus"]}
    assert focus["10:30–11:40"]["on"] == "beta" and focus["10:30–11:40"]["ended_by"] == "comms"
    assert focus["08:00–09:30"]["ended_by"] == "meeting"
    assert focus["13:00–14:00"]["ended_by"] == "break"  # private time is only a break


def test_review_can_take_out_meeting_notes():
    payload = {"entries": [], "meetings": [
        {"key": "Thu 10:00", "topics": ["A", "B"], "mine": ["x"], "waiting": [], "unassigned": []},
        {"key": "Thu 14:00", "topics": ["C"], "mine": [], "waiting": ["y"], "unassigned": []},
    ]}
    out = egress.without(payload, [{"meeting": "Thu 14:00"}, {"meeting": "Thu 10:00", "field": "topics", "value": "B"},
                                   {"meeting": "Thu 10:00", "field": "mine", "value": "not there"}])
    assert out["meetings"] == [{"key": "Thu 10:00", "topics": ["A"], "mine": ["x"], "waiting": [], "unassigned": []}]


def test_parse_keeps_observations_and_todos():
    reply = """<think>hm</think>{"narrative": "N", "themes": ["t"],
      "observations": [{"kind": "open_loop", "text": "Owe a write-up", "evidence": "Fri sync"},
                       {"kind": "weird", "text": "Odd kind", "evidence": ""}, {"text": ""}],
      "next_week": [{"project": "alpha", "todos": [{"text": "Write it up", "why": "Fri sync"}, {"text": ""}]},
                    {"project": "beta", "todos": []}]}"""
    parsed = llm.parse(reply)
    assert [o["kind"] for o in parsed["observations"]] == ["open_loop", "pattern"]
    assert parsed["next_week"] == [{"project": "alpha", "todos": [{"text": "Write it up", "why": "Fri sync"}]}]
    assert "next_week" in llm.WEEK_PROMPT and "next_week" not in llm.MONTH_PROMPT
