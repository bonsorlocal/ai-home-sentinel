"""Tests for the Event Ledger (Phase 3a)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.events import EVENTS_TABLE_SQL, EventLedger, VALID_SOURCES  # noqa: E402


def test_schema_columns(tmp_path):
    db_path = str(tmp_path / "test.db")
    EventLedger(db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute("PRAGMA table_info(events)").fetchall()
    conn.close()

    columns = [row[1] for row in rows]
    expected = [
        "id",
        "timestamp",
        "source",
        "tier",
        "importance",
        "title",
        "summary",
        "entities",
        "snapshot_path",
        "clip_path",
        "acknowledged",
        "notified",
    ]
    assert columns == expected


def test_insert_and_get_by_id(tmp_path):
    ledger = EventLedger(str(tmp_path / "test.db"))
    record = ledger.insert(
        source="motion",
        title="Motion detected",
        summary="Movement in frame",
        tier=1,
        importance=0.3,
        entities={"area": 1500},
        snapshot_path="/tmp/snap.jpg",
    )

    assert record.id >= 1
    assert record.source == "motion"
    assert record.title == "Motion detected"
    assert record.entities == {"area": 1500}
    assert record.snapshot_path == "/tmp/snap.jpg"
    assert record.acknowledged is False
    assert record.notified is False

    fetched = ledger.get_by_id(record.id)
    assert fetched is not None
    assert fetched.title == "Motion detected"


def test_list_recent_ordered(tmp_path):
    ledger = EventLedger(str(tmp_path / "test.db"))
    ledger.insert(source="motion", title="First", timestamp="2026-01-01T10:00:00")
    ledger.insert(source="motion", title="Second", timestamp="2026-01-01T11:00:00")
    ledger.insert(source="object", title="Person", timestamp="2026-01-01T12:00:00")

    all_events = ledger.list_recent(limit=10)
    assert len(all_events) == 3
    assert all_events[0].title == "Person"

    motion_only = ledger.list_recent(limit=10, source="motion")
    assert len(motion_only) == 2
    assert all(e.source == "motion" for e in motion_only)


def test_invalid_source_raises(tmp_path):
    ledger = EventLedger(str(tmp_path / "test.db"))
    try:
        ledger.insert(source="invalid", title="Bad")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_entities_stored_as_json(tmp_path):
    ledger = EventLedger(str(tmp_path / "test.db"))
    entities = {"persons": ["Alice"], "count": 2}
    record = ledger.insert(
        source="object",
        title="Detections",
        entities=entities,
    )
    assert record.entities == entities

    conn = sqlite3.connect(str(tmp_path / "test.db"))
    raw = conn.execute(
        "SELECT entities FROM events WHERE id = ?", (record.id,)
    ).fetchone()[0]
    conn.close()
    assert json.loads(raw) == entities


def test_valid_sources():
    assert "motion" in VALID_SOURCES
    assert "object" in VALID_SOURCES
    assert "face" in VALID_SOURCES


def test_count(tmp_path):
    ledger = EventLedger(str(tmp_path / "test.db"))
    assert ledger.count() == 0
    ledger.insert(source="system", title="Startup")
    assert ledger.count() == 1
