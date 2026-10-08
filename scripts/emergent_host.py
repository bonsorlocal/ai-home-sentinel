"""Single-process host for Emergent API + React SPA.

Runs the upstream FastAPI backend from the exported Emergent project and mounts
the built React app at `/` so users only need one port on the Pi.
"""

from __future__ import annotations

import os
import sys

import httpx
import uvicorn
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

SENTINEL_VIDEO_URL = os.environ.get("SENTINEL_VIDEO_URL", "http://127.0.0.1:5000/video")


def _build_app():
    app_dir = os.path.abspath(os.environ.get("EMERGENT_APP_DIR", "/home/sentinel/emergent-app"))
    backend_dir = os.path.join(app_dir, "backend")
    frontend_build = os.path.join(app_dir, "frontend", "build")
    stubs_dir = os.path.join(app_dir, "scripts", "emergent_stubs")

    if stubs_dir not in sys.path and os.path.isdir(stubs_dir):
        sys.path.insert(0, stubs_dir)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    # Imported from the Emergent web-app backend.
    from server import app as backend_app  # type: ignore

    @backend_app.get("/api/pi/live-feed")
    def pi_live_feed():  # type: ignore[unused-ignore]
        """Same-origin MJPEG proxy so the Emergent UI can render Pi camera tiles."""

        def iter_stream():
            with httpx.Client(timeout=None) as client:
                with client.stream("GET", SENTINEL_VIDEO_URL) as resp:
                    resp.raise_for_status()
                    for chunk in resp.iter_bytes():
                        yield chunk

        return StreamingResponse(
            iter_stream(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    if os.path.isfile(os.path.join(frontend_build, "index.html")):
        backend_app.mount("/", StaticFiles(directory=frontend_build, html=True), name="spa")
    else:
        raise RuntimeError(f"Missing React build output: {frontend_build}")
    return backend_app


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    app = _build_app()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
