"""Write a week or month summary: day episodes -> egress gate -> request kept for review -> (after review) Lumen
-> stored narrative. Nothing is sent without a review of the exact request.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import dayjson, egress, episodes, lumen, patterns
from .adapters import granola as notes
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
    observations: list[dict] = field(default_factory=list)
    next_week: list[dict] = field(default_factory=list)


@dataclass
class People:
    """Who to take out of meeting notes, and who "me" is."""
    names: set[str]
    me: str | None

    @classmethod
    def load(cls) -> "People":
        return cls(notes.people(notes.notes_dir()), notes.my_name(notes.notes_dir()))


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


def _meeting_notes(rows, start: datetime, end: datetime, now: datetime, people: People) -> list[dict]:
    """The day's meeting notes as digests, each with the calendar meeting it was taken in (its title as Lumen may
    see it), or no title when it matched none."""
    folder = notes.notes_dir()
    found = notes.notes_between(folder, start, min(end, now))
    if not found:
        return []
    calendar = sorted((max(parse_iso(r["start_at"]), start), min(parse_iso(r["end_at"]), end), r) for r in rows
                      if r["source"] == "outlook_calendar" and r["sensitivity"] != "private" and r["end_at"])
    out = []
    for n, i in zip(found, notes.match(found, [(s, e) for s, e, _ in calendar])):
        digest = notes.digest(folder, n["id"], people.names, people.me)
        if not any(digest.values()):
            continue
        if i is None:
            when, title = _local_clock(parse_iso(n["meeting_at"])), None
        else:
            s, e, r = calendar[i]
            when, title = f"{_local_clock(s)}–{_local_clock(e)}", episodes.lumen_title(r, json.loads(r["attrs"] or "{}"))
        out.append({"time": when, "title": title, **digest})
    return out


def _local_clock(dt: datetime) -> str:
    return dt.astimezone().strftime("%H:%M")


def _day_payload(store: Store, day: date, now: datetime, people: People | None = None) -> tuple[dict, int, list[str]]:
    """A day's gated payload, its entry count, and the ids of the events that went into it."""
    start, end = day_bounds(day)
    rows = store.events_between(iso(start), iso(end))
    eps = episodes.build(rows, start, end, now)
    items, also = episodes.entries(eps)
    zero = parse_iso(store.get_meta("day_zero") or iso(start))
    summary = dayjson.build(day, rows, {}, {}, zero, now)["summary"]
    day_patterns = patterns.day_patterns(rows, start, end, now, label=egress.clean)
    meetings = _meeting_notes(rows, start, end, now, people) if people else []
    payload = egress.build_payload(day.isoformat(), items, also, summary, _private(rows, start, end, now),
                                   day_patterns, meetings)
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
    people = People.load()
    for i in range((last - first).days + 1):
        day = first + timedelta(days=i)
        if day > today or day < zero:
            continue
        payload, entries, used = _day_payload(store, day, now, people)
        if entries or payload["stats"].get("active_min"):
            days.append(payload)
            ids += used
    if not days:
        raise ValueError(f"nothing recorded this {kind} yet")
    label = (f"week of {first:%A %Y-%m-%d} to {last:%A %Y-%m-%d}" if kind == "week" else f"{first:%B %Y}")
    payload = egress.build_period_payload(label, days)
    key = period_key(kind, first)
    audit = egress.write_audit(key.replace(":", "-"), "dry-run", _body(payload, kind), now)
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
    body = _body(payload, kind)
    folder = key.replace(":", "-")
    audit = egress.write_audit(folder, "request", body, now)
    reply = lumen.complete(body)
    egress.write_audit(folder, "response", {"reply": reply}, now)
    parsed = lumen.parse(reply)
    if kind != "week":
        parsed["next_week"] = []  # only a week plans the next one
    store.save_summary(key, {"themes": parsed["themes"], "observations": parsed["observations"],
                             "next_week": parsed["next_week"], "audit": str(audit),
                             "entries": len(payload["entries"]), "event_ids": row["stats"].get("event_ids", [])},
                       parsed["narrative"], body["model"], now)
    return SummaryResult("sent", audit, len(payload["entries"]), parsed["narrative"], parsed["themes"],
                         parsed["observations"], parsed["next_week"])


def _body(payload: dict, kind: str) -> dict:
    return lumen.request_body(payload, os.environ.get("LUMEN_MODEL") or "(unset)", system=lumen.prompt(kind))


def current_period(store: Store, kind: str, first: date, last: date) -> dict | None:
    """The stored week or month summary, withheld once any event it used has turned private."""
    story = store.summary(period_key(kind, first))
    if not story:
        return None
    start, end = day_bounds(first)[0], day_bounds(last)[1]
    private = {r["id"] for r in store.events_between(iso(start), iso(end)) if r["sensitivity"] == "private"}
    return None if set(story["stats"].get("event_ids", [])) & private else story
