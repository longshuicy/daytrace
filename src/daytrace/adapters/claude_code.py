"""Claude Code: active session spans per project, from local session logs. Never reads prompt content."""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from ..models import UTC, RawEvent, iso, parse_iso

DEFAULT_ROOT = Path("~/.claude/projects").expanduser()
GAP = timedelta(minutes=10)  # silence longer than this splits a session into separate spans
MIN_SPAN = timedelta(minutes=1)


class ClaudeCode:
    name = "claude_code"
    overlap = timedelta(0)

    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = root

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        for path in self.root.glob("*/*.jsonl"):
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if modified < since:
                continue
            for raw in parse_session_file(path):
                if raw.start_at < until:
                    yield raw


def parse_session_file(path: Path) -> list[RawEvent]:
    messages = defaultdict(list)  # session id -> [(time, cwd, prompt id or None, branch)]
    with path.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") not in ("user", "assistant") or not d.get("timestamp"):
                continue
            prompt = d.get("promptId") if d.get("type") == "user" else None
            messages[d.get("sessionId", path.stem)].append(
                (parse_iso(d["timestamp"]), d.get("cwd"), prompt, d.get("gitBranch"))
            )
    events = []
    for session_id, items in messages.items():
        items.sort(key=lambda m: m[0])
        span = [items[0]]
        for item in items[1:]:
            if item[0] - span[-1][0] > GAP:
                events.append(_span(session_id, span))
                span = []
            span.append(item)
        events.append(_span(session_id, span))
    return events


def _span(session_id: str, items: list[tuple]) -> RawEvent:
    start, end = items[0][0], items[-1][0]
    cwd = Counter(m[1] for m in items if m[1]).most_common(1)
    branch = next((m[3] for m in reversed(items) if m[3]), None)
    return RawEvent(
        "claude_code", f"{session_id}:{iso(start)}", "span", "recorded",
        start, max(end, start + MIN_SPAN), category="ai",
        path=cwd[0][0] if cwd else None,
        attrs={"prompts": len({m[2] for m in items if m[2]}), "branch": branch},
    )
