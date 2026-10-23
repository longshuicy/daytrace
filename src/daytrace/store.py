"""SQLite storage for events, cursors and metadata."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Event, iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id          TEXT PRIMARY KEY,
  source      TEXT NOT NULL,
  source_id   TEXT,
  kind        TEXT NOT NULL,
  evidence    TEXT NOT NULL,
  start_at    TEXT NOT NULL,
  end_at      TEXT,
  category    TEXT NOT NULL,
  project     TEXT,
  title       TEXT,
  url         TEXT,
  sensitivity TEXT NOT NULL,
  attrs       TEXT,
  ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_start ON events(start_at);

CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY, day TEXT, start_at TEXT, end_at TEXT,
  project TEXT, categories TEXT, sensitivity TEXT,
  label TEXT, event_ids TEXT
);
CREATE TABLE IF NOT EXISTS day_summaries (
  day TEXT PRIMARY KEY, stats TEXT, narrative TEXT, model TEXT, generated_at TEXT
);
CREATE TABLE IF NOT EXISTS cursors (source TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

UPSERT = """
INSERT INTO events (id, source, source_id, kind, evidence, start_at, end_at, category,
                    project, title, url, sensitivity, attrs, ingested_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  end_at = excluded.end_at, category = excluded.category, project = excluded.project,
  title = excluded.title, url = excluded.url, sensitivity = excluded.sensitivity,
  attrs = excluded.attrs, ingested_at = excluded.ingested_at
"""


class Store:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(episodes)")}
        if "details" not in columns:
            with self.conn:
                self.conn.execute("ALTER TABLE episodes ADD COLUMN details TEXT")
        if self.get_meta("privacy_model") != "private-list":
            # work/personal/unknown became normal/private; "ai" time became plain coding.
            with self.conn:
                self.conn.execute("UPDATE events SET sensitivity = 'normal' WHERE sensitivity IN ('work', 'unknown')")
                self.conn.execute("UPDATE events SET sensitivity = 'private', category = 'private', project = NULL, "
                                  "title = NULL, url = NULL, attrs = '{}' WHERE sensitivity = 'personal'")
                self.conn.execute("UPDATE events SET category = 'code' WHERE category = 'ai'")
                self.conn.execute("UPDATE episodes SET sensitivity = 'normal'")
            self.set_meta("privacy_model", "private-list")

    def close(self) -> None:
        self.conn.close()

    def upsert(self, events: list[Event], now: datetime) -> int:
        stamp = iso(now)
        rows = [
            (e.id, e.source, e.source_id, e.kind, e.evidence, e.start_at, e.end_at, e.category,
             e.project, e.title, e.url, e.sensitivity, json.dumps(e.attrs), stamp)
            for e in events
        ]
        with self.conn:
            self.conn.executemany(UPSERT, rows)
        return len(rows)

    def events_between(self, start: str, end: str) -> list[sqlite3.Row]:
        """Events overlapping [start, end)."""
        return self.conn.execute(
            "SELECT * FROM events WHERE start_at < ? AND COALESCE(end_at, start_at) >= ? "
            "ORDER BY start_at",
            (end, start),
        ).fetchall()

    def save_summary(self, day: str, stats: dict, narrative: str | None, model: str, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO day_summaries (day, stats, narrative, model, generated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(day) DO UPDATE SET stats = excluded.stats, narrative = excluded.narrative, "
                "model = excluded.model, generated_at = excluded.generated_at",
                (day, json.dumps(stats), narrative, model, iso(now)),
            )

    def summary(self, day: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM day_summaries WHERE day = ?", (day,)).fetchone()
        if not row:
            return None
        return {"stats": json.loads(row["stats"] or "{}"), "narrative": row["narrative"],
                "model": row["model"], "generated_at": row["generated_at"]}

    def has_source_ids(self, source: str, source_ids: list[str]) -> set[str]:
        found = set()
        for i in range(0, len(source_ids), 500):
            chunk = source_ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT source_id FROM events WHERE source = ? AND source_id IN ({marks})",
                (source, *chunk),
            )
            found.update(r[0] for r in rows)
        return found

    def replace_window(self, source: str, start: str, end: str, keep_ids: set[str]) -> int:
        """Delete this source's events starting in [start, end) that are not in keep_ids."""
        rows = self.conn.execute(
            "SELECT id FROM events WHERE source = ? AND start_at >= ? AND start_at < ?", (source, start, end)
        ).fetchall()
        stale = [r[0] for r in rows if r[0] not in keep_ids]
        with self.conn:
            self.conn.executemany("DELETE FROM events WHERE id = ?", [(i,) for i in stale])
        return len(stale)

    def delete_between(self, start: str, end: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM events WHERE start_at < ? AND COALESCE(end_at, start_at) >= ?",
                (end, start),
            )
            self.conn.execute(
                "DELETE FROM episodes WHERE start_at < ? AND end_at >= ?", (end, start)
            )
        return cur.rowcount

    def counts_by_source(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT source, COUNT(*) FROM events GROUP BY source")
        return {r[0]: r[1] for r in rows}

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_cursor(self, source: str) -> str | None:
        row = self.conn.execute("SELECT value FROM cursors WHERE source = ?", (source,)).fetchone()
        return row[0] if row else None

    def set_cursor(self, source: str, value: str, now: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO cursors (source, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (source, value, iso(now)),
            )
