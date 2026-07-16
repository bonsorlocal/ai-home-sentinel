import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://home-sentinel-ai-1.preview.emergentagent.com").rstrip("/")


def test_root():
    r = requests.get(f"{BASE_URL}/api/", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("service") == "AI Home Sentinel"
    assert d.get("status") == "ok"


def test_system_health():
    r = requests.get(f"{BASE_URL}/api/system/health", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("pi_connected") is True, d
    assert d.get("offline") is False, d
    assert d.get("camera_online") is True, d
    assert d.get("is_demo") is False, d


def test_camera_diagnostics():
    r = requests.get(f"{BASE_URL}/api/camera/diagnostics", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("camera_detected") is True, d
    assert d.get("stream_endpoint_reachable") is True, d
    assert d.get("frame_received") is True, d
    assert d.get("video_url") == "http://127.0.0.1:5000/video_feed", d


def test_camera_stream():
    r = requests.get(f"{BASE_URL}/api/camera/stream", timeout=15, stream=True)
    assert r.status_code == 200
    ct = r.headers.get("Content-Type", "")
    assert "multipart/x-mixed-replace" in ct, ct
    r.close()


def test_events_live():
    r = requests.get(f"{BASE_URL}/api/events", timeout=15)
    assert r.status_code == 200
    d = r.json()
    events = d if isinstance(d, list) else d.get("events", d.get("items", []))
    assert isinstance(events, list) and len(events) > 0, d
    ids = [str(e.get("id", "")) for e in events]
    assert any("live" in i for i in ids), ids
    blob = str(d).lower()
    assert "jane doe" not in blob
    assert "front porch" not in blob
    assert "package" not in blob
