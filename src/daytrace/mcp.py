"""stdio MCP server for Cursor, Claude Code, and Claude Desktop.

Newline-delimited JSON-RPC. The tools answer only after `daytrace kb enable`.
"""

import json
import sys
from datetime import date, datetime

from . import config, kb, period
from .models import UTC
from .report import day_bounds
from .store import Store
from .sync import NotInitialized, day_zero

_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}

TOOLS = [
    {
        "name": "at",
        "description": "What the user was doing around a clock time: merged activity blocks and point events "
        "(mail, commits, chat). Private time is only a start and an end. A meeting includes its "
        "Granola note id and a digest of topics and action items.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "time": {
                    "type": "string",
                    "description": "ISO-8601 time. Omit for now.",
                },
                "window": {
                    "type": "integer",
                    "description": "Minutes either side of time. Default 15.",
                },
            },
        },
    },
    {
        "name": "summary",
        "description": "Totals for a day, week, month, or date range: active time, meetings, projects, what "
        "shipped, meeting digests, and action items still open. A single day also includes that "
        "day's blocks. Use this to summarize a stretch of time.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["day", "week", "month"],
                    "description": "Default day.",
                },
                "date": {
                    "type": "string",
                    "description": "YYYY-MM-DD inside the day, week, or month. Default today.",
                },
                "start": {
                    "type": "string",
                    "description": "YYYY-MM-DD. With end, a custom range.",
                },
                "end": {"type": "string", "description": "YYYY-MM-DD, inclusive."},
            },
        },
    },
    {
        "name": "search",
        "description": "Keyword search over Granola notes, commit and mail and meeting subjects, and stored week "
        "or month write-ups. Window titles are not searched. At most 8 hits. Use note with a hit's id "
        "to read the whole note.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "project": {"type": "string"},
                "start": {
                    "type": "string",
                    "description": "YYYY-MM-DD or ISO time, inclusive.",
                },
                "end": {
                    "type": "string",
                    "description": "YYYY-MM-DD inclusive, or an ISO time that is exclusive.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "note",
        "description": "The Markdown of one Granola note, by the id that search or at returned.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
]


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("method") == "notifications/initialized" or "id" not in msg:
            continue
        try:
            result = handle(msg)
        except Exception as exc:  # a protocol error, not a tool failure
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "error": {"code": -32000, "message": str(exc)},
                }
            )
            continue
        _write({"jsonrpc": "2.0", "id": msg["id"], "result": result})


def handle(msg: dict) -> dict:
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        version = params.get("protocolVersion") or "2024-11-05"
        if version not in _VERSIONS:
            version = "2024-11-05"
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "daytrace", "version": "1.0.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return call_tool(params.get("name"), params.get("arguments") or {})
    raise ValueError(f"unknown method {method}")


def call_tool(name: str | None, arguments: dict) -> dict:
    try:
        store = _store()
    except NotInitialized as exc:
        return _err(str(exc))
    if not kb.enabled(store):
        return _err(
            "The daytrace knowledge base is off. Turn it on with `daytrace kb enable`."
        )
    now = datetime.now(UTC)
    try:
        if name == "at":
            payload = kb.at(
                store,
                _when(arguments.get("time")),
                int(arguments.get("window") or 15),
                now,
            )
        elif name == "summary":
            first, last, kind = _range(arguments)
            payload = kb.summary(store, first, last, now, kind)
        elif name == "search":
            payload = kb.search(
                store,
                arguments.get("query") or "",
                now,
                arguments.get("project") or None,
                _bound(arguments.get("start")),
                _bound(arguments.get("end"), inclusive_end=True),
            )
        elif name == "note":
            if not arguments.get("id"):
                return _err("note needs an id")
            payload = kb.note(arguments["id"], now)
        else:
            return _err(f"unknown tool {name}")
    except (NotInitialized, ValueError, OSError) as exc:
        return _err(str(exc))
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    }


def _store() -> Store:
    config.load_env()
    path = config.db_path()
    if not path.exists():
        raise NotInitialized("run `daytrace init` first")
    store = Store(path)
    day_zero(store)  # raises NotInitialized before tracking has started
    return store


def _when(value: str | None) -> datetime:
    if not value or value == "now":
        return datetime.now(UTC)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone() if dt.tzinfo is None else dt


def _bound(value: str | None, inclusive_end: bool = False) -> datetime | None:
    if not value:
        return None
    if len(value) == 10:
        start, end = day_bounds(date.fromisoformat(value))
        return end if inclusive_end else start
    return _when(value)


def _range(arguments: dict) -> tuple[date, date, str | None]:
    if arguments.get("start") and arguments.get("end"):
        return (
            date.fromisoformat(arguments["start"]),
            date.fromisoformat(arguments["end"]),
            None,
        )
    anchor = (
        date.fromisoformat(arguments["date"]) if arguments.get("date") else date.today()
    )
    kind = arguments.get("kind") or "day"
    if kind == "week":
        first, last = period.week_of(anchor)
        return first, last, "week"
    if kind == "month":
        first, last = period.month_of(anchor)
        return first, last, "month"
    return anchor, anchor, "day"


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()
