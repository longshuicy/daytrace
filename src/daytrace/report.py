"""A plain-text view of one day. Overlap-aware: time per category is a union, not a sum."""

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from .models import parse_iso

Interval = tuple[datetime, datetime]


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min).astimezone()
    return start, start + timedelta(days=1)


def merge(intervals: list[Interval], gap: timedelta = timedelta(0)) -> list[Interval]:
    merged: list[list[datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def total(intervals: list[Interval]) -> timedelta:
    return sum((e - s for s, e in merge(intervals)), timedelta(0))


def summarize(rows: list[sqlite3.Row], start: datetime, end: datetime) -> dict:
    spans, points = [], []
    for r in rows:
        if r["end_at"]:
            s, e = max(parse_iso(r["start_at"]), start), min(parse_iso(r["end_at"]), end)
            if e > s:
                spans.append((r, s, e))
        else:
            points.append(r)

    by_category, by_sensitivity, by_project = defaultdict(list), defaultdict(list), defaultdict(list)
    for r, s, e in spans:
        by_category[r["category"]].append((s, e))
        if r["category"] != "idle":
            by_sensitivity[r["sensitivity"]].append((s, e))
            if r["project"]:
                by_project[r["project"]].append((s, e))

    active = [(s, e) for r, s, e in spans if r["category"] != "idle"]
    active += [(parse_iso(p["start_at"]),) * 2 for p in points]
    commits = Counter(p["project"] or "(non-work)" for p in points if p["category"] == "commit")
    return {
        "categories": {k: total(v) for k, v in by_category.items()},
        "sensitivity": {k: total(v) for k, v in by_sensitivity.items()},
        "projects": {k: total(v) for k, v in by_project.items()},
        "commits": commits,
        "first": min((s for s, _ in active), default=None),
        "last": max((e for _, e in active), default=None),
        "timeline": _timeline(spans, points),
    }


def _timeline(spans: list, points: list) -> list[tuple[datetime, datetime | None, str]]:
    groups = defaultdict(list)
    for r, s, e in spans:
        if r["category"] == "idle":
            continue
        groups[(r["source"], r["category"], r["project"], r["sensitivity"])].append((s, e))
    lines = []
    for (source, category, project, sensitivity), intervals in groups.items():
        for s, e in merge(intervals, gap=timedelta(minutes=2)):
            if e - s >= timedelta(minutes=2):
                lines.append((s, e, _label(source, category, project, sensitivity)))
    for p in points:
        label = _label(p["source"], p["category"], p["project"], p["sensitivity"])
        if p["title"]:
            label += f'  "{p["title"]}"'
        lines.append((parse_iso(p["start_at"]), None, label))
    return sorted(lines, key=lambda line: line[0])


def _label(source: str, category: str, project: str | None, sensitivity: str) -> str:
    where = project or ("non-work" if sensitivity == "personal" else sensitivity)
    return f"{category:<9} {where:<14} {source}"


def fmt(td: timedelta) -> str:
    minutes = round(td.total_seconds() / 60)
    return f"{minutes // 60}h {minutes % 60:02d}m"


def clock(dt: datetime) -> str:
    return dt.astimezone().strftime("%H:%M")


def render(day: date, rows: list[sqlite3.Row], statuses: dict[str, str], day_zero: datetime) -> str:
    start, end = day_bounds(day)
    s = summarize(rows, start, end)
    out = [f"{day:%A, %d %B %Y}", f"tracking since {day_zero.astimezone():%Y-%m-%d %H:%M}"]

    status_bits = []
    for source, raw in sorted(statuses.items()):
        st = json.loads(raw)
        if st.get("configured", True):
            status_bits.append(f"{source} {'ok' if st['ok'] else 'FAILED'}")
    out.append("sources: " + (" · ".join(status_bits) or "never synced"))

    if not rows:
        out.append("\nNo traces for this day.")
        return "\n".join(out)

    if s["first"]:
        out.append(f"active from {clock(s['first'])} to {clock(s['last'])}")

    out.append("\nTime by category (overlapping, so these don't add up)")
    longest = max(s["categories"].values(), default=timedelta(0)) or timedelta(minutes=1)
    for cat, td in sorted(s["categories"].items(), key=lambda kv: -kv[1]):
        bar = "█" * max(1, round(20 * td / longest))
        out.append(f"  {cat:<10} {fmt(td):>8}  {bar}")

    sens = s["sensitivity"]
    out.append("\n" + " · ".join(f"{k} {fmt(sens[k])}" for k in ("work", "personal", "unknown") if k in sens))

    if s["projects"] or s["commits"]:
        out.append("\nProjects")
        for project in sorted(set(s["projects"]) | set(s["commits"]),
                              key=lambda p: -s["projects"].get(p, timedelta(0))):
            n = s["commits"].get(project, 0)
            commits = f" · {n} commit{'s' * (n != 1)}" if n else ""
            out.append(f"  {project:<14} {fmt(s['projects'].get(project, timedelta(0))):>8}{commits}")

    out.append("\nTimeline")
    for t0, t1, label in s["timeline"]:
        when = f"{clock(t0)}–{clock(t1)}" if t1 else f"{clock(t0)}      "
        out.append(f"  {when}  {label}")
    return "\n".join(out)
