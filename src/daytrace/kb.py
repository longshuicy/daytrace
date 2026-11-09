"""What an agent can ask daytrace: a moment, a stretch of time, a keyword search, and one note.

Private spans leave as a start and an end. Slack permalinks stay out. Window titles are not in the search
index. Secrets and addresses are scrubbed before a result is returned, and each result is copied to the audit log.
Cursor, Claude Code, and Claude Desktop send that result on with their own prompts; `daytrace kb enable` is the
switch for the server.
"""

import json
import re
import sqlite3
from datetime import date, datetime, timedelta

from . import dayjson, egress, episodes, period, summarize
from .adapters import granola as notes
from .adapters.granola import EARLY
from .models import UTC, iso, parse_iso
from .report import clock, day_bounds
from .scrub import scrub
from .store import Store

# Traces that name a piece of work. Focused-window titles are a different question (`at` answers it).
SUBJECTS = {
    "commit",
    "pr",
    "review",
    "issue",
    "wiki",
    "release",
    "email",
    "inbox",
    "meeting",
}
MAX_HITS = 8
EXCERPT = 240
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_WORD = re.compile(r"[0-9A-Za-z]{2,}")
MEETING_LISTS = ("topics", "mine", "waiting", "unassigned")

_DOCS = """
DROP TABLE IF EXISTS kb_fts;
DROP TABLE IF EXISTS kb_docs;
CREATE TABLE kb_docs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  ref TEXT NOT NULL,
  at TEXT,
  project TEXT,
  source TEXT,
  category TEXT,
  title TEXT,
  body TEXT
);
CREATE VIRTUAL TABLE kb_fts USING fts5(
  title, body, content='kb_docs', content_rowid='id', tokenize='porter unicode61'
);
"""


def enabled(store: Store) -> bool:
    return store.get_meta("kb") == "on"


def enable(store: Store) -> None:
    store.set_meta("kb", "on")


def at(
    store: Store, when: datetime, window: int = 15, now: datetime | None = None
) -> dict:
    """Blocks and points overlapping [when - window, when + window)."""
    start, end = when - timedelta(minutes=window), when + timedelta(minutes=window)
    payload = {
        "time": dayjson._local(when),
        "window_min": window,
        **_slice(store, start, end),
    }
    return _release("at", payload, now or datetime.now(UTC))


def summary(
    store: Store, first: date, last: date, now: datetime, kind: str | None = None
) -> dict:
    """Totals for first..last, plus meeting digests and the open "mine" items. One day also includes its blocks."""
    from .sync import day_zero

    zero = day_zero(store)
    built = period.build(
        store, first, last, zero, now, kind if kind in ("week", "month") else "week"
    )
    private = 0
    collected: list[dict] = []
    traces: list[tuple[datetime, str, str]] = []
    for row in built["days"]:
        if row["future"] or not row["has_data"]:
            continue
        day = date.fromisoformat(row["date"])
        start, end = day_bounds(day)
        rows = store.events_between(iso(start), iso(end))
        private += dayjson.build(day, rows, {}, {}, zero, now)["summary"]["private_min"]
        found = _meeting_notes(rows, start, end)
        if found:
            collected.append({"meetings": found})
        # Any day's commits can close an earlier meeting's items, not only days with notes.
        traces += summarize._traces(rows, start, end, now)
    summarize.drop_closed(collected, traces)
    meetings = [_public_meeting(m) for day in collected for m in day["meetings"]]
    story = None
    if kind in ("week", "month"):
        view = dayjson.story_view(summarize.current_period(store, kind, first, last))
        if view and view.get("narrative"):
            story = {
                k: view[k] for k in ("narrative", "themes", "observations", "next_week")
            }
    payload = {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "totals": {
            "active_min": built["totals"]["active_min"],
            "meeting_min": built["totals"]["meeting_min"],
            "meetings": built["totals"]["meetings"],
            "emails_sent": built["totals"]["emails_sent"],
            "emails_received": built["totals"]["emails_received"],
            "chats_sent": built["totals"]["chats_sent"],
            "commits": (built["commits"] or {}).get("count", 0),
            "private_min": private,
            "focus": built["focus"],
        },
        "projects": [
            {
                "project": p["project"],
                "minutes": p["minutes"],
                "inferred_min": p["inferred_min"],
                "commits": p["commits"],
                "prs": p["prs"],
            }
            for p in built["projects"]
        ],
        "days": [
            {
                "date": r["date"],
                "active_min": r["active_min"],
                "meeting_min": r["meeting_min"],
                "emails_sent": r["emails_sent"],
                "chats_sent": r["chats_sent"],
                "focus_min": r["focus_min"],
            }
            for r in built["days"]
            if not r["future"]
        ],
        "shipped": [
            {
                "date": x["date"],
                "title": x["title"],
                "project": x["project"],
                "category": x["category"],
                **(
                    {}
                    if not x.get("url") or "slack.com" in x["url"]
                    else {"url": x["url"]}
                ),
            }
            for x in built["shipped"]
        ],
        "meetings": meetings,
        "open": [
            {
                "project": m.get("project"),
                "text": item,
                "meeting": m.get("title"),
                "at": m.get("at"),
            }
            for m in meetings
            for item in m.get("mine", [])
        ],
        "story": story,
    }
    if first == last:
        payload.update(_slice(store, *day_bounds(first)))
    return _release("summary", payload, now)


def search(
    store: Store,
    query: str,
    now: datetime,
    project: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    """Up to eight hits: notes, then subjects, then a stored write-up. Window titles are not indexed."""
    reindex(store)
    words = _WORD.findall(query)
    if not words:
        return _release("search", {"query": query, "hits": []}, now)
    match = " AND ".join(f'"{w}"' for w in words)
    rows = store.conn.execute(
        "SELECT d.kind, d.ref, d.at, d.project, d.source, d.category, d.title, d.body, bm25(kb_fts) AS rank "
        "FROM kb_fts JOIN kb_docs d ON d.id = kb_fts.rowid WHERE kb_fts MATCH ? "
        "AND (? = '' OR d.at >= ?) AND (? = '' OR d.at < ?) AND (? = '' OR lower(d.project) = lower(?)) "
        "ORDER BY CASE d.kind WHEN 'note' THEN 0 WHEN 'subject' THEN 1 ELSE 2 END, rank LIMIT ?",
        (
            match,
            iso(start) if start else "",
            iso(start) if start else "",
            iso(end) if end else "",
            iso(end) if end else "",
            project or "",
            project or "",
            MAX_HITS,
        ),
    ).fetchall()
    hits = []
    for r in rows:
        if r["kind"] == "note":
            digest = _digest(r["ref"])
            hits.append(
                {
                    "kind": "note",
                    "id": r["ref"],
                    "at": r["at"],
                    "title": r["title"],
                    "project": r["project"],
                    "excerpt": _excerpt(r["body"] or "", words),
                    **({"digest": digest} if digest else {}),
                }
            )
        elif r["kind"] == "subject":
            hits.append(
                {
                    "kind": "subject",
                    "at": r["at"],
                    "source": r["source"],
                    "category": r["category"],
                    "project": r["project"],
                    "title": r["title"],
                }
            )
        else:
            hits.append(
                {
                    "kind": "narrative",
                    "id": r["ref"],
                    "at": r["at"],
                    "excerpt": _excerpt(r["body"] or "", words),
                }
            )
    return _release("search", {"query": query, "hits": hits}, now)


def note(note_id: str, now: datetime | None = None) -> dict:
    """One saved Granola note: its title, when it was taken, and the Markdown body."""
    saved = notes.read_note(notes.notes_dir(), note_id)
    payload = {
        "id": saved["id"],
        "title": saved["title"],
        "meeting_at": saved["meeting_at"],
        "markdown": saved["markdown"],
    }
    return _release("note", payload, now or datetime.now(UTC))


def reindex(store: Store) -> int:
    """Rebuild the keyword index from subjects, Granola notes, and stored write-ups."""
    conn = store.conn
    conn.executescript(_DOCS)
    docs = []
    for r in conn.execute("SELECT * FROM events"):
        if (
            r["sensitivity"] == "private"
            or r["source"] == "activitywatch"
            or not r["title"]
        ):
            continue
        if r["category"] not in SUBJECTS and r["kind"] != "plan":
            continue
        docs.append(
            (
                "subject",
                r["id"],
                r["start_at"],
                r["project"],
                r["source"],
                r["category"],
                r["title"],
                "",
            )
        )
    folder = notes.notes_dir()
    for note_id, entry in notes.load_index(folder).items():
        if not (folder / entry.get("path", "")).exists():
            continue
        body = notes.read_note(folder, note_id)["markdown"]
        docs.append(
            (
                "note",
                note_id,
                entry.get("meeting_at"),
                _note_project(store, entry.get("meeting_at")),
                "granola",
                "notes",
                entry.get("title"),
                body,
            )
        )
    for row in conn.execute(
        "SELECT day, stats, narrative, generated_at FROM day_summaries"
    ):
        if not row["narrative"]:
            continue
        stats = json.loads(row["stats"] or "{}")
        parts = [row["narrative"]]
        parts += [
            o.get("text", "")
            for o in stats.get("observations") or []
            if isinstance(o, dict)
        ]
        parts += [t for t in stats.get("themes") or [] if isinstance(t, str)]
        docs.append(
            (
                "narrative",
                row["day"],
                row["generated_at"],
                None,
                None,
                None,
                row["day"],
                "\n".join(parts),
            )
        )
    conn.executemany(
        "INSERT INTO kb_docs (kind, ref, at, project, source, category, title, body) VALUES (?,?,?,?,?,?,?,?)",
        docs,
    )
    conn.execute("INSERT INTO kb_fts(kb_fts) VALUES('rebuild')")
    conn.commit()
    return len(docs)


def _slice(store: Store, start: datetime, end: datetime) -> dict:
    rows = store.events_between(iso(start), iso(end))
    spans, points = [], []
    for r in rows:
        if r["end_at"]:
            s, e = max(parse_iso(r["start_at"]), start), min(
                parse_iso(r["end_at"]), end
            )
            if e > s:
                spans.append((r, s, e))
        else:
            t = parse_iso(r["start_at"])
            if start <= t < end:
                points.append(r)
    found = _meeting_notes(rows, start, end)
    by_event = {m["event_id"]: m for m in found if m.get("event_id")}
    blocks = []
    for block in dayjson._blocks(spans):
        presented = _present_block(block, spans)
        for r, s, e in spans:
            if r["id"] in by_event and _same_block(block, r, s, e):
                presented.update(
                    note_id=by_event[r["id"]]["note_id"],
                    digest=_digest_fields(by_event[r["id"]]),
                )
                break
        blocks.append(presented)
    presented_points = [_present_point(p) for p in points]
    attached = {b.get("note_id") for b in blocks}
    for m in found:
        at = parse_iso(m["at"])
        if m["note_id"] in attached or not (start <= at < end):
            continue
        presented_points.append(
            {
                "at": dayjson._local(at),
                "source": "granola",
                "category": "notes",
                "title": m.get("title"),
                "note_id": m["note_id"],
                "digest": _digest_fields(m),
            }
        )
    return {"blocks": blocks, "points": presented_points}


def _present_block(block: dict, spans: list) -> dict:
    if block["sensitivity"] == "private":
        return {"start": block["start"], "end": block["end"], "category": "private"}
    out = {
        "start": block["start"],
        "end": block["end"],
        "minutes": block["minutes"],
        "source": block["source"],
        "category": block["category"],
        "project": block["project"],
        "titles": block["titles"],
    }
    app = _app(block, spans)
    if app:
        out["app"] = app
    return out


def _present_point(row: sqlite3.Row) -> dict:
    when = dayjson._local(parse_iso(row["start_at"]))
    if row["sensitivity"] == "private":
        return {"at": when, "category": "private"}
    attrs = json.loads(row["attrs"] or "{}")
    out = {
        "at": when,
        "source": row["source"],
        "category": row["category"],
        "project": row["project"],
        "title": row["title"],
    }
    if attrs.get("app"):
        out["app"] = attrs["app"]
    if row["source"] == "slack" and attrs.get("count"):
        out["count"] = attrs["count"]
    return out


def _app(block: dict, spans: list) -> str | None:
    b0, b1 = datetime.fromisoformat(block["start"]), datetime.fromisoformat(
        block["end"]
    )
    counts: dict[str, float] = {}
    for r, s, e in spans:
        if r["sensitivity"] == "private" or r["source"] != block["source"]:
            continue
        if (
            dayjson.category_of(r) != block["category"]
            or r["project"] != block["project"]
        ):
            continue
        if e <= b0 or s >= b1:
            continue
        app = json.loads(r["attrs"] or "{}").get("app")
        if app:
            counts[app] = counts.get(app, 0) + (min(e, b1) - max(s, b0)).total_seconds()
    return max(counts, key=counts.get) if counts else None


def _same_block(block: dict, row: sqlite3.Row, start: datetime, end: datetime) -> bool:
    if row["sensitivity"] == "private" or block["sensitivity"] == "private":
        return False
    if (
        row["source"] != block["source"]
        or dayjson.category_of(row) != block["category"]
    ):
        return False
    if row["project"] != block["project"]:
        return False
    b0, b1 = datetime.fromisoformat(block["start"]), datetime.fromisoformat(
        block["end"]
    )
    return start < b1 and end > b0


def _meeting_notes(rows, start: datetime, end: datetime) -> list[dict]:
    """Notes taken in meetings that overlap [start, end), with the note id the week payload leaves off."""
    folder = notes.notes_dir()
    if not notes.load_index(folder):
        return []
    earliest = min((parse_iso(r["start_at"]) for r in rows), default=start) - EARLY
    found = notes.notes_between(folder, earliest, end + timedelta(seconds=1))
    if not found:
        return []
    people = summarize.People.load()

    def hosts(source, category=None):
        return sorted(
            (parse_iso(r["start_at"]), parse_iso(r["end_at"]), r)
            for r in rows
            if r["source"] == source
            and r["sensitivity"] != "private"
            and r["end_at"]
            and (category is None or r["category"] == category)
        )

    out, leftover = [], list(found)
    for group in (hosts("outlook_calendar"), hosts("slack", "meeting")):
        still = []
        for n, i in zip(leftover, notes.match(leftover, [(s, e) for s, e, _ in group])):
            if i is None:
                still.append(n)
                continue
            s, e, r = group[i]
            if e <= start or s >= end:
                still.append(n)
                continue
            out.append(_noted(folder, n, people, r, s, e))
        leftover = still
    for n in leftover:
        at = parse_iso(n["meeting_at"])
        if start <= at < end:
            out.append(_noted(folder, n, people, None, at, None))
    return out


def _noted(
    folder,
    entry: dict,
    people: summarize.People,
    row,
    start: datetime,
    end: datetime | None,
) -> dict:
    digest = notes.digest(folder, entry["id"], people.names, people.me)
    attrs = json.loads(row["attrs"] or "{}") if row is not None else {}
    title = episodes.shared_title(row, attrs) if row is not None else entry.get("title")
    when = f"{clock(start)}–{clock(end)}" if end is not None else clock(start)
    return {
        "event_id": row["id"] if row is not None else None,
        "note_id": entry["id"],
        "at": iso(start),
        "time": when,
        "project": row["project"] if row is not None else None,
        "title": title,
        **digest,
    }


def _digest(note_id: str) -> dict | None:
    folder = notes.notes_dir()
    if note_id not in notes.load_index(folder):
        return None
    people = summarize.People.load()
    digest = notes.digest(folder, note_id, people.names, people.me)
    return _digest_fields(digest) or None


def _digest_fields(meeting: dict) -> dict:
    return {k: meeting.get(k, []) for k in MEETING_LISTS}


def _public_meeting(meeting: dict) -> dict:
    return {
        k: meeting[k]
        for k in ("note_id", "at", "time", "project", "title", *MEETING_LISTS)
        if k in meeting
    }


def _note_project(store: Store, meeting_at: str | None) -> str | None:
    if not meeting_at:
        return None
    at = parse_iso(meeting_at)
    rows = store.events_between(iso(at - EARLY), iso(at + timedelta(seconds=1)))
    hosts = [
        r
        for r in rows
        if r["kind"] == "plan"
        and r["sensitivity"] != "private"
        and r["end_at"]
        and parse_iso(r["start_at"]) - EARLY <= at < parse_iso(r["end_at"])
    ]
    return hosts[-1]["project"] if hosts else None


def _excerpt(text: str, words: list[str]) -> str:
    low = text.lower()
    positions = [low.find(w.lower()) for w in words if w.lower() in low]
    pos = min(positions) if positions else 0
    start = max(0, pos - EXCERPT // 3)
    clip = " ".join(text[start:start + EXCERPT].split())
    if start:
        clip = "…" + clip
    if start + EXCERPT < len(text):
        clip += "…"
    return clip


def _release(tool: str, payload: dict, now: datetime) -> dict:
    cleaned = _scrub(payload)
    try:
        egress.check(cleaned)
    except ValueError:
        cleaned = _force(cleaned)
        egress.check(cleaned)
    egress.write_audit(
        "mcp", f"{tool}-{now.microsecond:06d}", {"tool": tool, "result": cleaned}, now
    )
    return cleaned


def _scrub(value):
    if isinstance(value, str):
        return _EMAIL.sub("[email]", scrub(value) or "")
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def _dirty(value) -> bool:
    if isinstance(value, str):
        return scrub(value) != value or _EMAIL.search(value) is not None
    if isinstance(value, list):
        return any(_dirty(v) for v in value)
    if isinstance(value, dict):
        return any(_dirty(v) for v in value.values())
    return False


def _force(value):
    if isinstance(value, str):
        return "[redacted]" if _dirty(value) else value
    if isinstance(value, list):
        return [_force(v) for v in value]
    if isinstance(value, dict):
        return {k: _force(v) for k, v in value.items()}
    return value
