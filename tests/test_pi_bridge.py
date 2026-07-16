"""PiBridge honesty and offline backoff helpers."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sentinel.pi_bridge import PiBridge  # noqa: E402


def _make_bridge(camera_online: bool = False) -> PiBridge:
    def _get(*args, **kwargs):
        if args and args[0] == "pi_bridge":
            return {
                "enabled": False,
                "base_url": "http://127.0.0.1:8080",
                "heartbeat_seconds": 10,
                "camera_sync_seconds": 60,
            }
        if args[:2] == ("dashboard", "port"):
            return kwargs.get("default", 5000)
        return {}

    config = SimpleNamespace(get=_get)
    return PiBridge(
        config,
        ledger=SimpleNamespace(list_recent=lambda limit=1: []),
        dvr=SimpleNamespace(enabled=False, index=None),
        camera_active_fn=lambda: camera_online,
    )


def test_camera_online_helper_respects_callback():
    online = _make_bridge(camera_online=True)
    offline = _make_bridge(camera_online=False)
    assert online._camera_is_online() is True
    assert offline._camera_is_online() is False


def test_sync_camera_payload_marks_offline_when_camera_down(monkeypatch):
    bridge = _make_bridge(camera_online=False)
    captured = {}

    def fake_post(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return True

    monkeypatch.setattr(bridge, "_post", fake_post)
    bridge._sync_camera()
    assert captured["path"] == "/api/pi/cameras"
    assert captured["payload"]["status"] == "offline"
    assert captured["payload"]["stream_url"] == ""
    assert captured["payload"]["thumbnail_url"] == ""


def test_sync_camera_payload_marks_online_when_camera_up(monkeypatch):
    bridge = _make_bridge(camera_online=True)
    captured = {}

    def fake_post(path, payload):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(bridge, "_post", fake_post)
    bridge._sync_camera()
    assert captured["payload"]["status"] == "online"
    assert captured["payload"]["stream_url"]
    assert captured["payload"]["thumbnail_url"]
