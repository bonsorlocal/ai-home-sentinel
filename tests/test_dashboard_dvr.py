"""Tests for DVR dashboard endpoints."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import load_config  # noqa: E402
from sentinel.dashboard import create_app  # noqa: E402
from sentinel.frame_store import FrameStore  # noqa: E402
from sentinel.runtime import SentinelRuntime  # noqa: E402


def test_dvr_status_and_range_endpoints(tmp_path):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    clips_path = tmp_path / "clips"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
clips:
  enabled: false
  clip_dir: "{clips_path.as_posix()}"
detector:
  enabled: false
video_metadata:
  enabled: false
dvr:
  enabled: true
  storage_root: "{storage_root.as_posix()}"
  retention_hours: 48
  segment_seconds: 60
  record_fps: 5
  record_width: 320
  record_height: 180
""",
        encoding="utf-8",
    )
    config = load_config(path=str(cfg_path))
    runtime = SentinelRuntime(config, FrameStore())
    assert runtime.dvr.index is not None

    seg_path = storage_root / "segments" / "test.mp4"
    seg_path.parent.mkdir(parents=True, exist_ok=True)
    seg_path.write_bytes(b"video")
    start = datetime.utcnow() - timedelta(minutes=10)
    end = start + timedelta(minutes=1)
    seg_id = runtime.dvr.index.add_segment(
        start_ts=start.isoformat(),
        end_ts=end.isoformat(),
        path=str(seg_path),
        size_bytes=5,
        motion_score=0.22,
    )

    app = create_app(config, runtime=runtime)
    client = app.test_client()

    status_resp = client.get("/api/dvr/status")
    assert status_resp.status_code == 200
    assert status_resp.get_json()["enabled"] is True

    range_resp = client.get(
        f"/api/dvr/range?start={(start - timedelta(minutes=1)).isoformat()}&end={(end + timedelta(minutes=1)).isoformat()}"
    )
    assert range_resp.status_code == 200
    payload = range_resp.get_json()
    assert payload["count"] >= 1
    ids = [item["id"] for item in payload["segments"]]
    assert seg_id in ids

    segment_resp = client.get(f"/api/dvr/segment/{seg_id}")
    assert segment_resp.status_code == 200


def test_dvr_settings_get_and_post(tmp_path):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    clips_path = tmp_path / "clips"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
clips:
  enabled: false
  clip_dir: "{clips_path.as_posix()}"
detector:
  enabled: false
video_metadata:
  enabled: false
dvr:
  enabled: true
  storage_root: "{storage_root.as_posix()}"
  retention_hours: 48
  segment_seconds: 3600
  record_fps: 5
  record_width: 320
  record_height: 180
""",
        encoding="utf-8",
    )
    config = load_config(path=str(cfg_path))
    runtime = SentinelRuntime(config, FrameStore())
    app = create_app(config, runtime=runtime)
    client = app.test_client()

    get_resp = client.get("/api/dvr/settings")
    assert get_resp.status_code == 200
    payload = get_resp.get_json()
    assert payload["allowed_segment_minutes"] == [15, 30, 45, 60]

    bad_resp = client.post(
        "/api/dvr/settings",
        json={"segment_minutes": 20},
    )
    assert bad_resp.status_code == 400

    ok_resp = client.post(
        "/api/dvr/settings",
        json={"segment_minutes": 45},
    )
    assert ok_resp.status_code == 200
    body = ok_resp.get_json()
    assert body["ok"] is True
    assert body["segment_minutes"] == 45
    assert body["migration"]["status"] in ("applying", "migrating", "done")


def test_dvr_segment_endpoint_uses_playback_path(tmp_path, monkeypatch):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    clips_path = tmp_path / "clips"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
clips:
  enabled: false
  clip_dir: "{clips_path.as_posix()}"
detector:
  enabled: false
video_metadata:
  enabled: false
dvr:
  enabled: true
  storage_root: "{storage_root.as_posix()}"
  retention_hours: 48
  segment_seconds: 60
  record_fps: 5
  record_width: 320
  record_height: 180
""",
        encoding="utf-8",
    )
    config = load_config(path=str(cfg_path))
    runtime = SentinelRuntime(config, FrameStore())
    assert runtime.dvr.index is not None

    original = storage_root / "segments" / "orig.mp4"
    playable = storage_root / "segments" / "orig.web-1.mp4"
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"orig")
    playable.write_bytes(b"playable")
    start = datetime.utcnow() - timedelta(minutes=5)
    seg_id = runtime.dvr.index.add_segment(
        start_ts=start.isoformat(),
        end_ts=(start + timedelta(minutes=1)).isoformat(),
        path=str(original),
        size_bytes=4,
        motion_score=0.01,
    )
    monkeypatch.setattr(runtime.dvr, "get_playback_path", lambda _segment_id: str(playable))

    app = create_app(config, runtime=runtime)
    client = app.test_client()
    resp = client.get(f"/api/dvr/segment/{seg_id}")
    assert resp.status_code == 200
    assert resp.data == b"playable"
