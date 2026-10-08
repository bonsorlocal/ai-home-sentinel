<#
.SYNOPSIS
  Run AI Home Sentinel on this Windows PC using the laptop webcam.

.DESCRIPTION
  Uses gitignored config.local.yaml so config.yaml stays Pi-ready.
  Open http://localhost:5000 after it starts.
#>

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Example = Join-Path $Root "config.local.yaml.example"
$Local = Join-Path $Root "config.local.yaml"
if (-not (Test-Path $Local)) {
    Copy-Item $Example $Local
    Write-Host "Created config.local.yaml from the example (webcam + local DVR path)."
}

$Activate = Join-Path $Root "venv\Scripts\Activate.ps1"
if (-not (Test-Path $Activate)) {
    Write-Host "Creating venv and installing dependencies (first run)..."
    python -m venv (Join-Path $Root "venv")
    & $Activate
    python -m pip install --upgrade pip
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    python -m pip install -r (Join-Path $Root "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Full requirements failed (face_recognition often cannot build on Windows)."
        Write-Host "Installing the PC-test set without face_recognition..."
        python -m pip install PyYAML Flask psutil numpy opencv-python google-auth requests pytest ultralytics
    }
} else {
    & $Activate
}

if (-not (Test-Path (Join-Path $Root "secrets.yaml"))) {
    Copy-Item (Join-Path $Root "secrets.yaml.example") (Join-Path $Root "secrets.yaml")
    Write-Host "Created secrets.yaml from the example. Add google_api_key for Gemini chat."
}

Write-Host "Starting Sentinel on http://localhost:5000  (Ctrl+C to stop)"
python (Join-Path $Root "run.py")
