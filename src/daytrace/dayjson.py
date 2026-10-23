"""One day as JSON, for the desktop app. All grouping and stats happen here, not in the UI."""

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .models import parse_iso
from .report import day_bounds, merge, total

# Display groups, in fixed palette order. Categories not listed fall into "other".
GROUPS = [
    ("coord", "Meetings & mail", {"meeting", "email", "chat"}),
    ("code", "Coding", {"code", "terminal", "commit", "push"}),
    ("ai", "AI pairing", {"ai"}),
    ("review", "Review & issues", {"pr", "review", "issue", "release", "repo"}),
    ("writing", "Docs & wiki", {"docs", "wiki"}),
    ("browse", "Browsing", {"browse"}),
]
_GROUP_OF = {cat: key for key, _, cats in GROUPS for cat in cats}
BLOCK_GAP = timedelta(minutes=1)
STRETCH_GAP = timedelta(minutes=5)


def group_of(category: str, sensitivity: str) -> str:
    if sensitivity == "personal":
        return "nonwork"
    if sensitivity == "unknown":
        return "unknown"
    return _GROUP_OF.get(category, "other")


def _local(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


def _minutes(td: timedelta) -> int:
    return round(td.total_seconds() / 60)


def build(day: date, rows: list[sqlite3.Row], statuses: dict[str, dict], counts: dict[str, int],
          day_zero: datetime, now: datetime, story: dict | None = None, episodes: list[dict] = ()) -> dict:
    start, end = day_bounds(day)
    spans, points, idle = [], [], []
    received = 0
    for r in rows:
        if r["category"] == "inbox":  # received mail is context, counted but not drawn
            received += r["sensitivity"] == "work"
            continue
        if r["end_at"]:
            s, e = max(parse_iso(r["start_at"]), start), min(parse_iso(r["end_at"]), end)
            if e <= s:
                continue
            if r["category"] == "idle":
                idle.append((s, e))
            else:
                spans.append((r, s, e))
        else:
            points.append(r)

    blocks = _blocks(spans)  # drawn in full, including meetings later today
    # Stats only count what has happened: scheduled time after now is not time spent.
    spans = [(r, s, min(e, now)) for r, s, e in spans if min(e, now) > s]
    work = [(s, e) for r, s, e in spans if r["sensitivity"] == "work"]
    by_group, by_project = defaultdict(list), defaultdict(list)
    for r, s, e in spans:
        by_group[group_of(r["category"], r["sensitivity"])].append((s, e))
        if r["sensitivity"] == "work" and r["project"]:
            by_project[r["project"]].append((s, e))

    point_counts = Counter(p["category"] for p in points if p["sensitivity"] == "work")
    project_points = defaultdict(Counter)
    for p in points:
        if p["sensitivity"] == "work" and p["project"]:
            project_points[p["project"]][p["category"]] += 1

    projects = sorted(set(by_project) | set(project_points),
                      key=lambda p: (-total(by_project.get(p, [])), p))
    longest = _longest_stretch(spans)
    # Meetings come from the calendar; Zoom windows seen by ActivityWatch would double count.
    calendar = [(s, e) for r, s, e in spans if r["kind"] == "plan" and r["sensitivity"] == "work"]
    active = [(s, e) for _, s, e in spans] + [(parse_iso(p["start_at"]),) * 2 for p in points]

    summary = {
        "first": _local(min((s for s, _ in active), default=None)),
        "last": _local(max((e for _, e in active), default=None)),
        "work_min": _minutes(total(work)),
        "nonwork_min": _minutes(total(by_group.get("nonwork", []))),
        "unknown_min": _minutes(total(by_group.get("unknown", []))),
        "idle_min": _minutes(total(idle)),
        "groups": {k: _minutes(total(v)) for k, v in by_group.items()},
        "projects": [{
            "project": p,
            "minutes": _minutes(total(by_project.get(p, []))),
            "commits": project_points[p]["commit"],
            "prs": project_points[p]["pr"],
            "reviews": project_points[p]["review"],
        } for p in projects],
        "longest": longest,
        "counts": {
            "commits": point_counts["commit"], "prs": point_counts["pr"],
            "reviews": point_counts["review"], "pushes": point_counts["push"],
            "wiki": point_counts["wiki"], "issues": point_counts["issue"],
            "meetings": len(calendar),
            "meeting_min": _minutes(total(calendar)),
            "emails_sent": point_counts["email"], "emails_received": received,
        },
    }
    return {
        "date": day.isoformat(),
        "start": _local(start),
        "end": _local(end),
        "now": _local(now),
        "day_zero": _local(day_zero),
        "before_tracking": end <= day_zero,
        "groups": [{"key": k, "label": label} for k, label, _ in GROUPS],
        "sources": [{"name": name, "events": counts.get(name, 0), **st} for name, st in sorted(statuses.items())],
        "summary": summary,
        "line": pill_line(summary),
        "blocks": blocks,
        "points": [{
            "at": _local(parse_iso(p["start_at"])),
            "group": group_of(p["category"], p["sensitivity"]),
            "category": p["category"], "project": p["project"], "sensitivity": p["sensitivity"],
            "source": p["source"], "title": p["title"], "url": p["url"],
        } for p in points],
        "idle": [{"start": _local(s), "end": _local(e)} for s, e in merge(idle)],
        "story": _story(story),
        "episodes": [{
            "id": ep["id"], "start": _local(parse_iso(ep["start_at"])), "end": _local(parse_iso(ep["end_at"])),
            "kind": ep.get("kind"), "project": ep.get("project"), "group": ep.get("group") or "code",
            "minutes": ep.get("minutes", 0), "label": ep.get("label"), "counts": ep.get("counts", {}),
        } for ep in episodes],
    }


def _story(row: dict | None) -> dict | None:
    if not row:
        return None
    stats = row["stats"]
    return {
        "narrative": row["narrative"], "themes": stats.get("themes", []), "model": row["model"],
        "generated_at": _local(parse_iso(row["generated_at"])), "dry_run": bool(stats.get("dry_run")),
        "audit": stats.get("audit"),
    }


def _blocks(spans: list) -> list[dict]:
    """Merge adjacent spans of the same kind; keep the top titles by time for work blocks."""
    keyed = defaultdict(list)
    for r, s, e in spans:
        key = (r["source"], r["category"], r["project"], r["sensitivity"], r["evidence"])
        keyed[key].append((s, e, r["title"], r["url"]))
    blocks = []
    for (source, category, project, sensitivity, evidence), items in keyed.items():
        items.sort(key=lambda i: i[0])
        current = None
        for s, e, title, url in items:
            if current and s <= current["_end"] + BLOCK_GAP:
                current["_end"] = max(current["_end"], e)
            else:
                current = {"_start": s, "_end": e, "_titles": Counter(), "_url": None}
                blocks.append((current, source, category, project, sensitivity, evidence))
            current["_url"] = current["_url"] or url
            if title:
                current["_titles"][title] += (e - s).total_seconds()
    out = []
    for b, source, category, project, sensitivity, evidence in blocks:
        out.append({
            "start": _local(b["_start"]), "end": _local(b["_end"]),
            "minutes": round((b["_end"] - b["_start"]).total_seconds() / 60, 1),
            "group": group_of(category, sensitivity), "category": category, "project": project,
            "sensitivity": sensitivity, "evidence": evidence, "source": source,
            "titles": [t for t, _ in b["_titles"].most_common(3)],
            "url": b["_url"] if evidence == "scheduled" else None,
        })
    return sorted(out, key=lambda b: b["start"])


def _longest_stretch(spans: list) -> dict | None:
    """Longest run of work on one project outside meetings and mail, allowing 5-minute gaps."""
    per_project = defaultdict(list)
    for r, s, e in spans:
        if r["sensitivity"] == "work" and r["project"] and group_of(r["category"], "work") != "coord":
            per_project[r["project"]].append((s, e))
    best = None
    for project, intervals in per_project.items():
        for s, e in merge(intervals, gap=STRETCH_GAP):
            if best is None or e - s > best[2] - best[1]:
                best = (project, s, e)
    if best is None:
        return None
    project, s, e = best
    return {"project": project, "minutes": _minutes(e - s), "start": _local(s), "end": _local(e)}


def fmt_minutes(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def pill_line(summary: dict) -> str:
    parts = []
    if summary["work_min"]:
        parts.append(f"{fmt_minutes(summary['work_min'])} work")
    if summary["longest"] and summary["longest"]["minutes"] >= 10:
        parts.append(f"longest {fmt_minutes(summary['longest']['minutes'])} on {summary['longest']['project']}")
    c = summary["counts"]
    for key, word in (("meetings", "meeting"), ("commits", "commit"), ("prs", "PR"), ("wiki", "wiki edit")):
        if c[key]:
            parts.append(f"{c[key]} {word}{'s' * (c[key] != 1)}")
    return " · ".join(parts) or "Nothing recorded yet today"


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
