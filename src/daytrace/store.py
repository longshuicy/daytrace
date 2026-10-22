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
