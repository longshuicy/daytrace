"""A week or month as JSON, for the desktop app: per-day stats plus totals, built from the same day summaries."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import dayjson, summarize
from .models import iso, parse_iso
from .report import day_bounds
from .store import Store


def week_of(day: date) -> tuple[date, date]:
    """Sunday to Saturday around a day."""
    start = day - timedelta(days=(day.weekday() + 1) % 7)
    return start, start + timedelta(days=6)


def month_of(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    following = (start + timedelta(days=32)).replace(day=1)
    return start, following - timedelta(days=1)


def build(store: Store, first: date, last: date, day_zero: datetime, now: datetime, kind: str = "week") -> dict:
    """Stats for first..last (inclusive). Days after today or before tracking started have no data."""
    today = now.astimezone().date()
    days, spans = [], []
    shipped, biggest, best = [], None, None
    projects = defaultdict(Counter)
    counts = Counter()
    for i in range((last - first).days + 1):
        day = first + timedelta(days=i)
        start, end = day_bounds(day)
        row = {"date": day.isoformat(), "future": day > today, "has_data": False, "active_min": 0, "meeting_min": 0,
               "emails_sent": 0, "focus_min": 0}
        days.append(row)
        if day > today or end <= day_zero:
            continue
        rows = store.events_between(iso(start), iso(end))
        d = dayjson.build(day, rows, {}, {}, day_zero, now)
        s = d["summary"]
        row["has_data"] = bool(s["active_min"] or d["points"])
        if not row["has_data"]:
            continue
        row.update(active_min=s["active_min"], meeting_min=s["counts"]["meeting_min"],
                   emails_sent=s["counts"]["emails_sent"], focus_min=(s["longest"] or {}).get("minutes", 0))
        counts.update({k: v for k, v in s["counts"].items()})
        for p in s["projects"]:
            projects[p["project"]].update({"minutes": p["minutes"], "commits": p["commits"], "prs": p["prs"]})
        h = d["highlights"]
        shipped += [{**x, "date": row["date"]} for x in h["shipped"]]
        big = h["biggest"]
        if big and (biggest is None or big["insertions"] + big["deletions"]
                    > biggest["insertions"] + biggest["deletions"]):
            biggest = {**big, "date": row["date"]}
        if s["longest"] and (best is None or s["longest"]["minutes"] > best["minutes"]):
            best = {**s["longest"], "date": row["date"]}
        spans += _spans(rows, start, end, now)

    data_days = [r for r in days if r["has_data"]]
    active = sum(r["active_min"] for r in days)
    summary = {"active_min": active, "longest": best, "counts": counts}
    return {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "today": today.isoformat(),
        "days": days,
        "elapsed": sum(not r["future"] for r in days),
        "line": dayjson.pill_line(summary) if data_days else "Nothing recorded yet",
        "totals": {
            "active_min": active,
            "avg_active_min": round(active / len(data_days)) if data_days else 0,
            "meeting_min": counts["meeting_min"],
            "meetings": counts["meetings"],
            "emails_sent": counts["emails_sent"],
            "emails_received": counts["emails_received"],
        },
        "focus": best,
        "biggest": biggest,
        "shipped": shipped,
        "working_on": dayjson.working_on(spans)[:8],
        "projects": sorted(({"project": p, "minutes": c["minutes"], "commits": c["commits"], "prs": c["prs"]}
                            for p, c in projects.items()), key=lambda r: (-r["minutes"], r["project"])),
        "sites": dayjson.sites(spans),
        "story": dayjson.story_view(summarize.current_period(store, kind, first, last)),
    }


def _spans(rows, start: datetime, end: datetime, now: datetime) -> list[tuple]:
    """(row, start, end) for activity spans, clipped to the day and to now, as dayjson.build counts them."""
    out = []
    for r in rows:
        if not r["end_at"] or r["category"] in ("inbox", "idle"):
            continue
        s, e = max(parse_iso(r["start_at"]), start), min(parse_iso(r["end_at"]), end, now)
        if e > s:
            out.append((r, s, e))
    return out
