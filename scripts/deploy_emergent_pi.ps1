# Deploy Emergent web-app branch to Raspberry Pi.
#
# This script builds React on Windows (fast), then copies runtime artifacts to
# the Pi. The Pi never runs npm/node for production deploys.

param(
    [string]$PiHost = $env:PI_HOST,
    [string]$RepoUrl = $env:EMERGENT_REPO_URL,
    [string]$Branch = $(if ($env:EMERGENT_BRANCH) { $env:EMERGENT_BRANCH } else { "web-app" }),
    [string]$BackendPort = $(if ($env:EMERGENT_BACKEND_PORT) { $env:EMERGENT_BACKEND_PORT } else { "8080" }),
    [string]$BackendPublicUrl = $env:EMERGENT_BACKEND_PUBLIC_URL,
    [string]$GitHubToken = $env:GITHUB_TOKEN,
    [string]$DbName = $(if ($env:EMERGENT_DB_NAME) { $env:EMERGENT_DB_NAME } else { "sentinel_app" })
)

$ErrorActionPreference = "Stop"
$LocalRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$PiDir = "/home/sentinel/ai-home-sentinel"
$PiAppDir = "/home/sentinel/emergent-app"
$TempRoot = Join-Path $env:TEMP ("emergent-build-" + [guid]::NewGuid().ToString("N"))
$TempRepo = Join-Path $TempRoot "repo"

if (-not $PiHost) {
    Write-Host @"

Pi host not set:

  `$env:PI_HOST = "sentinel@YOUR-PI-IP"
  `$env:EMERGENT_REPO_URL = "https://github.com/YOUR_USER/YOUR_REPO.git"
  .\scripts\deploy_emergent_pi.ps1

"@ -ForegroundColor Yellow
    exit 1
}

if (-not $RepoUrl) {
    Write-Host @"

Emergent GitHub repo not set.

Step-by-step to get your repo URL:
  1. Open https://app.emergent.sh and open your project.
  2. Click "Save to GitHub" in the chat toolbar.
  3. Create a new repo (or pick an existing one) and click PUSH TO GITHUB.
  4. On GitHub, open that repo and copy the URL from the green "Code" button.
     Example: https://github.com/bonsorlocal/my-sentinel-app.git

Then run:

  `$env:EMERGENT_REPO_URL = "https://github.com/YOUR_USER/YOUR_REPO.git"
  .\scripts\deploy_emergent_pi.ps1

See docs/EMERGENT_DEPLOY.md for full instructions.

"@ -ForegroundColor Yellow
    exit 1
}

Write-Host "Testing SSH to $PiHost..." -ForegroundColor Cyan
ssh -o ConnectTimeout=10 $PiHost "echo ok"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git is required on Windows for deploy_emergent_pi.ps1"
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm is required on Windows. Install Node.js LTS first."
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "OpenSSH client (ssh/scp) is required."
}

New-Item -ItemType Directory -Path $TempRepo -Force | Out-Null
try {
    $CloneUrl = $RepoUrl
    if ($GitHubToken -and $RepoUrl.StartsWith("https://github.com/")) {
        $CloneUrl = $RepoUrl -replace "^https://", ("https://{0}@" -f $GitHubToken)
    }

    Write-Host "Cloning $Branch branch locally..." -ForegroundColor Cyan
    # Use cmd so Git's stderr progress ("Cloning into...") does not trip
    # PowerShell ErrorActionPreference=Stop via NativeCommandError.
    cmd /c "git clone --depth 1 --branch $Branch `"$CloneUrl`" `"$TempRepo`""
    if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)" }

    $FrontendDir = Join-Path $TempRepo "frontend"
    $BackendDir = Join-Path $TempRepo "backend"
    if (-not (Test-Path (Join-Path $FrontendDir "package.json"))) {
        throw "frontend/package.json missing in branch '$Branch'"
    }
    if (-not (Test-Path (Join-Path $BackendDir "server.py"))) {
        throw "backend/server.py missing in branch '$Branch'"
    }

    $PiHostName = ($PiHost -split "@")[-1]
    if (-not $BackendPublicUrl) {
        $BackendPublicUrl = "http://${PiHostName}:${BackendPort}"
    }

    Write-Host "Building frontend on Windows..." -ForegroundColor Cyan
    $oldCwd = Get-Location
    Set-Location $FrontendDir
    try {
        $env:REACT_APP_BACKEND_URL = $BackendPublicUrl
        $lockFile = Join-Path $FrontendDir "package-lock.json"
        if (Test-Path $lockFile) {
            Write-Host "Found package-lock.json - running npm ci..." -ForegroundColor DarkGray
            cmd /c "npm ci --no-audit --no-fund --legacy-peer-deps"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "npm ci failed - falling back to npm install..." -ForegroundColor Yellow
                cmd /c "npm install --no-audit --no-fund --legacy-peer-deps"
                if ($LASTEXITCODE -ne 0) { throw "npm install failed (exit $LASTEXITCODE)" }
            }
        } else {
            Write-Host "No package-lock.json - running npm install..." -ForegroundColor Yellow
            cmd /c "npm install --no-audit --no-fund --legacy-peer-deps"
            if ($LASTEXITCODE -ne 0) { throw "npm install failed (exit $LASTEXITCODE)" }
        }
        # CRA/craco on modern Node often pulls a broken ajv pair
        # ("Cannot find module 'ajv/dist/compile/codegen'"). Pin ajv@8.
        Write-Host "Pinning ajv@8 for CRA/webpack build compatibility..." -ForegroundColor DarkGray
        cmd /c "npm install ajv@8.17.1 --no-audit --no-fund --legacy-peer-deps"
        if ($LASTEXITCODE -ne 0) { throw "ajv pin failed (exit $LASTEXITCODE)" }
        Write-Host "Running npm run build..." -ForegroundColor DarkGray
        # Avoid treating warnings as build failures on CI-like shells.
        $env:CI = "false"
        $env:DISABLE_ESLINT_PLUGIN = "true"
        cmd /c "npm run build"
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed (exit $LASTEXITCODE)" }
    } finally {
        Set-Location $oldCwd
    }

    if (-not (Test-Path (Join-Path $FrontendDir "build\index.html"))) {
        throw "Frontend build failed: build/index.html not found."
    }

    Write-Host "Copying runtime artifacts to Pi..." -ForegroundColor Cyan
    ssh $PiHost "rm -rf $PiAppDir/backend $PiAppDir/frontend/build; mkdir -p $PiAppDir/frontend $PiDir/scripts"
    scp -r "$BackendDir" "${PiHost}:$PiAppDir/"
    scp -r (Join-Path $FrontendDir "build") "${PiHost}:$PiAppDir/frontend/"
    scp (Join-Path $LocalRoot "scripts\setup_emergent_pi.sh") "${PiHost}:${PiDir}/scripts/setup_emergent_pi.sh"
    scp (Join-Path $LocalRoot "scripts\emergent_host.py") "${PiHost}:${PiDir}/scripts/emergent_host.py"
    scp (Join-Path $LocalRoot "scripts\emergent_requirements.pi.txt") "${PiHost}:${PiDir}/scripts/emergent_requirements.pi.txt"
    scp -r (Join-Path $LocalRoot "scripts\emergent_stubs") "${PiHost}:${PiDir}/scripts/"

    Write-Host "Configuring runtime service on Pi..." -ForegroundColor Cyan
    $remoteEnv = "EMERGENT_APP_DIR='$PiAppDir' EMERGENT_BACKEND_PORT='$BackendPort' EMERGENT_DB_NAME='$DbName' SENTINEL_DIR='$PiDir'"
    ssh $PiHost "$remoteEnv bash $PiDir/scripts/setup_emergent_pi.sh"
} finally {
    if (Test-Path $TempRoot) {
        Remove-Item -Recurse -Force $TempRoot
    }
}

Write-Host ""
Write-Host "Emergent deploy finished. Compare with Sentinel at:" -ForegroundColor Green
$PiHostName = ($PiHost -split "@")[-1]
Write-Host "  Sentinel:  http://${PiHostName}:5000"
Write-Host "  Emergent:  http://${PiHostName}:$BackendPort"
