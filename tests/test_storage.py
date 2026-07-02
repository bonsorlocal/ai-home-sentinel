"""Tests for snapshot storage (Phase 3b)."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.storage import ClipStorage, SnapshotStorage  # noqa: E402


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


def test_save_clip_creates_mp4(tmp_path):
    clip_storage = ClipStorage(
        str(tmp_path),
        max_clips_per_day=5,
        record_fps=8,
        record_width=160,
        record_height=120,
    )
    frames = []
    for i in range(12):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        frame[:, :, 1] = (i * 20) % 255
        frames.append(frame)
    path = clip_storage.save_clip(frames, prefix="motion")
    assert path is not None
    assert path.endswith(".mp4")
    assert os.path.isfile(path)
    assert os.path.getsize(path) > 0


def test_save_clip_prunes_oldest_when_full(tmp_path):
    clip_storage = ClipStorage(
        str(tmp_path),
        max_clips_per_day=2,
        record_fps=8,
        record_width=160,
        record_height=120,
        prune_when_full=True,
    )
    frames = [
        np.zeros((120, 160, 3), dtype=np.uint8),
        np.zeros((120, 160, 3), dtype=np.uint8),
    ]
    first = clip_storage.save_clip(frames, prefix="first")
    second = clip_storage.save_clip(frames, prefix="second")
    third = clip_storage.save_clip(frames, prefix="third")
    assert first is not None
    assert second is not None
    assert third is not None
    assert not os.path.isfile(first)
    assert os.path.isfile(second)
    assert os.path.isfile(third)
