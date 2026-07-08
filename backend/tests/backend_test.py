"""Backend tests for AI Home Sentinel."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fall back to frontend/.env if not exported
    from pathlib import Path
    for line in (Path(__file__).resolve().parents[2] / "frontend" / ".env").read_text().splitlines():
        if line.startswith("REACT_APP_BACKEND_URL="):
            BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

API = f"{BASE_URL}/api"


@pytest.fixture(scope="session")
def s():
    sess = requests.Session()
    sess.headers.update({"Content-Type": "application/json"})
    return sess


# ---------- System / Health / Settings ----------
class TestSystem:
    def test_root(self, s):
        r = s.get(f"{API}/")
        assert r.status_code == 200
        assert r.json().get("status") == "ok"

    def test_health(self, s):
        r = s.get(f"{API}/system/health")
        assert r.status_code == 200
        d = r.json()
        assert "pi_connected" in d and "is_demo" in d
        assert "cameras_online" in d and "cameras_total" in d
        assert "metrics" in d and "cpu_percent" in d["metrics"]

    def test_get_and_update_settings(self, s):
        r = s.get(f"{API}/settings")
        assert r.status_code == 200
        original = r.json()
        assert "mode" in original and "processing_mode" in original

        patch = {
            "mode": "away",
            "processing_mode": "cloud",
            "dvr_segment_minutes": 60,
            "dvr_retention_hours": 72,
            "recognition_enabled": False,
        }
        r = s.put(f"{API}/settings", json=patch)
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] == "away"
        assert d["processing_mode"] == "cloud"
        assert d["dvr_segment_minutes"] == 60
        assert d["dvr_retention_hours"] == 72
        assert d["recognition_enabled"] is False

        # Verify persistence
        r = s.get(f"{API}/settings")
        d2 = r.json()
        assert d2["mode"] == "away"
        # Restore
        s.put(f"{API}/settings", json={"mode": original["mode"], "processing_mode": original["processing_mode"],
                                       "dvr_segment_minutes": original["dvr_segment_minutes"],
                                       "dvr_retention_hours": original["dvr_retention_hours"],
                                       "recognition_enabled": original["recognition_enabled"]})


# ---------- Cameras / Events ----------
class TestEvents:
    def test_list_cameras(self, s):
        r = s.get(f"{API}/cameras")
        assert r.status_code == 200
        cams = r.json()
        assert isinstance(cams, list) and len(cams) > 0

    def test_list_events(self, s):
        r = s.get(f"{API}/events")
        assert r.status_code == 200
        evs = r.json()
        assert isinstance(evs, list) and len(evs) > 0

    def test_events_filters(self, s):
        cams = s.get(f"{API}/cameras").json()
        cam_id = cams[0]["id"]
        r = s.get(f"{API}/events", params={"camera_id": cam_id, "limit": 50})
        assert r.status_code == 200
        for e in r.json():
            assert e["camera_id"] == cam_id
        # search
        r = s.get(f"{API}/events", params={"search": "package"})
        assert r.status_code == 200

    def test_toggle_event_saved(self, s):
        evs = s.get(f"{API}/events").json()
        ev_id = evs[0]["id"]
        r = s.patch(f"{API}/events/{ev_id}/save", params={"saved": "true"})
        assert r.status_code == 200
        assert r.json()["saved"] is True
        # Verify via list filter
        r2 = s.get(f"{API}/events", params={"saved": "true"})
        ids = [e["id"] for e in r2.json()]
        assert ev_id in ids
        # Toggle off
        s.patch(f"{API}/events/{ev_id}/save", params={"saved": "false"})


# ---------- DVR ----------
class TestDVR:
    def test_list_segments(self, s):
        r = s.get(f"{API}/dvr/segments")
        assert r.status_code == 200
        segs = r.json()
        assert isinstance(segs, list) and len(segs) > 0

    def test_segments_filters(self, s):
        cams = s.get(f"{API}/cameras").json()
        cam_id = cams[0]["id"]
        r = s.get(f"{API}/dvr/segments", params={"camera_id": cam_id})
        assert r.status_code == 200
        for seg in r.json():
            assert seg["camera_id"] == cam_id
        r = s.get(f"{API}/dvr/segments", params={"search": "front"})
        assert r.status_code == 200


# ---------- People CRUD ----------
class TestPeople:
    def test_people_crud(self, s):
        # Create
        payload = {"name": f"TEST_Person_{uuid.uuid4().hex[:6]}", "relationship": "friend",
                   "status": "trusted", "notes": "test"}
        r = s.post(f"{API}/people", json=payload)
        assert r.status_code == 200
        p = r.json()
        assert p["name"] == payload["name"]
        assert p["is_demo"] is False
        pid = p["id"]

        # List includes it
        listed = s.get(f"{API}/people").json()
        assert any(x["id"] == pid for x in listed)

        # Update
        r = s.put(f"{API}/people/{pid}", json={"status": "watch", "notes": "updated"})
        assert r.status_code == 200
        assert r.json()["status"] == "watch"
        assert r.json()["notes"] == "updated"

        # Delete
        r = s.delete(f"{API}/people/{pid}")
        assert r.status_code == 200

        # 404 after delete
        r = s.put(f"{API}/people/{pid}", json={"status": "normal"})
        assert r.status_code == 404


# ---------- Rules CRUD ----------
class TestRules:
    def test_rules_crud(self, s):
        payload = {"name": f"TEST_Rule_{uuid.uuid4().hex[:6]}",
                   "description": "test rule",
                   "trigger_type": "unknown_after_hours",
                   "active": True, "severity": "high"}
        r = s.post(f"{API}/rules", json=payload)
        assert r.status_code == 200
        rule = r.json()
        assert rule["name"] == payload["name"]
        assert rule["is_demo"] is False
        rid = rule["id"]

        # Toggle active
        r = s.put(f"{API}/rules/{rid}", json={"active": False})
        assert r.status_code == 200
        assert r.json()["active"] is False

        # Delete
        r = s.delete(f"{API}/rules/{rid}")
        assert r.status_code == 200
        r = s.put(f"{API}/rules/{rid}", json={"active": True})
        assert r.status_code == 404


# ---------- Sentinel AI (Gemini) ----------
class TestAssistant:
    def test_chat_and_history(self, s):
        session_id = f"TEST_sess_{uuid.uuid4().hex[:8]}"
        r = s.post(f"{API}/assistant/chat",
                   json={"session_id": session_id, "message": "Did any packages arrive today?"},
                   timeout=90)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "answer" in d and isinstance(d["answer"], str) and len(d["answer"]) > 0
        assert "sources" in d and isinstance(d["sources"], list)

        # History
        r2 = s.get(f"{API}/assistant/history", params={"session_id": session_id})
        assert r2.status_code == 200
        hist = r2.json()
        assert len(hist) >= 2
        roles = [h["role"] for h in hist]
        assert "user" in roles and "assistant" in roles


# ---------- Pi Integration Contract ----------
class TestPi:
    def test_pi_heartbeat_sets_connected(self, s):
        r = s.post(f"{API}/pi/heartbeat", json={
            "cpu_percent": 22.5, "temp_c": 48.1, "memory_percent": 55.0,
            "disk_percent": 40.0, "uptime_seconds": 1000, "cameras_online": 2
        })
        assert r.status_code == 200
        r2 = s.get(f"{API}/system/health")
        d = r2.json()
        assert d["pi_connected"] is True
        assert d["is_demo"] is False
        assert d["metrics"]["cpu_percent"] == 22.5

    def test_pi_event_and_dvr(self, s):
        cams = s.get(f"{API}/cameras").json()
        cam = cams[0]
        # Pi event
        ev_payload = {"camera_id": cam["id"], "camera_name": cam["name"],
                      "type": "person", "confidence": 0.92, "importance": "medium",
                      "ai_summary": "TEST_PI event"}
        r = s.post(f"{API}/pi/events", json=ev_payload)
        assert r.status_code == 200
        assert r.json()["is_demo"] is False

        # Pi DVR segment
        seg_payload = {"camera_id": cam["id"], "camera_name": cam["name"],
                       "start_time": "2026-01-01T00:00:00Z", "end_time": "2026-01-01T00:30:00Z",
                       "length_minutes": 30, "ai_summary": "TEST_PI seg"}
        r = s.post(f"{API}/pi/dvr/segment", json=seg_payload)
        assert r.status_code == 200
        assert r.json()["is_demo"] is False

    def test_pi_camera_upsert(self, s):
        cam_id = f"TEST_cam_{uuid.uuid4().hex[:6]}"
        payload = {"id": cam_id, "name": "TEST Front", "location": "front", "status": "online"}
        r = s.post(f"{API}/pi/cameras", json=payload)
        assert r.status_code == 200
        assert r.json()["is_demo"] is False
