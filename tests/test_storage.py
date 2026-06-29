"""Tests for snapshot storage (Phase 3b)."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.storage import SnapshotStorage  # noqa: E402


def test_save_snapshot_creates_file(tmp_path):
    storage = SnapshotStorage(str(tmp_path), max_snapshots_per_day=10)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[10:30, 10:30] = (255, 255, 255)

    path = storage.save_snapshot(frame, prefix="test")
    assert path is not None
    assert os.path.isfile(path)
    assert path.startswith(str(tmp_path))


def test_daily_limit_enforced(tmp_path):
    storage = SnapshotStorage(str(tmp_path), max_snapshots_per_day=2)
    frame = np.zeros((50, 50, 3), dtype=np.uint8)

    assert storage.save_snapshot(frame) is not None
    assert storage.save_snapshot(frame) is not None
    assert storage.save_snapshot(frame) is None
    assert storage.can_save_today() is False


def test_save_jpeg_bytes(tmp_path):
    storage = SnapshotStorage(str(tmp_path), max_snapshots_per_day=10)
    fake_jpeg = b"\xff\xd8\xff\xd9"
    path = storage.save_jpeg_bytes(fake_jpeg, prefix="face")
    assert path is not None
    with open(path, "rb") as handle:
        assert handle.read() == fake_jpeg


def test_none_frame_returns_none(tmp_path):
    storage = SnapshotStorage(str(tmp_path))
    assert storage.save_snapshot(None) is None
