# Deploy AI Home Sentinel to Raspberry Pi
#
# Usage (from your laptop, with SSH configured):
#   ./scripts/deploy_pi.sh [pi-host]
#
# Default host: sentinel@<your-pi-ip>  (set PI_HOST env var or pass as arg)

set -euo pipefail

PI_HOST="${1:-${PI_HOST:-sentinel@raspberrypi.local}}"
PI_DIR="/home/sentinel/ai-home-sentinel"

echo "Deploying to ${PI_HOST}:${PI_DIR}"

ssh "${PI_HOST}" bash -s <<'REMOTE'
set -euo pipefail
cd /home/sentinel/ai-home-sentinel
git pull --ff-only
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart sentinel
sleep 3
systemctl is-active sentinel
curl -sf http://localhost:5000/health
echo ""
echo "Deploy OK. Open http://$(hostname -I | awk '{print $1}'):5000"
REMOTE

echo "Running brain smoke test on Pi..."
ssh "${PI_HOST}" "cd ${PI_DIR} && source venv/bin/activate && python scripts/smoke_brain.py"
