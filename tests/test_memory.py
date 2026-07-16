"""Tests for persistent conversational memory."""

from __future__ import annotations

import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.memory import MemoryStore  # noqa: E402


def _row_count(db_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM conversational_memory").fetchone()
    return int(row[0]) if row else 0


def test_memory_stores_explicit_feedback_and_preference(tmp_path):
    db_path = str(tmp_path / "sentinel.db")
    memory = MemoryStore(db_path, enabled=True, max_entries=20)
    memory.capture_explicit_feedback(
        question="Who was that?",
        answer="It looked like Jordan.",
        mode="footage",
        source="ledger",
        path="cloud_primary",
        helpful=False,
        correction="Call me Jordan from now on.",
        remember_preference=True,
    )
    assert _row_count(db_path) >= 2
    context = memory.build_prompt_context(limit=5)
    assert "preferred_name: Jordan" in context


def test_memory_infers_name_from_question(tmp_path):
    db_path = str(tmp_path / "sentinel.db")
    memory = MemoryStore(db_path, enabled=True, max_entries=20)
    memory.capture_inferred_feedback(
        question="I'm Casey, remember me.",
        answer="Nice to meet you Casey.",
    )
    context = memory.build_prompt_context(limit=5)
    assert "preferred_name: Casey" in context


def test_memory_prunes_when_over_limit(tmp_path):
    db_path = str(tmp_path / "sentinel.db")
    memory = MemoryStore(db_path, enabled=True, max_entries=10)
    for idx in range(18):
        memory.capture_explicit_feedback(
            question=f"Q{idx}",
            answer=f"A{idx}",
            mode="casual",
            source="conversation",
            path="cloud_primary",
            helpful=True,
            correction="",
            remember_preference=False,
        )
    assert _row_count(db_path) <= 10


def test_memory_status_reports_counts_and_preferences(tmp_path):
    db_path = str(tmp_path / "sentinel.db")
    memory = MemoryStore(db_path, enabled=True, max_entries=20)
    memory.capture_explicit_feedback(
        question="Who am I?",
        answer="You are Jordan.",
        mode="footage",
        source="ledger",
        path="cloud_primary",
        helpful=True,
        correction="Call me Jordan.",
        remember_preference=True,
    )
    status = memory.status(preference_limit=3)
    assert status["enabled"] is True
    assert status["entry_count"] >= 2
    assert status["max_entries"] == 20
    assert any(p["preference_key"] == "preferred_name" for p in status["preferences"])


def test_owner_profile_round_trip(tmp_path):
    db_path = str(tmp_path / "sentinel.db")
    memory = MemoryStore(db_path, enabled=True, max_entries=20)
    profile = memory.upsert_owner_profile(
        {
            "name": "Jordan",
            "role": "owner_admin",
            "appearance_signature": {"tattoos": True, "height_estimate": "6ft 0in"},
        }
    )
    assert profile["name"] == "Jordan"
    loaded = memory.get_owner_profile()
    assert loaded is not None
    assert loaded["role"] == "owner_admin"
    assert loaded["appearance_signature"]["tattoos"] is True


def test_resident_profile_round_trip(tmp_path):
    db_path = str(tmp_path / "sentinel.db")
    memory = MemoryStore(db_path, enabled=True, max_entries=20)
    saved = memory.upsert_resident_profile(
        "alex",
        {
            "name": "Alex",
            "role": "resident",
            "appearance_signature": {"height_estimate": "tall"},
            "preferences": {"tone": "calm"},
        },
    )
    assert saved["resident_id"] == "alex"
    listed = memory.list_resident_profiles(limit=10)
    assert listed
    assert listed[0]["resident_id"] == "alex"


def test_build_profile_context_includes_owner_and_residents(tmp_path):
    db_path = str(tmp_path / "sentinel.db")
    memory = MemoryStore(db_path, enabled=True, max_entries=20)
    memory.upsert_owner_profile(
        {
            "name": "Jordan",
            "role": "owner_admin",
            "appearance_signature": {"tattoos": True},
            "preferences": {"tone": "warm"},
            "personality_tone": {"style": "direct"},
        }
    )
    memory.upsert_resident_profile(
        "alex",
        {
            "name": "Alex",
            "role": "resident",
            "appearance_signature": {"height_estimate": "tall"},
        },
    )
    context = memory.build_profile_context(resident_limit=5)
    assert "owner: Jordan" in context
    assert "owner_appearance" in context
    assert "owner_preferences" in context
    assert "owner_tone" in context
    assert "resident: Alex" in context
    assert "height_estimate=tall" in context
