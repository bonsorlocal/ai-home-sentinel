# Deploy AI Home Sentinel from Windows to the Raspberry Pi.
#
# Usage:
#   $env:PI_HOST = "sentinel@192.168.1.XXX"
#   .\scripts\deploy_pi.ps1
#
# Or: .\scripts\deploy_pi.ps1 sentinel@192.168.1.XXX
#
# Requires: OpenSSH client (ssh/scp), Pi reachable on LAN, repo on Pi at
# /home/sentinel/ai-home-sentinel

param(
    [string]$PiHost = $env:PI_HOST
)

$ErrorActionPreference = "Stop"
$LocalRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PiDir = "/home/sentinel/ai-home-sentinel"

if (-not $PiHost) {
    Write-Host @"

Pi host not set. Set your Pi SSH address first:

  `$env:PI_HOST = "sentinel@YOUR-PI-IP"
  .\scripts\deploy_pi.ps1

One-time SSH config (~/.ssh/config):

  Host sentinel-pi
      HostName 192.168.1.XXX
      User sentinel

Then: `$env:PI_HOST = "sentinel-pi"

"@ -ForegroundColor Yellow
    exit 1
}

Write-Host "Testing SSH to $PiHost..." -ForegroundColor Cyan
ssh -o ConnectTimeout=10 $PiHost "echo ok"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Syncing project files to Pi (excludes venv, data, secrets)..." -ForegroundColor Cyan
# rsync is ideal but often missing on Windows; use scp -r with tar via ssh
ssh $PiHost "mkdir -p $PiDir"
tar -C $LocalRoot -cf - `
    --exclude=venv `
    --exclude=.venv `
    --exclude=data `
    --exclude=__pycache__ `
    --exclude=.pytest_cache `
    --exclude=*.db `
    --exclude=secrets.yaml `
    --exclude=.git `
    . | ssh $PiHost "tar -C $PiDir -xf -"

$SecretsPath = Join-Path $LocalRoot "secrets.yaml"
if (Test-Path $SecretsPath) {
    Write-Host "Copying secrets.yaml to Pi..." -ForegroundColor Cyan
    scp $SecretsPath "${PiHost}:${PiDir}/secrets.yaml"
}

Write-Host "Installing deps and restarting service on Pi..." -ForegroundColor Cyan
ssh $PiHost @"
set -e
cd $PiDir
source venv/bin/activate
pip install -q -r requirements.txt
sudo systemctl restart sentinel
sleep 3
systemctl is-active sentinel
curl -sf http://localhost:5000/health
echo ''
curl -sf http://localhost:5000/status | python3 -c "import sys,json; s=json.load(sys.stdin); print('phase', s.get('phase'), '| camera', s.get('camera_active'), '| brain', s.get('brain_active'))"
"@

Write-Host ""
Write-Host "Deploy complete. Open the dashboard at:" -ForegroundColor Green
ssh $PiHost "echo http://`$(hostname -I | awk '{print `$1}'):5000"
