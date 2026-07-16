#!/usr/bin/env bash
# First-time setup on the Raspberry Pi (run ON the Pi as user sentinel).
#
#   cd /home/sentinel/ai-home-sentinel
#   bash scripts/pi_setup.sh
#
# Prerequisites: Raspberry Pi OS, git clone at /home/sentinel/ai-home-sentinel

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

echo "== AI Home Sentinel — Pi setup =="
echo "Project: ${ROOT}"

echo "== System packages =="
sudo apt update
sudo apt install -y python3-picamera2 python3-venv cmake build-essential \
  libopenblas-dev liblapack-dev

echo "== Python venv (system-site-packages for Picamera2) =="
if [[ ! -d venv ]]; then
  python3 -m venv --system-site-packages venv
fi
source venv/bin/activate

echo "== CPU PyTorch + Python deps =="
pip install --upgrade pip
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

echo "== secrets.yaml =="
if [[ ! -f secrets.yaml ]]; then
  if [[ -f secrets.yaml.example ]]; then
    cp secrets.yaml.example secrets.yaml
    echo "Created secrets.yaml from example — add grok_api_key before using the brain."
  else
    echo "WARNING: no secrets.yaml. Create one with grok_api_key for the brain."
  fi
else
  echo "secrets.yaml already exists."
fi

echo "== data dirs =="
mkdir -p data/known_faces data/events/snapshots data/dvr models

echo "== systemd service =="
sudo cp sentinel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sentinel

echo ""
echo "Setup complete. Next steps:"
echo "  1. Edit secrets.yaml — add grok_api_key"
echo "  2. Place YOLO model at models/yolo_nano.pt (or let ultralytics fetch on first run)"
echo "  3. Add face photos to data/known_faces/"
echo "  4. sudo systemctl start sentinel"
echo "  5. Open http://$(hostname -I | awk '{print $1}'):5000"
