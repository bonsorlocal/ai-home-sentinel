#!/usr/bin/env bash
set -eu
echo "service=$(systemctl is-active sentinel || true)"
curl -sf http://127.0.0.1:5000/health || echo HEALTH_FAIL
echo
curl -sf http://127.0.0.1:5000/api/dvr/status || echo DVR_FAIL
echo
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:5000/status", timeout=10) as r:
    s = json.load(r)
keys = ["phase", "camera_active", "motion_active", "detector_active", "brain_active"]
print({k: s.get(k) for k in keys})
d = s.get("dvr") or {}
print("dvr", {
    "available": d.get("available"),
    "active": d.get("active"),
    "storage_root": d.get("storage_root"),
    "message": (d.get("message") or "")[:160],
})
PY
journalctl -u sentinel -n 40 --no-pager || true
