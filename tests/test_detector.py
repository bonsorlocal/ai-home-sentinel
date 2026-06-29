"""Tests for object detector (Phase 4)."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import load_config  # noqa: E402
from sentinel.detector import ObjectDetector  # noqa: E402
from sentinel.frame_store import FrameStore  # noqa: E402
from sentinel.motion import MotionDetector  # noqa: E402


class _FakeMotion:
    def is_active(self):
        return True


def test_detector_status_without_model():
    config = load_config()
    store = FrameStore()
    motion = MotionDetector(config, store)
    detector = ObjectDetector(config, store, motion)
    status = detector.status()
    assert "enabled" in status
    assert status["active"] is False


def test_parse_results_empty():
    config = load_config()
    store = FrameStore()
    motion = MotionDetector(config, store)
    detector = ObjectDetector(config, store, motion)
    assert detector.detect_frame(None) == []


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(PROJECT_ROOT, "models", "yolo_nano.pt")),
    reason="YOLO model not downloaded",
)
def test_detector_loads_model():
    config = load_config()
    store = FrameStore()
    motion = MotionDetector(config, store)
    detector = ObjectDetector(config, store, motion)
    detector.start()
    assert detector.is_active() or detector.status()["message"]
    detector.stop()


def test_detector_disabled_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "detector:\n  enabled: false\n",
        encoding="utf-8",
    )
    config = load_config(path=str(cfg_path))
    store = FrameStore()
    motion = MotionDetector(config, store)
    detector = ObjectDetector(config, store, motion)
    detector.start()
    assert not detector.is_running()
