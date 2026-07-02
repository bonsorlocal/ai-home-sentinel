"""Storage module (Phase 3).

Saves event snapshots to local folders and enforces daily limits.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from datetime import date, datetime
from typing import Any, Dict, List, Optional

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


class ClipStorage:
    """Saves short MP4 clips with a configurable daily limit."""

    def __init__(
        self,
        clip_dir: str,
        max_clips_per_day: int = 50,
        record_fps: float = 10.0,
        record_width: int = 640,
        record_height: int = 360,
        prefer_h264: bool = True,
        prune_when_full: bool = True,
    ):
        self._clip_dir = os.path.abspath(clip_dir)
        self._max_per_day = max(1, int(max_clips_per_day))
        self._record_fps = max(1.0, float(record_fps))
        self._record_width = max(64, int(record_width))
        self._record_height = max(64, int(record_height))
        self._prefer_h264 = bool(prefer_h264)
        self._prune_when_full = bool(prune_when_full)
        self._lock = threading.Lock()
        self._counter = 0
        self._last_skip_reason: Optional[str] = None
        os.makedirs(self._clip_dir, exist_ok=True)

    @property
    def clip_dir(self) -> str:
        return self._clip_dir

    def _today_dir(self) -> str:
        path = os.path.join(self._clip_dir, date.today().isoformat())
        os.makedirs(path, exist_ok=True)
        return path

    def _today_count(self, today_dir: str) -> int:
        count = 0
        try:
            for name in os.listdir(today_dir):
                if name.endswith(".mp4"):
                    count += 1
        except OSError:
            return 0
        return count

    def _unique_filename(self, prefix: str) -> str:
        self._counter += 1
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        return f"{stamp}_{prefix}_{self._counter}.mp4"

    @property
    def last_skip_reason(self) -> Optional[str]:
        return self._last_skip_reason

    def can_save_today(self) -> bool:
        with self._lock:
            today_dir = self._today_dir()
            return self._today_count(today_dir) < self._max_per_day

    def clips_today_status(self) -> Dict[str, Any]:
        with self._lock:
            today_dir = self._today_dir()
            count = self._today_count(today_dir)
        return {
            "count_today": count,
            "max_per_day": self._max_per_day,
            "remaining_today": max(0, self._max_per_day - count),
            "limit_reached": count >= self._max_per_day,
            "prune_when_full": self._prune_when_full,
        }

    def _prune_oldest_clip(self, today_dir: str) -> bool:
        candidates: List[tuple[float, str]] = []
        try:
            for name in os.listdir(today_dir):
                if not name.endswith(".mp4"):
                    continue
                full = os.path.join(today_dir, name)
                candidates.append((os.path.getmtime(full), full))
        except OSError:
            return False
        if not candidates:
            return False
        candidates.sort()
        oldest = candidates[0][1]
        try:
            os.remove(oldest)
            print(f"[storage] Pruned oldest clip to make room: {os.path.basename(oldest)}")
            return True
        except OSError as error:
            print(f"[storage] Could not prune clip: {error}")
            return False

    def save_clip(self, frames: List, prefix: str = "motion") -> Optional[str]:
        """Save a list of frames as browser-playable MP4 (H.264 when possible)."""
        if cv2 is None:
            self._last_skip_reason = "OpenCV not available"
            print("[storage] OpenCV not available; cannot save clip.")
            return None
        if not frames:
            self._last_skip_reason = "No frames available for clip"
            return None

        with self._lock:
            today_dir = self._today_dir()
            while self._today_count(today_dir) >= self._max_per_day:
                if not self._prune_when_full or not self._prune_oldest_clip(today_dir):
                    self._last_skip_reason = "Daily clip limit reached"
                    print("[storage] Daily clip limit reached; skipping save.")
                    return None
            self._last_skip_reason = None
            path = os.path.join(today_dir, self._unique_filename(prefix))

        prepared = []
        for frame in frames:
            if frame is None:
                continue
            try:
                prepared.append(
                    cv2.resize(frame, (self._record_width, self._record_height))
                )
            except Exception as error:  # noqa: BLE001
                print(f"[storage] Could not resize clip frame: {error}")
        if not prepared:
            self._last_skip_reason = "No valid frames for clip encoding"
            return None

        if self._prefer_h264 and self._save_via_ffmpeg(prepared, path):
            self._last_skip_reason = None
            return path
        if self._save_via_opencv(prepared, path, fourcc="avc1"):
            self._last_skip_reason = None
            return path
        if self._save_via_opencv(prepared, path, fourcc="H264"):
            self._last_skip_reason = None
            return path
        if self._save_via_opencv(prepared, path, fourcc="mp4v"):
            self._last_skip_reason = None
            return path
        self._last_skip_reason = "Clip encoding failed"
        print("[storage] All clip encoders failed.")
        return None

    def _save_via_ffmpeg(self, frames: List, path: str) -> bool:
        """Encode H.264 MP4 with ffmpeg — plays in Chrome/Safari."""
        if shutil.which("ffmpeg") is None:
            return False
        tmp = tempfile.mkdtemp(prefix="sentinel_clip_")
        try:
            for index, frame in enumerate(frames):
                cv2.imwrite(os.path.join(tmp, f"f{index:04d}.jpg"), frame)
            cmd = [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                str(self._record_fps),
                "-i",
                os.path.join(tmp, "f%04d.jpg"),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                path,
            ]
            subprocess.run(cmd, check=True, timeout=90)
            return os.path.exists(path) and os.path.getsize(path) > 0
        except Exception as error:  # noqa: BLE001
            print(f"[storage] ffmpeg clip encode failed: {error}")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _save_via_opencv(self, frames: List, path: str, *, fourcc: str) -> bool:
        writer = None
        try:
            codec = cv2.VideoWriter_fourcc(*fourcc)
            writer = cv2.VideoWriter(
                path,
                codec,
                self._record_fps,
                (self._record_width, self._record_height),
            )
            if not writer.isOpened():
                return False
            for frame in frames:
                writer.write(frame)
            writer.release()
            writer = None
            return os.path.exists(path) and os.path.getsize(path) > 0
        except Exception as error:  # noqa: BLE001
            print(f"[storage] OpenCV clip encode ({fourcc}) failed: {error}")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            return False
        finally:
            if writer is not None:
                writer.release()
