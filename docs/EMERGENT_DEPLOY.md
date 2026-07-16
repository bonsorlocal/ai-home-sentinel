# Deploy Emergent on Pi (RAM-efficient)

This flow keeps the Pi focused on camera + inference:

- React build runs on your Windows laptop
- Pi runs a single `emergent-app` service on port `8080`
- MongoDB uses Atlas (cloud), not local `mongod`

| App | URL |
|-----|-----|
| AI Home Sentinel (current) | `http://<pi-ip>:5000` |
| Emergent app (new) | `http://<pi-ip>:8080` |

## Prerequisites

1. Emergent project is pushed to GitHub.
2. Pi SSH works (`sentinel@<pi-ip>`).
3. Node.js + npm are installed on your Windows laptop.
4. `mongo_atlas_url` is set in Pi `secrets.yaml`.

## Step 1 — Create MongoDB Atlas URI

1. Create a free cluster at [mongodb.com/atlas](https://www.mongodb.com/atlas).
2. Create DB user credentials.
3. Add network access for your LAN (or temporary `0.0.0.0/0`).
4. Copy your `mongodb+srv://...` connection string.
5. On the Pi, add it to `/home/sentinel/ai-home-sentinel/secrets.yaml`:

```yaml
mongo_atlas_url: "mongodb+srv://..."
```

## Step 2 — Deploy from Windows

```powershell
cd C:\Users\jorda\OneDrive\Documents\ai-home-sentinel-pi

$env:PI_HOST = "sentinel@192.168.1.244"
$env:EMERGENT_REPO_URL = "https://github.com/bonsorlocal/ai-home-sentinel.git"
$env:EMERGENT_BRANCH = "web-app"

.\scripts\deploy_emergent_pi.ps1
```

Optional:

```powershell
$env:GITHUB_TOKEN = "ghp_..."                        # private repos
$env:EMERGENT_BACKEND_PUBLIC_URL = "http://192.168.1.244:8080"
$env:EMERGENT_DB_NAME = "sentinel_app"
```

## What the deploy script does

1. Clones `web-app` branch to a temporary local folder.
2. Builds React locally (`npm ci && npm run build`).
3. Copies `backend/`, built `frontend/build/`, and runtime scripts to Pi.
4. Runs `scripts/setup_emergent_pi.sh` on Pi.
5. Starts a single systemd service: `emergent-app`.

## Verification checklist

Run these after deploy:

- `curl http://192.168.1.244:8080/api/` returns success JSON
- Open `http://192.168.1.244:8080` and confirm UI loads
- Sentinel still works at `http://192.168.1.244:5000`
- `free -h` on Pi shows RAM headroom while camera + detection are active
- `systemctl status emergent-app` is `active (running)`

Optional comparison link in Sentinel (`config.yaml`):

```yaml
emergent_app:
  enabled: true
  url: "http://192.168.1.244:8080"
  label: "Emergent App"
```

## Troubleshooting

| Issue | Fix |
|------|-----|
| `npm` missing on Windows | Install Node.js LTS and retry |
| Pi service fails | `journalctl -u emergent-app -n 80 --no-pager` |
| Atlas auth error | Check `mongo_atlas_url` user/password and IP allowlist |
| API opens but UI is blank | Re-run deploy; ensure laptop build generated `frontend/build/index.html` |
| Wrong API endpoint in browser | Set `EMERGENT_BACKEND_PUBLIC_URL` before deploy |
