<#
.SYNOPSIS
  Run Emergent webapp on this Windows PC against local Sentinel (:5000).

.DESCRIPTION
  Writes backend/.env from secrets.yaml (gitignored), ensures frontend build,
  then starts scripts/emergent_host.py on port 8080.

  Prerequisites:
    - Sentinel already running on http://localhost:5000
    - secrets.yaml has google_api_key and mongo_atlas_url
#>

param(
    [int]$Port = 8080,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$SecretsPath = Join-Path $Root "secrets.yaml"
if (-not (Test-Path $SecretsPath)) {
    throw "secrets.yaml missing. Copy secrets.yaml.example and add google_api_key + mongo_atlas_url."
}

$parsed = & .\venv\Scripts\python.exe -c @"
import yaml, sys
d = yaml.safe_load(open(r'$SecretsPath', encoding='utf-8')) or {}
g = str(d.get('google_api_key') or '').strip()
m = str(d.get('mongo_atlas_url') or '').strip()
if not g:
    print('MISSING_GOOGLE_KEY')
    sys.exit(2)
if not m:
    print('MISSING_MONGO')
    sys.exit(3)
# Print values for env write only (script consumes them; do not log)
print(g)
print(m)
"@
if ($LASTEXITCODE -eq 2) { throw "secrets.yaml is missing google_api_key (Gemini AIza key from AI Studio)." }
if ($LASTEXITCODE -eq 3) { throw "secrets.yaml is missing mongo_atlas_url (MongoDB Atlas connection string)." }
if ($LASTEXITCODE -ne 0) { throw "Failed to read secrets.yaml" }

$lines = @($parsed)
$GoogleKey = $lines[0]
$MongoUrl = $lines[1]

$BackendDir = Join-Path $Root "backend"
$EnvFile = Join-Path $BackendDir ".env"
@"
MONGO_URL=$MongoUrl
DB_NAME=sentinel_app
EMERGENT_LLM_KEY=$GoogleKey
CORS_ORIGINS=*
PI_BASE_URL=http://127.0.0.1:5000
PI_VIDEO_URL=http://127.0.0.1:5000/video
EMERGENT_GEMINI_MODEL=gemini-1.5-flash
"@ | Set-Content -Path $EnvFile -Encoding UTF8
Write-Host "Wrote backend/.env (PI_BASE_URL=http://127.0.0.1:5000, Gemini key loaded)." -ForegroundColor DarkGray

$BuildIndex = Join-Path $Root "frontend\build\index.html"
if (-not (Test-Path $BuildIndex) -and -not $SkipBuild) {
    Write-Host "Building Emergent frontend (one-time)..." -ForegroundColor Cyan
    $FrontendDir = Join-Path $Root "frontend"
    Push-Location $FrontendDir
    try {
        $env:REACT_APP_BACKEND_URL = ""
        $env:CI = "false"
        $env:DISABLE_ESLINT_PLUGIN = "true"
        if (Test-Path "package-lock.json") {
            cmd /c "npm ci --no-audit --no-fund --legacy-peer-deps"
            if ($LASTEXITCODE -ne 0) {
                cmd /c "npm install --no-audit --no-fund --legacy-peer-deps"
            }
        } else {
            cmd /c "npm install --no-audit --no-fund --legacy-peer-deps"
        }
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        cmd /c "npm install ajv@8.17.1 --no-audit --no-fund --legacy-peer-deps"
        cmd /c "npm run build"
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    } finally {
        Pop-Location
    }
}

if (-not (Test-Path $BuildIndex)) {
    throw "frontend/build/index.html missing. Install Node.js LTS and re-run without -SkipBuild."
}

# Ensure FastAPI stack deps + stub package path
$Activate = Join-Path $Root "venv\Scripts\Activate.ps1"
if (Test-Path $Activate) { & $Activate }
python -m pip install -q -r (Join-Path $Root "backend\requirements.txt")
python -m pip install -q -r (Join-Path $Root "scripts\emergent_requirements.pi.txt")

$Stubs = Join-Path $Root "scripts\emergent_stubs"
$env:EMERGENT_APP_DIR = "$Root"
$env:PORT = "$Port"
$env:PYTHONPATH = "$Stubs;$BackendDir;$env:PYTHONPATH"
$env:PI_BASE_URL = "http://127.0.0.1:5000"
$env:PI_VIDEO_URL = "http://127.0.0.1:5000/video"
$env:SENTINEL_VIDEO_URL = "http://127.0.0.1:5000/video"

Write-Host "Checking Sentinel on :5000..." -ForegroundColor Cyan
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:5000/status" -TimeoutSec 3 -UseBasicParsing
    Write-Host "  Sentinel is up." -ForegroundColor Green
} catch {
    Write-Host "  WARNING: Sentinel :5000 not reachable. Start .\scripts\run_pc.ps1 first." -ForegroundColor Yellow
}

Write-Host "Starting Emergent on http://localhost:$Port  (Ctrl+C to stop)" -ForegroundColor Green
python (Join-Path $Root "scripts\emergent_host.py")
