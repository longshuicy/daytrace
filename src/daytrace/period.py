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
    days, spans, points = [], [], []
    shipped, commits, best = [], None, None
    projects, other_parts = defaultdict(Counter), Counter()
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
            projects[p["project"]].update({"minutes": p["minutes"], "inferred_min": p["inferred_min"],
                                           "commits": p["commits"], "prs": p["prs"]})
            other_parts.update(p.get("parts", {}))
        h = d["highlights"]
        shipped += [{**x, "date": row["date"]} for x in h["shipped"]]
        if h["commits"]:
            commits = commits or {"count": 0, "private": 0, "files": 0, "insertions": 0, "deletions": 0, "projects": []}
            for k in ("count", "private", "files", "insertions", "deletions"):
                commits[k] += h["commits"][k]
            commits["projects"] += [x for x in h["commits"]["projects"] if x not in commits["projects"]]
        if s["longest"] and (best is None or s["longest"]["minutes"] > best["minutes"]):
            best = {**s["longest"], "date": row["date"]}
        spans += _spans(rows, start, end, now)
        points += [r for r in rows if not r["end_at"] and r["category"] == "commit"]

    data_days = [r for r in days if r["has_data"]]
    workdays = [r for r in data_days if date.fromisoformat(r["date"]).weekday() < 5]
    active = sum(r["active_min"] for r in days)
    workday_active = sum(r["active_min"] for r in workdays)
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
            "avg_active_min": round(workday_active / len(workdays)) if workdays else 0,
            "avg_active_min_all": round(active / len(data_days)) if data_days else 0,
            "workdays": len(workdays),
            "data_days": len(data_days),
            "meeting_min": counts["meeting_min"],
            "meetings": counts["meetings"],
            "emails_sent": counts["emails_sent"],
            "emails_received": counts["emails_received"],
        },
        "focus": best,
        "commits": commits,
        "shipped": shipped,
        "working_on": dayjson.working_on(spans, points),
        "projects": sorted(({"project": p, "minutes": c["minutes"], "inferred_min": c["inferred_min"],
                             "commits": c["commits"], "prs": c["prs"], **({} if p else {"parts": dict(other_parts)})}
                            for p, c in projects.items()),
                           key=lambda r: (-r["minutes"], r["project"] is None, r["project"] or "")),
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
