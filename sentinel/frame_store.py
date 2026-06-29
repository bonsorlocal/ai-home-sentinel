"""Shared latest-frame buffer (Phase 2).

The whole system reads camera frames from one place. The camera capture loop
(see ``camera.py``) decodes the stream once and pushes the newest frame here;
everything else (the live video stream now, motion/AI later) reads the latest
frame from this store instead of touching the camera again.

Only the single newest frame is kept. Older frames are dropped on purpose so
the live view and any future AI always work on the freshest picture and never
fall behind on a busy Raspberry Pi.

It is safe to use from multiple threads at once: the capture loop writes while
web requests read.
"""

from __future__ import annotations

import threading
import time
from typing import Optional, Tuple


class FrameStore:
    """Holds the single most recent camera frame in a thread-safe way.

    The frame is stored as ready-to-send JPEG bytes (what the browser needs for
    the live MJPEG stream). The raw image array is kept too so later phases can
    run motion detection and AI on it without re-encoding.
    """

    def __init__(self, stale_after_seconds: float = 2.0):
        # A Condition lets the video stream sleep until a NEW frame arrives,
        # instead of busy-looping and wasting CPU on the Pi.
        self._condition = threading.Condition()
        self._jpeg: Optional[bytes] = None
        self._frame = None  # the raw image array, for later phases
        self._frame_count: int = 0
        self._updated_at: float = 0.0  # monotonic clock, for staleness checks
        # If no new frame arrives within this many seconds we treat the camera
        # as "not producing frames" so the dashboard can show it as unavailable.
        self._stale_after = float(stale_after_seconds)

    def update(self, jpeg_bytes: bytes, frame=None) -> None:
        """Store a freshly captured frame and wake up anyone waiting for one."""
        with self._condition:
            self._jpeg = jpeg_bytes
            self._frame = frame
            self._frame_count += 1
            self._updated_at = time.monotonic()
            self._condition.notify_all()

    def get_jpeg(self) -> Optional[bytes]:
        """Return the latest JPEG bytes, or None if no frame has arrived yet."""
        with self._condition:
            return self._jpeg

    def get_frame(self):
        """Return the latest raw image array (for motion/AI in later phases)."""
        with self._condition:
            return self._frame

    def wait_for_next(
        self, last_count: int, timeout: float = 5.0
    ) -> Optional[Tuple[bytes, int]]:
        """Block until a frame newer than ``last_count`` is available.

        Returns ``(jpeg_bytes, frame_count)`` for the new frame, or ``None`` if
        nothing newer arrived within ``timeout`` seconds. The video stream uses
        this to send each frame exactly once without burning CPU between them.
        """
        with self._condition:
            if self._frame_count <= last_count:
                self._condition.wait(timeout)
            if self._frame_count <= last_count or self._jpeg is None:
                return None
            return self._jpeg, self._frame_count

    def seconds_since_update(self) -> Optional[float]:
        """How long ago the last frame arrived, or None if there never was one."""
        with self._condition:
            if self._updated_at == 0.0:
                return None
            return time.monotonic() - self._updated_at

    def is_fresh(self) -> bool:
        """True if a frame arrived recently (the camera is producing frames)."""
        age = self.seconds_since_update()
        return age is not None and age <= self._stale_after

    @property
    def frame_count(self) -> int:
        """Total number of frames received since startup."""
        with self._condition:
            return self._frame_count
