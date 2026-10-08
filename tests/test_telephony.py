"""Tests for optional Twilio telephony stub (Phase 10)."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config  # noqa: E402
from sentinel.events import EventRecord  # noqa: E402
from sentinel.telephony import TelephonyBridge  # noqa: E402


def test_disabled_telephony_is_unavailable():
    bridge = TelephonyBridge(
        Config({"telephony": {"enabled": False}, "household": {"telephony_enabled": False}})
    )
    assert bridge.is_available() is False
    record = EventRecord(
        id=1,
        timestamp="2026-01-01T00:00:00",
        source="system",
        tier=2,
        importance=0.8,
        title="Door",
        summary="knock",
        entities={},
        snapshot_path=None,
        clip_path=None,
        acknowledged=False,
        notified=False,
    )
    result = bridge.connect_owner(record)
    assert result["ok"] is False
    assert "disabled" in result["message"].lower()
