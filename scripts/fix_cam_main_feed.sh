#!/usr/bin/env bash
set -euo pipefail
PI_IP="$(hostname -I | awk '{print $1}')"
FEED="http://${PI_IP}:8080/api/pi/live-feed"
curl -sf -X POST http://127.0.0.1:8080/api/pi/cameras \
  -H 'Content-Type: application/json' \
  -d "{\"id\":\"cam-main\",\"name\":\"Main Camera\",\"location\":\"Home\",\"status\":\"online\",\"stream_url\":\"${FEED}\",\"thumbnail_url\":\"${FEED}\"}"
echo
curl -sf http://127.0.0.1:8080/api/pi/live-feed -o /dev/null -w 'live-feed:%{http_code}\n' --max-time 3 || true
curl -sf http://127.0.0.1:8080/api/cameras | tr ',' '\n' | grep -E 'cam-main|live-feed|thumbnail' || true
