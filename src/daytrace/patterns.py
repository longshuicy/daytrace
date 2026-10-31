"""A day's work patterns for week and month requests: switching, meeting shape, focus and hours. Counted here, so
the LLM only has to interpret them. Private time is never part of any of it beyond ending a focus stretch as a
"break"."""

from datetime import datetime, time, timedelta

from .dayjson import INTERRUPTS, focus_stretches, group_of
from .models import parse_iso
from .report import clock, merge, total

WORK_START, WORK_END = time(9), time(17)  # the hours the summary treats as the working day
MORNING_END = time(12)
MEETING_RUN_GAP = timedelta(minutes=10)  # meetings closer than this are back to back
MIN_STAY = timedelta(minutes=3)  # time on a project shorter than this isn't a switch to it
MIN_FOCUS = 25  # minutes; shorter stretches aren't listed


def _at(day: datetime, t: time) -> datetime:
    local = day.astimezone()
    return local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)


def day_patterns(rows, start: datetime, end: datetime, now: datetime, label=lambda s: s) -> dict:
    """rows: the day's events; label cleans a focus label for sending. Times are local "HH:MM"."""
    spans, idle = [], []
    for r in rows:
        if not r["end_at"] or r["category"] == "inbox":
            continue
        s, e = max(parse_iso(r["start_at"]), start), min(parse_iso(r["end_at"]), end, now)
        if e <= s:
            continue
        if r["category"] == "idle":
            idle.append((s, e))
        else:
            spans.append((r, s, e))
    work = [(r, s, e) for r, s, e in spans if r["sensitivity"] != "private"]
    meetings = merge([(s, e) for r, s, e in work if r["kind"] == "plan"])

    # Switches: the order projects were worked on, ignoring visits too short to count.
    visits = []
    for r, s, e in sorted(work, key=lambda x: x[1]):
        if not r["project"] or group_of(r["category"], "normal") in INTERRUPTS:
            continue
        if visits and visits[-1][0] == r["project"]:
            visits[-1][2] = max(visits[-1][2], e)
            visits[-1][3] += e - s
        else:
            visits.append([r["project"], s, e, e - s])
    stays = [v for v in visits if v[3] >= MIN_STAY]
    order = [v[0] for i, v in enumerate(stays) if i == 0 or stays[i - 1][0] != v[0]]

    runs, run = [], []
    for s, e in meetings:
        if run and s - run[-1][1] > MEETING_RUN_GAP:
            runs.append(run)
            run = []
        run.append((s, e))
    if run:
        runs.append(run)

    morning0, morning1 = _at(start, WORK_START), _at(start, MORNING_END)
    in_morning = [(max(s, morning0), min(e, morning1)) for s, e in meetings if s < morning1 and e > morning0]
    edges = [morning0, *[t for iv in in_morning for t in iv], morning1]
    free = max(((edges[i + 1] - edges[i]) for i in range(0, len(edges), 2)), default=morning1 - morning0)

    day0, day1 = _at(start, WORK_START), _at(start, WORK_END)
    active = merge([(s, e) for _, s, e in work])
    outside = total([iv for s, e in active for iv in ((s, min(e, day0)), (max(s, day1), e)) if iv[1] > iv[0]])

    return {
        "weekend": start.astimezone().weekday() >= 5,
        "projects": len({v[0] for v in stays}),
        "switches": max(len(order) - 1, 0),
        "meetings": len(meetings),
        "meeting_runs": [f"{clock(r[0][0])}–{clock(r[-1][1])} ({len(r)} meetings)" for r in runs if len(r) > 1],
        "morning_meetings": len(in_morning),
        "morning_free_max_min": round(free.total_seconds() / 60),
        "focus": [{"time": f"{f['start'][11:16]}–{f['end'][11:16]}", "minutes": f["minutes"], "on": label(f["label"]),
                   "ended_by": f["ended_by"]}
                  for f in focus_stretches(spans, idle) if f["minutes"] >= MIN_FOCUS],
        "outside_9_5_min": round(outside.total_seconds() / 60),
    }
