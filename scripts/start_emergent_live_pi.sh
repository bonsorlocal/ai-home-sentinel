#!/usr/bin/env bash
# Start Emergent UI on Pi with live Sentinel camera via pi_bridge (no mock).
set -euo pipefail

SENTINEL_DIR="${SENTINEL_DIR:-/home/sentinel/ai-home-sentinel}"
APP_DIR="${EMERGENT_APP_DIR:-/home/sentinel/emergent-app}"
PI_IP="$(hostname -I | awk '{print $1}')"
STREAM_URL="http://${PI_IP}:5000/video"

source "$SENTINEL_DIR/venv/bin/activate"
pip install -q 'requests>=2.31.0'

export STREAM_URL
python3 - <<'PY'
from pathlib import Path
import os
import re

stream_url = os.environ["STREAM_URL"]
path = Path("/home/sentinel/ai-home-sentinel/config.yaml")
text = path.read_text(encoding="utf-8")
block = f"""
pi_bridge:
  enabled: true
  base_url: "http://127.0.0.1:8080"
  heartbeat_seconds: 10
  camera_sync_seconds: 30
  post_events: true
  post_dvr_segments: true
  request_timeout_seconds: 3
  stream_url: "{stream_url}"
"""
if "pi_bridge:" not in text:
    if not text.endswith("\n"):
        text += "\n"
    text += block
else:
    text = re.sub(r"(?m)^(\s*)enabled:\s*false", r"\1enabled: true", text, count=1)
    text = re.sub(r'(?m)^(\s*)stream_url:.*$', rf'\1stream_url: "{stream_url}"', text)
path.write_text(text, encoding="utf-8")
print("pi_bridge enabled with", stream_url)
PY

pkill -f '/home/sentinel/emergent-app/venv/bin/uvicorn server:app' 2>/dev/null || true
pkill -f 'http.server 3000' 2>/dev/null || true
pkill -f 'emergent_host.py' 2>/dev/null || true
sleep 1

export EMERGENT_APP_DIR="$APP_DIR"
export PORT=8080
nohup "$APP_DIR/venv/bin/python" "$SENTINEL_DIR/scripts/emergent_host.py" \
  > /home/sentinel/emergent-app.log 2>&1 < /dev/null &
echo $! > /home/sentinel/emergent-app.pid

sudo systemctl restart sentinel
sleep 8

echo "=== emergent api ==="
curl -sf --max-time 8 http://127.0.0.1:8080/api/ || true
echo
curl -sf --max-time 8 http://127.0.0.1:8080/ -o /dev/null -w 'ui:%{http_code}\n' || true

echo "=== cameras ==="
curl -sf --max-time 8 http://127.0.0.1:8080/api/cameras | tr ',' '\n' | grep -E 'cam-main|stream_url|is_demo' || true

echo "OPEN_URL=http://${PI_IP}:8080"
echo "LIVE_STREAM=${STREAM_URL}"
