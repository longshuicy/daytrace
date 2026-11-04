"""Slack: your sent messages (grouped) and huddles, via Slack MCP search. Never stores message text."""

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable, Iterable

from .. import slack
from ..models import UTC, RawEvent, iso
from .claude_code import GAP

SEARCH = "slack_search_public_and_private"
MAX_PAGES = 20
LIMIT = 20
HUDDLE = timedelta(minutes=30)  # Slack rarely reports end; long enough for Granola notes to attach
HUDDLE_PHRASES = ('"has started a meeting"', '"has started a huddle"')
_HUDDLE_START = re.compile(r"(?i)has started a (?:meeting|huddle)\s*$")
_HUDDLE_WINDOW = re.compile(r"(?i)huddle preview")
_HIT = re.compile(r"### Result \d+ of \d+\n(.*?)(?=\n### Result |\Z)", re.S)
_CHANNEL = re.compile(r"^Channel: (.+?) \(ID: ([A-Z0-9]+)\)", re.M)
_TS = re.compile(r"^Message_ts: (\S+)", re.M)
_LINK = re.compile(r"^Permalink: \[.*?\]\((https://[^)]+)\)", re.M)
_TEXT = re.compile(r"^Text:\s*\n(.*?)(?:\n---\s*$|\Z)", re.S | re.M)
_CURSOR = re.compile(r"cursor `([^`]+)`")


class Slack:
    name = "slack"
    overlap = timedelta(hours=1)

    def __init__(self, session: Callable[[], slack.Session] | None = None):
        self.session = session or slack.Session

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        s = self.session()  # Slack.Session raises NotConfigured before sign-in
        after = (since - timedelta(days=1)).astimezone(UTC).strftime("%Y-%m-%d")
        sent = [m for m in self._search(s, since, until, filters=f"from:me after:{after}")
                if not huddle_start(m)]
        huddles, seen = [], set()
        for phrase in HUDDLE_PHRASES:
            for m in self._search(s, since, until, keywords=[phrase], filters=f"after:{after}",
                                  include_bots=True):
                key = (m["channel_id"], m["at"])
                if huddle_start(m) and key not in seen:
                    seen.add(key)
                    huddles.append(m)
        for group in cluster(sent):
            yield burst_raw(group)
        yield from huddle_raws(huddles)

    def _search(self, s, since: datetime, until: datetime, **args) -> list[dict]:
        out, cursor = [], ""
        for _ in range(MAX_PAGES):
            params = {"natural_language_query": "", "content_types": "messages", "sort": "timestamp",
                      "sort_dir": "desc", "response_format": "detailed", "include_context": False,
                      "limit": LIMIT, **args}
            if cursor:
                params["cursor"] = cursor
            text = s.call(SEARCH, params)
            oldest = until
            for m in parse_results(text):
                oldest = min(oldest, m["at"])
                if since <= m["at"] < until:
                    out.append(m)
            cursor = next_cursor(text)
            if not cursor or oldest < since:
                break
        return out


def from_ts(value: str | None) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), UTC)
    except (TypeError, ValueError):
        return None


def unwrap(text: str) -> str:
    """Slack MCP returns either markdown or a JSON object with results + pagination_info."""
    raw = (text or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return text or ""
        if isinstance(data, dict) and "results" in data:
            return f"{data.get('results') or ''}\n{data.get('pagination_info') or ''}"
    return text or ""


def next_cursor(text: str) -> str:
    m = _CURSOR.search(unwrap(text))
    return m.group(1) if m else ""


def parse_results(text: str) -> list[dict]:
    return [h for block in _HIT.findall(unwrap(text)) if (h := parse_hit(block))]


def parse_hit(block: str) -> dict | None:
    """Channel, time and permalink only. Text is kept in memory for huddle detection, never stored."""
    ch, ts = _CHANNEL.search(block), _TS.search(block)
    at = from_ts(ts.group(1) if ts else None)
    if not ch or not at:
        return None
    kind, channel_id = ch.group(1).strip(), ch.group(2)
    link = _LINK.search(block)
    body = _TEXT.search(block)
    return {
        "at": at,
        "channel_id": channel_id,
        "channel": {
            "id": channel_id,
            "name": "" if kind in ("DM", "Group DM") else kind.lstrip("#"),
            "is_im": kind == "DM",
            "is_mpim": kind == "Group DM",
        },
        "permalink": link.group(1) if link else None,
        "text": (body.group(1).strip() if body else ""),
    }


def parse_match(m: dict) -> dict | None:
    """Same shape as parse_hit, from a Web-API-like dict. Tests still build fixtures this way."""
    at = from_ts(m.get("ts"))
    ch = m.get("channel") or {}
    channel_id = ch.get("id")
    if not at or not channel_id:
        return None
    return {
        "at": at,
        "channel_id": channel_id,
        "channel": ch,
        "permalink": m.get("permalink"),
        "text": m.get("text") or "",
    }


def huddle_start(m: dict) -> bool:
    return bool(_HUDDLE_START.search((m.get("text") or "").strip()))


def huddle_window(title: str | None) -> bool:
    """ActivityWatch titles Slack uses while a huddle is open. Those windows are seconds long; the day view
    treats them as a meeting so they sit on the meeting lane."""
    return bool(title and _HUDDLE_WINDOW.search(title))


def channel_label(ch: dict) -> str:
    """A channel as it may be stored: #name, or Direct message / Group DM with no people's names."""
    if ch.get("is_im"):
        return "Direct message"
    if ch.get("is_mpim"):
        return "Group DM"
    name = (ch.get("name") or "").lstrip("#")
    return f"#{name}" if name else "Slack"


def cluster(messages: list[dict], gap: timedelta = GAP) -> list[list[dict]]:
    """Group a person's messages in one channel when they fall within `gap` (the same 10 minutes as
    Claude Code / Cursor sessions)."""
    by_channel: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        by_channel[m["channel_id"]].append(m)
    out = []
    for items in by_channel.values():
        items.sort(key=lambda m: m["at"])
        run = [items[0]]
        for m in items[1:]:
            if m["at"] - run[-1]["at"] > gap:
                out.append(run)
                run = []
            run.append(m)
        out.append(run)
    return out


def burst_title(label: str, n: int) -> str:
    return label if n == 1 else f"{label} · {n} messages"


def burst_raw(group: list[dict]) -> RawEvent:
    first, n = group[0], len(group)
    label = channel_label(first["channel"])
    return RawEvent(
        "slack",
        f"{first['channel_id']}:{iso(first['at'])}",
        "point",
        "recorded",
        first["at"],
        category="chat",
        title=burst_title(label, n),
        url=first.get("permalink"),
        attrs={"count": n, "channel": first["channel_id"]},
    )


def huddle_raws(starts: list[dict], duration: timedelta = HUDDLE) -> list[RawEvent]:
    """One huddle span per start, ending when the next huddle in that channel begins or after `duration`."""
    by_channel: dict[str, list[dict]] = defaultdict(list)
    for m in starts:
        if not huddle_start(m):
            continue
        by_channel[m["channel_id"]].append(m)
    out = []
    for items in by_channel.values():
        items.sort(key=lambda m: m["at"])
        for i, m in enumerate(items):
            end = m["at"] + duration
            if i + 1 < len(items):
                end = min(end, items[i + 1]["at"])
            label = channel_label(m["channel"])
            title = "Huddle" if label in ("Direct message", "Group DM", "Slack") else f"Huddle in {label}"
            out.append(RawEvent(
                "slack",
                f"huddle:{m['channel_id']}:{iso(m['at'])}",
                "plan",
                "recorded",
                m["at"],
                end,
                category="meeting",
                title=title,
                url=m.get("permalink"),
                attrs={"huddle": True, "channel": m["channel_id"]},
            ))
    return out
