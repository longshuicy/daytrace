"""Group a day's events into episodes: chunks of related work an LLM can label.

Deterministic, and private time is never part of one. Calendar meetings are their own episodes; everything
else clusters per project (or per activity group when there's no project) with gaps under 10 minutes.
"""

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .dayjson import group_of
from .models import iso, parse_iso
from .report import merge, total

GAP = timedelta(minutes=10)
MAX_TITLES = 6
ENTRY_TITLES = 3
MIN_ENTRY_MINUTES = 2
# Traces whose titles say what was done; they come before window titles when picking an entry's titles.
SUBJECTS = ("commit", "pr", "review", "issue", "wiki", "release")


def build(rows: list[sqlite3.Row], start: datetime, end: datetime, now: datetime) -> list[dict]:
    """Episodes for [start, end), counting only time up to now. Received mail is left out."""
    meetings, clusters = [], defaultdict(list)
    for r in rows:
        if r["sensitivity"] == "private" or r["category"] in ("idle", "inbox"):
            continue
        s = max(parse_iso(r["start_at"]), start)
        e = min(parse_iso(r["end_at"]), end, now) if r["end_at"] else s
        if e < s or s >= now:
            continue
        item = (s, e, r)
        if r["kind"] == "plan":
            meetings.append([item])
        else:
            key = ("project", r["project"]) if r["project"] else ("activity", group_of(r["category"], "normal"))
            clusters[key].append(item)

    episodes = [_episode("meeting", None, items) for items in meetings]
    for (kind, name), items in clusters.items():
        items.sort(key=lambda i: i[0])
        run = [items[0]]
        for item in items[1:]:
            if item[0] - max(i[1] for i in run) > GAP:
                episodes.append(_episode(kind, name, run))
                run = []
            run.append(item)
        episodes.append(_episode(kind, name, run))

    episodes.sort(key=lambda ep: ep["start_at"])
    for ep in episodes:
        del ep["_length"]
    return episodes


def _episode(kind: str, name: str | None, items: list) -> dict:
    s0 = min(i[0] for i in items)
    e0 = max(i[1] for i in items)
    spans = [(s, e) for s, e, r in items if r["end_at"]]
    by_category = defaultdict(list)
    counts = Counter()
    titles = Counter()
    subjects = []
    sites = defaultdict(list)
    branches = set()
    for s, e, r in items:
        attrs = json.loads(r["attrs"] or "{}")
        branch = attrs.get("branch")
        if branch and branch not in ("HEAD", "main", "master", "develop"):
            branches.add(branch)
        if r["end_at"]:
            by_category[r["category"]].append((s, e))
        else:
            counts[r["category"]] += 1
        if attrs.get("site"):
            sites[attrs["site"]].append((s, e))
        if r["title"] and r["category"] in SUBJECTS and r["title"] not in subjects:
            subjects.append(r["title"])
        elif r["title"] and not attrs.get("site"):  # page titles stay local; only the site name is used
            titles[r["title"]] += max((e - s).total_seconds(), 60)  # points weigh like a minute
    project = name if kind == "project" else None
    group = name if kind == "activity" else ("coord" if kind == "meeting" else None)
    ident = hashlib.sha1(f"{iso(s0)}|{kind}|{name}".encode()).hexdigest()[:12]
    return {
        "id": ident,
        "kind": kind,
        "project": project,
        "group": group,
        "start_at": iso(s0),
        "end_at": iso(e0),
        "minutes": round(total(spans).total_seconds() / 60),
        "activities": {c: round(total(v).total_seconds() / 60) for c, v in by_category.items()},
        "counts": dict(counts),
        "titles": [t for t, _ in titles.most_common(MAX_TITLES)],
        "subjects": subjects,
        "sites": {site: round(total(v).total_seconds() / 60) for site, v in sites.items()},
        "branches": sorted(branches)[:3],
        "sources": sorted({r["source"] for _, _, r in items}),
        "event_ids": [r["id"] for _, _, r in items],
        "_length": max(e0 - s0, total(merge(spans))),
    }


def details(ep: dict) -> str:
    keys = ("kind", "group", "minutes", "activities", "counts", "titles", "subjects", "sites", "branches", "sources")
    return json.dumps({k: ep.get(k) for k in keys})


def entry_key(ep: dict) -> str:
    if ep["kind"] == "meeting":
        return "meetings"
    return ep["project"] or ep.get("group") or "other"


def entries(eps: list[dict]) -> tuple[list[dict], dict]:
    """One entry per project (or meetings, or kind of activity without a project), in order of first appearance.

    Episodes under two minutes with no traces are only counted in the returned "also" totals. An entry keeps at
    most three titles, commit and PR subjects first; for browsing only site names and minutes, never page titles.
    """
    out, also = {}, {"episodes": 0, "minutes": 0}
    for ep in sorted(eps, key=lambda ep: ep["start_at"]):
        if ep["minutes"] < MIN_ENTRY_MINUTES and not ep.get("counts"):
            also["episodes"] += 1
            also["minutes"] += ep["minutes"]
            continue
        key = entry_key(ep)
        entry = out.setdefault(key, {
            "key": key, "kind": ep["kind"], "project": ep.get("project"), "group": ep.get("group"),
            "minutes": 0, "times": [], "activities": Counter(), "counts": Counter(), "_subjects": [],
            "_titles": [], "sites": Counter(), "branches": set(),
        })
        entry["minutes"] += ep["minutes"]
        entry["times"].append((ep["start_at"], ep["end_at"]))
        entry["activities"].update(ep.get("activities") or {})
        entry["counts"].update(ep.get("counts") or {})
        entry["_subjects"] += [t for t in ep.get("subjects") or [] if t not in entry["_subjects"]]
        entry["_titles"] += [t for t in ep.get("titles") or [] if t not in entry["_titles"]]
        entry["sites"].update(ep.get("sites") or {})
        entry["branches"].update(ep.get("branches") or [])
    result = []
    for entry in out.values():
        limit = MAX_TITLES if entry["kind"] == "meeting" else ENTRY_TITLES  # meeting subjects are each distinct
        titles = (entry.pop("_subjects") + [t for t in entry.pop("_titles")])
        entry["titles"] = list(dict.fromkeys(titles))[:limit]
        entry["activities"] = dict(entry["activities"])
        entry["counts"] = dict(entry["counts"])
        entry["sites"] = dict(entry["sites"].most_common(5))
        entry["branches"] = sorted(entry["branches"])[:3]
        result.append(entry)
    return result, also
