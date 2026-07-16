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
from sentinel.dvr import (
    ALLOWED_SEGMENT_MINUTES,
    ContinuousRecorder,
    DvrIndex,
    build_local_segment_summary,
)  # noqa: E402


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


def test_dvr_uses_fallback_when_usb_missing(tmp_path):
    primary = tmp_path / "missing-usb"
    fallback = tmp_path / "data" / "dvr"
    recorder = ContinuousRecorder(
        Config(
            {
                "dvr": {
                    "enabled": True,
                    "storage_root": str(primary),
                    "fallback_storage_root": str(fallback),
                    "retention_hours": 48,
                    "segment_seconds": 10,
                    "record_fps": 5,
                    "record_width": 320,
                    "record_height": 180,
                }
            }
        ),
        frame_getter=lambda: np.zeros((180, 320, 3), dtype=np.uint8),
    )
    assert recorder.available is True
    assert recorder.storage_root == os.path.abspath(str(fallback))
    status = recorder.status()
    assert status["using_fallback"] is True
    assert recorder.index is not None


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


def test_dvr_range_includes_active_segment(tmp_path):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    recorder = ContinuousRecorder(
        _cfg(str(storage_root), retention_hours=48),
        frame_getter=lambda: np.zeros((180, 320, 3), dtype=np.uint8),
    )
    now = datetime.utcnow().replace(microsecond=0)
    recorder._active_segment_start = now - timedelta(minutes=15)
    recorder._active_segment_deadline = now + timedelta(minutes=45)

    segments = recorder.list_range(
        (now - timedelta(hours=1)).isoformat(),
        (now + timedelta(hours=1)).isoformat(),
    )

    assert segments
    active = segments[-1]
    assert active["active"] is True
    assert active["playable"] is False
    assert active["id"] is None
    assert "Recording now" in active["summary"]


def test_dvr_query_context_includes_active_segment_for_recent_question(tmp_path):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    recorder = ContinuousRecorder(
        _cfg(str(storage_root), retention_hours=48),
        frame_getter=lambda: np.zeros((180, 320, 3), dtype=np.uint8),
    )
    now = datetime.utcnow().replace(microsecond=0)
    recorder._active_segment_start = now - timedelta(minutes=20)
    recorder._active_segment_deadline = now + timedelta(minutes=40)

    bundle = recorder.build_query_context("What happened a few minutes earlier?")

    assert bundle["window_source"] == "relative_recent"
    assert "active segment (recording now)" in bundle["context"]
    assert "finalized video analysis is not available" in bundle["context"]


def test_build_local_segment_summary_describes_activity():
    start = datetime(2026, 7, 7, 14, 0, 0)
    end = start + timedelta(hours=1)
    summary = build_local_segment_summary(
        motion_score=0.05,
        person_count=2,
        object_labels=["person", "dog"],
        start_ts=start.isoformat(),
        end_ts=end.isoformat(),
    )
    assert "2 persons seen" in summary
    assert "moderate motion" in summary
    assert "person" in summary


def test_finalize_segment_summary_writes_local_text(tmp_path):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    recorder = ContinuousRecorder(
        Config(
            {
                "dvr": {
                    "enabled": True,
                    "storage_root": str(storage_root),
                    "retention_hours": 48,
                    "segment_seconds": 3600,
                    "auto_summarize": True,
                    "auto_summarize_cloud": False,
                    "record_fps": 5,
                    "record_width": 320,
                    "record_height": 180,
                }
            }
        ),
        frame_getter=lambda: np.zeros((180, 320, 3), dtype=np.uint8),
    )
    assert recorder.index is not None
    start = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    seg_path = storage_root / "segments" / "hour.mp4"
    seg_path.parent.mkdir(parents=True, exist_ok=True)
    seg_path.write_bytes(b"video")
    seg_id = recorder.index.add_segment(
        start_ts=start.isoformat(),
        end_ts=end.isoformat(),
        path=str(seg_path),
        size_bytes=100,
        motion_score=0.02,
    )
    recorder.index.annotate_overlap(
        start_ts=start.isoformat(),
        end_ts=end.isoformat(),
        person_count=1,
        object_labels=["person"],
    )
    recorder._finalize_segment_summary(seg_id)
    loaded = recorder.index.get(seg_id)
    assert loaded is not None
    assert loaded.summary
    assert "person" in loaded.summary.lower()


def test_dvr_settings_allowed_values_and_persistence(tmp_path):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    recorder = ContinuousRecorder(
        _cfg(str(storage_root), retention_hours=48),
        frame_getter=lambda: np.zeros((180, 320, 3), dtype=np.uint8),
    )
    settings = recorder.get_settings()
    assert settings["allowed_segment_minutes"] == list(ALLOWED_SEGMENT_MINUTES)

    bad = recorder.set_segment_minutes(20)
    assert bad["ok"] is False

    result = recorder.set_segment_minutes(30)
    assert result["ok"] is True
    assert result["segment_minutes"] == 30
    assert recorder._segment_seconds == 1800
    assert recorder._align_to_clock_hours is False

    settings_path = storage_root / "dvr_settings.json"
    assert settings_path.exists()

    reloaded = ContinuousRecorder(
        _cfg(str(storage_root), retention_hours=48),
        frame_getter=lambda: np.zeros((180, 320, 3), dtype=np.uint8),
    )
    assert reloaded.get_settings()["segment_minutes"] == 30


def test_dvr_maybe_finalize_splits_active_buffer(tmp_path):
    storage_root = tmp_path / "usb"
    storage_root.mkdir(parents=True, exist_ok=True)
    recorder = ContinuousRecorder(
        _cfg(str(storage_root), retention_hours=48),
        frame_getter=lambda: np.zeros((180, 320, 3), dtype=np.uint8),
    )
    assert recorder.index is not None
    recorder._segment_seconds = 2.0
    recorder._record_fps = 2.0
    recorder._force_finalize_active = True
    frames = [np.zeros((180, 320, 3), dtype=np.uint8) for _ in range(10)]
    start = datetime.utcnow().replace(microsecond=0)

    remaining, new_start, _, _ = recorder._maybe_finalize_for_reconfigure(frames, start, 0.4, 4)

    stored = recorder.index.list_all()
    assert len(stored) >= 2
    assert new_start >= start
    assert remaining


def test_dvr_index_delete_segment(tmp_path):
    index = DvrIndex(str(tmp_path / "dvr_index.db"))
    start = datetime.utcnow()
    seg_path = tmp_path / "seg.mp4"
    seg_path.write_bytes(b"video")
    seg_id = index.add_segment(
        start_ts=start.isoformat(),
        end_ts=(start + timedelta(minutes=1)).isoformat(),
        path=str(seg_path),
        size_bytes=5,
        motion_score=0.01,
    )
    deleted_path = index.delete_segment(seg_id)
    assert deleted_path == str(seg_path)
    assert index.get(seg_id) is None
    assert index.list_all() == []
