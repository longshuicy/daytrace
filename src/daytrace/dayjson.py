"""One day as JSON, for the desktop app. All grouping and stats happen here, not in the UI."""

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .adapters.granola import match
from .models import parse_iso
from .report import day_bounds, merge, total

# Display groups, in fixed palette order. Categories not listed fall into "other"; private time is "private".
GROUPS = [
    ("meeting", "Meetings", {"meeting"}),
    ("comms", "Mail & chat", {"email", "chat"}),
    ("code", "Coding", {"code", "terminal", "commit", "push"}),
    ("review", "Review & issues", {"pr", "review", "issue", "release", "repo"}),
    ("writing", "Docs & wiki", {"docs", "wiki"}),
    ("browse", "Browsing", {"browse"}),
]
_GROUP_OF = {cat: key for key, _, cats in GROUPS for cat in cats}
INTERRUPTS = ("meeting", "comms")  # groups that break a focus stretch
BLOCK_GAP = timedelta(minutes=1)
STRETCH_GAP = timedelta(minutes=5)
INTERRUPTION = timedelta(minutes=2)  # meetings, mail, chat or private time at least this long break focus
AWAY = timedelta(minutes=5)          # so does being away from the keyboard longer than this
MIN_SITE = timedelta(minutes=1)
MIN_BLOCK = timedelta(seconds=30)  # shorter blocks would read "0m"; they still count toward the totals
MAX_SITES = 8
SHIPPED = ("pr", "release", "wiki", "issue")  # traces that put something out into the world
TRUNKS = ("main", "master", "develop", "HEAD")  # listed last in "Working on": they don't name a thread of work


def group_of(category: str, sensitivity: str) -> str:
    if sensitivity == "private":
        return "private"
    return _GROUP_OF.get(category, "other")


def page_name(r, attrs: dict) -> str | None:
    """A browser page's site, or its title when the title names no site."""
    if attrs.get("site"):
        return attrs["site"]
    return r["title"] if r["category"] == "browse" and attrs.get("app") else None


def _local(dt: datetime | None) -> str | None:
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


def _minutes(td: timedelta) -> int:
    return round(td.total_seconds() / 60)


def build(day: date, rows: list[sqlite3.Row], statuses: dict[str, dict], counts: dict[str, int],
          day_zero: datetime, now: datetime, notes: list[dict] = ()) -> dict:
    start, end = day_bounds(day)
    spans, points, idle = [], [], []
    received = 0
    for r in rows:
        if r["category"] == "inbox":  # received mail is context, counted but not drawn
            received += r["sensitivity"] != "private"
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
    loose_notes = attach_notes(blocks, list(notes))
    # Stats only count what has happened: scheduled time after now is not time spent.
    spans = [(r, s, min(e, now)) for r, s, e in spans if min(e, now) > s]
    private = [(s, e) for r, s, e in spans if r["sensitivity"] == "private"]
    by_group, by_project = defaultdict(list), defaultdict(list)
    for r, s, e in spans:
        by_group[group_of(r["category"], r["sensitivity"])].append((s, e))
        if r["project"]:
            by_project[r["project"]].append((s, e))

    point_counts = Counter(p["category"] for p in points if p["sensitivity"] != "private")
    project_points = defaultdict(Counter)
    for p in points:
        if p["project"]:
            project_points[p["project"]][p["category"]] += 1

    projects = sorted(set(by_project) | set(project_points),
                      key=lambda p: (-total(by_project.get(p, [])), p))
    longest = _longest_focus(spans, idle)
    # Meetings come from the calendar; Zoom windows seen by ActivityWatch would double count.
    calendar = [(s, e) for r, s, e in spans if r["kind"] == "plan" and r["sensitivity"] != "private"]
    active = [(s, e) for _, s, e in spans] + [(parse_iso(p["start_at"]),) * 2 for p in points]

    summary = {
        "first": _local(min((s for s, _ in active), default=None)),
        "last": _local(max((e for _, e in active), default=None)),
        "active_min": _minutes(total([(s, e) for _, s, e in spans])),
        "private_min": _minutes(total(private)),
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
        } for p in points] + loose_notes,
        "idle": [{"start": _local(s), "end": _local(e)} for s, e in merge(idle)],
        "sites": sites(spans),
        "highlights": _highlights(points, spans, summary),
    }


def _highlights(points: list, spans: list, summary: dict) -> dict:
    """What the day produced, from data already stored: what went out, commit totals, the branches
    Claude Code sessions were on, and the longest focus. Meetings and mail have their own tiles."""
    shown = [p for p in points if p["sensitivity"] != "private"]
    shipped = [{"title": p["title"], "url": p["url"], "project": p["project"], "category": p["category"]}
               for p in shown if p["category"] in SHIPPED and p["title"]]

    commits = None
    for p in points:
        private = p["sensitivity"] == "private"
        if (p["source"] != "git") if private else (p["category"] != "commit"):
            continue
        commits = commits or {"count": 0, "private": 0, "files": 0, "insertions": 0, "deletions": 0, "projects": []}
        commits["count"] += 1
        if private:  # stored without project or line counts
            commits["private"] += 1
            continue
        a = json.loads(p["attrs"] or "{}")
        for k in ("files", "insertions", "deletions"):
            commits[k] += a.get(k) or 0
        if p["project"] and p["project"] not in commits["projects"]:
            commits["projects"].append(p["project"])

    return {
        "shipped": shipped,
        "commits": commits,
        "working_on": working_on(spans, points),
        "focus": summary["longest"],
    }


def working_on(spans: list, points: list) -> list[dict]:
    """Branches Claude Code sessions were on or commits landed on, with time and commits per
    (project, branch): feature branches before trunks, then most time, then most commits."""
    branches, commits = defaultdict(list), Counter()
    for r, s, e in spans:
        branch = json.loads(r["attrs"] or "{}").get("branch") if r["source"] == "claude_code" else None
        if branch and r["sensitivity"] != "private":
            branches[(r["project"], branch)].append((s, e))
    for p in points:
        branch = json.loads(p["attrs"] or "{}").get("branch") if p["category"] == "commit" else None
        if branch and p["sensitivity"] != "private":
            commits[(p["project"], branch)] += 1
    rows = [{"project": p, "branch": b, "minutes": _minutes(total(branches.get((p, b), []))), "commits": commits[(p, b)],
             "trunk": b in TRUNKS}
            for p, b in set(branches) | set(commits)]
    return sorted((w for w in rows if w["minutes"] or w["commits"]),
                  key=lambda w: (w["trunk"], -w["minutes"], -w["commits"], w["project"] or "", w["branch"]))


def sites(spans: list) -> list[dict]:
    """Browser time per site, named from tab titles; a page whose title names no site is listed by its title
    ("page": true). Sites and pages under a minute are folded into "Other pages"."""
    by_site, pages = defaultdict(list), set()
    for r, s, e in spans:
        attrs = json.loads(r["attrs"] or "{}")
        name = page_name(r, attrs) if r["sensitivity"] != "private" else None
        if name:
            by_site[name].append((s, e))
            if not attrs.get("site"):
                pages.add(name)
    named = sorted(((site, total(v)) for site, v in by_site.items()), key=lambda kv: -kv[1])
    shown = [(site, td) for site, td in named if td >= MIN_SITE][:MAX_SITES]
    rest = [iv for site, v in by_site.items() if site not in dict(shown) for iv in v]
    out = [{"site": site, "minutes": _minutes(td), "page": site in pages} for site, td in shown]
    if rest and total(rest) >= MIN_SITE:
        out.append({"site": None, "minutes": _minutes(total(rest)), "page": False})
    return out


def story_view(row: dict | None) -> dict | None:
    if not row:
        return None
    stats = row["stats"]
    return {
        "narrative": row["narrative"], "themes": stats.get("themes", []), "model": row["model"],
        "observations": stats.get("observations", []), "next_week": stats.get("next_week", []),
        "generated_at": _local(parse_iso(row["generated_at"])), "dry_run": bool(stats.get("dry_run")),
        "audit": stats.get("audit"), "entries": stats.get("entries", 0),
        "request": stats.get("request") if stats.get("dry_run") else None,
    }


def _blocks(spans: list) -> list[dict]:
    """Merge adjacent spans of the same kind; keep the top titles by time."""
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
        if b["_end"] - b["_start"] < MIN_BLOCK:
            continue
        out.append({
            "start": _local(b["_start"]), "end": _local(b["_end"]),
            "minutes": round((b["_end"] - b["_start"]).total_seconds() / 60, 1),
            "group": group_of(category, sensitivity), "category": category, "project": project,
            "sensitivity": sensitivity, "evidence": evidence, "source": source,
            "titles": [t for t, _ in b["_titles"].most_common(3)],
            "url": b["_url"] if source == "outlook_calendar" else None,
        })
    return sorted(out, key=lambda b: b["start"])


def attach_notes(blocks: list[dict], notes: list[dict]) -> list[dict]:
    """Give each calendar block the meeting notes taken in it; return the rest as points of their own."""
    meetings = [b for b in blocks if b["source"] == "outlook_calendar"]
    for b in meetings:
        b["notes"] = []
    loose = []
    for n, i in zip(notes, match(notes, [(parse_iso(b["start"]), parse_iso(b["end"])) for b in meetings])):
        note = {"id": n["id"], "title": n["title"], "url": n["url"], "path": n["path"]}
        if i is None:
            loose.append({"at": _local(parse_iso(n["meeting_at"])), "group": "meeting", "category": "notes",
                          "project": None, "sensitivity": "normal", "source": "granola", **note})
        else:
            meetings[i]["notes"].append(note)
    return loose


def _longest_focus(spans: list, idle: list) -> dict | None:
    """Longest stretch of activity without interruptions, labeled by what took most of it (see focus_stretches)."""
    stretches = focus_stretches(spans, idle)
    if not stretches:
        return None
    best = max(stretches, key=lambda f: f["_length"])
    return {k: best[k] for k in ("label", "kind", "minutes", "start", "end")}


def focus_stretches(spans: list, idle: list) -> list[dict]:
    """Stretches of activity without interruptions, in time order, each labeled by what took most of it and with
    what ended it: "meeting", "comms" (mail or chat), "break" (private time or away) or "stopped" (nothing tracked).

    Any non-private activity outside meetings and mail counts (code, docs, PDFs, the browser), with gaps under
    5 minutes joined. Meetings, mail, chat or private time of 2 minutes or more, and more than 5 minutes away
    from the keyboard, cut a stretch. The label is the project with the most time in it, else a document, else
    a site, else an app, decided per span.
    """
    work, breaks = [], [(s, e, "break") for s, e in idle if e - s > AWAY]
    for r, s, e in spans:
        if r["sensitivity"] == "private" or group_of(r["category"], r["sensitivity"]) in INTERRUPTS:
            if e - s >= INTERRUPTION:
                why = "break" if r["sensitivity"] == "private" else group_of(r["category"], r["sensitivity"])
                breaks.append((s, e, why))
        else:
            work.append((r, s, e))
    merged = merge([(s, e) for s, e, _ in breaks])
    segments = [seg for iv in merge([(s, e) for _, s, e in work], gap=STRETCH_GAP) for seg in _cut(iv, merged)]
    out = []
    for s0, e0 in sorted(segments):
        weight = defaultdict(timedelta)
        for r, s, e in work:
            a, b = max(s, s0), min(e, e0)
            if b > a:
                weight[_focus_key(r)] += b - a
        if not weight:
            continue
        kind, label = max(weight, key=weight.get)
        enders = sorted((bs, why) for bs, be, why in breaks if bs <= e0 < be)
        out.append({"label": label, "kind": kind, "minutes": _minutes(e0 - s0), "start": _local(s0),
                    "end": _local(e0), "ended_by": enders[0][1] if enders else "stopped", "_length": e0 - s0})
    return out


def _cut(interval: tuple, breaks: list) -> list[tuple]:
    """interval minus the (merged, sorted) breaks."""
    s, e = interval
    out = []
    for bs, be in breaks:
        if be <= s or bs >= e:
            continue
        if bs > s:
            out.append((s, bs))
        s = max(s, be)
    if e > s:
        out.append((s, e))
    return out


def _focus_key(r) -> tuple[str, str]:
    attrs = json.loads(r["attrs"] or "{}")
    if r["project"]:
        return ("project", r["project"])
    if r["category"] in ("docs", "wiki") and r["title"]:
        return ("document", r["title"])
    if attrs.get("site"):
        return ("site", attrs["site"])
    return ("app", attrs.get("app") or r["category"])


def fmt_minutes(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def pill_line(summary: dict) -> str:
    parts = []
    if summary["active_min"]:
        parts.append(f"{fmt_minutes(summary['active_min'])} active")
    if summary["longest"] and summary["longest"]["minutes"] >= 10:
        parts.append(f"longest focus {fmt_minutes(summary['longest']['minutes'])} on {summary['longest']['label']}")
    c = summary["counts"]
    for key, word in (("meetings", "meeting"), ("commits", "commit"), ("prs", "PR"), ("wiki", "wiki edit")):
        if c[key]:
            parts.append(f"{c[key]} {word}{'s' * (c[key] != 1)}")
    return " · ".join(parts) or "Nothing recorded yet today"


def dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)
