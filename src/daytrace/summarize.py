"""Write a week or month summary: day episodes -> egress gate -> request kept for review -> (after review) Lumen
-> stored narrative. Nothing is sent without a review of the exact request.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import dayjson, egress, episodes, lumen
from .models import iso, parse_iso
from .report import day_bounds, merge
from .store import Store


@dataclass
class SummaryResult:
    mode: str  # "dry-run" (built, waiting for review) or "sent"
    audit: Path
    entries: int
    narrative: str | None = None
    themes: list[str] | None = None


def _private(rows, start: datetime, end: datetime, now: datetime) -> list[tuple[str, str, int]]:
    """Private time as bare intervals; nothing else about it leaves."""
    spans = []
    for r in rows:
        if r["sensitivity"] == "private" and r["end_at"]:
            s, e = max(parse_iso(r["start_at"]), start), min(parse_iso(r["end_at"]), end, now)
            if e > s:
                spans.append((s, e))
    out = []
    for s, e in merge(spans, gap=timedelta(minutes=5)):
        minutes = round((e - s).total_seconds() / 60)
        if minutes >= 5:
            out.append((iso(s), iso(e), minutes))
    return out


def _day_payload(store: Store, day: date, now: datetime) -> tuple[dict, int, list[str]]:
    """A day's gated payload, its entry count, and the ids of the events that went into it."""
    start, end = day_bounds(day)
    rows = store.events_between(iso(start), iso(end))
    eps = episodes.build(rows, start, end, now)
    items, also = episodes.entries(eps)
    zero = parse_iso(store.get_meta("day_zero") or iso(start))
    summary = dayjson.build(day, rows, {}, {}, zero, now)["summary"]
    payload = egress.build_payload(day.isoformat(), items, also, summary, _private(rows, start, end, now))
    return payload, len(items), [i for ep in eps for i in ep["event_ids"]]


def period_key(kind: str, first: date) -> str:
    """Where a week's or month's summary is stored: "week:2026-10-18", "month:2026-10"."""
    return f"week:{first.isoformat()}" if kind == "week" else f"month:{first:%Y-%m}"


def prepare_period(store: Store, kind: str, first: date, last: date, now: datetime) -> SummaryResult:
    """Build a week's or month's request from the gated payloads of its days and keep it for review; nothing is
    sent. Days after today or with no entries are left out."""
    days, ids = [], []
    zero = parse_iso(store.get_meta("day_zero") or iso(day_bounds(first)[0])).astimezone().date()
    today = now.astimezone().date()
    for i in range((last - first).days + 1):
        day = first + timedelta(days=i)
        if day > today or day < zero:
            continue
        payload, entries, used = _day_payload(store, day, now)
        if entries or payload["stats"].get("active_min"):
            days.append(payload)
            ids += used
    if not days:
        raise ValueError(f"nothing recorded this {kind} yet")
    label = (f"week of {first:%A %Y-%m-%d} to {last:%A %Y-%m-%d}" if kind == "week" else f"{first:%B %Y}")
    payload = egress.build_period_payload(label, days)
    key = period_key(kind, first)
    audit = egress.write_audit(key.replace(":", "-"), "dry-run", _body(payload), now)
    store.save_summary(key, {"dry_run": True, "audit": str(audit), "entries": len(payload["entries"]),
                             "event_ids": ids, "request": payload}, None, "dry-run", now)
    return SummaryResult("dry-run", audit, len(payload["entries"]))


def send_period(store: Store, kind: str, first: date, now: datetime,
                exclude: list[dict] | None = None) -> SummaryResult:
    """Send the request prepare_period kept, minus what the review took out, and store the reply.

    Only a prepared request can be sent, and the review can only remove from it: exclude is a list of
    {"entry": key} (the whole entry) or {"entry": key, "field": "titles" | "sites" | "branches", "value": ...}.
    """
    key = period_key(kind, first)
    row = store.summary(key)
    if not row or not row["stats"].get("dry_run") or "request" not in row["stats"]:
        raise ValueError(f"no request to send: build this {kind}'s request and review it first")
    payload = egress.without(row["stats"]["request"], exclude or [])
    egress.check(payload)
    body = _body(payload)
    folder = key.replace(":", "-")
    audit = egress.write_audit(folder, "request", body, now)
    reply = lumen.complete(body)
    egress.write_audit(folder, "response", {"reply": reply}, now)
    parsed = lumen.parse(reply)
    store.save_summary(key, {"themes": parsed["themes"], "audit": str(audit), "entries": len(payload["entries"]),
                             "event_ids": row["stats"].get("event_ids", [])}, parsed["narrative"], body["model"], now)
    return SummaryResult("sent", audit, len(payload["entries"]), parsed["narrative"], parsed["themes"])


def _body(payload: dict) -> dict:
    return lumen.request_body(payload, os.environ.get("LUMEN_MODEL") or "(unset)", system=lumen.PERIOD_PROMPT)


def current_period(store: Store, kind: str, first: date, last: date) -> dict | None:
    """The stored week or month summary, withheld once any event it used has turned private."""
    story = store.summary(period_key(kind, first))
    if not story:
        return None
    start, end = day_bounds(first)[0], day_bounds(last)[1]
    private = {r["id"] for r in store.events_between(iso(start), iso(end)) if r["sensitivity"] == "private"}
    return None if set(story["stats"].get("event_ids", [])) & private else story
