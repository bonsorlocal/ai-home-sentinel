<#
.SYNOPSIS
  After GCP / AI Studio billing is topped up, verify Gemini + GCP services.

.DESCRIPTION
  1) Confirms google_api_key is present
  2) Optionally hits Sentinel /status (expects run_pc.ps1 or Pi sentinel running)
  3) Prints the one-time bucket setup command if Video Intelligence looks unready

.EXAMPLE
  .\scripts\verify_google_billing.ps1
  .\scripts\verify_google_billing.ps1 -BaseUrl http://127.0.0.1:5000
#>

param(
    [string]$BaseUrl = "http://127.0.0.1:5000",
    [string]$ProjectId = "sentinel-501222"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$SecretsPath = Join-Path $Root "secrets.yaml"
if (-not (Test-Path $SecretsPath)) {
    throw "secrets.yaml missing. Copy secrets.yaml.example and add google_api_key."
}

$parsed = & .\venv\Scripts\python.exe -c @"
import yaml, sys
d = yaml.safe_load(open(r'$SecretsPath', encoding='utf-8')) or {}
g = str(d.get('google_api_key') or '').strip()
print('yes' if g else 'no')
"@
if ($parsed -ne "yes") {
    throw "secrets.yaml is missing google_api_key (AI Studio AIza key)."
}
Write-Host "[ok] google_api_key present in secrets.yaml" -ForegroundColor Green

$GcpKey = Join-Path $Root "secrets\gcp-service-account.json"
if (Test-Path $GcpKey) {
    Write-Host "[ok] GCP service account JSON present" -ForegroundColor Green
} else {
    Write-Host "[warn] secrets/gcp-service-account.json missing — VI/TTS will stay off" -ForegroundColor Yellow
}

try {
    $status = Invoke-RestMethod -Uri "$BaseUrl/status" -TimeoutSec 10
} catch {
    throw "Cannot reach $BaseUrl/status — start Sentinel first (.\scripts\run_pc.ps1)."
}

$brain = $status.brain
$google = $status.runtime.google
Write-Host ""
Write-Host "Brain provider: $($brain.provider)  has_google_key=$($brain.has_google_key)  available=$($brain.available)"
Write-Host "GCP credentials: $($google.credentials.available)  ($($google.credentials.message))"
Write-Host "Video Intelligence: $($google.video_intelligence.available)  bucket=$($google.video_intelligence.gcs_bucket)"
Write-Host "Text-to-Speech: $($google.text_to_speech.available)"
Write-Host "Voice TTS provider: $($status.voice.tts_provider)"

if (-not $brain.has_google_key) {
    Write-Host ""
    Write-Host "Next: set brain.provider to google (or auto) in config.local.yaml and restart." -ForegroundColor Cyan
}

if (-not $google.video_intelligence.available -or -not $google.text_to_speech.available) {
    Write-Host ""
    Write-Host "Next (billing enabled + gcloud logged in):" -ForegroundColor Cyan
    Write-Host "  bash scripts/setup_gcp_bucket.sh $ProjectId us-west1"
    Write-Host "Then restart Sentinel and re-run this script."
    exit 2
}

Write-Host ""
Write-Host "Google surfaces look ready. Optional: switch config.local.yaml brain/video_metadata provider back to google or auto." -ForegroundColor Green
