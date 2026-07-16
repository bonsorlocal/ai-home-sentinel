#!/usr/bin/env bash
# Runtime-only setup for Emergent app on Raspberry Pi.
# Frontend build is done on laptop; Pi only installs Python runtime + service.

set -euo pipefail

APP_DIR="${EMERGENT_APP_DIR:-/home/sentinel/emergent-app}"
BACKEND_PORT="${EMERGENT_BACKEND_PORT:-8080}"
DB_NAME="${EMERGENT_DB_NAME:-sentinel_app}"
SENTINEL_DIR="${SENTINEL_DIR:-/home/sentinel/ai-home-sentinel}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REQ_FILE="${ROOT}/scripts/emergent_requirements.pi.txt"
HOST_FILE="${ROOT}/scripts/emergent_host.py"
STUBS_DIR="${ROOT}/scripts/emergent_stubs"
PI_IP="$(hostname -I | awk '{print $1}')"

BACKEND_DIR="${APP_DIR}/backend"
FRONTEND_BUILD_DIR="${APP_DIR}/frontend/build"

echo "== Emergent runtime setup =="
echo "App dir:      ${APP_DIR}"
echo "Backend port: ${BACKEND_PORT}"

if [[ ! -d "${BACKEND_DIR}" ]]; then
  echo "ERROR: Missing ${BACKEND_DIR}. Copy backend files from deploy script first."
  exit 1
fi
if [[ ! -f "${FRONTEND_BUILD_DIR}/index.html" ]]; then
  echo "ERROR: Missing frontend build at ${FRONTEND_BUILD_DIR}."
  echo "Build frontend on laptop first and copy build/ to Pi."
  exit 1
fi
if [[ ! -f "${REQ_FILE}" ]]; then
  echo "ERROR: Missing ${REQ_FILE}"
  exit 1
fi
if [[ ! -f "${HOST_FILE}" ]]; then
  echo "ERROR: Missing ${HOST_FILE}"
  exit 1
fi

echo "== Python runtime =="
if [[ ! -d "${APP_DIR}/venv" ]]; then
  python3 -m venv "${APP_DIR}/venv"
fi
source "${APP_DIR}/venv/bin/activate"
pip install --upgrade pip
pip install -r "${REQ_FILE}"
if [[ -d "${STUBS_DIR}/emergentintegrations" ]]; then
  cp -r "${STUBS_DIR}/emergentintegrations" "${APP_DIR}/venv/lib/python"*/site-packages/
fi

echo "== Backend .env =="
GOOGLE_KEY=""
MONGO_URL="${EMERGENT_MONGO_URL:-}"
if [[ -f "${SENTINEL_DIR}/secrets.yaml" ]]; then
  GOOGLE_KEY="$(grep -E '^google_api_key:' "${SENTINEL_DIR}/secrets.yaml" | head -1 | sed -E 's/^[^:]+:[[:space:]]*"?([^"#]+)"?.*/\1/' | tr -d '"' | xargs || true)"
  if [[ -z "${MONGO_URL}" ]]; then
    MONGO_URL="$(grep -E '^mongo_atlas_url:' "${SENTINEL_DIR}/secrets.yaml" | head -1 | sed -E 's/^[^:]+:[[:space:]]*"?([^"#]+)"?.*/\1/' | tr -d '"' | xargs || true)"
  fi
fi
if [[ -z "${MONGO_URL}" ]]; then
  echo "ERROR: Missing MongoDB Atlas URI. Add mongo_atlas_url to ${SENTINEL_DIR}/secrets.yaml."
  exit 1
fi

cat > "${BACKEND_DIR}/.env" <<EOF
MONGO_URL=${MONGO_URL}
DB_NAME=${DB_NAME}
EMERGENT_LLM_KEY=${GOOGLE_KEY}
CORS_ORIGINS=*
EOF

echo "== systemd: emergent-app (single host) =="
sudo tee /etc/systemd/system/emergent-app.service >/dev/null <<EOF
[Unit]
Description=Emergent webapp host (FastAPI + React SPA)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sentinel
Group=sentinel
WorkingDirectory=${APP_DIR}
Environment=EMERGENT_APP_DIR=${APP_DIR}
Environment=PORT=${BACKEND_PORT}
ExecStart=${APP_DIR}/venv/bin/python ${HOST_FILE}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=emergent-app

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable emergent-app
sudo systemctl restart emergent-app
sleep 3

if ! systemctl is-active --quiet emergent-app; then
  echo "Emergent app failed to start."
  journalctl -u emergent-app -n 40 --no-pager || true
  exit 1
fi
if ! curl -sf "http://127.0.0.1:${BACKEND_PORT}/api/" >/dev/null; then
  echo "Emergent app started but /api/ health check failed."
  journalctl -u emergent-app -n 40 --no-pager || true
  exit 1
fi

echo "== Cleanup old services =="
sudo systemctl disable --now emergent-backend emergent-frontend >/dev/null 2>&1 || true
sudo rm -f /etc/systemd/system/emergent-backend.service /etc/systemd/system/emergent-frontend.service
sudo systemctl disable --now mongod >/dev/null 2>&1 || true
sudo rm -f /etc/systemd/system/mongod.service
sudo rm -f /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo systemctl daemon-reload

echo ""
echo "Emergent app is running."
echo "  URL:      http://${PI_IP}:${BACKEND_PORT}"
echo "  API:      http://${PI_IP}:${BACKEND_PORT}/api/"
echo "  Compare:  http://${PI_IP}:5000"
echo ""
echo "Optional Sentinel nav link in config.yaml:"
echo "  emergent_app:"
echo "    enabled: true"
echo "    url: \"http://${PI_IP}:${BACKEND_PORT}\""
