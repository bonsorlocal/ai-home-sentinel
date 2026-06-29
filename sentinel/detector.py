"""Object detection module (Phase 4).

YOLO-based object detector behind a clean interface. Runs only when motion is
active or on a low fixed interval.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from sentinel.config import Config
from sentinel.frame_store import FrameStore
from sentinel.motion import MotionDetector

try:
    import cv2  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None

DetectionCallback = Callable[[List[Dict[str, Any]], object], None]


class ObjectDetector:
    """Motion-triggered YOLO object detector."""

    def __init__(
        self,
        config: Config,
        frame_store: FrameStore,
        motion: MotionDetector,
        on_detection: Optional[DetectionCallback] = None,
    ):
        det = config.get("detector") or {}
        camera = config.get("camera") or {}

        self._enabled = bool(det.get("enabled", True))
        self._model_path = str(det.get("model_path", "models/yolo_nano.pt"))
        self._confidence = float(det.get("confidence", 0.45))
        self._interval = float(det.get("run_every_n_seconds", 1.0))
        self._classes = set(str(c).lower() for c in (det.get("classes") or []))
        self._ai_width = int(camera.get("ai_width", 640))
        self._ai_height = int(camera.get("ai_height", 360))

        self._frame_store = frame_store
        self._motion = motion
        self._on_detection = on_detection

        self._model = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_run_at: float = 0.0
        self._message = "Object detector not started."

    def start(self) -> None:
        if not self._enabled:
            self._message = "Object detection disabled in config."
            return
        if cv2 is None:
            self._message = "OpenCV not available; object detection disabled."
            print(f"[detector] {self._message}")
            return

        try:
            from ultralytics import YOLO  # type: ignore

            self._model = YOLO(self._model_path)
            self._message = f"Loaded model from {self._model_path}"
            print(f"[detector] {self._message}")
        except Exception as error:  # noqa: BLE001
            self._message = f"Could not load YOLO model: {error}"
            print(f"[detector] {self._message}")
            return

        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="object-detector", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def is_active(self) -> bool:
        return self.is_running() and self._model is not None

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "active": self.is_active(),
            "enabled": self._enabled,
            "model_path": self._model_path,
            "message": self._message,
        }

    def detect_frame(self, frame) -> List[Dict[str, Any]]:
        """Run detection on a single frame (for tests and manual use)."""
        if self._model is None or frame is None or cv2 is None:
            return []

        try:
            small = cv2.resize(frame, (self._ai_width, self._ai_height))
            results = self._model.predict(
                small,
                conf=self._confidence,
                verbose=False,
            )
            return self._parse_results(results)
        except Exception as error:  # noqa: BLE001
            print(f"[detector] Detection error: {error}")
            return []

    def _parse_results(self, results) -> List[Dict[str, Any]]:
        detections: List[Dict[str, Any]] = []
        if not results:
            return detections

        result = results[0]
        names = result.names or {}
        boxes = result.boxes
        if boxes is None:
            return detections

        for box in boxes:
            cls_id = int(box.cls[0])
            label = str(names.get(cls_id, cls_id)).lower()
            if self._classes and label not in self._classes:
                continue
            conf = float(box.conf[0])
            xyxy = box.xyxy[0].tolist()
            detections.append(
                {
                    "label": label,
                    "confidence": round(conf, 3),
                    "bbox": [round(v, 1) for v in xyxy],
                }
            )
        return detections

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(0.2)
            if self._model is None:
                continue

            now = time.monotonic()
            if now - self._last_run_at < self._interval:
                continue

            if not self._motion.is_active():
                continue

            frame = self._frame_store.get_frame()
            if frame is None:
                continue

            self._last_run_at = now
            detections = self.detect_frame(frame)
            if detections and self._on_detection is not None:
                try:
                    self._on_detection(detections, frame)
                except Exception as error:  # noqa: BLE001
                    print(f"[detector] Detection callback error: {error}")
