"""Tests for face recognition (Phase 5)."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import Config, DEFAULT_CONFIG, load_config  # noqa: E402
from sentinel.face_recognition_module import FaceRecognizer  # noqa: E402
from sentinel.frame_store import FrameStore  # noqa: E402
from sentinel.motion import MotionDetector  # noqa: E402


def _face_config(**overrides):
    cfg = {
        "face_recognition": {
            "enabled": False,
            "known_faces_dir": "data/known_faces",
            "unknown_faces_dir": "data/events/unknown_faces",
            "tolerance": 0.5,
            "run_only_when_person_detected": True,
        }
    }
    cfg["face_recognition"].update(overrides)
    return Config(cfg)


def test_face_recognition_disabled_by_default_in_code_defaults():
    """Built-in defaults keep faces off until config.yaml enables them."""
    config = Config(DEFAULT_CONFIG)
    assert config.get("face_recognition", "enabled") is False


def test_face_recognition_enabled_in_project_config():
    config = load_config()
    assert config.get("face_recognition", "enabled") is True


def test_face_status_when_disabled():
    config = _face_config(enabled=False)
    store = FrameStore()
    motion = MotionDetector(config, store)
    faces = FaceRecognizer(config, store, motion)
    faces.start()
    assert not faces.is_running()
    assert "disabled" in faces.status()["message"].lower()


def test_face_status_when_enabled_without_library():
    config = _face_config(enabled=True)
    store = FrameStore()
    motion = MotionDetector(config, store)
    faces = FaceRecognizer(config, store, motion)
    faces.start()
    status = faces.status()
    if faces.is_running():
        assert status["enabled"] is True
    else:
        assert "face_recognition" in status["message"].lower()


def test_match_frame_empty():
    config = _face_config()
    store = FrameStore()
    motion = MotionDetector(config, store)
    faces = FaceRecognizer(config, store, motion)
    assert faces.match_frame(None) == []


@pytest.mark.skipif(
    True,
    reason="face_recognition requires dlib; run manually after install",
)
def test_enroll_and_match(tmp_path):
    pytest.importorskip("face_recognition")
    import cv2

    config = _face_config(enabled=True)
    store = FrameStore()
    motion = MotionDetector(config, store)
    faces = FaceRecognizer(config, store, motion)

    # Synthetic face-like image won't work; this is a placeholder for real photos
    img_path = str(tmp_path / "face.jpg")
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.imwrite(img_path, img)
    assert faces.enroll_face(img_path, "test1", "Test Person") is False
