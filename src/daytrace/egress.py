"""The only path from daytrace to an LLM. Builds the payload, scrubs it, and writes an audit copy.

A day payload holds one entry per project (time ranges, activity minutes, counts, up to three scrubbed titles,
site names (or the page title when a page names no site), branch names), totals for what was too short to list,
and day-level counts, with private time only as {"start", "end", "minutes"}. Day payloads are never sent alone.

A week or month is built only from day payloads that already passed this gate, and leaves less: per-day
totals instead of time ranges, and private time as one total. It is sent only after a person has reviewed the
exact request; the review can only take things out (see without).
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


def build_payload(day: str, entries: list[dict], also: dict, summary: dict,
                  private: list[tuple[str, str, int]]) -> dict:
    """entries/also: from episodes.entries; summary: dayjson summary; private: (start_iso, end_iso, minutes)."""
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
    }
    check(payload)
    return payload


def build_period_payload(label: str, days: list[dict]) -> dict:
    """label: "week of ..." or "October 2026"; days: build_payload results for the days that have data."""
    entries, stats, also = {}, Counter(), Counter()
    longest, private, per_day = None, 0, []
    for d in days:
        when = d["date"].split()[0][:3] + " " + d["date"].split()[1][5:]  # "Thursday 2026-10-22" -> "Thu 10-22"
        st = d["stats"]
        per_day.append({"date": when, "active_min": st.get("active_min"), "meeting_min": st.get("meeting_min"),
                        "first": st.get("first"), "last": st.get("last")})
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
        "also": dict(also),
        "private": {"minutes": private},
    }
    check(payload)
    return payload


REVIEWABLE = ("titles", "sites", "branches")


def without(payload: dict, exclude: list[dict]) -> dict:
    """The payload minus what a review took out: whole entries ({"entry": key}) or single titles, sites or branches
    ({"entry": key, "field": ..., "value": ...}). Unknown keys or values are ignored; nothing can be added."""
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
    return {**payload, "entries": entries}


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
