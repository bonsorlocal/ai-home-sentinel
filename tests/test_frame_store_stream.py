"""Frame store + live JPEG endpoint latency helpers."""

from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.config import load_config  # noqa: E402
from sentinel.dashboard import create_app  # noqa: E402
from sentinel.frame_store import FrameStore  # noqa: E402


def test_get_stream_returns_latest_jpeg_and_count():
    store = FrameStore()
    assert store.get_stream() is None
    store.update(frame=object(), stream_jpeg=b"jpeg-1")
    store.update(frame=object(), stream_jpeg=b"jpeg-2")
    snap = store.get_stream()
    assert snap == (b"jpeg-2", 2)


def test_live_jpeg_endpoint_serves_freshest_frame(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        """
dashboard:
  host: 127.0.0.1
  port: 5000
  require_token: false
camera:
  type: opencv
""",
        encoding="utf-8",
    )
    config = load_config(str(cfg_path))

    class FakeCamera:
        def __init__(self):
            self.frame_store = FrameStore()
            self.frame_store.update(frame=object(), stream_jpeg=b"\xff\xd8live")

        def is_active(self):
            return True

    app = create_app(config, camera=FakeCamera())
    client = app.test_client()
    response = client.get("/api/live.jpg?since=0")
    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert response.data.startswith(b"\xff\xd8")
    assert "no-store" in response.headers.get("Cache-Control", "").lower()
    assert response.headers.get("X-Stream-Count") == "1"
