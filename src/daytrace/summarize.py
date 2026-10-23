"""Write a day's summary: episodes -> egress gate -> audit -> (dry run | Lumen) -> stored labels and narrative."""

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
    episodes: int
    narrative: str | None = None
    themes: list[str] | None = None


def _nonwork(rows, start: datetime, end: datetime, now: datetime) -> list[tuple[str, str, int]]:
    """Personal and unclassified time as bare intervals; nothing else about it leaves."""
    spans = []
    for r in rows:
        if r["sensitivity"] != "work" and r["end_at"] and r["category"] != "idle":
            s, e = max(parse_iso(r["start_at"]), start), min(parse_iso(r["end_at"]), end, now)
            if e > s:
                spans.append((s, e))
    out = []
    for s, e in merge(spans, gap=timedelta(minutes=5)):
        minutes = round((e - s).total_seconds() / 60)
        if minutes >= 5:
            out.append((iso(s), iso(e), minutes))
    return out


def summarize(store: Store, rules: Rules, day: date, now: datetime, send: bool = False) -> SummaryResult:
    start, end = day_bounds(day)
    rows = store.events_between(iso(start), iso(end))
    eps = episodes.build(rows, start, end, now)
    store.save_episodes(day.isoformat(), eps)
    stored = store.episodes(day.isoformat())

    zero = parse_iso(store.get_meta("day_zero") or iso(start))
    summary = dayjson.build(day, rows, {}, {}, zero, now)["summary"]
    payload = egress.build_payload(day.isoformat(), stored, summary, _nonwork(rows, start, end, now))
    model = os.environ.get("LUMEN_MODEL") or "(unset)"
    body = lumen.request_body(payload, model)

    mode = "sent" if send or rules.lumen_mode == "send" else "dry-run"
    audit = egress.write_audit(day.isoformat(), "request" if mode == "sent" else "dry-run", body, now)
    if mode == "dry-run":
        store.save_summary(day.isoformat(), {"dry_run": True, "audit": str(audit), "episodes": len(stored)},
                           None, "dry-run", now)
        return SummaryResult(mode, audit, len(stored))

    reply = lumen.complete(body)
    egress.write_audit(day.isoformat(), "response", {"reply": reply}, now)
    parsed = lumen.parse(reply)
    known = {ep["id"] for ep in stored}
    store.set_labels({i: label for i, label in parsed["labels"].items() if i in known})
    store.save_summary(day.isoformat(), {"themes": parsed["themes"], "audit": str(audit), "episodes": len(stored)},
                       parsed["narrative"], model, now)
    return SummaryResult(mode, audit, len(stored), parsed["narrative"], parsed["themes"])
