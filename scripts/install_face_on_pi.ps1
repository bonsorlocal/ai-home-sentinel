# Push face install script to the Pi and run it (builds dlib/face_recognition).
# Usage:
#   $env:PI_PASS = "..."
#   .\scripts\install_face_on_pi.ps1
#   .\scripts\install_face_on_pi.ps1 -PiHostIp 192.168.1.243

param(
    [string]$PiHostIp = $(if ($env:PI_HOST_IP) { $env:PI_HOST_IP } else { "192.168.1.243" })
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $env:PI_PASS) {
    throw "Set PI_PASS in the environment (do not commit it)."
}

Set-Location $Root
Write-Host "== upload face install script ==" -ForegroundColor Cyan
python scripts/pi_remote.py --host $PiHostIp --put scripts/_pi_install_face_recognition.sh /home/sentinel/ai-home-sentinel/scripts/_pi_install_face_recognition.sh
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== run face install (may take a long time on Pi) ==" -ForegroundColor Cyan
python scripts/pi_remote.py --host $PiHostIp --sudo "chmod +x /home/sentinel/ai-home-sentinel/scripts/_pi_install_face_recognition.sh && sudo -u sentinel bash /home/sentinel/ai-home-sentinel/scripts/_pi_install_face_recognition.sh"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "== restart sentinel and check face status ==" -ForegroundColor Cyan
python scripts/pi_remote.py --host $PiHostIp --sudo "systemctl restart sentinel && sleep 4 && curl -s http://127.0.0.1:5000/api/status | python3 -c `"import sys,json; d=json.load(sys.stdin); print('face', d.get('face_recognition_active'), d.get('runtime',{}).get('face_recognition'))`""
exit $LASTEXITCODE
