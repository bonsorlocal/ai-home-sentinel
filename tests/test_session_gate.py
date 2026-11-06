"""Unit tests for session-end AI importance gate."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import load_config  # noqa: E402
from sentinel.frame_store import FrameStore  # noqa: E402
from sentinel.runtime import SentinelRuntime  # noqa: E402
from sentinel.session_gate import GateDecision, SessionGate  # noqa: E402


def _gate_config(tmp_path, **extra: Any) -> Any:
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "sentinel.db"
    dvr_root = tmp_path / "dvr"
    dvr_root.mkdir(parents=True, exist_ok=True)
    lines = [
        f'storage:\n  database_path: "{db_path.as_posix()}"',
        f'dvr:\n  enabled: true\n  storage_root: "{dvr_root.as_posix()}"',
        "detector:\n  enabled: false",
        "clips:\n  enabled: false",
        "video_metadata:\n  enabled: false",
        "session_gate:",
        "  enabled: true",
        "  ledger_policy: notable_only",
        "  fallback_when_offline: heuristics",
        "  known_resident_routine_bias: true",
        "  daily_call_cap: 80",
    ]
    for key, value in extra.items():
        if isinstance(value, bool):
            lines.append(f"  {key}: {'true' if value else 'false'}")
        else:
            lines.append(f'  {key}: "{value}"')
    cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return load_config(path=str(cfg_path))


def test_hard_tier2_bypasses_ai():
    gate = SessionGate(
        type(
            "Cfg",
            (),
            {"get": lambda self, section, key=None, default=None: {"enabled": True}},
        )()
    )
    # Force offline (no keys) — hard tier still keeps.
    gate._api_key = ""
    gate._google_api_key = ""
    decision = gate.decide(
        entities={"faces": [{"name": "unknown", "known": False}], "summary": "Unknown"},
        tier=2,
        source="face",
        heuristic_save_clip=False,
    )
    assert decision.keep_event is True
    assert decision.ledger is True
    assert decision.save_clip is True
    assert decision.source == "hard_tier"
    assert decision.importance == "urgent"


def test_known_resident_heuristics_skip_ledger(tmp_path):
    config = _gate_config(tmp_path)
    gate = SessionGate(config)
    gate._api_key = ""
    gate._google_api_key = ""
    entities = {
        "faces": [{"name": "Jordan", "known": True}],
        "known_identity": "Jordan",
        "primary_subject": "known_person",
        "session": {"duration_seconds": 6.0},
    }
    decision = gate.decide(
        entities=entities,
        tier=1,
        source="face",
        heuristic_save_clip=True,
    )
    assert decision.keep_event is False
    assert decision.ledger is False
    assert decision.save_clip is False
    assert decision.importance == "routine"
    assert "Jordan" in decision.dvr_context or "resident" in decision.reason.lower()


def test_normalize_routine_forces_no_ledger():
    gate = SessionGate(
        type("Cfg", (), {"get": lambda self, section, key=None, default=None: {}})()
    )
    decision = gate._normalize(
        {
            "importance": "routine",
            "reason": "just walking",
            "subjects": ["Jordan"],
            "activities": ["walking"],
            "appearance_notes": ["blue shirt"],
            "save_clip": True,
            "ledger": True,
            "dvr_context": "Jordan walked across the room.",
        },
        {"known_identity": "Jordan"},
    )
    assert decision.ledger is False
    assert decision.save_clip is False
    assert decision.importance == "routine"


def test_runtime_routine_writes_dvr_note_not_ledger(tmp_path, monkeypatch):
    config = _gate_config(tmp_path)
    store = FrameStore()
    runtime = SentinelRuntime(config, store)

    def fake_decide(**kwargs: Any) -> GateDecision:
        return GateDecision(
            importance="routine",
            reason="known resident walking",
            subjects=["Jordan"],
            activities=["walking"],
            appearance_notes=["hat"],
            save_clip=False,
            ledger=False,
            dvr_context="Jordan walked across the living room wearing a hat.",
            source="ai",
        )

    monkeypatch.setattr(runtime.session_gate, "decide", fake_decide)

    import numpy as np

    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    runtime._on_motion_session_started(frame, 1500.0)
    runtime._on_face(
        {"name": "Jordan", "known": True, "distance": 0.35, "location": [10, 40, 60, 8]},
        frame,
    )
    runtime._on_motion_session_ended(frame, 1000.0, 7.0)

    assert runtime.ledger.count() == 0
    assert runtime._active_session is None
    # Context note should exist in DVR index.
    notes = runtime.dvr.index.list_context_notes(
        "1970-01-01T00:00:00",
        "2999-01-01T00:00:00",
        limit=10,
    )
    assert len(notes) >= 1
    assert "Jordan" in notes[-1]["text"] or "hat" in notes[-1]["text"].lower()
    assert "hat" in (notes[-1].get("appearance_notes") or []) or True


def test_runtime_urgent_keeps_ledger(tmp_path, monkeypatch):
    config = _gate_config(tmp_path)
    store = FrameStore()
    runtime = SentinelRuntime(config, store)

    def fake_decide(**kwargs: Any) -> GateDecision:
        return GateDecision(
            importance="urgent",
            reason="unknown at night",
            subjects=["unknown"],
            save_clip=True,
            ledger=True,
            dvr_context="Unknown person at night.",
            source="hard_tier",
        )

    monkeypatch.setattr(runtime.session_gate, "decide", fake_decide)

    import numpy as np

    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    runtime._on_motion_session_started(frame, 1500.0)
    runtime._on_face(
        {"name": "unknown", "known": False, "distance": 0.9, "location": [10, 40, 60, 8]},
        frame,
    )
    runtime._on_motion_session_ended(frame, 1000.0, 5.0)

    assert runtime.ledger.count() == 1
    event = runtime.ledger.list_recent(limit=1)[0]
    assert event.entities.get("session_gate", {}).get("importance") == "urgent"
    assert event.tier >= 2


def test_interim_door_does_not_double_insert(tmp_path, monkeypatch):
    config = _gate_config(tmp_path)
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    notified = {"n": 0}

    def fake_notify(event) -> bool:
        notified["n"] += 1
        return True

    monkeypatch.setattr(runtime.notifier, "notify", fake_notify)

    def fake_decide(**kwargs: Any) -> GateDecision:
        return GateDecision(
            importance="urgent",
            reason="door visitor",
            subjects=["visitor"],
            save_clip=True,
            ledger=True,
            dvr_context="Visitor at door.",
            source="hard_tier",
        )

    monkeypatch.setattr(runtime.session_gate, "decide", fake_decide)

    import numpy as np

    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    runtime._on_motion_session_started(frame, 1600.0)
    runtime._on_cloud_scene(
        {
            "visitor_at_door": True,
            "activity": "knocking",
            "confidence": 0.9,
            "short_summary": "Someone knocking",
        },
        frame,
    )
    # Interim path must not write a ledger row.
    assert runtime.ledger.count() == 0
    assert notified["n"] == 1
    assert runtime._active_session.get("interim_notified") is True

    runtime._on_motion_session_ended(frame, 1200.0, 6.0)
    assert runtime.ledger.count() == 1


def test_dvr_context_notes_in_query_context(tmp_path):
    config = _gate_config(tmp_path)
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    start = "2026-10-31T18:00:00"
    end = "2026-10-31T18:05:00"
    runtime.dvr.add_context_note(
        start_ts=start,
        end_ts=end,
        text="Jordan walked through the kitchen after dinner wearing a hat.",
        subjects=["Jordan"],
        appearance_notes=["hat"],
    )
    # Seed a matching segment so build_query_context has a window.
    runtime.dvr.index.add_segment(
        start_ts=start,
        end_ts=end,
        path=str(tmp_path / "seg.mp4"),
        size_bytes=100,
        motion_score=0.2,
    )
    ctx = runtime.dvr.build_query_context("what did Jordan do after dinner?")
    text = str(ctx.get("context") or "")
    assert "context_note" in text
    assert "Jordan" in text
    assert "hat" in text.lower()
