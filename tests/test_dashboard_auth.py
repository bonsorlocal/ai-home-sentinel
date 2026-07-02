"""Tests for dashboard token authentication."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config  # noqa: E402
from sentinel.dashboard import create_app  # noqa: E402


def _app(require_token: bool, token: str = "secret123"):
    config = Config(
        {
            "dashboard": {
                "require_token": require_token,
                "token": token,
            }
        }
    )
    return create_app(config)


def test_health_always_open():
    app = _app(require_token=True, token="secret123")
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_no_auth_when_disabled():
    app = _app(require_token=False)
    client = app.test_client()
    assert client.get("/status").status_code == 200
    assert client.get("/").status_code == 200


def test_api_requires_token_when_enabled():
    app = _app(require_token=True, token="secret123")
    client = app.test_client()
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/events?token=secret123").status_code == 200


def test_bearer_token_accepted():
    app = _app(require_token=True, token="secret123")
    client = app.test_client()
    resp = client.get(
        "/status",
        headers={"Authorization": "Bearer secret123"},
    )
    assert resp.status_code == 200


def test_wrong_token_rejected():
    app = _app(require_token=True, token="secret123")
    client = app.test_client()
    assert client.get("/events?token=wrong").status_code == 401


def test_query_token_sets_cookie_for_followup_api_calls():
    app = _app(require_token=True, token="secret123")
    client = app.test_client()
    first = client.get("/events?token=secret123")
    assert first.status_code == 200
    assert "sentinel_dashboard_token=" in (first.headers.get("Set-Cookie") or "")
    second = client.get("/api/events")
    assert second.status_code == 200
