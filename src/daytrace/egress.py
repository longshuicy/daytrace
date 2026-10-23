"""The only path from daytrace to an LLM. Builds a work-only payload, scrubs it, and writes an audit copy.

What can leave: work episodes (times, project, activity minutes, counts, a few scrubbed titles, branch names)
and day-level counts. Non-work time leaves only as {"start", "end", "minutes"}. Nothing else.
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

EPISODE_FIELDS = ("id", "kind", "project", "group", "start", "end", "minutes", "activities", "counts",
                  "titles", "branches")
STAT_FIELDS = ("work_min", "meetings", "meeting_min", "emails_sent", "emails_received", "commits", "prs",
               "reviews", "pushes", "wiki", "issues", "first", "last", "longest")


def clean(text: str) -> str:
    text = _EMAIL.sub("[email]", scrub(text) or "")
    return text if len(text) <= MAX_TITLE else text[: MAX_TITLE - 1] + "…"


def _clock(iso_utc: str) -> str:
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone().strftime("%H:%M")


def build_payload(day: str, episodes: list[dict], summary: dict, nonwork: list[tuple[str, str, int]]) -> dict:
    """episodes: stored work episodes; summary: dayjson summary; nonwork: (start_iso, end_iso, minutes)."""
    eps = []
    for ep in episodes:
        item = {
            "id": ep["id"], "kind": ep["kind"], "project": ep.get("project"), "group": ep.get("group"),
            "start": _clock(ep["start_at"]), "end": _clock(ep["end_at"]), "minutes": ep["minutes"],
            "activities": ep.get("activities", {}), "counts": ep.get("counts", {}),
            "titles": [clean(t) for t in ep.get("titles", [])],
            "branches": [clean(b) for b in ep.get("branches", [])],
        }
        eps.append({k: item[k] for k in EPISODE_FIELDS})

    c = summary["counts"]
    flat = {**{k: summary.get(k) for k in ("work_min", "first", "last")}, **c}
    stats = {k: flat.get(k) for k in STAT_FIELDS if k in flat}
    stats["first"] = _clock(summary["first"]) if summary.get("first") else None
    stats["last"] = _clock(summary["last"]) if summary.get("last") else None
    longest = summary.get("longest")
    stats["longest"] = {"project": longest["project"], "minutes": longest["minutes"]} if longest else None

    payload = {
        "date": datetime.fromisoformat(day).strftime("%A %Y-%m-%d"),
        "stats": stats,
        "episodes": eps,
        "non_work": [{"start": _clock(s), "end": _clock(e), "minutes": m} for s, e, m in nonwork],
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
