"""The only path from daytrace to an LLM: builds the payload, scrubs it, and writes an audit copy.

Day payloads (per-project entries, scrubbed titles, private time as bare ranges) are never sent alone; a week or
month payload merges them, keeping less, and is sent only after a person reviewed it (see `without`).
"""

import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import config
from .scrub import scrub

MAX_TITLE = 140
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")

ENTRY_FIELDS = ("key", "kind", "project", "group", "times", "minutes", "activities", "counts", "titles", "sites",
                "branches")
PERIOD_ENTRY_FIELDS = ("key", "kind", "project", "group", "days", "minutes", "activities", "counts", "titles",
                       "sites", "branches")
PERIOD_TITLES, PERIOD_MEETING_TITLES, PERIOD_SITES = 3, 8, 5
STAT_FIELDS = ("active_min", "meetings", "meeting_min", "emails_sent", "emails_received", "commits", "prs",
               "reviews", "pushes", "wiki", "issues", "first", "last", "longest")


def clean(text: str) -> str:
    text = _EMAIL.sub("[email]", scrub(text) or "")
    return text if len(text) <= MAX_TITLE else text[: MAX_TITLE - 1] + "…"


def _clock(iso_utc: str) -> str:
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone().strftime("%H:%M")


MEETING_LISTS = ("topics", "mine", "waiting", "unassigned")


def build_payload(day: str, entries: list[dict], also: dict, summary: dict,
                  private: list[tuple[str, str, int]], patterns: dict | None = None,
                  meetings: list[dict] = ()) -> dict:
    """entries/also: from episodes.entries; summary: dayjson summary; private: (start_iso, end_iso, minutes);
    patterns: from patterns.day_patterns; meetings: meetings with notes, each {"time", "title", *MEETING_LISTS}."""
    items = []
    for en in entries:
        item = {
            **{k: en.get(k) for k in ("key", "kind", "project", "group", "minutes", "activities", "counts")},
            "times": [f"{_clock(s)}–{_clock(e)}" for s, e in en["times"]],
            "titles": [clean(t) for t in en.get("titles", [])],
            "sites": {clean(s): m for s, m in en.get("sites", {}).items()},
            "branches": [clean(b) for b in en.get("branches", [])],
        }
        items.append({k: item[k] for k in ENTRY_FIELDS})

    c = summary["counts"]
    flat = {**{k: summary.get(k) for k in ("active_min", "first", "last")}, **c}
    stats = {k: flat.get(k) for k in STAT_FIELDS if k in flat}
    stats["first"] = _clock(summary["first"]) if summary.get("first") else None
    stats["last"] = _clock(summary["last"]) if summary.get("last") else None
    longest = summary.get("longest")
    stats["longest"] = {"focus_on": clean(longest["label"]), "kind": longest["kind"], "minutes": longest["minutes"]} \
        if longest else None

    payload = {
        "date": datetime.fromisoformat(day).strftime("%A %Y-%m-%d"),
        "stats": stats,
        "entries": items,
        "also": also,
        "private": [{"start": _clock(s), "end": _clock(e), "minutes": m} for s, e, m in private],
        "patterns": patterns or {},
        "meetings": [{"time": m["time"], "title": clean(m["title"]) if m.get("title") else None,
                      **{k: [clean(v) for v in m.get(k, [])] for k in MEETING_LISTS}} for m in meetings],
    }
    check(payload)
    return payload


def build_period_payload(label: str, days: list[dict]) -> dict:
    """label: "week of ..." or "October 2026"; days: build_payload results for the days that have data."""
    entries, stats, also = {}, Counter(), Counter()
    longest, private, per_day, meetings = None, 0, [], []
    for d in days:
        when = d["date"].split()[0][:3] + " " + d["date"].split()[1][5:]  # "Thursday 2026-10-22" -> "Thu 10-22"
        st = d["stats"]
        per_day.append({"date": when, "active_min": st.get("active_min"), "meeting_min": st.get("meeting_min"),
                        "first": st.get("first"), "last": st.get("last"), **d.get("patterns", {})})
        meetings += [{"key": f"{when} {m['time']}", "day": when, **m} for m in d.get("meetings", [])]
        stats.update({k: v for k, v in st.items() if isinstance(v, int) and k != "active_min"})
        stats["active_min"] += st.get("active_min") or 0
        if st.get("longest") and (longest is None or st["longest"]["minutes"] > longest["minutes"]):
            longest = {**st["longest"], "day": when}
        also.update(d["also"])
        private += sum(p["minutes"] for p in d["private"])
        for item in d["entries"]:
            en = entries.setdefault(item["key"], {
                "key": item["key"], "kind": item["kind"], "project": item["project"], "group": item["group"],
                "days": [], "minutes": 0, "activities": Counter(), "counts": Counter(), "titles": [],
                "sites": Counter(), "branches": [],
            })
            en["days"].append(when)
            en["minutes"] += item["minutes"]
            en["activities"].update(item["activities"])
            en["counts"].update(item["counts"])
            en["titles"] += [t for t in item["titles"] if t not in en["titles"]]
            en["sites"].update(item["sites"])
            en["branches"] += [b for b in item["branches"] if b not in en["branches"]]
    items = []
    for en in sorted(entries.values(), key=lambda en: -en["minutes"]):
        limit = PERIOD_MEETING_TITLES if en["kind"] == "meeting" else PERIOD_TITLES
        en.update(activities=dict(en["activities"]), counts=dict(en["counts"]), titles=en["titles"][:limit],
                  sites=dict(en["sites"].most_common(PERIOD_SITES)), branches=en["branches"][:3])
        items.append({k: en[k] for k in PERIOD_ENTRY_FIELDS})
    payload = {
        "period": label,
        "stats": {**{k: stats[k] for k in STAT_FIELDS if k in stats}, "days_with_data": len(days), "longest": longest},
        "days": per_day,
        "entries": items,
        "meetings": meetings,
        "also": dict(also),
        "private": {"minutes": private},
    }
    check(payload)
    return payload


REVIEWABLE = ("titles", "sites", "branches")


def without(payload: dict, exclude: list[dict]) -> dict:
    """The payload minus what a review took out: whole entries ({"entry": key}) or single titles, sites or branches
    ({"entry": key, "field": ..., "value": ...}), and whole meetings' notes ({"meeting": key}) or single topics and
    action items ({"meeting": key, "field": ..., "value": ...}). Unknown keys or values are ignored; nothing can be
    added."""
    gone = {x["meeting"] for x in exclude if "meeting" in x and "field" not in x}
    cut = {(x["meeting"], x["field"], x["value"]) for x in exclude if "meeting" in x and x.get("field") in MEETING_LISTS}
    meetings = [{**m, **{f: [v for v in m[f] if (m["key"], f, v) not in cut] for f in MEETING_LISTS}}
                for m in payload.get("meetings", []) if m["key"] not in gone]
    exclude = [x for x in exclude if "entry" in x]
    drop = {x["entry"] for x in exclude if "field" not in x}
    parts = {(x["entry"], x["field"], x["value"]) for x in exclude if x.get("field") in REVIEWABLE}
    entries = []
    for en in payload["entries"]:
        if en["key"] in drop:
            continue
        en = dict(en)
        for f in REVIEWABLE:
            keep = [v for v in en[f] if (en["key"], f, v) not in parts]
            en[f] = {v: en[f][v] for v in keep} if isinstance(en[f], dict) else keep
        entries.append(en)
    return {**payload, "entries": entries, "meetings": meetings}


def check(payload: dict) -> None:
    """Last line of defence: refuse to send anything that still looks like a secret or an address."""
    # Every string was already cleaned, so scrubbing the whole payload again must change nothing.
    text = json.dumps(payload, ensure_ascii=False)
    if scrub(text) != text or _EMAIL.search(text):
        raise ValueError("payload failed the final secret/address check; not sending")


def audit_dir(day: str) -> Path:
    return config.home() / "audit" / day


def write_audit(day: str, name: str, body: dict, now: datetime) -> Path:
    path = audit_dir(day) / f"{now.astimezone():%H%M%S}-{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(body, f, indent=2, ensure_ascii=False)
    return path
