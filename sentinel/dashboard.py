"""Web dashboard and status API for AI Home Sentinel (Phase 2).

This builds a small Flask web application with these pages:

- ``/``         A simple status web page you open in a browser.
- ``/status``   A machine-readable JSON snapshot of system health + camera state.
- ``/video``    A live MJPEG video stream of the latest camera frame (Phase 2).

Phase 1 proved the foundation (config + health). Phase 2 adds the live camera
view: if a :class:`~sentinel.camera.Camera` is passed in, ``/video`` streams
its newest frames and ``camera_active`` in ``/status`` reflects whether the
camera is really producing frames.

Everything is wrapped so that a failure while reading health data (or the
absence of a camera) turns into a clear message instead of crashing the whole
web server. With no camera, the dashboard simply reports it as unavailable.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from flask import Flask, Response, jsonify, render_template

from sentinel import health
from sentinel.camera import Camera
from sentinel.config import Config, load_config
from sentinel.utils import now_iso


def _web_dirs() -> tuple[str, str]:
    """Return the absolute paths to the templates and static folders."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates = os.path.join(root, "web", "templates")
    static = os.path.join(root, "web", "static")
    return templates, static


# MJPEG streams send one JPEG after another, separated by this marker. The
# browser's <img> tag understands this "multipart/x-mixed-replace" format and
# simply swaps in each new picture as it arrives, giving smooth live video.
_BOUNDARY = "frame"


def create_app(config: Config | None = None, camera: Optional[Camera] = None) -> Flask:
    """Create and configure the Flask application.

    Passing ``config`` is optional; if omitted, settings are loaded from
    ``config.yaml``. ``camera`` is also optional: when supplied, the live video
    stream and the real ``camera_active`` state come from it; when omitted (for
    example on a PC with no camera), the dashboard simply shows the camera as
    unavailable. Returning the app from a function like this is a common Flask
    pattern that also makes the app easy to test.
    """
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
        """Gather a full status snapshot, never raising on a single failure."""
        try:
            data = health.get_health(max_cpu_temp_celsius=max_temp)
            data["ok"] = True
            data["error"] = None
        except Exception as error:  # noqa: BLE001 - report instead of crashing
            data = {"ok": False, "error": str(error)}
        data["timestamp"] = now_iso()
        # Phase markers so the page can show what is and isn't active yet.
        data["phase"] = 2
        # camera_active is True only when the camera is really producing frames,
        # so the badge and live view honestly reflect what the hardware is doing.
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
        data["motion_active"] = False
        data["detector_active"] = False
        return data

    def video_frames():
        """Yield the newest JPEG frame over and over for the MJPEG stream.

        It waits for each fresh frame (rather than spinning) and ends the stream
        if the camera stops producing frames, so a dead camera doesn't leave the
        browser hanging forever. The page re-opens the stream when the camera
        comes back.
        """
        if camera is None:
            return

        last_count = -1
        while True:
            result = camera.frame_store.wait_for_next(last_count, timeout=5.0)
            if result is None:
                # Nothing new for a while. If the camera has gone stale, stop;
                # otherwise keep waiting for the next frame.
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
        """Serve the human-friendly status page."""
        return render_template("index.html")

    @app.route("/status")
    def status():  # type: ignore[unused-ignore]
        """Serve the system health snapshot as JSON."""
        return jsonify(build_status())

    @app.route("/video")
    def video():  # type: ignore[unused-ignore]
        """Serve the live camera feed as an MJPEG stream the <img> tag can show."""
        if camera is None or not camera.is_active():
            # 503 = service not available right now; the page shows a friendly
            # "camera unavailable" message based on /status instead.
            return Response("Camera unavailable", status=503, mimetype="text/plain")
        return Response(
            video_frames(),
            mimetype=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
        )

    @app.route("/health")
    def healthcheck():  # type: ignore[unused-ignore]
        """A tiny endpoint that just says the server is up."""
        return jsonify({"ok": True, "timestamp": now_iso()})

    return app


def run_dashboard(config: Config | None = None, camera: Optional[Camera] = None) -> None:
    """Start the web server using the host and port from config.

    This is what ``run.py`` calls. The dashboard binds to the configured host
    (default ``0.0.0.0``, meaning reachable from other devices on your home
    network) and port (default ``5000``). The optional ``camera`` powers the
    live view and the ``camera_active`` status.
    """
    if config is None:
        config = load_config()

    app = create_app(config, camera=camera)
    host = str(config.get("dashboard", "host", default="0.0.0.0"))
    port = int(config.get("dashboard", "port", default=5000))

    print("=" * 60)
    print(" AI Home Sentinel - Phase 2 (Camera & Live Video)")
    print(f" Dashboard starting on http://{host}:{port}")
    if host == "0.0.0.0":
        print(" On this machine open:   http://localhost:5000")
        print(" From another device:    http://<this-device-ip>:5000")
    print(" Press CTRL+C to stop.")
    print("=" * 60)

    # debug=False and use_reloader=False keep it simple and stable for beginners.
    # threaded=True (Flask's default) lets the live video stream run while the
    # /status page keeps responding.
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
