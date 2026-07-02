"""Tests for dashboard events incident filtering."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import load_config  # noqa: E402
from sentinel.dashboard import create_app  # noqa: E402
from sentinel.frame_store import FrameStore  # noqa: E402
from sentinel.runtime import SentinelRuntime  # noqa: E402


def test_api_events_filter_incidents_returns_relevant_clips_only(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    clips_path = tmp_path / "clips"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
clips:
  enabled: true
  clip_dir: "{clips_path.as_posix()}"
  min_session_seconds_for_clip: 8
detector:
  enabled: false
video_metadata:
  enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(path=str(cfg_path))
    runtime = SentinelRuntime(config, FrameStore())

    runtime.ledger.insert(
        source="motion",
        title="Short motion",
        entities={"session": {"duration_seconds": 5.0}, "detections": []},
        clip_path="/tmp/short.mp4",
    )
    runtime.ledger.insert(
        source="motion",
        title="Long motion",
        entities={"session": {"duration_seconds": 20.0}, "detections": []},
        clip_path="/tmp/long.mp4",
    )
    runtime.ledger.insert(
        source="object",
        title="Person seen",
        entities={
            "session": {"duration_seconds": 4.0},
            "detections": [{"label": "person", "confidence": 0.95}],
        },
        clip_path="/tmp/person.mp4",
    )
    runtime.ledger.insert(
        source="motion",
        title="No clip",
        entities={"session": {"duration_seconds": 20.0}, "detections": []},
        clip_path=None,
    )

    app = create_app(config, runtime=runtime)
    client = app.test_client()

    resp = client.get("/api/events?filter=incidents&limit=30")
    assert resp.status_code == 200
    payload = resp.get_json()
    titles = [item["title"] for item in payload["events"]]
    assert "Long motion" in titles
    assert "Person seen" in titles
    assert "Short motion" not in titles
    assert "No clip" not in titles

    resp_all = client.get("/api/events?filter=all&limit=30")
    assert resp_all.status_code == 200
    payload_all = resp_all.get_json()
    titles_all = [item["title"] for item in payload_all["events"]]
    assert "Short motion" in titles_all
    assert "No clip" in titles_all
