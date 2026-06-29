"""Event Ledger module (Phase 3).

Records every noticed thing in a single SQLite database with a locked schema
used by all later phases. See ROADMAP.md Section 2 for the schema definition.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sentinel.utils import now_iso

# Locked Event Ledger schema — do not change column names after Phase 3a gate.
EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    tier INTEGER NOT NULL DEFAULT 1,
    importance REAL NOT NULL DEFAULT 0.0,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    entities TEXT NOT NULL DEFAULT '{}',
    snapshot_path TEXT,
    clip_path TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
"""

VALID_SOURCES = frozenset({"motion", "object", "face", "audio", "system"})


@dataclass
class EventRecord:
    """One row from the Event Ledger."""

    id: int
    timestamp: str
    source: str
    tier: int
    importance: float
    title: str
    summary: str
    entities: Dict[str, Any]
    snapshot_path: Optional[str]
    clip_path: Optional[str]
    acknowledged: bool
    notified: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "source": self.source,
            "tier": self.tier,
            "importance": self.importance,
            "title": self.title,
            "summary": self.summary,
            "entities": self.entities,
            "snapshot_path": self.snapshot_path,
            "clip_path": self.clip_path,
            "acknowledged": self.acknowledged,
            "notified": self.notified,
        }


class EventLedger:
    """Thread-safe SQLite Event Ledger."""

    def __init__(self, database_path: str):
        self._path = database_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(database_path)), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.executescript(EVENTS_TABLE_SQL)
                conn.commit()

    def insert(
        self,
        *,
        source: str,
        title: str,
        summary: str = "",
        tier: int = 1,
        importance: float = 0.0,
        entities: Optional[Dict[str, Any]] = None,
        snapshot_path: Optional[str] = None,
        clip_path: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> EventRecord:
        """Insert a new event and return the full record."""
        if source not in VALID_SOURCES:
            raise ValueError(f"Invalid source '{source}'; must be one of {sorted(VALID_SOURCES)}")

        ts = timestamp or now_iso()
        entities_json = json.dumps(entities or {})

        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO events (
                        timestamp, source, tier, importance, title, summary,
                        entities, snapshot_path, clip_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        source,
                        int(tier),
                        float(importance),
                        title,
                        summary,
                        entities_json,
                        snapshot_path,
                        clip_path,
                    ),
                )
                conn.commit()
                row_id = int(cursor.lastrowid)

        record = self.get_by_id(row_id)
        if record is None:
            raise RuntimeError(f"Failed to read back inserted event id={row_id}")
        return record

    def get_by_id(self, event_id: int) -> Optional[EventRecord]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM events WHERE id = ?", (event_id,)
                ).fetchone()
        return _row_to_record(row) if row else None

    def list_recent(self, limit: int = 50, source: Optional[str] = None) -> List[EventRecord]:
        """Return the most recent events, newest first."""
        limit = max(1, min(int(limit), 500))
        with self._lock:
            with self._connect() as conn:
                if source:
                    rows = conn.execute(
                        """
                        SELECT * FROM events WHERE source = ?
                        ORDER BY timestamp DESC, id DESC LIMIT ?
                        """,
                        (source, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM events
                        ORDER BY timestamp DESC, id DESC LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
        return [_row_to_record(row) for row in rows]

    def count(self) -> int:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return int(row["n"]) if row else 0


def _row_to_record(row: sqlite3.Row) -> EventRecord:
    try:
        entities = json.loads(row["entities"] or "{}")
    except json.JSONDecodeError:
        entities = {}
    if not isinstance(entities, dict):
        entities = {}

    return EventRecord(
        id=int(row["id"]),
        timestamp=str(row["timestamp"]),
        source=str(row["source"]),
        tier=int(row["tier"]),
        importance=float(row["importance"]),
        title=str(row["title"]),
        summary=str(row["summary"] or ""),
        entities=entities,
        snapshot_path=row["snapshot_path"],
        clip_path=row["clip_path"],
        acknowledged=bool(row["acknowledged"]),
        notified=bool(row["notified"]),
    )
