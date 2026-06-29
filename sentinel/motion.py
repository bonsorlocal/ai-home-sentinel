"""Motion detection module (Phase 3).

Lightweight OpenCV MOG2 background subtraction on every frame. Runs cheaply
and decides when to wake heavier AI and log motion events.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from sentinel.config import Config
from sentinel.frame_store import FrameStore

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None
    np = None


MotionCallback = Callable[[object, float], None]


class MotionDetector:
    """Background-subtraction motion detector reading from FrameStore."""

    def __init__(
        self,
        config: Config,
        frame_store: FrameStore,
        on_motion: Optional[MotionCallback] = None,
    ):
        motion = config.get("motion") or {}
        camera = config.get("camera") or {}

        self._enabled = bool(motion.get("enabled", True))
        self._min_area = int(motion.get("min_area", 1200))
        self._cooldown = float(motion.get("cooldown_seconds", 5))
        self._learning_rate = float(motion.get("background_learning_rate", 0.02))
        self._ai_width = int(camera.get("ai_width", 640))
        self._ai_height = int(camera.get("ai_height", 360))

        self._frame_store = frame_store
        self._on_motion = on_motion

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_motion_at: float = 0.0
        self._motion_active = False
        self._last_area: float = 0.0
        self._message = "Motion detector not started."

    def start(self) -> None:
        if not self._enabled:
            self._message = "Motion detection disabled in config."
            return
        if cv2 is None or np is None:
            self._message = "OpenCV not available; motion detection disabled."
            print(f"[motion] {self._message}")
            return
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="motion-detector", daemon=True
        )
        self._thread.start()
        self._message = "Motion detection running."

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def is_active(self) -> bool:
        """True when motion was detected recently (within cooldown window)."""
        with self._lock:
            return self._is_active_locked()

    def _is_active_locked(self) -> bool:
        """Active check that assumes the caller already holds ``self._lock``."""
        if not self._motion_active:
            return False
        return (time.monotonic() - self._last_motion_at) < self._cooldown * 2

    def seconds_since_motion(self) -> Optional[float]:
        with self._lock:
            if self._last_motion_at == 0.0:
                return None
            return time.monotonic() - self._last_motion_at

    def status(self) -> dict:
        running = self.is_running()
        with self._lock:
            return {
                "running": running,
                "active": self._is_active_locked(),
                "enabled": self._enabled,
                "last_area": self._last_area,
                "message": self._message,
            }

    def _run(self) -> None:
        subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=16, detectShadows=True
        )
        last_count = -1

        while not self._stop_event.is_set():
            result = self._frame_store.wait_for_next(last_count, timeout=1.0)
            if result is None:
                continue
            _, last_count = result

            frame = self._frame_store.get_frame()
            if frame is None:
                continue

            try:
                small = cv2.resize(frame, (self._ai_width, self._ai_height))
                fg_mask = subtractor.apply(small, learningRate=self._learning_rate)
                _, thresh = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
                contours, _ = cv2.findContours(
                    thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                max_area = 0.0
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area > max_area:
                        max_area = area

                if max_area >= self._min_area:
                    self._handle_motion(frame, max_area)
            except Exception as error:  # noqa: BLE001
                print(f"[motion] Frame processing error: {error}")

    def _handle_motion(self, frame, area: float) -> None:
        now = time.monotonic()
        with self._lock:
            self._last_area = area
            if now - self._last_motion_at < self._cooldown:
                self._motion_active = True
                return
            self._last_motion_at = now
            self._motion_active = True

        if self._on_motion is not None:
            try:
                self._on_motion(frame, area)
            except Exception as error:  # noqa: BLE001
                print(f"[motion] Motion callback error: {error}")
