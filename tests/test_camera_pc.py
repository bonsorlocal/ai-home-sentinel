"""PC webcam backend helpers (no hardware required)."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.camera import Camera  # noqa: E402
from sentinel.config import Config  # noqa: E402
from sentinel.frame_store import FrameStore  # noqa: E402


def _camera(index=None) -> Camera:
    cfg = Config({"camera": {"type": "opencv", "index": index, "startup_wait_seconds": 0}})
    return Camera(cfg, FrameStore())


def test_opencv_indices_default_probes_first_three():
    assert _camera()._opencv_indices() == [0, 1, 2]


def test_opencv_indices_honors_config():
    assert _camera(index=1)._opencv_indices() == [1]


def test_windows_prefers_directshow(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    cam = _camera()
    cam_cv2 = MagicMock()
    cam_cv2.CAP_DSHOW = 700
    cam_cv2.CAP_MSMF = 1400
    cam_cv2.CAP_ANY = 0
    monkeypatch.setattr("sentinel.camera.cv2", cam_cv2)
    assert cam._opencv_backends()[0] == 700
