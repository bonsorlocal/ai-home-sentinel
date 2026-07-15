#!/usr/bin/env bash
# deploy_emergent_pi.sh — AI Home Sentinel, Raspberry Pi LAN deploy (live-data mode)
#
# Native bash version for Raspberry Pi OS / Linux (no PowerShell needed).
# Builds the React frontend for same-origin (relative /api) and launches the FastAPI
# backend on :8080, which serves BOTH the API (/api/*) and the built frontend and
# PROXIES the existing on-Pi camera pipeline at PI_BASE_URL. It never opens the camera
# itself — only one process controls the Pi camera.
#
# RUN THIS ON THE RASPBERRY PI (not on Windows).
# Usage:
#   chmod +x deploy_emergent_pi.sh
#   ./deploy_emergent_pi.sh --install-service --host-ip 192.168.1.244 --pi-base-url http://127.0.0.1:5000
# Options:
#   --port N            (default 8080)
#   --bind-host H       (default 0.0.0.0)
#   --host-ip IP        (default 192.168.1.244)  LAN IP for the public URL
#   --pi-base-url URL   (default http://127.0.0.1:5000)  on-Pi Sentinel service
#   --pi-video-url URL  (optional) exact MJPEG feed if auto-discovery misses it
#   --camera-name NAME  (default "Pi Camera")
#   --install-service   install + enable systemd unit (survives reboot)
#   --skip-build        do not rebuild the frontend
#   --skip-install      do not (re)install Python deps
# Prereqs: python3+pip, node+yarn, running MongoDB (mongodb://localhost:27017).
set -euo pipefail

PORT=8080
BIND_HOST="0.0.0.0"
HOST_IP="192.168.1.244"
PI_BASE_URL="http://127.0.0.1:5000"
PI_VIDEO_URL=""
CAMERA_NAME="Pi Camera"
INSTALL_SERVICE=0
SKIP_BUILD=0
SKIP_INSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2;;
    --bind-host) BIND_HOST="$2"; shift 2;;
    --host-ip) HOST_IP="$2"; shift 2;;
    --pi-base-url) PI_BASE_URL="$2"; shift 2;;
    --pi-video-url) PI_VIDEO_URL="$2"; shift 2;;
    --camera-name) CAMERA_NAME="$2"; shift 2;;
    --install-service) INSTALL_SERVICE=1; shift;;
    --skip-build) SKIP_BUILD=1; shift;;
    --skip-install) SKIP_INSTALL=1; shift;;
    *) echo "Unknown option: $1" >&2; exit 1;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
PUBLIC_URL="http://${HOST_IP}:${PORT}"
ENV_FILE="$BACKEND/.env"

info() { echo -e "\033[36m[sentinel]\033[0m $*"; }
warn() { echo -e "\033[33m[sentinel]\033[0m $*"; }

# Upsert KEY=VALUE in an .env file (preserves other keys)
set_env() {
  local key="$1" val="$2"
  touch "$ENV_FILE"
  if grep -qE "^\s*${key}\s*=" "$ENV_FILE"; then
    local tmp; tmp="$(mktemp)"
    grep -vE "^\s*${key}\s*=" "$ENV_FILE" > "$tmp"
    printf '%s=%s\n' "$key" "$val" >> "$tmp"
    mv "$tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$val" >> "$ENV_FILE"
  fi
}

# 1) Backend environment
info "Configuring backend/.env"
if [[ ! -s "$ENV_FILE" ]]; then
  set_env MONGO_URL "mongodb://localhost:27017"
  set_env DB_NAME "sentinel"
  set_env CORS_ORIGINS "*"
  warn "Created a new .env — set EMERGENT_LLM_KEY for Sentinel AI (Gemini)."
fi
set_env PI_BASE_URL "$PI_BASE_URL"
set_env EMERGENT_BACKEND_PUBLIC_URL "$PUBLIC_URL"
set_env PI_CAMERA_NAME "$CAMERA_NAME"
[[ -n "$PI_VIDEO_URL" ]] && set_env PI_VIDEO_URL "$PI_VIDEO_URL"
grep -qE "^EMERGENT_LLM_KEY=" "$ENV_FILE" || warn "EMERGENT_LLM_KEY not set — Sentinel AI chat will not work until you add it."

# 2) Backend dependencies
if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  info "Installing backend Python dependencies"
  python3 -m pip install --upgrade pip >/dev/null
  python3 -m pip install -r "$BACKEND/requirements.txt"
fi

# 3) Frontend build (relative /api => same origin on :PORT)
if [[ "$SKIP_BUILD" -eq 0 ]]; then
  info "Building frontend (REACT_APP_BACKEND_URL='' -> relative /api, same origin)"
  ( cd "$FRONTEND" && REACT_APP_BACKEND_URL="" yarn install --frozen-lockfile && REACT_APP_BACKEND_URL="" yarn build )
fi
[[ -f "$FRONTEND/build/index.html" ]] || { echo "Frontend build missing — run without --skip-build first." >&2; exit 1; }

# 4) systemd (permanent, survives reboot) or foreground
if [[ "$INSTALL_SERVICE" -eq 1 ]]; then
  info "Installing systemd unit emergent-app.service"
  sudo tee /etc/systemd/system/emergent-app.service >/dev/null <<UNIT
[Unit]
Description=AI Home Sentinel (Emergent) web app
After=network-online.target mongod.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$BACKEND
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/env python3 -m uvicorn server:app --host $BIND_HOST --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable emergent-app.service
  sudo systemctl restart emergent-app.service
  sleep 2
  sudo systemctl status emergent-app.service --no-pager -l | head -n 12 || true
  info "Managed by systemd (auto-starts on boot). Open $PUBLIC_URL"
else
  info "Starting on $PUBLIC_URL (Pi stack: $PI_BASE_URL). Ctrl+C to stop."
  ( cd "$BACKEND" && exec python3 -m uvicorn server:app --host "$BIND_HOST" --port "$PORT" )
fi
