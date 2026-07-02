"""Tests for runtime wiring."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import load_config  # noqa: E402
from sentinel.frame_store import FrameStore  # noqa: E402
from sentinel.runtime import SentinelRuntime  # noqa: E402


def test_runtime_creates_ledger(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "db" / "sentinel.db"
    snap_path = tmp_path / "snaps"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
detector:
  enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(path=str(cfg_path))
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    assert runtime.ledger.count() == 0
    assert os.path.isdir(str(snap_path))


def test_session_end_writes_single_event(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
detector:
  enabled: false
clips:
  enabled: false
video_metadata:
  enabled: false
""",
        encoding="utf-8",
    )
    import numpy as np

    config = load_config(path=str(cfg_path))
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    frame = np.zeros((90, 160, 3), dtype=np.uint8)

    runtime._on_motion_session_started(frame, 1500.0)
    runtime._on_detection([{"label": "person", "confidence": 0.75}], frame)
    runtime._on_detection([{"label": "person", "confidence": 0.91}], frame)
    runtime._on_motion_session_updated(frame, 2200.0)
    runtime._on_motion_session_ended(frame, 1000.0, 8.4)

    assert runtime.ledger.count() == 1
    event = runtime.ledger.list_recent(limit=1)[0]
    assert event.source == "object"
    assert event.entities["session"]["duration_seconds"] == 8.4
    assert event.entities["session"]["peak_area"] == 2200
    assert event.entities["detections"][0]["label"] == "person"
    assert event.entities["detections"][0]["confidence"] == 0.91


def test_detection_without_motion_does_not_open_session(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
detector:
  enabled: false
clips:
  enabled: false
video_metadata:
  enabled: false
""",
        encoding="utf-8",
    )
    import numpy as np

    config = load_config(path=str(cfg_path))
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    frame = np.zeros((90, 160, 3), dtype=np.uint8)

    runtime._on_detection([{"label": "person", "confidence": 0.88}], frame)
    assert runtime._active_session is None
    assert runtime.ledger.count() == 0


def test_motion_session_clip_saved_once(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    clip_path = tmp_path / "clips"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
detector:
  enabled: false
clips:
  enabled: true
  clip_dir: "{clip_path.as_posix()}"
video_metadata:
  enabled: false
""",
        encoding="utf-8",
    )
    import numpy as np

    config = load_config(path=str(cfg_path))
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    for _ in range(8):
        store.update(frame)

    runtime._on_motion_session_started(frame, 1400.0)
    runtime._on_detection([{"label": "person", "confidence": 0.91}], frame)
    for i in range(12):
        tinted = frame.copy()
        tinted[:, :, 0] = min(255, i * 20)
        store.update(tinted)
        runtime._active_session["last_clip_sample_at"] = 0.0
        runtime._on_motion_session_updated(tinted, 1000.0 + i * 100)
    runtime._on_motion_session_ended(frame, 1000.0, 5.0)

    event = runtime.ledger.list_recent(limit=1)[0]
    assert event.clip_path is not None
    assert os.path.isfile(str(event.clip_path))
    assert runtime._active_session is None
    assert runtime.ledger.count() == 1
    assert event.entities["session"]["session_id"].startswith("session-")
    assert event.entities["session"]["lifecycle"][-1]["state"] == "event_closed"
    assert os.path.getsize(str(event.clip_path)) > 5000


def test_face_updates_enrich_active_session_only(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
detector:
  enabled: false
clips:
  enabled: false
video_metadata:
  enabled: false
""",
        encoding="utf-8",
    )
    import numpy as np

    config = load_config(path=str(cfg_path))
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    frame = np.zeros((90, 160, 3), dtype=np.uint8)

    runtime._on_face(
        {"name": "unknown", "known": False, "distance": 0.88, "location": [10, 40, 60, 8]},
        frame,
    )
    assert runtime._active_session is None
    assert runtime.ledger.count() == 0

    runtime._on_motion_session_started(frame, 1200.0)
    runtime._on_face(
        {"name": "unknown", "known": False, "distance": 0.52, "location": [12, 42, 62, 10]},
        frame,
    )
    runtime._on_face(
        {"name": "unknown", "known": False, "distance": 0.48, "location": [13, 43, 63, 11]},
        frame,
    )
    runtime._on_motion_session_ended(frame, 900.0, 4.0)
    event = runtime.ledger.list_recent(limit=1)[0]
    assert event.entities["face_tracks"]["unknown"]["observations"] == 2


def test_short_motion_session_skips_clip(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    clip_path = tmp_path / "clips"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
detector:
  enabled: false
clips:
  enabled: true
  clip_dir: "{clip_path.as_posix()}"
  min_session_seconds_for_clip: 8
video_metadata:
  enabled: false
""",
        encoding="utf-8",
    )
    import numpy as np

    config = load_config(path=str(cfg_path))
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    for _ in range(20):
        store.update(frame)

    runtime._on_motion_session_started(frame, 1500.0)
    runtime._on_motion_session_updated(frame, 1400.0)
    runtime._on_motion_session_ended(frame, 1200.0, 5.0)

    event = runtime.ledger.list_recent(limit=1)[0]
    assert event.clip_path is None
    assert event.entities["clip_status"] == "skipped"
    assert event.entities["clip_expected"] is False
    assert "short motion-only session" in event.entities.get("clip_note", "")


def test_short_person_session_still_saves_clip(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    clip_path = tmp_path / "clips"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path.as_posix()}"
  snapshot_dir: "{snap_path.as_posix()}"
detector:
  enabled: false
clips:
  enabled: true
  clip_dir: "{clip_path.as_posix()}"
  min_session_seconds_for_clip: 8
video_metadata:
  enabled: false
""",
        encoding="utf-8",
    )
    import numpy as np

    config = load_config(path=str(cfg_path))
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    frame = np.zeros((90, 160, 3), dtype=np.uint8)
    for i in range(24):
        moving = frame.copy()
        moving[:, :, 1] = (i * 10) % 255
        store.update(moving)
        runtime._on_motion_session_frame(moving, 1300.0)

    runtime._on_motion_session_started(frame, 1500.0)
    runtime._on_detection([{"label": "person", "confidence": 0.91}], frame)
    runtime._on_motion_session_updated(frame, 1400.0)
    runtime._on_motion_session_ended(frame, 1200.0, 5.0)

    event = runtime.ledger.list_recent(limit=1)[0]
    assert event.clip_path is not None
    assert os.path.isfile(str(event.clip_path))
    assert event.entities["clip_status"] == "saved"
