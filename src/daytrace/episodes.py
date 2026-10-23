"""Group a day's work events into episodes: chunks of related work an LLM can label.

Deterministic and work-only. Calendar meetings are their own episodes; other work clusters per project
(or per activity group when there's no project) with gaps under 10 minutes.
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
MIN_LENGTH = timedelta(minutes=3)
MAX_TITLES = 6


def build(rows: list[sqlite3.Row], start: datetime, end: datetime, now: datetime) -> list[dict]:
    """Episodes for [start, end), counting only time up to now. Received mail is left out."""
    meetings, clusters = [], defaultdict(list)
    for r in rows:
        if r["sensitivity"] != "work" or r["category"] in ("idle", "inbox"):
            continue
        s = max(parse_iso(r["start_at"]), start)
        e = min(parse_iso(r["end_at"]), end, now) if r["end_at"] else s
        if e < s or s >= now:
            continue
        item = (s, e, r)
        if r["kind"] == "plan":
            meetings.append([item])
        else:
            key = ("project", r["project"]) if r["project"] else ("activity", group_of(r["category"], "work"))
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

    kept = [ep for ep in episodes if ep["_length"] >= MIN_LENGTH or ep["counts"]]
    kept.sort(key=lambda ep: ep["start_at"])
    for ep in kept:
        del ep["_length"]
    return kept


def _episode(kind: str, name: str | None, items: list) -> dict:
    s0 = min(i[0] for i in items)
    e0 = max(i[1] for i in items)
    spans = [(s, e) for s, e, r in items if r["end_at"]]
    by_category = defaultdict(list)
    counts = Counter()
    titles = Counter()
    branches = set()
    for s, e, r in items:
        branch = json.loads(r["attrs"] or "{}").get("branch")
        if branch and branch not in ("HEAD", "main", "master", "develop"):
            branches.add(branch)
        if r["end_at"]:
            by_category[r["category"]].append((s, e))
        else:
            counts[r["category"]] += 1
        if r["title"]:
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
        "branches": sorted(branches)[:3],
        "sources": sorted({r["source"] for _, _, r in items}),
        "event_ids": [r["id"] for _, _, r in items],
        "_length": max(e0 - s0, total(merge(spans))),
    }


def details(ep: dict) -> str:
    keys = ("kind", "group", "minutes", "activities", "counts", "titles", "branches", "sources")
    return json.dumps({k: ep[k] for k in keys})
