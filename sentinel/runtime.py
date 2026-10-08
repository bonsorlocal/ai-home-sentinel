"""Runtime wiring for Phases 3–8.

Session-oriented pipeline:
- Motion opens/closes a single activity session.
- Detector/face modules enrich the active session.
- Session end writes one consolidated ledger event + optional clip.
"""

from __future__ import annotations

import time
from datetime import datetime
import os
from typing import Any, Dict, List, Optional

from sentinel.actions import ActionHandler
from sentinel.brain import Brain
from sentinel.clip_metadata import ClipMetadataWorker
from sentinel.config import Config
from sentinel.detector import ObjectDetector
from sentinel.dvr import ContinuousRecorder
from sentinel.events import EventLedger, EventRecord
from sentinel.face_recognition_module import FaceRecognizer
from sentinel.frame_store import FrameStore
from sentinel.live_vision import CloudLiveVisionWorker
from sentinel.motion import MotionDetector
from sentinel.notifier import Notifier
from sentinel.pi_bridge import PiBridge
from sentinel.reasoner import Reasoner
from sentinel.resource_guard import ResourceGuard
from sentinel.storage import ClipStorage, SnapshotStorage
from sentinel.telephony import TelephonyBridge
from sentinel.voice_out import CameraVoice
from sentinel.google_cloud import GoogleCloudServices, gemini_keyframe_analysis_is_weak
from sentinel.utils import now_iso


class SentinelRuntime:
    """Owns and coordinates all perception modules."""

    def __init__(self, config: Config, frame_store: FrameStore):
        self._config = config
        self._frame_store = frame_store
        storage_cfg = config.get("storage") or {}
        db_path = str(storage_cfg.get("database_path", "data/sentinel.db"))
        snapshot_dir = str(storage_cfg.get("snapshot_dir", "data/events/snapshots"))
        max_snapshots = int(storage_cfg.get("max_snapshots_per_day", 500))
        save_on_tier2 = bool(storage_cfg.get("save_snapshot_on_tier2", True))

        clips_cfg = config.get("clips") or {}
        clip_dir = str(clips_cfg.get("clip_dir", "data/events/clips"))
        max_clips = int(clips_cfg.get("max_clips_per_day", 50))
        pre_roll = float(clips_cfg.get("pre_roll_seconds", 3))
        post_roll = float(clips_cfg.get("post_roll_seconds", 5))
        record_fps = float(clips_cfg.get("record_fps", 10))
        record_width = int(clips_cfg.get("record_width", 640))
        record_height = int(clips_cfg.get("record_height", 360))

        self._clips_enabled = bool(clips_cfg.get("enabled", False))
        self._save_on_motion = bool(clips_cfg.get("save_on_motion", True))
        self._save_on_detection = bool(clips_cfg.get("save_on_detection", True))
        self._min_seconds_between_clips = max(
            0.0, float(clips_cfg.get("min_seconds_between_clips", 15))
        )
        self._min_session_seconds_for_clip = max(
            0.0, float(clips_cfg.get("min_session_seconds_for_clip", 8))
        )
        self._clip_pre_roll = max(0.0, pre_roll)
        self._clip_post_roll_seconds = max(0.0, post_roll)
        self._clip_post_repeat = max(1, int(record_fps * self._clip_post_roll_seconds))
        self._clip_record_fps = max(1.0, float(record_fps))
        self._clip_record_width = max(64, int(record_width))
        self._clip_record_height = max(64, int(record_height))
        self._clip_max_session = float(clips_cfg.get("max_session_seconds", 90))
        self._clip_sample_interval = 1.0 / self._clip_record_fps
        self._save_on_tier2 = save_on_tier2

        self.ledger = EventLedger(db_path)
        self.storage = SnapshotStorage(snapshot_dir, max_snapshots_per_day=max_snapshots)
        self.clip_storage = ClipStorage(
            clip_dir=clip_dir,
            max_clips_per_day=max_clips,
            record_fps=record_fps,
            record_width=record_width,
            record_height=record_height,
            prefer_h264=bool(clips_cfg.get("prefer_h264", True)),
            prune_when_full=bool(clips_cfg.get("prune_when_full", True)),
        )

        self._active_session: Optional[Dict[str, Any]] = None
        self._last_person_detected = False
        self._last_person_detected_at = 0.0
        self._last_clip_saved_at = 0.0
        self._session_seq = 0
        self._face_callback_errors = 0
        self._last_interim_notify_at = 0.0
        self._last_ai_mode = "local"

        self.motion = MotionDetector(
            config,
            frame_store,
            on_session_started=self._on_motion_session_started,
            on_session_updated=self._on_motion_session_updated,
            on_session_ended=self._on_motion_session_ended,
            on_session_frame=self._on_motion_session_frame,
        )
        self.resource_guard = ResourceGuard(
            config,
            cloud_available_fn=self._cloud_ai_available,
        )
        self.detector = ObjectDetector(
            config,
            frame_store,
            self.motion,
            on_detection=self._on_detection,
            mode_fn=lambda: self.resource_guard.mode,
        )
        self.faces = FaceRecognizer(
            config,
            frame_store,
            self.motion,
            on_face=self._on_face,
            person_detected_fn=self._person_recently_detected,
        )
        self.dvr = ContinuousRecorder(config, frame_store.get_frame)
        self.brain = Brain(
            config,
            self.ledger,
            frame_getter=frame_store.get_frame,
            camera_active_fn=frame_store.is_fresh,
            dvr_context_fn=self._build_dvr_context,
            owner_enroll_fn=self._enroll_owner,
        )
        self.reasoner = Reasoner(config)
        self.notifier = Notifier(config)
        self.clip_metadata = ClipMetadataWorker(config, self.ledger)
        self.google = GoogleCloudServices(config)
        synthesize = None
        if self.google.text_to_speech.is_available():
            synthesize = self.google.text_to_speech.synthesize
        self.voice_out = CameraVoice(config, synthesize_fn=synthesize)
        self.telephony = TelephonyBridge(config)
        self.actions = ActionHandler(
            config,
            self.ledger,
            speak_fn=self.voice_out.speak,
            brain_ask_fn=self.brain.ask,
            telephony_fn=self.telephony.connect_owner,
        )
        self.notifier.set_action_urls_fn(self.actions.action_urls)
        self.live_vision = CloudLiveVisionWorker(
            config,
            frame_getter=frame_store.get_frame,
            session_active_fn=lambda: bool(self._active_session)
            and not bool((self._active_session or {}).get("closed")),
            on_scene=self._on_cloud_scene,
        )
        self.pi_bridge = PiBridge(
            config,
            self.ledger,
            self.dvr,
            camera_active_fn=frame_store.is_fresh,
        )

    def start(self) -> None:
        if self.clip_metadata.is_available():
            self.dvr.configure_auto_summaries(analyzer_fn=self.clip_metadata.analyze_video)
        else:
            self.dvr.configure_auto_summaries()
        self.resource_guard.start()
        self.dvr.start()
        self.motion.start()
        self.detector.start()
        self.faces.start()
        self.live_vision.start()
        self.clip_metadata.start()
        self.pi_bridge.start()

    def stop(self) -> None:
        self.live_vision.stop()
        self.dvr.stop()
        self.faces.stop()
        self.detector.stop()
        self.motion.stop()
        self.clip_metadata.stop()
        self.pi_bridge.stop()
        self.resource_guard.stop()

    def status(self) -> Dict[str, Any]:
        self._sync_ai_mode()
        face_status = dict(self.faces.status())
        face_status["person_recently_detected"] = self._person_recently_detected()
        face_status["face_callback_errors"] = self._face_callback_errors
        return {
            "motion": self.motion.status(),
            "detector": self.detector.status(),
            "face_recognition": face_status,
            "brain": self.brain.status(),
            "reasoner": self.reasoner.status(),
            "notifications": self.notifier.status(),
            "clips": {
                "enabled": self._clips_enabled,
                "clip_dir": self.clip_storage.clip_dir,
                **self.clip_storage.clips_today_status(),
            },
            "dvr": self.dvr.status(),
            "video_metadata": self.clip_metadata.status(),
            "google": self.google.status(),
            "live_vision": self.live_vision.status(),
            "resource_guard": self.resource_guard.status(),
            "ai_mode": self.resource_guard.mode,
            "voice_out": self.voice_out.status(),
            "actions": self.actions.status(),
            "telephony": self.telephony.status(),
            "session_active": bool(self._active_session),
            "event_count": self.ledger.count(),
            "owner_profile": self.brain.owner_profile_status(),
            "pi_bridge": self.pi_bridge.status(),
        }

    def _cloud_ai_available(self) -> bool:
        try:
            if getattr(self, "live_vision", None) is not None and self.live_vision.is_available():
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            return bool(self.detector._cloud_available())
        except Exception:  # noqa: BLE001
            return False

    def _sync_ai_mode(self) -> None:
        """React to ResourceGuard mode changes (unload/reload local models)."""
        mode = self.resource_guard.mode
        if mode == self._last_ai_mode:
            return
        previous = self._last_ai_mode
        self._last_ai_mode = mode
        if mode in ("cloud", "degraded"):
            self.detector.unload_for_offload()
            # Face recognition is heavy; pause when offloaded.
            try:
                if self.faces.is_running():
                    self.faces.stop()
            except Exception:  # noqa: BLE001
                pass
        elif mode == "local" and previous in ("cloud", "degraded"):
            self.detector.ensure_local()
            try:
                if bool((self._config.get("face_recognition") or {}).get("enabled", False)):
                    self.faces.start()
            except Exception:  # noqa: BLE001
                pass

    def _on_cloud_scene(self, scene: Dict[str, Any], frame) -> None:
        """Interim tier-2 path when live cloud vision sees a door visitor."""
        session = self._active_session
        if session is None or bool(session.get("closed")):
            return
        session["cloud_scene"] = dict(scene)
        activity = str(scene.get("activity", "none"))
        visitor = bool(scene.get("visitor_at_door", False))
        if not visitor and activity not in ("knocking", "waiting", "delivering"):
            return
        # Avoid spamming interim events for the same session.
        if session.get("interim_notified"):
            return

        entities: Dict[str, Any] = {
            "cloud_scene": dict(scene),
            "interim": True,
            "session": {
                "session_id": session.get("session_id"),
                "started_at": session.get("started_at"),
            },
            "event_type": "door_visitor" if visitor else f"door_{activity}",
        }
        recent = self.ledger.list_recent(limit=20)
        tier, importance = self.reasoner.classify("system", entities, recent)
        if tier < 2:
            return

        summary = str(scene.get("short_summary") or f"Door activity: {activity}")
        title = "Visitor at the door"
        if activity == "knocking":
            title = "Someone is knocking"
        elif activity == "delivering":
            title = "Delivery at the door"
        elif activity == "waiting":
            title = "Someone is waiting at the door"

        snapshot_path = None
        if frame is not None and self._save_on_tier2:
            snapshot_path = self.storage.save_snapshot(frame, prefix="door")

        record = self._insert_event(
            source="system",
            title=title,
            summary=summary,
            tier=tier,
            importance=importance,
            entities=entities,
            snapshot_path=snapshot_path,
        )
        session["interim_notified"] = True
        self._last_interim_notify_at = time.monotonic()
        self._annotate_dvr_from_event(record)

    def _insert_event(self, **kwargs: Any) -> EventRecord:
        """Insert one session event and send downstream updates."""
        self._sync_ai_mode()
        # Attach latest cloud scene onto closing session events when present.
        entities = dict(kwargs.get("entities") or {})
        session = self._active_session
        if session and session.get("cloud_scene") and "cloud_scene" not in entities:
            entities["cloud_scene"] = dict(session["cloud_scene"])
            kwargs["entities"] = entities
            if kwargs.get("tier", 1) < 2:
                recent = self.ledger.list_recent(limit=20)
                tier, importance = self.reasoner.classify(
                    str(kwargs.get("source", "motion")),
                    entities,
                    recent,
                )
                kwargs["tier"] = tier
                kwargs["importance"] = importance
        record = self.ledger.insert(**kwargs)
        if record.clip_path:
            self.clip_metadata.enqueue(record.id, record.clip_path)
        if self.notifier.notify(record):
            self.ledger.mark_notified(record.id)
        self.pi_bridge.on_event(record)
        return record

    def _downsample_clip_frames(self, frames: List, max_frames: int) -> List:
        if len(frames) <= max_frames:
            return frames
        if max_frames <= 1:
            return [frames[-1]]
        step = (len(frames) - 1) / (max_frames - 1)
        return [frames[int(round(i * step))] for i in range(max_frames)]

    def _resize_clip_frame(self, frame):
        if frame is None:
            return None
        try:
            import cv2  # type: ignore

            return cv2.resize(frame, (self._clip_record_width, self._clip_record_height))
        except Exception:
            return frame.copy() if hasattr(frame, "copy") else frame

    def _maybe_sample_clip_frame(self, session: Dict[str, Any], frame) -> None:
        if frame is None or not self._clips_enabled:
            return
        now = time.monotonic()
        last = float(session.get("last_clip_sample_at", 0.0))
        if last > 0.0 and (now - last) < self._clip_sample_interval:
            return
        session["last_clip_sample_at"] = now
        sample = self._resize_clip_frame(frame)
        if sample is None:
            return
        clip_frames: List = session.setdefault("clip_frames", [])
        clip_frames.append(sample)
        max_session_frames = int(self._clip_record_fps * self._clip_max_session)
        if len(clip_frames) > max_session_frames:
            session["clip_frames"] = clip_frames[-max_session_frames:]

    def _within_clip_cooldown(self) -> bool:
        if self._last_clip_saved_at <= 0.0:
            return False
        return (time.monotonic() - self._last_clip_saved_at) < self._min_seconds_between_clips

    def _should_save_clip(
        self,
        *,
        source: str,
        labels: List[str],
        duration: float,
        peak_area: float,
        tier: int,
        has_person: bool,
    ) -> tuple[bool, str]:
        if not self._clips_enabled:
            return False, "clips disabled"

        if tier >= 2:
            return True, ""

        if has_person or "person" in labels:
            if not self._save_on_detection:
                return False, "save_on_detection disabled"
            return True, ""

        if source == "motion":
            if not self._save_on_motion:
                return False, "save_on_motion disabled"
            if duration < self._min_session_seconds_for_clip:
                return False, "short motion-only session"
            motion_min = float(self._config.get("motion", "min_area", default=1200))
            if peak_area < motion_min:
                return False, "low motion peak"
            if self._within_clip_cooldown():
                return False, "clip cooldown"
            return True, ""

        if source in ("object", "face"):
            if not self._save_on_detection:
                return False, "save_on_detection disabled"
            if self._within_clip_cooldown():
                return False, "clip cooldown"
            return True, ""

        return False, "no clip criteria met"

    def _build_motion_clip(
        self,
        session: Dict[str, Any],
        frame,
        prefix: str,
        session_duration: Optional[float] = None,
    ) -> Optional[str]:
        if not self._clips_enabled:
            return None
        pre_frames = list(session.get("clip_pre_frames") or [])
        active_frames = list(session.get("clip_frames") or [])
        # Session sampling runs through cooldown; no extra post-roll fetch (avoids overlap).
        frames = pre_frames + active_frames
        if not frames and frame is not None:
            frames = [self._resize_clip_frame(frame)]
        if not frames:
            return None
        target_seconds = min(
            self._clip_max_session,
            self._clip_pre_roll + float(session_duration or 0.0) + self._clip_post_roll_seconds,
        )
        max_frames = max(1, int(self._clip_record_fps * target_seconds))
        trimmed = len(frames) > max_frames
        if trimmed:
            frames = self._downsample_clip_frames(frames, max_frames)
        clip_path = self.clip_storage.save_clip(frames, prefix=prefix)
        return clip_path

    def _on_motion_session_frame(self, frame, area: float) -> None:
        session = self._active_session
        if session is None:
            return
        self._maybe_sample_clip_frame(session, frame)

    def _on_motion_session_started(self, frame, area: float) -> None:
        self._session_seq += 1
        session_id = f"session-{self._session_seq}-{int(time.time())}"
        self._active_session = {
            "session_id": session_id,
            "lifecycle_state": "active_event",
            "started_at": now_iso(),
            "started_monotonic": datetime.now().timestamp(),
            "last_update_at": now_iso(),
            "peak_area": float(area),
            "last_area": float(area),
            "motion_updates": 1,
            "detections": [],
            "faces": [],
            "face_tracks": {},
            "transitions": [{"state": "active_event", "at": now_iso()}],
            "snapshot_path": None,
            "last_frame": frame,
            "closed": False,
            "clip_pre_frames": [
                self._resize_clip_frame(f)
                for f in self._frame_store.get_recent_frames(
                    seconds=self._clip_pre_roll,
                    max_frames=max(15, int(self._clip_record_fps * self._clip_pre_roll * 3)),
                )
                if f is not None
            ],
            "clip_frames": [],
            "last_clip_sample_at": 0.0,
        }
        self._maybe_sample_clip_frame(self._active_session, frame)

    def _on_motion_session_updated(self, frame, area: float) -> None:
        session = self._active_session
        if session is None:
            return
        session["last_update_at"] = now_iso()
        session["lifecycle_state"] = "active_event"
        session["last_area"] = float(area)
        session["peak_area"] = max(float(session.get("peak_area", 0.0)), float(area))
        session["motion_updates"] = int(session.get("motion_updates", 0)) + 1
        if frame is not None:
            session["last_frame"] = frame
        self._maybe_sample_clip_frame(session, frame)

    def _on_motion_session_ended(self, frame, area: float, duration_seconds: float) -> None:
        session = self._active_session
        if session is None:
            return
        if bool(session.get("closed", False)):
            return
        session["closed"] = True
        session["lifecycle_state"] = "event_closed"
        session["last_update_at"] = now_iso()
        session["last_area"] = float(area)
        session["peak_area"] = max(float(session.get("peak_area", 0.0)), float(area))
        session["duration_seconds"] = round(float(duration_seconds), 1)
        transitions = list(session.get("transitions", []))
        transitions.append({"state": "event_closed", "at": now_iso()})
        session["transitions"] = transitions

        detections: List[Dict[str, Any]] = session.get("detections", [])
        faces: List[Dict[str, Any]] = session.get("faces", [])
        face_tracks: Dict[str, Dict[str, Any]] = session.get("face_tracks", {})
        labels = sorted({str(d.get("label", "")).lower() for d in detections if d.get("label")})
        known_faces = sorted(
            {
                str(f.get("name", "")).strip()
                for f in faces
                if f.get("known") and str(f.get("name", "")).strip()
            }
        )
        unknown_faces = sum(1 for f in faces if not f.get("known", False))

        if unknown_faces > 0 or known_faces:
            source = "face"
            if unknown_faces > 0:
                title = "Unknown face detected"
            else:
                title = f"Known face: {', '.join(known_faces[:3])}"
        elif labels:
            source = "object"
            title = f"Detected: {', '.join(labels)}"
        else:
            source = "motion"
            title = "Motion session"

        primary_subject = "motion"
        known_identity: Optional[str] = None
        event_type = "motion_event"
        if unknown_faces > 0:
            primary_subject = "unknown_person"
            event_type = "unknown_person_detected"
        elif known_faces:
            primary_subject = "known_person"
            known_identity = known_faces[0]
            event_type = "known_person_detected"
        elif labels:
            primary_subject = labels[0]
            event_type = f"{labels[0]}_detected"

        duration = float(session.get("duration_seconds", 0.0))
        summary_parts = [f"Session lasted {duration:.1f}s"]
        if labels:
            summary_parts.append(f"objects: {', '.join(labels)}")
        if known_faces:
            summary_parts.append(f"known faces: {', '.join(known_faces[:3])}")
        if unknown_faces:
            summary_parts.append(f"unknown faces: {unknown_faces}")
        summary = "; ".join(summary_parts)

        entities: Dict[str, Any] = {
            "session": {
                "started_at": session.get("started_at"),
                "ended_at": session.get("last_update_at"),
                "duration_seconds": duration,
                "peak_area": int(float(session.get("peak_area", 0.0))),
                "motion_updates": int(session.get("motion_updates", 0)),
            }
        }
        entities["event_type"] = event_type
        entities["primary_subject"] = primary_subject
        entities["known_identity"] = known_identity
        entities["zone"] = str((self._config.get("motion", "zone", default="") or "")).strip() or None
        entities["tracking"] = {
            "tracked_labels": len(labels),
            "face_track_count": len(face_tracks),
            "detection_count": len(detections),
            "face_count": len(faces),
        }
        if detections:
            entities["detections"] = detections
        if faces:
            entities["faces"] = faces
        if source == "face":
            entities["face"] = {
                "known": unknown_faces == 0 and bool(known_faces),
                "name": known_faces[0] if known_faces else "unknown",
                "unknown_count": unknown_faces,
            }
            entities["face_tracks"] = face_tracks
        if source == "motion":
            entities["area"] = int(float(session.get("peak_area", 0.0)))
        entities["session"]["session_id"] = session.get("session_id")
        entities["session"]["lifecycle"] = session.get("transitions", [])

        has_person = "person" in labels or bool(known_faces) or unknown_faces > 0
        peak_area = float(session.get("peak_area", 0.0))
        recent = self.ledger.list_recent(limit=20)
        tier, importance = self.reasoner.classify(source, entities, recent)

        save_clip, skip_reason = self._should_save_clip(
            source=source,
            labels=labels,
            duration=duration,
            peak_area=peak_area,
            tier=tier,
            has_person=has_person,
        )

        clip_prefix = "person" if "person" in labels else source
        clip_frame = frame if frame is not None else session.get("last_frame")
        clip_path: Optional[str] = None
        if save_clip:
            clip_path = self._build_motion_clip(
                session, clip_frame, prefix=clip_prefix, session_duration=duration
            )
            if clip_path:
                self._last_clip_saved_at = time.monotonic()

        if not self._clips_enabled:
            entities["clip_status"] = "disabled"
            entities["clip_expected"] = False
        elif clip_path:
            entities["clip_status"] = "saved"
            entities["clip_expected"] = True
        elif not save_clip:
            entities["clip_status"] = "skipped"
            entities["clip_expected"] = False
            entities["clip_note"] = skip_reason
        else:
            entities["clip_status"] = "not_saved"
            entities["clip_expected"] = True
            skip_reason = self.clip_storage.last_skip_reason
            entities["clip_note"] = skip_reason or "No clip could be encoded from this session."
        snapshot_path = session.get("snapshot_path")

        if snapshot_path is None and tier >= 2 and self._save_on_tier2 and frame is not None:
            snap_prefix = "person" if "person" in labels else source
            snapshot_path = self.storage.save_snapshot(frame, prefix=snap_prefix)

        record = self._insert_event(
            source=source,
            title=title,
            summary=summary,
            tier=tier,
            importance=importance,
            entities=entities,
            snapshot_path=snapshot_path,
            clip_path=clip_path,
        )
        self._annotate_dvr_from_event(record)
        self._active_session = None

    def _on_motion(self, frame, area: float) -> None:
        """Legacy helper used by tests; creates a short one-shot session."""
        if self._active_session is None:
            self._on_motion_session_started(frame, area)
        self._on_motion_session_updated(frame, area)
        self._on_motion_session_ended(frame, area, max(1.0, float(area > 0)))

    def _on_detection(self, detections: List[Dict[str, Any]], _frame) -> None:
        labels = [str(d.get("label", "")).lower() for d in detections]
        self._last_person_detected = "person" in labels
        if self._last_person_detected:
            self._last_person_detected_at = time.monotonic()
        if self._last_person_detected:
            person = max(
                (d for d in detections if str(d.get("label", "")).lower() == "person"),
                key=lambda d: float(d.get("confidence", 0.0)),
                default=None,
            )
            if person is not None:
                self.motion.report_person_seen(person.get("bbox"))

        session = self._active_session
        if session is None:
            return
        session["last_update_at"] = now_iso()
        existing: List[Dict[str, Any]] = session.setdefault("detections", [])
        by_label = {str(d.get("label", "")).lower(): d for d in existing}
        now = now_iso()
        for item in detections:
            label = str(item.get("label", "")).lower()
            if not label:
                continue
            current = by_label.get(label)
            if current is None:
                enriched = dict(item)
                enriched["first_seen_at"] = now
                enriched["last_seen_at"] = now
                enriched["observations"] = 1
                by_label[label] = enriched
                continue

            current["last_seen_at"] = now
            current["observations"] = int(current.get("observations", 1)) + 1
            if float(item.get("confidence", 0.0)) > float(current.get("confidence", 0.0)):
                current.update(item)
                current["first_seen_at"] = current.get("first_seen_at", now)
        session["detections"] = list(by_label.values())

    def _on_face(self, face: Dict[str, Any], frame) -> None:
        session = self._active_session
        if session is None:
            return
        now = now_iso()
        session["last_update_at"] = now
        faces: List[Dict[str, Any]] = session.setdefault("faces", [])
        name = str(face.get("name", "")).strip() or "unknown"
        known = bool(face.get("known", False))
        identity = name if known else "unknown"
        best: Optional[Dict[str, Any]] = next(
            (f for f in faces if str(f.get("identity", "")) == identity),
            None,
        )
        if best is None:
            merged = dict(face)
            merged["identity"] = identity
            merged["first_seen_at"] = now
            merged["last_seen_at"] = now
            merged["observations"] = 1
            faces.append(merged)
        else:
            best["last_seen_at"] = now
            best["observations"] = int(best.get("observations", 1)) + 1
            old_distance = float(best.get("distance", 1.0))
            new_distance = float(face.get("distance", 1.0))
            if new_distance <= old_distance:
                keep = dict(face)
                keep["identity"] = identity
                keep["first_seen_at"] = best.get("first_seen_at", now)
                keep["last_seen_at"] = now
                keep["observations"] = best["observations"]
                faces.remove(best)
                faces.append(keep)

        tracks: Dict[str, Dict[str, Any]] = session.setdefault("face_tracks", {})
        track = tracks.get(identity)
        if track is None:
            tracks[identity] = {
                "known": known,
                "name": name,
                "first_seen_at": now,
                "last_seen_at": now,
                "observations": 1,
            }
        else:
            track["last_seen_at"] = now
            track["observations"] = int(track.get("observations", 1)) + 1

        self.motion.report_person_seen(face.get("location"))

        if known or frame is None:
            return
        if session.get("snapshot_path"):
            return
        try:
            import cv2  # type: ignore

            top, right, bottom, left = face["location"]
            crop = frame[top:bottom, left:right]
            if crop.size > 0:
                unknown_path = self.faces.save_unknown_face(crop)
                if unknown_path:
                    session["unknown_face_path"] = unknown_path
                session["snapshot_path"] = self.storage.save_snapshot(crop, prefix="unknown_face")
        except Exception as error:  # noqa: BLE001
            self._face_callback_errors += 1
            print(f"[face] Could not save unknown face: {error}")

    def _person_recently_detected(self) -> bool:
        if not self._last_person_detected:
            return False
        return (time.monotonic() - self._last_person_detected_at) <= 4.0

    def _annotate_dvr_from_event(self, record: EventRecord) -> None:
        if not self.dvr.enabled:
            return
        entities = record.entities or {}
        session = entities.get("session") or {}
        start_ts = str(session.get("started_at") or "").strip()
        end_ts = str(session.get("ended_at") or "").strip()
        if not start_ts or not end_ts:
            return
        detections = entities.get("detections") or []
        self.dvr.annotate_event(
            start_ts=start_ts,
            end_ts=end_ts,
            detections=detections if isinstance(detections, list) else [],
            summary=record.summary,
        )

    def _build_dvr_context(self, question: str, use_heavy: bool) -> Dict[str, Any]:
        if not self.dvr.enabled:
            return {
                "context": "(DVR disabled in config.)",
                "analysis_context": "",
                "window_start": None,
                "window_end": None,
                "window_source": None,
                "segment_count": 0,
            }
        if not self.dvr.available:
            status = self.dvr.status()
            return {
                "context": f"(DVR unavailable: {status.get('message', 'storage unavailable')})",
                "analysis_context": "",
                "window_start": None,
                "window_end": None,
                "window_source": None,
                "segment_count": 0,
            }
        bundle = self.dvr.build_query_context(question)
        analysis_context = ""
        segments = bundle.get("segments") or []
        gemini_analyzed: List[Dict[str, Any]] = []
        if use_heavy and self.clip_metadata.is_available():
            gemini_analyzed = self.dvr.analyze_segments(
                segments,
                analyzer_fn=self.clip_metadata.analyze_video,
            )
            if gemini_analyzed:
                parts: List[str] = []
                for item in gemini_analyzed:
                    payload = item.get("analysis") or {}
                    scene = str(payload.get("scene_summary", "")).strip()
                    actions = payload.get("actions") or []
                    actors = payload.get("actors") or []
                    parts.append(
                        f"- segment#{item.get('segment_id')} [{item.get('start_ts')} -> {item.get('end_ts')}] "
                        f"scene={scene or 'n/a'} actors={actors} actions={actions}"
                    )
                analysis_context = "\n".join(parts)

        vi = self.google.video_intelligence
        try_vi = (
            use_heavy
            and vi.is_available()
            and segments
            and (
                not vi.fallback_only
                or gemini_keyframe_analysis_is_weak(gemini_analyzed)
            )
        )
        if try_vi:
            seg = segments[0]
            path = str(getattr(seg, "path", "") or "")
            if path and os.path.isfile(path):
                try:
                    vi_result = vi.analyze_file(path)
                    scene = str(vi_result.get("scene_summary", "")).strip()
                    if scene:
                        block = (
                            f"- segment#{getattr(seg, 'id', '?')} "
                            f"[{getattr(seg, 'start_ts', '')} -> {getattr(seg, 'end_ts', '')}] "
                            f"video_intelligence={scene}"
                        )
                        analysis_context = (
                            f"{analysis_context}\n{block}".strip()
                            if analysis_context
                            else block
                        )
                except Exception as error:  # noqa: BLE001
                    note = f"(Video Intelligence fallback unavailable: {str(error)[:160]})"
                    analysis_context = (
                        f"{analysis_context}\n{note}".strip()
                        if analysis_context
                        else note
                    )
        return {
            "context": str(bundle.get("context", "")),
            "analysis_context": analysis_context,
            "window_start": bundle.get("window_start"),
            "window_end": bundle.get("window_end"),
            "window_source": bundle.get("window_source"),
            "segment_count": len(segments),
        }

    def _enroll_owner(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        if not name:
            return {"ok": False, "message": "Owner enrollment requires a name."}
        role = str(payload.get("role", "owner_admin")).strip() or "owner_admin"
        appearance = payload.get("appearance_signature") or {}
        profile = {
            "name": name,
            "role": role,
            "appearance_signature": appearance if isinstance(appearance, dict) else {},
            "enrollment_source": "natural_language",
        }
        stored = self.brain.save_owner_profile(profile)
        frame = self._frame_store.get_frame()
        if frame is None:
            return {
                "ok": True,
                "message": "Saved owner profile, but no live frame was available for face enrollment.",
                "profile": stored,
                "face_enrolled": False,
            }
        known_root = str(
            (self._config.get("face_recognition") or {}).get("known_faces_dir", "data/known_faces")
        )
        os.makedirs(known_root, exist_ok=True)
        owner_id = "owner_admin"
        person_dir = os.path.join(known_root, owner_id)
        os.makedirs(person_dir, exist_ok=True)
        image_path = os.path.join(person_dir, f"enroll_{int(time.time())}.jpg")
        try:
            import cv2  # type: ignore

            if not cv2.imwrite(image_path, frame):
                return {
                    "ok": True,
                    "message": "Saved owner profile, but could not write live enrollment frame.",
                    "profile": stored,
                    "face_enrolled": False,
                }
        except Exception as error:  # noqa: BLE001
            return {
                "ok": True,
                "message": f"Saved owner profile, but frame capture failed: {error}",
                "profile": stored,
                "face_enrolled": False,
            }
        try:
            enrolled = self.faces.enroll_face(image_path, owner_id, name)
        except Exception as error:  # noqa: BLE001
            return {
                "ok": True,
                "message": f"Saved owner profile, but face enrollment failed: {error}",
                "profile": stored,
                "face_enrolled": False,
            }
        if enrolled:
            return {
                "ok": True,
                "message": f"Owner profile saved and face enrolled for {name}.",
                "profile": stored,
                "face_enrolled": True,
            }
        return {
            "ok": True,
            "message": "Owner profile saved, but no clear face was found in the live frame.",
            "profile": stored,
            "face_enrolled": False,
        }
