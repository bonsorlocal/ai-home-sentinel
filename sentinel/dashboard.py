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
from typing import Any, Dict, Optional, TYPE_CHECKING

from flask import Flask, Response, jsonify, render_template, request

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
            data["motion_active"] = runtime.motion.is_running()
            data["detector_active"] = runtime.detector.is_active()
            data["face_recognition_active"] = runtime.faces.is_active()
            data["brain_active"] = runtime.brain.is_available()
            data["brain"] = runtime.brain.status()
            data["runtime"] = runtime.status()
            data["event_count"] = runtime.ledger.count()
        else:
            data["motion_active"] = False
            data["detector_active"] = False
            data["face_recognition_active"] = False
            data["brain_active"] = False
            data["brain"] = {"available": False, "enabled": False}
            data["event_count"] = 0

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

    @app.route("/status")
    def status():  # type: ignore[unused-ignore]
        return jsonify(build_status())

    @app.route("/api/events")
    def api_events():  # type: ignore[unused-ignore]
        if runtime is None:
            return jsonify({"events": [], "count": 0})
        limit = 50
        records = runtime.ledger.list_recent(limit=limit)
        return jsonify(
            {
                "events": [r.to_dict() for r in records],
                "count": runtime.ledger.count(),
            }
        )

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
    print(" Press CTRL+C to stop.")
    print("=" * 60)

    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
