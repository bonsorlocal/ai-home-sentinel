"""Storage module (Phase 3).

Saves event snapshots to local folders and enforces daily limits.
"""

from __future__ import annotations

import os
import threading
from datetime import date, datetime
from typing import Optional

try:
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None


class SnapshotStorage:
    """Saves JPEG snapshots with a configurable daily limit."""

    def __init__(
        self,
        snapshot_dir: str,
        max_snapshots_per_day: int = 500,
    ):
        self._snapshot_dir = os.path.abspath(snapshot_dir)
        self._max_per_day = max(1, int(max_snapshots_per_day))
        self._lock = threading.Lock()
        self._counter = 0
        os.makedirs(self._snapshot_dir, exist_ok=True)

    @property
    def snapshot_dir(self) -> str:
        return self._snapshot_dir

    def _today_count(self) -> int:
        today = date.today().isoformat()
        count = 0
        try:
            for name in os.listdir(self._snapshot_dir):
                if name.startswith(today) and name.endswith(".jpg"):
                    count += 1
        except OSError:
            return 0
        return count

    def _unique_filename(self, prefix: str) -> str:
        """Build a date-prefixed, collision-free filename (caller holds lock)."""
        self._counter += 1
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        return f"{stamp}_{prefix}_{self._counter}.jpg"

    def can_save_today(self) -> bool:
        with self._lock:
            return self._today_count() < self._max_per_day

    def save_snapshot(self, frame, prefix: str = "motion") -> Optional[str]:
        """Save a frame as JPEG. Returns the file path or None if limit reached."""
        if cv2 is None:
            print("[storage] OpenCV not available; cannot save snapshot.")
            return None
        if frame is None:
            return None

        with self._lock:
            if self._today_count() >= self._max_per_day:
                print("[storage] Daily snapshot limit reached; skipping save.")
                return None

            path = os.path.join(self._snapshot_dir, self._unique_filename(prefix))

            try:
                ok = cv2.imwrite(path, frame)
                if not ok:
                    print(f"[storage] Failed to write snapshot to {path}")
                    return None
                return path
            except Exception as error:  # noqa: BLE001
                print(f"[storage] Error saving snapshot: {error}")
                return None

    def save_jpeg_bytes(self, jpeg_bytes: bytes, prefix: str = "face") -> Optional[str]:
        """Save raw JPEG bytes (for face crops etc.)."""
        if not jpeg_bytes:
            return None

        with self._lock:
            if self._today_count() >= self._max_per_day:
                return None

            path = os.path.join(self._snapshot_dir, self._unique_filename(prefix))

            try:
                with open(path, "wb") as handle:
                    handle.write(jpeg_bytes)
                return path
            except OSError as error:
                print(f"[storage] Error saving JPEG: {error}")
                return None
