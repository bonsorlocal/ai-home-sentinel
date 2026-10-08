#!/usr/bin/env bash
# Run ON the Pi (keyboard/SSH) to bring up the investor demo in ~2 minutes.
# Pi-local Emergent UI + live camera + Gemini brain — no Cloudflare tunnel needed on LAN.
set -euo pipefail

SENTINEL_DIR="${SENTINEL_DIR:-/home/sentinel/ai-home-sentinel}"
APP_DIR="${EMERGENT_APP_DIR:-/home/sentinel/emergent-app}"
PORT="${EMERGENT_BACKEND_PORT:-8080}"
PI_IP="$(hostname -I | awk '{print $1}')"

echo "== Investor demo bootstrap (Pi-local) =="

echo "[1/5] Ensure SSH stays on (for laptop deploys)..."
sudo systemctl enable --now ssh 2>/dev/null || sudo systemctl enable --now sshd 2>/dev/null || true

echo "[2/5] Start Sentinel camera stack (:5000)..."
sudo systemctl restart sentinel
for i in $(seq 1 20); do
  if curl -sf http://127.0.0.1:5000/status >/dev/null 2>&1; then
    echo "  Sentinel /status OK"
    break
  fi
  sleep 2
  if [[ "$i" -eq 20 ]]; then
    echo "ERROR: Sentinel not responding on :5000. Check: journalctl -u sentinel -n 50"
    exit 1
  fi
done

echo "[3/5] Start Emergent app (:${PORT})..."
if [[ ! -f "${APP_DIR}/frontend/build/index.html" ]]; then
  echo "ERROR: Emergent app not deployed yet."
  echo "  From Windows (same Wi-Fi):"
  echo "    \$env:PI_HOST = \"sentinel@${PI_IP}\""
  echo "    \$env:EMERGENT_REPO_URL = \"https://github.com/YOUR_USER/YOUR_EMERGENT_REPO.git\""
  echo "    .\\scripts\\deploy_emergent_pi.ps1"
  echo "  Or: .\\scripts\\finish_emergent_pi_setup.ps1  (if files already copied)"
  exit 1
fi

if systemctl list-unit-files emergent-app.service >/dev/null 2>&1; then
  sudo systemctl restart emergent-app
else
  echo "  emergent-app.service missing — run setup_emergent_pi.sh first"
  exit 1
fi

for i in $(seq 1 15); do
  if curl -sf "http://127.0.0.1:${PORT}/api/" >/dev/null 2>&1; then
    echo "  Emergent /api/ OK"
    break
  fi
  sleep 2
  if [[ "$i" -eq 15 ]]; then
    echo "ERROR: Emergent not responding. Check: journalctl -u emergent-app -n 50"
    exit 1
  fi
done

echo "[4/5] Health check (camera pipeline)..."
HEALTH="$(curl -sf "http://127.0.0.1:${PORT}/api/system/health" || echo '{}')"
echo "$HEALTH" | head -c 400
echo

echo "[5/5] Enable pi_bridge (events flow Sentinel -> Emergent)..."
if [[ -f "${SENTINEL_DIR}/scripts/start_emergent_live_pi.sh" ]]; then
  bash "${SENTINEL_DIR}/scripts/start_emergent_live_pi.sh" >/dev/null 2>&1 || true
  sudo systemctl restart sentinel
  sleep 5
fi

echo ""
echo "=========================================="
echo " INVESTOR DEMO READY"
echo "=========================================="
echo " Open on any device on your Wi-Fi:"
echo "   http://${PI_IP}:${PORT}"
echo ""
echo " Demo flow:"
echo "   1. LIVE EVENTS — camera feed + detections"
echo "   2. SENTINEL AI — ask \"What do you see right now?\""
echo "   3. EVENT LOG / DVR — recorded activity"
echo ""
echo " Sentinel (native): http://${PI_IP}:5000"
echo "=========================================="
