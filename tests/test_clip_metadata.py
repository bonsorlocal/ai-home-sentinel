"""Tests for async clip metadata worker (Phase 8)."""

from __future__ import annotations

import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.clip_metadata import ClipMetadataWorker  # noqa: E402
from sentinel.config import Config  # noqa: E402
from sentinel.events import EventLedger  # noqa: E402


def _cfg(enabled: bool = True) -> Config:
    return Config(
        {
            "video_metadata": {
                "enabled": enabled,
                "provider": "grok",
                "base_url": "https://api.x.ai/v1",
                "model": "grok-4.3",
                "max_keyframes": 3,
                "daily_call_cap": 10,
                "request_timeout_seconds": 2,
                "secrets_file": "unused.yaml",
            }
        }
    )


def test_parse_json_response_with_wrappers():
    wrapped = "```json\n{\"scene_summary\":\"a person\"}\n```"
    parsed = ClipMetadataWorker._parse_analysis_json(wrapped)
    assert parsed is not None
    assert parsed["scene_summary"] == "a person"


def test_worker_updates_entities(tmp_path, monkeypatch):
    ledger = EventLedger(str(tmp_path / "events.db"))
    event = ledger.insert(source="motion", title="Motion detected", clip_path="/tmp/x.mp4")

    monkeypatch.setattr(ClipMetadataWorker, "_load_api_key", lambda self: "xai-test")
    worker = ClipMetadataWorker(_cfg(enabled=True), ledger)
    monkeypatch.setattr(
        worker,
        "_analyze_clip",
        lambda _clip_path: {"scene_summary": "A person walked by.", "actors": ["person"]},
    )

    worker.start()
    try:
        assert worker.enqueue(event.id, "/tmp/x.mp4") is True
        deadline = time.time() + 2.0
        while time.time() < deadline:
            updated = ledger.get_by_id(event.id)
            if updated and updated.entities.get("clip_analysis_status") == "ok":
                break
            time.sleep(0.05)
        updated = ledger.get_by_id(event.id)
        assert updated is not None
        assert updated.entities.get("clip_analysis_status") == "ok"
        assert updated.entities.get("clip_analysis", {}).get("scene_summary")
    finally:
        worker.stop()
