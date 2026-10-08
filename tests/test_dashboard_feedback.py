"""Tests for dashboard feedback capture endpoint."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config  # noqa: E402
from sentinel.dashboard import create_app  # noqa: E402


class _FakeBrain:
    def __init__(self):
        self.payload = None
        self.enroll_payload = None
        self.saved_resident = None
        self.preference_payload = None
        self.clear_payload = None

    def capture_feedback(self, payload):
        self.payload = payload
        return {"ok": True, "stored": True}

    def memory_status(self):
        return {
            "enabled": True,
            "entry_count": 3,
            "max_entries": 100,
            "preferences": [{"preference_key": "preferred_name", "preference_value": "Jordan"}],
        }

    def owner_profile_status(self):
        return {"ok": True, "configured": True, "profile": {"name": "Jordan"}}

    def enroll_owner_from_payload(self, payload):
        self.enroll_payload = payload
        return {"ok": True, "message": "enrolled"}

    def list_resident_profiles(self, limit=100):
        return [{"resident_id": "alex", "name": "Alex", "role": "resident"}]

    def save_resident_profile(self, resident_id, profile):
        self.saved_resident = (resident_id, profile)
        merged = dict(profile)
        merged["resident_id"] = resident_id
        return merged

    def set_owner_preference(self, key, value):
        self.preference_payload = (key, value)
        return {"name": "Jordan", "role": "owner_admin", "preferences": {key: value}}

    def clear_memory(self, preference_key=""):
        self.clear_payload = preference_key
        return {"ok": True, "deleted": 2, "scope": "all" if not preference_key else "preference"}


def test_feedback_endpoint_passes_payload_to_brain():
    fake_brain = _FakeBrain()
    runtime = SimpleNamespace(brain=fake_brain, ledger=SimpleNamespace(insert=lambda **_: None))
    app = create_app(Config({"dashboard": {"require_token": False}}), runtime=runtime)
    client = app.test_client()
    payload = {
        "response_id": "abc-123",
        "question": "What happened?",
        "answer": "A person came by.",
        "helpful": False,
        "correction": "It was the mail carrier.",
        "remember_preference": True,
    }
    resp = client.post("/api/chat/feedback", json=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert fake_brain.payload == payload
    # Backwards-compatible endpoint remains available.
    legacy = client.post("/api/feedback", json=payload)
    assert legacy.status_code == 200


def test_feedback_endpoint_handles_missing_runtime():
    app = create_app(Config({"dashboard": {"require_token": False}}), runtime=None)
    client = app.test_client()
    resp = client.post("/api/chat/feedback", json={"question": "x"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is False


def test_chat_memory_status_endpoint():
    fake_brain = _FakeBrain()
    runtime = SimpleNamespace(brain=fake_brain, ledger=SimpleNamespace(insert=lambda **_: None))
    app = create_app(Config({"dashboard": {"require_token": False}}), runtime=runtime)
    client = app.test_client()
    resp = client.get("/api/chat/memory/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["enabled"] is True
    assert data["entry_count"] == 3


def test_chat_memory_clear_endpoint():
    fake_brain = _FakeBrain()
    runtime = SimpleNamespace(brain=fake_brain, ledger=SimpleNamespace(insert=lambda **_: None))
    app = create_app(Config({"dashboard": {"require_token": False}}), runtime=runtime)
    client = app.test_client()
    resp = client.post("/api/chat/memory/clear", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["deleted"] == 2
    assert fake_brain.clear_payload == ""


def test_owner_profile_endpoints():
    fake_brain = _FakeBrain()
    runtime = SimpleNamespace(brain=fake_brain, ledger=SimpleNamespace(insert=lambda **_: None))
    app = create_app(Config({"dashboard": {"require_token": False}}), runtime=runtime)
    client = app.test_client()

    status_resp = client.get("/api/profile/owner")
    assert status_resp.status_code == 200
    assert status_resp.get_json()["configured"] is True

    enroll_payload = {"name": "Jordan", "role": "owner_admin"}
    enroll_resp = client.post("/api/profile/enroll-owner", json=enroll_payload)
    assert enroll_resp.status_code == 200
    assert enroll_resp.get_json()["ok"] is True
    assert fake_brain.enroll_payload == enroll_payload


def test_resident_endpoints_with_owner_gate():
    fake_brain = _FakeBrain()
    runtime = SimpleNamespace(brain=fake_brain, ledger=SimpleNamespace(insert=lambda **_: None))
    app = create_app(Config({"dashboard": {"require_token": False}}), runtime=runtime)
    client = app.test_client()

    list_resp = client.get("/api/profile/residents")
    assert list_resp.status_code == 200
    assert list_resp.get_json()["ok"] is True

    blocked = client.post(
        "/api/profile/resident",
        json={"resident_id": "alex", "actor_role": "resident"},
    )
    assert blocked.status_code == 403

    allowed_payload = {
        "resident_id": "alex",
        "name": "Alex",
        "role": "resident",
        "actor_role": "owner_admin",
        "actor_name": "Jordan",
    }
    allowed = client.post("/api/profile/resident", json=allowed_payload)
    assert allowed.status_code == 200
    assert allowed.get_json()["ok"] is True

    pref = client.post(
        "/api/profile/preferences",
        json={"actor_role": "owner_admin", "actor_name": "Jordan", "key": "tone", "value": "warm"},
    )
    assert pref.status_code == 200
    assert pref.get_json()["ok"] is True
