"""Backend tests for AI Home Sentinel LAN/live-data-first proxy.

Tests LIVE state (mock Pi running) and OFFLINE state (mock Pi stopped).
Leaves mock Pi RUNNING at the end.
"""
import os
import subprocess
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://home-sentinel-ai-1.preview.emergentagent.com").rstrip("/")
# fallback: read frontend/.env
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")


def _start_mock_pi():
    subprocess.Popen(
        ["bash", "-c", "cd /app && nohup python tools/mock_pi.py >/tmp/mockpi.log 2>&1 &"]
    )
    # wait until healthy
    for _ in range(20):
        try:
            r = requests.get("http://127.0.0.1:5000/status", timeout=1)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)


def _stop_mock_pi():
    subprocess.run(["pkill", "-f", "mock_pi.py"])
    time.sleep(3)


@pytest.fixture(scope="module", autouse=True)
def ensure_live_at_end():
    # Ensure live before tests
    _start_mock_pi()
    yield
    # Leave live at end
    _start_mock_pi()


# ============== LIVE STATE ==============
class TestLiveState:
    def test_health_live(self):
        r = requests.get(f"{BASE_URL}/api/system/health", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["pi_connected"] is True
        assert d["is_demo"] is False
        assert d["offline"] is False
        assert d["cameras_online"] == 2
        assert d["cameras_total"] == 2
        m = d["metrics"]
        assert m["cpu_percent"] == 41.5
        assert m["temp_c"] == 55.2

    def test_events_live(self):
        r = requests.get(f"{BASE_URL}/api/events", timeout=10)
        assert r.status_code == 200
        evs = r.json()
        ids = {e["id"] for e in evs}
        assert "live-1" in ids and "live-2" in ids
        # No demo event ids
        for demo_id in ("ev-1", "ev-2", "ev-3", "ev-4", "ev-5", "ev-6", "ev-7"):
            assert demo_id not in ids
        # Alex recognized
        alex = [e for e in evs if e["id"] == "live-1"][0]
        assert "Alex" in alex["ai_summary"]
        # No 'Jane Doe' anywhere
        for e in evs:
            assert e.get("person_name") != "Jane Doe"

    def test_cameras_live(self):
        r = requests.get(f"{BASE_URL}/api/cameras", timeout=10)
        assert r.status_code == 200
        cams = r.json()
        ids = {c["id"] for c in cams}
        assert ids == {"picam0", "usb1"}
        names = {c["name"] for c in cams}
        assert "Picamera2" in names and "USB Cam" in names

    def test_stream_live(self):
        r = requests.get(f"{BASE_URL}/api/pi/stream", timeout=5, stream=True)
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert ct.startswith("multipart/x-mixed-replace")
        r.close()

    def test_assistant_online(self):
        r = requests.post(
            f"{BASE_URL}/api/assistant/chat",
            json={"session_id": "test-live", "message": "Are the cameras online?"},
            timeout=60,
        )
        assert r.status_code == 200
        d = r.json()
        assert "answer" in d
        assert not d.get("offline")


# ============== OFFLINE STATE ==============
class TestOfflineState:
    @classmethod
    def setup_class(cls):
        _stop_mock_pi()

    @classmethod
    def teardown_class(cls):
        _start_mock_pi()

    def test_health_offline(self):
        r = requests.get(f"{BASE_URL}/api/system/health", timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert d["pi_connected"] is False
        assert d["is_demo"] is False
        assert d["offline"] is True
        assert d["metrics"] is None

    def test_events_offline(self):
        r = requests.get(f"{BASE_URL}/api/events", timeout=10)
        assert r.status_code == 200
        assert r.json() == []

    def test_cameras_offline(self):
        r = requests.get(f"{BASE_URL}/api/cameras", timeout=10)
        assert r.status_code == 200
        assert r.json() == []

    def test_stream_offline(self):
        r = requests.get(f"{BASE_URL}/api/pi/stream", timeout=10)
        assert r.status_code == 503

    def test_assistant_offline(self):
        r = requests.post(
            f"{BASE_URL}/api/assistant/chat",
            json={"session_id": "test-offline", "message": "Any activity?"},
            timeout=30,
        )
        assert r.status_code == 200
        d = r.json()
        assert d.get("offline") is True
        assert "offline" in d["answer"].lower() or "⚠️" in d["answer"]
