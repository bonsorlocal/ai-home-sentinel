"""Tests for always-on DVR recorder/index."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config  # noqa: E402
from sentinel.dvr import ContinuousRecorder, DvrIndex  # noqa: E402


def _cfg(storage_root: str, retention_hours: float = 48) -> Config:
    return Config(
        {
            "dvr": {
                "enabled": True,
                "storage_root": storage_root,
                "retention_hours": retention_hours,
                "segment_seconds": 10,
                "record_fps": 5,
                "record_width": 320,
                "record_height": 180,
            }
        }
    )


def test_dvr_index_range_and_search(tmp_path):
    index = DvrIndex(str(tmp_path / "dvr_index.db"))
    start = datetime.utcnow()
    index.add_segment(
        start_ts=start.isoformat(),
        end_ts=(start + timedelta(seconds=60)).isoformat(),
        path=str(tmp_path / "seg1.mp4"),
        size_bytes=1000,
        motion_score=0.32,
    )
    index.annotate_overlap(
        start_ts=start.isoformat(),
        end_ts=(start + timedelta(seconds=60)).isoformat(),
        person_count=1,
        object_labels=["person", "laptop"],
        summary="Person moved laptop from TV stand.",
    )
    in_range = index.list_range(
        (start - timedelta(seconds=1)).isoformat(),
        (start + timedelta(seconds=61)).isoformat(),
    )
    assert len(in_range) == 1
    assert in_range[0].person_count == 1
    assert "laptop" in in_range[0].object_labels

    hits = index.search("who moved the laptop", limit=5)
    assert hits
    assert hits[0].id == in_range[0].id


def test_dvr_recorder_prunes_old_segments(tmp_path):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    recorder = ContinuousRecorder(
        _cfg(str(storage_root), retention_hours=1),
        frame_getter=lambda: np.zeros((180, 320, 3), dtype=np.uint8),
    )
    assert recorder.available is True
    assert recorder.index is not None

    old_path = storage_root / "segments" / "old.mp4"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_bytes(b"old")
    old_start = datetime.utcnow() - timedelta(hours=3)
    old_end = old_start + timedelta(minutes=1)
    recorder.index.add_segment(
        start_ts=old_start.isoformat(),
        end_ts=old_end.isoformat(),
        path=str(old_path),
        size_bytes=3,
        motion_score=0.01,
    )
    recorder._prune_old_segments()
    assert not old_path.exists()


def test_dvr_query_context_infers_time_window(tmp_path):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    recorder = ContinuousRecorder(
        _cfg(str(storage_root), retention_hours=48),
        frame_getter=lambda: np.zeros((180, 320, 3), dtype=np.uint8),
    )
    assert recorder.index is not None
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=2)
    end = start + timedelta(minutes=20)
    seg_path = storage_root / "segments" / "time-window.mp4"
    seg_path.parent.mkdir(parents=True, exist_ok=True)
    seg_path.write_bytes(b"video")
    recorder.index.add_segment(
        start_ts=start.isoformat(),
        end_ts=end.isoformat(),
        path=str(seg_path),
        size_bytes=5,
        motion_score=0.12,
    )
    bundle = recorder.build_query_context("What happened in the last 3 hours?")
    assert bundle["window_start"] is not None
    assert bundle["window_end"] is not None
    assert bundle["window_source"] == "relative_recent"
    assert bundle["segments"]
