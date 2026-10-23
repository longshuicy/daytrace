"""Write a summary: episodes -> egress gate -> audit -> (dry run | Lumen) -> stored narrative.

A day is summarized from its episodes; a week or month from the gated payloads of its days.
"""

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import dayjson, egress, episodes, lumen
from .models import iso, parse_iso
from .report import day_bounds, merge
from .rules import Rules
from .store import Store


@dataclass
class SummaryResult:
    mode: str  # "dry-run" or "sent"
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


def current(store: Store, day: date, rows) -> dict | None:
    """The stored story, if still safe to show.

    The story is a snapshot from the last summarize. Rules can change since (a folder made private), so a story
    that used events which are now private is withheld.
    """
    private = {r["id"] for r in rows if r["sensitivity"] == "private"}
    return None if store.episode_event_ids(day.isoformat()) & private else store.summary(day.isoformat())


def _day_payload(store: Store, day: date, now: datetime, save: bool = False) -> tuple[dict, int, list[str]]:
    """A day's gated payload, its entry count, and the ids of the events that went into it."""
    start, end = day_bounds(day)
    rows = store.events_between(iso(start), iso(end))
    eps = episodes.build(rows, start, end, now)
    if save:
        store.save_episodes(day.isoformat(), eps)
    items, also = episodes.entries(eps)
    zero = parse_iso(store.get_meta("day_zero") or iso(start))
    summary = dayjson.build(day, rows, {}, {}, zero, now)["summary"]
    payload = egress.build_payload(day.isoformat(), items, also, summary, _private(rows, start, end, now))
    return payload, len(items), [i for ep in eps for i in ep["event_ids"]]


def _send(store: Store, rules: Rules, key: str, body: dict, entries: int, extra: dict, now: datetime,
          send: bool) -> SummaryResult:
    """Audit the request, then stop (dry run) or send it and store the reply under key."""
    model = body["model"]
    folder = key.replace(":", "-")
    mode = "sent" if send or rules.lumen_mode == "send" else "dry-run"
    audit = egress.write_audit(folder, "request" if mode == "sent" else "dry-run", body, now)
    if mode == "dry-run":
        store.save_summary(key, {"dry_run": True, "audit": str(audit), "entries": entries, **extra}, None, "dry-run", now)
        return SummaryResult(mode, audit, entries)

    reply = lumen.complete(body)
    egress.write_audit(folder, "response", {"reply": reply}, now)
    parsed = lumen.parse(reply)
    store.save_summary(key, {"themes": parsed["themes"], "audit": str(audit), "entries": entries, **extra},
                       parsed["narrative"], model, now)
    return SummaryResult(mode, audit, entries, parsed["narrative"], parsed["themes"])


def summarize(store: Store, rules: Rules, day: date, now: datetime, send: bool = False) -> SummaryResult:
    payload, entries, _ = _day_payload(store, day, now, save=True)
    body = lumen.request_body(payload, os.environ.get("LUMEN_MODEL") or "(unset)")
    return _send(store, rules, day.isoformat(), body, entries, {}, now, send)


def period_key(kind: str, first: date) -> str:
    """Where a week's or month's summary is stored: "week:2026-10-18", "month:2026-10"."""
    return f"week:{first.isoformat()}" if kind == "week" else f"month:{first:%Y-%m}"


def summarize_period(store: Store, rules: Rules, kind: str, first: date, last: date, now: datetime,
                     send: bool = False) -> SummaryResult:
    """A week or month from the gated payloads of its days. Days after today or with no entries are left out."""
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
    body = lumen.request_body(payload, os.environ.get("LUMEN_MODEL") or "(unset)", system=lumen.PERIOD_PROMPT)
    return _send(store, rules, period_key(kind, first), body, len(payload["entries"]), {"event_ids": ids}, now, send)


def current_period(store: Store, kind: str, first: date, last: date) -> dict | None:
    """The stored week or month summary, withheld once any event it used has turned private."""
    story = store.summary(period_key(kind, first))
    if not story:
        return None
    start, end = day_bounds(first)[0], day_bounds(last)[1]
    private = {r["id"] for r in store.events_between(iso(start), iso(end)) if r["sensitivity"] == "private"}
    return None if set(story["stats"].get("event_ids", [])) & private else story
