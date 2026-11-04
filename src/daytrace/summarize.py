"""Write a week or month summary: day episodes -> egress gate -> request kept for review -> (after review) the LLM
-> stored narrative. Nothing is sent without a review of the exact request.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import dayjson, egress, episodes, llm, patterns
from .adapters import granola as notes
from .models import iso, parse_iso
from .report import clock, day_bounds, merge
from .store import Store

# A later one of these on the same project can close an action item, when a distinctive word overlaps.
TRACE = {"commit", "pr", "wiki", "docs"}
_WORD = re.compile(r"[a-z0-9]{4,}")
# Generic words that would close an item against almost any title.
_STOP = frozenset("""
about after also been before being from have into just more next note notes only over
some that them then there these they this those were what when where which while will
with would your meeting update please need send write follow check make look week
today item items action sync call chat
""".split())


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


def distinctive(text: str) -> set[str]:
    """Words long enough, and uncommon enough, to say an action item and a title are the same work."""
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def echoed(item: str, titles: list[str]) -> bool:
    """True when a later title shares a distinctive word with the item. An item with none is kept."""
    words = distinctive(item)
    return bool(words) and any(words & distinctive(title) for title in titles)


def drop_closed(days: list[dict], traces: list[tuple[datetime, str, str]]) -> None:
    """Drop "mine" items a later commit, pull request, wiki edit or doc on that project already echoes.

    `at` stays on the day payload for this comparison; the period payload leaves it off. A meeting with no
    project keeps its items: there is nothing to match them to."""
    for day in days:
        for m in day.get("meetings", []):
            project, at = m.get("project"), m.get("at")
            if not project or not at:
                continue
            start = parse_iso(at)
            later = [title for when, proj, title in traces if proj == project and when >= start]
            m["mine"] = [item for item in m["mine"] if not echoed(item, later)]


def _traces(rows, start: datetime, end: datetime, now: datetime) -> list[tuple[datetime, str, str]]:
    """(when, project, cleaned title) for the traces that can close an action item."""
    out = []
    for r in rows:
        if (r["sensitivity"] == "private" or r["category"] not in TRACE or not r["title"] or not r["project"]):
            continue
        when = parse_iso(r["start_at"])
        if when < start or when >= min(end, now):
            continue
        title = egress.clean(r["title"])
        if title:
            out.append((when, r["project"], title))
    return out


def _meeting_notes(rows, start: datetime, end: datetime, now: datetime, people: People) -> list[dict]:
    """The day's meeting notes as digests, each with the meeting's project and the calendar title as the LLM may
    see it (no title when it matched none). `at` is the start, for telling which traces came after."""
    folder = notes.notes_dir()
    found = notes.notes_between(folder, start, min(end, now))
    if not found:
        return []
    def hosts(source, category=None):
        return sorted((max(parse_iso(r["start_at"]), start), min(parse_iso(r["end_at"]), end), r)
                      for r in rows if r["source"] == source and r["sensitivity"] != "private" and r["end_at"]
                      and (category is None or r["category"] == category))

    out, leftover = [], list(found)
    for group in (hosts("outlook_calendar"), hosts("slack", "meeting")):
        still = []
        for n, i in zip(leftover, notes.match(leftover, [(s, e) for s, e, _ in group])):
            if i is None:
                still.append(n)
                continue
            digest = notes.digest(folder, n["id"], people.names, people.me)
            if not any(digest.values()):
                continue
            s, e, r = group[i]
            out.append({"time": f"{clock(s)}–{clock(e)}", "at": iso(s), "project": r["project"],
                        "title": episodes.shared_title(r, json.loads(r["attrs"] or "{}")), **digest})
        leftover = still
    for n in leftover:
        digest = notes.digest(folder, n["id"], people.names, people.me)
        if any(digest.values()):
            out.append({"time": clock(parse_iso(n["meeting_at"])), "at": n["meeting_at"], "project": None,
                        "title": None, **digest})
    return out


def _day_payload(store: Store, day: date, now: datetime, people: People | None = None
                 ) -> tuple[dict, int, list[str], list[tuple[datetime, str, str]]]:
    """A day's gated payload, its entry count, the ids of the events that went into it, and the traces that can
    close an action item."""
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
    return payload, len(items), [i for ep in eps for i in ep["event_ids"]], _traces(rows, start, end, now)


def period_key(kind: str, first: date) -> str:
    """Where a week's or month's summary is stored: "week:2026-10-18", "month:2026-10"."""
    return f"week:{first.isoformat()}" if kind == "week" else f"month:{first:%Y-%m}"


def prepare_period(store: Store, kind: str, first: date, last: date, now: datetime) -> SummaryResult:
    """Build a week's or month's request from the gated payloads of its days and keep it for review; nothing is
    sent. Days after today or with no entries are left out."""
    days, ids, traces = [], [], []
    zero = parse_iso(store.get_meta("day_zero") or iso(day_bounds(first)[0])).astimezone().date()
    today = now.astimezone().date()
    people = People.load()
    for i in range((last - first).days + 1):
        day = first + timedelta(days=i)
        if day > today or day < zero:
            continue
        payload, entries, used, day_traces = _day_payload(store, day, now, people)
        if entries or payload["stats"].get("active_min"):
            days.append(payload)
            ids += used
            traces += day_traces
    if not days:
        raise ValueError(f"nothing recorded this {kind} yet")
    label = (f"week of {first:%A %Y-%m-%d} to {last:%A %Y-%m-%d}" if kind == "week" else f"{first:%B %Y}")
    drop_closed(days, traces)
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
    {"entry": key} (the whole entry), {"entry": key, "field": "titles" | "sites" | "branches", "value": ...},
    {"meeting": key} (a meeting's whole notes) or {"meeting": key, "field": "topics" | "mine" | "waiting" |
    "unassigned", "value": ...}. See egress.without.
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
    reply = llm.complete(body)
    egress.write_audit(folder, "response", {"reply": reply}, now)
    parsed = llm.parse(reply)
    if kind != "week":
        parsed["next_week"] = []  # only a week plans the next one
    store.save_summary(key, {"themes": parsed["themes"], "observations": parsed["observations"],
                             "next_week": parsed["next_week"], "audit": str(audit),
                             "entries": len(payload["entries"]), "event_ids": row["stats"].get("event_ids", [])},
                       parsed["narrative"], body["model"], now)
    return SummaryResult("sent", audit, len(payload["entries"]), parsed["narrative"], parsed["themes"],
                         parsed["observations"], parsed["next_week"])


def _body(payload: dict, kind: str) -> dict:
    return llm.request_body(payload, llm.model_name(), system=llm.prompt(kind))


def current_period(store: Store, kind: str, first: date, last: date) -> dict | None:
    """The stored week or month summary, withheld once any event it used has turned private."""
    story = store.summary(period_key(kind, first))
    if not story:
        return None
    start, end = day_bounds(first)[0], day_bounds(last)[1]
    private = {r["id"] for r in store.events_between(iso(start), iso(end)) if r["sensitivity"] == "private"}
    return None if set(story["stats"].get("event_ids", [])) & private else story
