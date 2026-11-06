#!/usr/bin/env bash
set -eu
echo "== rpicam =="
rpicam-hello --list-cameras 2>&1 || true
echo "== status =="
curl -sf http://127.0.0.1:5000/status -o /tmp/sentinel_status.json
python3 - <<'PY'
import json
s=json.load(open("/tmp/sentinel_status.json"))
print("camera_active", s.get("camera_active"))
print("detector_active", s.get("detector_active"))
print("brain_active", s.get("brain_active"))
d=s.get("dvr") or {}
print("dvr_available", d.get("available"))
print("dvr_message", (d.get("message") or "")[:160])
cam = s.get("camera") or (s.get("runtime") or {}).get("camera")
print("camera_block", cam)
PY
echo "== service =="
systemctl is-active sentinel || true
echo "== recent camera logs =="
journalctl -u sentinel -n 50 --no-pager | grep -iE 'camera|picamera|libcamera|No cameras|unavailable|error' || true
