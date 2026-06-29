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


def test_runtime_creates_ledger(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "db" / "sentinel.db"
    snap_path = tmp_path / "snaps"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path}"
  snapshot_dir: "{snap_path}"
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


def test_motion_callback_writes_event(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    db_path = tmp_path / "sentinel.db"
    snap_path = tmp_path / "snaps"
    cfg_path.write_text(
        f"""
storage:
  database_path: "{db_path}"
  snapshot_dir: "{snap_path}"
  save_snapshot_on_motion: false
detector:
  enabled: false
motion:
  enabled: true
  cooldown_seconds: 0
""",
        encoding="utf-8",
    )
    import numpy as np

    config = load_config(path=str(cfg_path))
    store = FrameStore()
    runtime = SentinelRuntime(config, store)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    runtime._on_motion(frame, 2000.0)
    assert runtime.ledger.count() == 1
    events = runtime.ledger.list_recent()
    assert events[0].source == "motion"
