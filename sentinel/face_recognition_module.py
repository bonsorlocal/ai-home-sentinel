"""Face recognition module (Phase 5).

Compares detected faces against known encodings and labels them. Runs only when
a person is detected and is disabled by default until faces are enrolled.
"""

from __future__ import annotations

import json
import os
import uuid
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sentinel.config import Config
from sentinel.frame_store import FrameStore
from sentinel.motion import MotionDetector

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # noqa: BLE001
    cv2 = None
    np = None

FaceCallback = Callable[[Dict[str, Any], object], None]

# Lazy import — heavy dependency, only loaded when enabled.
_face_recognition = None


def _get_face_recognition():
    global _face_recognition
    if _face_recognition is None:
        import face_recognition as fr  # type: ignore

        _face_recognition = fr
    return _face_recognition


class FaceRecognizer:
    """Face encoding and matching against enrolled known faces."""

    def __init__(
        self,
        config: Config,
        frame_store: FrameStore,
        motion: MotionDetector,
        on_face: Optional[FaceCallback] = None,
        person_detected_fn: Optional[Callable[[], bool]] = None,
    ):
        face_cfg = config.get("face_recognition") or {}

        self._enabled = bool(face_cfg.get("enabled", False))
        self._known_dir = str(face_cfg.get("known_faces_dir", "data/known_faces"))
        self._unknown_dir = str(face_cfg.get("unknown_faces_dir", "data/events/unknown_faces"))
        self._tolerance = float(face_cfg.get("tolerance", 0.5))
        self._run_on_person = bool(face_cfg.get("run_only_when_person_detected", True))

        self._frame_store = frame_store
        self._motion = motion
        self._on_face = on_face
        self._person_detected_fn = person_detected_fn

        self._known_encodings: List[Any] = []
        self._known_names: List[str] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_run_at: float = 0.0
        self._message = "Face recognition not started."

        os.makedirs(self._known_dir, exist_ok=True)
        os.makedirs(self._unknown_dir, exist_ok=True)

    def start(self) -> None:
        if not self._enabled:
            self._message = "Face recognition disabled in config."
            return
        if cv2 is None or np is None:
            self._message = "OpenCV not available; face recognition disabled."
            print(f"[face] {self._message}")
            return

        try:
            _get_face_recognition()
            self._reload_known_faces()
        except Exception as error:  # noqa: BLE001
            self._message = f"Could not load face_recognition: {error}"
            print(f"[face] {self._message}")
            return

        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="face-recognizer", daemon=True
        )
        self._thread.start()
        self._message = f"Face recognition running ({len(self._known_names)} known faces)"

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def is_active(self) -> bool:
        return self.is_running() and self._enabled

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "active": self.is_active(),
            "enabled": self._enabled,
            "known_faces": len(self._known_names),
            "last_run_seconds_ago": (
                None
                if self._last_run_at <= 0.0
                else round(max(0.0, time.monotonic() - self._last_run_at), 2)
            ),
            "message": self._message,
        }

    def reload_known_faces(self) -> int:
        """Reload encodings from disk. Returns count of known faces."""
        self._reload_known_faces()
        return len(self._known_names)

    def _reload_known_faces(self) -> None:
        fr = _get_face_recognition()
        encodings: List[Any] = []
        names: List[str] = []

        if not os.path.isdir(self._known_dir):
            self._known_encodings = encodings
            self._known_names = names
            return

        for name in sorted(os.listdir(self._known_dir)):
            person_dir = os.path.join(self._known_dir, name)
            if not os.path.isdir(person_dir):
                continue
            meta_path = os.path.join(person_dir, "meta.json")
            label = name
            if os.path.isfile(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as handle:
                        meta = json.load(handle)
                    label = str(meta.get("label", name))
                except (OSError, json.JSONDecodeError):
                    pass

            for fname in os.listdir(person_dir):
                if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
                    continue
                path = os.path.join(person_dir, fname)
                try:
                    image = fr.load_image_file(path)
                    face_locs = fr.face_locations(image)
                    if not face_locs:
                        continue
                    enc = fr.face_encodings(image, face_locs)[0]
                    encodings.append(enc)
                    names.append(label)
                except Exception as error:  # noqa: BLE001
                    print(f"[face] Could not load {path}: {error}")

        self._known_encodings = encodings
        self._known_names = names

    def enroll_face(self, image_path: str, person_id: str, label: str) -> bool:
        """Enroll a face image for a person. Creates person_dir with meta.json."""
        fr = _get_face_recognition()
        person_dir = os.path.join(self._known_dir, person_id)
        os.makedirs(person_dir, exist_ok=True)

        meta = {"label": label, "person_id": person_id}
        with open(os.path.join(person_dir, "meta.json"), "w", encoding="utf-8") as handle:
            json.dump(meta, handle, indent=2)

        dest = os.path.join(person_dir, os.path.basename(image_path))
        if os.path.abspath(image_path) != os.path.abspath(dest):
            import shutil

            shutil.copy2(image_path, dest)

        image = fr.load_image_file(dest)
        if not fr.face_locations(image):
            return False

        self._reload_known_faces()
        return True

    def save_unknown_face(self, frame) -> Optional[str]:
        """Persist an unknown-face crop in unknown_faces_dir."""
        if frame is None or cv2 is None:
            return None
        ts = int(time.time())
        name = f"unknown_{ts}_{uuid.uuid4().hex[:8]}.jpg"
        path = os.path.join(self._unknown_dir, name)
        try:
            ok = cv2.imwrite(path, frame)
            return path if ok else None
        except Exception:  # noqa: BLE001
            return None

    def match_frame(self, frame) -> List[Dict[str, Any]]:
        """Find and identify faces in a frame."""
        if frame is None:
            return []

        fr = _get_face_recognition()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = fr.face_locations(rgb)
        if not locations:
            return []

        encodings = fr.face_encodings(rgb, locations)
        results: List[Dict[str, Any]] = []

        for encoding, location in zip(encodings, locations):
            name = "unknown"
            distance = 1.0
            if self._known_encodings:
                distances = fr.face_distance(self._known_encodings, encoding)
                best_idx = int(np.argmin(distances))
                best_dist = float(distances[best_idx])
                if best_dist <= self._tolerance:
                    name = self._known_names[best_idx]
                    distance = best_dist

            top, right, bottom, left = location
            results.append(
                {
                    "name": name,
                    "known": name != "unknown",
                    "distance": round(distance, 3),
                    "location": [top, right, bottom, left],
                }
            )
        return results

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(0.5)
            if not self._motion.is_active():
                continue
            if self._run_on_person and self._person_detected_fn is not None:
                if not self._person_detected_fn():
                    continue

            now = time.monotonic()
            if now - self._last_run_at < 2.0:
                continue

            frame = self._frame_store.get_frame()
            if frame is None:
                continue

            self._last_run_at = now
            faces = self.match_frame(frame)
            for face in faces:
                if self._on_face is not None:
                    try:
                        self._on_face(face, frame)
                    except Exception as error:  # noqa: BLE001
                        print(f"[face] Face callback error: {error}")
