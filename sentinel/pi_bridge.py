"""Bridge local Sentinel runtime data into the Emergent API."""

from __future__ import annotations

import queue
import socket
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from urllib.parse import urlparse

import requests

from sentinel import health
from sentinel.events import EventRecord


def _best_effort_ip() -> str:
    try:
        import subprocess

        ips = subprocess.check_output(["hostname", "-I"], text=True).strip().split()
        for ip in ips:
            if ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172."):
                return ip
        if ips:
            return ips[0]
    except Exception:  # noqa: BLE001
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def _iso_to_dt(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", ""))
    except ValueError:
        return None


class PiBridge:
    """Posts heartbeats/events/DVR metadata to Emergent in the background."""

    def __init__(
        self,
        config: Any,
        ledger: Any,
        dvr: Any,
        camera_active_fn: Optional[Callable[[], bool]] = None,
    ):
        cfg = config.get("pi_bridge") or {}
        self._enabled = bool(cfg.get("enabled", False))
        self._base_url = str(cfg.get("base_url", "http://127.0.0.1:8080")).rstrip("/")
        self._heartbeat_seconds = max(5, int(cfg.get("heartbeat_seconds", 10)))
        self._camera_sync_seconds = max(15, int(cfg.get("camera_sync_seconds", 60)))
        self._post_events = bool(cfg.get("post_events", True))
        self._post_dvr = bool(cfg.get("post_dvr_segments", True))
        self._timeout = max(1.0, float(cfg.get("request_timeout_seconds", 3)))
        self._camera_id = str(cfg.get("camera_id", "cam-main"))
        self._camera_name = str(cfg.get("camera_name", "Main Camera"))
        self._stream_url = str(cfg.get("stream_url", "")).strip()
        self._dashboard_port = int(config.get("dashboard", "port", default=5000))
        self._camera_active_fn = camera_active_fn

        self._ledger = ledger
        self._dvr = dvr
        self._session = requests.Session()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._events: queue.Queue[EventRecord] = queue.Queue(maxsize=256)
        self._last_error = ""
        self._last_event_id = 0
        self._last_segment_id = 0
        self._events_sent = 0
        self._segments_sent = 0
        self._heartbeats_sent = 0
        self._last_heartbeat_at = ""
        self._consecutive_failures = 0

    def start(self) -> None:
        if not self._enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        # Start forwarding only new records to avoid flooding historical data.
        recent_events = self._ledger.list_recent(limit=1)
        if recent_events:
            self._last_event_id = int(recent_events[0].id)
        if getattr(self._dvr, "index", None) is not None:
            recent_segments = self._dvr.index.list_recent(limit=1)
            if recent_segments:
                self._last_segment_id = int(recent_segments[0].id)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pi-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def on_event(self, record: EventRecord) -> None:
        if not self._enabled or not self._post_events:
            return
        if int(record.id) <= self._last_event_id:
            return
        try:
            self._events.put_nowait(record)
        except queue.Full:
            self._last_error = "event queue full; dropping event"

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "base_url": self._base_url,
            "post_events": self._post_events,
            "post_dvr_segments": self._post_dvr,
            "active": bool(self._thread and self._thread.is_alive()),
            "last_error": self._last_error or None,
            "events_sent": self._events_sent,
            "segments_sent": self._segments_sent,
            "heartbeats_sent": self._heartbeats_sent,
            "last_heartbeat_at": self._last_heartbeat_at or None,
        }

    def _run(self) -> None:
        next_heartbeat = 0.0
        next_camera_sync = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            # Slow down when Emergent is unreachable so we do not hammer a dead port.
            failure_scale = min(6, max(0, self._consecutive_failures))
            heartbeat_gap = self._heartbeat_seconds * (1 + failure_scale)
            camera_gap = self._camera_sync_seconds * (1 + failure_scale)
            if now >= next_heartbeat:
                self._send_heartbeat()
                next_heartbeat = now + heartbeat_gap
            if now >= next_camera_sync:
                self._sync_camera()
                next_camera_sync = now + camera_gap
            self._drain_events(max_items=6)
            self._sync_dvr_segments()
            self._stop.wait(0.5)

    def _camera_is_online(self) -> bool:
        if self._camera_active_fn is None:
            return False
        try:
            return bool(self._camera_active_fn())
        except Exception:  # noqa: BLE001
            return False

    def _post(self, path: str, payload: Dict[str, Any]) -> bool:
        try:
            resp = self._session.post(
                f"{self._base_url}{path}",
                json=payload,
                timeout=self._timeout,
            )
            if 200 <= resp.status_code < 300:
                self._consecutive_failures = 0
                return True
            self._consecutive_failures += 1
            self._last_error = f"{path} failed: {resp.status_code}"
            return False
        except Exception as error:  # noqa: BLE001
            self._consecutive_failures += 1
            self._last_error = f"{path} error: {str(error)[:160]}"
            return False

    def _send_heartbeat(self) -> None:
        snapshot = health.get_health()
        cameras_online = 1 if self._camera_is_online() else 0
        payload = {
            "cpu_percent": float(snapshot.get("cpu_percent") or 0.0),
            "temp_c": float((snapshot.get("temperature") or {}).get("celsius") or 0.0),
            "memory_percent": float((snapshot.get("memory") or {}).get("percent") or 0.0),
            "disk_percent": float((snapshot.get("disk") or {}).get("percent") or 0.0),
            "uptime_seconds": int(time.monotonic()),
            "cameras_online": cameras_online,
        }
        if self._post("/api/pi/heartbeat", payload):
            self._heartbeats_sent += 1
            self._last_heartbeat_at = datetime.utcnow().isoformat()

    def _public_emergent_base(self) -> str:
        parsed = urlparse(self._base_url)
        port = parsed.port or 8080
        if parsed.hostname in ("127.0.0.1", "localhost"):
            return f"http://{_best_effort_ip()}:{port}"
        return self._base_url.rstrip("/")

    def _sync_camera(self) -> None:
        live_feed = f"{self._public_emergent_base()}/api/pi/live-feed"
        online = self._camera_is_online()
        payload = {
            "id": self._camera_id,
            "name": self._camera_name,
            "location": "Home",
            "status": "online" if online else "offline",
            "stream_url": live_feed if online else "",
            # Emergent dashboard tiles render thumbnail_url only (not stream_url).
            "thumbnail_url": live_feed if online else "",
        }
        self._post("/api/pi/cameras", payload)

    def _drain_events(self, max_items: int = 6) -> None:
        for _ in range(max_items):
            try:
                record = self._events.get_nowait()
            except queue.Empty:
                return
            payload = self._event_payload(record)
            if self._post("/api/pi/events", payload):
                self._events_sent += 1
                self._last_event_id = max(self._last_event_id, int(record.id))
            self._events.task_done()

    def _event_payload(self, record: EventRecord) -> Dict[str, Any]:
        entities = record.entities or {}
        detections = entities.get("detections") or []
        labels = sorted(
            {
                str(item.get("label", "")).lower().strip()
                for item in detections
                if str(item.get("label", "")).strip()
            }
        )
        is_person = "person" in labels or record.source == "face"
        event_type = "person" if is_person else "object"
        if record.source == "motion":
            event_type = "motion"
        if str(entities.get("event_type", "")).strip() == "unknown_person_detected":
            event_type = "unknown_person"
        face_info = entities.get("face") or {}
        known_identity = str(entities.get("known_identity") or "").strip()
        name = known_identity or str(face_info.get("name") or "").strip() or None
        known = bool(name) and name != "unknown" and event_type != "unknown_person"
        confidence = 0.0
        for item in detections:
            confidence = max(confidence, float(item.get("confidence", 0.0)))
        if confidence <= 0.0:
            confidence = min(1.0, max(0.1, record.importance / 10.0))
        importance_label = "low"
        if record.tier >= 2:
            importance_label = "high"
        elif record.importance >= 4:
            importance_label = "medium"
        tags = [f"tier-{record.tier}", record.source]
        if event_type == "unknown_person":
            tags.append("unknown")
        clip_url = None
        if record.clip_path:
            clip_url = f"http://{_best_effort_ip()}:{self._dashboard_port}/api/clips/{record.id}"
        return {
            "camera_id": self._camera_id,
            "camera_name": self._camera_name,
            "type": event_type,
            "person_id": None,
            "person_name": name,
            "known": known,
            "objects": labels,
            "confidence": round(confidence, 3),
            "importance": importance_label,
            "ai_summary": record.summary,
            "ai_interpretation": "",
            "tags": tags,
            "thumbnail_url": None,
            "clip_url": clip_url,
            "saved": bool(record.tier >= 2 or record.clip_path),
            "timestamp": record.timestamp,
        }

    def _sync_dvr_segments(self) -> None:
        if not self._post_dvr:
            return
        if not getattr(self._dvr, "enabled", False):
            return
        if not getattr(self._dvr, "index", None):
            return
        recent = self._dvr.index.list_recent(limit=30)
        for seg in sorted(recent, key=lambda item: int(item.id)):
            if int(seg.id) <= self._last_segment_id:
                continue
            payload = self._segment_payload(seg)
            if self._post("/api/pi/dvr/segment", payload):
                self._segments_sent += 1
                self._last_segment_id = int(seg.id)

    def _segment_payload(self, seg: Any) -> Dict[str, Any]:
        start = _iso_to_dt(str(seg.start_ts))
        end = _iso_to_dt(str(seg.end_ts))
        if start is None or end is None or end <= start:
            length_minutes = 0
        else:
            length_minutes = int(max(1, round((end - start).total_seconds() / 60.0)))
        people = ["person"] if int(getattr(seg, "person_count", 0)) > 0 else []
        objects = [str(v) for v in list(getattr(seg, "object_labels", []) or []) if str(v).strip()]
        return {
            "camera_id": self._camera_id,
            "camera_name": self._camera_name,
            "start_time": str(seg.start_ts),
            "end_time": str(seg.end_ts),
            "length_minutes": length_minutes,
            "ai_summary": str(getattr(seg, "summary", "") or ""),
            "tags": objects[:6],
            "people": people,
            "objects": objects[:6],
            "linked_event_ids": [],
            "thumbnail_url": None,
        }
