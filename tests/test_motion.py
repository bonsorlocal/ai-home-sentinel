"""Tests for motion detection (Phase 3c)."""

from __future__ import annotations

import os
import sys
import time

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config
from sentinel.frame_store import FrameStore
from sentinel.motion import MotionDetector  # noqa: E402


class _MiniConfig:
    def get(self, section, key=None, default=None):
        data = {
            "motion": {
                "enabled": True,
                "min_area": 500,
                "cooldown_seconds": 0.1,
                "background_learning_rate": 0.5,
            },
            "camera": {"ai_width": 160, "ai_height": 120},
        }
        section_data = data.get(section, default if key is None else {})
        if key is None:
            return section_data
        if isinstance(section_data, dict):
            return section_data.get(key, default)
        return default


def test_motion_detects_change_on_synthetic_frames():
    store = FrameStore()
    triggered = []

    def on_motion(frame, area):
        triggered.append((frame is not None, area))

    detector = MotionDetector(_MiniConfig(), store, on_motion=on_motion)
    detector.start()

    bg = np.zeros((120, 160, 3), dtype=np.uint8)
    for _ in range(5):
        store.update(b"fake", bg)
        time.sleep(0.05)

    moved = bg.copy()
    moved[40:80, 40:80] = 255
    for _ in range(10):
        store.update(b"fake2", moved)
        time.sleep(0.05)

    detector.stop()
    assert len(triggered) >= 1
    assert triggered[0][1] >= 500


def test_motion_disabled_does_not_start():
    class DisabledConfig:
        def get(self, section, key=None, default=None):
            if section == "motion":
                motion = {
                    "enabled": False,
                    "min_area": 500,
                    "cooldown_seconds": 0.1,
                    "background_learning_rate": 0.5,
                }
                if key is None:
                    return motion
                return motion.get(key, default)
            return _MiniConfig().get(section, key, default)

    store = FrameStore()
    detector = MotionDetector(DisabledConfig(), store)
    detector.start()
    assert not detector.is_running()


def test_motion_status_fields():
    store = FrameStore()
    detector = MotionDetector(_MiniConfig(), store)
    status = detector.status()
    assert "running" in status
    assert "enabled" in status
