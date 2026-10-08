<#
.SYNOPSIS
  One-shot investor demo: Pi-local Emergent UI + live camera + Gemini brain.

.DESCRIPTION
  Most efficient path for an in-room demo (same Wi-Fi, no Cloudflare tunnel):
    Laptop -> SSH -> Pi runs Sentinel (:5000) + Emergent (:8080)
    Investors open http://<pi-ip>:8080

  Optional -Cloud switch starts a Cloudflare tunnel and prints PI_BASE_URL for
  Emergent cloud hosting (remote investors).

.EXAMPLE
  .\scripts\investor_demo.ps1
  .\scripts\investor_demo.ps1 -Cloud
#>

param(
    [string]$PiHost = $(if ($env:PI_HOST) { $env:PI_HOST } else { "sentinel-pi" }),
    [string]$RepoUrl = $env:EMERGENT_REPO_URL,
    [switch]$Cloud,
    [switch]$SkipDeploy
)

$ErrorActionPreference = "Stop"
$LocalRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PiDir = "/home/sentinel/ai-home-sentinel"
$PiIp = ($PiHost -split "@")[-1]

function Invoke-ScpViaTmp {
    param(
        [string]$LocalPath,
        [string]$RemoteDest
    )
    $name = Split-Path $LocalPath -Leaf
    $tmp = "/tmp/$name"
    scp $LocalPath "${PiHost}:$tmp"
    if ($LASTEXITCODE -ne 0) { throw "scp to $tmp failed" }
    ssh $PiHost "sudo mv '$tmp' '$RemoteDest'; sudo chown sentinel:sentinel '$RemoteDest'; sudo chmod +x '$RemoteDest'"
    if ($LASTEXITCODE -ne 0) { throw "sudo mv to $RemoteDest failed" }
}

Write-Host "== Investor demo (house Wi-Fi, no cloud) ==" -ForegroundColor Cyan
Write-Host "Pi: $PiHost"

Write-Host "`n[1/4] Testing SSH..." -ForegroundColor Cyan
ssh -o ConnectTimeout=10 $PiHost "echo ok"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Cannot reach the Pi. That is expected if it is unplugged or still booting." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "1. Plug the Pi in and wait about one minute." -ForegroundColor Yellow
    Write-Host "2. Put this laptop on the same Wi-Fi as the Pi." -ForegroundColor Yellow
    Write-Host "3. Run this again: .\scripts\investor_demo.ps1" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "If it is plugged in and still fails, attach a keyboard and monitor and run:" -ForegroundColor Yellow
    Write-Host "  cd /home/sentinel/ai-home-sentinel"
    Write-Host "  bash scripts/investor_demo_on_pi.sh"
    exit 1
}

if (-not $SkipDeploy) {
    if (-not $RepoUrl) {
        Write-Host "`n[2/4] Emergent repo not set - checking if app already on Pi..." -ForegroundColor Yellow
        $hasApp = ssh $PiHost 'test -f /home/sentinel/emergent-app/frontend/build/index.html && echo yes || echo no'
        if ($hasApp -match "yes") {
            Write-Host "  Emergent build found - running finish_emergent_pi_setup.ps1" -ForegroundColor DarkGray
            & (Join-Path $LocalRoot "scripts\finish_emergent_pi_setup.ps1") -PiHost $PiHost
        } else {
            Write-Host ""
            Write-Host "Emergent app not on Pi yet. Set your Emergent GitHub repo URL:" -ForegroundColor Yellow
            Write-Host '  $env:EMERGENT_REPO_URL = "https://github.com/YOUR_USER/YOUR_EMERGENT_REPO.git"'
            Write-Host "  .\scripts\investor_demo.ps1"
            exit 1
        }
    } else {
        Write-Host "`n[2/4] Deploying Emergent web-app to Pi..." -ForegroundColor Cyan
        $env:PI_HOST = $PiHost
        $env:EMERGENT_REPO_URL = $RepoUrl
        & (Join-Path $LocalRoot "scripts\deploy_emergent_pi.ps1")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} else {
    Write-Host "`n[2/4] Skipping deploy (-SkipDeploy)" -ForegroundColor DarkGray
}

Write-Host "`n[3/4] Starting services on Pi..." -ForegroundColor Cyan
ssh $PiHost "mkdir -p $PiDir/scripts"
Invoke-ScpViaTmp (Join-Path $LocalRoot "scripts\investor_demo_on_pi.sh") "$PiDir/scripts/investor_demo_on_pi.sh"
ssh $PiHost "bash $PiDir/scripts/investor_demo_on_pi.sh"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n[4/4] Verifying from laptop..." -ForegroundColor Cyan
try {
    $health = Invoke-RestMethod -Uri "http://${PiIp}:8080/api/system/health" -TimeoutSec 10
    Write-Host "  pi_connected: $($health.pi_connected)" -ForegroundColor $(if ($health.pi_connected) { "Green" } else { "Red" })
    Write-Host "  camera_online: $($health.camera_online)"
} catch {
    Write-Host "  Could not reach http://${PiIp}:8080 from laptop (firewall?). Open on Pi browser to confirm." -ForegroundColor Yellow
}

if ($Cloud) {
    Write-Host "`n[Cloud] Starting Cloudflare tunnel for remote Emergent..." -ForegroundColor Cyan
    & (Join-Path $LocalRoot "scripts\deploysshtunnel.ps1") -PiHost $PiHost
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Green
Write-Host " INVESTOR DEMO URL" -ForegroundColor Green
Write-Host "   http://${PiIp}:8080" -ForegroundColor Yellow
Write-Host "==========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Show investors: LIVE EVENTS -> camera, then SENTINEL AI -> ask about the scene." -ForegroundColor Cyan
if ($Cloud) {
    Write-Host "For Emergent cloud: paste PI_BASE_URL from tunnel output into Emergent Secrets and redeploy." -ForegroundColor Cyan
}
