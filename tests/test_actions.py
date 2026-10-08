"""Tests for notification ActionHandler (Phase 9D)."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.actions import ActionHandler  # noqa: E402
from sentinel.config import Config  # noqa: E402
from sentinel.events import EventLedger  # noqa: E402


def _cfg(public_base_url: str = "http://example.test:5000", **overrides):
    data = {
        "notifications": {
            "actions_enabled": True,
            "action_token_ttl_seconds": 900,
            "public_base_url": public_base_url,
            "topic": "test-topic",
        },
        "dashboard": {"token": "secret-token", "secrets_file": "secrets.yaml"},
    }
    data["notifications"].update(overrides)
    return Config(data)


def test_token_roundtrip_and_answer_door(tmp_path):
    spoken = {}

    def speak(text):
        spoken["text"] = text
        return {"ok": True, "spoken": True, "text": text}

    def brain_ask(question):
        return {"ok": True, "answer": "Hi there, how can I help?"}

    ledger = EventLedger(str(tmp_path / "events.db"))
    record = ledger.insert(
        source="system",
        title="Someone is knocking",
        summary="Visitor at door",
        tier=2,
        entities={
            "cloud_scene": {
                "short_summary": "person knocking",
                "activity": "knocking",
            }
        },
    )
    handler = ActionHandler(
        _cfg(),
        ledger,
        speak_fn=speak,
        brain_ask_fn=brain_ask,
    )
    token = handler.make_token("answer-door", record.id)
    assert handler.verify_token("answer-door", record.id, token) is True
    assert handler.verify_token("answer-door", record.id, "0.bad") is False

    result = handler.handle("answer-door", record.id, token)
    assert result["ok"] is True
    assert spoken["text"]
    updated = ledger.get_by_id(record.id)
    assert updated is not None
    assert updated.entities.get("action_taken") == "answer-door"


def test_ignore_marks_event(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.db"))
    record = ledger.insert(source="system", title="Alert", summary="x", tier=2)
    handler = ActionHandler(_cfg(), ledger)
    token = handler.make_token("ignore", record.id)
    result = handler.handle("ignore", record.id, token)
    assert result["ok"] is True
    assert ledger.get_by_id(record.id).entities.get("action_taken") == "ignore"


def test_action_urls_when_public_base_set(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.db"))
    handler = ActionHandler(_cfg(public_base_url="http://pi.local:5000"), ledger)
    urls = handler.action_urls(42)
    assert "answer-door" in urls
    assert urls["answer-door"].startswith("http://pi.local:5000/api/actions/answer-door")
    assert "token=" in urls["answer-door"]


def test_action_urls_empty_without_public_base(tmp_path):
    ledger = EventLedger(str(tmp_path / "events.db"))
    handler = ActionHandler(_cfg(public_base_url=""), ledger)
    assert handler.action_urls(1) == {}
