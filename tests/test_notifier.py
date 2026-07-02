"""Tests for phone notifications (Phase 7b)."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config  # noqa: E402
from sentinel.events import EventLedger  # noqa: E402
from sentinel.notifier import Notifier  # noqa: E402


def _make_config(**overrides):
    notifications = {
        "enabled": True,
        "provider": "ntfy",
        "topic": "test-topic",
        "cooldown_seconds": 0,
        "notify_on_tier": 2,
        "request_timeout_seconds": 5,
    }
    notifications.update(overrides)
    return Config({"notifications": notifications})


def _tier2_event(ledger: EventLedger):
    return ledger.insert(
        source="object",
        title="Unknown face detected",
        summary="tier 2 alert",
        tier=2,
        importance=0.9,
    )


def test_offline_when_disabled(tmp_path):
    notifier = Notifier(_make_config(enabled=False))
    ledger = EventLedger(str(tmp_path / "test.db"))
    event = _tier2_event(ledger)
    assert notifier.is_available() is False
    assert notifier.notify(event) is False


def test_offline_without_topic(tmp_path):
    notifier = Notifier(_make_config(topic=""))
    ledger = EventLedger(str(tmp_path / "test.db"))
    event = _tier2_event(ledger)
    assert notifier.is_available() is False
    assert notifier.notify(event) is False


def test_skips_tier1(tmp_path):
    captured = []

    def fake_post(url, headers, body, timeout):
        captured.append(url)
        return 200

    notifier = Notifier(_make_config(), http_post=fake_post)
    ledger = EventLedger(str(tmp_path / "test.db"))
    event = ledger.insert(
        source="motion",
        title="Motion",
        summary="low tier",
        tier=1,
    )
    assert notifier.notify(event) is False
    assert captured == []


def test_sends_ntfy_on_tier2(tmp_path):
    captured = {}

    def fake_post(url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = body.decode("utf-8")
        return 200

    notifier = Notifier(_make_config(), http_post=fake_post)
    ledger = EventLedger(str(tmp_path / "test.db"))
    event = _tier2_event(ledger)
    assert notifier.notify(event) is True
    assert captured["url"] == "https://ntfy.sh/test-topic"
    assert "Unknown face" in captured["body"]


def test_cooldown_blocks_second_send(tmp_path):
    calls = []

    def fake_post(url, headers, body, timeout):
        calls.append(1)
        return 200

    notifier = Notifier(_make_config(cooldown_seconds=60), http_post=fake_post)
    ledger = EventLedger(str(tmp_path / "test.db"))
    e1 = _tier2_event(ledger)
    e2 = _tier2_event(ledger)
    assert notifier.notify(e1) is True
    assert notifier.notify(e2) is False
    assert len(calls) == 1


def test_http_error_graceful(tmp_path):
    def fail_post(url, headers, body, timeout):
        return 500

    notifier = Notifier(_make_config(), http_post=fail_post)
    ledger = EventLedger(str(tmp_path / "test.db"))
    event = _tier2_event(ledger)
    assert notifier.notify(event) is False


def test_auth_token_from_secrets(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('ntfy_auth_token: "secret-token"\n', encoding="utf-8")
    captured = {}

    def fake_post(url, headers, body, timeout):
        captured["auth"] = headers.get("Authorization")
        return 200

    notifier = Notifier(
        _make_config(),
        secrets_path=str(secrets),
        http_post=fake_post,
    )
    ledger = EventLedger(str(tmp_path / "test.db"))
    notifier.notify(_tier2_event(ledger))
    assert captured["auth"] == "Bearer secret-token"


def test_mark_notified(tmp_path):
    ledger = EventLedger(str(tmp_path / "test.db"))
    event = ledger.insert(source="motion", title="t", summary="s", tier=2)
    assert event.notified is False
    assert ledger.mark_notified(event.id) is True
    updated = ledger.get_by_id(event.id)
    assert updated is not None
    assert updated.notified is True
