"""Run adapters from their cursors, pass results through intake, and store them."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .adapters import Adapter
from .intake import transform
from .models import iso, parse_iso
from .rules import Rules
from .store import Store


class NotInitialized(RuntimeError):
    pass


@dataclass
class SyncResult:
    source: str
    ok: bool
    stored: int = 0
    dropped: int = 0
    purged: int = 0
    error: str | None = None
    at: str = ""


def day_zero(store: Store) -> datetime:
    value = store.get_meta("day_zero")
    if not value:
        raise NotInitialized("run `daytrace init` first")
    return parse_iso(value)


def sync(store: Store, rules: Rules, adapters: list[Adapter], now: datetime) -> list[SyncResult]:
    zero = day_zero(store)
    results = []
    for adapter in adapters:
        result = SyncResult(adapter.name, ok=True, at=iso(now))
        try:
            cursor = store.get_cursor(adapter.name)
            since = max(zero, parse_iso(cursor) - adapter.overlap) if cursor else zero
            events = []
            for raw in adapter.fetch(since, now):
                # Tracking is forward-only: nothing before day zero is kept.
                if (raw.end_at or raw.start_at) < zero:
                    continue
                raw.start_at = max(raw.start_at, zero)
                event = transform(raw, rules)
                if event is None:
                    result.dropped += 1
                else:
                    events.append(event)
            result.stored = store.upsert(events, now)
            store.set_cursor(adapter.name, iso(now), now)
            result.purged = _purge_raw(store, rules, adapter, zero, now)
        except Exception as exc:  # one failing source must not stop the others
            result.ok, result.error = False, f"{type(exc).__name__}: {exc}"
        store.set_meta(f"status:{adapter.name}", json.dumps(asdict(result)))
        results.append(result)
    return results


def _purge_raw(store: Store, rules: Rules, adapter: Adapter, zero: datetime, now: datetime) -> int:
    purge = getattr(adapter, "purge", None)
    if purge is None or rules.activitywatch_raw_days <= 0:
        return 0
    key = f"purged_until:{adapter.name}"
    since = parse_iso(store.get_meta(key) or iso(zero))
    before = now - timedelta(days=rules.activitywatch_raw_days)
    if before <= since:
        return 0
    count = purge(before, since, lambda ids: store.has_source_ids(adapter.name, ids))
    store.set_meta(key, iso(before))
    return count
