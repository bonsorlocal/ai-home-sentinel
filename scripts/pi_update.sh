#!/usr/bin/env bash
# Update and restart AI Home Sentinel on the Pi (run ON the Pi).
#
#   cd /home/sentinel/ai-home-sentinel
#   bash scripts/pi_update.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

echo "== AI Home Sentinel — Pi update =="

if [[ ! -d venv ]]; then
  echo "No venv found. Run: bash scripts/pi_setup.sh"
  exit 1
fi

source venv/bin/activate
pip install -q -r requirements.txt

if [[ ! -f secrets.yaml ]]; then
  echo "WARNING: secrets.yaml missing — brain will be offline."
fi

sudo systemctl restart sentinel
sleep 3

echo ""
systemctl status sentinel --no-pager -l | head -15
echo ""
curl -sf http://localhost:5000/health && echo ""
curl -sf http://localhost:5000/status | python3 -c "
import sys, json
s = json.load(sys.stdin)
print('phase:', s.get('phase'))
print('camera:', s.get('camera_active'), '|', (s.get('camera') or {}).get('message',''))
print('motion:', s.get('motion_active'))
print('detector:', s.get('detector_active'))
print('faces:', s.get('face_recognition_active'))
print('brain:', s.get('brain_active'))
print('events:', s.get('event_count'))
"
echo ""
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):5000"
