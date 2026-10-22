"""ActivityWatch: focused window and away-from-keyboard spans, via its local REST API."""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Callable, Iterable

from ..models import RawEvent, iso, parse_iso

DEFAULT_URL = "http://localhost:5600/api/0"
WATCHED_TYPES = ("currentwindow", "afkstatus")


class ActivityWatch:
    name = "activitywatch"
    overlap = timedelta(hours=1)  # the latest window event keeps growing via heartbeats

    def __init__(self, base_url: str = DEFAULT_URL, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, params: dict | None = None, method: str = "GET"):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = resp.read()
        return json.loads(body) if body else None

    def buckets(self) -> dict[str, dict]:
        return {
            bid: b for bid, b in self._request("/buckets/").items()
            if b.get("type") in WATCHED_TYPES
        }

    def _events(self, bucket_id: str, since: datetime, until: datetime) -> list[dict]:
        path = f"/buckets/{urllib.parse.quote(bucket_id)}/events"
        return self._request(path, {"start": iso(since), "end": iso(until), "limit": -1})

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        for bucket_id, bucket in self.buckets().items():
            for e in self._events(bucket_id, since, until):
                raw = to_raw(bucket_id, bucket["type"], e)
                if raw:
                    yield raw

    def purge(self, before: datetime, since: datetime,
              ingested: Callable[[list[str]], set[str]]) -> int:
        """Delete raw ActivityWatch events in [since, before) that daytrace has already stored."""
        deleted = 0
        for bucket_id in self.buckets():
            events = self._events(bucket_id, since, before)
            ids = {f"{bucket_id}:{e['id']}": e for e in events}
            keep = ingested(list(ids))
            for source_id, e in ids.items():
                end = parse_iso(e["timestamp"]) + timedelta(seconds=e.get("duration", 0))
                if source_id in keep and end < before:
                    path = f"/buckets/{urllib.parse.quote(bucket_id)}/events/{e['id']}"
                    self._request(path, method="DELETE")
                    deleted += 1
        return deleted


def to_raw(bucket_id: str, bucket_type: str, e: dict) -> RawEvent | None:
    start = parse_iso(e["timestamp"])
    duration = float(e.get("duration") or 0)
    if duration < 1:
        return None
    end = start + timedelta(seconds=duration)
    data = e.get("data", {})
    source_id = f"{bucket_id}:{e['id']}"
    if bucket_type == "afkstatus":
        if data.get("status") != "afk":
            return None
        return RawEvent("activitywatch", source_id, "span", "observed", start, end, category="idle")
    return RawEvent(
        "activitywatch", source_id, "span", "observed", start, end,
        app=data.get("app"), title=data.get("title"), url=data.get("url"),
    )
