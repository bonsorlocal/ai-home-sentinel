"""Motion detection module (Phase 3).

Lightweight OpenCV background subtraction with an explicit event lifecycle:
idle -> candidate_event -> active_event -> cooldown -> event_closed.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Sequence

from sentinel.config import Config
from sentinel.frame_store import FrameStore

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None
    np = None


MotionCallback = Callable[[object, float], None]
SessionEndCallback = Callable[[object, float, float], None]


class MotionDetector:
    """Background-subtraction motion detector reading from FrameStore."""

    def __init__(
        self,
        config: Config,
        frame_store: FrameStore,
        on_motion: Optional[MotionCallback] = None,
        on_session_started: Optional[MotionCallback] = None,
        on_session_updated: Optional[MotionCallback] = None,
        on_session_ended: Optional[SessionEndCallback] = None,
        on_session_frame: Optional[MotionCallback] = None,
    ):
        motion = config.get("motion") or {}
        camera = config.get("camera") or {}

        self._enabled = bool(motion.get("enabled", True))
        self._min_area = int(motion.get("min_area", 1200))
        self._start_min_area = int(motion.get("start_min_area", self._min_area))
        self._start_consecutive = max(1, int(motion.get("start_consecutive_frames", 2)))
        self._end_still_seconds = float(motion.get("end_still_seconds", 3.0))
        self._end_grace_seconds = float(motion.get("end_grace_seconds", 2.0))
        self._min_session_seconds = float(motion.get("min_session_seconds", 4.0))
        self._session_update_every = float(motion.get("session_update_every_seconds", 1.0))
        self._candidate_window_seconds = float(motion.get("candidate_window_seconds", 1.5))
        self._person_missing_seconds = float(motion.get("person_missing_seconds", 10.0))
        self._person_still_seconds = float(motion.get("person_still_seconds", 15.0))
        self._person_presence_stale_seconds = float(
            motion.get("person_presence_stale_seconds", 4.0)
        )
        self._person_still_bbox_quiet_seconds = float(
            motion.get("person_still_bbox_quiet_seconds", 2.0)
        )
        self._still_bbox_center_drift_px = float(
            motion.get("still_bbox_center_drift_px", 25.0)
        )
        self._still_bbox_area_change_ratio = float(
            motion.get("still_bbox_area_change_ratio", 0.2)
        )
        self._still_motion_area_ratio = float(motion.get("still_motion_area_ratio", 0.35))
        self._global_motion_ratio_max = float(motion.get("global_motion_ratio_max", 0.65))
        self._max_noise_contours = int(motion.get("max_noise_contours", 20))
        self._brightness_jump_threshold = float(
            motion.get("brightness_jump_threshold", 38.0)
        )
        clips = config.get("clips") or {}
        self._max_event_seconds = float(
            motion.get("max_event_seconds", clips.get("max_session_seconds", 90.0))
        )
        self._cooldown = float(motion.get("cooldown_seconds", 5))
        self._learning_rate = float(motion.get("background_learning_rate", 0.02))
        self._ai_width = int(camera.get("ai_width", 640))
        self._ai_height = int(camera.get("ai_height", 360))

        self._frame_store = frame_store
        self._on_motion = on_motion
        self._on_session_started = on_session_started
        self._on_session_updated = on_session_updated
        self._on_session_ended = on_session_ended
        self._on_session_frame = on_session_frame

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._state = "idle"
        self._last_motion_at: float = 0.0
        self._motion_active = False
        self._last_area: float = 0.0
        self._last_motion_ratio: float = 0.0
        self._noise_suppressed_frames = 0
        self._previous_luma: Optional[float] = None
        self._message = "Motion detector not started."
        self._session_active = False
        self._session_started_at = 0.0
        self._session_last_update_at = 0.0
        self._candidate_started_at = 0.0
        self._cooldown_started_at = 0.0
        self._still_started_at = 0.0
        self._start_hits = 0
        self._person_seen_in_session = False
        self._person_last_seen_at = 0.0
        self._last_person_bbox: Optional[tuple[float, float, float, float]] = None
        self._bbox_motion_at = 0.0

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
        """True while in an active session or shortly after recent motion."""
        with self._lock:
            return self._is_active_locked()

    def _is_active_locked(self) -> bool:
        if self._session_active:
            return True
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
                "session_active": self._session_active,
                "state": self._state,
                "last_area": self._last_area,
                "last_motion_ratio": round(self._last_motion_ratio, 4),
                "noise_suppressed_frames": self._noise_suppressed_frames,
                "person_seen_in_session": self._person_seen_in_session,
                "message": self._message,
            }

    def report_person_seen(self, bbox: Optional[Sequence[float]] = None) -> None:
        """Report person presence so session close can use out-of-frame rules."""
        now = time.monotonic()
        parsed_bbox: Optional[tuple[float, float, float, float]] = None
        if bbox is not None and len(bbox) == 4:
            try:
                x1, y1, x2, y2 = (float(v) for v in bbox)
                parsed_bbox = (x1, y1, x2, y2)
            except (TypeError, ValueError):
                parsed_bbox = None

        with self._lock:
            self._person_seen_in_session = True
            self._person_last_seen_at = now
            if parsed_bbox is None:
                return
            previous = self._last_person_bbox
            self._last_person_bbox = parsed_bbox
            if previous is None:
                self._bbox_motion_at = now
                return

            if self._bbox_changed(previous, parsed_bbox):
                self._bbox_motion_at = now

    def _bbox_changed(
        self,
        previous: tuple[float, float, float, float],
        current: tuple[float, float, float, float],
    ) -> bool:
        px1, py1, px2, py2 = previous
        cx1, cy1, cx2, cy2 = current
        prev_center_x = (px1 + px2) / 2.0
        prev_center_y = (py1 + py2) / 2.0
        cur_center_x = (cx1 + cx2) / 2.0
        cur_center_y = (cy1 + cy2) / 2.0
        center_dx = abs(cur_center_x - prev_center_x)
        center_dy = abs(cur_center_y - prev_center_y)
        prev_area = max(1.0, abs(px2 - px1) * abs(py2 - py1))
        cur_area = max(1.0, abs(cx2 - cx1) * abs(cy2 - cy1))
        area_ratio_delta = abs(cur_area - prev_area) / prev_area
        return (
            center_dx > self._still_bbox_center_drift_px
            or center_dy > self._still_bbox_center_drift_px
            or area_ratio_delta > self._still_bbox_area_change_ratio
        )

    def _run(self) -> None:
        subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=16, detectShadows=True
        )

        while not self._stop_event.is_set():
            self._stop_event.wait(0.05)
            if self._stop_event.is_set():
                break

            frame = self._frame_store.get_frame()
            if frame is None:
                continue

            try:
                small = cv2.resize(frame, (self._ai_width, self._ai_height))
                fg_mask = subtractor.apply(small, learningRate=self._learning_rate)
                _, thresh = cv2.threshold(fg_mask, 200, 255, cv2.THRESH_BINARY)
                motion_pixels = float(cv2.countNonZero(thresh))
                total_pixels = float(self._ai_width * self._ai_height)
                motion_ratio = motion_pixels / total_pixels if total_pixels > 0 else 0.0
                luma = float(cv2.mean(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))[0])
                contours, _ = cv2.findContours(
                    thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
                )
                max_area = 0.0
                for contour in contours:
                    area = cv2.contourArea(contour)
                    if area > max_area:
                        max_area = area

                self._handle_area(
                    frame,
                    max_area,
                    contour_count=len(contours),
                    motion_ratio=motion_ratio,
                    luma=luma,
                )
            except Exception as error:  # noqa: BLE001
                print(f"[motion] Frame processing error: {error}")

    def _handle_area(
        self,
        frame,
        area: float,
        *,
        contour_count: int = 1,
        motion_ratio: float = 0.0,
        luma: Optional[float] = None,
    ) -> None:
        now = time.monotonic()
        meaningful_motion = self._is_meaningful_motion(
            area=area,
            contour_count=contour_count,
            motion_ratio=motion_ratio,
            luma=luma,
        )
        with self._lock:
            self._last_area = area
            self._last_motion_ratio = motion_ratio
            if meaningful_motion and area >= self._min_area:
                self._last_motion_at = now
                self._motion_active = True

            if not self._session_active:
                if meaningful_motion and area >= self._start_min_area:
                    if self._state != "candidate_event":
                        self._state = "candidate_event"
                        self._candidate_started_at = now
                    self._start_hits += 1
                else:
                    if (
                        self._state == "candidate_event"
                        and self._candidate_started_at > 0.0
                        and (now - self._candidate_started_at) > self._candidate_window_seconds
                    ):
                        self._state = "idle"
                        self._candidate_started_at = 0.0
                    self._start_hits = 0

                if self._start_hits >= self._start_consecutive:
                    self._session_active = True
                    self._state = "active_event"
                    self._session_started_at = now
                    self._session_last_update_at = now
                    self._candidate_started_at = 0.0
                    self._cooldown_started_at = 0.0
                    self._still_started_at = 0.0
                    self._start_hits = 0
                    self._person_seen_in_session = False
                    self._person_last_seen_at = 0.0
                    self._last_person_bbox = None
                    self._bbox_motion_at = now
                    started = True
                else:
                    started = False
                updated = False
                ended = False
                duration = 0.0
            else:
                started = False
                updated = (now - self._session_last_update_at) >= self._session_update_every
                if updated:
                    self._session_last_update_at = now

                session_age = now - self._session_started_at
                person_out_of_frame = (
                    self._person_seen_in_session
                    and self._person_last_seen_at > 0.0
                    and (now - self._person_last_seen_at) >= self._person_missing_seconds
                )

                still_motion_threshold = max(1.0, self._min_area * self._still_motion_area_ratio)
                low_motion = area < still_motion_threshold or not meaningful_motion
                person_recently_seen = (
                    self._person_seen_in_session
                    and self._person_last_seen_at > 0.0
                    and (now - self._person_last_seen_at) <= self._person_presence_stale_seconds
                )
                bbox_quiet = (now - self._bbox_motion_at) >= self._person_still_bbox_quiet_seconds
                person_still = person_recently_seen and low_motion and bbox_quiet

                if self._person_seen_in_session:
                    if person_still:
                        if self._still_started_at == 0.0:
                            self._still_started_at = now
                    else:
                        self._still_started_at = 0.0
                    still_for = 0.0 if self._still_started_at == 0.0 else now - self._still_started_at
                    still_timeout = still_for >= self._person_still_seconds
                else:
                    if area >= self._min_area:
                        self._still_started_at = 0.0
                    elif self._still_started_at == 0.0:
                        self._still_started_at = now
                    quiet_for = (
                        0.0 if self._still_started_at == 0.0 else now - self._still_started_at
                    )
                    still_timeout = quiet_for >= (self._end_still_seconds + self._end_grace_seconds)

                should_close = (
                    (person_out_of_frame or still_timeout)
                    and session_age >= self._min_session_seconds
                )
                maxed_out = session_age >= self._max_event_seconds
                if maxed_out:
                    self._state = "cooldown"
                elif should_close:
                    if self._state != "cooldown":
                        self._state = "cooldown"
                        self._cooldown_started_at = now
                elif meaningful_motion or person_recently_seen:
                    self._state = "active_event"
                    self._cooldown_started_at = 0.0

                cooldown_elapsed = (
                    self._cooldown_started_at > 0.0
                    and (now - self._cooldown_started_at) >= self._cooldown
                )
                if maxed_out or (self._state == "cooldown" and cooldown_elapsed):
                    self._session_active = False
                    self._motion_active = False
                    self._state = "event_closed"
                    ended = True
                    duration = session_age
                else:
                    ended = False
                    duration = 0.0

        if started and self._on_session_started is not None:
            self._safe_motion_cb(self._on_session_started, frame, area, "session start")

        # Legacy callback for compatibility (fires at session start only).
        if started and self._on_motion is not None:
            self._safe_motion_cb(self._on_motion, frame, area, "motion callback")

        if updated and self._on_session_updated is not None:
            self._safe_motion_cb(self._on_session_updated, frame, area, "session update")

        if self._session_active and self._on_session_frame is not None:
            self._safe_motion_cb(self._on_session_frame, frame, area, "session frame")

        if ended and self._on_session_ended is not None:
            try:
                self._on_session_ended(frame, area, duration)
            except Exception as error:  # noqa: BLE001
                print(f"[motion] session end callback error: {error}")
        if ended:
            with self._lock:
                self._state = "idle"
                self._cooldown_started_at = 0.0
                self._candidate_started_at = 0.0
                self._still_started_at = 0.0
                self._last_person_bbox = None
                self._bbox_motion_at = 0.0
                self._person_seen_in_session = False
                self._person_last_seen_at = 0.0

    @staticmethod
    def _safe_motion_cb(cb: MotionCallback, frame, area: float, label: str) -> None:
        try:
            cb(frame, area)
        except Exception as error:  # noqa: BLE001
            print(f"[motion] {label} callback error: {error}")

    def _is_meaningful_motion(
        self,
        *,
        area: float,
        contour_count: int,
        motion_ratio: float,
        luma: Optional[float],
    ) -> bool:
        if area < self._min_area:
            return False
        if motion_ratio >= self._global_motion_ratio_max:
            self._noise_suppressed_frames += 1
            return False
        if contour_count >= self._max_noise_contours and area < (self._start_min_area * 1.2):
            self._noise_suppressed_frames += 1
            return False
        if luma is not None and self._previous_luma is not None:
            delta = abs(float(luma) - self._previous_luma)
            if delta >= self._brightness_jump_threshold and area < (self._start_min_area * 2.0):
                self._noise_suppressed_frames += 1
                self._previous_luma = float(luma)
                return False
        if luma is not None:
            self._previous_luma = float(luma)
        return True
