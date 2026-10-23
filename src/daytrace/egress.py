"""The only path from daytrace to an LLM. Builds the payload, scrubs it, and writes an audit copy.

What can leave: one entry per project (time ranges, activity minutes, counts, up to three scrubbed titles, site
names, branch names), totals for what was too short to list, and day-level counts. Private time leaves only as
{"start", "end", "minutes"}. Nothing else: no page titles.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import config
from .scrub import scrub

MAX_TITLE = 140
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")

ENTRY_FIELDS = ("key", "kind", "project", "group", "times", "minutes", "activities", "counts", "titles", "sites",
                "branches")
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
