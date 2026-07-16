"""Web dashboard and status API for AI Home Sentinel.

Pages and endpoints:

- ``/``         Status web page with live view and system health.
- ``/events``   Event log page (Phase 3).
- ``/dvr``      DVR timeline (Phase 9A).
- ``/status``   JSON snapshot of system health + module state.
- ``/api/events`` JSON list of recent Event Ledger records.
- ``/api/profile/*`` Owner/resident profile APIs (Phase 9C–10).
- ``/video``    Live MJPEG video stream (Phase 2).
- ``/api/live.jpg`` Fresh single JPEG for low-latency polling (preferred live view).

Canonical phase labels: docs/ROADMAP_PHASES.md
"""

from __future__ import annotations

import os
import time
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
    try:
        residents = runtime.brain.list_resident_profiles(limit=1)
        if residents:
            return 10
        if runtime.brain.is_available() and runtime.dvr.enabled:
            return 9
        if runtime.clip_metadata.is_available():
            return 8
        if runtime.reasoner.status().get("enabled", False):
            return 7
    except Exception:
        pass
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
    emergent_cfg = config.get("emergent_app") or {}
    emergent_enabled = bool(emergent_cfg.get("enabled", False))
    emergent_url = str(emergent_cfg.get("url", "") or "").strip()
    emergent_label = str(emergent_cfg.get("label", "Emergent App") or "Emergent App")
    require_token = bool(config.get("dashboard", "require_token", default=False))
    dashboard_token = str(config.get("dashboard", "token", default="") or "")
    if not dashboard_token:
        # Prefer secrets.yaml so the token is never committed in config.yaml.
        secrets_file = str(
            config.get("dashboard", "secrets_file", default="secrets.yaml") or "secrets.yaml"
        )
        try:
            import yaml as _yaml

            with open(secrets_file, encoding="utf-8") as handle:
                secret_data = _yaml.safe_load(handle) or {}
            if isinstance(secret_data, dict):
                dashboard_token = str(
                    secret_data.get("dashboard_token")
                    or secret_data.get("DASHBOARD_TOKEN")
                    or ""
                ).strip()
        except (OSError, ValueError, TypeError):
            dashboard_token = ""
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

    def _owner_gate(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if runtime is None:
            return {"ok": False, "message": "Runtime unavailable."}
        owner = runtime.brain.owner_profile_status().get("profile") or {}
        owner_name = str(owner.get("name", "")).strip().lower()
        actor_name = str(payload.get("actor_name", "")).strip().lower()
        actor_role = str(payload.get("actor_role", "")).strip().lower()
        if actor_role != "owner_admin":
            return {"ok": False, "message": "Only owner_admin can perform this action."}
        if owner_name and actor_name and actor_name != owner_name:
            return {"ok": False, "message": "Actor does not match enrolled owner profile."}
        return None

    @app.context_processor
    def _inject_emergent_nav():  # type: ignore[unused-ignore]
        if emergent_enabled and emergent_url:
            return {
                "emergent_app_url": emergent_url,
                "emergent_app_label": emergent_label,
            }
        return {}

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
        tts_provider = str(voice_cfg.get("tts_provider", "auto")).strip().lower() or "auto"
        google_tts_ok = False
        if runtime is not None:
            google_tts_ok = runtime.google.text_to_speech.is_available()
        if tts_provider == "auto":
            tts_provider = "google" if google_tts_ok else "browser"
        data["voice"] = {
            "enabled": bool(voice_cfg.get("enabled", True)),
            "speak_text_queries": bool(voice_cfg.get("speak_text_queries", False)),
            "wake_word_enabled": bool(voice_cfg.get("wake_word_enabled", False)),
            "wake_word": str(voice_cfg.get("wake_word", "hey sentinel")),
            "language": str(voice_cfg.get("language", "en-US")),
            "tts_provider": tts_provider,
            "google_tts_available": google_tts_ok,
        }

        return data

    def video_frames():
        """Yield MJPEG parts over one TCP connection (best over Wi-Fi).

        Always send the freshest JPEG. Never try to catch up on old frames —
        that is what caused multi-second lag. Let TCP backpressure pace us.
        """
        if camera is None:
            return

        last_count = -1
        while True:
            result = camera.frame_store.wait_for_next(last_count, timeout=5.0)
            if result is None:
                if not camera.is_active():
                    return
                continue
            snap = camera.frame_store.get_stream()
            if snap is None:
                continue
            jpeg, last_count = snap
            yield (
                b"--" + _BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                + jpeg + b"\r\n"
            )
            # If the client is slow (Wi-Fi), skip everything that piled up
            # while this frame was being written.
            snap = camera.frame_store.get_stream()
            if snap is not None:
                _jpeg2, last_count = snap

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
        path = runtime.dvr.get_playback_path(segment_id)
        if not path:
            return Response("Segment unavailable", status=404, mimetype="text/plain")
        path = os.path.abspath(path)
        root = os.path.abspath(runtime.dvr.storage_root)
        if not path.startswith(root):
            return Response("Segment unavailable", status=403, mimetype="text/plain")
        if not os.path.exists(path):
            return Response("Segment file missing", status=404, mimetype="text/plain")
        response = send_file(path, mimetype="video/mp4", conditional=True)
        response.headers["Accept-Ranges"] = "bytes"
        return response

    @app.route("/api/dvr/settings", methods=["GET", "POST"])
    def api_dvr_settings():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"enabled": False, "available": False})
        if request.method == "GET":
            return jsonify(runtime.dvr.get_settings())
        body = request.get_json(silent=True) or {}
        try:
            minutes = int(body.get("segment_minutes"))
        except (TypeError, ValueError):
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "segment_minutes is required (15, 30, 45, or 60).",
                    }
                ),
                400,
            )
        result = runtime.dvr.set_segment_minutes(minutes)
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)

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

    @app.route("/api/chat/feedback", methods=["POST"])
    @app.route("/api/feedback", methods=["POST"])
    def api_feedback():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify(
                {
                    "ok": False,
                    "message": "The brain is not available on this dashboard.",
                }
            )
        payload = request.get_json(silent=True) or {}
        return jsonify(runtime.brain.capture_feedback(payload))

    @app.route("/api/voice/tts", methods=["POST"])
    def api_voice_tts():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"ok": False, "message": "Runtime unavailable."}), 503
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"ok": False, "message": "text is required."}), 400
        tts = runtime.google.text_to_speech
        if not tts.is_available():
            return jsonify({"ok": False, "message": "Google TTS is not configured."}), 503
        try:
            audio = tts.synthesize(text)
        except Exception as error:  # noqa: BLE001
            return jsonify({"ok": False, "message": str(error)[:240]}), 502
        return Response(audio, mimetype="audio/mpeg")

    @app.route("/api/chat/memory/status")
    def api_chat_memory_status():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify(
                {
                    "enabled": False,
                    "entry_count": 0,
                    "max_entries": 0,
                    "preferences": [],
                }
            )
        return jsonify(runtime.brain.memory_status())

    @app.route("/api/profile/owner")
    def api_owner_profile_status():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"ok": False, "configured": False, "profile": None})
        return jsonify(runtime.brain.owner_profile_status())

    @app.route("/api/profile/enroll-owner", methods=["POST"])
    def api_enroll_owner():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"ok": False, "message": "Runtime is unavailable."})
        payload = request.get_json(silent=True) or {}
        result = runtime.brain.enroll_owner_from_payload(payload)
        if result.get("ok"):
            runtime.ledger.insert(
                source="system",
                title="Owner profile updated",
                summary=str(result.get("message", "Owner enrollment completed.")),
                entities={"event_type": "owner_profile_update"},
            )
        return jsonify(result)

    @app.route("/api/profile/residents")
    def api_list_residents():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"ok": False, "residents": []})
        return jsonify({"ok": True, "residents": runtime.brain.list_resident_profiles(limit=100)})

    @app.route("/api/profile/resident", methods=["POST"])
    def api_upsert_resident():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"ok": False, "message": "Runtime is unavailable."})
        payload = request.get_json(silent=True) or {}
        blocked = _owner_gate(payload)
        if blocked is not None:
            return jsonify(blocked), 403
        resident_id = str(payload.get("resident_id", "")).strip().lower()
        if not resident_id:
            return jsonify({"ok": False, "message": "resident_id is required."}), 400
        household_cfg = config.get("household", default={}) or {}
        if not bool(household_cfg.get("enabled", True)):
            return jsonify({"ok": False, "message": "Household profiles are disabled."}), 403
        max_residents = int(household_cfg.get("max_residents", 8) or 8)
        existing = runtime.brain.list_resident_profiles(limit=max_residents + 1)
        is_new = not any(r.get("resident_id") == resident_id for r in existing)
        if is_new and len(existing) >= max_residents:
            return jsonify(
                {"ok": False, "message": f"Resident limit reached ({max_residents})."}
            ), 400
        profile = {
            "name": str(payload.get("name", "")).strip() or resident_id,
            "role": str(payload.get("role", "resident")).strip() or "resident",
            "appearance_signature": payload.get("appearance_signature") or {},
            "routines": payload.get("routines") or {},
            "preferences": payload.get("preferences") or {},
            "personality_tone": payload.get("personality_tone") or {},
        }
        saved = runtime.brain.save_resident_profile(resident_id, profile)
        runtime.ledger.insert(
            source="system",
            title="Resident profile updated",
            summary=f"Resident profile saved for {resident_id}.",
            entities={"event_type": "resident_profile_update", "resident_id": resident_id},
        )
        return jsonify({"ok": True, "resident": saved})

    @app.route("/api/profile/preferences", methods=["POST"])
    def api_set_owner_preference():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"ok": False, "message": "Runtime is unavailable."})
        payload = request.get_json(silent=True) or {}
        blocked = _owner_gate(payload)
        if blocked is not None:
            return jsonify(blocked), 403
        key = str(payload.get("key", "")).strip()
        if not key:
            return jsonify({"ok": False, "message": "key is required."}), 400
        updated = runtime.brain.set_owner_preference(key, payload.get("value"))
        if not updated:
            return jsonify({"ok": False, "message": "Owner profile is not enrolled yet."}), 400
        runtime.ledger.insert(
            source="system",
            title="Owner preference updated",
            summary=f"Owner preference '{key}' updated.",
            entities={"event_type": "owner_preference_update", "key": key},
        )
        return jsonify({"ok": True, "profile": updated})

    @app.route("/api/live.jpg")
    def live_jpeg():  # type: ignore[unused-ignore]
        """Freshest JPEG; optional ``since`` long-polls until a newer frame exists."""
        if camera is None or not camera.is_active():
            return Response("Camera unavailable", status=503, mimetype="text/plain")
        try:
            since = int(request.args.get("since", "-1"))
        except (TypeError, ValueError):
            since = -1
        # Wait briefly for a *new* frame so the browser can pace itself to
        # real capture rate instead of re-downloading the same JPEG.
        result = camera.frame_store.wait_for_next_stream(since, timeout=1.0)
        if result is None:
            result = camera.frame_store.get_stream()
        if result is None:
            return Response("No frame yet", status=503, mimetype="text/plain")
        jpeg, count = result
        return Response(
            jpeg,
            mimetype="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-Accel-Buffering": "no",
                "X-Stream-Count": str(count),
            },
        )

    @app.route("/video")
    def video():  # type: ignore[unused-ignore]
        if camera is None or not camera.is_active():
            return Response("Camera unavailable", status=503, mimetype="text/plain")
        return Response(
            video_frames(),
            mimetype=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "X-Accel-Buffering": "no",
            },
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
        7: "Reasoner",
        8: "Clip Metadata",
        9: "DVR + Brain + Profiles",
        10: "Household Expansion",
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
