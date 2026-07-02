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
                "start_min_area": 300,
                "start_consecutive_frames": 1,
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


def test_motion_session_waits_before_end():
    class SessionConfig(_MiniConfig):
        def get(self, section, key=None, default=None):
            if section == "motion":
                motion = {
                    "enabled": True,
                    "min_area": 500,
                    "start_min_area": 500,
                    "start_consecutive_frames": 1,
                    "end_still_seconds": 0.25,
                    "end_grace_seconds": 0.25,
                    "min_session_seconds": 0.2,
                    "session_update_every_seconds": 0.05,
                    "cooldown_seconds": 0.1,
                    "background_learning_rate": 0.5,
                }
                if key is None:
                    return motion
                return motion.get(key, default)
            return super().get(section, key, default)

    store = FrameStore()
    events = {"started": 0, "ended": 0}
    detector = MotionDetector(
        SessionConfig(),
        store,
        on_session_started=lambda _f, _a: events.__setitem__("started", events["started"] + 1),
        on_session_ended=lambda _f, _a, _d: events.__setitem__("ended", events["ended"] + 1),
    )
    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    detector._handle_area(frame, 900.0)
    assert events["started"] == 1

    detector._handle_area(frame, 0.0)
    time.sleep(0.2)
    detector._handle_area(frame, 0.0)
    detector._handle_area(frame, 900.0)
    assert events["ended"] == 0

    detector._handle_area(frame, 0.0)
    time.sleep(0.55)
    detector._handle_area(frame, 0.0)
    time.sleep(0.12)
    detector._handle_area(frame, 0.0)
    assert events["ended"] == 1


def test_motion_starts_session_on_meaningful_area():
    store = FrameStore()
    triggered = []

    def on_motion(frame, area):
        triggered.append((frame is not None, area))

    detector = MotionDetector(_MiniConfig(), store, on_motion=on_motion)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    detector._handle_area(frame, 900.0)
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


def test_person_out_of_frame_ends_session():
    class PersonConfig(_MiniConfig):
        def get(self, section, key=None, default=None):
            if section == "motion":
                motion = {
                    "enabled": True,
                    "min_area": 500,
                    "start_min_area": 500,
                    "start_consecutive_frames": 1,
                    "min_session_seconds": 0.1,
                    "person_missing_seconds": 0.2,
                    "person_still_seconds": 2.0,
                    "person_presence_stale_seconds": 0.1,
                    "person_still_bbox_quiet_seconds": 0.05,
                    "still_motion_area_ratio": 0.9,
                    "cooldown_seconds": 0.1,
                    "background_learning_rate": 0.5,
                }
                if key is None:
                    return motion
                return motion.get(key, default)
            return super().get(section, key, default)

    store = FrameStore()
    ended = {"count": 0}
    detector = MotionDetector(
        PersonConfig(),
        store,
        on_session_ended=lambda _f, _a, _d: ended.__setitem__("count", ended["count"] + 1),
    )
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    detector._handle_area(frame, 1000.0)
    detector.report_person_seen([10, 10, 70, 70])
    time.sleep(0.25)
    detector._handle_area(frame, 1000.0)
    time.sleep(0.12)
    detector._handle_area(frame, 1000.0)
    assert ended["count"] == 1


def test_person_still_ends_session_after_threshold():
    class StillConfig(_MiniConfig):
        def get(self, section, key=None, default=None):
            if section == "motion":
                motion = {
                    "enabled": True,
                    "min_area": 500,
                    "start_min_area": 500,
                    "start_consecutive_frames": 1,
                    "min_session_seconds": 0.1,
                    "person_missing_seconds": 2.0,
                    "person_still_seconds": 0.3,
                    "person_presence_stale_seconds": 2.0,
                    "person_still_bbox_quiet_seconds": 0.05,
                    "still_motion_area_ratio": 0.8,
                    "still_bbox_center_drift_px": 10.0,
                    "still_bbox_area_change_ratio": 0.5,
                    "cooldown_seconds": 0.1,
                    "background_learning_rate": 0.5,
                }
                if key is None:
                    return motion
                return motion.get(key, default)
            return super().get(section, key, default)

    store = FrameStore()
    ended = {"count": 0}
    detector = MotionDetector(
        StillConfig(),
        store,
        on_session_ended=lambda _f, _a, _d: ended.__setitem__("count", ended["count"] + 1),
    )
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    detector._handle_area(frame, 900.0)
    detector.report_person_seen([20, 20, 80, 80])
    time.sleep(0.1)
    detector.report_person_seen([21, 21, 81, 81])
    detector._handle_area(frame, 50.0)
    time.sleep(0.35)
    detector._handle_area(frame, 40.0)
    time.sleep(0.12)
    detector._handle_area(frame, 40.0)
    assert ended["count"] == 1


def test_candidate_event_needs_persistence_before_activation():
    class CandidateConfig(_MiniConfig):
        def get(self, section, key=None, default=None):
            if section == "motion":
                motion = {
                    "enabled": True,
                    "min_area": 400,
                    "start_min_area": 500,
                    "start_consecutive_frames": 2,
                    "candidate_window_seconds": 1.0,
                    "min_session_seconds": 0.1,
                    "cooldown_seconds": 0.1,
                    "background_learning_rate": 0.5,
                }
                if key is None:
                    return motion
                return motion.get(key, default)
            return super().get(section, key, default)

    store = FrameStore()
    events = {"started": 0}
    detector = MotionDetector(
        CandidateConfig(),
        store,
        on_session_started=lambda _f, _a: events.__setitem__("started", events["started"] + 1),
    )
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    detector._handle_area(frame, 520.0)
    assert detector.status()["state"] == "candidate_event"
    assert events["started"] == 0
    detector._handle_area(frame, 540.0)
    assert detector.status()["state"] == "active_event"
    assert events["started"] == 1


def test_global_noise_is_filtered_before_session_start():
    class NoiseConfig(_MiniConfig):
        def get(self, section, key=None, default=None):
            if section == "motion":
                motion = {
                    "enabled": True,
                    "min_area": 500,
                    "start_min_area": 500,
                    "start_consecutive_frames": 1,
                    "global_motion_ratio_max": 0.45,
                    "max_noise_contours": 12,
                    "background_learning_rate": 0.5,
                }
                if key is None:
                    return motion
                return motion.get(key, default)
            return super().get(section, key, default)

    store = FrameStore()
    detector = MotionDetector(NoiseConfig(), store)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    detector._handle_area(
        frame,
        900.0,
        contour_count=25,
        motion_ratio=0.8,
        luma=180.0,
    )
    status = detector.status()
    assert status["session_active"] is False
    assert status["state"] == "idle"
    assert status["noise_suppressed_frames"] >= 1
