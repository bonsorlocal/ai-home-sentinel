#!/usr/bin/env bash
set -euo pipefail
PI_IP="$(hostname -I | awk '{print $1}')"
CFG=/home/sentinel/ai-home-sentinel/config.yaml
sed -i "s|stream_url: .*|stream_url: \"http://${PI_IP}:5000/video\"|" "$CFG"
grep stream_url "$CFG"
sudo systemctl restart sentinel
sleep 8
curl -sf http://127.0.0.1:8080/api/cameras | tr ',' '\n' | grep -E 'cam-main|stream_url|is_demo' || true
