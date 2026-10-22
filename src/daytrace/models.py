"""Event types and time helpers shared across daytrace."""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

UTC = timezone.utc

KINDS = ("point", "span", "plan")
EVIDENCE = ("observed", "scheduled", "recorded", "inferred")
SENSITIVITY = ("work", "personal", "unknown")


def iso(dt: datetime) -> str:
    """UTC timestamp at second precision; sorts correctly as a string."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def event_id(source: str, source_id: str) -> str:
    return hashlib.sha1(f"{source}:{source_id}".encode()).hexdigest()[:16]


@dataclass
class RawEvent:
    """What an adapter emits, before intake classifies and redacts it."""

    source: str
    source_id: str
    kind: str
    evidence: str
    start_at: datetime
    end_at: datetime | None = None
    category: str | None = None
    app: str | None = None
    title: str | None = None
    url: str | None = None
    path: str | None = None  # repo or working directory, used for classification
    always_work: bool = False  # e.g. every Outlook or Confluence event
    attrs: dict = field(default_factory=dict)


@dataclass
class Event:
    """A normalized, classified and redacted event, as stored."""

    source: str
    source_id: str
    kind: str
    evidence: str
    start_at: str
    end_at: str | None
    category: str
    project: str | None
    title: str | None
    url: str | None
    sensitivity: str
    attrs: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return event_id(self.source, self.source_id)
