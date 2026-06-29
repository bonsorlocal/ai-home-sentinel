"""Runtime wiring for Phases 3–5.

Connects motion, storage, events, detector, and face recognition into one
coherent pipeline started from run.py.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sentinel.brain import Brain
from sentinel.config import Config
from sentinel.detector import ObjectDetector
from sentinel.events import EventLedger
from sentinel.face_recognition_module import FaceRecognizer
from sentinel.frame_store import FrameStore
from sentinel.motion import MotionDetector
from sentinel.reasoner import Reasoner
from sentinel.storage import SnapshotStorage


class SentinelRuntime:
    """Owns and coordinates all perception modules."""

    def __init__(self, config: Config, frame_store: FrameStore):
        self._config = config
        storage_cfg = config.get("storage") or {}
        db_path = str(storage_cfg.get("database_path", "data/sentinel.db"))
        snapshot_dir = str(storage_cfg.get("snapshot_dir", "data/events/snapshots"))
        max_snapshots = int(storage_cfg.get("max_snapshots_per_day", 500))
        save_on_motion = bool(storage_cfg.get("save_snapshot_on_motion", True))

        self.ledger = EventLedger(db_path)
        self.storage = SnapshotStorage(snapshot_dir, max_snapshots_per_day=max_snapshots)
        self._save_on_motion = save_on_motion
        self._last_person_detected = False

        self.motion = MotionDetector(
            config, frame_store, on_motion=self._on_motion
        )
        self.detector = ObjectDetector(
            config, frame_store, self.motion, on_detection=self._on_detection
        )
        self.faces = FaceRecognizer(
            config,
            frame_store,
            self.motion,
            on_face=self._on_face,
            person_detected_fn=lambda: self._last_person_detected,
        )
        # The language brain (B1) only needs config + the event ledger. It never
        # runs a thread and never touches the camera, so there is no start/stop.
        self.brain = Brain(config, self.ledger)
        # Phase 7 reasoner: contextual tier/importance scoring.
        self.reasoner = Reasoner(config)

    def start(self) -> None:
        self.motion.start()
        self.detector.start()
        self.faces.start()

    def stop(self) -> None:
        self.faces.stop()
        self.detector.stop()
        self.motion.stop()

    def status(self) -> Dict[str, Any]:
        return {
            "motion": self.motion.status(),
            "detector": self.detector.status(),
            "face_recognition": self.faces.status(),
            "brain": self.brain.status(),
            "reasoner": self.reasoner.status(),
            "event_count": self.ledger.count(),
        }

    def _on_motion(self, frame, area: float) -> None:
        snapshot_path = None
        if self._save_on_motion:
            snapshot_path = self.storage.save_snapshot(frame, prefix="motion")

        entities = {"area": int(area)}
        recent = self.ledger.list_recent(limit=20)
        tier, importance = self.reasoner.classify("motion", entities, recent)
        self.ledger.insert(
            source="motion",
            title="Motion detected",
            summary=f"Movement detected (area={int(area)} pixels)",
            tier=tier,
            importance=importance,
            entities=entities,
            snapshot_path=snapshot_path,
        )

    def _on_detection(self, detections: List[Dict[str, Any]], frame) -> None:
        labels = [d["label"] for d in detections]
        self._last_person_detected = "person" in labels

        storage_cfg = self._config_save_on_person()
        snapshot_path = None
        if storage_cfg and "person" in labels:
            snapshot_path = self.storage.save_snapshot(frame, prefix="person")

        title = f"Detected: {', '.join(sorted(set(labels)))}"
        summary = "; ".join(
            f"{d['label']} ({d['confidence']:.0%})" for d in detections
        )
        entities = {"detections": detections}
        recent = self.ledger.list_recent(limit=20)
        tier, importance = self.reasoner.classify("object", entities, recent)
        self.ledger.insert(
            source="object",
            title=title,
            summary=summary,
            tier=tier,
            importance=importance,
            entities=entities,
            snapshot_path=snapshot_path,
        )

    def _on_face(self, face: Dict[str, Any], frame) -> None:
        name = str(face.get("name", "unknown"))
        known = bool(face.get("known", False))

        snapshot_path = None
        if not known and frame is not None:
            try:
                import cv2  # type: ignore

                top, right, bottom, left = face["location"]
                crop = frame[top:bottom, left:right]
                if crop.size > 0:
                    path = self.storage.save_snapshot(crop, prefix="unknown_face")
                    if path:
                        snapshot_path = path
            except Exception as error:  # noqa: BLE001
                print(f"[face] Could not save unknown face: {error}")

        title = f"Known face: {name}" if known else "Unknown face detected"
        entities = {"face": face}
        recent = self.ledger.list_recent(limit=20)
        tier, importance = self.reasoner.classify("face", entities, recent)
        self.ledger.insert(
            source="face",
            title=title,
            summary=f"Face match: {name} (distance={face.get('distance', '?')})",
            tier=tier,
            importance=importance,
            entities=entities,
            snapshot_path=snapshot_path,
        )

    def _config_save_on_person(self) -> bool:
        storage_cfg = self._config.get("storage") or {}
        return bool(storage_cfg.get("save_snapshot_on_person", True))
