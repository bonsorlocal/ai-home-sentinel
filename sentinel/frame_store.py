"""Shared latest-frame buffer (Phase 2).

The capture loop stores the full-resolution raw frame on every grab so motion
and AI always see the freshest picture. Browser JPEGs are updated separately at
a lower rate/resolution (see ``camera.stream_*`` in config) so the live view
stays smooth on a busy Pi.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional, Tuple


class FrameStore:
    """Thread-safe buffer for the latest raw frame and stream JPEG."""

    def __init__(self, stale_after_seconds: float = 2.0):
        self._condition = threading.Condition()
        self._jpeg: Optional[bytes] = None
        self._frame = None
        self._capture_count: int = 0
        self._stream_count: int = 0
        self._updated_at: float = 0.0
        self._stale_after = float(stale_after_seconds)
        # Keep a short rolling history for clip pre-roll (not hundreds of
        # full-res frames — that stalls the capture loop on a Pi).
        self._history = deque(maxlen=90)

    def update(self, frame, stream_jpeg: Optional[bytes] = None) -> None:
        """Store a raw capture; optionally publish a new browser stream frame."""
        with self._condition:
            self._frame = frame
            self._capture_count += 1
            if frame is not None:
                self._history.append((time.monotonic(), frame))
            if stream_jpeg is not None:
                self._jpeg = stream_jpeg
                self._stream_count += 1
            self._updated_at = time.monotonic()
            self._condition.notify_all()

    def get_jpeg(self) -> Optional[bytes]:
        with self._condition:
            return self._jpeg

    def get_stream(self) -> Optional[Tuple[bytes, int]]:
        """Return the freshest browser JPEG and its sequence number."""
        with self._condition:
            if self._jpeg is None:
                return None
            return self._jpeg, self._stream_count

    def get_frame(self):
        with self._condition:
            return self._frame

    def wait_for_next_stream(
        self, last_count: int, timeout: float = 5.0
    ) -> Optional[Tuple[bytes, int]]:
        """Block until a new browser stream frame is available.

        Always returns the *latest* JPEG (not an intermediate one), so a slow
        MJPEG client can skip backlog instead of replaying stale frames.
        """
        with self._condition:
            if self._stream_count <= last_count:
                self._condition.wait(timeout)
            if self._stream_count <= last_count or self._jpeg is None:
                return None
            return self._jpeg, self._stream_count

    def wait_for_next(
        self, last_count: int, timeout: float = 5.0
    ) -> Optional[Tuple[bytes, int]]:
        """Alias for :meth:`wait_for_next_stream` (MJPEG consumers)."""
        return self.wait_for_next_stream(last_count, timeout)

    def seconds_since_update(self) -> Optional[float]:
        with self._condition:
            if self._updated_at == 0.0:
                return None
            return time.monotonic() - self._updated_at

    def is_fresh(self) -> bool:
        age = self.seconds_since_update()
        return age is not None and age <= self._stale_after

    @property
    def frame_count(self) -> int:
        """Stream frame count (legacy name used by tests)."""
        with self._condition:
            return self._stream_count

    @property
    def capture_count(self) -> int:
        with self._condition:
            return self._capture_count

    def get_recent_frames(self, seconds: float = 3.0, max_frames: int = 90) -> list:
        """Return newest frames captured in the last ``seconds`` window."""
        cutoff = time.monotonic() - max(0.0, float(seconds))
        max_frames = max(1, int(max_frames))
        with self._condition:
            frames = [frame for ts, frame in self._history if ts >= cutoff]
        if len(frames) > max_frames:
            frames = frames[-max_frames:]
        return frames
