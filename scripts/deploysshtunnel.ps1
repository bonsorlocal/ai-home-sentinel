<#
.SYNOPSIS
Bootstrap a fresh Cloudflare quick tunnel to Sentinel on the Pi.

.DESCRIPTION
From a Windows PowerShell shell, this script will:
  1) SSH to the Pi
  2) Ensure cloudflared is installed
  3) Verify Sentinel /status locally on Pi
  4) Restart a fresh cloudflared quick tunnel (http2)
  5) Extract and print the new trycloudflare URL
  6) Print copy/paste instructions for Emergent PI_BASE_URL + redeploy

Usage:
  .\scripts\deploysshtunnel.ps1
  .\scripts\deploysshtunnel.ps1 -PiHost sentinel@192.168.1.244
#>

param(
    [string]$PiHost = $env:PI_HOST
)

$ErrorActionPreference = "Stop"

if (-not $PiHost) {
    $PiHost = "sentinel-pi"
}

Write-Host "== deploysshtunnel ==" -ForegroundColor Cyan
Write-Host "Pi host: $PiHost"

Write-Host "`n[1/3] Testing SSH..." -ForegroundColor Cyan
ssh -o ConnectTimeout=10 $PiHost "echo ok"
if ($LASTEXITCODE -ne 0) {
    throw "SSH failed. Set `$env:PI_HOST or pass -PiHost."
}

Write-Host "[2/3] Starting fresh Cloudflare tunnel on Pi..." -ForegroundColor Cyan
$remote = @'
set -euo pipefail

if ! command -v cloudflared >/dev/null 2>&1; then
  curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb -o /tmp/cloudflared.deb
  sudo dpkg -i /tmp/cloudflared.deb
fi

if ! systemctl is-active --quiet sentinel; then
  sudo systemctl restart sentinel
  sleep 3
fi

# Best effort local health check (non-fatal if it fails right after restart).
curl -sf http://127.0.0.1:5000/status >/dev/null || true

mkdir -p "$HOME/.cloudflared"
LOG_FILE="$HOME/.cloudflared/quick-tunnel.log"
PID_FILE="$HOME/.cloudflared/quick-tunnel.pid"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    kill "$OLD_PID" || true
    sleep 1
  fi
fi
pkill -f -- "cloudflared tunnel --protocol http2 --url http://127.0.0.1:5000" || true

: > "$LOG_FILE"
nohup cloudflared tunnel --protocol http2 --url http://127.0.0.1:5000 > "$LOG_FILE" 2>&1 < /dev/null &
echo $! > "$PID_FILE"

URL=""
for _ in $(seq 1 45); do
  URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_FILE" | tail -1 || true)"
  if [[ -n "$URL" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$URL" ]]; then
  echo "ERROR=No tunnel URL found yet. Check $LOG_FILE"
  echo "LOG_FILE=$LOG_FILE"
  exit 2
fi

echo "TUNNEL_URL=$URL"
echo "LOG_FILE=$LOG_FILE"
echo "PID_FILE=$PID_FILE"
'@

# Normalize to LF so bash on the Pi does not choke on Windows CRLF ("pipefail\r").
$remoteUnix = ($remote -replace "`r`n", "`n") -replace "`r", "`n"
$output = $remoteUnix | ssh $PiHost "bash -s"
if ($LASTEXITCODE -ne 0) {
    throw "Remote tunnel bootstrap failed."
}

$tunnelUrl = ""
$logFile = ""
foreach ($line in ($output -split "`r?`n")) {
    if ($line -like "TUNNEL_URL=*") { $tunnelUrl = $line.Substring("TUNNEL_URL=".Length) }
    if ($line -like "LOG_FILE=*") { $logFile = $line.Substring("LOG_FILE=".Length) }
}

if (-not $tunnelUrl) {
    throw "Tunnel URL was not returned. Raw output:`n$output"
}

Write-Host "[3/3] Done." -ForegroundColor Green
Write-Host ""
Write-Host "Tunnel URL:" -ForegroundColor Green
Write-Host "  $tunnelUrl" -ForegroundColor Yellow
Write-Host ""
Write-Host "Set this in Emergent Secrets:" -ForegroundColor Green
Write-Host "  PI_BASE_URL=$tunnelUrl" -ForegroundColor Yellow
Write-Host ""
Write-Host "Then redeploy your app in Emergent." -ForegroundColor Green
Write-Host ""
Write-Host "Optional quick checks:" -ForegroundColor Cyan
Write-Host "  $tunnelUrl/status"
if ($logFile) {
    Write-Host ""
    Write-Host "Pi tunnel log file: $logFile" -ForegroundColor DarkGray
}

try {
    Set-Clipboard -Value $tunnelUrl
    Write-Host "Copied tunnel URL to clipboard." -ForegroundColor DarkGray
} catch {
    # Clipboard may fail in some shells; ignore.
}
