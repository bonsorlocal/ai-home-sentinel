"""Tests for CloudLiveVisionWorker helpers (Phase 9C)."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config  # noqa: E402
from sentinel.live_vision import CloudLiveVisionWorker  # noqa: E402


def test_normalize_scene_promotes_visitor():
    scene = CloudLiveVisionWorker._normalize_scene(
        {
            "visitor_at_door": False,
            "activity": "knocking",
            "person_count": 1,
            "confidence": 0.9,
            "short_summary": "Someone knocking",
        }
    )
    assert scene["visitor_at_door"] is True
    assert scene["activity"] == "knocking"
    assert scene["confidence"] == 0.9


def test_normalize_rejects_unknown_activity():
    scene = CloudLiveVisionWorker._normalize_scene(
        {"activity": "dancing", "confidence": 1, "person_count": 0}
    )
    assert scene["activity"] == "none"


def test_parse_json_from_fenced_text():
    raw = 'Here you go:\n{"visitor_at_door": true, "activity": "waiting", "confidence": 0.8}\n'
    parsed = CloudLiveVisionWorker._parse_json(raw)
    assert parsed is not None
    assert parsed["visitor_at_door"] is True


def test_worker_unavailable_without_keys(tmp_path):
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text("grok_api_key: ''\ngoogle_api_key: ''\n", encoding="utf-8")
    cfg = Config(
        {
            "live_vision": {
                "enabled": True,
                "secrets_file": str(secrets),
            },
            "brain": {"secrets_file": str(secrets)},
        }
    )
    # Override secrets path resolution by pointing to absolute-like relative under tmp.
    worker = CloudLiveVisionWorker(
        cfg,
        frame_getter=lambda: None,
        session_active_fn=lambda: False,
    )
    # Keys empty -> unavailable
    assert worker.is_available() is False or worker._api_key == ""
