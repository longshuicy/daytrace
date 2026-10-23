"""Read-only source adapters. Each yields RawEvents for a time window."""

from datetime import datetime, timedelta
from typing import Iterable, Protocol

from ..models import RawEvent
from ..rules import Rules


class NotConfigured(RuntimeError):
    """The source needs setup (e.g. a token) before it can sync. Not an error."""


class Adapter(Protocol):
    name: str
    # How far before the last sync to re-read, for events that arrive or grow late.
    overlap: timedelta

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]: ...


def all_adapters(rules: Rules) -> list[Adapter]:
    from .activitywatch import ActivityWatch
    from .claude_code import ClaudeCode
    from .confluence import Confluence
    from .git import Git
    from .github import GitHub

    return [ActivityWatch(), Git(rules), ClaudeCode(), GitHub(rules.personal_owners), Confluence()]
