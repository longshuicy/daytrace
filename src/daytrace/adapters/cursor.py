"""Cursor: active agent chat spans per project, from Cursor's local state database. Reads only message times
and kinds, never message text or chat titles. Cursor doesn't record the git branch a chat ran on."""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

from ..models import RawEvent, iso, parse_iso
from .claude_code import GAP, MIN_SPAN

DEFAULT_ROOT = Path("~/Library/Application Support/Cursor/User").expanduser()
USER = 1  # message type in a chat's header list; 2 is Cursor's reply


class Cursor:
    name = "cursor"
    overlap = timedelta(0)

    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = root

    def fetch(self, since: datetime, until: datetime) -> Iterable[RawEvent]:
        path = self.root / "globalStorage" / "state.vscdb"
        if not path.exists():
            return
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            chats = db.execute(
                "SELECT composerId, workspaceId FROM composerHeaders "
                "WHERE lastUpdatedAt >= ? AND NOT isSubagent", (int(since.timestamp() * 1000),)).fetchall()
            folders = {}
            for chat_id, workspace in chats:
                row = db.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (f"composerData:{chat_id}",)).fetchone()
                if not row or not row[0]:
                    continue
                if workspace not in folders:
                    folders[workspace] = self.folder(workspace)
                for raw in chat_spans(chat_id, json.loads(row[0]), folders[workspace]):
                    if raw.start_at < until:
                        yield raw
        finally:
            db.close()

    def folder(self, workspace: str | None) -> str | None:
        """The folder a Cursor window had open: the first one of a multi-root workspace. None for empty
        windows and workspace files that are gone."""
        try:
            d = json.loads((self.root / "workspaceStorage" / workspace / "workspace.json").read_text())
            if d.get("folder"):
                return _path(d["folder"])
            file = Path(_path(d["workspace"]))
            first = json.loads(file.read_text())["folders"][0]
        except (TypeError, OSError, ValueError, KeyError, IndexError):
            return None
        return _path(first["uri"]) if "uri" in first else str((file.parent / first["path"]).resolve())


def _path(uri: str) -> str:
    return unquote(urlparse(uri).path)


def chat_spans(chat_id: str, data: dict, folder: str | None) -> list[RawEvent]:
    messages = sorted((parse_iso(h["createdAt"]), h.get("type") == USER)
                      for h in data.get("fullConversationHeadersOnly") or [] if h.get("createdAt"))
    if not messages:
        return []
    events, span = [], [messages[0]]
    for m in messages[1:]:
        if m[0] - span[-1][0] > GAP:
            events.append(_span(chat_id, span, folder))
            span = []
        span.append(m)
    events.append(_span(chat_id, span, folder))
    return events


def _span(chat_id: str, items: list[tuple[datetime, bool]], folder: str | None) -> RawEvent:
    start, end = items[0][0], items[-1][0]
    return RawEvent(
        "cursor", f"{chat_id}:{iso(start)}", "span", "recorded",
        start, max(end, start + MIN_SPAN), category="code", path=folder,
        attrs={"prompts": sum(user for _, user in items)},
    )
