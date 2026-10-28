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
SESSIONS = ("claude_code", "cursor")  # agent chats: they run on while you look at something else
HANDS_ON = ("code", "terminal")       # on screen during a session, these are taken as working in it
NO_BORROW = ("meeting", "email", "chat", "private")  # count toward a project only when their own title names it
BORROW_MAX = timedelta(minutes=10)   # untagged stretches up to this long take the project on both sides of them
DETOUR = timedelta(minutes=10)       # time on something else that doesn't end a focus stretch
GLANCE = timedelta(seconds=30)       # a return to the thing shorter than this doesn't carry its stretch on
FOCUS_SHARE = 0.6                    # a focus stretch spends at least this much of its time on its thing
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
    # Away from the keyboard in a meeting is still the meeting, so it isn't shaded as away.
    in_meetings = merge([(s, e) for r, s, e in spans if r["source"] == "outlook_calendar"])
    away = [iv for idle_iv in merge(idle) for iv in _cut(idle_iv, in_meetings)]
    loose_notes = attach_notes(blocks, list(notes))
    # Stats only count what has happened: scheduled time after now is not time spent.
    spans = [(r, s, min(e, now)) for r, s, e in spans if min(e, now) > s]
    private = [(s, e) for r, s, e in spans if r["sensitivity"] == "private"]
    by_group = defaultdict(list)
    for r, s, e in spans:
        by_group[group_of(r["category"], r["sensitivity"])].append((s, e))

    point_counts = Counter(p["category"] for p in points if p["sensitivity"] != "private")
    project_points = defaultdict(Counter)
    for p in points:
        if p["project"]:
            project_points[p["project"]][p["category"]] += 1

    by_project, unclaimed = project_time(spans)
    zero = {"minutes": timedelta(0), "inferred": timedelta(0)}
    project_rows = [{
        "project": p,
        "minutes": _minutes(by_project.get(p, zero)["minutes"]),
        "inferred_min": _minutes(by_project.get(p, zero)["inferred"]),
        "commits": project_points[p]["commit"],
        "prs": project_points[p]["pr"],
        "reviews": project_points[p]["review"],
    } for p in set(by_project) | set(project_points)]
    # Private time is no project's and nobody's business, so it is left out rather than shown as Other.
    other = {why: _minutes(td) for why, td in unclaimed.items() if why != "private" and _minutes(td)}
    if other:
        project_rows.append({"project": None, "minutes": _minutes(sum(
            (td for why, td in unclaimed.items() if why != "private"), timedelta(0))), "inferred_min": 0,
            "commits": 0, "prs": 0, "reviews": 0, "parts": other})
    project_rows.sort(key=lambda r: (-r["minutes"], r["project"] is None, r["project"] or ""))
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
        "projects": project_rows,
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
        "idle": [{"start": _local(s), "end": _local(e)} for s, e in away],
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


def project_time(spans: list) -> tuple[dict[str, dict], dict[str, timedelta]]:
    """Active time per project, each moment counted once, and the rest by why no project claims it.
    Returns ({project: {"minutes": timedelta, "inferred": timedelta}}, {why: timedelta}), why being "meeting",
    "comms" (mail and chat), "private" or "untagged"; the two together add up to active time.

    A calendar meeting owns its time (its project, else none). Otherwise what is on screen decides: a window
    with a project counts for it. An untagged editor, terminal or Claude window during an agent session (Claude
    Code, Cursor) counts for the session's project, inferred. Other untagged activity of 10 minutes or less
    between two stretches of the same project counts for it, inferred, except meetings, mail, chat and private
    time. Moments shared by several projects are split evenly.
    """
    segments = [[a, b, _owners(rows)] for a, b, rows in _moments(spans)]

    for j, seg in enumerate(segments):
        if seg[2] is not None:
            continue
        k = j
        while k + 1 < len(segments) and segments[k + 1][2] is None:
            k += 1
        before, after = _sole(segments[j - 1] if j else None), _sole(segments[k + 1] if k + 1 < len(segments) else None)
        near = (j > 0 and k + 1 < len(segments) and seg[0] - segments[j - 1][1] <= STRETCH_GAP
                and segments[k + 1][0] - segments[k][1] <= STRETCH_GAP)
        borrowed = before if near and before and before == after and segments[k][1] - seg[0] <= BORROW_MAX else None
        for run in segments[j:k + 1]:
            run[2] = [(borrowed, borrowed is not None)] if borrowed else [("untagged", None)]

    out = defaultdict(lambda: {"minutes": timedelta(0), "inferred": timedelta(0)})
    unclaimed = defaultdict(timedelta)
    for a, b, owners in segments:
        for owner, inferred in owners:
            share = (b - a) / len(owners)
            if inferred is None:
                unclaimed[owner] += share
                continue
            out[owner]["minutes"] += share
            if inferred:
                out[owner]["inferred"] += share
    return dict(out), dict(unclaimed)


def _moments(spans: list):
    """(start, end, rows) for each stretch of time in which the same spans are running, in time order."""
    edges = sorted({t for _, s, e in spans for t in (s, e)})
    order = sorted(spans, key=lambda x: x[1])
    cover, i = [], 0
    for a, b in zip(edges, edges[1:]):
        while i < len(order) and order[i][1] <= a:
            cover.append(order[i])
            i += 1
        cover = [x for x in cover if x[2] > a]
        if cover:
            yield a, b, [r for r, _, _ in cover]


def _owners(rows: list) -> list[tuple[str, bool | None]] | None:
    """Who owns a moment covered by these rows, as shares: (project, inferred), or (why, None) when no project
    does. None when its neighbors decide."""
    def split(rs: list, inferred: bool, why: str) -> list[tuple[str, bool | None]]:
        return list(dict.fromkeys((r["project"], inferred) if r["project"] else
                                  ("private" if r["sensitivity"] == "private" else why, None) for r in rs))

    plans = [r for r in rows if r["kind"] == "plan"]
    if plans:
        return split(plans, False, "meeting")
    shown = [r for r in rows if r["source"] not in SESSIONS]
    sessions = [r for r in rows if r["source"] in SESSIONS and r["project"]]
    if any(r["project"] for r in shown):
        return split([r for r in shown if r["project"]], False, "untagged")
    if not shown:
        return split(sessions, False, "untagged") if sessions else [_unclaimed(rows)]
    if any(r["sensitivity"] == "private" or r["category"] in NO_BORROW for r in shown):
        return [_unclaimed(shown)]
    if sessions and any(r["category"] in HANDS_ON for r in shown):
        return split(sessions, True, "untagged")
    return None


def _unclaimed(rows: list) -> tuple[str, None]:
    """Why no project claims a moment: private time first, then meetings, then mail and chat."""
    groups = {group_of(r["category"], r["sensitivity"]) for r in rows}
    return (next((g for g in ("private", "meeting", "comms") if g in groups), "untagged"), None)


def _sole(segment: list | None) -> str | None:
    """The one project a segment went to, if it went to exactly one."""
    if not segment or not segment[2] or len(segment[2]) != 1 or segment[2][0][1] is None:
        return None
    return segment[2][0][0]


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
    """Longest stretch of time on one thing (see focus_stretches)."""
    stretches = focus_stretches(spans, idle)
    if not stretches:
        return None
    best = max(stretches, key=lambda f: f["_length"])
    return {k: best[k] for k in ("label", "kind", "minutes", "start", "end")}


def focus_stretches(spans: list, idle: list) -> list[dict]:
    """Stretches of time on one thing, in time order, each with what ended it: "meeting", "comms" (mail or chat),
    "break" (private time or away), "switch" (moved on to something else) or "stopped" (nothing tracked).

    The thing is what is on screen, as project_time reads it: a project, else a document, else a site, else an
    app; an agent session counts only while its editor, terminal or Claude window is in front. Stretches don't
    overlap: one runs from its first to its last moment on its thing, through detours to anything else of up to
    10 minutes of activity (a lookup, a glance at chat), and spends at least 60% of its time on the thing; returns
    to it under 30 seconds don't carry it on. Hopping between many things is no focus at all. Meetings, mail,
    chat or private time of 2 minutes or more, and more than 5 minutes away from the keyboard, cut a stretch.
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
    moments = []
    for a, b, rows in _moments(work):
        key = _subject(rows)
        if moments and moments[-1][2] == key and a - moments[-1][1] < STRETCH_GAP:
            moments[-1][1] = b
        else:
            moments.append([a, b, key])
    out = []
    for s0, e0 in segments:
        blocks = [(max(a, s0), min(b, e0), key) for a, b, key in moments if a < e0 and b > s0]
        runs, i = [], 0
        while i < len(blocks):
            start, end, key = blocks[i]
            on, last, detour, j = end - start, i, timedelta(0), i + 1
            while j < len(blocks) and detour <= DETOUR:
                length = blocks[j][1] - blocks[j][0]
                if blocks[j][2] == key and length >= GLANCE:
                    end, last, detour, on = blocks[j][1], j, timedelta(0), on + length
                else:
                    detour += length
                j += 1
            if on >= FOCUS_SHARE * (end - start):
                runs.append((start, end, key))
                i = last + 1
            else:
                i += 1
        enders = sorted((bs, why) for bs, be, why in breaks if bs <= e0 < be)
        for s, e, (kind, label) in runs:
            ended = (enders[0][1] if enders else "stopped") if e0 - e <= STRETCH_GAP else "switch"
            out.append({"label": label, "kind": kind, "minutes": _minutes(e - s), "start": _local(s),
                        "end": _local(e), "ended_by": ended, "_length": e - s})
    return sorted(out, key=lambda f: f["start"])


def _subject(rows: list) -> tuple[str, str]:
    """What a moment of work is on: what is on screen, with an agent session only behind its own window."""
    shown = [r for r in rows if r["source"] not in SESSIONS]
    sessions = [r for r in rows if r["source"] in SESSIONS and r["project"]]
    tagged = [r for r in shown if r["project"]]
    if tagged:
        return ("project", tagged[0]["project"])
    if sessions and (not shown or any(r["category"] in HANDS_ON for r in shown)):
        return ("project", sessions[0]["project"])
    return _focus_key(shown[0] if shown else rows[0])


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
    if page_name(r, attrs):
        return ("site", page_name(r, attrs))
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
