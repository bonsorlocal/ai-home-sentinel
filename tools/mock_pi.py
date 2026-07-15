"""QA-ONLY mock of the on-Pi Sentinel Flask stack (http://127.0.0.1:5000).

This is a TEST HARNESS. It is NOT part of the Emergent app and is NOT deployed by
deploy_emergent_pi.ps1. It exists only so the live-data proxy path can be verified in the
cloud preview where no physical Pi is attached. Run:  python tools/mock_pi.py
"""
import base64
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# A tiny valid JPEG (1x1) — enough to validate MJPEG stream plumbing / <img> loading.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q=="
)

NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

STATUS = {
    "cpu_percent": 41.5, "temp_c": 55.2, "memory_percent": 58.0, "disk_percent": 44.0,
    "uptime_seconds": 90000, "mode": "home", "processing_mode": "hybrid",
    "cameras_online": 2, "cameras_total": 2,
    "cameras": [
        {"id": "picam0", "name": "Picamera2", "location": "Front door", "status": "online"},
        {"id": "usb1", "name": "USB Cam", "location": "Backyard", "status": "online"},
    ],
}
EVENTS = [
    {"id": "live-1", "timestamp": NOW, "camera_id": "picam0", "camera_name": "Picamera2",
     "type": "person", "person_id": "p1", "person_name": "Alex", "known": True,
     "objects": ["person"], "confidence": 0.97, "importance": "low",
     "ai_summary": "Alex recognized at front door.", "ai_interpretation": "Household member arrived.",
     "tags": ["known", "arrival"], "thumbnail_url": None, "clip_url": None, "saved": True, "is_demo": False},
    {"id": "live-2", "timestamp": NOW, "camera_id": "picam0", "camera_name": "Picamera2",
     "type": "unknown_person", "person_id": None, "person_name": None, "known": False,
     "objects": ["person"], "confidence": 0.82, "importance": "high",
     "ai_summary": "Unknown person at front door.", "ai_interpretation": "No face match found.",
     "tags": ["unknown"], "thumbnail_url": None, "clip_url": None, "saved": True, "is_demo": False},
]
SEGMENTS = [
    {"id": "s-live-1", "camera_id": "picam0", "camera_name": "Picamera2",
     "start_time": NOW, "end_time": NOW, "length_minutes": 30,
     "ai_summary": "Front door activity: one known arrival, one unknown visit.",
     "tags": ["known", "unknown"], "people": ["Alex"], "objects": ["person"],
     "linked_event_ids": ["live-1", "live-2"], "thumbnail_url": None, "is_demo": False},
]
PEOPLE = [
    {"id": "p1", "name": "Alex", "relationship": "Resident", "status": "trusted",
     "photo_url": None, "encodings_count": 5, "recognition_count": 20, "last_seen": NOW,
     "notes": "", "is_demo": False, "created_at": NOW},
]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/status":
            return self._json(STATUS)
        if path == "/api/events":
            return self._json(EVENTS)
        if path == "/api/dvr/segments":
            return self._json(SEGMENTS)
        if path == "/api/people":
            return self._json(PEOPLE)
        if path.startswith("/api/events/") and path.endswith("/save"):
            return self._json({"ok": True})
        if path == "/video_feed":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                for _ in range(1000):
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(JPEG)}\r\n\r\n".encode())
                    self.wfile.write(JPEG)
                    self.wfile.write(b"\r\n")
                    time.sleep(0.2)
            except (BrokenPipeError, ConnectionResetError):
                return
            return
        self.send_response(404)
        self.end_headers()


if __name__ == "__main__":
    print("Mock Pi Sentinel stack on http://127.0.0.1:5000")
    ThreadingHTTPServer(("127.0.0.1", 5000), H).serve_forever()
