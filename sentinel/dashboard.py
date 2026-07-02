"""Web dashboard and status API for AI Home Sentinel (Phases 3–5).

Pages and endpoints:

- ``/``         Status web page with live view and system health.
- ``/events``   Event log page (Phase 3).
- ``/status``   JSON snapshot of system health + module state.
- ``/api/events`` JSON list of recent Event Ledger records.
- ``/video``    Live MJPEG video stream (Phase 2).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, TYPE_CHECKING

from flask import Flask, Response, jsonify, render_template, request, abort, send_file, g

from sentinel import health
from sentinel.camera import Camera
from sentinel.config import Config, load_config
from sentinel.utils import now_iso

if TYPE_CHECKING:
    from sentinel.runtime import SentinelRuntime


def _web_dirs() -> tuple[str, str]:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates = os.path.join(root, "web", "templates")
    static = os.path.join(root, "web", "static")
    return templates, static


def _current_phase(runtime: Optional["SentinelRuntime"]) -> int:
    if runtime is None:
        return 2
    if runtime.faces.is_active():
        return 5
    if runtime.detector.is_active():
        return 4
    if runtime.motion.is_running():
        return 3
    return 2


_BOUNDARY = "frame"


def _event_has_person(entities: Dict[str, Any]) -> bool:
    detections = entities.get("detections") or []
    for item in detections:
        if str(item.get("label", "")).lower() == "person":
            return True
    faces = entities.get("faces") or []
    return len(faces) > 0


def _is_incident_record(
    record: Any,
    *,
    min_session_seconds: float,
) -> bool:
    if not record.clip_path:
        return False
    entities = record.entities or {}
    if _event_has_person(entities):
        return True
    session = entities.get("session") or {}
    duration = session.get("duration_seconds")
    if duration is not None and float(duration) >= min_session_seconds:
        return True
    return False


def _serialize_event(record: Any) -> Dict[str, Any]:
    item = record.to_dict()
    entities = item.get("entities") or {}
    session = entities.get("session") or {}
    clip_analysis = entities.get("clip_analysis") or {}
    detections = entities.get("detections") or []
    faces = entities.get("faces") or []
    actors = clip_analysis.get("actors")
    if not isinstance(actors, list) or not actors:
        actors = sorted(
            {
                str(d.get("label", "")).lower()
                for d in detections
                if str(d.get("label", "")).strip()
            }
            | {
                str(f.get("name", "")).strip()
                for f in faces
                if str(f.get("name", "")).strip()
            }
        )
    actions = clip_analysis.get("actions")
    if not isinstance(actions, list):
        actions = []
    item["session_duration_seconds"] = session.get("duration_seconds")
    item["session_started_at"] = session.get("started_at")
    item["session_ended_at"] = session.get("ended_at")
    item["session_peak_area"] = session.get("peak_area")
    item["incident_id"] = str(session.get("session_id") or f"event-{record.id}")
    lifecycle = session.get("lifecycle") or []
    item["lifecycle"] = lifecycle if isinstance(lifecycle, list) else []
    item["repeat_count"] = int((entities.get("repeat_count") or 1))
    item["actors"] = actors[:6]
    item["actions"] = [str(a) for a in actions[:6]]
    item["clip_status"] = str(entities.get("clip_status", "unknown"))
    item["clip_summary"] = str(clip_analysis.get("scene_summary", "")).strip()
    item["clip_expected"] = bool(entities.get("clip_expected", False))
    item["clip_note"] = str(entities.get("clip_note", "")).strip()
    item["primary_actor"] = (
        item["actors"][0] if item["actors"] else str(item.get("source", "motion"))
    )
    item["clip_url"] = f"/api/clips/{record.id}" if record.clip_path else None
    return item


def create_app(
    config: Config | None = None,
    camera: Optional[Camera] = None,
    runtime: Optional["SentinelRuntime"] = None,
) -> Flask:
    if config is None:
        config = load_config()

    templates_dir, static_dir = _web_dirs()
    app = Flask(
        __name__,
        template_folder=templates_dir,
        static_folder=static_dir,
    )

    max_temp = float(config.get("performance", "max_cpu_temp_celsius", default=75))
    require_token = bool(config.get("dashboard", "require_token", default=False))
    dashboard_token = str(config.get("dashboard", "token", default="") or "")
    token_cookie_name = "sentinel_dashboard_token"
    token_cookie_max_age = int(
        config.get("dashboard", "token_cookie_max_age_seconds", default=12 * 60 * 60)
    )

    def _token_ok() -> bool:
        if not require_token:
            return True
        if not dashboard_token:
            return False
        supplied = request.args.get("token") or request.headers.get("X-Sentinel-Token")
        if not supplied:
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
        if not supplied:
            supplied = request.cookies.get(token_cookie_name)
        if supplied == dashboard_token and request.args.get("token") == dashboard_token:
            setattr(g, "_set_dashboard_token_cookie", True)
        return supplied == dashboard_token

    @app.before_request
    def _check_dashboard_token():  # type: ignore[unused-ignore]
        if request.endpoint == "healthcheck":
            return None
        if not require_token:
            return None
        if _token_ok():
            return None
        if request.path.startswith("/api/") or request.path in ("/status",):
            abort(401)
        abort(401)

    @app.after_request
    def _persist_dashboard_token_cookie(response: Response):  # type: ignore[unused-ignore]
        if require_token and bool(getattr(g, "_set_dashboard_token_cookie", False)):
            response.set_cookie(
                token_cookie_name,
                dashboard_token,
                max_age=token_cookie_max_age,
                httponly=True,
                samesite="Lax",
                secure=bool(request.is_secure),
            )
        return response

    def build_status() -> Dict[str, Any]:
        try:
            data = health.get_health(max_cpu_temp_celsius=max_temp)
            data["ok"] = True
            data["error"] = None
        except Exception as error:  # noqa: BLE001
            data = {"ok": False, "error": str(error)}
        data["timestamp"] = now_iso()
        data["phase"] = _current_phase(runtime)

        if camera is not None:
            data["camera_active"] = camera.is_active()
            data["camera"] = camera.status()
        else:
            data["camera_active"] = False
            data["camera"] = {
                "running": False,
                "active": False,
                "backend": None,
                "message": "No camera is attached to this dashboard.",
            }

        if runtime is not None:
            data["motion_active"] = runtime.motion.is_active()
            data["detector_active"] = runtime.detector.is_active()
            data["face_recognition_active"] = runtime.faces.is_active()
            data["brain_active"] = runtime.brain.is_available()
            data["brain"] = runtime.brain.status()
            data["runtime"] = runtime.status()
            data["event_count"] = runtime.ledger.count()
            data["dvr"] = runtime.dvr.status()
        else:
            data["motion_active"] = False
            data["detector_active"] = False
            data["face_recognition_active"] = False
            data["brain_active"] = False
            data["brain"] = {"available": False, "enabled": False}
            data["event_count"] = 0
            data["dvr"] = {"enabled": False, "available": False, "active": False}

        voice_cfg = config.get("voice") or {}
        data["voice"] = {
            "enabled": bool(voice_cfg.get("enabled", True)),
            "speak_text_queries": bool(voice_cfg.get("speak_text_queries", False)),
            "wake_word_enabled": bool(voice_cfg.get("wake_word_enabled", False)),
            "wake_word": str(voice_cfg.get("wake_word", "hey sentinel")),
            "language": str(voice_cfg.get("language", "en-US")),
        }

        return data

    def video_frames():
        if camera is None:
            return

        last_count = -1
        while True:
            result = camera.frame_store.wait_for_next(last_count, timeout=5.0)
            if result is None:
                if not camera.is_active():
                    return
                continue
            jpeg, last_count = result
            yield (
                b"--" + _BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            )

    @app.route("/")
    def index():  # type: ignore[unused-ignore]
        return render_template("index.html")

    @app.route("/events")
    def events_page():  # type: ignore[unused-ignore]
        return render_template("events.html")

    @app.route("/dvr")
    def dvr_page():  # type: ignore[unused-ignore]
        return render_template("dvr.html")

    @app.route("/status")
    def status():  # type: ignore[unused-ignore]
        return jsonify(build_status())

    @app.route("/api/events")
    def api_events():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"events": [], "count": 0})
        try:
            limit = max(1, min(200, int(request.args.get("limit", 50))))
        except (TypeError, ValueError):
            limit = 50
        filter_mode = str(request.args.get("filter", "all") or "all").lower()
        clips_cfg = config.get("clips") or {}
        min_session_seconds = float(clips_cfg.get("min_session_seconds_for_clip", 8))

        fetch_limit = limit * 4 if filter_mode == "incidents" else limit
        records = runtime.ledger.list_recent(limit=fetch_limit)
        events = []
        for record in records:
            if filter_mode == "incidents" and not _is_incident_record(
                record, min_session_seconds=min_session_seconds
            ):
                continue
            events.append(_serialize_event(record))
            if len(events) >= limit:
                break
        return jsonify(
            {
                "events": events,
                "count": runtime.ledger.count(),
                "filter": filter_mode,
            }
        )

    @app.route("/api/clips/<int:event_id>")
    def api_clip(event_id: int):  # type: ignore[unused-ignore]
        if runtime is None:
            return Response("Clip unavailable", status=404, mimetype="text/plain")
        record = runtime.ledger.get_by_id(event_id)
        if record is None or not record.clip_path:
            return Response("Clip unavailable", status=404, mimetype="text/plain")
        path = os.path.abspath(str(record.clip_path))
        clip_root = os.path.abspath(runtime.clip_storage.clip_dir)
        if not path.startswith(clip_root):
            return Response("Clip unavailable", status=403, mimetype="text/plain")
        if not os.path.exists(path):
            return Response("Clip file missing", status=404, mimetype="text/plain")
        response = send_file(path, mimetype="video/mp4", conditional=True)
        response.headers["Accept-Ranges"] = "bytes"
        return response

    @app.route("/api/dvr/status")
    def api_dvr_status():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"enabled": False, "available": False, "active": False})
        return jsonify(runtime.dvr.status())

    @app.route("/api/dvr/range")
    def api_dvr_range():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"segments": [], "count": 0})
        end_raw = str(request.args.get("end", "")).strip()
        start_raw = str(request.args.get("start", "")).strip()
        try:
            limit = max(1, min(1000, int(request.args.get("limit", 200))))
        except (TypeError, ValueError):
            limit = 200

        if end_raw:
            try:
                end_dt = datetime.fromisoformat(end_raw.replace("Z", ""))
            except ValueError:
                end_dt = datetime.utcnow()
        else:
            end_dt = datetime.utcnow()
        if start_raw:
            try:
                start_dt = datetime.fromisoformat(start_raw.replace("Z", ""))
            except ValueError:
                start_dt = end_dt - timedelta(hours=1)
        else:
            start_dt = end_dt - timedelta(hours=1)
        if start_dt > end_dt:
            start_dt, end_dt = end_dt, start_dt
        segments = runtime.dvr.list_range(start_dt.isoformat(), end_dt.isoformat(), limit=limit)
        return jsonify(
            {
                "segments": segments,
                "count": len(segments),
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
            }
        )

    @app.route("/api/dvr/segment/<int:segment_id>")
    def api_dvr_segment(segment_id: int):  # type: ignore[unused-ignore]
        if runtime is None:
            return Response("DVR unavailable", status=404, mimetype="text/plain")
        seg = runtime.dvr.get_segment(segment_id)
        if seg is None:
            return Response("Segment unavailable", status=404, mimetype="text/plain")
        path = os.path.abspath(seg.path)
        root = os.path.abspath(runtime.dvr.storage_root)
        if not path.startswith(root):
            return Response("Segment unavailable", status=403, mimetype="text/plain")
        if not os.path.exists(path):
            return Response("Segment file missing", status=404, mimetype="text/plain")
        response = send_file(path, mimetype="video/mp4", conditional=True)
        response.headers["Accept-Ranges"] = "bytes"
        return response

    @app.route("/api/chat", methods=["POST"])
    def api_chat():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify(
                {
                    "ok": False,
                    "answer": "The brain is not available on this dashboard.",
                    "offline": True,
                }
            )
        payload = request.get_json(silent=True) or {}
        question = str(payload.get("question", ""))
        return jsonify(runtime.brain.ask(question))

    @app.route("/api/summary", methods=["POST"])
    def api_summary():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify(
                {
                    "ok": False,
                    "answer": "The brain is not available on this dashboard.",
                    "offline": True,
                }
            )
        return jsonify(runtime.brain.summarize_day())

    @app.route("/video")
    def video():  # type: ignore[unused-ignore]
        if camera is None or not camera.is_active():
            return Response("Camera unavailable", status=503, mimetype="text/plain")
        return Response(
            video_frames(),
            mimetype=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
        )

    @app.route("/health")
    def healthcheck():  # type: ignore[unused-ignore]
        return jsonify({"ok": True, "timestamp": now_iso()})

    return app


def run_dashboard(
    config: Config | None = None,
    camera: Optional[Camera] = None,
    runtime: Optional["SentinelRuntime"] = None,
) -> None:
    if config is None:
        config = load_config()

    app = create_app(config, camera=camera, runtime=runtime)
    host = str(config.get("dashboard", "host", default="0.0.0.0"))
    port = int(config.get("dashboard", "port", default=5000))
    phase = _current_phase(runtime)

    phase_names = {
        2: "Camera & Live Video",
        3: "Motion & Event Ledger",
        4: "Object Detection",
        5: "Face Recognition",
    }
    phase_label = phase_names.get(phase, f"Phase {phase}")

    print("=" * 60)
    print(f" AI Home Sentinel - Phase {phase} ({phase_label})")
    print(f" Dashboard starting on http://{host}:{port}")
    if host == "0.0.0.0":
        print(" On this machine open:   http://localhost:5000")
        print(" From another device:    http://<this-device-ip>:5000")
    print(" Event log:              http://localhost:5000/events")
    print(" DVR timeline:           http://localhost:5000/dvr")
    print(" Press CTRL+C to stop.")
    print("=" * 60)

    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
