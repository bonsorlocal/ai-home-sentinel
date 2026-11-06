#!/usr/bin/env bash
# Fresh Raspberry Pi OS bootstrap for AI Home Sentinel (16GB SD + USB data stick).
# Run ON the Pi as user sentinel (will prompt for sudo password):
#
#   bash scripts/bootstrap_fresh_pi.sh
#
# Or from Windows after syncing the repo:
#   ssh -t sentinel-pi "bash /home/sentinel/ai-home-sentinel/scripts/bootstrap_fresh_pi.sh"

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USB_UUID="${USB_UUID:-82C6-6C2D}"
USB_MOUNT="/mnt/sentinel-data"
cd "${ROOT}"

echo "== AI Home Sentinel — fresh Pi bootstrap =="
echo "Project: ${ROOT}"
echo "USB UUID: ${USB_UUID} -> ${USB_MOUNT}"

echo "== System packages =="
sudo apt-get update
sudo apt-get install -y \
  python3-picamera2 python3-venv cmake build-essential \
  libopenblas-dev liblapack-dev ffmpeg exfatprogs \
  rpicam-apps || sudo apt-get install -y \
  python3-picamera2 python3-venv cmake build-essential \
  libopenblas-dev liblapack-dev ffmpeg

echo "== Persist USB data mount =="
sudo mkdir -p "${USB_MOUNT}"
# Desktop auto-mount may hold the device — release it.
if mountpoint -q /media/sentinel/Instruction 2>/dev/null; then
  sudo umount /media/sentinel/Instruction || true
fi
if ! grep -q "${USB_UUID}" /etc/fstab; then
  echo "UUID=${USB_UUID} ${USB_MOUNT} exfat defaults,uid=1000,gid=1000,umask=0022,nofail,x-systemd.device-timeout=10 0 0" \
    | sudo tee -a /etc/fstab
fi
sudo systemctl daemon-reload
if ! mountpoint -q "${USB_MOUNT}"; then
  sudo mount "${USB_MOUNT}"
fi

echo "== USB data directories =="
mkdir -p \
  "${USB_MOUNT}/events/snapshots" \
  "${USB_MOUNT}/events/clips" \
  "${USB_MOUNT}/events/unknown_faces" \
  "${USB_MOUNT}/dvr/segments" \
  "${USB_MOUNT}/models" \
  "${USB_MOUNT}/known_faces"
# Keep a tiny local fallback for DVR if USB is missing at boot.
mkdir -p "${ROOT}/data/dvr" "${ROOT}/data/events" "${ROOT}/models"

echo "== Python venv (system-site-packages for Picamera2) =="
if [[ ! -d venv ]]; then
  python3 -m venv --system-site-packages venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --upgrade pip

echo "== CPU PyTorch (prefer piwheels / default index on aarch64) =="
if ! python -c "import torch" 2>/dev/null; then
  # Official CPU wheel index is mostly x86; on Pi use default indexes first.
  pip install torch torchvision || \
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
fi

echo "== Python requirements =="
# face_recognition/dlib often fail on Python 3.13 — install the rest first.
pip install -r <(grep -v '^face_recognition' requirements.txt || cat requirements.txt) || \
  pip install -r requirements.txt
# Explicit face install (no silent fake stack). Set INSTALL_FACE_RECOGNITION=0 to skip.
if [[ "${INSTALL_FACE_RECOGNITION:-1}" == "1" ]]; then
  bash scripts/_pi_install_face_recognition.sh || \
    echo "WARNING: face_recognition not installed; gate uses YOLO person + owner notes until fixed."
fi

echo "== YOLO model =="
if [[ -f models/yolo_nano.pt ]]; then
  cp -n models/yolo_nano.pt "${USB_MOUNT}/models/yolo_nano.pt" || true
elif [[ -f yolov8n.pt ]]; then
  cp -n yolov8n.pt "${USB_MOUNT}/models/yolo_nano.pt" || true
  cp -n yolov8n.pt models/yolo_nano.pt || true
fi

echo "== secrets.yaml =="
if [[ ! -f secrets.yaml ]]; then
  if [[ -f secrets.yaml.example ]]; then
    cp secrets.yaml.example secrets.yaml
    echo "Created secrets.yaml from example — add API keys."
  fi
else
  echo "secrets.yaml already present."
fi

echo "== systemd service =="
sudo cp sentinel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sentinel
sudo systemctl restart sentinel
sleep 5

echo "== Verify =="
systemctl is-active sentinel || true
curl -sf http://localhost:5000/health || true
echo ""
curl -sf http://localhost:5000/api/dvr/status || true
echo ""
df -h / "${USB_MOUNT}"
echo ""
echo "Dashboard: http://$(hostname -I | awk '{print $1}'):5000"
echo "Done."
