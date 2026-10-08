# Finish Emergent Pi setup after deploy copied artifacts but setup failed.
# Use when backend/frontend are already on the Pi but emergent-app is not running.

param(
    [string]$PiHost = $env:PI_HOST
)

$ErrorActionPreference = "Stop"
$LocalRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PiDir = "/home/sentinel/ai-home-sentinel"
$PiAppDir = "/home/sentinel/emergent-app"
$BackendPort = $(if ($env:EMERGENT_BACKEND_PORT) { $env:EMERGENT_BACKEND_PORT } else { "8080" })
$DbName = $(if ($env:EMERGENT_DB_NAME) { $env:EMERGENT_DB_NAME } else { "sentinel_app" })

if (-not $PiHost) {
    Write-Host 'Set PI_HOST first, e.g. $env:PI_HOST = "sentinel@192.168.1.244"' -ForegroundColor Yellow
    exit 1
}

function Invoke-ScpViaTmp {
    param(
        [string]$LocalPath,
        [string]$RemoteDest
    )
    $name = Split-Path $LocalPath -Leaf
    $tmp = "/tmp/$name"
    scp $LocalPath "${PiHost}:$tmp"
    if ($LASTEXITCODE -ne 0) { throw "scp to $tmp failed" }
    ssh $PiHost "sudo mv '$tmp' '$RemoteDest'; sudo chown sentinel:sentinel '$RemoteDest'"
    if ($LASTEXITCODE -ne 0) { throw "sudo mv to $RemoteDest failed" }
}

Write-Host "Testing SSH to $PiHost..." -ForegroundColor Cyan
ssh -o ConnectTimeout=15 $PiHost "echo ok"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Ensuring setup scripts and requirements are on Pi..." -ForegroundColor Cyan
ssh $PiHost "mkdir -p $PiDir/scripts"
Invoke-ScpViaTmp (Join-Path $LocalRoot "scripts\setup_emergent_pi.sh") "$PiDir/scripts/setup_emergent_pi.sh"
Invoke-ScpViaTmp (Join-Path $LocalRoot "scripts\emergent_host.py") "$PiDir/scripts/emergent_host.py"
Invoke-ScpViaTmp (Join-Path $LocalRoot "scripts\emergent_requirements.pi.txt") "$PiDir/scripts/emergent_requirements.pi.txt"
Invoke-ScpViaTmp (Join-Path $LocalRoot "scripts\investor_demo_on_pi.sh") "$PiDir/scripts/investor_demo_on_pi.sh"
scp -r (Join-Path $LocalRoot "scripts\emergent_stubs") "${PiHost}:$PiDir/scripts/"

Write-Host "Running setup_emergent_pi.sh..." -ForegroundColor Cyan
$remoteEnv = "EMERGENT_APP_DIR='$PiAppDir' EMERGENT_BACKEND_PORT='$BackendPort' EMERGENT_DB_NAME='$DbName' SENTINEL_DIR='$PiDir'"
ssh $PiHost "$remoteEnv bash $PiDir/scripts/setup_emergent_pi.sh"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Verifying emergent-app..." -ForegroundColor Cyan
ssh $PiHost "systemctl is-active emergent-app; curl -sf http://127.0.0.1:${BackendPort}/api/ | head -c 200; echo"

$PiHostName = ($PiHost -split "@")[-1]
Write-Host ""
Write-Host "Done. Open http://${PiHostName}:${BackendPort}" -ForegroundColor Green
