<#
  deploy_emergent_pi.ps1 — AI Home Sentinel, Raspberry Pi LAN deploy (live-data mode)

  Builds the React frontend for same-origin (relative /api) and launches the FastAPI
  backend on port 8080, which serves BOTH the API (/api/*) and the built frontend.
  The backend proxies the on-Pi Sentinel stack at PI_BASE_URL.

  Runs under PowerShell 7+ (pwsh) on Raspberry Pi OS / Linux.
  Usage:
    pwsh ./deploy_emergent_pi.ps1
    pwsh ./deploy_emergent_pi.ps1 -Port 8080 -PiBaseUrl http://127.0.0.1:5000 -HostIp 192.168.1.244
  Prereqs on the Pi: python3 + pip, node + yarn, and a running MongoDB (mongodb://localhost:27017).
#>
param(
  [int]$Port = 8080,
  [string]$BindHost = "0.0.0.0",
  [string]$HostIp = "192.168.1.244",
  [string]$PiBaseUrl = "http://127.0.0.1:5000",
  [switch]$SkipBuild,
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$PublicUrl = "http://${HostIp}:${Port}"

function Info($m) { Write-Host "[sentinel] $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "[sentinel] $m" -ForegroundColor Yellow }

# --- Upsert a KEY=VALUE line in an .env file (preserves other keys) ---
function Set-EnvVar([string]$File, [string]$Key, [string]$Value) {
  $lines = @()
  if (Test-Path $File) { $lines = Get-Content $File }
  $found = $false
  $out = foreach ($line in $lines) {
    if ($line -match "^\s*$([regex]::Escape($Key))\s*=") { $found = $true; "$Key=$Value" }
    else { $line }
  }
  if (-not $found) { $out += "$Key=$Value" }
  Set-Content -Path $File -Value $out -Encoding utf8
}

# 1) Backend environment ------------------------------------------------------
Info "Configuring backend/.env"
$envFile = Join-Path $Backend ".env"
if (-not (Test-Path $envFile)) {
  Set-EnvVar $envFile "MONGO_URL" "mongodb://localhost:27017"
  Set-EnvVar $envFile "DB_NAME" "sentinel"
  Set-EnvVar $envFile "CORS_ORIGINS" "*"
  Warn "Created a new .env — set EMERGENT_LLM_KEY for Sentinel AI (Gemini)."
}
Set-EnvVar $envFile "PI_BASE_URL" $PiBaseUrl
Set-EnvVar $envFile "EMERGENT_BACKEND_PUBLIC_URL" $PublicUrl
if (-not (Select-String -Path $envFile -Pattern "^EMERGENT_LLM_KEY=" -Quiet)) {
  Warn "EMERGENT_LLM_KEY is not set in backend/.env — the Sentinel AI chat will not work until you add it."
}

# 2) Backend dependencies -----------------------------------------------------
if (-not $SkipInstall) {
  Info "Installing backend Python dependencies"
  Push-Location $Backend
  python3 -m pip install --upgrade pip | Out-Null
  python3 -m pip install -r requirements.txt
  Pop-Location
}

# 3) Frontend build (relative /api => same origin) ----------------------------
if (-not $SkipBuild) {
  Info "Building frontend (REACT_APP_BACKEND_URL='' -> relative /api, same origin)"
  Push-Location $Frontend
  $env:REACT_APP_BACKEND_URL = ""      # critical: keeps API calls same-origin on :$Port
  yarn install --frozen-lockfile
  yarn build
  Pop-Location
}
if (-not (Test-Path (Join-Path $Frontend "build/index.html"))) {
  throw "Frontend build not found. Run without -SkipBuild first."
}

# 4) Launch backend (serves API + built frontend) -----------------------------
Info "Starting AI Home Sentinel on $PublicUrl  (Pi stack: $PiBaseUrl)"
Info "Open $PublicUrl in a LAN browser. Ctrl+C to stop."
Push-Location $Backend
try {
  python3 -m uvicorn server:app --host $BindHost --port $Port
}
finally {
  Pop-Location
}
